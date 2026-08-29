"""EQ band centre frequencies.

A `select` rather than a `number`, deliberately. The device does not take a frequency -- it takes
an index into a fixed 31-entry table of ISO 1/3-octave centres. Exposing that as a slider from 0
to 30 would show the user an index, which means nothing to anyone tuning a room; exposing it as a
free-form Hz value would imply a precision the hardware does not have and would silently snap to
the nearest entry.

A select of real labels -- ``63 Hz``, ``1.6 kHz`` -- shows exactly the choices that exist. It is
the rare case where the constrained control is also the friendlier one.
"""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .ams.eq import EQ_FREQUENCIES, format_frequency, parse_frequency_text
from .ams.errors import TriadError
from .ams.settings import EntrySettings
from .coordinator import TriadCoordinator
from .entity import TriadOutputDspEntity

EQ_BANDS = (1, 2, 3, 4, 5)

#: Labels in table order, so the picker reads low-to-high like every EQ ever built.
FREQUENCY_OPTIONS: list[str] = [format_frequency(hz) for hz in EQ_FREQUENCIES]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """One frequency selector per EQ band per active output."""
    coordinator = entry.runtime_data
    settings = EntrySettings.resolve(entry.data, entry.options)
    async_add_entities(
        TriadEqFrequencySelect(coordinator, entry, output, band)
        for output in settings.active_outputs
        for band in EQ_BANDS
    )


class TriadEqFrequencySelect(TriadOutputDspEntity, SelectEntity):
    """The centre frequency of one EQ band."""

    _attr_entity_registry_enabled_default = False
    _attr_options = FREQUENCY_OPTIONS

    def __init__(
        self,
        coordinator: TriadCoordinator,
        entry: TriadConfigEntry,
        output: int,
        band: int,
    ) -> None:
        super().__init__(coordinator, entry, output, f"eq_band_{band}_frequency")
        self._band = band
        self._attr_name = f"EQ band {band} frequency"

    @property
    def current_option(self) -> str | None:
        """Label the frequency the device reported.

        Formatted from the reading rather than looked up by index, so a device answering with a
        frequency outside the table still shows what it actually said instead of nothing.
        """
        band = self.band(self._band)
        return format_frequency(band.frequency_hz) if band else None

    async def async_select_option(self, option: str) -> None:
        try:
            hz = parse_frequency_text(option)
        except ValueError as err:
            msg = f"{option!r} is not a frequency this matrix offers"
            raise HomeAssistantError(msg) from err
        try:
            await self.coordinator.client.set_eq_frequency(self._output, self._band, hz)
        except TriadError as err:
            msg = f"command failed for output {self._output}: {err}"
            raise HomeAssistantError(msg) from err
        await self.coordinator.async_refresh_output_dsp(self._output)
