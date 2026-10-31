"""Derive the Tuya 'BMP token' from assets/t_s.bmp for a given appKey.

Pure-Python reimplementation of tuya-sign-hacking's read-keys-from-bmp. The
token is one component of the API-signing HMAC key (see signing.py):
    hmac_key = <package> _ <cert_sha256> _ <bmp_token> _ <appSecret>

The pixel bytes at offsets derived from the appKey's hash encode coefficient
pairs (a_i, b_i); each key is the integer solving the linear system over the
powers of a_i, read back as an ASCII hex string. Uses exact rationals.
"""

from __future__ import annotations

from fractions import Fraction

PIXEL_DATA_OFFS = 0x36  # BMP header size; pixel array follows


def _i32(x: int) -> int:
    x &= 0xFFFFFFFF
    return x - 0x100000000 if x & 0x80000000 else x


def _java_hashcode(s: str) -> int:
    acc = 0
    for ch in s:
        acc = ((acc << 5) - acc + ord(ch)) & 0xFFFFFFFF  # acc*31 + ch, uint32
    return acc


def _bytes_to_hex(px: bytes, cnt: int, idx: int) -> str:
    out = []
    n = len(px)
    for _ in range(cnt):
        out.append(f"{px[idx % n]:02x}")
        idx += 1
    return "".join(out)


def _coeffs_to_key(coeffs: list[tuple[str, str]], mul: int) -> str:
    # Build the mul x (mul+1) matrix: row = [a^(mul-1), ..., a, 1, b]
    M: list[list[Fraction]] = []
    for (s1, s2) in coeffs:
        a = int(s1, 16)
        b = int(s2, 16)
        row = [Fraction(a ** exp) for exp in range(mul - 1, -1, -1)]
        row.append(Fraction(b))
        M.append(row)

    # Gaussian elimination to upper-triangular (exact), matching the C code.
    for row in range(mul):
        if M[row][row] == 0:
            row2 = row
            while True:
                row2 += 1
                if row2 >= mul:
                    raise ValueError("cannot make diagonal non-zero")
                if M[row2][row] != 0:
                    break
            M[row], M[row2] = M[row2], M[row]
        if row < mul - 1:
            a_xx = M[row][row]
            for y in range(row + 1, mul):
                a_xy = M[y][row]
                if a_xy != 0:
                    ratio = a_xx / a_xy
                    for col in range(row, mul + 1):
                        M[y][col] = M[y][col] * ratio - M[row][col]

    a_mm = M[mul - 1][mul - 1]
    a_m1 = M[mul - 1][mul]       # last row's b (column mul+1)
    if a_mm == 0:
        raise ValueError("final diagonal is zero")
    res = a_m1 / a_mm
    if res.denominator != 1:
        raise ValueError("result is not an integer")
    num = res.numerator
    if num < 0:
        raise ValueError("negative result")
    return format(num, "x")


def read_token(app_id: str, bmp_bytes: bytes) -> str:
    """Return the decoded BMP token (ASCII) for app_id."""
    px = bmp_bytes[PIXEL_DATA_OFFS:]
    n = len(px)

    h = _i32(_java_hashcode(app_id))
    if h < 0:
        h = -h
    h = (h % n) // 2

    keys_cnt = px[(h + 1) % n]
    coeffs_cnt = px[(h + 2) % n]
    if not (1 <= keys_cnt <= 4) or coeffs_cnt == 0:
        raise ValueError(f"appId/bmp mismatch (keys_cnt={keys_cnt}, coeffs_cnt={coeffs_cnt})")

    magic = ((px[(h + 3) % n] << 24) | (px[(h + 4) % n] << 16)
             | (px[(h + 5) % n] << 8) | px[(h + 6) % n])
    offs = _i32(h ^ magic)

    coeffs: list[tuple[str, str]] = []
    for _ in range(coeffs_cnt):
        v1_off = offs % n
        v1_len = px[v1_off]
        s1 = _bytes_to_hex(px, v1_len, v1_off + 1)
        v2_off = (v1_off + v1_len + 1) % n
        v2_len = px[v2_off]
        s2 = _bytes_to_hex(px, v2_len, v2_off + 1)
        vnext_off = (v2_off + v2_len + 1) % n
        vnext = ((px[vnext_off % n] << 24) | (px[(vnext_off + 1) % n] << 16)
                 | (px[(vnext_off + 2) % n] << 8) | px[(vnext_off + 3) % n])
        offs = _i32(v1_off ^ vnext)
        coeffs.append((s1, s2))

    key_hex = _coeffs_to_key(coeffs, coeffs_cnt)
    # hex -> ascii
    if len(key_hex) % 2:
        key_hex = "0" + key_hex
    return bytes.fromhex(key_hex).decode("latin1")


if __name__ == "__main__":
    import sys
    app_id, path = sys.argv[1], sys.argv[2]
    print(read_token(app_id, open(path, "rb").read()))
