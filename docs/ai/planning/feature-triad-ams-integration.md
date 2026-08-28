# Planning — Triad AMS Audio Matrix integration

**Phase:** planning · **Updated:** 2026-08-28

Protocol reference: [`docs/triad-ams-protocol.md`](../../triad-ams-protocol.md).
Requirements: [`../requirements/feature-triad-ams-integration.md`](../requirements/feature-triad-ams-integration.md).

## Done

| # | Task | Evidence |
|---|---|---|
| 1 | Capture the protocol from live hardware | `docs/triad-ams-protocol.md`; every response string measured |
| 2 | Public repo, CI, privacy guard | 5/5 CI jobs green; `tests/test_no_site_data.py` caught a real leak on its first run |
| 3 | `ams/protocol.py` — command builders and parsers | 42 tests, all built from captured strings |
| 4 | `ams/volume.py` — dB taper, nearest-match | Round-trips all 101 steps; handles the `-108.5` the table lacks |
| 5 | `ams/client.py` — serialised socket, learned framing | Passes against both firmware personalities |
| 6 | `tests/simulator.py` — fake matrix | Both framings, injectable faults, external mutation |
| 7 | Integration core, config flow, `media_player` | hassfest + HACS validation pass |

## Remaining

| # | Task | Depends on | Notes |
|---|---|---|---|
| 8 | **Live capture harness** (`scripts/probe_*.py`) | — | Settles both open questions and produces fixtures. Output is gitignored. **Do this before task 12.** |
| 9 | `number` platform — tone, EQ, gains, caps | 3, 5 | Disabled by default; a 24×24 would otherwise add ~500 entities |
| 10 | `switch` platform — loudness, mono, triggers | 3, 5 | ASG opcode depends on model; already handled in `protocol.py` |
| 11 | `binary_sensor` platform — audio sense | 8 | Push vs poll depends on task 8's answer |
| 12 | Output grouping (A–G) | 8 | Club BBQ's 2.1 zone depends on this (AV-03) |
| 13 | `sensor` platform — firmware, connection state | 7 | Diagnostic category |
| 14 | Services: `set_route`, `set_eq_band`, `sync_all`, `send_raw` | 9, 10 | |
| 15 | `diagnostics.py` + `repairs.py` | 7 | Diagnostics must redact host and MAC |
| 16 | `tests/ha/` — config flow, coordinator, entity registry | 7 | CI-only; must pin the `unique_id` format |
| 17 | Brand assets (`brand/icon.png`) | — | HACS warns without them |
| 18 | **Cutover** | 7, 16 | Runbook is in the private repo; back up `.storage` first |

## Task 18 — cutover, in order

1. Back up `.storage/core.config_entries` and `.storage/core.entity_registry`.
2. Record the current entity count and a sample of entity IDs, areas and aliases.
3. HACS → remove the old repository. **Do not delete the config entries.**
4. HACS → add this repository as a custom repository, install, restart.
5. Verify: entity count unchanged, sampled entity IDs still present with their areas and aliases,
   no new errors in the log.
6. Verify a source and volume change from Home Assistant reads back correctly from a raw socket
   query, and that a Control4 change appears within one poll.
7. If anything is wrong: re-add the old repository and restart. Entries are untouched throughout.

## Risks

| Risk | Mitigation |
|---|---|
| Entity IDs change on cutover | `unique_id` format pinned by test (task 16); `.storage` backed up; rollback symmetric |
| A guessed write-response string rejects a command that succeeded | Writes consume and discard their frame; callers re-read. No guessed strings anywhere |
| Site data reaches the public repo | `tests/test_no_site_data.py` enumerates every tracked file; denylist stays gitignored |
| Poll traffic disturbs Control4 | Concurrency measured safe; interval configurable, default 30 s |
