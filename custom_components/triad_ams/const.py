"""Constants, and the compatibility contract with the config entries already on disk.

This integration reuses the ``triad_ams`` domain deliberately, as a drop-in replacement for
``bharat/homeassistant-triad-ams``. Config entries live in ``.storage`` and survive a HACS
uninstall of the repository files, so an installation that swaps one for the other keeps its
entries -- and with them every entity_id, area, alias, dashboard card and automation reference.

That only holds if the keys below stay exactly as they are. They are not free to rename.
"""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "triad_ams"

DEFAULT_PORT: Final = 52000
DEFAULT_NAME: Final = "Triad AMS"

#: The device announces neither its model nor its channel count, so setup has to ask.
MODELS: Final[dict[str, tuple[int, int]]] = {
    "AMS8": (8, 8),
    "AMS16": (16, 16),
    "AMS24": (24, 24),
}

# -- config entry data keys. Frozen for drop-in compatibility; see the module docstring. -------
CONF_NAME: Final = "name"
CONF_HOST: Final = "host"
CONF_PORT: Final = "port"
CONF_MODEL: Final = "model"
CONF_INPUT_COUNT: Final = "input_count"
CONF_OUTPUT_COUNT: Final = "output_count"

# -- options keys, equally frozen --------------------------------------------------------------
CONF_ACTIVE_INPUTS: Final = "active_inputs"
CONF_ACTIVE_OUTPUTS: Final = "active_outputs"
CONF_INPUT_LINKS: Final = "input_links"
CONF_OUTPUT_MAX_VOLUMES: Final = "output_max_volumes"

#: New in this implementation, so it must have a default that reproduces the previous behaviour
#: for an entry written by the integration being replaced.
CONF_SCAN_INTERVAL: Final = "scan_interval"
DEFAULT_SCAN_INTERVAL: Final = 30

#: Percentage, as the options flow presents it.
MAX_VOLUME_PERCENT: Final = 100

#: Entity unique_ids are ``{entry_id}_output_{n}``. Changing this orphans every existing entity
#: and silently recreates them with a ``_2`` suffix, breaking dashboards and automations.
UNIQUE_ID_OUTPUT: Final = "{entry_id}_output_{number}"
