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
from dataclasses import dataclass
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ams.client import AmsClient
from .ams.errors import CommandError, ParseError, TransportError

_LOGGER = logging.getLogger(__name__)

#: How long to wait for the socket to close during unload. Home Assistant blocks on this.
SHUTDOWN_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class MatrixSnapshot:
    """What one poll learned, for the whole matrix."""

    outputs: dict[int, OutputSnapshot]
    #: Per-input audio sense. ``None`` for an input means the matrix is not measuring it.
    audio_sense: dict[int, bool | None]
    #: Whether the matrix measures audio sense at all. ``None`` until first read.
    audio_sense_enabled: bool | None = None


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

    def request_input_polling(self) -> callable:
        """Register a need for per-input data; returns an unsubscribe.

        Called from an entity's ``async_added_to_hass``. Reference-counted so the last entity
        being removed also stops the polling it caused.
        """
        self._input_consumers += 1

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
        return MatrixSnapshot(outputs=snapshots, audio_sense=sense, audio_sense_enabled=enabled)

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
        self.async_set_updated_data(
            MatrixSnapshot(
                outputs={**current.outputs, output: snapshot},
                audio_sense=current.audio_sense,
                audio_sense_enabled=current.audio_sense_enabled,
            )
        )

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
