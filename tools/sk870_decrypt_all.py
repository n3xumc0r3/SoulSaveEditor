#!/usr/bin/env python3
"""
sk870_decrypt_all.py — try every known Soul Knight save scheme on a blob.

Schemes (docs/REVERSING.md §3.2, tools/sk870_crept_crypto.py):
  1. DES statistic : key crst1\\0\\0\\0   IV Ahbool\\0\\0   (payload often Base64)
  2. DES default   : key iambo\\0\\0\\0   IV Ahbool\\0\\0   (payload often Base64)
  3. DES SDK       : key Ahbool\\0\\0    IV abo123\\0\\0   (8.7.0 SDK strings)
  4. poly-XOR      : out[i] = in[i] ^ ((i%5)*(i%15)%92) ^ key[i%len(key)], key PASSWORD
  5. repeat-XOR    : key slcz}guc\\x7fWmlkJ_  (legacy game.data)
  6. Rijndael-128  : fixed key/IV 1234567890123456 (migration, 8.7.0)
  7. per-install Rijndael/AES256 — pass keys from shared_prefs/*.v2.playerprefs.xml:
     --rijndael KEY --riv IV | --aes KEY --aiv IV
     (accepts raw string, hex, or base64 - all interpretations are tried)

Usage:
  python sk870_decrypt_all.py blob.bin
  python sk870_decrypt_all.py blob.bin --rijndael <key> --riv <iv>
  python sk870_decrypt_all.py selftest

Candidates are scored by printability + known markers (GPV2_, JSON head...)
and the best ones are written next to the input:
  <input>.dec_<scheme>.bin
"""

import argparse
import base64
import binascii
import string
import sys

try:
    from Crypto.Cipher import AES, DES
except ImportError:
    AES = DES = None

# --- constants recovered from the game ---------------------------------------

DES_STAT_KEY = b"crst1\x00\x00\x00"
DES_DEF_KEY = b"iambo\x00\x00\x00"
DES_SDK_KEY = b"Ahbool\x00\x00"
DES_LEGACY_IV = b"Ahbool\x00\x00"
DES_SDK_IV = b"abo123\x00\x00"

POLY_KEY = b"PASSWORD"
XOR_GAME_KEY = b"slcz}guc\x7fWmlkJ_"
RIJNDAEL_FIXED = b"1234567890123456"   # migration key/IV seen in 8.7.0

MARKERS = (b"gpv2_", b"player", b"weapon", b"hero", b'"nick', b"save",
           b"stage", b"knight", b"pet", b"unlock")


def pad_pkcs5(data: bytes) -> bytes:
    n = 8 - len(data) % 8
    return data + bytes([n]) * n


def strip_pkcs5(data: bytes) -> bytes:
    if not data:
        return data
    p = data[-1]
    if 1 <= p <= 8 and data[-p:] == bytes([p]) * p:
        return data[:-p]
    return data


def poly_xorm(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ ((i % 5) * (i % 15) % 92) ^ key[i % len(key)]
                 for i, b in enumerate(data))


