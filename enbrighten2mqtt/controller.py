"""State-preserving controller mapping HA light commands to device DPs.

Home Assistant sends partial commands (just brightness, just effect, just
color). The device packs everything into one dp106 string, so we keep the last
known LightState and patch only what changed before re-encoding.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .codec import eternity as codec
from .codec.eternity import Color, LightState
from .local.device import DeviceInfo, LocalDevice


@dataclass
class DeviceState:
    power: bool = True
    mask: list[bool] = field(default_factory=lambda: [True] * codec.NUM_BULBS)
    light: LightState = field(default_factory=LightState)


class Controller:
    def __init__(self, info: DeviceInfo, on_state=None):
        self.info = info
        self.state = DeviceState()
        self.on_state = on_state            # callback(DeviceState) on any change
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
        if str(codec.DP_MASK) in dps:
            self.state.mask = codec.decode_mask(str(dps[str(codec.DP_MASK)])); changed = True
        if str(codec.DP_STATE) in dps:
            try:
                self.state.light = codec.decode_state(str(dps[str(codec.DP_STATE)]))
                changed = True
            except Exception:
                pass
        if changed and self.on_state:
            self.on_state(self.state)

    # -- outbound HA commands -> device ------------------------------------

    def _notify(self):
        if self.on_state:
            self.on_state(self.state)

    def set_power(self, on: bool):
        self.dev.set_dp(codec.DP_SWITCH, bool(on))
        self.state.power = on
        self._notify()

    def _push_light(self):
        self.dev.set_dp(codec.DP_STATE, codec.encode_state(self.state.light))
        self._notify()

    def set_brightness(self, dev_0_1000: int):
        self.state.light.brightness = max(1, min(codec.SCALE_MAX, dev_0_1000))
        self._push_light()

    def set_speed(self, dev_0_1000: int):
        self.state.light.speed = max(0, min(codec.SCALE_MAX, dev_0_1000))
        self._push_light()

    def set_effect(self, effect: str):
        """effect may be an animation name or a scene name."""
        if effect in codec.SCENE_PAYLOADS:
            self.state.light.mode = "scene"
            self.state.light.scene = effect
        elif effect in codec.EFFECTS:
            self.state.light.mode = "color"
            self.state.light.effect = effect
        else:
            return
        self._push_light()

    def set_colors(self, colors: list[Color]):
        self.state.light.mode = "color"
        self.state.light.colors = colors[:6] or [Color(w=255)]
        self._push_light()

    def set_rgbw(self, r: int, g: int, b: int, w: int = 0):
        self.set_colors([Color(r=r, g=g, b=b, w=w)])

    def set_mask(self, on_bulbs: list[bool]):
        self.dev.set_dp(codec.DP_MASK, codec.encode_mask(on_bulbs))
        self.state.mask = on_bulbs
        self._notify()

    def set_countdown(self, seconds: int):
        self.dev.set_dp(codec.DP_COUNTDOWN, int(seconds))
