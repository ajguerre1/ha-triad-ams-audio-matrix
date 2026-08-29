"""Per-matrix diagnostic sensors: firmware, connection health, and the audio-sense timeout.

All diagnostic, all disabled by default. These answer "why is this matrix behaving oddly", which
is a question worth having a home for and not worth 3 extra entities per matrix by default.
"""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .ams.settings import EntrySettings
from .coordinator import TriadCoordinator
from .entity import TriadEntity, TriadOutputDspEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One set of diagnostics per matrix, plus turn-on volume when the integration owns it."""
    coordinator = entry.runtime_data
    settings = EntrySettings.resolve(entry.data, entry.options)

    entities: list[SensorEntity] = [
        TriadFirmwareSensor(coordinator, entry),
        TriadAddressingSensor(coordinator, entry),
    ]
    # Only when tracking is on. Otherwise `number.py` offers a writable one, and having both would
    # put the same value in two entities with only one of them able to change it.
    if settings.track_turn_on_volume:
        entities.extend(
            TriadTurnOnVolumeSensor(coordinator, entry, output)
            for output in settings.active_outputs
        )
    async_add_entities(entities)


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


# ``TriadAudioSenseDelaySensor`` lived here until 2026-08-29, reporting the off delay read-only.
# FR-14 made the value settable, so it became a `number` -- see `number.TriadAudioSenseOffDelay`.
# A read-only sensor beside a writable number would be the same value in two places, and the
# sensor is the weaker of the pair.


class TriadAddressingSensor(TriadDiagnosticSensor):
    """Whether the matrix takes its address from DHCP or holds a static one.

    **Not the address itself.** The vendor driver calls this command ``getIpAddress``, but
    measured on two units across two firmware revisions it answers the literal ``dynamic_ip`` --
    the mode, with no address in it. The entity is named for what the hardware returns.

    Worth having because a unit that has quietly been switched to a static address is a plausible
    cause of "it worked until the router was replaced", and nothing else here would show it.
    """

    _attr_name = "Addressing"

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry) -> None:
        super().__init__(coordinator, entry, "ip_mode")

    @property
    def native_value(self) -> str | None:
        data = self.coordinator.data
        return data.ip_mode if data else None


class TriadTurnOnVolumeSensor(TriadOutputDspEntity, SensorEntity):
    """The volume a zone will come back at, while the integration is the one maintaining it.

    Exists only when ``track_turn_on_volume`` is on. In that mode the integration rewrites this
    register whenever a zone's volume settles, so offering it as something the user can type into
    would be offering a value that changes back -- which is exactly the confusion the vendor causes
    today, and the thing FR-12 is meant to end rather than reproduce.

    Turn tracking off and this disappears, replaced by a writable `number`.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_name = "Turn-on volume"

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, output: int) -> None:
        super().__init__(coordinator, entry, output, "turn_on_volume")

    @property
    def native_value(self) -> int | None:
        dsp = self.dsp
        return dsp.turn_on_step if dsp else None
