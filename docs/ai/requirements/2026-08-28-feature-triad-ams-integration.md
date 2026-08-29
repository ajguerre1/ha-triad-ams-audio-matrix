---
phase: requirements
title: Requirements & Problem Understanding — Triad AMS Audio Matrix integration
description: First-party Home Assistant integration for Triad TS-AMS8/16/24 audio matrices — replaces a third-party integration in place, and replaces Control4 as the matrices' controller
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

### Reframed 2026-08-29 — replacement, not coexistence

This document was written on the premise that Control4 stays. **It does not.** The owner's stated
purpose for this integration is to **decommission Control4**, and the audio matrices are the
workstream that has to complete before that can happen.

That inverts the standard the integration is judged against. "Complete" no longer means *the
device's command surface* — it means **everything Control4 does that would be missed**. Those are
different lists, and the second is not a subset of the first: the matrices persist their own
state, so most of what Control4 appears to provide is really the hardware. What Control4 actually
maintains is small, and it is not made of commands.

| What Control4 maintains that the matrix does not | Consequence of removing Control4 |
|---|---|
| **2.1 pairing** (`SyncPairedOutput`) — restored on driver init, re-applied on every master volume change | The paired zone's output 2 stops tracking output 1 |
| **Turn-on volume tracking** — a C4 volume change schedules a 10 s debounced write of that volume into the device's turn-on register | Zones stop resuming at the volume they were left at |
| **250 ms routing debounce** | Rapid source changes reach the matrix one-for-one |

Two driver entry points, and they do different things — worth separating, because conflating them
understates what Control4 owns:

* **`OnDriverLateInit`** (driver start) writes exactly one thing to the hardware: the pairing
  restore.
* **`SyncStateToDevice`** (on *reconnect*) pushes a great deal — audio mode for every output, which
  writes that output's EQ gains and bass/treble; routing for every output; every trigger bank; and
  `disableAudioSense`.

So Control4 does not merely read the matrix's persisted state — **on every reconnect it overwrites
it** from its own cache. That is the mechanism behind C-01's coexistence hazard and behind the
audio-sense revert that blocked FR-14: the setting is re-asserted by the driver, not by the device.

Once Control4 is gone, the matrix's own persisted state becomes authoritative and nothing
overrides it. Volume, routing, EQ, tone, triggers and input gain all live in the hardware and
survive decommissioning untouched.

**Scope boundary.** Control4 runs more than audio in this installation. This feature covers the
audio matrices only; the wider decommissioning is tracked separately. *(Owner decision,
2026-08-29.)*

## Goals & Objectives

**Primary goals**

*The FR series is defined here and nowhere else* — the design doc's coverage table maps these to
components, it does not define them. Numbering FR-01…FR-11 matches the identifiers that table has
used since 2026-08-28, so nothing already written needs renumbering. *(Consolidated 2026-08-29,
after a new requirement was numbered FR-08 and collided with an existing one.)*

| ID | Goal |
|---|---|
| FR-01 | Routing, volume, mute and on/off per output through `media_player` |
| FR-02 | Per-output tone — bass, treble, balance — and the 5-band parametric EQ |
| FR-03 | Per-input gain |
| FR-04 | Per-output loudness and mono-sum |
| FR-05 | The 12 V trigger banks and the ASG trigger |
| FR-06 | Audio-sense per input |
| FR-07 | ~~The device's native output grouping~~ — **withdrawn** |
| FR-08 | Firmware version and connection state, for diagnosis |
| FR-09 | Configured entirely in the UI |
| FR-10 | Install and update through HACS |
| FR-11 | Services for direct routing and raw diagnostic commands |

**FR-07 was withdrawn during design review, 2026-08-28; re-examined and the withdrawal upheld
2026-08-29.** The premise was wrong: all seven groups are empty on all three matrices, and the
Control4 driver never calls `setOutputToGroup`. The one 2.1 zone is a driver-side construct the
matrix has no record of. See the design doc's "FR-07 grouping — withdrawn, on evidence", and "The
2.1 pairing after decommissioning" below.

**Replacement goals** *(added 2026-08-29, when the framing changed)*

