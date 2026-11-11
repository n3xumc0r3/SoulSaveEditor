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
VERSION = "1.4.0"

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


def _uid_variant_hint(doc):
    """Подсказка, если рядом лежат другие копии той же секции. У 8.7.0 <sec>.data
    часто пустой шаблон, реальные данные — в <sec>_<accountId>_.data (каноничная
    копия, см. LOCAL-SAVE-870.md §1.1 и GUIDE-TOP-SAVE §3)."""
    d = os.path.dirname(os.path.abspath(doc.path)) or "."
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return ""
    own = parse_save_name(doc.path)["name"].lower()
    sibs = [fn for fn in names
            if fn != os.path.basename(doc.path)
            and fn.lower().endswith(SAVE_SUFFIXES)
            and not fn.lower().endswith(".data.new")
            and parse_save_name(fn)["name"].lower() == own]
    if not sibs:
        return ""
    return (" В каталоге есть другие копии этой секции: %s. Реальные данные обычно "
            "в UID-варианте (<sec>_<accountId>_.data) — прогони пресет и на нём, "
            "напр.: python sk_save_editor.py preset \"%s\" <пресет> --yes"
            % (", ".join(sibs), os.path.join(d, sibs[-1])))


# Каталог игровых id для синтеза предметов на ПУСТОМ аккаунте (materials/seeds/
# blueprints там пустые — массово править нечего). Источники — только реальные
# строки: (а) itemData облачного сейва аккаунта 235288966 (skdump/
# snap_SoulKnightCloud), (б) литералы global-metadata.dat 8.7.0 — соседство в
# строковой куче (id зажат между двумя подтверждёнными) = подтверждение.
# Игра молча игнорирует неизвестные id, так что риск ограничен. Расширение:
# preset ... grant-materials 499 --ids-file мой.json
_IDS_870_JSON = r'''{
  "_source": "itemData cloud snapshot GPV2_235288940 (2026-11) + global-metadata.dat 8.7.0 string literals + device prefs rewards config (1.4.1: + fragment_1/2); verified by context",
  "materials": [
    "material_ancient_weapon_fragment", "material_battery", "material_c17_book_1", "material_c17_book_2",
    "material_c18_book_1", "material_c18_book_2", "material_c20_book_1", "material_c20_book_2",
    "material_c21_book_1", "material_c21_book_2", "material_cell", "material_dead_cell",
    "material_fertilize", "material_fish", "material_fragment_c6h8o6", "material_fragment_zulan",
    "material_gear", "material_gem", "material_generic_weapon_fragment_1", "material_generic_weapon_fragment_2",
    "material_generic_weapon_fragment_3", "material_generic_weapon_fragment_4",
    "material_generic_weapon_fragment_5", "material_grain", "material_hopper_ticket", "material_iron",
    "material_jewelry_fragment", "material_magic_black", "material_magic_blue", "material_magic_cyan",
    "material_magic_green", "material_magic_orange", "material_magic_purple", "material_magic_red",
    "material_mech_fragment", "material_master_enlightenment", "material_master_insight",
    "material_mentor_wish", "material_delta", "material_skill_Druid_1", "material_skill_Paladin_2",
    "material_skill_Priest_1", "material_skill_Viking_2", "material_skill_Warlock_1",
    "material_skin_fragment_boss07", "material_skin_fragment_boss08", "material_skin_fragment_boss16",
    "material_skin_fragment_boss19", "material_skin_fragment_boss23", "material_skin_fragment_boss30",
    "material_skin_fragment_c12_s12", "material_skin_fragment_c12_s6", "material_skin_fragment_c16_s6",
    "material_skin_fragment_c7_s12", "material_skin_fragment_flag", "material_skin_fragment_law",
    "material_skin_fragment_shop", "material_tape_boss1", "material_tape_boss2", "material_tape_castle",
    "material_tape_graveyard", "material_tape_ice", "material_tape_level_2g", "material_tape_ruins",
    "material_ticket", "material_ticket_bossrush", "material_wood", "material_combo_gun_spark_currency",
    "material_season_coin", "material_season_ticket_treasure_box", "material_season_treasure_box_1",
    "material_season_treasure_box_2", "material_season_treasure_box_3"
  ],
  "seeds": [
    "plant_banboo_seed", "plant_binary_tree_seed", "plant_cactus_seed", "plant_carrot_seed",
    "plant_datura_seed", "plant_defend_flower_seed", "plant_eator_seed", "plant_fantastic_flower_seed",
    "plant_firecracker_seed", "plant_gear_flower_seed", "plant_gem_flower_seed", "plant_gem_tree_seed",
    "plant_heptacolor_seed", "plant_icepear_seed", "plant_iron_tree_seed", "plant_lavender_seed",
    "plant_lotus_seed", "plant_lycoris_seed", "plant_magic_flower_seed", "plant_mandrake_seed",
    "plant_mirror_plane_flower_seed", "plant_mushroom_seed", "plant_pumpkin_seed", "plant_qilixiang_seed",
    "plant_rainbow_grass_seed", "plant_roselle_seed", "plant_shallot_seed", "plant_silver_poplar_seed",
    "plant_strange_flower_seed", "plant_tree_seed", "plant_trumpet_seed", "plant_vine_seed",
    "plant_watermelon_seed", "plant_worm_seed", "plant_xmas_seed", "plant_zongzi_seed"
  ],
  "blueprints": {
    "blueprint_m_mech_2": "Researched", "blueprint_m_mech_4": "Researched",
    "blueprint_room_decorate_fishbowl": "Got", "blueprint_room_decorate_magic_well": "Got",
    "blueprint_room_decorate_mysteriou_statue": "Got", "blueprint_skill_Priest_1": "Researched",
    "blueprint_skin_alchemist_8": "Researched", "blueprint_skin_ranger_6": "Researched",
    "blueprint_skin_ranger_12": "Researched", "blueprint_skin_taoist_14": "Researched",
    "blueprint_transform_weapon_beheaded": "Researched", "blueprint_transform_weapon_specialforces": "Researched",
    "blueprint_weapon_211": "Researched", "blueprint_weapon_213": "Researched",
    "blueprint_weapon_223": "Researched", "blueprint_weapon_237": "Researched",
    "blueprint_weapon_245": "Researched", "blueprint_weapon_294": "Researched",
    "blueprint_weapon_333": "Researched", "blueprint_weapon_340": "Researched",
    "blueprint_weapon_341": "Researched", "blueprint_weapon_356": "Researched",
    "blueprint_weapon_370": "Researched", "blueprint_weapon_fish_rod": "Researched",
    "blueprint_weapon_fish_rod_a": "Researched",
    "weapon_evolution_unlock_blueprint": "Got",
    "garden_skin_1": "Got", "multi_room_skin_7": "Got"
  }
}'''

# Каталог id оружия 8.7.0 для weapon-evol: все weapon_NNN, встреченные в
# облачном топ-сейве (itemData.forgeWeapons, WeaponGameStatisticData
# ._weaponUsedTimes и др.) — 353 реальных id, ни один не выдуман.
_WEAPON_IDS_870_JSON = r'''["weapon_000","weapon_001","weapon_002","weapon_003","weapon_004","weapon_005","weapon_006","weapon_007","weapon_008","weapon_009","weapon_010","weapon_011","weapon_012","weapon_013","weapon_014","weapon_015","weapon_016","weapon_017","weapon_018","weapon_019","weapon_020","weapon_021","weapon_022","weapon_023","weapon_024","weapon_025","weapon_026","weapon_027","weapon_028","weapon_029","weapon_030","weapon_031","weapon_032","weapon_033","weapon_034","weapon_035","weapon_036","weapon_037","weapon_038","weapon_039","weapon_040","weapon_041","weapon_042","weapon_043","weapon_044","weapon_045","weapon_046","weapon_047","weapon_048","weapon_049","weapon_050","weapon_051","weapon_052","weapon_053","weapon_054","weapon_055","weapon_056","weapon_057","weapon_058","weapon_059","weapon_060","weapon_061","weapon_062","weapon_063","weapon_064","weapon_065","weapon_066","weapon_067","weapon_068","weapon_069","weapon_070","weapon_071","weapon_072","weapon_073","weapon_074","weapon_075","weapon_076","weapon_077","weapon_078","weapon_079","weapon_080","weapon_081","weapon_082","weapon_083","weapon_084","weapon_085","weapon_086","weapon_087","weapon_088","weapon_089","weapon_090","weapon_091","weapon_092","weapon_093","weapon_094","weapon_095","weapon_096","weapon_097","weapon_098","weapon_099","weapon_100","weapon_101","weapon_102","weapon_103","weapon_104","weapon_105","weapon_106","weapon_107","weapon_108","weapon_109","weapon_110","weapon_111","weapon_112","weapon_113","weapon_114","weapon_115","weapon_116","weapon_117","weapon_118","weapon_119","weapon_120","weapon_121","weapon_122","weapon_123","weapon_124","weapon_125","weapon_126","weapon_127","weapon_128","weapon_129","weapon_130","weapon_131","weapon_132","weapon_133","weapon_134","weapon_135","weapon_136","weapon_137","weapon_138","weapon_139","weapon_140","weapon_141","weapon_142","weapon_143","weapon_144","weapon_145","weapon_146","weapon_147","weapon_148","weapon_149","weapon_150","weapon_151","weapon_152","weapon_153","weapon_154","weapon_155","weapon_156","weapon_157","weapon_158","weapon_159","weapon_160","weapon_161","weapon_162","weapon_163","weapon_164","weapon_165","weapon_166","weapon_167","weapon_168","weapon_169","weapon_170","weapon_171","weapon_172","weapon_173","weapon_174","weapon_175","weapon_176","weapon_177","weapon_178","weapon_179","weapon_180","weapon_181","weapon_182","weapon_183","weapon_184","weapon_185","weapon_186","weapon_187","weapon_188","weapon_189","weapon_190","weapon_191","weapon_192","weapon_193","weapon_194","weapon_195","weapon_196","weapon_197","weapon_198","weapon_199","weapon_200","weapon_201","weapon_202","weapon_203","weapon_204","weapon_205","weapon_206","weapon_207","weapon_208","weapon_209","weapon_210","weapon_211","weapon_212","weapon_213","weapon_214","weapon_215","weapon_216","weapon_217","weapon_218","weapon_219","weapon_220","weapon_221","weapon_222","weapon_223","weapon_224","weapon_230","weapon_231","weapon_233","weapon_234","weapon_235","weapon_236","weapon_237","weapon_238","weapon_239","weapon_240","weapon_241","weapon_242","weapon_243","weapon_244","weapon_245","weapon_246","weapon_247","weapon_248","weapon_249","weapon_250","weapon_251","weapon_252","weapon_253","weapon_254","weapon_255","weapon_256","weapon_257","weapon_258","weapon_259","weapon_260","weapon_261","weapon_264","weapon_265","weapon_266","weapon_267","weapon_268","weapon_270","weapon_273","weapon_274","weapon_275","weapon_276","weapon_277","weapon_278","weapon_279","weapon_280","weapon_281","weapon_282","weapon_283","weapon_284","weapon_285","weapon_286","weapon_287","weapon_288","weapon_289","weapon_290","weapon_291","weapon_292","weapon_293","weapon_294","weapon_295","weapon_296","weapon_297","weapon_298","weapon_299","weapon_300","weapon_301","weapon_302","weapon_303","weapon_304","weapon_305","weapon_306","weapon_307","weapon_308","weapon_309","weapon_310","weapon_311","weapon_312","weapon_313","weapon_314","weapon_315","weapon_316","weapon_317","weapon_318","weapon_319","weapon_320","weapon_321","weapon_322","weapon_323","weapon_324","weapon_325","weapon_326","weapon_327","weapon_328","weapon_329","weapon_330","weapon_331","weapon_332","weapon_333","weapon_334","weapon_335","weapon_336","weapon_337","weapon_338","weapon_339","weapon_340","weapon_341","weapon_342","weapon_343","weapon_344","weapon_345","weapon_346","weapon_347","weapon_348","weapon_349","weapon_350","weapon_351","weapon_352","weapon_353","weapon_354","weapon_355","weapon_356","weapon_357","weapon_358","weapon_359","weapon_900","weapon_901","weapon_902","weapon_903"]'''


