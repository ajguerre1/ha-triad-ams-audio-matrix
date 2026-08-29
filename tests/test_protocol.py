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
        """The vendor driver's own constant declares 03 here, and the device rejects it.

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

    def test_eq_frequencies_above_a_kilohertz_carry_their_multiplier(self) -> None:
        """All captured 2026-08-29. The kHz suffix is not decoration.

        A regex that grabs the number and ignores the unit turns 1.6 kHz into 1.6 Hz -- three
        orders of magnitude out, and entirely plausible-looking in a UI. Every band above 1 kHz
        reports this way, so it is the common case, not an edge one.
        """
        assert p.parse_eq_frequency("Get Out[1] Band 3 Freq : 1 kHz") == (1, 3, 1000.0)
        assert p.parse_eq_frequency("Get Out[2] Band 3 Freq : 1.6 kHz") == (2, 3, 1600.0)
        assert p.parse_eq_frequency("Get Out[2] Band 4 Freq : 2.5 kHz") == (2, 4, 2500.0)
        assert p.parse_eq_frequency("Get Out[1] Band 5 Freq : 20 kHz") == (1, 5, 20000.0)
        assert p.parse_eq_frequency("Get Out[1] Band 2 Freq : 250 Hz") == (1, 2, 250.0)

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
        """Captured 2026-08-29. 0x1 is the 1-minute default the maintainer confirms, not one second.

        Worth pinning because the vendor driver initialises this field to 30, which on a
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


class TestAudioSenseSetters:
    """FR-14. The wire format comes from `ariel_protocol.lua`, not from guesswork.

    Both were reachable all along; they were withheld while the vendor re-asserted its own value on
    every reconnect, which made a control that appeared to work and silently reverted.
    """

    def test_enabling_sends_one_not_zero_despite_the_drivers_function_name(self) -> None:
        """The driver's function is `disableAudioSense(disabled)` and writes 1 for *enabled*.

        Reading the name rather than the body is the obvious way to get this exactly backwards,
        and the mistake is invisible: both values are accepted, and the device reports success
        either way. Pinned here so the inversion cannot be reintroduced silently.
        """
        assert p.set_audio_sense_enabled(enabled=True) == bytes.fromhex("FF55040AA201FF")
        assert p.set_audio_sense_enabled(enabled=False) == bytes.fromhex("FF55040AA200FF")

    def test_the_trailing_ff_byte_is_sent_though_its_purpose_is_undocumented(self) -> None:
        """The driver always packs 255 as the second argument and never explains why.

        Copied rather than dropped: an unexplained constant that real firmware has always received
        is not a byte to omit on the grounds that we cannot account for it.
        """
        assert p.set_audio_sense_enabled(enabled=True)[-1] == 0xFF

    def test_the_off_delay_setter_mirrors_its_query_with_the_marker_replaced(self) -> None:
        """Query is 0A A3 F5 00; the setter puts 00 where F5 was and the value after it."""
        assert p.set_audio_sense_off_delay(1) == bytes.fromhex("FF55040AA30001")
        assert p.set_audio_sense_off_delay(30) == bytes.fromhex("FF55040AA3001E")

    def test_an_off_delay_outside_one_byte_is_refused_rather_than_truncated(self) -> None:
        """The value is packed into a single byte. 256 would silently become 0 -- which reads as
        'no delay' and is the opposite of what the caller asked for."""
        for bad in (-1, 256):
            with pytest.raises(ValueError):
                p.set_audio_sense_off_delay(bad)


class TestAddressingMode:
    """FR-17. The command's name in the driver promises more than it delivers."""

    def test_the_query_is_the_drivers_constant(self) -> None:
        assert p.query_ip_mode() == bytes.fromhex("FF55030881F5")

    def test_dynamic_ip_is_what_dhcp_looks_like(self) -> None:
        """Measured 2026-08-29 on an AMS8 (V1.05.74) and an AMS24 (V1.06.84).

        Both answer the literal `dynamic_ip`. There is no address in the response, which is why
        the function is not called `query_ip_address` despite the driver's constant being
        `getIpAddress` -- naming it that would promise something it does not return.
        """
        assert p.parse_ip_mode("dynamic_ip") == "dhcp"

    def test_an_unrecognised_answer_is_returned_rather_than_raised(self) -> None:
        """The deliberate exception to this module's parse-or-raise rule.

        This feeds a diagnostic whose job is to report what the unit says. A firmware answering
        something new is the case most worth seeing, and a ParseError would hide exactly the
        information the entity exists to surface. The static spelling is itself unverified -- no
        unit here is statically addressed.
        """
        assert p.parse_ip_mode("something_new") == "something_new"

    def test_a_command_error_still_raises(self) -> None:
        """Tolerating unknown text must not extend to tolerating a failed command."""
        with pytest.raises(CommandError):
            p.parse_ip_mode("Command error")


