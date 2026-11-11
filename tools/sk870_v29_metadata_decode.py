#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
decrypt_metadata.py — расшифровка global-metadata.dat Soul Knight 8.7.0 (IL2CPP v29)
и полнотекстовый поиск литералов.

Схема шифрования (восстановлена ранее): XOR 0x66 по зонам
  [page*0x10000 + 0x1000, page*0x10000 + 0x5000) каждой 64КБ-страницы,
  плюс заголовок [0x0, 0x1000) первой страницы.

Использование:
  python3 decrypt_metadata.py decrypt  <in.dat> <out.dat>
  python3 decrypt_metadata.py strings  <dec.dat> <out.txt>      # все строки
  python3 decrypt_metadata.py grep     <dec.dat> <regex> [...]  # поиск по строкам
"""
import re
import struct
import sys


def decrypt(raw: bytes) -> bytes:
    out = bytearray(raw)
    n = len(raw)
    page = 0
    while page * 0x10000 < n:
        base = page * 0x10000
        lo = base if page == 0 else base + 0x1000
        hi = min(base + 0x5000, n)
        for o in range(lo, hi):
            out[o] ^= 0x66
        page += 1
    return bytes(out)


HEADER_MAGIC = 0xFAB11BAF


def parse_header(dec: bytes):
    sanity, version, strLitOff, strLitCnt, strOff, strCnt, evOff, evCnt, \
        propOff, propCnt, fieldOff, fieldCnt, typeOff, typeCnt, methOff, methCnt = \
        struct.unpack_from('<16I', dec, 0)
    assert sanity == HEADER_MAGIC, "magic mismatch: %08x" % sanity
    return dict(version=version, strLitOff=strLitOff, strLitCnt=strLitCnt,
                strOff=strOff, strCnt=strCnt, fieldOff=fieldOff, fieldCnt=fieldCnt)


def read_cstring(dec: bytes, off: int) -> str:
    end = dec.index(b'\x00', off)
    return dec[off:end].decode('utf-8', 'replace')


def extract_all_strings(dec: bytes):
    h = parse_header(dec)
    # string literals: пары (length, offset)
    lits = []
    for i in range(h['strLitCnt']):
        ln, off = struct.unpack_from('<II', dec, h['strLitOff'] + i * 8)
        if 0 < ln < 1 << 20:
            lits.append(dec[off:off + ln].decode('utf-8', 'replace'))
    # metadata strings: C-строки в зоне [strOff, strOff+strCnt)
    strs = []
    zone = dec[h['strOff']:h['strOff'] + h['strCnt']]
    pos = 0
    for chunk in zone.split(b'\x00'):
        if chunk:
            try:
                strs.append(chunk.decode('utf-8'))
            except UnicodeDecodeError:
                strs.append(chunk.decode('utf-8', 'replace'))
        pos += len(chunk) + 1
    return lits, strs


def main(argv):
    if len(argv) < 3:
        print(__doc__)
        return 2
    cmd = argv[1]
    if cmd == 'decrypt':
        raw = open(argv[2], 'rb').read()
        dec = decrypt(raw)
        open(argv[3], 'wb').write(dec)
        print('[ok] %d bytes -> %s' % (len(dec), argv[3]))
        return 0
    dec = open(argv[2], 'rb').read()
    lits, strs = extract_all_strings(dec)
    if cmd == 'strings':
        with open(argv[3], 'w', encoding='utf-8') as f:
            f.write('=== STRING LITERALS (%d) ===\n' % len(lits))
            for s in lits:
                f.write(s + '\n')
            f.write('\n=== METADATA STRINGS (names) (%d) ===\n' % len(strs))
            for s in strs:
                f.write(s + '\n')
        print('[ok] %d literals + %d names -> %s' % (len(lits), len(strs), argv[3]))
        return 0
    if cmd == 'grep':
        pats = argv[3:]
        rx = re.compile('|'.join('(?:%s)' % p for p in pats), re.I)
        seen = set()
        for s in lits:
            if rx.search(s) and s not in seen:
                seen.add(s)
                print('LIT  %r' % s)
        for s in strs:
            if rx.search(s) and s not in seen:
                seen.add(s)
                print('NAME %r' % s)
        return 0
    print(__doc__)
    return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv))
