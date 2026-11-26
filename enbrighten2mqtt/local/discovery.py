"""LAN discovery for Tuya devices (no cloud).

The cloud device list reports each device's *public* (WAN) IP, which is useless
for local control. On the LAN, Tuya devices are found by tinytuya's scanner,
which listens for the UDP broadcasts (6666 v3.1, 6667 v3.3+) and actively probes
for the newer v3.5 devices that do not broadcast the same way. This needs the
bridge to be on the same network as the devices and talks only to the LAN.

tinytuya.find_device / deviceScan auto-load the working directory's devices.json
expecting tinytuya's own {id,key} schema and crash on ours. We call the
lower-level scanner.devices() directly and pass a dummy tuyadevices list so it
never touches that file.

Results are kept per device with the time each was last seen, guarded by a
lock so only one scan runs at a time and the bridge's per-device worker threads
can share scans. Only recent sightings are returned: a device that has moved to
a new IP, or gone offline, must never be handed its old address again.
"""

from __future__ import annotations

import logging
import threading
import time

import tinytuya
import tinytuya.scanner as _scanner

log = logging.getLogger("enbrighten2mqtt.local")

# Non-empty, schema-valid list so scanner.devices() skips loading ./devices.json
# (an empty list is falsy and still triggers the file load that crashes on ours).
_DUMMY_DEVICES = [{"id": "0" * 22, "key": "0" * 16, "name": "_"}]

_lock = threading.Lock()
_seen: dict[str, dict] = {}     # dev_id -> {"ip", "version", "ts"}


def _as_version(v) -> float | None:
    try:
        return float(v) if v else None
    except (TypeError, ValueError):
        return None


def _scan(want_ids, scantime: float) -> dict[str, dict]:
    try:
        raw = _scanner.devices(
            verbose=False, color=False, poll=False, forcescan=False, byID=True,
            scantime=scantime, wantids=tuple(want_ids) if want_ids else None,
            tuyadevices=_DUMMY_DEVICES) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("LAN scan error: %s", e)
        return {}
    out: dict[str, dict] = {}
    for gw, r in raw.items():
        if gw and gw != _DUMMY_DEVICES[0]["id"] and r.get("ip"):
            out[gw] = {"ip": r.get("ip"), "version": _as_version(r.get("version"))}
    return out


def _recent(max_age: float) -> dict[str, dict]:
    now = time.time()
    return {i: {"ip": e["ip"], "version": e["version"]}
            for i, e in _seen.items() if now - e["ts"] < max_age}


def discover(want_ids=None, *, scantime: float = 10.0,
             max_age: float = 25.0) -> dict[str, dict]:
    """Return {dev_id: {"ip", "version"}} for devices seen in the last `max_age`s.

    With `want_ids`, a recent sighting of every one of them is reused rather
    than scanning again, so concurrent workers share one scan. Without, the LAN
    is always scanned, to pick up IP changes and devices not seen before.
    """
    with _lock:
        if want_ids:
            recent = _recent(max_age)
            if all(i in recent for i in want_ids):
                return recent
        now = time.time()
        for dev_id, entry in _scan(want_ids, scantime).items():
            old = _seen.get(dev_id)
            if old and old["ip"] != entry["ip"]:
                log.info("device %s moved from %s to %s",
                         dev_id[:8], old["ip"], entry["ip"])
            _seen[dev_id] = {**entry, "ts": now}
        return _recent(max_age)


def forget() -> None:
    """Drop every sighting (for tests)."""
    with _lock:
        _seen.clear()


def find_device_ip(dev_id: str) -> tuple[str | None, float | None]:
    """Discover one device's LAN IP + protocol version by id."""
    entry = discover([dev_id]).get(dev_id)
    if not entry:
        return None, None
    return entry.get("ip"), entry.get("version")


def scan_lan(dev_ids) -> dict[str, dict]:
    """Return {dev_id: {"ip", "version"}} for each of dev_ids found on the LAN."""
    seen = discover(list(dev_ids))
    return {i: seen[i] for i in dev_ids if i in seen}
