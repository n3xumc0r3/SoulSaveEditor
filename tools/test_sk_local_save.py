#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Тест sk_local_save.py: roundtrip всех форматов + извлечение cloudSaveId."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sk_local_save as sk  # noqa: E402

SAMPLE = {
    "serialzedVersion": 1,
    "cloudSaveId": "GPV2_TEST_123",
    "gameData": {"gems": 1000, "lastGems": 1000, "varGems": 0, "coins": 34815,
                 "roleList": [{"heroId": 1, "star": 5}], "emoji": "тест кириллица ✓"},
}

ok = True


def check(name, cond):
    global ok
    print(("  [ok] " if cond else "  [FAIL] ") + name)
    ok = ok and cond


for kind, fname in [("json", "battles_12345_1.data"), ("xor", "game.data"),
                    ("des:statistic", "statistic_12345.data"), ("des:default", "role_12345_1.data")]:
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, fname)
        enc = sk.encrypt_from_json_bytes(json.dumps(SAMPLE, ensure_ascii=False).encode(), kind)
        with open(p, "wb") as f:
            f.write(enc)
        back = sk.decrypt_to_json_bytes(open(p, "rb").read(), kind)
        obj = json.loads(back.decode())
        check("%s roundtrip" % kind, obj == SAMPLE)
        # автодетект по имени
        check("%s autodetect" % kind, sk.detect_kind(p) == kind)

# имя файла
parts = sk.parse_data_name("role_1277883351937828180_2.data")
check("name parse account", parts and parts["account"] == "1277883351937828180" and parts["name"] == "role" and parts["slot"] == "2")
parts2 = sk.parse_data_name("weapon_LOCAL_1.data")
check("name parse LOCAL", parts2 and parts2["account"] == "LOCAL")

# playerprefs
prefs = '''<map><string name="cloudSaveId">GPV2_235288940</string>
<string name="some.uid">abc</string></map>'''
ids = sk.extract_ids(prefs)
check("cloudSaveId extract", ids.get("cloudSaveId") == "GPV2_235288940")

# DES ключи из доки SKDEditor (контроль констант)
check("DES IV", sk.DES_IV == b"Ahbool\0\0")
check("KEY_STATISTIC", sk.KEY_STATISTIC == b"crst1\0\0\0")
check("KEY_DEFAULT", sk.KEY_DEFAULT == b"iambo\0\0\0")
check("XOR len 15", len(sk.XOR_KEY) == 15)

print("\nRESULT:", "ALL OK" if ok else "FAILURES PRESENT")
sys.exit(0 if ok else 1)
