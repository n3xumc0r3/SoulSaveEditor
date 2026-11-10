# Дамп облачного сейва Soul Knight 8.7.0 — разбор (GPGS snapshot)

> Практическое продолжение [CLOUD-SAVE.md](CLOUD-SAVE.md): реальный выкачанный
> снапшот `SoulKnightCloud` из процесса игры 8.7.0 (легаси-ветка GPGS,
> PGS v1 SDK). Дата: 11.11.2026.

## 1. Как выкачан

Инструмент — `tools/frida_sk_snapshot_dump.js` **v3.2** (Frida 17.6.2,
attach-only `-n "Soul Knight"`, spawn `-f` на этой связке не работает).
Хуки не используются вовсе (политика v3: только REPL + Java.choose) — всё
делается «руками» из REPL через игровой же PGS v1 SDK.

Ключевые факты о среде (важно для воспроизведения):

1. **Конфликт-политики**: данная связка (старый PGS v1 SDK + свежий GMS Core)
   отклоняет **все** явные `open(String, boolean, int)` с сообщением
   `IllegalStateException: Must provide a valid conflict policy!` — включая
   `MANUAL(0)`. Константы политик через рефлексию читаются как 0.
2. **Рабочий путь чтения** — перегрузка **`open(SnapshotMetadata)` без
   политики**: возвращает `Task<DataOrConflict<Snapshot>>`;
   `getData()` → `Snapshot` → `getSnapshotContents().readFully()`.
3. Android 11+ scoped storage: `/sdcard/skdump` не пишется (ENOENT), fallback
   `Android/data/com.ChillyRoom.DungeonShooter/files/skdump` работает.
4. Чтение снапшота **работает на замороженном аккаунте** (фриз ChillyRoom
   на GPGS-ветку не распространяется).

## 2. Файл дампа

| Свойство | Значение |
|---|---|
| Имя снапшота | `SoulKnightCloud` (1 шт. в списке `load(true)`) |
| Размер | 693 800 байт |
| SHA-256 | `1490c88da14110f69bce0fa274e4ea7f67bcb37b1f15cd0936390fb73d3dfb4c` |
| Кодировка | **UTF-16LE, без BOM** |
| Формат | **один валидный JSON, 346 900 символов, БЕЗ ШИФРОВАНИЯ** |
| Начало | `{"serialzedVersion":1,"cloudSaveId":"GPV2_235288940",…` |

Содержимое дампа в репозиторий **не публикуется** (личные данные сейва).

### Выводы

1. **Облачный сейв 8.7.0 (легаси GPGS-ветка) — открытый UTF-16LE JSON.**
   Ни XOR, ни polyxor не применяются. `tools/sk870_decrypt_all.py` предназначен
   только для ЛОКАЛЬНЫХ сейвов; его вывод на дампе — ложные срабатывания
   (XOR поверх уже чистого текста).
2. `GPV2_235288940` — это внутренний `cloudSaveId` **внутри JSON**, а не имя
   снапшота. Имя снапшота в GPGS — `SoulKnightCloud`. Прошедшая путаница снята.
3. В JSON смешанные эскейпы (кириллица в письмах `\u04XX`, в других ключах —
   сырые символы) ⇒ байт-в-байт пересобрать одним `json.dumps()` нельзя.
   Для правок: точечная замена подстрок в декодированном тексте, либо полная
   пересборка с `ensure_ascii=True` (парсер игры видит оба вида эскейпов
   в оригинале).

## 3. Структура (18 секций верхнего уровня)

| Секция | Содержимое |
|---|---|
| `serialzedVersion` | 1 (опечатка игры — ключ так и называется) |
| `cloudSaveId` / `platformId` | `GPV2_235288940` / UUID |
| `gameData` | прогресс: скины, герои, питомцы, **валюты**, комната |
| `itemData` | инвентарь: материалы, чертежи, семена, мифик-оружие, тикеты |
| `statisticData` | статистика, письма, ачивки, платёжные маркеры, забеги |
| `settingData` | настройки, `ChillyUid: None` |
| `taskData` | задачи (18 новых + 7 активных) |
| `maps` | 1 слот — комната-трейлер (4328 объектов) |
| `seasonData` | сезон: монеты, чипы, эвенты |
| `sandboxConfig`, `mallReloadData`, `monsterRiseData`, `weaponEvolutionData`, `miscData`, `pvpData`, `battlePassSaveData`, `escapeSeasonData` | все = `null` |