| ID | Goal | Why it exists |
|---|---|---|
| FR-12 | Turn-on volume tracking — write the current volume into the device's turn-on register, debounced, behind a setting defaulting to **on** | Replaces the C4 behaviour that makes zones resume where they were left. Off by default would silently change how the house behaves on the day C4 is removed |
| FR-13 | Debounce routing commands by 250 ms | Replaces C4's coalescing so rapid source changes do not reach the matrix one-for-one |
| FR-14 | Audio-sense enable and off-delay setters | Only correct with a single writer. Under coexistence C4 re-asserted its own value, so an HA control would appear to work and silently revert |
| FR-15 | Max volume enforced by the device, not only by Home Assistant | The device has a setter and **no getter**. With one writer, HA's stored value is authoritative — the missing query was the obstacle, not the missing setter. *(Amended 2026-08-29 during design review: originally "as an entity". The value already lives in the `output_max_volumes` option, and a second home would be two sources of truth. It stays one setting, enforced in two places — the slider scale and the device's own register. See design D-14.)* |
| FR-16 | EQ presets — the 7 generic curves plus user-defined slots | Flat is a genuine reset; user-defined slots carry whatever AV-19's audit produces |
| FR-17 | `getIpAddress` as a diagnostic sensor | A read, useful for confirming a unit has not moved; closes A-03 |

**Secondary goals** *(not numbered — these are qualities, not capabilities)*

- Document the protocol well enough that the next person does not have to rediscover it

**Non-goals**

| Not doing | Why |
|---|---|
| Mains power control | The device's power-on delay is long enough that the Control4 driver disables the command outright. `media_player` on/off means routing |
| Discovery | The matrices speak SDDP, which Home Assistant does not |
| Model auto-detection | No command reports the model or channel count; setup asks |
| Submission to HACS default or HA core | Custom repository is sufficient for now |
| ~~Replacing Control4~~ | **Reversed 2026-08-29.** Replacing Control4 as the matrices' controller is now the *purpose*. See "Reframed" above |
| **Pushing cached state to the device on connect** | The Control4 driver does exactly this (`SyncStateToDevice` on every reconnect, writing audio mode, routing, every trigger bank and the audio-sense setting). **The original reason no longer applies** — with a single writer it is no longer destructive to a second controller. It stays a non-goal on stronger grounds: the matrix persists its own state, so a push on connect can only overwrite the truth with a stale cache. This integration only ever reads on connect |
| **Mirroring the 2.1 pairing** | Would mean shipping faithful support for a configuration AV-03 records as wrong — a synthesised sub channel feeding a full-range outdoor satellite, in a zone already flagged at 3 Ω against an 8 Ω minimum (AV-13). Handled with an HA-side link instead; see below |
| **Audio mode (Bypass / Tone / EQ)** | Not a device feature. Control4 implements it by zeroing the inactive layer and holding the real values itself, which would mean Home Assistant keeping shadow copies of numbers the device also stores — two writers for one value. Home Assistant already has purpose-built storage for capture-and-restore: scenes. Documented as a recipe instead. *(Owner decision, 2026-08-29.)* |
| **The 76 Triad speaker EQ presets** | Two independent reasons. They are tunings for Triad's own speakers, and this installation has **none** — every `Triad` reference in the A/V inventory is a matrix, and every speaker is another manufacturer's. And `ariel_presets.lua` is marked "Copyright 2022, Wirepath Home Systems, LLC. All rights reserved.", so republishing 76 curated tunings in a public MIT repository is redistributing their work product rather than documenting a protocol for interoperability. The 7 generic curves ship (FR-16); these do not |
| **Network configuration, factory reset, network standby, firmware update** | Each can leave a matrix unreachable or unusable, and decommissioning makes none of them safer. Firmware decides it: the driver's bundled image is `v1.05.74` — exactly what the AMS8 runs, and *older* than the AMS24s' `v1.06.84` — so there is nothing to apply. Skipping these removes a button, not a capability: `send_raw` with `allow_write` remains for anyone who knows precisely what they are sending. *(Owner decision, 2026-08-29.)* |

## User Stories & Use Cases

- As the owner, I want to route any zone to any source from a dashboard, so that audio follows
  what is happening in the house without reaching for a Control4 remote.
