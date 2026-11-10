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

## 4. Путь записи (v3.3, в разработке)

Так как `open(SnapshotMetadata)` возвращает полный `Snapshot`, цепочка записи
короткая — те же вызовы, что и на чтение:

1. `blockWritesOff()` — временно снять блокиратор записи (см. v3.2).
2. `open(SnapshotMetadata)` — подтверждённый путь.
3. `contents.writeBytes(data)` — перезапись буфера с нуля.
4. `client.commitAndClose(snapshot, metadataChange)` — коммит; Google проверяет
   только Google-авторизацию и размер (≤3 МБ), ChillyRoom GPGS-запись не видит
   (см. [CLOUD-SAVE.md §3.2](CLOUD-SAVE.md)).
5. Проверка перед коммитом: `readFully()` после `writeBytes` должен дать тот же
   sha256, что и вносимый буфер; после — `listNow()` должен показать новый
   `LastModifiedTimestamp`.

Безопасность: конфликт при `open()` — **аборт** (никаких авто-resolve); любые
операции только при живом эталоне дампа (файл + sha256 из §2).

## 5. Инструменты

| Файл | Назначение |
|---|---|
| `tools/frida_sk_snapshot_dump.js` | v3.2: listNow / readSnap / passiveOn / blockWritesOn |
| `tools/sk870_decrypt_all.py` | расшифровка ЛОКАЛЬНЫХ сейвов (поли XOR / polyxor) |
| `tools/analyze_cloud_dump.py` | UTF-16LE → UTF-8, обзор секций дампа |
| `tools/drill_cloud_save.py` | точечный дрилл значений (леджер, герои, инвентарь) |
| `tools/sk870_crept_crypto.py`, `tools/sk870_metadata_*.py` | крипта/метадата локальных сейвов (см. [REVERSING.md](REVERSING.md)) |

## 6. Следующие шаги

- [ ] v3.3 `writeSnapNoop()` — write-back тех же байт (проверка write path).
- [ ] v3.3 `writeSnapFile(path)` — запись выбранного файла (восстановление/правка).
- [ ] Патчер JSON (`patch_cloud_save.py`) — согласованные правки леджера.
- [ ] Определить, возвращает ли `Err/Banned` при записи в **новое** облако
      ChillyRoom (отдельно от GPGS).
