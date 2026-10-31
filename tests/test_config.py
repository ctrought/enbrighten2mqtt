"""Config loading / device bootstrap (offline, no cloud or LAN)."""

import json
import os
from pathlib import Path

from enbrighten2mqtt.config import Config, devices_from_json
from enbrighten2mqtt.mqtt.bridge import bootstrap_devices


def test_env_overrides_file(monkeypatch, tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("mqtt:\n  host: fromfile\n")
    monkeypatch.setenv("MQTT_HOST", "fromenv")
    monkeypatch.setenv("MQTT_PORT", "8883")
    monkeypatch.setenv("ENBRIGHTEN_EMAIL", "a@b.com")
    monkeypatch.setenv("ENBRIGHTEN_PASSWORD", "pw")
    monkeypatch.setenv("DEVICES_REFRESH", "always")
    cfg = Config.load(str(cfg_file))
    assert cfg.mqtt.host == "fromenv"          # env beats file
    assert cfg.mqtt.port == 8883
    assert cfg.enbrighten.email == "a@b.com"
    assert cfg.enbrighten.password == "pw"
    assert cfg.enbrighten.refresh == "always"


def test_devices_from_json_ignores_cloud_ip(tmp_path):
    p = tmp_path / "devices.json"
    p.write_text(json.dumps([{
        "dev_id": "eb123", "local_key": "0123456789abcdef",
        "name": "Light", "product_id": "pid", "ip": "203.0.113.5",  # WAN ip
        "version": "3.5"}]))
    devs = devices_from_json(str(p))
    assert len(devs) == 1
    assert devs[0].ip == ""                     # cloud WAN ip is dropped
    assert devs[0].dev_id == "eb123"
    assert devs[0].version == 3.5


def test_bootstrap_no_login_when_cache_present(tmp_path, monkeypatch):
    p = tmp_path / "devices.json"
    p.write_text(json.dumps([{"dev_id": "eb1", "local_key": "k", "name": "L"}]))
    monkeypatch.delenv("ENBRIGHTEN_EMAIL", raising=False)
    cfg = Config.load(str(tmp_path / "none.yaml"))
    cfg.enbrighten.devices_file = str(p)
    cfg.enbrighten.refresh = "if_missing"
    # No credentials + cache present -> returns cache, never touches the network.
    devs = bootstrap_devices(cfg)
    assert [d.dev_id for d in devs] == ["eb1"]


def test_bootstrap_refresh_never_skips_login(tmp_path):
    cfg = Config.load(str(tmp_path / "none.yaml"))
    cfg.enbrighten.email = "a@b.com"
    cfg.enbrighten.password = "pw"
    cfg.enbrighten.refresh = "never"
    cfg.enbrighten.devices_file = str(tmp_path / "missing.json")
    # refresh=never + missing cache -> no login attempted, empty list.
    assert bootstrap_devices(cfg) == []
