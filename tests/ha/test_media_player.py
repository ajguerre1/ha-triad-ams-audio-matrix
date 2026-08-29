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
from pytest_homeassistant_custom_component.common import async_fire_time_changed

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


def _route_commands(sim: AmsSimulator, output: int = 1) -> list[str]:
    """Route writes for one output. ``ff5504031d`` is set-route; a query carries f5 after it."""
    prefix = f"ff5504031d{output - 1:02x}"
    return [f for f in sim.received if f.startswith(prefix)]


class TestRoutingIsCoalesced:
    """FR-13. Nothing else proves the debounce does the one thing it exists for."""

    async def test_rapid_selections_reach_the_device_once(
        self, hass: HomeAssistant, simulator: AmsSimulator, freezer
    ) -> None:
        """Four selections inside the window must not become four writes.

        The leading edge sends the first immediately; the rest are coalesced into a single
        trailing run carrying the *last* value. Without the debounce this is four commands, which
        is exactly the behaviour Control4 added its own 250 ms to avoid.
        """
        await _setup(hass, simulator)
        before = len(_route_commands(simulator))

        for source in ("Input 2", "Input 3", "Input 4", "Input 5"):
            await _call(hass, SERVICE_SELECT_SOURCE, **{ATTR_INPUT_SOURCE: source})
        await hass.async_block_till_done()

        # Let the cooldown expire so the coalesced trailing run fires.
        freezer.tick(1.0)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        writes = len(_route_commands(simulator)) - before
        assert writes < 4, f"all four selections reached the device ({writes} writes)"
        assert writes >= 1, "nothing reached the device at all"

    async def test_the_last_selection_is_the_one_that_sticks(
        self, hass: HomeAssistant, simulator: AmsSimulator, freezer
    ) -> None:
        """Coalescing must keep the newest value, not the one that happened to arrive first."""
        await _setup(hass, simulator)

        for source in ("Input 2", "Input 3", "Input 6"):
            await _call(hass, SERVICE_SELECT_SOURCE, **{ATTR_INPUT_SOURCE: source})
        await hass.async_block_till_done()
        freezer.tick(1.0)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()

        assert simulator.state.channels[1].source == 6

    async def test_a_single_selection_is_not_delayed(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The leading edge, and the reason it is leading.

        Home Assistant's select_source is a discrete choice, so a trailing debounce would make a
        synchronous call asynchronous -- a caller reading state straight afterwards would get the
        old source. This is the test that would have caught the original `immediate=False`.
        """
        await _setup(hass, simulator)
        await _call(hass, SERVICE_SELECT_SOURCE, **{ATTR_INPUT_SOURCE: "Input 4"})
        await hass.async_block_till_done()
        # No clock advance: the state must already be right.
        assert hass.states.get(ENTITY).attributes[ATTR_INPUT_SOURCE] == "Input 4"
        assert simulator.state.channels[1].source == 4
