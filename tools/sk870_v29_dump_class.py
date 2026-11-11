#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dump_class.py — дамп всех полей+методов typeDef'ов IL2CPP v29 по имени класса.

Layout typeDef (88Б), проверен эмпирически на statistic.data 8.7.0:
  nameIdx@0 nsIdx@4 byval@8 decl@12 parent@16 elem@20 genCont@24 flags@28
  fieldStart@32(i32) methodStart@36 eventStart@40 propStart@44
  nestedStart@48 ifaceStart@52 X@56
  counts(u16x8)@64: [64]=fieldCount [66]=? [68]=? [70]=?
  bitfield@84? token@80? (младшие не критичны)

Использование:
  python3 dump_class.py /tmp/global-metadata.dec.dat Class1 [Class2 ...]
  python3 dump_class.py /tmp/global-metadata.dec.dat --byfield zongzi2026ActivityData
"""
import struct
import sys


def load(dec_path):
    dec = open(dec_path, 'rb').read()
    pairs = []
    for i in range(31):
        off, sz = struct.unpack_from('<II', dec, 8 + i * 8)
        pairs.append((off, sz))
    sec = {'litTable': pairs[0], 'litData': pairs[1], 'strs': pairs[2],
           'types': pairs[19], 'fields': pairs[11], 'methods': pairs[5],
           'params': pairs[10]}
    return dec, sec


class Meta:
    def __init__(self, dec_path):
        self.dec, self.sec = load(dec_path)
        dec, sec = self.dec, self.sec
        sOff, sSz = sec['strs']
        self.sOff, self.sSz = sOff, sSz
        # name -> позиция в зоне строк
        self.name_pos = {}
        pos = 0
        for chunk in dec[sOff:sOff + sSz].split(b'\x00'):
            if chunk:
                try:
                    self.name_pos.setdefault(chunk.decode('utf-8'), pos)
                except UnicodeDecodeError:
                    pass
            pos += len(chunk) + 1
        self.nTypes = sec['types'][1] // 88
        self.nFields = sec['fields'][1] // 12
        self.nMethods = sec['methods'][1] // 36

    def s(self, idx):
        if idx is None or idx >= self.sSz or idx < 0:
            return None
        o = self.sOff + idx
        try:
            end = self.dec.index(b'\x00', o)
        except ValueError:
            return None
        return self.dec[o:end].decode('utf-8', 'replace')

    def find_types(self, name):
        p = self.name_pos.get(name)
        if p is None:
            return []
        out = []
        for i in range(self.nTypes):
            base = self.sec['types'][0] + i * 88
            if struct.unpack_from('<I', self.dec, base)[0] == p:
                out.append((i, base))
        return out

    def dump(self, i, base):
        dec = self.dec
        name = self.s(struct.unpack_from('<I', dec, base)[0])
        ns = self.s(struct.unpack_from('<I', dec, base + 4)[0])
        field_start = struct.unpack_from('<i', dec, base + 32)[0]
        method_start = struct.unpack_from('<i', dec, base + 36)[0]
        fcount = struct.unpack_from('<H', dec, base + 68)[0]   # проверено тайлингом
        mcount = struct.unpack_from('<H', dec, base + 66)[0]
        print('=== typeDef[%d] %s%s @0x%x ===' % (i, (ns + '.' if ns else ''), name, base))
        print('  fieldStart=%d fieldCount=%d methodStart=%d methodCount=%d' % (
            field_start, fcount, method_start, mcount))
        fOff = self.sec['fields'][0]
        if 0 <= field_start and 0 < fcount and field_start + fcount <= self.nFields:
            for k in range(fcount):
                fo = fOff + (field_start + k) * 12
                fnidx = struct.unpack_from('<I', dec, fo)[0]
                print('    FIELD %s' % self.s(fnidx))
        mOff = self.sec['methods'][0]
        if 0 <= method_start and 0 < mcount and method_start + mcount <= self.nMethods:
            for k in range(mcount):
                mo = mOff + (method_start + k) * 36
                mnidx = struct.unpack_from('<I', dec, mo)[0]
                pcnt = struct.unpack_from('<H', dec, mo + 34)[0]
                print('    method %s(%d params)' % (self.s(mnidx), pcnt))


def main():
    dec_path, args = sys.argv[1], sys.argv[2:]
    m = Meta(dec_path)
    if args[0] == '--byfield':
        fname = args[1]
        p = m.name_pos.get(fname)
        if p is None:
            print('field %r not found' % fname)
            return 1
        fOff = m.sec['fields'][0]
        hits = []
        for fi in range(m.nFields):
            if struct.unpack_from('<I', m.dec, fOff + fi * 12)[0] == p:
                hits.append(fi)
        print('# field %r at field indexes: %s' % (fname, hits))
        # найти типы, чьи поля покрывают эти индексы
        for i in range(m.nTypes):
            base = m.sec['types'][0] + i * 88
            fs = struct.unpack_from('<i', m.dec, base + 32)[0]
            fc = struct.unpack_from('<H', m.dec, base + 68)[0]
            if 0 <= fs and 0 < fc:
                for h in hits:
                    if fs <= h < fs + fc:
                        m.dump(i, base)
        return 0
    for name in args:
        found = m.find_types(name)
        if not found:
            print('# %r: typeDef not found' % name)
            continue
        for i, base in found:
            m.dump(i, base)
    return 0


if __name__ == '__main__':
    sys.exit(main())
