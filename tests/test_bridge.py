"""Bridge tests: discovery payloads and command routing, with no broker or LAN."""

import json

import pytest

from enbrighten2mqtt.config import Config, DeviceConfig, MqttConfig
from enbrighten2mqtt.codec.eternity import Color, decode_mask
from enbrighten2mqtt.controller import DeviceState
from enbrighten2mqtt.mqtt import bridge as B

DEV = DeviceConfig(dev_id="devabc", ip="1.2.3.4", local_key="k",
                   name="Eternity Landscape Lights  2 ", product_id="wagmuetuwhn4lgr7")


class FakeClient:
    def __init__(self):
        self.published = {}
        self.subscribed = []

    def publish(self, topic, payload=None, retain=False):
        self.published[topic] = payload

    def subscribe(self, topic):
        self.subscribed.append(topic)

    def is_connected(self):
        return True


class FakeCtrl:
    def __init__(self):
        self.state = DeviceState()
        self.calls = []

    def apply(self, **kw):
        self.calls.append(kw)

    def save_look(self, name):
        self.calls.append({"save_look": name})

    def refresh(self):
        self.calls.append({"refresh": True})


class FakeWorker:
    online = True
    preset_name = ""

    def __init__(self):
        self.ctrl = FakeCtrl()

    def submit(self, fn):
        fn()


@pytest.fixture
def bridge(tmp_path):
    cfg = Config(mqtt=MqttConfig(base_topic="home/lights"), devices=[DEV])
    cfg.enbrighten.devices_file = str(tmp_path / "devices.json")
    b = B.Bridge(cfg)
    b.client = FakeClient()
    b.workers["devabc"] = FakeWorker()
    return b


def calls(b):
    return b.workers["devabc"].ctrl.calls


def test_device_block_is_friendly(bridge):
    block = bridge._device_block(DEV)
    assert block["name"] == "Eternity Landscape Lights 2"
    assert block["model"] == "Eternity Landscape Lights"
    assert block["model_id"] == "wagmuetuwhn4lgr7"


def test_all_entities_discovered(bridge):
    p = bridge._discovery_payloads(DEV)
    kinds = {k: v[0] for k, v in p.items()}
    assert kinds[""] == "light"
    assert kinds["speed"] == "number" and kinds["timer"] == "number"
    assert kinds["effect"] == "select" and kinds["scene"] == "select"
    assert kinds["colors"] == "text" and kinds["save_preset"] == "button"
    assert kinds["reverse"] == "switch" and kinds["connectivity"] == "binary_sensor"
    assert sum(k.startswith("bulb_") for k in kinds) == 12
    uids = [v[1]["unique_id"] for v in p.values()]
    assert len(set(uids)) == len(uids)
    assert p[""][1]["unique_id"] == "enbrighten_devabc"     # existing entity kept


def test_light_lists_scenes_and_uses_both_availabilities(bridge):
    conf = bridge._discovery_payloads(DEV)[""][1]
    assert "flame" in conf["effect_list"] and "scene:christmas" in conf["effect_list"]
    assert conf["availability_mode"] == "all"
    assert {a["topic"] for a in conf["availability"]} == {
        "home/lights/bridge/availability", "home/lights/devabc/availability"}
    assert set(conf["supported_color_modes"]) == {"color_temp", "rgbw"}


def test_connectivity_outlives_device_offline(bridge):
    conf = bridge._discovery_payloads(DEV)["connectivity"][1]
    assert conf["availability"] == [{"topic": "home/lights/bridge/availability"}]


def test_learned_scene_republishes_discovery(bridge):
    bridge.scenes.scene_for("2a07012c03e8")
    conf = json.loads(bridge.client.published[
        "homeassistant/select/enbrighten_devabc_scene/config"])
    assert "scene_2a" in conf["options"]
    assert {"refresh": True} in calls(bridge)


def test_light_command_is_one_apply(bridge):
    bridge._route("home/lights/devabc/set", json.dumps(
        {"state": "ON", "brightness": 255, "color": {"r": 1, "g": 2, "b": 3, "w": 4},
         "effect": "scene:rainbow"}))
    assert calls(bridge) == [{"power": True, "brightness": 1000,
                              "colors": [Color(1, 2, 3, 4)], "effect": "rainbow"}]


