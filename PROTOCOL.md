# Enbrighten Eternity — dp106 protocol

Device: productId `wagmuetuwhn4lgr7`, Tuya LAN protocol 3.5, category `dj`.
Only 5 DPs exist. Everything (color, white, scenes, effects, speed, brightness)
is packed into **dp106**.

Sources: driving the device live, plus the app's own control panel, a Tuya
mini-app (`0.0.5`, built 2025-10-28). Its bundle holds the encoder, preset table
and effect table, and on an Android device with the app installed it's under
`Android/data/com.enbrighten.smart/files/Godzilla/MiniApp/`.

## DPs
| DP | type | panel code | meaning |
|----|------|------------|---------|
| 20 | bool | `switch_led` | power on/off |
| 26 | int | `countdown` | countdown seconds (0 = off, max 86400) |
| 101 | bool | `reverse_direction` | reverse effect direction (confirmed: follows the app's setting) |
| 104 | string | `lightcustomization` | per-bulb on/off mask, one char per bulb, `1`=on `0`=off (12 bulbs here) |
| 106 | string | `mode` | the light state (see below) |

## dp106 format
```
[B0 mode][B1 effect][B2 B3 speed][B4 B5 brightness]  (+ optional  N '#' colors)
```
- **B0 mode**: `ff` = color/effect mode. `00` = the same, seen only on a factory-fresh
  light (`00004303e80#FF000000`, warm white); the app never sends it. Any other value
  = a scene id.
- **B1 effect**: the animation, in both modes (table below). The app's scene editor
  changes B1 and keeps B0, so a scene can run any effect. It only offers Solid on
  scenes 157–165.
- **B2B3 speed**: 16-bit, 0..1000.
- **B4B5 brightness** (`dimming`): 16-bit, 1..1000.
- Optional colors: a single digit = **(number of colors − 1)**, then `#` before
  each color, each **8 hex = W,R,G,B**. 1 color → `0#`, up to 6 → `5#`.
  The device also accepts and reports colors run together with no separators.

Built-in scenes send no colors, since the firmware supplies them. **DIY and edited
scenes send a scene id with a color list.**

### Worked examples
```
ff 00 0064 02da 0#00ff0000              solid RED, bright 730
ff 00 0064 02da 0#ff000000              solid WARM WHITE (W channel)
ff 09 0064 02da 0#21308048              Flame effect on white 6000K
ff 00 01f4 03e8 1#00ff0000#000000ff     2 colors RED+BLUE
06 0c 01f4 03e8                         Mardi Gras preset
12 05 01f4 03e8 1#00ff0000#000000ff     DIY scene (id 18), Chase, 2 colors
```

### Effects (B1)
Solid=00, Twinkle=01, Strobe=02, Pulse=03, WaveChase=04, Chase=05, Wave=06,
FadeShift=07, Lightning=08, Flame=09, Stacking=0a, Chain=0b, Blocking=0c,
Shuffle=0d, Collide=10, Vibe=11, Morph=12, Drift=13, Thunderstorm=14, Shimmer=15,
FadeMelt=16.

The panel disables its speed slider for Solid, Blocking, Drift and Shimmer, and its
brightness slider for Lightning. Ids 0e and 0f aren't offered as effects, but the
It's Alive and Halloween presets use them.

### Scene ids (B0)
| Range | Use |
|---|---|
| 2–17, 48–64 | built-in Celebration presets |
| 18–47 | DIY, Celebration tab |
| 101–106 | DIY, Game Day tab |
| 107–127 | built-in Game Day presets |
| 151–156 | DIY, Every Day tab |
| 157–165 | built-in Every Day presets |

The full list is `SCENE_PAYLOADS` in `codec/eternity.py`. B1 and speed in a preset
are defaults that can be edited, so **B0 alone identifies a scene**: the device was
found on Halloween as `0212…` where the table's default is `020f…`.

### White temperature
The app's white presets, as W + RGB (`WHITE_POINTS` in the codec):
2200K `ff 000000`, 2700K `94 004a20`, 3000K `6b 20652b`, 4000K `42 147036`,
5000K `31 1e7d44`, 6000K `21 308048`.

## DIY scenes and edited presets
There's no DP listing scenes. The panel keeps its scene records in Tuya's cloud
device storage: `s.m.dev.property.get` (v2.0, `{devId, bizType: 0}`) to read. Records are
stored as JSON nested inside JSON, one per key derived from `modeState_<mode>`:

```json
{"name": "Patio", "mode": 18, "effect": 5, "speed": 500, "dimming": 1000,
 "colorMode": "1", "colorData": [{"w":0,"r":255,"g":0,"b":0}, {"w":0,"r":0,"g":0,"b":255}],
 "lightStr": "111111111111", "type": "Celebration", "image": "..."}
```

Recalling one is an ordinary local dp106 write (plus dp104 for `lightStr`); only
reading the list needs the cloud. The record layout and the formatter come from the
panel code, and the device accepts and holds the resulting dp106.

## Open items
- Visual confirmation of multi-color rendering (the device accepts the format).
- The panel also knows `reverse_direction`'s siblings `lamp_number`,
  `lamp_number_user` and `music_sync_mode`, and the schema lists dp28
  `control_data`. None of them appear in this device's status.
