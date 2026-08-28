"""Shared entity base.

The device identity here is load-bearing. Both the device ``identifiers`` and every entity
``unique_id`` are derived from the config entry id, which is exactly what the integration this
one replaces did. Reproducing that scheme is what lets an installation swap implementations and
keep its entity_ids, areas, aliases and every dashboard and automation that names them.

Deriving identity from the host or MAC instead would be cleaner in isolation and would orphan
every existing entity on the first restart.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_MODEL, DOMAIN, UNIQUE_ID_OUTPUT
from .coordinator import OutputSnapshot, TriadCoordinator


class TriadOutputEntity(CoordinatorEntity[TriadCoordinator]):
    """Base for anything that represents a single output of a matrix."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TriadCoordinator, entry: ConfigEntry, output: int) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._output = output
        self._attr_unique_id = UNIQUE_ID_OUTPUT.format(entry_id=entry.entry_id, number=output)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Triad",
            model=entry.data.get(CONF_MODEL),
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )

    @property
    def snapshot(self) -> OutputSnapshot | None:
        """The last reading for this output, or None if it has never answered."""
        return (self.coordinator.data or {}).get(self._output)

    @property
    def available(self) -> bool:
        """Unavailable until this specific output has been read at least once.

        The coordinator can succeed overall while one output keeps failing, and an entity with no
        reading should say so rather than report a default.
        """
        return super().available and self.snapshot is not None
