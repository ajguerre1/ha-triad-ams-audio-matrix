"""The Triad AMS Audio Matrix integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .ams.client import AmsClient
from .ams.errors import TransportError
from .const import (
    CONF_ACTIVE_OUTPUTS,
    CONF_HOST,
    CONF_INPUT_COUNT,
    CONF_MODEL,
    CONF_OUTPUT_COUNT,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    MODELS,
)
from .coordinator import TriadCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.MEDIA_PLAYER]

type TriadConfigEntry = ConfigEntry[TriadCoordinator]


def _channel_counts(entry: ConfigEntry) -> tuple[int, int]:
    """Resolve output and input counts, preferring what the entry recorded.

    An entry written by the integration this one replaces stores both counts explicitly. Falling
    back to the model table covers an entry that predates them, rather than raising on setup.
    """
    model = entry.data.get(CONF_MODEL, "AMS24")
    default_outputs, default_inputs = MODELS.get(model, MODELS["AMS24"])
    return (
        int(entry.data.get(CONF_OUTPUT_COUNT) or default_outputs),
        int(entry.data.get(CONF_INPUT_COUNT) or default_inputs),
    )


def _active_outputs(entry: ConfigEntry, output_count: int) -> list[int]:
    """Outputs the user marked active, or all of them if the entry never said.

    An empty selection means 'none chosen yet', not 'none wanted' -- treating it literally would
    set the integration up with no entities and look like a broken install.
    """
    configured = entry.options.get(CONF_ACTIVE_OUTPUTS) or entry.data.get(CONF_ACTIVE_OUTPUTS)
    if not configured:
        return list(range(1, output_count + 1))
    return sorted(int(output) for output in configured if 1 <= int(output) <= output_count)


async def async_setup_entry(hass: HomeAssistant, entry: TriadConfigEntry) -> bool:
    """Set up one matrix from a config entry."""
    output_count, input_count = _channel_counts(entry)
    client = AmsClient(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        output_count=output_count,
        input_count=input_count,
    )

    # Fail setup loudly if the matrix is unreachable, so Home Assistant retries rather than
    # creating entities that can never have a state.
    try:
        await client.connect()
    except TransportError as err:
        await client.disconnect()
        from homeassistant.exceptions import ConfigEntryNotReady

        raise ConfigEntryNotReady(str(err)) from err

    coordinator = TriadCoordinator(
        hass,
        client,
        active_outputs=_active_outputs(entry, output_count),
        scan_interval=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        name=entry.title,
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_change))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TriadConfigEntry) -> bool:
    """Tear down, closing the socket so a reload does not leave one open."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


async def _async_reload_on_options_change(hass: HomeAssistant, entry: TriadConfigEntry) -> None:
    """Changing which outputs are active adds or removes entities, so reload rather than patch."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Accept entries written by the integration this one replaces.

    Those are version 1, minor version 4, and their keys are the ones in const.py. Nothing needs
    rewriting, so this only has to refuse a *newer* schema than this code understands -- which is
    what happens after a downgrade, and where silently proceeding would corrupt the entry.
    """
    return entry.version <= 1
