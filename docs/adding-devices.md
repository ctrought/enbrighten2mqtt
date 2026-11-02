# Adding support for a new device

This guide explains how to work out what another Tuya-based light sends over the
LAN, and how to add it to the bridge.

> [!IMPORTANT]
> The bridge only supports one model today, and treats **every** device on your
> account as that model. There's no per-model selection yet, so the first new model
> also has to add it. See [Step 5](#step-5-select-the-codec-by-product).

## What's reusable

| Part | Files | Reusable? |
|---|---|---|
| Account login and key retrieval | `cloud/` | Yes, for devices on an Enbrighten account |
| LAN discovery and connection | `local/` | Yes, for Tuya LAN protocol 3.1–3.5 |
| MQTT connection and topic routing | `mqtt/bridge.py` | Yes |
| State encoding, effects and scenes | `codec/eternity.py` | No, this is specific to the model |
| Home Assistant entities and command mapping | `mqtt/bridge.py`, `controller.py` | Partly, they assume the Eternity's features |

## Before you start

You need the light paired in the Enbrighten app, plus the bridge's container image.
The commands below use it because it includes what `login` needs.

**Stop the bridge first.** These lights serve one LAN session at a time, so a
running bridge will conflict with the commands below.

Set up a shortcut for the commands:

```bash
alias e2m='docker run --rm -it --network host -v /srv/enbrighten2mqtt:/data -e ENBRIGHTEN_EMAIL -e ENBRIGHTEN_PASSWORD ghcr.io/ctrought/enbrighten2mqtt:latest'
```

## Step 1: Identify the device

Fetch your device keys, then list your devices and find their LAN IPs:

```bash
e2m login
```

```bash
e2m list
```

```bash
e2m discover
```

Note the device's **product ID**, **category** and **protocol version**. If Tuya's
cloud provides one, `login` also saves the product's schema to `schema/<productId>.json`.
This lists each datapoint's code and type. See
[`schema/wagmuetuwhn4lgr7_functions.json`](../schema/wagmuetuwhn4lgr7_functions.json)
for an example.

## Step 2: Read the datapoints

Tuya devices expose their state as numbered datapoints (DPs). Read the current ones,
taking the local key from `devices.json`:

```bash
e2m probe --ip <lan-ip> --device-id <id> --local-key <key>
```

Compare what you see with the schema:

- **Standard Tuya codes**, such as `switch_led`, `work_mode`, `bright_value_v2` and
  `colour_data_v2`, each hold one setting. These are the simplest to support, and a
  single standard codec could cover many models.
- **A custom packed string** holds many settings in one DP, as `dp106` does on the
  Eternity. You'll need to decode the format.

## Step 3: Capture changes from the app

Change one setting at a time in the app and record which DPs change:

```bash
e2m capture --device <id> --ip <lan-ip>
```

Each prompt asks you to do something in the app, then press Enter. Results are
saved to `captures/` as JSON Lines. The prompts are written for the Eternity, so
press Enter to skip any that don't apply, and Ctrl-C to stop.

To watch changes continuously instead, run `tools/lan_monitor.py <id>` from a source
checkout. It reads `devices.json`, so first set the device's `ip` there to its LAN
IP, and create a `captures/` folder.

Capture at least:

- Power, and brightness at its minimum and maximum
- Solid red, green, blue and white, and the white temperature limits
- Every effect, and speed at its minimum and maximum
- A few scenes, including one DIY scene
- Timers, and anything specific to the device

To decode a packed format, change one setting and look for which bytes move. See
[`PROTOCOL.md`](../PROTOCOL.md) for how the Eternity's format is documented.

## Step 4: Write a codec

Create `enbrighten2mqtt/codec/<model>.py`, using `codec/eternity.py` as the template.
It provides:

| Item | Purpose |
|---|---|
| `DP_*` constants | The number of each datapoint |
| `EFFECTS`, `SCENE_PAYLOADS` | Names and values for effects and scenes |
| `LightState`, `Color` | The decoded state |
| `decode_state()`, `encode_state()` | Convert between the device's format and `LightState` |
| `decode_mask()`, `encode_mask()` | Per-bulb on/off, if the device has it |
| `ha_to_dev_brightness()`, `dev_to_ha_brightness()` | Convert between brightness scales |

Follow these rules, which prevent the bugs that were most visible on the Eternity:

- **Keep what you don't recognise.** Unknown scene and effect values must survive a
  decode and re-encode unchanged, or a brightness change could switch the scene.
- **Only write datapoints you understand.** Show an unknown DP as read-only until
  you've confirmed what it does.
- **Send one write per command.** Several writes can make the lights flash.

Then add:

- `enbrighten2mqtt/profiles/<productId>.yaml`, recording the datapoints for reference
  (the bridge doesn't read these files at runtime yet)
- Tests in `tests/test_<model>.py`, using real values from your captures, as
  `tests/test_codec.py` does
- A protocol write-up, either in `PROTOCOL.md` or a new file beside it

## Step 5: Select the codec by product

Each device already has a `product_id` (`DeviceConfig` in `config.py`), but these
files import the Eternity codec directly:

| File | What needs to change |
|---|---|
| `controller.py` | Take the codec for the device's product instead of importing it |
| `scenes.py` | Use that codec's built-in scenes and effects |
| `mqtt/bridge.py` | Choose the codec by `product_id`, and publish only the entities the model supports. For example, the 12 bulb switches and `KELVIN_MIN`/`KELVIN_MAX` come from the Eternity codec |
| `mqtt/bridge.py` | Add the model's name to `MODEL_NAMES` |

Devices with an unknown product ID should be skipped with a log message rather than
treated as an Eternity. Keep the existing entity IDs and `scene:<name>` effect names
unchanged, so current users' automations keep working.

## Step 6: Test it

Run the unit tests:

```bash
make test
```

Then run the bridge against the real device and check that:

- [ ] Every entity registers in Home Assistant, with no MQTT discovery errors in its log
- [ ] Each entity changes the light, and its state updates to match
- [ ] Changing brightness or speed doesn't change the current scene or colours
- [ ] Changes made in the app or with the physical button appear in Home Assistant
- [ ] The state is correct after restarting the bridge
- [ ] The light shows as unavailable when it's unplugged, and recovers afterwards

## Step 7: Share it

Open a pull request with your code, tests and protocol notes. To request support
without writing code, open an issue with:

- The model name and where you bought it
- The product ID, category and protocol version
- The schema file, and your captures for each setting

> [!WARNING]
> **Remove private data before sharing.** Don't post your local key, device ID, IP
> addresses, account details or `devices.json`. Check your captures and logs first.
> Local keys let anyone on your network control the device.

Only list a model as supported once it's been tested on real hardware.
