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


class TriadCoordinator(DataUpdateCoordinator[dict[int, OutputSnapshot]]):
    """Reads the active outputs of one matrix on an interval."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AmsClient,
        *,
        active_outputs: list[int],
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

    async def _async_update_data(self) -> dict[int, OutputSnapshot]:
        previous = self.data or {}
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
        return snapshots

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
        self.async_set_updated_data({**(self.data or {}), output: snapshot})

    async def async_shutdown(self) -> None:
        await super().async_shutdown()
        with asyncio.timeout(5):
            await self.client.disconnect()
