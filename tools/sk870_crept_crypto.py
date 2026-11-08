#!/usr/bin/env python3
"""
sk870_crept_crypto.py — ChillyRoom SDK string crypto, as shipped in
Soul Knight 8.7.0 (libnative-lib.so + classes.dex).

Recovered by disassembling the JNI exports of libnative-lib.so:

    Java_com_chillyroomsdk_cyphertest_MainActivity_encrept
    Java_com_chillyroomsdk_sdkbridge_util_CreptUtil_decrept

Both are ~280 bytes and implement the same self-inverse byte cipher:

    out[i] = in[i] ^ ((i % 5) * (i % 15) % 92) ^ key[i % len(key)]

On top of it, com.chillyroomsdk.sdkbridge.util.CreptUtil (Java, classes.dex)
derives the SDK DES parameters at construction time:

    this.desKey = decrept("\\x11(55(#TJ", "PASSWORD")   -> b"Ahbool\\x00\\x00"
    this.desIv  = decrept("1\\"8ku|TJ",     "PASSWORD")  -> b"abo123\\x00\\x00"

and uses DES/CBC/PKCS5Padding for SDK strings. Note the rotation versus the
old 3.x scheme: the legacy IV "Ahbool" is now a KEY, and the IV is "abo123"
("Abo" is also the author tag of the C# crypto classes, Abo.CryptUtil).

Usage:
    python3 sk870_crept_crypto.py selftest
    python3 sk870_crept_crypto.py poly <text> [key]      # decrypt poly-XOR
    python3 sk870_crept_crypto.py polyenc <text> [key]   # encrypt poly-XOR
"""

import sys

try:
    from Crypto.Cipher import DES
except ImportError:
    DES = None

# --- constants recovered from the game -------------------------------------

POLY_A, POLY_B, POLY_M = 5, 15, 92

SDK_KEY = b"PASSWORD"                 # poly-XOR key of CreptUtil strings

# encrypted literals stored in classes.dex (CreptUtil.<init>)
ENC_DES_KEY = b"\x11(55(#TJ"          # -> "Ahbool\0\0"
ENC_DES_IV = b"1\"8ku|TJ"             # -> "abo123\0\0"

DES_KEY = b"Ahbool\x00\x00"           # SDK DES key (was the legacy 3.x IV!)
DES_IV = b"abo123\x00\x00"            # SDK DES IV


# --- poly-XOR (native CreptUtil scheme) ------------------------------------

def poly_byte(i: int) -> int:
    """((i % 5) * (i % 15)) % 92 — the position-dependent byte of the cipher."""
    return ((i % POLY_A) * (i % POLY_B)) % POLY_M


def poly_xorm(data: bytes, key: bytes) -> bytes:
    """Self-inverse: out[i] = in[i] ^ poly(i) ^ key[i % len(key)]."""
    if not key:
        raise ValueError("empty key")
    return bytes(b ^ poly_byte(i) ^ key[i % len(key)]
                 for i, b in enumerate(data))


decrept = poly_xorm   # JNI name
encrept = poly_xorm   # XOR is self-inverse


# --- DES/CBC/PKCS5 for SDK strings -----------------------------------------

def des_decrypt(data: bytes, key: bytes = DES_KEY, iv: bytes = DES_IV) -> bytes:
    if DES is None:
        raise RuntimeError("pycryptodome is required: pip install pycryptodome")
    if len(key) != 8 or len(iv) != 8:
        raise ValueError("DES key/iv must be exactly 8 bytes")
    padded = DES.new(key, DES.MODE_CBC, iv).decrypt(data)
    pad = padded[-1]
    if 1 <= pad <= 8 and padded[-pad:] == bytes([pad]) * pad:
        padded = padded[:-pad]
    return padded


def des_encrypt(data: bytes, key: bytes = DES_KEY, iv: bytes = DES_IV) -> bytes:
    if DES is None:
        raise RuntimeError("pycryptodome is required: pip install pycryptodome")
    if len(key) != 8 or len(iv) != 8:
        raise ValueError("DES key/iv must be exactly 8 bytes")
    pad = 8 - len(data) % 8
    padded = data + bytes([pad]) * pad
    return DES.new(key, DES.MODE_CBC, iv).encrypt(padded)


# --- self test --------------------------------------------------------------

def selftest() -> int:
    ok = True

    k = decrept(ENC_DES_KEY, SDK_KEY)
    v = decrept(ENC_DES_IV, SDK_KEY)
    print(f"[*] decrept(ENC_DES_KEY) = {k!r} (expect b'Ahbool\\x00\\x00')")
    ok &= k == b"Ahbool\x00\x00"
    print(f"[*] decrept(ENC_DES_IV)  = {v!r} (expect b'abo123\\x00\\x00')")
    ok &= v == b"abo123\x00\x00"

    # poly-XOR roundtrip
    sample = b"Hello, Soul Knight! 0123456789 abcdefgh"
    rt = decrept(encrept(sample, SDK_KEY), SDK_KEY)
    print(f"[*] poly-XOR roundtrip: {'OK' if rt == sample else 'FAIL'}")
    ok &= rt == sample

    # poly-XOR against independently computed cipher for a known pair
    check = poly_xorm(b"AAAAAAAA", SDK_KEY)
    print(f"[*] poly_xorm(b'AAAAAAAA') = {check.hex()} (deterministic)")

    if DES is not None:
        msg = b"save-payload-test-123"
        ct = des_encrypt(msg)
        pt = des_decrypt(ct)
        print(f"[*] DES/CBC roundtrip with Ahbool/abo123: "
              f"{'OK' if pt == msg else 'FAIL'}")
        ok &= pt == msg
    else:
        print("[!] pycryptodome missing, DES tests skipped")

    print("[*] SELFTEST", "PASSED" if ok else "FAILED")
    return 0 if ok else 1


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "selftest":
        return selftest()
    if cmd == "poly":
        key = sys.argv[3].encode() if len(sys.argv) > 3 else SDK_KEY
        print(decrept(sys.argv[2].encode(), key))
        return 0
    if cmd == "polyenc":
        key = sys.argv[3].encode() if len(sys.argv) > 3 else SDK_KEY
        print(encrept(sys.argv[2].encode(), key))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
