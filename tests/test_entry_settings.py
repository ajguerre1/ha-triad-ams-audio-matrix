"""Resolving a config entry's data and options into settings the platforms can use.

This logic lived as two private functions on ``__init__.py`` that ``media_player.py`` imported by
name from the package root -- a leaked internal, and an import cycle waiting to happen as more
platforms are added.

It takes plain mappings rather than a ``ConfigEntry`` so it has no Home Assistant import, which
is what lets these tests run on the development box at all.
"""

from __future__ import annotations

import pytest
from ams.settings import EntrySettings


def _data(**overrides: object) -> dict[str, object]:
    base = {
        "name": "Matrix",
        "host": "192.0.2.10",
        "port": 52000,
        "model": "AMS8",
        "output_count": 8,
        "input_count": 8,
    }
    return {**base, **overrides}


class TestChannelCounts:
    def test_counts_come_from_the_entry_when_it_records_them(self) -> None:
        settings = EntrySettings.resolve(_data(), {})
        assert settings.spec.outputs == 8
        assert settings.spec.inputs == 8

    def test_an_entry_predating_the_counts_falls_back_to_the_model_table(self) -> None:
        """Entries written by the integration this one replaces are not all identical.

        Raising here would strand a working installation on setup, which is a worse failure than
        deriving the counts from a model name the entry definitely has.
        """
        data = _data(model="AMS24")
        del data["output_count"]
        del data["input_count"]
        assert EntrySettings.resolve(data, {}).spec.outputs == 24

    def test_an_unknown_model_with_no_counts_falls_back_rather_than_failing_setup(self) -> None:
        data = _data(model="AMS99")
        del data["output_count"]
        del data["input_count"]
        assert EntrySettings.resolve(data, {}).spec.outputs == 24


class TestActiveChannels:
    def test_an_empty_selection_means_not_chosen_yet_not_none_wanted(self) -> None:
        """Taking an empty list literally sets the integration up with no entities at all, which
        reads as a broken install rather than as a configuration choice."""
        assert EntrySettings.resolve(_data(), {}).active_outputs == list(range(1, 9))
        assert EntrySettings.resolve(_data(), {"active_outputs": []}).active_outputs == list(
            range(1, 9)
        )

    def test_a_selection_is_honoured_and_sorted(self) -> None:
        settings = EntrySettings.resolve(_data(), {"active_outputs": [5, 1, 3]})
        assert settings.active_outputs == [1, 3, 5]

    def test_a_channel_outside_the_matrix_is_dropped_rather_than_creating_a_dead_entity(
        self,
    ) -> None:
        """Shrinking the model in options can leave stale numbers behind."""
        settings = EntrySettings.resolve(_data(), {"active_outputs": [1, 2, 99]})
        assert settings.active_outputs == [1, 2]

    def test_options_take_precedence_over_data(self) -> None:
        settings = EntrySettings.resolve(_data(active_outputs=[1, 2, 3]), {"active_outputs": [4]})
        assert settings.active_outputs == [4]


class TestVolumeCaps:
    def test_caps_are_keyed_by_int_whichever_way_they_were_stored(self) -> None:
        """Options survive a JSON round-trip, so the same key can arrive as int or str."""
        settings = EntrySettings.resolve(_data(), {"output_max_volumes": {"3": 60, 4: 70}})
        assert settings.max_volume(3) == 60
        assert settings.max_volume(4) == 70

    def test_an_uncapped_output_reports_full_scale(self) -> None:
        assert EntrySettings.resolve(_data(), {}).max_volume(1) == 100

    @pytest.mark.parametrize("stored", [0, -5, 150])
    def test_a_nonsensical_cap_is_clamped_into_range(self, stored: int) -> None:
        """A cap of 0 would mute a zone permanently with no visible cause."""
        settings = EntrySettings.resolve(_data(), {"output_max_volumes": {"1": stored}})
        assert 1 <= settings.max_volume(1) <= 100


class TestScanInterval:
    def test_the_default_applies_when_the_entry_never_said(self) -> None:
        assert EntrySettings.resolve(_data(), {}).scan_interval == 30

    def test_a_configured_interval_is_honoured(self) -> None:
        assert EntrySettings.resolve(_data(), {"scan_interval": 15}).scan_interval == 15


class TestTurnOnVolumeTracking:
    """FR-12. The default is the whole point, so it is pinned rather than assumed."""

    def test_a_missing_key_means_tracking_is_on(self) -> None:
        """Every entry written by the integration this one replaces lacks the key entirely.

        `options.get(key, False)` would read those as "tracking off" and silently drop the
        behaviour for every existing installation on upgrade -- zones would quietly stop resuming
        at the volume they were left at, with nothing in the UI having changed.
        """
        settings = EntrySettings.resolve({"model": "AMS8"}, {})
        assert settings.track_turn_on_volume is True

    def test_an_explicit_false_is_honoured(self) -> None:
        settings = EntrySettings.resolve({"model": "AMS8"}, {"track_turn_on_volume": False})
        assert settings.track_turn_on_volume is False

    def test_an_explicit_true_is_honoured(self) -> None:
        settings = EntrySettings.resolve({"model": "AMS8"}, {"track_turn_on_volume": True})
        assert settings.track_turn_on_volume is True
