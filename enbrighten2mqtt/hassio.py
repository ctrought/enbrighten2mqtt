"""Home Assistant add-on glue.

When this runs as an HA add-on, the Supervisor writes the user's options to
/data/options.json and (with `services: mqtt`) exposes the broker via the
Supervisor API. apply_addon_options() turns both into the same environment
variables the bridge already reads, so one plain container image works as both
a standalone container and an HA add-on — no bashio, no second image.

Outside an add-on (/data/options.json absent) this is a no-op.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("enbrighten2mqtt.hassio")

OPTIONS_PATH = "/data/options.json"
_OEM_CANDIDATES = ("/config/oem.json", "/app/secrets/oem.json")


def _setenv(key: str, value) -> None:
    """Set env var if we have a value and it isn't already set (env wins)."""
    if value not in (None, "") and not os.environ.get(key):
        os.environ[key] = str(value)


def _mqtt_from_supervisor() -> None:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token or os.environ.get("MQTT_HOST"):
        return
    try:
        import requests
        r = requests.get("http://supervisor/services/mqtt",
                         headers={"Authorization": f"Bearer {token}"}, timeout=10)
        data = (r.json() or {}).get("data", {})
    except Exception as e:  # noqa: BLE001
        log.warning("could not read MQTT service from Supervisor: %s", e)
        return
    if data.get("host"):
        _setenv("MQTT_HOST", data.get("host"))
        _setenv("MQTT_PORT", data.get("port"))
        _setenv("MQTT_USER", data.get("username"))
        _setenv("MQTT_PASS", data.get("password"))
        log.info("using Home Assistant MQTT broker at %s:%s",
                 data.get("host"), data.get("port"))


def apply_addon_options() -> bool:
    """If running as an HA add-on, map options + MQTT service into env.

    Returns True when add-on options were applied, False otherwise.
    """
    if not os.path.exists(OPTIONS_PATH):
        return False
    try:
        opts = json.loads(open(OPTIONS_PATH).read())
    except Exception as e:  # noqa: BLE001
        log.warning("could not read %s: %s", OPTIONS_PATH, e)
        return False

    _setenv("ENBRIGHTEN_EMAIL", opts.get("enbrighten_email"))
    _setenv("ENBRIGHTEN_PASSWORD", opts.get("enbrighten_password"))
    _setenv("DEVICES_REFRESH", opts.get("device_refresh"))
    lvl = opts.get("log_level")
    _setenv("LOG_LEVEL", lvl.upper() if isinstance(lvl, str) else None)

    if opts.get("mqtt_host"):
        _setenv("MQTT_HOST", opts.get("mqtt_host"))
        _setenv("MQTT_PORT", opts.get("mqtt_port"))
        _setenv("MQTT_USER", opts.get("mqtt_user"))
        _setenv("MQTT_PASS", opts.get("mqtt_pass"))
    else:
        _mqtt_from_supervisor()

    for path in _OEM_CANDIDATES:
        if os.path.exists(path):
            _setenv("OEM_CREDS", path)
            break
    _setenv("DEVICES_FILE", "/data/devices.json")
    log.info("loaded Home Assistant add-on options")
    return True
