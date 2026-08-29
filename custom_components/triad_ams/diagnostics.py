"""Diagnostics download for one matrix.

**Redaction is the whole point of this file.** A diagnostics download is routinely pasted into a
public issue tracker by someone trying to get help, so anything identifying goes out with it. The
host is a LAN address and the MAC identifies the unit; neither helps debug a protocol problem, and
both are exactly what should not end up in a bug report.

What IS included is what actually explains behaviour: the firmware revision (it changes framing),
the model, the poll tiers currently active, and the state the coordinator last read.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TriadConfigEntry
from .ams.errors import CommandError, ParseError, TransportError
from .const import CONF_HOST

#: Config-entry keys never to include. ``unique_id`` is redacted too because it is built from the
#: host and port, so publishing it publishes the address by another route.
REDACT = {CONF_HOST, "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TriadConfigEntry
) -> dict[str, Any]:
    """Everything useful about this matrix, with nothing identifying."""
    coordinator = entry.runtime_data
    data = coordinator.data

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), REDACT),
            "options": async_redact_data(dict(entry.options), REDACT),
            "version": entry.version,
            "minor_version": entry.minor_version,
        },
        "matrix": {
            "model": coordinator.client.spec.name,
            "outputs": coordinator.client.spec.outputs,
            "inputs": coordinator.client.spec.inputs,
            # Firmware first: it decides frame padding, so it is the first thing worth knowing
            # when one unit misbehaves and its siblings do not.
            "firmware": data.firmware if data else None,
            "connected": coordinator.client.connected,
        },
        "polling": {
            "interval_seconds": (
                coordinator.update_interval.total_seconds() if coordinator.update_interval else None
            ),
            "active_outputs": coordinator.active_outputs,
            "active_inputs": coordinator.active_inputs,
            # Which tiers are live explains the traffic volume, and explains why a given entity
            # might be sitting at unavailable.
            "polls_inputs": coordinator.polls_inputs,
            "polls_triggers": coordinator.polls_triggers,
            "dsp_outputs": coordinator.dsp_outputs,
            "last_update_success": coordinator.last_update_success,
        },
        "state": (_state(data) | {"turn_on_volume": await _turn_on_registers(coordinator)})
        if data
        else None,
    }


async def _turn_on_registers(coordinator: Any) -> dict[str, int | None]:
    """Read every active output's turn-on register, live.

    **A zone comes on at this register, not at the volume it was left at**, and measured across a
    live installation on 2026-08-29 it read step 100 -- 0.0 dB, full output -- on most zones.

    It is read here rather than taken from the snapshot because the snapshot usually does not have
    it: ``state.dsp`` is populated only for outputs with a DSP consumer, and DSP entities ship
    disabled. So the register that decides how loud a zone comes on was absent from the one
    artefact someone sends when asking why a zone came on loud.

    Diagnostics is on demand, so these round trips cost nothing in steady state -- which is the
    reason this can be a live read while the poll tiers stay as narrow as they are. A read that
    fails reports ``None`` rather than failing the whole download; a partial answer is worth more
    than none to whoever is reading it.
    """
    registers: dict[str, int | None] = {}
    for output in coordinator.active_outputs:
        try:
            registers[str(output)] = await coordinator.client.get_turn_on_volume_step(output)
        except (CommandError, ParseError, TransportError):
            registers[str(output)] = None
    return registers


def _state(data: Any) -> dict[str, Any]:
    """The last reading, flattened. No addresses appear anywhere in here."""
    return {
        "outputs": {
            str(number): {
                "source": snapshot.source,
                "volume_step": snapshot.volume_step,
                "muted": snapshot.muted,
            }
            for number, snapshot in sorted(data.outputs.items())
        },
        "dsp": {
            str(number): {
                "bass_db": dsp.bass_db,
                "treble_db": dsp.treble_db,
                "balance_db": dsp.balance_db,
                "turn_on_step": dsp.turn_on_step,
                "loudness": dsp.loudness,
                "mono": dsp.mono,
                "bands": [
                    {"frequency_hz": b.frequency_hz, "gain_db": b.gain_db, "q": b.q}
                    for b in dsp.bands
                ],
            }
            for number, dsp in sorted(data.dsp.items())
        },
        "audio_sense": {
            "enabled": data.audio_sense_enabled,
            "off_delay_minutes": data.audio_sense_off_delay,
            "inputs": {str(k): v for k, v in sorted(data.audio_sense.items())},
        },
        "input_gains": {str(k): v for k, v in sorted(data.input_gains.items())},
        "triggers": dict(sorted(data.triggers.items())),
    }
