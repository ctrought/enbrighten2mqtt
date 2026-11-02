# enbrighten2mqtt

Control Enbrighten Eternity (VIBE) landscape and eave lights from Home Assistant
over your LAN, with no re-pairing. The Tuya cloud is used only to fetch device
keys and DIY scenes.

Modeled on [govee2mqtt](https://github.com/wez/govee2mqtt).

> [!NOTE]
> Tested on one model: Enbrighten Eternity Landscape Lights (`wagmuetuwhn4lgr7`,
> Tuya LAN protocol 3.5). See [Supported devices](#supported-devices).

## Features

- **Full light control:** on/off, brightness, RGB plus dedicated white, and white
  temperature (2200–6000K)
- **Effects:** all 21, with adjustable speed and direction
- **Scenes:** all 58 built-in presets, plus DIY and edited scenes from the app
- **Colour sequences:** 1–6 colours
- **Individual bulbs:** on/off for each of the 12 bulbs
- **Extras:** auto-off timer and saved presets
- **Two-way state:** changes from the app or the physical button show up in Home
  Assistant

## Contents

- [Quick start](#quick-start)
- [How it works](#how-it-works)
- [Install](#install)
- [Configuration](#configuration)
- [Home Assistant entities](#home-assistant-entities)
- [Scenes](#scenes)
- [MQTT topics](#mqtt-topics)
- [Command line](#command-line)
- [Supported devices](#supported-devices)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Security](#security)
- [Legal](#legal)

## Quick start

You need an Enbrighten account with the lights already paired in the app, an MQTT
broker connected to Home Assistant, and a host on the same LAN as the lights.

```bash
docker run -d --name enbrighten2mqtt --network host \
  -e ENBRIGHTEN_EMAIL=you@example.com \
  -e ENBRIGHTEN_PASSWORD='your-password' \
  -e MQTT_HOST=your-broker-host \
  -v /srv/enbrighten2mqtt:/data \
  ghcr.io/ctrought/enbrighten2mqtt:latest
```

The lights then appear in Home Assistant through MQTT discovery.

## How it works

```text
Home Assistant <--MQTT--> enbrighten2mqtt <--Tuya LAN protocol--> your lights
                                 |
                                 +--HTTPS, at login only--> Tuya cloud
```

Each light needs a `localKey` to talk to it directly. The bridge logs in to your
Enbrighten account to read those keys, and to read any DIY scenes, then caches the
keys in `devices.json`.

Control commands never go through the cloud. Once the keys are cached, the bridge
keeps working if the internet or Tuya is down. It only needs to log in again when
you add or re-pair a light, or to pick up new DIY scenes.

The phone app keeps working, because the bridge uses the keys the cloud already
issued.

## Install

### Container

Use this for Home Assistant Container or Core, or any Docker or Podman host. This
is the most thoroughly tested option.

Run the command in [Quick start](#quick-start), or use Compose:

```yaml
services:
  enbrighten2mqtt:
    image: ghcr.io/ctrought/enbrighten2mqtt:latest
    restart: unless-stopped
    network_mode: host
    environment:
      ENBRIGHTEN_EMAIL: you@example.com
      ENBRIGHTEN_PASSWORD: your-password
      MQTT_HOST: your-broker-host
    volumes:
      - /srv/enbrighten2mqtt:/data
```

Host networking is required because LAN discovery uses UDP broadcast. The `/data`
volume keeps the key cache and your scenes.

### Home Assistant app (add-on)

Use this for Home Assistant OS or Supervised. It runs the same image, but the
install steps themselves haven't been tested yet.

1. Go to **Settings > Apps > Install app**, then **⋮ > Repositories**, and add
   `https://github.com/ctrought/enbrighten2mqtt`.
2. Install **Enbrighten2MQTT**.
3. In **Configuration**, set `enbrighten_email` and `enbrighten_password`. Leave
   the `mqtt_*` options blank to use your existing MQTT broker app.
4. Start it.

### From source

```bash
pip install -e .
cp config.example.yaml config.yaml    # then set your broker and account
enbrighten2mqtt serve
```

## Configuration

Set options with environment variables or `config.yaml`. Environment variables
take priority. The Home Assistant app has the same settings as options.

| Environment variable | `config.yaml` | App option | Purpose |
|---|---|---|---|
| `ENBRIGHTEN_EMAIL` | `enbrighten.email` | `enbrighten_email` | Account email |
| `ENBRIGHTEN_PASSWORD` | `enbrighten.password` | `enbrighten_password` | Account password |
| `MQTT_HOST` | `mqtt.host` | `mqtt_host` | Broker host. Blank in the app means Home Assistant's broker |
| `MQTT_PORT` | `mqtt.port` | `mqtt_port` | Broker port (default `1883`) |
| `MQTT_USER` | `mqtt.username` | `mqtt_user` | Broker username |
| `MQTT_PASS` | `mqtt.password` | `mqtt_pass` | Broker password |
| `DEVICES_REFRESH` | `enbrighten.refresh` | `device_refresh` | When to log in: `if_missing` (default), `always` or `never` |
| `OEM_CREDS` | `enbrighten.oem_creds` | – | Path to the [app constants](#app-constants) |
| `DEVICES_FILE` | `enbrighten.devices_file` | – | Path to the key cache |
| `LOG_LEVEL` | `log_level` | `log_level` | `INFO`, `DEBUG` and so on |

To skip the cloud entirely, list your devices and their keys in `config.yaml`. See
[`config.example.yaml`](config.example.yaml).

### App constants

`oem.json` holds the Enbrighten app's own Tuya API constants: app key, secret and
signing values. They're the same for every user and aren't your login. The
published image includes them, so you don't need to do anything.

If you build the image yourself, put your own `oem.json` in `secrets/`. The Home
Assistant app also accepts one in its config folder.

## Home Assistant entities

Each light gets these entities:

| Entity | Type | What it does |
|---|---|---|
| Light | `light` | On/off, brightness, RGBW, white temperature and a list of every effect and scene |
| Speed | `number` | Animation speed, 0–100% |
| Effect | `select` | The animation, such as `solid`, `twinkle`, `chase` or `flame` |
| Scene | `select` | Built-in, learned and DIY scenes, plus saved presets |
| Colors | `text` | 1–6 colours, comma-separated, as `#RRGGBB` or `#RRGGBBWW` |
| Bulb 1–12 | `switch` | Turns one bulb on or off |
| Auto-off timer | `number` | Minutes until the light turns off. `0` cancels it |
| Reverse effect direction | `switch` | Runs effects the other way |
| Preset name | `text` | The name to use for Save preset |
| Save preset | `button` | Saves what's showing now |
| Connectivity | `binary_sensor` | Whether the bridge can reach the light |

### How effects and scenes interact

These work the same way as in the app:

| You do this | What happens |
|---|---|
| Choose an effect during a scene | The scene and its colours stay, and only the animation changes |
| Reselect the scene | Its default effect and speed come back |
| Set a colour | The light leaves the scene, and Scene shows as unknown |

To keep an effect you've chosen for a scene, use **Save preset**. Reselecting the
original scene will otherwise reset it.

Some settings have no visible effect, as in the app:

- Speed does nothing on `solid`, `blocking`, `drift` and `shimmer`.
- Brightness does nothing on `lightning`.
- Every Day scenes only support `solid`.

## Scenes

The lights can't list their scenes, so the bridge gets them from four places:

| Source | Where it comes from | Keeping it up to date |
|---|---|---|
| **Built-in** | The 58 presets from the app's Celebration, Every Day and Game Day tabs | Included with the bridge |
| **DIY and edited** | The app's cloud storage, read when the bridge logs in | Run an [import](#command-line), then restart |
| **Learned** | Any unknown scene the light reports, saved with a name like `scene_2a` | Automatic. [Rename it](#mqtt-topics) if you like |
| **Saved presets** | Saved from Home Assistant with Save preset | Stored on the bridge only, so they don't appear in the app |

Imported scenes are recalled locally, so only the import uses the cloud. A DIY
scene selected in the app still appears as a learned scene without an import, but
it won't have its name.

Learned, imported and saved scenes are kept in `scenes.json`, next to
`devices.json`.

## MQTT topics

Live state is published to `enbrighten2mqtt/<devId>/attributes` and is also shown
on the light entity. It includes speed, mode, the bulb mask, the full colour list
and the raw `dp106` value.

For automations, send JSON to `enbrighten2mqtt/<devId>/set/advanced`:

| Purpose | Payload |
|---|---|
| Set colours and speed | `{"colors":[{"r":255},{"g":255},{"b":255}],"speed":300}` |
| Set bulbs on or off | `{"mask":"110011001100"}` |
| Select a scene | `{"scene":"rainbow"}` |
| Turn off after 10 minutes | `{"countdown":600}` |
| Rename a scene | `{"rename_scene":{"from":"scene_2a","to":"Porch party"}}` |
| Delete a learned scene, imported scene or preset | `{"delete_scene":"Porch party"}` |

## Command line

`serve` logs in by itself when it needs to. The other commands are mostly for setup
and troubleshooting.

| Command | What it does |
|---|---|
| `enbrighten2mqtt serve` | Runs the MQTT bridge. This is what the container does |
| `enbrighten2mqtt login` | Fetches device keys into `devices.json` |
| `enbrighten2mqtt list` | Shows cached devices |
| `enbrighten2mqtt discover` | Finds lights on the LAN and shows their IPs |
| `enbrighten2mqtt probe --ip <ip> --device-id <id> --local-key <key>` | Tests a connection to one light |
| `enbrighten2mqtt scenes list` | Shows every scene and effect the bridge knows |
| `enbrighten2mqtt scenes import` | Fetches DIY and edited scenes from the cloud |
| `enbrighten2mqtt scenes capture` | Names scenes as you select them in the app |

## Supported devices

**Tested:** Enbrighten Eternity Landscape Lights, product ID `wagmuetuwhn4lgr7`,
Tuya LAN protocol 3.5.

Other lights may partly work:

- **Works with any device on your account:** login, key retrieval, LAN discovery,
  protocol version detection and the MQTT plumbing.
- **Specific to this model:** how the light's state is encoded. The Eternity packs
  nearly everything into one value (`dp106`). Other Tuya lights use different
  datapoints, so colours and effects won't map correctly without their own codec.
  Basic on/off may work, but it's unverified.

To add a model, see `codec/eternity.py`, `profiles/wagmuetuwhn4lgr7.yaml` and
[`PROTOCOL.md`](PROTOCOL.md).

## Limitations

- **Per-bulb colour isn't possible.** The protocol has per-bulb on/off and a
  sequence of 1–6 colours, and the app offers nothing finer.
- **The bulb count is fixed at 12.** The app supports cut or extended strands, but
  other lengths would need a configurable number of switches.
- **Schedules aren't exposed.** They're Tuya cloud timers rather than device
  settings. Use Home Assistant automations instead.
- **DIY scenes can't be created in the app from Home Assistant.** Saved presets are
  the equivalent, but stay on the bridge.
- **White temperature is approximate** between the app's six presets.
- **App updates could break login.** Login follows the app's current API signing
  (Thing SDK 6.11.0). Control of lights with cached keys doesn't depend on this.

## Troubleshooting

| Problem | What to do |
|---|---|
| Lights stop responding after re-pairing | Re-pairing changes the key. Run `enbrighten2mqtt login`, or set `DEVICES_REFRESH=always`, then restart |
| No lights discovered | The bridge must be on the same broadcast domain and needs host networking. Across VLANs or subnets, set each light's IP in `config.yaml` |
| New or edited DIY scene is missing or out of date | Run `enbrighten2mqtt scenes import`, or set `DEVICES_REFRESH=always`, then restart |
| Light connects intermittently | These lights serve one LAN session at a time. Make sure nothing else, such as a second bridge, is connected directly |
| More detail needed | Set `LOG_LEVEL=DEBUG` |

## Development

```bash
make test     # unit tests
make ci       # tests, lint and a local image build
```

The Makefile uses Podman by default. Add `ENGINE=docker` to use Docker. It reads
the image owner from your git remote, or you can set `OWNER=you`.

To release:

1. Bump `version` in `pyproject.toml` and `ha-addon/config.yaml`.
2. Run `make login` and `make release` to test and push a multi-arch image tagged
   `X.Y.Z` and `latest`.
3. Run `make tag` to create the git tag `vX.Y.Z`, then push it.

| Path | Contents |
|---|---|
| `enbrighten2mqtt/cloud/` | Tuya mobile API client: login, device list, DIY scenes |
| `enbrighten2mqtt/local/` | LAN connection and broadcast discovery, using tinytuya |
| `enbrighten2mqtt/codec/eternity.py` | `dp106` encoding and decoding |
| `enbrighten2mqtt/controller.py` | Device control that preserves state |
| `enbrighten2mqtt/scenes.py` | Learned scenes, DIY import and saved presets |
| `enbrighten2mqtt/mqtt/bridge.py` | MQTT, Home Assistant discovery and automatic login |
| `enbrighten2mqtt/profiles/` | Datapoint map for each product |
| `ha-addon/` | Home Assistant app manifest |
| `tests/` | Unit tests (`pytest`) |

Protocol details are in [`PROTOCOL.md`](PROTOCOL.md).

## Security

- Prefer environment variables or app options for your password. `config.yaml` is
  gitignored, but it's still a plain text file.
- `devices.json` holds your device keys, so keep it private. `secrets/`,
  `devices.json`, `scenes.json`, `config.yaml` and `.passwd` are all gitignored.
- The bridge connects to the broker without TLS. Use broker authentication, and treat
  your broker as able to control your lights.

## Legal

This is an independent project. It isn't affiliated with, endorsed by or supported
by Jasco, Enbrighten or Tuya. Product names and trademarks belong to their owners
and are used only to identify compatible devices.

It's meant for controlling your own devices with your own account. You're
responsible for following the terms that apply to your account and devices.

Licensed under the [Apache License 2.0](LICENSE), and provided without warranty.
