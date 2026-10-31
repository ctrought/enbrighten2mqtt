# enbrighten2mqtt

Local **LAN** control of Enbrighten Eternity (VIBE) landscape/eave lights in
Home Assistant over MQTT — no Tuya cloud, no Smart Life, no re-pairing. Modeled
on govee2mqtt.

These lights are white-label **Tuya "Thing"** devices. This bridge talks the
Tuya **LAN protocol (3.5)** directly using each device's `localKey`, and
decodes the compact `dp106` state so Home Assistant gets a full light: on/off,
brightness, RGB + dedicated white, 16 animated effects, built-in scenes,
multi-colour sequences, and per-bulb masking.

Status: **working and validated on real hardware** (Eternity Landscape Lights,
productId `wagmuetuwhn4lgr7`, protocol 3.5). See `PROTOCOL.md` for the decoded
`dp106` format.

## How it works
```
Home Assistant ── MQTT ──> enbrighten2mqtt ── Tuya LAN 3.5 ──> controller on your LAN
       (MQTT discovery)         (this bridge)        (localKey)        (your lights)
```

## Setup

1. **Install**: `pip install -r requirements.txt` (Python 3.11+), or use the
   Dockerfile / the `ha-addon/` Home Assistant add-on.
2. **Get your device keys** → writes `devices.json`:
   ```
   python -m enbrighten2mqtt login            # Enbrighten email + password
   ```
   This logs into your own Enbrighten (Tuya OEM) account and fetches each
   device's `localKey` and product id. Just your email + password — no emulator,
   no Tuya developer project. (If you signed up with Google/Apple, set an
   account password in the app first, via *Forgot password*.)
3. **Configure**: copy `config.example.yaml` to `config.yaml`, set your MQTT
   broker. Devices load from `devices.json` automatically.
4. **Check the lights are reachable** (optional): `python -m enbrighten2mqtt
   discover` scans your LAN and shows each device's local IP.
5. **Run**: `python -m enbrighten2mqtt serve`. The bridge finds each device on
   the LAN by broadcast and the light appears in Home Assistant via MQTT
   discovery.

### Cloud vs. local
The cloud is only touched by `login`, and only to read the `localKey`s. Once
`devices.json` exists, everything — discovery and all control — is **local**;
the bridge keeps working with the internet (or Tuya) down. Re-run `login` only
when you add a device or re-pair one in the app (re-pairing rotates its key).

## Home Assistant entity
A JSON-schema MQTT light per device:
- **on/off**, **brightness**, **rgbw** colour (dedicated white channel)
- **effect_list**: 16 effects (`solid`, `strobe`, `pulse`, `chase`, `flame`,
  `shimmer`, `morph`, `drift`, …) plus built-in scenes (`scene:rainbow`,
  `scene:mardi_gras`, `scene:clover`, …)

Advanced control via `enbrighten2mqtt/<devId>/set/advanced` (JSON), for
automations:
```json
{"colors":[{"r":255},{"g":255},{"b":255}], "speed":300}
{"mask":"110011001100"}          // per-bulb on/off (12 bulbs)
{"scene":"rainbow"}
{"countdown":600}                // seconds
```
Live attributes (speed, mode, per-bulb mask, full colour list) are published to
`enbrighten2mqtt/<devId>/attributes`.

## CLI
```
python -m enbrighten2mqtt extract-keys <enbrighten.apk>   # pull Tuya OEM creds
python -m enbrighten2mqtt login                           # fetch localKeys -> devices.json
python -m enbrighten2mqtt list                            # show saved devices
python -m enbrighten2mqtt discover                        # find devices on the LAN
python -m enbrighten2mqtt probe --ip <ip> --device-id <id> --local-key <key>
python -m enbrighten2mqtt capture --device <id>           # interactive DP capture
python -m enbrighten2mqtt serve                           # run the MQTT bridge
```


## Deployment
No custom Home Assistant integration is needed — the bridge publishes via MQTT
discovery, so HA's built-in MQTT integration creates the light entities itself.
The bridge **auto-logs-in on startup** (from config/env credentials) when it has
no cached keys, so a container just needs your email, password and an MQTT host.

**Everything is configurable by env var or file** (env wins):

| Env | Purpose |
|-----|---------|
| `ENBRIGHTEN_EMAIL` / `ENBRIGHTEN_PASSWORD` | account login (fetches localKeys) |
| `OEM_CREDS` | path to the Enbrighten app constants (`oem.json`) |
| `DEVICES_FILE` | where the fetched key cache is written/read |
| `DEVICES_REFRESH` | `if_missing` (default) / `always` / `never` |
| `MQTT_HOST` / `MQTT_PORT` / `MQTT_USER` / `MQTT_PASS` | broker |

`oem.json` holds the Enbrighten app's own API constants (extracted once from the
APK; the same for every user). It is **not** in this repo — provide it via
`OEM_CREDS` / the add-on config folder, or bake it into your image.

### Standalone container (HA Container/Core, or any Docker host)
```
docker run --network host \
  -e ENBRIGHTEN_EMAIL=you@example.com -e ENBRIGHTEN_PASSWORD=... \
  -e MQTT_HOST=your-broker-host \
  -v /srv/enbrighten2mqtt:/data \   # holds secrets/oem.json + devices.json cache
  ghcr.io/youruser/enbrighten2mqtt
```
`--network host` is required: LAN discovery is a UDP broadcast.

### Home Assistant add-on (HA OS / Supervised)
`ha-addon/` is a standard add-on: `host_network: true` for LAN access and
`services: [mqtt:want]` so it uses HA's Mosquitto broker automatically. You set
your Enbrighten email/password in the add-on options and drop `oem.json` in the
add-on config folder; it self-provisions on start. (Publish the image for your
arches from `ha-addon/Dockerfile` and point the add-on's repo at it.)

## Files
- `enbrighten2mqtt/codec/eternity.py` — dp106 encode/decode (the protocol)
- `enbrighten2mqtt/controller.py` — state-preserving device control
- `enbrighten2mqtt/mqtt/bridge.py` — MQTT + HA discovery
- `enbrighten2mqtt/local/device.py` — tinytuya LAN wrapper
- `enbrighten2mqtt/cloud/` — Tuya OEM mobile API client (for `login`)
- `profiles/wagmuetuwhn4lgr7.yaml` — per-product DP map
- `tests/` — codec + controller tests (run `pytest`)

## Safety / scope
Secrets (`devices.json`, `secrets/`) are gitignored and chmod 600. This controls
only your own devices on your own LAN.
