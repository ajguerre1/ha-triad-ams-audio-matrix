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


class TestTheRepairFixesItself:
    """FR-14 made the issue fixable, not merely better worded.

    Pointing at the new switch reads fine until you remember every non-media_player entity ships
    disabled: the instruction would really have been "find the entity, enable it, then turn it on".
    """

    async def test_the_flow_enables_measuring_and_clears_the_issue(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        from homeassistant.components.repairs import repairs_flow_manager
        from homeassistant.components.repairs.issue_handler import (
            async_process_repairs_platforms,
        )
        from homeassistant.setup import async_setup_component

        # The repairs component is not loaded by default in the test harness, and without it the
        # flow manager this test drives does not exist at all.
        assert await async_setup_component(hass, "repairs", {})

        simulator.state.audio_sense_enabled = False
        entry = await _setup(hass, simulator, enable=[SENSE_1])

        issue_id = f"{ISSUE_AUDIO_SENSE_DISABLED}_{entry.entry_id}"
        registry = ir.async_get(hass)
        assert registry.async_get_issue(DOMAIN, issue_id) is not None, "the issue was never raised"

        await async_process_repairs_platforms(hass)
        # The public accessor, rather than reaching into hass.data for a key that is an internal
        # detail -- the first version of this test guessed at that key and got it wrong.
        manager = repairs_flow_manager(hass)
        assert manager is not None, "the repairs component did not register a flow manager"
        # The handler key is the *domain*; `data` carries the issue id. Home Assistant looks the
        # issue up and hands its stored data to async_create_fix_flow, which is where entry_id
        # comes from -- the flow never receives it from the caller.
        started = await manager.async_init(DOMAIN, data={"issue_id": issue_id})
        result = await manager.async_configure(started["flow_id"], {})
        await hass.async_block_till_done()

        assert result["type"] == "create_entry"
        assert simulator.state.audio_sense_enabled is True, "the flow did not enable measuring"

        await entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert registry.async_get_issue(DOMAIN, issue_id) is None, "the issue did not clear"


class TestApplyEqPreset:
    """FR-16. Hosted on media_player because every DSP entity ships disabled."""

    async def test_a_preset_writes_all_five_bands(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator)
        await hass.services.async_call(
            DOMAIN,
            "apply_eq_preset",
            {"entity_id": "media_player.test_matrix_output_1", "preset": "Rock"},
            blocking=True,
        )
        await hass.async_block_till_done()

        channel = simulator.state.channels[1]
        assert channel.band_gain == [5, 2, -1, 2, 5], "Rock's gains did not reach the device"
        assert channel.band_freq == [4, 10, 16, 22, 28]

    async def test_it_works_with_every_dsp_entity_disabled(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The reason it lives on media_player rather than on an EQ entity.

        Nothing is enabled here beyond the default set, which is exactly how a fresh installation
        looks. A service hosted on a disabled entity would be unreachable in that state.
        """
        await _setup(hass, simulator)
        assert hass.states.get(GAIN_1_B1) is None, "an EQ entity was enabled; the test proves less"

        await hass.services.async_call(
            DOMAIN,
            "apply_eq_preset",
            {"entity_id": "media_player.test_matrix_output_1", "preset": "Flat"},
            blocking=True,
        )
        await hass.async_block_till_done()
        assert simulator.state.channels[1].band_gain == [0, 0, 0, 0, 0]

    async def test_an_unknown_preset_is_refused(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Falling back to Flat would silently apply the opposite of what was asked for."""
        await _setup(hass, simulator)
        with pytest.raises(Exception):  # noqa: B017 - voluptuous raises before our code runs
            await hass.services.async_call(
                DOMAIN,
                "apply_eq_preset",
                {"entity_id": "media_player.test_matrix_output_1", "preset": "Dubstep"},
                blocking=True,
            )
