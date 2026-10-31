"""Interactive DP capture harness (Phase 3).

Holds a persistent LAN session to the controller and watches for DP changes
that the Enbrighten app pushes. The operator drives the app ("select
Christmas, then press Enter") while this records the before/after DP deltas to
a JSONL file. Differential captures like these are what let us decode the
proprietary mode/effect DPs without the cloud.

Run:  python -m enbrighten2mqtt capture --device <id>
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .local.device import DeviceInfo, LocalDevice

# The checklist from the plan, as prompts. Each produces one labelled snapshot.
CHECKLIST = [
    ("power_on", "Turn the lights ON in the app"),
    ("power_off", "Turn the lights OFF, then ON again"),
    ("button_short", "Short-press the physical button (cycles presets)"),
    ("button_long", "Long-press the physical button, then release"),
    ("bright_15", "Set brightness preset 15%"),
    ("bright_50", "Set brightness preset 50%"),
    ("bright_100", "Set brightness preset 100%"),
    ("bright_min", "Drag brightness slider to minimum"),
    ("bright_max", "Drag brightness slider to maximum"),
    ("white_2200", "Set white temperature 2200K (warmest)"),
    ("white_6000", "Set white temperature 6000K (coolest)"),
    ("color_red", "Set a solid RED"),
    ("color_green", "Set a solid GREEN"),
    ("color_blue", "Set a solid BLUE"),
    ("hue_sweep", "Set hue to ~120 (green) at full saturation"),
    ("sat_half", "Set the current color to ~50% saturation"),
    ("preset_fademelt", "Select preset: Fade Melt"),
    ("preset_christmas", "Select preset: Christmas"),
    ("preset_fall", "Select preset: Fall"),
    ("preset_halloween", "Select preset: Halloween"),
    ("preset_july4", "Select preset: July 4th"),
    ("preset_mardigras", "Select preset: Mardi Gras"),
    ("preset_rainbow", "Select preset: Rainbow"),
    ("preset_spring", "Select preset: Spring"),
    ("preset_stpatricks", "Select preset: St. Patrick's Day"),
    ("preset_valentines", "Select preset: Valentine's Day"),
    ("effect_solid", "On one preset, set effect: Solid"),
    ("effect_twinkle", "Set effect: Twinkle"),
    ("effect_strobe", "Set effect: Strobe"),
    ("effect_pulse", "Set effect: Pulse"),
    ("effect_wavechase", "Set effect: Wave Chase"),
    ("effect_chase", "Set effect: Chase"),
    ("effect_wave", "Set effect: Wave"),
    ("effect_fade", "Set effect: Fade"),
    ("effect_lightning", "Set effect: Lightning"),
    ("effect_flame", "Set effect: Flame"),
    ("effect_stacking", "Set effect: Stacking"),
    ("speed_min", "Set effect speed to minimum"),
    ("speed_max", "Set effect speed to maximum"),
    ("pattern_1color", "Pattern editor: single color sequence"),
    ("pattern_2color", "Pattern editor: 2 repeating colors"),
    ("pattern_6color", "Pattern editor: 6-color sequence"),
    ("pixel_one_off", "My Lights: turn ONE LED off"),
    ("pixel_alt_off", "My Lights: turn every other LED off"),
    ("custom_save", "Save the current look as a custom mode"),
    ("custom_recall", "Switch away, then recall that custom mode"),
    ("timer_countdown", "Set a 1-minute countdown timer"),
    ("schedule_add", "Add a dusk-to-dawn schedule"),
]


class CaptureSession:
    def __init__(self, info: DeviceInfo, out_dir: str = "captures"):
        self.dev = LocalDevice(info)
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=True)
        self.log_path = self.out / f"capture_{int(time.time())}.jsonl"
        self._last: dict = {}

    def _snapshot(self) -> dict:
        """Drain pushed updates, then query, returning the merged DP map."""
        merged = dict(self._last)
        deadline = time.time() + 2.0
        while time.time() < deadline:
            msg = self.dev.receive()
            if msg and "dps" in msg:
                merged.update(msg["dps"])
        try:
            st = self.dev.status()
            if st and "dps" in st:
                merged.update(st["dps"])
        except Exception:  # noqa: BLE001
            pass
        return merged

    def record(self, label: str, prompt: str) -> dict:
        input(f"\n>> {prompt}\n   (do it in the app, then press Enter) ")
        dps = self._snapshot()
        delta = {k: v for k, v in dps.items() if self._last.get(k) != v}
        entry = {
            "ts": time.time(),
            "label": label,
            "prompt": prompt,
            "dps": dps,
            "changed": delta,
        }
        with self.log_path.open("a") as f:
            f.write(json.dumps(entry) + "\n")
        self._last = dps
        print(f"   changed DPs: {json.dumps(delta)}")
        return entry

    def run(self, checklist=CHECKLIST) -> None:
        self.dev.connect()
        print(f"Connected to {self.dev.info.ip} "
              f"(protocol {self.dev.info.version}). "
              f"Logging to {self.log_path}")
        self._last = self._snapshot()
        print(f"Baseline DPs: {json.dumps(self._last)}")
        for label, prompt in checklist:
            try:
                self.record(label, prompt)
            except KeyboardInterrupt:
                print("\nStopping capture.")
                break
        print(f"\nDone. Capture saved to {self.log_path}")
