"""LAN connection to a Tuya device, built on tinytuya.

Wraps tinytuya with the things the bridge needs: protocol-version auto-detect
(3.3/3.4/3.5), a persistent socket with heartbeats, reconnect-with-backoff, and
a callback for pushed DP updates so Home Assistant reflects changes made from
the Enbrighten app or the physical button.

All control stays on the LAN; nothing here talks to the cloud.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

import tinytuya

log = logging.getLogger("enbrighten2mqtt.local")

_VERSIONS = (3.4, 3.5, 3.3, 3.2, 3.1)

_IO_TIMEOUT = 5.0       # status and writes wait for the device's reply
_POLL_TIMEOUT = 0.2     # receive() must not hold up queued commands
_ERR_EMPTY_REPLY = "904"    # tinytuya's "Unexpected Payload": an ack with no data,
                            # which these devices send routinely; the link is fine


class DeviceError(RuntimeError):
    """The device refused or failed a request; the session should reconnect."""


@dataclass
class DeviceInfo:
    dev_id: str
    ip: str
    local_key: str
    version: float | None = None


def detect_version(info: DeviceInfo, timeout: float = 5.0) -> float | None:
    """Find the protocol version that gives a readable status reply."""
    order = ([info.version] if info.version else []) + list(_VERSIONS)
    for v in order:
        if v is None:
            continue
        d = tinytuya.Device(info.dev_id, info.ip, info.local_key, version=float(v))
        d.set_socketTimeout(timeout)
        try:
            status = d.status()
        except Exception:  # noqa: BLE001
            continue
        if isinstance(status, dict) and "dps" in status:
            log.info("device %s speaks protocol %s", info.dev_id, v)
            return float(v)
    return None


class LocalDevice:
    """Persistent LAN session with push support."""

    def __init__(self, info: DeviceInfo, *, on_dps: Callable[[dict], None] | None = None):
        self.info = info
        self.on_dps = on_dps
        self._dev: tinytuya.Device | None = None
        self._connected = False

    # -- lifecycle ----------------------------------------------------------

    def connect(self) -> None:
        v = self.info.version or detect_version(self.info)
        if not v:
            raise RuntimeError(f"could not reach {self.info.ip} on any protocol")
        self.info.version = v
        self.close()    # these devices serve one LAN session at a time
        d = tinytuya.Device(self.info.dev_id, self.info.ip, self.info.local_key,
                            version=float(v))
        d.set_socketPersistent(True)
        d.set_socketTimeout(_IO_TIMEOUT)
        d.set_socketRetryLimit(1)
        self._dev = d
        self._connected = True

    def close(self) -> None:
        if self._dev:
            self._dev.close()
            self._dev = None
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    # -- control ------------------------------------------------------------

    def status(self) -> dict:
        self._ensure()
        return self._checked(self._dev.status())

    def set_dp(self, dp: int | str, value) -> dict:
        return self.set_dps({dp: value})

    def set_dps(self, dps: dict) -> dict:
        self._ensure()
        return self._checked(self._dev.set_multiple_values(dps, nowait=False))

    def turn(self, on: bool, switch_dp: int = 20) -> dict:
        return self.set_dp(switch_dp, bool(on))

    def heartbeat(self) -> None:
        if self._dev:
            self._dev.heartbeat(nowait=True)

    def receive(self) -> dict | None:
        """Pump one pushed message (call in a loop in the bridge)."""
        self._ensure()
        self._dev.set_socketTimeout(_POLL_TIMEOUT)
        try:
            data = self._dev.receive()
        finally:
            self._dev.set_socketTimeout(_IO_TIMEOUT)
        data = self._unwrap(data)
        if data and "dps" in data and self.on_dps:
            self.on_dps(data["dps"])
        return data

    # -- internals ----------------------------------------------------------

    def _ensure(self) -> None:
        if not self._connected:
            self.connect()

    def _checked(self, data):
        """Like _unwrap, but raise so the caller doesn't assume success."""
        data = self._unwrap(data)
        if isinstance(data, dict) and data.get("Err"):
            raise DeviceError(f"{data.get('Error', 'device error')} ({data['Err']})")
        return data

    def _unwrap(self, data):
        if isinstance(data, dict) and str(data.get("Err")) == _ERR_EMPTY_REPLY:
            log.debug("device %s sent an empty reply", self.info.dev_id)
            return None
        if isinstance(data, dict) and data.get("Err"):
            # tinytuya signals errors as dicts; drop the socket so we reconnect.
            log.warning("device %s error: %s", self.info.dev_id, data)
            self._connected = False
        return data


def reconnect_loop(info: DeviceInfo, on_dps, *, max_backoff: float = 60.0):
    """Generator yielding a connected LocalDevice, reconnecting on failure."""
    backoff = 1.0
    while True:
        dev = LocalDevice(info, on_dps=on_dps)
        try:
            dev.connect()
            backoff = 1.0
            yield dev
        except Exception as e:  # noqa: BLE001
            log.warning("connect to %s failed: %s; retry in %.0fs",
                        info.ip, e, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)
