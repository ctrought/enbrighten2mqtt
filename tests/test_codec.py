"""Codec tests against real dp106 samples captured from the device."""

import pytest

from enbrighten2mqtt.codec.eternity import (
    Color, LightState, encode_state, decode_state,
    encode_mask, decode_mask, EFFECTS, SCENE_PAYLOADS, white_to_color,
    ha_to_dev_brightness, dev_to_ha_brightness)

# (raw dp106, expected decoded fields) from live capture — see PROTOCOL.md
SAMPLES = [
    ("ff00006402da0#00ff0000", "color", "solid",   100, 730, [(255, 0, 0, 0)]),
    ("ff00006402da0#0000ff00", "color", "solid",   100, 730, [(0, 255, 0, 0)]),
    ("ff00006402da0#000000ff", "color", "solid",   100, 730, [(0, 0, 255, 0)]),
    ("ff00006402da0#ff000000", "color", "solid",   100, 730, [(0, 0, 0, 255)]),
    ("ff09006402da0#21308048", "color", "flame",   100, 730, [(0x30, 0x80, 0x48, 0x21)]),
    ("ff0a038e02da0#21308048", "color", "stacking", 910, 730, [(0x30, 0x80, 0x48, 0x21)]),
]


@pytest.mark.parametrize("raw,mode,effect,speed,bright,colors", SAMPLES)
def test_decode_matches_capture(raw, mode, effect, speed, bright, colors):
    st = decode_state(raw)
    assert st.mode == mode and st.effect == effect
    assert st.speed == speed and st.brightness == bright
    got = [(c.r, c.g, c.b, c.w) for c in st.colors]
    assert got == colors


@pytest.mark.parametrize("raw", [s[0] for s in SAMPLES])
def test_encode_decode_roundtrip(raw):
    assert encode_state(decode_state(raw)) == raw


def test_two_color_encode():
    st = LightState(mode="color", effect="solid", speed=500, brightness=1000,
                    colors=[Color(r=255), Color(b=255)])
    # 2 colors -> digit '1' before '#', W,R,G,B each
    assert encode_state(st) == "ff0001f403e81#00ff0000#000000ff"


def test_scene_encode_decode():
    st = decode_state("0900006403e8")
    assert st.mode == "scene" and st.scene == "clover"
    assert st.brightness == 1000 and st.speed == 100
    assert encode_state(st) == "0900006403e8"


def test_all_effects_known():
    assert len(EFFECTS) == 21 and EFFECTS["fade_melt"] == 0x16


def test_mask_roundtrip():
    m = "011111111110"
    bulbs = decode_mask(m)
    assert bulbs[0] is False and bulbs[-1] is False and bulbs[5] is True
    assert encode_mask(bulbs) == m


def test_brightness_scaling():
    assert ha_to_dev_brightness(255) == 1000
    assert dev_to_ha_brightness(1000) == 255
    assert 120 <= ha_to_dev_brightness(128) <= 520


def test_unknown_scene_round_trips():
    # Not in the table: must not turn into some other scene when re-encoded.
    st = decode_state("2a07012c03e8")
    st.brightness = 500
    assert encode_state(st) == "2a07012c01f4"


def test_scene_speed_is_applied():
    st = decode_state("0301012c03e8")
    st.speed = 800
    assert encode_state(st) == "0301032003e8"


def test_unknown_effect_round_trips():
    st = decode_state("ff0e006402da0#00ff0000")
    st.brightness = 500
    assert encode_state(st).startswith("ff0e006401f4")


def test_scene_with_colors_round_trips():
    # DIY scenes carry a color list after a scene id.
    raw = "120501f403e81#00ff0000#000000ff"
    st = decode_state(raw)
    assert st.mode == "scene" and st.scene_id == 0x12
    assert [(c.r, c.b) for c in st.scene_colors] == [(255, 0), (0, 255)]
    assert encode_state(st) == raw


def test_colors_without_separators_decode():
    # Captured live: three colors run together.
    st = decode_state("ff00012c03e80#00ff00000000ff00000000ff")
    assert [(c.r, c.g, c.b) for c in st.colors] == [(255, 0, 0), (0, 255, 0), (0, 0, 255)]


def test_unknown_named_scene_raises():
    with pytest.raises(ValueError):
        encode_state(LightState(mode="scene", scene="nope"))


def test_catalogue_matches_captures():
    # Payloads captured from hardware before the app's table was found.
    for name, raw in {"bubbly": "3301012c03e8", "mardi_gras": "060c01f403e8",
                      "love": "0701012c03e8", "rainbow": "0301012c03e8",
                      "clover": "0900006403e8"}.items():
        assert SCENE_PAYLOADS[name] == raw
    assert len(SCENE_PAYLOADS) == 58
    ids = [p[:2] for p in SCENE_PAYLOADS.values()]
    assert len(set(ids)) == len(ids) and "ff" not in ids


def test_white_points():
    assert white_to_color(2200) == Color(w=255)
    assert white_to_color(6000) == Color(r=0x30, g=0x80, b=0x48, w=0x21)   # captured
    assert white_to_color(1000) == Color(w=255)                            # clamps
    mid = white_to_color(2450)
    assert 0x94 < mid.w < 0xff and 0 < mid.g < 0x4a
