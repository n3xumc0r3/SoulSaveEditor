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

### 3.1 Дополнение от 09.11: телефон vs эмулятор — что это говорит о бане

Симптомы: на телефоне при входе показывается «Ваш аккаунт заморожен», но
после выхода из Google в приложении игра предложила легаси-миграцию и
**успешно загрузила** старый облачный сейв; на эмуляторе тот же флоу упал с
«относится к GPV2_235288940». Выводы:

1. Ownership-чек на телефоне пройден ⇒ сессия телефона разрешилась в ту же
   идентичность, что владелец старого сейва (`…940`), т.е. замороженный
   аккаунт **залогинился и вычитал легаси-облако**: в миграционном флоу
   флаг заморозки не читается вовсе. «Из локалки» это не объясняется —
   диалог требует GPGS-авторизацию и говорит именно про облачный сейв.
2. Легаси-облако физически у Google ⇒ серверно ChillyRoom его блокировать
   не может; единственная точка контроля — клиент, и она флаг не проверяет.
   Корректная формулировка: бан **не распространяется** на легаси-ветку,
   а не «бан-система сломана».
3. UID сессии эмулятора `…966` и владелец сейва `…940` различаются на 26 —
   обе идентичности почти наверняка выданы недавно и рядом (гостевая +
   привязанная на эмуляторе), а не «старый аккаунт против нового».
   Проверка за 10 секунд: какой `G UID:` показывает телефон после успешной
   загрузки (ожидаемо `…940`).
4. «Ограничивает ли сервер запись кроме флага» — статически открыт: в
   клиенте есть код `Err/Banned` (сервер умеет отказывать по бану) и
   SafeCheck-валидация, но на каком именно вызове он возвращается (login /
   read / write) — покажет только трафик или динамический дамп.

### 3.2 Запись в GPGS при бане (анализ от 09.11)

Вопрос: примет ли Google-облако снапшот, записанный «в обход» заморозки?

* По докам PGS снапшот — «unstructured binary blob, game is responsible
  for parsing»; Google проверяет только авторизацию Google-аккаунта и
  размер (блоб и обложка — единицы МБ, ≥3 МБ гарантировано; количество
  снапшотов не ограничено). Контент и правила версионирования — целиком
  на стороне игры. О бане ChillyRoom Google не знает ничего ⇒ серверно
  заблокировать запись невозможно.
* Все содержательные чеки — клиентские: имя снапшота `GPV2_{uid}` (чек
  владельца — наш диалог «относится к GPV2_235288940»), анти-откат
  (коммитится только «более продвинутый» локальный), валидация payload
  при миграции. Чинится патчем/трафиком; саму запись в GPGS ChillyRoom
  не видит.
* Значит GPGS — идеальное ХРАНИЛИЩЕ (бэкап + бутстрап миграции в новую
  идентичность), но не канал онлайна: каноническая копия для сетевых
  режимов — новое облако ChillyRoom, и выгрузка туда — ровно то место,
  где стоят SafeCheck/Err/Banned и device-корреляция (эмулятор уже
  светился с замороженной сессией в пятницу).
* Открытый вопрос: пишет ли 8.7.0 снапшоты параллельно с новым облаком
  (dual-write). Если да — актуальный сейв замороженного аккаунта уже
  лежит у Google. Дискриминация: список снапшотов (имена `GPV2_*` +
  updated-timestamp) — фрида-токеном из игры (OAuth-scope games наружу
  не выдают) или mitm GMS-трафика.
* Write-тест почти без риска: на замороженном телефоне после миграции
  дать игре автосинк и пересмотреть timestamp снапшота. Запись идёт
  только в сторону Google, ChillyRoom её не видит.
* Крафт снапшота руками: имя `GPV2_{текущий uid}` + payload легаси-
  формата (крипта эры ≤5.5.2 снята: DES crst1/iambo, XOR game.data) —
  кандидат в фичи форка (бэкап/восстановление GPGS-сейвов).

### 3.3 Статический вердикт по dual-write (11.11, дамп 8.7.0)

APK 8.7.0 перекачан побайтово (pixeldrain i7TH581X, 679 467 257 Б,
APKCube .apks: base + split_UnityDataAssetPack + split_config.arm64_v8a),
метадата декодирована, дамп имён 46 241 тип / 376 499 метод —
`tools/sk870_metadata_dump.py`; скан литералов — `tools/sk870_litgrep.py`
и `tools/sk870_litmulti.py`.

Двойная запись в GPGS подтверждается на уровне имён и конфига:

* `GooglePlayLoginAndCloudSaveAgent.SaveCloudSaveData` — метод записи
  снапшота жив (поля `saveName`, `_authCode`, `savedGameMetadata`; в
  литералах — `com.google.android.gms.games.snapshot.SnapshotMetadataChange$Builder`,
  обработка конфликтов `getConflictingSnapshot` / `getResolutionSnapshotContents`);
* `NewCloudSaveAgent.DealGPAndIosSendDataToLegencyWhenBinded` + RPC-вариант —
  после привязки GP/iOS данные отправляются в легаси-канал;
* `NewDynamicConfig.AutoUploadLegencySave { bool AutoUpload; bool NeedInspect; }`
  — автозагрузка в легаси управляется **серверным динамическим конфигом**
  (`NeedInspect` — гейт `PayInspect`);
* `CallFrom.Auto` + `LegencyLoginAndCloudSaveManager.HasAutoRestoreAfterFirstInstallApp`
  — автоматические пути вызова при логине/первой установке;
* `MoveStep`: `LegencyAccountChange`, `CloudSaveIdDifferent` — коды ошибок
  с наших скриншотов; `BindLogicFrom`: `GuestUpload`/`GuestDnload`;
* список версий-отсечек легаси одним литералом: `5.2.0…5.5.2…5.x.x`.

Бан-контуры клиента (что умеет показывать):

* `Err/Banned` в семействе Err/*; локализация `player_banned/account_title`,
  `player_banned/account_desc_0`, `player_banned/multi_game_desc`,
  `im/tip/player_banned`;
* `Utils.CommonUtils.ErrorUtil.ShowPlayerBannedDetails` внутри
  `HandleApiException`/_`Unauthorized`/_`Common` — централизованный
  обработчик API-ошибок ⇒ теоретически сервер может ответить баном на
  любой вызов; где реально возвращается — покажет только трафик;
* `<SelfIsBanned>k__BackingField`, `ACTIVITY_LEGEND_RANKING_PLAYER_BANNED`
  — флаги в соц./рейтинговых контурах;
* `Utils.CommonUtils.EmulatorUtil`: `IsEmulator`/`CheckEmulator` +
  `GPCheckEmulator`/`IOSCheckEmulator`/`CNCheckEmulator` — клиентский
  детект эмулятора по платформам (питает SafeCheck-репорты).

`DefineCloudSaveID.json` полного URL в бинаре не имеет — собирается в
рантайме (cdn2.chillyroom.com = Aliyun OSS; пробы `game/soulknight/…` и
`game/SoulKnight/…` дают NoSuchKey — путь ветко-зависимый).

**Итог:** автозапись в GPGS существует и включается серверным флагом
`AutoUpload`. Значит актуальный сейв замороженного аккаунта, скорее всего,
дублируется в Google прямо сейчас. Проверка за 10 минут —
`tools/frida_gpgs_snapshots.js`: имена `GPV2_*` + `lastModifiedTimestamp`.

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
