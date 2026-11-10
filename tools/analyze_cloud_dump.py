#!/usr/bin/env python3
# Analyze the Soul Knight cloud snapshot dump (GPGS snapshot content).
# Verdict expected: plain JSON in UTF-16LE. Produce structure report + pretty JSON.
import hashlib, json, os, sys, collections, math

UP = '/home/z/my-project/upload'
DL = '/home/z/my-project/download'
RAW = os.path.join(UP, 'snap_SoulKnightCloud_1794336542160.bin')
EXPECT_SHA = '1490c88da14110f69bce0fa274e4ea7f67bcb37b1f15cd0936390fb73d3dfb4c'

KEYWORDS = ['gem', 'gold', 'money', 'coin', 'diamond', 'energy', 'level',
            'hero', 'weapon', 'material', 'seed', 'version', 'time', 'date',
            'name', 'count', 'unlock', 'skin', 'pet', 'season', 'diy']


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def entropy(b):
    if not b:
        return 0.0
    cnt = collections.Counter(b)
    n = len(b)
    return -sum((c / n) * math.log2(c / n) for c in cnt.values())


def printable_ratio(b):
    if not b:
        return 0.0
    ok = sum(1 for x in b if 32 <= x < 127 or x in (9, 10, 13))
    return ok / len(b)


def shape(obj, depth=0, maxdepth=6):
    """Compact structural description, lists sampled."""
    pad = '  ' * depth
    if isinstance(obj, dict):
        if depth >= maxdepth:
            return pad + '{...%d keys}' % len(obj)
        lines = [pad + '{']
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append(pad + '  "%s":' % k)
                lines.append(shape(v, depth + 2, maxdepth))
            else:
                s = repr(v)
                if len(s) > 80:
                    s = s[:60] + '...<%d chars>' % len(s)
                lines.append(pad + '  "%s": %s' % (k, s))
        lines.append(pad + '}')
        return '\n'.join(lines)
    if isinstance(obj, list):
        head = pad + '[len=%d]' % len(obj)
        if depth >= maxdepth or not obj:
            return head
        lines = [head]
        for el in obj[:2]:
            lines.append(shape(el, depth + 1, maxdepth))
        if len(obj) > 2:
            lines.append(pad + '  ... (%d more)' % (len(obj) - 2))
        return '\n'.join(lines)
    s = repr(obj)
    if len(s) > 80:
        s = s[:60] + '...<%d chars>' % len(s)
    return pad + s


def walk(obj, path='', out=None):
    if out is None:
        out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            walk(v, path + '/' + str(k), out)
    elif isinstance(obj, list):
        out.append((path + '[]', 'list[%d]' % len(obj), None))
        if obj:
            walk(obj[0], path + '[0]', out)
    else:
        v = obj
        if isinstance(v, str) and len(v) > 300:
            out.append((path, 'str<%d>' % len(v), v[:40] + '...'))
        else:
            out.append((path, type(v).__name__, v))
    return out


def main():
    data = open(RAW, 'rb').read()
    h = sha256(data)
    print('=== RAW DUMP ===')
    print('size   :', len(data))
    print('sha256 :', h, '(matches log:', h == EXPECT_SHA, ')')
    print('head   :', data[:48].hex(' '))
    print('tail   :', data[-48:].hex(' '))

    print()
    print('=== DECODE UTF-16LE ===')
    try:
        text = data.decode('utf-16-le')
        print('decode OK, chars =', len(text))
    except UnicodeDecodeError as e:
        print('decode FAILED:', e)
        return
    print('first 100 chars:', text[:100])
    print('last  100 chars:', text[-100:])

    print()
    print('=== JSON PARSE ===')
    obj = None
    try:
        obj = json.loads(text)
        print('parse OK; top-level:', type(obj).__name__)
    except json.JSONDecodeError as e:
        print('parse FAILED:', e)
        idx = text.rfind('}')
        print('last } at char', idx, 'of', len(text), '; trailing chars:', len(text) - idx - 1)
        try:
            obj = json.loads(text[:idx + 1])
            print('parse OK after trimming trailing', len(text) - idx - 1, 'chars')
        except Exception as e2:
            print('still failed:', e2)
            return

    assert isinstance(obj, dict)
    print('top-level keys:', list(obj.keys()))

    print()
    print('=== STRUCTURE (depth 4) ===')
    print(shape(obj, maxdepth=4))

    print()
    print('=== ALL SCALAR PATHS (flat list) ===')
    rows = walk(obj)
    print('total paths:', len(rows))
    kw_rows = []
    for p, t, v in rows:
        lp = p.lower()
        if any(k in lp for k in KEYWORDS):
            kw_rows.append((p, t, v))
    print('keyword-matched paths:', len(kw_rows))
    for p, t, v in kw_rows[:200]:
        vv = repr(v)
        if len(vv) > 100:
            vv = vv[:80] + '...'
        print('  %-70s %-10s %s' % (p, t, vv))

    print()
    print('=== LONG STRINGS (>300 chars) ===')
    n_long = 0
    for p, t, v in rows:
        if t and t.startswith('str<'):
            n_long += 1
            print('  %-70s %s' % (p, t))
    print('long strings total:', n_long)

    pretty_path = os.path.join(DL, 'SoulKnightCloud_pretty.json')
    with open(pretty_path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print()
    print('pretty JSON ->', pretty_path, os.path.getsize(pretty_path), 'bytes')

    print()
    print('=== THE TWO "DECRYPTED" FILES (sanity check) ===')
    for name in ['snap_SoulKnightCloud_1794336542160.bin.dec_polyxor_raw.bin',
                 'snap_SoulKnightCloud_1794336542160.bin.dec_xor_game_raw.bin']:
        b = open(os.path.join(UP, name), 'rb').read()
        ent = entropy(b)
        pr = printable_ratio(b)
        j1 = False
        for enc in ('utf-8', 'utf-16-le'):
            try:
                json.loads(b.decode(enc))
                j1 = True
                break
            except Exception:
                pass
        print('%-58s size=%d entropy=%.3f printable=%.2f json=%s' % (name, len(b), ent, pr, j1))
        odd = b[1::2]
        even = b[0::2]
        print('    even-byte printable=%.2f  odd-byte printable=%.2f' % (
            printable_ratio(even), printable_ratio(odd)))


if __name__ == '__main__':
    main()
