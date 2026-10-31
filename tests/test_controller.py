"""Controller tests with a fake LAN device (no network)."""

import pytest

from enbrighten2mqtt.controller import Controller
from enbrighten2mqtt.codec.eternity import Color
from enbrighten2mqtt.local.device import DeviceInfo


class FakeDev:
    def __init__(self, *a, **k):
        self.dps = {}
        self.on_dps = k.get("on_dps")

    def connect(self): pass
    def status(self): return {"dps": {"20": True, "106": "0212019003e8",
                                       "104": "111111111111"}}
    def set_dp(self, dp, v): self.dps[dp] = v
    def set_dps(self, dps): self.dps.update(dps)
    def receive(self): return None


def make_ctrl():
    c = Controller(DeviceInfo("id", "1.2.3.4", "key", 3.5))
    c.dev = FakeDev()
    return c


def test_brightness_preserves_color():
    c = make_ctrl()
    c.set_rgbw(0, 0, 255, 0)          # blue
    c.set_brightness(500)             # dim
    val = c.dev.dps[106]
    # still blue, now at brightness 500 (0x01f4)
    assert val.endswith("#000000ff") and "01f4" in val


def test_effect_keeps_colors():
    c = make_ctrl()
    c.set_rgbw(255, 0, 0, 0)
    c.set_effect("flame")
    assert c.dev.dps[106].startswith("ff09") and c.dev.dps[106].endswith("#00ff0000")


def test_scene_recall_faithful():
    c = make_ctrl()
    c.set_effect("rainbow")
    assert c.dev.dps[106] == "0301012c03e8"


def test_power_and_mask():
    c = make_ctrl()
    c.set_power(False)
    assert c.dev.dps[20] is False
    c.set_mask([False] + [True] * 11)
    assert c.dev.dps[104] == "011111111111"


def test_incoming_dps_update_state():
    seen = []
    c = Controller(DeviceInfo("id", "1.2.3.4", "key", 3.5), on_state=seen.append)
    c.dev = FakeDev()
    c._apply_dps({"20": False, "106": "ff00012c03e80#0000ff00"})
    assert c.state.power is False
    assert c.state.light.effect == "solid"
    assert c.state.light.colors[0].g == 255
    assert seen  # callback fired


class CountingDev(FakeDev):
    def __init__(self):
        super().__init__()
        self.writes = []

    def set_dps(self, dps):
        self.writes.append(dict(dps))
        super().set_dps(dps)


def make_counting():
    c = make_ctrl()
    c.dev = CountingDev()
    return c


def test_one_command_is_one_write():
    c = make_counting()
    c.apply(power=True, brightness=500, colors=[Color(r=255)], effect="flame")
    assert len(c.dev.writes) == 1
    w = c.dev.writes[0]
    assert w[20] is True and w[106] == "ff09006401f40#00ff0000"


def test_brightness_on_learned_scene_keeps_it():
    c = make_counting()
    c._apply_dps({"106": "2a07012c03e8"})
    assert c.state.light.scene == "scene_2a"
    c.set_brightness(500)
    assert c.dev.dps[106] == "2a07012c01f4"
    assert "scene_2a" in c.scenes.scene_names()


def test_unknown_effect_is_learned():
    c = make_counting()
    c._apply_dps({"106": "ff0e006402da0#00ff0000"})
    assert c.state.light.effect == "fx_0e"
    c.set_effect("solid")
    c.set_effect("fx_0e")
    assert c.dev.dps[106].startswith("ff0e")


def test_scene_speed_changes_and_recall_resets_it():
    c = make_counting()
    c.set_effect("rainbow")
    c.set_speed(800)
    assert c.dev.dps[106] == "0301032003e8"
    c.set_effect("rainbow")
    assert c.dev.dps[106] == "0301012c03e8"


def test_palette_survives_a_scene():
    c = make_counting()
    c.set_rgbw(0, 0, 255)
    c.set_effect("rainbow")
    c._apply_dps({"106": "0301012c03e8"})   # the device echoes it back
    c.set_effect("flame")
    assert c.dev.dps[106].startswith("ff09") and c.dev.dps[106].endswith("#000000ff")


def test_unknown_name_writes_nothing():
    c = make_counting()
    c.set_effect("nope")
    assert c.dev.writes == []


def test_white_temperature_tracked_until_color_changes():
    c = make_counting()
    c.apply(kelvin=6000)
    assert c.dev.dps[106].endswith("#21308048") and c.state.kelvin == 6000
    c.set_brightness(500)
    assert c.state.kelvin == 6000
    c.set_rgbw(255, 0, 0)
    assert c.state.kelvin is None


def test_countdown_and_reverse_state():
    c = make_counting()
    c._apply_dps({"26": 120, "101": True})
    assert c.state.countdown == 120 and c.state.reverse is True
    c.apply(reverse=False, countdown=0)
    assert c.dev.dps[101] is False and c.dev.dps[26] == 0 and c.state.reverse is False


def test_saved_look_recall_restores_mask():
    c = make_counting()
    c.set_rgbw(0, 255, 0)
    c.set_mask([False] * 2 + [True] * 10)
    c.save_look("Porch")
    want = c.state.raw
    c.set_rgbw(255, 0, 0)
    c.set_mask([True] * 12)
    c.set_effect("Porch")
    assert c.dev.dps[106] == want and c.dev.dps[104] == "001111111111"


def test_failed_write_propagates():
    c = make_ctrl()

    class Broken(FakeDev):
        def set_dps(self, dps):
            raise RuntimeError("boom")
    c.dev = Broken()
    with pytest.raises(RuntimeError):
        c.set_power(False)
