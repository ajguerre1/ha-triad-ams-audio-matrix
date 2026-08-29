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

from custom_components.triad_ams.ams.model import MatrixSpec
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
    and any drift orphans live entities.

    Deriving the model from the simulator rather than hardcoding it is not tidiness: a 24-output
    simulator paired with an entry claiming AMS8 gives the client an 8-output spec, so every
    model-dependent index -- the ASG trigger above all -- is computed for the wrong matrix. A test
    written that way passes or fails for reasons unrelated to what it is checking, which is how
    the 24x24 ASG test failed on its first CI run.
    """
    model = simulator.state.model
    return MockConfigEntry(
        domain=DOMAIN,
        title="Test Matrix",
        unique_id=f"127.0.0.1:{simulator.port}:{model}",
        version=version,
        minor_version=minor_version,
        data={
            "name": "Test Matrix",
            "host": "127.0.0.1",
            "port": simulator.port,
            "model": model,
            "output_count": simulator.state.outputs,
            "input_count": simulator.state.inputs,
        },
        options=options or {},
    )


def channel_input(
    model: str, *, outputs_off: set[int] | None = None, inputs_off: set[int] | None = None
) -> dict[str, bool]:
    """The channel checkboxes a form submission carries, named as the schema names them.

    Derived from ``MatrixSpec`` rather than spelled out, because the key now carries the
    connector kind and that varies by model -- input 5 is ``input_5_shared`` on an AMS8 and
    ``input_5_analog`` on an AMS24. Hardcoding either would pass on one model and fail on the
    other for a reason that reads as a schema bug.
    """
    spec = MatrixSpec.for_model(model)
    off_out, off_in = outputs_off or set(), inputs_off or set()
    return {
        **{spec.output_field(o): o not in off_out for o in range(1, spec.outputs + 1)},
        **{spec.input_field(i): i not in off_in for i in range(1, spec.inputs + 1)},
    }
