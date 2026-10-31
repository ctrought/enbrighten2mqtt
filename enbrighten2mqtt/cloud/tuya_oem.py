"""Tuya/Thing OEM mobile-API client for the Enbrighten app.

Logs into the user's own Enbrighten account (email + password) and reads back
their devices' localKeys. Request signing is in signing.py and bmp_token.py.
No Tuya developer project is needed — just the user's credentials.

Flow: username.token.get -> RSA-encrypt password -> email.password.login ->
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


def _rsa_encrypt(plain: str, public_key: str, exponent: str) -> str:
    """RSA/NONE/PKCS1Padding (== PKCS1 v1.5) encrypt, lowercase-hex output.

    token.create returns the modulus and exponent as base-10 DECIMAL strings;
    the app parses them with new BigInteger(str) (radix 10), so decode base 10,
    not hex. (A 1024-bit modulus is ~309 decimal digits -- parsing it as hex
    yields a wrong key and the server rejects the password as USER_PASSWD_WRONG.)
    """
    from cryptography.hazmat.primitives.asymmetric import rsa, padding
    from cryptography.hazmat.backends import default_backend
    n = int(public_key, 10)
    e = int(exponent, 10)
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
        # The token + RSA key for email login come from username.token.get
        # (v2.0, account in the "username" field, isUid=false), NOT from
        # email.token.create. email.token.create returns a usable-looking key
        # too, but the keypair it mints is not the one the password-login
        # handler validates against, so the server rejects the (correctly
        # encrypted) password as USER_PASSWD_WRONG.
        tok = self._call("thing.m.user.username.token.get", version="2.0", data={
            "countryCode": self.country_code, "username": email, "isUid": False,
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
        # Home/space list shown at app start: an array of HomeResponseBean,
        # each with a `gid` (long) reused as the homeId for the device list.
        res = self._call("m.life.home.space.list", version="1.0")
        if isinstance(res, list):
            return res
        return res.get("list", res.get("spaceList", res.get("datas", [])))

    def device_properties(self, dev_id: str):
        """The panel's cloud storage for a device, where DIY scenes are kept."""
        last: TuyaOemError | None = None
        # The panel calls it s.m.dev.property.get; other Tuya apps use these prefixes.
        for prefix in ("s", "thing", "tuya", "smartlife"):
            try:
                return self._call(f"{prefix}.m.dev.property.get", version="2.0",
                                  data={"devId": dev_id, "bizType": 0})
            except TuyaOemError as e:
                last = e
        raise last

    def schema(self) -> dict[str, dict]:
        """Best-effort per-product DP standard config, keyed by productId.

        Fetched from m.life.product.standard.config.list (v1.1, {gid}) for each
        home. This describes each product's datapoints (code/type/range) for
        generic device support; the Enbrighten Eternity lights are already fully
        handled by codec/eternity.py and do not need it. Returns {} on failure.
        """
        out: dict[str, dict] = {}
        for home in self.homes():
            gid = home.get("gid") or home.get("groupId") or home.get("id")
            try:
                res = self._call("m.life.product.standard.config.list",
                                 version="1.1", data={"gid": gid})
            except TuyaOemError:
                continue
            items = res if isinstance(res, list) else \
                res.get("list", res.get("datas", []))
            for it in items:
                pid = it.get("productId") or it.get("id")
                if pid:
                    out[pid] = it
        return out

    # Candidate home-device-list endpoints, in priority order. Different
    # Tuya/Thing home setups populate different lists, so we take the first
    # that returns devices. Each is (action, version, postData-template); {gid}
    # is substituted per home.
    _DEVICE_LIST_APIS = [
        ("m.life.my.group.device.list", "2.2", {"gid": "{gid}"}),
        ("m.life.app.smart.local.device.list", "1.1",
         {"homeId": "{gid}", "groupType": "homeGroup"}),
        ("thing.m.location.dashboard.list", "3.1",
         {"gid": "{gid}", "limit": 100, "options": "{}", "lat": "0", "lon": "0"}),
    ]

    @staticmethod
    def _extract_devices(resp) -> list[dict]:
        if isinstance(resp, list):
            return [x for x in resp if isinstance(x, dict)]
        if isinstance(resp, dict):
            for k in ("deviceList", "devices", "list", "datas", "data"):
                v = resp.get(k)
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    return v
        return []

    def _home_devices(self, gid) -> list[dict]:
        for action, ver, tmpl in self._DEVICE_LIST_APIS:
            data = {k: (gid if v == "{gid}" else
                        str(gid) if v == "{gid!s}" else v)
                    for k, v in tmpl.items()}
            try:
                items = self._extract_devices(
                    self._call(action, version=ver, data=data))
            except TuyaOemError:
                continue
            if items:
                return items
        return []

    def devices(self) -> list[Device]:
        out: list[Device] = []
        seen: set[str] = set()
        for home in self.homes():
            gid = home.get("gid") or home.get("groupId") or home.get("id")
            for d in self._home_devices(gid):
                did = d.get("devId") or d.get("id")
                if not did or did in seen:
                    continue
                seen.add(did)
                out.append(Device(
                    dev_id=did, name=d.get("name", did),
                    local_key=d.get("localKey", ""), product_id=d.get("productId", ""),
                    category=d.get("category", ""),
                    # The cloud "ip" is the device's public/WAN address, useless
                    # for LAN control -- leave it unset so the bridge discovers
                    # the LAN IP by UDP broadcast (see local/discovery.py).
                    ip=None,
                    version=str(d.get("verSw") or d.get("version") or ""),
                    online=bool(d.get("isOnline") or d.get("cloudOnline")), raw=d))
        return out


def save_devices(devices: list[Device], path: str = "devices.json") -> None:
    import os
    p = Path(path)
    p.write_text(json.dumps([d.to_json() for d in devices], indent=2))
    os.chmod(p, 0o600)
