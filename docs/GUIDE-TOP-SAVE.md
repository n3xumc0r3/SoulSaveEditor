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
>
> **С версии 1.1.0**: если аккаунт ПУСТОЙ (секция пуста и там, и там — на свежем
> аккаунте материалов не бывает ВООБЩЕ), масс-пресеты править нечего — используй
> синтез `grant-materials` / `grant-seeds` / `grant-blueprints` / `grant-all`:
> они СОЗДАЮТ записи из каталога реальных id 8.7.0 (источники — itemData
> облачного дампа и литералы global-metadata.dat; ни один id не выдуман).

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

:: крафт: все чертежи + материалы/семена (только те, что уже есть в сейве)
python tools\sk_save_editor.py preset .\profile\files\item_data.data research-all --yes
python tools\sk_save_editor.py preset .\profile\files\item_data.data all-materials 499 --yes
python tools\sk_save_editor.py preset .\profile\files\item_data.data all-seeds 99 --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data research-all --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data all-materials 499 --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data all-seeds 99 --yes

:: ПУСТОЙ аккаунт (материалов/семян/чертежей нет ВООБЩЕ — править нечего):
:: синтез из каталога реальных id 8.7.0 (облачный дамп + метадата игры; игра
:: игнорирует неизвестные id, так что попытка безобидна). Создаёт недостающие записи:
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data grant-all 499 --yes
:: или по частям: grant-materials 499 / grant-seeds 99 / grant-blueprints
:: свой список id: --ids-file мой.json (формат — см. `presets`)

:: карта возрождения (gameData.rebornCard + restoreKeys)
python tools\sk_save_editor.py preset .\profile\files\game.data reborn-card --yes

:: проверить итог
python tools\sk_save_editor.py preset .\profile\files\game.data currencies
```

### 3.1. Полный набор для ПУСТОГО аккаунта (v1.3.0) — строго в этом порядке

Уроки боевых прогонов 12.11–13.11:

1. **Сейв = две половины** (`.data` + playerprefs). Уровни героев и скины игра
   держит ещё и в prefs (`<UID>_cN_level`, `<UID>_cN_skinN`) — патч только
   `game.data` даёт «все герои 1 уровня, скинов 0». **Обе тулзы нужно скачать
   заново** (v1.3.0): в старом sk_prefs.py нет `unlock-levels`/`unlock-skins`.
2. **Rij-канал**: если `setting.data UseRijData=true` (или в prefs
   `OpenRijTest_<UID>=1`), секции Rij-группы (item_data, monsrise, misc, pvp,
   task, bp, mall_reload) читаются/пишутся через Rij-путь — правки обычных
   `.data` ИГНОРИРУЮТСЯ («материалов 0» при заённых 499). Лечение — `rij-legacy`.
3. **Копии секции**: правь обе сразу флагом `--all-copies` (легаси-`item_data.data`
   + UID-`item_data_235288966_.data`) — какая из копий читается этой сборкой,
   заранее не знает никто; `doctor` покажет расхождение.
4. **Счётчики статистики — словари по РЕЖИМАМ** (`Any`/`Normal`/`Season`),
   а не по героям: формат снят с топ-сейва (`_enterDungeonTimes
   {"Any":123,"Normal":107,...}`). Пресет v1.3.0 `dungeon-times` пишет правильные
   ключи (в 1.2.0 формат был неверный — гейты «сыграй 3 раза» не срабатывали).
5. **Привязанность питомцев** лежит в `statistic.data` → `event2Count`
   (`pet_5_intimacy=240`, `pet_5_full_intimacy=1`, `pet_5_action_unlock=1`;
   `pet_full_intimacy` = число питомцев с полным уровнем). Пресет `pets-intimacy`.

```bat
:: 0) СВЕЖИЙ pull (профиль на диске мог устареть после последнего запуска игры)
python tools\sk_save_editor.py pull -o .\profile --kill --adb "D:\LDPlayer\LDPlayer14\adb.exe"

:: 1) Rij-канал ВЫКЛ (включи заново никогда не нужно; monsrise/misc сбросятся в дефолт)
python tools\sk_save_editor.py preset .\profile\files\setting.data rij-legacy --yes

:: 2) предметы в ОБЕ копии item_data (--all-copies)
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data grant-all 499 --all-copies --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data fish-total 1999 --all-copies --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data grant-decor --all-copies --yes
python tools\sk_save_editor.py preset .\profile\files\item_data_235288966_.data item-unlock --all-copies --yes

:: 3) герои/скиллы/скины/питомцы/зал в game.data (создаёт записи с нуля)
python tools\sk_save_editor.py preset .\profile\files\game.data unlock-heroes --yes
python tools\sk_save_editor.py preset .\profile\files\game.data grant-heroes-levels 8 --yes
python tools\sk_save_editor.py preset .\profile\files\game.data grant-skill-unlock --yes
python tools\sk_save_editor.py preset .\profile\files\game.data grant-skins --yes
python tools\sk_save_editor.py preset .\profile\files\game.data grant-pets --yes
python tools\sk_save_editor.py preset .\profile\files\game.data grant-room-objects 5 --yes
python tools\sk_save_editor.py preset .\profile\files\game.data gems 50000 --yes
python tools\sk_save_editor.py preset .\profile\files\game.data fish-chips 1999 --yes
python tools\sk_save_editor.py preset .\profile\files\game.data reborn-card --yes

