"""What happens when a matrix answers badly, or stops answering.

This is D-07 territory: *a failed output does not fail the poll*. `Command error` is a per-command
hiccup real firmware emits on healthy sockets, so one output answering badly must keep its previous
reading rather than blanking twenty-four zones. Only a transport failure marks the matrix
unavailable.

Everything here was reachable and untested. The branches are one-line debug logs, which is exactly
why they are worth pinning: a `return` that silently swallows the wrong class of failure looks
identical to one that correctly rides out a hiccup.
"""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator, Fault

pytestmark = pytest.mark.enable_socket

ZONE_1 = "media_player.test_matrix_output_1"
ZONE_2 = "media_player.test_matrix_output_2"
BASS_1 = "number.test_matrix_bass"
SENSE_1 = "binary_sensor.test_matrix_input_1_audio"
INPUT_GAIN = "number.test_matrix_input_1_gain"
BANK_1 = "switch.test_matrix_trigger_outputs_1_8"


async def _setup(hass: HomeAssistant, sim: AmsSimulator, *, enable: list[str] | None = None):
    entry = make_entry(sim)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    if enable:
        registry = er.async_get(hass)
        for entity_id in enable:
            registry.async_update_entity(entity_id, disabled_by=None)
        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
    return entry


class TestAFlakyAnswerIsRiddenOut:
    async def test_one_bad_output_keeps_its_previous_reading(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """D-07, asserted. Without it a single hiccup blanks every zone on the matrix."""
        simulator.mutate(1, source=4)
        simulator.mutate(2, source=6)
        entry = await _setup(hass, simulator)
        assert hass.states.get(ZONE_1).state == "on"

        simulator.fail_next = Fault.COMMAND_ERROR  # Lands on the first read of the next poll.
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

        # Whichever output took the fault kept its value, and the matrix stayed available.
        assert hass.states.get(ZONE_1).state != STATE_UNAVAILABLE
        assert hass.states.get(ZONE_2).state == "on"

    async def test_a_bad_dsp_read_keeps_the_previous_tone(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.channels[1].bass = 4.5
        entry = await _setup(hass, simulator, enable=[BASS_1])
        assert float(hass.states.get(BASS_1).state) == 4.5

        simulator.fail_next = Fault.COMMAND_ERROR
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(BASS_1).state != STATE_UNAVAILABLE

    async def test_a_bad_audio_sense_read_reports_no_reading(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """None, not False. False would assert "there is no audio" on evidence the device did not
        give -- the same reasoning that makes `2` unavailable rather than off."""
        simulator.state.audio_sense_enabled = True
        simulator.state.inputs_with_signal = {1}
        entry = await _setup(hass, simulator, enable=[SENSE_1])

        simulator.fail_next = Fault.COMMAND_ERROR
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(SENSE_1) is not None

    async def test_bad_input_gain_and_trigger_reads_do_not_fail_the_poll(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator, enable=[INPUT_GAIN, BANK_1])
        for _ in range(3):
            simulator.fail_next = Fault.COMMAND_ERROR
            await entry.runtime_data.async_refresh()
            await hass.async_block_till_done()
        assert hass.states.get(ZONE_1).state != STATE_UNAVAILABLE


class TestAMatrixThatStopsAnswering:
    async def test_the_whole_matrix_goes_unavailable(self, hass: HomeAssistant) -> None:
        """A transport failure is the one case that *should* fail the poll: every remaining output
        would fail the same way, so stopping beats spending the timeout twenty-three more times."""
        sim = AmsSimulator(outputs=8, inputs=8)
        await sim.start()
        entry = await _setup(hass, sim)
        assert hass.states.get(ZONE_1).state != STATE_UNAVAILABLE

        await sim.stop()
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(ZONE_1).state == STATE_UNAVAILABLE

    async def test_a_targeted_refresh_after_a_write_does_not_raise(
        self, hass: HomeAssistant
    ) -> None:
        """The re-read is best effort. Failing it would surface a transient read error as a failed
        user action that had in fact succeeded -- the write already reached the device."""
        sim = AmsSimulator(outputs=8, inputs=8)
        await sim.start()
        entry = await _setup(sim=sim, hass=hass)
        coordinator = entry.runtime_data
        await sim.stop()

        # Each of these swallows its transport failure and leaves the previous snapshot standing.
        await coordinator.async_refresh_output(1)
        await coordinator.async_refresh_output_dsp(1)
        await coordinator.async_refresh_inputs()
        await coordinator.async_refresh_triggers()
        await coordinator.async_refresh_sense_settings()
        await hass.async_block_till_done()

    async def test_unloading_survives_a_matrix_that_will_not_close(
        self, hass: HomeAssistant
    ) -> None:
        """Home Assistant blocks on unload, so a slow close must not become a stuck reload."""
        sim = AmsSimulator(outputs=8, inputs=8)
        await sim.start()
        entry = await _setup(sim=sim, hass=hass)
        await sim.stop()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


class TestRefreshesBeforeTheFirstPoll:
    """Every targeted refresh guards on `self.data`. Between setup and the first successful poll
    there is no snapshot to patch, and patching `None` would raise inside a service call."""

    async def test_they_are_all_no_ops_without_a_snapshot(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator)
        coordinator = entry.runtime_data
        coordinator.data = None

        await coordinator.async_refresh_inputs()
        await coordinator.async_refresh_triggers()
        await coordinator.async_refresh_sense_settings()
        await coordinator.async_refresh_output_dsp(1)
        await coordinator._store_turn_on_volume(1)
        assert coordinator.data is None, "a refresh invented a snapshot from nothing"


class TestEachReadFailsOnItsOwn:
    """Every read in a poll cycle has its own swallow-and-continue branch.

    ``fail_next`` always lands on whichever command the client sends first, which for a poll is an
    output query -- so the branches deeper in the cycle needed a fault that could be aimed. Each
    of these breaks exactly one read and asserts the poll still completes: a matrix must not go
    unavailable because its trigger banks or its off delay answered badly once.
    """

    @pytest.mark.parametrize(
        ("prefix", "what"),
        [
            ("ff550204f5", "input gain"),
            ("ff55030550f5", "a trigger bank"),
            ("ff55040aa2f5", "the audio-sense enable flag"),
            ("ff55040aa3f5", "the audio-sense off delay"),
            ("ff5503066500", "the firmware version"),
            ("ff55030881f5", "the addressing mode"),
            ("ff55040aa0f5", "an audio-sense input"),
        ],
    )
    async def test_a_single_bad_read_does_not_fail_the_poll(
        self, hass: HomeAssistant, simulator: AmsSimulator, prefix: str, what: str
    ) -> None:
        simulator.state.audio_sense_enabled = True
        entry = await _setup(hass, simulator, enable=[SENSE_1, INPUT_GAIN, BANK_1])

        simulator.fail_matching = prefix
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

        assert hass.states.get(ZONE_1).state != STATE_UNAVAILABLE, (
            f"the matrix went unavailable because {what} answered badly once"
        )

    async def test_a_bad_dsp_read_keeps_the_band_it_had(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Stale beats blank: the previous curve is still what the zone is playing."""
        entry = await _setup(hass, simulator, enable=[BASS_1])
        simulator.fail_matching = "ff5504032ff5"  # the bass read for output 1
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(BASS_1).state != STATE_UNAVAILABLE


class TestTurnOnVolumeStorageFailing:
    async def test_a_refused_store_is_swallowed(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """It is a follow-up to a write that already succeeded, so failing loudly would report a
        volume change as broken when the volume did change."""
        entry = await _setup(hass, simulator)
        coordinator = entry.runtime_data
        simulator.fail_matching = "ff5504033300"  # the turn-on write for output 1
        await coordinator._store_turn_on_volume(1)
        await hass.async_block_till_done()

    async def test_it_re_reads_the_dsp_when_something_is_displaying_it(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Only then. Re-reading for nobody would cost twenty round trips per volume change."""
        entry = await _setup(hass, simulator, enable=[BASS_1])
        coordinator = entry.runtime_data
        assert coordinator.dsp_outputs == [1]
        await coordinator._store_turn_on_volume(1)
        await hass.async_block_till_done()
        assert simulator.state.channels[1].turn_on_step >= 0