def _opt(opts, key, default=None):
    """opts бывает dict (vars(args) из пресетов) и Namespace — поддержать оба."""
    if opts is None:
        return default
    if isinstance(opts, dict):
        return opts.get(key, default)
    return getattr(opts, key, default)


def _id_catalog(opts=None):
    cat = json.loads(_IDS_870_JSON)
    custom = _opt(opts, "ids_file")
    if custom:
        with open(custom, "r", encoding="utf-8") as f:
            extra = json.load(f)
        for k in ("materials", "seeds"):
            for v in extra.get(k, []):
                if v not in cat[k]:
                    cat[k].append(v)
        cat.get("blueprints", {}).update(extra.get("blueprints", {}))
        print("[i] каталог дополнен из %s" % custom)
    return cat


def _empty_account_hint():
    return (" Если аккаунт ПУСТОЙ (секция пуста и в соседней копии) — массово править нечего:\n"
            "    синтезируй предметы из каталога: preset <файл> grant-materials 499 --yes\n"
            "    (аналогично grant-seeds 99, grant-blueprints, grant-all 499)")


# Каталоги 8.7.0 для синтеза РАЗБЛОКИРОВОК на пустом аккаунте. Источник —
# облачный дамп реального played-сейва аккаунта 235288966 (топ-сейв):
# gameData.heroUnlock/heroLevel/petUnlock/skinLock/roomObjectLevel,
# itemData.roomDecorateDic/itemUnlock. Ни одно имя/id не выдумано.
_HEROES_870 = [
    "Airbender", "Alchemist", "Assassin", "Beheaded", "Druid", "Elves",
    "Engineer", "Envoy", "Knight", "Mage", "Miner", "Necromancer", "Ninja",
    "Officer", "Paladin", "Priest", "Ranger", "Robot", "SpecialForces",
    "Taoist", "Transcendent", "TrapMaster", "Vampire", "Viking", "Warlock",
    "Werewolf",
]
_PETS_870 = [
    "Bat", "Blagny", "Bug", "CanPig", "Cat", "Cat1", "Cat2", "Cat3", "Cat4",
    "Cat5", "Cat6", "Corgi", "Dog", "Dog1", "Dog2", "Dog3", "Gugu", "Hide",
    "Hippo", "LiangSir", "Owl", "Panda", "Pet4399", "Pig", "Pig1", "Pug",
    "Rabbit", "Rabbit1", "Robot", "Seal", "Serenade", "Slime", "TapTap",
    "Tengo", "Tortoise", "dragon",
]
# число скинов на героя = len(skinLock[hero]) в топ-сейве (Key 0..n-1);
# Value=1 в топ-сейве всегда означает «открыт», прочие коды — замок/цена
_SKIN_COUNTS_870 = {
    "Airbender": 5, "Alchemist": 14, "Assassin": 18, "Beheaded": 1,
    "Druid": 16, "Elves": 15, "Engineer": 18, "Envoy": 1, "Knight": 20,
    "Mage": 18, "Miner": 4, "Necromancer": 12, "Ninja": 1, "Officer": 12,
    "Paladin": 18, "Priest": 17, "Ranger": 19, "Robot": 16,
    "SpecialForces": 1, "Taoist": 13, "Transcendent": 1, "TrapMaster": 3,
    "Vampire": 15, "Viking": 19, "Warlock": 6, "Werewolf": 19,
}
_ROOM_OBJECTS_870 = ["Book", "CatFood", "Chest", "Planet", "Safe"]
_ROOM_DECOR_870 = {
    "ItemEggMachine": [0], "ItemTrashCan": [0], "ObjectBooks": [0],
    "ObjectPlant": [0], "ObjectSafe": [0], "PetFood": [0],
}
_ITEM_UNLOCK_870 = ["plant_pot3", "plant_pot4", "plant_pot5", "plant_pot7",
                    "Motorcycle"]


@preset("research-all", "", "все известные чертежи -> Researched (itemData.blueprints)")
def p_research(doc, a, opts):
    it = _gdoc(doc, "itemData")
    bp = it.get("blueprints")
    if not isinstance(bp, dict) or not bp:
        raise RuntimeError("blueprints в этом файле пуст — править нечего." +
                           _uid_variant_hint(doc) + _empty_account_hint())
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
        raise RuntimeError("materials пуст — нечего массово править." + _uid_variant_hint(doc) + _empty_account_hint())
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
        raise RuntimeError("seeds пуст — нечего массово править." + _uid_variant_hint(doc) + _empty_account_hint())
    cnt = int(a[0])
    changes = []
    for k, v in seeds.items():
        if v != cnt:
            seeds[k] = cnt
            changes.append(("itemData.seeds[%s]" % k, v, cnt))
    return changes


# --- синтез предметов на пустом аккаунте (каталог реальных id, см. _IDS_870_JSON) ---
@preset("grant-materials", "COUNT", "выдать ВСЕ материалы каталога 8.7.0 (создать недостающие) — для пустого аккаунта")
def p_grant_materials(doc, a, opts):
    it = _gdoc(doc, "itemData")
    mat = it.get("materials")
    if not isinstance(mat, dict):
        mat = {}
        it["materials"] = mat
    cnt = int(a[0])
    changes = []
    for mid in _id_catalog(opts)["materials"]:
        if mat.get(mid) != cnt:
            changes.append(("itemData.materials[%s]" % mid, mat.get(mid, "<создан>"), cnt))
        mat[mid] = cnt
    return changes


@preset("grant-seeds", "COUNT", "выдать ВСЕ семена каталога 8.7.0 — для пустого аккаунта")
def p_grant_seeds(doc, a, opts):
    it = _gdoc(doc, "itemData")
    seeds = it.get("seeds")
    if not isinstance(seeds, dict):
        seeds = {}
        it["seeds"] = seeds
    cnt = int(a[0])
    changes = []
    for sid in _id_catalog(opts)["seeds"]:
        if seeds.get(sid) != cnt:
            changes.append(("itemData.seeds[%s]" % sid, seeds.get(sid, "<создан>"), cnt))
        seeds[sid] = cnt
    return changes


@preset("grant-blueprints", "", "все чертежи каталога 8.7.0 -> Researched (создать недостающие); STATE переопределяет")
def p_grant_blueprints(doc, a, opts):
    it = _gdoc(doc, "itemData")
    bp = it.get("blueprints")
    if not isinstance(bp, dict):
        bp = {}
        it["blueprints"] = bp
    state = a[0] if a else None
    changes = []
    for bid, defstate in _id_catalog(opts)["blueprints"].items():
        want = state or defstate
        if bp.get(bid) != want:
            changes.append(("itemData.blueprints[%s]" % bid, bp.get(bid, "<создан>"), want))
        bp[bid] = want
    return changes


@preset("grant-all", "COUNT", "grant-materials + grant-seeds + grant-blueprints одним заходом — для пустого аккаунта")
def p_grant_all(doc, a, opts):
    changes = p_grant_materials(doc, a, opts) + p_grant_seeds(doc, a, opts)
    it = _gdoc(doc, "itemData")
    bp = it.get("blueprints")
    if not isinstance(bp, dict):
        bp = {}
        it["blueprints"] = bp
    for bid, defstate in _id_catalog(opts)["blueprints"].items():
        if bp.get(bid) != defstate:
            changes.append(("itemData.blueprints[%s]" % bid, bp.get(bid, "<создан>"), defstate))
        bp[bid] = defstate
    return changes


# --- синтез разблокировок 1.2.0 (каталоги из топ-сейва, см. выше) ---
def _known_heroes(gd):
    """Каталог героев + все герои, уже присутствующие в сейве."""
    out = set(_HEROES_870)
    for fld in ("heroUnlock", "heroLevel", "heroSkillUnlock", "skinLock"):
        v = gd.get(fld)
        if isinstance(v, dict):
            out |= set(v.keys())
    return out


