"""Setup, identity and lifecycle against a simulated matrix.

The identity assertions here are the most important tests in the repository. This integration
replaces one that is live on 26 zones, and it keeps their entity IDs only by reproducing the
previous ``unique_id`` and device-identifier schemes exactly. A refactor that changes either
would not fail loudly -- Home Assistant would silently register new entities with a ``_2`` suffix
and leave every dashboard card and automation pointing at an entity that no longer updates.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.triad_ams.const import DOMAIN
from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket


async def _setup(hass: HomeAssistant, sim: AmsSimulator, **kwargs):
    entry = make_entry(sim, **kwargs)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


class TestSetup:
    async def test_an_entry_creates_one_media_player_per_active_output(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator)
        assert entry.state is ConfigEntryState.LOADED
        players = [s for s in hass.states.async_all() if s.entity_id.startswith("media_player.")]
        assert len(players) == 8

    async def test_only_the_selected_outputs_get_entities(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        await _setup(hass, simulator, options={"active_outputs": [1, 2, 3]})
        players = [s for s in hass.states.async_all() if s.entity_id.startswith("media_player.")]
        assert len(players) == 3

    async def test_an_unreachable_matrix_retries_rather_than_creating_dead_entities(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = make_entry(simulator)
        await simulator.stop()  # Nothing is listening now.
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY


class TestIdentity:
    async def test_entity_unique_ids_use_the_scheme_the_previous_integration_used(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Pins C-07. Changing this format orphans every entity in the live installation.

        Scoped to ``media_player`` deliberately. Only the output entities inherit a format frozen
        by the integration this one replaces; platforms added since are free to name themselves,
        and an assertion over *every* entity would block every new platform for no reason. That
        is what happened when the audio-sense sensors landed.
        """
        entry = await _setup(hass, simulator)
        registry = er.async_get(hass)
        outputs = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain == "media_player"
        ]
        assert outputs, "no media_player entities were registered"
        assert {e.unique_id for e in outputs} == {
            f"{entry.entry_id}_output_{n}" for n in range(1, 9)
        }

    async def test_entities_added_since_cannot_collide_with_the_frozen_output_format(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Newer platforms namespace themselves, so they can never shadow an output entity."""
        entry = await _setup(hass, simulator)
        registry = er.async_get(hass)
        others = [
            e
            for e in er.async_entries_for_config_entry(registry, entry.entry_id)
            if e.domain != "media_player"
        ]
        assert others, "expected at least the audio-sense sensors"
        assert not [e for e in others if "_output_" in e.unique_id]

    async def test_the_device_is_identified_by_the_config_entry_id(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator)
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        assert len(devices) == 1
        assert devices[0].identifiers == {(DOMAIN, entry.entry_id)}


class TestNeverWritesOnConnect:
    async def test_setting_up_only_reads_from_the_device(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Pins design decision D-05, and it is not a stylistic one.

        The Control4 driver pushes its cached state to the matrix on every reconnect, writing
        every output. That is correct for the only controller and destructive for a second one:
        a Home Assistant restart would overwrite whatever Control4 had just set, across all
        56 outputs, with values Home Assistant happened to be holding.
        """
        await _setup(hass, simulator)
        assert simulator.write_commands == [], (
            f"setup wrote to the device: {simulator.write_commands}"
        )


class TestMigration:
    async def test_an_entry_from_the_replaced_integration_loads_unchanged(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        entry = await _setup(hass, simulator, version=1, minor_version=4)
        assert entry.state is ConfigEntryState.LOADED
        assert entry.version == 1
        assert entry.minor_version == 4

    async def test_an_entry_from_a_newer_schema_is_refused_rather_than_corrupted(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """What a downgrade looks like. Proceeding would rewrite an entry this code misreads."""
        entry = make_entry(simulator, version=2, minor_version=1)
        entry.add_to_hass(hass)
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.MIGRATION_ERROR


class TestUnload:
    async def test_unloading_closes_cleanly(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The phase 7 audit found ``with asyncio.timeout(...)`` here, which raises TypeError.

        Every unload and reload would have failed, including the reload that fires on an options
        change. This is the test that would have caught it.
        """
        entry = await _setup(hass, simulator)
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.NOT_LOADED

    async def test_deselecting_outputs_leaves_them_unavailable_rather_than_deleting_them(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """Deselected outputs keep their registry entry and go ``unavailable``.

        This is Home Assistant's behaviour for an entity that stops being provided, and for this
        integration it is the behaviour we want. Removing the registry entries would look tidier,
        but a later re-select would then mint a fresh entity with a ``_2`` suffix -- the same
        failure mode the whole drop-in design exists to avoid. Leaving them means deselect and
        re-select round-trips to the identical entity_id.

        The cost is stale ``unavailable`` entities after a deselect, which is worth stating
        plainly rather than discovering on a dashboard.
        """
        entry = await _setup(hass, simulator)
        hass.config_entries.async_update_entry(entry, options={"active_outputs": [1, 2]})
        await hass.async_block_till_done()

        players = [s for s in hass.states.async_all() if s.entity_id.startswith("media_player.")]
        live = [s for s in players if s.state != "unavailable"]
        assert len(live) == 2, "only the selected outputs should be live"
        assert len(players) == 8, "the rest stay registered, unavailable"

    async def test_reselecting_an_output_restores_the_same_entity_id(
        self, hass: HomeAssistant, simulator: AmsSimulator
    ) -> None:
        """The reason the above behaviour is correct, asserted rather than assumed."""
        entry = await _setup(hass, simulator)
        before = hass.states.get("media_player.test_matrix_output_3").entity_id

        hass.config_entries.async_update_entry(entry, options={"active_outputs": [1, 2]})
        await hass.async_block_till_done()
        hass.config_entries.async_update_entry(entry, options={"active_outputs": [1, 2, 3]})
        await hass.async_block_till_done()

        after = hass.states.get("media_player.test_matrix_output_3")
        assert after is not None and after.entity_id == before
        assert after.state != "unavailable"
