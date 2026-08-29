"""Loudness, mono-sum and the 12 V trigger banks."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

LOUDNESS = "switch.test_matrix_loudness"
MONO = "switch.test_matrix_mono_sum"
BANK_1 = "switch.test_matrix_trigger_outputs_1_8"
ASG = "switch.test_matrix_asg_trigger"


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


async def _toggle(hass: HomeAssistant, entity_id: str, *, on: bool) -> None:
    await hass.services.async_call(
        "switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


class TestPerOutput:
    async def test_switches_ship_disabled(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        assert hass.states.get(LOUDNESS) is None

    async def test_loudness_reflects_and_reaches_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[LOUDNESS])
        assert hass.states.get(LOUDNESS).state == STATE_OFF
        await _toggle(hass, LOUDNESS, on=True)
        assert simulator.state.channels[1].loudness is True
        assert hass.states.get(LOUDNESS).state == STATE_ON

    async def test_mono_sum_reflects_and_reaches_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.channels[1].mono = True
        await _setup(hass, simulator, enable=[MONO])
        assert hass.states.get(MONO).state == STATE_ON
        await _toggle(hass, MONO, on=False)
        assert simulator.state.channels[1].mono is False

    async def test_loudness_costs_no_extra_polling_beyond_the_dsp_read(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Loudness and mono come free with the tone/EQ read an output already does."""
        entry = await _setup(hass, simulator, enable=[LOUDNESS])
        assert entry.runtime_data.dsp_outputs == [1]


class TestTriggers:
    async def test_triggers_ship_disabled_and_are_not_polled(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator)
        assert hass.states.get(BANK_1) is None
        assert entry.runtime_data.polls_triggers is False

    async def test_a_bank_reflects_and_reaches_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator, enable=[BANK_1])
        assert entry.runtime_data.polls_triggers is True
        assert hass.states.get(BANK_1).state == STATE_OFF
        await _toggle(hass, BANK_1, on=True)
        assert simulator.state.triggers[0] is True
        assert hass.states.get(BANK_1).state == STATE_ON

    async def test_an_eight_by_eight_offers_only_the_bank_it_has(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """An 8x8 has one output trigger bank. Offering a switch for bank 2 would be offering a
        control that answers Command error."""
        entry = await _setup(hass, simulator)
        registry = er.async_get(hass)
        banks = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if "_trigger_bank_" in e.unique_id
        ]
        assert [e.unique_id.rsplit("_", 1)[-1] for e in banks] == ["1"]

    async def test_asg_uses_the_index_for_this_model_not_a_literal(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The model-dependent trap, asserted end to end.

        ASG sits after the last output bank, so an 8x8 puts it at wire index 1 -- exactly where a
        24x24 keeps its 9-16 bank. Getting this wrong toggles the wrong bank and reports success.
        """
        await _setup(hass, simulator, enable=[ASG])
        await _toggle(hass, ASG, on=True)
        assert simulator.state.triggers.get(1) is True, "ASG did not land on the 8x8's index"
        assert hass.states.get(ASG).state == STATE_ON

    async def test_on_a_24x24_asg_does_not_collide_with_the_second_bank(
        self, hass: HomeAssistant
    ) -> None:
        """The other half of the same trap: on a 24x24, index 1 must remain bank 9-16."""
        sim = AmsSimulator(outputs=24, inputs=24)
        await sim.start()
        try:
            entry = await _setup(hass, sim, enable=["switch.test_matrix_asg_trigger"])
            await _toggle(hass, "switch.test_matrix_asg_trigger", on=True)
            assert sim.state.triggers.get(3) is True, "ASG should be index 3 on a 24x24"
            assert sim.state.triggers.get(1) is not True, "bank 9-16 must not have been touched"
            assert entry.runtime_data.polls_triggers is True
        finally:
            await sim.stop()


AUDIO_SENSE = "switch.test_matrix_audio_sense"


class TestAudioSenseSwitch:
    """FR-14. Withheld until the vendor stopped re-asserting its own value on every reconnect."""

    async def test_it_turns_measuring_on_and_off(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.audio_sense_enabled = False
        await _setup(hass, simulator, enable=[AUDIO_SENSE])
        assert hass.states.get(AUDIO_SENSE).state == STATE_OFF

        await _toggle(hass, AUDIO_SENSE, on=True)
        assert simulator.state.audio_sense_enabled is True
        assert hass.states.get(AUDIO_SENSE).state == STATE_ON

        await _toggle(hass, AUDIO_SENSE, on=False)
        assert simulator.state.audio_sense_enabled is False
        assert hass.states.get(AUDIO_SENSE).state == STATE_OFF

    async def test_the_reply_burst_does_not_desynchronise_the_stream(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """C-09, end to end through the entity rather than only at the client.

        Enabling is answered by roughly one frame per input. If the switch used the ordinary write
        path, the surplus frames would be collected by later queries and every zone would report
        its neighbour's state -- cleanly parsed and entirely wrong.
        """
        simulator.state.audio_sense_enabled = False
        for output in range(1, 9):
            simulator.mutate(output, source=output)
        entry = await _setup(hass, simulator, enable=[AUDIO_SENSE])

        await _toggle(hass, AUDIO_SENSE, on=True)
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()

        outputs = entry.runtime_data.data.outputs
        for output in range(1, 9):
            assert outputs[output].source == output, f"desynchronised at output {output}"

    async def test_it_does_not_drag_the_per_input_polling_tier_with_it(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """One matrix-wide flag must not cost one read per input.

        Riding the input tier would make enabling this switch alone pull 8 audio-sense reads on
        this matrix, and 24 on an AMS24, to learn a single boolean.
        """
        entry = await _setup(hass, simulator, enable=[AUDIO_SENSE])
        coordinator = entry.runtime_data
        assert coordinator.polls_audio_sense_settings is True
        assert coordinator.polls_inputs is False