class TestBuildersNothingElseExercises:
    """Builders reachable only from paths no other test walks.

    Several are commands the integration deliberately does not expose -- power, the group set, the
    device's own volume-step opcode. They are implemented because the protocol has them and the
    document records them; leaving them unasserted means the wire format could drift from the
    document with nothing to say so.
    """

    def test_the_devices_own_volume_step_opcodes(self) -> None:
        """Not used by `media_player`, which steps from the last reading instead -- this opcode
        ignores the configured cap, so a zone could be nudged past it one press at a time."""
        assert p.step_output_volume(AMS8, 1, up=True) == bytes.fromhex("FF5503031300")
        assert p.step_output_volume(AMS8, 1, up=False) == bytes.fromhex("FF5503031400")
        assert p.step_output_volume(AMS8, 1, up=True, large=True) == bytes.fromhex("FF5503031500")
        assert p.step_output_volume(AMS8, 1, up=False, large=True) == bytes.fromhex("FF5503031600")

    def test_treble_and_balance_share_the_tone_encoding(self) -> None:
        assert p.set_output_treble(AMS8, 1, 0) == bytes.fromhex("FF5504033000") + b""
        assert p.set_output_balance(AMS8, 1, 0) == bytes.fromhex("FF55040331 0018".replace(" ", ""))
        # -12 is full left / full cut, +12 full right / full boost.
        assert p.set_output_balance(AMS8, 1, -12)[-1] == 0x00
        assert p.set_output_balance(AMS8, 1, 12)[-1] == 0x30

    def test_power_is_built_though_the_integration_never_sends_it(self) -> None:
        """`media_player` on/off means routing. The device's power-on delay is long enough that the
        vendor driver disables the command outright, and this follows it."""
        assert p.query_power() == bytes.fromhex("FF55030101F5")
        assert p.set_power(on=True) == bytes.fromhex("FF5503010100")
        assert p.set_power(on=False) == bytes.fromhex("FF5503010200")

    def test_the_mac_query(self) -> None:
        assert p.query_mac_address() == bytes.fromhex("FF55030880F5")

    def test_the_group_commands_exist_although_no_hardware_here_uses_them(self) -> None:
        """FR-07 was withdrawn on measurement -- all seven groups are empty on all three matrices
        and the vendor driver never calls `setOutputToGroup`. The wire format is still correct
        and still recorded, so it stays asserted."""
        assert p.assign_output_to_group(AMS8, 1, 1) == bytes.fromhex("FF550403320000")
        assert p.query_group_volume(1) == bytes.fromhex("FF5504044 7F500".replace(" ", ""))
        assert p.query_group_source(1) == bytes.fromhex("FF5504044 8F500".replace(" ", ""))

    @pytest.mark.parametrize("group", [0, 8, -1])
    def test_a_group_outside_a_to_g_is_refused(self, group: int) -> None:
        with pytest.raises(ValueError, match=r"outside 1\.\.7"):
            p.query_group_volume(group)


class TestValuesRefusedBeforeTheWire:
    """The device accepts several out-of-range writes and reports success, so the guard has to be
    here. Each of these would otherwise be a silent wrong value rather than an error."""

    @pytest.mark.parametrize("step", [-1, 101, 255])
    def test_a_volume_step_outside_the_scale(self, step: int) -> None:
        with pytest.raises(ValueError, match="volume step"):
            p.set_output_volume(AMS8, 1, step)

    @pytest.mark.parametrize("db", [-12.5, 12.5, 100])
    def test_a_tone_value_outside_twelve_decibels(self, db: float) -> None:
        with pytest.raises(ValueError, match=r"outside -12\.\.\+12"):
            p.set_output_bass(AMS8, 1, db)

    @pytest.mark.parametrize("gain", [-0.5, 12.5])
    def test_an_input_gain_outside_the_boost_only_range(self, gain: float) -> None:
        """Boost only, and the device **clamps** above 0x18 while reporting success -- raw 0x1E
        still reads back 12. An out-of-range write would look like it worked."""
        with pytest.raises(ValueError, match="input gain"):
            p.set_input_gain(AMS8, 1, gain)


class TestParsersRejectingMalformedText:
    """The failure branches. Each exists because the alternative is believing a wrong value."""

    def test_a_source_line_without_a_recognisable_input(self) -> None:
        with pytest.raises(ParseError):
            p.parse_output_route("Get Out[1] Input Source : something else entirely")

    def test_a_mute_line_with_an_unknown_word(self) -> None:
        with pytest.raises(ParseError):
            p.parse_output_mute("Get Out[1] Mute status : perhaps")

    def test_a_balance_line_that_is_neither_centre_nor_a_side(self) -> None:
        with pytest.raises(ParseError):
            p.parse_output_balance("Get Out[1] Balance : sideways")

    def test_balance_reports_left_as_negative_and_right_as_positive(self) -> None:
        """Sign convention, and it is the one place the device answers in words. Getting it
        backwards would swap the channels of every corrected zone."""
        assert p.parse_output_balance("Get Out[1] Balance : Bal L6") == (1, -6.0)
        assert p.parse_output_balance("Get Out[1] Balance : Bal R6") == (1, 6.0)

    def test_an_eq_frequency_with_an_unparseable_unit(self) -> None:
        with pytest.raises(ParseError):
            p.parse_eq_frequency("Get Out[1] Band 1 Freq : 63 furlongs")

    def test_the_mac_address_is_read_out_of_its_line(self) -> None:
        assert p.parse_mac_address("Get MAC Add AA:BB:CC:DD:EE:FF") == "AA:BB:CC:DD:EE:FF"

    def test_a_static_address_reads_as_static(self) -> None:
        """Unverified against hardware -- no unit here is statically addressed -- so this pins the
        spelling the parser expects rather than one observed on a device."""
        assert p.parse_ip_mode("static_ip") == "static"
