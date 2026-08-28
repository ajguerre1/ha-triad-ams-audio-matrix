# Triad AMS Audio Matrix — Home Assistant integration

Local control of **Triad TS-AMS8, TS-AMS16 and TS-AMS24** audio matrix switches over TCP, with no
cloud, no polling of a vendor API, and no Control4 controller required.

> **Status: in development.** The device client and protocol layer are implemented and tested;
> the Home Assistant entity platforms are being built. Not yet ready to install.

## What it exposes

The hardware does considerably more than route audio, and this integration aims to surface all of
it rather than just the parts a media player needs.

| Platform | Per | What |
|---|---|---|
| `media_player` | output | Source select, volume, mute, on/off |
| `number` | output | Bass, treble, balance, max volume, turn-on volume, 5-band parametric EQ |
| `number` | input | Input gain |
| `switch` | output | Loudness, mono-sum |
| `switch` | matrix | 12 V trigger banks, ASG trigger |
| `binary_sensor` | input | Audio sense |
| `sensor` | matrix | Firmware version, connection state |

Everything beyond `media_player` is **disabled by default**. A 24×24 matrix would otherwise add
several hundred entities to the recorder on first setup; enable the ones you need per zone.

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

## Licence

MIT.
