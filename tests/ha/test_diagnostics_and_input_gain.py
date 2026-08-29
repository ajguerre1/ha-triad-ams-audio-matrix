"""Input gain entities, and the diagnostics redaction."""

from __future__ import annotations

import json

import pytest
from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.triad_ams.diagnostics import async_get_config_entry_diagnostics
from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

GAIN_1 = "number.test_matrix_input_1_gain"


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


class TestInputGain:
    async def test_it_ships_disabled(self, hass: HomeAssistant, simulator: AmsSimulator) -> None:
        await _setup(hass, simulator)
        assert hass.states.get(GAIN_1) is None

    async def test_it_reads_and_writes_in_decibels(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[GAIN_1])
        assert float(hass.states.get(GAIN_1).state) == 0.0

        await hass.services.async_call(
            "number", "set_value", {"entity_id": GAIN_1, "value": 6.0}, blocking=True
        )
        await hass.async_block_till_done()
        # Wire carries the doubled value; measured on hardware as raw 0x0C -> 6 dB.
        assert simulator.state.input_gain_raw[0] == 12
        assert float(hass.states.get(GAIN_1).state) == 6.0

    async def test_the_range_is_boost_only(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The device offers no attenuation, and clamps above +12 rather than refusing.

        A slider that went negative or past 12 would look like it worked while the hardware
        quietly did something else, so the bounds are advertised rather than discovered.
        """
        await _setup(hass, simulator, enable=[GAIN_1])
        attrs = hass.states.get(GAIN_1).attributes
        assert attrs["min"] == 0.0
        assert attrs["max"] == 12.0
        assert attrs["step"] == 0.5

    async def test_it_shares_the_input_tier_with_audio_sense(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator, enable=[GAIN_1])
        assert entry.runtime_data.polls_inputs is True


class TestDiagnosticsRedaction:
    async def test_the_host_never_appears_anywhere_in_the_download(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The reason this file exists.

        A diagnostics download gets pasted into public issue trackers. Searching the serialised
        blob rather than checking a key is deliberate: the address could reach the output through
        the entry data, the unique_id, or something added later, and only a full-text check
        catches the route nobody thought of.
        """
        entry = await _setup(hass, simulator)
        blob = json.dumps(await async_get_config_entry_diagnostics(hass, entry))

        assert "127.0.0.1" not in blob
        assert str(simulator.port) not in blob.replace(f'"{simulator.port}"', "")
        assert REDACTED in blob

    async def test_it_still_carries_what_explains_behaviour(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Redaction that removed the useful parts would just move the problem."""
        entry = await _setup(hass, simulator)
        diag = await async_get_config_entry_diagnostics(hass, entry)

        assert diag["matrix"]["model"] == "AMS8"
        assert diag["matrix"]["firmware"] == "V1.05.74"
        assert diag["polling"]["active_outputs"] == list(range(1, 9))
        assert diag["state"]["outputs"]["1"]["volume_step"] == 0

    async def test_it_reports_which_poll_tiers_are_live(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Explains both the traffic volume and why a given entity may be unavailable."""
        entry = await _setup(hass, simulator, enable=[GAIN_1])
        diag = await async_get_config_entry_diagnostics(hass, entry)
        assert diag["polling"]["polls_inputs"] is True
        assert diag["polling"]["dsp_outputs"] == []
