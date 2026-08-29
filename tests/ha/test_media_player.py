"""Media player behaviour against a simulated matrix."""

from __future__ import annotations

import pytest
from homeassistant.components.media_player import (
    ATTR_INPUT_SOURCE,
    ATTR_MEDIA_VOLUME_LEVEL,
    SERVICE_SELECT_SOURCE,
    SERVICE_VOLUME_SET,
)
from homeassistant.components.media_player import (
    DOMAIN as MP_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant

from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

ENTITY = "media_player.test_matrix_output_1"


async def _setup(hass: HomeAssistant, sim: AmsSimulator, **kwargs):
    entry = make_entry(sim, **kwargs)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _call(hass: HomeAssistant, service: str, **data) -> None:
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY, **data}, blocking=True
    )
    await hass.async_block_till_done()


class TestState:
    async def test_an_unrouted_output_reports_off(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        assert hass.states.get(ENTITY).state == STATE_OFF

    async def test_a_routed_output_reports_on_with_its_source(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.mutate(1, source=3, step=50)
        await _setup(hass, simulator)
        state = hass.states.get(ENTITY)
        assert state.state == STATE_ON
        assert state.attributes[ATTR_INPUT_SOURCE] == "Input 3"

    async def test_the_source_list_offers_off_plus_every_active_input(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, options={"active_inputs": [1, 2]})
        assert hass.states.get(ENTITY).attributes["source_list"] == ["Off", "Input 1", "Input 2"]


class TestCommands:
    async def test_selecting_a_source_routes_the_output_and_reads_it_back(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        await _call(hass, SERVICE_SELECT_SOURCE, **{ATTR_INPUT_SOURCE: "Input 4"})
        assert simulator.state.channels[1].source == 4
        assert hass.states.get(ENTITY).attributes[ATTR_INPUT_SOURCE] == "Input 4"

    async def test_turning_off_disconnects_rather_than_powering_the_device_down(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.mutate(1, source=2)
        await _setup(hass, simulator)
        await _call(hass, SERVICE_TURN_OFF)
        assert simulator.state.channels[1].source is None
        assert hass.states.get(ENTITY).state == STATE_OFF

    async def test_setting_volume_reaches_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        await _call(hass, SERVICE_VOLUME_SET, **{ATTR_MEDIA_VOLUME_LEVEL: 0.5})
        assert simulator.state.channels[1].step == 50

    async def test_a_configured_cap_limits_what_reaches_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The cap must bind at the device, not just in the UI.

        A cap enforced only on the slider is not a cap: an automation calling the service
        directly would drive the zone past it.
        """
        await _setup(hass, simulator, options={"output_max_volumes": {"1": 40}})
        await _call(hass, SERVICE_VOLUME_SET, **{ATTR_MEDIA_VOLUME_LEVEL: 1.0})
        assert simulator.state.channels[1].step == 40


class TestExternalChanges:
    async def test_a_change_made_by_another_controller_appears_on_the_next_poll(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The Control4 case, which is the normal case on this hardware.

        The matrix announces nothing, so a change made elsewhere is invisible until polled. This
        is what makes the integration local_polling rather than local_push.
        """
        entry = await _setup(hass, simulator)
        assert hass.states.get(ENTITY).state == STATE_OFF

        simulator.mutate(1, source=5)  # As a second controller would.
        assert hass.states.get(ENTITY).state == STATE_OFF, "must not be seen without a poll"

        await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert hass.states.get(ENTITY).state == STATE_ON
