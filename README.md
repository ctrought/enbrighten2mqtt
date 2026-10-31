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
   device's `localKey`, IP and product id. (The cloud-login signing is still
   being finalised — see *Key retrieval* below for the method that works today.)
3. **Configure**: copy `config.example.yaml` to `config.yaml`, set your MQTT
   broker. Devices load from `devices.json` automatically.
4. **Run**: `python -m enbrighten2mqtt serve`. The light appears in Home
   Assistant via MQTT discovery.

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
python -m enbrighten2mqtt probe --ip <ip> --device-id <id> --local-key <key>
python -m enbrighten2mqtt capture --device <id>           # interactive DP capture
python -m enbrighten2mqtt serve                           # run the MQTT bridge
```


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
