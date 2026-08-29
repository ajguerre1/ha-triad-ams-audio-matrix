"""Audio-sense binary sensors: one per input, plus a per-matrix diagnostic.

Everything here is disabled by default. A 24-input matrix would otherwise add 25 entities that,
in an installation with audio sense switched off, could never report anything but "no reading".
"""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .ams.settings import EntrySettings
from .coordinator import TriadCoordinator
from .entity import TriadEntity, TriadInputEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One audio-sense sensor per active input, plus the matrix-wide diagnostic."""
    coordinator = entry.runtime_data
    settings = EntrySettings.resolve(entry.data, entry.options)

    entities: list[BinarySensorEntity] = [
        TriadAudioSenseSensor(coordinator, entry, source) for source in settings.active_inputs
    ]
    entities.append(TriadAudioSenseEnabledSensor(coordinator, entry))
    async_add_entities(entities)


class TriadAudioSenseSensor(TriadInputEntity, BinarySensorEntity):
    """Whether the matrix detects audio on one input."""

    _attr_device_class = BinarySensorDeviceClass.SOUND
    # Off by default: see the module docstring.
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, source: int) -> None:
        super().__init__(coordinator, entry, source)
        self._attr_name = f"Input {source} audio"

    def registers_input_polling(self) -> bool:
        """This entity is the reason inputs get polled at all."""
        return True

    @property
    def _reading(self) -> bool | None:
        data = self.coordinator.data
        return data.audio_sense.get(self._source) if data else None

    @property
    def available(self) -> bool:
        """Unavailable when the matrix is not measuring, which is a real and common state.

        The device reports the same value for "no audio" and "audio sense is switched off", so
        reporting ``off`` in the second case would assert something the hardware has not
        determined -- an input carrying music reads identically to a dead one. Unavailable says
        "no reading", which is both true and actionable: it prompts the question that leads to the
        setting, where a confident ``off`` would not.
        """
        return super().available and self._reading is not None

    @property
    def is_on(self) -> bool | None:
        return self._reading


class TriadAudioSenseEnabledSensor(TriadEntity, BinarySensorEntity):
    """Whether this matrix measures audio sense at all.

    Exists to answer the question the per-input sensors provoke. When audio sense is off, all 24
    of them go unavailable at once, and without this the user is left guessing why.

    There is deliberately no switch to change it. Enabling returns a burst of roughly one frame
    per input, and the Control4 driver re-asserts its own value on every sync -- so a control here
    would appear to work and silently revert. The durable setting lives in that driver.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_name = "Audio sense enabled"

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_audio_sense_enabled"

    def registers_input_polling(self) -> bool:
        return True

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.request_input_polling())

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.audio_sense_enabled if data else None
