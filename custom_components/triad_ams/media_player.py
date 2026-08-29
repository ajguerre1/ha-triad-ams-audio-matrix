"""One media player per active output."""

from __future__ import annotations

import logging
from collections.abc import Awaitable

import voluptuous as vol
from homeassistant.components.media_player import (
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import TriadConfigEntry
from .ams.errors import TriadError
from .ams.presets import PRESET_NAMES, bands_for
from .ams.settings import EntrySettings
from .ams.volume import MAX_STEP
from .coordinator import TriadCoordinator
from .entity import TriadOutputEntity

_LOGGER = logging.getLogger(__name__)

#: Shown for an output with no source routed.
SOURCE_OFF = "Off"

SERVICE_APPLY_EQ_PRESET = "apply_eq_preset"
ATTR_PRESET = "preset"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TriadConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create one entity per active output."""
    coordinator = entry.runtime_data
    settings = EntrySettings.resolve(entry.data, entry.options)
    sources = {number: f"Input {number}" for number in settings.active_inputs}

    async_add_entities(
        TriadOutputMediaPlayer(
            coordinator,
            entry,
            output,
            sources=sources,
            max_volume_percent=settings.max_volume(output),
        )
        for output in settings.active_outputs
    )

    # Hosted here rather than on the EQ entities, and that is the point: every DSP entity is
    # disabled by default, so a service registered on one would be unreachable in the default
    # configuration. `media_player` is the only entity an output is guaranteed to have.
    entity_platform.async_get_current_platform().async_register_entity_service(
        SERVICE_APPLY_EQ_PRESET,
        {vol.Required(ATTR_PRESET): vol.In(PRESET_NAMES)},
        "async_apply_eq_preset",
    )


class TriadOutputMediaPlayer(TriadOutputEntity, MediaPlayerEntity):
    """An output zone: what it is routed to, how loud, and whether it is muted."""

    _attr_device_class = MediaPlayerDeviceClass.SPEAKER
    _attr_supported_features = (
        MediaPlayerEntityFeature.SELECT_SOURCE
        | MediaPlayerEntityFeature.VOLUME_SET
        | MediaPlayerEntityFeature.VOLUME_STEP
        | MediaPlayerEntityFeature.VOLUME_MUTE
        | MediaPlayerEntityFeature.TURN_ON
        | MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(
        self,
        coordinator: TriadCoordinator,
        entry: TriadConfigEntry,
        output: int,
        *,
        sources: dict[int, str],
        max_volume_percent: int = MAX_STEP,
    ) -> None:
        super().__init__(coordinator, entry, output)
        self._attr_name = f"Output {output}"
        self._sources = sources
        self._max_step = max(1, min(max_volume_percent, MAX_STEP))
        # Remembered so turning the zone back on restores what it was listening to. The device
        # cannot answer this: an unrouted output reports 'Audio Off' and nothing more.
        self._last_source: int | None = None

    # -- state ---------------------------------------------------------------------------------

    @property
    def state(self) -> MediaPlayerState | None:
        snapshot = self.snapshot
        if snapshot is None:
            return None
        return MediaPlayerState.ON if snapshot.is_on else MediaPlayerState.OFF

    @property
    def volume_level(self) -> float | None:
        snapshot = self.snapshot
        return None if snapshot is None else snapshot.volume_step / MAX_STEP

    @property
    def is_volume_muted(self) -> bool | None:
        snapshot = self.snapshot
        return None if snapshot is None else snapshot.muted

    @property
    def source(self) -> str | None:
        snapshot = self.snapshot
        if snapshot is None:
            return None
        if snapshot.source is None:
            return SOURCE_OFF
        # An input the user excluded can still be routed -- by another controller, or from before
        # it was excluded. Reporting the number beats reporting None and looking like a fault.
        return self._sources.get(snapshot.source, f"Input {snapshot.source}")

    @property
    def source_list(self) -> list[str]:
        return [SOURCE_OFF, *(self._sources[i] for i in sorted(self._sources))]

    # -- commands ------------------------------------------------------------------------------

    async def async_select_source(self, source: str) -> None:
        if source == SOURCE_OFF:
            await self.async_turn_off()
            return
        for number, name in self._sources.items():
            if name == source:
                await self._route(number)
                self._last_source = number
                return
        msg = f"{source!r} is not one of this matrix's inputs"
        raise HomeAssistantError(msg)

    async def async_turn_on(self) -> None:
        """Restore the last known source.

        With nothing remembered, input 1 is the least surprising choice: the alternative is a
        control that appears to do nothing, since 'on' has no meaning to this hardware beyond
        being routed to something.
        """
        target = self._last_source or next(iter(sorted(self._sources)), None)
        if target is None:  # pragma: no cover - unreachable by construction, see below
            # `EntrySettings._active` treats an empty selection as "not chosen yet" and returns
            # every channel, so `self._sources` is never empty and this cannot fire today. Kept
            # rather than deleted because removing it makes the fallback a silent disconnect
            # (`set_route(output, None)`) if that rule ever changes, and marked rather than left
            # uncovered so the coverage report is not quietly carrying an untested branch.
            msg = "no inputs are enabled for this matrix"
            raise HomeAssistantError(msg)
        await self._route(target)

    async def async_turn_off(self) -> None:
        """Disconnect the output. This is routing, not mains power.

        The matrix's own power-off is never sent -- its power-on delay is long enough that the
        Control4 driver disables the command outright, and a zone that takes tens of seconds to
        come back is not an off switch anyone wants on a dashboard.
        """
        if (snapshot := self.snapshot) is not None and snapshot.source is not None:
            self._last_source = snapshot.source
        await self._route(None)

    async def _route(self, source: int | None) -> None:
        """Route through the coordinator, which coalesces repeats. ``None`` disconnects."""
        try:
            await self.coordinator.async_set_route(self._output, source)
        except TriadError as err:
            msg = f"command failed for output {self._output}: {err}"
            raise HomeAssistantError(msg) from err

    async def async_set_volume_level(self, volume: float) -> None:
        step = round(max(0.0, min(volume, 1.0)) * MAX_STEP)
        if step > self._max_step:
            _LOGGER.debug("output %s capped from step %s to %s", self._output, step, self._max_step)
            step = self._max_step
        await self._set_volume(step)

    async def _set_volume(self, step: int) -> None:
        """Volume goes through the coordinator, which owns the turn-on-volume follow-up.

        Not through :meth:`_command`: ``async_set_volume`` already re-reads the output, and
        routing it through the generic helper would refresh twice for one change.
        """
        try:
            await self.coordinator.async_set_volume(self._output, step)
        except TriadError as err:
            msg = f"command failed for output {self._output}: {err}"
            raise HomeAssistantError(msg) from err

    async def async_volume_up(self) -> None:
        await self._step_volume(+1)

    async def async_volume_down(self) -> None:
        await self._step_volume(-1)

    async def _step_volume(self, direction: int) -> None:
        """Step from the last reading rather than using the device's own step command.

        The device's step opcode does not respect the configured cap, so a zone could be nudged
        past it one press at a time.
        """
        snapshot = self.snapshot
        if snapshot is None:
            return
        step = max(0, min(snapshot.volume_step + direction, self._max_step))
        await self._set_volume(step)

    async def async_mute_volume(self, mute: bool) -> None:
        await self._command(self.coordinator.client.set_mute(self._output, mute=mute))

    async def async_apply_eq_preset(self, preset: str) -> None:
        """Write all five bands of a preset to this output, then re-read once.

        A preset is an **action, not a state**. The device stores band values and has no notion of
        a preset identifier, so there is nothing to query afterwards and no entity here pretending
        otherwise -- "which preset is this zone on" is a question the hardware cannot answer.

        Each band takes three writes, so this is fifteen commands. They are sent in band order and
        the DSP is re-read once at the end rather than after each, which is both faster and avoids
        publishing four intermediate curves nobody asked for.
        """
        bands = bands_for(preset)
        client = self.coordinator.client
        try:
            for number, band in enumerate(bands, start=1):
                await client.set_eq_frequency(self._output, number, band.frequency_hz)
                await client.set_eq_gain(self._output, number, band.gain_db)
                await client.set_eq_q(self._output, number, band.q)
        except TriadError as err:
            # Partial application is possible and worth saying so: some bands may already have
            # moved, and the re-read below is what shows which.
            msg = f"applying {preset!r} to output {self._output} failed part-way: {err}"
            raise HomeAssistantError(msg) from err
        finally:
            await self.coordinator.async_refresh_output_dsp(self._output)

    async def _command(self, awaitable: Awaitable[None]) -> None:
        """Run a device command, then re-read this output only.

        Re-reading is not belt and braces: the device may cap the volume itself, and another
        controller on the LAN may have changed the same zone moments earlier. What the device
        reports afterwards is the truth; what was asked for is not.
        """
        try:
            await awaitable
        except TriadError as err:
            msg = f"command failed for output {self._output}: {err}"
            raise HomeAssistantError(msg) from err
        await self.coordinator.async_refresh_output(self._output)
