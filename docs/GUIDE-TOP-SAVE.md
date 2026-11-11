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

Забирает `files/*.data` + `shared_prefs/*.playerprefs.xml`, печатает
`cloudSaveId`. Результат: каталог `.\profile` — рабочая копия профиля.

## 2. Осмотреться

```bat
python tools\sk_save_editor.py list .\profile
python tools\sk_save_editor.py preset .\profile\game.data currencies
python tools\sk_prefs.py info .\profile\com.ChillyRoom.DungeonShooter.v2.playerprefs.xml
```

## 3. Патчим `.data` (пресеты редактора)

«Надолго, но не горы» (каждая правка показывает диф и пишет бэкап;
`--yes` — без вопросов):

```bat
:: алмазы: gems = lastGems = N, varGems = 0, cumulativeGems += — леджер согласован
python tools\sk_save_editor.py preset .\profile\game.data gems 50000 --yes

:: рыбные чипы и монеты сезона
python tools\sk_save_editor.py preset .\profile\game.data fish-chips 20000 --yes
python tools\sk_save_editor.py preset .\profile\season_data.data season-coins 30000 --yes

:: герои, скиллы, питомцы
python tools\sk_save_editor.py preset .\profile\game.data unlock-heroes --yes
python tools\sk_save_editor.py preset .\profile\game.data heroes-level 8 --yes
python tools\sk_save_editor.py preset .\profile\game.data unlock-pets --yes

:: крафт: все чертежи + материалы/семена (только те, что уже есть в сейве)
python tools\sk_save_editor.py preset .\profile\item_data.data research-all --yes
python tools\sk_save_editor.py preset .\profile\item_data.data all-materials 499 --yes
python tools\sk_save_editor.py preset .\profile\item_data.data all-seeds 99 --yes

:: карта возрождения (gameData.rebornCard + restoreKeys)
python tools\sk_save_editor.py preset .\profile\game.data reborn-card --yes

:: проверить итог
python tools\sk_save_editor.py preset .\profile\game.data currencies
```

Справка по всем пресетам: `python tools\sk_save_editor.py presets`.

## 4. Патчим prefs.xml (вторая половина сэйва)

```bat
set PREFS=.\profile\com.ChillyRoom.DungeonShooter.v2.playerprefs.xml

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
  `python tools\sk_save_editor.py write .\profile\game.data roomLevel 9 --yes`
* Не трогай: `cloudSaveId*`, `Sdk*Cache*`, `UsedAccounts*`, `unity.*`,
  `has_*_data` (при живых `.data`-файлах).

## 5. Положить обратно

```bat
python tools\sk_save_editor.py push .\profile --kill --adb "D:\LDPlayer\LDPlayer14\adb.exe"
```

Push делает страховочный tar текущего профиля на устройстве
(`/data/local/tmp/sk_pre_backup_<ts>.tar`), заливает каталог (включая prefs),
восстанавливает владельца файлов.

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
python tools\sk_save_editor.py write .\profile\mall_reload_data.data buyGemCount5000 10 --yes
```
