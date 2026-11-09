#!/usr/bin/env python3
"""litmulti.py — one-pass multi-pattern scan of printable strings.

Usage: python3 litmulti.py <file>
Prints unique fragments around each match for every pattern group.
"""
import re
import sys

path = sys.argv[1]
data = open(path, 'rb').read()
runs = [m.group().decode('ascii') for m in re.finditer(rb'[\x20-\x7e]{20,}', data)]
print(f'-- {len(runs)} runs >=20 chars --')

pats = {
    'GPV':    (r'GPV[0-9][\w_]*', 30, 90),
    'SNAP':   (r'snapshot', 50, 90),
    'BAN':    (r'banned|frozen|Err/Ban|Punish', 60, 90),
    'VER552': (r'5\.5\.2', 60, 80),
    'MIG':    (r'ChannelMigration', 40, 90),
    'LEGACY': (r'[Ll]egenc?y[A-Z]\w*|\w*[Ll]egenc?y/', 40, 80),
}

for name, (p, lim, w) in pats.items():
    rx = re.compile(p, re.I)
    seen, frags = set(), []
    for s in runs:
        for mm in rx.finditer(s):
            a = max(0, mm.start() - w // 2)
            b = min(len(s), mm.end() + w)
            frag = s[a:b]
            key = frag[:120]
            if key in seen:
                continue
            seen.add(key)
            frags.append(frag)
            break
    print(f'==== {name}: {len(frags)} unique frags ====')
    for h in frags[:lim]:
        print(h[:230])
    print()

# full URLs, unique
url_rx = re.compile(r'https?://[\w.\-]+(?:/[\w.\-/]*?)?(?=[^/\w.\-]|$)')
seen = set()
print('==== URLS ====')
for s in runs:
    for mm in url_rx.finditer(s):
        u = mm.group()
        if u in seen or len(u) < 12:
            continue
        seen.add(u)
        if len(seen) > 60:
            break
    if len(seen) > 60:
        break
for u in sorted(seen):
    print(u)