@preset("grant-heroes-levels", "N", "уровни ВСЕХ героев каталога -> N (создаёт heroLevel с нуля; 1..8)")
def p_grant_heroes_levels(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    hl = gd.get("heroLevel")
    if not isinstance(hl, dict):
        hl = {}
        gd["heroLevel"] = hl
    n = max(1, min(8, int(a[0])))
    changes = []
    for h in sorted(_known_heroes(gd)):
        if hl.get(h) != n:
            changes.append(("gameData.heroLevel[%s]" % h, hl.get(h, "<создан>"), n))
        hl[h] = n
    print("[i] помни: UI героя читает уровень и из playerprefs (ключи c{0}_level) —")
    print("    python sk_prefs.py unlock-levels %d <profile>/shared_prefs/com.ChillyRoom.DungeonShooter.v2.playerprefs.xml --extend --yes" % n)
    return changes


@preset("grant-skill-unlock", "", "открыть все 3 скилла всех героев (heroSkillUnlock[hero] = [{Key:0..2, Value:true}])")
def p_grant_skill_unlock(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    su = gd.get("heroSkillUnlock")
    if not isinstance(su, dict):
        su = {}
        gd["heroSkillUnlock"] = su
    want = [{"Key": i, "Value": True} for i in range(3)]
    changes = []
    for h in sorted(_known_heroes(gd)):
        cur = su.get(h)
        cur_norm = ([(e.get("Key"), e.get("Value")) for e in cur if isinstance(e, dict)]
                    if isinstance(cur, list) else None)
        if cur_norm != [(0, True), (1, True), (2, True)]:
            changes.append(("gameData.heroSkillUnlock[%s]" % h,
                            "<создан>" if cur is None else cur, want))
        su[h] = json.loads(json.dumps(want))
    return changes


@preset("grant-skins", "", "открыть все скины всех героев: skinLock[hero] = [{Key:i, Value:1}] по каталогу топ-сейва; существующие Value != 1 -> 1")
def p_grant_skins(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    sl = gd.get("skinLock")
    if not isinstance(sl, dict):
        sl = {}
        gd["skinLock"] = sl
    changes = []
    for h in sorted(set(_SKIN_COUNTS_870) | set(sl.keys())):
        n = _SKIN_COUNTS_870.get(h)
        cur = sl.get(h)
        if isinstance(cur, list) and cur:
            for e in cur:
                if isinstance(e, dict) and e.get("Value") != 1:
                    changes.append(("gameData.skinLock[%s][Key %s].Value" % (h, e.get("Key")),
                                    e.get("Value"), 1))
                    e["Value"] = 1
            if n is not None:
                have = {e.get("Key") for e in cur if isinstance(e, dict)}
                for i in range(n):
                    if i not in have:
                        cur.append({"Key": i, "Value": 1})
                        changes.append(("gameData.skinLock[%s][%d]" % (h, i), "<создан>", 1))
            continue
        if n is None:
            continue
        want = [{"Key": i, "Value": 1} for i in range(n)]
        changes.append(("gameData.skinLock[%s]" % h, "<создан>" if cur is None else cur,
                        "%d скинов Value=1" % n))
        sl[h] = want
    return changes


@preset("grant-pets", "", "открыть всех питомцев каталога (36, создаёт petUnlock с нуля)")
def p_grant_pets(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    pu = gd.get("petUnlock")
    if not isinstance(pu, dict):
        pu = {}
        gd["petUnlock"] = pu
    changes = []
    for pet in _PETS_870:
        if pu.get(pet) is not True:
            changes.append(("gameData.petUnlock[%s]" % pet, pu.get(pet, "<создан>"), True))
        pu[pet] = True
    return changes


@preset("grant-room-objects", "N", "уровни объектов зала (Book/CatFood/Chest/Planet/Safe) -> N (1..5)")
def p_grant_room_objects(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    ro = gd.get("roomObjectLevel")
    if not isinstance(ro, dict):
        ro = {}
        gd["roomObjectLevel"] = ro
    n = max(1, min(5, int(a[0])))
    changes = []
    for o in _ROOM_OBJECTS_870:
        if ro.get(o) != n:
            changes.append(("gameData.roomObjectLevel[%s]" % o, ro.get(o, "<создан>"), n))
        ro[o] = n
    return changes


@preset("room-unlock", "", "gameData.hasTutorial=1 — зал считает обучение пройденным (эксперимент: гейт мастерской/стендов)", experimental=True)
def p_room_unlock(doc, a, opts):
    gd = _gdoc(doc, "gameData")
    old = gd.get("hasTutorial")
    gd["hasTutorial"] = 1
    return [("gameData.hasTutorial", old, 1)]


# --- item_data: рыба/декор/itemUnlock (каталоги из топ-сейва) ---
@preset("fish-total", "N", "itemData.cumulativeFishCount = max(old, N) — счётчик ВСЕЙ купленной сушёной рыбы (двигает подарочную дорожку)")
def p_fish_total(doc, a, opts):
    it = _gdoc(doc, "itemData")
    n = int(a[0])
    old = it.get("cumulativeFishCount") or 0
    new = max(old, n)
    it["cumulativeFishCount"] = new
    return [("itemData.cumulativeFishCount", old, new)]


@preset("grant-decor", "", "украшения зала: itemData.roomDecorateDic = {ObjectBooks/ObjectSafe/ObjectPlant/ItemEggMachine/ItemTrashCan/PetFood: [0]}")
def p_grant_decor(doc, a, opts):
    it = _gdoc(doc, "itemData")
    rd = it.get("roomDecorateDic")
    if not isinstance(rd, dict):
        rd = {}
        it["roomDecorateDic"] = rd
    changes = []
    for did, skins in _ROOM_DECOR_870.items():
        if rd.get(did) != skins:
            changes.append(("itemData.roomDecorateDic[%s]" % did, rd.get(did, "<создан>"), skins))
        rd[did] = list(skins)
    return changes


@preset("item-unlock", "", "itemData.itemUnlock += горшки plant_pot3/4/5/7 + Motorcycle (id из топ-сейва)")
def p_item_unlock(doc, a, opts):
    it = _gdoc(doc, "itemData")
    iu = it.get("itemUnlock")
    if not isinstance(iu, list):
        iu = []
        it["itemUnlock"] = iu
    changes = []
    for iid in _ITEM_UNLOCK_870:
        if iid not in iu:
            iu.append(iid)
            changes.append(("itemData.itemUnlock += %s" % iid, "<нет>", iid))
    return changes


# --- прочие секции ---
@preset("rij-legacy", "", "settingData.UseRijData=false: секции Rij-группы (item_data, monsrise, misc, pvp, task, bp, mall_reload) читать/писать легаси-.data, а не .rijndael — без этого правки item_data игнорируются, если канал включён")
def p_rij_legacy(doc, a, opts):
    if doc.cls != "SettingData":
        raise RuntimeError("rij-legacy — пресет для setting.data (класс SettingData), а тут %s" % (doc.cls or "?"))
    old = doc.obj.get("UseRijData")
    doc.obj["UseRijData"] = False
    if old:
        print("[!] UseRijData был ВКЛЮЧЁН (%r) — вот почему правки item_data могли "
              "игнорироваться: игра читала/писала Rij-путь (.rijndael/.data.new), а не "
              "наши .data. Теперь Rij-группа читается из обычных .data." % old)
        print("[!] выключи канал и в prefs: python sk_prefs.py rij-off <prefs.xml> --yes")
        print("[!] monsrise/misc/task/bp/mall сбросятся в дефолт (на пустом аккаунте там нечего терять)")
    return [("settingData.UseRijData", old, False)]


@preset("dungeon-times", "N", "statistic: счётчики режима уровня -> N (ФОРМАТ ТОП-СЕЙВА: ключи-режимы Any/Normal/Season, а не герои!) — гейты «сыграйте в режиме уровня N раз»", experimental=True)
def p_dungeon_times(doc, a, opts):
    if doc.cls != "StatisticData":
        raise RuntimeError("dungeon-times — пресет для statistic.data (класс StatisticData), а тут %s" % (doc.cls or "?"))
    st = doc.obj.get("StatisticsData")
    if not isinstance(st, dict):
        st = {}
        doc.obj["StatisticsData"] = st
    lg = st.get("LevelGameStatisticData")
    if not isinstance(lg, dict):
        lg = {}
        st["LevelGameStatisticData"] = lg
    n = max(1, int(a[0]))
    changes = []

    def bump(fld, keys, val):
        d = lg.get(fld)
        if not isinstance(d, dict):
            d = {}
            lg[fld] = d
        for k in keys:
            cur = d.get(k) or 0
            want = val if not isinstance(cur, int) else max(cur, val)
            if cur != want:
                changes.append(("StatisticsData.LevelGameStatisticData.%s[%s]" % (fld, k),
                                cur, want))
                d[k] = want

    # Формат ключей — из реального топ-сейва (LevelGameStatisticData):
    # _enterDungeonTimes {"ARAM":14,"Any":123,"Any#MultiGame":2,"BossRush":2,
    #                     "MultiGame#Normal":2,"Normal":107,"Season":11},
    # _finishDungeonTimes {"Any":3,"Normal":3},
    # _finishLevelTimes {"Any#0":81,"Any#1":56,"ARAM#0":6,...,"Normal#0":..},
    # _highestLevelArrived {"Any":15,"BossRush":6,"Normal":15}.
    # Гейты «сыграйте в режиме уровня 3 раза» читают именно эти словари.
    bump("_enterDungeonTimes", ["Any", "Normal", "Season", "ARAM", "BossRush"], n)
    bump("_finishDungeonTimes", ["Any", "Normal", "Season"], n)
    bump("_finishLevelTimes",
         ["Any#0", "Any#1", "Any#2", "Normal#0", "Normal#1", "Normal#2",
          "Season#0", "Season#1", "Season#2"], n)
    bump("_highestLevelArrived", ["Any", "Normal", "BossRush"], 15)
    # 1.4.0: гейт может читать и простой счётчик событий (топ-сейв:
    # event2Count.enter_normal_mode_count = 111), а не только LevelGameStatisticData
    ev = doc.obj.get("event2Count")
    if not isinstance(ev, dict):
        ev = {}
        doc.obj["event2Count"] = ev
    for k in ("enter_normal_mode_count", "new_player_enter_normal_mode_count"):
        cur = ev.get(k) or 0
        if not isinstance(cur, int) or cur < n:
            changes.append(("statistic.event2Count[%s]" % k, cur, n))
            ev[k] = n
    return changes


@preset("pets-intimacy", "[N]", "statistic: привязанность ВСЕХ питомцев -> N (event2Count pet_i_intimacy/full_intimacy/action_unlock — форма из топ-сейва и метадаты pet_{0}_intimacy; ПОЛНАЯ привязанность = 240 — значение топ-сейва, дефолт с 1.4.0)", experimental=True)
def p_pets_intimacy(doc, a, opts):
    if doc.cls != "StatisticData":
        raise RuntimeError("pets-intimacy — пресет для statistic.data (класс StatisticData), а тут %s" % (doc.cls or "?"))
    n = int(a[0]) if a else 240
    ev = doc.obj.get("event2Count")
    if not isinstance(ev, dict):
        ev = {}
        doc.obj["event2Count"] = ev
    changes = []
    n_pets = 36  # 0..35 — диапазон, виденный в топ-сейве (pet_0..pet_35)
    for i in range(n_pets):
        for suf, val in (("intimacy", n), ("full_intimacy", 1), ("action_unlock", 1)):
            key = "pet_%d_%s" % (i, suf)
            if ev.get(key) != val:
                changes.append(("statistic.event2Count[%s]" % key, ev.get(key, "<создан>"), val))
                ev[key] = val
    if (ev.get("pet_full_intimacy") or 0) < n_pets:
        changes.append(("statistic.event2Count[pet_full_intimacy]",
                        ev.get("pet_full_intimacy", "<создан>"), n_pets))
        ev["pet_full_intimacy"] = n_pets
    return changes


@preset("weapon-evol", "[SKINS] [LEVEL]",
        "облики оружия: WeaponEvolutionData.weapons -> локальная форма RGScript.WeaponEvolution.WUWeapon "
        "{Name, Level, CurrentSkinIndex, UnlockedSkins} (1.4.0: ранее писали облачную V1-форму с "
        "CurrentSkinId/ModuleSlots/UpdateMaterialCount — игра эти поля ИГНОРИРУЕТ, потому скины и не открылись). "
        "SKINS — сколько скинов открыть (дефолт 10), LEVEL — уровень эволюции оружия (необязателен). "
        "Фрагменты эволюции — отдельным заходом: preset item_data*.data grant-materials 499", experimental=True)
def p_weapon_evol(doc, a, opts):
    if not (doc.cls or "").endswith("WeaponEvolutionData"):
        raise RuntimeError("weapon-evol — пресет для weapon_evolution_data*.data (класс WeaponEvolutionData), а тут %s" % (doc.cls or "?"))
    # обратная совместимость: старый вызов "weapon-evol 999 10" нёс N=999 (фрагменты,
    # теперь через grant-materials) и SKINS=10 — игнорируем чисто числовой первый
    # аргумент, если их больше одного
    if len(a) >= 2:
        skins = int(a[1])
        level = int(a[2]) if len(a) > 2 and str(a[2]).lstrip("-").isdigit() else None
    else:
        skins = int(a[0]) if a else 10
        level = None
    wp = doc.obj.get("weapons")
    changes = []
    catalog = json.loads(_WEAPON_IDS_870_JSON)

    def want_entry(wid):
        # ЛОКАЛЬНЫЙ класс RGScript.WeaponEvolution.WUWeapon (метадата 8.7.0):
        # Name, Level, CurrentSkinIndex, UnlockedSkins — ровно 4 поля.
        return {"Name": wid, "Level": int(level or 0), "CurrentSkinIndex": 0,
                "UnlockedSkins": list(range(max(1, skins)))}

    def fix_entry(cur, wid):
        """Привести существующую запись (возможно, в старой облачной форме) к WUWeapon."""
        touched = []
        if cur.get("Name") != wid:
            cur["Name"] = wid
            touched.append(("Name", cur.get("Name"), wid))
        # мусорные облачные поля молча убираем — игра их всё равно игнорирует
        for ghost in ("CurrentSkinId", "ModuleSlots", "UpdateMaterialCount"):
            if ghost in cur:
                touched.append((ghost, cur.pop(ghost), "<удалено>"))
        if level is not None and (cur.get("Level") or 0) < level:
            touched.append(("Level", cur.get("Level"), level))
            cur["Level"] = int(level)
        us = cur.get("UnlockedSkins")
        want = list(range(max(1, skins)))
        if not isinstance(us, list):
            touched.append(("UnlockedSkins", us, want))
            cur["UnlockedSkins"] = want
        else:
            have = {x for x in us if isinstance(x, int)}
            add = [i for i in want if i not in have]
            if add:
                us.extend(add)
                touched.append(("UnlockedSkins", len(us) - len(add), len(us)))
        if "CurrentSkinIndex" not in cur:
            cur["CurrentSkinIndex"] = 0
            touched.append(("CurrentSkinIndex", "<создан>", 0))
        return touched

    if isinstance(wp, dict):
        # словарь {weapon_id: {...}} — обновляем существующие, досоздаём из каталога
        for wid in sorted(set(list(wp.keys()) + catalog)):
            cur = wp.get(wid)
            if not isinstance(cur, dict):
                changes.append(("weapons[%s]" % wid, "<создан>",
                                "WUWeapon: UnlockedSkins<=%d%s" % (skins, ", Level=%d" % level if level else "")))
                wp[wid] = want_entry(wid)
                continue
            for fld, old, new in fix_entry(cur, wid):
                changes.append(("weapons[%s].%s" % (wid, fld), old, new))
    else:
        if not isinstance(wp, list):
            wp = []
            doc.obj["weapons"] = wp
        by_name = {e.get("Name"): e for e in wp if isinstance(e, dict) and e.get("Name")}
        for wid in catalog:
            cur = by_name.get(wid)
            if cur is None:
                entry = want_entry(wid)
                wp.append(entry)
                changes.append(("weapons += %s" % wid, "<создан>",
                                "WUWeapon: UnlockedSkins<=%d%s" % (skins, ", Level=%d" % level if level else "")))
                continue
            for fld, old, new in fix_entry(cur, wid):
                changes.append(("weapons[%s].%s" % (wid, fld), old, new))
    return changes


@preset("troop2-open", "", "сезон «защитники» (Troop2): 5 скиллов=true, сложность/размер отряда вверх; id наемников — только из --ids-file или уже купленных (не выдумываем)", experimental=True)
def p_troop2_open(doc, a, opts):
    if doc.cls != "SeasonData":
        raise RuntimeError("troop2-open — пресет для season_data*.data (класс SeasonData), а тут %s" % (doc.cls or "?"))
    changes = []
    for f in ("hasUnlockTroop2SkillHaste", "hasUnlockTroop2SkillFrostBurst",
              "hasUnlockTroop2SkillShield", "hasUnlockTroop2SkillPowerUp",
              "hasUnlockTroop2SkillBonus"):
        if doc.obj.get(f) is not True:
            changes.append(("seasonData.%s" % f, doc.obj.get(f, "<создан>"), True))
            doc.obj[f] = True
    for f, v in (("troop2UnlockedDifficulty", 3), ("troop2ExtraTeamSize", 4),
                 ("troop2MaxExtraTeamSize", 4), ("troop2MaxExtraHeroSeatCount", 4)):
        cur = doc.obj.get(f) or 0
        if cur < v:
            changes.append(("seasonData.%s" % f, cur, v))
            doc.obj[f] = v
    bm = doc.obj.get("troop2BoughtMercenary")
    if not isinstance(bm, dict):
        bm = {}
        doc.obj["troop2BoughtMercenary"] = bm
    ids = _opt(opts, "troop2_ids") or []
    if not ids:
        # --ids-file: {"troop2_mercenaries": ["0","1",...]} — id только из реальных данных
        custom = _opt(opts, "ids_file")
        if custom:
            with open(custom, "r", encoding="utf-8") as f:
                extra = json.load(f)
            ids = extra.get("troop2_mercenaries") or []
            if ids:
                print("[i] troop2 наемники из %s: %s" % (custom, ids))
    if ids:
        for mid in ids:
            if bm.get(str(mid)) in (None, False, 0):
                changes.append(("seasonData.troop2BoughtMercenary[%s]" % mid,
                                bm.get(str(mid), "<создан>"), 1))
                bm[str(mid)] = 1
    else:
        for k, v in list(bm.items()):
            if v in (False, 0, None):
                changes.append(("seasonData.troop2BoughtMercenary[%s]" % k, v, 1))
                bm[k] = 1
    return changes


# Задачи сезона (Troop2 «защитники»/ARAM2/Artifacts2). Класс записи —
# CommonSeasonTaskData/ChampionTaskSaveData {Id, IsComplete, HasGetReward,
# CurrentScore, GetTaskTimeStamp} (метадата 8.7.0). ФАКТ 1.4.0: на живом
# устройстве реальные списки задач лежали в base season_data.data, а
# season_data_<acc>_.data был с ПУСТЫМИ списками — поэтому пустые списки
# дозаполняем из копии-сиблинга, и все IsComplete -> true.
_SEASON_MODES = ("troop2", "aram2", "artifacts2")
_SEASON_TASK_FIELDS = ("troop2ChampionNormalTasks", "troop2ChampionWeeklyTasks",
                       "aram2ChampionNormalTasks", "aram2ChampionWeeklyTasks",
                       "artifacts2ChampionNormalTasks", "artifacts2ChampionWeeklyTasks")


def _sibling_season_tasks(doc):
    """Самый наполненный sibling этой же секции: {field: [tasks]}."""
    best = {}
    for sp in _section_siblings(doc.path):
        try:
            sib = load_doc(sp, None)
        except Exception:
            continue
        if (sib.cls or "") != "SeasonData":
            continue
        for fld in _SEASON_TASK_FIELDS:
            lst = sib.obj.get(fld)
            if isinstance(lst, list) and lst and len(lst) > len(best.get(fld, [])):
                best[fld] = lst
    return best


@preset("season-open", "[TROPHY]",
        "сезон (защитники Troop2/ARAM2/Artifacts2): все задачи *ChampionNormalTasks/"
        "*ChampionWeeklyTasks -> IsComplete=true (+CurrentScore), TaskExp/TrophyLevel вверх "
        "(TROPHY, дефолт 60; ClaimedTrophyLevel не трогаем — награды останутся к получению). "
        "Пустые списки задач дозаполняются из копии-сиблинга (факт: реальные списки живут в base season_data.data)",
        experimental=True)
def p_season_open(doc, a, opts):
    if doc.cls != "SeasonData":
        raise RuntimeError("season-open — пресет для season_data*.data (класс SeasonData), а тут %s" % (doc.cls or "?"))
    trophy = int(a[0]) if a else 60
    changes = []
    siblings = _sibling_season_tasks(doc)
    for fld in _SEASON_TASK_FIELDS:
        lst = doc.obj.get(fld)
        if not isinstance(lst, list):
            lst = []
            doc.obj[fld] = lst
        if not lst and siblings.get(fld):
            lst.extend(json.loads(json.dumps(siblings[fld])))
            changes.append(("%s: дозаполнено из копии-сиблинга" % fld,
                            "<пусто>", "%d задач" % len(lst)))
        done = 0
        for t in lst:
            if not isinstance(t, dict):
                continue
            key = "IsComplete" if "IsComplete" in t else ("isComplete" if "isComplete" in t else "IsComplete")
            if t.get(key) is not True:
                t[key] = True
                done += 1
            cs_key = "CurrentScore" if "CurrentScore" in t else "currentScore"
            if cs_key in t and isinstance(t.get(cs_key), int) and t[cs_key] < 99999:
                t[cs_key] = 99999
        if done:
            changes.append(("%s: IsComplete=true" % fld, "%d не завершено" % done,
                            "все завершены (%d задач)" % len(lst)))
    for mode in _SEASON_MODES:
        for fld in ("%sTaskExp" % mode, "%sTrophyLevel" % mode):
            cur = doc.obj.get(fld) or 0
            want = 99999 if fld.endswith("TaskExp") else trophy
            if cur < want:
                changes.append(("seasonData.%s" % fld, cur, want))
                doc.obj[fld] = want
    return changes


# Карьерные задачи (task.data, NewTaskData): {ID, Status("End"/"Progress"),
# TaskType("Career"...), CurrentProgress, TargetProgress, ProgressCacheInfos...}
# — форма из топ-сейва. Гейты вида «Сыграйте в режиме уровня N раз» могут жить
# здесь (прогресс пересчитывается TaskSystem-ProgressChecker из statistic).
@preset("tasks-done", "",
        "task.data: все NewTaskData -> Status='End', CurrentProgress=TargetProgress "
        "(карьерные задачи, в т.ч. гейты «сыграйте в режиме уровня»); HasGotReward не трогаем",
        experimental=True)
def p_tasks_done(doc, a, opts):
    if (doc.cls or "") != "RGScript.Data.TaskData":
        raise RuntimeError("tasks-done — пресет для task*.data (класс RGScript.Data.TaskData), а тут %s" % (doc.cls or "?"))
    ntd = doc.obj.get("NewTaskData")
    if not isinstance(ntd, list):
        raise RuntimeError("в этом task.data нет NewTaskData (пустая секция?)")
    changes = []
    done = 0
    for t in ntd:
        if not isinstance(t, dict) or "ID" not in t:
            continue
        touched = False
        if t.get("Status") != "End":
            t["Status"] = "End"
            touched = True
        tp = t.get("TargetProgress")
        if isinstance(tp, int) and t.get("CurrentProgress", 0) < tp:
            t["CurrentProgress"] = tp
            touched = True
        if touched:
            done += 1
    if done:
        changes.append(("NewTaskData: Status=End, CurrentProgress=Target",
                        "%d в прогрессе" % done, "все завершены (%d задач)" % len(ntd)))
    # tasks[] (легаси-испытания): count >= total, isCompleted=true
    tasks = doc.obj.get("tasks")
    if isinstance(tasks, list):
        done2 = 0
        for t in tasks:
            if not isinstance(t, dict):
                continue
            tot = t.get("total")
            if isinstance(tot, int) and t.get("count", 0) < tot:
                t["count"] = tot
                t["isCompleted"] = True
                done2 += 1
            elif t.get("isCompleted") is not True:
                t["isCompleted"] = True
                done2 += 1
        if done2:
            changes.append(("tasks[]: count=total, isCompleted=true",
                            "%d не завершено" % done2, "все завершены"))
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
    # [OPT] — необязательный аргумент, ОБЯЗАТЕЛЬНЫЕ — без скобок
    need = [x for x in spec["args"].split() if x and not x.startswith("[")]
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
        # старые adb часть ошибок пишут в stdout, а иногда молчат вовсе —
        # показываем всё, что есть, плюс код возврата (диагностика без пустых [!])
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        out = (p.stdout or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("adb %s (rc=%d): %s" % (" ".join(args[:3]), p.returncode,
                                                   err or out or "<adb молчит — см. rc>"))
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


def device_file_size(adb_path, root, path, timeout=60):
    """Размер файла на устройстве в байтах (wc -c через su)."""
    out = device_sh(adb_path, root, "wc -c < %s" % _q(path), timeout=timeout).decode("utf-8", "replace").strip()
    if not out.isdigit():
        raise RuntimeError("не удалось узнать размер %s на устройстве (%r)" % (path, out[:80]))
    return int(out)


def device_write_file(adb_path, root, local_path, remote_path, timeout=900):
    """Бинарно-безопасная ЗАПИСЬ файла на устройство (зеркало device_read_file).
    1) adb push (sync-протокол) + контроль размера на устройстве;
    2) fallback: base64 чанками через exec-out su -c "printf '...' >> f.b64" —
       тот же канал, на котором pull доказанно работает на старых adb LDPlayer
       (бинарные потоки они портят, текст — нет); чанки < 4KB payload древнего
       adbd; алфавит base64 без кавычек и % — printf безопасен.
    Возвращает способ доставки: 'push' | 'base64'."""
    with open(local_path, "rb") as f:
        data = f.read()
    try:
        adb_run(adb_path, ["push", local_path, remote_path], timeout=timeout)
        got = device_file_size(adb_path, root, remote_path, timeout=timeout)
        if got == len(data):
            return "push"
        print("[!] adb push доложил %d/%d байт — доставляю base64-чанками" % (got, len(data)))
    except Exception as e:
        print("[i] adb push не сработал (%s)" % e)
        print("[i] -> fallback: base64-чанками через exec-out (медленнее, но надёжно)")
    b64 = base64.b64encode(data).decode("ascii")
    tmp_b64 = remote_path + ".b64"
    device_sh(adb_path, root, "rm -f %s %s" % (_q(remote_path), _q(tmp_b64)), timeout=timeout)
    chunk, per_call = 1200, 3   # ~3.6KB на вызов — влезает в 4KB payload старого adbd
    span = chunk * per_call
    n_calls = (len(b64) + span - 1) // span
    for done, i in enumerate(range(0, len(b64), span), 1):
        cmds = ["printf %s >> %s" % (_q(b64[j:j + chunk]), _q(tmp_b64))
                for j in range(i, min(i + span, len(b64)), chunk)]
        device_sh(adb_path, root, "; ".join(cmds), timeout=timeout)
        if n_calls < 10 or done % max(1, n_calls // 10) == 0 or done == n_calls:
            print("[i] base64-заливка: %d%% (%d/%d)" % (done * 100 // n_calls, done, n_calls))
    device_sh(adb_path, root, "base64 -d %s > %s && rm -f %s"
              % (_q(tmp_b64), _q(remote_path), _q(tmp_b64)), timeout=timeout)
    got = device_file_size(adb_path, root, remote_path, timeout=timeout)
    if got != len(data):
        raise RuntimeError("заливка не сошлась: на устройстве %d байт, локально %d" % (got, len(data)))
    return "base64"


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


_BAK_RE = re.compile(r"\.bak(_\d{8}_\d{6})?$", re.I)


def _push_tar_filter(ti):
    """tarfile-фильтр push: на устройство не едут локальные артефакты —
    бэкапы .bak_<ts>, временные .data.new (могут откатить правки при первом
    запуске игры) и мусор Windows (desktop.ini/Thumbs.db)."""
    if ti.name.endswith(".data.new"):
        return None
    base = ti.name.rsplit("/", 1)[-1]
    if base.lower() in ("desktop.ini", "thumbs.db") or _BAK_RE.search(base):
        return None
    return ti


def _app_uid(adb_path, root):
    """uid приложения (владелец /data/data/<pkg>). Берём ДО распаковки tar:
    сам пакетный каталог tar не трогает, а вот files/ и shared_prefs/ после
    распаковки могут быть уже перезаписаны чужим владельцем."""
    out = device_sh(adb_path, root, "stat -c %%u /data/data/%s" % PKG).decode().strip()
    if not out.isdigit() or int(out) == 0:
        raise RuntimeError("не удалось определить uid приложения (stat вернул %r); "
                           "нужен рабочий root (adb shell su -c id -> uid=0)" % out[:40])
    return int(out)


def _push_tar_ownership(app_uid):
    """Фильтр tarfile: вшивает владельца и права Android в ЧЛЕНЫ tar.
    Без этого tar, собранный на Windows, несёт uid=0 и права 0666/0777;
    root-tar на устройстве честно их восстанавливает — и файлы сейва
    становились root:root (в логе это выглядело как «владелец uid 0»),
    а запуск игры мог зависать/падать на записи сейва."""
    def filt(ti):
        ti2 = _push_tar_filter(ti)
        if ti2 is None:
            return None
        ti2.uid = ti2.gid = app_uid
        ti2.uname = ti2.gname = ""
        ti2.mode = 0o771 if ti2.isdir() else 0o660
        return ti2
    return filt


def _device_md5(adb_path, root, paths, batch=8):
    """md5 списка файлов НА УСТРОЙСТВЕ (чанками — su -c не любит длинные строки)."""
    out = {}
    for i in range(0, len(paths), batch):
        cmd = "md5sum %s" % " ".join(_q(p) for p in paths[i:i + batch])
        txt = device_sh(adb_path, root, cmd, timeout=300).decode("utf-8", "replace")
        for line in txt.splitlines():
            m = re.match(r"^([0-9a-fA-F]{32})\s+(.+?)\s*$", line.strip())
            if m:
                out[m.group(2)] = m.group(1).lower()
    return out


def _fix_ownership(adb_path, root, app_uid):
    """chown/chmod/restorecon поверх распакованного профиля (страховка к вшитым в tar правам)."""
    device_sh(adb_path, root, "chown -R %d:%d /data/data/%s/files /data/data/%s/shared_prefs"
              % (app_uid, app_uid, PKG, PKG))
    for sub in ("files", "shared_prefs"):
        device_sh(adb_path, root,
                  "chmod 771 /data/data/%s/%s; find /data/data/%s/%s -maxdepth 1 -type f "
                  "-exec chmod 660 {} +" % (PKG, sub, PKG, sub))
    try:
        r = device_sh(adb_path, root,
                      "restorecon -R /data/data/%s/files /data/data/%s/shared_prefs 2>&1" % (PKG, PKG)
                      ).decode("utf-8", "replace").strip()
        if r and "not found" not in r.lower() and "No such tool" not in r:
            print("[i] restorecon: %s" % r[:120])
    except Exception:
        pass


def cmd_push(args):
    adb_path = args.adb or shutil.which("adb")
    if not adb_path:
        raise RuntimeError("adb не найден. Укажи путь: --adb \"D:\\LDPlayer\\LDPlayer14\\adb.exe\" "
                           "(или добавь adb в PATH)")
    src = os.path.abspath(args.dir)
    if not os.path.isdir(src):
        raise RuntimeError("нет каталога: %s" % src)
    root = device_has_root(adb_path)
    if not root:
        raise RuntimeError("нет root: залить в /data/data/%s не выйдет. LDPlayer: настройки "
                           "эмулятора -> Root-права = ON (проверка: adb shell su -c id -> uid=0); "
                           "если adb не в PATH — укажи --adb" % PKG)
    if args.kill:
        adb_run(adb_path, ["shell", "am force-stop %s" % PKG])
        print("[i] игра закрыта (иначе кэш секций перезатрёт правки)")
    app_uid = _app_uid(adb_path, root)
    print("[i] uid приложения: %d (его вшьём в tar как владельца файлов)" % app_uid)
    ts = time.strftime("%Y%m%d_%H%M%S")
    # ВАЖНО: только прямые слэши. os.path.join на Windows даёт '\', а на Android
    # '\' — часть ИМЕНИ файла: старые adb на таком назначении падают молча, а tar
    # потом не находит файл. Ровно это и был баг push (пустая ошибка adb push).
    tmp_dir = "/data/local/tmp"
    pre_backup = "%s/sk_pre_backup_%s.tar" % (tmp_dir, ts)
    push_tar = "%s/sk_push_%s.tar" % (tmp_dir, ts)
    device_sh(adb_path, root, "tar -cf %s -C /data/data/%s files shared_prefs" % (pre_backup, PKG))
    print("[i] страховка на устройстве: %s" % pre_backup)
    buf = io.BytesIO()
    counts = {}
    pushed = []  # члены tar (файлы), реально ушедшие на устройство — для md5-сверки
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for sub in ("files", "shared_prefs"):
            subdir = os.path.join(src, sub)
            if not os.path.isdir(subdir):
                counts[sub] = 0
                continue
            before = len(tf.getmembers())
            tf.add(subdir, arcname=sub, filter=_push_tar_ownership(app_uid))
            pushed += [m.name for m in tf.getmembers()[before:] if m.isfile()]
        counts["files"] = sum(1 for n in pushed if n.startswith("files/"))
        counts["shared_prefs"] = sum(1 for n in pushed if n.startswith("shared_prefs/"))
    if counts.get("files", 0) + counts.get("shared_prefs", 0) == 0:
        raise RuntimeError("в %s нет files/ и shared_prefs/ — нечего заливать" % src)
    local_tar = os.path.join(src, "_sk_push.tar")
    with open(local_tar, "wb") as f:
        f.write(buf.getvalue())
    print("[i] tar: files/=%d, shared_prefs/=%d, %d байт (бэкапы .bak_*, .data.new и мусор Windows в tar не попадают)"
          % (counts.get("files", 0), counts.get("shared_prefs", 0), len(buf.getvalue())))
    how = device_write_file(adb_path, root, local_tar, push_tar, timeout=900)
    print("[i] tar доставлен на устройство (%s): %s" % (how, push_tar))
    device_sh(adb_path, root, "tar -xf %s -C /data/data/%s" % (push_tar, PKG), timeout=600)
    # ПОЛНАЯ сверка: md5 КАЖДОГО залитого файла на устройстве == локальному
    # (старая сверка двух размеров ничего не гарантировала для остальных 63)
    dev_md5 = _device_md5(adb_path, root, ["/data/data/%s/%s" % (PKG, n) for n in pushed])
    mism = []
    for name in pushed:
        with open(os.path.join(src, name.replace("/", os.sep)), "rb") as f:
            local = hashlib.md5(f.read()).hexdigest()
        got = dev_md5.get("/data/data/%s/%s" % (PKG, name))
        if got != local:
            mism.append((name, got))
    if mism:
        for name, got in mism[:10]:
            print("[!] MD5 НЕ СХОДИТСЯ: %s (на устройстве %s)" % (name, got or "<нет>"))
        raise RuntimeError("push не сошёлся для %d/%d файлов — повтори push, при повторении "
                           "запусти doctor и пришли вывод" % (len(mism), len(pushed)))
    print("[i] сверка md5: %d/%d файлов совпали байт-в-байт" % (len(pushed), len(pushed)))
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
    _fix_ownership(adb_path, root, app_uid)
    print("[i] владелец/права: uid %d, каталоги 771, файлы 660 (проверка: doctor)" % app_uid)
    if args.no_keep:
        device_sh(adb_path, root, "rm -f %s %s" % (push_tar, pre_backup))
    else:
        device_sh(adb_path, root, "rm -f %s" % push_tar)
    os.remove(local_tar)
    print("[ok] залито из %s; запускай игру (первый запуск — офлайн, см. GUIDE-TOP-SAVE §6)" % src)
    if not args.no_keep:
        print("[i] pre-backup оставлен на устройстве: %s (или просто: python sk_save_editor.py rescue %s)"
              % (pre_backup, "--adb ..."))
    return 0


# ---------------------------------------------------------------------------
# 12.1. rescue: откат на pre-backup, doctor: диагностика устройства
#      (появились после зависания игры на 100% при первом прогоне push)
# ---------------------------------------------------------------------------
def cmd_rescue(args):
    """Восстановить профиль из pre-backup'а push (страховочный tar создаётся
    КАЖДЫМ push на самом устройстве, поэтому в нём сохранены исходные владелец
    и права — откат возвращает рабочее состояние ДО экспериментов)."""
    adb_path = args.adb or shutil.which("adb")
    if not adb_path:
        raise RuntimeError("adb не найден — укажи --adb")
    root = device_has_root(adb_path)
    if not root:
        raise RuntimeError("нет root: rescue работает через su")
    adb_run(adb_path, ["shell", "am force-stop %s" % PKG])
    print("[i] игра закрыта")
    out = device_sh(adb_path, root, "ls -1 /data/local/tmp 2>/dev/null").decode("utf-8", "replace")
    tars = sorted({l.strip() for l in out.splitlines()
                   if l.strip().startswith("sk_pre_backup_") and l.strip().endswith(".tar")})
    if not tars:
        raise RuntimeError("pre-backup'ов нет (/data/local/tmp/sk_pre_backup_*.tar). "
                           "Если удалял их вручную — остался только план с бэкапами на ПК "
                           "(*.bak_*) и LDPlayer-бэкапом приложения")
    print("[i] найдены pre-backup'ы (новее сверху):")
    for i, t in enumerate(reversed(tars), 1):
        print("    %d) %s" % (i, t))
    pick = tars[-1]
    if args.pick:
        if args.pick in tars:
            pick = args.pick
        elif args.pick.isdigit() and 1 <= int(args.pick) <= len(tars):
            pick = list(reversed(tars))[int(args.pick) - 1]
        else:
            raise RuntimeError("нет pre-backup'а %r (см. список выше)" % args.pick)
    app_uid = _app_uid(adb_path, root)
    device_sh(adb_path, root, "tar -xf /data/local/tmp/%s -C /data/data/%s" % (pick, PKG),
              timeout=600)
    _fix_ownership(adb_path, root, app_uid)
    if not getattr(args, "keep_new", False):
        try:
            lst = device_sh(adb_path, root,
                            "find /data/data/%s/files -maxdepth 1 -type f -name '*.data.new'" % PKG
                            ).decode("utf-8", "replace").split()
            if lst:
                device_sh(adb_path, root, "rm -f %s" % " ".join(lst))
        except Exception:
            pass
    print("[ok] восстановлено из %s (uid %d восстановлен)" % (pick, app_uid))
    print("[i] ПЕРВЫЙ ЗАПУСК — ОФЛАЙН (авиарежим/сеть выключена): иначе игра может "
          "подтянуть облако поверх отката")
    print("[i] если игра снова висит на 100% уже на ЭТОМ состоянии — проблема не в правках, "
          "а в окружении (сеть/Google-аккаунт); разбирайся с doctor: "
          "python sk_save_editor.py doctor -p <профиль>")
    return 0


def cmd_doctor(args):
    """Диагностика (read-only): владелец/права файлов на устройстве, .aes/.new,
    сверка md5 с локальным профилем, разбор ключевых секций профиля."""
    adb_path = args.adb or shutil.which("adb")
    if not adb_path:
        raise RuntimeError("adb не найден — укажи --adb")
    root = device_has_root(adb_path)
    if not root:
        raise RuntimeError("нет root: doctor смотрит /data/data через su")
    try:
        pid = device_sh(adb_path, root, "pidof %s" % PKG).decode().strip()
        if pid:
            print("[!] ИГРА ЗАПУЩЕНА (pid %s) — файлы меняются на лету, сверка md5 условна" % pid[:20])
    except Exception:
        pass
    app_uid = _app_uid(adb_path, root)
    print("[i] uid приложения: %d" % app_uid)
    problems = []
    _copy_state = {}  # item_data копии -> (materials, blueprints, seeds)

    def ls_stat(sub):
        out = device_sh(adb_path, root, "ls -ln /data/data/%s/%s" % (PKG, sub)
                        ).decode("utf-8", "replace")
        names, bad_uid, specials = [], 0, []
        for line in out.splitlines():
            f = line.split(None, 7)
            if len(f) < 8 or not f[0].startswith("-"):
                continue  # total/dir/симлинк/кривая строка
            names.append(f[7])
            try:
                if int(f[2]) != app_uid:
                    bad_uid += 1
            except ValueError:
                pass
            if f[7].endswith((".data.new", ".aes", ".rijndael")):
                specials.append(f[7])
        return names, bad_uid, specials

    for sub in ("files", "shared_prefs"):
        names, bad_uid, specials = ls_stat(sub)
        print("[i] %s: %d файлов, не-владельцев (чужой uid): %d" % (sub, len(names), bad_uid))
        if specials:
            print("    спец-файлы: %s" % ", ".join(sorted(specials)[:12]))
        if bad_uid:
            problems.append("%s: %d файлов с чужим uid (лечится: rescue или повторный push)" % (sub, bad_uid))

    prof = os.path.abspath(args.profile) if args.profile else None
    if prof:
        for sub in ("files", "shared_prefs"):
            pd = os.path.join(prof, sub)
            if not os.path.isdir(pd):
                print("[!] в профиле нет %s/ — сравнение неполное" % sub)
                continue
            loc = {}
            for fn in sorted(os.listdir(pd)):
                lp = os.path.join(pd, fn)
                if os.path.isfile(lp) and not _BAK_RE.search(fn) and not fn.endswith((".data.new", "_sk_push.tar")):
                    with open(lp, "rb") as f:
                        loc["/data/data/%s/%s/%s" % (PKG, sub, fn)] = hashlib.md5(f.read()).hexdigest()
            dev = _device_md5(adb_path, root, sorted(loc))
            ok = diff = extra = 0
            for p, h in loc.items():
                if p not in dev:
                    extra += 1
                    print("[!] нет на устройстве: %s" % os.path.basename(p))
                elif dev[p] != h:
                    diff += 1
                    print("[!] РАЗЛИЧАЕТСЯ: %s" % os.path.basename(p))
                else:
                    ok += 1
            print("[i] сверка с профилем %s/%s: совпало %d, различаются %d, нет на устройстве %d"
                  % (prof, sub, ok, diff, extra))
            if diff or extra:
                problems.append("профиль %s/%s не совпадает с устройством (%d/%d)" % (sub, ok, diff + extra))
    else:
        print("[i] профиль не задан (-p ПУТЬ) — md5-сверка пропущена, сравниваю только владельца")

    if prof:
        prefs_path = autodetect_prefs(prof) or autodetect_prefs(os.path.join(prof, "files"))
        if prefs_path:
            p = load_prefs_xml(prefs_path)
            print("[i] prefs: cloudSaveId=%s, Rij=%s, AES256=%s"
                  % (p.get("cloudSaveId"),
                     "есть" if p.get(PREF_RIJ_KEY) else "НЕТ",
                     "есть" if p.get(PREF_AES_KEY) else "НЕТ"))
            with open(prefs_path, "r", encoding="utf-8", errors="replace") as f:
                _ptxt = f.read()
            has = sorted(re.findall(r'name="(has_[a-z_]+_data)"', _ptxt))
            if has:
                print("[i] prefs: флаги секций: %s" % ", ".join(has))
        else:
            print("[!] playerprefs.xml в профиле не найден")
        for rel, sect, fields in (("files/setting.data", "settingData", ["UseRijData", "UseNewtonJsonData"]),
                                  ("files/game.data", "gameData", ["gameVersion", "gems", "lastGems"])):
            fp = os.path.join(prof, rel.replace("/", os.sep))
            if not os.path.isfile(fp):
                continue
            try:
                d = load_doc(fp, prefs_path)
                sd = d.obj if d.mode == "section" else (d.obj.get(sect) or {})
                vals = {k: sd.get(k) for k in fields}
                print("[i] %s: %s" % (rel, vals))
                if sect == "settingData" and vals.get("UseRijData"):
                    problems.append(
                        "setting.data: UseRijData=%r — Rij-канал ВКЛЮЧЁН: item_data/monsrise/misc/task/bp/mall "
                        "читаются из .rijndael/.data.new, и правки обычных .data ИГНОРИРУЮТСЯ. "
                        "Лечение: preset files/setting.data rij-legacy + sk_prefs.py rij-off" % vals.get("UseRijData"))
            except Exception as e:
                print("[!] %s не читается: %s" % (rel, e))
        for fn in sorted(os.listdir(os.path.join(prof, "files"))):
            if fn.startswith("item_data") and fn.endswith(".data"):
                try:
                    d = load_doc(os.path.join(prof, "files", fn), prefs_path)
                    it = d.obj if d.mode == "section" else (d.obj.get("itemData") or {})
                    m, b, s = it.get("materials") or {}, it.get("blueprints") or {}, it.get("seeds") or {}
                    print("[i] %s: materials=%d blueprints=%d seeds=%d" % (fn, len(m), len(b), len(s)))
                    _copy_state[fn] = (len(m), len(b), len(s))
                except Exception as e:
                    print("[!] %s не читается: %s" % (fn, e))
        # расхождение копий секции item_data: правили только одну -> игра видит пустую
        if len(_copy_state) > 1 and len(set(_copy_state.values())) > 1:
            problems.append(
                "копии item_data РАСХОДЯТСЯ (%s) — игра читает только одну из них; "
                "прогони пресеты с --all-copies, чтобы правка легла в обе"
                % "; ".join("%s: mat=%d bp=%d" % (fn, v[0], v[1]) for fn, v in _copy_state.items()))

    # живое состояние НА УСТРОЙСТВЕ: расшифровываем ключевые .data прямо там —
    # видно, какую копию секции игра реально перезаписала и что в ней сейчас
    try:
        names = device_list_files(adb_path, root, "/data/data/%s/files" % PKG)
        base_names = [n.rsplit("/", 1)[-1] for n in names]
        hits = [n for n in base_names
                if re.match(r"^(game|item_data|setting|statistic)(_|\.).*\.data$", n)]
        pets = [n for n in base_names
                if re.search(r"follower|pet|room_", n, re.I) and n.endswith((".data", ".data.new"))]
        if pets:
            print("[i] на устройстве есть файлы follower/pet/room_: %s" % ", ".join(sorted(pets)))
        for name in sorted(hits):
            try:
                raw = device_read_file(adb_path, root, "/data/data/%s/files/%s" % (PKG, name),
                                       timeout=120)
                kind, obj, codec, note = decode_any(raw, name, {})
                if not isinstance(obj, dict):
                    print("[i] устройство files/%s: kind=%s (%s)" % (name, kind, note))
                    continue
                sec = parse_save_name(name)["name"]
                if sec == "item_data":
                    print("[i] устройство files/%s: materials=%d blueprints=%d seeds=%d fish=%s" % (
                        name, len(obj.get("materials") or {}), len(obj.get("blueprints") or {}),
                        len(obj.get("seeds") or {}), obj.get("cumulativeFishCount")))
                elif sec == "game":
                    hu = obj.get("heroUnlock") or {}
                    print("[i] устройство files/%s: gems=%s heroUnlock=%d/%d heroLevel=%d skinLock=%d skillUnlock=%d" % (
                        name, obj.get("gems"), sum(1 for v in hu.values() if v is True), len(hu),
                        len(obj.get("heroLevel") or {}), len(obj.get("skinLock") or {}),
                        len(obj.get("heroSkillUnlock") or {})))
                elif sec == "setting":
                    print("[i] устройство files/%s: UseRijData=%s UseNewtonJsonData=%s" % (
                        name, obj.get("UseRijData"), obj.get("UseNewtonJsonData")))
            except Exception as e:
                print("[!] устройство files/%s: %s" % (name, e))
    except Exception as e:
        print("[!] device-side разбор не удался: %s" % e)
    print()
    if problems:
        print("DOCTOR: %d проблем(ы):" % len(problems))
        for pr in problems:
            print("  - %s" % pr)
        return 1
    print("DOCTOR: критичных проблем не видно (владелец ок; сверь md5 с профилем, если задан -p)")
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

    # --- 9) push-фильтр tar: бэкапы/временные/мусор не едут на устройство ---
    ti = lambda n: tarfile.TarInfo(n)
    check("push-фильтр tar",
          _push_tar_filter(ti("files/game.data.bak_20261111_212637")) is None
          and _push_tar_filter(ti("files/game.data.new")) is None
          and _push_tar_filter(ti("files/desktop.ini")) is None
          and _push_tar_filter(ti("shared_prefs/x.xml.bak_20261111_213343")) is None
          and _push_tar_filter(ti("files/game.data")) is not None)

    # --- 10) grant-*: синтез предметов на ПУСТОМ аккаунте (materials={} и т.д.) ---
    cat = json.loads(_IDS_870_JSON)
    check("каталог id: парсится и чистый",
          len(cat["materials"]) >= 70 and len(cat["seeds"]) >= 30 and len(cat["blueprints"]) >= 25
          and all(re.fullmatch(r"[A-Za-z0-9_]+", i)
                  for i in cat["materials"] + cat["seeds"] + list(cat["blueprints"])))
    fitem = os.path.join(tmp, "item_data.data")
    with open(fitem, "wb") as f:
        f.write(encode_any({"materials": {}, "blueprints": {}, "seeds": {}, "forgeWeapons": []},
                           "utf8", "des-default", prefs))
    d_it = load_doc(fitem)
    ch = run_preset(d_it, "grant-materials", ["499"], {})
    check("grant-materials создаёт записи на пустом",
          len(d_it.obj["materials"]) == len(cat["materials"])
          and all(v == 499 for v in d_it.obj["materials"].values()),
          "%d id, правок %d" % (len(d_it.obj["materials"]), len(ch)))
    run_preset(d_it, "grant-all", ["99"], {})
    check("grant-all: seeds+blueprints",
          len(d_it.obj["seeds"]) == len(cat["seeds"])
          and len(d_it.obj["blueprints"]) == len(cat["blueprints"]))
    save_doc(d_it, verify=True)
    d_it2 = load_doc(fitem)
    check("grant round-trip (des-default)",
          d_it2.obj["materials"] == d_it.obj["materials"]
          and d_it2.obj["blueprints"] == d_it.obj["blueprints"])

    # --- 11) push tar: владелец/права Android вшиты в члены ---
    ti_d = tarfile.TarInfo("files")
    ti_d.type = tarfile.DIRTYPE
    flt = _push_tar_ownership(10123)
    d2, f2 = flt(ti_d), flt(ti("files/game.data"))
    check("push tar: uid/режимы Android",
          d2.uid == f2.uid == 10123 and d2.mode == 0o771 and f2.mode == 0o660
          and d2.gid == 10123)

    # --- 12) grant-разблокировки 1.2.0 на ПУСТОМ game.data ---
    g2dir = os.path.join(tmp, "g2")
    os.makedirs(g2dir, exist_ok=True)
    fgame2 = os.path.join(g2dir, "game.data")
    with open(fgame2, "wb") as f:
        f.write(encode_any({"gems": 0}, "utf8", "xor", prefs))
    dg = load_doc(fgame2)
    run_preset(dg, "grant-heroes-levels", ["8"], {})
    run_preset(dg, "grant-skill-unlock", [], {})
    run_preset(dg, "grant-skins", [], {})
    run_preset(dg, "grant-pets", [], {})
    run_preset(dg, "grant-room-objects", ["5"], {})
    check("grant-heroes-levels: все герои = 8",
          len(dg.obj["heroLevel"]) == len(_HEROES_870)
          and all(v == 8 for v in dg.obj["heroLevel"].values()),
          "%d героев" % len(dg.obj["heroLevel"]))
    check("grant-skill-unlock: 3 скилла/герой",
          len(dg.obj["heroSkillUnlock"]) == len(_HEROES_870)
          and all([(e["Key"], e["Value"]) for e in v] == [(0, True), (1, True), (2, True)]
                  for v in dg.obj["heroSkillUnlock"].values()))
    check("grant-skins: все Value=1",
          len(dg.obj["skinLock"]) == len(_SKIN_COUNTS_870)
          and all(e["Value"] == 1 for lst in dg.obj["skinLock"].values() for e in lst)
          and len(dg.obj["skinLock"]["Knight"]) == 20)
    check("grant-pets: 36 питомцев", len(dg.obj["petUnlock"]) == len(_PETS_870))
    check("grant-room-objects: 5 объектов", all(v == 5 for v in dg.obj["roomObjectLevel"].values()))
    save_doc(dg, verify=True)
    dg2 = load_doc(fgame2)
    check("grant-разблокировки round-trip",
          dg2.obj["heroLevel"] == dg.obj["heroLevel"] and dg2.obj["skinLock"] == dg.obj["skinLock"])
    # grant-skins поверх существующих замков: Value != 1 -> 1
    dg.obj["skinLock"]["Knight"][0]["Value"] = -8
    ch = run_preset(dg, "grant-skins", [], {})
    check("grant-skins чинит существующие замки",
          dg.obj["skinLock"]["Knight"][0]["Value"] == 1 and ch)

    # --- 13) fish-total / grant-decor / item-unlock / rij-legacy / dungeon-times ---
    run_preset(d_it, "fish-total", ["1999"], {})
    check("fish-total: cumulativeFishCount", d_it.obj.get("cumulativeFishCount") == 1999)
    run_preset(d_it, "grant-decor", [], {})
    check("grant-decor: 6 объектов зала", len(d_it.obj["roomDecorateDic"]) == len(_ROOM_DECOR_870))
    run_preset(d_it, "item-unlock", [], {})
    check("item-unlock: горшки+мотоцикл",
          all(i in d_it.obj["itemUnlock"] for i in _ITEM_UNLOCK_870))
    fset = os.path.join(tmp, "setting.data")
    with open(fset, "wb") as f:
        f.write(encode_any({"UseRijData": True, "UseNewtonJsonData": True, "MaxLevel": 3},
                           "utf8", "des-default", prefs))
    dset = load_doc(fset)
    check("setting класс", dset.cls == "SettingData", str(dset.cls))
    run_preset(dset, "rij-legacy", [], {})
    check("rij-legacy: UseRijData=false", dset.obj["UseRijData"] is False)
    save_doc(dset, verify=True)
    fst = os.path.join(tmp, "statistic.data")
    with open(fst, "wb") as f:
        f.write(encode_any({"StatisticsData": {"LevelGameStatisticData": {
            "_enterDungeonTimes": {}, "$type": "GameStatisticSystem.LevelGameStatisticData"}}},
            "utf8", "des-statistic", prefs))
    dst_ = load_doc(fst)
    run_preset(dst_, "dungeon-times", ["5"], {})
    lg = dst_.obj["StatisticsData"]["LevelGameStatisticData"]
    check("dungeon-times: ключи-РЕЖИМЫ (формат топ-сейва)",
          lg["_enterDungeonTimes"]["Any"] == 5 and lg["_enterDungeonTimes"]["Normal"] == 5
          and lg["_finishDungeonTimes"]["Any"] == 5
          and lg["_finishLevelTimes"]["Any#0"] == 5 and lg["_finishLevelTimes"]["Normal#2"] == 5
          and lg["_highestLevelArrived"]["Any"] == 15,
          json.dumps(lg["_enterDungeonTimes"], ensure_ascii=False))
    run_preset(dst_, "pets-intimacy", ["300"], {})
    ev = dst_.obj["event2Count"]
    check("pets-intimacy: pet_0..35 intimacy/full/action",
          ev["pet_0_intimacy"] == 300 and ev["pet_35_full_intimacy"] == 1
          and ev["pet_10_action_unlock"] == 1 and ev["pet_full_intimacy"] == 36,
          "event2Count=%d ключей pet_*" % len([k for k in ev if k.startswith("pet_")]))
    save_doc(dst_, verify=True)

    # --- 13b) weapon-evol (список и словарь weapons) + troop2-open ---
    fwe = os.path.join(tmp, "weapon_evolution_data.data")
    with open(fwe, "wb") as f:
        f.write(encode_any({"weapons": [], "blindBoxOpenCount0": 0, "favorWeapons": []},
                           "utf8", "des-default", prefs))
    dwe = load_doc(fwe)
    check("weapon_evolution класс", (dwe.cls or "").endswith("WeaponEvolutionData"), str(dwe.cls))
    run_preset(dwe, "weapon-evol", ["999", "12"], {})
    wlist = dwe.obj["weapons"]
    check("weapon-evol: список из %d записей каталога (локальная форма WUWeapon)" % len(json.loads(_WEAPON_IDS_870_JSON)),
          isinstance(wlist, list) and len(wlist) == len(json.loads(_WEAPON_IDS_870_JSON))
          and wlist[0]["Name"] == "weapon_000" and wlist[0]["CurrentSkinIndex"] == 0
          and "CurrentSkinId" not in wlist[0] and "UpdateMaterialCount" not in wlist[0]
          and wlist[0]["UnlockedSkins"] == list(range(12)),
          "weapons=%d" % len(wlist))
    save_doc(dwe, verify=True)
    fwe2 = os.path.join(tmp, "weapon_evolution_data_235288966_.data")
    with open(fwe2, "wb") as f:
        f.write(encode_any({"weapons": {"weapon_001": {"Name": "weapon_001", "Level": 3,
                                                       "CurrentSkinId": 1, "UnlockedSkins": [0, 1],
                                                       "ModuleSlots": [], "UpdateMaterialCount": 5}}},
                           "utf8", "des-default", prefs))
    dwe2 = load_doc(fwe2)
    run_preset(dwe2, "weapon-evol", ["999", "12"], {})
    wd = dwe2.obj["weapons"]
    check("weapon-evol: словарь — апдейт + досоздание + чистка облачных полей",
          "CurrentSkinId" not in wd["weapon_001"] and "UpdateMaterialCount" not in wd["weapon_001"]
          and wd["weapon_001"]["CurrentSkinIndex"] == 0
          and wd["weapon_001"]["UnlockedSkins"] == list(range(12))
          and wd["weapon_002"]["CurrentSkinIndex"] == 0
          and len(wd) == len(json.loads(_WEAPON_IDS_870_JSON)),
          "weapons=%d" % len(wd))
    save_doc(dwe2, verify=True)
    fsd = os.path.join(tmp, "season_data.data")
    with open(fsd, "wb") as f:
        f.write(encode_any({"coin": 0, "troop2BoughtMercenary": {"7": 1}},
                           "utf8", "des-default", prefs))
    dsd = load_doc(fsd)
    check("season_data класс", dsd.cls == "SeasonData", str(dsd.cls))
    run_preset(dsd, "troop2-open", [], {})
    check("troop2-open: скиллы+сложность+отряд",
          dsd.obj["hasUnlockTroop2SkillHaste"] is True
          and dsd.obj["hasUnlockTroop2SkillBonus"] is True
          and dsd.obj["troop2UnlockedDifficulty"] == 3
          and dsd.obj["troop2ExtraTeamSize"] == 4
          and dsd.obj["troop2BoughtMercenary"] == {"7": 1},
          json.dumps({k: v for k, v in dsd.obj.items() if "troop2" in k}, ensure_ascii=False)[:120])
    save_doc(dsd, verify=True)

    # --- 13c) season-open: задачи сезона -> IsComplete=true ---
    dsd2 = load_doc(fsd)
    dsd2.obj["troop2ChampionNormalTasks"] = [
        {"Id": 101, "IsComplete": False, "HasGetReward": False, "CurrentScore": 0},
        {"Id": 102, "IsComplete": False, "HasGetReward": False, "CurrentScore": 2}]
    dsd2.obj["troop2TaskExp"] = 0
    run_preset(dsd2, "season-open", ["60"], {})
    tn = dsd2.obj["troop2ChampionNormalTasks"]
    check("season-open: задачи IsComplete=true + TaskExp/TrophyLevel",
          all(t["IsComplete"] is True for t in tn) and tn[1]["CurrentScore"] == 99999
          and dsd2.obj["troop2TaskExp"] == 99999 and dsd2.obj["troop2TrophyLevel"] == 60
          and not dsd2.obj.get("troop2ClaimedTrophyLevel"),
          json.dumps(tn, ensure_ascii=False)[:100])
    save_doc(dsd2, verify=True)

    # --- 13d) tasks-done: NewTaskData + легаси tasks[] ---
    ftd = os.path.join(tmp, "task_235288966_.data")
    with open(ftd, "wb") as f:
        f.write(encode_any({"NewTaskData": [
                                {"ID": 300, "Status": "Progress", "TaskType": "Career",
                                 "CurrentProgress": 2, "TargetProgress": 5},
                                {"ID": 301, "Status": "End", "TaskType": "Career",
                                 "CurrentProgress": 1, "TargetProgress": 1}],
                            "tasks": [{"type": "Enemy", "enemyId": "boss08", "total": 1,
                                       "count": 0, "isCompleted": False, "isRewarded": False},
                                      {"type": "Challenge", "enemyId": None, "total": 0,
                                       "count": 10, "isCompleted": False, "isRewarded": False}],
                            "currentTask": None, "completedTask": None},
                           "utf8", "des-default", prefs))
    dtd = load_doc(ftd)
    run_preset(dtd, "tasks-done", [], {})
    check("tasks-done: NewTaskData Status=End + tasks[] isCompleted",
          dtd.obj["NewTaskData"][0]["Status"] == "End"
          and dtd.obj["NewTaskData"][0]["CurrentProgress"] == 5
          and dtd.obj["tasks"][0]["count"] == 1 and dtd.obj["tasks"][0]["isCompleted"] is True
          and dtd.obj["tasks"][1]["isCompleted"] is True,
          json.dumps(dtd.obj["NewTaskData"][0], ensure_ascii=False))
    save_doc(dtd, verify=True)

    # --- 14) --all-copies: находим обе копии item_data ---
    sibs = _section_siblings(fitem)  # fitem = <tmp>/item_data.data
    check("--all-copies: копий вне tmp нет", sibs == [], str(sibs))
    fitem_uid = os.path.join(tmp, "item_data_235288966_.data")
    with open(fitem_uid, "wb") as f:
        f.write(encode_any({"materials": {}, "blueprints": {}, "seeds": {}}, "utf8", "des-default", prefs))
    sibs2 = _section_siblings(fitem_uid)
    check("--all-copies: sibling найден",
          [os.path.basename(s) for s in sibs2] == ["item_data.data"], str(sibs2))

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


def _section_siblings(path):
    """Другие копии той же секции в каталоге (для --all-copies): item_data.data
    + item_data_<acc>_.data — правятся одинаково, т.к. неизвестно, какую копию
    читает конкретная сборка. Временные/бэкапы не участвуют."""
    d = os.path.dirname(os.path.abspath(path)) or "."
    own = parse_save_name(path)["name"].lower()
    own_base = os.path.basename(path)
    out = []
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return out
    for fn in names:
        if fn == own_base or ".bak_" in fn or fn.endswith(".data.new") \
                or fn == "_sk_push.tar" or fn.endswith(".tmp"):
            continue
        if not fn.lower().endswith(SAVE_SUFFIXES):
            continue
        if parse_save_name(fn)["name"].lower() == own:
            out.append(os.path.join(d, fn))
    return out


def _mutator_cmd(args, apply_fn, desc):
    prefs_xml = args.prefs or autodetect_prefs(args.file)
    docs = [(args.file, load_doc(args.file, prefs_xml))]
    # 1.4.0: --all-copies ВКЛЮЧЁН ПО УМОЛЧАНИЮ (игра может читать любую из копий
    # секции: base <sec>.data или UID <sec>_<acc>_.data — факт 1.4.0: у task/
    # season реальный контент был в base, у item_data/statistic — в UID; правим
    # обе). Отключить: --only-this.
    if not getattr(args, "only_this", False):
        for sp in _section_siblings(args.file):
            try:
                docs.append((sp, load_doc(sp, prefs_xml)))
            except Exception as e:
                print("[!] %s пропущен (%s)" % (os.path.basename(sp), e))
        if len(docs) > 1:
            print("[i] all-copies (по умолчанию): цели %s" % ", ".join(os.path.basename(p) for p, _ in docs))
    applied = 0
    for path, doc in docs:
        changes = apply_fn(doc)
        if not changes:
            print("[i] изменений нет (%s) в %s" % (desc, os.path.basename(path)))
            continue
        print("Планируемые правки (%s) -> %s:" % (desc, os.path.basename(path)))
        print_changes(changes)
        if args.dry_run:
            print("[dry-run] ничего не записано")
            continue
        if not confirm("Применить к %s?" % path, args.yes):
            print("отменено")
            return 1
        backup_path = save_doc(doc, args.backup_dir)
        new_sha = hashlib.sha256(open(path, "rb").read()).hexdigest()
        print("[ok] записано; бэкап: %s; sha256: %s" % (backup_path, new_sha[:16]))
        applied += 1
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
    common.add_argument("--all-copies", action="store_true", default=argparse.SUPPRESS,
                        help="(с 1.4.0 вкл. по умолчанию) применять правку ко ВСЕМ копиям секции")
    common.add_argument("--only-this", action="store_true", default=argparse.SUPPRESS,
                        help="править ТОЛЬКО указанный файл, без копий секции (отключает all-copies)")

    ap = argparse.ArgumentParser(
        prog="sk_save_editor.py",
        description="Редактор локальных сохранений Soul Knight 8.7.0 и облачных дампов "
                    "(docs/LOCAL-SAVE-870.md; форк SKDEditor, GPL-2.0)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Примеры:\n"
               "  %(prog)s info ./device_saves\n"
               "  %(prog)s show game.data gems\n"
               "  %(prog)s preset game.data gems 800000\n"
               "  %(prog)s preset item_data.data grant-all 499   # пустой аккаунт\n"
               "  %(prog)s preset cloud.bin currencies\n"
               "  %(prog)s pull -o ./device_saves --kill\n"
               "  %(prog)s push ./device_saves --kill\n"
               "  %(prog)s rescue                     # откат на pre-backup push\n"
               "  %(prog)s doctor -p ./device_saves   # диагностика устройства\n"
               "  %(prog)s selftest\n")
    ap.add_argument("--prefs", help="playerprefs.xml (для .aes-ключей и cloudSaveId)")
    ap.add_argument("--yes", "-y", action="store_true", help="не спрашивать подтверждение")
    ap.add_argument("--dry-run", action="store_true", help="показать правки, не записывать")
    ap.add_argument("--backup-dir", help="каталог для бэкапов (по умолчанию рядом с файлом)")
    ap.add_argument("--force-id", action="store_true",
                    help="разрешить правку cloudSaveId/platformId/ChillyUid/serialzedVersion")
    ap.add_argument("--all-copies", action="store_true",
                    help="(с 1.4.0 вкл. по умолчанию) править все копии секции")
    ap.add_argument("--only-this", action="store_true",
                    help="править только указанный файл, без копий секции")
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
    sp.add_argument("--ids-file", help="JSON с дополнительными id для grant-*: {\"materials\":[...], \"seeds\":[...], \"blueprints\":{...}}")
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

    sp = sub.add_parser("rescue", help="откатить устройство на pre-backup последнего push (root)")
    sp.add_argument("pick", nargs="?", help="имя sk_pre_backup_*.tar или номер в списке (1 = новейший)")
    sp.add_argument("--adb", help="путь к adb")
    sp.add_argument("--keep-new", action="store_true", help="НЕ удалять *.data.new после отката")
    sp.set_defaults(fn=cmd_rescue)

    sp = sub.add_parser("doctor", help="диагностика устройства: владелец/права, .aes/.new, сверка md5 с профилем")
    sp.add_argument("-p", "--profile", help="локальный профиль (папка с files/ и shared_prefs/) для md5-сверки")
    sp.add_argument("--adb", help="путь к adb")
    sp.set_defaults(fn=cmd_doctor)

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