### Леджер алмазов (важно для восстановления)

```
gameData.gems                    1 124 375
gameData.lastGems                1 124 278
gameData.varGems                         97   ← gems - lastGems
gameData.cumulativeGems                   0
statisticData.KrppVerifiedUsdCents        0   ← верифицированных покупок на $0
statisticData.KrppCountedOrderIds        []
```

> >1,1 млн алмазов при нулевой сумме верифицированных покупок — характерный
> след серверной заморозки (гипотеза, не факт). При правке/восстановлении
> леджер править согласованно: `gems = lastGems = X; varGems = 0`.
> Признаков фриза **в самом сейве нет** — фриз живёт на стороне сервера игры;
> `ChillyUid: None` — привязка только к GPGS.

Прочее: все 26 героев открыты (ур. 7–8), 36 питомцев, 53 типа материалов,
7 мифик-оружий (все ур. 3), `restoreKeys: 124`, `usedSecretKeys: 18`
(`SKGIFT`, `SKNIGHT`, …), `gameVersion: 2130`, режимные saveId — в
`mode2SaveGameIdDic`.

## 4. Путь записи (v3.3 — ДОКАЗАН noop-записью 11.11.2026)

Так как `open(SnapshotMetadata)` возвращает полный `Snapshot`, цепочка записи —
те же вызовы, что и на чтение. Полностью подтверждена живым прогоном
`tools/frida_sk_snapshot_write.js` (attach к процессу игры, REPL, без хуков):

1. `open(SnapshotMetadata)` → `DataOrConflict` → `getData()` → `Snapshot`
   (конфликт → **аборт**, авто-resolve не используется).
2. `contents.writeBytes(byte[])` — **прямой вызов на типизированной обёртке
   Frida** (стратегия S1). Резолвить метод через
   `Java.use(<runtimeClass>).writeBytes.overloads` НЕЛЬЗЯ — мост в этой связке
   даёт `TypeError: not a function` (см. «Грабли»).
3. VERIFY перед коммитом: `readFully()` после записи обязан дать тот же
   sha256, что и вносимый буфер; не совпал — **NO COMMIT**.
4. `client.commitAndClose(snapshot, SnapshotMetadataChange)` — legacy v1
   перегрузка `(Snapshot, MetadataChange)`. С пустым change описание и
   playedTime сохраняются, обновляется только LastModifiedTimestamp. Google
   проверяет только Google-авторизацию и размер (≤3 МБ), ChillyRoom GPGS-запись
   не видит (см. [CLOUD-SAVE.md §3.2](CLOUD-SAVE.md)).
5. Авто-проверка: `listNow()` показывает свежий `LastModifiedTimestamp`.

### Доказательство (live-лог 11.11.2026, `writeSnapNoop`)

| Шаг | Результат |
|---|---|
| `open(SnapshotMetadata)` | OK, конфликтов нет |
| текущий контент облака | 693 800 байт, sha256 `1490c88d…` == эталонный дамп §2 |
| запись | OK via **S1** `contents.writeBytes(byte[])` — 693 800 байт в буфер |
| VERIFY | sha256 прочитанного обратно буфера совпал → commit разрешён |
| `commitAndClose` | **COMMIT OK**: LastModifiedTimestamp 1794328956355 → 1794342792628 (2026-11-10T20:33:12Z) |
| `listNow()` после | свежий LastModifiedTimestamp подтверждён; Description/PlayedTime не изменились |

Итог: **восстановление/замена облачного сейва возможны**; эталонный дамп §2 —
точка восстановления.

### Вторая сессия (11.11.2026): первая РЕАЛЬНАЯ правка закоммичена

