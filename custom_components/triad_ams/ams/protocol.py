"""Wire format for the Triad AMS audio matrices: command builders and response parsers.

Pure functions only -- no sockets, no state, no Home Assistant. Everything here is driven by
``docs/triad-ams-protocol.md``, and every parser is pinned by a test quoting a response captured
from real hardware.

Two conventions run through the whole module:

* **Callers speak 1-based indices.** The device takes them 0-based on the wire and prints them
  1-based in responses, so 1-based is the only base that is consistent at the boundary. The
  conversion happens here and nowhere else.
* **Audio sense is the exception.** It prints 0-based. ``parse_audio_sense`` converts, so callers
  still see 1-based.
"""

from __future__ import annotations

import re
from typing import Final

from .errors import CommandError, ParseError

#: Every frame the device sends ends with at least one of these.
FRAME_TERMINATOR: Final = b"\x00"

#: Volume, max volume and turn-on volume all take a step in this range.
MIN_STEP: Final = 0x00
MAX_STEP: Final = 0x64

#: Bass, treble, balance and EQ gain encode -12..+12 dB in half-steps around this centre.
_TONE_CENTRE: Final = 0x18

_EQ_FREQUENCY_BASE: Final = 0x20
_EQ_GAIN_BASE: Final = 0x25
_EQ_Q_BASE: Final = 0x2A

#: Groups are lettered A..G on the wire index 0..6.
GROUP_LETTERS: Final = "ABCDEFG"


# --------------------------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------------------------


def _channel_byte(index: int, count: int, what: str) -> int:
    """Validate a 1-based channel and return its 0-based wire byte."""
    if not 1 <= index <= count:
        msg = f"{what} {index} outside 1..{count}"
        raise ValueError(msg)
    return index - 1


def _step_byte(step: int) -> int:
    if not MIN_STEP <= step <= MAX_STEP:
        msg = f"volume step {step} outside {MIN_STEP}..{MAX_STEP}"
        raise ValueError(msg)
    return step


def _tone_byte(db: float) -> int:
    """Encode -12..+12 dB as 0x00..0x30 in half-steps."""
    if not -12 <= db <= 12:
        msg = f"tone value {db} outside -12..+12 dB"
        raise ValueError(msg)
    return round((12 + db) * 2)


def _band_opcode(base: int, band: int) -> int:
    if not 1 <= band <= 5:
        msg = f"EQ band {band} outside 1..5"
        raise ValueError(msg)
    return base + band - 1


# --------------------------------------------------------------------------------------------
# Command builders
# --------------------------------------------------------------------------------------------


def _frame(payload: bytes) -> bytes:
    """Prepend the FF 55 header and the length byte that counts ``payload``."""
    return b"\xff\x55" + bytes([len(payload)]) + payload


def set_route(output: int, source: int, *, output_count: int = 24, input_count: int = 24) -> bytes:
    """Route ``output`` to ``source``, both 1-based."""
    out = _channel_byte(output, output_count, "output")
    src = _channel_byte(source, input_count, "input")
    return _frame(bytes([0x03, 0x1D, out, src]))


def disconnect_output(output: int, input_count: int, *, output_count: int = 24) -> bytes:
    """Silence ``output``.

    There is no disconnect opcode. The device treats an input index one past the last valid one
    as 'no source', which is why ``input_count`` -- not ``input_count - 1`` -- is sent.
    """
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x1D, out, input_count]))


def query_route(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x1D, 0xF5, _channel_byte(output, output_count, "output")]))


def set_output_volume(output: int, step: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x1E, out, _step_byte(step)]))


def query_output_volume(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x1E, 0xF5, _channel_byte(output, output_count, "output")]))


def set_output_max_volume(output: int, step: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x1F, out, _step_byte(step)]))


def set_output_turn_on_volume(output: int, step: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x33, out, _step_byte(step)]))


def query_output_turn_on_volume(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x33, 0xF5, _channel_byte(output, output_count, "output")]))


def step_output_volume(
    output: int, *, up: bool, large: bool = False, output_count: int = 24
) -> bytes:
    """Nudge the volume. ``large`` is the device's 3 dB step."""
    opcode = {(True, False): 0x13, (False, False): 0x14, (True, True): 0x15, (False, True): 0x16}[
        (up, large)
    ]
    return _frame(bytes([0x03, opcode, _channel_byte(output, output_count, "output")]))


def set_output_mute(output: int, *, mute: bool, output_count: int = 24) -> bytes:
    opcode = 0x17 if mute else 0x18
    return _frame(bytes([0x03, opcode, _channel_byte(output, output_count, "output")]))


