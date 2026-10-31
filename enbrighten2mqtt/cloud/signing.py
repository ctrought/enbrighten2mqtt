"""Tuya/Thing mobile-API request signing (as used by the Enbrighten app).

Algorithm:
  1. From the request params, keep only the whitelisted keys (SIGN_KEYS) whose
     values are non-empty.
  2. Sort those keys lexicographically.
  3. The `postData` value is replaced by MD5(postData) (lowercase hex).
  4. Join as `key=value` with `||` between pairs  ->  the string-to-sign.
  5. sign = HMAC-SHA256(string-to-sign, HMAC_KEY), lowercase hex.

HMAC_KEY = "<package>_<cert_sha256>_<bmp_token>_<appSecret>", where:
  - package     is the app's Android package name (BuildConfig APPLICATION_ID),
                e.g. "com.enbrighten.smart",
  - cert_sha256 is the APK signing cert's SHA-256 as UPPERCASE colon-separated
                hex (32 bytes, 95 chars),
  - bmp_token   is decoded from assets/t_s.bmp for this appKey (see bmp_token.py),
  - appSecret   is the app's Thing/Tuya secret (from BuildConfig).

The session id (sid) is only ever a signed *parameter*, never part of the key,
so login and all post-login requests use this same static key.

Everything here is deterministic from constants shipped in the APK, so an
end user only needs to provide their Enbrighten email + password.
"""

from __future__ import annotations

import hashlib
import hmac

# Exact whitelist + order-independence from ThingApiSignManager (sorted at use).
SIGN_KEYS = [
    "a", "v", "lat", "lon", "lang", "deviceId", "appVersion", "ttid",
    "isH5", "h5Token", "os", "clientId", "postData", "time", "requestId",
    "et", "n4h5", "sid", "chKey", "sp",
]
PAIR_SEP = "||"
KV_SEP = "="


def cert_sha256_colon(hex64: str) -> str:
    """64-hex SHA-256 -> 'AA:BB:..' uppercase, 32 bytes (95 chars)."""
    h = hex64.replace(":", "").upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def build_hmac_key(cert_sha256_hex: str, bmp_token: str, app_secret: str,
                   package: str) -> str:
    """Compose the native signer's HMAC key (Thing SDK 6.11.0).

    Order is package || cert(uppercase colon) || bmp_token || appSecret, joined
    by '_'. The package-name prefix is what distinguishes 6.11.0 from the older
    documented `<cert>_<bmp>_<secret>` scheme.
    """
    return f"{package}_{cert_sha256_colon(cert_sha256_hex)}_{bmp_token}_{app_secret}"


def _swap_sign_string(s: str) -> str:
    # ThingApiSignManager.swapSignString on a 32-char md5-hex string.
    return s[8:16] + s[0:8] + s[24:32] + s[16:24]


def post_data_sign(post_data: str) -> str:
    # postDataMD5Hex = swapSignString(md5_hex(postData)); md5AsBase64 is hex.
    return _swap_sign_string(hashlib.md5(post_data.encode()).hexdigest())


def string_to_sign(params: dict) -> str:
    parts = []
    for key in sorted(params.keys()):
        if key not in SIGN_KEYS:
            continue
        val = params[key]
        if val is None or val == "":
            continue
        if key == "postData":
            val = post_data_sign(str(val))
        parts.append(f"{key}{KV_SEP}{val}")
    return PAIR_SEP.join(parts)


def sign_request(params: dict, hmac_key: str) -> str:
    sts = string_to_sign(params)
    return hmac.new(hmac_key.encode(), sts.encode(), hashlib.sha256).hexdigest()
