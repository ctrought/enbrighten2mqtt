"""MQTT bridge: Home Assistant <-> Enbrighten Eternity (LAN).

paho-mqtt (its own network thread) plus one worker thread per device (tinytuya
is synchronous, so each device owns a thread that does all its LAN I/O and
drains a command queue). Device state changes are published back to MQTT.

HA entities per device, via MQTT discovery:
  - light: on/off, brightness, rgbw colour, white temperature, effect_list =
    effects + scenes + saved looks
  - number: Speed (%), Auto-off timer (minutes)
  - select: Effect, Scene
  - switch: Bulb 1-12, Reverse effect direction
  - text: Colors (1-6 colour sequence), Preset name
  - button: Save preset
  - binary_sensor: Connectivity
JSON command topic `.../set/advanced` remains for automations.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
from importlib import metadata
from pathlib import Path

import paho.mqtt.client as mqtt

from ..config import Config, DeviceConfig, devices_from_json
from ..codec import eternity as codec
from ..codec.eternity import Color
from ..controller import Controller
from ..local.device import DeviceInfo
from ..local.discovery import find_device_ip
from ..scenes import SceneStore, custom_modes

log = logging.getLogger("enbrighten2mqtt.bridge")

MODEL_NAMES = {"wagmuetuwhn4lgr7": "Eternity Landscape Lights"}

_HEX_COLOR = "#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?"
COLORS_PATTERN = rf"^{_HEX_COLOR}(?:, *{_HEX_COLOR}){{0,5}}$"

MIRED_MIN = round(1_000_000 / codec.KELVIN_MAX)
MIRED_MAX = round(1_000_000 / codec.KELVIN_MIN)


def format_colors(colors: list[Color]) -> str:
    """Colour list as `#RRGGBB`, with a trailing `WW` when white is in use."""
    return ",".join(
        f"#{c.r:02x}{c.g:02x}{c.b:02x}" + (f"{c.w:02x}" if c.w else "")
        for c in colors[:6])


def parse_colors(text: str) -> list[Color]:
    out = []
    for item in text.split(","):
        h = item.strip().lstrip("#")
        if len(h) not in (6, 8):
            raise ValueError(f"bad colour: {item!r}")
        out.append(Color(r=int(h[0:2], 16), g=int(h[2:4], 16), b=int(h[4:6], 16),
                         w=int(h[6:8], 16) if len(h) == 8 else 0))
    if not 1 <= len(out) <= 6:
        raise ValueError("need 1 to 6 colours")
    return out


class DeviceWorker(threading.Thread):
    """Owns one device's LAN connection and command queue."""

    def __init__(self, dcfg: DeviceConfig, publish_state, publish_online,
                 scenes: SceneStore | None = None):
        super().__init__(daemon=True, name=f"dev-{dcfg.dev_id[:8]}")
        self.dcfg = dcfg
        self.publish_state = publish_state
        self.publish_online = publish_online
        self.cmds: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        info = DeviceInfo(dcfg.dev_id, dcfg.ip, dcfg.local_key, dcfg.version)
        self.ctrl = Controller(info, on_state=self._state_changed, scenes=scenes)
        # An IP pinned in config is kept across failures; a discovered one is
        # dropped on failure so we re-discover (device IPs can change on DHCP).
        self._ip_from_config = bool(dcfg.ip)
        self.online = False
        self.preset_name = ""

    def submit(self, fn):
        self.cmds.put(fn)

    def stop(self):
        self._stop.set()

    def _state_changed(self, state):
        self.publish_state(self.dcfg, state)

    def _set_online(self, online: bool):
        self.online = online
        self.publish_online(self.dcfg, online)

    def _ensure_ip(self) -> bool:
        """Make sure we have a LAN IP, discovering one by broadcast if not."""
        if self.ctrl.info.ip:
            return True
        ip, ver = find_device_ip(self.dcfg.dev_id)
        if not ip:
            log.warning("device %s not found on LAN (UDP broadcast); is the "
                        "bridge on the same network?", self.dcfg.dev_id[:8])
            return False
        self.ctrl.info.ip = ip
        if ver and not self.ctrl.info.version:
            self.ctrl.info.version = ver
        log.info("discovered %s at %s (proto %s)", self.dcfg.dev_id[:8], ip, ver)
        return True

    def _run_command(self, fn):
        try:
            fn()
        except ValueError as e:     # a bad command, not a bad connection
            log.warning("rejected command for %s: %s", self.dcfg.dev_id[:8], e)

    def run(self):
        backoff = 1.0
        while not self._stop.is_set():
            try:
                if not self._ensure_ip():
                    if self._stop.wait(min(backoff * 5, 60)):
                        break
                    backoff = min(backoff * 2, 60)
                    continue
                self.ctrl.connect()
                self._set_online(True)
                backoff = 1.0
                self._state_changed(self.ctrl.state)
                log.info("device %s online (proto %s)",
                         self.dcfg.dev_id[:8], self.ctrl.info.version)
                last_refresh = time.time()
                while not self._stop.is_set():
                    # drain queued commands
                    try:
                        while True:
                            self._run_command(self.cmds.get_nowait())
                    except queue.Empty:
                        pass
                    # pump pushes and periodically refresh
                    self.ctrl.pump()
                    if time.time() - last_refresh > 30:
                        self.ctrl.refresh()
                        last_refresh = time.time()
                    time.sleep(0.02)
            except Exception as e:  # noqa: BLE001
                self._set_online(False)
                if not self._ip_from_config:
                    self.ctrl.info.ip = ""  # re-discover on the next attempt
                log.warning("device %s error: %s; reconnect in %.0fs",
                            self.dcfg.dev_id[:8], e, backoff)
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 60)


