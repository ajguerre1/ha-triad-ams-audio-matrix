"""Per-matrix diagnostic sensors: firmware, connection health, and the audio-sense timeout.

All diagnostic, all disabled by default. These answer "why is this matrix behaving oddly", which
is a question worth having a home for and not worth 3 extra entities per matrix by default.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .coordinator import TriadCoordinator
from .entity import TriadEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One set of diagnostics per matrix."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            TriadFirmwareSensor(coordinator, entry),
            TriadAudioSenseDelaySensor(coordinator, entry),
        ]
    )


class TriadDiagnosticSensor(TriadEntity, SensorEntity):
    """Shared shape: diagnostic category, off by default."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, key: str) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_{key}"


class TriadFirmwareSensor(TriadDiagnosticSensor):
    """The matrix's firmware version.

    Worth surfacing because behaviour genuinely differs between revisions: the AMS8 on V1.05.74
    pads error frames to 150 bytes, and knowing which unit is on which build is the first question
    when one matrix misbehaves and its siblings do not.
    """

    _attr_name = "Firmware"

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry) -> None:
        super().__init__(coordinator, entry, "firmware")

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.firmware if data else None


class TriadAudioSenseDelaySensor(TriadDiagnosticSensor):
    """Minutes of silence before the matrix sleeps an analog input.

    Reported in minutes because that is the device's unit -- it answers ``0x1`` for the one-minute
    default. The Control4 driver initialises the same field to 30, which on this scale is half an
    hour; surfacing the real value is the cheapest way to notice that.
    """

    _attr_name = "Audio sense off delay"
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry) -> None:
        super().__init__(coordinator, entry, "audio_sense_off_delay")

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        return data.audio_sense_off_delay if data else None
