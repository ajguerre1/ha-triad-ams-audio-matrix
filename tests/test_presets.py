"""The generic EQ presets. FR-16."""

from __future__ import annotations

import pytest
from ams import presets
from ams.eq import EQ_FREQUENCIES, EQ_Q_VALUES


class TestPresetTable:
    def test_every_preset_has_exactly_five_bands(self) -> None:
        """The device has five. A preset with any other count cannot be applied at all."""
        for name, bands in presets.PRESETS.items():
            assert len(bands) == presets.BANDS_PER_PRESET, name

    def test_every_index_resolves_to_a_real_frequency_and_q(self) -> None:
        """Guards against a transcription slip in the table, which is otherwise invisible.

        An out-of-range Q index does not fail loudly on this hardware -- the device clamps it and
        reports success -- so a bad literal would ship and simply sound wrong.
        """
        for name, bands in presets.PRESETS.items():
            for band in bands:
                assert band.frequency_hz in EQ_FREQUENCIES, name
                assert band.q in EQ_Q_VALUES, name

    def test_flat_is_actually_flat(self) -> None:
        """The one preset whose correctness can be asserted rather than merely checked."""
        assert all(band.gain_db == 0 for band in presets.PRESETS["Flat"])

    def test_the_filter_presets_keep_the_drivers_band_order(self) -> None:
        """High Pass and Low Pass are not ordered by frequency, and that is deliberate.

        Band order is what reaches the device, so sorting these into ascending frequency would
        move each correction onto a different band -- a tidy-looking change that alters the sound.
        """
        high_pass = [band.frequency_index for band in presets.PRESETS["High Pass"]]
        assert high_pass == [11, 9, 6, 3, 0]
        assert high_pass != sorted(high_pass)

    def test_a_bad_band_is_refused_at_construction(self) -> None:
        for bad in (
            {"frequency_index": 31, "gain_db": 0, "q_index": 0},
            {"frequency_index": 0, "gain_db": 0, "q_index": 8},
            {"frequency_index": 0, "gain_db": 13, "q_index": 0},
        ):
            with pytest.raises(ValueError):
                presets.PresetBand(**bad)


class TestLookup:
    def test_names_are_matched_case_insensitively(self) -> None:
        assert presets.bands_for("rock") == presets.PRESETS["Rock"]
        assert presets.bands_for("HIGH PASS") == presets.PRESETS["High Pass"]

    def test_an_unknown_name_raises_and_lists_the_real_ones(self) -> None:
        """Falling back to Flat would silently apply the opposite of what was asked for."""
        with pytest.raises(KeyError) as excinfo:
            presets.bands_for("Dubstep")
        assert "Rock" in str(excinfo.value)


def test_a_preset_with_the_wrong_band_count_is_refused() -> None:
    """The device has exactly five bands. Four would leave the fifth holding an unrelated
    correction from whatever was applied before, which is worse than refusing outright."""
    with pytest.raises(ValueError, match="exactly 5 bands"):
        presets._preset((5, 0, 2), (11, 0, 2))
