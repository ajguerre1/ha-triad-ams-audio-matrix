---
phase: testing
title: Testing Strategy — Triad AMS Audio Matrix integration
description: Scenarios derived from requirements success criteria and design components
feature: triad-ams-integration
status: in-progress
---

# Testing Strategy

Requirements: [`../requirements/2026-08-28-feature-triad-ams-integration.md`](../requirements/2026-08-28-feature-triad-ams-integration.md).
Design: [`../design/2026-08-28-feature-triad-ams-integration.md`](../design/2026-08-28-feature-triad-ams-integration.md).

## Test Coverage Goals

The suite splits on one hard constraint (C-06): **Home Assistant cannot be imported on Windows**,
where development happens.

| Suite | Location | Runs | Target |
|---|---|---|---|
| Offline — `ams/` client and protocol | `tests/` | Anywhere | Every parser, every builder, every failure mode |
| Home Assistant — entities, flows, registry | `tests/ha/` | **CI only (Ubuntu)** | Config flow, coordinator, entity identity |
| Live — the three real matrices | Manual, gated | On request | Cutover verification only |

The offline suite is the one that must stay fast and complete, because it is the only one that can
be run while writing code.

**Fixtures are captured, not invented.** Every response string asserted anywhere in this suite is
quoted from `docs/triad-ams-protocol.md`, which records what real hardware actually said. Inventing
plausible-looking response text would make the tests agree with a misreading of the protocol.

## Unit Tests

### `ams/protocol.py` — wire format

- [x] Query framing sets the length byte to account for the `F5` marker
- [x] Indices go on the wire 0-based while callers speak 1-based
- [x] An output outside the matrix is refused before reaching the wire
- [x] Disconnect routes one past the last input, per model
- [x] The mute query uses length byte `04` — the working form, not the driver's constant
- [x] EQ opcodes are a base plus band index; bands outside 1–5 refused
- [x] The ASG trigger opcode differs between an 8×8 and a 24×24
- [x] Single-NUL and 150-byte-padded frames both decode to their text
- [x] Every captured response string parses to the right value
- [x] Two-digit output indices parse (a `\d` regex would clip `Out[24]` to 2)
- [x] Balance parses words (`Bal Center`), not numbers
- [x] Audio-sense indices are 0-based unlike every other response
- [x] An undocumented audio-sense value reads as not-detected (A-02)
- [x] `Command error` raises `CommandError`; unparseable text raises `ParseError`
- [x] An empty frame is retryable (`CommandError`), not a parse failure
- [ ] `MatrixSpec` replaces threaded counts; ASG index derives from the spec

### `ams/volume.py` — the taper

- [x] The curve spans every step the device accepts
- [x] Steps map to the decibels the device reports
- [x] Every step round-trips
- [x] A decibel value absent from the table resolves to the nearest step (`-108.5`)
- [x] Values beyond the curve clamp rather than extrapolate
- [x] An out-of-range step is rejected, not silently clamped

### `ams/client.py` — transport

- [x] Queries return the device's answer; writes round-trip through a read
- [x] **Both firmware framings stay in step over many exchanges** — the desync guard
- [x] `Command error` raises without dropping the connection
- [x] An empty frame is treated the same
- [x] A refused connection and a mid-session disappearance both raise `TransportError`
- [x] Concurrent commands are serialised onto one socket
- [ ] A response naming a different output than the one asked about raises `ParseError`

### Privacy

- [x] The audit enumerates a real set of files
- [x] No private address, unexpected MAC, or denylisted term is committed
- [x] `local/` and `scripts/output/` stay ignored, and nothing under `local/` is tracked

## Integration Tests

Against `tests/simulator.py`, which serves both framing personalities, injects faults, and can
mutate state without a command — standing in for the Control4 controller that shares the hardware.

- [ ] Setup creates one device and one `media_player` per active output
- [ ] **Entity `unique_id` is exactly `{entry_id}_output_{n}`** — pins C-07; a refactor that
      changes it would silently orphan 26 live entities
- [ ] Device `identifiers` are `{(DOMAIN, entry_id)}`
- [ ] A config entry at VERSION 1 / MINOR_VERSION 4 loads without migration
- [ ] A config entry from a newer schema is refused rather than corrupted
- [ ] Options change reloads and adds/removes entities
- [ ] A per-output `CommandError` keeps that output's previous reading; others still update
- [ ] A `TransportError` marks the matrix unavailable and stops early
- [ ] **The integration never writes to the device on connect** — pins design decision D-05; the
      Control4 driver does, and for a second controller that is destructive
- [ ] External mutation is picked up on the next poll (the Control4 case)
- [ ] A write is followed by a re-read of that output only
- [ ] Volume is capped by the per-output max, including via step-up
- [ ] Config flow: success, `cannot_connect`, and duplicate-matrix abort

## End-to-End Tests

- [ ] Add a matrix through the UI, choose channels, entities appear with state
- [ ] Route a zone, change volume, mute, turn off — each verified by read-back

## Test Data

- Simulator MAC `AA:BB:CC:DD:EE:FF`; hosts `127.0.0.1` or `192.0.2.x` (TEST-NET-1)
- Generic `Output N` / `Input N` labels only — never a room or source name
- Captured response strings from `docs/triad-ams-protocol.md`

## Test Reporting & Coverage

`pytest tests/ -v` locally for the offline suite; CI runs the full suite plus hassfest, HACS
validation, ruff check and format, and the strings/translations parity diff.

## Manual Testing

Requires the real matrices; **query-only unless stated**. The house is occupied and a set command
moves audio instantly and audibly.

- [ ] All 56 outputs read back state matching a direct socket query (success criterion 1)
- [ ] Entity count and sampled entity IDs, areas and aliases unchanged after cutover (criterion 2)
- [ ] A Control4 change appears within one poll; an HA change is visible to Control4 (criterion 3)
- [ ] Rollback rehearsed (criterion 5)
- [ ] **Club BBQ output 2 does not follow output 1 from HA** — expected, not a defect. The pairing
      is Control4-side and the matrix has no record of it
- [ ] Capture with a zone playing: does an idle socket push audio-sense? What does value `2` mean?
      (settles A-01 and A-02)

## Performance Testing

- [ ] NFR-01 — a full poll of a 24-output matrix completes well inside the interval. Timed against
      the simulator, then against a real AMS24
- [ ] NFR-02 — steady-state polling adds no measurable churn. Sampled with the owner's
      `tools/ha_state_churn.py`, ≥300 s, like-for-like, before and after
- [ ] NFR-03 — one unreachable matrix does not stall the others. Stop one simulator, assert the
      other coordinators keep updating

## Bug Tracking

Defects found here go to this repository's issue tracker. Anything touching the wider A/V system
goes to the owner's private backlog under the `AV-` series.
