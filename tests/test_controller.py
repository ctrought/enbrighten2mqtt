"""Controller tests with a fake LAN device (no network)."""

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
