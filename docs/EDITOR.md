# EDITOR.md — sk_save_editor.py: быстрый старт

`tools/sk_save_editor.py` — флагманский редактор локальных сохранений
Soul Knight 8.7.0 **и** облачных дампов (GPGS-снапшотов). Один инструмент
на весь цикл: вытащить профиль → посмотреть → отредактировать → залить назад.

Требования: Python 3.8+, `pip install pycryptodome`, для `pull`/`push` — adb
(в PATH или `--adb D:\LDPlayer\LDPlayer14\adb.exe`).

## 0. Проверка инструмента

```bash
python3 tools/sk_save_editor.py selftest
```

28 проверок: round-trip всех криптосхем (UTF-8 JSON / UTF-16LE / XOR game.data /
DES `crst1` / DES `iambo` / SDK-DES / Rijndael-AES по ключам playerprefs),
пресеты, движок путей, защиты служебных полей.

## 1. Вытащить профиль с устройства (root, игру закроет сам)

```bash
python3 tools/sk_save_editor.py pull -o ./device_saves --kill
# свой adb:
python3 tools/sk_save_editor.py pull -o ./device_saves --kill --adb "D:\LDPlayer\LDPlayer14\adb.exe"
```

Забирает `files/*.data` (все секции) + `shared_prefs/*.playerprefs.xml`,
печатает `cloudSaveId` и наличие per-install ключей Rijndael/AES
(`DataRijndael128Key` / `DataRijndaelIV` / `DataAES256Key`).
**`--kill` обязателен по смыслу:** игра кэширует секции в памяти
(`SaveUtil._saveDataDic`) и пишет отложенно (`SaveInterval`) — если её не
закрыть, на диске окажутся устаревшие данные, а после push игра перезатрёт
правки своим кэшем.

## 2. Посмотреть, что есть

```bash
python3 tools/sk_save_editor.py list ./device_saves      # инвентарь + форматы
python3 tools/sk_save_editor.py info ./device_saves/*.data
python3 tools/sk_save_editor.py show ./device_saves/game.data gems
python3 tools/sk_save_editor.py show ./device_saves/game.data materials
python3 tools/sk_save_editor.py dump  ./device_saves/game.data -o game.json
```

Формат определяется автоматически по содержимому (не по расширению): открытый
JSON / UTF-16LE / XOR / DES-crst1 / DES-iambo / SDK-DES / Rijndael-AES
(для `.aes` ключи берутся из `--prefs playerprefs.xml` — рядом лежащий
находится сам).

## 3. Отредактировать

### Пресеты (проверенные операции; список: `presets`)

```bash
python3 tools/sk_save_editor.py preset ./device_saves/game.data gems 800000
python3 tools/sk_save_editor.py preset ./device_saves/game.data unlock-heroes
python3 tools/sk_save_editor.py preset ./device_saves/game.data heroes-level 8
python3 tools/sk_save_editor.py preset ./device_saves/game.data unlock-pets
python3 tools/sk_save_editor.py preset ./device_saves/item_data.data research-all
python3 tools/sk_save_editor.py preset ./device_saves/item_data.data all-materials 499
python3 tools/sk_save_editor.py preset ./device_saves/season_data.data season-coins 80000
python3 tools/sk_save_editor.py preset ./device_saves/game.data currencies   # read-only сводка
```

Перед записью каждая правка показывается построчно (`старое -> новое`) и
требует подтверждения; `--dry-run` — посмотреть без записи, `--yes` — без
вопросов. Перед каждой записью создаётся бэкап (`<файл>.bak_<timestamp>` или
`--backup-dir`), после записи выполняется round-trip-верификация.

Принцип пресетов: **игровые id не выдумываются** — правятся только ключи, уже
присутствующие в сейве (или явно запрошенные, как `material ID N`).

### Произвольные правки по путям

```bash
python3 tools/sk_save_editor.py write ./device_saves/game.data gems 900000
python3 tools/sk_save_editor.py add   ./device_saves/game.data cumulativeGems 100
python3 tools/sk_save_editor.py write ./device_saves/item_data.data materials.material_battery 999
python3 tools/sk_save_editor.py show  ./device_saves/item_data.data plants[0].watered
python3 tools/sk_save_editor.py append ./device_saves/item_data.data forgeWeapons weapon_034
```

Пути: `секция.поле.подполе`, элементы списков — `[индекс]`. В облачном дампе
первый сегмент — секция CloudSaveGameData (`gameData.gems`), в секционном файле
корень — сам класс (`gems`).

### Что инструмент не даст сломать

* `serialzedVersion`, `cloudSaveId`, `platformId`, `ChillyUid` — заблокированы
  без `--force-id` (привязка профиля и версия схемы);
* неизвестные полю класса правки помечаются предупреждением: игра молча
  ИГНОРИРУЕТ неизвестные поля — правка потеряется (см. LOCAL-SAVE-870.md §6);
* запись происходит только после round-trip-проверки; формат файла при записи
  сохраняется тем же (XOR остаётся XOR, b64-DES остаётся b64, raw-AES — raw).

## 4. Залить обратно

```bash
python3 tools/sk_save_editor.py push ./device_saves --kill
```

Делает страховочный tar текущего профиля на устройстве
(`/data/local/tmp/sk_pre_backup_<ts>.tar`), заливает каталог, восстанавливает
владельца файлов (chown по uid папки `files`), подсказывает команду отката.

## 5. Облачный дамп (GPGS-снапшот)

```bash
python3 tools/sk_save_editor.py show snap_SoulKnightCloud.bin gameData.gems
python3 tools/sk_save_editor.py preset snap_SoulKnightCloud.bin gems 800000 --dry-run
python3 tools/sk_save_editor.py preset snap_SoulKnightCloud.bin currencies
```

UTF-16LE JSON определяется и правится автоматически (формат `ToJsonWithUnicode`
игры). Леджер алмазов правится согласованно (`gems = lastGems = N, varGems = 0`)
— проверено на живом дампе 693 800 байт и залито в облако через Frida
(см. CLOUD-DUMP-870.md).

## 6. Чего ещё не проверено на живых файлах (честный список)

* легаси XOR-ключ `game.data` не подтверждён живым файлом 8.7.0 (схема и ключ —
  из SKDEditor + метадаты; selftest гарантирует только внутреннюю
  консистентность);
* Rijndael/AES wire-format: реализованы разумные варианты (raw/base64,
  CBC/ECB, ключи из playerprefs как base64/hex/utf8/sha256) — точная схема
  станет известна после первого `pull` с устройства, где есть `.aes`;
* `statistic*.data` IV предположительно легаси `Ahbool\0\0` (ключ `crst1`
  подтверждён литералом в метадате).

Все три закрываются одной командой `pull` на устройстве с реальными сейвами.

## 7. Заметка о статистике-антифроде

`statisticData.KrppVerifiedUsdCents` / `KrppCountedOrderIds` — верифицированные
покупки; `mallReloadData.buyGemCount*` — счётчики покупок алмазов. Резкие
значения валют при нулевых счётчиках покупок — характерный след редактирования
(в живом дампе именно так: 1 124 375 алмазов при 0 центов — и сейв заморожен
сервером). Инструмент эти поля не трогает, но имей в виду при выборе значений.