- As the owner, I want per-zone volume with a cap, so that an automation or a guest cannot drive
  an outdoor zone to full.
- As the owner, I want the tone and EQ of a zone exposed, so that a correction can be applied
  from Home Assistant rather than through a separate tool.
- As the owner, I want the integration to keep working while Control4 also controls the matrices,
  so that adopting it is not a migration. *(**Transitional**, 2026-08-29 — this holds only until
  Control4 is decommissioned. It is a migration-window requirement, not a permanent one.)*
- As the owner, I want to swap the existing integration for this one without rebuilding
  dashboards or automations, so that adoption costs an install and a restart.
- As the owner, I want everything Control4 does for the matrices to be reachable from Home
  Assistant, so that switching Control4 off costs me no capability I use today.

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
3. ~~A change made in Control4 appears in Home Assistant within one poll interval; a change made in
   Home Assistant is visible to Control4.~~ — **transitional, 2026-08-29.** Verified once during the
   migration window, then retired: after decommissioning there is no second controller to reconcile
   with. Replaced by criteria 7-8 below.
4. hassfest, HACS validation, ruff and pytest all pass in CI.
5. ~~Rollback to the previous integration is rehearsed and works.~~ — **withdrawn 2026-08-29,
   after the cutover verified clean.** The owner will not return to the third-party integration, so
   rehearsing the path has no value. Recorded as withdrawn rather than left open: an unmet
   criterion and an abandoned one look identical in a checklist, and only one of them is a problem.

   **The consequence is worth stating.** Several cutover decisions were argued as safe *because*
   rollback existed. Recovery now means forward-fix — diagnose, patch, push, reinstall — so a green
   CI run is the last gate before a live A/V system rather than the first of two. What survives is
   the config-entry restore point, which guards against entry *loss*; that is a different failure
   from wanting the old integration back, and it would orphan all 27 zone entities.
6. No site data — addresses, MACs, room, zone or source names — reaches the public repository.
7. **Every capability Control4 provides for the matrices is reachable from Home Assistant, or is
   recorded as a deliberate non-goal with a reason.** This is the decommissioning bar: the gap
   analysis of 2026-08-29 enumerated the driver's full command and behaviour surface, and each item
   is either a goal, a replacement goal (FR-12…FR-17), or a row in the non-goals table.
8. **With Control4 stopped, no zone changes behaviour except the two accepted below** — the 2.1
   zone's output 2 volume tracking, and turn-on volume if FR-12's setting is turned off. Verified by
   stopping the Control4 driver and exercising every zone from Home Assistant before the hardware
   is decommissioned, so the check is reversible.

**Non-functional criteria** *(added during requirements review)*

| ID | Criterion | Why it matters | How it is checked |
|---|---|---|---|
| NFR-01 | A full poll of a 24-output matrix completes in well under the poll interval | Commands are serialised on one socket, so a 24-output matrix costs ~72 round trips per cycle. If a poll cannot finish within its interval the coordinator overlaps itself and the queue grows without bound | Time a full refresh against the simulator and against a real AMS24 |
| NFR-02 | Steady-state polling adds no measurable state-change churn when nothing is playing | This system's measured baseline is 4.48 events/s and `media_player` is already its 4th largest contributor. A poll that rewrites identical state 26 times a minute would be a regression in a place that is already watched | Sample churn before and after with the owner's existing `tools/ha_state_churn.py`, ≥300 s, like-for-like |
| NFR-03 | An unreachable matrix degrades that matrix only | Three matrices share nothing but the LAN; one being down must not stall or fail the other two | Stop one simulator, assert the other coordinators keep updating |

**The poll interval was paying for coexistence** *(2026-08-29)*. The 30 s default exists because
Control4 mutates state behind Home Assistant's back, so reactivity to an external writer set the
floor. With a single writer, state changes only when Home Assistant changes it, and the interval
can lengthen considerably — which is the cheapest available win against NFR-02, and it arrives by
deleting a constraint rather than by adding a mechanism. The specific default is a design-phase
decision, not a requirement; what the requirement records is that its *justification* has gone.

## Constraints & Assumptions

**Technical constraints**