def xor_repeat(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def des_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if DES is None:
        raise RuntimeError("pycryptodome is required: pip install pycryptodome")
    if len(data) % 8 or len(data) == 0:
        raise ValueError("not DES-blocked")
    return strip_pkcs5(DES.new(key, DES.MODE_CBC, iv).decrypt(data))


def aes_cbc_decrypt(data: bytes, key: bytes, iv: bytes) -> bytes:
    if AES is None:
        raise RuntimeError("pycryptodome is required: pip install pycryptodome")
    if len(key) not in (16, 24, 32):
        raise ValueError("bad AES key size %d" % len(key))
    if len(data) % 16 or len(data) == 0:
        raise ValueError("not AES-blocked")
    return AES.new(key, AES.MODE_CBC, iv).decrypt(data)  # no unpad, keep raw


def key_variants(s: str):
    """raw string, hex, base64 - whichever decodes."""
    out = []
    s = s.strip()
    if s:
        out.append(("str", s.encode("utf-8", "ignore")))
    try:
        h = bytes.fromhex(s)
        if h:
            out.append(("hex", h))
    except ValueError:
        pass
    try:
        b = base64.b64decode(s, validate=True)
        if b:
            out.append(("b64", b))
    except (binascii.Error, ValueError):
        pass
    return out


def maybe_b64(data: bytes):
    """(decoded, tag) if the blob looks like base64 text, else None."""
    t = data.strip()
    if len(t) < 16 or len(t) % 4:
        return None
    ok_chars = set(string.ascii_letters + string.digits + "+/=\r\n")
    if not set(t) <= ok_chars:
        return None
    try:
        d = base64.b64decode(t, validate=True)
        return (d, "b64")
    except (binascii.Error, ValueError):
        return None


def score(pt: bytes) -> float:
    if not pt:
        return -1.0
    printable = sum(1 for x in pt[:4096] if 32 <= x < 127 or x in (9, 10, 13))
    s = printable / min(len(pt), 4096)
    low = pt[:8192].lower()
    for m in MARKERS:
        if m in low:
            s += 0.5
    if pt.lstrip()[:1] in (b"{", b"["):
        s += 1.0
    return s


def schemes(data: bytes, extra: list):
    cands = [("raw", data)]
    b = maybe_b64(data)
    if b:
        cands.append(b)
    for tag, blob in cands:
        yield ("des_stat_%s" % tag, lambda d=blob: des_cbc_decrypt(d, DES_STAT_KEY, DES_LEGACY_IV))
        yield ("des_def_%s" % tag, lambda d=blob: des_cbc_decrypt(d, DES_DEF_KEY, DES_LEGACY_IV))
        yield ("des_sdk_%s" % tag, lambda d=blob: des_cbc_decrypt(d, DES_SDK_KEY, DES_SDK_IV))
        yield ("polyxor_%s" % tag, lambda d=blob: poly_xorm(d, POLY_KEY))
        yield ("xor_game_%s" % tag, lambda d=blob: xor_repeat(d, XOR_GAME_KEY))
        yield ("rijndael128_fixed_%s" % tag, lambda d=blob: aes_cbc_decrypt(d, RIJNDAEL_FIXED, RIJNDAEL_FIXED))
        for name, fn in extra:
            yield ("%s_%s" % (name, tag), lambda f=fn, d=blob: f(d))


def keyfns(name, kstr, istr, minkey):
    """build (name, fn) pairs for every key/iv interpretation."""
    out = []
    for kt, k in key_variants(kstr):
        if len(k) < minkey or len(k) not in (16, 24, 32):
            continue
        for it, iv in key_variants(istr) or [("none", b"\x00" * 16)]:
            if len(iv) != 16:
                continue
            out.append((f"{name}[k{kt}{len(k)}-i{it}]",
                        lambda d, kk=k, ii=iv: aes_cbc_decrypt(d, kk, ii)))
    return out


def run(path: str, rij_key, rij_iv, aes_key, aes_iv) -> int:
    data = open(path, "rb").read()
    print(f"[*] {path}: {len(data)} bytes, head={data[:16].hex()}")

    extra = []
    if rij_key and rij_iv:
        extra += keyfns("rijndael128", rij_key, rij_iv, 16)
    if aes_key and aes_iv:
        extra += keyfns("aes256", aes_key, aes_iv, 16)

    results = []
    for name, fn in schemes(data, extra):
        try:
            pt = fn()
        except Exception:
            continue
        sc = score(pt)
        if sc >= 0.6:
            results.append((sc, name, pt))

    if not results:
        print("[!] no plausible plaintext found with known schemes")
        print("    -> payload is probably per-install Rijndael/AES;")
        print("       grab DataRijndael128Key/DataRijndaelIV/DataAES256Key from")
        print("       shared_prefs/*.v2.playerprefs.xml and rerun with --rijndael/--aes")
        return 1

    results.sort(key=lambda x: -x[0])
    for sc, name, pt in results[:6]:
        out = f"{path}.dec_{name}.bin"
        with open(out, "wb") as f:
            f.write(pt)
        preview = "".join(chr(c) if 32 <= c < 127 else "." for c in pt[:160])
        print(f"[+] score={sc:.2f} {name:28s} -> {out}")
        print(f"    {preview}")
    return 0


def selftest() -> int:
    ok = True
    sample = b'{ "player": { "gpv2_test": 1 }, "weapon": "w" }'
    for name, enc, dec in [
        ("des_stat", lambda d: DES.new(DES_STAT_KEY, DES.MODE_CBC, DES_LEGACY_IV).encrypt(pad_pkcs5(d)),
                     lambda d: des_cbc_decrypt(d, DES_STAT_KEY, DES_LEGACY_IV)),
        ("des_sdk", lambda d: DES.new(DES_SDK_KEY, DES.MODE_CBC, DES_SDK_IV).encrypt(pad_pkcs5(d)),
                    lambda d: des_cbc_decrypt(d, DES_SDK_KEY, DES_SDK_IV)),
        ("polyxor", lambda d: poly_xorm(d, POLY_KEY), lambda d: poly_xorm(d, POLY_KEY)),
        ("xor_game", lambda d: xor_repeat(d, XOR_GAME_KEY), lambda d: xor_repeat(d, XOR_GAME_KEY)),
        ("rijndael128", lambda d: AES.new(RIJNDAEL_FIXED, AES.MODE_CBC, RIJNDAEL_FIXED).encrypt(pad_pkcs5(d)),
                        lambda d: strip_pkcs5(aes_cbc_decrypt(d, RIJNDAEL_FIXED, RIJNDAEL_FIXED))),
    ]:
        rt = dec(enc(sample))
        print(f"[*] {name}: {'OK' if rt == sample else 'FAIL'}")
        ok &= rt == sample
    print("[*] SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?")
    ap.add_argument("--rijndael", help="Rijndael-128 key from playerprefs XML")
    ap.add_argument("--riv", help="Rijndael IV from playerprefs XML")
    ap.add_argument("--aes", help="AES-256 key from playerprefs XML")
    ap.add_argument("--aiv", help="AES IV from playerprefs XML")
    ap.add_argument("selftest_", nargs="*", help=argparse.SUPPRESS)
    a = ap.parse_args()

    if not a.path and not a.selftest_:
        ap.print_help()
        return 2
    if "selftest" in (a.path or "") + "".join(a.selftest_):
        return selftest()
    return run(a.path, a.rijndael, a.riv, a.aes, a.aiv)


if __name__ == "__main__":
    sys.exit(main())
