"""Services that the entity model cannot express.

Three, and the list is short on purpose. A service that duplicates an existing one is a second
place for the same bug to live:

* ``sync_all`` was dropped -- ``homeassistant.update_entity`` already forces a refresh.
* A ``set_volume`` service was dropped -- ``media_player.volume_set`` already does it.

What is left is genuinely not otherwise reachable.
"""

from __future__ import annotations

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .ams.errors import TriadError
from .ams.protocol import is_command_error
from .const import DOMAIN

SERVICE_SEND_RAW = "send_raw"

ATTR_DEVICE_ID = "device_id"
ATTR_COMMAND = "command"
ATTR_ALLOW_WRITE = "allow_write"

#: The query marker. A command containing it asks a question; one without it changes something.
QUERY_MARKER = 0xF5

SEND_RAW_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): cv.string,
        vol.Required(ATTR_COMMAND): cv.string,
        vol.Optional(ATTR_ALLOW_WRITE, default=False): cv.boolean,
    }
)


def _parse_command(raw: str) -> bytes:
    cleaned = raw.replace(" ", "").replace("-", "").replace(":", "")
    try:
        command = bytes.fromhex(cleaned)
    except ValueError as err:
        msg = f"{raw!r} is not valid hex"
        raise ServiceValidationError(msg) from err
    if not command.startswith(b"\xff\x55"):
        msg = "every Triad command starts FF 55; refusing to send something the device cannot parse"
        raise ServiceValidationError(msg)
    return command


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration-level services. Entity services live on their platforms."""

    async def send_raw(call: ServiceCall) -> ServiceResponse:
        """Send one raw command and return what the device said.

        **Reads by default.** A command without the F5 query marker changes the device, and this
        service will refuse it unless ``allow_write`` is set explicitly. That guard exists because
        this hardware accepts a malformed or out-of-range write and reports success -- the Q and
        input-gain ranges both clamp silently -- so an accidental write here would be invisible.

        Returning the response is the point: this is the tool for reading something the
        integration does not model yet, which is exactly how its protocol was reconstructed.
        """
        device_id = call.data[ATTR_DEVICE_ID]
        command = _parse_command(call.data[ATTR_COMMAND])

        if QUERY_MARKER not in command and not call.data[ATTR_ALLOW_WRITE]:
            msg = (
                "this command has no F5 query marker, so it would change the device. "
                "Pass allow_write: true if that is intended."
            )
            raise ServiceValidationError(msg)

        entry = _entry_for_device(hass, device_id)
        client = entry.runtime_data.client
        try:
            text = await client.send_raw(command)
        except TriadError as err:
            msg = f"the matrix did not answer: {err}"
            raise HomeAssistantError(msg) from err

        return {
            "response": text,
            "is_error": is_command_error(text),
            "wrote": QUERY_MARKER not in command,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_RAW,
        send_raw,
        schema=SEND_RAW_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )


def _entry_for_device(hass: HomeAssistant, device_id: str):
    """Resolve a device to its loaded config entry, or say clearly why it could not be."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        msg = f"no device with id {device_id}"
        raise ServiceValidationError(msg)
    for entry_id in device.config_entries:
        entry = hass.config_entries.async_get_entry(entry_id)
        if entry and entry.domain == DOMAIN and hasattr(entry, "runtime_data"):
            return entry
    msg = "that device is not a loaded Triad AMS matrix"
    raise ServiceValidationError(msg)