def query_output_mute(output: int, *, output_count: int = 24) -> bytes:
    """Note the 0x04 length byte.

    The Control4 driver's ``getOutputMutePrefix`` constant declares 0x03 here and the device
    answers 'Command error'. The driver's own diagnostics routine uses 0x04, which works.
    """
    return _frame(bytes([0x03, 0x17, 0xF5, _channel_byte(output, output_count, "output")]))


def set_output_bass(output: int, db: float, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x2F, out, _tone_byte(db)]))


def query_output_bass(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x2F, 0xF5, _channel_byte(output, output_count, "output")]))


def set_output_treble(output: int, db: float, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x30, out, _tone_byte(db)]))


def query_output_treble(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x30, 0xF5, _channel_byte(output, output_count, "output")]))


def set_output_balance(output: int, db: float, *, output_count: int = 24) -> bytes:
    """Balance uses the tone encoding: -12 is full left, +12 full right, 0 centre."""
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x31, out, _tone_byte(db)]))


def query_output_balance(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x31, 0xF5, _channel_byte(output, output_count, "output")]))


def set_output_loudness(output: int, *, on: bool, output_count: int = 24) -> bytes:
    opcode = 0x1A if on else 0x1B
    return _frame(bytes([0x03, opcode, _channel_byte(output, output_count, "output")]))


def query_output_loudness(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x1A, 0xF5, _channel_byte(output, output_count, "output")]))


def set_output_mono(output: int, *, mono: bool, output_count: int = 24) -> bytes:
    opcode = 0x11 if mono else 0x10
    return _frame(bytes([0x03, opcode, _channel_byte(output, output_count, "output")]))


def query_output_mono(output: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x03, 0x10, 0xF5, _channel_byte(output, output_count, "output")]))


def set_eq_frequency(output: int, band: int, value: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, _band_opcode(_EQ_FREQUENCY_BASE, band), out, value]))


def query_eq_frequency(output: int, band: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, _band_opcode(_EQ_FREQUENCY_BASE, band), 0xF5, out]))


def set_eq_gain(output: int, band: int, db: float, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, _band_opcode(_EQ_GAIN_BASE, band), out, _tone_byte(db)]))


def query_eq_gain(output: int, band: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, _band_opcode(_EQ_GAIN_BASE, band), 0xF5, out]))


def set_eq_q(output: int, band: int, value: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, _band_opcode(_EQ_Q_BASE, band), out, value]))


def query_eq_q(output: int, band: int, *, output_count: int = 24) -> bytes:
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, _band_opcode(_EQ_Q_BASE, band), 0xF5, out]))


def set_input_gain(source: int, gain: int, *, input_count: int = 24) -> bytes:
    """Input gain is sent doubled, per the Control4 driver."""
    src = _channel_byte(source, input_count, "input")
    return _frame(bytes([0x02, 0x04, src, gain * 2]))


def query_input_gain(source: int, *, input_count: int = 24) -> bytes:
    return _frame(bytes([0x02, 0x04, 0xF5, _channel_byte(source, input_count, "input")]))


def query_audio_sense(source: int, *, input_count: int = 24) -> bytes:
    return _frame(bytes([0x0A, 0xA0, 0xF5, _channel_byte(source, input_count, "input")]))


def assign_output_to_group(output: int, group: int, *, output_count: int = 24) -> bytes:
    """``group`` is 1-based (1 = A). Group 0 on the wire is group A."""
    if not 1 <= group <= len(GROUP_LETTERS):
        msg = f"group {group} outside 1..{len(GROUP_LETTERS)}"
        raise ValueError(msg)
    out = _channel_byte(output, output_count, "output")
    return _frame(bytes([0x03, 0x32, out, group - 1]))


def query_group_source(group: int) -> bytes:
    return _frame(bytes([0x04, 0x48, 0xF5, group - 1]))


def query_group_volume(group: int) -> bytes:
    return _frame(bytes([0x04, 0x47, 0xF5, group - 1]))


def _trigger_bank_byte(bank: int, output_count: int) -> int:
    """Bank 1..3 are the output banks; ASG is addressed by :func:`set_trigger_asg`."""
    if not 1 <= bank <= 3:
        msg = f"trigger bank {bank} outside 1..3"
        raise ValueError(msg)
    if bank > 1 and output_count <= 8:
        msg = f"an {output_count}-output matrix has no trigger bank {bank}"
        raise ValueError(msg)
    return bank - 1


def set_trigger_bank(bank: int, on: bool, *, output_count: int = 24) -> bytes:
    opcode = 0x50 if on else 0x51
    return _frame(bytes([0x05, opcode, _trigger_bank_byte(bank, output_count)]))


def query_trigger_bank(bank: int, *, output_count: int = 24) -> bytes:
    return _frame(bytes([0x05, 0x50, 0xF5, _trigger_bank_byte(bank, output_count)]))


