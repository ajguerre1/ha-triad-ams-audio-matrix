"""Input gain entities, and the diagnostics redaction."""

from __future__ import annotations

import json
import re

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
    async def test_no_network_address_appears_anywhere_in_the_download(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The reason this file exists.

        A diagnostics download gets pasted into public issue trackers. Searching the serialised
        blob with a general address pattern -- rather than checking a key, or grepping for the one
        address this test happens to use -- is deliberate: the host could reach the output through
        the entry data, the unique_id, or a field added later, and only a full-text check catches
        the route nobody thought of.
        """
        entry = await _setup(hass, simulator)
        blob = json.dumps(await async_get_config_entry_diagnostics(hass, entry))

        addresses = re.findall(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", blob)
        assert not addresses, f"an address reached the diagnostics: {addresses}"
        assert REDACTED in blob, "nothing was redacted at all"

    async def test_the_port_is_deliberately_not_redacted(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """A deliberate exception, asserted so it stays deliberate.

        The port is 52000 on every installation -- it is the documented default and appears
        throughout the public protocol reference, so redacting it protects nothing. A *non*-default
        port, on the other hand, is exactly the sort of thing worth knowing when a connection
        fails, so hiding it would cost real debugging signal for no privacy gain.
        """
        entry = await _setup(hass, simulator)
        diag = await async_get_config_entry_diagnostics(hass, entry)
        assert diag["entry"]["data"]["port"] == simulator.port
        assert diag["entry"]["data"]["host"] == REDACTED

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


class TestTurnOnRegisterIsObservable:
    """AV-21: a zone comes on at its turn-on register, and nothing surfaced that register.

    Measured 2026-08-29 across a live installation: the register read **step 100 -- 0.0 dB, full
    output -- on 23 of 27 zones**. Routing a source to one of those brings it up at maximum,
    whatever volume was set beforehand.

    It was invisible. There is no enabled entity for it by default, and `state.dsp` is populated
    only for outputs with a DSP consumer, so with DSP entities disabled -- the default -- the one
    artefact someone would send when asking why a zone came on loud omitted the answer.
    """

    async def test_diagnostics_report_it_without_the_dsp_platform(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator)
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        # The precondition: no DSP consumer, so the existing dsp section cannot carry it.
        assert diagnostics["polling"]["dsp_outputs"] == []
        assert diagnostics["state"]["dsp"] == {}

        registers = diagnostics["state"]["turn_on_volume"]
        assert set(registers) == {str(o) for o in diagnostics["polling"]["active_outputs"]}
        assert all(v is not None for v in registers.values())

    async def test_it_reports_what_the_device_holds(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """A value read from the device, not echoed back from the volume the coordinator knows."""
        entry = await _setup(hass, simulator)
        simulator.state.channels[1].turn_on_step = 97

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)

        assert diagnostics["state"]["turn_on_volume"]["1"] == 97
