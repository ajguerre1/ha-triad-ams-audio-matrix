---
phase: implementation
title: Implementation & Phase 7 Audit — Triad AMS Audio Matrix integration
description: What shipped, how it deviates from the design, and what was done about it
feature: triad-ams-integration
status: in-progress
---

# Implementation Notes

Design: [`../design/2026-08-28-feature-triad-ams-integration.md`](../design/2026-08-28-feature-triad-ams-integration.md) ·
Plan: [`../planning/2026-08-28-feature-triad-ams-integration.md`](../planning/2026-08-28-feature-triad-ams-integration.md)

## What shipped

| File | Role |
|---|---|
| `ams/model.py` | `MatrixSpec`, model table, derived indices |
| `ams/errors.py` | `TransportError` / `CommandError` / `ParseError` |
| `ams/protocol.py` | Builders and parsers, pure functions |
| `ams/volume.py` | 101-point dB taper, nearest-match; owns `MIN_STEP`/`MAX_STEP` |
| `ams/settings.py` | `EntrySettings`; owns the config-entry key strings |
| `ams/client.py` | One serialised socket, learned framing, reconnect |
| `coordinator.py` | Poll cadence, snapshot cache, per-output refresh |
| `entity.py` | Identity and availability |
| `media_player.py` | One entity per active output |
| `config_flow.py`, `const.py`, `__init__.py` | Setup, options, keys |
| `tests/simulator.py` | Fake matrix: both framings, faults, external mutation, bursty replies |

## Milestone 7 — Control4 replacement

### Task 30 — bursty writes and the audio-sense setters (FR-14)

| Changed | What |
|---|---|
| `ams/protocol.py` | `set_audio_sense_enabled`, `set_audio_sense_off_delay`, off-delay range constants |
| `ams/client.py` | `send_bursty`, `set_audio_sense_enabled`, `set_audio_sense_off_delay`, `BURST_QUIET_TIMEOUT`, `MAX_BURST_FRAMES` |
| `tests/simulator.py` | Enable command replies with a burst and applies state; `burst_extra_frames` |
| `tests/test_protocol.py`, `tests/test_client.py` | 8 tests |

**Two things the wire format carried that a reasonable guess would have got wrong.**

The driver's function is `disableAudioSense(disabled)` and it writes **`1` when sense is
enabled**. Naming and behaviour are opposites. Nothing catches the inversion — the device accepts
either value and reports success — so the only symptom would be audio sense doing the reverse of
what the switch says. Taken from the function body, and pinned by a test named after the trap.

The enable command also always carries a trailing `0xFF` that the driver never explains. Copied
rather than dropped: an unexplained constant that real firmware has always received is not a byte
to omit merely because the reason was not written down.

**The drain terminates on a quiet socket, never a frame count.** C-09 measured "roughly one frame
per input" — and *roughly* is the whole point. A client reading exactly `spec.inputs` frames
desyncs by the difference the moment firmware sends one more or fewer, and stays wrong for the life
of the connection with every frame parsing cleanly. `MAX_BURST_FRAMES` is a runaway guard set far
above any plausible burst; reaching it logs a warning rather than being a normal stopping point.
The test that pins this makes the simulator send *more* frames than there are inputs, so the
cheaper implementation fails it.

**Framing state is forgotten after a burst.** The learned single-NUL heuristic draws conclusions
from the padding after a lone reply; a frame inside a burst is not evidence about that, so the
next ordinary exchange re-learns rather than trusting a reading taken under conditions that do not
recur.

