# enbrighten2mqtt

Local **LAN** control of Enbrighten Eternity (VIBE) landscape/eave lights in
Home Assistant over MQTT — **no Tuya cloud, no Smart Life app, no re-pairing**.
Modeled on [govee2mqtt](https://github.com/wez/govee2mqtt).

These lights are white-label **Tuya "Thing"** devices. The bridge logs into your
Enbrighten account once to read each device's `localKey`, then talks the Tuya
**LAN protocol** to the lights directly and exposes a full Home Assistant light
over MQTT: on/off, brightness, speed, RGB + dedicated white, white temperature,
21 animated effects, all 58 built-in scenes, your DIY scenes, multi-colour
sequences, per-bulb on/off, auto-off timer and effect direction.

> **Status:** working and validated on real hardware — Enbrighten Eternity
> Landscape Lights, productId `wagmuetuwhn4lgr7`, protocol 3.5. The whole chain
> (cloud login → localKey → LAN discovery → local control) is proven end to end.
> See [Supported devices](#supported-devices--limitations) for what else should
> work.

## How it works

```
Home Assistant ──MQTT──> enbrighten2mqtt ──Tuya LAN──> your lights on your LAN
   (MQTT discovery)         (this bridge)    (localKey)
```

**Cloud vs. local.** The Tuya cloud is touched *only* at startup, and *only* to
read your devices' `localKey`s (the cloud never sees a control command). Once
those keys are cached, discovery and all control are **100% local** — the bridge
keeps working with the internet, or Tuya, down. You only need the cloud again
when you **add** a device or **re-pair** one (re-pairing rotates its key).

## Install

Pick one. All three use the same bridge; the only per-user settings are your
**Enbrighten email + password** and the **MQTT broker**.

### A. Home Assistant add-on  *(HA OS / Supervised — easiest)*

1. **Settings → Add-ons → Add-on store → ⋮ → Repositories**, add this repo's URL.
2. Install **Enbrighten2MQTT**, open **Configuration**, set `enbrighten_email`
   and `enbrighten_password`. Leave `mqtt_*` blank to use your existing
   Mosquitto add-on automatically.
3. **Start**. The lights appear in Home Assistant via MQTT discovery.

The add-on uses host networking (required for LAN discovery) and pulls the
broker from HA's MQTT service, so there's nothing else to configure.

### B. Standalone container  *(HA Container/Core, or any Docker host)*

```bash
docker run -d --name enbrighten2mqtt --network host \
  -e ENBRIGHTEN_EMAIL=you@example.com \
  -e ENBRIGHTEN_PASSWORD='your-password' \
  -e MQTT_HOST=your-broker-host \
  -v /srv/enbrighten2mqtt:/data \
  ghcr.io/ctrought/enbrighten2mqtt:latest
```

`--network host` is **required**: LAN discovery is a UDP broadcast. The `/data`
volume persists the fetched key cache (`devices.json`).

### C. From source  *(development)*

```bash
pip install -e .
cp config.example.yaml config.yaml      # set broker + account
enbrighten2mqtt serve
```

## Configuration

Everything is settable by **environment variable** or **config file**; env wins.
The HA add-on exposes the same settings as options.

| Env var | Config (`config.yaml`) | Add-on option | Meaning |
|---|---|---|---|
| `ENBRIGHTEN_EMAIL` | `enbrighten.email` | `enbrighten_email` | account email |
| `ENBRIGHTEN_PASSWORD` | `enbrighten.password` | `enbrighten_password` | account password |
| `MQTT_HOST` | `mqtt.host` | `mqtt_host` | broker host (blank add-on = HA's) |
| `MQTT_PORT` | `mqtt.port` | `mqtt_port` | broker port (1883) |
| `MQTT_USER` / `MQTT_PASS` | `mqtt.username` / `.password` | `mqtt_user` / `mqtt_pass` | broker auth |
| `DEVICES_REFRESH` | `enbrighten.refresh` | `device_refresh` | `if_missing` (default) / `always` / `never` |
| `OEM_CREDS` | `enbrighten.oem_creds` | — (baked / `/config/oem.json`) | path to the app constants |
| `DEVICES_FILE` | `enbrighten.devices_file` | — (`/data/devices.json`) | key-cache path |
| `LOG_LEVEL` | `log_level` | `log_level` | `INFO` / `DEBUG` / … |

> **Security:** prefer env vars / add-on options over putting your password in a
> committed `config.yaml` (it's gitignored here for that reason).

On startup the bridge logs in automatically when the key cache is missing (or
`DEVICES_REFRESH=always`), caches `devices.json`, discovers each light on the
LAN, and serves — **no manual steps**. You can also pin devices explicitly and
skip the cloud entirely (see `config.example.yaml`).

### `oem.json` — the app constants

`oem.json` holds the **Enbrighten app's own** Tuya API constants (app key,
secret, signing key, …), extracted once from the public APK. They are **the same
for every user** — *not* your login — so the published image **bakes them in**
and you never deal with them. (Building the image yourself? Drop your `oem.json`
in `secrets/`; the add-on also accepts one in its config folder.)

## Home Assistant entities

Each device appears with these entities, via MQTT discovery:

| Entity | What it does |
|---|---|
| **Light** | on/off, brightness, RGBW, white temperature (2200–6000K), effect list with every effect and scene |
| **Speed** (number) | animation speed, 0–100% |
| **Effect** (select) | `solid`, `twinkle`, `strobe`, `pulse`, `wave_chase`, `chase`, `wave`, `flame`, … |
| **Scene** (select) | built-in, learned and DIY scenes, plus saved looks |
| **Colors** (text) | the 1–6 colour sequence: `#RRGGBB` or `#RRGGBBWW`, comma-separated |
| **Bulb 1–12** (switch) | turn individual bulbs on or off |
| **Auto-off timer** (number) | minutes until the light turns off; 0 cancels |
| **Reverse effect direction** (switch) | run effects the other way |
| **Preset name** (text) + **Save preset** (button) | save what's showing now as a look |
| **Connectivity** (binary sensor) | whether the bridge can reach the light |

Effect and Scene report as unknown while the other one is active. As in the app,
speed has no visible effect on `solid`, `blocking`, `drift` and `shimmer`, and
brightness has none on `lightning`.

### Scenes and DIY

The device has no way to list scenes, so they come from four places:

- **Built-in:** all 58 presets from the app's Celebration, Every Day and Game Day
  tabs ship with the bridge.
- **DIY and edited presets:** these live in the app's cloud storage. They're
  imported whenever the bridge logs in (`DEVICES_REFRESH=always`, or when the key
  cache is missing), or on demand with `enbrighten2mqtt scenes import`. After that
  they're recalled locally. A DIY scene made later needs another import.
- **Learned:** any scene id the device reports that the bridge doesn't know, such
  as from a newer app or the physical button, is saved as `scene_2a` and appears
  in Home Assistant straight away. Selecting a DIY scene in the app therefore
  works even without an import, but it needs a rename to get a friendly name.
- **Saved looks:** type a name in Preset name and press Save preset. This stores
  the current colours, effect, speed, brightness and bulb mask. Looks live on the
  bridge and don't appear in the app.

Learned scenes and looks are kept in `scenes.json` beside `devices.json`.

### Automation topic

`enbrighten2mqtt/<devId>/set/advanced` takes JSON:

```json
{"colors":[{"r":255},{"g":255},{"b":255}], "speed":300}
{"mask":"110011001100"}          // per-bulb on/off (12 bulbs)
{"scene":"rainbow"}
{"countdown":600}                // seconds
{"rename_scene":{"from":"scene_2a","to":"Porch party"}}
{"delete_scene":"Porch party"}   // learned scene, imported scene or look
```

Live attributes (speed, mode, per-bulb mask, full colour list, raw `dp106`) are
published to `enbrighten2mqtt/<devId>/attributes`, and show on the light entity.

## CLI

The bridge is also a CLI (handy for setup and troubleshooting):

```
enbrighten2mqtt login       # fetch localKeys -> devices.json (email + password)
enbrighten2mqtt list        # show cached devices
enbrighten2mqtt discover    # find devices on the LAN and show their IPs
enbrighten2mqtt probe --ip <ip> --device-id <id> --local-key <key>
enbrighten2mqtt serve       # run the MQTT bridge (what the container does)
enbrighten2mqtt scenes list     # every scene and effect the bridge knows
enbrighten2mqtt scenes import   # fetch DIY and edited scenes from the app's cloud
enbrighten2mqtt scenes capture  # name scenes as you select them in the app
```

`serve` does `login` for you on startup; the standalone `login`/`discover`
commands are mainly for debugging.

## Supported devices & limitations

**Tested:** Enbrighten Eternity Landscape Lights, productId `wagmuetuwhn4lgr7`,
LAN protocol 3.5. This is the only model verified end to end.

**What is generic vs. model-specific:**

- *Generic* (works for any device on your account): the cloud login + `localKey`
  retrieval, LAN discovery, protocol-version handling, and the MQTT/HA plumbing.
- *Model-specific*: how a light's state is encoded. The Eternity packs
  everything into one `dp106` string, decoded by `codec/eternity.py` and
  described by `profiles/wagmuetuwhn4lgr7.yaml` / `PROTOCOL.md`. **Other Tuya
  light models use different datapoints**, so colour/effects won't map correctly
  until a profile/codec for that product is added. Basic on/off may work via the
  common switch datapoint, but treat anything beyond the tested model as
  unverified.

Device control limits:

- **No per-bulb colour.** The protocol has per-bulb on/off and a 1–6 colour
  sequence, and the app offers nothing finer.
- **Bulb count is fixed at 12** in Home Assistant. The app supports cut or extended
  strands; other lengths would need the switch count made configurable.
- **Schedules aren't exposed.** They're Tuya cloud timers rather than device
  datapoints; use Home Assistant automations instead.
- **DIY scenes can't be created from Home Assistant in the app.** Saved looks are
  the equivalent, and stay on the bridge.
- White temperature between the app's six presets is interpolated.

Other known limitations:

- The bridge must be on the **same LAN/broadcast domain** as the lights (UDP
  discovery). Across VLANs/subnets, make the device subnet reachable or pin IPs
  in `config.yaml`.
- **Re-pairing** a device in the Enbrighten app **rotates its `localKey`** — run
  `login` again (or set `DEVICES_REFRESH=always`).
- Cloud login tracks the Enbrighten app's current API signing (Thing SDK
  6.11.0); a future app change could require updating `oem.json` / the signer.
- The HA add-on runs the same validated image, but add-on *installation* on HA
  OS depends on your setup; the standalone container is the most thoroughly
  exercised path.

## Building & releasing

A `Makefile` runs the whole pipeline locally (no CI service needed). It uses
**podman** by default, or **docker** (`make … ENGINE=docker`) — multi-arch push
goes through `podman manifest` or `docker buildx` accordingly. It hardcodes no
account: `OWNER` is parsed from your git remote and everything is overridable
(`make push OWNER=you`).

```bash
make ci                 # test + lint + build the image locally
make login              # registry login (uses $GHCR_TOKEN or `gh auth token`)
make release            # test, then multi-arch push of :<version> and :latest
make tag                # create the git tag vX.Y.Z
```

Versioning is semver: the git tag is `vX.Y.Z`, the image tags are `X.Y.Z` and
`latest`. Bump `version` in `pyproject.toml` (and the add-on's `config.yaml`
`version`) per release. GHCR is free for public images.


## Layout

- `enbrighten2mqtt/cloud/` — Tuya OEM mobile API client (login, device list)
- `enbrighten2mqtt/local/` — tinytuya LAN wrapper + broadcast discovery
- `enbrighten2mqtt/codec/eternity.py` — `dp106` encode/decode (the protocol)
- `enbrighten2mqtt/controller.py` — state-preserving device control
- `enbrighten2mqtt/scenes.py` — learned scenes, DIY import, saved looks
- `enbrighten2mqtt/mqtt/bridge.py` — MQTT + HA discovery + auto-login bootstrap
- `profiles/wagmuetuwhn4lgr7.yaml` — per-product datapoint map
- `ha-addon/` — Home Assistant add-on manifest
- `tests/` — codec, controller, signing, config tests (`pytest`)

## Safety / scope

Secrets (`secrets/`, `devices.json`, `config.yaml`, `.passwd`) are gitignored.
This controls only your own devices, on your own LAN, using keys already issued
to your own account.
