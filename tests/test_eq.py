"""The EQ frequency table and its text format."""

from __future__ import annotations

import pytest
from ams.eq import (
    EQ_FREQUENCIES,
    EQ_Q_VALUES,
    MAX_FREQUENCY_INDEX,
    MAX_Q_INDEX,
    format_frequency,
    format_q,
    frequency_for_index,
    index_for_frequency,
    index_for_q,
    parse_frequency_text,
    q_for_index,
)


class TestTheTable:
    def test_it_spans_exactly_the_range_the_driver_documents(self) -> None:
        """0x00 is 20 Hz and 0x1E is 20 kHz, per the Control4 driver. 31 steps between them is
        exactly the ISO 1/3-octave series, which is what makes the interior inferable."""
        assert MAX_FREQUENCY_INDEX == 30
        assert frequency_for_index(0) == 20
        assert frequency_for_index(30) == 20000

    @pytest.mark.parametrize("hz", [63, 250, 800, 1000, 1600, 2500, 4000, 16000, 20000])
    def test_every_frequency_observed_on_hardware_is_in_the_table(self, hz: float) -> None:
        """Captured across two matrices. A table that failed to contain a real reading would be
        the wrong table, whatever its provenance."""
        assert hz in EQ_FREQUENCIES

    def test_the_factory_band_defaults_land_on_a_sensible_spread(self) -> None:
        """Bands default to 63 Hz, 250 Hz, 1 kHz, 4 kHz, 20 kHz. On this table those are indices
        5, 11, 17, 23, 30 -- evenly spaced, which is corroboration rather than coincidence."""
        assert [index_for_frequency(f) for f in (63, 250, 1000, 4000, 20000)] == [5, 11, 17, 23, 30]

    @pytest.mark.parametrize("index", [-1, 31])
    def test_an_index_outside_the_table_is_refused(self, index: int) -> None:
        with pytest.raises(ValueError):
            frequency_for_index(index)


class TestParsingWhatTheDeviceSays:
    @pytest.mark.parametrize(
        ("text", "hz"),
        [
            ("63 Hz", 63.0),
            ("250 Hz", 250.0),
            ("1 kHz", 1000.0),
            ("1.6 kHz", 1600.0),
            ("2.5 kHz", 2500.0),
            ("20 kHz", 20000.0),
        ],
    )
    def test_the_unit_is_honoured(self, text: str, hz: float) -> None:
        assert parse_frequency_text(text) == hz

    def test_a_frequency_without_a_unit_is_refused_rather_than_guessed(self) -> None:
        """Assuming Hz would silently turn a kHz reading into a sub-audible one."""
        with pytest.raises(ValueError):
            parse_frequency_text("Band 1 Freq : 1.6")


class TestRoundTrip:
    def test_every_index_survives_a_trip_through_the_devices_own_text(self) -> None:
        """The device reports rounded text (31.5 Hz, 3.15 kHz). A value written as an index and
        read back as text must land on the index it started from, or the UI drifts on every poll.
        """
        for index in range(MAX_FREQUENCY_INDEX + 1):
            hz = frequency_for_index(index)
            assert index_for_frequency(parse_frequency_text(format_frequency(hz))) == index

    def test_labels_read_the_way_audio_people_write_them(self) -> None:
        assert format_frequency(63) == "63 Hz"
        assert format_frequency(31.5) == "31.5 Hz"
        assert format_frequency(1000) == "1 kHz"
        assert format_frequency(1600) == "1.6 kHz"
        assert format_frequency(20000) == "20 kHz"


class TestTheQTable:
    """Measured 2026-08-29, not inferred.

    Swept the index on an AMS8 output that was unrouted, at minimum volume and muted, then
    restored it and verified on a fresh connection. Eight discrete values and a hard clamp.
    """

    def test_it_has_the_eight_values_the_device_offers(self) -> None:
        assert EQ_Q_VALUES == (0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 2.0, 3.0)
        assert MAX_Q_INDEX == 7

    def test_indices_map_to_the_values_the_hardware_reported(self) -> None:
        assert q_for_index(0) == 0.5
        assert q_for_index(2) == 0.7  # the factory default on every band
        assert q_for_index(7) == 3.0

    @pytest.mark.parametrize("index", [-1, 8, 47])
    def test_an_index_outside_the_table_is_refused(self, index: int) -> None:
        """The device CLAMPS anything above 7 to Q 3 rather than rejecting it.

        So an out-of-range write would appear to succeed while quietly doing something else.
        Refusing here is what stops a caller believing it set Q 5.
        """
        with pytest.raises(ValueError):
            q_for_index(index)

    def test_every_q_survives_a_round_trip_through_the_devices_text(self) -> None:
        """The device prints 1 for 1.0 and 3 for 3.0, so the label must map back to its index."""
        for index in range(MAX_Q_INDEX + 1):
            assert index_for_q(float(format_q(q_for_index(index)))) == index

    def test_labels_drop_the_trailing_zero(self) -> None:
        assert format_q(0.7) == "0.7"
        assert format_q(1.0) == "1"
        assert format_q(3.0) == "3"
