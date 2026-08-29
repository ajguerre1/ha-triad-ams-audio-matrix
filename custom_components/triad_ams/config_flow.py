"""Config and options flow.

``VERSION``/``MINOR_VERSION`` match the integration this one replaces (1/4) so that entries
already in ``.storage`` load without migration. The entry ``unique_id`` scheme --
``host:port:model`` -- matches for the same reason: a different scheme would let the same matrix
be added twice.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .ams.client import AmsClient
from .ams.errors import TriadError
from .ams.model import MODELS
from .const import (
    CONF_ACTIVE_INPUTS,
    CONF_ACTIVE_OUTPUTS,
    CONF_HOST,
    CONF_INPUT_COUNT,
    CONF_MODEL,
    CONF_NAME,
    CONF_OUTPUT_COUNT,
    CONF_OUTPUT_MAX_VOLUMES,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_VOLUME_PERCENT,
)


def _connection_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    current = defaults or {}
    return vol.Schema(
        {
            vol.Optional(CONF_NAME, default=current.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Required(CONF_HOST, default=current.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=current.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Required(CONF_MODEL, default=current.get(CONF_MODEL, "AMS8")): selector(
                {
                    "select": {
                        "options": [{"value": name, "label": f"TS-{name}"} for name in MODELS],
                        "mode": "dropdown",
                    }
                }
            ),
        }
    )


async def _probe(host: str, port: int) -> str | None:
    """Confirm something on the far end talks this protocol; return its firmware.

    Asking for the firmware version rather than merely opening a socket is deliberate: port 52000
    open is not evidence of a Triad matrix, and an entry that sets up against the wrong device
    fails later in a way that is much harder to read.
    """
    client = AmsClient(host, port)
    try:
        return await client.firmware_version()
    finally:
        await client.disconnect()


class TriadConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle adding a matrix."""

    VERSION = 1
    MINOR_VERSION = 4

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = int(user_input[CONF_PORT])
            model = user_input[CONF_MODEL]

            await self.async_set_unique_id(f"{host}:{port}:{model}")
            self._abort_if_unique_id_configured()

            try:
                await _probe(host, port)
            except TriadError:
                errors["base"] = "cannot_connect"
            else:
                spec = MODELS[model]
                outputs, inputs = spec.outputs, spec.inputs
                self._data = {
                    CONF_NAME: user_input.get(CONF_NAME) or DEFAULT_NAME,
                    CONF_HOST: host,
                    CONF_PORT: port,
                    CONF_MODEL: model,
                    CONF_OUTPUT_COUNT: outputs,
                    CONF_INPUT_COUNT: inputs,
                }
                return await self.async_step_channels()

        return self.async_show_form(
            step_id="user", data_schema=_connection_schema(user_input), errors=errors
        )

    async def async_step_channels(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose which outputs and inputs are actually wired up.

        A 24x24 matrix in a house rarely has all 24 of either in use, and an entity per unused
        channel is noise in every picker for the life of the installation.
        """
        outputs = self._data[CONF_OUTPUT_COUNT]
        inputs = self._data[CONF_INPUT_COUNT]

        if user_input is not None:
            return self.async_create_entry(
                title=self._data[CONF_NAME],
                data=self._data,
                options={
                    CONF_ACTIVE_OUTPUTS: [
                        i for i in range(1, outputs + 1) if user_input.get(f"output_{i}")
                    ],
                    CONF_ACTIVE_INPUTS: [
                        i for i in range(1, inputs + 1) if user_input.get(f"input_{i}")
                    ],
                    CONF_SCAN_INTERVAL: int(
                        user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ),
                },
            )

        schema: dict[Any, Any] = {}
        for i in range(1, outputs + 1):
            schema[vol.Optional(f"output_{i}", default=True)] = bool
        for i in range(1, inputs + 1):
            schema[vol.Optional(f"input_{i}", default=True)] = bool
        schema[vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL)] = selector(
            {"number": {"min": 5, "max": 300, "step": 5, "unit_of_measurement": "s"}}
        )
        return self.async_show_form(step_id="channels", data_schema=vol.Schema(schema))

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> TriadOptionsFlow:
        return TriadOptionsFlow()


class TriadOptionsFlow(OptionsFlow):
    """Change which channels are active, the poll interval, and per-zone volume caps."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        outputs = int(entry.data.get(CONF_OUTPUT_COUNT, 24))
        inputs = int(entry.data.get(CONF_INPUT_COUNT, 24))
        options = entry.options

        active_outputs = set(options.get(CONF_ACTIVE_OUTPUTS) or range(1, outputs + 1))
        active_inputs = set(options.get(CONF_ACTIVE_INPUTS) or range(1, inputs + 1))
        caps = options.get(CONF_OUTPUT_MAX_VOLUMES) or {}

        if user_input is not None:
            return self.async_create_entry(
                data={
                    **options,
                    CONF_ACTIVE_OUTPUTS: [
                        i for i in range(1, outputs + 1) if user_input.get(f"output_{i}")
                    ],
                    CONF_ACTIVE_INPUTS: [
                        i for i in range(1, inputs + 1) if user_input.get(f"input_{i}")
                    ],
                    CONF_OUTPUT_MAX_VOLUMES: {
                        str(i): int(user_input[f"max_volume_{i}"])
                        for i in range(1, outputs + 1)
                        if user_input.get(f"max_volume_{i}") is not None
                    },
                    CONF_SCAN_INTERVAL: int(
                        user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                    ),
                }
            )

        schema: dict[Any, Any] = {}
        for i in range(1, outputs + 1):
            schema[vol.Optional(f"output_{i}", default=i in active_outputs)] = bool
            schema[
                vol.Optional(
                    f"max_volume_{i}",
                    default=int(caps.get(str(i), caps.get(i, MAX_VOLUME_PERCENT))),
                )
            ] = selector(
                {
                    "number": {
                        "min": 1,
                        "max": MAX_VOLUME_PERCENT,
                        "step": 1,
                        "unit_of_measurement": "%",
                        "mode": "slider",
                    }
                }
            )
        for i in range(1, inputs + 1):
            schema[vol.Optional(f"input_{i}", default=i in active_inputs)] = bool
        schema[
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default=int(options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
            )
        ] = selector({"number": {"min": 5, "max": 300, "step": 5, "unit_of_measurement": "s"}})

        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))
