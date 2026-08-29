"""Wire format for the Triad AMS audio matrices: command builders and response parsers.

Pure functions only -- no sockets, no state, no Home Assistant. Everything here is driven by
``docs/triad-ams-protocol.md``, and every parser is pinned by a test quoting a response captured
from real hardware.

Three conventions run through the module:

* **Builders take a** :class:`~ams.model.MatrixSpec`. It carries the channel counts and the
  indices derived from them, so range checking happens in one place instead of in every function.
* **Callers speak 1-based indices.** The device takes them 0-based on the wire and prints them
  1-based in responses, so 1-based is the only base that is consistent at the boundary. The
  conversion happens in the spec and nowhere else.
* **Audio sense is the exception.** It prints 0-based. ``parse_audio_sense`` converts, so callers
  still see 1-based.
"""

from __future__ import annotations

import re
from typing import Final

from .errors import CommandError, ParseError
from .model import MatrixSpec
from .volume import MAX_STEP, MIN_STEP

#: Every frame the device sends ends with at least one of these.
FRAME_TERMINATOR: Final = b"\x00"

#: Bass, treble, balance and EQ gain encode -12..+12 dB in half-steps around this centre.
_TONE_CENTRE: Final = 0x18

_EQ_FREQUENCY_BASE: Final = 0x20
_EQ_GAIN_BASE: Final = 0x25
_EQ_Q_BASE: Final = 0x2A

#: Groups are lettered A..G on the wire index 0..6. Implemented for completeness; no hardware
#: seen so far uses them -- see the design doc's note on FR-07.
GROUP_LETTERS: Final = "ABCDEFG"


# --------------------------------------------------------------------------------------------
# Value guards that are not the spec's business
# --------------------------------------------------------------------------------------------


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


def _out(spec: MatrixSpec, opcode: int, output: int, *tail: int) -> bytes:
    """A set command addressing one output."""
    return _frame(bytes([0x03, opcode, spec.output_byte(output), *tail]))


def _query_out(spec: MatrixSpec, opcode: int, output: int) -> bytes:
    """A query addressing one output. The F5 marker sits before the index."""
    return _frame(bytes([0x03, opcode, 0xF5, spec.output_byte(output)]))


# -- routing -----------------------------------------------------------------------------------


def set_route(spec: MatrixSpec, output: int, source: int) -> bytes:
    """Route ``output`` to ``source``, both 1-based."""
    return _out(spec, 0x1D, output, spec.input_byte(source))


def disconnect_output(spec: MatrixSpec, output: int) -> bytes:
    """Silence ``output`` by routing it one past the last input."""
    return _out(spec, 0x1D, output, spec.disconnect_source)


def query_route(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x1D, output)


# -- volume ------------------------------------------------------------------------------------


def set_output_volume(spec: MatrixSpec, output: int, step: int) -> bytes:
    return _out(spec, 0x1E, output, _step_byte(step))


def query_output_volume(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x1E, output)


def set_output_max_volume(spec: MatrixSpec, output: int, step: int) -> bytes:
    return _out(spec, 0x1F, output, _step_byte(step))


def set_output_turn_on_volume(spec: MatrixSpec, output: int, step: int) -> bytes:
    return _out(spec, 0x33, output, _step_byte(step))


def query_output_turn_on_volume(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x33, output)


def step_output_volume(spec: MatrixSpec, output: int, *, up: bool, large: bool = False) -> bytes:
    """Nudge the volume. ``large`` is the device's 3 dB step."""
    opcode = {(True, False): 0x13, (False, False): 0x14, (True, True): 0x15, (False, True): 0x16}[
        (up, large)
    ]
    return _frame(bytes([0x03, opcode, spec.output_byte(output)]))


# -- mute --------------------------------------------------------------------------------------


def set_output_mute(spec: MatrixSpec, output: int, *, mute: bool) -> bytes:
    return _frame(bytes([0x03, 0x17 if mute else 0x18, spec.output_byte(output)]))


