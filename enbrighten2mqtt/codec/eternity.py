"""Encode/decode the Enbrighten Eternity dp106 light protocol.

Decoded live from the device (see PROTOCOL.md). The light exposes 5 DPs; dp106
carries color, white, scene, effect, speed and brightness in one compact string.

dp106 layout (hex string):
    B0 mode  B1 effect  B2B3 speed(0..1000)  B4B5 brightness(0..1000)
    then (ncolors-1) digit, '#', then each color as 8 hex = W,R,G,B.

    B0 = 0xff  -> color/effect mode (colors follow)
    B0 = other -> built-in scene preset id (no color list)

dp104 = 12-char per-bulb mask ('1' on / '0' off).
dp20  = power. dp26 = countdown seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DP_SWITCH = 20
DP_COUNTDOWN = 26
DP_UNKNOWN101 = 101
DP_MASK = 104
DP_STATE = 106

SCALE_MAX = 1000  # speed & brightness 16-bit range
COLOR_MODE = 0xFF

# Effect name <-> B1 id (color mode). From live capture.
EFFECTS = {
    "solid": 0x00, "strobe": 0x02, "pulse": 0x03, "chase": 0x05,
    "fade_shift": 0x07, "lightning": 0x08, "flame": 0x09, "stacking": 0x0a,
    "chain": 0x0b, "blocking": 0x0c, "shuffle": 0x0d, "collide": 0x10,
    "morph": 0x12, "drift": 0x13, "shimmer": 0x15, "fade_melt": 0x16,
}
EFFECTS_INV = {v: k for k, v in EFFECTS.items()}

# Built-in scene presets, stored as their exact captured dp106 payloads so
# recall is faithful (each scene has its own internal B1 byte and default
# speed). Extend this map by capturing more presets from the app.
SCENE_PAYLOADS = {
    "halloween": "0212019003e8",
    "rainbow": "0301012c03e8",
    "mardi_gras": "060c01f403e8",
    "love": "0701012c03e8",
    "clover": "0900006403e8",
    "bubbly": "3301012c03e8",
}
# scene id (B0) -> name, for decoding device state back to a name.
SCENE_ID_TO_NAME = {int(p[0:2], 16): n for n, p in SCENE_PAYLOADS.items()}

NUM_BULBS = 12


@dataclass
class Color:
    """One palette entry: dedicated white + RGB, each 0..255."""
    r: int = 0
    g: int = 0
    b: int = 0
    w: int = 0

    def to_hex(self) -> str:
        return f"{self.w:02x}{self.r:02x}{self.g:02x}{self.b:02x}"

    @classmethod
    def from_hex(cls, h: str) -> "Color":
        w, r, g, b = (int(h[i:i+2], 16) for i in (0, 2, 4, 6))
        return cls(r=r, g=g, b=b, w=w)


@dataclass
class LightState:
    mode: str = "color"          # "color" or "scene"
    effect: str = "solid"        # when mode == color
    scene: str | None = None     # when mode == scene
    speed: int = 100             # 0..1000
    brightness: int = 1000       # 0..1000
    colors: list[Color] = field(default_factory=lambda: [Color(w=255)])


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


# ---- encode ---------------------------------------------------------------

def encode_state(st: LightState) -> str:
    speed = _clamp(st.speed, 0, SCALE_MAX)
    bright = _clamp(st.brightness, 1, SCALE_MAX)
    if st.mode == "scene":
        raw = SCENE_PAYLOADS.get(st.scene or "")
        if raw:
            # Replay the captured payload, overriding only brightness (B4B5).
            return raw[:8] + f"{bright:04x}"
        return f"0200{speed:04x}{bright:04x}"
    b1 = EFFECTS.get(st.effect, 0x00)
    colors = st.colors or [Color(w=255)]
    colors = colors[:6]
    # Colors are '#'-separated; the section is prefixed with (count-1)'#'.
    payload = "#".join(c.to_hex() for c in colors)
    n = len(colors) - 1
    return f"{COLOR_MODE:02x}{b1:02x}{speed:04x}{bright:04x}{n}#{payload}"


def decode_state(raw: str) -> LightState:
    parts = raw.split("#")
    header = parts[0]            # mode+effect+speed+bright+(count-1)
    b0 = int(header[0:2], 16)
    b1 = int(header[2:4], 16)
    speed = int(header[4:8], 16)
    bright = int(header[8:12], 16)
    if b0 == COLOR_MODE:
        # parts[1:] are the '#'-separated colors (each 8 hex = W,R,G,B).
        colors = [Color.from_hex(p) for p in parts[1:] if len(p) == 8]
        return LightState(mode="color", effect=EFFECTS_INV.get(b1, f"fx_{b1:02x}"),
                          speed=speed, brightness=bright,
                          colors=colors or [Color(w=255)])
    return LightState(mode="scene", scene=SCENE_ID_TO_NAME.get(b0, f"scene_{b0:02x}"),
                     speed=speed, brightness=bright)


# ---- segment mask ---------------------------------------------------------

def encode_mask(on_bulbs: list[bool]) -> str:
    bits = (on_bulbs + [True] * NUM_BULBS)[:NUM_BULBS]
    return "".join("1" if b else "0" for b in bits)


def decode_mask(raw: str) -> list[bool]:
    return [c == "1" for c in raw]


# ---- HA brightness (0..255) <-> device (0..1000) --------------------------

def ha_to_dev_brightness(v: int) -> int:
    return _clamp(round(v / 255 * SCALE_MAX), 1, SCALE_MAX)


def dev_to_ha_brightness(v: int) -> int:
    return _clamp(round(v / SCALE_MAX * 255), 1, 255)
