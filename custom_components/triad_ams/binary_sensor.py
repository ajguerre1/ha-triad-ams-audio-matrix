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
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .ams.settings import EntrySettings
from .coordinator import TriadCoordinator
from .entity import TriadInputEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One audio-sense sensor per active input, plus the matrix-wide diagnostic."""
    coordinator = entry.runtime_data
    settings = EntrySettings.resolve(entry.data, entry.options)

    async_add_entities(
        TriadAudioSenseSensor(coordinator, entry, source) for source in settings.active_inputs
    )


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


# ``TriadAudioSenseEnabledSensor`` lived here until 2026-08-29, reporting whether the matrix was
# measuring. FR-14 replaced it with a `switch`, which shows the same state and can change it --
# keeping both would have put one value in two entities, and the read-only one is strictly the
# weaker of the pair. See `switch.TriadAudioSenseSwitch`.
