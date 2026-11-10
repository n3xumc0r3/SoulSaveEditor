#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sk_local_save.py — расшифровка/шифрование ЛОКАЛЬНЫХ сохранений Soul Knight 8.x
(android: /data/data/com.ChillyRoom.DungeonShooter/files/*.data + shared_prefs).

Форматы (установлены по SKDEditor, github.com/HTCheater/SKDEditor, GPLv2 —
использованы факты о формате, код написан независимо):

  battles*.data / battle*.data (старые)   -> чистый JSON (UTF-8)
  game.data                               -> XOR (15-байтный ключ) -> JSON
  statistic*.data                         -> Base64(DES/CBC/PKCS5, key "crst1") -> JSON
  прочие *.data                           -> Base64(DES/CBC/PKCS5, key "iambo") -> JSON
  IV для всего DES: "Ahbool" + 2 нулевых байта
  Шаблон имён: <имя>_(<accountId>|LOCAL)_<N>.data — accountId = cloudSaveId профиля

Использование:
  python3 sk_local_save.py decrypt FILE [-o OUT]   # -> JSON на stdout или в OUT
  python3 sk_local_save.py encrypt FILE [-o OUT]   # JSON из FILE -> файл формата игры
  python3 sk_local_save.py info FILE [FILE...]     # определить тип каждого файла
  python3 sk_local_save.py ids PREFS.XML           # вытащить cloudSaveId из playerprefs.xml
"""
import argparse
import base64
import json
import os
import re
import sys

from Crypto.Cipher import DES

# --- крипто-константы (факты о формате, встречаются в открытом виде в игре) ---
DES_IV = bytes([0x41, 0x68, 0x62, 0x6F, 0x6F, 0x6C, 0x00, 0x00])          # "Ahbool\0\0"
KEY_STATISTIC = bytes([0x63, 0x72, 0x73, 0x74, 0x31, 0x00, 0x00, 0x00])   # "crst1\0\0\0"
KEY_DEFAULT = bytes([0x69, 0x61, 0x6D, 0x62, 0x6F, 0x00, 0x00, 0x00])     # "iambo\0\0\0"
XOR_KEY = bytes([115, 108, 99, 122, 125, 103, 117, 99, 127, 87, 109, 108, 107, 74, 95])  # 15 байт


def xor_crypt(data: bytes) -> bytes:
    """Симметричный XOR по кольцу 15-байтного ключа (game.data)."""
    return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data))


def pkcs5_pad(data: bytes) -> bytes:
    n = 8 - (len(data) % 8)
    return data + bytes([n]) * n


def pkcs5_unpad(data: bytes) -> bytes:
    if not data or len(data) % 8:
        raise ValueError("DES: длина не кратна 8")
    n = data[-1]
    if not (1 <= n <= 8) or data[-n:] != bytes([n]) * n:
        raise ValueError("DES: некорректный PKCS5-пэдинг (не тот ключ/файл?)")
    return data[:-n]


def des_decrypt(data: bytes, key: bytes) -> bytes:
    cipher = DES.new(key, DES.MODE_CBC, DES_IV)
    return pkcs5_unpad(cipher.decrypt(data))


def des_encrypt(data: bytes, key: bytes) -> bytes:
    cipher = DES.new(key, DES.MODE_CBC, DES_IV)
    return cipher.encrypt(pkcs5_pad(data))


def detect_kind(filename: str) -> str:
    """Тип файла по имени (правила GameFileResolver из SKDEditor)."""
    name = os.path.basename(filename).lower()
    if name.startswith("battles") or name.startswith("battle"):
        return "json"
    if name == "game.data":
        return "xor"
    if name.startswith("statistic"):
        return "des:statistic"
    if name.endswith(".data"):
        return "des:default"
    return "unknown"


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as f:
        return f.read()


def decrypt_to_json_bytes(raw: bytes, kind: str) -> bytes:
    """Любой из форматов -> минифицированный JSON (UTF-8)."""
    if kind == "json":
        data = raw
    elif kind == "xor":
        data = xor_crypt(raw)
    elif kind.startswith("des:"):
        key = KEY_STATISTIC if kind.endswith("statistic") else KEY_DEFAULT
        data = des_decrypt(base64.b64decode(raw.strip()), key)
    else:
        raise ValueError("неизвестный тип файла: " + kind)
    # проверка, что получился JSON
    obj = json.loads(data.decode("utf-8"))
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def encrypt_from_json_bytes(json_bytes: bytes, kind: str) -> bytes:
    """Минифицированный JSON -> байты файла формата игры."""
    obj = json.loads(json_bytes.decode("utf-8"))  # валидация
    compact = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if kind == "json":
        return compact
    if kind == "xor":
        return xor_crypt(compact)
    if kind.startswith("des:"):
        key = KEY_STATISTIC if kind.endswith("statistic") else KEY_DEFAULT
        return base64.b64encode(des_encrypt(compact, key))
    raise ValueError("неизвестный тип файла: " + kind)


NAME_ACCOUNT_RE = re.compile(r"^(?P<name>.+?)_(?P<acc>\d+|LOCAL)(_(?P<slot>\d+))?\.data$", re.I)


def parse_data_name(filename: str):
    """'<name>_(accountId|LOCAL)_[slot].data' -> dict или None."""
    m = NAME_ACCOUNT_RE.match(os.path.basename(filename))
    if not m:
        return None
    return {
        "name": m.group("name"),
        "account": m.group("acc"),
        "slot": m.group("slot"),
    }


CLOUD_SAVE_ID_RE = re.compile(r'name="cloudSaveId">(.*?)<', re.S)


def extract_ids(prefs_xml_text: str):
    """playerprefs.xml -> {cloudSaveId: ...} (+ прочие string-префы, похожие на id)."""
    out = {}
    m = CLOUD_SAVE_ID_RE.search(prefs_xml_text)
    if m:
        out["cloudSaveId"] = m.group(1).strip()
    for pm in re.finditer(r'<string name="([^"]*(?:[Ii]d|[Uu]uid|account)[^"]*)">(.*?)</string>', prefs_xml_text, re.S):
        k, v = pm.group(1), pm.group(2).strip()
        if k not in out and v:
            out[k] = v
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Soul Knight local save decrypt/encrypt")
    ap.add_argument("mode", choices=["decrypt", "encrypt", "info", "ids"])
    ap.add_argument("files", nargs="+")
    ap.add_argument("-o", "--out", help="выходной файл (для одиночного decrypt/encrypt)")
    args = ap.parse_args(argv)

    if args.mode == "ids":
        for path in args.files:
            txt = read_bytes(path).decode("utf-8", errors="replace")
            ids = extract_ids(txt)
            print(path)
            for k, v in ids.items():
                print("  %s = %s" % (k, v))
        return 0

    if args.mode == "info":
        for path in args.files:
            raw = read_bytes(path)
            kind = detect_kind(path)
            head = raw[:48]
            note = ""
            try:
                decrypt_to_json_bytes(raw, kind)
                note = "OK: расшифровывается в JSON"
            except Exception as e:
                note = "не расшифровывается: %s" % e
            extra = parse_data_name(path)
            print("%s\n  kind=%s%s\n  size=%d\n  head=%s\n  %s" % (
                path, kind,
                ("  name_parts=" + json.dumps(extra, ensure_ascii=False)) if extra else "",
                len(raw), head.hex(), note))
        return 0

    # decrypt / encrypt
    if len(args.files) != 1:
        ap.error("для decrypt/encrypt нужен ровно один файл")
    path = args.files[0]
    kind = detect_kind(path)
    raw = read_bytes(path)
    try:
        if args.mode == "decrypt":
            out = decrypt_to_json_bytes(raw, kind)
        else:
            out = encrypt_from_json_bytes(raw, kind)
    except Exception as e:
        print("[!] %s: %s" % (path, e), file=sys.stderr)
        return 1
    if args.out:
        with open(args.out, "wb") as f:
            f.write(out)
        print("[ok] %s -> %s (%d bytes)" % (path, args.out, len(out)))
    else:
        sys.stdout.buffer.write(out)
        sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
