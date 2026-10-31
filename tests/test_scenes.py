"""Scene store tests: learning, persistence, renames, DIY import."""

import json

import pytest

from enbrighten2mqtt.scenes import SceneStore, custom_modes


def test_learned_scene_persists(tmp_path):
    p = tmp_path / "scenes.json"
    seen = []
    store = SceneStore(str(p), on_change=lambda: seen.append(1))
    assert store.scene_for("2a07012c03e8") == "scene_2a"
    assert store.scene_for("2a07032003e8") == "scene_2a"   # same id: not a new scene
    assert len(seen) == 1
    again = SceneStore(str(p))
    assert "scene_2a" in again.scene_names()
    assert again.scene_payload("scene_2a") == "2a07012c03e8"


def test_known_scene_not_learned():
    store = SceneStore()
    assert store.scene_for("0301012c0100") == "rainbow"
    assert store.scene_names().count("rainbow") == 1


def test_rename_keeps_identity():
    store = SceneStore()
    store.scene_for("2a07012c03e8")
    store.rename("scene_2a", "Porch party")
    assert store.scene_for("2a07012c03e8") == "Porch party"
    assert "scene_2a" not in store.scene_names()


def test_rename_refuses_taken_or_builtin():
    store = SceneStore()
    store.scene_for("2a07012c03e8")
    with pytest.raises(ValueError):
        store.rename("scene_2a", "rainbow")
    with pytest.raises(ValueError):
        store.rename("rainbow", "mine")


def test_look_cannot_shadow_builtin():
    store = SceneStore()
    with pytest.raises(ValueError):
        store.save_look("rainbow", "ff00", "1" * 12)


def test_learned_effect():
    store = SceneStore()
    assert store.effect_for(0x09) == "flame"
    assert store.effect_for(0x0e) == "fx_0e"
    assert store.effect_id("fx_0e") == 0x0e


def _record(**over):
    return {"name": "Patio", "mode": 18, "effect": 5, "speed": 500, "dimming": 1000,
            "colorMode": "1", "colorData": [{"w": 0, "r": 255, "g": 0, "b": 0},
                                            {"w": 0, "r": 0, "g": 0, "b": 255}],
            "type": "Celebration", **over}


def test_import_builds_app_format():
    store = SceneStore()
    assert store.import_scenes([_record(lightStr="1" * 6 + "0" * 6)]) == ["Patio"]
    assert store.scene_payload("Patio") == "120501f403e81#00ff0000#000000ff"
    assert store.scene_mask("Patio") == "111111000000"
    assert store.scene_for(store.scene_payload("Patio")) == "Patio"


def test_import_replaces_previous_import_only():
    store = SceneStore()
    store.scene_for("2a07012c03e8")
    store.import_scenes([_record()])
    store.import_scenes([_record(name="Deck", mode=19)])
    names = store.scene_names()
    assert "Deck" in names and "Patio" not in names and "scene_2a" in names


def test_edited_builtin_keeps_its_name():
    store = SceneStore()
    assert store.import_scenes([_record(name="Customize", mode=3)]) == ["rainbow"]
    assert store.scene_payload("rainbow").startswith("0305")
    assert store.scene_names().count("rainbow") == 1
    assert store.scene_for("0305012c03e8") == "rainbow"


def test_custom_modes_finds_nested_records():
    # Cloud values are JSON strings holding JSON, as the panel stores them.
    inner = json.dumps({"value": _record(), "type": "Object"})
    props = {"abc123": json.dumps({"data": inner, "time": 1}),
             "other": json.dumps({"data": json.dumps({"value": {"like": True}})}),
             # A default preset carries no colour data and must be left alone.
             "def456": json.dumps({"data": json.dumps(
                 {"value": {"mode": 3, "effect": 1, "colorData": []}})})}
    found = custom_modes(props)
    assert [r["mode"] for r in found] == [18]


def test_custom_modes_reads_lists():
    props = {"ui": {"data": {"value": {"modes": [_record(), _record(mode=19)]}}}}
    assert [r["mode"] for r in custom_modes(props)] == [18, 19]


def test_mistakenly_learned_color_state_is_dropped(tmp_path):
    p = tmp_path / "scenes.json"
    p.write_text(json.dumps({"scenes": {"scene_00": "00004303e80#FF000000",
                                        "scene_2a": "2a07012c03e8"}}))
    names = SceneStore(str(p)).scene_names()
    assert "scene_00" not in names and "scene_2a" in names