def test_color_temp_command(bridge):
    bridge._route("home/lights/devabc/set", json.dumps({"color_temp": 250}))
    assert calls(bridge) == [{"kelvin": 4000}]


@pytest.mark.parametrize("leaf,payload,want", [
    ("speed", "30", {"speed": 300}),
    ("effect", "flame", {"effect": "flame"}),
    ("scene", "christmas", {"effect": "christmas"}),
    ("timer", "2", {"countdown": 120}),
    ("reverse", "ON", {"reverse": True}),
    ("colors", "#ff0000,#0000ff80", {"colors": [Color(r=255), Color(b=255, w=0x80)]}),
])
def test_entity_commands(bridge, leaf, payload, want):
    bridge._route(f"home/lights/devabc/set/{leaf}", payload)
    assert calls(bridge) == [want]


def test_bulb_switch_patches_current_mask(bridge):
    ctrl = bridge.workers["devabc"].ctrl
    ctrl.state.mask = decode_mask("011111111111")
    bridge._route("home/lights/devabc/set/bulb/3", "OFF")
    assert calls(bridge)[0]["mask"] == decode_mask("010111111111")


def test_bad_bulb_rejected(bridge):
    with pytest.raises(ValueError):
        bridge._route("home/lights/devabc/set/bulb/13", "ON")


def test_save_preset_uses_name(bridge):
    bridge._route("home/lights/devabc/set/preset_name", " Porch ")
    bridge._route("home/lights/devabc/set/save_preset", "PRESS")
    assert calls(bridge) == [{"save_look": "Porch"}]


def test_save_preset_without_name_generates_one(bridge):
    bridge._route("home/lights/devabc/set/save_preset", "PRESS")
    assert calls(bridge) == [{"save_look": "Look 1"}]


def test_bad_json_doesnt_escape_callback(bridge):
    class Msg:
        topic = "home/lights/devabc/set"
        payload = b"{nope"
    bridge._on_message(None, None, Msg)     # must not raise into paho
    assert calls(bridge) == []


def test_ha_restart_republishes(bridge):
    bridge._route("homeassistant/status", "online")
    assert "homeassistant/light/enbrighten_devabc/config" in bridge.client.published
    assert "home/lights/devabc/state" in bridge.client.published


def test_state_in_scene_mode(bridge):
    st = DeviceState()
    st.light.mode, st.light.scene, st.light.scene_b1 = "scene", "rainbow", 0x09
    bridge._publish_state(DEV, st)
    state = json.loads(bridge.client.published["home/lights/devabc/state"])
    attrs = json.loads(bridge.client.published["home/lights/devabc/attributes"])
    assert state["effect"] == "scene:rainbow"
    assert attrs["scene_select"] == "rainbow" and attrs["effect_select"] == "flame"


def test_scene_with_internal_effect_reports_none(bridge):
    st = DeviceState()
    st.light.mode, st.light.scene, st.light.scene_b1 = "scene", "halloween", 0x0f
    bridge._publish_state(DEV, st)
    attrs = json.loads(bridge.client.published["home/lights/devabc/attributes"])
    assert attrs["effect_select"] == "None" and "fx_0f" not in bridge.scenes.effect_names()


def test_state_reports_color_temp(bridge):
    st = DeviceState()
    st.kelvin = 4000
    bridge._publish_state(DEV, st)
    state = json.loads(bridge.client.published["home/lights/devabc/state"])
    assert state["color_mode"] == "color_temp" and state["color_temp"] == 250
    assert "color" not in state


def test_timer_rounds_up(bridge):
    st = DeviceState()
    st.countdown = 61
    bridge._publish_state(DEV, st)
    attrs = json.loads(bridge.client.published["home/lights/devabc/attributes"])
    assert attrs["timer_min"] == 2


def test_colors_text_round_trips():
    cols = [Color(r=255), Color(b=255, w=0x80)]
    assert B.parse_colors(B.format_colors(cols)) == cols


@pytest.mark.parametrize("bad", ["", "#12", ",".join(["#000000"] * 7)])
def test_bad_colors_rejected(bad):
    with pytest.raises(ValueError):
        B.parse_colors(bad)
