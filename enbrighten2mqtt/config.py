"""Configuration loading for the bridge."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class MqttConfig:
    host: str = "localhost"
    port: int = 1883
    username: str | None = None
    password: str | None = None
    base_topic: str = "enbrighten2mqtt"
    discovery_prefix: str = "homeassistant"


@dataclass
class DeviceConfig:
    dev_id: str
    ip: str
    local_key: str
    name: str = ""
    product_id: str = ""
    version: float | None = None


@dataclass
class Config:
    mqtt: MqttConfig = field(default_factory=MqttConfig)
    devices: list[DeviceConfig] = field(default_factory=list)
    cloud_fallback: bool = False
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: str = "config.yaml",
             devices_json: str = "devices.json") -> "Config":
        raw = {}
        if Path(path).exists():
            raw = yaml.safe_load(Path(path).read_text()) or {}
        mqtt = MqttConfig(**{**_env_mqtt(), **(raw.get("mqtt") or {})})
        cfg = cls(
            mqtt=mqtt,
            cloud_fallback=raw.get("cloud_fallback", False),
            log_level=raw.get("log_level", "INFO"),
        )
        # Devices: prefer explicit config, else fall back to devices.json.
        dev_entries = raw.get("devices")
        if not dev_entries and Path(devices_json).exists():
            dev_entries = [
                {"dev_id": d["dev_id"], "ip": d.get("ip") or "",
                 "local_key": d["local_key"], "name": d.get("name", ""),
                 "product_id": d.get("product_id", ""),
                 "version": _ver(d.get("version"))}
                for d in json.loads(Path(devices_json).read_text())
            ]
        for d in (dev_entries or []):
            cfg.devices.append(DeviceConfig(**d))
        return cfg


def _ver(v):
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def _env_mqtt() -> dict:
    out = {}
    for key, env in (("host", "MQTT_HOST"), ("username", "MQTT_USER"),
                     ("password", "MQTT_PASS")):
        if os.environ.get(env):
            out[key] = os.environ[env]
    if os.environ.get("MQTT_PORT"):
        out["port"] = int(os.environ["MQTT_PORT"])
    return out
