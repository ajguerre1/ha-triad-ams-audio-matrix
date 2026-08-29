# Triad AMS Audio Matrix — Home Assistant integration

Control your Triad audio matrix from Home Assistant. Choose what plays in each room, set the
volume, mute a zone, and adjust the sound — all from your dashboards and automations.

Works entirely on your own network. No cloud account, no internet connection, and no separate
controller required.

**Supported models:** TS-AMS8 and TS-AMS24.

## What you get

Each audio zone appears in Home Assistant as a media player, so it works with the standard cards,
voice assistants and automations you already use.

| In Home Assistant | For each | What you can do |
|---|---|---|
| Media player | Zone | Pick the source, change volume, mute, turn the zone on or off |
| Number | Zone | Bass, treble, balance, and five bands of equaliser |
| Number | Source | Input level, so sources match each other in loudness |
| Select | Zone | Equaliser frequency and width |
| Switch | Zone | Loudness, mono |
| Switch | Matrix | 12 V amplifier triggers, audio detection |
| Sensor | Zone / matrix | Start-up volume, firmware version, network setting |

Only the media players are switched on to begin with. A 24-zone matrix could add several hundred
items to Home Assistant, and most homes do not want that. Everything else is available but hidden
until you turn it on — see [Turning on the extra controls](#turning-on-the-extra-controls).

### Handy extras

Three actions are available to automations and scripts:

| Action | What it does |
|---|---|
| Set equaliser band | Change one band's frequency, level and width together |
| Apply equaliser preset | Flat, Rock, Pop, Jazz, Classical, High Pass or Low Pass in one step |
| Send command | For advanced troubleshooting. Read-only unless you explicitly allow changes |

## What each model has

Sources connect differently depending on the model, and the setup screen labels each one so you
know which socket you are enabling.

| | Sources | Zones |
|---|---|---|
| **TS-AMS8** | 1–4 analog · **5–8 analog *or* digital** | 1–8 |
| **TS-AMS24** | 1–16 analog · 17–24 digital | 1–24 |

On a TS-AMS8, sources 5–8 are printed twice on the back of the unit — once under the analog
sockets and once under the digital ones. They are the same four sources. Each one takes analog
**or** digital, not both, so the unit has eight sources in total and not twelve.

## Before you start

You will need:

- Home Assistant 2026.8.0 or newer
- Your matrix connected to the same network as Home Assistant
- The matrix's IP address
- To know which model you have — the unit does not report this, so you choose it during setup

**Tip:** give the matrix a fixed IP address in your router. If its address changes, Home Assistant
will lose contact with it until you update the setting.

## Installing

### Using HACS (recommended)

1. In Home Assistant, open **HACS**
2. Select the menu (⋮) and choose **Custom repositories**
3. Paste `https://github.com/ajguerre1/ha-triad-ams-audio-matrix` and choose the **Integration**
   category
4. Find **Triad AMS Audio Matrix** in the list and select **Download**
5. Restart Home Assistant
6. Go to **Settings → Devices & Services → Add Integration** and search for
   **Triad AMS Audio Matrix**
7. Enter the matrix's IP address and choose your model
8. Tick the zones and sources you actually use, then finish

Only tick what is connected. Empty zones and unused sources clutter every dropdown in Home
Assistant from then on, and you can change the selection later at any time.

### Installing by hand

Copy the `custom_components/triad_ams` folder into your Home Assistant `config/custom_components`
folder, restart, then follow steps 6–8 above.

## Changing your settings

Go to **Settings → Devices & Services**, find **Triad AMS Audio Matrix**, and choose
**Configure**. You can change:

- **Which zones and sources are in use** — tick or untick at any time
- **Maximum volume per zone** — a ceiling a zone cannot be driven past, useful for a bedroom or a
  nursery. It applies to automations and voice commands too, not only the slider
- **How often the matrix is checked** — every 30 seconds by default
- **Remember volume per zone** — when on, a zone comes back on at the volume it was left at

### Turning on the extra controls

Tone, equaliser, input levels and triggers are installed but hidden, so they do not clutter your
system unless you want them.

1. Go to **Settings → Devices & Services → Triad AMS Audio Matrix**
2. Select your matrix, then **+ N entities not shown**
3. Pick the ones you want and select **Enable**

Turn on only the zones you plan to adjust. Each enabled zone adds regular checks against the
matrix, so enabling everything on a 24-zone unit makes the system work considerably harder for
controls you may never touch.

## Uninstalling

**Your settings are kept unless you remove the integration itself**, so you can safely reinstall
or update without setting everything up again.

To remove it completely:

1. Go to **Settings → Devices & Services**
2. Find **Triad AMS Audio Matrix**, select the menu (⋮) on the entry, and choose **Delete**
3. In **HACS**, find the integration and choose **Remove**
4. Restart Home Assistant

Deleting the entry in step 2 removes its zones from Home Assistant, along with any dashboard cards
and automations that referred to them. **The matrix itself is not changed** — whatever was playing
carries on playing, and volumes stay where they are.

To step away temporarily instead, use **Disable** rather than **Delete**. That stops Home
Assistant contacting the matrix but keeps all your settings.

## Troubleshooting

### It cannot find the matrix during setup

- Check the IP address is correct and the matrix is switched on
- Confirm Home Assistant can reach it — they must be on the same network, and some networks
  separate guest or smart-home devices from each other
- The matrix uses port 52000. If you run a firewall between them, allow it

### Zones show as unavailable

Home Assistant has lost contact with the matrix. The usual causes are the matrix being switched
off, a network problem, or its IP address having changed. Contact is restored automatically once
the matrix is reachable again — no restart needed.

### A zone comes on much louder than expected

**Each zone has its own start-up volume**, stored in the matrix, and a zone comes on at that level
rather than at whatever it was set to last. On a new or reset unit this is often maximum.

Two ways to deal with it:

- Turn on **Remember volume per zone** in the settings, so a zone comes back at the level it was
  left at
- Set a **maximum volume** for that zone in the settings, which it cannot be driven past

### A change I made elsewhere is slow to appear

If something else on your network changes the matrix — a wall keypad, another app — Home Assistant
finds out on its next check rather than straight away, because the matrix does not announce
changes. You can shorten the interval in the settings. Changes you make *through* Home Assistant
appear immediately.

### The sound is right but the volume numbers look odd

Volume is shown as a percentage, but the matrix works in decibels, and decibels are not a straight
line. 50% is much quieter than half as loud. This is normal and matches how the matrix reports
itself.

### Getting help

Open an issue at
[github.com/ajguerre1/ha-triad-ams-audio-matrix/issues](https://github.com/ajguerre1/ha-triad-ams-audio-matrix/issues).

Please attach the diagnostics file — go to **Settings → Devices & Services → Triad AMS Audio
Matrix**, select the menu (⋮) and choose **Download diagnostics**. It describes what the
integration can see and **has your network address and hardware identifiers removed**, so it is
safe to attach to a public issue.

## Technical reference

The control protocol is documented in
[docs/triad-ams-protocol.md](docs/triad-ams-protocol.md) for anyone building against the same
hardware.

## Credits

An independent implementation, not a fork.
[`bharat/homeassistant-triad-ams`](https://github.com/bharat/homeassistant-triad-ams) was read as a
reference and deserves credit for two findings reproduced here: that some firmware pads its replies
with filler, which confuses a naive reader, and that the unit occasionally sends an empty reply on
a perfectly healthy connection and should not be disconnected when it does.

## Trademarks

**Triad** and the Triad logo are trademarks of their respective owners. This project is independent
and is **not affiliated with, endorsed by, or supported by** the manufacturer.

The logo appears only to identify which product the integration works with, which is what an
integration icon is for in Home Assistant. The artwork remains the property of its owner.

## Licence

MIT, covering the code in this repository. The licence does not extend to the Triad marks; see
**Trademarks** above.