`patch_cloud_save.py`: `gameData.gems = gameData.lastGems = 1000`,
`varGems = 0` (согласованный леджер) → 698 716 байт (было 693 800; +4 916 —
ожидаемо: `ensure_ascii` разворачивает кириллицу в `\uXXXX`, 12 байт на escape
против 2 байт на символ; до лимита 3 МБ далеко). sha256
`094216e9151e2554d248ad1c0ddeb5aef54111a77e0e86472b9572d77a8064b3` →
VERIFY OK → **COMMIT OK ×2** (1794343747132, 1794343762167), свежий
LastModifiedTimestamp подтверждён `listNow()`. Осталось: контрольная загрузка
в игре (см. §6).

### Третья сессия (11.11.2026): проверка владельца — `cloudSaveId`

При попытке загрузить облачный сейв на эмуляторе игра отказывает:
«Не удалось загрузить сохранение: Старое сохранение относится к
GPV2_235288940, его нельзя загрузить». Сейв несёт `cloudSaveId`
телефон-аккаунта (см. §2), а эмуляторная установка ждёт свой собственный id —
проверка владения на стороне клиента. Сработала именно сейчас, потому что
наши коммиты сделали облако самым свежим — эмулятор впервые попытался его
подтянуть.

План обхода (id подменяем в самом сейве):

1. Найти ожидаемый id: `findIds()` в REPL (v3.3.4) — скан rw-памяти на
   UTF-16LE/ASCII строки `GPV2_*` (в Unity IL2CPP это mono-строки, НЕ
   java.lang.String). Запускать при открытом диалоге ошибки или главном меню.
   Ожидаемый id — одиночная строка `GPV2_...`, ОТЛИЧНАЯ от id из текста
   ошибки; хит вида `{"serialzedVersion"...` — сам буфер сейва.
2. Запасной путь: расшифровать ЛОКАЛЬНЫЙ сейв эмулятора
   (`tools/sk870_decrypt_all.py`) и взять `cloudSaveId` оттуда (заодно
   глянуть `platformId` — UUID).
3. Запатчить ту версию, которая сейчас в облаке (gems=1000, `094216e9…`;
   сверить LMT через `listNow()` — 1794343762167):
   `python patch_cloud_save.py cloud_patched.bin cloud_patched_id.bin --set-str cloudSaveId=GPV2_<ожидаемый>`
4. Залить коротким именем без точек (грабли №4):
   `adb push cloud_patched_id.bin /sdcard/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/cpid1`
   → `writeSnapFile('SoulKnightCloud', '.../cpid1')` → `listNow()`.
5. force-stop → рестарт → проверить загрузку.

Предостережения: меняем ОДНО поле за раз (`platformId` — только если ошибка
останется); после подмены id ТЕЛЕФОН может отказаться грузить этот сейв
(зеркальная проверка) — id сцепляет сейв с одним устройством; откат —
заливка `cloud_patched.bin` (`094216e9…`).

### Грабли (зафиксированы прогоном)

1. **`getClass().getName()` на типизированных обёртках не вызывается** в этой
   связке (Frida 17.6.2 + legacy PGS v1): метадата печатается как `?`, а
   `Java.use(<runtimeClass>)` по такому имени даёт ClassNotFoundException,
   `.writeBytes.overloads` — TypeError. Правило v3.2 расширено и на запись:
   методы звать только напрямую на обёртке (`w.contents.writeBytes(...)`),
   имя класса при необходимости — из свойства `$className` (v3.3.2).
2. **Scoped storage**: `/sdcard/skdump` из процесса приложения не создаётся
   (`mkdirs` молча не срабатывает, затем ENOENT). Рабочий каталог для дампов
   и push-файлов —
   `/storage/emulated/0/Android/data/com.ChillyRoom.DungeonShooter/files/skdump`.
3. Блокиратор записи снимать вручную не пришлось: сессия шла без
   вооружённого `blockWritesOn`, а на случай вооружённого скрипт сам поднимает
   его на время нашей записи и переармирует после (`withWritesUnblocked`).
4. **Имя push-файла на устройстве может не совпасть** (live-сессия 2):
   `adb push .../cloud_patched.bin` отчитался успехом, но из процесса игры
   точное имя давало ENOENT, а читалось укороченное имя `cloud_patche`
   (ровно тот же размер и содержимое). v3.3.3 при ENOENT сам печатает листинг
   каталога глазами процесса игры (`listDeviceDir`); контроль снаружи —
   `adb shell ls -la /sdcard/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/`.
