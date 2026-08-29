---
phase: requirements
title: Requirements & Problem Understanding — Triad AMS Audio Matrix integration
description: First-party Home Assistant integration for Triad TS-AMS8/16/24 audio matrices, replacing a third-party one in place
feature: triad-ams-integration
status: accepted
---

# Requirements & Problem Understanding

## Problem Statement

**What problem are we solving?**

Triad TS-AMS8/16/24 audio matrix switches have no first-party Home Assistant support. The
available third-party integration (`bharat/homeassistant-triad-ams` v2026.7.1) exposes
`media_player` entities only, which reaches a fraction of what the hardware does.

Unreachable from Home Assistant today: per-output bass, treble, balance, loudness and mono-sum;
a 5-band parametric EQ per output; per-input gain; the 12 V trigger banks; the device's native
2.1 output grouping; and audio-sense detection.

**Who is affected.** The owner of an installation with three matrices — one TS-AMS8 and two
TS-AMS24, 56 outputs, 26 named zones in use. The grouping gap is concrete rather than
theoretical: one zone already relies on the device's 2.1 pairing (see A/V finding AV-03), and
Home Assistant cannot see or change it.

**Current workaround.** Tone, EQ and grouping are set through Control4 or the unit's own web
interface, outside Home Assistant entirely, and are therefore invisible to automation.

## Goals & Objectives

**Primary goals**

- Control routing, volume, mute and on/off per output through `media_player`
- Expose the per-output DSP the hardware provides: bass, treble, balance, max volume, turn-on
  volume, 5-band EQ, loudness, mono-sum
- Expose per-input gain and audio-sense
- Expose the 12 V trigger banks and the ASG trigger
- ~~Support the device's native output grouping~~ — **withdrawn during design review, 2026-08-28.**
  The premise was wrong: all seven groups are empty on all three matrices, and the Control4 driver
  never calls `setOutputToGroup`. Club BBQ's 2.1 is a driver-side construct the matrix has no
  record of. See the design doc's "FR-07 grouping — withdrawn, on evidence"
- Install and update through HACS, configured entirely in the UI

**Secondary goals**

- Report firmware version and connection state for diagnosis
- Provide services for direct routing and raw diagnostic commands
- Document the protocol well enough that the next person does not have to rediscover it

**Non-goals**

| Not doing | Why |
|---|---|
| Mains power control | The device's power-on delay is long enough that the Control4 driver disables the command outright. `media_player` on/off means routing |
| Discovery | The matrices speak SDDP, which Home Assistant does not |
| Model auto-detection | No command reports the model or channel count; setup asks |
| Submission to HACS default or HA core | Custom repository is sufficient for now |
| Replacing Control4 | It stays live on all three matrices and is expected to keep changing them |
| **Pushing cached state to the device on connect** | The Control4 driver does exactly this (`SyncStateToDevice` on every reconnect, writing every output). For a *second* controller it is destructive: a Home Assistant restart would overwrite whatever Control4 had just set, on all 56 outputs at once. This integration only ever reads on connect |

## User Stories & Use Cases

- As the owner, I want to route any zone to any source from a dashboard, so that audio follows
  what is happening in the house without reaching for a Control4 remote.
- As the owner, I want per-zone volume with a cap, so that an automation or a guest cannot drive
  an outdoor zone to full.
- As the owner, I want the tone and EQ of a zone exposed, so that a correction can be applied
  from Home Assistant rather than through a separate tool.
- As the owner, I want the integration to keep working while Control4 also controls the matrices,
  so that adopting it is not a migration.
- As the owner, I want to swap the existing integration for this one without rebuilding
  dashboards or automations, so that adoption costs an install and a restart.

**Key workflows**

1. Add a matrix: host, port, model → choose which outputs and inputs are wired → entities appear.
2. Change a zone's source or volume from Home Assistant; the device confirms on read-back.
3. Control4 changes a zone; Home Assistant reflects it within one poll interval.

**Edge cases**

- Another controller changes a zone between poll and command
- An output is routed to an input the user excluded from the config
- The matrix answers `Command error` or an empty frame on a healthy socket
- Firmware pads response frames to 150 bytes with NULs
- The matrix becomes unreachable mid-poll

## Success Criteria

