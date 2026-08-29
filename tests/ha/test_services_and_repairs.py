"""Services, and the one repair issue."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from custom_components.triad_ams.const import DOMAIN
from custom_components.triad_ams.repairs import ISSUE_AUDIO_SENSE_DISABLED
from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

GAIN_1_B1 = "number.test_matrix_eq_band_1_gain"
SENSE_1 = "binary_sensor.test_matrix_input_1_audio"


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


class TestSetEqBand:
    async def test_it_sets_all_three_parameters_at_once(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, enable=[GAIN_1_B1])
        await hass.services.async_call(
            DOMAIN,
            "set_eq_band",
            {"entity_id": GAIN_1_B1, "frequency": 2500, "gain": -3.0, "q": 1.0},
            blocking=True,
        )
        await hass.async_block_till_done()

        channel = simulator.state.channels[1]
        assert channel.band_freq[0] == 21  # 2.5 kHz
        assert channel.band_gain[0] == -3.0
        assert channel.band_q[0] == 5  # index of Q 1.0

    async def test_omitted_parameters_are_left_alone(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Defaulting the missing ones would silently reset a frequency tuned by ear."""
        await _setup(hass, simulator, enable=[GAIN_1_B1])
        before_freq = simulator.state.channels[1].band_freq[0]

        await hass.services.async_call(
            DOMAIN, "set_eq_band", {"entity_id": GAIN_1_B1, "gain": -6.0}, blocking=True
        )
        await hass.async_block_till_done()

        assert simulator.state.channels[1].band_gain[0] == -6.0
        assert simulator.state.channels[1].band_freq[0] == before_freq


class TestSendRaw:
    async def _device_id(self, hass: HomeAssistant, entry) -> str:
        return dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[0].id

    async def test_a_query_returns_the_devices_reply(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator)
        result = await hass.services.async_call(
            DOMAIN,
            "send_raw",
            {"device_id": await self._device_id(hass, entry), "command": "FF5504031EF500"},
            blocking=True,
            return_response=True,
        )
        assert "Volume" in result["response"]
        assert result["is_error"] is False
        assert result["wrote"] is False

    async def test_a_write_is_refused_unless_asked_for(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The guard that matters.

        This hardware accepts out-of-range writes and reports success -- Q and input gain both
        clamp silently -- so an accidental write through this service would be invisible.
        """
        entry = await _setup(hass, simulator)
        device_id = await self._device_id(hass, entry)
        before = len(simulator.write_commands)

        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                "send_raw",
                {"device_id": device_id, "command": "FF5504031E0050"},
                blocking=True,
                return_response=True,
            )
        assert len(simulator.write_commands) == before, "the write reached the device anyway"

    async def test_a_write_goes_through_when_explicitly_allowed(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator)
        result = await hass.services.async_call(
            DOMAIN,
            "send_raw",
            {
                "device_id": await self._device_id(hass, entry),
                "command": "FF5504031E0032",
                "allow_write": True,
            },
            blocking=True,
            return_response=True,
        )
        assert result["wrote"] is True
        assert simulator.state.channels[1].step == 50

    @pytest.mark.parametrize("command", ["not hex", "0102030405"])
    async def test_a_malformed_command_is_rejected_before_it_is_sent(
        self, hass: HomeAssistant, simulator: AmsSimulator, command: str
    ) -> None:
        """Anything not starting FF 55 cannot be parsed by the device, so sending it only
        risks desynchronising a connection that other entities are using."""
        entry = await _setup(hass, simulator)
        with pytest.raises(ServiceValidationError):
            await hass.services.async_call(
                DOMAIN,
                "send_raw",
                {"device_id": await self._device_id(hass, entry), "command": command},
                blocking=True,
                return_response=True,
            )


class TestAudioSenseRepair:
    async def test_no_issue_when_nobody_is_using_audio_sense(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Audio sense off with no consumers is the shipping default, not a fault.

        Flagging it would be nagging about a setting nobody enabled, and a repairs list full of
        those trains people to ignore the one that matters.
        """
        simulator.state.audio_sense_enabled = False
        entry = await _setup(hass, simulator)
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_AUDIO_SENSE_DISABLED}_{entry.entry_id}"
        )
        assert issue is None

    async def test_an_issue_is_raised_when_the_entities_can_never_report(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.audio_sense_enabled = False
        entry = await _setup(hass, simulator, enable=[SENSE_1])
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_AUDIO_SENSE_DISABLED}_{entry.entry_id}"
        )
        assert issue is not None
        assert issue.severity is ir.IssueSeverity.WARNING
        assert issue.translation_placeholders["matrix"] == "Test Matrix"

    async def test_no_issue_when_the_matrix_is_measuring(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        simulator.state.audio_sense_enabled = True
        entry = await _setup(hass, simulator, enable=[SENSE_1])
        issue = ir.async_get(hass).async_get_issue(
            DOMAIN, f"{ISSUE_AUDIO_SENSE_DISABLED}_{entry.entry_id}"
        )
        assert issue is None
