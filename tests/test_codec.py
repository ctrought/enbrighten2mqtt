"""Codec tests against real dp106 samples captured from the device."""

import pytest

from enbrighten2mqtt.codec.eternity import (
    Color, LightState, encode_state, decode_state,
    encode_mask, decode_mask, EFFECTS,
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
    assert len(EFFECTS) == 16 and EFFECTS["fade_melt"] == 0x16


def test_mask_roundtrip():
    m = "011111111110"
    bulbs = decode_mask(m)
    assert bulbs[0] is False and bulbs[-1] is False and bulbs[5] is True
    assert encode_mask(bulbs) == m


def test_brightness_scaling():
    assert ha_to_dev_brightness(255) == 1000
    assert dev_to_ha_brightness(1000) == 255
    assert 120 <= ha_to_dev_brightness(128) <= 520
