"""Tone, EQ gain and EQ frequency, plus the per-output polling they drive."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

BASS_1 = "number.test_matrix_bass"
GAIN_1_B1 = "number.test_matrix_eq_band_1_gain"
FREQ_1_B1 = "select.test_matrix_eq_band_1_frequency"


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


class TestCost:
    async def test_dsp_ships_disabled_and_costs_nothing(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """An 8x8 adds 75 DSP entities; a 24x24 adds 225. None should be on by default, and none
        should cost a round trip until someone enables one."""
        entry = await _setup(hass, simulator)
        assert hass.states.get(BASS_1) is None
        assert entry.runtime_data.dsp_outputs == []

    async def test_enabling_one_output_polls_only_that_output(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The efficiency decision, asserted.

        Reference counting is per output, not global. Enabling the EQ on one zone must cost one
        output's worth of reads -- twenty round trips -- not the whole matrix's hundred and sixty.
        """
        entry = await _setup(hass, simulator, enable=[GAIN_1_B1])
        assert entry.runtime_data.dsp_outputs == [1]


class TestReading:
    async def test_tone_reflects_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.channels[1].bass = 4.5
        await _setup(hass, simulator, enable=[BASS_1])
        assert float(hass.states.get(BASS_1).state) == 4.5

    async def test_a_band_carries_its_frequency_and_q_as_attributes(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The band should read as a band, not as a bare gain number."""
        await _setup(hass, simulator, enable=[GAIN_1_B1])
        attrs = hass.states.get(GAIN_1_B1).attributes
        assert attrs["frequency_hz"] == 63.0  # factory default for band 1
        assert attrs["q"] == 0.7

    async def test_frequency_is_labelled_the_way_audio_people_write_it(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[FREQ_1_B1])
        state = hass.states.get(FREQ_1_B1)
        assert state.state == "63 Hz"
        assert "1.6 kHz" in state.attributes["options"]
        assert len(state.attributes["options"]) == 31

    async def test_a_kilohertz_band_is_not_reported_as_hertz(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The bug this platform was built on top of: 1.6 kHz parsed as 1.6 Hz is three orders of
        magnitude out and looks entirely plausible in a UI."""
        simulator.state.channels[1].band_freq[0] = 19  # 1.6 kHz
        await _setup(hass, simulator, enable=[FREQ_1_B1, GAIN_1_B1])
        assert hass.states.get(FREQ_1_B1).state == "1.6 kHz"
        assert hass.states.get(GAIN_1_B1).attributes["frequency_hz"] == 1600.0


class TestWriting:
    async def test_setting_tone_reaches_the_device_and_reads_back(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[BASS_1])
        await hass.services.async_call(
            "number", "set_value", {"entity_id": BASS_1, "value": -6.0}, blocking=True
        )
        await hass.async_block_till_done()
        assert simulator.state.channels[1].bass == -6.0
        assert float(hass.states.get(BASS_1).state) == -6.0

    async def test_setting_eq_gain_reaches_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[GAIN_1_B1])
        await hass.services.async_call(
            "number", "set_value", {"entity_id": GAIN_1_B1, "value": -3.5}, blocking=True
        )
        await hass.async_block_till_done()
        assert simulator.state.channels[1].band_gain[0] == -3.5

    async def test_selecting_a_frequency_sends_the_table_index_not_the_hertz(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The device takes an index; the user picks a frequency. The translation is the point of
        making this a select, and a round trip proves it lands on the right entry."""
        await _setup(hass, simulator, enable=[FREQ_1_B1])
        await hass.services.async_call(
            "select", "select_option", {"entity_id": FREQ_1_B1, "option": "2.5 kHz"}, blocking=True
        )
        await hass.async_block_till_done()
        assert simulator.state.channels[1].band_freq[0] == 21  # index of 2500 Hz
        assert hass.states.get(FREQ_1_B1).state == "2.5 kHz"

    async def test_every_offered_frequency_round_trips(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """A label the picker offers but the device cannot land on would be a lie in the UI.

        This is the test that would catch an error in the inferred interior of the frequency
        table, which is the one part not taken directly from the driver's documentation.
        """
        await _setup(hass, simulator, enable=[FREQ_1_B1])
        options = hass.states.get(FREQ_1_B1).attributes["options"]
        for option in options:
            await hass.services.async_call(
                "select", "select_option", {"entity_id": FREQ_1_B1, "option": option}, blocking=True
            )
            await hass.async_block_till_done()
            assert hass.states.get(FREQ_1_B1).state == option, f"{option} did not round-trip"
