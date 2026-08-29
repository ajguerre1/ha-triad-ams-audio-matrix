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
- [x] `MatrixSpec` replaces threaded counts; ASG index derives from the spec

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
- [x] A response naming a different output than the one asked about raises `ParseError`
      (regression-verified: removing the guard fails the test)

#### Bursty writes — FR-14, task 30

- [x] **Enabling audio sense leaves the stream aligned** — every following query is answered about
      what it asked. This is the C-09 failure: the surplus frames are read as answers to later
      queries and every one parses cleanly, so only the index reveals it
- [x] **The drain ends on a quiet socket, not on an expected frame count** — the simulator sends
      more frames than there are inputs (`burst_extra_frames`), which is what C-09's "*roughly*
      one per input" leaves room for. A client trusting the count desyncs by the difference and
      stays wrong for the life of the connection
- [x] The enable setting actually takes effect, both directions
- [x] The off-delay setter round-trips and does **not** use the bursty path — it is an ordinary
      single-response write, and routing it through the drain would cost half a second per call
- [x] **`1` means enabled**, despite the driver's function being named `disableAudioSense(disabled)`
      — an inversion nothing else would catch, since the device accepts either value and reports
      success

#### EQ presets — FR-16, task 35

- [x] Every preset has exactly five bands, and every frequency and Q index resolves to a real
      value. Guards a transcription slip that is otherwise invisible: the device **clamps** an
      out-of-range Q and reports success, so a bad literal would ship and merely sound wrong
- [x] Flat is actually flat — the one preset whose correctness can be asserted rather than checked
- [x] **High Pass and Low Pass keep the driver's non-ascending band order.** Band order is what
      reaches the device, so sorting them tidily would move each correction onto a different band
- [x] An unknown preset name raises and lists the real ones, rather than falling back to Flat and
      applying the opposite of what was asked

#### Addressing mode — FR-17, task 36

- [x] The query matches the driver's constant
- [x] `dynamic_ip` parses as DHCP — measured on two units across two firmware revisions
- [x] An unrecognised answer is **returned rather than raised**, deliberately against this
      module's parse-or-raise rule: this feeds a diagnostic whose job is to report what the unit
      says, and a ParseError would hide the one case worth seeing
- [x] A `Command error` still raises — tolerating unknown text must not extend to tolerating a
      failed command

#### Turn-on volume tracking — FR-12, task 33

- [x] **A missing option key means tracking is on.** Every entry written by the integration this
      one replaces lacks the key, so reading it as `False` would silently drop the behaviour for
      every existing installation on upgrade
- [x] Explicit `true` and `false` are both honoured

### Privacy

- [x] The audit enumerates a real set of files
- [x] No private address, unexpected MAC, or denylisted term is committed
- [x] `local/` and `scripts/output/` stay ignored, and nothing under `local/` is tracked

## Integration Tests

Against `tests/simulator.py`, which serves both framing personalities, injects faults, and can
mutate state without a command — standing in for the Control4 controller that shares the hardware.

In `tests/ha/` — **CI only**.

- [x] Setup creates one device and one `media_player` per active output
- [x] **Entity `unique_id` is exactly `{entry_id}_output_{n}`** — pins C-07; a refactor that
      changes it would silently orphan 26 live entities
- [x] Device `identifiers` are `{(DOMAIN, entry_id)}`
- [x] A config entry at VERSION 1 / MINOR_VERSION 4 loads without migration
- [x] A config entry from a newer schema is refused rather than corrupted
- [x] An unreachable matrix retries setup rather than creating dead entities
- [x] Unloading closes cleanly — **the test that would have caught the phase 7 shutdown bug**
- [x] Deselecting outputs leaves them registered and unavailable, and re-selecting restores the
      identical entity_id
- [x] **The integration never writes to the device on connect** — pins design decision D-05; the
      Control4 driver does, and for a second controller that is destructive
- [x] External mutation is picked up on the next poll (the Control4 case)
- [x] Volume is capped by the per-output max **at the device**, not only on the slider
- [x] Routing, turn-off and volume commands reach the device and read back
- [ ] A per-output `CommandError` keeps that output's previous reading; others still update
- [ ] A `TransportError` marks the matrix unavailable and stops early
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

`tests/conftest.py` skips `tests/ha/` wherever Home Assistant is not importable — detected via
`find_spec`, not by platform, since what matters is whether the dependency is present. The
directory is ignored wholesale rather than by glob, because pytest loads a conftest before
applying any file-level ignore.

**Results, 2026-08-28**

| Run | Where | Result |
|---|---|---|
| Offline suite | Windows dev box | **92 passed**, exit 0 |
| Full suite | CI (Ubuntu) | **119 passed**, exit 0 |
| `ruff check` / `ruff format --check` | Both | Clean, exit 0 |
| hassfest, HACS validation, strings parity | CI | Pass |

The 27-test gap between the two runs is exactly `tests/ha/`, and it is the part that cannot be
verified locally at any point. CI is the gate for it.

## Manual Testing

Requires the real matrices; **query-only unless stated**. The house is occupied and a set command
moves audio instantly and audibly.

- [ ] All 56 outputs read back state matching a direct socket query (success criterion 1)
- [ ] Entity count and sampled entity IDs, areas and aliases unchanged after cutover (criterion 2)
- [ ] A Control4 change appears within one poll; an HA change is visible to Control4 (criterion 3)
- [ ] Rollback rehearsed (criterion 5)
- [ ] **The 2.1 zone's output 2 does not follow output 1 from HA** — expected, not a defect. The
      pairing is Control4-side and the matrix has no record of it
- [x] **Capture with a zone playing, 2026-08-29** — settled A-02 (`2` = not measuring) and mostly
      A-01 (a passive socket saw nothing in 40 s). Surfaced C-09: enabling audio sense returns a
      burst of ~one frame per input
- [ ] Once audio sense is enabled in the Control4 driver: confirm the input sensors report real
      values, and whether a signal *transition* pushes an unsolicited frame

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
