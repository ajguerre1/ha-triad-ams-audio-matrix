"""Shared entity bases.

The device identity here is load-bearing. Both the device ``identifiers`` and every output
entity's ``unique_id`` are derived from the config entry id, which is exactly what the integration
this one replaces did. Reproducing that scheme is what lets an installation swap implementations
and keep its entity_ids.

Deriving identity from the host or MAC instead would be cleaner in isolation and would orphan
every existing entity on the first restart.
"""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_HOST, CONF_MODEL, DOMAIN, UNIQUE_ID_OUTPUT
from .coordinator import OutputSnapshot, TriadCoordinator


class TriadEntity(CoordinatorEntity[TriadCoordinator]):
    """Anything belonging to one matrix. Owns the device identity and nothing else."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TriadCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Triad",
            model=entry.data.get(CONF_MODEL),
            configuration_url=f"http://{entry.data[CONF_HOST]}",
        )


class TriadOutputEntity(TriadEntity):
    """Base for anything representing a single output of a matrix."""

    def __init__(self, coordinator: TriadCoordinator, entry: ConfigEntry, output: int) -> None:
        super().__init__(coordinator, entry)
        self._output = output
        # Frozen format: see const.UNIQUE_ID_OUTPUT. Changing it orphans live entities.
        self._attr_unique_id = UNIQUE_ID_OUTPUT.format(entry_id=entry.entry_id, number=output)

    @property
    def snapshot(self) -> OutputSnapshot | None:
        """The last reading for this output, or None if it has never answered."""
        data = self.coordinator.data
        return data.outputs.get(self._output) if data else None

    @property
    def available(self) -> bool:
        """Unavailable until this specific output has been read at least once.

        The coordinator can succeed overall while one output keeps failing, and an entity with no
        reading should say so rather than report a default.
        """
        return super().available and self.snapshot is not None


class TriadInputEntity(TriadEntity):
    """Base for anything representing a single input of a matrix.

    Input entities did not exist in the integration this one replaces, so their ``unique_id``
    format is free to choose -- there is nothing to stay compatible with. It is namespaced with
    ``_input_`` so it can never collide with the frozen output format.
    """

    def __init__(self, coordinator: TriadCoordinator, entry: ConfigEntry, source: int) -> None:
        super().__init__(coordinator, entry)
        self._source = source
        self._attr_unique_id = f"{entry.entry_id}_input_{source}"

    def registers_input_polling(self) -> bool:
        """Whether this entity needs the coordinator to poll inputs. Override to opt in."""
        return False

    async def async_added_to_hass(self) -> None:
        """Ask the coordinator for input data, and stop asking when removed.

        This is the one place entities reach back into the coordinator, and it is deliberate:
        polling 24 inputs to populate entities nobody enabled would cost a round trip each, every
        cycle. Disabled entities are never added, so they never register.
        """
        await super().async_added_to_hass()
        if self.registers_input_polling():
            self.async_on_remove(self.coordinator.request_input_polling())
