"""A fake Triad AMS matrix, speaking the captured protocol over a real loopback socket.

This exists because the alternative is testing 24 outputs and every failure mode against
hardware that is wired into a house. It replays the exact response grammar recorded in
docs/triad-ams-protocol.md, including the parts that are awkward:

* **Framing personality.** Real firmware differs. ``Padding.SINGLE`` terminates with one NUL;
  ``Padding.FIXED_150`` pads every frame to 150 bytes, which is what an AMS8 on V1.05.74 does for
  error responses. A client that only ever meets the first personality looks correct and desyncs
  in the field.
* **Injectable faults.** ``fail_next`` makes the device answer ``Command error`` or an empty
  frame, both of which real hardware emits on healthy connections.
* **External mutation.** ``mutate()`` changes state without a command, standing in for the
  Control4 controller that shares this device and moves things behind Home Assistant's back.

No site data: the invented MAC is AA:BB:CC:DD:EE:FF and it listens on loopback.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from enum import Enum

#: Frame size the AMS8 pads error responses to, measured.
FIXED_FRAME = 150

SIMULATOR_MAC = "AA:BB:CC:DD:EE:FF"

#: Command groups that change device state, and the marker that makes a command a query instead.
#: Named rather than inlined as byte literals: an earlier inline version was silently mangled to
#: empty bytes, which made ``write_commands`` return nothing and let the D-05 test pass without
#: checking anything.
OUTPUT_GROUP = b"\x03"
INPUT_GROUP = b"\x02"
QUERY_MARKER = 0xF5


class Padding(Enum):
    """How this firmware personality terminates a frame."""

    SINGLE = "single"
    FIXED_150 = "fixed150"


class Fault(Enum):
    """A fault to inject into the next response."""

    COMMAND_ERROR = "command_error"
    EMPTY = "empty"
    #: Answer correctly but name a different output. This is what a desynchronised stream looks
    #: like from the client's side: a well-formed, parseable response about the wrong zone.
    WRONG_OUTPUT = "wrong_output"
    #: Answer with a BURST of frames instead of one. Measured on real hardware: enabling audio
    #: sense returns roughly one AudioSense frame per input. A client assuming one response per
    #: command reads the surplus as answers to later queries and desyncs silently.
    BURST = "burst"


#: Factory band defaults, read from real hardware: 63 Hz, 250 Hz, 1 kHz, 4 kHz, 20 kHz.
DEFAULT_BAND_INDICES = (5, 11, 17, 23, 30)

#: ISO 1/3-octave centres, duplicated deliberately -- a test double that imports the table it is
#: checking can agree with a bug in it.
_FREQ_HZ = (
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160,
    200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000,
)  # fmt: skip

#: Q values by index, measured on real hardware. Indices above 7 clamp to 3 on the device, and
#: this double reproduces that -- a client that sends an out-of-range index must not appear to
#: have set something the hardware ignored.
_Q_VALUES = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 2.0, 3.0)


@dataclass
class OutputState:
    source: int | None = None
    step: int = 0
    muted: bool = False
    bass: float = 0.0
    treble: float = 0.0
    balance: float = 0.0
    loudness: bool = False
    mono: bool = False
    turn_on_step: int = 0
    band_freq: list[int] = field(default_factory=lambda: list(DEFAULT_BAND_INDICES))
    band_gain: list[float] = field(default_factory=lambda: [0.0] * 5)
    band_q: list[int] = field(default_factory=lambda: [2] * 5)  # index 2 == Q 0.7


@dataclass
class MatrixState:
    model: str = "AMS8"
    outputs: int = 8
    inputs: int = 8
    firmware: str = "V1.05.74"
    #: Off on every matrix in the reference installation, which is why every input reads 2 there.
    audio_sense_enabled: bool = False
    #: Inputs with signal, 1-based. Only observable when audio_sense_enabled.
    inputs_with_signal: set[int] = field(default_factory=set)
    #: Minutes of silence before an analog input sleeps. Hardware default is 1.
    audio_sense_off_delay: int = 1
    #: 12 V trigger banks by wire index, plus the ASG index. All off from the factory.
    triggers: dict[int, bool] = field(default_factory=dict)
    #: Per-input gain, stored as the raw doubled value the wire carries.
    input_gain_raw: dict[int, int] = field(default_factory=dict)
    channels: dict[int, OutputState] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.channels:
            self.channels = {i: OutputState() for i in range(1, self.outputs + 1)}


class AmsSimulator:
    """An asyncio TCP server that answers like a Triad AMS matrix."""

    def __init__(
        self,
        *,
        outputs: int = 8,
        inputs: int = 8,
        padding: Padding = Padding.SINGLE,
        firmware: str = "V1.05.74",
    ) -> None:
        self.state = MatrixState(
            model=f"AMS{outputs}", outputs=outputs, inputs=inputs, firmware=firmware
        )
        self.padding = padding
        self.fail_next: Fault | None = None
        #: Every command received, as hex. Lets a test assert what went on the wire.
        self.received: list[str] = []
        #: Connections currently open. Real hardware accepts several; so does this.
        self.connections = 0
        self._server: asyncio.Server | None = None
        self._writers: set[asyncio.StreamWriter] = set()

    # -- lifecycle ----------------------------------------------------------------------------

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Start listening and return the bound port (0 asks the OS for a free one)."""
        self._server = await asyncio.start_server(self._handle, host, port)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Tear down deterministically, including any connection still open.

        ``Server.wait_closed()`` waits for in-flight handlers, and a handler parked in
        ``readexactly`` waiting for the next command never returns on its own. Without closing
        the client connections first this deadlocks -- and it deadlocks precisely when a test has
        already failed and skipped its cleanup, replacing a useful assertion message with a hang.
        """
        for writer in list(self._writers):
            writer.close()
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def __aenter__(self) -> AmsSimulator:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    @property
    def write_commands(self) -> list[str]:
        """Received commands that would CHANGE the device, as hex.

        A query carries the 0xF5 marker; anything in the output (0x03) or input (0x02) groups
        without it is a set. Lets a test assert that setup only ever reads -- which is design
        decision D-05, and the difference between coexisting with another controller and
        overwriting whatever it just did.
        """
        writes = []
        for frame in self.received:
            payload = bytes.fromhex(frame)[3:]
            if payload[:1] in (OUTPUT_GROUP, INPUT_GROUP) and QUERY_MARKER not in payload:
                writes.append(frame)
        return writes

    @property
    def port(self) -> int:
        assert self._server is not None, "simulator not started"
        return self._server.sockets[0].getsockname()[1]

    # -- external mutation --------------------------------------------------------------------

    def mutate(self, output: int, *, source: int | None = ..., step: int | None = None) -> None:  # type: ignore[assignment]
        """Change state without a command, as a second controller on the LAN would.

        The device does not announce this. A client only learns about it by polling, which is the
        behaviour that makes ``local_polling`` the correct classification for the integration.
        """
        channel = self.state.channels[output]
        if source is not ...:
            channel.source = source
        if step is not None:
            channel.step = step

    # -- wire ---------------------------------------------------------------------------------

    def _frame(self, text: str) -> bytes:
        body = text.encode("ascii")
        if self.padding is Padding.FIXED_150:
            return body.ljust(FIXED_FRAME, b"\x00")
        return body + b"\x00"

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self._writers.add(writer)
        try:
            while True:
                header = await reader.readexactly(3)
                if header[:2] != b"\xff\x55":
                    writer.write(self._frame("Command error"))
                    await writer.drain()
                    continue
                payload = await reader.readexactly(header[2])
                self.received.append((header + payload).hex())
                for text in self._respond_frames(payload):
                    writer.write(self._frame(text))
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            self.connections -= 1
            self._writers.discard(writer)
            writer.close()

    def _respond_frames(self, payload: bytes) -> list[str]:
        """Frames to send for one command. Usually one; a burst fault sends many."""
        if self.fail_next is Fault.BURST:
            self.fail_next = None
            # One frame per input, as the real enable command produces.
            return [f"AudioSense:Input[{i}]: 0" for i in range(self.state.inputs)]
        return [self._respond(payload)]

    def _respond(self, payload: bytes) -> str:
        fault, self.fail_next = self.fail_next, None
        if fault is Fault.EMPTY:
            return ""
        if fault is Fault.COMMAND_ERROR:
            return "Command error"
        try:
            answer = self._dispatch(payload)
        except (IndexError, KeyError, ValueError):
            return "Command error"
        if fault is Fault.WRONG_OUTPUT:
            answer = _rename_output(answer)
        return answer

    def _dispatch(self, payload: bytes) -> str:
        group, opcode = payload[0], payload[1]
        query = len(payload) > 2 and payload[2] == 0xF5
        rest = payload[3:] if query else payload[2:]

        if group == 0x06 and opcode == 0x65:
            return f"Fw version : {self.state.firmware}"
        if group == 0x08 and opcode == 0x80:
            return f"Get MAC Add {SIMULATOR_MAC}"
        if group == 0x01 and opcode == 0x01:
            return "Get Power status : Working"

        if group == 0x05 and opcode in (0x50, 0x51):
            return self._trigger(opcode, rest, query=query)

        if group == 0x02 and opcode == 0x04:
            source = rest[0]
            if query:
                # The device clamps above 0x18; reproduce that rather than reporting a value
                # it would never actually apply.
                raw = min(self.state.input_gain_raw.get(source, 0), 24)
                return f"Get In[{source + 1}] input gain : {raw / 2:g}"
            self.state.input_gain_raw[source] = rest[1]
            return f"Set In[{source + 1}] input gain"

        if group == 0x0A and opcode == 0xA2:
            if query:
                word = "Enable" if self.state.audio_sense_enabled else "Disable"
                return f"Get AutoSenseEnable : {word}"
            return "Set AutoSenseEnable"

        if group == 0x0A and opcode == 0xA3 and query:
            return f"Get Analog nosignal sleep timeout : 0x{self.state.audio_sense_off_delay:X}"

        if group == 0x0A and opcode == 0xA0 and query:
            source = rest[0] + 1
            if not self.state.audio_sense_enabled:
                # 2 is "not measuring", and it is what a live input reports too.
                return f"AudioSense:Input[{rest[0]}]: 2"
            value = 1 if source in self.state.inputs_with_signal else 0
            return f"AudioSense:Input[{rest[0]}]: {value}"

        if group != 0x03:
            return "Command error"

        output = self._output(rest[0])
        channel = self.state.channels[output]

        if opcode == 0x1D:  # routing
            if query:
                if channel.source is None:
                    return f"Get Out[{output}] Input Source : Audio Off"
                return f"Get Out[{output}] Input Source : input {channel.source}"
            source = rest[1]
            # An index one past the last input means disconnect; there is no disconnect opcode.
            channel.source = None if source >= self.state.inputs else source + 1
            return f"Set Out[{output}] Input Source"

        if opcode == 0x1E:  # volume
            if query:
                return f"Get Out[{output}] Volume : {_db_for(channel.step)}"
            channel.step = rest[1]
            return f"Set Out[{output}] Volume"

        if opcode == 0x17:  # mute on, and the mute query
            if query:
                word = "mute" if channel.muted else "Unmute"
                return f"Get Out[{output}] Mute status : {word}"
            channel.muted = True
            return f"Set Out[{output}] Mute"

        if opcode == 0x18:
            channel.muted = False
            return f"Set Out[{output}] Unmute"

        if opcode == 0x2F:
            if query:
                return f"Get Out[{output}] Bass : {channel.bass:g}"
            channel.bass = rest[1] / 2 - 12
            return f"Set Out[{output}] Bass"

        if opcode == 0x30:
            if query:
                return f"Get Out[{output}] Treble : {channel.treble:g}"
            channel.treble = rest[1] / 2 - 12
            return f"Set Out[{output}] Treble"

        if opcode == 0x31:
            if query:
                if channel.balance == 0:
                    return f"Get Out[{output}] Balance : Bal Center"
                side = "L" if channel.balance < 0 else "R"
                return f"Get Out[{output}] Balance : Bal {side}{abs(channel.balance):g}"
            channel.balance = rest[1] / 2 - 12
            return f"Set Out[{output}] Balance"

        if opcode == 0x33:
            if query:
                return f"Get Out[{output}] Turn on Vol : {_db_for(channel.turn_on_step)}"
            channel.turn_on_step = rest[1]
            return f"Set Out[{output}] Turn on Vol"

        if 0x20 <= opcode <= 0x2E:
            return self._eq(channel, output, opcode, rest, query=query)

        if opcode in (0x1A, 0x1B):
            if query:
                return f"Get Out[{output}] Loudness status : {'On' if channel.loudness else 'Off'}"
            channel.loudness = opcode == 0x1A
            return f"Set Out[{output}] Loudness"

        if opcode in (0x10, 0x11):
            if query:
                word = "mono" if channel.mono else "stereo"
                return f"Get Out[{output}] Stereo Mono status : {word}"
            channel.mono = opcode == 0x11
            return f"Set Out[{output}] Stereo Mono"

        return "Command error"

    def _trigger(self, opcode: int, rest: bytes, *, query: bool) -> str:
        """Trigger banks are indexed on the wire; ASG sits after the last output bank.

        On an 8x8 that puts ASG at index 1 -- the same index a 24x24 uses for its 9-16 bank -- so
        this double answers by index and lets the client be the thing that knows the difference.
        """
        index = rest[0]
        banks = -(-self.state.outputs // 8)
        if not query:
            self.state.triggers[index] = opcode == 0x50
            return "Set trigger"
        on = self.state.triggers.get(index, False)
        label = "ASG" if index >= banks else f"Zone {index * 8 + 1}-{index * 8 + 8}"
        return f"Get {label} trigger status : {'On' if on else 'Off'}"

    def _eq(self, channel, output: int, opcode: int, rest: bytes, *, query: bool) -> str:
        """EQ opcodes are a base plus the 0-based band index; three parameters share the range."""
        if opcode <= 0x24:
            band, kind = opcode - 0x20, "Freq"
        elif opcode <= 0x29:
            band, kind = opcode - 0x25, "Gain"
        else:
            band, kind = opcode - 0x2A, "Q"

        if kind == "Freq":
            if query:
                hz = _FREQ_HZ[channel.band_freq[band]]
                text = f"{hz / 1000:g} kHz" if hz >= 1000 else f"{hz:g} Hz"
                return f"Get Out[{output}] Band {band + 1} Freq : {text}"
            channel.band_freq[band] = rest[1]
            return f"Set Out[{output}] Band {band + 1} Freq"
        if kind == "Gain":
            if query:
                return f"Get Out[{output}] Band {band + 1} Gain : {channel.band_gain[band]:g}"
            channel.band_gain[band] = rest[1] / 2 - 12
            return f"Set Out[{output}] Band {band + 1} Gain"
        if query:
            index = min(channel.band_q[band], len(_Q_VALUES) - 1)
            return f"Get Out[{output}] Band {band + 1} Q : {_Q_VALUES[index]:g}"
        channel.band_q[band] = rest[1]
        return f"Set Out[{output}] Band {band + 1} Q"

    def _output(self, wire_index: int) -> int:
        """Wire indices are 0-based; everything else in this file is 1-based."""
        output = wire_index + 1
        if not 1 <= output <= self.state.outputs:
            msg = f"output {output} outside this matrix"
            raise ValueError(msg)
        return output


# The taper, duplicated deliberately: importing the integration's own table would let a bug in
# it agree with itself and pass. A test double should not share code with what it tests.
_DB_POINTS = (
    -108.0, -100.0, -92.7, -85.8, -79.5, -73.9, -69.0, -64.6, -61.0, -58.0,
    -55.6, -53.9, -52.0, -50.5, -49.6, -48.7, -47.7, -46.8, -45.9, -45.0,
)  # fmt: skip


def _db_for(step: int) -> str:
    """Report a step as the device would: decibels, and ``0`` rather than ``0.0`` at the top."""
    if step >= 100:
        return "0"
    # Above step 20 a straight line is close enough for a test double; the real taper lives in
    # ams/volume.py, and duplicating it here would let a bug in it agree with itself.
    value = _DB_POINTS[step] if step < len(_DB_POINTS) else round(-45.0 + (step - 19) * 0.5625, 1)
    return f"{value:g}"


def _rename_output(answer: str) -> str:
    """Shift the output index a response names, leaving everything else intact.

    Produces the shape a frame-boundary slip produces: valid text, correct format, wrong zone.
    """
    return re.sub(r"Out\[(\d+)\]", lambda m: f"Out[{int(m.group(1)) + 1}]", answer, count=1)
