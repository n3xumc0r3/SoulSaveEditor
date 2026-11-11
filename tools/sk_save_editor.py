#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sk_save_editor.py — редактор локальных сохранений Soul Knight 8.7.0 (Android)
и облачных дампов (GPGS snapshot / UTF-16LE JSON).

Всё, что здесь реализовано, доказано на декодированной global-metadata.dat 8.7.0
(IL2CPP v31) и живых дампах; см. docs/LOCAL-SAVE-870.md, docs/REVERSING.md,
docs/CLOUD-SAVE.md в репо n3xumc0r3/SoulSaveEditor.

Форматы локальных файлов (internalPersistentPath = /data/data/com.ChillyRoom.DungeonShooter/files/):

  battles*.data / battle*.data   -> чистый JSON (UTF-8)
  game.data (+ .aes)             -> XOR по кольцу 15-байтного ключа -> JSON
  statistic*.data                -> Base64(DES/CBC/PKCS5, key "crst1")  -> JSON
  прочие *.data                  -> Base64(DES/CBC/PKCS5, key "iambo")  -> JSON
  *.aes / *.rijndael             -> Rijndael-128/AES-256, per-install ключи
                                    из playerprefs (DataRijndael128Key /
                                    DataRijndaelIV / DataAES256Key)
  IV для всего легаси-DES: "Ahbool" + 2 нулевых байта
  Имена: <section>_(<accountId>|LOCAL)_[<slot>].data[.aes-варианты]

Облачный дамп — UTF-16LE JSON без BOM (head 7b 00 22 00), секции CloudSaveGameData.

Поведение парсера игры при отсутствующих полях (ДОКАЗАНО, LOCAL-SAVE-870.md §6):
  - отсутствующее поле = дефолт C#-класса (0/false/null/пустой список), не ошибка;
  - лишние/неизвестные поля молча ИГНОРИРУЮТСЯ -> правка неизвестного поля потеряется;
  - null для ссылочных полей валиден; списки лучше не занулять (NRE -> секция
    уедет в SaveBrokenData);
  - serialzedVersion (опечатка игры) — версия схемы, не трогать.

