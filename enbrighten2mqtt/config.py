"""Configuration loading for the bridge.

Values come from a YAML file and/or environment variables (env overrides file),
so the same image works as a standalone container (env) and as a Home Assistant
add-on (its run.sh maps add-on options to env). Device keys are normally fetched
automatically at startup by logging into the Enbrighten account (see
bootstrap_devices); an explicit `devices:` list or a pre-written devices.json
also works and skips the cloud entirely.
"""

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
class EnbrightenConfig:
    """Enbrighten account + where to find the (app-level) OEM credentials."""
    email: str | None = None
    password: str | None = None
    oem_creds: str = "secrets/oem.json"   # app constants (client_id, secrets...)
    devices_file: str = "devices.json"    # cache of fetched device keys
    refresh: str = "if_missing"           # if_missing | always | never


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
    enbrighten: EnbrightenConfig = field(default_factory=EnbrightenConfig)
    devices: list[DeviceConfig] = field(default_factory=list)
    log_level: str = "INFO"

    @classmethod
    def load(cls, path: str = "config.yaml") -> "Config":
        raw = {}
        if path and Path(path).exists():
            raw = yaml.safe_load(Path(path).read_text()) or {}
        mqtt = MqttConfig(**{**(raw.get("mqtt") or {}), **_env_mqtt()})
        enb = EnbrightenConfig(**{**(raw.get("enbrighten") or {}), **_env_enbrighten()})
        cfg = cls(
            mqtt=mqtt,
            enbrighten=enb,
            log_level=os.environ.get("LOG_LEVEL", raw.get("log_level", "INFO")),
        )
        # Only devices listed explicitly in config are loaded here; a cached
        # devices.json (and, failing that, an automatic login) is resolved at
        # serve time by bootstrap_devices so the cloud step can run on startup.
        for d in (raw.get("devices") or []):
            cfg.devices.append(DeviceConfig(**d))
        return cfg


def devices_from_json(path: str) -> list[DeviceConfig]:
    """Load DeviceConfig entries from a devices.json written by `login`."""
    if not Path(path).exists():
        return []
    # Ignore any ip in devices.json: it comes from the cloud (the device's WAN
    # address) and is never a usable LAN target, so always leave it blank and
    # let the bridge discover the LAN IP. Pin an IP via a `devices:` config entry
    # instead if you must.
    return [
        DeviceConfig(
            dev_id=d["dev_id"], ip="", local_key=d["local_key"],
            name=d.get("name", ""), product_id=d.get("product_id", ""),
            version=_ver(d.get("version")))
        for d in json.loads(Path(path).read_text())
    ]


def _ver(v):
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def _env_mqtt() -> dict:
    out = {}
    for key, env in (("host", "MQTT_HOST"), ("username", "MQTT_USER"),
                     ("password", "MQTT_PASS"), ("base_topic", "MQTT_BASE_TOPIC"),
                     ("discovery_prefix", "MQTT_DISCOVERY_PREFIX")):
        if os.environ.get(env):
            out[key] = os.environ[env]
    if os.environ.get("MQTT_PORT"):
        out["port"] = int(os.environ["MQTT_PORT"])
    return out


def _env_enbrighten() -> dict:
    out = {}
    for key, env in (("email", "ENBRIGHTEN_EMAIL"), ("password", "ENBRIGHTEN_PASSWORD"),
                     ("oem_creds", "OEM_CREDS"), ("devices_file", "DEVICES_FILE"),
                     ("refresh", "DEVICES_REFRESH")):
        if os.environ.get(env):
            out[key] = os.environ[env]
    return out