5. **Stale Activity**: в долгой Frida-сессии `findActivity()` может вернуть
   отвязанный Activity — `getExternalFilesDir` падает NPE и убивает всю
   fallback-цепочку дампов (на коммит не влияет). v3.3.3 берёт каталоги
   из Application (`ActivityThread.currentApplication()`) в первую очередь.
6. **Проверка владельца**: игра грузит облачный сейв только при совпадении
   `cloudSaveId` внутри JSON с ожидаемым на устройстве — иначе
   «Старое сохранение относится к GPV2_..., его нельзя загрузить».
   Обход и инструменты — см. «Третья сессия» выше.

### Рецепт реальной правки (сейф-процедура)

PC:

```
# эталонный дамп уже есть: 693 800 байт, sha256 1490c88d… (точка восстановления)
python patch_cloud_save.py snap_SoulKnightCloud_XXXX.bin cloud_patched.bin \
    --set gameData.gems=1000000 --set gameData.lastGems=1000000 --set gameData.varGems=0
adb push cloud_patched.bin /storage/emulated/0/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/
```

Устройство (REPL того же скрипта):

```
writeSnapFile('SoulKnightCloud', '/storage/emulated/0/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/cloud_patched.bin')
listNow()   // свежий LastModifiedTimestamp = запись принята
```

Перед `writeSnapFile` сверьте имя файла на устройстве глазами процесса игры
(скрипт v3.3.3 сам печатает листинг при ENOENT) или снаружи:
`adb shell ls -la /sdcard/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/`.

Пока игра запущена со старым состоянием в памяти, её очередной автосейв может
перезаписать облако обратно: после коммита сразу
`adb shell am force-stop com.ChillyRoom.DungeonShooter`, затем рестарт и
проверка. Откат — тот же `writeSnapFile` с эталонным дампом
(восстановление уже доказано noop-прогоном).

## 5. Инструменты

| Файл | Назначение |
|---|---|
| `tools/frida_sk_snapshot_dump.js` | v3.2: listNow / readSnap / passiveOn / blockWritesOn |
| `tools/frida_sk_snapshot_write.js` | v3.3.4: WRITE-путь — writeSnapNoop / writeSnapFile (S1, verify, commit, listDeviceDir) + findIds |
| `tools/patch_cloud_save.py` | патчер облачного JSON: UTF-16LE → `--set PATH=VALUE` / `--set-str PATH=VALUE` → UTF-16LE (typo-guard на ключи) |
| `tools/sk870_decrypt_all.py` | расшифровка ЛОКАЛЬНЫХ сейвов (поли XOR / polyxor) |
| `tools/analyze_cloud_dump.py` | UTF-16LE → UTF-8, обзор секций дампа |
| `tools/drill_cloud_save.py` | точечный дрилл значений (леджер, герои, инвентарь) |
| `tools/sk870_crept_crypto.py`, `tools/sk870_metadata_*.py` | крипта/метадата локальных сейвов (см. [REVERSING.md](REVERSING.md)) |

## 6. Следующие шаги

- [x] v3.3 `writeSnapNoop()` — write-back тех же байт: **доказано** 11.11.2026
      (VERIFY OK → COMMIT OK, свежий LastModifiedTimestamp в `listNow`).
- [x] v3.3 `writeSnapFile(path)` — запись файла готова (обстреляна noop'ом);
      применить при первой реальной правке/восстановлении.
- [x] Патчер JSON (`patch_cloud_save.py`) — готов; round-trip на эталонном
      дампе даёт валидный UTF-16LE JSON (`7b 00 22 00`).
- [x] Первая реальная правка на согласованном леджере закоммичена в облако
      11.11.2026 (gems = lastGems = 1000, varGems = 0; VERIFY+COMMIT OK ×2).
- [ ] Контрольная загрузка на эмуляторе: сначала подменить `cloudSaveId`
      (см. «Третья сессия» §4) → force-stop → рестарт → сейв загрузился,
      1000 алмазов, инвентарь/герои на месте.
- [ ] Проверить обратную сторону: после подмены id телефон может отказаться
      грузить сейв (зеркальная проверка владельца).
- [ ] Определить, возвращает ли `Err/Banned` при записи в **новое** облако
      ChillyRoom (отдельно от GPGS).
