"""Tuya OEM mobile-API client.

Emulates what the Enbrighten Android app does when it logs in: authenticate
with the user's own Enbrighten account against the OEM API host, list their
homes and devices, and read back each device's ``localKey`` plus the product
DP schema. We only ever touch the user's own account and devices.

The request signing follows Tuya's mobile SDK scheme (HMAC-SHA256 over a sorted
parameter string using a key derived from ``client_id`` + ``app_secret`` and,
on newer SDKs, the signing-cert hash and the t_s.bmp token). The exact recipe
is confirmed during Phase 1 from the APK; this module keeps the pieces
pluggable so we can match whatever variant the app uses.

Only stdlib + requests + cryptography, so it runs anywhere the bridge runs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from .signing import SignScheme, default_scheme


@dataclass
class Device:
    dev_id: str
    name: str
    local_key: str
    product_id: str
    category: str
    ip: str | None
    version: str | None
    online: bool
    raw: dict

    def to_json(self) -> dict:
        d = {k: getattr(self, k) for k in (
            "dev_id", "name", "local_key", "product_id",
            "category", "ip", "version", "online")}
        return d


class TuyaOemError(RuntimeError):
    pass


class TuyaOemApi:
    """Minimal client for the Tuya OEM app API (`/api.json` gateway)."""

    def __init__(self, creds: dict, *, scheme: SignScheme | None = None,
                 session: requests.Session | None = None):
        self.host = creds.get("api_region_host", "a1-us.iotbing.com")
        self.client_id = creds["client_id"]
        self.app_secret = creds["app_secret"]
        self.bmp_secret = creds.get("bmp_secret")
        self.cert_sha256 = creds.get("cert_sha256")
        self.country_code = creds.get("country_code", "1")
        self.app_schema = creds.get("app_schema") or "tuyaSmart"
        self.scheme = scheme or default_scheme(creds)
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = "TY-UA=APP/Android/1.0/SDK/1.0"
        self.sid: str | None = None            # session id after login
        self.session_secret: str | None = None  # per-session signing salt
        self.device_id = uuid.uuid4().hex       # fake stable device install id

    # -- low level ----------------------------------------------------------

    def _call(self, action: str, version: str = "1.0", data: dict | None = None,
              *, requires_sid: bool = True) -> dict:
        now = int(time.time() * 1000)
        params = {
            "a": action,
            "v": version,
            "clientId": self.client_id,
            "time": str(now // 1000),
            "requestId": uuid.uuid4().hex,
            "lang": "en",
            "osSystem": "13",
            "os": "Android",
            "ttid": f"sdk_tuya_{self.device_id}",
            "et": "0.0.1",
            "sdkVersion": "3.37.5",
            "appVersion": "1.0.0",
            "appRnVersion": "5.60",
            "platform": "ONEPLUS",
            "deviceId": self.device_id,
        }
        if requires_sid:
            if not self.sid:
                raise TuyaOemError("not logged in")
            params["sid"] = self.sid
        payload = json.dumps(data or {}, separators=(",", ":"))
        params["postData"] = payload
        params["sign"] = self.scheme.sign(
            params, app_secret=self.app_secret,
            session_secret=self.session_secret,
            bmp_secret=self.bmp_secret, cert_sha256=self.cert_sha256)
        url = f"https://{self.host}/api.json"
        r = self.s.post(url, params=params, timeout=15)
        r.raise_for_status()
        body = r.json()
        if not body.get("success", False):
            raise TuyaOemError(f"{action}: {body.get('errorCode')} "
                               f"{body.get('errorMsg') or body}")
        return body.get("result", {})

    # -- auth ---------------------------------------------------------------

    def login(self, email: str, password: str) -> None:
        """Email + password login (US region)."""
        md5pw = hashlib.md5(password.encode()).hexdigest()
        result = self._call(
            "tuya.m.user.email.password.login", data={
                "email": email,
                "passwd": md5pw,
                "countryCode": self.country_code,
            }, requires_sid=False)
        self.sid = result.get("sid")
        self.session_secret = result.get("secret") or result.get("sessionSecret")
        if not self.sid:
            raise TuyaOemError("login returned no sid")

    # -- data ---------------------------------------------------------------

    def homes(self) -> list[dict]:
        return self._call("tuya.m.location.list")

    def devices(self) -> list[Device]:
        """All devices across all of the account's homes."""
        out: list[Device] = []
        seen: set[str] = set()
        for home in self.homes():
            gid = home.get("groupId") or home.get("gid")
            raw = self._call("tuya.m.my.group.device.list",
                             data={"gid": gid})
            for d in raw:
                did = d.get("devId")
                if not did or did in seen:
                    continue
                seen.add(did)
                out.append(Device(
                    dev_id=did,
                    name=d.get("name", did),
                    local_key=d.get("localKey", ""),
                    product_id=d.get("productId", ""),
                    category=d.get("category", ""),
                    ip=d.get("ip"),
                    version=d.get("version"),
                    online=bool(d.get("isOnline")),
                    raw=d,
                ))
        return out

    def schema(self, product_id: str) -> dict:
        """DP schema for a product (function + status definitions)."""
        return self._call("tuya.m.device.ref.info.my.list",
                          data={"devId": product_id})

    def panel_bundle_url(self, product_id: str, ui: str | None = None) -> dict:
        """RN panel bundle metadata; its JS holds the app's mode encoder."""
        return self._call("tuya.m.app.rn.info", version="2.0",
                          data={"productId": product_id, "ui": ui or ""})

    def publish_dps(self, dev_id: str, dps: dict) -> dict:
        """Cloud fallback: push DPs through the Tuya cloud to the device."""
        return self._call("tuya.m.device.dp.publish", data={
            "devId": dev_id,
            "dps": json.dumps(dps, separators=(",", ":")),
        })


def save_devices(devices: list[Device], path: str = "devices.json") -> None:
    p = Path(path)
    p.write_text(json.dumps([d.to_json() for d in devices], indent=2))
    p.chmod(0o600)
