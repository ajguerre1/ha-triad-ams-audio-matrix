"""Conversion between the device's volume steps and the decibels it reports.

The matrix is asymmetric: it is *set* with a step in ``0x00``--``0x64`` but *reports* a decibel
figure. The mapping between them is a measured taper, not a formula, so the only faithful
conversion is the manufacturer's own table.

The table below is transcribed from ``g_dbVolMap`` in the vendor driver's ``driver.lua`` --
101 points, one per step. It is reproduced rather than approximated because a fitted curve would
disagree with the hardware in the middle of the range, where listening actually happens.

No Home Assistant imports here, deliberately: this module is testable on a Windows dev box, where
Home Assistant itself cannot be imported.
"""

from __future__ import annotations

from bisect import bisect_left

#: The step range the device accepts, 0x00..0x64. Defined here and only here: this module owns
#: the volume scale, and a second copy in the protocol module is a constant waiting to drift.
MIN_STEP = 0
MAX_STEP = 100

#: Decibels reported for each step, indexed by step. Ascending, which ``step_for_db`` relies on.
#:
#: Laid out ten steps per row so the shape of the taper stays visible -- it is very steep below
#: step 10 and nearly linear above step 40, which is the reason a fitted curve was rejected.
#: Formatting is fenced off because one value per line turns a readable table into 101 lines.
# fmt: off
_DB_BY_STEP: tuple[float, ...] = (
    -108.0, -100.0,  -92.7,  -85.8,  -79.5,  -73.9,  -69.0,  -64.6,  -61.0,  -58.0,  # 0-9
     -55.6,  -53.9,  -52.0,  -50.5,  -49.6,  -48.7,  -47.7,  -46.8,  -45.9,  -45.0,  # 10-19
     -44.1,  -43.2,  -42.3,  -41.4,  -40.6,  -39.7,  -38.9,  -38.0,  -37.2,  -36.4,  # 20-29
     -35.6,  -34.8,  -34.0,  -33.2,  -32.4,  -31.7,  -30.9,  -30.2,  -29.4,  -28.7,  # 30-39
     -28.0,  -27.2,  -26.5,  -25.8,  -25.1,  -24.5,  -23.8,  -23.1,  -22.5,  -21.8,  # 40-49
     -21.2,  -20.5,  -19.9,  -19.3,  -18.7,  -18.1,  -17.5,  -16.9,  -16.4,  -15.8,  # 50-59
     -15.3,  -14.7,  -14.2,  -13.7,  -13.1,  -12.6,  -12.1,  -11.6,  -11.1,  -10.7,  # 60-69
     -10.2,   -9.7,   -9.3,   -8.9,   -8.4,   -8.0,   -7.6,   -7.2,   -6.8,   -6.4,  # 70-79
      -6.0,   -5.6,   -5.3,   -4.9,   -4.6,   -4.2,   -3.9,   -3.6,   -3.3,   -3.0,  # 80-89
      -2.7,   -2.4,   -2.1,   -1.8,   -1.6,   -1.3,   -1.1,   -0.9,   -0.6,   -0.4,  # 90-99
       0.0,                                                                          # 100
)
# fmt: on


def db_for_step(step: int) -> float:
    """Return the decibel figure the device reports for ``step``.

    Raises ``ValueError`` outside 0..MAX_STEP rather than clamping. Clamping belongs at the entity
    boundary, where a user-supplied percentage arrives; a caller passing step 137 has a bug, and
    silently correcting it would hide the bug rather than surface it.
    """
    if not 0 <= step <= MAX_STEP:
        msg = f"volume step {step} outside 0..{MAX_STEP}"
        raise ValueError(msg)
    return _DB_BY_STEP[step]


def step_for_db(db: float) -> int:
    """Return the step whose reported decibels are closest to ``db``.

    Nearest-match, not exact lookup. Real hardware reports values absent from the table -- a silent
    output on firmware V1.05.74 answers ``-108.5``, where the table's lowest point is ``-108.0``.
    An exact lookup fails on the first muted zone it meets.

    Values beyond either end clamp, since the device cannot report outside its own taper and an
    extrapolated step would be meaningless.
    """
    if db <= _DB_BY_STEP[0]:
        return 0
    if db >= _DB_BY_STEP[-1]:
        return MAX_STEP

    above = bisect_left(_DB_BY_STEP, db)
    below = above - 1
    # Ties resolve downward: of two equally close steps, the quieter one is the safer guess.
    if _DB_BY_STEP[above] - db < db - _DB_BY_STEP[below]:
        return above
    return below
