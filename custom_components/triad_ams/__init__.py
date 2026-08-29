"""The Triad AMS Audio Matrix integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .ams.client import AmsClient
from .ams.errors import TransportError
from .ams.settings import EntrySettings
from .const import CONF_HOST, CONF_PORT, DEFAULT_PORT
from .coordinator import TriadCoordinator
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.MEDIA_PLAYER,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]

type TriadConfigEntry = ConfigEntry[TriadCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: TriadConfigEntry) -> bool:
    """Set up one matrix from a config entry."""
    settings = EntrySettings.resolve(entry.data, entry.options)
    client = AmsClient(
        entry.data[CONF_HOST],
        entry.data.get(CONF_PORT, DEFAULT_PORT),
        spec=settings.spec,
    )

    # Fail setup loudly if the matrix is unreachable, so Home Assistant retries rather than
    # creating entities that can never have a state.
    try:
        await client.connect()
    except TransportError as err:
        await client.disconnect()
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = TriadCoordinator(
        hass,
        client,
        active_outputs=settings.active_outputs,
        active_inputs=settings.active_inputs,
        scan_interval=settings.scan_interval,
        name=entry.title,
        entry_id=entry.entry_id,
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    # Registered once for the integration, not per entry -- a second matrix must not replace
    # the first one's services.
    await async_setup_services(hass)
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
