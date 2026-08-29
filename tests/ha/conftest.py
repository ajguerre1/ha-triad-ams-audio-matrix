"""Fixtures for the Home Assistant-dependent suite.

These tests run in CI only. Home Assistant imports POSIX-only ``fcntl``, so it cannot be imported
on the Windows development box, which is why the device client under ``ams/`` is kept free of
Home Assistant imports and tested separately.

The phase 7 audit is the argument for this suite existing: it found ``async_shutdown`` using
``with`` on an async context manager, which would have raised on every unload and reload. Nothing
caught it, because nothing here imported ``coordinator.py``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.triad_ams.const import DOMAIN
from tests.simulator import AmsSimulator

# No ``pytest_plugins`` declaration here. pytest refuses it in a non-root conftest, and it is
# unnecessary anyway: pytest-homeassistant-custom-component registers itself through entry points
# when installed, so its fixtures are available wherever it is present -- which is exactly the
# condition tests/conftest.py already tests to decide whether to collect this directory.


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Home Assistant will not load a custom component in tests without this."""
    return


@pytest.fixture
async def simulator() -> AsyncIterator[AmsSimulator]:
    """An 8x8 matrix on loopback."""
    sim = AmsSimulator(outputs=8, inputs=8)
    await sim.start()
    yield sim
    await sim.stop()


def make_entry(
    simulator: AmsSimulator,
    *,
    options: dict | None = None,
    version: int = 1,
    minor_version: int = 4,
) -> MockConfigEntry:
    """A config entry shaped exactly like the ones this integration adopts.

    Version 1 / minor 4 and these key names are the schema written by
    ``bharat/homeassistant-triad-ams``. Reproducing them here is the whole point: entries in
    ``.storage`` survive a HACS uninstall, so the real installation loads entries of this shape
    and any drift orphans 26 live entities.
    """
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Matrix",
        unique_id=f"127.0.0.1:{simulator.port}:AMS8",
        version=version,
        minor_version=minor_version,
        data={
            "name": "Test Matrix",
            "host": "127.0.0.1",
            "port": simulator.port,
            "model": "AMS8",
            "output_count": 8,
            "input_count": 8,
        },
        options=options or {},
    )
