# Enbrighten Eternity — dp106 protocol (decoded by driving the device)

Device: productId `wagmuetuwhn4lgr7`, Tuya LAN protocol 3.5, category `dj`.
Only 5 DPs exist. Everything (color, white, scenes, effects, speed, brightness)
is packed into **dp106**.

## DPs
| DP | type | meaning |
|----|------|---------|
| 20 | bool | power on/off |
| 26 | int | countdown seconds (0 = off) |
| 101 | bool | unknown (stayed false; likely music/mic mode) |
| 104 | string(12) | per-bulb on/off mask, one char per bulb, `1`=on `0`=off (12 bulbs) |
| 106 | string | the light state (see below) |

## dp106 format
Two shapes share one header:

```
[B0 mode][B1 effect][B2 B3 speed][B4 B5 brightness]  (+ optional  N '#' colors)
```
- **B0 mode**: `ff` = color/effect mode (colors follow). Any other value = a
  built-in scene preset (B0 = scene id); no color list.
- **B1 effect** (color mode): the animation. Decoded IDs:
  Solid=00, Strobe=02, Pulse=03, Chase=05, FadeShift=07, Lightning=08,
  Flame=09, Stacking=0a, Chain=0b, Blocking=0c, Shuffle=0d, Collide=10,
  Morph=12, Drift=13, Shimmer=15, FadeMelt=16.
- **B2B3 speed**: 16-bit, 0..1000 (0x03e8). e.g. 0x0064=100 slow, 0x038e=910 fast.
- **B4B5 brightness**: 16-bit, min..1000. e.g. 0x0104=260 (~25%), 0x03e8=1000 (100%).
- Then a single digit = **(number of colors − 1)**, then `#`, then each color as
  **8 hex = W,R,G,B** bytes (00..ff). 1 color → `0#`, 2 → `1#`, up to 6 → `5#`.

### Worked examples (captured live)
```
ff 00 0064 02da 0#00ff0000              solid RED,   bright 730
ff 00 0064 02da 0#0000ff00              solid GREEN
ff 00 0064 02da 0#000000ff              solid BLUE
ff 00 0064 02da 0#ff000000              solid WARM WHITE (W channel)
ff 00 0064 02da 0#21308048              white 6000K (W21 R30 G80 B48 mix)
ff 09 0064 02da 0#21308048              Flame effect on that color
ff 0a 038e 02da 0#21308048              Stacking, speed 910
ff 00 01f4 03e8 1#00ff0000 000000ff     2 colors RED+BLUE (written; device accepted)
```

### Built-in scene presets (B0 = scene id), captured
Bubbly=0x33, MardiGras=0x06, Love=0x07, Rainbow=0x03, Clover=0x09, (baseline)=0x02.
Same trailing `[speed][brightness]`; brightness editable, scene supplies colors.
(Many more presets exist under Game Day / Every Day / Celebration — ids to be
catalogued; not required for full manual control.)

## What this enables in Home Assistant
Full control without the cloud: on/off, brightness, speed, any single color
(RGB + dedicated white), all 16 effects, multi-color sequences (1–6), per-bulb
mask, and recall of built-in presets. The color/effect mode is strictly more
capable than the named presets.

## Open items
- Visual confirmation of multi-color rendering (format accepted by device; needs eyes-on).
- dp101 purpose; full scene-id catalogue; exact speed min/max clamping.