def query_output_mute(spec: MatrixSpec, output: int) -> bytes:
    """Note the 0x04 length byte that ``_query_out`` produces.

    The Control4 driver's ``getOutputMutePrefix`` constant declares 0x03 here and the device
    answers 'Command error'. The driver's own diagnostics routine uses 0x04, which works.
    """
    return _query_out(spec, 0x17, output)


# -- tone and DSP ------------------------------------------------------------------------------


def set_output_bass(spec: MatrixSpec, output: int, db: float) -> bytes:
    return _out(spec, 0x2F, output, _tone_byte(db))


def query_output_bass(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x2F, output)


def set_output_treble(spec: MatrixSpec, output: int, db: float) -> bytes:
    return _out(spec, 0x30, output, _tone_byte(db))


def query_output_treble(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x30, output)


def set_output_balance(spec: MatrixSpec, output: int, db: float) -> bytes:
    """Balance uses the tone encoding: -12 is full left, +12 full right, 0 centre."""
    return _out(spec, 0x31, output, _tone_byte(db))


def query_output_balance(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x31, output)


def set_output_loudness(spec: MatrixSpec, output: int, *, on: bool) -> bytes:
    return _frame(bytes([0x03, 0x1A if on else 0x1B, spec.output_byte(output)]))


def query_output_loudness(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x1A, output)


def set_output_mono(spec: MatrixSpec, output: int, *, mono: bool) -> bytes:
    return _frame(bytes([0x03, 0x11 if mono else 0x10, spec.output_byte(output)]))


def query_output_mono(spec: MatrixSpec, output: int) -> bytes:
    return _query_out(spec, 0x10, output)


# -- parametric EQ -----------------------------------------------------------------------------


def set_eq_frequency(spec: MatrixSpec, output: int, band: int, value: int) -> bytes:
    return _frame(
        bytes([0x03, _band_opcode(_EQ_FREQUENCY_BASE, band), spec.output_byte(output), value])
    )


def query_eq_frequency(spec: MatrixSpec, output: int, band: int) -> bytes:
    return _frame(
        bytes([0x03, _band_opcode(_EQ_FREQUENCY_BASE, band), 0xF5, spec.output_byte(output)])
    )


def set_eq_gain(spec: MatrixSpec, output: int, band: int, db: float) -> bytes:
    return _frame(
        bytes([0x03, _band_opcode(_EQ_GAIN_BASE, band), spec.output_byte(output), _tone_byte(db)])
    )


def query_eq_gain(spec: MatrixSpec, output: int, band: int) -> bytes:
    return _frame(bytes([0x03, _band_opcode(_EQ_GAIN_BASE, band), 0xF5, spec.output_byte(output)]))


def set_eq_q(spec: MatrixSpec, output: int, band: int, value: int) -> bytes:
    return _frame(bytes([0x03, _band_opcode(_EQ_Q_BASE, band), spec.output_byte(output), value]))


def query_eq_q(spec: MatrixSpec, output: int, band: int) -> bytes:
    return _frame(bytes([0x03, _band_opcode(_EQ_Q_BASE, band), 0xF5, spec.output_byte(output)]))


# -- inputs ------------------------------------------------------------------------------------


def set_input_gain(spec: MatrixSpec, source: int, gain: int) -> bytes:
    """Input gain is sent doubled, per the Control4 driver."""
    return _frame(bytes([0x02, 0x04, spec.input_byte(source), gain * 2]))


def query_input_gain(spec: MatrixSpec, source: int) -> bytes:
    return _frame(bytes([0x02, 0x04, 0xF5, spec.input_byte(source)]))


def query_audio_sense(spec: MatrixSpec, source: int) -> bytes:
    return _frame(bytes([0x0A, 0xA0, 0xF5, spec.input_byte(source)]))


def query_audio_sense_off_delay() -> bytes:
    """How long the matrix waits on silence before sleeping an analog input. Minutes."""
    return _frame(bytes([0x0A, 0xA3, 0xF5, 0x00]))


def query_audio_sense_enabled() -> bytes:
    """Whether the matrix is measuring at all. Matrix-wide, not per input.

    There is deliberately no setter here. Enabling audio sense returns a burst of roughly one
    frame per input (C-09), and the Control4 driver re-asserts its own value on every sync -- so a
    control exposed here would appear to work and silently revert. The durable setting lives in
    the Control4 driver; this integration reports it and does not try to own it.
    """
    return _frame(bytes([0x0A, 0xA2, 0xF5, 0x00]))


