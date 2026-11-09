#!/usr/bin/env python3
"""litgrep.py — fast printable-string extractor with regex filter.

Usage: python3 litgrep.py <file> <regex> [max_lines]
Scans the file for runs of printable ASCII (>=4 chars) and prints
offset + string for every run matching the regex (case-insensitive).
"""
import re
import sys

path, pattern = sys.argv[1], sys.argv[2]
limit = int(sys.argv[3]) if len(sys.argv) > 3 else 200
rx = re.compile(pattern, re.I)
run_rx = re.compile(rb'[\x20-\x7e]{4,}')
n = 0
with open(path, 'rb') as f:
    for m in run_rx.finditer(f.read()):
        s = m.group().decode('ascii')
        if rx.search(s):
            print(f'{m.start():#x}: {s}')
            n += 1
            if n >= limit:
                print(f'... truncated at {limit}')
                break
print(f'-- {n} matches shown --')
