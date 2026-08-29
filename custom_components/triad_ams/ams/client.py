"""Async TCP client for a Triad AMS matrix.

One socket per matrix, one command at a time. The device is a strict request/response peer with
no message IDs, so the only way to know which answer belongs to which question is to ask one
question at a time -- hence the lock around every exchange.

Two behaviours drove the design, both measured:

* **Frame padding varies by firmware.** Some pad every frame to 150 bytes with NULs. Reading to
  the first NUL and stopping leaves the rest buffered, and every later exchange then answers the
  previous question. Nothing raises; outputs just report each other's state. The reader drains
  to a quiet socket after each frame, and holds back any non-NUL byte it over-reads.
* **Errors are not transport failures.** ``Command error`` and empty frames arrive on healthy
  connections. They raise ``CommandError`` and leave the socket open. Only real socket failures
  raise ``TransportError`` and reset the connection.

No Home Assistant imports. See tests/conftest.py for why that is structural.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Final

from . import protocol as p
from .eq import index_for_frequency, index_for_q
from .errors import CommandError, ParseError, TransportError
from .model import MODELS, MatrixSpec
from .volume import step_for_db

_LOGGER = logging.getLogger(__name__)

#: How long to wait for a frame before calling the device unresponsive.
READ_TIMEOUT: Final = 5.0
CONNECT_TIMEOUT: Final = 5.0

#: How long to keep listening for padding after a frame ends. Short: it is a local socket and
#: the padding, when it comes, is already in the kernel buffer.
DRAIN_TIMEOUT: Final = 0.05

#: Consecutive clean exchanges before the drain is skipped. Firmware does not change framing
#: mid-connection, so once single-NUL framing is established the timeout is pure latency --
#: 24 outputs times several attributes makes it worth avoiding.
CLEAN_EXCHANGES_TO_TRUST: Final = 3


class AmsClient:
    """Talks to one matrix. Connects lazily and reconnects on transport failure."""

    def __init__(self, host: str, port: int = 52000, *, spec: MatrixSpec | None = None) -> None:
        self.host = host
        self.port = port
        # Defaults to the largest model so a probe before the model is known still validates
        # sanely; setup replaces it with the configured spec.
        self.spec = spec or MODELS["AMS24"]

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

        # Framing is learned per connection and reset with it.
        self._held_byte = b""
        self._padding_seen = False
        self._clean_exchanges = 0

    @property
    def connected(self) -> bool:
        return self._writer is not None

    # -- connection ---------------------------------------------------------------------------

    async def connect(self) -> None:
        if self._writer is not None:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port), timeout=CONNECT_TIMEOUT
            )
        except (OSError, TimeoutError) as err:
            self._reset()
            msg = f"could not connect to {self.host}:{self.port}: {err}"
            raise TransportError(msg) from err
        self._forget_framing()

    async def disconnect(self) -> None:
        writer, self._writer, self._reader = self._writer, None, None
        self._forget_framing()
        if writer is None:
            return
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()

    def _reset(self) -> None:
        """Drop the transport without awaiting it, so a failed exchange can reconnect next time."""
        if self._writer is not None:
            self._writer.close()
        self._writer = None
        self._reader = None
        self._forget_framing()

    def _forget_framing(self) -> None:
        self._held_byte = b""
        self._padding_seen = False
        self._clean_exchanges = 0

    # -- exchange -----------------------------------------------------------------------------

    async def _exchange(self, command: bytes) -> str:
        """Send one command, return the decoded response text.

        Serialised: the device has no way to say which answer goes with which question.
        """
        async with self._lock:
            await self.connect()
            assert self._reader is not None and self._writer is not None
            try:
                await self._discard_stale()
                self._writer.write(command)
                await self._writer.drain()
                raw = await asyncio.wait_for(
                    self._reader.readuntil(p.FRAME_TERMINATOR), timeout=READ_TIMEOUT
                )
                if self._held_byte:
                    raw, self._held_byte = self._held_byte + raw, b""
                await self._drain_padding()
            except (OSError, TimeoutError, asyncio.IncompleteReadError) as err:
                self._reset()
                msg = f"{self.host}:{self.port} failed mid-exchange: {err}"
                raise TransportError(msg) from err
            return p.decode_frame(raw)

    async def _drain_padding(self) -> None:
        """Swallow NUL padding that trails a frame on some firmware.

        A non-NUL byte means the padding has ended and real data has started; it is held back and
        prepended to the next frame, so over-reading cannot lose anything.
        """
        if self._trusts_single_nul():
            return
        assert self._reader is not None
        drained = 0
        while True:
            try:
                byte = await asyncio.wait_for(self._reader.readexactly(1), timeout=DRAIN_TIMEOUT)
            except (TimeoutError, OSError, asyncio.IncompleteReadError):
                break  # Nothing more buffered; a real failure surfaces on the next read.
            if byte != b"\x00":
                self._held_byte = byte
                self._note_framing(clean=False)
                return
            drained += 1
        self._note_framing(clean=drained == 0)

    async def _discard_stale(self) -> None:
        """Drop anything buffered before a command is written.

        Belt and braces to :meth:`_drain_padding`: whatever is already in the buffer cannot be an
        answer to a question not yet asked, so keeping it would only desync the next read.
        """
        if self._trusts_single_nul():
            return
        assert self._reader is not None
        stale = bytearray(self._held_byte)
        self._held_byte = b""
        while True:
            try:
                chunk = await asyncio.wait_for(self._reader.read(1024), timeout=DRAIN_TIMEOUT)
            except (TimeoutError, OSError):
                break
            if not chunk:
                break
            stale += chunk
        if stale:
            _LOGGER.debug("discarded %d stale byte(s) before sending", len(stale))
            self._note_framing(clean=False)

    def _trusts_single_nul(self) -> bool:
        return not self._padding_seen and self._clean_exchanges >= CLEAN_EXCHANGES_TO_TRUST

    def _note_framing(self, *, clean: bool) -> None:
        if clean:
            self._clean_exchanges += 1
        else:
            self._padding_seen = True
            self._clean_exchanges = 0

    async def _write(self, command: bytes) -> None:
        """Issue a command whose response carries no information worth parsing.

        The device does answer writes, but the wording was never captured -- the live probe was
        deliberately query-only, since a set command would have moved audio in an occupied house.
        Rather than guess at strings and validate against them, the frame is consumed and
        discarded, and callers that need certainty re-read the value. Depending on unverified
        response text is how a client ends up rejecting a command that in fact succeeded.
        """
        text = await self._exchange(command)
        if p.is_command_error(text):
            raise CommandError(text or "empty response")

    # -- reads --------------------------------------------------------------------------------

    async def firmware_version(self) -> str:
        return p.parse_firmware(await self._exchange(p.query_firmware()))

    async def mac_address(self) -> str:
        return p.parse_mac_address(await self._exchange(p.query_mac_address()))

    async def power(self) -> bool:
        return p.parse_power(await self._exchange(p.query_power()))

    async def get_route(self, output: int) -> int | None:
        text = await self._exchange(p.query_route(self.spec, output))
        index, source = p.parse_output_route(text)
        self._verify(index, output, "route")
        return source

    async def get_volume_step(self, output: int) -> int:
        """Return 0..100. The device answers in decibels; the taper converts."""
        text = await self._exchange(p.query_output_volume(self.spec, output))
        index, db = p.parse_output_volume(text)
        self._verify(index, output, "volume")
        return step_for_db(db)

    async def get_mute(self, output: int) -> bool:
        text = await self._exchange(p.query_output_mute(self.spec, output))
        index, muted = p.parse_output_mute(text)
        self._verify(index, output, "mute")
        return muted

    async def get_bass(self, output: int) -> float:
        text = await self._exchange(p.query_output_bass(self.spec, output))
        index, value = p.parse_output_bass(text)
        self._verify(index, output, "bass")
        return value

    async def get_treble(self, output: int) -> float:
        text = await self._exchange(p.query_output_treble(self.spec, output))
        index, value = p.parse_output_treble(text)
        self._verify(index, output, "treble")
        return value

    async def get_loudness(self, output: int) -> bool:
        text = await self._exchange(p.query_output_loudness(self.spec, output))
        index, value = p.parse_output_loudness(text)
        self._verify(index, output, "loudness")
        return value

    async def get_audio_sense(self, source: int) -> bool | None:
        """True/False when the matrix is measuring, None when audio sense is disabled."""
        text = await self._exchange(p.query_audio_sense(self.spec, source))
        index, detected = p.parse_audio_sense(text)
        if index != source:
            msg = f"asked input {source} for audio sense, device answered for input {index}"
            raise ParseError(msg)
        return detected

    async def get_audio_sense_enabled(self) -> bool:
        """Whether this matrix measures audio sense at all."""
        return p.parse_audio_sense_enabled(await self._exchange(p.query_audio_sense_enabled()))

    async def get_balance(self, output: int) -> float:
        text = await self._exchange(p.query_output_balance(self.spec, output))
        index, value = p.parse_output_balance(text)
        self._verify(index, output, "balance")
        return value

    async def get_turn_on_volume_step(self, output: int) -> int:
        text = await self._exchange(p.query_output_turn_on_volume(self.spec, output))
        index, db = p.parse_output_turn_on_volume(text)
        self._verify(index, output, "turn-on volume")
        return step_for_db(db)

    async def get_eq_band(self, output: int, band: int) -> tuple[float, float, float]:
        """Return ``(frequency_hz, gain_db, q)`` for one band.

        Three round trips; the device offers no way to ask for a whole band at once.
        """
        out, got, hz = p.parse_eq_frequency(
            await self._exchange(p.query_eq_frequency(self.spec, output, band))
        )
        self._verify(out, output, f"EQ band {band} frequency")
        self._verify_band(got, band)

        out, got, gain = p.parse_eq_gain(
            await self._exchange(p.query_eq_gain(self.spec, output, band))
        )
        self._verify(out, output, f"EQ band {band} gain")
        self._verify_band(got, band)

        out, got, q = p.parse_eq_q(await self._exchange(p.query_eq_q(self.spec, output, band)))
        self._verify(out, output, f"EQ band {band} Q")
        self._verify_band(got, band)
        return hz, gain, q

    def _verify_band(self, reported: int, asked: int) -> None:
        """Bands carry their own index, so a slip within one output is catchable too."""
        if reported != asked:
            msg = f"asked EQ band {asked}, device answered for band {reported}"
            raise ParseError(msg)

    async def get_trigger_bank(self, bank: int) -> bool:
        """Whether one 12 V output trigger bank is on."""
        text = await self._exchange(p.query_trigger_bank(self.spec, bank))
        _name, on = p.parse_trigger(text)
        return on

    async def get_trigger_asg(self) -> bool:
        """Whether the ASG trigger is on.

        Its wire index depends on the model -- an 8x8 puts ASG where a 24x24 keeps its 9-16 bank
        -- which is why this goes through the spec rather than a literal.
        """
        _name, on = p.parse_trigger(await self._exchange(p.query_trigger_asg(self.spec)))
        return on

    async def set_trigger_bank(self, bank: int, *, on: bool) -> None:
        await self._write(p.set_trigger_bank(self.spec, bank, on=on))

    async def set_trigger_asg(self, *, on: bool) -> None:
        await self._write(p.set_trigger_asg(self.spec, on=on))

    async def get_audio_sense_off_delay(self) -> int:
        """Minutes of silence before an analog input sleeps."""
        return p.parse_audio_sense_off_delay(await self._exchange(p.query_audio_sense_off_delay()))

    async def get_mono(self, output: int) -> bool:
        text = await self._exchange(p.query_output_mono(self.spec, output))
        index, value = p.parse_output_mono(text)
        self._verify(index, output, "stereo/mono")
        return value

    def _verify(self, reported: int, asked: int, what: str) -> None:
        """Catch a desync at the point it happens rather than three polls later.

        Every response names the output it describes. If that disagrees with the output asked
        about, the stream has slipped and the value is another zone's -- which is exactly the
        failure that unhandled frame padding produces, and exactly the one that otherwise looks
        like the hardware behaving oddly.
        """
        if reported != asked:
            msg = f"asked output {asked} for {what}, device answered for output {reported}"
            raise ParseError(msg)

    # -- writes -------------------------------------------------------------------------------

    async def set_route(self, output: int, source: int) -> None:
        await self._write(p.set_route(self.spec, output, source))

    async def disconnect_output(self, output: int) -> None:
        await self._write(p.disconnect_output(self.spec, output))

    async def set_volume_step(self, output: int, step: int) -> None:
        await self._write(p.set_output_volume(self.spec, output, step))

    async def set_mute(self, output: int, *, mute: bool) -> None:
        await self._write(p.set_output_mute(self.spec, output, mute=mute))

    async def set_bass(self, output: int, db: float) -> None:
        await self._write(p.set_output_bass(self.spec, output, db))

    async def set_treble(self, output: int, db: float) -> None:
        await self._write(p.set_output_treble(self.spec, output, db))

    async def set_loudness(self, output: int, *, on: bool) -> None:
        await self._write(p.set_output_loudness(self.spec, output, on=on))

    async def set_mono(self, output: int, *, mono: bool) -> None:
        await self._write(p.set_output_mono(self.spec, output, mono=mono))

    async def set_balance(self, output: int, db: float) -> None:
        await self._write(p.set_output_balance(self.spec, output, db))

    async def set_max_volume_step(self, output: int, step: int) -> None:
        await self._write(p.set_output_max_volume(self.spec, output, step))

    async def set_turn_on_volume_step(self, output: int, step: int) -> None:
        await self._write(p.set_output_turn_on_volume(self.spec, output, step))

    async def set_eq_frequency(self, output: int, band: int, hz: float) -> None:
        """Set a band's centre frequency, given in Hz.

        Converted to the device's table index here, so no caller ever handles the raw index. It
        is an artefact of the wire format and would be meaningless in a UI to anyone tuning a room.
        """
        await self._write(p.set_eq_frequency(self.spec, output, band, index_for_frequency(hz)))

    async def set_eq_gain(self, output: int, band: int, db: float) -> None:
        await self._write(p.set_eq_gain(self.spec, output, band, db))

    async def set_eq_q(self, output: int, band: int, q: float) -> None:
        """Set a band's Q, given as the Q value itself.

        Converted to the device's table index here, so no caller handles the raw index. The device
        clamps anything above the table to Q 3, which would silently ignore an out-of-range
        request -- converting through the table is what stops that being possible.
        """
        await self._write(p.set_eq_q(self.spec, output, band, index_for_q(q)))