def set_trigger_asg(on: bool, *, output_count: int = 24) -> bytes:
    """ASG sits after the last output bank, so its index depends on the model.

    An 8x8 has one output bank, putting ASG at index 1 -- the same index a 24x24 uses for its
    9-16 bank. Addressing ASG without knowing the model toggles the wrong bank on a 24x24.
    """
    index = 1 if output_count <= 8 else 3
    opcode = 0x50 if on else 0x51
    return _frame(bytes([0x05, opcode, index]))


def query_trigger_asg(*, output_count: int = 24) -> bytes:
    index = 1 if output_count <= 8 else 3
    return _frame(bytes([0x05, 0x50, 0xF5, index]))


def query_power() -> bytes:
    return _frame(bytes([0x01, 0x01, 0xF5]))


def set_power(on: bool) -> bytes:
    """Only power-on is ever useful.

    The Control4 driver disables power-off entirely, commenting that the device's power-on delay
    is too long to handle. This integration follows that: ``media_player`` on/off controls
    routing, not mains power.
    """
    return _frame(bytes([0x01, 0x01 if on else 0x02, 0x00]))


def query_firmware() -> bytes:
    return _frame(bytes([0x06, 0x65, 0x00]))


def query_mac_address() -> bytes:
    return _frame(bytes([0x08, 0x80, 0xF5]))


# --------------------------------------------------------------------------------------------
# Frame decoding
# --------------------------------------------------------------------------------------------


def decode_frame(raw: bytes) -> str:
    """Turn a raw frame into its text, tolerating either framing style.

    Some firmware terminates with a single NUL; some pads every frame to 150 bytes with trailing
    NULs. Stripping all of them handles both, and is why the caller must still drain the socket:
    the padding bytes are on the wire whether or not this function ignores them.
    """
    return raw.decode("ascii", errors="replace").strip("\x00").strip()


def is_command_error(text: str) -> bool:
    """An empty frame counts: the firmware returns one intermittently for mute queries."""
    return text == "" or bool(re.fullmatch(r"command\s+error", text.strip(), re.IGNORECASE))


def _guard(text: str) -> None:
    if is_command_error(text):
        raise CommandError(text or "empty response")


def _fail(text: str, what: str) -> ParseError:
    return ParseError(f"could not read {what} from {text!r}")


# --------------------------------------------------------------------------------------------
# Response parsers
# --------------------------------------------------------------------------------------------

_OUT_INDEX = r"Out\[(\d+)\]"
_IN_INDEX = r"In\[(\d+)\]"


def _match(pattern: str, text: str, what: str) -> re.Match[str]:
    _guard(text)
    found = re.search(pattern, text, re.IGNORECASE)
    if not found:
        raise _fail(text, what)
    return found


def parse_output_volume(text: str) -> tuple[int, float]:
    """``Get Out[1] Volume : -39.7`` -> ``(1, -39.7)``. Decibels, not steps."""
    m = _match(rf"{_OUT_INDEX}\s+Volume\s*:\s*(-?\d+(?:\.\d+)?)", text, "output volume")
    return int(m.group(1)), float(m.group(2))


def parse_output_turn_on_volume(text: str) -> tuple[int, float]:
    m = _match(rf"{_OUT_INDEX}\s+Turn on Vol\s*:\s*(-?\d+(?:\.\d+)?)", text, "turn-on volume")
    return int(m.group(1)), float(m.group(2))


def parse_output_route(text: str) -> tuple[int, int | None]:
    """``input 7`` -> 7 (1-based); ``Audio Off`` -> ``None``."""
    m = _match(rf"{_OUT_INDEX}\s+Input Source\s*:\s*(.+)", text, "output source")
    output, value = int(m.group(1)), m.group(2).strip()
    if re.match(r"audio\s+off", value, re.IGNORECASE):
        return output, None
    source = re.search(r"input\s+(\d+)", value, re.IGNORECASE)
    if not source:
        raise _fail(text, "output source")
    return output, int(source.group(1))


_MUTED = {"on", "mute", "muted", "1", "true", "yes"}
_UNMUTED = {"off", "unmute", "unmuted", "0", "false", "no"}


def parse_output_mute(text: str) -> tuple[int, bool]:
    m = _match(rf"{_OUT_INDEX}\s+Mute status\s*:\s*(\w+)", text, "mute status")
    token = m.group(2).lower()
    if token in _MUTED:
        return int(m.group(1)), True
    if token in _UNMUTED:
        return int(m.group(1)), False
    raise _fail(text, "mute status")


