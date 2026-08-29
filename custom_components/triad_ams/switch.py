"""Per-output loudness and mono-sum, plus the matrix's 12 V trigger banks.

All disabled by default, like every platform beyond `media_player`.

Loudness and mono come free with the DSP read an output already does for its tone and EQ, so
enabling them costs nothing beyond what the EQ entities already pay. Triggers are matrix-wide and
tiered separately -- a handful of reads, but an integration whose trigger switches are all
disabled should put nothing extra on the wire.
"""

from __future__ import annotations

from collections.abc import Awaitable

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .ams.errors import TriadError
from .ams.settings import EntrySettings
from .coordinator import TriadCoordinator
from .entity import TriadEntity, TriadOutputDspEntity

#: Outputs covered by each 12 V trigger bank, for labelling.
OUTPUTS_PER_BANK = 8


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Loudness and mono per output; trigger banks per matrix."""
    coordinator = entry.runtime_data
    settings = EntrySettings.resolve(entry.data, entry.options)

    entities: list[SwitchEntity] = []
    for output in settings.active_outputs:
        entities.append(TriadLoudnessSwitch(coordinator, entry, output))
        entities.append(TriadMonoSwitch(coordinator, entry, output))

    # Only the banks this model actually has. An 8x8 has one; asking it about bank 2 is a
    # Command error, and offering a switch for it would be offering a control that cannot work.
    for bank in range(1, settings.spec.trigger_banks + 1):
        entities.append(TriadTriggerBankSwitch(coordinator, entry, bank))
    entities.append(TriadAsgTriggerSwitch(coordinator, entry))
    entities.append(TriadAudioSenseSwitch(coordinator, entry))

    async_add_entities(entities)


class TriadDspSwitch(TriadOutputDspEntity, SwitchEntity):
    """Shared write path for the per-output switches: send, then re-read that output's DSP."""

    _attr_entity_registry_enabled_default = False

    async def _apply(self, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except TriadError as err:
            msg = f"command failed for output {self._output}: {err}"
            raise HomeAssistantError(msg) from err
        await self.coordinator.async_refresh_output_dsp(self._output)


class TriadLoudnessSwitch(TriadDspSwitch):
    """Loudness compensation on one output."""

    _attr_name = "Loudness"

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, output: int) -> None:
        super().__init__(coordinator, entry, output, "loudness")

    @property
    def is_on(self) -> bool | None:
        dsp = self.dsp
        return dsp.loudness if dsp else None

    async def async_turn_on(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_loudness(self._output, on=True))

    async def async_turn_off(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_loudness(self._output, on=False))


class TriadMonoSwitch(TriadDspSwitch):
    """Mono-summing on one output.

    Worth knowing what this does in practice: the vendor driver forces it on for the slave of a
    2.1 pair, so a zone reading `on` here may be half of a pairing configured elsewhere rather
    than a choice made in Home Assistant.
    """

    _attr_name = "Mono sum"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, output: int) -> None:
        super().__init__(coordinator, entry, output, "mono")

    @property
    def is_on(self) -> bool | None:
        dsp = self.dsp
        return dsp.mono if dsp else None

    async def async_turn_on(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_mono(self._output, mono=True))

    async def async_turn_off(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_mono(self._output, mono=False))


class TriadTriggerSwitchBase(TriadEntity, SwitchEntity):
    """Shared behaviour for the matrix-wide trigger switches."""

    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG

    _key: str

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.request_trigger_polling())

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.triggers.get(self._key) if data else None

    @property
    def available(self) -> bool:
        """Unavailable until the trigger has actually been read, rather than assuming off."""
        return super().available and self.is_on is not None

    async def _apply(self, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except TriadError as err:
            msg = f"trigger command failed: {err}"
            raise HomeAssistantError(msg) from err
        await self.coordinator.async_refresh_triggers()


class TriadTriggerBankSwitch(TriadTriggerSwitchBase):
    """One 12 V trigger bank, covering eight outputs."""

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry, bank: int) -> None:
        super().__init__(coordinator, entry)
        self._bank = bank
        self._key = str(bank)
        first = (bank - 1) * OUTPUTS_PER_BANK + 1
        self._attr_name = f"Trigger outputs {first}-{first + OUTPUTS_PER_BANK - 1}"
        self._attr_unique_id = f"{entry.entry_id}_trigger_bank_{bank}"

    async def async_turn_on(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_trigger_bank(self._bank, on=True))

    async def async_turn_off(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_trigger_bank(self._bank, on=False))


class TriadAsgTriggerSwitch(TriadTriggerSwitchBase):
    """The ASG trigger.

    Its wire index is model-dependent: it sits after the last output bank, so on an 8x8 it lands
    exactly where a 24x24 keeps its 9-16 bank. Addressing it without knowing the model toggles the
    wrong bank on a 24x24 and reports success -- which is why the index comes from `MatrixSpec`
    rather than a literal anywhere in this file.
    """

    _attr_name = "ASG trigger"
    _key = "asg"

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_trigger_asg"

    async def async_turn_on(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_trigger_asg(on=True))

    async def async_turn_off(self, **_: object) -> None:
        await self._apply(self.coordinator.client.set_trigger_asg(on=False))


class TriadAudioSenseSwitch(TriadEntity, SwitchEntity):
    """Whether this matrix measures audio sense at all. Matrix-wide, not per input.

    Replaces the read-only binary sensor that used to report this. A control that both shows the
    state and changes it is strictly better than one that only shows it, and having both would put
    the same value in two places.

    **This was withheld until the vendor was on its way out (FR-14).** The driver re-asserts its own
    value on every reconnect, so under coexistence this switch would have appeared to work and
    silently reverted -- worse than no switch. With a single writer the device value is durable.

    Enabling is answered by a burst of roughly one frame per input, which the client drains; that
    is why the write costs about half a second and why nothing here parses a reply.
    """

    _attr_entity_registry_enabled_default = False
    _attr_entity_category = EntityCategory.CONFIG
    _attr_name = "Audio sense"

    def __init__(self, coordinator: TriadCoordinator, entry: TriadConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_audio_sense_enabled"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # The settings tier, not the input tier: this needs one matrix-wide read, where input
        # polling would cost one per input to learn the same flag.
        self.async_on_remove(self.coordinator.request_audio_sense_settings())

    @property
    def available(self) -> bool:
        """Unavailable until actually read, rather than defaulting to off.

        Off is a meaningful state here -- it is what every matrix in the measured units
        ships as -- so showing it before it has been read would be asserting the common case
        rather than reporting one.
        """
        return super().available and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        return data.audio_sense_enabled if data else None

    async def _apply(self, *, enabled: bool) -> None:
        try:
            await self.coordinator.client.set_audio_sense_enabled(enabled=enabled)
        except TriadError as err:
            msg = f"could not change audio sense: {err}"
            raise HomeAssistantError(msg) from err
        await self.coordinator.async_refresh_sense_settings()

    async def async_turn_on(self, **_: object) -> None:
        await self._apply(enabled=True)

    async def async_turn_off(self, **_: object) -> None:
        await self._apply(enabled=False)
