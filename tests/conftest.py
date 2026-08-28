"""Shared test configuration for the offline suite.

The device client is placed on ``sys.path`` as a top-level ``ams`` package rather than being
reached through ``custom_components.triad_ams``. That is not a shortcut: importing the parent
package would execute ``custom_components/triad_ams/__init__.py``, which imports Home Assistant
and therefore cannot run on Windows (``homeassistant.runner`` imports POSIX-only ``fcntl``).

Importing ``ams`` directly is what lets this suite run on the development box, and it
structurally enforces that the client has no Home Assistant imports -- if one is ever added,
these tests stop collecting rather than passing on a technicality.

Tests that genuinely need Home Assistant live in ``tests/ha/`` and run in CI only.
"""

from __future__ import annotations

import sys
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "triad_ams"

if str(_COMPONENT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT))