def parse_output_bass(text: str) -> tuple[int, float]:
    m = _match(rf"{_OUT_INDEX}\s+Bass\s*:\s*(-?\d+(?:\.\d+)?)", text, "bass")
    return int(m.group(1)), float(m.group(2))


def parse_output_treble(text: str) -> tuple[int, float]:
    m = _match(rf"{_OUT_INDEX}\s+Treble\s*:\s*(-?\d+(?:\.\d+)?)", text, "treble")
    return int(m.group(1)), float(m.group(2))


def parse_output_balance(text: str) -> tuple[int, float]:
    """Balance answers in words -- ``Bal Center`` -- not the number the setter takes.

    ``Bal L6`` / ``Bal R6`` carry the magnitude; centre carries none. Parsing this as a float
    raises on the very first output of a factory-default matrix.
    """
    m = _match(rf"{_OUT_INDEX}\s+Balance\s*:\s*(.+)", text, "balance")
    output, value = int(m.group(1)), m.group(2).strip()
    if re.search(r"cent(er|re)", value, re.IGNORECASE):
        return output, 0.0
    side = re.search(r"\bBal\s*([LR])\s*(\d+(?:\.\d+)?)", value, re.IGNORECASE)
    if not side:
        raise _fail(text, "balance")
    magnitude = float(side.group(2))
    return output, -magnitude if side.group(1).upper() == "L" else magnitude


def parse_output_loudness(text: str) -> tuple[int, bool]:
    m = _match(rf"{_OUT_INDEX}\s+Loudness status\s*:\s*(\w+)", text, "loudness")
    return int(m.group(1)), m.group(2).lower() == "on"


def parse_output_mono(text: str) -> tuple[int, bool]:
    m = _match(rf"{_OUT_INDEX}\s+Stereo Mono status\s*:\s*(\w+)", text, "stereo/mono")
    return int(m.group(1)), m.group(2).lower() == "mono"


def _parse_band(text: str, label: str, what: str) -> tuple[int, int, float]:
    m = _match(
        rf"{_OUT_INDEX}\s+Band\s+(\d+)\s+{label}\s*:\s*(-?\d+(?:\.\d+)?)",
        text,
        what,
    )
    return int(m.group(1)), int(m.group(2)), float(m.group(3))


def parse_eq_frequency(text: str) -> tuple[int, int, float]:
    """``Get Out[1] Band 1 Freq : 63 Hz`` -> ``(1, 1, 63.0)``; the unit is dropped."""
    return _parse_band(text, "Freq", "EQ frequency")


def parse_eq_gain(text: str) -> tuple[int, int, float]:
    return _parse_band(text, "Gain", "EQ gain")


def parse_eq_q(text: str) -> tuple[int, int, float]:
    return _parse_band(text, "Q", "EQ Q")


def parse_input_gain(text: str) -> tuple[int, float]:
    m = _match(rf"{_IN_INDEX}\s+input gain\s*:\s*(-?\d+(?:\.\d+)?)", text, "input gain")
    return int(m.group(1)), float(m.group(2))


def parse_audio_sense(text: str) -> tuple[int, bool]:
    """``AudioSense:Input[0]: 1`` -> ``(1, True)``.

    The index is 0-based here and nowhere else, so it is converted to keep callers consistent.
    Only ``1`` means detected: ``2`` has been observed on live hardware, is undocumented, and the
    Control4 driver also treats anything other than 1 as stopped. Some firmware appends ``$``.
    """
    m = _match(r"AudioSense:Input\[(\d+)\]\s*:\s*(\d+)", text, "audio sense")
    return int(m.group(1)) + 1, m.group(2) == "1"


def parse_group_membership(text: str) -> tuple[str, bool]:
    """``Group[A] is empty`` -> ``("A", False)``; anything else means the group has members."""
    m = _match(r"Group\[([A-G])\]\s*(.*)", text, "group membership")
    return m.group(1).upper(), not re.search(r"is\s+empty", m.group(2), re.IGNORECASE)


def parse_trigger(text: str) -> tuple[str, bool]:
    """``Get Zone 1-8 trigger status : Off`` -> ``("1-8", False)``."""
    m = _match(r"Get\s+(?:Zone\s+)?(\S+)\s+trigger status\s*:\s*(\w+)", text, "trigger status")
    return m.group(1).upper() if m.group(1).isalpha() else m.group(1), m.group(2).lower() == "on"


def parse_firmware(text: str) -> str:
    return _match(r"Fw version\s*:\s*(\S+)", text, "firmware version").group(1)


def parse_power(text: str) -> bool:
    m = _match(r"Get Power status\s*:\s*(\w+)", text, "power status")
    return m.group(1).lower() in {"working", "on"}


def parse_mac_address(text: str) -> str:
    return _match(r"((?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2})", text, "MAC address").group(1)
