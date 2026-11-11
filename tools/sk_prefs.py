#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sk_prefs.py — парсер/редактор prefs.xml (Unity/Android SharedPreferences) игры
Soul Knight 8.7.0. Закрывает вторую половину локального сэйва: файлы `.data`
правит sk_save_editor.py, а playerprefs — этот инструмент.

Файл: /data/data/com.ChillyRoom.DungeonShooter/shared_prefs/
      com.ChillyRoom.DungeonShooter.v2.playerprefs.xml
Кодировка: на устройстве — обычный UTF-8/ASCII (стандартный Android
SharedPreferences); UTF-16LE c BOM (FF FE) встречается в дампах, сохранённых
через PowerShell-редирект. Формат `<map>`: элементы
<int name="K" value="V"/> / <string name="K">V</string> / <long|float|boolean>.

Ключевой факт (реальное устройство, 12.11.2026): игра хранит данные В ДВУХ
неймспейсах — `<accountId>_*` и `LOCAL*` (зеркало ~1:1). Регистр bool
различается: `<UID>_*` → "true"/"false", `LOCAL*` → "True"/"False".
Имена ключей и строковые значения могут быть URL-экранированы ("%23" → "#").

Правка — хирургическая, на уровне текста: файл не пересобирается, меняются
только нужные элементы, перед записью — бэкап, после — повторный парс.

Примеры:
  python3 sk_prefs.py info prefs.xml
  python3 sk_prefs.py schema prefs.xml
  python3 sk_prefs.py mirror prefs.xml
  python3 sk_prefs.py gems prefs.xml 50000 --yes
  python3 sk_prefs.py unlock-heroes prefs.xml --yes
  python3 sk_prefs.py set prefs.xml 235288966_room_level 9 --mirror --yes
  python3 sk_prefs.py get prefs.xml LOCALlast_gems