| ID | Constraint | Basis |
|---|---|---|
| C-01 | Must coexist with Control4 on the same matrix, concurrently — **transitional, 2026-08-29** | **Measured**: three simultaneous TCP clients answered correctly with Control4 connected. Binding only until decommissioning; kept because the migration window still needs it, and because it is what makes the cutover reversible. Retire it, do not delete it — it is the evidence that the transition was safe |
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
  therefore direct, with a `.storage` backup and a pre-captured entity baseline. *(Owner decision,
  2026-08-28; a throwaway HA instance was offered and declined.)* ~~and a rehearsed rollback~~ —
  the rollback half was dropped 2026-08-29 once the cutover verified clean; see criterion 5.

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

The one 2.1 zone's pairing is implemented inside the Control4 driver, not in the matrix. After
cutover, setting that zone's volume from Home Assistant moves output 1 only; output 2 stays where
it is. Under Control4 both move together. Accepted rather than mirrored, because AV-03 plans to
undo the pairing and rewire the zone as true stereo. *(Owner decision, 2026-08-28.)*

### The 2.1 pairing after decommissioning

*(2026-08-29.)* The reasoning above assumed Control4 stays and the rewire lands first. Neither
holds, so this was re-examined rather than left standing.

**What decommissioning does for free.** AV-03's completion criterion includes "the pairing undone
**in the Control4 driver**". Since the pairing exists only in that driver and the matrix holds no
record of it, switching Control4 off satisfies that clause with no action.

**What it does not fix.** AV-03's real remedy — rewiring as true stereo — is entangled with AV-13
(the zone bridges at **3 Ω** against an 8 Ω minimum, and a naive three-per-side rewire would be
2 Ω, worse) and therefore folded into the AV-19 audit. It will not land inside the migration
window. So output 2 will sit as a low-passed mono satellite holding its last volume.

**Why this is still not mirrored in the integration.** Building `SyncPairedOutput` would mean
shipping faithful support for a configuration AV-03 records as a mistake: a *synthesised* sub
channel — there is no subwoofer — feeding a full-range outdoor satellite, one of six in the
zone. Encoding that into a public repository would make a local defect look like a product
feature, and it would outlive the defect.

**Remedy.** A Home Assistant script or automation links output 2's volume to output 1 with the
`subVolOffset` trim. Restores the behaviour, costs no integration code, and is deleted when AV-03
completes. Documented as a recipe. *(Owner decision, 2026-08-29.)*

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

**Resolved during the 2026-08-29 replacement review**

| Question | Answer |
|---|---|
| Is this integration the whole Control4 decommissioning, or one workstream? | One workstream — audio matrices only |
| How soon does Control4 go? | Imminent — weeks. Build for a single writer |
| Mirror the 2.1 zone's pairing, or not? | Not in the integration; an HA-side link, deleted when AV-03 lands |
| Replicate turn-on volume tracking? | Yes, opt-in, **default on**, with the number entity read-only while tracking |
| Which EQ presets ship? | The 7 generic ones plus user-defined slots; the 76 speaker curves excluded |
| Network config, factory reset, standby, firmware? | Reads only — `getIpAddress` as a diagnostic. No risky writers |
| Replicate the Bypass / Tone / EQ audio mode? | No — scenes already do capture-and-restore without shadow state |

**Still open**

| # | Item | Needs |
|---|---|---|
| 1 | Does an idle socket receive an audio-sense frame on a signal *transition*? (A-01) | A capture spanning audio actually starting or stopping. The 2026-08-29 window had music playing throughout, so the transition was never observed |
| 2 | Can the Control4 driver be **stopped** without decommissioning the hardware? | Success criterion 8 wants the no-Control4 state exercised while rollback is still possible. If the driver cannot be stopped independently, that check has to move after the point of no return, which weakens it |
| 3 | When does AV-19 produce measured curves for the installed speakers? | Sets whether FR-16's user-defined presets have anything to hold at launch, or stay empty for now |

~~2. What does `AudioSense:Input[n]: 2` mean? (A-02)~~ — resolved 2026-08-29: `2` means audio sense
is disabled. See "What the 2026-08-29 capture changed".

None of the three blocks the design phase. Item 2 is the one worth answering early, because it
changes how criterion 8 is verified rather than merely when.
