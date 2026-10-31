"""Tuya/Thing OEM mobile-API client for the Enbrighten app.

Logs into the user's own Enbrighten account (email + password) and reads back
their devices' localKeys. Request signing is in signing.py and bmp_token.py.
No Tuya developer project is needed — just the user's credentials.

Flow: email token.create -> RSA-encrypt password -> email.password.login ->
location list -> per-home device list (each device carries its localKey).
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from .signing import sign_request


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
        return {k: getattr(self, k) for k in (
            "dev_id", "name", "local_key", "product_id",
            "category", "ip", "version", "online")}


class TuyaOemError(RuntimeError):
    pass


def _rsa_encrypt(plain: str, public_key_hex: str, exponent_hex: str) -> str:
    """RSA/ECB/PKCS1v1.5 encrypt, returns lowercase hex (Tuya login scheme)."""
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.backends import default_backend
    n = int(public_key_hex, 16)
    e = int(exponent_hex, 16)
    pub = rsa.RSAPublicNumbers(e, n).public_key(default_backend())
    ct = pub.encrypt(plain.encode(), padding.PKCS1v15())
    return ct.hex()


class TuyaOemApi:
    def __init__(self, creds: dict, *, session: requests.Session | None = None):
        self.host = creds.get("api_region_host", "a1-us.iotbing.com")
        self.client_id = creds["client_id"]
        self.hmac_key = creds["hmac_key"]            # package_cert_bmp_secret (see signing.py)
        self.country_code = creds.get("country_code", "1")
        self.ttid = creds.get("ttid", "enbrightensmart")
        self.ch_key = creds.get("ch_key", "")   # channel key seen in app traffic
        self.s = session or requests.Session()
        self.s.headers["User-Agent"] = "TY-UA=APP/Android/1.2.6/SDK/6.11.0"
        self.device_id = uuid.uuid4().hex + uuid.uuid4().hex[:8]  # 40 hex
        self.sid: str | None = None

    # -- low level ----------------------------------------------------------

    def _call(self, action: str, version: str = "1.0", data: dict | None = None,
              *, requires_sid: bool = True) -> dict:
        params = {
            "a": action,
            "v": version,
            "clientId": self.client_id,
            "os": "Android",
            "lang": "en",
            "ttid": self.ttid,
            "deviceId": self.device_id,
            "appVersion": "1.2.6",
            "sdkVersion": "6.11.0",
            "time": str(int(time.time())),
            "requestId": str(uuid.uuid4()),
            "et": "0",                     # plaintext postData (no et=3 encryption)
            "chKey": self.ch_key,          # channel key (app constant), signed
            "cp": "gzip",
            "deviceCoreVersion": "6.11.0",
            "appRnVersion": "5.99",
            "osSystem": "14",
            "nd": "1",
        }
        if data is not None:
            params["postData"] = json.dumps(data, separators=(",", ":"))
        if requires_sid:
            if not self.sid:
                raise TuyaOemError("not logged in")
            params["sid"] = self.sid
        params["sign"] = sign_request(params, self.hmac_key)
        r = self.s.post(f"https://{self.host}/api.json", data=params, timeout=15)
        r.raise_for_status()
        body = r.json()
        if not body.get("success", False):
            raise TuyaOemError(f"{action}: {body.get('errorCode')} "
                               f"{body.get('errorMsg') or body}")
        return body.get("result", body)

    # -- auth ---------------------------------------------------------------

    def login(self, email: str, password: str) -> None:
        tok = self._call("thing.m.user.email.token.create", data={
            "countryCode": self.country_code, "email": email,
        }, requires_sid=False)
        token = tok["token"]
        pub, exp = tok["publicKey"], tok["exponent"]
        # Thing SDK: passwd = RSA/PKCS1( md5hex(password) ). The hash is plain
        # lowercase MD5 hex -- MD5Util.md5AsBase64 is a misnomer, it returns
        # HexUtil.bytesToHexString(md5(x)) -- and there is NO token prefix: the
        # token travels only in its own postData field.
        md5pw = hashlib.md5(password.encode()).hexdigest()
        enc = _rsa_encrypt(md5pw, pub, exp)
        result = self._call("thing.m.user.email.password.login", version="3.0",
                            data={
            "countryCode": self.country_code, "email": email,
            "passwd": enc, "options": '{"group": 1}',
            "token": token, "ifencrypt": 1,
        }, requires_sid=False)
        self.sid = result.get("sid")
        if not self.sid:
            raise TuyaOemError("login returned no sid")

    # -- data ---------------------------------------------------------------

    def homes(self) -> list[dict]:
        res = self._call("thing.m.location.extend.list")
        return res if isinstance(res, list) else res.get("list", [])

    def devices(self) -> list[Device]:
        out: list[Device] = []
        seen: set[str] = set()
        for home in self.homes():
            gid = home.get("groupId") or home.get("gid") or home.get("id")
            raw = self._call("thing.m.my.group.device.list", data={"gid": gid})
            items = raw if isinstance(raw, list) else raw.get("deviceList", raw.get("list", []))
            for d in items:
                did = d.get("devId")
                if not did or did in seen:
                    continue
                seen.add(did)
                out.append(Device(
                    dev_id=did, name=d.get("name", did),
                    local_key=d.get("localKey", ""), product_id=d.get("productId", ""),
                    category=d.get("category", ""), ip=d.get("ip"),
                    version=str(d.get("version") or ""), online=bool(d.get("isOnline")),
                    raw=d))
        return out


def save_devices(devices: list[Device], path: str = "devices.json") -> None:
    import os
    p = Path(path)
    p.write_text(json.dumps([d.to_json() for d in devices], indent=2))
    os.chmod(p, 0o600)
