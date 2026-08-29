"""The async client, driven against the simulator over a real loopback socket.

Marked ``enable_socket`` because pytest-homeassistant-custom-component brings pytest-socket,
which blocks sockets for the whole session in CI.
"""

from __future__ import annotations

import asyncio

import pytest
from ams.client import AmsClient
from ams.errors import CommandError, ParseError, TransportError
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