# -- groups ------------------------------------------------------------------------------------


def _group_byte(group: int) -> int:
    if not 1 <= group <= len(GROUP_LETTERS):
        msg = f"group {group} outside 1..{len(GROUP_LETTERS)}"
        raise ValueError(msg)
    return group - 1


def assign_output_to_group(spec: MatrixSpec, output: int, group: int) -> bytes:
    """``group`` is 1-based (1 = A)."""
    return _out(spec, 0x32, output, _group_byte(group))


def query_group_source(group: int) -> bytes:
    return _frame(bytes([0x04, 0x48, 0xF5, _group_byte(group)]))


def query_group_volume(group: int) -> bytes:
    return _frame(bytes([0x04, 0x47, 0xF5, _group_byte(group)]))


# -- triggers ----------------------------------------------------------------------------------


def set_trigger_bank(spec: MatrixSpec, bank: int, *, on: bool) -> bytes:
    return _frame(bytes([0x05, 0x50 if on else 0x51, spec.trigger_bank_byte(bank)]))


def query_trigger_bank(spec: MatrixSpec, bank: int) -> bytes:
    return _frame(bytes([0x05, 0x50, 0xF5, spec.trigger_bank_byte(bank)]))


def set_trigger_asg(spec: MatrixSpec, *, on: bool) -> bytes:
    """ASG sits after the last output bank, so its index depends on the model."""
    return _frame(bytes([0x05, 0x50 if on else 0x51, spec.asg_index]))


def query_trigger_asg(spec: MatrixSpec) -> bytes:
    return _frame(bytes([0x05, 0x50, 0xF5, spec.asg_index]))


# -- system ------------------------------------------------------------------------------------


def query_power() -> bytes:
    return _frame(bytes([0x01, 0x01, 0xF5]))


def set_power(*, on: bool) -> bytes:
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


def parse_audio_sense(text: str) -> tuple[int, bool | None]:
    """``AudioSense:Input[0]: 1`` -> ``(1, True)``. Three-state, not boolean.

    The index is 0-based here and nowhere else, so it is converted to keep callers consistent.
    Some firmware appends a literal ``$``.

    The value:

    * ``1`` -- signal present
    * ``0`` -- no signal
    * anything else, in practice ``2`` -- **the matrix is not measuring**, because audio sense is
      disabled. Measured 2026-08-29: an input carrying live music reports ``2`` identically to a
      dead one.

    ``2`` returns ``None`` rather than ``False`` deliberately. ``False`` would assert "there is no
    audio", which the device has not determined and cannot; ``None`` says "no reading", which the
    entity layer renders as unavailable. The Control4 driver collapses this to a boolean, which is
    safe for it because it only ever acts on ``1``.
    """
    m = _match(r"AudioSense:Input\[(\d+)\]\s*:\s*(\d+)", text, "audio sense")
    value = m.group(2)
    detected = {"1": True, "0": False}.get(value)
    return int(m.group(1)) + 1, detected


def parse_audio_sense_off_delay(text: str) -> int:
    """``Get Analog nosignal sleep timeout : 0x1`` -> ``1``.

    The value is hex-formatted and the unit is **minutes**: hardware reporting ``0x1`` is the
    1-minute default. The Control4 driver initialises this field to 30, which on this scale is
    thirty minutes rather than the half-minute the number suggests.
    """
    m = _match(r"nosignal sleep timeout\s*:\s*(?:0x)?([0-9A-Fa-f]+)", text, "audio sense delay")
    return int(m.group(1), 16)


def parse_audio_sense_enabled(text: str) -> bool:
    """``Get AutoSenseEnable : Disable`` -> ``False``.

    Worth surfacing per matrix: when sense is off, every input entity goes unavailable at once,
    and this is the thing that explains why.
    """
    m = _match(r"AutoSenseEnable\s*:\s*(\w+)", text, "audio sense enable")
    return m.group(1).lower() in {"enable", "enabled", "on", "1"}


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
