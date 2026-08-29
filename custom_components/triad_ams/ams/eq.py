"""The EQ frequency table, and the units the device reports.

The device takes a frequency as an index 0..30 and reports it as text with a unit -- ``63 Hz``,
``1.6 kHz``. Two things follow, and both have bitten this code:

* **The unit is load-bearing.** ``1.6 kHz`` parsed as ``1.6`` is three orders of magnitude out and
  looks entirely plausible in a UI. Every band above 1 kHz reports with a multiplier.
* **The index is not the frequency.** Setting requires the table below.

## Where the table comes from

Two endpoints are documented in the Control4 driver: index ``0x00`` is 20 Hz and ``0x1E`` (30) is
20 kHz. That is 31 steps from 20 Hz to 20 kHz, which is exactly the ISO 1/3-octave centre
frequencies -- there is only one such series, and it has exactly 31 members over that span.

The interior is therefore inferred from the ISO standard rather than measured directly. It is
corroborated: **every frequency observed on real hardware is a member of this table** (63, 250,
800, 1k, 1.6k, 2.5k, 4k, 16k, 20k across two matrices), and the factory band defaults land on
indices 5, 11, 17, 23 and 30 -- a sensible spread rather than arbitrary values.

Reading back after a write is what makes an error in the interior safe rather than silent: the
coordinator re-reads the band, so a wrong index shows the wrong frequency immediately in the UI
instead of quietly filtering the wrong part of the spectrum.
"""

from __future__ import annotations

import re
from typing import Final

#: ISO 1/3-octave centre frequencies in Hz, indexed by the device's 0..30 band-frequency value.
# fmt: off
EQ_FREQUENCIES: Final[tuple[float, ...]] = (
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160,          # 0-9
    200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,  # 10-19
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000,  # 20-29
    20000,                                                # 30
)
# fmt: on

MAX_FREQUENCY_INDEX: Final = len(EQ_FREQUENCIES) - 1

_UNITS: Final[dict[str, float]] = {"hz": 1.0, "khz": 1000.0}

#: Q values, indexed by the device's 0..7 band-Q value.
#:
#: Measured 2026-08-29 by sweeping the index on an output that was unrouted, at minimum volume and
#: muted, then restoring it -- verified on a fresh connection afterwards. Unlike the frequency
#: table, none of this is inferred.
#:
#: The device clamps: any index above 7 reports Q 3. That makes an out-of-range write harmless,
#: but it also means a caller must constrain to the real range or a user will believe they set
#: something the hardware quietly ignored.
EQ_Q_VALUES: Final[tuple[float, ...]] = (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 2.0, 3.0)

MAX_Q_INDEX: Final = len(EQ_Q_VALUES) - 1


def parse_frequency_text(text: str) -> float:
    """``1.6 kHz`` -> ``1600.0``. Raises ValueError if the unit is missing or unknown."""
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*(k?Hz)", text, re.IGNORECASE)
    if not match:
        msg = f"no frequency with a unit in {text!r}"
        raise ValueError(msg)
    return float(match.group(1)) * _UNITS[match.group(2).lower()]


def frequency_for_index(index: int) -> float:
    if not 0 <= index <= MAX_FREQUENCY_INDEX:
        msg = f"EQ frequency index {index} outside 0..{MAX_FREQUENCY_INDEX}"
        raise ValueError(msg)
    return EQ_FREQUENCIES[index]


def index_for_frequency(hz: float) -> int:
    """Nearest table index for a frequency in Hz.

    Nearest rather than exact for the same reason the volume taper is: the device reports rounded
    text (``31.5 Hz``, ``3.15 kHz``), and a round-trip through that text must land back on the
    index it came from.
    """
    return min(range(len(EQ_FREQUENCIES)), key=lambda i: abs(EQ_FREQUENCIES[i] - hz))


def format_frequency(hz: float) -> str:
    """Label a frequency the way the device and audio people write it: ``1.6 kHz``, ``250 Hz``."""
    if hz >= 1000:
        khz = hz / 1000
        return f"{khz:g} kHz"
    return f"{hz:g} Hz"


def q_for_index(index: int) -> float:
    if not 0 <= index <= MAX_Q_INDEX:
        msg = f"EQ Q index {index} outside 0..{MAX_Q_INDEX}"
        raise ValueError(msg)
    return EQ_Q_VALUES[index]


def index_for_q(q: float) -> int:
    """Nearest table index for a Q value.

    Nearest rather than exact so a value read back from the device -- which prints ``1`` for 1.0
    and ``3`` for 3.0 -- lands on the index it came from.
    """
    return min(range(len(EQ_Q_VALUES)), key=lambda i: abs(EQ_Q_VALUES[i] - q))


def format_q(q: float) -> str:
    """Label a Q the way the device prints it: ``0.7``, ``1``, ``3``."""
    return f"{q:g}"
