#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sk870_save_fields.py — извлечение классов/полей/методов, связанных с сохранениями
Soul Knight 8.7.0, из ДЕКОДИРОВАННОГО global-metadata.dat
(декодер: tools/sk870_metadata_decode.py; layout v31 = Unity 2022.3.62f2).

Проверено перекрёстной сверкой (тайлинг таблиц):
  Il2CppTypeDefinition v31: 16 int + 8 ushort, где счётчики в порядке
    [0]=method_count, [1]=property_count, [2]=field_count, [3]=event_count,
    [4]=nested_type_count, [5]=vtable_count, [6]=interfaces_count,
    [7]=interface_offsets_count
  (field_count именно на позиции 2 — иначе диапазоны полей перекрываются на
   ~820K записей; на позиции 2 тайлятся идеально, 0 перекрытий, 37521 типов)

Usage:
    python3 sk870_save_fields.py <decoded-metadata.dat> <out-json> [grep-regex]
"""
import json
import re
import struct
import sys

REGION_NAMES = ['stringLiteral','stringLiteralData','string','events','properties','methods',
                'parameterDefaultValues','fieldDefaultValues','fieldAndParameterDefaultValueData',
                'fieldMarshaledSizes','parameters','fields','genericParameters',
                'genericParameterConstraints','genericContainers','nestedTypes','interfaces',
                'vtableMethods','interfaceOffsets','typeDefinitions','images','assemblies',
                'fieldRefs','referencedAssemblies','attributeData','attributeDataRange',
                'unresolvedVirtualCallParameterTypes','unresolvedVirtualCallParameterRanges',
                'windowsRuntimeTypeNames','windowsRuntimeStrings','exportedTypeDefinitions']

NOISE_NS_PREFIX = ('System', 'Microsoft', 'Unity', 'TMPro', 'Mono.', 'I18N',
                   'Newtonsoft', 'Google', 'JetBrains', 'Zenject', 'Cysharp')


class Meta:
    def __init__(self, path):
        self.d = open(path, 'rb').read()
        sanity, self.version = struct.unpack_from('<II', self.d, 0)
        if sanity != 0xFAB11BAF:
            raise SystemExit('не декодированный metadata (bad magic) — прогони sk870_metadata_decode.py')
        raw = struct.unpack_from('<62I', self.d, 8)
        self.reg = {n: (raw[i*2], raw[i*2+1]) for i, n in enumerate(REGION_NAMES)}
        so, ss = self.reg['string']
        tab = self.d[so:so+ss]
        self._strtab = tab
        self._cache = {}

    def s(self, idx):
        v = self._cache.get(idx)
        if v is None:
            end = self._strtab.find(b'\x00', idx, idx+2048)
            if end < 0:
                end = idx+2048
            v = self._strtab[idx:end].decode('utf-8', 'replace')
            self._cache[idx] = v
        return v

    def types(self):
        to, ts = self.reg['typeDefinitions']
        n = ts // 88
        fo, _ = self.reg['fields']
        mo, _ = self.reg['methods']
        out = []
        for i in range(n):
            b = to + i*88
            (name_idx, ns_idx, _byval, _decl, _parent, _elem, _gc, tflags,
             field_start, method_start, ev_start, pr_start, nested_start,
             if_start, vt_start, ioff_start) = struct.unpack_from('<8i8i', self.d, b)
            (m_count, p_count, f_count, e_count, n_count, vt_count,
             if_count, io_count) = struct.unpack_from('<8H', self.d, b+64)
            bitfield, token = struct.unpack_from('<2I', self.d, b+80)
            fields = []
            if 0 <= field_start and f_count:
                for j in range(field_start, field_start+f_count):
                    fn, _ft, _tok = struct.unpack_from('<IiI', self.d, fo+j*12)
                    fields.append(self.s(fn))
            methods = []
            if 0 <= method_start and m_count:
                for j in range(method_start, method_start+m_count):
                    mn, _decl, _ret, _rpt, _ps, _gci, _tok = struct.unpack_from('<7i', self.d, mo+j*36)
                    methods.append(self.s(mn))
            out.append({
                'idx': i, 'ns': self.s(ns_idx), 'name': self.s(name_idx),
                'flags': tflags, 'token': token,
                'fields': fields, 'methods': methods,
                'starts': {'field': field_start, 'method': method_start,
                           'event': ev_start, 'prop': pr_start, 'nested': nested_start},
                'counts': {'field': f_count, 'method': m_count, 'prop': p_count,
                           'event': e_count, 'nested': n_count},
            })
        return out

    def literals(self):
        slo, sls = self.reg['stringLiteral']
        sdo, sds = self.reg['stringLiteralData']
        out = []
        for i in range(sls // 8):
            ln, di = struct.unpack_from('<Ii', self.d, slo+i*8)
            if 0 <= di < sds and 0 < ln < 8192:
                out.append(self.d[sdo+di:sdo+di+ln].decode('utf-8', 'replace'))
        return out


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    path, out_json = sys.argv[1], sys.argv[2]
    grep = re.compile(sys.argv[3], re.I) if len(sys.argv) > 3 else None

    md = Meta(path)
    types = md.types()
    print('[*] v%d: %d типов' % (md.version, len(types)), file=sys.stderr)

    # полный map: 'Ns.Name' -> fields (дубликаты имён с суффиксом #idx)
    all_map = {}
    for t in types:
        full = (t['ns'] + '.' if t['ns'] else '') + t['name']
        key = full if full not in all_map else '%s#T%d' % (full, t['idx'])
        all_map[key] = t['fields']
    with open(out_json, 'w', encoding='utf-8') as fp:
        json.dump(all_map, fp, ensure_ascii=False, indent=0, separators=(',', ':'))
    print('[*] %s: %d классов' % (out_json, len(all_map)), file=sys.stderr)

    # литералы по теме сейвов
    lit_pat = re.compile(r'save|\.data|\.json|\.xml|\.aes|\.rijndael|gems?|lastGems|varGems|'
                         r'snapshot|persistent|cloudSaveId|serialzedVersion|encrypt|decrypt|'
                         r'Rijndael|AesSuffix|backup|gameData', re.I)
    lit_hits = sorted(set(l for l in md.literals() if lit_pat.search(l) and len(l) < 300))
    with open(out_json.replace('.json', '_literals.txt'), 'w', encoding='utf-8') as fp:
        fp.write('\n'.join(lit_hits))
    print('[*] литералов по теме: %d' % len(lit_hits), file=sys.stderr)

    # классы по теме сейвов
    cls_pat = re.compile(r'save|gamedata|playerdata|recorddata|yearreview|cloud|snapshot|'
                         r'backup|storage|serialize|crypt|rijndael|aes', re.I)
    shown = 0
    for t in types:
        full = (t['ns'] + '.' if t['ns'] else '') + t['name']
        if t['ns'].startswith(NOISE_NS_PREFIX):
            continue
        if not (cls_pat.search(t['name']) or cls_pat.search(t['ns'])):
            continue
        if grep and not (grep.search(full) or grep.search(' '.join(t['fields']))):
            continue
        print('\n=== %s  (token=0x%08X, flags=0x%08X)' % (full, t['token'], t['flags']))
        if t['fields']:
            print('  fields(%d): %s' % (len(t['fields']), ', '.join(t['fields'][:80])))
            if len(t['fields']) > 80:
                print('    ... +%d ещё' % (len(t['fields'])-80))
        if t['methods']:
            print('  methods(%d): %s' % (len(t['methods']), ', '.join(t['methods'][:40])))
            if len(t['methods']) > 40:
                print('    ... +%d ещё' % (len(t['methods'])-40))
        shown += 1
    print('\n[*] классов по теме показано: %d' % shown, file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
