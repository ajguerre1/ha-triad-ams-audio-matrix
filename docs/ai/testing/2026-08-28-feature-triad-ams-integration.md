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

#### The replacement features on the HA layer — tasks 31-36

- [x] **Rapid selections reach the device once** (FR-13). Four selections inside the window; the
      leading edge sends the first, the rest coalesce into one trailing run
- [x] The last selection is the one that sticks — coalescing must keep the newest value
- [x] A single selection is **not delayed**. This is the test that would have caught the original
      trailing-edge debounce
- [x] **Turn-on volume stores what the device reported, not what was sent** (FR-12)
- [x] A volume change is not stored before it settles
- [x] Tracking on gives a read-only sensor; tracking off gives a writable number, and never both
- [x] Audio-sense switch toggles measuring, and its **reply burst does not desync the stream**
      end to end (C-09 through the entity, not only at the client)
- [x] The switch does not drag the per-input tier with it — one matrix-wide flag must not cost
      one read per input
- [x] Off-delay number round-trips, in minutes
- [x] The repair **fixes itself**: the flow enables measuring and the issue clears
- [x] `apply_eq_preset` writes all five bands, and works with **every DSP entity disabled** —
      which is the reason it lives on `media_player`
- [x] Max volume reaches the device on an options change and **not** on connect (D-05 still holds)

### Regression-verified

Each of the two load-bearing guards was removed on a throwaway branch and the suite re-run. The
result was **exactly two failures, the right two**, with messages naming the real defect:

| Guard removed | Test that caught it | Message |
|---|---|---|
| The routing debounce | `test_rapid_selections_reach_the_device_once` | `all four selections reached the device (4 writes)` |
| Storing the re-read value | `test_the_stored_value_is_what_the_device_reported_not_what_was_sent` | `the requested step was stored rather than the one the device adopted` |

Nothing else failed, so neither test is passing for an unrelated reason.

**A third guard, added 2026-08-29 with the AV-21 fix, was verified the same way** — and it had to
be, because the first attempt at that fix passed its tests while still failing on hardware.

| Guard removed | Tests that caught it |
|---|---|
| The 30 ms spacing between route read-back retries | `test_a_source_change_is_not_published_stale`, `test_turning_off_is_not_published_stale` |

**This is the one where the test double was the defect.** The simulator first modelled the device's
read-after-write race as *one stale answer per write*, chosen so that `asyncio.sleep()` could not
count as a fix. That model is satisfied by any retry at all, so it passed a retry loop whose three
attempts completed in about a millisecond — inside a window lasting up to 25 ms, where every read
returns the same stale answer. Green build, same bug on the device.

The lesson generalises past this integration: **a double's simplification decides which fixes it is
capable of rejecting.** Model the mechanism, not the symptom. The race is now a time window, so a
fix has to be spaced as well as repeated, and removing the spacing fails both tests.

### Two findings from writing these

**The simulator's volume taper was wrong, and it had been wrong all along.** It interpolated
decibels from twenty points, on the stated grounds that a straight line was "close enough for a
test double". It was not: **78 of the 101 steps failed a step → dB → step round trip** — step 30
reported −38.8 dB, which parses back as step 26. Every volume assertion in this suite had been
made against a curve the hardware does not have, and the turn-on test could not have passed
however correct the code was. The table is now duplicated literally, exactly as the frequency and
Q tables in the same file already are and for the same stated reason. All 101 steps round-trip.

**`freezer` cannot drive a `Debouncer`.** It schedules with `hass.loop.call_later`, which runs on
the event loop's own clock: `async_fire_time_changed` does not drive it, and freezing the clock
stops it firing at all. The first version of these tests hung CI for fifteen minutes before it was
cancelled. Route coalescing now waits out the real 0.25 s; turn-on patches its 10 s constant down
to 0.2 s. The pytest job gained `timeout-minutes: 10`, because a hang is not a failure — it would
otherwise hold a runner until GitHub's six-hour default.

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

**Results, 2026-08-29** *(supersedes the 2026-08-28 figures below)*

| Run | Where | Result |
|---|---|---|
| Offline suite | Windows dev box | **181 passed**, exit 0 |
| Full suite | CI (Ubuntu) | **341 passed**, exit 0 |
| Coverage | CI | **100%** — 1936 statements, 0 missed |
| `ruff check` / `ruff format --check` | Both | Clean, exit 0 |
| hassfest, HACS validation, strings parity | CI | Pass |

