"""Audio-sense sensors, and the tiered polling they drive."""

from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

SENSE_1 = "binary_sensor.test_matrix_input_1_audio"
SENSE_3 = "binary_sensor.test_matrix_input_3_audio"
#: FR-14 replaced the read-only `binary_sensor.…audio_sense_enabled` with a switch that reports
#: the same state and can change it. Keeping both would have put one value in two entities.
ENABLED = "switch.test_matrix_audio_sense"


async def _setup(hass: HomeAssistant, sim: AmsSimulator, *, enable: list[str] | None = None):
    """Set up, optionally enabling entities that ship disabled, then reload so they load."""
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


class TestDisabledByDefault:
    async def test_audio_sense_entities_ship_disabled(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """A 24-input matrix would otherwise add 25 entities nobody asked for."""
        entry = await _setup(hass, simulator)
        registry = er.async_get(hass)
        sensors = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain == "binary_sensor"
        ]
        assert sensors, "the platform registered nothing"
        assert all(e.disabled_by is not None for e in sensors)
        assert hass.states.get(SENSE_1) is None

    async def test_inputs_are_not_polled_when_nothing_consumes_them(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The point of tiering. 24 inputs polled for disabled entities is 24 wasted round
        trips every single cycle."""
        entry = await _setup(hass, simulator)
        assert entry.runtime_data.polls_inputs is False


class TestWhenTheMatrixIsNotMeasuring:
    async def test_inputs_are_unavailable_rather_than_reported_off(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The finding this platform was designed around.

        With audio sense disabled the device answers 2 for every input, and an input carrying
        live music is indistinguishable from a dead one. Reporting ``off`` would assert "there is
        no audio" on evidence the device does not have.
        """
        simulator.state.audio_sense_enabled = False
        simulator.state.inputs_with_signal = {3}  # Real signal, but nothing is measuring it.
        await _setup(hass, simulator, enable=[SENSE_1, SENSE_3])

        assert hass.states.get(SENSE_3).state == STATE_UNAVAILABLE
        assert hass.states.get(SENSE_1).state == STATE_UNAVAILABLE

    async def test_the_matrix_diagnostic_explains_why(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Without this, 24 entities go unavailable at once with nothing saying what happened."""
        simulator.state.audio_sense_enabled = False
        await _setup(hass, simulator, enable=[ENABLED])
        assert hass.states.get(ENABLED).state == STATE_OFF


class TestWhenTheMatrixIsMeasuring:
    async def test_signal_and_silence_are_distinguished(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.audio_sense_enabled = True
        simulator.state.inputs_with_signal = {3}
        await _setup(hass, simulator, enable=[SENSE_1, SENSE_3, ENABLED])

        assert hass.states.get(ENABLED).state == STATE_ON
        assert hass.states.get(SENSE_3).state == STATE_ON
        assert hass.states.get(SENSE_1).state == STATE_OFF

    async def test_enabling_a_sensor_turns_input_polling_on(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.audio_sense_enabled = True
        entry = await _setup(hass, simulator, enable=[SENSE_1])
        assert entry.runtime_data.polls_inputs is True

    async def test_audio_sense_is_a_sound_device_class(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.audio_sense_enabled = True
        await _setup(hass, simulator, enable=[SENSE_1])
        assert hass.states.get(SENSE_1).attributes["device_class"] == "sound"
