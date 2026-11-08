#!/usr/bin/env python3
"""
sk870_metadata_decode.py — decoder for Soul Knight 8.7.0 `global-metadata.dat`

Soul Knight (ChillyRoom) protects its IL2CPP metadata with a lightweight
anti-dump mask instead of full encryption:

  * the whole first 64 KiB page (metadata header + start of literal data)
    is XOR-ed with the single byte 0x66, and the first 8 bytes are ALSO
    substituted: stored magic 0x12724394 instead of 0xFAB11BAF and a garbage
    version field instead of the real one;
  * every following 64 KiB page has only the window [0x1000, 0x5000) (16 KiB)
    XOR-ed with 0x66, the rest of the page is plaintext;
  * the real header is: magic 0xFAB11BAF + version 31 (Unity 2022.3.62f2)
    + 31 (offset, size) pairs = 256 bytes total; regions are contiguous and
    the last one ends exactly at EOF.

Usage:
    python3 sk870_metadata_decode.py <global-metadata.dat> [output.dat]

If <output.dat> is omitted, writes "<input>-decoded.dat" next to the input.
The header layout, region contiguity and known strings are verified after
decoding (exit code 1 on failure).
"""

import struct
import sys

MAGIC = 0xFAB11BAF
VERSION = 31                 # metadata v31 == Unity 2022.3.62f2
STORED_MAGIC = 0x12724394    # what the shipped file has instead of MAGIC
MASK = 0x66
PAGE = 0x10000               # 64 KiB
WIN_LO, WIN_HI = 0x1000, 0x5000   # masked window inside every page > 0
HEADER_PAIRS = 31            # (offset, size) pairs after magic+version
HEADER_SIZE = 8 + HEADER_PAIRS * 8   # == 256


def xor(data: bytes, key: int) -> bytes:
    return bytes(b ^ key for b in data)


def decode(raw: bytes) -> bytes:
    """Undo the 8.7.0 metadata mask. Pure byte transform, offsets unchanged."""
    buf = bytearray(raw)
    n = len(buf)

    # page 0: entirely masked
    page0 = min(PAGE, n)
    buf[0:page0] = xor(bytes(buf[0:page0]), MASK)

    # every other page: only [0x1000, 0x5000) is masked
    for base in range(PAGE, n, PAGE):
        lo = base + WIN_LO
        hi = min(base + WIN_HI, n)
        if lo >= n:
            break
        buf[lo:hi] = xor(bytes(buf[lo:hi]), MASK)

    # restore the real magic + version (they are substituted, not just masked)
    struct.pack_into("<II", buf, 0, MAGIC, VERSION)
    return bytes(buf)


def verify(dec: bytes, verbose: bool = True) -> bool:
    ok = True
    sanity, version = struct.unpack_from("<II", dec, 0)
    if sanity != MAGIC:
        print(f"[!] bad magic after decode: 0x{sanity:08X}")
        ok = False
    if version != VERSION:
        print(f"[!] unexpected version: {version}")
        ok = False

    pairs = struct.unpack_from(f"<{HEADER_PAIRS * 2}I", dec, 8)
    regions = list(zip(pairs[0::2], pairs[1::2]))

    # regions must be contiguous, starting right after the 256-byte header,
    # and the last one must end exactly at EOF
    expect = HEADER_SIZE
    for i, (off, size) in enumerate(regions):
        if off != expect:
            print(f"[!] region {i}: offset {off} (0x{off:X}), expected {expect} (0x{expect:X})")
            ok = False
        expect += size
    if expect != len(dec):
        print(f"[!] regions end at 0x{expect:X}, file size 0x{len(dec):X}")
        ok = False

    if verbose:
        print(f"[*] header: magic 0x{sanity:08X}, version {version}, "
              f"{len(regions)} regions, regions contiguous through EOF: {ok}")
        for i, (off, size) in enumerate(regions):
            print(f"    [{i:2d}] off=0x{off:08X} size=0x{size:X}")

    # known plaintext smoke tests
    for needle in (b"mscorlib", b"Assembly-CSharp", b"crst1", b"iambo"):
        found = dec.find(needle)
        print(f"[*] {needle.decode()!r}: "
              f"{'OK at 0x%X' % found if found >= 0 else 'NOT FOUND'}")
        if found < 0:
            ok = False
    return ok


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = sys.argv[1]
    if len(sys.argv) >= 3:
        dst = sys.argv[2]
    else:
        dst = (src[:-4] if src.lower().endswith(".dat") else src) + "-decoded.dat"

    with open(src, "rb") as f:
        raw = f.read()

    stored_magic = struct.unpack_from("<I", raw, 0)[0]
    if stored_magic == MAGIC:
        print("[*] input already looks decoded, copying as is")
        dec = raw
    else:
        print(f"[*] stored magic 0x{stored_magic:08X} "
              f"(expected 0x{MAGIC:08X}) -> decoding")
        dec = decode(raw)

    with open(dst, "wb") as f:
        f.write(dec)
    print(f"[*] wrote {dst} ({len(dec)} bytes)")

    return 0 if verify(dec) else 1


if __name__ == "__main__":
    sys.exit(main())
