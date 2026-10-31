"""MQTT bridge: Home Assistant <-> Enbrighten Eternity (LAN).

paho-mqtt (its own network thread) plus one worker thread per device (tinytuya
is synchronous, so each device owns a thread that does all its LAN I/O and
drains a command queue). Device state changes are published back to MQTT.

HA entities per device (MQTT JSON-schema light + helpers), via discovery:
  - light: on/off, brightness, rgbw colour, effect_list = 16 effects + scenes
  - number: Speed (0-1000)
  - text command topic `.../set/advanced`: JSON for multi-colour, mask, scene
  - binary_sensor: connectivity
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time

import paho.mqtt.client as mqtt

from ..config import Config, DeviceConfig
from ..codec import eternity as codec
from ..codec.eternity import Color
from ..controller import Controller
from ..local.device import DeviceInfo

log = logging.getLogger("enbrighten2mqtt.bridge")

EFFECT_LIST = list(codec.EFFECTS.keys()) + [
    f"scene:{n}" for n in codec.SCENE_PAYLOADS]


class DeviceWorker(threading.Thread):
    """Owns one device's LAN connection and command queue."""

    def __init__(self, dcfg: DeviceConfig, publish_state):
        super().__init__(daemon=True, name=f"dev-{dcfg.dev_id[:8]}")
        self.dcfg = dcfg
        self.publish_state = publish_state
        self.cmds: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        info = DeviceInfo(dcfg.dev_id, dcfg.ip, dcfg.local_key, dcfg.version)
        self.ctrl = Controller(info, on_state=self._state_changed)
        self.online = False

    def submit(self, fn):
        self.cmds.put(fn)

    def stop(self):
        self._stop.set()

    def _state_changed(self, state):
        self.publish_state(self.dcfg, state, self.online)

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self.ctrl.connect()
                self.online = True
                backoff = 1.0
                self._state_changed(self.ctrl.state)
                log.info("device %s online (proto %s)",
                         self.dcfg.dev_id[:8], self.ctrl.info.version)
                last_refresh = time.time()
                while not self._stop.is_set():
                    # drain queued commands
                    try:
                        while True:
                            self.cmds.get_nowait()()
                    except queue.Empty:
                        pass
                    # pump pushes and periodically refresh
                    try:
                        self.ctrl.pump()
                    except Exception:
                        raise
                    if time.time() - last_refresh > 30:
                        self.ctrl.refresh()
                        last_refresh = time.time()
                    time.sleep(0.2)
            except Exception as e:  # noqa: BLE001
                self.online = False
                self._state_changed(self.ctrl.state)
                log.warning("device %s error: %s; reconnect in %.0fs",
                            self.dcfg.dev_id[:8], e, backoff)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 60)


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.workers: dict[str, DeviceWorker] = {}
        self.client = mqtt.Client(client_id="enbrighten2mqtt")
        if cfg.mqtt.username:
            self.client.username_pw_set(cfg.mqtt.username, cfg.mqtt.password)
        self.client.on_connect = self._on_connect
        self.client.on_message = self._on_message
        base = cfg.mqtt.base_topic
        self.client.will_set(f"{base}/bridge/availability", "offline", retain=True)

    # -- topics -------------------------------------------------------------

    def _t(self, dev_id, leaf):
        return f"{self.cfg.mqtt.base_topic}/{dev_id}/{leaf}"

    # -- MQTT callbacks -----------------------------------------------------

    def _on_connect(self, client, userdata, flags, rc):
        log.info("connected to MQTT (rc=%s)", rc)
        client.publish(f"{self.cfg.mqtt.base_topic}/bridge/availability",
                       "online", retain=True)
        for d in self.cfg.devices:
            self._publish_discovery(d)
            client.subscribe(self._t(d.dev_id, "set"))
            client.subscribe(self._t(d.dev_id, "set/advanced"))

    def _on_message(self, client, userdata, msg):
        parts = msg.topic.split("/")
        # base/<dev_id>/set[/advanced]
        try:
            dev_id = parts[1]
            worker = self.workers.get(dev_id)
            if not worker:
                return
            payload = json.loads(msg.payload.decode() or "{}")
        except Exception as e:  # noqa: BLE001
            log.warning("bad command on %s: %s", msg.topic, e)
            return
        if msg.topic.endswith("/set/advanced"):
            self._handle_advanced(worker, payload)
        else:
            self._handle_light(worker, payload)

    # -- command handling ---------------------------------------------------

    def _handle_light(self, worker: DeviceWorker, p: dict):
        c = worker.ctrl
        if "state" in p:
            worker.submit(lambda: c.set_power(str(p["state"]).upper() == "ON"))
        if "brightness" in p:  # HA 0-255
            worker.submit(lambda v=p["brightness"]:
                          c.set_brightness(codec.ha_to_dev_brightness(int(v))))
        if "color" in p and isinstance(p["color"], dict):
            col = p["color"]
            worker.submit(lambda col=col: c.set_rgbw(
                int(col.get("r", 0)), int(col.get("g", 0)),
                int(col.get("b", 0)), int(col.get("w", 0))))
        if "effect" in p:
            eff = p["effect"]
            name = eff.split("scene:", 1)[1] if str(eff).startswith("scene:") else eff
            worker.submit(lambda name=name: c.set_effect(name))

    def _handle_advanced(self, worker: DeviceWorker, p: dict):
        c = worker.ctrl
        if "speed" in p:
            worker.submit(lambda v=p["speed"]: c.set_speed(int(v)))
        if "mask" in p:  # list of 12 bools or "101010..." string
            m = p["mask"]
            bulbs = [ch == "1" for ch in m] if isinstance(m, str) else [bool(x) for x in m]
            worker.submit(lambda bulbs=bulbs: c.set_mask(bulbs))
        if "colors" in p:  # list of {r,g,b,w}
            cols = [Color(int(x.get("r", 0)), int(x.get("g", 0)),
                          int(x.get("b", 0)), int(x.get("w", 0))) for x in p["colors"]]
            worker.submit(lambda cols=cols: c.set_colors(cols))
        if "scene" in p:
            worker.submit(lambda s=p["scene"]: c.set_effect(s))
        if "countdown" in p:
            worker.submit(lambda v=p["countdown"]: c.set_countdown(int(v)))

    # -- state -> MQTT ------------------------------------------------------

    def _publish_state(self, dcfg: DeviceConfig, state, online: bool):
        ls = state.light
        first = (ls.colors or [Color(w=255)])[0]
        payload = {
            "state": "ON" if state.power else "OFF",
            "brightness": codec.dev_to_ha_brightness(ls.brightness),
            "color": {"r": first.r, "g": first.g, "b": first.b, "w": first.w},
            "color_mode": "rgbw",
            "effect": (f"scene:{ls.scene}" if ls.mode == "scene" else ls.effect),
        }
        self.client.publish(self._t(dcfg.dev_id, "state"), json.dumps(payload),
                            retain=True)
        self.client.publish(self._t(dcfg.dev_id, "availability"),
                            "online" if online else "offline", retain=True)
        self.client.publish(self._t(dcfg.dev_id, "attributes"), json.dumps({
            "speed": ls.speed, "mode": ls.mode,
            "mask": codec.encode_mask(state.mask),
            "colors": [{"r": c.r, "g": c.g, "b": c.b, "w": c.w} for c in ls.colors],
        }), retain=True)

    # -- discovery ----------------------------------------------------------

    def _publish_discovery(self, d: DeviceConfig):
        uid = f"enbrighten_{d.dev_id}"
        dev_block = {
            "identifiers": [uid], "manufacturer": "Enbrighten",
            "model": d.product_id or "Eternity", "name": d.name or "Enbrighten Light",
        }
        light = {
            "name": None, "unique_id": uid, "schema": "json",
            "state_topic": self._t(d.dev_id, "state"),
            "command_topic": self._t(d.dev_id, "set"),
            "availability_topic": self._t(d.dev_id, "availability"),
            "brightness": True, "supported_color_modes": ["rgbw"],
            "effect": True, "effect_list": EFFECT_LIST,
            "device": dev_block,
        }
        self.client.publish(
            f"{self.cfg.mqtt.discovery_prefix}/light/{uid}/config",
            json.dumps(light), retain=True)

    # -- run ----------------------------------------------------------------

    def start(self):
        for d in self.cfg.devices:
            w = DeviceWorker(d, self._publish_state)
            self.workers[d.dev_id] = w
            w.start()
        self.client.connect(self.cfg.mqtt.host, self.cfg.mqtt.port, 60)
        self.client.loop_forever()

    def stop(self):
        for w in self.workers.values():
            w.stop()
        self.client.disconnect()


def run(config_path: str = "config.yaml") -> None:
    cfg = Config.load(config_path)
    logging.getLogger().setLevel(cfg.log_level)
    if not cfg.devices:
        raise SystemExit("no devices configured; run `login` or set config.yaml")
    Bridge(cfg).start()