1. All 56 outputs across the three matrices report state matching a direct socket query. Because
   the two integrations cannot coexist, this is verified **after** cutover, not before — which is
   what makes criteria 2 and 5 load-bearing rather than ceremonial.
2. After cutover, the `triad_ams` entity count is unchanged, and sampled entity IDs keep their
   areas and aliases — verified against a baseline captured immediately before.
3. A change made in Control4 appears in Home Assistant within one poll interval; a change made in
   Home Assistant is visible to Control4.
4. hassfest, HACS validation, ruff and pytest all pass in CI.
5. Rollback to the previous integration is rehearsed and works.
6. No site data — addresses, MACs, room, zone or source names — reaches the public repository.

**Non-functional criteria** *(added during requirements review)*

| ID | Criterion | Why it matters | How it is checked |
|---|---|---|---|
| NFR-01 | A full poll of a 24-output matrix completes in well under the poll interval | Commands are serialised on one socket, so a 24-output matrix costs ~72 round trips per cycle. If a poll cannot finish within its interval the coordinator overlaps itself and the queue grows without bound | Time a full refresh against the simulator and against a real AMS24 |
| NFR-02 | Steady-state polling adds no measurable state-change churn when nothing is playing | This system's measured baseline is 4.48 events/s and `media_player` is already its 4th largest contributor. A poll that rewrites identical state 26 times a minute would be a regression in a place that is already watched | Sample churn before and after with the owner's existing `tools/ha_state_churn.py`, ≥300 s, like-for-like |
| NFR-03 | An unreachable matrix degrades that matrix only | Three matrices share nothing but the LAN; one being down must not stall or fail the other two | Stop one simulator, assert the other coordinators keep updating |

## Constraints & Assumptions

**Technical constraints**

| ID | Constraint | Basis |
|---|---|---|
| C-01 | Must coexist with Control4 on the same matrix, concurrently | **Measured**: three simultaneous TCP clients answered correctly with Control4 connected |
| C-02 | Must be a drop-in replacement: same `triad_ams` domain, existing entity IDs preserved | Owner decision. **Stakes corrected 2026-08-29 by measurement** — see below |
| C-03 | No site data in the repository | The repository is public and auto-pushes on commit |
| C-04 | Frame framing must be learned per connection, not assumed | **Measured**: some firmware pads error frames to 150 bytes with NULs |
| C-05 | `Command error` and empty frames must not close the connection | **Measured**: both arrive on healthy sockets |
| C-06 | The device client must not import Home Assistant | Development is on Windows, where Home Assistant cannot be imported |
| C-07 | Entity `unique_id` must remain `{entry_id}_output_{n}` | Changing it orphans every existing entity and recreates it with a `_2` suffix |
| C-08 | **The control port is unauthenticated.** Anything on the LAN can route and set volume on port 52000 | Protocol has no auth of any kind. Security rests entirely on network segmentation, which is an existing property of this installation, not something this integration can improve. Stated so it is a known accepted risk rather than an unexamined one |
| C-09 | **Some commands are answered by a burst of frames, not one.** Enabling audio sense returns ~one frame per input | **Measured 2026-08-29.** A client assuming one response per command desyncs for as many exchanges as the burst is long, and every frame parses cleanly, so nothing raises. Any such command must be followed by a drain, and response indices must be verified |

**Rollout constraint**

- **The two integrations cannot run side by side.** They share the `triad_ams` domain, so
  validating the new one against the live system before cutover is impossible. Cutover is
  therefore direct, with a `.storage` backup, a pre-captured entity baseline, and a rehearsed
  rollback. *(Owner decision, 2026-08-28; a throwaway HA instance was offered and declined.)*

**Named assumptions**

| ID | Assumption | Status |
|---|---|---|
| A-01 | Audio sense is **polled**, not pushed | **Mostly confirmed 2026-08-29.** A passive socket received nothing across 40 s with sense enabled and music playing. Polling is correct. One case remains unobserved — a signal *transition* — because audio never started or stopped during the window |
| A-02 | In `AudioSense:Input[n]: v`, only `v == 1` means detected | **Confirmed, and the reason is now known.** `1` = signal, `0` = none, `2` = **audio sense is disabled**. The code was already right; it was right for a reason nobody had established |
| A-03 | The two AMS24s' DHCP assignment resembles the AMS8's | Open. Low impact — neither has moved |

