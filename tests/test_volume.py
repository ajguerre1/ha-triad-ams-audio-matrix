"""The device sets volume in steps and reports it in decibels, so the two must round-trip.

The curve is not linear and not invertible by formula -- it is a 101-point table lifted from the
Control4 driver's ``g_dbVolMap``. These tests pin the properties that matter at the boundaries,
where a plausible-looking implementation goes wrong quietly.
"""

from __future__ import annotations

import pytest
from ams.volume import MAX_STEP, db_for_step, step_for_db


def test_the_curve_spans_every_step_the_device_accepts() -> None:
    assert MAX_STEP == 100
    assert db_for_step(0) == -108.0
    assert db_for_step(MAX_STEP) == 0.0


def test_steps_map_to_the_decibel_values_the_device_reports() -> None:
    # Sampled from the driver table; -39.7 was also observed on real hardware.
    assert db_for_step(25) == -39.7
    assert db_for_step(50) == -21.2
    assert db_for_step(80) == -6.0


def test_reported_decibels_map_back_to_the_step_that_produced_them() -> None:
    for step in range(MAX_STEP + 1):
        assert step_for_db(db_for_step(step)) == step


def test_a_decibel_value_absent_from_the_table_resolves_to_the_nearest_step() -> None:
    """The reason this function exists.

    Real hardware reported ``-108.5`` for a silent output. The driver's table has no such key --
    its lowest is ``-108`` -- so an exact-key lookup returns nothing and the first muted zone
    reads as an error. Nearest-match is not a nicety here, it is the only thing that works.
    """
    assert step_for_db(-108.5) == 0
    assert step_for_db(-39.68) == 25
    assert step_for_db(-0.5) in (98, 99)


def test_decibels_beyond_the_curve_clamp_instead_of_extrapolating() -> None:
    assert step_for_db(-200.0) == 0
    assert step_for_db(12.0) == MAX_STEP


@pytest.mark.parametrize("step", [-1, MAX_STEP + 1, 500])
def test_an_out_of_range_step_is_rejected_rather_than_silently_clamped(step: int) -> None:
    """A caller asking for step 137 has a bug; clamping would hide it.

    Clamping belongs at the entity boundary where a user-supplied percentage arrives, not here.
    """
    with pytest.raises(ValueError):
        db_for_step(step)