**Deviation from design, accepted:** the design said the drain would reuse D-03's quiet-socket
primitive. It uses a longer timeout instead (0.5 s against `DRAIN_TIMEOUT`'s 0.05 s) — the device
is generating a frame per input rather than flushing a buffer it had already filled, so a gap
between frames is not the end of the burst. This makes each enable/disable cost about half a
second, which is acceptable for a configuration action and is why the off-delay setter
deliberately does *not* use this path.

### Tasks 31–36 — the replacement features

| Changed | What |
|---|---|
| `coordinator.py` | `request_audio_sense_settings` tier, `async_set_route` + `async_set_volume` with per-output debouncers, `_store_turn_on_volume`, `ip_mode` on the snapshot |
| `switch.py`, `number.py`, `sensor.py`, `binary_sensor.py` | Audio-sense switch and off-delay number **replacing** two read-only entities; turn-on volume sensor; addressing sensor |
| `repairs.py`, `strings.json` | The issue became a fixable flow |
| `config_flow.py`, `ams/settings.py`, `const.py` | `track_turn_on_volume`; max volume pushed to the device on change |
| `ams/presets.py`, `media_player.py`, `services.yaml` | 7 generic presets and `apply_eq_preset` |
| `tests/` | 14 new offline tests |

**Three places the plan changed on contact.**

*Task 31 became a subtraction.* The design added a switch and a number. What shipped **replaced**
two read-only entities with them — `TriadAudioSenseEnabledSensor` and `TriadAudioSenseDelaySensor`
both reported values their replacements now show *and* control. Keeping both would have put one
value in two entities with only one able to change it. Net: fewer entities, more capability.

The repair issue became **fixable** rather than re-worded. Pointing users at the new switch reads
fine until you remember every non-`media_player` entity is disabled by default — so the
instruction would really have been "find the entity, enable it, then turn it on". A button that
does it is a better answer than better prose.

*The enable flag needed a tier of its own.* It had been riding the per-input tier, which meant
learning one matrix-wide flag cost one read per input — 24 on an AMS24. `request_audio_sense_settings`
separates "two reads for the matrix" from "one read per input".

*Task 36's requirement was wrong.* FR-17 asked for an IP-address diagnostic, on the strength of
the driver's `getIpAddress` constant. Probed read-only against both firmware revisions, the answer
is the literal `dynamic_ip` — the **addressing mode**, no address in it. The entity is named
`Addressing` for what the hardware returns rather than for what the driver's constant is called.
Two consequences: it closes A-03 by confirming both units are on DHCP, and the design's
"redact it alongside host and MAC" requirement falls away, because a mode is not site data.

**A consequence of debouncing worth stating plainly.** `async_set_route` returns before the write
reaches the device, so a routing failure reaches the log rather than the caller. That is inherent
to coalescing, not a shortcut — what the user sees instead is the zone's source staying where it
was, because the re-read reports what the device actually did. Volume is deliberately **not**
debounced for this reason; only its turn-on-volume follow-up waits.

## Phase 7 audit — code against design

Every file read against the design doc. Four deviations were predicted by the design review and
fixed as Milestone 3; the audit then found two more that no test could have caught.

### Fixed

| # | Deviation | Severity | Resolution |
|---|---|---|---|
| 1 | `output_count=`/`input_count=` threaded through ~40 functions, each re-validating | Medium | `MatrixSpec` (D-09) |
| 2 | `MAX_STEP` defined in both `protocol.py` and `volume.py` | Medium | `volume.py` owns it |
| 3 | `_channel_counts`/`_active_outputs` imported as private names from the package root | Medium | `EntrySettings` |
| 4 | Index-mismatch path untested | Low | Simulator fault + regression-verified test |
| 5 | **`with asyncio.timeout(5)` in `async_shutdown`** | **High** | `async with`, plus `TimeoutError` handling |
| 6 | `ams/settings.py` restated the option keys and `DEFAULT_SCAN_INTERVAL` | Medium | `settings.py` owns them; `const.py` re-exports |

**Finding 5 is the one worth dwelling on.** `asyncio.timeout` is an *async* context manager;
`with` raises `TypeError` at runtime, so **every unload and reload of the integration would have
failed** — including the reload that fires whenever options change. It survived because the Home
Assistant layer has no test coverage yet (task 23), ruff does not model it, and nothing in the
offline suite imports `coordinator.py`. Confirmed by executing the construct directly rather than
by reading it.

The generalisation: `ruff` and a green offline suite say nothing at all about the ~40% of this
integration that imports Home Assistant. Until task 23 lands, that code is unverified, and the
audit is the only thing looking at it.

**Finding 6 is self-inflicted**, and worth recording as such. The Milestone 3 refactor that
removed a duplicated constant (`MAX_STEP`) introduced duplicated key strings and a duplicated
`DEFAULT_SCAN_INTERVAL` in the same commit. Moving code between layers invites exactly this: the
new module needs names the old one already had, and restating them is the path of least
resistance.

### Accepted deviations

| Deviation | Why it stands |
|---|---|
| `OutputSnapshot` has no `tone` field yet | Tiered polling (D-10) is task 13. Adding the field before its consumers exist would be untested structure |
| `media_player._command` takes an already-created coroutine | Works, and reads cleanly at the call sites. A `ValueError` from channel validation would escape as-is rather than as `HomeAssistantError`, but every call site passes an output the entity was constructed with, so it cannot be out of range in practice. Revisit if services expose arbitrary channels (task 20) |
| `config_flow._probe` is typed `str \| None` but never returns `None` | Cosmetic; the return value is unused |
| Group commands implemented but unused | FR-07 withdrawn on evidence; the wire format is correct and tested, and nothing here uses it |

## Design decisions confirmed by the audit

- **D-05 (never write on connect)** — verified: `__init__.py` calls only `connect`, and the
  coordinator only `get_*`. No write path exists during setup. This matters because the Control4
  driver *does* push its cached state on reconnect, and doing the same from a second controller
  would overwrite the first one's state across every output.
- **D-01 (no Home Assistant imports under `ams/`)** — verified structurally: the offline suite
  imports `ams` as a top-level package and would stop collecting if an HA import appeared.
- **D-02 (one serialised socket)** — verified by test: eight concurrent reads produce eight
  correct answers over a single connection.

## Follow-ups

1. **Task 23 is now the highest-value remaining work**, not merely a box to tick. Finding 5 shows
   the HA layer is entirely unverified.
2. Revisit `_command`'s exception surface when services expose arbitrary channel numbers.
3. `CONF_INPUT_LINKS` is preserved but unconsumed — decide whether to implement source-name
   mirroring from linked media players, or drop it at the next options-schema change.

## Verification at the close of phase 7

`pytest tests/` — 87 passed, exit 0 · `ruff check .` — clean, exit 0 ·
`ruff format --check .` — 59 files formatted, exit 0 · corrected `async with asyncio.timeout`
executed directly and confirmed to run.

## Phase 9 review — 2026-08-29

Holistic pass over the ~950 lines FR-12…FR-17 added. **No blocking findings.** Four acted on,
three of which were invisible to the test suite because they were about lifetime and cost rather
than behaviour.

| # | Finding | Severity | Resolution |
|---|---|---|---|
| 1 | Debouncers were `async_cancel()`-ed, not `async_shutdown()`-ed | Medium | Cancelling stops the timer but leaves the `Debouncer` holding its function — a closure over the coordinator, and through it the client and socket. Home Assistant added `async_shutdown` to release exactly that (core#137237). This integration reloads on **every options change**, so cancelling alone left one coordinator reachable per edit |
| 2 | A volume-cap change was silently dropped when the entry was not loaded | Medium | A matrix unreachable at startup leaves its entry unloaded while the options flow still opens. Now logged. It matters more here than elsewhere: the max-volume register has **no getter**, so nothing downstream could ever notice the device enforcing a different ceiling than the UI shows |
| 3 | The audio-sense off delay was re-read on every poll | Low | It was cached with the firmware until FR-14 made it settable, then went to the other extreme. It is configuration, and a write refreshes it explicitly. The **enable flag beside it is still polled**, because entity availability and the repair issue both depend on it being current |
| 4 | `send_bursty` returned a byte count nobody read | Low | Now asserted. Unasserted, a drain that read one frame and returned would pass every other test — the surplus would sit in the buffer and desynchronise a *later* exchange, which is precisely how C-09 hid on real hardware |

**Recorded, not fixed.** Removing the two read-only entities (`binary_sensor.…audio_sense_enabled`,
`sensor.…audio_sense_off_delay`) orphans them in the registry for anyone who had enabled them.
Harmless here — the integration this one replaces never created them — but it belongs in a release
note. And `config_flow` remains the thinnest area at 61%; it covers the add-a-matrix flow, which is
not on this installation's cutover path because the entries already exist and are adopted.

**Final state:** 237 tests, 88% coverage, five CI jobs green, working tree clean against
`origin/main`.
