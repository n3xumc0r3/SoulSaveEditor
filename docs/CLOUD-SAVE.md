# Облачная система сохранений Soul Knight 8.7.0 и система банов

> Реверс по декодированной метадате 8.7.0 (см. [REVERSING.md](REVERSING.md),
> [RE-NOTES.md](RE-NOTES.md)) + официальные FAQ ChillyRoom.
> Практический кейс: «старое сохранение относится к GPV2_235288940, его нельзя
> загрузить» после входа на новом устройстве (эмуляторе).

---

## 1. TL;DR — что показывают диалоги

Диалог 2 (скриншот 005900): «Требуется вход через Google Play Games; после
входа попробуем загрузить облачное сохранение, созданное **до версии 5.5.2**».
Это класс `ChannelMigrationManager` / легаси-агент: игра пытается вытащить
старый сейв из **Google Play Saved Games** (снапшоты) и перенести в новое
облако ChillyRoom.

Диалог 1 (скриншот 005931): «Не удалось загрузить сохранение: Старое
сохранение относится к **GPV2_235288940**, его нельзя загрузить» — это
проверка принадлежности saveId. Формат идентификатора — литерал `GPV2_{0}`:
номер = идентификатор аккаунта-владельца в момент загрузки. Текущая сессия
вошла под другой идентичностью (в игре отображается `UID:235288966`), поэтому
сервер/клиент отказывается отдавать чужой сейв. В коде это семантика
`cloudsave/upload_fail_different_account`; в официальном FAQ ChillyRoom это
пункт «存档不属于此账号» («сейв не принадлежит этому аккаунту»).

**Итог: это не бан.** Аккаунт на месте, просто текущий вход разрешился в
другую учётку (новый UID), а старый сейв остался привязан к старой.

## 2. Архитектура облачных сохранений (по метадате 8.7.0)

### 2.1 Два поколения облака

| Поколение | Где хранится | Классы | Когда действует |
|---|---|---|---|
| Легаси (до 5.5.2) | **Google Play Saved Games** (снапшоты GPGS) | `GooglePlayLoginAndCloudSaveAgent` (`GetISavedGameMetadata`, `FetchCloudSaveData`, `SaveCloudSaveData`, конфликты снапшотов) | сейвы «сделанные до 5.5.2» |
| Актуальное | **Собственное облако ChillyRoom** (blob-хранилище) | `NewCloudSaveAgent` + `CloudSaveUtil` + `CloudPackage.V1.BackupClient` | после 5.5.2 |

