# Triad AMS control protocol

Reference for the TCP control protocol spoken by the Triad TS-AMS8 and TS-AMS24 audio
matrix switches (Hansong "Ariel" platform, also sold under other labels).

Two sources, in order of authority:

1. **Live capture** against real hardware — an AMS8 on firmware `V1.05.74` and two AMS24s on
   `V1.06.84`. Every response string quoted below was observed, not inferred.
2. **The vendor driver** `triad_ams8 archive` / `triad_ams24 archive` (the vendor 3.4.3), specifically
   `ariel_protocol.lua` for the command table and `driver.lua` for response handling and the dB
   volume curve. The two archives contain **byte-identical Lua**; the models differ only in
   `audio_provider_count` / `audio_consumer_count` in `driver.xml`. One implementation covers all.

Where the two disagree, the capture wins. It disagrees in at least one place — see
[Known documentation errors](#known-documentation-errors).

---

## Transport

| Property | Value |
|---|---|
| Protocol | TCP |
| Port | 52000 |
| Request | Binary |
| Response | **ASCII text, terminated by `0x00`** |
| Concurrent clients | **Supported** — three simultaneous sockets were verified answering correctly |

Concurrency matters: these matrices are commonly driven by another controller holding a
persistent keep-alive socket. An additional client does not displace it, so Home Assistant can
coexist with an existing control system. This was verified with the vendor connected throughout.

### Request framing

```
FF 55 <len> <cmd…>
```

`<len>` counts the bytes that follow it. A **query** inserts `F5` immediately before the index
byte, and increments `<len>` accordingly.

```
FF 55 04 03 1E 05        set output 6 volume to 0x05
FF 55 04 03 1E F5 05     query output 6 volume
   ^        ^  ^  ^
   |        |  |  +-- index, 0-based
   |        |  +----- query marker
   |        +-------- opcode
   +----------------- length
```

### Index bases — the trap

Output and input index **bytes are 0-based on the wire**, but responses print them **1-based**:
sending `00` yields `Get Out[1] …`.

The one exception is audio sense, which prints 0-based: `AudioSense:Input[0]: …` refers to input 1.
Mixing these up produces an off-by-one that only shows on one entity type, which is the hardest
kind to notice.

### Response framing — learn it, never assume it

Most responses end with a single `0x00`. **Error responses on some firmware are padded to a fixed
150-byte frame with trailing NULs.** Confirmed on the AMS8: an out-of-range output query returned
`Command error` followed by 136 NUL bytes.

A naive `readuntil(b"\x00")` consumes only the first NUL and leaves 136 bytes buffered, which are
then read as the *next* command's response — desyncing every subsequent exchange on that
connection. The framing must be detected per connection and per response, never assumed from the
model or firmware string.

---

## Commands

Index bytes below are written `<out>` / `<in>` and are **0-based**.

### Routing

| Operation | Bytes | Response |
|---|---|---|
| Route output to input | `FF 55 04 03 1D <out> <in>` | echo |
| Disconnect output | `FF 55 04 03 1D <out> <input_count>` | echo |
| Query source | `FF 55 04 03 1D F5 <out>` | `Get Out[7] Input Source : input 7` |
| Query source, unrouted | " | `Get Out[1] Input Source : Audio Off` |

Disconnect works by routing to an input index one past the last valid one — `0x08` on an AMS8,
`0x18` on an AMS24. There is no dedicated disconnect opcode.

Note the response says `input 7` in lowercase and is **1-based**.

### Volume

| Operation | Bytes | Response |
|---|---|---|
| Set volume | `FF 55 04 03 1E <out> <step>` | echo |
| Query volume | `FF 55 04 03 1E F5 <out>` | `Get Out[1] Volume : -39.7` |
| Step up / down | `FF 55 03 03 13 <out>` / `FF 55 03 03 14 <out>` | echo |
| Step up / down ×3 | `FF 55 03 03 15 <out>` / `FF 55 03 03 16 <out>` | echo |
| Set maximum volume | `FF 55 04 03 1F <out> <step>` | echo |
| Set turn-on volume | `FF 55 04 03 33 <out> <step>` | echo |
| Query turn-on volume | `FF 55 04 03 33 F5 <out>` | `Get Out[1] Turn on Vol : -39.7` |

**Volume is asymmetric: set in steps, read in decibels.** `<step>` is `0x00`–`0x64` (0–100), where
`0x00` is off. The query returns a dB figure with one decimal.

Converting back therefore needs the device's dB curve, which is non-linear and lives in
`driver.lua` as `g_dbVolMap`. Observed values include `-108.5` (off), `-39.7`, and `0` (full).

**The lookup must be nearest-match, not exact.** `-108.5` was observed on real hardware and is
*not* a key in the driver's table, whose lowest entry is `-108`. An exact-key lookup fails on the
first zone that happens to be off.

### Mute

| Operation | Bytes | Response |
|---|---|---|
| Mute on | `FF 55 03 03 17 <out>` | echo |
| Mute off | `FF 55 03 03 18 <out>` | echo |
| Mute toggle | `FF 55 03 03 19 <out>` | echo |
| Query mute | `FF 55 04 03 17 F5 <out>` | `Get Out[1] Mute status : Unmute` |

### Per-output tone and DSP

| Operation | Bytes | Response |
|---|---|---|
| Bass | `FF 55 04 03 2F <out> <v>` · query `…2F F5 <out>` | `Get Out[1] Bass : 0` |
| Treble | `FF 55 04 03 30 <out> <v>` · query `…30 F5 <out>` | `Get Out[1] Treble : 0` |
| Balance | `FF 55 04 03 31 <out> <v>` · query `…31 F5 <out>` | `Get Out[1] Balance : Bal Center` |
| Loudness on / off | `FF 55 03 03 1A <out>` / `FF 55 03 03 1B <out>` | echo |
| Loudness query | `FF 55 04 03 1A F5 <out>` | `Get Out[1] Loudness status : Off` |
| Stereo / mono | `FF 55 03 03 10 <out>` / `FF 55 03 03 11 <out>` | echo |
| Stereo-mono query | `FF 55 04 03 10 F5 <out>` | `Get Out[1] Stereo Mono status : mono` |

Bass, treble and balance encode −12…+12 dB in half-steps as `0x00`–`0x30`, centre `0x18`:
`value = (12 + dB) * 2`. Balance reads back as **text** (`Bal Center`), not a number.

### Parametric EQ — 5 bands per output

Opcodes are a base plus the 0-based band index:

| Parameter | Base opcode | Band 1…5 |
|---|---|---|
| Frequency | `0x20` | `0x20`–`0x24` |
| Gain | `0x25` | `0x25`–`0x29` |
| Q | `0x2A` | `0x2A`–`0x2E` |

```
FF 55 04 03 <op> <out> <v>        set
FF 55 04 03 <op> F5 <out>         query
```

Responses report **human units**, not the raw index: `Get Out[1] Band 1 Freq : 63 Hz`,
`Band 1 Gain : 0`, `Band 1 Q : 0.7`. Gain uses the same `(12 + dB) * 2` encoding as bass/treble.

#### Frequency — index 0–30, ISO 1/3-octave

The endpoints are documented in the vendor driver: `0x00` is 20 Hz, `0x1E` (30) is 20 kHz. 31
steps across that span is exactly the ISO 1/3-octave series, and every frequency observed on real
hardware is a member of it:

```
20  25  31.5  40  50  63  80  100  125  160  200  250  315  400  500  630  800
1k  1.25k  1.6k  2k  2.5k  3.15k  4k  5k  6.3k  8k  10k  12.5k  16k  20k
```

Factory band defaults are 63 Hz, 250 Hz, 1 kHz, 4 kHz, 20 kHz — indices 5, 11, 17, 23 and 30.

**Read the unit, not just the number.** Everything above 1 kHz reports with a `kHz` suffix, so a
regex that takes the digits and ignores the unit turns `1.6 kHz` into `1.6 Hz`.

#### Q — index 0–7, eight discrete values, measured

| Index | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7 |
|---|---|---|---|---|---|---|---|---|
| Q | 0.5 | 0.6 | 0.7 | 0.8 | 0.9 | 1 | 2 | 3 |

Measured 2026-08-29 by sweeping the index on an output that was unrouted, at minimum volume and
muted, then restoring it and verifying on a fresh connection. Factory default is index 2 (Q 0.7)
on every band.

**Anything above index 7 clamps to Q 3** rather than erroring, so an out-of-range write appears to
succeed while doing something else. A client must constrain to 0–7 rather than rely on the device
to reject.

### Inputs

| Operation | Bytes | Response |
|---|---|---|
| Set input gain | `FF 55 04 02 04 <in> <v>` | echo |
| Query input gain | `FF 55 04 02 04 F5 <in>` | `Get In[1] input gain : 0` |
| Query audio sense | `FF 55 04 0A A0 F5 <in>` | `AudioSense:Input[0]: 2` |
| Audio-sense enable query | `FF 55 04 0A A2 F5 <in>` | `Get AutoSenseEnable : Disable` |
| Audio-sense enable set | `FF 55 04 0A A2 <0\|1> FF` | echo |
| Audio-sense off delay | `FF 55 04 0A A3 00 <delay>` | echo |
| Off-delay query | `FF 55 04 0A A3 F5 00` | `Get Analog nosignal sleep timeout : 0x1` |

Input gain is sent doubled (`value * 2`) per the vendor driver.

#### Audio sense values — measured 2026-08-29

| Value | Meaning |
|---|---|
| `2` | **Audio sense is disabled on this matrix.** Not a signal state |
| `1` | Signal present |
| `0` | No signal |

`2` is the value the vendor driver never documents, because it only ever tests for `1`. With the
feature disabled, every input answers `2` whether or not audio is flowing — confirmed against an
input carrying live music, which read `2` over eight samples, identical to a dead input.

With sense enabled the values become a clean `0`/`1`, and `2` disappears entirely: a later sweep of
all 56 inputs across three matrices with sense on returned only `0` and `1`. That is the strongest
form of the evidence — the value is not a rare signal state, it is the absence of measurement.

*(The measured units originally shipped with sense disabled on all three matrices. It was
enabled in the vendor driver on 2026-08-29, which is the only place the setting survives a
sync.)*

#### Enabling audio sense returns a burst, not one frame

```
FF 55 04 0A A2 01 FF     enable   -> Set AutoSenseEnable : Enable
FF 55 04 0A A2 00 FF     disable
FF 55 04 0A A2 F5 00     query    -> Get AutoSenseEnable : Disable
```

**The enable command is answered by roughly one `AudioSense:Input[n]` frame per input**, not by a
single response. On a 24-input matrix that is ~24 frames arriving where a client expects one.

This is a real hazard, and it was hit while measuring: a probe that assumes one frame per command
read those frames as answers to its *subsequent* queries and stayed desynchronised for ~19
exchanges — asking about input 7 and being told about inputs 1, 5, 8, 11 and 14 in turn — before
catching up. Every frame parsed cleanly. Only the index in each response revealed the slip.

A client that sends this command must drain to a quiet socket afterwards, or verify the index in
every response, or both.

#### The off delay is in MINUTES, not seconds

```
FF 55 04 0A A3 F5 00     ->  Get Analog nosignal sleep timeout : 0x1
FF 55 04 0A A3 00 <n>        set
```

The response names it for what it is: an *analog no-signal sleep timeout*. It reads back as a raw
hex value, and **the unit is minutes** — hardware reporting `0x1` corresponds to the 1-minute
default the maintainer confirms, not to one second.

That matters for anyone reading the vendor driver, which initialises
`g_arielData.audioSenseOffDelay = 30`. On this scale that is **30 minutes**, not 30 seconds. The
value is only pushed when an installer sets it, so a driver-side default of 30 does not
necessarily reach the device — but it is not the half-minute the number suggests.

#### It is a burst, not a broadcast

A second socket, connected throughout and issuing nothing, received **zero** frames during 40 s
with sense enabled and music playing. The burst goes to the socket that sent the command.

**Still unmeasured:** whether a *transition* — audio starting or stopping while sense is enabled —
pushes an unsolicited frame. Music played continuously throughout the window, so no transition
occurred. The vendor driver's receive handler is written as though it does, which is suggestive
but not evidence.

### Output groups

Groups are lettered **A–G** (seven groups) and back the product's 2.1 zone-pairing feature.

| Operation | Bytes | Response |
|---|---|---|
| Assign output to group | `FF 55 04 03 32 <out> <group 0-7>` | echo |
| Query group volume | `FF 55 04 04 47 F5 <grp>` | `Group[A] is empty` |
| Query group mute | `FF 55 04 04 44 F5 <grp>` | `Group[A] is empty` |
| Query group source | `FF 55 04 04 48 F5 <grp>` | `Group[A] is empty` |

### 12 V triggers

| Bank | On | Off | Query |
|---|---|---|---|
| Outputs 1–8 | `FF 55 03 05 50 00` | `FF 55 03 05 51 00` | `FF 55 04 05 50 F5 00` |
| Outputs 9–16 | `…50 01` | `…51 01` | `…50 F5 01` |
| Outputs 17–24 | `…50 02` | `…51 02` | `…50 F5 02` |
| ASG (24×24) | `…50 03` | `…51 03` | `…50 F5 03` |
| ASG (8×8) | `…50 01` | `…51 01` | `…50 F5 01` |

Responses: `Get Zone 1-8 trigger status : Off`, `Get ASG trigger status : Off`.

**The ASG opcode collides with the 9–16 bank opcode on the 8×8**, because an 8×8 has no bank 2.
The model must be known to address ASG correctly.

### System

| Operation | Bytes | Response |
|---|---|---|
| Power on / off / toggle | `FF 55 03 01 01 00` / `…02 00` / `…03 00` | echo |
| Power query | `FF 55 03 01 01 F5` | `Get Power status : Working` |
| Firmware version | `FF 55 03 06 65 00` | `Fw version : V1.05.74` |
| Bootloader version | `FF 55 03 09 91 FF` | `BL version : V1.08` |
| MAC address | `FF 55 03 08 80 F5` | `Get MAC Add <mac>` |
| IP assignment query | `FF 55 03 08 81 F5` | `dynamic_ip` |
| Network standby query | `FF 55 03 08 83 F5` | `Get AutoNetworkStandby : Disable` |
| Network standby on / off | `FF 55 03 08 83 01` / `…83 00` | echo |

The vendor driver deliberately **never sends power-off**, commenting that the power-on delay is
too long to handle. This integration follows that precedent: `media_player` on/off controls
routing, not device mains power.

There is **no command that reports the model or channel count.** The model must be supplied during
configuration. Querying an out-of-range output returns `Command error`, which is a usable probe but
a poor one — it is indistinguishable from other command errors.

---

## Error responses

| Response | Meaning |
|---|---|
| `Command error` | Malformed command, unknown opcode, or index out of range |
| *(empty)* | Observed intermittently on healthy connections, notably for mute queries |

Both are **application-layer** failures. They do not indicate a broken socket and must not trigger
a reconnect — doing so produces a reconnect loop under load. Retry the command instead.

---

## Known documentation errors

**The vendor driver's mute-query constant is wrong.** `ariel_protocol.lua` declares:

```lua
ariel_commands.getOutputMutePrefix = "FF55030317F5"
```

Sending that returns `Command error`. The working query has length byte `04`, not `03`:

```
FF 55 04 03 17 F5 <out>
```

which is the form the same driver uses in its own diagnostics routine
(`ariel_protocol.sendGetDiagnosticCommands`). Where the constants block and the diagnostics list
disagree, **the diagnostics list is correct** — it is the path that was actually exercised.

---

## Open questions

1. **Does a signal transition push an unsolicited frame?** Enabling audio sense produces a burst on
   the commanding socket, and a passive socket saw nothing across 40 s of steady music — but no
   audio started or stopped in that window, so a transition has never been observed. Answering it
   needs sense enabled *and* someone starting or stopping playback.

*Resolved 2026-08-29 and documented above: what `AudioSense: 2` means, and that the enable command
is answered by a burst rather than a single frame.*
