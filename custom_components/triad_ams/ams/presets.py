"""The generic EQ presets, as five (frequency, gain, Q) triples each.

Pure data and pure functions -- no Home Assistant, no sockets -- so this is testable on the
development box like everything else under ``ams/``.

## Where these came from, and what is deliberately absent

These seven reproduce the generic presets the Control4 driver ships, so that a zone set to "Rock"
under Control4 sounds the same after the changeover. That is the whole reason for matching the
values rather than inventing equivalents: the point of this integration is that decommissioning
Control4 costs no capability, and "your presets now sound different" would be a cost.

They are also unremarkable as EQ curves — five bands on standard ISO centres, integer gains, the
smiley-face and genre shapes every consumer device has shipped for decades.

**The driver's other 76 presets are not here and will not be.** Those are per-speaker tunings for
Triad's own models, and they are the manufacturer's measurement work rather than a convention.
Documenting a wire protocol for interoperability is one thing; republishing a vendor's speaker
tuning library in a public repository is another. See the design doc's non-goals.

## The encoding

Frequencies and Q are **indices**, not values -- the device takes them that way, and the tables
that resolve them live in :mod:`ams.eq`. Gain is in dB and goes on the wire as a tone byte.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .eq import MAX_FREQUENCY_INDEX, MAX_Q_INDEX, frequency_for_index, q_for_index

#: Bands per preset. The device has exactly five; a preset with any other count cannot be applied.
BANDS_PER_PRESET: Final = 5


@dataclass(frozen=True, slots=True)
class PresetBand:
    """One band of a preset, in the device's own units."""

    frequency_index: int
    gain_db: float
    q_index: int

    def __post_init__(self) -> None:
        """Validate at construction, so a bad table cannot reach the wire.

        These are literals in this module, so an error here is a typo rather than user input --
        which is exactly the kind of mistake that otherwise ships silently, because the device
        clamps out-of-range Q and reports success either way.
        """
        if not 0 <= self.frequency_index <= MAX_FREQUENCY_INDEX:
            msg = f"frequency index {self.frequency_index} outside 0..{MAX_FREQUENCY_INDEX}"
            raise ValueError(msg)
        if not 0 <= self.q_index <= MAX_Q_INDEX:
            msg = f"Q index {self.q_index} outside 0..{MAX_Q_INDEX}"
            raise ValueError(msg)
        if not -12 <= self.gain_db <= 12:
            msg = f"gain {self.gain_db} outside -12..+12 dB"
            raise ValueError(msg)

    @property
    def frequency_hz(self) -> float:
        return frequency_for_index(self.frequency_index)

    @property
    def q(self) -> float:
        return q_for_index(self.q_index)


def _preset(*triples: tuple[int, float, int]) -> tuple[PresetBand, ...]:
    if len(triples) != BANDS_PER_PRESET:
        msg = f"a preset needs exactly {BANDS_PER_PRESET} bands, got {len(triples)}"
        raise ValueError(msg)
    return tuple(PresetBand(f, g, q) for f, g, q in triples)


#: Keyed by the name the user selects. Insertion order is the order they are offered in.
PRESETS: Final[dict[str, tuple[PresetBand, ...]]] = {
    "Flat": _preset((5, 0, 2), (11, 0, 2), (17, 0, 2), (23, 0, 2), (27, 0, 2)),
    "Rock": _preset((4, 5, 2), (10, 2, 2), (16, -1, 2), (22, 2, 2), (28, 5, 2)),
    "Pop": _preset((4, -2, 2), (10, 1, 2), (16, 4, 2), (22, 1, 2), (28, -2, 2)),
    "Jazz": _preset((4, 4, 2), (10, 2, 2), (16, -1, 2), (22, 1, 2), (28, 4, 2)),
    "Classical": _preset((4, 5, 2), (10, 3, 2), (16, -2, 2), (22, 1, 2), (28, 4, 2)),
    # Descending frequency indices are not a transcription error: the driver orders these two by
    # the shape of the filter rather than by frequency, and the band order is what reaches the
    # device, so reordering them "tidily" would change which band holds which correction.
    "High Pass": _preset((11, -5, 3), (9, -5, 2), (6, -12, 0), (3, -12, 0), (0, -12, 0)),
    "Low Pass": _preset((16, -6, 3), (17, -12, 0), (19, -12, 0), (21, -12, 0), (29, -12, 0)),
}

PRESET_NAMES: Final[tuple[str, ...]] = tuple(PRESETS)


def bands_for(name: str) -> tuple[PresetBand, ...]:
    """Look a preset up by name, case-insensitively.

    Raises ``KeyError`` with the available names, because the alternative -- falling back to Flat
    -- would silently apply the opposite of what an automation asked for.
    """
    for candidate, bands in PRESETS.items():
        if candidate.casefold() == name.casefold():
            return bands
    msg = f"{name!r} is not a known preset; try one of {', '.join(PRESET_NAMES)}"
    raise KeyError(msg)