"""
import argparse, os, re, sys, time, shutil, collections, urllib.parse
import xml.etree.ElementTree as ET

BOM = b"\xff\xfe"
INT_RE = re.compile(r'<int name="([^"]+)" value="([^"]*)"\s*/>')
STR_RE = re.compile(r'<string name="([^"]+)">(.*?)</string>', re.S)
LONG_RE = re.compile(r'<long name="([^"]+)" value="([^"]*)"\s*/>')
BOOL_RE = re.compile(r'<boolean name="([^"]+)" value="([^"]*)"\s*/>')
FLOAT_RE = re.compile(r'<float name="([^"]+)" value="([^"]*)"\s*/>')

ELEM_RE = collections.OrderedDict([
    ("int", INT_RE), ("string", STR_RE), ("long", LONG_RE),
    ("boolean", BOOL_RE), ("float", FLOAT_RE),
])


def read_text(path):
    """-> (text, bom_flag). Устройство пишет UTF-8/ASCII без BOM;
    UTF-16LE — артефакт PowerShell-дампов (всегда с BOM)."""
    raw = open(path, "rb").read()
    if raw[:2] == BOM:
        return raw.decode("utf-16"), True
    if raw[:2] == b"\xfe\xff":
        return raw.decode("utf-16-be"), True
    if b"\x00" in raw[:128]:          # UTF-16LE без BOM (редкий случай)
        return raw.decode("utf-16-le"), True
    return raw.decode("utf-8"), False


def write_text(path, text, bom):
    data = text.encode("utf-16-le") if bom else text.encode("utf-8")
    if bom:
        data = BOM + data
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def parse_entries(text):
    """-> dict name -> (kind, raw_value, span, elem_regex_group_text)"""
    out = {}
    for kind, rx in ELEM_RE.items():
        for m in rx.finditer(text):
            name = m.group(1)
            val = m.group(2) if kind != "string" else m.group(2)
            out[name] = {"kind": kind, "value": val, "span": m.span(),
                         "match": m}
    return out


def looks_url_encoded(s):
    return bool(s) and ("%" in s) and re.search(r"%[0-9A-Fa-f]{2}", s)


def try_decode(s):
    if s is None:
        return None
    if looks_url_encoded(s):
        try:
            return urllib.parse.unquote(s)
        except Exception:
            return s
    return s


def guess_kind(value):
    if re.fullmatch(r"-?\d+", value or ""):
        return "int"
    return "string"


def backup(path):
    b = f"{path}.bak_{time.strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(path, b)
    return b


def verify(path):
    text, bom = read_text(path)
    root = ET.fromstring(text)
    return len(list(root)), bom


def iter_all(path):
    text, bom = read_text(path)
    entries = parse_entries(text)
    return text, bom, entries


def detect_uid(entries):
    c = collections.Counter()
    for name in entries:
        m = re.match(r"^(\d+)_", name)
        if m:
            c[m.group(1)] += 1
    return c.most_common(1)[0][0] if c else None


def sniff_encoding(path):
    """Человеческая метка кодировки файла (для info)."""
    raw = open(path, "rb").read(4)
    if raw[:2] == BOM:
        return "UTF-16LE+BOM (PowerShell-дамп)"
    if raw[:2] == b"\xfe\xff":
        return "UTF-16BE+BOM"
    if raw[:1] == b"\xff" or raw[:1] == b"\xfe":
        return "UTF-16LE+BOM"
    return "UTF-8/ASCII (как на устройстве)"


# ---------------------------------------------------------------- commands --

def cmd_info(a):
    text, bom, entries = iter_all(a.prefs)
    kinds = collections.Counter(e["kind"] for e in entries.values())
    uid = detect_uid(entries)
    print(f"file        : {a.prefs}")
    print(f"encoding    : {sniff_encoding(a.prefs)}")
    print(f"entries     : {len(entries)}  ({', '.join(f'{k}={v}' for k, v in kinds.items())})")
    print(f"accountId   : {uid}")
    for k in ("cloudSaveId", f"{uid}_cloudSaveId", "last_chilly_uid",
              "last_account_id", "LOCALlast_pid"):
        if k in entries:
            print(f"{k:24s}= {entries[k]['value']}")
    rij = [k for k in ("DataRijndael128Key", "DataRijndaelIV", "DataAES256Key")
           if k in entries]
    print(f"Rijndael ключи в prefs: {rij if rij else 'НЕТ (не хранятся в playerprefs)'}")
    for k in sorted(entries):
        if re.match(r"(OpenRijTest|OpenNewtonJsonTest)_", k):
            print(f"{k} = {entries[k]['value']}")
    flags = sorted(k for k in entries
                   if re.match(r"(\d+_)?has_\w+_data$", k) and
                   (k.startswith("LOCAL") or k.startswith(f"{uid}_")))
    print(f"has_*_data флагов: {len(flags)}; все = "
          f"{sorted(set(entries[k]['value'] for k in flags))}")
    if uid:
        led = [f"{uid}_gems", f"{uid}_last_gems", f"{uid}_var_gems",
               f"{uid}_cumulative_gems", f"{uid}_cumulative_consume_gems",
               f"{uid}_reborn_card", f"{uid}_magic_stone_index"]
        for k in led:
            if k in entries:
                print(f"{k:38s}= {entries[k]['value']}")


def cmd_schema(a):
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)

    def pat(name):
        p = name.replace(uid, "<UID>") if uid else name
        return re.sub(r"\d+", "N", p)

    groups = collections.defaultdict(list)
    for name, e in entries.items():
        groups[pat(name)].append(name)
    print(f"{'count':>6}  {'kind':7s} pattern")
    for p, names in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        kinds = collections.Counter(entries[n]["kind"] for n in names)
        ks = ",".join(f"{k}x{v}" for k, v in kinds.items())
        print(f"{len(names):>6}  {ks:7s} {p}")
    print(f"\nитого: {len(entries)} ключей, {len(groups)} паттернов")


def cmd_dump(a):
    text, bom, entries = iter_all(a.prefs)
    out = {}
    for name, e in sorted(entries.items()):
        v = e["value"]
        row = {"kind": e["kind"], "value": v}
        dec = try_decode(v)
        if dec != v:
            row["decoded"] = dec
        out[name] = row
    js = json.dumps(out, ensure_ascii=False, indent=1)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(js)
        print(f"[+] {len(out)} ключей -> {a.out}")
    else:
        print(js)


def cmd_blobs(a):
    text, bom, entries = iter_all(a.prefs)
    for name, e in sorted(entries.items()):
        dec = try_decode(e["value"])
        if dec != e["value"] and dec and dec.lstrip()[:1] in "{[":
            print(f"=== {name} (decoded {len(dec)} ch) ===")
            print(dec[:a.head] + (f"\n... [+{len(dec)-a.head} ch]" if len(dec) > a.head else ""))
            print()


def cmd_get(a):
    text, bom, entries = iter_all(a.prefs)
    e = entries.get(a.key)
    if not e:
        print(f"<нет ключа: {a.key}>", file=sys.stderr)
        sys.exit(1)
    print(f"kind   : {e['kind']}")
    print(f"raw    : {e['value']}")
    dec = try_decode(e["value"])
    if dec != e["value"]:
        print(f"decoded: {dec}")


def cmd_mirror(a):
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    same = diff = 0
    diffs = []
    for name, e in entries.items():
        if not name.startswith("LOCAL"):
            continue
        alt = f"{uid}_{name[5:]}"
        if alt in entries:
            if str(e["value"]).lower() == str(entries[alt]["value"]).lower():
                same += 1
            else:
                diff += 1
                diffs.append((name, e["value"], entries[alt]["value"]))
    print(f"сравнено {same + diff}, совпало {same}, РАЗЛИЧИЙ {diff}")
    for n, lv, uv in diffs[:a.limit]:
        print(f"  {n}: LOCAL={lv!r}  UID={uv!r}")


def set_key(text, key, value, kind):
    """Хирургическая правка/вставка одного ключа. -> (new_text, action)"""
    for kind_rx, rx in ELEM_RE.items():
        for m in rx.finditer(text):
            if m.group(1) == key:
                if kind_rx == "string":
                    new = f'<string name="{key}">{value}</string>'
                else:
                    new = f'<{kind_rx} name="{key}" value="{value}" />'
                return text[:m.start()] + new + text[m.end():], "replaced"
    if kind == "int":
        el = f'<int name="{key}" value="{value}" />'
    elif kind == "long":
        el = f'<long name="{key}" value="{value}" />'
    elif kind == "boolean":
        el = f'<boolean name="{key}" value="{value}" />'
    else:
        el = f'<string name="{key}">{value}</string>'
    if "</map>" in text:
        i = text.rindex("</map>")
        return text[:i] + el + "\n" + text[i:], "added"
    return text + el + "\n</map>\n", "added+closed_map"


def mirror_key(key, uid):
    """-> второй ключ зеркала или None. Учитывает регистр-суффиксы."""
    if uid and key.startswith(f"{uid}_"):
        cand = "LOCAL" + key[len(uid) + 1:]
        return cand
    if key.startswith("LOCAL"):
        return f"{uid}_{key[5:]}"
    return None


def edit_keys(path, ops, yes=False):
    """ops: list of (key, value, kind). Показывает дифф, бэкап, верификация."""
    text, bom, entries = iter_all(path)
    plan = []
    for key, value, kind in ops:
        old = entries.get(key)
        plan.append((key, kind, old["value"] if old else "<отсутствует>",
                     value, old["kind"] if old else "новый"))
    if not plan:
        print("нечего менять")
        return
    w = max(len(p[0]) for p in plan)
    for key, kind, old, new, okind in plan:
        print(f"  [{okind:>9}] {key:{w}s}  {old!r} -> {new!r} ({kind})")
    if not yes and input("Применить? [y/N] ").strip().lower() not in ("y", "yes"):
        print("отменено")
        return
    b = backup(path)
    for key, kind, old, new, okind in plan:
        text, _ = set_key(text, key, new, kind)
    write_text(path, text, bom)
    n, _ = verify(path)
    print(f"[+] записано, бэкап: {b}; повторный парс: {n} элементов, OK")


def cmd_set(a):
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    kind = a.kind if a.kind != "auto" else guess_kind(a.value)
    ops = [(a.key, a.value, kind)]
    if a.mirror:
        mk = mirror_key(a.key, uid)
        if mk and mk in entries:
            # сохраняем регистр bool существующего зеркала
            mval, mkind = a.value, kind
            if entries[mk]["kind"] == "string" and a.value.lower() in ("true", "false"):
                mval = a.value.capitalize() if entries[mk]["value"][:1].isupper() else a.value.lower()
            ops.append((mk, mval, mkind))
        else:
            print(f"[i] зеркала для {a.key} нет ({mk}) — правлю только основной")
    edit_keys(a.prefs, ops, yes=a.yes)


def cmd_del(a):
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    keys = [a.key]
    if a.mirror:
        mk = mirror_key(a.key, uid)
        if mk and mk in entries:
            keys.append(mk)
    plan = []
    for k in keys:
        e = entries.get(k)
        if not e:
            print(f"<нет ключа: {k}>", file=sys.stderr)
            continue
        plan.append((k, e["kind"], e["value"], "<удалён>", e["kind"]))
    if not plan:
        return
    for k, kind, old, new, ok in plan:
        print(f"  {k}: {old!r} -> <удалён>")
    if not a.yes and input("Удалить? [y/N] ").strip().lower() not in ("y", "yes"):
        print("отменено")
        return
    b = backup(a.prefs)
    for k, *_ in plan:
        m = entries[k]["match"]
        text = text[:m.start()] + text[m.end():]
    write_text(a.prefs, text, bom)
    verify(a.prefs)
    print(f"[+] удалено, бэкап: {b}")


def cmd_gems(a):
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    n = a.n
    keys = {
        f"{uid}_gems": str(n),
        f"{uid}_last_gems": str(n),
        f"{uid}_var_gems": "0",
    }
    cum_k = f"{uid}_cumulative_gems"
    if cum_k in entries:
        try:
            cum = int(entries[cum_k]["value"])
        except ValueError:
            cum = 0
        keys[cum_k] = str(max(cum, n))  # ledger: cumulative >= текущие
    if a.all_ns:
        keys["LOCALlast_gems"] = str(n)
    ops = [(k, v, "int") for k, v in keys.items()]
    edit_keys(a.prefs, ops, yes=a.yes)
    print("[!] не забудь синхронизировать game.data (gems/lastGems/varGems):")
    print(f"    python3 sk_save_editor.py preset <profile>/game.data gems {n} --yes")


def cmd_unlock_heroes(a):
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    ops = []
    for name, e in sorted(entries.items(), key=lambda kv: kv[0]):
        if re.fullmatch(rf"{uid}_c\d+_unlock", name):
            ops.append((name, "true", "string"))
        elif re.fullmatch(r"LOCALc\d+_unlock", name):
            ops.append((name, "True", "string"))
    if not ops:
        print("ключей *_cN_unlock не найдено")
        return
    if a.extend:
        ids = sorted({int(g) for k in entries
                      for g in re.findall(r"c(\d+)_unlock$", k)})
        for i in ids:
            for key, val in ((f"{uid}_c{i}_unlock", "true"),
                             (f"LOCALc{i}_unlock", "True")):
                if key not in entries:
                    ops.append((key, val, "string"))
    print(f"[i] будет выставлено {len(ops)} ключей (регистр bool по неймспейсу)")
    edit_keys(a.prefs, ops, yes=a.yes)


def cmd_unlock_plants(a):
    """Все растения: <UID>_pN_unlock -> true, LOCALpN_unlock -> True."""
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    ops = []
    for name in sorted(entries):
        if re.fullmatch(rf"{uid}_p\d+_unlock", name):
            ops.append((name, "true", "string"))
        elif re.fullmatch(r"LOCALp\d+_unlock", name):
            ops.append((name, "True", "string"))
    if a.extend:
        ids = sorted({int(g) for k in entries
                      for g in re.findall(r"p(\d+)_unlock$", k)})
        for i in ids:
            for key, val in ((f"{uid}_p{i}_unlock", "true"),
                             (f"LOCALp{i}_unlock", "True")):
                if key not in entries:
                    ops.append((key, val, "string"))
    if not ops:
        print("ключей *_pN_unlock не найдено")
        return
    print(f"[i] будет выставлено {len(ops)} ключей (регистр bool по неймспейсу)")
    edit_keys(a.prefs, ops, yes=a.yes)


def cmd_unlock_skills(a):
    """Все скиллы героев: <UID>_c_<Name>_skill_N_unlock -> 1 (только <UID>_,
    LOCAL-зеркала для скиллов на устройстве не observed)."""
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    ops = []
    rx = re.compile(rf"{uid}_c_[A-Za-z]+_skill_\d+_unlock$")
    for name in sorted(entries):
        if rx.fullmatch(name):
            ops.append((name, "1", "int"))
    if not ops:
        print("ключей *_c_<Hero>_skill_N_unlock не найдено")
        return
    print(f"[i] будет выставлено {len(ops)} ключей скиллов в 1")
    edit_keys(a.prefs, ops, yes=a.yes)


def cmd_unlock_levels(a):
    """Уровни героев в prefs: <UID>_cN_level и LOCALcN_level -> N.
    Без --extend правятся только существующие ключи; с --extend создаются
    для всех виденных cN (по cN_unlock/cN_level/cN_skinN).
    1.4.0: порядок аргументов любой — 'unlock-levels 8 prefs.xml' и
    'unlock-levels prefs.xml 8' равнозначны."""
    prefs, n = getattr(a, "prefs", None), getattr(a, "n", None)
    x, y = getattr(a, "x", None), getattr(a, "y", None)
    if x is not None:
        if y is None:
            sys.exit("нужны два аргумента: prefs.xml и уровень (в любом порядке)")
        if x.lstrip("-").isdigit() and not y.lstrip("-").isdigit():
            prefs, n = y, int(x)
        elif y.lstrip("-").isdigit():
            prefs, n = x, int(y)
        else:
            sys.exit(f"не понял, где уровень: '{x}' / '{y}' (нужны prefs.xml и число)")
    a.prefs, a.n = prefs, int(n)
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    ops = []
    for name in sorted(entries):
        if re.fullmatch(rf"{uid}_c\d+_level", name):
            ops.append((name, str(a.n), "int"))
        elif re.fullmatch(r"LOCALc\d+_level", name):
            ops.append((name, str(a.n), "int"))
    if a.extend:
        ids = sorted({int(g) for k in entries
                      for g in re.findall(r"c(\d+)_(?:unlock|level|skin\d+)$", k)})
        for i in ids:
            for key in (f"{uid}_c{i}_level", f"LOCALc{i}_level"):
                if key not in entries:
                    ops.append((key, str(a.n), "int"))
    if not ops:
        print("ключей *_cN_level не найдено (попробуй --extend)")
        return
    print(f"[i] будет выставлено {len(ops)} ключей уровней в {a.n}")
    edit_keys(a.prefs, ops, yes=a.yes)
    print("[i] семантика cN_level: на свежем аккаунте 0; максимум героя = 8. Если в игре"
          " уровень отобразится некорректно — повтори с соседним числом (7 или 8)")
    print("[!] синхронизируй и game.data:")
    print(f"    python sk_save_editor.py preset <profile>/files/game.data grant-heroes-levels {a.n} --yes")


def cmd_unlock_skins(a):
    """Скины героев в prefs: <UID>_cN_skinM и LOCALcN_skinM -> 1 (код открытого
    скина в skinLock/top-сейве; замки/цены — отрицательные и крупные значения)."""
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    rx_uid = re.compile(rf"{uid}_c\d+_skin\d+$")
    rx_loc = re.compile(r"LOCALc\d+_skin\d+$")
    ops = []
    for name in sorted(entries):
        if rx_uid.fullmatch(name) or rx_loc.fullmatch(name):
            if entries[name]["value"] != str(a.value):
                ops.append((name, str(a.value), "int"))
    if not ops:
        print("скины уже открыты (или ключей *_cN_skinM нет)")
        return
    print(f"[i] будет выставлено {len(ops)} ключей скинов в {a.value}")
    edit_keys(a.prefs, ops, yes=a.yes)
    print("[!] синхронизируй и game.data:")
    print("    python sk_save_editor.py preset <profile>/files/game.data grant-skins --yes")


def cmd_rij_off(a):
    """Выключить Rij-канал в prefs: OpenRijTest_<UID> (+LOCAL-зеркало) -> 0.
    Без этого игра может продолжать читать item_data мимо наших .data."""
    text, bom, entries = iter_all(a.prefs)
    uid = detect_uid(entries)
    if not uid:
        sys.exit(" accountId не найден")
    ops = []
    for name, e in sorted(entries.items()):
        if re.fullmatch(rf"OpenRijTest_{uid}", name) or re.fullmatch(r"LOCALOpenRijTest", name) \
                or re.fullmatch(rf"{uid}_OpenRijTest", name):
            if e["value"] not in ("0", "false", "False"):
                ops.append((name, "0", "int"))
    if not ops:
        print("[i] Rij-канал в prefs уже выключен (или флагов нет)")
        return
    for k, v, kind in ops:
        print(f"  {k}: {entries[k]['value']!r} -> '0'")
    edit_keys(a.prefs, ops, yes=a.yes)
    print("[!] также выставь UseRijData=false в setting.data:")
    print("    python sk_save_editor.py preset <profile>/files/setting.data rij-legacy --yes")


def cmd_find(a):
    """Поиск по ключам и ДЕКОДИРОВАННЫМ значениям prefs (без учёта регистра).
    Для разведки: python3 sk_prefs.py find prefs.xml follower"""
    text, bom, entries = iter_all(a.prefs)
    q = a.substr.lower()
    uid = detect_uid(entries)
    hits = 0
    for name, e in sorted(entries.items()):
        dec = try_decode(e["value"]) or ""
        if q in name.lower() or q in dec.lower():
            hits += 1
            if hits > a.limit:
                print("... (обрезано, увеличь --limit)")
                break
            shown = dec if len(dec) <= a.head else dec[:a.head] + "..."
            print(f"{name}\n    [{e['kind']}] {shown}")
    if not hits:
        print("<не найдено>")
    else:
        print(f"\nнайдено: {hits}" + (" (ограничено)" if hits > a.limit else ""))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in [("info", cmd_info), ("schema", cmd_schema), ("dump", cmd_dump),
                     ("blobs", cmd_blobs), ("get", cmd_get), ("set", cmd_set),
                     ("del", cmd_del), ("mirror", cmd_mirror),
                     ("gems", cmd_gems), ("unlock-heroes", cmd_unlock_heroes),
                     ("unlock-plants", cmd_unlock_plants),
                     ("unlock-skills", cmd_unlock_skills),
                     ("unlock-levels", cmd_unlock_levels),
                     ("unlock-skins", cmd_unlock_skins),
                     ("rij-off", cmd_rij_off), ("find", cmd_find)]:
        p = sub.add_parser(name)
        if name == "unlock-levels":
            # 1.4.0: оба позиционных — строки; порядок любой (unlock-levels 8 prefs.xml
            # == unlock-levels prefs.xml 8). Именно из-за старого жёсткого порядка
            # команда на устройстве упала и уровни героев не записались.
            p.add_argument("x", help="prefs.xml ИЛИ уровень (порядок не важен)")
            p.add_argument("y", nargs="?", default=None,
                           help="prefs.xml ИЛИ уровень")
        else:
            p.add_argument("prefs", help="путь к prefs.xml (v2.playerprefs.xml)")
        if name == "dump":
            p.add_argument("-o", "--out", help="JSON-файл для выгрузки")
        if name == "blobs":
            p.add_argument("--head", type=int, default=1200, help="символов на blob")
        if name == "get":
            p.add_argument("key")
        if name == "mirror":
            p.add_argument("--limit", type=int, default=20)
        if name == "set":
            p.add_argument("key")
            p.add_argument("value")
            p.add_argument("--kind", choices=["auto", "int", "string", "long",
                                              "boolean", "float"], default="auto")
            p.add_argument("--mirror", action="store_true",
                           help="править и LOCAL*/<UID>_ зеркало")
        if name == "del":
            p.add_argument("key")
            p.add_argument("--mirror", action="store_true")
        if name == "gems":
            p.add_argument("n", type=int, help="новое значение gems")
            p.add_argument("--all-ns", action="store_true", default=True,
                           help="также LOCALlast_gems (по умолчанию вкл)")
        if name in ("set", "del", "gems", "unlock-heroes", "unlock-plants",
                    "unlock-skills", "unlock-levels", "unlock-skins", "rij-off"):
            p.add_argument("--yes", action="store_true")
        if name in ("unlock-heroes", "unlock-plants"):
            p.add_argument("--extend", action="store_true",
                           help="создать ключи для всех виденных id")
        if name == "unlock-levels":
            p.add_argument("--extend", action="store_true",
                           help="создать cN_level для всех виденных героев")
        if name == "unlock-skins":
            p.add_argument("--value", type=int, default=1,
                           help="код открытого скина (по умолчанию 1)")
        if name == "find":
            p.add_argument("substr", help="подстрока для поиска по ключам/значениям")
            p.add_argument("--limit", type=int, default=40)
            p.add_argument("--head", type=int, default=200,
                           help="символов значения на показ")
        p.set_defaults(fn=fn)
    a = ap.parse_args()
    a.fn(a)


import json  # noqa: E402  (используется в cmd_dump)

if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:  # piped into `head` etc.
        import os
        os._exit(0)