### What the 2026-08-29 capture changed

Both audio-sense assumptions were checked against live hardware with music playing through the
Office chain (`wiim → dsp-06 → Matrix 02 input 7 → output 7`). Audio sense was enabled on that
matrix for 40 s and restored, verified on two independent fresh connections.

**Audio sense is switched off on all three matrices**, and `2` is what that looks like. An input
carrying music reads `2`, identically to a dead one. This is the finding that matters for FR-06:
as the installation currently stands, a `binary_sensor` per input would report "not detected" for
all 56 inputs, permanently. The entity could never be true.

**A new constraint fell out of it (C-09).** Enabling audio sense is answered by roughly one frame
*per input* — ~24 on an AMS24 — not by a single response. The measuring probe read those as
answers to later queries and stayed desynchronised for ~19 exchanges. Every frame parsed cleanly;
only the index revealed the slip. Any command that can return a burst must be followed by a drain,
and every response's index must be checked. The client already does the latter — this is the first
evidence that the guard earns its place against real hardware rather than only against the
simulator.

**C-02's stakes were overstated, and the correction is worth recording**

This document originally justified the drop-in requirement as protecting entities "referenced by
live dashboards and automations". A pre-cutover audit of the installation found otherwise:

| Measured 2026-08-29 | |
|---|---|
| Entities across three config entries | **27** (12 + 11 + 4 — a subset of 56 outputs, not all) |
| With an area assigned | **0** |
| With aliases | **0** |
| Renamed by the user | **0** |
| References in YAML config | **0** |
| References anywhere in `.storage` outside the entity registry itself | **0** |
| `unique_id` already matching `{entry_id}_output_{n}` | **27 / 27** |

**Nothing consumes these entities.** No dashboard card, no automation, no script. If the cutover
renamed every one of them, nothing downstream would break.

The requirement stands, because reproducing the scheme costs nothing and 27/27 already match. But
it is *insurance*, not a load-bearing constraint, and the difference matters: it means the cutover
is low-risk rather than delicate, and it means the `unique_id` test earns its place by protecting
against a future in which dashboards *do* reference these entities, not against a present crisis.

Recording it rather than quietly softening the language, because the original claim shaped how
several decisions were argued.

**Accepted behaviour change at cutover**

Club BBQ's 2.1 pairing is implemented inside the Control4 driver, not in the matrix. After
cutover, setting that zone's volume from Home Assistant moves output 1 only; output 2 stays where
it is. Under Control4 both move together. Accepted rather than mirrored, because AV-03 plans to
undo the pairing and rewire the zone as true stereo. *(Owner decision, 2026-08-28.)*

**Assumptions already retired by measurement**

- ~~The matrix may allow only one TCP client~~ — three concurrent clients verified.
- ~~The Control4 driver's command constants are authoritative~~ — its `getOutputMutePrefix` is
  wrong and returns `Command error`; the driver's own diagnostics path has the working form.
- ~~The AMS8 and AMS24 need separate protocol handling~~ — their driver Lua is byte-identical.

**Scope constraint on entities**

Only `media_player` is enabled by default. Every other entity is created but disabled, so the
owner enables per zone what they want. A fully-populated 24×24 matrix is roughly 500 entities,
against a measured system-wide churn baseline of 4.48 state-change events/s. *(Owner decision,
2026-08-28.)*

## Questions & Open Items

**Resolved during this phase**

| Question | Answer |
|---|---|
| Validate on a throwaway instance, or cut over directly? | Cut over directly; verify on the live system with backup and rehearsed rollback |
| Defer audio-sense, or build on an assumption? | Build on named assumptions A-01/A-02; revisit at that task |
| Which entities enabled by default? | `media_player` only |
| Public or private repository? | Public, with an enumerating privacy guard |
| Reuse the `triad_ams` domain? | Yes — drop-in replacement, entity IDs preserved |

**Still open**

| # | Item | Needs |
|---|---|---|
| 1 | Does an idle socket receive unsolicited audio-sense events? (A-01) | Hardware with a zone playing |
| 2 | What does `AudioSense:Input[n]: 2` mean? (A-02) | Same |

Neither blocks this phase. Both are recorded as named assumptions above and carried into the
planning doc as an explicit task.