Примеры:
  python3 sk_save_editor.py info files/*.data
  python3 sk_save_editor.py list ./device_saves
  python3 sk_save_editor.py show game.data gameData.gems      # cloud-дамп: gameData.gems
  python3 sk_save_editor.py preset game.data gems 800000
  python3 sk_save_editor.py preset cloud.bin unlock-heroes
  python3 sk_save_editor.py pull -o ./device_saves --kill     # adb + root
  python3 sk_save_editor.py push ./device_saves --kill
  python3 sk_save_editor.py selftest

Зависимости: python3, pycryptodome (pip install pycryptodome), для pull/push — adb.
Лицензия: GPL-2.0 (форк SKDEditor © HTCheater / rosstonovsky), без монетизации.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time

try:
    from Crypto.Cipher import AES, DES
except ImportError:  # pragma: no cover
    AES = DES = None

PKG = "com.ChillyRoom.DungeonShooter"
VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 1. Крипто-константы (литералы из игры/метадаты, REVERSING.md §3.2, §4)
# ---------------------------------------------------------------------------
DES_IV = bytes([0x41, 0x68, 0x62, 0x6F, 0x6F, 0x6C, 0x00, 0x00])          # "Ahbool\0\0"
KEY_STATISTIC = bytes([0x63, 0x72, 0x73, 0x74, 0x31, 0x00, 0x00, 0x00])   # "crst1\0\0\0"
KEY_DEFAULT = bytes([0x69, 0x61, 0x6D, 0x62, 0x6F, 0x00, 0x00, 0x00])     # "iambo\0\0\0"
XOR_KEY = bytes([115, 108, 99, 122, 125, 103, 117, 99, 127, 87, 109, 108, 107, 74, 95])  # 15 байт

# SDK-крипта (CreptUtil, libnative-lib.so — подтверждено дизасемблером)
SDK_DES_KEY = DES_IV          # "Ahbool\0\0" — старый IV повышен до ключа
SDK_DES_IV = b"abo123\0\0"    # новый IV ("Abo" — тег автора крипто-классов)

# playerprefs-ключи per-install крипты 8.7.0
PREF_RIJ_KEY = "DataRijndael128Key"
PREF_RIJ_IV = "DataRijndaelIV"
PREF_AES_KEY = "DataAES256Key"

# Поля, которые нельзя трогать без --force-id (привязка профиля/схемы)
PROTECTED = {"serialzedVersion", "cloudSaveId", "platformId", "ChillyUid"}

# ---------------------------------------------------------------------------
# 2. База знаний: карта секций, классы, поля (из декодированной метадаты v31)
# ---------------------------------------------------------------------------
CLOUD_SECTIONS = [
    "serialzedVersion", "cloudSaveId", "platformId", "gameData", "itemData",
    "statisticData", "settingData", "taskData", "maps", "sandboxConfig",
    "seasonData", "mallReloadData", "monsterRiseData", "weaponEvolutionData",
    "miscData", "pvpData", "battlePassSaveData", "escapeSeasonData",
]

# имя секции (после вычищения accountId/слота и суффиксов) -> C#-класс
SECTION_CLASS = {
    "game": "AboGameData",
    "item_data": "ItemData",
    "statistic": "StatisticData",
    "setting": "SettingData",
    "season_data": "SeasonData",
    "task": "RGScript.Data.TaskData",
    "mall_reload_data": "RGScript.Data.Mall.MallReloadData",
    "monsrise_data": "MonsterRiseData",
    "weapon_evolution_data": "RGScript.WeaponEvolution.WeaponEvolutionData",
    "misc_data": "RGScript.Data.MiscData",
    "pvp_data": "RGScript.Data.PvpData",
    "bp_data": "RGScript.Data.BattlePassSaveData",
    "escape_season_data": "ModeSeason.Escape.Scripts.EscapeSeasonData",
    "escape_run_save": "ModeSeason.Escape.Scripts.EscapeRunSaveData",
    "backup": "BackupGameData",
}

SAVE_SUFFIXES = (".data", ".data.new", ".aes", ".rijndael")

# Класс секции-обёртки и поле-обёртка (backup.data = {"gameData": {...}})
WRAPPER_CLASS_FIELD = {"BackupGameData": "gameData"}

# Полные списки полей — tools/sk870_save_classes.json (лежит рядом в репо).
# Ниже — встроенный fallback (валюты/разблокировки; полный список грузится из JSON).
_FALLBACK_FIELDS = {
    "AboGameData": [
        "skinLock", "currentSkinDatas", "heroUnlock", "heroLevel", "heroSkill",
        "petUnlock", "roomObjectLevel", "usedSecretKeys", "promotions2RewardVersion",
        "unlockRoomDecorate", "certificates", "normalModePassCertificates",
        "bossRushPassCertificate", "ironTidePassCertificate", "aramPassCertificate",
        "currentRoomDecorate", "gems", "lastGems", "varGems", "cumulativeGems",
        "cumulativeConsumeGems", "rebornCard", "hasTutorial", "firstInRoom",
        "roomLevel", "fishChip", "gameVersion", "lastPlayTime",
        "lastPlayTimeWithNetworkTime", "totalIndex", "totalGameTime", "generalSeed",
        "tutorialEndTime", "checkUnlockWorkShopTime", "fontSettingValue",
        "superHeroInHeroRoom", "isWaveMachineUpgraded", "retiredKnightGotAdItemTime",
        "lastTalentAdRefreshDay", "activeShopGemReceiptMarkers",
    ],
    "ItemData": [
        "MagicNames", "AppVersion", "RebornCard", "Plantpot_1", "Plantpot_2",
        "Plantpot_3", "Plantpot_4", "GemName", "FishName", "TokenEquipment",
        "TokenHero", "HopperTicket", "PostmanUpgrade", "plants", "sellerRefreshTime",
        "commodities", "itemUnlock", "forgeWeapons", "skinTrialEntries",
        "mechInRoom", "blueprints", "seeds", "materials", "activeShopReceiptMarkers",
        "tokenTickets", "restoreKeys", "specialTickets", "jewelryData",
        "jewelryBlueprints", "jewelryPlayerData", "mythicWeapons",
        "CanNotSaleWeapons", "specialTickets", "fishChip", "seasonCoinName",
        "roomDecorateDic", "roomDecorateCurSkinDic", "ItemLoad",
    ],
    "SettingData": [
        "MaxLevel", "shakeLevel", "sandboxFlyable", "bgmName", "movePosition",
        "UseRijData", "UseNewtonJsonData", "lastSendEmailTime", "useNewLayout",
        "ChillyUid", "SettingLoad",
    ],
    "SeasonData": [
        "coin", "gainedCoinToday", "gainedCoinThisWeek", "gainedCoinInSeason",
        "cumulativeCoinCount", "treasureBox1OpenBoxCount", "chips", "chipSchemes",
        "comboGunData", "SeasonDataLoad",
    ],
    "StatisticData": [
        "StatisticsData", "object2PassLevel", "object2PassTime", "object2ObtainTime",
        "KrppVerifiedUsdCents", "KrppCountedOrderIds", "_enemyKilledCount",
        "passInfo", "goldframeShopSaveData", "season2Prizes",
    ],
    "CloudSaveGameData": CLOUD_SECTIONS,
}


def _load_class_fields():
    """Пытается загрузить полную карту полей из sk870_save_classes.json рядом."""
    fields = {k: list(v) for k, v in _FALLBACK_FIELDS.items()}
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "sk870_save_classes.json"),
                 os.path.join(here, "..", "tools", "sk870_save_classes.json")):
        if os.path.isfile(cand):
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for cls, lst in data.items():
                    if isinstance(lst, list):
                        fields.setdefault(cls, [])
                        for name in lst:
                            if name not in fields[cls]:
                                fields[cls].append(name)
                break
            except Exception:
                pass
    return fields


CLASS_FIELDS = _load_class_fields()


def class_fields(cls: str):
    return CLASS_FIELDS.get(cls)


# ---------------------------------------------------------------------------
# 3. Легаси-крипто: XOR, DES
# ---------------------------------------------------------------------------
def xor_crypt(data: bytes) -> bytes:
    """Симметричный XOR по кольцу ключа (game.data)."""
    return bytes(b ^ XOR_KEY[i % len(XOR_KEY)] for i, b in enumerate(data))


def pkcs5_pad(data: bytes, block: int = 8) -> bytes:
    n = block - (len(data) % block)
    return data + bytes([n]) * n


def pkcs5_unpad(data: bytes, block: int = 8) -> bytes:
    if not data or len(data) % block:
        raise ValueError("длина не кратна блоку")
    n = data[-1]
    if not (1 <= n <= block) or data[-n:] != bytes([n]) * n:
        raise ValueError("некорректный PKCS5/7-пэдинг (не тот ключ?)")
    return data[:-n]


def des_decrypt(data: bytes, key: bytes, iv: bytes = DES_IV) -> bytes:
    if DES is None:
        raise RuntimeError("нет pycryptodome (pip install pycryptodome)")
    return pkcs5_unpad(DES.new(key, DES.MODE_CBC, iv).decrypt(data), 8)


def des_encrypt(data: bytes, key: bytes, iv: bytes = DES_IV) -> bytes:
    if DES is None:
        raise RuntimeError("нет pycryptodome (pip install pycryptodome)")
    return DES.new(key, DES.MODE_CBC, iv).encrypt(pkcs5_pad(data, 8))


# ---------------------------------------------------------------------------
# 4. Rijndael/AES-256 слой 8.7.0 (мульти-догадка: ключ/IV из playerprefs)
#    Механика доказана (SaveDataWithRijndael, суффикс .aes, ключи per-install),
#    точный wire-format не видел живьём -> пробуем разумные варианты и
#    валидируем результат парсингом JSON.
# ---------------------------------------------------------------------------
def _key_candidates(value: str):
    """Строка из playerprefs -> набор байтовых кандидатов ключа (16/24/32)."""
    out = []
    if not value:
        return out
    # 1) base64
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) in (16, 24, 32):
            out.append(("b64", raw))
    except Exception:
        pass
    # 2) hex
    try:
        raw = bytes.fromhex(value)
        if len(raw) in (16, 24, 32):
            out.append(("hex", raw))
    except Exception:
        pass
    # 3) сырая строка (utf-8), добитая нулями / обрезанная
    raw = value.encode("utf-8")
    for size in (16, 24, 32):
        if len(raw) <= size:
            out.append(("utf8-pad%d" % size, raw.ljust(size, b"\0")))
    # 4) SHA-256 строки (на случай хеширования в CryptUtil)
    out.append(("sha256", hashlib.sha256(raw).digest()))
    # дедуп по байтам
    seen, uniq = set(), []
    for tag, raw in out:
        if raw not in seen:
            seen.add(raw)
            uniq.append((tag, raw))
    return uniq


def _iv_candidates(value: str, data: bytes):
    out = []
    for tag, raw in _key_candidates(value):
        out.append((("iv-" + tag), raw[:16]))
    out.append(("iv-zero", bytes(16)))
    out.append(("iv-firstblock", data[:16]))  # IV, prepend-нутый к шифртексту
    return out


def try_rijndael_decrypt(raw: bytes, prefs: dict):
    """Перебор разумных комбинаций AES/Rijndael. Возвращает (json_bytes, note) или None."""
    if AES is None:
        return None
    key_values = [prefs.get(PREF_AES_KEY, ""), prefs.get(PREF_RIJ_KEY, "")]
    iv_value = prefs.get(PREF_RIJ_IV, "")
    payload_variants = [("raw", raw)]
    b64_ok = re.fullmatch(rb"[A-Za-z0-9+/=\s]+", raw[:64] or b" ")
    if b64_ok:
        try:
            payload_variants.append(("b64", base64.b64decode(raw, validate=False)))
        except Exception:
            pass
    for payload_tag, payload in payload_variants:
        if len(payload) < 16 or len(payload) % 16:
            continue
        for kv in key_values:
            for ktag, key in _key_candidates(kv):
                for itag, iv in _iv_candidates(iv_value, payload):
                    for mode_tag, mode in (("cbc", AES.MODE_CBC), ("ecb", AES.MODE_ECB)):
                        try:
                            if mode == AES.MODE_CBC:
                                pt = AES.new(key, mode, iv).decrypt(payload)
                            else:
                                pt = AES.new(key, mode).decrypt(payload)
                        except Exception:
                            continue
                        for cut_tag, body in (("whole", pt), ("whole-unpad", _unpad16(pt)),
                                              ("skip16", pt[16:])):
                            if body is None:
                                continue
                            if _try_parse_json_bytes(body) is not None:
                                note = "AES %s key=%s %s payload=%s" % (mode_tag, ktag, itag, payload_tag)
                                return (body, note)
    return None


def _unpad16(data: bytes):
    """PKCS7(block=16) или None, если пэдинг не сходится."""
    if not data or len(data) % 16:
        return None
    n = data[-1]
    if 1 <= n <= 16 and data[-n:] == bytes([n]) * n:
        return data[:-n]
    return None


def try_rijndael_encrypt(json_bytes: bytes, prefs: dict, mode_hint: str = "cbc",
                         key_hint: str = None, iv_hint: str = "iv-zero"):
    """Обратная сторона мульти-догадки. Возвращает байты или None.
    mode_hint — cbc|ecb; iv_hint — тег из note распознавания (напр. iv-b64)."""
    if AES is None:
        return None
    key_values = [key_hint] if key_hint else [prefs.get(PREF_AES_KEY, ""), prefs.get(PREF_RIJ_KEY, "")]
    iv_value = prefs.get(PREF_RIJ_IV, "")
    for kv in key_values:
        for ktag, key in _key_candidates(kv):
            ivs = _iv_candidates(iv_value, b"")
            ivs.sort(key=lambda t: 0 if t[0] == iv_hint else 1)  # нужный IV первым
            mode = AES.MODE_CBC if mode_hint == "cbc" else AES.MODE_ECB
            return AES.new(key, mode, ivs[0][1]).encrypt(pkcs5_pad(json_bytes, 16))
    return None


# ---------------------------------------------------------------------------
# 5. JSON-примитивы (форматы игры: UTF-8 и UTF-16LE "WithUnicode")
# ---------------------------------------------------------------------------
def _try_parse_json_bytes(data: bytes):
    """bytes -> (obj, codec) если это валидный JSON-объект, иначе None."""
    if not data:
        return None
    stripped = data.lstrip(b"\xef\xbb\xbf \t\r\n")
    if stripped[:1] in (b"{", b"["):
        try:
            return (json.loads(stripped.decode("utf-8")), "utf8")
        except Exception:
            pass
    if data[:2] in (b"{\x00", b"[\x00", b"\xff\xfe", b"\xfe\xff"):
        try:
            body = data[2:] if data[:2] in (b"\xff\xfe", b"\xfe\xff") else data
            return (json.loads(body.decode("utf-16-le")), "utf16le")
        except Exception:
            pass
    return None


def json_to_bytes(obj, codec: str) -> bytes:
    """Объект -> байты файла (компактный JSON, как пишет игра)."""
    if codec == "utf16le":
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
        return text.encode("utf-16-le")
    text = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8")


# ---------------------------------------------------------------------------
# 6. Детект формата файла: имя -> подсказка, контент -> решение
# ---------------------------------------------------------------------------
NAME_ACC_RE = re.compile(r"^(?P<name>.+?)_\(?(?P<acc>\d{4,}|LOCAL)\)?_(?P<slot>\d*)$", re.I)


def parse_save_name(filename: str):
    """'statistic_(235288966)_2.data' / 'monsrise_data_235288966_.data.new'
    -> dict(name, account, slot). Реальные имена на устройстве 8.7.0 —
    без скобок и с пустым слотом: 'monsrise_data_<acc>_.data' (факт §1.1)."""
    base = os.path.basename(filename)
    # атомарная запись: игра пишет '<имя>.data.new', затем rename — снимаем .new
    for suf in (".rijndael", ".aes", ".data.new", ".data"):
        if base.lower().endswith(suf):
            base = base[: -len(suf)]
            break
    m = NAME_ACC_RE.match(base)
    if not m:
        return {"name": base, "account": None, "slot": None}
    return {"name": m.group("name"), "account": m.group("acc"), "slot": m.group("slot") or None}


def section_of(filename: str):
    parts = parse_save_name(filename)
    name = parts["name"].lower()
    if name in SECTION_CLASS:
        return parts["name"], SECTION_CLASS[name.lower()]
    # легаси-имена
    legacy = {"game": "AboGameData", "statistic": "StatisticData",
              "item_data": "ItemData", "setting": "SettingData"}
    for key, cls in legacy.items():
        if name == key or name.startswith(key + "_") or name.startswith(key + "s"):
            return parts["name"], cls
    return parts["name"], None


def decode_any(raw: bytes, filename: str, prefs: dict):
    """Все известные схемы -> (kind, obj, codec, note). kind из:
    utf8 | utf16le | xor | des-statistic | des-default | des-sdk | rijndael | unknown"""
    # 0) чистый JSON
    got = _try_parse_json_bytes(raw)
    if got:
        return ("utf8" if got[1] == "utf8" else "utf16le", got[0], got[1], "открытый JSON")
    # 1) легаси XOR (game.data)
    try:
        got = _try_parse_json_bytes(xor_crypt(raw))
        if got:
            return ("xor", got[0], got[1], "XOR game.data (легаси-ключ)")
    except Exception:
        pass
    # 2) Base64+DES варианты
    b64_ok = bool(re.fullmatch(rb"[A-Za-z0-9+/=\s]+", raw[:64] or b" "))
    if b64_ok:
        try:
            dec = base64.b64decode(raw)
            for kind, key in (("des-statistic", KEY_STATISTIC),
                              ("des-default", KEY_DEFAULT),
                              ("des-sdk", SDK_DES_KEY)):
                try:
                    iv = SDK_DES_IV if kind == "des-sdk" else DES_IV
                    got = _try_parse_json_bytes(des_decrypt(dec, key, iv))
                    if got:
                        return (kind, got[0], got[1], kind + " (легаси-DES)")
                except Exception:
                    continue
            # 2b) base64 без крипты
            got = _try_parse_json_bytes(dec)
            if got:
                return ("utf8", got[0], got[1], "base64(открытый JSON)")
        except Exception:
            pass
    # 3) Rijndael/AES 8.7.0
    if prefs:
        got = try_rijndael_decrypt(raw, prefs)
        if got:
            parsed = _try_parse_json_bytes(got[0])
            if parsed:
                return ("rijndael", parsed[0], parsed[1], got[1])
    return ("unknown", None, None, "ни одна схема не подошла")


def encode_any(obj, codec: str, kind: str, prefs: dict, mode_note: str = ""):
    """Объект -> байты файла в исходном формате."""
    js = json_to_bytes(obj, codec)
    if kind == "utf8" or kind == "utf16le":
        return js
    if kind == "xor":
        return xor_crypt(js)
    if kind == "des-statistic":
        return base64.b64encode(des_encrypt(js, KEY_STATISTIC))
    if kind == "des-default":
        return base64.b64encode(des_encrypt(js, KEY_DEFAULT))
    if kind == "des-sdk":
        return base64.b64encode(des_encrypt(js, SDK_DES_KEY, SDK_DES_IV))
    if kind == "rijndael":
        # mode/iv/payload-подсказки из note распознанного формата:
        # "AES {mode} key={ktag} {itag} payload={payload_tag}"
        mode_hint = "cbc"
        iv_hint = "iv-zero"
        payload_hint = "raw"
        m = re.search(r"AES (cbc|ecb)", mode_note or "")
        if m:
            mode_hint = m.group(1)
        m = re.search(r"(iv-[\w-]+)", mode_note or "")
        if m:
            iv_hint = m.group(1)
        m = re.search(r"payload=(\w+)", mode_note or "")
        if m:
            payload_hint = m.group(1)
        ct = try_rijndael_encrypt(js, prefs, mode_hint, key_hint=None, iv_hint=iv_hint)
        if ct is None:
            raise RuntimeError("Rijndael: не нашёлся ключ для обратного шифрования")
        if payload_hint == "b64":
            return base64.b64encode(ct)
        return ct
    raise RuntimeError("неизвестный kind: " + str(kind))


# ---------------------------------------------------------------------------
# 7. Модель документа: загрузка/сохранение, режим section|cloud
# ---------------------------------------------------------------------------
# класс -> имя секции в CloudSaveGameData
CLASS_SECTION = {
    "AboGameData": "gameData",
    "ItemData": "itemData",
    "StatisticData": "statisticData",
    "SettingData": "settingData",
    "SeasonData": "seasonData",
    "RGScript.Data.TaskData": "taskData",
    "RGScript.Data.Mall.MallReloadData": "mallReloadData",
    "MonsterRiseData": "monsterRiseData",
    "RGScript.WeaponEvolution.WeaponEvolutionData": "weaponEvolutionData",
    "RGScript.Data.MiscData": "miscData",
    "RGScript.Data.PvpData": "pvpData",
    "RGScript.Data.BattlePassSaveData": "battlePassSaveData",
    "ModeSeason.Escape.Scripts.EscapeSeasonData": "escapeSeasonData",
    "ModeSeason.Escape.Scripts.EscapeRunSaveData": None,   # только локальный
    "BackupGameData": None,                                 # обёртка backup.data
}
class SaveDoc:
    def __init__(self, path, kind, obj, codec, note, prefs, raw_len=0):
        self.path = path
        self.kind = kind              # utf8|utf16le|xor|des-*|rijndael
        self.obj = obj                # распарсенный JSON (dict)
        self.codec = codec            # utf8|utf16le
        self.note = note              # как расшифровали / детали rijndael
        self.prefs = prefs or {}
        self.raw_len = raw_len
        self.mode = self._detect_mode()
        self.section, self.cls = self._detect_section()

    def _detect_mode(self):
        if isinstance(self.obj, dict):
            keys = set(self.obj.keys())
            if "serialzedVersion" in keys and "cloudSaveId" in keys and "gameData" in keys:
                return "cloud"
        return "section"

    def _detect_section(self):
        parts = parse_save_name(self.path)
        name, cls = section_of(self.path)
        if self.mode == "cloud":
            return None, "CloudSaveGameData"
        if cls == "BackupGameData" and isinstance(self.obj, dict):
            return name, "BackupGameData"
        # bare-JSON без узнаваемого имени (например battles.data) — класс не знаем
        return name, cls

    # -- доступ к секции -----------------------------------------------------
    def get_section(self, section_name: str, create=False):
        """Корень секции: для section-режима — сам obj; для cloud — obj[name]."""
        if self.mode == "cloud":
            if section_name not in self.obj:
                if not create:
                    raise KeyError("в облачном дампе нет секции %r (null?)" % section_name)
                self.obj[section_name] = {}
            return self.obj[section_name]
        # section-режим: файл сам и есть секция
        cls = self.cls or ""
        expect = WRAPPER_CLASS_FIELD.get(cls)
        if expect and expect == section_name:
            if expect not in self.obj and create:
                self.obj[expect] = {}
            return self.obj[expect]
        if CLASS_SECTION.get(cls, "__none__") == section_name:
            return self.obj
        if cls == section_name:
            return self.obj
        raise KeyError("файл %s — секция %s (класс %s), а запрошена %r"
                       % (os.path.basename(self.path), CLASS_SECTION.get(cls) or "?", cls, section_name))

    def encode(self):
        return encode_any(self.obj, self.codec, self.kind, self.prefs, self.note)

    def sha(self):
        return hashlib.sha256(self.encode()).hexdigest()[:16]

    def known_fields(self):
        return class_fields(self.cls) if self.cls else None


def load_prefs_xml(path_or_text):
    """playerprefs.xml (путь или текст) -> dict строковых префов + Rijndael-ключи."""
    text = path_or_text
    if "\n" not in text and "<" not in text[:200]:
        with open(path_or_text, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    prefs = {}
    for m in re.finditer(r'<string name="([^"]+)">(.*?)</string>', text, re.S):
        prefs[m.group(1)] = m.group(2)
    out = {"cloudSaveId": prefs.get("cloudSaveId"),
           PREF_RIJ_KEY: prefs.get(PREF_RIJ_KEY),
           PREF_RIJ_IV: prefs.get(PREF_RIJ_IV),
           PREF_AES_KEY: prefs.get(PREF_AES_KEY)}
    out["_all"] = prefs
    return out


def load_doc(path: str, prefs_xml: str = None):
    prefs = {}
    if prefs_xml:
        prefs = load_prefs_xml(prefs_xml)
    elif os.environ.get("SK_PREFS"):
        prefs = load_prefs_xml(os.environ["SK_PREFS"])
    with open(path, "rb") as f:
        raw = f.read()
    kind, obj, codec, note = decode_any(raw, path, prefs)
    if obj is None:
        raise RuntimeError(
            "%s: формат не распознан (%s). Если это .aes — передайте --prefs "
            "playerprefs.xml с ключами DataRijndael128Key/DataRijndaelIV/DataAES256Key"
            % (path, note))
    return SaveDoc(path, kind, obj, codec, note, prefs, len(raw))


# ---------------------------------------------------------------------------
# 8. Движок путей: gameData.gems, materials.material_battery, plants[0].watered
# ---------------------------------------------------------------------------
def parse_path(path: str):
    """'plants[0].watered' -> ['plants', 0, 'watered']"""
    out = []
    for m in re.finditer(r"([A-Za-z0-9_]+)|\[(\d+)\]", path):
        if m.group(1) is not None:
            out.append(m.group(1))
        else:
            out.append(int(m.group(2)))
    if not out:
        raise ValueError("пустой путь: %r" % path)
    return out


def walk_get(obj, tokens):
    cur = obj
    for t in tokens:
        if isinstance(t, int):
            if not isinstance(cur, list) or t >= len(cur):
                raise KeyError("индекс %s вне диапазона" % t)
            cur = cur[t]
        else:
            if not isinstance(cur, dict) or t not in cur:
                raise KeyError("нет пути: %s" % t)
            cur = cur[t]
    return cur


def walk_set(obj, tokens, value, create=True):
    """Возвращает список созданных промежуточных ключей."""
    cur = obj
    created = []
    for i, t in enumerate(tokens):
        last = i == len(tokens) - 1
        if isinstance(t, int):
            if not isinstance(cur, list):
                raise TypeError("ожидался список на шаге %s" % t)
            if t >= len(cur):
                raise KeyError("индекс %d вне списка (для добавления есть append)" % t)
            if last:
                cur[t] = value
            else:
                cur = cur[t]
        else:
            if not isinstance(cur, dict):
                raise TypeError("ожидался объект на шаге %s" % t)
            if last:
                if t not in cur:
                    created.append(t)
                cur[t] = value
            else:
                if t not in cur:
                    if not create:
                        raise KeyError("нет ключа %r (создание отключено)" % t)
                    created.append(t)
                    cur[t] = {}
                cur = cur[t]
    return created


def coerce_value(text: str):
    """CLI-строка -> bool/int/float/None/dict/list/str."""
    if text == "true":
        return True
    if text == "false":
        return False
    if text in ("null", "none", "None"):
        return None
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    if re.fullmatch(r"-?\d+\.\d+", text):
        return float(text)
    if text[:1] in "{[\"":
        try:
            return json.loads(text)
        except Exception:
            pass
    return text


# ---------------------------------------------------------------------------
# 9. Валидация правок по базе знаний
# ---------------------------------------------------------------------------
def validate_path(doc: SaveDoc, tokens):
    """Проверка пути по классу секции. Возвращает (warnings, errors)."""
    warnings, errors = [], []
    if not tokens or not isinstance(tokens[0], str):
        return warnings, errors
    root = tokens[0]
    if doc.mode == "cloud":
        if root in CLOUD_SECTIONS:
            cls = {"gameData": "AboGameData", "itemData": "ItemData",
                   "statisticData": "StatisticData", "settingData": "SettingData",
                   "seasonData": "SeasonData", "mallReloadData": "RGScript.Data.Mall.MallReloadData",
                   "monsterRiseData": "MonsterRiseData", "miscData": "RGScript.Data.MiscData",
                   "pvpData": "RGScript.Data.PvpData",
                   "battlePassSaveData": "RGScript.Data.BattlePassSaveData",
                   "escapeSeasonData": "ModeSeason.Escape.Scripts.EscapeSeasonData"}.get(root)
            if cls and len(tokens) > 1 and isinstance(tokens[1], str):
                fields = class_fields(cls)
                if fields and tokens[1] not in fields:
                    warnings.append(
                        "неизвестное поле %s.%s (нет в классе %s) — игра ИГНОРИРУЕТ "
                        "неизвестные поля, правка потеряется" % (root, tokens[1], cls))
        else:
            errors.append("%r — не секция CloudSaveGameData (%s)" % (root, ", ".join(CLOUD_SECTIONS)))
        if root in PROTECTED:
            errors.append("%s защищён (привязка/схема); нужно --force-id" % root)
        return warnings, errors
    # section-режим
    if root in PROTECTED:
        errors.append("%s защищён (привязка/схема); нужно --force-id" % root)
        return warnings, errors
    fields = doc.known_fields()
    if fields and root not in fields and root not in WRAPPER_CLASS_FIELD.values():
        warnings.append(
            "неизвестное поле %r для %s — игра ИГНОРИРУЕТ неизвестные поля, "
            "правка потеряется (список полей: tools/sk870_save_classes.json)" % (root, doc.cls))
    return warnings, errors


# ---------------------------------------------------------------------------
# 10. Мутации: запись с бэкапом + round-trip верификацией
# ---------------------------------------------------------------------------
def autodetect_prefs(path: str):
    """Ищет playerprefs рядом с файлом (для .aes без --prefs)."""
    here = os.path.dirname(os.path.abspath(path)) or "."
    for root in (here, os.path.dirname(here), os.path.join(here, "shared_prefs"),
                 os.path.join(os.path.dirname(here), "shared_prefs")):
        if not os.path.isdir(root):
            continue
        for fn in sorted(os.listdir(root)):
            if fn.endswith(".playerprefs.xml") or fn == "playerprefs.xml":
                return os.path.join(root, fn)
    return None


def backup_file(path: str, backup_dir: str = None):
    ts = time.strftime("%Y%m%d_%H%M%S")
    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        dst = os.path.join(backup_dir, "%s.bak_%s" % (os.path.basename(path), ts))
    else:
        dst = "%s.bak_%s" % (path, ts)
    shutil.copy2(path, dst)
    return dst


def save_doc(doc: SaveDoc, backup_dir=None, verify=True):
    """Бэкап -> запись -> перечитывание -> deep-compare. Возвращает путь бэкапа."""
    if doc.kind == "unknown":
        raise RuntimeError("нельзя записать файл с нераспознанным форматом")
    backup_path = backup_file(doc.path, backup_dir)
    new_bytes = doc.encode()
    with open(doc.path, "wb") as f:
        f.write(new_bytes)
    if verify:
        kind2, obj2, codec2, note2 = decode_any(new_bytes, doc.path, doc.prefs)
        if obj2 != doc.obj:
            raise RuntimeError("round-trip верификация ПРОВАЛЕНА для %s — бэкап: %s"
                               % (doc.path, backup_path))
    return backup_path


def confirm(prompt: str, yes: bool):
    if yes:
        return True
    try:
        ans = input("%s [y/N]: " % prompt)
    except EOFError:
        return False
    return ans.strip().lower() in ("y", "yes", "д", "да")


def print_changes(changes):
    for path_s, old, new in changes:
        olds = json.dumps(old, ensure_ascii=False) if not isinstance(old, str) else repr(old)
        news = json.dumps(new, ensure_ascii=False) if not isinstance(new, str) else repr(new)
        if len(olds) > 60:
            olds = olds[:57] + "..."
        if len(news) > 60:
            news = news[:57] + "..."
        print("  %s: %s -> %s" % (path_s, olds, news))


# ---------------------------------------------------------------------------
# 11. Пресеты (безопасные операции; формы полей сверены с живым дампом)
#     ПРИНЦИП: не выдумываем игровые id — правим только ключи, уже
#     присутствующие в сейве, либо явно запрошенные пользователем.
# ---------------------------------------------------------------------------
PRESETS = {}


def preset(name, args_spec, help_text, experimental=False):
    def deco(fn):
        PRESETS[name] = {"args": args_spec, "help": help_text,
                         "fn": fn, "experimental": experimental}
        return fn
    return deco


def _gdoc(doc, section, create=False):
    return doc.get_section(section, create=create)


@preset("gems", "N", "алмазы: gems = lastGems = N, varGems = 0 (согласованный леджер)")
def p_gems(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    n = int(a[0])
    old_gems = gd.get("gems") or 0
    changes = [("gameData.gems", gd.get("gems"), n),
               ("gameData.lastGems", gd.get("lastGems"), n),
               ("gameData.varGems", gd.get("varGems"), 0)]
    if "cumulativeGems" in gd:
        new_cum = (gd.get("cumulativeGems") or 0) + max(0, n - old_gems)
        if new_cum != gd.get("cumulativeGems"):
            changes.append(("gameData.cumulativeGems", gd.get("cumulativeGems"), new_cum))
            gd["cumulativeGems"] = new_cum
    gd["gems"] = n
    gd["lastGems"] = n
    gd["varGems"] = 0
    return changes


@preset("fish-chips", "N", "рыбные чипы: gameData.fishChip = N")
def p_fish(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    n = int(a[0])
    gd["fishChip"] = n
    return [("gameData.fishChip", gd.get("fishChip"), n)]


@preset("season-coins", "N", "монеты сезона: seasonData.coin = N")
def p_season(doc, a, opts):
    sd = _gdoc(doc, "seasonData")
    n = int(a[0])
    sd["coin"] = n
    return [("seasonData.coin", sd.get("coin"), n)]


@preset("unlock-heroes", "", "открыть всех героев (значения существующих ключей heroUnlock -> true)")
def p_heroes(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    hu = gd.get("heroUnlock")
    if not isinstance(hu, dict):
        raise RuntimeError("heroUnlock пуст/не словарь — править нечего")
    changes = []
    for k, v in hu.items():
        if v is not True:
            hu[k] = True
            changes.append(("gameData.heroUnlock[%s]" % k, v, True))
    return changes


@preset("heroes-level", "N", "уровень всех открытых героев -> N (1..8, как в живом сейве)")
def p_heroes_lv(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    hl = gd.get("heroLevel")
    if not isinstance(hl, dict):
        raise RuntimeError("heroLevel пуст/не словарь")
    n = max(1, min(8, int(a[0])))
    changes = []
    for k, v in hl.items():
        if v != n:
            hl[k] = n
            changes.append(("gameData.heroLevel[%s]" % k, v, n))
    return changes


@preset("unlock-pets", "", "открыть всех питомцев (petUnlock -> true)")
def p_pets(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    pu = gd.get("petUnlock")
    if not isinstance(pu, dict):
        raise RuntimeError("petUnlock пуст/не словарь")
    changes = []
    for k, v in pu.items():
        if v is not True:
            pu[k] = True
            changes.append(("gameData.petUnlock[%s]" % k, v, True))
    return changes


@preset("research-all", "", "все известные чертежи -> Researched (itemData.blueprints)")
def p_research(doc, a, opts):
    it = _gdoc(doc, "itemData")
    bp = it.get("blueprints")
    if not isinstance(bp, dict):
        raise RuntimeError("blueprints пуст/не словарь")
    changes = []
    for k, v in bp.items():
        if v != "Researched":
            bp[k] = "Researched"
            changes.append(("itemData.blueprints[%s]" % k, v, "Researched"))
    return changes


@preset("material", "ID COUNT", "материал: itemData.materials[ID] = COUNT")
def p_material(doc, a, opts):
    it = _gdoc(doc, "itemData")
    mat = it.get("materials")
    if not isinstance(mat, dict):
        mat = {}
        it["materials"] = mat
    mid, cnt = a[0], int(a[1])
    old = mat.get(mid)
    mat[mid] = cnt
    return [("itemData.materials[%s]" % mid, old, cnt)]


@preset("all-materials", "COUNT", "все материалы, уже лежащие в сейве -> COUNT")
def p_all_materials(doc, a, opts):
    it = _gdoc(doc, "itemData")
    mat = it.get("materials")
    if not isinstance(mat, dict) or not mat:
        raise RuntimeError("materials пуст — нечего массово править")
    cnt = int(a[0])
    changes = []
    for k, v in mat.items():
        if v != cnt:
            mat[k] = cnt
            changes.append(("itemData.materials[%s]" % k, v, cnt))
    return changes


@preset("seed", "ID COUNT", "семена: itemData.seeds[ID] = COUNT")
def p_seed(doc, a, opts):
    it = _gdoc(doc, "itemData")
    seeds = it.get("seeds")
    if not isinstance(seeds, dict):
        seeds = {}
        it["seeds"] = seeds
    sid, cnt = a[0], int(a[1])
    old = seeds.get(sid)
    seeds[sid] = cnt
    return [("itemData.seeds[%s]" % sid, old, cnt)]


@preset("all-seeds", "COUNT", "все семена из сейва -> COUNT")
def p_all_seeds(doc, a, opts):
    it = _gdoc(doc, "itemData")
    seeds = it.get("seeds")
    if not isinstance(seeds, dict) or not seeds:
        raise RuntimeError("seeds пуст — нечего массово править")
    cnt = int(a[0])
    changes = []
    for k, v in seeds.items():
        if v != cnt:
            seeds[k] = cnt
            changes.append(("itemData.seeds[%s]" % k, v, cnt))
    return changes


@preset("skins-unlock", "", "скины: skinLock[*][*].Value != 1 -> 1 (экспериментально: семантика -1/-10 не доказана)", experimental=True)
def p_skins(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    sl = gd.get("skinLock")
    if not isinstance(sl, dict) or not sl:
        raise RuntimeError("skinLock пуст")
    changes = []
    for hero, lst in sl.items():
        if not isinstance(lst, list):
            continue
        for i, item in enumerate(lst):
            if isinstance(item, dict) and item.get("Value") != 1:
                old = item.get("Value")
                item["Value"] = 1
                changes.append(("gameData.skinLock[%s][%d].Value" % (hero, i), old, 1))
    return changes


@preset("reborn-card", "", "карточка возрождения: gameData.rebornCard=true + restoreKeys += reborn_card")
def p_reborn(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    old = gd.get("rebornCard")
    gd["rebornCard"] = True
    changes = [("gameData.rebornCard", old, True)]
    try:
        it = _gdoc(doc, "itemData")
        rk = it.get("restoreKeys")
        if isinstance(rk, list) and "reborn_card" not in rk:
            it["restoreKeys"] = rk + ["reborn_card"]
            changes.append(("itemData.restoreKeys += reborn_card", len(rk), len(rk) + 1))
    except KeyError:
        pass
    return changes


@preset("forge-add", "WEAPON_ID", "добавить оружие в кузницу: itemData.forgeWeapons += ID")
def p_forge(doc, a, opts):
    it = _gdoc(doc, "itemData")
    fw = it.get("forgeWeapons")
    if not isinstance(fw, list):
        fw = []
        it["forgeWeapons"] = fw
    wid = a[0]
    if wid in fw:
        return []
    fw.append(wid)
    return [("itemData.forgeWeapons += %s" % wid, len(fw) - 1, len(fw))]


@preset("currencies", "", "READ-ONLY сводка: валюты, герои, питомцы, материалы")
def p_currencies(doc, a, opts):
    def n(sec, path):
        try:
            return walk_get(_gdoc(doc, sec), parse_path(path))
        except Exception:
            return "<нет>"
    print("  gameData.gems           = %s" % n("gameData", "gems"))
    print("  gameData.lastGems       = %s" % n("gameData", "lastGems"))
    print("  gameData.varGems        = %s" % n("gameData", "varGems"))
    print("  gameData.fishChip       = %s" % n("gameData", "fishChip"))
    print("  gameData.rebornCard     = %s" % n("gameData", "rebornCard"))
    print("  seasonData.coin         = %s" % n("seasonData", "coin"))
    hu = n("gameData", "heroUnlock")
    print("  герои открыто           = %s" % (len(hu) if isinstance(hu, dict) else hu))
    pu = n("gameData", "petUnlock")
    print("  питомцы открыто         = %s" % (len(pu) if isinstance(pu, dict) else pu))
    mat = n("itemData", "materials")
    if isinstance(mat, dict):
        print("  материалов типов        = %d (сумма %d)" % (len(mat), sum(v for v in mat.values() if isinstance(v, (int, float)))))
        print("  материалов пример       = %s" % json.dumps(dict(list(mat.items())[:3]), ensure_ascii=False))
    bp = n("itemData", "blueprints")
    if isinstance(bp, dict):
        res = sum(1 for v in bp.values() if v == "Researched")
        print("  чертежей                = %d (Researched: %d)" % (len(bp), res))
    return None  # read-only


def run_preset(doc: SaveDoc, name: str, pargs, opts):
    if name not in PRESETS:
        raise KeyError("нет пресета %r (см. `presets`)" % name)
    spec = PRESETS[name]
    need = [x for x in spec["args"].split() if x]
    if len(pargs) < len(need):
        raise KeyError("пресету %r нужны аргументы: %s" % (name, spec["args"]))
    changes = spec["fn"](doc, pargs, opts)
    if changes is None:
        return None  # read-only
    return changes


# ---------------------------------------------------------------------------
# 12. adb: pull/push профиля игры (root-эмулятор/устройство)
#     ВАЖНО: игра кэширует секции в памяти (SaveUtil._saveDataDic) и пишет
#     отложенно (SaveInterval) — перед pull и push игру нужно закрыть (--kill).
# ---------------------------------------------------------------------------
def adb_run(adb_path, args, timeout=180):
    p = subprocess.run([adb_path] + args, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError("adb %s: %s" % (" ".join(args[:3]), p.stderr.decode("utf-8", "replace").strip()))
    return p.stdout


def device_has_root(adb_path):
    try:
        out = adb_run(adb_path, ["shell", "id"]).decode("utf-8", "replace")
        if "uid=0" in out:
            return "direct"
    except Exception:
        pass
    try:
        out = adb_run(adb_path, ["shell", "su -c id"]).decode("utf-8", "replace")
        if "uid=0" in out:
            return "su"
    except Exception:
        pass
    return None


def device_sh(adb_path, root_mode, cmd, timeout=180):
    if root_mode == "direct":
        return adb_run(adb_path, ["exec-out", cmd], timeout=timeout)
    return adb_run(adb_path, ["exec-out", "su -c \"%s\"" % cmd.replace('"', '\\"')], timeout=timeout)


def _q(path):
    """Одинарные кавычки для device-side пути (внутри su -c "...")."""
    return "'%s'" % path.replace("'", "'\\''")


def device_read_file(adb_path, root, path, timeout=300):
    """Читает файл с устройства БИНАРНО-БЕЗОПАСНО: base64 через exec-out.
    Старые adb (LDPlayer ставит ~1.0.31) портят бинарные потоки exec-out
    (PTY: \n -> \r\n) — tar -cf - через su молча теряет хвост; base64 —
    текст и проходит без искажений. Пустой файл -> пустой результат."""
    raw = device_sh(adb_path, root, "base64 %s" % _q(path), timeout=timeout)
    try:
        return base64.b64decode(b"".join(raw.split()))
    except Exception as e:
        raise RuntimeError("base64 не декодировался (%s) — на устройстве нет toybox base64?" % e)


def device_list_files(adb_path, root, d, timeout=120):
    """Список РЕГУЛЯРНЫХ файлов каталога (без подкаталогов). find без кавычек
    и глобов — не ломается об toybox ('bad arg') и старые su."""
    out = device_sh(adb_path, root, "find %s -maxdepth 1 -type f" % d,
                    timeout=timeout).decode("utf-8", "replace")
    return sorted({l.strip("\r") for l in out.splitlines() if l.strip()})


def cmd_pull(args):
    adb_path = args.adb or shutil.which("adb") or "adb"
    debug = bool(getattr(args, "debug", False))

    def dbg(msg):
        if debug:
            print("[dbg] %s" % msg)

    adb_run(adb_path, ["start-server"])
    try:
        dev = adb_run(adb_path, ["get-state"]).decode().strip()
    except RuntimeError as e:
        raise RuntimeError("adb: устройство не найдено (%s). LDPlayer: '.\\adb.exe devices', "
                           "затем 'adb connect 127.0.0.1:5554' (порт — из строки эмулятора: "
                           "5554/5555/5556...), либо укажи --adb" % e)
    if dev != "device":
        raise RuntimeError("устройство в состоянии %r (offline/unauthorized)" % dev)
    root = device_has_root(adb_path)
    if not root:
        raise RuntimeError("нет root: забрать /data/data/%s не выйдет. "
                           "LDPlayer: настройки эмулятора -> Root-права = ON, проверь: "
                           "adb shell su -c id" % PKG)
    if args.kill:
        adb_run(adb_path, ["shell", "am force-stop %s" % PKG])
        print("[i] игра закрыта (кэш секций сброшен на диск)")
    outdir = os.path.abspath(args.out)
    os.makedirs(outdir, exist_ok=True)
    base = "/data/data/%s" % PKG
    n_err = 0
    counts = {}
    for sub in ("files", "shared_prefs"):
        d = "%s/%s" % (base, sub)
        try:
            paths = device_list_files(adb_path, root, d)
        except RuntimeError as e:
            print("[!] %s не читается: %s" % (d, e))
            counts[sub] = 0
            continue
        dbg("%s: %d файлов" % (d, len(paths)))
        got = 0
        for rp in paths:
            name = rp.rsplit("/", 1)[-1]
            lp = os.path.join(outdir, sub, name)
            os.makedirs(os.path.dirname(lp), exist_ok=True)
            try:
                data = device_read_file(adb_path, root, rp)
            except Exception as e:
                n_err += 1
                print("[!] %s: %s" % (rp, e))
                continue
            with open(lp, "wb") as f:
                f.write(data)
            got += 1
            dbg("  %s (%d байт)" % (name, len(data)))
        counts[sub] = got
        print("[i] %s: забрано %d/%d файлов" % (sub, got, len(paths)))
    if n_err:
        print("[!] ошибок чтения: %d — повтори pull или запусти с --debug" % n_err)
    total = counts.get("files", 0) + counts.get("shared_prefs", 0)
    if total == 0:
        raise RuntimeError("ни один файл не прочитан: root есть, но su/base64 не отработали; "
                           "запусти с --debug и пришли вывод")
    fdir = os.path.join(outdir, "files")
    data_cnt = len([f for f in (os.listdir(fdir) if os.path.isdir(fdir) else [])
                    if f.endswith((".data", ".new"))])
    print("[ok] профиль вытащен в %s: files/=%d, shared_prefs/=%d, из них *.data/.new=%d"
          % (outdir, counts.get("files", 0), counts.get("shared_prefs", 0), data_cnt))
    if data_cnt < 10:
        print("[!] *.data подозрительно мало (<10) — секции сейва не долетели; "
              "запусти ещё раз с --debug и пришли вывод")
    prefs = autodetect_prefs(fdir) or autodetect_prefs(outdir)
    if prefs:
        p = load_prefs_xml(prefs)
        print("[i] playerprefs: %s" % os.path.relpath(prefs, outdir))
        print("[i] cloudSaveId = %s" % p.get("cloudSaveId"))
        print("[i] ключи Rijndael/AES: Rij=%s IV=%s AES256=%s" % (
            "есть" if p.get(PREF_RIJ_KEY) else "НЕТ",
            "есть" if p.get(PREF_RIJ_IV) else "НЕТ",
            "есть" if p.get(PREF_AES_KEY) else "НЕТ"))
    else:
        print("[!] playerprefs.xml не найден в вытащенном профиле — .aes не расшифруются")
    return 0


def cmd_push(args):
    adb_path = args.adb or shutil.which("adb") or "adb"
    src = os.path.abspath(args.dir)
    if not os.path.isdir(src):
        raise RuntimeError("нет каталога: %s" % src)
    root = device_has_root(adb_path)
    if not root:
        raise RuntimeError("нет root")
    if args.kill:
        adb_run(adb_path, ["shell", "am force-stop %s" % PKG])
        print("[i] игра закрыта (иначе кэш секций перезатрёт правки)")
    ts = time.strftime("%Y%m%d_%H%M%S")
    pre_backup = "/data/local/tmp/sk_pre_backup_%s.tar" % ts
    device_sh(adb_path, root, "tar -cf %s -C /data/data/%s files shared_prefs" % (pre_backup, PKG))
    print("[i] страховка на устройстве: %s" % pre_backup)
    buf = io.BytesIO()

    def _skip_new(ti):
        return None if ti.name.endswith(".data.new") else ti

    with tarfile.open(fileobj=buf, mode="w") as tf:
        for sub in ("files", "shared_prefs"):
            subdir = os.path.join(src, sub)
            if os.path.isdir(subdir):
                tf.add(subdir, arcname=sub, filter=_skip_new)
    tmp = os.path.join("/data/local/tmp", "sk_push_%s.tar" % ts)
    local_tar = os.path.join(src, "_sk_push.tar")
    with open(local_tar, "wb") as f:
        f.write(buf.getvalue())
    adb_run(adb_path, ["push", local_tar, tmp])
    device_sh(adb_path, root, "tar -xf %s -C /data/data/%s" % (tmp, PKG))
    if not getattr(args, "keep_new", False):
        # .data.new — временные файлы атомарной записи; если на устройстве остался
        # свежий .new, игра при первом запуске может откатить им наши правки .data.
        try:
            lst = device_sh(adb_path, root,
                            "find /data/data/%s/files -maxdepth 1 -type f -name '*.data.new'" % PKG
                            ).decode("utf-8", "replace").split()
            if lst:
                device_sh(adb_path, root, "rm -f %s" % " ".join(lst))
                print("[i] убраны временные .data.new (%d шт.) — иначе первый запуск мог бы откатить правки" % len(lst))
        except Exception as e:
            print("[!] не удалось убрать .data.new (%s) — если правки откатятся, удали их вручную: "
                  "adb shell \"su -c 'rm /data/data/%s/files/*.data.new'\"" % (e, PKG))
    try:
        uid = device_sh(adb_path, root, "stat -c %%u /data/data/%s/files" % PKG).decode().strip()
        if uid.isdigit():
            device_sh(adb_path, root, "chown -R %s:%s /data/data/%s/files /data/data/%s/shared_prefs"
                      % (uid, uid, PKG, PKG))
            print("[i] владелец файлов восстановлен: uid %s" % uid)
    except Exception as e:
        print("[!] chown не удался (%s) — если игра не увидит правки, проверь владельца" % e)
    if args.no_keep:
        device_sh(adb_path, root, "rm -f %s %s" % (tmp, pre_backup))
    else:
        device_sh(adb_path, root, "rm -f %s" % tmp)
    os.remove(local_tar)
    print("[ok] залито из %s; запускай игру" % src)
    if not args.no_keep:
        print("[i] pre-backup оставлен на устройстве: %s (восстановление: su -c 'tar -xf %s -C /data/data/%s')"
              % (pre_backup, pre_backup, PKG))
    return 0


# ---------------------------------------------------------------------------
# 13. Selftest: round-trip всех форматов + правки + движок путей
# ---------------------------------------------------------------------------
def cmd_selftest(args):
    fails = []

    def check(name, cond, extra=""):
        print("  [%s] %s %s" % ("OK" if cond else "FAIL", name, extra))
        if not cond:
            fails.append(name)

    tmp = tempfile.mkdtemp(prefix="sk_selftest_")

    # --- синтетический сейв с реальными формами полей (по живому дампу) ---
    game_obj = {
        "gems": 1124375, "lastGems": 1124278, "varGems": 97, "cumulativeGems": 0,
        "fishChip": 0, "rebornCard": False, "roomLevel": 3,
        "heroUnlock": {"Airbender": True, "Alchemist": False},
        "heroLevel": {"Airbender": 7, "Alchemist": 1},
        "petUnlock": {"Bat": True},
        "skinLock": {"Airbender": [{"Key": 0, "Value": 1}, {"Key": 2, "Value": -10}]},
        "usedSecretKeys": ["SKGIFT"], "gameVersion": 2130,
    }
    item_obj = {
        "materials": {"material_battery": 136, "material_ancient_weapon_fragment": 87},
        "blueprints": {"blueprint_m_mech_2": "Researched", "blueprint_x": "Got"},
        "seeds": {"plant_banboo_seed": 1}, "forgeWeapons": ["weapon_034"],
        "mythicWeapons": [{"id": 5, "level": 3}], "restoreKeys": ["reborn_card"],
    }

    # --- 1) round-trip каждого формата ---
    codecs_objs = [
        ("utf8", game_obj, "battles.data"),
        ("xor", game_obj, "game.data"),
        ("des-statistic", item_obj, "statistic.data"),
        ("des-default", item_obj, "item_data.data"),
        ("des-sdk", item_obj, "misc_data.data"),
    ]
    prefs = {PREF_AES_KEY: base64.b64encode(bytes(range(32))).decode(),
             PREF_RIJ_KEY: base64.b64encode(bytes(range(16))).decode(),
             PREF_RIJ_IV: base64.b64encode(bytes(range(16))).decode()}
    for kind, obj, fname in codecs_objs:
        raw = encode_any(obj, "utf8", kind, prefs)
        kind2, obj2, codec2, note2 = decode_any(raw, fname, prefs)
        check("round-trip %s" % kind, obj2 == obj, "(%s)" % note2)

    # --- 2) AES/Rijndael round-trip через playerprefs-ключи ---
    js = json_to_bytes(game_obj, "utf8")
    ct = try_rijndael_encrypt(js, prefs, "cbc")
    check("aes: шифрование работает", ct is not None and len(ct) % 16 == 0)
    if ct is not None:
        kind2, obj2, codec2, note2 = decode_any(ct, "game.aes", prefs)
        check("aes: расшифровка по prefs", kind2 == "rijndael" and obj2 == game_obj, note2)

    # --- 3) полный цикл: файл -> загрузка -> пресет -> запись -> перечитывание ---
    fgame = os.path.join(tmp, "game.data")
    with open(fgame, "wb") as f:
        f.write(encode_any(game_obj, "utf8", "xor", prefs))
    doc = load_doc(fgame)
    check("режим section + класс", doc.mode == "section" and doc.cls == "AboGameData", doc.cls or "?")
    ch = run_preset(doc, "gems", ["800000"], {})
    check("пресет gems: леджер",
          doc.obj["gems"] == 800000 and doc.obj["lastGems"] == 800000 and doc.obj["varGems"] == 0
          and doc.obj["cumulativeGems"] == 0, str(len(ch)) + " правок")
    save_doc(doc, verify=True)
    doc2 = load_doc(fgame)
    check("перезачитка после записи", doc2.obj["gems"] == 800000)

    # --- 4) cloud-режим на UTF-16LE (как GPGS-снапшот) ---
    cloud_obj = {"serialzedVersion": 1, "cloudSaveId": "GPV2_235288940", "platformId": "U",
                 "gameData": json.loads(json.dumps(game_obj)),
                 "itemData": json.loads(json.dumps(item_obj)),
                 "statisticData": None, "settingData": None, "taskData": None,
                 "maps": None, "sandboxConfig": None, "seasonData": {"coin": 34815},
                 "mallReloadData": None, "monsterRiseData": None,
                 "weaponEvolutionData": None, "miscData": None, "pvpData": None,
                 "battlePassSaveData": None, "escapeSeasonData": None}
    fcloud = os.path.join(tmp, "SoulKnightCloud")
    with open(fcloud, "wb") as f:
        f.write(json_to_bytes(cloud_obj, "utf16le"))
    cdoc = load_doc(fcloud)
    check("cloud-режим + utf16le", cdoc.mode == "cloud" and cdoc.codec == "utf16le")
    run_preset(cdoc, "unlock-heroes", [], {})
    check("cloud: unlock-heroes", cdoc.obj["gameData"]["heroUnlock"]["Alchemist"] is True)
    run_preset(cdoc, "season-coins", ["100000"], {})
    check("cloud: season-coins", cdoc.obj["seasonData"]["coin"] == 100000)
    ch = run_preset(cdoc, "gems", ["500000"], {})
    check("cloud: gems по секции", cdoc.obj["gameData"]["gems"] == 500000)
    back = json_to_bytes(cdoc.obj, "utf16le")
    kind2, obj2, _, _ = decode_any(back, "SoulKnightCloud", {})
    check("cloud round-trip utf16le", obj2 == cdoc.obj)

    # --- 5) движок путей ---
    t = parse_path("plants[0].watered")
    check("parse_path с индексом", t == ["plants", 0, "watered"], str(t))
    o = {"a": {"b": [10, 20]}}
    walk_set(o, parse_path("a.b[1]"), 99)
    check("walk_set по индексу", o["a"]["b"][1] == 99)
    created = walk_set(o, parse_path("a.new.deep"), 5)
    check("walk_set создаёт недостающее", o["a"]["new"]["deep"] == 5 and created == ["new", "deep"])
    try:
        walk_set(o, parse_path("a.b[9]"), 1)
        check("walk_set запрещает выход за список", False)
    except KeyError:
        check("walk_set запрещает выход за список", True)
    check("coerce_value типы", coerce_value("true") is True and coerce_value("-3") == -3
          and coerce_value("1.5") == 1.5 and coerce_value('{"x":1}') == {"x": 1}
          and coerce_value("SKGIFT") == "SKGIFT")

    # --- 6) имена файлов ---
    p = parse_save_name("statistic_(235288966)_2.data")
    check("parse_save_name accountId+слот", p == {"name": "statistic", "account": "235288966", "slot": "2"}, str(p))
    p2 = parse_save_name("game.data")
    check("parse_save_name без акка", p2["name"] == "game" and p2["account"] is None)
    check("section_of statistic", section_of("statistic_(1)_0.data")[1] == "StatisticData")
    check("section_of game", section_of("game.data")[1] == "AboGameData")

    # --- 7) защита служебных полей + валидация неизвестных ---
    cdoc2 = load_doc(fcloud)
    warn, err = validate_path(cdoc2, ["cloudSaveId"])
    check("защита cloudSaveId", bool(err))
    warn, err = validate_path(cdoc2, ["gameData", "not_a_real_field"])
    check("предупреждение о неизвестном поле", bool(warn) and not err, warn[0][:60] if warn else "")
    warn, err = validate_path(cdoc2, ["gameData", "gems"])
    check("известное поле чисто", not warn and not err)

    # --- 8) DES-вектор из SKDEditor-схемы (детерминированность) ---
    sample = json_to_bytes({"x": 1}, "utf8")
    enc = base64.b64encode(des_encrypt(sample, KEY_DEFAULT))
    dec = des_decrypt(base64.b64decode(enc), KEY_DEFAULT)
    check("DES iambo round-trip", dec == sample)

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if fails:
        print("SELFTEST ПРОВАЛЕН: %s" % ", ".join(fails))
        return 1
    print("SELFTEST ПРОЙДЕН: все форматы, пресеты, пути и защиты работают")
    return 0


# ---------------------------------------------------------------------------
# 14. CLI
# ---------------------------------------------------------------------------
def _info_line(path, prefs=None):
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError as e:
        return "%s: %s" % (path, e)
    if prefs is None:
        prefs_xml = autodetect_prefs(path)
        prefs = load_prefs_xml(prefs_xml) if prefs_xml else {}
    kind, obj, codec, note = decode_any(raw, path, prefs)
    parts = parse_save_name(path)
    _, cls = section_of(path)
    if kind == "unknown":
        return ("%s\n    kind=НЕРАСПОЗНАН (%s); size=%d; head=%s"
                % (path, note, len(raw), raw[:32].hex()))
    mode = "cloud" if (isinstance(obj, dict) and "serialzedVersion" in obj and "cloudSaveId" in obj) else "section"
    nkeys = len(obj) if isinstance(obj, dict) else "?"
    extra = ""
    if mode == "cloud":
        extra = "; cloudSaveId=%s" % obj.get("cloudSaveId")
    known = class_fields(cls if mode == "section" else "CloudSaveGameData")
    unk = ""
    if known and isinstance(obj, dict):
        diff = [k for k in obj.keys() if k not in known and not k.startswith("_")]
        if diff and mode == "section" and cls:
            unk = "; ВНЕ КЛАССА: %s" % ", ".join(diff[:6])
    return ("%s\n    kind=%s (%s); mode=%s; class=%s; ключей=%s%s%s\n    name_parts=%s"
            % (path, kind, note, mode, cls or "?", nkeys, extra, unk, json.dumps(parts, ensure_ascii=False)))


def cmd_info(args):
    prefs = None
    if args.prefs:
        prefs = load_prefs_xml(args.prefs)
    for path in args.files:
        if os.path.isdir(path):
            for fn in sorted(os.listdir(path)):
                fp = os.path.join(path, fn)
                if fn.endswith(SAVE_SUFFIXES) or fn == "SoulKnightCloud":
                    print(_info_line(fp, prefs))
        else:
            print(_info_line(path, prefs))
    return 0


def cmd_list(args):
    d = args.dir
    files = []
    for root, _dirs, fns in os.walk(d):
        for fn in fns:
            if fn.endswith(SAVE_SUFFIXES) or fn == "SoulKnightCloud" or ".playerprefs" in fn:
                if ".bak_" in fn:
                    continue
                files.append(os.path.join(root, fn))
    if not files:
        print("в %s нет сейв-файлов (*%s)" % (d, "/".join(SAVE_SUFFIXES)))
        return 1
    for fp in sorted(files):
        if ".playerprefs" in fp:
            try:
                p = load_prefs_xml(fp)
                print("%s\n    playerprefs: cloudSaveId=%s; Rij=%s; IV=%s; AES256=%s" % (
                    fp, p.get("cloudSaveId"),
                    bool(p.get(PREF_RIJ_KEY)), bool(p.get(PREF_RIJ_IV)), bool(p.get(PREF_AES_KEY))))
            except Exception as e:
                print("%s: %s" % (fp, e))
        else:
            print(_info_line(fp))
    return 0


def cmd_dump(args):
    doc = load_doc(args.file, args.prefs or autodetect_prefs(args.file))
    js = json_to_bytes(doc.obj, "utf8")
    if args.out:
        with open(args.out, "wb") as f:
            f.write(js)
        print("[ok] %s -> %s (%d bytes, kind=%s, %s)" % (args.file, args.out, len(js), doc.kind, doc.note))
    else:
        sys.stdout.buffer.write(js + b"\n")
    return 0


def cmd_show(args):
    doc = load_doc(args.file, args.prefs or autodetect_prefs(args.file))
    if args.path:
        obj = walk_get(doc.obj, parse_path(args.path))
    else:
        obj = doc.obj
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if args.limit and len(text) > args.limit * 80:
        print(text[: args.limit * 80] + "\n... (обрезано, --limit N)")
    else:
        print(text)
    return 0


def _mutator_cmd(args, apply_fn, desc):
    prefs_xml = args.prefs or autodetect_prefs(args.file)
    doc = load_doc(args.file, prefs_xml)
    changes = apply_fn(doc)
    if not changes:
        print("[i] изменений нет (%s)" % desc)
        return 0
    print("Планируемые правки (%s):" % desc)
    print_changes(changes)
    if args.dry_run:
        print("[dry-run] ничего не записано")
        return 0
    if not confirm("Применить к %s?" % args.file, args.yes):
        print("отменено")
        return 1
    backup_path = save_doc(doc, args.backup_dir)
    new_sha = hashlib.sha256(open(args.file, "rb").read()).hexdigest()
    print("[ok] записано; бэкап: %s; sha256: %s" % (backup_path, new_sha[:16]))
    return 0


def cmd_write(args):
    def fn(doc):
        tokens = parse_path(args.path)
        warn, err = validate_path(doc, tokens)
        if err and not args.force_id:
            raise RuntimeError("; ".join(err))
        for w in warn:
            print("[!] %s" % w)
        value = coerce_value(args.value)
        old = None
        try:
            old = walk_get(doc.obj, tokens)
        except Exception:
            pass
        walk_set(doc.obj, tokens, value, create=not args.no_create)
        return [(args.path, old, value)]
    return _mutator_cmd(args, fn, "write %s" % args.path)


def cmd_add(args):
    def fn(doc):
        tokens = parse_path(args.path)
        warn, err = validate_path(doc, tokens)
        if err and not args.force_id:
            raise RuntimeError("; ".join(err))
        for w in warn:
            print("[!] %s" % w)
        cur = walk_get(doc.obj, tokens)
        if not isinstance(cur, (int, float)) or isinstance(cur, bool):
            raise TypeError("по пути %s лежит %s — add только для чисел" % (args.path, type(cur).__name__))
        new = cur + args.delta
        walk_set(doc.obj, tokens, new)
        return [(args.path, cur, new)]
    return _mutator_cmd(args, fn, "add %s" % args.path)


def cmd_append(args):
    def fn(doc):
        tokens = parse_path(args.path)
        warn, err = validate_path(doc, tokens)
        if err and not args.force_id:
            raise RuntimeError("; ".join(err))
        for w in warn:
            print("[!] %s" % w)
        lst = walk_get(doc.obj, tokens)
        if not isinstance(lst, list):
            raise TypeError("по пути %s лежит %s — append только в список" % (args.path, type(lst).__name__))
        value = coerce_value(args.value)
        lst.append(value)
        return [("%s += %s" % (args.path, args.value), len(lst) - 1, len(lst))]
    return _mutator_cmd(args, fn, "append %s" % args.path)


def cmd_delete(args):
    def fn(doc):
        tokens = parse_path(args.path)
        warn, err = validate_path(doc, tokens)
        if err and not args.force_id:
            raise RuntimeError("; ".join(err))
        for w in warn:
            print("[!] %s" % w)
        old = walk_get(doc.obj, tokens)
        parent = walk_get(doc.obj, tokens[:-1]) if len(tokens) > 1 else doc.obj
        key = tokens[-1]
        if isinstance(key, int):
            removed = parent.pop(key)
        else:
            removed = parent.pop(key)
        print("[!] помни: игра при следующем Save материализует удалённое поле дефолтом класса")
        return [(args.path, old, "<удалено>")]
    return _mutator_cmd(args, fn, "delete %s" % args.path)


def cmd_preset(args):
    def fn(doc):
        return run_preset(doc, args.name, args.pargs, vars(args))
    spec = PRESETS.get(args.name)
    if spec and spec["experimental"] and not args.yes and not args.dry_run:
        print("[!] ЭКСПЕРИМЕНТАЛЬНЫЙ пресет: %s" % spec["help"])
    return _mutator_cmd(args, fn, "preset %s %s" % (args.name, " ".join(args.pargs)))


def cmd_presets(args):
    print("Пресеты (цель -> применение):")
    for name, spec in PRESETS.items():
        mark = " [ЭКСПЕРИМЕНТ]" if spec["experimental"] else ""
        print("  %-16s %-10s %s%s" % (name, spec["args"], spec["help"], mark))
    print("\nВалидация: известные поля — tools/sk870_save_classes.json;")
    print("неизвестные поля игра игнорирует; serialzedVersion/cloudSaveId/ChillyUid защищены.")
    return 0


def cmd_keys(args):
    p = load_prefs_xml(args.prefs_xml)
    print("cloudSaveId       = %s" % p.get("cloudSaveId"))
    print("DataRijndael128Key= %s" % (p.get(PREF_RIJ_KEY) or "<нет>"))
    print("DataRijndaelIV    = %s" % (p.get(PREF_RIJ_IV) or "<нет>"))
    print("DataAES256Key     = %s" % (p.get(PREF_AES_KEY) or "<нет>"))
    if p.get(PREF_RIJ_KEY):
        for tag, raw in _key_candidates(p[PREF_RIJ_KEY])[:4]:
            print("  ключ-кандидат (%s): %s..." % (tag, raw.hex()[:32]))
    return 0


def cmd_backup(args):
    ts = time.strftime("%Y%m%d_%H%M%S")
    dst = args.out or ("sk_backup_" + ts)
    os.makedirs(dst, exist_ok=True)
    n = 0
    for root, _dirs, fns in os.walk(args.dir):
        for fn in fns:
            if ".bak_" in fn:
                continue
            if fn.endswith(SAVE_SUFFIXES) or fn == "SoulKnightCloud" or ".playerprefs" in fn:
                shutil.copy2(os.path.join(root, fn), os.path.join(dst, fn))
                n += 1
    print("[ok] %d файлов -> %s" % (n, dst))
    return 0


def cmd_restore(args):
    src, dst = args.backup_dir, args.dir
    if not confirm("Восстановить %d файлов из %s поверх %s?" % (len(os.listdir(src)), src, dst), args.yes):
        print("отменено")
        return 1
    n = 0
    for fn in os.listdir(src):
        base = fn.split(".bak_")[0]
        s = os.path.join(src, fn)
        d1 = os.path.join(dst, base)
        if os.path.isfile(d1):
            shutil.copy2(s, d1)
            n += 1
        else:
            for root, _dirs, fns in os.walk(dst):
                if base in fns:
                    shutil.copy2(s, os.path.join(root, base))
                    n += 1
                    break
    print("[ok] восстановлено %d файлов" % n)
    return 0


def main(argv=None):
    # общий флаги, доступные и ДО, и ПОСЛЕ подкоманды (SUPPRESS не затирает
    # значения, уже распарсенные главным парсером)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--prefs", default=argparse.SUPPRESS,
                        help="playerprefs.xml (для .aes-ключей и cloudSaveId)")
    common.add_argument("--yes", "-y", action="store_true", default=argparse.SUPPRESS,
                        help="не спрашивать подтверждение")
    common.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS,
                        help="показать правки, не записывать")
    common.add_argument("--backup-dir", default=argparse.SUPPRESS,
                        help="каталог для бэкапов (по умолчанию рядом с файлом)")
    common.add_argument("--force-id", action="store_true", default=argparse.SUPPRESS,
                        help="разрешить правку cloudSaveId/platformId/ChillyUid/serialzedVersion")

    ap = argparse.ArgumentParser(
        prog="sk_save_editor.py",
        description="Редактор локальных сохранений Soul Knight 8.7.0 и облачных дампов "
                    "(docs/LOCAL-SAVE-870.md; форк SKDEditor, GPL-2.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
               "  %(prog)s info ./device_saves\n"
               "  %(prog)s show game.data gems\n"
               "  %(prog)s preset game.data gems 800000\n"
               "  %(prog)s preset cloud.bin currencies\n"
               "  %(prog)s pull -o ./device_saves --kill\n"
               "  %(prog)s push ./device_saves --kill\n"
               "  %(prog)s selftest\n")
    ap.add_argument("--prefs", help="playerprefs.xml (для .aes-ключей и cloudSaveId)")
    ap.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждение")
    ap.add_argument("--dry-run", action="store_true", help="показать правки, не записывать")
    ap.add_argument("--backup-dir", help="каталог для бэкапов (по умолчанию рядом с файлом)")
    ap.add_argument("--force-id", action="store_true",
                    help="разрешить правку cloudSaveId/platformId/ChillyUid/serialzedVersion")
    sub = ap.add_subparsers(dest="cmd")

    sp = sub.add_parser("info", parents=[common], help="форматы файлов")
    sp.add_argument("files", nargs="+")
    sp.set_defaults(fn=cmd_info)

    sp = sub.add_parser("list", parents=[common], help="инвентарь каталога сейвов")
    sp.add_argument("dir")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("dump", parents=[common], help="расшифровать файл в JSON")
    sp.add_argument("file")
    sp.add_argument("-o", "--out")
    sp.set_defaults(fn=cmd_dump)

    sp = sub.add_parser("show", parents=[common], help="показать JSON-поддерево")
    sp.add_argument("file")
    sp.add_argument("path", nargs="?", help="напр. gameData.gems / materials / plants[0]")
    sp.add_argument("--limit", type=int, default=60)
    sp.set_defaults(fn=cmd_show)

    sp = sub.add_parser("read", parents=[common], help="прочитать значение по пути")
    sp.add_argument("file")
    sp.add_argument("path")
    sp.set_defaults(fn=lambda a: print(json.dumps(
        walk_get(load_doc(a.file, a.prefs or autodetect_prefs(a.file)).obj, parse_path(a.path)),
        ensure_ascii=False)) or 0)

    sp = sub.add_parser("write", parents=[common], help="записать значение по пути")
    sp.add_argument("file")
    sp.add_argument("path")
    sp.add_argument("value")
    sp.add_argument("--no-create", action="store_true", help="не создавать недостающие ключи")
    sp.set_defaults(fn=cmd_write)

    sp = sub.add_parser("add", parents=[common], help="прибавить число по пути")
    sp.add_argument("file")
    sp.add_argument("path")
    sp.add_argument("delta", type=int)
    sp.set_defaults(fn=cmd_add)

    sp = sub.add_parser("append", parents=[common], help="добавить элемент в список")
    sp.add_argument("file")
    sp.add_argument("path")
    sp.add_argument("value")
    sp.set_defaults(fn=cmd_append)

    sp = sub.add_parser("delete", parents=[common], help="удалить ключ/индекс (осторожно: см. вывод)")
    sp.add_argument("file")
    sp.add_argument("path")
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("presets", help="список пресетов")
    sp.set_defaults(fn=cmd_presets)

    sp = sub.add_parser("preset", parents=[common], help="применить пресет")
    sp.add_argument("file")
    sp.add_argument("name")
    sp.add_argument("pargs", nargs="*")
    sp.set_defaults(fn=cmd_preset)

    sp = sub.add_parser("backup", parents=[common], help="бэкап каталога сейвов")
    sp.add_argument("dir")
    sp.add_argument("-o", "--out")
    sp.set_defaults(fn=cmd_backup)

    sp = sub.add_parser("restore", parents=[common], help="восстановить из бэкапа")
    sp.add_argument("backup_dir")
    sp.add_argument("dir")
    sp.set_defaults(fn=cmd_restore)

    sp = sub.add_parser("keys", help="ключи/ID из playerprefs.xml")
    sp.add_argument("prefs_xml")
    sp.set_defaults(fn=cmd_keys)

    sp = sub.add_parser("pull", help="вытащить профиль игры по adb (root)")
    sp.add_argument("-o", "--out", default="./device_saves")
    sp.add_argument("--adb", help="путь к adb (напр. D:\\LDPlayer\\LDPlayer14\\adb.exe)")
    sp.add_argument("--kill", action="store_true", help="закрыть игру перед pull")
    sp.add_argument("--debug", action="store_true", help="печатать каждый файл и размер")
    sp.set_defaults(fn=cmd_pull)

    sp = sub.add_parser("push", help="залить каталог сейвов на устройство (root)")
    sp.add_argument("dir")
    sp.add_argument("--adb", help="путь к adb")
    sp.add_argument("--kill", action="store_true", help="закрыть игру перед push")
    sp.add_argument("--no-keep", action="store_true", help="удалить pre-backup с устройства")
    sp.add_argument("--keep-new", action="store_true",
                    help="НЕ удалять *.data.new на устройстве (не рекомендуется)")
    sp.set_defaults(fn=cmd_push)

    sp = sub.add_parser("selftest", help="проверка всех форматов и правок")
    sp.set_defaults(fn=cmd_selftest)

    args = ap.parse_args(argv)
    if not getattr(args, "fn", None):
        ap.print_help()
        return 2
    try:
        return args.fn(args)
    except Exception as e:
        print("[!] %s" % e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
