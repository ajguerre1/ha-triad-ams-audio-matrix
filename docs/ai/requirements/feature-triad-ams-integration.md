# Requirements — Triad AMS Audio Matrix integration

**Status:** accepted · **Phase:** requirements

## Problem

Triad TS-AMS8/16/24 audio matrices have no first-party Home Assistant support. The available
third-party integration exposes `media_player` entities only, which reaches a fraction of what the
hardware does: per-output tone and 5-band parametric EQ, per-input gain, 12 V trigger banks,
native 2.1 output grouping, and audio-sense detection are all unreachable from Home Assistant.

These matrices are also typically installed behind a Control4 controller, which is not going away.
Any integration has to share the device rather than own it.

## Functional requirements

| ID | Requirement | Priority |
|---|---|---|
| FR-01 | Control routing, volume, mute and on/off per output via `media_player` | H |
| FR-02 | Expose per-output bass, treble, balance, max volume, turn-on volume and 5-band EQ | M |
| FR-03 | Expose per-input gain | M |
| FR-04 | Expose loudness and mono-sum per output | M |
| FR-05 | Expose the 12 V trigger banks and the ASG trigger | M |
| FR-06 | Expose audio-sense per input | M |
| FR-07 | Support the device's native output grouping (2.1 pairing) | M |
| FR-08 | Report firmware version and connection state | L |
| FR-09 | Configure entirely through the UI — no YAML | H |
| FR-10 | Install and update through HACS | H |
| FR-11 | Offer services for direct routing and raw diagnostic commands | L |

## Constraints

| ID | Constraint | Source |
|---|---|---|
| C-01 | **Must coexist with Control4** on the same matrix, concurrently | Measured: three simultaneous TCP clients answer correctly |
| C-02 | **Must be a drop-in replacement** for `bharat/homeassistant-triad-ams` — same `triad_ams` domain, existing entity IDs preserved | Owner decision; 26 zones are referenced by live dashboards and automations |
| C-03 | **No site data in the repository.** No addresses, MACs, room, zone or source names | Repository is public and auto-pushes |
| C-04 | Frame framing must be learned per connection, not assumed | Measured: some firmware pads error frames to 150 bytes with NULs |
| C-05 | `Command error` and empty frames must not close the connection | Measured; they arrive on healthy sockets |
| C-06 | The device client must not import Home Assistant | Development is on Windows, where HA cannot be imported |

## Non-goals

- **Mains power control.** The device's power-off is never sent; its power-on delay is long
  enough that the Control4 driver disables the command outright. `media_player` on/off means
  routing.
- **Discovery.** These matrices support SDDP, which Home Assistant does not speak. Setup is manual.
- **Model auto-detection.** No command reports the model or channel count; setup asks.
- **Submission to HACS default or Home Assistant core.** Custom repository only, for now.

## Success criteria

1. All 56 outputs across three matrices read back state matching a direct socket query.
2. After the cutover, entity count is unchanged and `media_player.*_output_7` and its siblings keep
   their entity IDs, areas and aliases.
3. A change made from Control4 appears in Home Assistant within one poll; a change made from Home
   Assistant is visible to Control4.
4. hassfest, HACS validation, ruff and pytest all pass in CI.
5. Rollback to the previous integration is rehearsed and works.

## Open questions

1. Does an idle socket receive unsolicited audio-sense events? Decides push vs poll for FR-06.
2. What does `AudioSense:Input[n]: 2` mean? Undocumented; observed on live hardware.
