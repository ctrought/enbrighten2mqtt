"""Scenes the bridge knows beyond the built-in table.

The device has no datapoint listing its scenes, so the only way to find one is
to see it: any scene or effect id the device reports that isn't in the codec
tables is learned here and offered to Home Assistant from then on. DIY scenes
and edited presets live in the app's cloud storage, and can be imported from
there. Saved looks are the user's own snapshots (dp106 + per-bulb mask).
Everything persists to one JSON file.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path

from .codec import eternity as codec

log = logging.getLogger("enbrighten2mqtt.scenes")


class SceneStore:
    def __init__(self, path: str | None = None, on_change=None):
        self.path = Path(path) if path else None
        self.on_change = on_change      # callback() when the name lists change
        self._lock = threading.RLock()
        self._scenes: dict[str, str] = {}       # name -> dp106 payload
        self._effects: dict[str, int] = {}      # name -> B1 id
        self._looks: dict[str, dict] = {}       # name -> {"dp106", "mask"}
        self._masks: dict[str, str] = {}        # imported scene -> per-bulb mask
        self._imported: list[str] = []          # scenes the last import wrote
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        if not self.path or not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text())
        except (OSError, ValueError) as e:
            log.warning("ignoring unreadable %s: %s", self.path, e)
            return
        # Id 00 is a color state, though older versions learned it as a scene.
        self._scenes = {n: p for n, p in raw.get("scenes", {}).items() if p[:2] != "00"}
        self._effects = raw.get("effects", {})
        self._looks = raw.get("looks", {})
        self._masks = raw.get("masks", {})
        self._imported = raw.get("imported", [])

    def _changed(self) -> None:
        if self.path:
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text(json.dumps({"scenes": self._scenes,
                                       "effects": self._effects,
                                       "looks": self._looks,
                                       "masks": self._masks,
                                       "imported": self._imported}, indent=2))
            tmp.replace(self.path)
        if self.on_change:
            self.on_change()

    # -- names --------------------------------------------------------------

    def scene_names(self) -> list[str]:
        """Built-in scenes, then learned scenes, then saved looks."""
        with self._lock:
            return list(dict.fromkeys(
                [*codec.SCENE_PAYLOADS, *self._scenes, *self._looks]))

    def effect_names(self) -> list[str]:
        with self._lock:
            return list(codec.EFFECTS) + list(self._effects)

    # -- lookup -------------------------------------------------------------

    def scene_payload(self, name: str) -> str | None:
        with self._lock:
            return self._scenes.get(name) or codec.SCENE_PAYLOADS.get(name)

    def scene_mask(self, name: str) -> str | None:
        with self._lock:
            return self._masks.get(name)

    def effect_id(self, name: str) -> int | None:
        with self._lock:
            return codec.EFFECTS.get(name, self._effects.get(name))

    def effect_name(self, effect_id: int | None) -> str | None:
        """Name for an effect id, without learning it: scenes use some internal ids."""
        with self._lock:
            for table in (codec.EFFECTS, self._effects):
                for name, known in table.items():
                    if known == effect_id:
                        return name
            return None

    def look(self, name: str) -> dict | None:
        with self._lock:
            return self._looks.get(name)

    def look_matching(self, dp106: str) -> str | None:
        """The saved look showing this exact state, if any."""
        with self._lock:
            return next((n for n, v in self._looks.items()
                         if v["dp106"] == dp106), None)

    # -- learning -----------------------------------------------------------

    def scene_for(self, raw: str) -> str:
        """Name for a scene-mode dp106, learning it if the id is new."""
        scene_id = int(raw[0:2], 16)
        with self._lock:
            for table in (self._scenes, codec.SCENE_PAYLOADS):
                for name, payload in table.items():
                    if int(payload[0:2], 16) == scene_id:
                        return name
            name = f"scene_{scene_id:02x}"
            self._scenes[name] = raw
            log.info("learned new scene %s (%s)", name, raw)
            self._changed()
            return name

    def effect_for(self, effect_id: int) -> str:
        """Name for a color-mode effect id, learning it if the id is new."""
        with self._lock:
            for table in (codec.EFFECTS, self._effects):
                for name, known in table.items():
                    if known == effect_id:
                        return name
            name = f"fx_{effect_id:02x}"
            self._effects[name] = effect_id
            log.info("learned new effect %s", name)
            self._changed()
            return name

    # -- user edits ---------------------------------------------------------

    def import_scenes(self, scenes: list[dict]) -> list[str]:
        """Replace previously imported scenes with these app `mode` records.

        An edited built-in keeps its built-in name, so it just recalls as edited.
        """
        with self._lock:
            for name in self._imported:
                self._scenes.pop(name, None)
                self._masks.pop(name, None)
            self._imported = []
            for rec in scenes:
                name = _imported_name(rec)
                self._scenes[name] = _encode_record(rec)
                if rec.get("lightStr"):
                    self._masks[name] = rec["lightStr"]
                self._imported.append(name)
            self._changed()
            return list(self._imported)

    def save_look(self, name: str, dp106: str, mask: str) -> None:
        if not name or name in codec.SCENE_PAYLOADS or name in self._scenes:
            raise ValueError(f"can't save a look as {name!r}")
        with self._lock:
            self._looks[name] = {"dp106": dp106, "mask": mask}
            self._changed()

    def rename(self, old: str, new: str) -> None:
        """Rename a learned scene, learned effect or saved look."""
        with self._lock:
            if not new or new in self.scene_names() or new in self.effect_names():
                raise ValueError(f"name not available: {new!r}")
            for table in (self._scenes, self._effects, self._looks):
                if old in table:
                    table[new] = table.pop(old)
                    if old in self._masks:
                        self._masks[new] = self._masks.pop(old)
                    if old in self._imported:
                        self._imported[self._imported.index(old)] = new
                    self._changed()
                    return
            raise ValueError(f"not a learned scene, effect or look: {old!r}")

    def delete(self, name: str) -> None:
        with self._lock:
            for table in (self._scenes, self._effects, self._looks):
                if name in table:
                    del table[name]
                    self._changed()
                    return
            raise ValueError(f"not a learned scene, effect or look: {name!r}")


def _imported_name(rec: dict) -> str:
    builtin = next((n for n, p in codec.SCENE_PAYLOADS.items()
                    if int(p[0:2], 16) == rec["mode"]), None)
    return builtin or str(rec.get("name") or f"scene_{rec['mode']:02x}")


def _encode_record(rec: dict) -> str:
    """dp106 for an app `mode` record, as the panel's own formatter builds it."""
    colors = [codec.Color(r=c.get("r", 0), g=c.get("g", 0), b=c.get("b", 0), w=c.get("w", 0))
              for c in rec.get("colorData") or []]
    return codec.encode_state(codec.LightState(
        mode="scene", scene_id=rec["mode"], scene_b1=rec.get("effect", 0),
        speed=rec.get("speed", 500), brightness=rec.get("dimming", 1000),
        scene_colors=colors))


def custom_modes(properties) -> list[dict]:
    """Find the app's DIY and edited `mode` records in its cloud device storage.

    Values are JSON nested inside JSON, under keys the panel derives per device,
    so search for the record shape rather than rely on a path.
    """
    found: dict[int, dict] = {}

    def walk(node):
        if isinstance(node, str):
            try:
                walk(json.loads(node))
            except ValueError:
                pass
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif isinstance(node, dict):
            # The panel only stores records it has changed, so an edited preset
            # counts even with no colors of its own (and isDefault still true).
            if isinstance(node.get("mode"), int) and "effect" in node:
                found[node["mode"]] = node
            else:
                for item in node.values():
                    walk(item)

    walk(properties)
    return [found[m] for m in sorted(found)]