При входе через Google игра **автоматически переносит** старые данные с
серверов Google на сервера ChillyRoom (официальный FAQ: «your cloud data
stored on Google will automatically migrate to ChillyRoom's server»).
Серверная миграция — `ChillyRoom.Services.Soulknight.MigrationClient`
(`OrdersAsync`, `SavesAsync`, `MigrationSavesRequest`).

### 2.2 Идентичности и каналы

`CloudSaveUtil` держит набор идентификаторов: `_uid`, `_chilly_uid`,
`channel_uid`, `_pf_id`, `_mac`, device id — и ключи `LOCAL_MOVED_KEY`,
`CLOUD_SAVE_KEY`. Идентификатор облачного сейва собирается по каналу:

* литералы `GPV2`, `GPV2_`, `GPV2_{0}`, `GPV2_Test`, `GPV2_Test_`;
  есть и `GPV4` (`hasClearLocalIdGPV4`, `hasClearNetIdGPV4`);
* соответствие каналов конкретной сборке — внешний конфиг
  **`/DefineCloudSaveID.json`** (качается с CDN; в APK его нет);
* метод `CloudSaveUtil.GetDefineCloudSaveChannelId` / `GetFixChannelName`.

Канал определяется тем, **откуда установлена игра** (Google Play → `GPV2_*`,
другие магазины → свои префиксы, Huawei/Harmony — отдельный путь,
`UnBindHarmonyInfoAsync`, `_fetchHarmonySaveIdSuc`). Отсюда официальное
правило: сейв можно загрузить только из клиента **того же канала**.

### 2.3 Пайплайн загрузки/выгрузки

Жизненный цикл — `RGScript.Other.NewCloudSaveAgent`:

```
Init → login (UserAuthService: GooglePlayLogin / Binding / Inspect)
     → FetchGameDataAsync (текущие данные аккаунта)
     → GetBlobSaveAsync   (сам сейв-блоб)
     → AfterFetchCloudSaveEvent → onLoadGameSuccess / onLoadGameFail /
       OnLoadGameTimeOutOrResponseNull → FixData → SaveGame (upload)
```

Транспорт — `ChillyRoom.SoulKnight.CloudPackage.V1.BackupClient`
(сгенерированный NSwg-клиент): `FetchGameDataAsync`, `GetBlobSaveAsync`,
`UnBindHarmonyInfoAsync`, gzip-сжатие, `GetRequestToken`/`ResetRequestToken`.
Хосты: `api-gateway.soulknight.chillyroom.com` (шлюз),
`accounts.chillyroom.com`, `aas.chillyroom.com`.

Защита от абьюза на клиенте:

* `GetCurrentAccountSaveId` vs `GetLocalSaveId` / `LocalSaveIdValid` —
  сверка принадлежности (наш диалог 1);
* `cloudsave/upload_fail_different_account`, `cloudsave/uid_account_cannot_change_account`;
* анти-откат: `cloudsave/local_cloudsave_fall_behind`,
  `cloudsave/net_cloudsave_fall_behind_local_cloudsave`,
  `cloudsave/local_more_payment_point_upload` (грузится только «более
  продвинутый» локальный сейв);
* `CheckAndTryUploadBrokenSaveData`, `cloud_save_fix_{0}_level_statistics_backup`
  (бэкап статистики уровней при фиксе), `CloudSaveClearLocalData`;
* гейм-сервисы вокруг: `ActiveShopClient`, `GameWorldClient`, `FriendClient`,
  `VersionCheckInClient` — аккаунт теперь живёт на сервере, а не только в сейве.

### 2.4 Миграция каналов (`ChannelMigrationManager`)

Полный флоу: `Init → ShouldShowEntry → StartProcess → ShowUploadGuideDialog →
StartUpload → NewCloudSaveAgent.SaveGame() → OnAfterUploadGame(Rpc) →
MoveData/FinishMoveData`, статусы этапов в PlayerPrefs
(`StageKey`, `GuidedKey`, `SaveUploadedKey`; литералы
`[ChannelMigration] StartUpload -> NewCloudSaveAgent.SaveGame()`,
`ChannelMove_SaveUploaded_{0}_{1}`, флаги `save_uploaded` с деградацией в
память). Это и есть диалог «загрузим сейв до 5.5.2» со скриншота 2.

## 3. Что случилось конкретно у тебя

1. Раньше: Google-вход → облачный сейв в канале GPV2 под владельцем
   `...940` (либо уже перенесён в облако ChillyRoom под этим saveId).
2. Пятница: установка на эмулятор + сетевая игра. Эмулятор = **новое
   устройство** (новый device id, вероятно другой/новый GPGS-профиль) →
   ChillyRoom-бэкенд выдал/разрешил новую идентичность `...966`.
3. Игра: «старый сейв принадлежит GPV2_235288940 — не твой, не грузим».

Косвенные причины смены идентичности (по официальному FAQ):
* ChillyRoom-аккаунт и Google-аккаунт — **разные сущности**, даже с одним
  ящиком; автосоздание при первом входе;
* на эмуляторе без сертифицированных GMS GPGS может отдать другой профиль;
* сборка не из того канала, что раньше (сейчас у тебя APKCube-сборка).

### Варианты возврата сейва (по официальному FAQ + реверсу)

1. **Тот же Google-профиль**: в Play Games сменить активный профиль на тот,
   что был при загрузке старого сейва, перезапустить игру.
2. **Прямой вход**: если старый сейв был на ChillyRoom-аккаунте (email+пароль)
   — войти им (экран «Учётная запись и облачное сохранение»).
3. **Поддержка ChillyRoom**: официальная схема — зайти с того устройства,
   с которым загружался сейв, открыть «Учётная запись и облачное сохранение»
   → человечек справа внизу → скриншот **кода устройства** → в поддержку.
   ⚠️ Код устройства на новом эмуляторе другой; если старое устройство
   недоступно/сброшено — официально восстановление невозможно.
4. Квоты на привязки (официально, важны для эмулятора): **5 привязок
   устройств на аккаунт за 90 дней**, между привязками ≥24 ч, **каждый вход
   с эмулятора сжигает привязку**, смена аккаунта на одном устройстве ≤1
   раза в 7 дней. Не дёргай входы — квоты кончатся.

## 4. Система банов и риск-контроль

### 4.1 Что видно в клиенте 8.7.0

| Компонент | Что делает |
|---|---|
| `ChillyRoom.Services.SafeCheckClient` | REST: **`/SafeCheck/Validate`**, **`/SafeCheck/Record?`**, **`/SafeCheck/DownloadResource`** через шлюз — серверная валидация окружения/поведения и сбор отчётов |
| `AntiCheatState` | enum `Checking` / `Punishment` — клиент знает про наказание |
| `SkillDemoAntiCheat` | локальная проверка (`OnCheck`, `Punish`) |
| `UIItemStationWindowDesc.ReportCheating`, `UIPvpSocialPanel._reportToggleCheat` | **жалобы игроков** на читеров в сетевых режимах |
| Лидерборды | сервер отдаёт флаг «是否被封禁» («забанен ли») по каждому игроку |
| `Err/Banned` | код серверной ошибки «аккаунт заблокирован» |
| `ChillyRoom.Idcheck.V1` (`ValidateRequest/Response`, `ValidateVN*`) | проверка личности/региона (real-name) |
| Tencent ACE (`libanogs.so`, `libanort.so`, `__acinfo.tsb`) | нативный античит-агент |

### 4.2 Официальная политика ChillyRoom

FAQ поддержки (chillyroom.com, раздел «为什么账号会被冻结?»):

> Система замораживает аккаунт при обнаружении **взлома или модификации
> сохранений**. Заморозка — **постоянная**, разбана нет, «очистки сейва»
> тоже не существует.

Плюс правила облачной системы: скачанный из облака сейв **нельзя** загрузить
под другим аккаунтом (проверка принадлежности), аккаунт↔устройство — квоты
из п.3.4, передача/продажа аккаунтов запрещена.

### 4.3 Матрица риска для пользователей SoulSaveEditor

| Действие | Риск | Комментарий |
|---|---|---|
| Правка локальных сейвов офлайн, без облака | минимальный технически | формально это «модификация сейва»; сервер видит только то, что ты выгрузишь |
| Выгрузка отредактированного сейва в облако | **высокий** | SafeCheck/анти-абьюз на выгрузке; официальная политика — перманентная заморозка |
| Сетевая игра с отредактированным сейвом | **высокий** | серверная валидация + репорты других игроков + флаг банов в рейтингах |
| Перенос сейва между СВОИМИ устройствами (IdStripper) | средний | ломает привязки; для облака официально нужен код устройства; квоты привязок |

Вывод для форка: позиционируем редактор как офлайн-инструмент для локальных
сейвов; в UI обязательное предупреждение, что выгрузка изменённых сейвов в
облако и игра в сетевых режимах могут привести к **перманентной блокировке
аккаунта** (TODO: добавить в README и диалог редактора).

## 5. Что забрать с эмулятора (уточнение реверса)

1. `shared_prefs/*.v2.playerprefs.xml` ДО и ПОСЛЕ входа: `DataRijndael128Key`,
   `DataRijndaelIV`, `DataAES256Key`, `cloudSaveId`, `UsedAccounts`,
   `channel_uid`, StageKey/GuidedKey/SaveUploadedKey от миграции.
2. Дамп трафика (frida/mitm): тела `GetBlobSaveAsync` / `FetchGameDataAsync`
   (gzip+JSON — увидим структуру облака и формат `GPV2_{uid}`).
3. Ответ `/SafeCheck/Validate` — что именно проверяется.
4. Динамический дамп `dump.cs` (frida-il2cpp-bridge) — точные сигнатуры
   `CloudSaveUtil`/`NewCloudSaveAgent`.

## 6. Источники

* Метадата 8.7.0 (декодированная) — литералы `GPV2_{0}`, `5.5.2`,
  `cloudsave/upload_fail_different_account`, `/SafeCheck/Validate`,
  `[ChannelMigration] StartUpload -> NewCloudSaveAgent.SaveGame()`,
  `Err/Banned`, `是否被封禁` и классы из раздела 2/4.
* Официальный FAQ ChillyRoom (RU-игры, CN): `https://www.chillyroom.com/zh/soul-knight/support`
  — «存档不属于此账号» (восстановление по коду устройства), «为什么账号会被冻结?»
  (перманентная заморозка за модификацию сейвов), квоты привязок.
* Официальный FAQ (EN): `https://soulknight.chillyroom.com/et/article/base/6`
  — автоперенос Google→ChillyRoom, различие ChillyRoom/Google аккаунтов,
  «we are unable to reverse track a player's Google account».