:: 4) статистика: «сыграй в режиме уровня» + привязанность питомцев (эксперимент)
python tools\sk_save_editor.py preset .\profile\files\statistic.data dungeon-times 5 --yes
python tools\sk_save_editor.py preset .\profile\files\statistic.data pets-intimacy 300 --yes
python tools\sk_save_editor.py preset .\profile\files\game.data room-unlock --yes

:: 5) эволюция оружия: фрагменты (UpdateMaterialCount) + скины (эксперимент; N SKINS)
python tools\sk_save_editor.py preset .\profile\files\weapon_evolution_data_235288966_.data weapon-evol 999 10 --all-copies --yes

:: 6) сезон «защитники» (Troop2): скиллы отряда + сложность (эксперимент)
python tools\sk_save_editor.py preset .\profile\files\season_data_235288966_.data troop2-open --all-copies --yes
```

**Про «защитников» (наемников Troop2)**: карточки закрываются «покупкой» в
магазине режима — список купленных лежит в `seasonData.troop2BoughtMercenary`.
На свежем аккаунте он `{}`, а id наемников нигде в дампах не видны — их НЕЛЬЗЯ
выдумывать. План: зайди в режим один раз, купи ЛЮБОГО наемника за монеты
сезона (они уже выданы), закрой игру, pull — и пришли мне вывод
`python tools\sk_save_editor.py dump .\profile\files\season_data_235288966_.data`
(только поле troop2BoughtMercenary). По реальным id дополню `troop2-open`
(`--ids-file файл.json` с `{"troop2_mercenaries": [...]}` уже поддержан).

**Про облики оружия**: модель записи снята с облачного DTO (`WUWeapon`:
Name/Level/CurrentSkinId/UnlockedSkins/ModuleSlots/UpdateMaterialCount),
каталог 353 реальных id weapon_NNN — из топ-сейва. Структура локального
`weapons` (список/словарь) угадывается по обоим вариантам — если после
weapon-evol игра падает на загрузке, сделай `rescue` и пришли мне вывод
`dump` твоего weapon_evolution_data — подгоню формат по факту.

### 3.2. prefs-половина (v1.2.0: уровни и скины героев тоже здесь)

```bat
set PREFS=.\profile\shared_prefs\com.ChillyRoom.DungeonShooter.v2.playerprefs.xml

python tools\sk_prefs.py gems %PREFS% 50000 --yes
python tools\sk_prefs.py unlock-heroes %PREFS% --extend --yes
python tools\sk_prefs.py unlock-levels 8 %PREFS% --extend --yes
python tools\sk_prefs.py unlock-skins %PREFS% --yes
python tools\sk_prefs.py unlock-skills %PREFS% --yes
python tools\sk_prefs.py unlock-plants %PREFS% --extend --yes
python tools\sk_prefs.py rij-off %PREFS% --yes
python tools\sk_prefs.py set %PREFS% 235288966_room_level 9 --mirror --yes
```

Разведка (если что-то всё ещё закрыто — защитники/питомцы-уровни/оружейные скины):
`python tools\sk_prefs.py find %PREFS% follower` (и `pet`, `skin`, `stand`) —
поиск по ключам и декодированным значениям; вывод — в репорт.

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

Push (v1.1.0) делает:

1. страховочный tar текущего профиля на устройстве
   (`/data/local/tmp/sk_pre_backup_<ts>.tar`);
2. определяет uid приложения ДО распаковки и **вшивает владельца/права Android
   в члены tar** (uid приложения, каталоги 771, файлы 660) — раньше tar с
   Windows нёс uid=0/0666/0777, root-распаковка делала файлы сейва root:root
   (в логе выглядело как «владелец файлов восстановлен: uid 0»), из-за чего
   игра могла зависать на 100% при попытке писать сейв;
3. локальный tar профиля — **без** `.bak_*`, `.data.new` и мусора Windows
   (`desktop.ini`/`Thumbs.db`);
4. доставку tar: `adb push` + контроль размера на устройстве; если push
   не сработал (старые adb LDPlayer молча падают на путях) — автоматически
   заливает base64-чанками через `exec-out` (тот же канал, что у pull,
   бинарно-безопасно; путь назначения — только прямые слэши, старый баг
   `os.path.join` с `\` в имени удалён);
5. распаковку в `/data/data/<pkg>`, **полную md5-сверку КАЖДОГО залитого файла**
   с локальным (раньше сверялись размеры только двух), удаление временных
   `*.data.new` на устройстве (иначе первый запуск может откатить ими свежие
   `.data`), `chown`/`chmod`/`restorecon` как страховку к вшитым правам.

Отключить удаление `.new`: `--keep-new` (не рекомендуется).

### 5.1. Если игра после push висит на 100%

Сейв при этом обычно ЦЕЛ (игра просто не доходит до загрузки/сброса). Порядок:

```bat
:: 1) откатить устройство на страховочный tar (список + выбор, владелец чинится сам)
python tools\sk_save_editor.py rescue --adb "D:\LDPlayer\LDPlayer14\adb.exe"

