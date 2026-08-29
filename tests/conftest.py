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

import importlib.util
import sys
from pathlib import Path

_COMPONENT = Path(__file__).resolve().parents[1] / "custom_components" / "triad_ams"

if str(_COMPONENT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT))

#: Skip the Home Assistant suite wherever Home Assistant cannot be imported, rather than letting
#: it fail collection. Detected rather than keyed to the platform: what matters is whether the
#: dependency is installed, and a Linux box without it should skip for the same reason a Windows
#: one does. On CI, where it IS installed, nothing is skipped.
#
#: The whole directory is ignored, not a glob of its files: ``tests/ha/conftest.py`` declares the
#: ``pytest_homeassistant_custom_component`` plugin, and pytest loads a conftest before it applies
#: any file-level ignore. Ignoring the directory stops it descending at all.
if importlib.util.find_spec("homeassistant") is None:
    collect_ignore = ["ha"]
