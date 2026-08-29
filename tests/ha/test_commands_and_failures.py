"""The controls nothing else exercised, and what happens when the device refuses a command.

Two kinds of gap live here, and only one of them is about error handling.

The **Q select was entirely untested** — a whole entity with no coverage, not merely an
unexercised `except`. The rest are the failure branches: every write wraps `TriadError` into
`HomeAssistantError` so the user sees a failed action rather than a silent no-op, and until now
nothing checked that any of those wrappers fired.
"""

from __future__ import annotations

import asyncio

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from custom_components.triad_ams.coordinator import ROUTE_DEBOUNCE_SECONDS
from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator, Fault

pytestmark = pytest.mark.enable_socket

ZONE = "media_player.test_matrix_output_1"
FREQ = "select.test_matrix_eq_band_1_frequency"
Q = "select.test_matrix_eq_band_1_q"


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


async def _mp(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        "media_player", service, {"entity_id": ZONE, **data}, blocking=True
    )
    await hass.async_block_till_done()


def _entity(hass: HomeAssistant, entity_id: str):
    """Reach the entity object itself, for properties HA never calls while a zone is off."""
    return hass.data["entity_components"][entity_id.split(".")[0]].get_entity(entity_id)


class TestTheControls:
    async def test_selecting_off_disconnects_the_output(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.mutate(1, source=3)
        await _setup(hass, simulator)
        await _mp(hass, "select_source", source="Off")
        assert simulator.state.channels[1].source is None

    async def test_an_input_this_matrix_does_not_have_is_refused(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        with pytest.raises(HomeAssistantError, match="not one of this matrix's inputs"):
            await _mp(hass, "select_source", source="Input 99")

    async def test_turning_on_restores_the_source_it_was_turned_off_from(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """'On' has no meaning to this hardware beyond being routed somewhere, so turn_off has to
        remember where it was in order for turn_on to be anything but a guess."""
        simulator.mutate(1, source=5)
        await _setup(hass, simulator)
        await _mp(hass, "turn_off")
        assert simulator.state.channels[1].source is None

        await _mp(hass, "turn_on")
        # turn_off and turn_on are two route commands inside the 250 ms window, so the second is
        # coalesced into a trailing run rather than sent immediately. Waiting it out is the test
        # acknowledging FR-13 rather than working around it -- asserting straight away reads the
        # state before the coalesced write lands, which is exactly what this debounce is for.
        await asyncio.sleep(ROUTE_DEBOUNCE_SECONDS + 0.15)
        await hass.async_block_till_done()
        assert simulator.state.channels[1].source == 5

    async def test_volume_up_and_down_step_from_the_last_reading(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Stepping from the reading rather than using the device's own step opcode is deliberate:
        that opcode ignores the configured cap, so a zone could be nudged past it one press at a
        time."""
        simulator.mutate(1, step=50)
        await _setup(hass, simulator)
        await _mp(hass, "volume_up")
        assert simulator.state.channels[1].step == 51
        await _mp(hass, "volume_down")
        await _mp(hass, "volume_down")
        assert simulator.state.channels[1].step == 49

    async def test_muting_reaches_the_device_and_reads_back(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        await _mp(hass, "volume_mute", is_volume_muted=True)
        assert simulator.state.channels[1].muted is True
        await _mp(hass, "volume_mute", is_volume_muted=False)
        assert simulator.state.channels[1].muted is False

    async def test_the_source_attribute_says_off_rather_than_nothing(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Home Assistant suppresses a media player's attributes while it is off, so this property
        is only reachable directly -- but it still has to be right, because anything reading the
        entity object (a template, another integration) sees it."""
        await _setup(hass, simulator)
        entity = _entity(hass, ZONE)
        assert entity.source == "Off"

    async def test_a_routed_input_the_user_excluded_still_reports_its_number(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Another controller can route an input this integration was told to ignore. Reporting
        the number beats reporting None and looking like a fault."""
        await _setup(hass, simulator)
        simulator.mutate(1, source=7)
        entry = next(iter(hass.config_entries.async_entries("triad_ams")))
        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        entity = _entity(hass, ZONE)
        assert entity.source is not None


class TestWhenTheDeviceRefuses:
    """Every write wraps TriadError into HomeAssistantError so a failed action looks failed."""

    async def test_a_refused_route_surfaces_as_an_error(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match="command failed for output 1"):
            await _mp(hass, "select_source", source="Input 2")

    async def test_a_refused_volume_surfaces_as_an_error(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match="command failed for output 1"):
            await _mp(hass, "volume_set", volume_level=0.5)

    async def test_a_refused_mute_surfaces_as_an_error(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match="command failed for output 1"):
            await _mp(hass, "volume_mute", is_volume_muted=True)

    async def test_a_refused_preset_says_it_may_have_applied_part_way(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Fifteen writes, so a failure halfway leaves the band shape half-changed. Saying so is
        the difference between a user re-running it and a user wondering what they are hearing."""
        await _setup(hass, simulator)
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match="failed part-way"):
            await hass.services.async_call(
                "triad_ams",
                "apply_eq_preset",
                {"entity_id": ZONE, "preset": "Rock"},
                blocking=True,
            )


class TestTheEqSelects:
    async def test_the_q_select_reports_and_sets(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The Q entity had no test at all -- not an unexercised branch, a whole entity."""
        await _setup(hass, simulator, enable=[Q])
        assert hass.states.get(Q).state == "0.7"  # factory default, index 2

        await hass.services.async_call(
            "select", "select_option", {"entity_id": Q, "option": "2"}, blocking=True
        )
        await hass.async_block_till_done()
        assert simulator.state.channels[1].band_q[0] == 6  # index of Q 2.0
        assert hass.states.get(Q).state == "2"

    async def test_every_q_the_picker_offers_round_trips(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The device clamps an out-of-range Q index and reports success, so an option the picker
        offers but the hardware cannot land on would be a lie the device never contradicts."""
        await _setup(hass, simulator, enable=[Q])
        for option in hass.states.get(Q).attributes["options"]:
            await hass.services.async_call(
                "select", "select_option", {"entity_id": Q, "option": option}, blocking=True
            )
            await hass.async_block_till_done()
            assert hass.states.get(Q).state == option, f"Q {option} did not round-trip"

    async def test_a_frequency_outside_the_table_is_refused(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[FREQ])
        entity = _entity(hass, FREQ)
        with pytest.raises(HomeAssistantError, match="not a frequency this matrix offers"):
            await entity.async_select_option("banana")

    async def test_a_q_outside_the_table_is_refused(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[Q])
        entity = _entity(hass, Q)
        with pytest.raises(HomeAssistantError, match="not a Q this matrix offers"):
            await entity.async_select_option("banana")

    async def test_a_refused_frequency_write_surfaces_as_an_error(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[FREQ])
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match="command failed for output 1"):
            await hass.services.async_call(
                "select", "select_option", {"entity_id": FREQ, "option": "1 kHz"}, blocking=True
            )

    async def test_a_refused_q_write_surfaces_as_an_error(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[Q])
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match="command failed for output 1"):
            await hass.services.async_call(
                "select", "select_option", {"entity_id": Q, "option": "1"}, blocking=True
            )


BASS = "number.test_matrix_bass"
TREBLE = "number.test_matrix_treble"
BALANCE = "number.test_matrix_balance"
GAIN_B1 = "number.test_matrix_eq_band_1_gain"
INPUT_GAIN = "number.test_matrix_input_1_gain"
OFF_DELAY = "number.test_matrix_audio_sense_off_delay"
LOUDNESS = "switch.test_matrix_loudness"
MONO = "switch.test_matrix_mono_sum"
BANK_1 = "switch.test_matrix_trigger_outputs_1_8"
ASG = "switch.test_matrix_asg_trigger"
AUDIO_SENSE = "switch.test_matrix_audio_sense"


async def _set_number(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        "number", "set_value", {"entity_id": entity_id, "value": value}, blocking=True
    )
    await hass.async_block_till_done()


async def _switch(hass: HomeAssistant, entity_id: str, *, on: bool) -> None:
    await hass.services.async_call(
        "switch", "turn_on" if on else "turn_off", {"entity_id": entity_id}, blocking=True
    )
    await hass.async_block_till_done()


class TestToneAndTheRestOfTheDsp:
    async def test_treble_and_balance_round_trip(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Bass had a test; its two siblings did not, though all three share one encoding and one
        write path -- so a mistake in that shared path was only ever checked in one third."""
        await _setup(hass, simulator, enable=[TREBLE, BALANCE])
        await _set_number(hass, TREBLE, -6.0)
        await _set_number(hass, BALANCE, 4.0)
        assert simulator.state.channels[1].treble == -6.0
        assert simulator.state.channels[1].balance == 4.0

    async def test_input_gain_round_trips(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[INPUT_GAIN])
        await _set_number(hass, INPUT_GAIN, 6.0)
        assert simulator.state.input_gain_raw[0] == 12  # sent doubled

    async def test_set_eq_band_needs_at_least_one_parameter(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Calling it empty would send nothing and re-read for no reason, which reads as success."""
        await _setup(hass, simulator, enable=[GAIN_B1])
        with pytest.raises(HomeAssistantError, match="at least one of"):
            await hass.services.async_call(
                "triad_ams", "set_eq_band", {"entity_id": GAIN_B1}, blocking=True
            )

    async def test_set_eq_band_writes_all_three_and_re_reads_once(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[GAIN_B1])
        await hass.services.async_call(
            "triad_ams",
            "set_eq_band",
            {"entity_id": GAIN_B1, "frequency": 1000, "gain": -3.0, "q": 2.0},
            blocking=True,
        )
        await hass.async_block_till_done()
        channel = simulator.state.channels[1]
        assert channel.band_freq[0] == 17  # 1 kHz
        assert channel.band_gain[0] == -3.0
        assert channel.band_q[0] == 6  # Q 2.0


class TestTheSwitchesBothWays:
    """Each switch had its `on` path tested and its `off` path not, though they are separate
    opcodes on this hardware rather than one command with a flag."""

    async def test_loudness_and_mono_toggle_both_directions(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[LOUDNESS, MONO])
        await _switch(hass, LOUDNESS, on=True)
        assert simulator.state.channels[1].loudness is True
        await _switch(hass, LOUDNESS, on=False)
        assert simulator.state.channels[1].loudness is False

        await _switch(hass, MONO, on=True)
        assert simulator.state.channels[1].mono is True
        await _switch(hass, MONO, on=False)
        assert simulator.state.channels[1].mono is False

    async def test_the_trigger_banks_toggle_both_directions(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[BANK_1, ASG])
        for entity in (BANK_1, ASG):
            await _switch(hass, entity, on=True)
            assert hass.states.get(entity).state == "on"
            await _switch(hass, entity, on=False)
            assert hass.states.get(entity).state == "off"


class TestEveryWriteReportsItsFailure:
    """One shared shape, applied per platform: TriadError becomes HomeAssistantError so a refused
    command looks refused. Each wrapper is separate code, so each needs its own check."""

    @pytest.mark.parametrize(
        ("entity", "value", "match"),
        [
            (BASS, -6.0, "command failed for output 1"),
            (INPUT_GAIN, 6.0, "command failed for input 1"),
            (OFF_DELAY, 30, "could not set the audio-sense off delay"),
        ],
    )
    async def test_a_refused_number_write(
        self,
        hass: HomeAssistant,
        simulator: AmsSimulator,
        entity: str,
        value: float,
        match: str,
    ) -> None:
        await _setup(hass, simulator, enable=[entity])
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match=match):
            await _set_number(hass, entity, value)

    async def test_a_refused_eq_band_service_names_the_band(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[GAIN_B1])
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match="output 1 band 1"):
            await hass.services.async_call(
                "triad_ams", "set_eq_band", {"entity_id": GAIN_B1, "gain": -3.0}, blocking=True
            )

    @pytest.mark.parametrize(
        ("entity", "match"),
        [
            (LOUDNESS, "command failed for output 1"),
            (BANK_1, "trigger command failed"),
            (AUDIO_SENSE, "could not change audio sense"),
        ],
    )
    async def test_a_refused_switch_write(
        self, hass: HomeAssistant, simulator: AmsSimulator, entity: str, match: str
    ) -> None:
        await _setup(hass, simulator, enable=[entity])
        simulator.fail_next = Fault.COMMAND_ERROR
        with pytest.raises(HomeAssistantError, match=match):
            await _switch(hass, entity, on=True)
