# GUIDE-TOP-SAVE — пошаговый гайд: топовый локальный сэйв Soul Knight 8.7.0

Проверено на реальном устройстве (аккаунт 235288966, LDPlayer, Windows).
Команды даны под Windows (`python`, пути `\`); на Linux/macOS — `python3` и `/`.
Инструменты: [`tools/sk_save_editor.py`](../tools/sk_save_editor.py) (секционные
`.data`) и [`tools/sk_prefs.py`](../tools/sk_prefs.py) (playerprefs). Теория:
[LOCAL-SAVE-870.md](LOCAL-SAVE-870.md), [PREFS-870.md](PREFS-870.md),
[EDITOR.md](EDITOR.md).

## 0. Принципы (не пропускать)

1. **Игра закрыта** во время pull/правок/push — игра кэширует секции в памяти
   и пишет отложенно; живой процесс перезатрёт правки своим кэшем.
   У редактора это флаг `--kill` (сам закрывает игру).
2. **Бэкапы**: редактор и sk_prefs.py кладут `<файл>.bak_<timestamp>` рядом с
   каждой правкой; `pull` перед push делает tar текущего профиля на устройстве.
3. Сэйв состоит из **двух половин**: `files/*.data` и `shared_prefs/…v2.playerprefs.xml`.
   Править согласованно (гемы: леджер в обоих; герои: и там, и там).
4. В prefs **два неймспейса** (`<UID>_*` и `LOCAL*`) — править оба
   (`--mirror`/готовые команды делают сами, регистр bool соблюдается).
5. **Игровые id не выдумываются**: пресеты правят только то, что уже есть в
   сейве/видено на устройстве.
6. **Антифрод**: резкие значения валют при нулевых счётчиках покупок —
   характерный след (см. EDITOR.md §9). Значения ниже выбраны «на долго, но
   без гор» именно поэтому.

## 1. Забрать профиль (root, игру закроет сам)

```bat
python tools\sk_save_editor.py pull -o .\profile --kill --adb "D:\LDPlayer\LDPlayer14\adb.exe"
```

Забирает ВСЕ файлы верхнего уровня `files/` + `shared_prefs/` (включая
playerprefs.xml), печатает `cloudSaveId`. Чтение пофайловое через
`base64` — бинарно-безопасно даже на старых adb, где `tar`-поток молча
теряет хвост (ровно это и случалось: pull отдавал 3 файла из 50).
Результат: `profile\files\*.data` и `profile\shared_prefs\*.xml`.

Если что-то не взялось — запускай с `--debug` (печатает каждый файл и
размер) и смотри §8.

### 1.1. План Б: бэкап приложения от LDPlayer (без adb)

LDPlayer умеет бэкапить приложение целиком в `.rar` (Меню → Резервное
копирование). Внутри `DungeonShooter_backup\` лежат те же `files\` и
`shared_prefs\` — это и есть профиль:

```bat
:: распакуй rar правым кликом (WinRAR/7-Zip), затем:
mkdir profile
xcopy /E /I /Y DungeonShooter_backup\files profile\files
xcopy /E /I /Y DungeonShooter_backup\shared_prefs profile\shared_prefs
python tools\sk_save_editor.py list .\profile
```

Дальше все шаги §2–§4 идентичны; для заливки (§5) всё равно нужен adb.

## 2. Осмотреться

```bat
python tools\sk_save_editor.py list .\profile
python tools\sk_save_editor.py preset .\profile\files\game.data currencies
python tools\sk_prefs.py info .\profile\shared_prefs\com.ChillyRoom.DungeonShooter.v2.playerprefs.xml
```

## 3. Патчим `.data` (пресеты редактора)

**Важно: у секций две копии** — `<sec>.data` (легаси) и
`<sec>_<accountId>_.data` (каноническая, именно её пишет игра; в метадате
литералы `_<accountId>_`). Правь ОБЕ одинаково — тогда неважно, какую
читает твоя сборка. Исключения: `game.data` существует только без uid,
`battles_*`/`escape_run_save_*` — только с uid. Файлы `.data.new` не
трогаем (временные, push сам их почистит на устройстве).

«Надолго, но не горы» (каждая правка показывает диф и пишет бэкап;
`--yes` — без вопросов; ниже accountId 235288966 — замени на свой,
он в выводе `info`):

> **С версии 1.0.1**: если blueprints/materials/seeds в указанном файле пусты,
> пресет не молчит, а подсказывает соседние копии секции — у 8.7.0 `<sec>.data`
> часто пустой шаблон, а реальные предметы лежат в `<sec>_<accountId>_.data`.
> Правь обе копии (как в блоке ниже).

```bat
:: алмазы: gems = lastGems = N, varGems = 0, cumulativeGems += — леджер согласован
python tools\sk_save_editor.py preset .\profile\files\game.data gems 50000 --yes

:: рыбные чипы и монеты сезона (обе копии season_data)
python tools\sk_save_editor.py preset .\profile\files\game.data fish-chips 20000 --yes
python tools\sk_save_editor.py preset .\profile\files\season_data.data season-coins 30000 --yes
python tools\sk_save_editor.py preset .\profile\files\season_data_235288966_.data season-coins 30000 --yes

:: герои, скиллы, питомцы
python tools\sk_save_editor.py preset .\profile\files\game.data unlock-heroes --yes
python tools\sk_save_editor.py preset .\profile\files\game.data heroes-level 8 --yes
python tools\sk_save_editor.py preset .\profile\files\game.data unlock-pets --yes

:: крафт: все чертежи + материалы/семена (только те, что уже есть в сейве;
:: если materials/seeds пусты — пресет честно откажется, это норма)
python tools\sk_save_editor.py preset .\profile\files\item_data.data research-all --yes
python tools\sk_save_editor.py preset .\profile\files\item_data.data all-materials 499 --yes
python tools\sk_save_editor.py preset .\profile\files\item_data.data all-seeds 99 --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data research-all --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data all-materials 499 --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data all-seeds 99 --yes

:: карта возрождения (gameData.rebornCard + restoreKeys)
python tools\sk_save_editor.py preset .\profile\files\game.data reborn-card --yes

:: проверить итог
python tools\sk_save_editor.py preset .\profile\files\game.data currencies
```

Справка по всем пресетам: `python tools\sk_save_editor.py presets`.

## 4. Патчим prefs.xml (вторая половина сэйва)

```bat
set PREFS=.\profile\shared_prefs\com.ChillyRoom.DungeonShooter.v2.playerprefs.xml

:: леджер гемов: gems=last_gems=50000, var_gems=0, cumulative=max(old,50000), LOCALlast_gems
python tools\sk_prefs.py gems %PREFS% 50000 --yes

:: герои (оба неймспейса, --extend создаёт ключи для всех виденных id)
python tools\sk_prefs.py unlock-heroes %PREFS% --extend --yes

:: скиллы всех героев -> 1
python tools\sk_prefs.py unlock-skills %PREFS% --yes

:: растения (оба неймспейса)
python tools\sk_prefs.py unlock-plants %PREFS% --extend --yes

:: точечные правки с зеркалом
python tools\sk_prefs.py set %PREFS% 235288966_room_level 9 --mirror --yes
python tools\sk_prefs.py set %PREFS% 235288966_reborn_card 10 --yes
```

* Номер аккаунта (235288966) замени на свой — он в выводе `info`.
* `room_level` продублируй в game.data, если хочешь то же значение там:
  `python tools\sk_save_editor.py write .\profile\files\game.data roomLevel 9 --yes`
* Не трогай: `cloudSaveId*`, `Sdk*Cache*`, `UsedAccounts*`, `unity.*`,
  `has_*_data` (при живых `.data`-файлах).

## 5. Положить обратно

```bat
python tools\sk_save_editor.py push .\profile --kill --adb "D:\LDPlayer\LDPlayer14\adb.exe"
```

Push (v1.0.1) делает:

1. страховочный tar текущего профиля на устройстве
   (`/data/local/tmp/sk_pre_backup_<ts>.tar`);
2. локальный tar профиля — **без** `.bak_*`, `.data.new` и мусора Windows
   (`desktop.ini`/`Thumbs.db`);
3. доставку tar: `adb push` + контроль размера на устройстве; если push
   не сработал (старые adb LDPlayer молча падают на путях) — автоматически
   заливает base64-чанками через `exec-out` (тот же канал, что у pull,
   бинарно-безопасно; путь назначения — только прямые слэши, старый баг
   `os.path.join` с `\` в имени удалён);
4. распаковку в `/data/data/<pkg>`, удаление временных `*.data.new` на
   устройстве (иначе первый запуск может откатить ими свежие `.data`),
   выборочную сверку размеров ключевых файлов, `chown` на uid приложения
   и `restorecon` (SELinux-контекст);

Отключить удаление `.new`: `--keep-new` (не рекомендуется).

## 6. Первый запуск — важное

1. **Сеть**: при логине игра может подтянуть облачный сейв и перезалить
   локальные правки. Первый запуск — с **выключенной сетью** (авиарежим):
   локальный сэйв загрузится, игра сохранится локально. Дальше либо играть
   оффлайн, либо править и облако (путь proven — CLOUD-DUMP-870.md), либо
   принять, что синк заберёт локалку как более новую (`upload_save_count`).
2. Если что-то пошло не так — откат: `*.bak_*` рядом с файлами на ПК,
   tar-бэкап на устройстве (команда отката печатается при push).

## 7. Значения «на долго» — шпаргалка

| Что | Значение | Где |
|---|---|---|
| Алмазы | 50 000 | game.data + prefs |
| Рыбные чипы | 20 000 | game.data |
| Монеты сезона | 30 000 | season_data |
| Герои | все, уровень 8 | game.data + prefs |
| Скиллы | все = 1 | prefs |
| Питомцы | все | game.data |
| Материалы | 499 | item_data |
| Семена | 99 | item_data |
| Комната | уровень 9 | prefs (+game.data roomLevel) |
| Карта возрождения | да (10) | game.data + prefs |

(опц.) консистентность покупок: `mall_reload_data.data buyGemCount800/buyGemCount5000…`
— счётчики купленных паков; можно поднять, чтобы 50k алмазов выглядели
«заработанными» (паки 800/5000/12000/25000/35000/76000/115000):

```bat
python tools\sk_save_editor.py write .\profile\files\mall_reload_data.data buyGemCount5000 10 --yes
python tools\sk_save_editor.py write .\profile\files\mall_reload_data_235288966_.data buyGemCount5000 10 --yes
```

## 8. Troubleshooting (LDPlayer/Windows)

| Симптом | Причина / что делать |
|---|---|
| `can't open file ... sk_save_editor.py` | ты не в каталоге с тулзой. Скрипты лежат в репо (`tools\`); запускай из корня репо или укажи полный путь |
| `adb: устройство не найдено` | `D:\LDPlayer\LDPlayer14\adb.exe devices` — пусто? Выполни `adb connect 127.0.0.1:5554` (порт — из заголовка окна эмулятора: 5554/5555/5556…) |
| `нет root` | В настройках LDPlayer включи Root-права; проверка: `adb shell su -c id` → `uid=0(root)` (у тебя уже включён — ок) |
| push падал с пустым `[!] adb push ...:` | Старый баг v1.0.0: `os.path.join` на Windows ставил `\` в путь НА УСТРОЙСТВЕ, и старый adb молча падал. Исправлено в v1.0.1: путь с прямыми слэшами + fallback base64-чанками + внятный отчёт об ошибке (rc/stdout). Перескачай `sk_save_editor.py` и повтори |
| push пишет `base64-заливка: N%` | Это не ошибка: adb push не сработал (частое поведение старых adb LDPlayer), тулза автоматически льёт tar base64-чанками — медленнее, но так же надёжно |
| pull отдал только 2–3 файла | Это старый баг tar-потока (исправлен в этой версии): перескачай `sk_save_editor.py` из репо и повтори; при повторении — `--debug` |
| `adb pull /data/data/...` даёт `0 files pulled` | Норма: shell-пользователь не имеет доступа к `/data/data` — нужен root-pull (наш скрипт) |
| `find: bad arg '...'` в ручных командах | Кавычки съедаются PowerShell/adb при передаче глобов (`*data*`); в скрипте глобов нет — используй его |
| playerprefs «UTF-8/ASCII» в `info`, а раньше был UTF-16 | На устройстве файл UTF-8; UTF-16LE+BOM — артефакт PowerShell-редиректа при ручном `adb shell ... > file`. Обе кодировки теперь читаются автоматически |
| После push правки слетели | (а) игра была запущена при push — повтори с `--kill`; (б) игра подтянула облако — первый запуск офлайн (§6); (в) вручную удали `*.data.new`: `adb shell "su -c 'rm /data/data/com.ChillyRoom.DungeonShooter/files/*.data.new'"` и перезалей |