**Coverage is measured in CI, not locally, and the distinction matters.** A local run reports 82%
for `ams/` alone and understates it, because everything the entities exercise is invisible to a
suite that cannot import Home Assistant. Measured where the whole suite runs:

**Every module is at 100%.** The suite reached it on 2026-08-29, from 88%, and two of the last
lines were closed by *deleting* code rather than testing it — see below.

| Suite | Tests |
|---|---|
| Offline (`ams/`, protocol, simulator) | 181 |
| Home Assistant (`tests/ha/`, CI only) | 160 |
| **Total** | **341** |

### What the coverage push actually found

Chasing the number was worth it for the defects it surfaced, not the number:

| Finding | Why it mattered |
|---|---|
| **`send_bursty` swallowed refusals** | It drained every frame without looking at one, so a matrix answering `Command error` was counted, discarded and reported as success — the switch showed the setting it had just failed to make |
| **`send_bursty` reported success when unreachable** | Zero frames read as "the burst is over". The repair flow told the user it had enabled audio sense on a matrix that was not there |
| **The simulator's volume taper was wrong** | It interpolated from twenty points; **78 of 101 steps failed a step → dB → step round trip**. Every volume assertion in the suite had been made against a curve the hardware does not have |
| **Two fault prefixes were missing a length byte** | Those tests passed while exercising nothing — a green assertion that a poll survived a read which never failed |
| **`freezer` cannot drive a `Debouncer`** | It schedules on the event loop's clock; freezing it hangs rather than fails. Cost fifteen minutes of CI before the job gained a timeout |

### Two branches removed rather than tested

Reaching 100% by contorting a test around unreachable code would make the number a lie. Both of
these were dead, and the second was only found *because* it was the last line standing:

* **`media_player.async_turn_on`'s "no inputs enabled" guard** — `EntrySettings._active` treats an
  empty selection as "not chosen yet" and returns every channel, so `self._sources` is never empty.
  Kept but marked `# pragma: no cover`, because deleting it makes the fallback a silent disconnect
  if that rule ever changes.
* **`AmsClient._exchange`'s held-byte prepend** — deleted. `_drain_padding` sets `_held_byte` only
  on padded firmware, which permanently disables the single-NUL shortcut, which guarantees
  `_discard_stale` runs first and clears it. The prepend always saw an empty value. The *clearing*
  is correct — whatever arrived before a question was asked cannot be its answer — so the prepend
  was the wrong half to keep.

**Superseded, 2026-08-28**

| Run | Where | Result |
|---|---|---|
| Offline suite | Windows dev box | 92 passed |
| Full suite | CI (Ubuntu) | 119 passed |

The gap between the two runs is exactly `tests/ha/`, and it is the part that cannot be verified
locally at any point. CI is the gate for it.

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

- [x] **NFR-01 — a full poll completes well inside the interval.** Measured on the live system
      2026-08-29 from the coordinator's own debug output: **0.015 s** for the 12-output matrix,
      **0.005 s** for the 4-output one, against a **30 s** interval. Three orders of magnitude of
      headroom
- [x] **NFR-02 — steady-state polling adds no measurable churn.** 300 s sample on the live system
      2026-08-29: **1629 events, 5.43/s overall, and not one of them from a Triad entity.** Zero,
      while the integration was polling three matrices roughly every 30 s — some 2,400 device reads
      over the window. The `media_player` domain's 98 events are entirely one unrelated desktop PC

      **The rate comparison is deliberately not the evidence here, and reporting it as such would
      be a mistake this project has already made three times.** The backlog's own warning is that
      four samples in one evening gave 3.08, 6.50, 5.78 and 4.48/s — a >2× spread — and that short
      samples produced *confidently wrong* conclusions (HEALTH-12). 5.43/s sits inside that band,
      and this was a 300 s sample against a 480 s trustworthy baseline, so **5.43 vs 4.48 supports
      no conclusion in either direction**. What does support one is the absence: a rate can drift
      with whatever is playing in the house, but zero events from 27 entities under active polling
      cannot
- [ ] NFR-03 — one unreachable matrix does not stall the others. Stop one simulator, assert the
      other coordinators keep updating

## Bug Tracking

Defects found here go to this repository's issue tracker. Anything touching the wider A/V system
goes to the owner's private backlog under the `AV-` series.
