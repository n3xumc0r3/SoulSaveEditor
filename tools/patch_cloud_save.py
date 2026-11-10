#!/usr/bin/env python3
"""
patch_cloud_save.py - patch the dumped Soul Knight 8.7.0 cloud save (GPGS snapshot).

Input : the dumped snapshot .bin (UTF-16LE JSON, no BOM) or the pretty UTF-8 JSON
Output: .bin ready for writeSnapFile() (UTF-16LE, compact, ensure_ascii=True)

The original document mixes \\uXXXX and raw escapes, so a byte-identical
rebuild is impossible; the game's JSON parser accepts both styles, therefore
we rebuild with ensure_ascii=True (valid JSON, same keys/values).

The gem ledger should be kept consistent (see docs/CLOUD-DUMP-870.md):
    gems = lastGems = X, varGems = 0

Usage:
  python patch_cloud_save.py snap_SoulKnightCloud_XXXX.bin cloud_patched.bin \
      --set gameData.gems=1124375 \
      --set gameData.lastGems=1124375 \
      --set gameData.varGems=0

  --json-if-utf8 : treat input as UTF-8 JSON file instead of UTF-16LE bin
Values are parsed as JSON literals: numbers, true/false/null, "strings".
"""
import argparse
import json
import sys


def load_input(path, as_utf8):
    if as_utf8:
        raw = open(path, 'rb').read()
        return json.loads(raw.decode('utf-8')), 'utf8-json'
    raw = open(path, 'rb').read()
    # UTF-16LE without BOM is the cloud-save format; tolerate BOM variants.
    if raw[:2] in (b'\xff\xfe', b'\xfe\xff'):
        raw = raw[2:]
    try:
        text = raw.decode('utf-16-le')
    except UnicodeDecodeError as e:
        sys.exit(f'[!] not UTF-16LE and no --json-if-utf8: {e}')
    return json.loads(text), 'utf16le-bin'


def assign(obj, dotted, value):
    parts = dotted.split('.')
    cur = obj
    for p in parts[:-1]:
        if isinstance(cur, list):
            cur = cur[int(p)]
        else:
            if p not in cur:
                sys.exit(f'[!] path {dotted!r}: key {p!r} missing')
            cur = cur[p]
    last = parts[-1]
    if isinstance(cur, list):
        cur[int(last)] = value
    else:
        if last not in cur:
            sys.exit(f'[!] path {dotted!r}: key {last!r} missing (typo guard)')
        cur[last] = value


def parse_value(s):
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        sys.exit(f'[!] value {s!r} is not a JSON literal (numbers: 123, 1.5; '
                 f'strings need quotes: \\"abc\\"; null / true / false)')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('infile')
    ap.add_argument('outfile')
    ap.add_argument('--set', action='append', default=[], metavar='PATH=VALUE')
    ap.add_argument('--json-if-utf8', action='store_true')
    args = ap.parse_args()

    obj, src = load_input(args.infile, args.json_if_utf8)
    print(f'[i] loaded {args.infile} ({src}), top-level keys: {len(obj)}')

    for pair in args.set:
        if '=' not in pair:
            sys.exit(f'[!] --set expects PATH=VALUE, got {pair!r}')
        path, _, val = pair.partition('=')
        v = parse_value(val)
        assign(obj, path, v)
        print(f'[+] set {path} = {v!r}')

    text = json.dumps(obj, ensure_ascii=True, separators=(',', ':'))
    data = text.encode('utf-16-le')
    open(args.outfile, 'wb').write(data)
    print(f'[+] wrote {args.outfile}: {len(data)} bytes (UTF-16LE, no BOM, compact)')
    if len(data) > 2_500_000:
        print('[!] WARNING: >2.5MB - GPGS size limit risk')
    print('[i] next: adb push <outfile> /sdcard/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/')
    print('           then: writeSnapFile("SoulKnightCloud", "<path on device>")')


if __name__ == '__main__':
    main()
