"""Signing tests for the Tuya/Thing OEM mobile API (SDK 6.11.0).

The pure-algorithm tests use dummy values and need no secrets. The ground-truth
regression only runs when the gitignored secrets/oem.json and the captured
re_workspace/ground_truth_sign.txt are present (developer machine), so no secret
or capture is committed.
"""

import json
from pathlib import Path

import pytest

from enbrighten2mqtt.cloud.signing import (
    build_hmac_key, cert_sha256_colon, _swap_sign_string, post_data_sign,
    string_to_sign, sign_request)

REPO = Path(__file__).resolve().parent.parent


def test_cert_sha256_colon_is_upper_colon():
    assert cert_sha256_colon("aabbcc") == "AA:BB:CC"
    assert cert_sha256_colon("AA:bb:CC") == "AA:BB:CC"


def test_build_hmac_key_order_is_package_cert_bmp_secret():
    # package _ cert(upper colon) _ bmp _ secret
    key = build_hmac_key("aabb", "BMPTOK", "SECRET", "com.example.app")
    assert key == "com.example.app_AA:BB_BMPTOK_SECRET"


def test_swap_sign_string_matches_native():
    # swapSignString(s) = s[8:16] + s[0:8] + s[24:32] + s[16:24]  (32-char md5hex)
    s = "0123456789abcdeffedcba9876543210"
    assert _swap_sign_string(s) == s[8:16] + s[0:8] + s[24:32] + s[16:24]


def test_post_data_sign_is_swapped_md5hex():
    import hashlib
    raw = '{"a":1}'
    expect = _swap_sign_string(hashlib.md5(raw.encode()).hexdigest())
    assert post_data_sign(raw) == expect


def test_string_to_sign_whitelist_sort_and_join():
    params = {
        "a": "x.y.z", "v": "1.0", "clientId": "cid", "time": "123",
        "os": "Android", "sdkVersion": "6.11.0",   # not whitelisted -> dropped
        "cp": "gzip",                                # not whitelisted -> dropped
        "lat": "",                                   # empty -> dropped
    }
    sts = string_to_sign(params)
    assert sts == "a=x.y.z||clientId=cid||os=Android||time=123||v=1.0"


def test_string_to_sign_replaces_postdata_with_swapped_md5():
    params = {"a": "act", "postData": '{"k":"v"}'}
    sts = string_to_sign(params)
    assert "postData=" + post_data_sign('{"k":"v"}') in sts
    assert '{"k":"v"}' not in sts  # raw postData never appears


_GT = REPO / "re_workspace" / "ground_truth_sign.txt"
_SECRETS = REPO / "secrets" / "oem.json"


@pytest.mark.skipif(not (_GT.exists() and _SECRETS.exists()),
                    reason="needs local secrets + captured ground truth")
def test_ground_truth_signature_reproduces():
    """HMAC-SHA256(derived key, captured string-to-sign) == captured sign."""
    sts = sign = None
    for line in _GT.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if sts is None:
            sts = line
        elif sign is None:
            sign = line
    key = json.loads(_SECRETS.read_text())["hmac_key"]
    import hmac as _h, hashlib as _hh
    assert _h.new(key.encode(), sts.encode(), _hh.sha256).hexdigest() == sign