class Bridge:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.workers: dict[str, DeviceWorker] = {}
        self.scenes = SceneStore(
            scenes_path(cfg.enbrighten.devices_file),
            on_change=self._scenes_changed)
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
        client.subscribe(f"{self.cfg.mqtt.discovery_prefix}/status")
        for d in self.cfg.devices:
            self._publish_discovery(d)
            client.subscribe(self._t(d.dev_id, "set/#"))    # also matches `set`

    def _on_message(self, client, userdata, msg):
        try:
            self._route(msg.topic, msg.payload.decode())
        except Exception as e:  # noqa: BLE001
            log.warning("bad command on %s: %s", msg.topic, e)

    def _route(self, topic: str, payload: str):
        if topic == f"{self.cfg.mqtt.discovery_prefix}/status":
            if payload == "online":     # HA restarted: announce ourselves again
                self._republish_all()
            return
        # <base_topic>/<dev_id>/set[/<leaf>]; the base topic may have levels
        dev_id, _, leaf = topic[len(self.cfg.mqtt.base_topic) + 1:].partition("/")
        worker = self.workers.get(dev_id)
        if not worker:
            return
        if leaf == "set":
            self._handle_light(worker, json.loads(payload or "{}"))
        elif leaf == "set/advanced":
            self._handle_advanced(worker, json.loads(payload or "{}"))
        else:
            self._handle_entity(worker, leaf.removeprefix("set/"), payload)

    # -- command handling ---------------------------------------------------

    def _handle_light(self, worker: DeviceWorker, p: dict):
        kw = {}
        if "state" in p:
            kw["power"] = str(p["state"]).upper() == "ON"
        if "brightness" in p:  # HA 0-255
            kw["brightness"] = codec.ha_to_dev_brightness(int(p["brightness"]))
        if isinstance(p.get("color"), dict):
            col = p["color"]
            kw["colors"] = [Color(int(col.get("r", 0)), int(col.get("g", 0)),
                                  int(col.get("b", 0)), int(col.get("w", 0)))]
        if "color_temp" in p:  # HA mireds
            kw["kelvin"] = round(1_000_000 / int(p["color_temp"]))
        if "effect" in p:
            kw["effect"] = str(p["effect"]).removeprefix("scene:")
        worker.submit(lambda: worker.ctrl.apply(**kw))

    def _handle_advanced(self, worker: DeviceWorker, p: dict):
        c = worker.ctrl
        kw = {}
        if "speed" in p:
            kw["speed"] = int(p["speed"])
        if "mask" in p:  # list of 12 bools or "101010..." string
            m = p["mask"]
            kw["mask"] = [ch == "1" for ch in m] if isinstance(m, str) else [bool(x) for x in m]
        if "colors" in p:  # list of {r,g,b,w}
            kw["colors"] = [Color(int(x.get("r", 0)), int(x.get("g", 0)),
                                  int(x.get("b", 0)), int(x.get("w", 0))) for x in p["colors"]]
        if "scene" in p:
            kw["effect"] = str(p["scene"])
        if "countdown" in p:
            kw["countdown"] = int(p["countdown"])
        if kw:
            worker.submit(lambda: c.apply(**kw))
        if "rename_scene" in p:
            self.scenes.rename(p["rename_scene"]["from"], p["rename_scene"]["to"])
        if "delete_scene" in p:
            self.scenes.delete(p["delete_scene"])

    def _handle_entity(self, worker: DeviceWorker, leaf: str, payload: str):
        """Plain-payload command topics behind the helper entities."""
        c = worker.ctrl
        if leaf == "speed":
            pct = float(payload)
            worker.submit(lambda: c.apply(speed=round(pct * 10)))
        elif leaf in ("effect", "scene"):
            worker.submit(lambda: c.apply(effect=payload))
        elif leaf == "colors":
            cols = parse_colors(payload)
            worker.submit(lambda: c.apply(colors=cols))
        elif leaf == "timer":
            minutes = float(payload)
            worker.submit(lambda: c.apply(countdown=round(minutes * 60)))
        elif leaf.startswith("bulb/"):
            n = int(leaf.removeprefix("bulb/"))
            if not 1 <= n <= codec.NUM_BULBS:
                raise ValueError(f"no bulb {n}")
            on = payload == "ON"

            def set_bulb():     # read the mask when it runs, not when queued
                mask = list(c.state.mask)
                mask[n - 1] = on
                c.apply(mask=mask)
            worker.submit(set_bulb)
        elif leaf == "reverse":
            on = payload == "ON"
            worker.submit(lambda: c.apply(reverse=on))
        elif leaf == "preset_name":
            worker.preset_name = payload.strip()
        elif leaf == "save_preset":
            name = worker.preset_name or self._next_look_name()
            worker.submit(lambda: c.save_look(name))
        else:
            log.debug("unhandled command leaf %s", leaf)

    def _next_look_name(self) -> str:
        taken = set(self.scenes.scene_names())
        return next(f"Look {n}" for n in range(1, 1000) if f"Look {n}" not in taken)

    def _scenes_changed(self):
        """A scene or effect list changed: update HA's options and names."""
        if not self.client.is_connected():
            return
        for d in self.cfg.devices:
            self._publish_discovery(d)
        for w in self.workers.values():
            if w.online:
                w.submit(w.ctrl.refresh)

    def _republish_all(self):
        for d in self.cfg.devices:
            self._publish_discovery(d)
            w = self.workers.get(d.dev_id)
            if w and w.online:
                self._publish_state(d, w.ctrl.state)

    # -- state -> MQTT ------------------------------------------------------

    def _publish_online(self, dcfg: DeviceConfig, online: bool):
        self.client.publish(self._t(dcfg.dev_id, "availability"),
                            "online" if online else "offline", retain=True)

    def _publish_state(self, dcfg: DeviceConfig, state):
        ls = state.light
        first = (ls.colors or [Color(w=255)])[0]
        look = self.scenes.look_matching(state.raw)
        scene = look or (ls.scene if ls.mode == "scene" else None)
        payload = {
            "state": "ON" if state.power else "OFF",
            "brightness": codec.dev_to_ha_brightness(ls.brightness),
            "color": {"r": first.r, "g": first.g, "b": first.b, "w": first.w},
            "color_mode": "rgbw",
            "effect": f"scene:{scene}" if scene else ls.effect,
        }
        if state.kelvin:
            payload["color_mode"] = "color_temp"
            payload["color_temp"] = round(1_000_000 / state.kelvin)
            del payload["color"]
        self.client.publish(self._t(dcfg.dev_id, "state"), json.dumps(payload),
                            retain=True)
        self.client.publish(self._t(dcfg.dev_id, "attributes"), json.dumps({
            "speed": ls.speed, "speed_pct": round(ls.speed / 10), "mode": ls.mode,
            "mask": codec.encode_mask(state.mask),
            "bulbs": ["ON" if b else "OFF" for b in state.mask],
            "colors": [{"r": c.r, "g": c.g, "b": c.b, "w": c.w} for c in ls.colors],
            "colors_text": format_colors(ls.colors),
            # "None" is how an MQTT select reports that no option applies.
            "effect_select": "None" if scene else ls.effect,
            "scene_select": scene or "None",
            "countdown": state.countdown,
            "timer_min": -(-state.countdown // 60),
            "reverse": "ON" if state.reverse else "OFF",
            "dp106": state.raw,
        }), retain=True)

    # -- discovery ----------------------------------------------------------

    def _device_block(self, d: DeviceConfig) -> dict:
        block = {
            "identifiers": [f"enbrighten_{d.dev_id}"], "manufacturer": "Enbrighten",
            "model": MODEL_NAMES.get(d.product_id, d.product_id or "Eternity"),
            "name": " ".join(d.name.split()) or "Enbrighten Light",
        }
        if d.product_id:
            block["model_id"] = d.product_id
        if version := _version():
            block["sw_version"] = version
        return block

    def _discovery_payloads(self, d: DeviceConfig) -> dict[str, tuple[str, dict]]:
        """Every entity for a device: {object id suffix: (component, config)}."""
        attrs = self._t(d.dev_id, "attributes")
        bridge_avail = {"topic": f"{self.cfg.mqtt.base_topic}/bridge/availability"}
        scenes = self.scenes.scene_names()
        effects = self.scenes.effect_names()

        def cmd(leaf):
            return self._t(d.dev_id, f"set/{leaf}")

        entities: dict[str, tuple[str, dict]] = {
            "": ("light", {
                "name": None, "schema": "json",
                "state_topic": self._t(d.dev_id, "state"),
                "command_topic": self._t(d.dev_id, "set"),
                "json_attributes_topic": attrs,
                "brightness": True,
                "supported_color_modes": ["color_temp", "rgbw"],
                "min_mireds": MIRED_MIN, "max_mireds": MIRED_MAX,
                "effect": True,
                "effect_list": effects + [f"scene:{n}" for n in scenes],
            }),
            "speed": ("number", {
                "name": "Speed", "icon": "mdi:speedometer", "mode": "slider",
                "min": 0, "max": 100, "step": 1, "unit_of_measurement": "%",
                "state_topic": attrs, "value_template": "{{ value_json.speed_pct }}",
                "command_topic": cmd("speed"),
            }),
            "effect": ("select", {
                "name": "Effect", "icon": "mdi:auto-fix", "options": effects,
                "state_topic": attrs, "value_template": "{{ value_json.effect_select }}",
                "command_topic": cmd("effect"),
            }),
            "scene": ("select", {
                "name": "Scene", "icon": "mdi:palette", "options": scenes,
                "state_topic": attrs, "value_template": "{{ value_json.scene_select }}",
                "command_topic": cmd("scene"),
            }),
            "colors": ("text", {
                "name": "Colors", "icon": "mdi:palette-swatch",
                "pattern": COLORS_PATTERN, "min": 7, "max": 64,
                "state_topic": attrs, "value_template": "{{ value_json.colors_text }}",
                "command_topic": cmd("colors"),
            }),
            "timer": ("number", {
                "name": "Auto-off timer", "icon": "mdi:timer-outline", "mode": "box",
                "min": 0, "max": 1440, "step": 1, "unit_of_measurement": "min",
                "state_topic": attrs, "value_template": "{{ value_json.timer_min }}",
                "command_topic": cmd("timer"),
            }),
            "preset_name": ("text", {
                "name": "Preset name", "icon": "mdi:form-textbox",
                "entity_category": "config", "min": 0, "max": 64,
                # Retained, as the bridge keeps no copy across restarts.
                "command_topic": cmd("preset_name"), "retain": True,
            }),
            "save_preset": ("button", {
                "name": "Save preset", "icon": "mdi:content-save",
                "entity_category": "config",
                "command_topic": cmd("save_preset"),
            }),
            "reverse": ("switch", {
                "name": "Reverse effect direction", "icon": "mdi:swap-horizontal",
                "entity_category": "config",
                "state_topic": attrs, "value_template": "{{ value_json.reverse }}",
                "command_topic": cmd("reverse"),
            }),
        }
        for n in range(1, codec.NUM_BULBS + 1):
            entities[f"bulb_{n}"] = ("switch", {
                "name": f"Bulb {n}", "icon": "mdi:lightbulb-outline",
                "state_topic": attrs,
                "value_template": f"{{{{ value_json.bulbs[{n - 1}] }}}}",
                "command_topic": cmd(f"bulb/{n}"),
            })

        device_avail = {"topic": self._t(d.dev_id, "availability")}
        out = {}
        for suffix, (component, conf) in entities.items():
            uid = f"enbrighten_{d.dev_id}" + (f"_{suffix}" if suffix else "")
            out[suffix] = (component, {
                **conf, "unique_id": uid, "device": self._device_block(d),
                "availability": [bridge_avail, device_avail],
                "availability_mode": "all",
            })
        # Connectivity reports the device being offline, so it can't itself go
        # unavailable with the device; it depends on the bridge alone.
        uid = f"enbrighten_{d.dev_id}_connectivity"
        out["connectivity"] = ("binary_sensor", {
            "name": "Connectivity", "unique_id": uid,
            "device_class": "connectivity", "entity_category": "diagnostic",
            "state_topic": self._t(d.dev_id, "availability"),
            "payload_on": "online", "payload_off": "offline",
            "availability": [bridge_avail], "device": self._device_block(d),
        })
        return out

    def _publish_discovery(self, d: DeviceConfig):
        for component, conf in self._discovery_payloads(d).values():
            self.client.publish(
                f"{self.cfg.mqtt.discovery_prefix}/{component}/{conf['unique_id']}/config",
                json.dumps(conf), retain=True)
        worker = self.workers.get(d.dev_id)
        self._publish_online(d, bool(worker and worker.online))

    # -- run ----------------------------------------------------------------

    def start(self):
        for d in self.cfg.devices:
            w = DeviceWorker(d, self._publish_state, self._publish_online, self.scenes)
            self.workers[d.dev_id] = w
            w.start()
        self.client.connect(self.cfg.mqtt.host, self.cfg.mqtt.port, 60)
        self.client.loop_forever()

    def stop(self):
        for w in self.workers.values():
            w.stop()
        self.client.disconnect()


def _version() -> str | None:
    try:
        return metadata.version("enbrighten2mqtt")
    except metadata.PackageNotFoundError:
        return None


def scenes_path(devices_file: str) -> str:
    return str(Path(devices_file).with_name("scenes.json"))


def import_custom_scenes(api, devices, path: str) -> list[str]:
    """Bring the app's DIY scenes (kept in its cloud storage) into the store."""
    modes: dict[int, dict] = {}
    for d in devices:
        for rec in custom_modes(api.device_properties(d.dev_id)):
            modes[rec["mode"]] = rec
    return SceneStore(path).import_scenes(list(modes.values()))


def _login_and_cache(cfg: Config) -> None:
    from ..cloud.tuya_oem import TuyaOemApi, save_devices
    eb = cfg.enbrighten
    log.info("logging in to Enbrighten to fetch device keys...")
    creds = json.loads(Path(eb.oem_creds).read_text())
    api = TuyaOemApi(creds)
    api.login(eb.email, eb.password)
    devices = api.devices()
    save_devices(devices, eb.devices_file)
    log.info("fetched %d device(s) -> %s", len(devices), eb.devices_file)
    try:
        names = import_custom_scenes(api, devices, scenes_path(eb.devices_file))
        log.info("imported %d DIY/edited scene(s) from the app", len(names))
    except Exception as e:  # noqa: BLE001
        log.warning("couldn't import DIY scenes from the app: %s", e)


def bootstrap_devices(cfg: Config) -> list[DeviceConfig]:
    """Resolve the devices to serve.

    Order: an explicit `devices:` config list (handled by the caller) wins; then
    a cached devices.json; and when that cache is missing or stale AND account
    credentials are configured, log into Enbrighten automatically to (re)build
    it. This lets the container/add-on self-provision from just email+password.
    """
    eb = cfg.enbrighten
    have_cache = Path(eb.devices_file).exists()
    want_login = eb.refresh == "always" or (not have_cache and eb.refresh != "never")
    if want_login and eb.email and eb.password:
        if not Path(eb.oem_creds).exists():
            log.warning("auto-login skipped: OEM creds file %s not found",
                        eb.oem_creds)
        else:
            try:
                _login_and_cache(cfg)
            except SystemExit:
                raise
            except Exception as e:  # noqa: BLE001
                if have_cache:
                    log.warning("auto-login failed (%s); using cached %s",
                                e, eb.devices_file)
                else:
                    raise SystemExit(f"auto-login failed and no cached devices "
                                     f"to fall back on: {e}")
    return devices_from_json(eb.devices_file)


def run(config_path: str = "config.yaml") -> None:
    cfg = Config.load(config_path)
    logging.getLogger().setLevel(cfg.log_level)
    if not cfg.devices:
        cfg.devices = bootstrap_devices(cfg)
    if not cfg.devices:
        raise SystemExit(
            "no devices to serve. Set enbrighten.email/password (+ an OEM creds "
            "file) for automatic login, or provide a devices.json / a `devices:` "
            "list in the config.")
    Bridge(cfg).start()
