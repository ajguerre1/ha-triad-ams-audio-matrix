"""Constants, and the compatibility contract with the config entries already on disk.

This integration reuses the ``triad_ams`` domain deliberately, as a drop-in replacement for
``bharat/homeassistant-triad-ams``. Config entries live in ``.storage`` and survive a HACS
uninstall of the repository files, so an installation that swaps one for the other keeps its
entries -- and with them every entity_id, area, alias, dashboard card and automation reference.

That only holds if the keys below stay exactly as they are. They are not free to rename.

Two things deliberately do *not* live here:

* **The model table** is in ``ams/model.py`` as ``MatrixSpec`` objects, so the channel counts and
  the indices derived from them cannot drift apart.
* **The keys ``ams/settings.py`` interprets** are re-exported from there rather than restated. Two
  copies of a key string is the same drift hazard as two copies of a constant.
"""

from __future__ import annotations

from typing import Final

from .ams.settings import (
    CONF_ACTIVE_INPUTS,
    CONF_ACTIVE_OUTPUTS,
    CONF_INPUT_COUNT,
    CONF_MODEL,
    CONF_OUTPUT_COUNT,
    CONF_OUTPUT_MAX_VOLUMES,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
)

DOMAIN: Final = "triad_ams"

DEFAULT_PORT: Final = 52000
DEFAULT_NAME: Final = "Triad AMS"

# -- config entry data keys. Frozen for drop-in compatibility; see the module docstring. -------
CONF_NAME: Final = "name"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"

# -- options keys, equally frozen --------------------------------------------------------------
#: Carried by entries written by the integration this one replaces, which used it to mirror
#: metadata from a linked media player. Not consumed here yet, and named so that round-tripping
#: the options through this integration does not silently discard it. Decide at the next
#: options-schema change: implement source-name mirroring, or drop the key deliberately.
CONF_INPUT_LINKS: Final = "input_links"

#: Percentage, as the options flow presents it. Equal to ``ams.volume.MAX_STEP`` by construction
#: -- the device's step scale IS 0..100 -- and named here only so the config flow does not reach
#: into the device layer for what is really a UI bound.
MAX_VOLUME_PERCENT: Final = 100

#: Entity unique_ids are ``{entry_id}_output_{n}``. Changing this orphans every existing entity
#: and silently recreates them with a ``_2`` suffix, breaking dashboards and automations.
UNIQUE_ID_OUTPUT: Final = "{entry_id}_output_{number}"

__all__ = [
    "CONF_ACTIVE_INPUTS",
    "CONF_ACTIVE_OUTPUTS",
    "CONF_HOST",
    "CONF_INPUT_COUNT",
    "CONF_INPUT_LINKS",
    "CONF_MODEL",
    "CONF_NAME",
    "CONF_OUTPUT_COUNT",
    "CONF_OUTPUT_MAX_VOLUMES",
    "CONF_PORT",
    "CONF_SCAN_INTERVAL",
    "DEFAULT_NAME",
    "DEFAULT_PORT",
    "DEFAULT_SCAN_INTERVAL",
    "DOMAIN",
    "MAX_VOLUME_PERCENT",
    "UNIQUE_ID_OUTPUT",
]
