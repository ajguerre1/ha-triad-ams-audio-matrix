"""Command construction and response parsing.

Every response string asserted here was captured from real hardware -- an AMS8 on firmware
V1.05.74 and two AMS24s on V1.06.84 -- and is quoted verbatim from docs/triad-ams-protocol.md.
Inventing plausible-looking response text would defeat the purpose of the exercise.
"""

from __future__ import annotations

import pytest
from ams import protocol as p
from ams.errors import CommandError, ParseError
from ams.model import MatrixSpec

AMS8 = MatrixSpec.for_model("AMS8")
AMS24 = MatrixSpec.for_model("AMS24")


class TestCommandFraming:
    def test_a_query_sets_the_length_byte_to_match_its_extra_marker(self) -> None:
        # The set form carries output+value; the query form carries the F5 marker plus output.
        assert p.set_output_volume(AMS24, 1, 0x05) == bytes.fromhex("FF5504031E0005")
        assert p.query_output_volume(AMS24, 1) == bytes.fromhex("FF5504031EF500")

    def test_indices_go_on_the_wire_zero_based(self) -> None:
        """Responses print 1-based, so the whole codebase speaks 1-based and converts here."""
        assert p.query_output_volume(AMS24, 1)[-1] == 0
        assert p.query_output_volume(AMS24, 24)[-1] == 23

    @pytest.mark.parametrize("output", [0, -1, 25])
    def test_an_output_outside_the_matrix_is_refused_before_it_reaches_the_wire(
        self, output: int
    ) -> None:
        with pytest.raises(ValueError):
            p.query_output_volume(AMS24, output)

    def test_routing_carries_both_indices_zero_based(self) -> None:
        assert p.set_route(AMS24, output=7, source=7) == bytes.fromhex("FF5504031D0606")

    def test_disconnecting_routes_one_past_the_last_input(self) -> None:
        """There is no disconnect opcode; 'off' is an out-of-range input index."""
        assert p.disconnect_output(AMS8, 1) == bytes.fromhex("FF5504031D0008")
        assert p.disconnect_output(AMS24, 1) == bytes.fromhex("FF5504031D0018")

    def test_the_mute_query_uses_the_length_byte_that_actually_works(self) -> None:
        """The Control4 driver's own constant declares 03 here, and the device rejects it.

        Captured: FF55030317F500 -> 'Command error'; FF55040317F500 -> the mute status.
        """
        assert p.query_output_mute(AMS24, 1) == bytes.fromhex("FF55040317F500")

    def test_eq_opcodes_are_a_base_plus_the_band_index(self) -> None:
        assert p.query_eq_frequency(AMS24, 1, band=1) == bytes.fromhex("FF55040320F500")
        assert p.query_eq_frequency(AMS24, 1, band=5) == bytes.fromhex("FF55040324F500")
        assert p.query_eq_gain(AMS24, 1, band=1) == bytes.fromhex("FF55040325F500")
        assert p.query_eq_q(AMS24, 1, band=1) == bytes.fromhex("FF5504032AF500")

    @pytest.mark.parametrize("band", [0, 6])
    def test_an_eq_band_outside_one_to_five_is_refused(self, band: int) -> None:
        with pytest.raises(ValueError):
            p.query_eq_gain(AMS24, 1, band=band)

    def test_the_asg_trigger_opcode_depends_on_the_model(self) -> None:
        """An 8x8 has no 9-16 bank, so ASG reuses that opcode. Getting this wrong on a 24x24
        toggles the wrong bank silently."""
        assert p.set_trigger_asg(AMS8, on=True) == bytes.fromhex("FF5503055001")
        assert p.set_trigger_asg(AMS24, on=True) == bytes.fromhex("FF5503055003")


class TestFrameDecoding:
    def test_a_single_nul_terminator_is_stripped(self) -> None:
        assert p.decode_frame(b"Fw version : V1.05.74\x00") == "Fw version : V1.05.74"

    def test_the_hundred_and_fifty_byte_padded_error_frame_decodes_to_its_text(self) -> None:
        """Captured from an AMS8: 'Command error' then 136 NULs, padding the frame to 150 bytes.

        Left unhandled, the padding is read as the next command's response and every subsequent
        exchange on the connection is answering the previous question.
        """
        frame = b"Command error" + b"\x00" * 137
        assert len(frame) == 150
        assert p.decode_frame(frame) == "Command error"

    def test_an_error_response_is_recognised_rather_than_parsed(self) -> None:
        assert p.is_command_error("Command error")
        assert p.is_command_error("COMMAND ERROR")
        assert not p.is_command_error("Get Out[1] Volume : -39.7")


