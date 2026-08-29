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
| `tests/simulator.py` | Fake matrix: both framings, faults, external mutation |

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
