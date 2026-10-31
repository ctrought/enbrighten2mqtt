"""State-preserving controller mapping HA light commands to device DPs.

Home Assistant sends partial commands (just brightness, just effect, just
color). The device packs everything into one dp106 string, so we keep the last
known LightState and patch only what changed before re-encoding.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .codec import eternity as codec
from .codec.eternity import Color, LightState
from .local.device import DeviceInfo, LocalDevice
from .scenes import SceneStore

log = logging.getLogger("enbrighten2mqtt.controller")


@dataclass
class DeviceState:
    power: bool = True
    mask: list[bool] = field(default_factory=lambda: [True] * codec.NUM_BULBS)
    light: LightState = field(default_factory=LightState)
    countdown: int = 0                  # seconds left
    reverse: bool = False               # effects run in the opposite direction
    raw: str = ""                       # last dp106, as sent or reported
    kelvin: int | None = None           # white temperature, while still showing


class Controller:
    def __init__(self, info: DeviceInfo, on_state=None, scenes: SceneStore | None = None):
        self.info = info
        self.state = DeviceState()
        self.on_state = on_state            # callback(DeviceState) on any change
        self.scenes = scenes or SceneStore()
        self.dev = LocalDevice(info, on_dps=self._on_dps)

    # -- lifecycle ----------------------------------------------------------

    def connect(self):
        self.dev.connect()
        self.refresh()

    def refresh(self):
        st = self.dev.status()
        if isinstance(st, dict) and "dps" in st:
            self._apply_dps(st["dps"])

    def pump(self):
        """Process one pushed message (call in the bridge loop)."""
        self.dev.receive()

    # -- inbound device DPs -> state ---------------------------------------

    def _on_dps(self, dps: dict):
        self._apply_dps(dps)

    def _apply_dps(self, dps: dict):
        changed = False
        if str(codec.DP_SWITCH) in dps:
            self.state.power = bool(dps[str(codec.DP_SWITCH)]); changed = True
        if str(codec.DP_COUNTDOWN) in dps:
            self.state.countdown = int(dps[str(codec.DP_COUNTDOWN)]); changed = True
        if str(codec.DP_REVERSE) in dps:
            self.state.reverse = bool(dps[str(codec.DP_REVERSE)]); changed = True
        if str(codec.DP_MASK) in dps:
            self.state.mask = codec.decode_mask(str(dps[str(codec.DP_MASK)])); changed = True
        if str(codec.DP_STATE) in dps:
            raw = str(dps[str(codec.DP_STATE)])
            try:
                self._apply_light(raw)
                changed = True
            except ValueError as e:
                log.warning("undecodable dp106 %r: %s", raw, e)
        if changed and self.on_state:
            self.on_state(self.state)

    def _apply_light(self, raw: str):
        new = codec.decode_state(raw)
        old = self.state.light
        if new.mode == "scene":
            new.scene = self.scenes.scene_for(raw)
            # A scene supplies its own colors; keep ours for leaving the scene.
            new.colors, new.effect, new.effect_id = old.colors, old.effect, old.effect_id
        else:
            new.effect = self.scenes.effect_for(new.effect_id)
        if raw != self.state.raw:
            self.state.kelvin = self._still_white(new)
        self.state.light, self.state.raw = new, raw

    def _still_white(self, light: LightState) -> int | None:
        k = self.state.kelvin
        if k is not None and light.mode == "color" \
                and light.colors == [codec.white_to_color(k)]:
            return k
        return None

    # -- outbound HA commands -> device ------------------------------------

    def _notify(self):
        if self.on_state:
            self.on_state(self.state)

    def apply(self, *, power: bool | None = None, brightness: int | None = None,
              speed: int | None = None, colors: list[Color] | None = None,
              kelvin: int | None = None, effect: str | None = None,
              mask: list[bool] | None = None, countdown: int | None = None,
              reverse: bool | None = None):
        """Send one command as a single device write, so it changes all at once."""
        dps: dict = {}
        light = self.state.light
        touched = False
        if power is not None:
            dps[codec.DP_SWITCH] = bool(power)
        if countdown is not None:
            dps[codec.DP_COUNTDOWN] = max(0, min(86400, int(countdown)))
        if reverse is not None:
            dps[codec.DP_REVERSE] = bool(reverse)
        if mask is not None:
            dps[codec.DP_MASK] = codec.encode_mask(mask)
        if kelvin is not None:
            colors = [codec.white_to_color(kelvin)]
        if colors is not None:
            light.mode = "color"
            light.colors = colors[:6] or [Color(w=255)]
            touched = True
        if effect is not None:
            look = self.scenes.look(effect)
            if look:
                self._recall_look(look, dps)
                light = self.state.light
            elif self._select(effect):
                touched = True
                scene_mask = self.scenes.scene_mask(effect)
                if scene_mask and mask is None:
                    dps[codec.DP_MASK] = scene_mask
            else:
                log.warning("ignoring unknown effect or scene %r", effect)
        if brightness is not None:
            light.brightness = max(1, min(codec.SCALE_MAX, int(brightness)))
            touched = True
        if speed is not None:
            light.speed = max(0, min(codec.SCALE_MAX, int(speed)))
            touched = True
        if touched:
            self.state.raw = dps[codec.DP_STATE] = codec.encode_state(light)
        if not dps:
            return
        self.dev.set_dps(dps)
        if power is not None:
            self.state.power = bool(power)
        if countdown is not None:
            self.state.countdown = dps[codec.DP_COUNTDOWN]
        if reverse is not None:
            self.state.reverse = bool(reverse)
        if codec.DP_MASK in dps:
            self.state.mask = codec.decode_mask(dps[codec.DP_MASK])
        self.state.kelvin = kelvin if kelvin is not None else self._still_white(light)
        self._notify()

    def _select(self, name: str) -> bool:
        """Point the light state at a named scene or effect."""
        light = self.state.light
        payload = self.scenes.scene_payload(name)
        if payload:
            # Recalling a scene starts from its own speed, as the app does.
            preset = codec.decode_state(payload)
            light.mode, light.scene = "scene", name
            light.scene_id, light.scene_b1 = preset.scene_id, preset.scene_b1
            light.scene_colors = preset.scene_colors
            light.speed = preset.speed
            return True
        effect_id = self.scenes.effect_id(name)
        if effect_id is None:
            return False
        if light.mode != "scene":
            light.effect, light.effect_id = name, effect_id
        elif light.scene_id in codec.SOLID_ONLY_SCENES and effect_id != codec.EFFECTS["solid"]:
            log.warning("scene %s only supports solid; ignoring %s", light.scene, name)
        else:
            # As in the app: a scene keeps its id and colors, and animates differently.
            light.scene_b1 = effect_id
        return True

    def _recall_look(self, look: dict, dps: dict):
        dps[codec.DP_STATE] = look["dp106"]
        dps[codec.DP_MASK] = look["mask"]
        self._apply_light(look["dp106"])
        self.state.mask = codec.decode_mask(look["mask"])

    def save_look(self, name: str):
        """Snapshot what the light is showing now as a recallable look."""
        self.scenes.save_look(name, self.state.raw or codec.encode_state(self.state.light),
                              codec.encode_mask(self.state.mask))

    def set_power(self, on: bool):
        self.apply(power=on)

    def set_brightness(self, dev_0_1000: int):
        self.apply(brightness=dev_0_1000)

    def set_speed(self, dev_0_1000: int):
        self.apply(speed=dev_0_1000)

    def set_effect(self, effect: str):
        """effect may be an animation name, a scene name or a saved look."""
        self.apply(effect=effect)

    def set_colors(self, colors: list[Color]):
        self.apply(colors=colors)

    def set_rgbw(self, r: int, g: int, b: int, w: int = 0):
        self.set_colors([Color(r=r, g=g, b=b, w=w)])

    def set_mask(self, on_bulbs: list[bool]):
        self.apply(mask=on_bulbs)

    def set_countdown(self, seconds: int):
        self.apply(countdown=seconds)
