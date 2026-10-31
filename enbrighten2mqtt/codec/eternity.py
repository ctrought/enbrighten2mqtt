"""Encode/decode the Enbrighten Eternity dp106 light protocol.

Decoded live from the device (see PROTOCOL.md). The light exposes 5 DPs; dp106
carries color, white, scene, effect, speed and brightness in one compact string.

dp106 layout (hex string):
    B0 mode  B1 effect  B2B3 speed(0..1000)  B4B5 brightness(0..1000)
    then (ncolors-1) digit, '#', then each color as 8 hex = W,R,G,B.

    B0 = 0xff  -> color/effect mode (colors follow)
    B0 = 0x00  -> the same, as found on a factory-fresh light (never sent by the app)
    B0 = other -> scene id; built-in presets have no color list, DIY scenes do

dp104 = 12-char per-bulb mask ('1' on / '0' off).
dp20  = power. dp26 = countdown seconds.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DP_SWITCH = 20
DP_COUNTDOWN = 26
DP_REVERSE = 101
DP_MASK = 104
DP_STATE = 106

SCALE_MAX = 1000  # speed & brightness 16-bit range
COLOR_MODE = 0xFF

# Effect name <-> B1 id (color mode), from the app panel's effect table.
EFFECTS = {
    "solid": 0x00, "twinkle": 0x01, "strobe": 0x02, "pulse": 0x03,
    "wave_chase": 0x04, "chase": 0x05, "wave": 0x06, "fade_shift": 0x07,
    "lightning": 0x08, "flame": 0x09, "stacking": 0x0a, "chain": 0x0b,
    "blocking": 0x0c, "shuffle": 0x0d, "collide": 0x10, "vibe": 0x11,
    "morph": 0x12, "drift": 0x13, "thunderstorm": 0x14, "shimmer": 0x15,
    "fade_melt": 0x16,
}
EFFECTS_INV = {v: k for k, v in EFFECTS.items()}

# Built-in scene presets as dp106 payloads, from the app panel's preset table
# (mode, effect, speed, dimming); names are its English labels. See PROTOCOL.md.
SCENE_PAYLOADS = {
    # Celebration
    "bubbly": "3301012c03e8", "mardi_gras": "060c01f403e8", "love": "0701012c03e8",
    "rainbow": "0301012c03e8", "clover": "0900006403e8", "spring": "0a00012c03e8",
    "july_4th": "040c012c03e8", "fall": "080500c803e8", "witches_brew": "100900c803e8",
    "frankenstein": "110e01f403e8", "halloween": "020f01f403e8",
    "winter_wonderland": "300101f403e8", "candy_cane": "310d01f403e8",
    "classic_christmas": "3205012c03e8", "christmas": "0505012c03e8",
    "santas_helper": "3a15012c03e8", "tropical_vibes": "3912019003e8",
    "fiesta_glow": "4005019003e8", "high_tide": "340400c803e8",
    "cosmic": "3504012c03e8", "mermaid": "361501f403e8", "aurora": "371301f403e8",
    "tranquil": "3801006403e8", "chroma": "3b0501f403e8", "solar_flare": "3c0901f403e8",
    "hero": "3d0b032003e8", "pop_art": "3e04012c03e8", "stormy_night": "3f14032003e8",
    # EveryDay
    "welcome_home_warm_white": "9d00000003e8",
    "welcome_home_cool_white": "9e00000003e8", "welcome_home_daylight": "9f00000003e8",
    "security_warm_white": "a000000003e8", "security_cool_white": "a100000003e8",
    "security_daylight": "a200000003e8", "safeguard_warm_white": "a300000003e8",
    "safeguard_cool_white": "a400000003e8", "safeguard_daylight": "a500000003e8",
    # GameDay
    "red_gold": "6b0c000003e8", "red_white": "6c0c000003e8", "red_blue": "6d0c000003e8",
    "red_silver": "6e0c000003e8", "blue_gold": "6f0c000003e8",
    "blue_white": "700c000003e8", "blue_silver": "710c000003e8",
    "blue_orange": "720c000003e8", "blue_yellow": "730c000003e8",
    "green_gold": "740c000003e8", "green_white": "750c000003e8",
    "green_blue": "760c000003e8", "green_silver": "770c000003e8",
    "green_orange": "780c000003e8", "green_yellow": "790c000003e8",
    "purple_gold": "7a0c000003e8", "purple_white": "7b0c000003e8",
    "purple_silver": "7c0c000003e8", "purple_yellow": "7d0c000003e8",
    "purple_teal": "7e0c000003e8", "orange_white": "7f0c000003e8",
}
# scene id (B0) -> name, for decoding device state back to a name.
SCENE_ID_TO_NAME = {int(p[0:2], 16): n for n, p in SCENE_PAYLOADS.items()}

# The app only offers Solid on the Every Day scenes (Welcome Home etc.), whose
# colors are positional.
SOLID_ONLY_SCENES = range(157, 166)

NUM_BULBS = 12

# The app's white presets: kelvin -> (W, R, G, B). Between them we interpolate.
WHITE_POINTS = {
    2200: (0xff, 0x00, 0x00, 0x00), 2700: (0x94, 0x00, 0x4a, 0x20),
    3000: (0x6b, 0x20, 0x65, 0x2b), 4000: (0x42, 0x14, 0x70, 0x36),
    5000: (0x31, 0x1e, 0x7d, 0x44), 6000: (0x21, 0x30, 0x80, 0x48),
}
KELVIN_MIN = min(WHITE_POINTS)
KELVIN_MAX = max(WHITE_POINTS)


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
    # Wire bytes for names the tables above don't know, so a learned scene or
    # effect re-encodes exactly as the device reported it.
    scene_id: int | None = None  # B0 when mode == scene
    scene_b1: int | None = None  # B1 when mode == scene
    effect_id: int | None = None # B1 when mode == color
    scene_colors: list[Color] = field(default_factory=list)  # DIY scenes only


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, int(v)))


# ---- encode ---------------------------------------------------------------

def encode_state(st: LightState) -> str:
    speed = _clamp(st.speed, 0, SCALE_MAX)
    bright = _clamp(st.brightness, 1, SCALE_MAX)
    if st.mode == "scene":
        if st.scene_id is not None:
            b0, b1 = st.scene_id, st.scene_b1 or 0
        elif st.scene in SCENE_PAYLOADS:
            raw = SCENE_PAYLOADS[st.scene]
            b0, b1 = int(raw[0:2], 16), int(raw[2:4], 16)
        else:
            raise ValueError(f"unknown scene: {st.scene!r}")
        return f"{b0:02x}{b1:02x}{speed:04x}{bright:04x}" + _encode_colors(st.scene_colors)
    b1 = st.effect_id if st.effect_id is not None else EFFECTS.get(st.effect, 0x00)
    return (f"{COLOR_MODE:02x}{b1:02x}{speed:04x}{bright:04x}"
            + _encode_colors(st.colors or [Color(w=255)]))


def _encode_colors(colors: list[Color]) -> str:
    """The color section: (count-1), then '#' before each color."""
    colors = colors[:6]
    if not colors:
        return ""
    return f"{len(colors) - 1}#" + "#".join(c.to_hex() for c in colors)


def decode_state(raw: str) -> LightState:
    parts = raw.split("#")
    header = parts[0]            # mode+effect+speed+bright+(count-1)
    b0 = int(header[0:2], 16)
    b1 = int(header[2:4], 16)
    if len(header) == 11:   # factory-fresh state: a one-byte speed
        speed, bright = int(header[4:6], 16), int(header[6:10], 16)
    else:
        speed, bright = int(header[4:8], 16), int(header[8:12], 16)
    # Colors are 8 hex each (W,R,G,B), normally '#'-separated, though the device
    # also accepts and reports them run together.
    body = "".join(parts[1:])
    colors = [Color.from_hex(body[i:i+8]) for i in range(0, len(body) - 7, 8)]
    if b0 in (COLOR_MODE, 0x00):
        return LightState(mode="color", effect=EFFECTS_INV.get(b1, f"fx_{b1:02x}"),
                          effect_id=b1, speed=speed, brightness=bright,
                          colors=colors or [Color(w=255)])
    return LightState(mode="scene", scene=SCENE_ID_TO_NAME.get(b0, f"scene_{b0:02x}"),
                      scene_id=b0, scene_b1=b1, speed=speed, brightness=bright,
                      scene_colors=colors)


# ---- white temperature ----------------------------------------------------

def white_to_color(kelvin: int) -> Color:
    """The app's white for a color temperature, interpolated between its presets."""
    k = _clamp(kelvin, KELVIN_MIN, KELVIN_MAX)
    points = sorted(WHITE_POINTS)
    hi = next(p for p in points if p >= k)
    lo = points[max(points.index(hi) - 1, 0)]
    t = (k - lo) / (hi - lo) if hi != lo else 0
    w, r, g, b = (round(a + (z - a) * t)
                  for a, z in zip(WHITE_POINTS[lo], WHITE_POINTS[hi]))
    return Color(r=r, g=g, b=b, w=w)


# ---- segment mask ---------------------------------------------------------

def encode_mask(on_bulbs: list[bool]) -> str:
    bits = on_bulbs + [True] * (NUM_BULBS - len(on_bulbs))
    return "".join("1" if b else "0" for b in bits)


def decode_mask(raw: str) -> list[bool]:
    return [c == "1" for c in raw]


# ---- HA brightness (0..255) <-> device (0..1000) --------------------------

def ha_to_dev_brightness(v: int) -> int:
    return _clamp(round(v / 255 * SCALE_MAX), 1, SCALE_MAX)


def dev_to_ha_brightness(v: int) -> int:
    return _clamp(round(v / SCALE_MAX * 255), 1, 255)
