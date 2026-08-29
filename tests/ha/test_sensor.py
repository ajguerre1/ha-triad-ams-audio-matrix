"""Per-matrix diagnostic sensors."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from tests.ha.conftest import make_entry
from tests.simulator import AmsSimulator

pytestmark = pytest.mark.enable_socket

FIRMWARE = "sensor.test_matrix_firmware"
ADDRESSING = "sensor.test_matrix_addressing"
#: FR-14 made the off delay settable, so the read-only sensor became a `number`.
DELAY = "number.test_matrix_audio_sense_off_delay"


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


async def test_diagnostics_ship_disabled(hass: HomeAssistant, simulator: AmsSimulator) -> None:
    await _setup(hass, simulator)
    assert hass.states.get(FIRMWARE) is None


async def test_firmware_is_reported(hass: HomeAssistant, simulator: AmsSimulator) -> None:
    """Behaviour genuinely differs between revisions -- the AMS8 on V1.05.74 pads error frames --
    so knowing which unit is on which build is the first diagnostic question."""
    await _setup(hass, simulator, enable=[FIRMWARE])
    assert hass.states.get(FIRMWARE).state == "V1.05.74"


async def test_the_off_delay_is_reported_in_minutes(
    hass: HomeAssistant, simulator: AmsSimulator
) -> None:
    """The device's own unit. It answers 0x1 for the one-minute default, and the Control4
    driver's initialiser of 30 is thirty minutes on this scale, not thirty seconds."""
    simulator.state.audio_sense_off_delay = 1
    await _setup(hass, simulator, enable=[DELAY])
    state = hass.states.get(DELAY)
    assert state.state == "1"
    assert state.attributes["unit_of_measurement"] == "min"


async def test_the_addressing_mode_is_reported(
    hass: HomeAssistant, simulator: AmsSimulator
) -> None:
    """FR-17, renamed on measurement.

    The Control4 driver calls this command ``getIpAddress``, but both real units answer the
    literal ``dynamic_ip`` -- the addressing mode, with no address in it. The entity is named for
    what the hardware returns rather than for the driver's constant.
    """
    await _setup(hass, simulator, enable=[ADDRESSING])
    assert hass.states.get(ADDRESSING).state == "dhcp"


async def test_firmware_is_not_re_read_on_every_poll(
    hass: HomeAssistant, simulator: AmsSimulator
) -> None:
    """Two extra round trips per cycle, forever, for a value that cannot change mid-session."""
    entry = await _setup(hass, simulator, enable=[FIRMWARE])
    before = sum(1 for f in simulator.received if f.startswith("ff5503066500"))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    after = sum(1 for f in simulator.received if f.startswith("ff5503066500"))
    assert after == before, "firmware was re-read on a later poll"
