# PREFS-870 — playerprefs: вторая половина локального сэйва (реальное устройство, 12.11.2026)

Источник: `shared_prefs/com.ChillyRoom.DungeonShooter.v2.playerprefs.xml`, снят
root-pull'ом с реального устройства 8.7.0 (аккаунт `235288966`). Это ответ на
«задачу №1 для эмулятора» из REVERSING.md §3.1.

## 1. TL;DR

* Локальный сэйв состоит из **двух половин**: секционные `.data`-файлы в `files/`
  (см. LOCAL-SAVE-870.md) и **playerprefs** — 2 105 ключей в 183 паттернах.
* Данные хранятся в **двух неймспейсах**: `<accountId>_*` (1 098 ключей) и
  зеркала `LOCAL*` (≈1:1, 952/954 значений совпадают; расходятся только живые
  счётчики `*_enter_game_count_today` и `*local_season_box_factor`).
* **Регистр bool различается по неймспейсам** (критично при правках):
  `<UID>_*` → `"true"/"false"`, `LOCAL*` → `"True"/"False"` (C# `bool.ToString()`).
* Имена ключей **и** строковые значения URL-экранируются (`%23`→`#`,
  `%2F`→`/`, `%7B…`→JSON, `%2C`→запятая).
* Леджер алмазов дублируется в prefs (`<UID>_gems/_last_gems/_var_gems/
  _cumulative_gems/_cumulative_consume_gems`) — при правке гемов синхронизировать
  **и** prefs, **и** `game.data` (поля `gems/lastGems/varGems/cumulativeGems/
  cumulativeConsumeGems` класса AboGameData).
* **Per-install Rijndael-ключи в playerprefs ОТСУТСТВУЮТ** — при активном
  `OpenRijTest_<UID>=1` ключей `DataRijndael128Key`/`DataRijndaelIV`/
  `DataAES256Key` в файле нет. Гипотеза «ключи в playerprefs» опровергнута;
  источник ключа — открытый вопрос (серверный конфиг канала `SupportRijChannel`,
  константа в коде рядом с `rij_decrypt`, либо отдельный файл).

## 2. Файл и формат

| Свойство | Значение |
|---|---|
| Кодировка | UTF-16LE, BOM `FF FE` |
| Корень | Android-формат: `<?xml version='1.0' encoding='utf-8' standalone='yes' ?><map>…</map>` |
| Элементы | `<int name="K" value="V" />`, `<string name="K">V</string>` (на устройстве: int=1857, string=248; long/boolean/float не встречены) |
| Размер | 231 540 Б на 2 105 ключей |
| Служебные | `__UNITY_PLAYERPREFS_VERSION__`, `unity.cloud_userid`, `unity.player_session*`, `Screenmanager%2F…` |
| Парсер | Unity PlayerPrefs (через Android SharedPreferencesImpl); отсутствующий ключ читается дефолтом вызывающего кода (int→0, string→пусто/null) |

## 3. Два неймспейса и зеркала

| Неймспейс | Пример | Роль |
|---|---|---|
| `<UID>_*` | `235288966_c0_unlock = "true"` | привязан к аккаунту ChillyRoom SDK (Id из `SdkUserCacheV2#1`) |
| `LOCAL*` | `LOCALc0_unlock = "True"` | локальное зеркало (оффлайн-профиль до логина / после логаута) |

* Зеркальные пары покрывают героев (`cN_unlock/cN_level/cN_skinN`), растения
  (`pN_unlock`), флаги данных (`has_*_data`), леджер (`last_gems`), прочее.
* Скиллы (`c_<HeroName>_skill_N_unlock`) существуют **только** в `<UID>_`.
* Правило редактора: **править оба неймспейса** (`sk_prefs.py set --mirror`,
  `gems`, `unlock-heroes` делают это сами, с сохранением регистра bool).

## 4. Каталог ключей (по факту устройства)

### 4.1 Герои

| Паттерн | Тип | Наблюдения |
|---|---|---|
| `<UID>_cN_unlock` / `LOCALcN_unlock` | string bool | N=0..41 (42 героя); `"true"` только у стартового КНайта, остальные `"false"` |
| `<UID>_cN_level` / `LOCALcN_level` | int | все 0 на свежем аккаунте |
| `<UID>_cN_skinN` / `LOCALcN_skinN` | int | 792 пары; значения: `0`(438), `-13`(292), `-6`(214), `12000`(114), `1`(86), `-5`,`-10`,`-8`,`-1`, `2000..18000`. **Семантика кодов не подтверждена — не выдумывать**; положительные круглые похожи на цену/штамп получения, отрицательные — на статус-коды |
| `<UID>_c_<HeroName>_skill_N_unlock` | int 0/1 | только `<UID>_`; имена героев литералами: Knight, Alchemist, Airbender, ArcaneKnight, Assassin, Astrologist, Bard, Beheaded, Captain, CostumePrince, Doctor, Druid, Elves, Engineer, Envoy, Fighter, GunsExpert, Joker, Lancer, Mage, Miner, Necromancer, Ninja, Officer, Paladin, Priest, Ranger, Robot, Shooter, SpecialForces, SwordMaster, Taoist, Transcendent, TrapMaster, Vampire, Viking, Warliege, Warlock, Werewolf, YinYang, AiGirl; count 1..3 по числу скиллов |

### 4.2 Валюты и леджер (зеркало AboGameData)

| Ключ | Значение на устройстве | Комментарий |
|---|---|---|
| `<UID>_gems`, `_last_gems`, `_var_gems` | 0/0/0 | кэш `gameData.gems/lastGems/varGems` из `game.data` |
| `<UID>_cumulative_gems` | 0 | `cumulativeGems` |
| `<UID>_cumulative_consume_gems` | 0 | `cumulativeConsumeGems` |
| `<UID>_reborn_card` | 0 | карты возрождения |
| `<UID>_magic_stone_index` | 0 | магический камень |
| `LOCALlast_gems` | 0 | зеркало без `_gems`-пары |

Правка гемов: `sk_prefs.py gems prefs.xml N` выставляет
`gems=last_gems=N, var_gems=0, cumulative_gems=max(old,N), LOCALlast_gems=N`
и напоминает синхронизировать `game.data` через `sk_save_editor.py`.

### 4.3 Растения, комната, мебель

| Паттерн | Тип | Наблюдения |
|---|---|---|
| `<UID>_pN_unlock` / `LOCALpN_unlock` | string bool | N=1..58 (58 растений; `true` у p2?) |
| `<UID>_room_level` | int | уровень гостиной |
| `<UID>_furniture_book_level`, `_furniture_chest`, `_furniture_fish_chips`, `_furniture_pet_food_level`, `_furniture_plant_level`, `_furniture_safe_level` | int | 0 на свежем аккаунте |
| `<UID>_is_wave_machine_upgraded` | string | |

### 4.4 Флаги секций `.data` и синхронизация

`<UID>_has_<X>_data` + `LOCALhas_<X>_data` (int 0/1) — по одному на каждую
секцию: `item, misc, season, pvp, statistic, bp, mall_reload, escape_season,
monsrise, weapon_evolution` (+ `tutorial`, `has_save_date` string).
На устройстве все = 1. Семантика: маркер «секция когда-либо сохранялась» —
при создании секции с нуля выставлять флаг вместе с файлом; при удалении
`.data`-файла без сброса флага игра может упасть на чтении (проверено только
частично — см. RE-NOTES чеклист).

### 4.5 Идентификаторы и счётчики

| Ключ | Значение | Смысл |
|---|---|---|
| `cloudSaveId`, `<UID>_cloudSaveId` | `GPV2_235288966` | неймспейс облачных/локальных данных (GPV2 = поколение синка) |
| `_movedLocalDataIds_` | `GPV2_235288966%2C` | список мигрированных id (URL-экранированная запятая) |
| `last_chilly_uid`, `last_account_id` | `235288966` | SDK Id |
| `LOCALlast_pid` | `282193169` | PlayerId (≠ SDK Id!) |
| `<UID>_download_save_count` / `_upload_save_count` | 2 / 1 | счётчики синков |
| `<UID>_game_times`, `_total_game_time`, `_last_play_time(_with_network_time)`, `_first_play`, `_first_in_room`, `_last_day_index` | 0/1/unix | статистика сессий |
| `<UID>_general_random_seed` | int | сид RNG комнаты |
| `modpkg_8.7.0_555535796` | 2 | версия мод-пака (суффикс — билд-номер) |
| `total_enter_game_count_today` | `9811_2` | формат `<общий>_<сегодняшний>` (наблюдение) |
| `ReturnPlayerDistroFixMigrated_24_235288966_` | 1 | миграция «вернувшегося игрока» (24 — канал) |
| `AccountSaveSettingsIds` | `%7B"235288966":"235288966"%7D` | маппинг аккаунт→профиль |

### 4.6 Канальные флаги крипто/JSON (v29-ветка)

| Ключ | Значение | Смысл |
|---|---|---|
| `OpenRijTest_235288966` | **1** | per-account раскат Rijndael-канала ВКЛЮЧЁН |
| `OpenNewtonJsonTest_235288966` | 1 | канал Newtonsoft.Json включён |
| `RijUserAutoOpenJsonTestIfSupport`, `SupportRijChannel`, `SupportUseNewtonJsonChannel` | — | серверные флаги канала (методы SaveUtil; в prefs отсутствуют) |
| **`DataRijndael128Key` / `DataRijndaelIV` / `DataAES256Key`** | **ОТСУТСТВУЮТ** | ключи не хранятся в playerprefs — см. §7 |

### 4.7 SDK-блобы (URL-экранированные JSON в значениях)

| Ключ (raw → decoded) | Содержимое |
|---|---|
| `SdkUserCacheV2%231` → `SdkUserCacheV2#1` | аккаунт: `Id`, `CreatedAt`, `PlayerId`, `NativeUserInfo.type="Guest"` + guest-uuid, `LegacyGateway{host: legacy-save.soulknight.chillyroom.com, token: a1a3…bbb4}`, `BetaAccess`, `Banned`, `RemainingBanTime`, `IsNewPlayer` |
| `SdkSessionCacheV2%231` → `SdkSessionCacheV2#1` | сессия: `Token` (совпадает с LegacyGateway.token), `Expire` (+3 дня), `LoginBy:"Guest"` |
| `<UID>_active_shop_task_cache` | магазин: `PeriodSequence`, `Snapshot{balance, dailyActivity, weeklyActivity, utcOffsetMinutes:180, dailyPeriod.nextResetAtUtc, rewardTiers[{configId, rankRequire, rewards[{itemType, itemId:"material_gem", count}]}]}`, хэши определений |
| `<UID>_RedDotTree_*` | состояние красных точек UI (пути канвасов) |
| `<UID>_MallJumpTrackingData` | `savedVersion:"8.7.0"`, `trackingRecords[]`, `conditionProgressList[]` |
| `UsedAccounts%231` → `UsedAccounts#1` | `[ null ]` |
| `UsedAccountsHistory` | `{"MaxCapacity":10,"_list":["235288966",…]}` — Odin-стиль CircularBuffer |
| `GUEST_ID%235ab1…%231` → `GUEST_ID#5ab1…#1` | guest-uuid (совпадает с NativeUserInfo.id) |
| `ChillyRoom%2FServices%2FSKD…` | `ChillyRoom/Services/SDK/-/IN/Language` — ключ с литеральным `/` после экранирования |

**Токены в блобах живые — в репо не коммитить и в логи не печатать.**
Для правок безопасно: это кэш сессии, игра перекачает/перепишет; удалять не нужно.

## 5. URL-экранирование имён и значений

Игра экранирует имена ключей и строковые значения (Unity `EscapeURL`-стиль):
`#`→`%23`, `/`→`%2F`, `,`→`%2C`, `{`→`%7B`, пробел→`%20`. Следствия:

* `sk_prefs.py` декодирует значения автоматически (`dump`/`blobs` показывают
  `decoded`), при записи экранировать **не нужно** — правится сырая строка.
* Не «чинить» ключи вида `SdkUserCacheV2%231` — это канонические имена.

## 6. Поведение при отсутствующих ключах

* Чтение отсутствующего pref-ключа возвращает дефолт вызывающего кода
  (`GetInt(key, 0)`, `GetString(key, null)` …) — игра НЕ падает, а считает
  фичу «не открытой»/«нулевой». Это тот же класс поведения, что и у JSON-полей
  секций (LOCAL-SAVE-870.md §6): **отсутствие ≠ ноль, отсутствие = дефолт**.
* Поэтому: добавить герою `cN_unlock=true` можно, а «убрать» героя надёжнее
  (`"false"`), чем удалять ключ — код может кэшировать значение между запусками.
* `has_*_data=0`/отсутствие при живом `.data`-файле — секция будет
  перезаписана с нуля при первом сохранении.

## 7. Rijndael-ключи: закрытая задача №1 и новый открытый вопрос

**Закрыто (12.11.2026):** ключей `DataRijndael128Key`/`DataRijndaelIV`/
`DataAES256Key` в playerprefs НЕТ, при том что канал включён
(`OpenRijTest_<UID>=1`), JSON-канал включён (`OpenNewtonJsonTest_<UID>=1`) и
на диске лежит нерасшифровываемый легаси-схемами `monsrise_data_*.data.new`.

**Открытый вопрос — где живёт per-install ключ:**

1. **Серверный конфиг канала** (`SupportRijChannel` уже оттуда): ключ может
   приходить в dynamic-config вместе с флагом — тогда он общий на канал, а не
   per-install. Проверка: MITM динамического конфига при старте игры.
2. **Константа в коде**: рядом с литералами `rij_decrypt`/`.rij` в
   `libnative-lib.so` (нативка снята в REVERSING.md §4 — но `rij_decrypt`
   дизассемблирован не был).
3. **Отдельный файл** в `files/` (рядом с `.data.new`).

Редактор (sk_save_editor.py) продолжает мульти-догадку (raw/b64 × CBC/ECB ×
b64/hex/utf8/sha256 ключей), плюс фиксированные легаси-ключи; подтверждение
схемы — за первым живым `.aes`/`.data.new` + знанием источника ключа.

## 8. Кулинарная книга

```bash
# Снять prefs с устройства (root):
adb exec-out "su -c 'cat /data/data/com.ChillyRoom.DungeonShooter/shared_prefs/com.ChillyRoom.DungeonShooter.v2.playerprefs.xml'" > prefs.xml

python3 tools/sk_prefs.py info prefs.xml          # сводка: UID, леджер, флаги, Rij-ключи
python3 tools/sk_prefs.py schema prefs.xml        # 183 паттерна с частотами
python3 tools/sk_prefs.py mirror prefs.xml        # сверка LOCAL* vs <UID>_*
python3 tools/sk_prefs.py blobs prefs.xml         # декод URL-блобов (токены маскировать!)
python3 tools/sk_prefs.py get prefs.xml LOCALlast_gems

# Правки (хирургические, с бэкапом .bak_<ts> и round-trip-проверкой):
python3 tools/sk_prefs.py gems prefs.xml 50000 --yes
python3 tools/sk_prefs.py unlock-heroes prefs.xml --extend --yes
python3 tools/sk_prefs.py set prefs.xml 235288966_room_level 9 --mirror --yes
python3 tools/sk_prefs.py set prefs.xml 235288966_c_Airbender_skill_1_unlock 1 --yes

# Залить назад (игра закрыта):
adb push prefs.xml /sdcard/ && adb shell "su -c 'cp /sdcard/prefs.xml /data/data/com.ChillyRoom.DungeonShooter/shared_prefs/com.ChillyRoom.DungeonShooter.v2.playerprefs.xml'"
adb shell "su -c 'chown $(stat -c %u:%g /data/data/com.ChillyRoom.DungeonShooter/shared_prefs/) /data/data/com.ChillyRoom.DungeonShooter/shared_prefs/com.ChillyRoom.DungeonShooter.v2.playerprefs.xml'"
```

Правила безопасности правок:

1. **Игра закрыта** до и во время правки (кэш в памяти перезатрёт файл).
2. Править **оба неймспейса** (`--mirror`), сохраняя регистр bool.
3. Леджер гемов менять согласованно в prefs **и** game.data.
4. Не трогать: `cloudSaveId*`, `Sdk*Cache*`, `UsedAccounts*`, `unity.*`,
   `__UNITY_PLAYERPREFS_VERSION__`, `_movedLocalDataIds_`.
5. Кодировка при записи — UTF-16LE с BOM (sk_prefs.py сохраняет исходную).
6. Значения id не выдумывать: править только существующие ключи/диапазоны
   (для создания — `--extend` по реально виденным id).

## 9. Инструмент

`tools/sk_prefs.py` — команды: `info`, `schema`, `dump`, `blobs`, `get`,
`set [--mirror]`, `del [--mirror]`, `mirror`, `gems N`, `unlock-heroes
[--extend]`. Правка текстовая (файл не пересобирается), бэкап + верификация
парсом после каждой записи. Проверено на реальном prefs.xml с устройства:
82 героя × 2 неймспейса, леджер, round-trip байт-в-байт вне изменённых строк.