class TestResponseParsing:
    def test_volume_is_reported_in_decibels(self) -> None:
        assert p.parse_output_volume("Get Out[1] Volume : -39.7") == (1, -39.7)
        assert p.parse_output_volume("Get Out[6] Volume : -108.5") == (6, -108.5)
        assert p.parse_output_volume("Get Out[3] Volume : 0") == (3, 0.0)

    def test_a_routed_output_reports_its_one_based_input(self) -> None:
        assert p.parse_output_route("Get Out[7] Input Source : input 7") == (7, 7)

    def test_an_unrouted_output_reports_no_source_rather_than_input_zero(self) -> None:
        assert p.parse_output_route("Get Out[1] Input Source : Audio Off") == (1, None)

    def test_mute_state_survives_the_wording_the_firmware_chooses(self) -> None:
        assert p.parse_output_mute("Get Out[1] Mute status : Unmute") == (1, False)
        assert p.parse_output_mute("Get Out[5] Mute status : mute") == (5, True)

    def test_two_digit_output_indices_parse(self) -> None:
        """A 24x24 spends most of its range in double digits; a \\d regex would clip to 1."""
        assert p.parse_output_volume("Get Out[24] Volume : 0") == (24, 0.0)
        assert p.parse_output_route("Get Out[10] Input Source : input 10") == (10, 10)

    def test_tone_controls_parse_as_numbers(self) -> None:
        assert p.parse_output_bass("Get Out[1] Bass : 0") == (1, 0)
        assert p.parse_output_treble("Get Out[1] Treble : 0") == (1, 0)

    def test_balance_reports_words_not_numbers(self) -> None:
        """'Bal Center' is what the device says. A float parse here raises on real hardware."""
        assert p.parse_output_balance("Get Out[1] Balance : Bal Center") == (1, 0)

    def test_loudness_and_mono_report_status_words(self) -> None:
        assert p.parse_output_loudness("Get Out[1] Loudness status : Off") == (1, False)
        assert p.parse_output_mono("Get Out[1] Stereo Mono status : mono") == (1, True)

    def test_eq_bands_report_human_units(self) -> None:
        assert p.parse_eq_frequency("Get Out[1] Band 1 Freq : 63 Hz") == (1, 1, 63.0)
        assert p.parse_eq_gain("Get Out[1] Band 1 Gain : 0") == (1, 1, 0.0)
        assert p.parse_eq_q("Get Out[1] Band 1 Q : 0.7") == (1, 1, 0.7)

    def test_input_gain_parses_from_the_in_prefix(self) -> None:
        assert p.parse_input_gain("Get In[1] input gain : 0") == (1, 0)

    def test_audio_sense_indices_are_zero_based_unlike_every_other_response(self) -> None:
        """The one place the device prints a 0-based index. Input[0] is input 1."""
        assert p.parse_audio_sense("AudioSense:Input[0]: 1") == (1, True)
        assert p.parse_audio_sense("AudioSense:Input[3]: 0") == (4, False)

    def test_a_value_of_two_means_the_matrix_is_not_measuring_at_all(self) -> None:
        """Measured 2026-08-29, and this is the deliberate change the old test was pinned against.

        ``2`` is what the device reports when audio sense is DISABLED. It is not a signal state:
        an input carrying live music reads 2 identically to a dead one. Mapping it to False would
        assert "there is no audio", which the device has not determined and cannot -- so it
        returns None, and the entity layer turns that into `unavailable`.
        """
        assert p.parse_audio_sense("AudioSense:Input[0]: 2") == (1, None)

    def test_an_unknown_audio_sense_value_is_also_treated_as_not_measuring(self) -> None:
        """Only 0 and 1 are known signal states; anything else is 'the device did not say'."""
        assert p.parse_audio_sense("AudioSense:Input[0]: 7") == (1, None)

    def test_a_trailing_dollar_sign_some_firmware_appends_is_tolerated(self) -> None:
        assert p.parse_audio_sense("AudioSense:Input[0]: 1 $") == (1, True)

    def test_the_off_delay_is_hex_and_measured_in_minutes(self) -> None:
        """Captured 2026-08-29. 0x1 is the 1-minute default the owner confirms, not one second.

        Worth pinning because the Control4 driver initialises this field to 30, which on a
        minutes scale is half an hour rather than half a minute.
        """
        assert p.parse_audio_sense_off_delay("Get Analog nosignal sleep timeout : 0x1") == 1
        assert p.parse_audio_sense_off_delay("Get Analog nosignal sleep timeout : 0x1E") == 30

    def test_audio_sense_enable_parses_both_words(self) -> None:
        assert p.parse_audio_sense_enabled("Get AutoSenseEnable : Enable") is True
        assert p.parse_audio_sense_enabled("Get AutoSenseEnable : Disable") is False

    def test_an_empty_group_is_reported_as_empty_not_as_a_failure(self) -> None:
        assert p.parse_group_membership("Group[A] is empty") == ("A", False)

    def test_trigger_banks_report_their_own_names(self) -> None:
        assert p.parse_trigger("Get Zone 1-8 trigger status : Off") == ("1-8", False)
        assert p.parse_trigger("Get ASG trigger status : Off") == ("ASG", False)

    def test_system_responses_parse(self) -> None:
        assert p.parse_firmware("Fw version : V1.05.74") == "V1.05.74"
        assert p.parse_power("Get Power status : Working") is True

    def test_parsing_an_error_frame_raises_command_error_not_a_parse_failure(self) -> None:
        """The caller retries a CommandError and gives up on a ParseError, so the distinction
        decides whether a transient firmware hiccup takes the integration down."""
        with pytest.raises(CommandError):
            p.parse_output_volume("Command error")

    def test_an_unrecognised_response_raises_rather_than_returning_a_default(self) -> None:
        """Returning 0.0 here would render a silent zone as full volume in the UI."""
        with pytest.raises(ParseError):
            p.parse_output_volume("Get Out[1] Volume : banana")

    def test_an_empty_frame_is_retryable_not_a_parse_failure(self) -> None:
        """The firmware returns empty frames intermittently on healthy connections.

        This is the whole reason the two exception types exist. Classifying an empty frame as a
        ParseError would make a transient hiccup permanent, because the caller does not retry
        those -- a zone would go unavailable until Home Assistant restarted.
        """
        with pytest.raises(CommandError):
            p.parse_output_volume("")
