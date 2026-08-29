"""The async client, driven against the simulator over a real loopback socket.

Marked ``enable_socket`` because pytest-homeassistant-custom-component brings pytest-socket,
which blocks sockets for the whole session in CI.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest import mock

import pytest
from ams import protocol as p
from ams.client import AmsClient
from ams.errors import CommandError, ParseError, TransportError, TriadError
from ams.model import MatrixSpec

from tests.simulator import AmsSimulator, Fault, Padding

pytestmark = pytest.mark.enable_socket


async def _client(sim: AmsSimulator) -> AmsClient:
    return AmsClient("127.0.0.1", sim.port, spec=MatrixSpec.for_model(sim.state.model))


class TestExchange:
    async def test_a_query_returns_the_devices_answer(self) -> None:
        async with AmsSimulator() as sim:
            client = await _client(sim)
            assert await client.firmware_version() == "V1.05.74"
            await client.disconnect()

    async def test_reading_an_output_returns_its_routing_and_volume(self) -> None:
        async with AmsSimulator() as sim:
            sim.mutate(3, source=5, step=100)
            client = await _client(sim)
            assert await client.get_route(3) == 5
            assert await client.get_volume_step(3) == 100
            await client.disconnect()

    async def test_an_unrouted_output_reads_as_no_source(self) -> None:
        async with AmsSimulator() as sim:
            client = await _client(sim)
            assert await client.get_route(1) is None
            await client.disconnect()

    async def test_a_write_then_read_round_trips_through_the_device(self) -> None:
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.set_route(2, 4)
            assert await client.get_route(2) == 4
            await client.set_mute(2, mute=True)
            assert await client.get_mute(2) is True
            await client.disconnect()

    async def test_disconnecting_an_output_clears_its_source(self) -> None:
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.set_route(1, 3)
            await client.disconnect_output(1)
            assert await client.get_route(1) is None
            await client.disconnect()


class TestFraming:
    @pytest.mark.parametrize("padding", list(Padding))
    async def test_both_firmware_framings_stay_in_step_over_many_exchanges(
        self, padding: Padding
    ) -> None:
        """The failure this guards against is silent and cumulative.

        With 150-byte padded frames, a reader that stops at the first NUL leaves 137 bytes
        buffered; the next query then reads the *previous* answer. Everything still parses, so
        nothing raises -- outputs simply report each other's state, one behind. Asking a series of
        questions with distinguishable answers is what catches it.
        """
        async with AmsSimulator(outputs=8, padding=padding) as sim:
            for output in range(1, 9):
                sim.mutate(output, source=output)
            client = await _client(sim)
            for output in range(1, 9):
                assert await client.get_route(output) == output, f"desync at output {output}"
            await client.disconnect()


class TestFaults:
    async def test_a_command_error_raises_without_dropping_the_connection(self) -> None:
        """Distinguishing this from a transport failure is the point.

        Real firmware answers 'Command error' on a healthy socket. Tearing the connection down
        turns an occasional hiccup into a reconnect loop.
        """
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.COMMAND_ERROR
            with pytest.raises(CommandError):
                await client.get_route(1)
            assert client.connected, "a command error must not close the socket"
            assert await client.get_route(1) is None, "the connection must still be usable"
            await client.disconnect()

    async def test_an_empty_frame_is_treated_the_same_as_a_command_error(self) -> None:
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.EMPTY
            with pytest.raises(CommandError):
                await client.get_route(1)
            assert client.connected
            await client.disconnect()

    async def test_a_response_about_a_different_output_is_rejected(self) -> None:
        """The desync guard, tested directly rather than only via the framing scenarios.

        Every response names the output it describes. When that disagrees with the output asked
        about, the stream has slipped and the value belongs to another zone -- so it must raise
        rather than be believed. Without this check the failure is invisible: the text parses
        cleanly and the only symptom is zones reporting each other's state.

        Characterising existing behaviour, brought under test during design reconciliation.
        """
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.WRONG_OUTPUT
            with pytest.raises(ParseError):
                await client.get_route(1)
            await client.disconnect()

    async def test_a_burst_response_does_not_desync_later_commands(self) -> None:
        """C-09, and the only finding here that came from breaking a real matrix.

        Enabling audio sense is answered by roughly one frame per input, not one frame. While
        measuring that on live hardware, the probe read the surplus as answers to its later
        queries and stayed wrong for ~19 exchanges -- asking about input 7 and being told about
        inputs 1, 5, 8, 11 and 14 in turn. Every frame parsed cleanly; only the index gave it away.

        The client must either resynchronise or refuse, never quietly return another zone's state.
        """
        async with AmsSimulator(outputs=8, inputs=8) as sim:
            for output in range(1, 9):
                sim.mutate(output, source=output)
            client = await _client(sim)
            await client.firmware_version()

            sim.fail_next = Fault.BURST
            with contextlib.suppress(TriadError):
                await client.get_route(1)  # Swallowed by the burst, however it fails.

            # The next several commands must be answered correctly, or the surplus frames are
            # still being read as responses.
            for output in range(1, 9):
                assert await client.get_route(output) == output, (
                    f"still desynchronised at output {output}"
                )
            await client.disconnect()

    async def test_a_refused_connection_raises_a_transport_error(self) -> None:
        async with AmsSimulator() as sim:
            port = sim.port
        # Simulator stopped: nothing is listening now.
        client = AmsClient("127.0.0.1", port)
        with pytest.raises(TransportError):
            await client.firmware_version()

    async def test_the_device_going_away_mid_session_surfaces_as_a_transport_error(self) -> None:
        sim = AmsSimulator()
        await sim.start()
        client = await _client(sim)
        await client.firmware_version()
        await sim.stop()
        with pytest.raises(TransportError):
            for _ in range(3):
                await client.firmware_version()


class TestAudioSense:
    async def test_a_matrix_that_is_not_measuring_reports_no_reading(self) -> None:
        """Every matrix in the measured units ships like this.

        The device answers 2 for every input, and a live input is indistinguishable from a dead
        one. None -- not False -- is the only honest answer.
        """
        async with AmsSimulator() as sim:
            sim.state.audio_sense_enabled = False
            sim.state.inputs_with_signal = {3}  # Signal present, but nothing is measuring it.
            client = await _client(sim)
            assert await client.get_audio_sense_enabled() is False
            assert await client.get_audio_sense(3) is None
            assert await client.get_audio_sense(4) is None
            await client.disconnect()

    async def test_with_measuring_enabled_signal_and_silence_are_distinguished(self) -> None:
        async with AmsSimulator() as sim:
            sim.state.audio_sense_enabled = True
            sim.state.inputs_with_signal = {3, 7}
            client = await _client(sim)
            assert await client.get_audio_sense_enabled() is True
            assert await client.get_audio_sense(3) is True
            assert await client.get_audio_sense(7) is True
            assert await client.get_audio_sense(4) is False
            await client.disconnect()

    async def test_an_answer_about_a_different_input_is_rejected(self) -> None:
        """The same desync guard the outputs get. Audio sense prints a 0-based index, which is
        the one place an off-by-one would look like a plausible reading rather than an error."""
        async with AmsSimulator() as sim:
            sim.state.audio_sense_enabled = True
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.BURST
            with contextlib.suppress(TriadError):
                await client.get_audio_sense(1)
            # Whatever happened, later reads must be about the input actually asked for.
            assert await client.get_audio_sense(4) is False
            await client.disconnect()


class TestConcurrency:
    async def test_commands_issued_together_are_serialised_onto_one_socket(self) -> None:
        """Two coroutines interleaving writes on one socket would mix up the answers.

        Home Assistant refreshes every output on a poll, so this is the normal case, not an edge
        case.
        """
        async with AmsSimulator(outputs=8) as sim:
            for output in range(1, 9):
                sim.mutate(output, source=output)
            client = await _client(sim)
            results = await asyncio.gather(*(client.get_route(o) for o in range(1, 9)))
            assert results == list(range(1, 9))
            assert sim.connections == 1, "the client must not open a socket per command"
            await client.disconnect()


class TestBurstyWrites:
    """FR-14. Enabling audio sense is answered by a burst, not by one frame (C-09).

    Measured 2026-08-29: the surplus frames were read as answers to later queries and the probe
    stayed desynchronised for ~19 exchanges. Every frame parsed cleanly; only the index revealed
    the slip. These tests exist because that failure is silent in exactly the way that matters.
    """

    async def test_enabling_audio_sense_leaves_the_stream_aligned(self) -> None:
        async with AmsSimulator(outputs=8, inputs=8) as sim:
            for output in range(1, 9):
                sim.mutate(output, source=output)
            client = await _client(sim)
            await client.firmware_version()

            await client.set_audio_sense_enabled(enabled=True)

            for output in range(1, 9):
                assert await client.get_route(output) == output, (
                    f"desynchronised at output {output} -- the burst was not fully drained"
                )
            await client.disconnect()

    async def test_the_setting_actually_takes_effect(self) -> None:
        async with AmsSimulator(inputs=8) as sim:
            sim.state.audio_sense_enabled = False
            client = await _client(sim)
            await client.set_audio_sense_enabled(enabled=True)
            assert await client.get_audio_sense_enabled() is True
            await client.set_audio_sense_enabled(enabled=False)
            assert await client.get_audio_sense_enabled() is False
            await client.disconnect()

    async def test_the_drain_reports_how_much_it_swallowed(self) -> None:
        """The byte count is the only external evidence the drain consumed the whole burst.

        Unasserted, a ``send_bursty`` that read a single frame and returned would pass every other
        test in this class -- the surplus would sit in the buffer and only desynchronise a
        *later* exchange, which is precisely how C-09 hid on real hardware.
        """
        async with AmsSimulator(inputs=8) as sim:
            client = await _client(sim)
            drained = await client.send_bursty(p.set_audio_sense_enabled(enabled=True))
            # Eight frames of "AudioSense:Input[n]: 0" plus terminators. A drain that stopped at
            # the first frame returns about an eighth of this.
            assert drained > 100, f"only {drained} bytes drained -- the burst was not consumed"
            await client.disconnect()

    async def test_the_drain_ends_on_a_quiet_socket_not_on_an_expected_frame_count(self) -> None:
        """The design decision this pins is worth more than the behaviour it checks.

        C-09 measured "roughly one frame per input" -- roughly. A client that reads exactly
        ``spec.inputs`` frames desyncs by the difference the moment firmware sends one more or one
        fewer, and stays wrong for the life of the connection. Quiet-socket is the only terminator
        that cannot be off by one.
        """
        async with AmsSimulator(outputs=8, inputs=8) as sim:
            for output in range(1, 9):
                sim.mutate(output, source=output)
            sim.burst_extra_frames = 3  # Firmware sends more than one per input.
            client = await _client(sim)
            await client.firmware_version()

            await client.set_audio_sense_enabled(enabled=True)

            for output in range(1, 9):
                assert await client.get_route(output) == output, (
                    f"desynchronised at output {output} -- the drain assumed a frame count"
                )
            await client.disconnect()

    async def test_the_off_delay_round_trips(self) -> None:
        """An ordinary single-response write -- it must NOT go through the bursty path."""
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.set_audio_sense_off_delay(30)
            assert await client.get_audio_sense_off_delay() == 30
            await client.disconnect()

    async def test_a_refusal_is_not_swallowed_by_the_drain(self) -> None:
        """Draining without ever looking meant a refusal was discarded like any other frame.

        The device can answer `Command error` instead of a burst. Before this, `send_bursty` read
        it, counted its bytes and returned successfully -- so the switch reported the setting it
        had just failed to make. The first frame is now checked, and only the first: the rest are
        still discarded unparsed, because a burst carries no ack worth reading.
        """
        async with AmsSimulator(inputs=8) as sim:
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.COMMAND_ERROR
            with pytest.raises(CommandError):
                await client.set_audio_sense_enabled(enabled=True)
            # The socket must still be usable: a refusal is an application-layer answer, not a
            # transport failure.
            assert await client.get_audio_sense_enabled() in (True, False)
            await client.disconnect()

    async def test_silence_is_not_mistaken_for_a_finished_burst(self) -> None:
        """A quiet socket ends a burst only if a burst started.

        With the matrix gone the write went nowhere, the read timed out, and zero frames read as
        "the burst is over" -- so the caller was told a device it cannot reach had accepted the
        command. Found by a repair-flow test that expected an abort and got a success.
        """
        sim = AmsSimulator(inputs=8)
        await sim.start()
        client = await _client(sim)
        await client.firmware_version()
        await sim.stop()

        with pytest.raises(TransportError):
            await client.set_audio_sense_enabled(enabled=True)


class TestIndexGuardsOnEveryFamily:
    """The output guard was regression-verified during design reconciliation. Inputs and EQ bands
    have the same guard and had no test, though the input one is the riskiest of the three: audio
    sense prints a 0-based index, so an off-by-one there reads as a plausible value rather than an
    obvious error.
    """

    async def test_an_answer_about_a_different_input_is_rejected(self) -> None:
        async with AmsSimulator() as sim:
            sim.state.audio_sense_enabled = True
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.WRONG_INPUT
            with pytest.raises(ParseError, match="audio sense"):
                await client.get_audio_sense(1)
            await client.disconnect()

    async def test_an_answer_about_a_different_inputs_gain_is_rejected(self) -> None:
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.WRONG_INPUT
            with pytest.raises(ParseError, match="gain"):
                await client.get_input_gain(1)
            await client.disconnect()

    async def test_an_answer_about_a_different_band_is_rejected(self) -> None:
        """A band is addressed by opcode, so a slip here edits the wrong part of the spectrum."""
        async with AmsSimulator() as sim:
            client = await _client(sim)
            await client.firmware_version()
            sim.fail_next = Fault.WRONG_BAND
            with pytest.raises(ParseError, match="band"):
                await client.get_eq_band(1, 1)
            await client.disconnect()


class TestTheDiagnosticReads:
    async def test_the_mac_address_and_power_state(self) -> None:
        """Neither is exposed as an entity -- the MAC is redacted from diagnostics and power is
        deliberately never sent -- but both are read paths the document records."""
        async with AmsSimulator() as sim:
            client = await _client(sim)
            assert await client.mac_address() == "AA:BB:CC:DD:EE:FF"
            assert await client.power() is True
            await client.disconnect()

    async def test_a_burst_that_never_stops_is_cut_off_and_logged(self) -> None:
        """MAX_BURST_FRAMES is a runaway guard, not a terminator.

        Reaching it means the device is still talking well past any plausible burst -- one frame
        per input on a 24-input matrix -- so it is a fault worth a warning rather than a routine
        stopping point. Without the cap a device stuck in a loop would hold the event loop.
        """
        async with AmsSimulator(inputs=8) as sim:
            sim.burst_extra_frames = 300  # Far past the cap.
            client = await _client(sim)
            drained = await client.send_bursty(p.set_audio_sense_enabled(enabled=True))
            assert drained > 0
            await client.disconnect()

    async def test_a_socket_that_fails_mid_burst_is_a_transport_error(self) -> None:
        """Distinct from the device answering nothing: here the write itself fails, so the
        connection is dropped rather than merely reported."""
        async with AmsSimulator(inputs=8) as sim:
            client = await _client(sim)
            await client.firmware_version()
            with (
                mock.patch.object(client._writer, "drain", side_effect=OSError("gone")),
                pytest.raises(TransportError, match="bursty write"),
            ):
                await client.set_audio_sense_enabled(enabled=True)
            assert not client.connected, "the socket should have been dropped"


class TestPaddedFramingHoldsBackWhatItOverreads:
    async def test_a_byte_read_past_the_padding_is_kept_for_the_next_frame(self) -> None:
        """The drain stops at the first non-NUL, and that byte belongs to the *next* frame.

        Discarding it would truncate every following response by one character -- which parses
        cleanly often enough to be dangerous. A burst on padded firmware is where two frames sit
        back to back and the drain runs straight into the second.
        """
        async with AmsSimulator(outputs=8, inputs=8, padding=Padding.FIXED_150) as sim:
            for output in range(1, 9):
                sim.mutate(output, source=output)
            client = await _client(sim)
            await client.firmware_version()

            sim.fail_next = Fault.BURST
            with contextlib.suppress(TriadError):
                await client.get_route(1)

            # Whatever was held back must not have corrupted what follows.
            for output in range(1, 9):
                assert await client.get_route(output) == output, f"desync at output {output}"
            await client.disconnect()
