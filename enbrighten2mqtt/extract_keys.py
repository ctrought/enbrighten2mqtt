"""Extract Tuya OEM mobile-API credentials from an Enbrighten (Jasco) APK.

The Enbrighten app is a white-label Tuya app. The Tuya mobile SDK signs every
API request with an HMAC key derived from three secrets baked into the APK:

  * ``appKey`` / clientId  - a short public-ish identifier
  * ``appSecret``          - the paired secret
  * a token hidden in ``assets/t_s.bmp`` - decoded from the pixel bytes

Newer SDKs combine those with the APK signing certificate's SHA-256. This
module pulls what it can statically (no app execution) and writes the result to
``secrets/oem.json``. Anything it cannot find is left null with a note so the
operator can fill it from a jadx dump.

Prior art: nalajcie/tuya-sign-hacking, rgsilva/homeassistant-positivo,
FlagX/ha-ledvance-tuya-resync-localkey.

Nothing here runs the app or phones home; it only reads the APK on disk.
"""

from __future__ import annotations

import json
import re
import struct
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class OemCredentials:
    """Everything needed to emulate the app's Tuya mobile API client."""

    app_schema: str | None = None          # e.g. "jasco" / "enbrighten"
    client_id: str | None = None           # appKey
    app_secret: str | None = None          # appSecret
    bmp_secret: str | None = None          # decoded from assets/t_s.bmp
    cert_sha256: str | None = None         # APK v2 signing cert, hex
    api_region_host: str = "a1-us.iotbing.com"   # the app's API host
    country_code: str = "1"                # US
    notes: list[str] = field(default_factory=list)

    def is_complete(self) -> bool:
        return bool(self.client_id and self.app_secret)


# ---- t_s.bmp decoder -------------------------------------------------------

def decode_ts_bmp(data: bytes) -> str | None:
    """Recover the secret string hidden in Tuya's ``t_s.bmp``.

    Tuya stores an ASCII string in the low bytes of the pixel array. The
    classic layout (per tuya-sign-hacking) is a 24-bit BMP where every pixel's
    blue channel carries one character until a NUL. We read the pixel-array
    offset from the BMP header and walk it, which is robust to size changes.
    """
    if len(data) < 54 or data[:2] != b"BM":
        return None
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    if pixel_offset >= len(data):
        return None
    out = bytearray()
    for b in data[pixel_offset:]:
        if b == 0:
            break
        if 32 <= b < 127:
            out.append(b)
        else:
            # Not the simple low-byte layout; bail rather than emit garbage.
            return None
    text = out.decode("ascii", "ignore")
    # A real secret is a longish token; reject obvious false positives.
    return text if len(text) >= 8 else None


# ---- APK scanning ----------------------------------------------------------

_KEY_PATTERNS = {
    # Tuya appKey is ~20 lowercase alnum chars; appSecret ~20-32.
    "client_id": re.compile(rb'"?app[_]?[kK]ey"?\s*[:=]\s*"([a-z0-9]{16,32})"'),
    "app_secret": re.compile(rb'"?app[_]?[sS]ecret"?\s*[:=]\s*"([a-z0-9]{16,32})"'),
    "app_schema": re.compile(rb'"?appSchema"?\s*[:=]\s*"([a-z0-9_]{3,20})"'),
}

# Resource strings Tuya SDK reads at runtime.
_RES_PATTERNS = {
    "client_id": re.compile(rb'name="tuya_smart_appkey">\s*([a-z0-9]{16,32})'),
    "app_secret": re.compile(rb'name="tuya_smart_secret">\s*([a-z0-9]{16,32})'),
}


def scan_apk(apk_path: Path) -> OemCredentials:
    creds = OemCredentials()
    with zipfile.ZipFile(apk_path) as z:
        names = z.namelist()

        # 1) t_s.bmp
        for n in names:
            if n.lower().endswith("t_s.bmp") or n.lower().endswith("/t_s.bmp"):
                secret = decode_ts_bmp(z.read(n))
                if secret:
                    creds.bmp_secret = secret
                    creds.notes.append(f"bmp_secret from {n}")
                break

        # 2) appKey/appSecret in resources.arsc and classes/assets
        scan_targets = [n for n in names if n.endswith(
            ("resources.arsc", ".xml", ".json", ".properties"))]
        scan_targets += [n for n in names if n.startswith("assets/")]
        for n in scan_targets:
            try:
                blob = z.read(n)
            except (KeyError, RuntimeError):
                continue
            for field_name, pat in {**_KEY_PATTERNS, **_RES_PATTERNS}.items():
                if getattr(creds, field_name):
                    continue
                m = pat.search(blob)
                if m:
                    setattr(creds, field_name, m.group(1).decode("ascii"))
                    creds.notes.append(f"{field_name} from {n}")

        # 3) signing cert SHA-256 (APK Signing Block v2 / META-INF cert)
        creds.cert_sha256 = _cert_sha256(z, names, creds)

    if not creds.is_complete():
        creds.notes.append(
            "Incomplete: open the APK in jadx, search for 'appKey'/'appSecret' "
            "or Tuya init (TuyaSdk.init), and fill secrets/oem.json by hand.")
    return creds


def _cert_sha256(z: zipfile.ZipFile, names, creds: OemCredentials) -> str | None:
    import hashlib
    for n in names:
        if n.upper().startswith("META-INF/") and n.upper().endswith(
                (".RSA", ".EC", ".DSA")):
            try:
                from cryptography.x509 import load_der_x509_certificate
                from cryptography.hazmat.primitives import hashes
                # PKCS7 container: find the embedded cert DER.
                blob = z.read(n)
                # Best-effort: hash the whole signature block's cert via pkcs7.
                from cryptography.hazmat.primitives.serialization import pkcs7
                certs = pkcs7.load_der_pkcs7_certificates(blob)
                if certs:
                    digest = certs[0].fingerprint(hashes.SHA256())
                    return digest.hex()
            except Exception as e:  # noqa: BLE001
                creds.notes.append(f"cert parse failed for {n}: {e}")
    return None


def extract(apk_path: str, out_path: str = "secrets/oem.json") -> OemCredentials:
    apk = Path(apk_path)
    if not apk.exists():
        raise FileNotFoundError(apk)
    creds = scan_apk(apk)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(creds), indent=2))
    out.chmod(0o600)
    return creds
