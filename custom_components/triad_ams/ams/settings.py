"""Resolve a config entry's stored data and options into the settings platforms need.

Deliberately takes plain mappings rather than a ``ConfigEntry``, so this module imports nothing
from Home Assistant and can be tested on the development box. It also keeps the resolution rules
-- which are all judgement calls about malformed or legacy entries -- in one readable place
instead of scattered across ``__init__.py`` and each platform.

It sits under ``ams/`` rather than beside the platforms for a plain reason: a module in the
integration root must use relative imports to reach ``ams``, and relative imports do not resolve
when a test loads it as a top-level module -- which is the mechanism that keeps this suite
runnable on Windows. Its only dependencies are ``MatrixSpec`` and the volume scale, both of which
live here, so this is where it can be both correct and tested.

Every fallback here exists because this integration adopts entries written by the integration it
replaces. Those entries are real, in use, and not all identical, so a strict reading that raised
on an unexpected shape would strand a working installation on setup.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .model import MODELS, MatrixSpec
from .volume import MAX_STEP

#: Used when an entry records neither channel counts nor a recognised model. The largest model is
#: the safe guess: it over-creates entities the user can disable, where under-guessing would make
#: channels unreachable with no obvious cause.
_FALLBACK_MODEL = "AMS24"

DEFAULT_SCAN_INTERVAL = 30

# Option and data keys, defined here because this module is the only thing that interprets them.
# ``const.py`` re-exports them for the config flow. They are frozen for drop-in compatibility
# with the integration this one replaces -- renaming any of them orphans a live config entry.
CONF_ACTIVE_OUTPUTS = "active_outputs"
CONF_ACTIVE_INPUTS = "active_inputs"
CONF_OUTPUT_MAX_VOLUMES = "output_max_volumes"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_MODEL = "model"
CONF_OUTPUT_COUNT = "output_count"
CONF_INPUT_COUNT = "input_count"
#: Added by this integration, so it is absent from every entry written by the one it replaces.
CONF_TRACK_TURN_ON_VOLUME = "track_turn_on_volume"

#: On by default, and the default matters. Control4 does this today: any volume change it makes
#: schedules a write of that volume into the device's turn-on register, which is what makes zones
#: resume where they were left. Defaulting off would silently change how the house behaves on the
#: day Control4 is switched off, which is the one thing the replacement is supposed not to do.
DEFAULT_TRACK_TURN_ON_VOLUME = True


@dataclass(frozen=True, slots=True)
class EntrySettings:
    """Everything the platforms need from a config entry, already validated."""

    spec: MatrixSpec
    active_outputs: list[int]
    active_inputs: list[int]
    scan_interval: int
    track_turn_on_volume: bool
    _max_volumes: dict[int, int]

    @classmethod
    def resolve(cls, data: Mapping[str, Any], options: Mapping[str, Any]) -> EntrySettings:
        spec = cls._spec(data)
        return cls(
            spec=spec,
            active_outputs=cls._active(data, options, CONF_ACTIVE_OUTPUTS, spec.outputs),
            active_inputs=cls._active(data, options, CONF_ACTIVE_INPUTS, spec.inputs),
            scan_interval=int(options.get(CONF_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL),
            track_turn_on_volume=cls._tracking(options),
            _max_volumes=cls._caps(options),
        )

    @staticmethod
    def _tracking(options: Mapping[str, Any]) -> bool:
        """Absent means on -- ``options.get(key, default)`` is wrong here.

        Entries written by the integration this one replaces have no such key, and neither does a
        newly created one until the options flow is opened. Reading a missing key as ``False``
        would turn the behaviour off for every existing installation on upgrade, silently.
        """
        stored = options.get(CONF_TRACK_TURN_ON_VOLUME)
        return DEFAULT_TRACK_TURN_ON_VOLUME if stored is None else bool(stored)

    @staticmethod
    def _spec(data: Mapping[str, Any]) -> MatrixSpec:
        """Prefer the counts the entry recorded; fall back to the model table, then to the largest.

        The counts are authoritative when present because a user could in principle have an
        entry whose model name and counts disagree, and the counts are what the previous
        integration actually used.
        """
        model = str(data.get(CONF_MODEL) or _FALLBACK_MODEL)
        default = MODELS.get(model, MODELS[_FALLBACK_MODEL])
        outputs = int(data.get(CONF_OUTPUT_COUNT) or default.outputs)
        inputs = int(data.get(CONF_INPUT_COUNT) or default.inputs)
        if (outputs, inputs) == (default.outputs, default.inputs):
            return default
        return MatrixSpec(name=model, outputs=outputs, inputs=inputs)

    @staticmethod
    def _active(
        data: Mapping[str, Any], options: Mapping[str, Any], key: str, count: int
    ) -> list[int]:
        """Options win over data; an empty selection means 'not chosen yet', so all are active.

        Channels outside the matrix are dropped rather than kept: shrinking the model in options
        can leave stale numbers behind, and an entity for a channel that cannot be addressed is
        permanently unavailable for no visible reason.
        """
        configured = options.get(key) or data.get(key)
        if not configured:
            return list(range(1, count + 1))
        return sorted({int(c) for c in configured if 1 <= int(c) <= count})

    @staticmethod
    def _caps(options: Mapping[str, Any]) -> dict[int, int]:
        """Normalise volume caps to int keys and a sane range.

        Options survive a JSON round-trip, so the same cap can come back keyed by ``3`` or
        ``"3"``. A stored 0 is clamped to 1: it would otherwise mute a zone permanently with
        nothing in the UI to explain why.
        """
        stored = options.get(CONF_OUTPUT_MAX_VOLUMES) or {}
        return {int(k): max(1, min(int(v), MAX_STEP)) for k, v in stored.items()}

    def max_volume(self, output: int) -> int:
        """The cap for an output, or full scale if none is set."""
        return self._max_volumes.get(output, MAX_STEP)
