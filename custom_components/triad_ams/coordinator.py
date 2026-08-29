"""Polling coordinator for one matrix.

Polling rather than pushing is forced by the hardware: the matrix announces nothing except
audio-sense events, and these matrices commonly share a LAN with a Control4 controller that
changes routing and volume independently. Home Assistant finds out on the next poll.

Two decisions worth stating:

* **A failed output does not fail the poll.** ``Command error`` is a per-command hiccup that real
  firmware emits on healthy sockets. One output answering badly keeps its previous reading; only
  a transport failure marks the whole matrix unavailable.
* **Writes refresh just their own output.** A full refresh after every command would multiply
  traffic by the output count and widen the window in which another controller's change is read
  back over the one just made.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ams.client import AmsClient
from .ams.errors import CommandError, ParseError, TransportError

_LOGGER = logging.getLogger(__name__)

#: How long to wait for the socket to close during unload. Home Assistant blocks on this.
SHUTDOWN_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class BandState:
    """One parametric EQ band."""

    frequency_hz: float
    gain_db: float
    q: float


@dataclass(frozen=True, slots=True)
class OutputDsp:
    """The tone and EQ settings of one output.

    Read only for outputs whose DSP entities are enabled -- twenty round trips per output, and a
    24-output matrix would otherwise spend 480 of them per cycle populating entities nobody
    turned on.
    """

    bass_db: float
    treble_db: float
    balance_db: float
    turn_on_step: int
    loudness: bool
    mono: bool
    bands: tuple[BandState, ...]


@dataclass(frozen=True, slots=True)
class MatrixSnapshot:
    """What one poll learned, for the whole matrix."""

    outputs: dict[int, OutputSnapshot]
    #: Per-input audio sense. ``None`` for an input means the matrix is not measuring it.
    audio_sense: dict[int, bool | None]
    #: Whether the matrix measures audio sense at all. ``None`` until first read.
    audio_sense_enabled: bool | None = None
    #: Firmware version, read once per connection. Behaviour differs between revisions.
    firmware: str | None = None
    #: Minutes of silence before an analog input sleeps. Device unit is minutes.
    audio_sense_off_delay: int | None = None
    #: Tone and EQ, only for outputs whose DSP entities are enabled.
    dsp: dict[int, OutputDsp] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class OutputSnapshot:
    """What one poll learned about one output."""

    source: int | None
    volume_step: int
    muted: bool

    @property
    def is_on(self) -> bool:
        """An output with no source is off. There is no separate power state per output."""
        return self.source is not None


class TriadCoordinator(DataUpdateCoordinator[MatrixSnapshot]):
    """Reads one matrix on an interval.

    Outputs are always polled. Inputs are polled only when something is listening -- see
    :meth:`request_input_polling`. A 24-input matrix with no audio-sense entity enabled would
    otherwise spend 24 extra round trips per cycle populating nothing.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        client: AmsClient,
        *,
        active_outputs: list[int],
        active_inputs: list[int] | None = None,
        scan_interval: int,
        name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.active_outputs = active_outputs
        self.active_inputs = active_inputs or []
        #: Entities that want input data register here. Disabled entities are never added to Home
        #: Assistant, so they never register, and an untouched platform costs nothing on the wire.
        self._input_consumers = 0
        #: Per-output DSP consumers, so one zone's EQ does not poll the whole matrix.
        self._dsp_consumers: dict[int, int] = {}

    def request_output_dsp(self, output: int) -> callable:
        """Register a need for one output's tone and EQ; returns an unsubscribe.

        Reference-counted **per output**, not globally. Enabling the EQ on one zone should cost
        one output's worth of reads, not the whole matrix's -- which is the difference between
        20 extra round trips per cycle and 480 on a 24-output unit.
        """
        self._dsp_consumers[output] = self._dsp_consumers.get(output, 0) + 1
        if self._dsp_consumers[output] == 1:
            # Entities are added after the first refresh, so without this the first poll skips
            # this output's DSP and nothing asks again until the next interval.
            self.hass.async_create_task(self.async_request_refresh())

        def _release() -> None:
            remaining = self._dsp_consumers.get(output, 0) - 1
            if remaining > 0:
                self._dsp_consumers[output] = remaining
            else:
                self._dsp_consumers.pop(output, None)

        return _release

    @property
    def dsp_outputs(self) -> list[int]:
        """Outputs with at least one enabled DSP entity."""
        return sorted(self._dsp_consumers)

    def request_input_polling(self) -> callable:
        """Register a need for per-input data; returns an unsubscribe.

        Called from an entity's ``async_added_to_hass``. Reference-counted so the last entity
        being removed also stops the polling it caused.

        The first registration also asks for a refresh, and that is not optional. Entities are
        added *after* the coordinator's first refresh, so without it the first poll skips inputs
        -- nobody was listening yet -- and nothing ever asks again. The entities would sit at
        ``unknown`` until the next scheduled interval, which is exactly what CI caught.
        """
        self._input_consumers += 1
        if self._input_consumers == 1:
            self.hass.async_create_task(self.async_request_refresh())

        def _release() -> None:
            self._input_consumers = max(0, self._input_consumers - 1)

        return _release

    @property
    def polls_inputs(self) -> bool:
        return self._input_consumers > 0

    async def _async_update_data(self) -> MatrixSnapshot:
        previous = (self.data.outputs if self.data else {}) or {}
        snapshots: dict[int, OutputSnapshot] = {}
        failures: list[int] = []

        for output in self.active_outputs:
            try:
                snapshots[output] = await self._read_output(output)
            except TransportError as err:
                # The socket is gone. Every remaining output would fail the same way, so stop and
                # let the coordinator mark the device unavailable rather than spend the timeout
                # 23 more times.
                msg = f"{self.name} is unreachable: {err}"
                raise UpdateFailed(msg) from err
            except (CommandError, ParseError) as err:
                failures.append(output)
                _LOGGER.debug("output %s did not answer cleanly: %s", output, err)
                if (stale := previous.get(output)) is not None:
                    snapshots[output] = stale

        if failures:
            _LOGGER.debug(
                "%s: %d of %d outputs kept their previous reading",
                self.name,
                len(failures),
                len(self.active_outputs),
            )
        sense, enabled = await self._read_audio_sense()
        firmware, delay = await self._read_static()
        return MatrixSnapshot(
            outputs=snapshots,
            audio_sense=sense,
            audio_sense_enabled=enabled,
            firmware=firmware,
            audio_sense_off_delay=delay,
            dsp=await self._read_dsp(),
        )

    async def _read_dsp(self) -> dict[int, OutputDsp]:
        """Read tone and EQ for the outputs that have a consumer, and only those."""
        readings: dict[int, OutputDsp] = {}
        previous = self.data.dsp if self.data else {}
        for output in self.dsp_outputs:
            try:
                readings[output] = await self._read_output_dsp(output)
            except (CommandError, ParseError) as err:
                _LOGGER.debug("output %s DSP did not answer cleanly: %s", output, err)
                if (stale := previous.get(output)) is not None:
                    readings[output] = stale
        return readings

    async def _read_output_dsp(self, output: int) -> OutputDsp:
        bands = [BandState(*(await self.client.get_eq_band(output, band))) for band in range(1, 6)]
        return OutputDsp(
            bass_db=await self.client.get_bass(output),
            treble_db=await self.client.get_treble(output),
            balance_db=await self.client.get_balance(output),
            turn_on_step=await self.client.get_turn_on_volume_step(output),
            loudness=await self.client.get_loudness(output),
            mono=await self.client.get_mono(output),
            bands=tuple(bands),
        )

    async def async_refresh_output_dsp(self, output: int) -> None:
        """Re-read one output's DSP after a write, without disturbing anything else."""
        try:
            dsp = await self._read_output_dsp(output)
        except (CommandError, ParseError, TransportError) as err:
            _LOGGER.debug("could not re-read output %s DSP: %s", output, err)
            return
        current = self.data
        if current is None:
            return
        self.async_set_updated_data(replace(current, dsp={**current.dsp, output: dsp}))

    async def _read_static(self) -> tuple[str | None, int | None]:
        """Read values that do not change while the session lasts, once.

        Re-reading firmware every 30 seconds would be two wasted round trips per cycle forever.
        Cached until a reconnect, which is also exactly when a firmware change could have happened.
        """
        if self.data and self.data.firmware is not None:
            return self.data.firmware, self.data.audio_sense_off_delay
        firmware = delay = None
        try:
            firmware = await self.client.firmware_version()
            delay = await self.client.get_audio_sense_off_delay()
        except (CommandError, ParseError) as err:
            _LOGGER.debug("%s: diagnostics did not answer cleanly: %s", self.name, err)
        return firmware, delay

    async def _read_audio_sense(self) -> tuple[dict[int, bool | None], bool | None]:
        """Read per-input audio sense, but only when an entity is consuming it.

        A failure here is never fatal: audio sense is a secondary signal, and losing it must not
        take a matrix's zones offline.
        """
        if not self.polls_inputs:
            return {}, (self.data.audio_sense_enabled if self.data else None)
        try:
            enabled = await self.client.get_audio_sense_enabled()
        except (CommandError, ParseError) as err:
            _LOGGER.debug("%s: could not read audio-sense enable: %s", self.name, err)
            enabled = None
        readings: dict[int, bool | None] = {}
        for source in self.active_inputs:
            try:
                readings[source] = await self.client.get_audio_sense(source)
            except (CommandError, ParseError) as err:
                _LOGGER.debug("input %s audio sense did not answer cleanly: %s", source, err)
                readings[source] = None
        return readings, enabled

    async def _read_output(self, output: int) -> OutputSnapshot:
        return OutputSnapshot(
            source=await self.client.get_route(output),
            volume_step=await self.client.get_volume_step(output),
            muted=await self.client.get_mute(output),
        )

    async def async_refresh_output(self, output: int) -> None:
        """Re-read one output and publish it, without disturbing the others.

        Called after a write so the UI reflects what the device actually did rather than what was
        asked for -- the two differ whenever a max-volume cap or another controller intervenes.
        """
        try:
            snapshot = await self._read_output(output)
        except (CommandError, ParseError, TransportError) as err:
            # Not fatal: the scheduled poll will pick this up. Failing here would surface a
            # transient read error as a failed user action that had in fact succeeded.
            _LOGGER.debug("could not re-read output %s after a command: %s", output, err)
            return
        current = self.data or MatrixSnapshot(outputs={}, audio_sense={})
        self.async_set_updated_data(replace(current, outputs={**current.outputs, output: snapshot}))

    async def async_shutdown(self) -> None:
        """Close the socket, but never let shutdown hang or raise.

        ``asyncio.timeout`` is an *async* context manager; ``with`` raises TypeError at runtime,
        which would make every unload and reload fail. Caught by the phase 7 design audit rather
        than by a test, because the Home Assistant layer has no test coverage yet -- task 23.

        A timeout here is not theoretical: ``disconnect`` awaits ``wait_closed``, and a socket to
        a matrix that has stopped answering can sit there. Home Assistant is waiting on this, so
        a slow close must not become a stuck reload.
        """
        await super().async_shutdown()
        try:
            async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                await self.client.disconnect()
        except (TimeoutError, OSError) as err:
            _LOGGER.debug("%s did not close cleanly: %s", self.name, err)
