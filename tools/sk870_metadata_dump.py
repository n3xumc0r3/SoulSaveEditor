#!/usr/bin/env python3
"""
sk870_metadata_dump.py — name-only dump of Soul Knight 8.7.0 IL2CPP metadata.

Parses the DECODED global-metadata.dat (see sk870_metadata_decode.py) and
produces a text listing of every image, type, method and field name.
No code addresses and no method bodies — names only, straight from the
metadata tables (v31 = Unity 2022.3.62f2).

Every table is validated against its expected record size (the region size
must divide evenly); a mismatch is reported loudly instead of producing
garbage.

Usage:
    python3 sk870_metadata_dump.py <decoded-global-metadata.dat> [out.txt]
"""

import struct
import sys

# region index -> (name, record size in bytes) for v31
EXPECTED_RECORD_SIZES = {
    0: ("stringLiteral", 8),
    3: ("events", 24),
    4: ("properties", 16),
    5: ("methods", 36),          # v29+: + returnParameterToken
    6: ("parameterDefaultValues", 8),
    7: ("fieldDefaultValues", 12),
    9: ("fieldMarshaledSizes", 12),
    10: ("parameters", 12),
    11: ("fields", 12),
    12: ("genericParameters", 16),
    13: ("genericParameterConstraints", 4),
    14: ("genericContainers", 16),
    15: ("nestedTypes", 4),
    16: ("interfaces", 4),
    17: ("vtableMethods", 4),
    18: ("interfaceOffsets", 8),
    19: ("typeDefinitions", 88),           # 16 int + 8 ushort counts + bitfield + token
    20: ("images", 40),
    21: ("assemblies", 64),
    22: ("fieldRefs", 8),
    23: ("referencedAssemblies", 4),
    25: ("attributeDataRange", 8),
    26: ("unresolvedVirtualCallParameterTypes", 4),
    27: ("unresolvedVirtualCallParameterRanges", 8),
    30: ("exportedTypeDefinitions", 4),
}

ACCESS = {0: "compiler-controlled", 1: "private", 2: "fam-and-assem",
          3: "internal", 4: "protected", 5: "fam-or-assem", 6: "public"}


class Metadata:
    def __init__(self, path: str):
        self.data = open(path, "rb").read()
        sanity, version = struct.unpack_from("<II", self.data, 0)
        if sanity != 0xFAB11BAF:
            raise SystemExit("not a decoded metadata file (bad magic) — "
                             "run sk870_metadata_decode.py first")
        self.version = version
        n = 31
        raw = struct.unpack_from(f"<{n * 2}I", self.data, 8)
        self.regions = [(raw[i * 2], raw[i * 2 + 1]) for i in range(n)]
        self.strings = self._read_strings()

    def region(self, idx: int):
        return self.regions[idx]

    def _read_strings(self):
        start, size = self.regions[2]           # stringData
        data = self.data
        end = start + size
        strings = {0: ""}
        pos = start
        while pos < end:
            nul = data.find(b"\x00", pos, end)
            if nul < 0:
                break
            strings[pos - start] = data[pos:nul].decode("utf-8", "replace")
            pos = nul + 1
        return strings

    def s(self, idx: int) -> str:
        return self.strings.get(idx, f"<str:{idx}>")

    def records(self, idx: int, size: int):
        _, region_size = self.regions[idx]
        return region_size // size, region_size % size


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out_path = sys.argv[2] if len(sys.argv) > 2 else "/tmp/sk870_names.txt"
    md = Metadata(sys.argv[1])
    ok = True

    print(f"[*] metadata version {md.version}, {len(md.regions)} regions",
          file=sys.stderr)
    for idx, (name, rec) in sorted(EXPECTED_RECORD_SIZES.items()):
        count, rem = md.records(idx, rec)
        if rem:
            print(f"[!] region {idx} ({name}): size {md.region(idx)[1]} "
                  f"NOT divisible by {rec} (remainder {rem})", file=sys.stderr)
            ok = False
        else:
            print(f"    [{idx:2d}] {name:38s} {count:>9d} x {rec} B",
                  file=sys.stderr)

    # ---- images ------------------------------------------------------------
    img_off, img_size = md.region(20)
    images = []
    for i in range(img_size // 40):
        base = img_off + i * 40
        name_idx, _asm_idx = struct.unpack_from("<Ii", md.data, base)
        images.append(md.s(name_idx))
    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"=== IMAGES ({len(images)}) ===\n")
        for i, name in enumerate(images):
            out.write(f"[{i}] {name}\n")

        # ---- types + fields + methods --------------------------------------
        td_off, td_size = md.region(19)
        m_off, _ = md.region(5)
        m_count = md.region(5)[1] // 36
        f_off, _ = md.region(11)
        f_count = md.region(11)[1] // 12
        type_count = td_size // 88
        out.write(f"\n=== TYPES ({type_count}) ===\n")

        # Il2CppTypeDefinition v27+: 16 int indices/flags + 8 ushort counts +
        # bitfield + token = 88 B
        bad_types = 0
        for t in range(type_count):
            base = td_off + t * 88
            (name_idx, ns_idx, _byval, _decl, _parent, _elem, _gc, _tflags,
             field_start, method_start, _ev_start, _pr_start, _nested_start,
             _if_start, _vt_start, _ioff_start,
             method_count, _prop_count, field_count, _ev_count, _nested_count,
             _vt_count, _if_count, _ioff_count,
             _bitfield, _token) = struct.unpack_from("<2I5iI8i8H2I", md.data, base)
            ok_m = method_count == 0 or (0 <= method_start
                                         and method_start + method_count <= m_count)
            ok_f = field_count == 0 or (0 <= field_start
                                        and field_start + field_count <= f_count)
            if not (ok_m and ok_f):
                bad_types += 1
                continue
            tname = md.s(name_idx)
            ns = md.s(ns_idx)
            full = f"{ns}.{tname}" if ns else tname
            out.write(f"\n--- T{t} {full}\n")
            for fi in range(field_count):
                fb = f_off + (field_start + fi) * 12
                fname_idx, _ftype, _ftok = struct.unpack_from("<IiI", md.data, fb)
                out.write(f"  .field {md.s(fname_idx)}\n")
            for mi in range(method_count):
                mb = m_off + (method_start + mi) * 36
                mname_idx, _decl, _ret, _rpt, _pstart, _gcidx, _tok, \
                    mflags, _iflags, _slot, _pcount = struct.unpack_from(
                        "<7i4H", md.data, mb)
                attrs = []
                if mflags & 0x0010:
                    attrs.append("static")
                attrs.append(ACCESS.get(mflags & 0x0007, "?"))
                out.write(f"  .method {' '.join(attrs)} {md.s(mname_idx)}\n")

    print(f"[*] wrote {out_path} ({type_count} types, "
          f"{bad_types} skipped as invalid)", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
