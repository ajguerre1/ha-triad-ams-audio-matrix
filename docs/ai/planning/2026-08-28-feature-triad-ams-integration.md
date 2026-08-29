---
phase: planning
title: Implementation Plan — Triad AMS Audio Matrix integration
description: Ordered tasks traced to requirements, design decisions and test scenarios
feature: triad-ams-integration
status: in-progress
---

# Implementation Plan

Requirements: [`../requirements/2026-08-28-feature-triad-ams-integration.md`](../requirements/2026-08-28-feature-triad-ams-integration.md) ·
Design: [`../design/2026-08-28-feature-triad-ams-integration.md`](../design/2026-08-28-feature-triad-ams-integration.md) ·
Testing: [`../testing/2026-08-28-feature-triad-ams-integration.md`](../testing/2026-08-28-feature-triad-ams-integration.md)

Branch `feature-triad-ams-integration`, no-worktree mode. Task tracing is **unavailable**
(`ai-devkit task` does not exist in 0.56.0); status is tracked in this document instead.

## Milestone 1 — Device layer ✅ complete

| # | Task | Traces to | Evidence |
|---|---|---|---|
| 1 | Capture the protocol from live hardware | C-01, C-04, C-05 | `docs/triad-ams-protocol.md`; every string measured |
| 2 | Public repo, CI, privacy guard | C-03, FR-10 | 5/5 CI jobs; guard caught a real leak on first run |
| 3 | `protocol.py` builders and parsers | D-01 | 42 tests from captured strings |
| 4 | `volume.py` nearest-match taper | — | Round-trips 101 steps; handles `-108.5` |
| 5 | `client.py` serialised socket, learned framing | D-02, D-03, D-04 | Passes both framing personalities |
| 6 | `simulator.py` | — | Both framings, faults, external mutation |

## Milestone 2 — Home Assistant core ✅ complete

| # | Task | Traces to | Evidence |
|---|---|---|---|
| 7 | Integration core, config flow, `media_player` | FR-01, FR-09, C-02, C-07, D-06, D-07, D-08 | hassfest + HACS pass |

## Milestone 3 — Design reconciliation ⏳ next

Phase 7 audits the code against the design; these are the deviations the design review already
predicted. **Do these before adding platforms** — every new platform written against the current
shape multiplies the eventual change.

| # | Task | Traces to | Done when |
|---|---|---|---|
| 8 | Introduce `MatrixSpec`; stop threading `output_count`/`input_count` | D-09 | One spec argument replaces the pairs; `asg_index` defined once; test added |
| 9 | Single `MAX_STEP`, owned by `volume.py` | Design "single source" | `protocol.py` imports it; no second definition |
| 10 | Move `_channel_counts` / `_active_outputs` off `__init__.py` | Design "leaked internal" | `media_player.py` imports no private name from the package root |
| 11 | Verify index mismatch raises `ParseError` | Test scenario | Test asserts a response naming another output is rejected |
| 12 | **Phase 7 audit** — full code-vs-design pass | — | Every deviation listed, fixed or accepted in writing |

## Milestone 4 — Remaining platforms

| # | Task | Traces to | Depends on | Done when |
|---|---|---|---|---|
| 13 | Tiered polling in the coordinator | D-10, NFR-01 | 8 | DSP attributes polled only when a consumer is enabled |
| 14 | `number` — bass, treble, balance, max/turn-on volume, 5-band EQ | FR-02 | 13 | Disabled by default; values round-trip against the simulator |
| 15 | `number` — input gain | FR-03 | 13 | As above |
| 16 | `switch` — loudness, mono-sum | FR-04 | 13 | As above |
| 17 | `switch` — trigger banks and ASG | FR-05 | 8 | ASG index from `MatrixSpec`; 8×8 and 24×24 both covered |
| 18 | `binary_sensor` — audio sense | FR-06, A-01, A-02 | 13 | Polled per A-01; only value `1` detected per A-02 |
| 19 | `sensor` — firmware, connection state | FR-08 | 7 | Diagnostic category, disabled by default |
| 20 | Services: `set_route`, `set_eq_band`, `sync_all`, `send_raw` | FR-11 | 14, 16 | `services.yaml` + translations; hassfest passes |
| 21 | `diagnostics.py`, `repairs.py` | Design "Security" | 7 | **Host and MAC redacted** — diagnostics get pasted into public issues |
| 22 | Brand assets `brand/icon.png` | FR-10 | — | HACS brands warning clears |

~~FR-07 output grouping~~ — **withdrawn**, see the design doc. The group commands stay in
`protocol.py`; nothing here uses them.

## Milestone 5 — Verification

| # | Task | Traces to | Done when |
|---|---|---|---|
| 23 | `tests/ha/` — config flow, coordinator, entity registry | Test scenarios | CI green; **`unique_id` format pinned by test** |
| 24 | Test D-05: never write to the device on connect | D-05 | Simulator asserts zero writes during setup |
| 25 | NFR-01 poll timing; NFR-03 isolation | NFR-01, NFR-03 | Measured against the simulator |
| 26 | Phase 8 coverage pass, phase 9 review | — | `dev-testing` and `dev-review` complete |

## Milestone 6 — Cutover

| # | Task | Traces to | Done when |
|---|---|---|---|
| 27 | Live capture with a zone playing — settles A-01, A-02 | A-01, A-02 | Both answered; assumptions confirmed or the code corrected |
| 28 | **Cutover** per the private runbook | Criteria 1, 2, 3, 5 | Entity count and IDs unchanged; read-back matches; rollback rehearsed |
| 29 | NFR-02 churn measurement, before and after | NFR-02 | ≥300 s samples, like-for-like |

## Risks and sequencing notes

| Risk | Mitigation |
|---|---|
| Platforms written before Milestone 3 multiply the refactor | Milestone 3 is sequenced first, deliberately |
| Entity IDs change at cutover | Task 23 pins the format by test; `.storage` backed up; rollback symmetric |
| A guessed write-response string rejects a successful command | D-04: writes discard their frame. No guessed strings anywhere |
| Site data reaches the public repo | Guard enumerates every tracked file; denylist gitignored |
| Club BBQ 2.1 behaves differently after cutover | Known, documented in the runbook, accepted by the owner |
| HA-side tests cannot run locally at all | CI is the only evidence for Milestone 5; treat a green run as the gate |

## Progress summary

Milestones 1 and 2 are complete: the device layer, the simulator, and a working
`media_player`-only integration that passes hassfest, HACS validation, ruff and 60 tests.

The design review changed the shape of what remains. It withdrew FR-07 on measurement — the
device grouping the requirement assumed is unused by this installation and unimplemented by the
Control4 driver — and it identified four structural deviations in code already written. Those are
Milestone 3 and they come first, because every platform added before them inherits the shape that
has to change.

**Next three actions:** task 8 (`MatrixSpec`), task 9 (single `MAX_STEP`), task 10 (move the
private helpers off `__init__.py`). All three are internal refactors covered by the existing
suite, so regressions surface immediately.

**Riskiest area** is not the code: it is the cutover, because the two integrations cannot coexist
and validation therefore happens after the swap. Task 23's `unique_id` test is the cheapest
insurance available and should not be deferred.
