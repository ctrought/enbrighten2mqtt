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

A short module-level cache (guarded by a lock so only one scan runs at a time)
lets the bridge's per-device worker threads share one scan instead of each
launching their own.
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
_cache: dict[str, dict] = {}
_cache_ts = 0.0


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


def discover(want_ids=None, *, scantime: float = 10.0,
             max_age: float = 25.0) -> dict[str, dict]:
    """Return {dev_id: {"ip", "version"}} for devices currently on the LAN.

    Results are cached for `max_age` seconds; concurrent callers share one scan.
    A device that is simply offline stays absent until the cache goes stale, so
    its worker's retries don't each trigger a fresh scan.
    """
    global _cache_ts
    with _lock:
        if (time.time() - _cache_ts) < max_age and _cache:
            return dict(_cache)
        seen = _scan(want_ids, scantime)
        if seen:
            _cache.update(seen)
            _cache_ts = time.time()
        return dict(_cache)


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
