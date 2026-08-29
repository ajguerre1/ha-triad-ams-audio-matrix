"""Adding a matrix, and the options-flow paths that only run when something is wrong.

This was the thinnest area in the repository at 61%, and the gap mattered more than the number
suggests: the add-a-matrix flow is the *only* thing a new installation touches. The reference
installation never exercises it, because its three entries already existed and were adopted -- so
nothing in daily use would ever have found a break here.
"""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.triad_ams.const import DOMAIN
from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket


async def _start_user_step(hass: HomeAssistant) -> dict:
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


def _connection(sim: AmsSimulator, *, model: str = "AMS8") -> dict:
    return {"name": "Test Matrix", "host": "127.0.0.1", "port": sim.port, "model": model}


class TestAddingAMatrix:
    async def test_the_connection_form_is_shown_first(self, hass: HomeAssistant) -> None:
        result = await _start_user_step(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"
        assert not result["errors"]

    async def test_a_matrix_that_does_not_answer_is_refused(self, hass: HomeAssistant) -> None:
        """Port 52000 being open is not evidence of a Triad matrix, which is why setup asks for
        the firmware version rather than merely opening a socket. Here nothing is listening."""
        async with AmsSimulator() as sim:
            dead_port = sim.port  # Captured, then released when the context exits.
        result = await _start_user_step(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"name": "Nope", "host": "127.0.0.1", "port": dead_port, "model": "AMS8"},
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}

    async def test_a_full_walk_creates_the_entry(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        result = await _start_user_step(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _connection(simulator)
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "channels"

        # Leave output 2 and input 3 unwired, which is the whole point of this step.
        channels = {f"output_{i}": i != 2 for i in range(1, 9)}
        channels |= {f"input_{i}": i != 3 for i in range(1, 9)}
        channels["scan_interval"] = 45
        result = await hass.config_entries.flow.async_configure(result["flow_id"], channels)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Test Matrix"
        assert result["data"]["output_count"] == 8
        assert result["data"]["input_count"] == 8
        assert result["options"]["active_outputs"] == [1, 3, 4, 5, 6, 7, 8]
        assert result["options"]["active_inputs"] == [1, 2, 4, 5, 6, 7, 8]
        assert result["options"]["scan_interval"] == 45

    async def test_the_model_decides_the_channel_counts(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """No command reports the model, so the choice made here is the only source of it. Getting
        it wrong makes every model-dependent index -- the ASG trigger above all -- address the
        wrong matrix, and the device answers plausibly either way."""
        result = await _start_user_step(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _connection(simulator, model="AMS24")
        )
        assert result["step_id"] == "channels"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {f"output_{i}": True for i in range(1, 25)}
            | {f"input_{i}": True for i in range(1, 25)},
        )
        assert result["data"]["output_count"] == 24
        assert result["data"]["input_count"] == 24

    async def test_the_same_matrix_cannot_be_added_twice(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The unique_id is host:port:model. Without it the same matrix could be added again and
        every zone would exist twice, with the duplicates fighting over one socket."""
        entry = make_entry(simulator)
        entry.add_to_hass(hass)
        result = await _start_user_step(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], _connection(simulator)
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "already_configured"


class TestVolumeCapsReachingTheDevice:
    """The two paths that only run when something is wrong, and both used to be silent."""

    async def test_a_cap_change_is_reported_when_the_entry_is_not_loaded(
        self, hass: HomeAssistant, simulator: AmsSimulator, caplog
    ) -> None:
        """A matrix unreachable at startup leaves its entry unloaded while the options flow still
        opens. The caps save and never reach the device -- and because the max-volume register has
        no getter, nothing downstream would ever notice the mismatch.
        """
        entry = make_entry(simulator, options={"output_max_volumes": {"1": 100}})
        entry.add_to_hass(hass)  # Added, deliberately never set up: no runtime_data.

        result = await hass.config_entries.options.async_init(entry.entry_id)
        user_input = {f"output_{i}": True for i in range(1, 9)}
        user_input |= {f"input_{i}": True for i in range(1, 9)}
        user_input |= {f"max_volume_{i}": 100 for i in range(1, 9)}
        user_input["max_volume_1"] = 40
        user_input["scan_interval"] = 30
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["output_max_volumes"]["1"] == 40, "the cap was not saved"
        assert "not loaded" in caplog.text, "the silent no-op was not reported"

    async def test_a_device_that_refuses_the_write_does_not_lose_the_edit(
        self, hass: HomeAssistant, caplog
    ) -> None:
        """The Home Assistant-side clamp still enforces the cap, so failing the whole options save
        over the hardware belt-and-braces would throw away the user's entire edit."""
        sim = AmsSimulator(outputs=8, inputs=8)
        await sim.start()
        entry = make_entry(sim, options={"output_max_volumes": {"1": 100}})
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await sim.stop()  # The matrix goes away between opening the form and submitting it.

        result = await hass.config_entries.options.async_init(entry.entry_id)
        user_input = {f"output_{i}": True for i in range(1, 9)}
        user_input |= {f"input_{i}": True for i in range(1, 9)}
        user_input |= {f"max_volume_{i}": 100 for i in range(1, 9)}
        user_input["max_volume_1"] = 55
        user_input["scan_interval"] = 30
        result = await hass.config_entries.options.async_configure(result["flow_id"], user_input)

        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["output_max_volumes"]["1"] == 55, "the edit was lost"
        assert "max volume" in caplog.text, "the failed write was not reported"

    async def test_the_options_form_is_shown_before_anything_is_submitted(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = make_entry(simulator)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "init"
