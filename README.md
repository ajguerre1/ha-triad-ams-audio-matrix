# Triad AMS Audio Matrix — Home Assistant integration

Local control of **Triad TS-AMS8, TS-AMS16 and TS-AMS24** audio matrix switches over TCP, with no
cloud, no polling of a vendor API, and no Control4 controller required.

> **Status: in use.** Verified against live hardware — an AMS8 on firmware `V1.05.74` and two
> AMS24s on `V1.06.84` — and driving them in a production Home Assistant instance. The test suite
> is 341 tests over 1936 statements at 100% coverage, run against a device simulator in CI.

## What it exposes

The hardware does considerably more than route audio, and this integration aims to surface all of
it rather than just the parts a media player needs.

| Platform | Per | What |
|---|---|---|
| `media_player` | output | Source select, volume, mute, on/off |
| `number` | output | Bass, treble, balance, EQ band gain ×5 |
| `number` | input | Input gain |
| `number` | matrix | Audio-sense off delay |
| `select` | output | EQ band frequency ×5, EQ band Q ×5 |
| `switch` | output | Loudness, mono sum |
| `switch` | matrix | 12 V trigger banks, ASG trigger, audio sense |
| `binary_sensor` | input | Input audio |
| `sensor` | matrix | Firmware, addressing |
| `sensor` | output | Turn-on volume, when tracking is enabled |

EQ frequency and Q are `select` rather than `number` because the device takes them as indices into
fixed tables — 31 frequencies and 8 Q values — not as continuous quantities. Offering a slider
would invent precision the hardware does not have.

Everything beyond `media_player` is **disabled by default**. A 24×24 matrix would otherwise add
several hundred entities to the recorder on first setup; enable the ones you need per zone.

### Services

| Service | Target | What |
|---|---|---|
| `triad_ams.set_eq_band` | `media_player` | Frequency, gain and Q of one band in a single write |
| `triad_ams.apply_eq_preset` | `media_player` | All five bands at once — Flat, Rock, Pop, Jazz, Classical, High Pass, Low Pass |
| `triad_ams.send_raw` | device | Send an arbitrary command; refuses anything without the `F5` query marker unless `allow_write` is set |

`set_eq_band` exists because a band is three parameters across three entities. Setting them
individually costs three round trips and leaves the filter in two intermediate shapes on the way.

### Options

Per-output **maximum volume** is a config option rather than an entity — a ceiling that a user can
raise from a dashboard is not a ceiling. Also configurable: the poll interval, and whether the
integration tracks turn-on volume the way a Control4 controller does (on by default; switching it
off replaces the read-only `sensor` with a writable `number`).

## Requirements

- Home Assistant 2026.8.0 or newer
- A Triad AMS matrix reachable on your LAN, TCP port 52000
- The matrix's model — there is no command that reports it, so you choose it during setup

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom repositories**
2. Add `https://github.com/ajguerre1/ha-triad-ams-audio-matrix`, category **Integration**
3. Install, then restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → *Triad AMS Audio Matrix*

### Manual

Copy `custom_components/triad_ams/` into your Home Assistant `config/custom_components/`
directory and restart.

## Coexisting with Control4 and other controllers

These matrices are frequently installed behind a Control4 controller, which holds a persistent
keep-alive socket to port 52000.

**That is fine.** The hardware accepts multiple concurrent TCP clients — verified with three
simultaneous connections answering correctly while a Control4 controller was connected. This
integration does not displace an existing controller, and an existing controller does not block
it.

The consequence to understand is that the matrix does **not** announce routing or volume changes.
If another controller changes a zone, this integration finds out on its next poll, not
immediately. That is why the integration is `local_polling` and why the poll interval is
configurable. Changes made *through* Home Assistant are read back straight away.

### Replacing a Control4 controller

Coexistence is the safe default, but the harder question is what you lose by switching the
controller off. Read against the driver's Lua, Control4 maintains exactly three things beyond
issuing commands: a debounce on routing, turn-on volume tracked as the volume a zone was left at,
and a state resync that overwrites the device on every reconnect. The first two are reproduced
here — a 250 ms leading-edge debounce on route changes, and optional turn-on volume tracking. The
third is deliberately not: overwriting hardware state because a socket reconnected is a behaviour
worth losing.

This has not been verified with a controller actually powered down, only with one running
alongside. If you are planning the same move, that is the gap to close yourself.

## Protocol

The control protocol is documented in **[docs/triad-ams-protocol.md](docs/triad-ams-protocol.md)** —
command framing, the full opcode table, every response string, the firmware quirks, and the
errors in the vendor documentation.

It was reconstructed from two sources: live capture against real hardware (an AMS8 on firmware
`V1.05.74` and two AMS24s on `V1.06.84`), and the Control4 driver's Lua. Where they disagree, the
capture wins — and they do disagree, in at least one place that costs an afternoon to find.

## Credits

Independent implementation, not a fork. [`bharat/homeassistant-triad-ams`](https://github.com/bharat/homeassistant-triad-ams)
was read as a reference and deserves credit for two hard-won findings that are reproduced here
with acknowledgement: that some firmware pads response frames to 150 bytes with NULs, which
desyncs a naive reader, and that the device returns empty frames intermittently on healthy
connections and must not be disconnected when it does.

## Trademarks

**Triad** and the Triad logo are trademarks of their owner. This project is independent and is
**not affiliated with, endorsed by, or supported by Triad**.

The logo appears here only to identify which product the integration works with, which is what an
integration icon is for in Home Assistant. The artwork remains the property of its owner.

## Licence

MIT, covering the code in this repository. The licence does not extend to the Triad marks; see
**Trademarks** above.
