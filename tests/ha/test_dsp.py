"""Tone, EQ gain and EQ frequency, plus the per-output polling they drive."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

BASS_1 = "number.test_matrix_bass"
GAIN_1_B1 = "number.test_matrix_eq_band_1_gain"
FREQ_1_B1 = "select.test_matrix_eq_band_1_frequency"


async def _setup(
    hass: HomeAssistant,
    sim: AmsSimulator,
    *,
    enable: list[str] | None = None,
    options: dict | None = None,
):
    entry = make_entry(sim, options=options)
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


OFF_DELAY = "number.test_matrix_audio_sense_off_delay"
TURN_ON_1 = "sensor.test_matrix_turn_on_volume"

#: Set turn-on volume for output 1: FF 55 04 03 33 <output>. A query carries f5 in place of it.
TURN_ON_WRITE_PREFIX = "ff55040333"


def _turn_on_writes(sim: AmsSimulator) -> list[str]:
    return [f for f in sim.received if f.startswith(TURN_ON_WRITE_PREFIX) and "f5" not in f[10:12]]


class TestTurnOnVolumeTracking:
    """FR-12. Replaces the Control4 behaviour that makes zones resume where they were left."""

    async def test_a_volume_change_is_stored_after_it_settles(
        self, hass: HomeAssistant, simulator: AmsSimulator, freezer
    ) -> None:
        entry = await _setup(hass, simulator)
        before = len(_turn_on_writes(simulator))

        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": "media_player.test_matrix_output_1", "volume_level": 0.4},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert len(_turn_on_writes(simulator)) == before, "written before the volume settled"

        freezer.tick(15)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        assert len(_turn_on_writes(simulator)) > before, "never stored after settling"
        assert entry.runtime_data.track_turn_on_volume is True

    async def test_the_stored_value_is_what_the_device_reported_not_what_was_sent(
        self, hass: HomeAssistant, simulator: AmsSimulator, freezer
    ) -> None:
        """The failure this guards against is silent and arrives weeks late.

        The matrix caps a zone against its own max-volume register, so the step it adopts can be
        lower than the step that was sent. Storing the requested value would persist a turn-on
        volume the device never accepted -- and the zone would come back louder than it can go,
        long after anyone connects it to this code.
        """
        simulator.state.channels[1].max_step = 30
        await _setup(hass, simulator)

        await hass.services.async_call(
            "media_player",
            "volume_set",
            {"entity_id": "media_player.test_matrix_output_1", "volume_level": 0.9},
            blocking=True,
        )
        await hass.async_block_till_done()
        freezer.tick(15)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        adopted = simulator.state.channels[1].step
        assert simulator.state.channels[1].turn_on_step == adopted, (
            "the requested step was stored rather than the one the device adopted"
        )
        assert adopted != 90, "the simulator did not cap, so this test proves nothing"

    async def test_tracking_off_offers_a_writable_number_instead(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The option decides which entity exists -- never an entity's writability."""
        entry = await _setup(hass, simulator, enable=None)
        assert hass.states.get(TURN_ON_1) is None  # disabled by default, but registered

        registry = er.async_get(hass)
        ids = {e.entity_id for e in er.async_entries_for_config_entry(registry, entry.entry_id)}
        assert TURN_ON_1 in ids, "no read-only turn-on volume while tracking is on"
        assert "number.test_matrix_turn_on_volume" not in ids, (
            "a writable turn-on volume exists while the integration owns the value"
        )


class TestAudioSenseOffDelay:
    """FR-14. Was a read-only sensor until the setter existed."""

    async def test_the_delay_round_trips_through_the_number(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.audio_sense_off_delay = 1
        await _setup(hass, simulator, enable=[OFF_DELAY])
        assert hass.states.get(OFF_DELAY).state == "1"

        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": OFF_DELAY, "value": 30},
            blocking=True,
        )
        await hass.async_block_till_done()

        assert simulator.state.audio_sense_off_delay == 30
        assert hass.states.get(OFF_DELAY).state == "30"

    async def test_it_is_reported_in_minutes(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The device's unit. Reading 30 as seconds understates it by a factor of sixty."""
        await _setup(hass, simulator, enable=[OFF_DELAY])
        assert hass.states.get(OFF_DELAY).attributes["unit_of_measurement"] == "min"


class TestMaxVolumeReachesTheDevice:
    """FR-15. One setting, two enforcement points: the slider scale and the device's own ceiling."""

    async def test_nothing_is_written_on_connect(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """D-05 still holds. A cap in the options is not a reason to write on every startup."""
        await _setup(hass, simulator, options={"output_max_volumes": {"1": 40}})
        assert not [f for f in simulator.received if f.startswith("ff5504031f")], (
            "setup pushed the max volume to the device"
        )

    async def test_changing_it_in_options_reaches_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The device has a setter and no getter, so the write has to happen at the moment of the
        change -- the options flow is the only place that knows both the old value and the new."""
        entry = await _setup(hass, simulator, options={"output_max_volumes": {"1": 100}})

        result = await hass.config_entries.options.async_init(entry.entry_id)
        user_input = {f"output_{i}": True for i in range(1, 9)}
        user_input |= {f"input_{i}": True for i in range(1, 9)}
        user_input |= {f"max_volume_{i}": 100 for i in range(1, 9)}
        user_input["max_volume_1"] = 40
        user_input["scan_interval"] = 30
        await hass.config_entries.options.async_configure(result["flow_id"], user_input)
        await hass.async_block_till_done()

        assert simulator.state.channels[1].max_step == 40
        assert simulator.state.channels[2].max_step == 100, "an unchanged cap was written anyway"