:: 2) запустить игру ОФЛАЙН (авиарежим/сеть эмулятора выключена).
::    Если на откоченном состоянии игра грузится — причина в правках:
::    применяй пресеты по одному и push после каждого.

:: 3) если висит ДАЖЕ на откоченном — проблема в окружении, а не в сейве:
::    сеть/Google-аккаунт (GPGS ждёт сеть на старте), запуск НЕ в офлайне.
::    Диагностика (владелец файлов, .aes/.new, md5-сверка с профилем, флаги UseRij):
python tools\sk_save_editor.py doctor -p .\profile --adb "D:\LDPlayer\LDPlayer14\adb.exe"
```

Норма: первый запуск после push — только офлайн; сеть включать после того,
как убедились, что локальный сейв загрузился.

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
| Рыбные чипы | 20 000 | game.data (+ fish-total в item_data — подарочная дорожка) |
| Монеты сезона | 30 000 | season_data |
| Герои | все, уровень 8 | game.data + prefs (обязательно!) |
| Скиллы | все = 1 | prefs |
| Скины героев | все Value=1 | game.data + prefs |
| Питомцы | все | game.data |
| Привязанность питомцев | 300 (полная) + action_unlock | statistic.data `pets-intimacy` |
| «Сыграй в режиме уровня N раз» | 5 | statistic.data `dungeon-times` |
| Фрагменты эволюции оружия | 999 на оружие | weapon_evolution_data `weapon-evol` |
| Материалы (вкл. осколки древнего оружия) | 499 | item_data grant-all |
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
| Игра висит на 100% после push | см. §5.1: `rescue` (откат на pre-backup) -> запуск офлайн -> если и на откате висит — окружение (сеть/GPGS), а не сейв; `doctor -p <профиль>` покажет владельца файлов и md5-сверку |
| В логе push было «владелец файлов восстановлен: uid 0» | Баг v1.0.1 (uid снимался с каталога ПОСЛЕ распаковки, когда тот уже стал root). Исправлено в v1.1.0: uid берётся до распаковки и вшивается в tar; вылечить старый прогон — `rescue` или повторный `push` новой версией |
| playerprefs «UTF-8/ASCII» в `info`, а раньше был UTF-16 | На устройстве файл UTF-8; UTF-16LE+BOM — артефакт PowerShell-редиректа при ручном `adb shell ... > file`. Обе кодировки теперь читаются автоматически |
| После push правки слетели | (а) игра была запущена при push — повтори с `--kill`; (б) игра подтянула облако — первый запуск офлайн (§6); (в) вручную удали `*.data.new`: `adb shell "su -c 'rm /data/data/com.ChillyRoom.DungeonShooter/files/*.data.new'"` и перезалей |
| Материалы/рыба в item_data не появились в игре (хоть push сошёлся md5) | Классика 12.11: Rij-канал читает item_data мимо наших файлов. `doctor -p .\profile` покажет `UseRijData` и содержимое КАЖДОЙ копии прямо на устройстве. Лечение: `preset setting.data rij-legacy` + `sk_prefs.py rij-off` + пресеты item_data с `--all-copies` (§3.1) |
| Герои открыты, но все 1 уровня; скинов 0 | Патчили только game.data — уровни и скины живут ещё и в prefs (`cN_level`, `cN_skinN`). Прогони §3.2: `unlock-levels 8 --extend` + `unlock-skins` + `grant-heroes-levels 8` + `grant-skins`. ВАЖНО: если в твоём sk_prefs.py нет `unlock-levels` — файл старый, скачай v1.3.0 заново (это и была причина «уровней 1» в прогоне 12.11) |
| «Сыграй в режиме уровня» не засчитывается | В 1.2.0 `dungeon-times` писал счётчики по героям — а игра читает словари по РЕЖИМАМ (Any/Normal). Обнови sk_save_editor.py до v1.3.0 и повтори |
| Привязанность питомцев 0 | Она в statistic.data → event2Count (`pet_N_intimacy/full_intimacy/action_unlock`), не в game.data/prefs. v1.3.0: `preset statistic.data pets-intimacy 300 --yes` |
| «Защитники» (Troop2) закрыты | Скиллы/сложность/отряд — `preset season_data_*_.data troop2-open --all-copies --yes`. Самих наемников пока только покупать за монеты сезона (id собираем с устройства — см. §3.1) |
| doctor: «копии item_data РАСХОДЯТСЯ» | Правка легла только в одну копию секции — игра может читать другую. Все item_data-пресеты запускай с `--all-copies` |
