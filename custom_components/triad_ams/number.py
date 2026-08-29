"""Per-output tone and EQ gain.

All disabled by default. A 24-output matrix would otherwise add 240 sliders on first setup.

Each output contributes ten: bass, treble, balance, max volume, turn-on volume, and one gain per
EQ band. Band **frequency** is a `select`, not a number -- see `select.py` for why. Band **Q** is
not settable at all; it rides along as an attribute on the band's gain entity, which keeps the
whole band visible in one place without adding five more entities per output.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import PERCENTAGE, UnitOfSoundPressure
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .ams.errors import TriadError
from .ams.settings import EntrySettings
from .ams.volume import MAX_STEP
from .coordinator import OutputDsp, TriadCoordinator
from .entity import TriadOutputDspEntity

#: Tone controls share one encoding and therefore one range: -12..+12 dB in half-steps.
TONE_MIN, TONE_MAX, TONE_STEP = -12.0, 12.0, 0.5

EQ_BANDS = (1, 2, 3, 4, 5)


@dataclass(frozen=True, kw_only=True)
class TriadNumberSpec:
    """One slider, described once instead of one class per control."""

    key: str
    name: str
    minimum: float
    maximum: float
    step: float
    unit: str | None
    read: Callable[[OutputDsp], float]
    write: Callable[[TriadCoordinator, int, float], Awaitable[None]]
    category: EntityCategory | None = None


def _tone(key: str, name: str, read, write) -> TriadNumberSpec:
    return TriadNumberSpec(
        key=key,
        name=name,
        minimum=TONE_MIN,
        maximum=TONE_MAX,
        step=TONE_STEP,
        unit=UnitOfSoundPressure.DECIBEL,
        read=read,
        write=write,
    )


TONE_SPECS: tuple[TriadNumberSpec, ...] = (
    _tone(
        "bass",
        "Bass",
        lambda dsp: dsp.bass_db,
        lambda c, out, v: c.client.set_bass(out, v),
    ),
    _tone(
        "treble",
        "Treble",
        lambda dsp: dsp.treble_db,
        lambda c, out, v: c.client.set_treble(out, v),
    ),
    _tone(
        "balance",
        "Balance",
        lambda dsp: dsp.balance_db,
        lambda c, out, v: c.client.set_balance(out, v),
    ),
    TriadNumberSpec(
        key="turn_on_volume",
        name="Turn-on volume",
        minimum=0,
        maximum=MAX_STEP,
        step=1,
        unit=PERCENTAGE,
        read=lambda dsp: float(dsp.turn_on_step),
        write=lambda c, out, v: c.client.set_turn_on_volume_step(out, round(v)),
        category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Tone and EQ gain for every active output."""
    coordinator = entry.runtime_data
    settings = EntrySettings.resolve(entry.data, entry.options)

    entities: list[NumberEntity] = []
    for output in settings.active_outputs:
        entities.extend(TriadToneNumber(coordinator, entry, output, spec) for spec in TONE_SPECS)
        entities.extend(TriadEqGainNumber(coordinator, entry, output, band) for band in EQ_BANDS)
    async_add_entities(entities)


class TriadNumberBase(TriadOutputDspEntity, NumberEntity):
    """Shared write path: send, then re-read this output's DSP."""

    _attr_entity_registry_enabled_default = False
    _attr_mode = NumberMode.BOX

    async def _apply(self, awaitable: Awaitable[None]) -> None:
        try:
            await awaitable
        except TriadError as err:
            msg = f"command failed for output {self._output}: {err}"
            raise HomeAssistantError(msg) from err
        await self.coordinator.async_refresh_output_dsp(self._output)


class TriadToneNumber(TriadNumberBase):
    """Bass, treble, balance, or turn-on volume."""

    def __init__(
        self,
        coordinator: TriadCoordinator,
        entry: TriadConfigEntry,
        output: int,
        spec: TriadNumberSpec,
    ) -> None:
        super().__init__(coordinator, entry, output, spec.key)
        self._spec = spec
        self._attr_name = spec.name
        self._attr_native_min_value = spec.minimum
        self._attr_native_max_value = spec.maximum
        self._attr_native_step = spec.step
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_entity_category = spec.category

    @property
    def native_value(self) -> float | None:
        dsp = self.dsp
        return self._spec.read(dsp) if dsp else None

    async def async_set_native_value(self, value: float) -> None:
        await self._apply(self._spec.write(self.coordinator, self._output, value))


class TriadEqGainNumber(TriadNumberBase):
    """The gain of one EQ band.

    Gain is the parameter people actually reach for, so it is the entity that represents the band.
    Its centre frequency and Q ride along as attributes, which keeps the whole band legible in one
    place -- five more entities per output to show two read-only numbers would not be a kindness.
    """

    _attr_native_min_value = TONE_MIN
    _attr_native_max_value = TONE_MAX
    _attr_native_step = TONE_STEP
    _attr_native_unit_of_measurement = UnitOfSoundPressure.DECIBEL

    def __init__(
        self,
        coordinator: TriadCoordinator,
        entry: TriadConfigEntry,
        output: int,
        band: int,
    ) -> None:
        super().__init__(coordinator, entry, output, f"eq_band_{band}_gain")
        self._band = band
        self._attr_name = f"EQ band {band} gain"

    @property
    def native_value(self) -> float | None:
        band = self.band(self._band)
        return band.gain_db if band else None

    @property
    def extra_state_attributes(self) -> dict[str, float] | None:
        """Frequency and Q, so the band reads as a band rather than a bare number.

        Q is here rather than as its own entity because it is not settable: the device takes a raw
        index for it and the index-to-value table has not been measured. Showing it read-only is
        honest; a slider that set the wrong Q would not be.
        """
        band = self.band(self._band)
        if band is None:
            return None
        return {"frequency_hz": band.frequency_hz, "q": band.q}

    async def async_set_native_value(self, value: float) -> None:
        await self._apply(self.coordinator.client.set_eq_gain(self._output, self._band, value))
