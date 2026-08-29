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
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .ams.client import AmsClient
from .ams.errors import CommandError, ParseError, TransportError
from .repairs import async_check_audio_sense

_LOGGER = logging.getLogger(__name__)

#: How long to wait for the socket to close during unload. Home Assistant blocks on this.
SHUTDOWN_TIMEOUT = 5.0

#: Routing commands are coalesced over this window, matching the Control4 driver's own 250 ms.
#: Honest about its value: Home Assistant's `select_source` is a discrete choice, not a scroll, so
#: this is insurance against a looping automation rather than a fix for observed behaviour.
ROUTE_DEBOUNCE_SECONDS = 0.25

#: How long a volume must settle before it is stored as the zone's turn-on volume. Matches the
#: Control4 driver's own 10 s, which exists so dragging a slider does not write fifty times.
TURN_ON_VOLUME_DEBOUNCE_SECONDS = 10.0


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
    #: ``"dhcp"`` or ``"static"`` -- the addressing mode, not an address. Read with the firmware.
    ip_mode: str | None = None
    #: Minutes of silence before an analog input sleeps. Device unit is minutes.
    audio_sense_off_delay: int | None = None
    #: Tone and EQ, only for outputs whose DSP entities are enabled.
    dsp: dict[int, OutputDsp] = field(default_factory=dict)
    #: 12 V trigger banks by 1-based bank number, plus ``asg``. Only read when consumed.
    triggers: dict[str, bool] = field(default_factory=dict)
    #: Per-input gain in dB. Shares the input tier with audio sense.
    input_gains: dict[int, float] = field(default_factory=dict)


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
        entry_id: str = "",
        track_turn_on_volume: bool = True,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=name,
            update_interval=timedelta(seconds=scan_interval),
        )
        self.client = client
        self.entry_id = entry_id
        self.active_outputs = active_outputs
        self.active_inputs = active_inputs or []
        #: Entities that want input data register here. Disabled entities are never added to Home
        #: Assistant, so they never register, and an untouched platform costs nothing on the wire.
        self._input_consumers = 0
        #: Per-output DSP consumers, so one zone's EQ does not poll the whole matrix.
        self._dsp_consumers: dict[int, int] = {}
        self._trigger_consumers = 0
        #: Consumers of the matrix-wide audio-sense settings (enable + off delay).
        self._sense_settings_consumers = 0
        #: One debouncer per output, plus the routing each is waiting to apply.
        self._route_debouncers: dict[int, Debouncer] = {}
        self._pending_routes: dict[int, int | None] = {}
        self.track_turn_on_volume = track_turn_on_volume
        self._turn_on_debouncers: dict[int, Debouncer] = {}

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

    def request_trigger_polling(self) -> callable:
        """Register a need for trigger state; returns an unsubscribe.

        Only a handful of reads, but the same rule applies: an integration whose trigger switches
        are all disabled should put nothing extra on the wire.
        """
        self._trigger_consumers += 1
        if self._trigger_consumers == 1:
            self.hass.async_create_task(self.async_request_refresh())

        def _release() -> None:
            self._trigger_consumers = max(0, self._trigger_consumers - 1)

        return _release

    @property
    def polls_triggers(self) -> bool:
        return self._trigger_consumers > 0

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

    def request_audio_sense_settings(self) -> callable:
        """Register a need for the matrix-wide audio-sense settings; returns an unsubscribe.

        A tier of its own, and the distinction is the point: the enable flag and the off delay are
        **two reads for the whole matrix**, where input polling is **one read per input**. An
        entity that only needs to know whether sense is switched on should not put 24 extra round
        trips on the wire to find out, which is what riding the input tier would cost it.
        """
        self._sense_settings_consumers += 1
        if self._sense_settings_consumers == 1:
            self.hass.async_create_task(self.async_request_refresh())

        def _release() -> None:
            self._sense_settings_consumers = max(0, self._sense_settings_consumers - 1)

        return _release

    @property
    def polls_audio_sense_settings(self) -> bool:
        """Input consumers need these too -- the enable flag is what makes their readings mean
        anything, so anyone polling inputs is implicitly a consumer of the settings as well."""
        return self._sense_settings_consumers > 0 or self.polls_inputs

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
        sense = await self._read_audio_sense()
        enabled, delay = await self._read_sense_settings()
        firmware, ip_mode = await self._read_static()
        snapshot = MatrixSnapshot(
            outputs=snapshots,
            audio_sense=sense,
            audio_sense_enabled=enabled,
            firmware=firmware,
            ip_mode=ip_mode,
            audio_sense_off_delay=delay,
            dsp=await self._read_dsp(),
            triggers=await self._read_triggers(),
            input_gains=await self._read_input_gains(),
        )
        self._check_issues(snapshot)
        return snapshot

    def _check_issues(self, snapshot: MatrixSnapshot) -> None:
        """Raise or clear repair issues from what the poll just learned."""
        async_check_audio_sense(
            self.hass,
            self.entry_id,
            self.name or "",
            enabled=snapshot.audio_sense_enabled,
            consumers=self.polls_audio_sense_settings,
        )

    async def _read_input_gains(self) -> dict[int, float]:
        """Gains ride the same tier as audio sense -- both are per-input reads."""
        if not self.polls_inputs:
            return self.data.input_gains if self.data else {}
        readings: dict[int, float] = {}
        for source in self.active_inputs:
            try:
                readings[source] = await self.client.get_input_gain(source)
            except (CommandError, ParseError) as err:
                _LOGGER.debug("input %s gain did not answer cleanly: %s", source, err)
        return readings

    async def async_refresh_inputs(self) -> None:
        """Re-read per-input state after a write."""
        current = self.data
        if current is None:
            return
        try:
            gains = await self._read_input_gains()
        except TransportError as err:
            _LOGGER.debug("could not re-read input gains: %s", err)
            return
        self.async_set_updated_data(replace(current, input_gains=gains))

    async def _read_triggers(self) -> dict[str, bool]:
        """Read the trigger banks this model has, and only when something consumes them."""
        if not self.polls_triggers:
            return self.data.triggers if self.data else {}
        readings: dict[str, bool] = {}
        spec = self.client.spec
        for bank in range(1, spec.trigger_banks + 1):
            try:
                readings[str(bank)] = await self.client.get_trigger_bank(bank)
            except (CommandError, ParseError) as err:
                _LOGGER.debug("trigger bank %s did not answer cleanly: %s", bank, err)
        try:
            readings["asg"] = await self.client.get_trigger_asg()
        except (CommandError, ParseError) as err:
            _LOGGER.debug("ASG trigger did not answer cleanly: %s", err)
        return readings

    async def async_refresh_triggers(self) -> None:
        """Re-read trigger state after a write."""
        current = self.data
        if current is None:
            return
        try:
            triggers = await self._read_triggers()
        except TransportError as err:
            _LOGGER.debug("could not re-read triggers: %s", err)
            return
        self.async_set_updated_data(replace(current, triggers=triggers))

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

    async def _read_static(self) -> tuple[str | None, str | None]:
        """Read the firmware version and addressing mode once per connection.

        Re-reading these every 30 seconds would be two wasted round trips per cycle forever.
        Cached until a reconnect -- which is also exactly when either could have changed, since a
        firmware update and a DHCP-to-static switch both end the session.

        The audio-sense off delay used to be cached alongside them, and could not stay: FR-14
        makes it settable, and a cached value would never reflect a write.
        """
        if self.data and self.data.firmware is not None:
            return self.data.firmware, self.data.ip_mode
        firmware = ip_mode = None
        try:
            firmware = await self.client.firmware_version()
        except (CommandError, ParseError) as err:
            _LOGGER.debug("%s: firmware did not answer cleanly: %s", self.name, err)
        try:
            ip_mode = await self.client.ip_mode()
        except (CommandError, ParseError) as err:
            _LOGGER.debug("%s: addressing mode did not answer cleanly: %s", self.name, err)
        return firmware, ip_mode

    async def _read_sense_settings(self) -> tuple[bool | None, int | None]:
        """The matrix-wide audio-sense settings: whether it measures, and the sleep timeout."""
        if not self.polls_audio_sense_settings:
            previous = self.data
            return (
                previous.audio_sense_enabled if previous else None,
                previous.audio_sense_off_delay if previous else None,
            )
        enabled = delay = None
        try:
            enabled = await self.client.get_audio_sense_enabled()
        except (CommandError, ParseError) as err:
            _LOGGER.debug("%s: could not read audio-sense enable: %s", self.name, err)
        try:
            delay = await self.client.get_audio_sense_off_delay()
        except (CommandError, ParseError) as err:
            _LOGGER.debug("%s: could not read audio-sense off delay: %s", self.name, err)
        return enabled, delay

    async def _read_audio_sense(self) -> dict[int, bool | None]:
        """Read per-input audio sense, but only when an entity is consuming it.

        A failure here is never fatal: audio sense is a secondary signal, and losing it must not
        take a matrix's zones offline.
        """
        if not self.polls_inputs:
            return {}
        readings: dict[int, bool | None] = {}
        for source in self.active_inputs:
            try:
                readings[source] = await self.client.get_audio_sense(source)
            except (CommandError, ParseError) as err:
                _LOGGER.debug("input %s audio sense did not answer cleanly: %s", source, err)
                readings[source] = None
        return readings

    async def async_refresh_sense_settings(self) -> None:
        """Re-read the audio-sense settings after a write.

        Enabling is answered by a burst that the client drains, so the value cannot be inferred
        from the write -- and the device is the only authority on whether it took.
        """
        current = self.data
        if current is None:
            return
        try:
            enabled, delay = await self._read_sense_settings()
        except TransportError as err:
            _LOGGER.debug("could not re-read audio-sense settings: %s", err)
            return
        self.async_set_updated_data(
            replace(current, audio_sense_enabled=enabled, audio_sense_off_delay=delay)
        )

    async def _read_output(self, output: int) -> OutputSnapshot:
        return OutputSnapshot(
            source=await self.client.get_route(output),
            volume_step=await self.client.get_volume_step(output),
            muted=await self.client.get_mute(output),
        )

    async def async_set_volume(self, output: int, step: int) -> None:
        """Set a zone's volume, re-read it, and if tracking is on remember it as the turn-on value.

        Unlike routing this is **not** debounced: a volume change should reach the device at once,
        and its failure should reach whoever asked for it. Only the turn-on follow-up waits.
        """
        await self.client.set_volume_step(output, step)
        await self.async_refresh_output(output)
        if self.track_turn_on_volume:
            await self._turn_on_debouncer(output).async_call()

    def _turn_on_debouncer(self, output: int) -> Debouncer:
        if (existing := self._turn_on_debouncers.get(output)) is not None:
            return existing

        async def _apply() -> None:
            await self._store_turn_on_volume(output)

        debouncer = Debouncer(
            self.hass,
            _LOGGER,
            cooldown=TURN_ON_VOLUME_DEBOUNCE_SECONDS,
            immediate=False,
            function=_apply,
        )
        self._turn_on_debouncers[output] = debouncer
        return debouncer

    async def _store_turn_on_volume(self, output: int) -> None:
        """Write the zone's settled volume into its turn-on register.

        **The value written is the one the device reported, not the one that was sent.** They
        differ whenever the matrix caps a zone against its own max-volume register, and storing
        the requested step would persist a turn-on volume the device never adopted -- the same
        class of error D-08 exists to prevent, surfacing weeks later as a zone that comes on
        louder than it can actually go.
        """
        snapshot = (self.data.outputs if self.data else {}).get(output)
        if snapshot is None:
            return
        try:
            await self.client.set_turn_on_volume_step(output, snapshot.volume_step)
        except (CommandError, ParseError, TransportError) as err:
            _LOGGER.debug("could not store the turn-on volume for output %s: %s", output, err)
            return
        if output in self._dsp_consumers:
            # Something is displaying this value, so it has to be re-read to be believed.
            await self.async_refresh_output_dsp(output)

    async def async_set_route(self, output: int, source: int | None) -> None:
        """Route an output, coalescing rapid changes. ``None`` disconnects it.

        The debounce wraps **the write and its re-read together**, and that is the whole reason it
        lives here rather than in ``AmsClient``. Debouncing inside the client would let the call
        return before the write reached the device, so the re-read that follows would read
        pre-write state -- an ordering inversion that is silent, because the value it returns is
        entirely plausible, just stale.

        **Errors reach the log, not the caller.** A debounced write happens after the service call
        has returned, so a failure cannot be raised at whoever asked for it. This is inherent to
        coalescing rather than a shortcut: what the user sees instead is the zone's source staying
        where it was, because the re-read reports what the device actually did.
        """
        self._pending_routes[output] = source
        await self._route_debouncer(output).async_call()

    def _route_debouncer(self, output: int) -> Debouncer:
        if (existing := self._route_debouncers.get(output)) is not None:
            return existing

        async def _apply() -> None:
            await self._apply_pending_route(output)

        debouncer = Debouncer(
            self.hass,
            _LOGGER,
            cooldown=ROUTE_DEBOUNCE_SECONDS,
            immediate=False,
            function=_apply,
        )
        self._route_debouncers[output] = debouncer
        return debouncer

    async def _apply_pending_route(self, output: int) -> None:
        # Membership, not a None check: None is a real pending value meaning "disconnect", and
        # treating it as "nothing pending" would silently drop every turn-off.
        if output not in self._pending_routes:
            return
        source = self._pending_routes.pop(output)
        try:
            if source is None:
                await self.client.disconnect_output(output)
            else:
                await self.client.set_route(output, source)
        except (CommandError, ParseError, TransportError) as err:
            _LOGGER.error("could not route output %s to %s: %s", output, source, err)
            return
        await self.async_refresh_output(output)

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
        # A pending route would otherwise fire against a client that is about to be closed, or
        # after a reload has replaced this coordinator -- writing a stale routing to the device.
        for debouncer in (*self._route_debouncers.values(), *self._turn_on_debouncers.values()):
            debouncer.async_cancel()
        self._route_debouncers.clear()
        self._pending_routes.clear()
        self._turn_on_debouncers.clear()
        try:
            async with asyncio.timeout(SHUTDOWN_TIMEOUT):
                await self.client.disconnect()
        except (TimeoutError, OSError) as err:
            _LOGGER.debug("%s did not close cleanly: %s", self.name, err)
