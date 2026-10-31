"""Tuya mobile-API request signing.

Tuya's app SDK has used a few signing variants over the years. Rather than
hard-code one, we express each as a ``SignScheme`` and pick based on what the
APK analysis finds. All variants build a canonical string from the request
parameters and HMAC/MD5 it with an app-derived key.

The concrete recipe is pinned in Phase 1 against the real APK. Until then
``default_scheme`` returns the most common modern variant (HMAC-SHA256 over
sorted params, keyed by app_secret, optionally salted by the session secret).
"""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

# Params excluded from the signed canonical string (Tuya convention).
_SKIP = {"sign", "postData"}


def _canonical(params: dict) -> str:
    """`key=value||key=value` over sorted keys, with postData hashed in."""
    items = sorted((k, v) for k, v in params.items() if k not in _SKIP)
    base = "||".join(f"{k}={v}" for k, v in items)
    post = params.get("postData")
    if post:
        base += "||" + hashlib.sha256(post.encode()).hexdigest()
    return base


@dataclass
class SignScheme:
    algo: str = "hmac-sha256"   # or "md5"
    use_session_secret: bool = True
    use_bmp: bool = False
    use_cert: bool = False

    def _key(self, *, app_secret, session_secret, bmp_secret, cert_sha256):
        parts = [app_secret]
        if self.use_session_secret and session_secret:
            parts.append(session_secret)
        if self.use_bmp and bmp_secret:
            parts.append(bmp_secret)
        if self.use_cert and cert_sha256:
            parts.append(cert_sha256)
        return "_".join(parts)

    def sign(self, params: dict, *, app_secret: str,
             session_secret: str | None = None,
             bmp_secret: str | None = None,
             cert_sha256: str | None = None) -> str:
        key = self._key(app_secret=app_secret, session_secret=session_secret,
                        bmp_secret=bmp_secret, cert_sha256=cert_sha256)
        msg = _canonical(params)
        if self.algo == "md5":
            return hashlib.md5((msg + key).encode()).hexdigest()
        return hmac.new(key.encode(), msg.encode(), hashlib.sha256).hexdigest()


def default_scheme(creds: dict) -> SignScheme:
    """Pick a scheme from what the APK yielded.

    If the APK exposed a bmp token or cert hash, fold them in (newer SDK).
    Otherwise use the classic HMAC-SHA256 scheme.
    """
    return SignScheme(
        algo="hmac-sha256",
        use_session_secret=True,
        use_bmp=bool(creds.get("bmp_secret")),
        use_cert=bool(creds.get("cert_sha256")),
    )
