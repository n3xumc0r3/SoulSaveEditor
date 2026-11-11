# LOCAL-SAVE-870 — как Soul Knight 8.7.0 хранит и парсит локальные сохранения

Ответ на вопрос «как именно игра парсит локальные сохранения (JSON|XML), какие там
названия полей и что происходит, когда поле не записано». Всё ниже — из
декодированной `global-metadata.dat` 8.7.0 (маска снята, см. [RE-NOTES.md](RE-NOTES.md)),
экстрактор — `tools/sk870_save_fields.py`, полная карта 46 241 классов:
`sk870_types_fields.json` (локально, 12 МБ), выжимка по сейвам:
`tools/sk870_save_classes.json` (в репо).

## 1. TL;DR

* Локальный прогресс — это **набор секционных JSON-файлов `<section>.data`** во
  внутреннем хранилище (`internalPersistentPath` → `/data/data/com.ChillyRoom.DungeonShooter/files/`).
  Каждая секция = отдельный файл + отдельный C#-класс + суффикс-шифрование.
* Легаси-шифрование (XOR/DES) описано в [REVERSING.md §3.2](REVERSING.md). Новый слой
  8.7.0 — **Rijndael-128/AES-256 с per-install ключом из playerprefs**
  (`DataRijndael128Key` / `DataRijndaelIV` / `DataAES256Key`), файлы с суффиксом **`.aes`**
  (литерал `'.aes'`, классы `SaveDataWithRijndael` / `LoadRijndaelData` / `CheckRijndaelData`).
* Сериализатор — **JSON, не XML**. Старый путь — Unity `JsonUtility`, новый —
  **Newtonsoft.Json**: в `RGScript.Manager.SDK.SaveUtil` есть поля
  `_openNewtonJsonTestOverride` / `_openRijTestOverride`, методы
  `TryOpenNewtonJsonTest / DoOpenNewtonJsonTest / CloseNewtonJsonTest`,
  канальный флаг `get_SupportUseNewtonJsonChannel`, а в самих сейвах
  (`SettingData`) сохраняются флаги **`UseRijData`** и **`UseNewtonJsonData`**.
* Облачный JSON (`CloudSaveGameData`, 18 секций) собирается из **тех же самых
  секций** — поэтому структура облачного сейва (наш доказанный write-путь) и
  локальных файлов совпадает один в один.
* **Незаписанное поле ≠ ошибка**: и `JsonUtility`, и Newtonsoft при отсутствии
  поля в JSON оставляют значение из конструктора/инициализатора C#-класса
  (`0` / `false` / `null` / пустой список). Неизвестные (лишние) поля молча
  игнорируются. Подробности и следствия для редактора — §6.

## 1.1. Подтверждение на реальных файлах устройства (11.11.2026) — v29-сборка эмулятора

С устройства (LDPlayer, `Android/data/com.ChillyRoom.DungeonShooter/files/`) сняты
живые сейвы (`device_saves.zip`) и прогнаны через нашу схему. Это первая
**физическая** валидация (до этого всё было из метадаты v31 APK и редакторов):

| Файл | Размер | Результат |
|---|---|---|
| `statistic.data` | 42 144 Б | **Расшифрован** Base64(DES/CBC, key `crst1`, IV `Ahbool\0\0`, PKCS5) → 30 727 Б JSON, **161 ключ верхнего уровня**. Все 161 — поля класса `StatisticData` (0 посторонних). Не-JSON поля класса: 2 события (`Handbook*RecordedEvent`), 2 static-константы (`PassHistoryMaxCount`, `MultiGameHistoryMaxCount`), `StatisticLoad`, `cy`, `_data` — ровно то, что и не должно сериализоваться |
| `monsrise_data_235288966_.data.new` | 400 Б | **НЕ расшифровывается** ни одной легаси-схемой (DES crst1/iambo/Ahbool+abo123, XOR 15Б, poly-XOR PASSWORD, фиксированный Rijndael) — энтропия 7.994. Класс `MonsterRiseData` в v29-метадате устройства имеет `get_RijPath` ⇒ файл из **Rijndael-группы миграции**; ключ per-install в playerprefs (`DataRijndael128Key`/`DataRijndaelIV`) — нужен root-pull (см. §6.1) |
| `bugly_last_us_up_tm` | 13 Б | timestamp телеметрии Bugly, не сейв |

Факты, извлечённые из v29-метадаты **самого устройства** (54 307 520 Б, XOR-0x66-зоны,
46 241 typeDef × 88Б, 376 499 методов × 36Б, 347 702 полей × 12Б):

* Layout typeDef v29 подтверждён тайлингом: `fieldStart` i32@+32, `fieldCount` u16**@+68**
  (не @+64!); диапазоны полей соседних классов стыкуются без щелей (22 974+168 = 23 142 =
  `fieldStart` следующего typeDef). Секция `types` — пара №19 заголовка (31 пара с 0x08),
  `fields` — №11, `methods` — №5, `parameters` — №10, `images` — №20 (193 сборки).
* Полная карта файлов повторена на v29: литералы `/monsrise_data_`, `/item_data_` и т.д.
  (суффикс `_<accountId>_`), флаги-префы `has_monsrise_data`, `has_pvp_data`, `hasEscapeRunSave`.
* **`.data.new` — атомарная запись**: реальный файл `monsrise_data_235288966_.data.new` —
  временный файл незавершённой записи (игра пишет в `.new`, затем rename). Редактору
  сейвов такие файлы надо тоже подхватывать.
* Группа Rijndael-миграции по v29-метадате (все имеют `get_RijPath`): **MonsterRiseData,
  ItemData, PvpData, MiscData, TaskDatas, BattlePassSaveData, MallReloadData**;
  литералы `.rij`, `rij_decrypt`, `rijndael`; канальный раскат `OpenRijTest_{accountId}` /
  `SupportRijChannel` / `RijUserAutoOpenJsonTestIfSupport` (методы `SaveUtil`:
  `TryOpenRijTest`, `DoOpenRijTest`, `CloseRijTest`, `DealRijAndJsonTest`).
  `statistic.data` при этом остался на DES — раскат per-файл.
* В реальном сейве присутствуют `$type`-поля (`RGScript.Data.Mall.MallConditionStatisticData`)
  — **Newtonsoft.Json с именами типов** подтверждён на живом файле (не только по метадате).
* Round-trip-правка на живом файле: `write _usedEmoji`, `add TodayActivityPlayTime 0→42`,
  перешифровка DES, обратное чтение — ОК (бэкапы + sha256 в логе редактора).
* Инструменты v29: `tools/sk870_v29_metadata_decode.py` (снятие XOR-зон + все строки),
  `tools/sk870_v29_dump_class.py` (дамп класса по имени: поля+методы),
  `tools/sk870_v29_extract_schema.py` (генератор `tools/sk870_v29_save_schema.json` —
  карта 19 файлов → классы → поля).

## 2. Карта файлов: секция ↔ класс ↔ поля

Литеральные имена файлов из метадаты (все с ведущим `/` — кладутся под
`internalPersistentPath`); колонка «облако» — поле `CloudSaveGameData`:

| Файл | Класс (полей) | Секция облака |
|---|---|---|
| `/game.data` (+`.aes`) | **AboGameData** (41) | `gameData` |
| `/item_data.data` | **ItemData** (114) | `itemData` |
| `/statistic.data` | **StatisticData** (168) | `statisticData` |
| `/setting.data` | **SettingData** (47) | `settingData` |
| `/season_data.data` | **SeasonData** (78) | `seasonData` |
| `/task.data` | RGScript.Data.TaskData (10) | `taskData` |
| `/sandbox_maps.data` | (maps) | `maps` |
| `/sandbox_config.data` | (sandbox config) | `sandboxConfig` |
| `/mall_reload_data.data` | RGScript.Data.Mall.MallReloadData (28) | `mallReloadData` |
| `/monsrise_data.data` | **MonsterRiseData** (23) | `monsterRiseData` |
| `/weapon_evolution_data.data` | RGScript.WeaponEvolution.WeaponEvolutionData (6) | `weaponEvolutionData` |
| `/misc_data.data` | RGScript.Data.MiscData (19) | `miscData` |
| `/pvp_data.data` | RGScript.Data.PvpData (12) | `pvpData` |
| `/bp_data.data` | RGScript.Data.BattlePassSaveData (5) | `battlePassSaveData` |
| `/escape_season_data.data` | ModeSeason.Escape.Scripts.EscapeSeasonData (42) | `escapeSeasonData` |
| `/escape_run_save.data` | ModeSeason.Escape.Scripts.EscapeRunSaveData (33) | — (сезонный рогалик) |
| `/backup.data` | **BackupGameData** { `gameData` } | — (бэкап-обёртка) |
| `/recordSave.data`, `/recordSave_*` | rolling-запись (SaveUtil.get_RecordSavePath) | — |
| `/battle.data`, `/battles.data` | боевые (легаси, чистый JSON) | — |

`CloudSaveGameData` (18 полей): `serialzedVersion` (опечатка оригинальная),
`cloudSaveId`, `platformId`, `gameData`, `itemData`, `statisticData`, `settingData`,
`taskData`, `maps`, `sandboxConfig`, `seasonData`, `mallReloadData`,
`monsterRiseData`, `weaponEvolutionData`, `miscData`, `pvpData`,
`battlePassSaveData`, `escapeSeasonData`.
Методы: `CreateFromPlayerPref` / `CoverPlayerPref` / `CreateNew` / `Merge` /
`MergePaymentHistoryCacheForSameAccount` / `FuncForHotFixBeforeCover` /
`FuncForHotFixAfterCover` — т.е. облако собирается из секций, а после загрузки
покрывает PlayerPrefs/секции обратно. В облачном дампе `escapeSeasonData: null`
— секция просто ни разу не сохранялась.

Обёртки: `BackupGameData` = `{ "gameData": <AboGameData> }` (файл `/backup.data`);
`SaveDataPara` = `{ SavePath, Data }` (параметр отложенной записи);
`SaveDataState` = enum `{ UnKnown, NotExist, Exist }` — им помечены служебные
поля `<Section>Load` в классах (состояние файла при загрузке, не игровые данные).

## 3. Пайплайн записи/чтения: `RGScript.Manager.SDK.SaveUtil`

48 методов, ключевые поля: `className`, `dataSuffix`, `_saveDataDic` /
`_saveDataLockDic` / `_saveDataList` (кэш секций), `_saveTimer`, `SaveInterval`,
`SaveMaxCount` (отложенная пакетная запись — поэтому файл на диске отстаёт от
состояния игры), `AlreadyCheckRijndaelPaths`, `_compressData`, `showLog`,
`RijAndJsonTest`, `_deferredLoadDiagnostics`.

Методы (в порядке метадаты): `SaveImage*`, `CheckRijndaelData`,
`TaskRunCheckRijndaelData`, `DoCheckRijndaelData`, `AddSaveRijndaelDataTask`
(миграция старых файлов в Rijndael), `SaveRijndaelTaskLoop`, `SaveDataArray`,
`SaveDataWithRijndael`, `SaveStr`, `SaveData` ×3, `LoadRijndaelData`,
`LoadData` ×2, `ParseJson`, `LoadCryptData`, `SaveCryptData` (легаси),
`DeleteSaveDataFiles`, `get_RecordSavePath`, `get/set_OpenRijTest`,
`get/set_OpenNewtonJsonTest`, `GetOpenRijTest`, `GetOpenNewtonJsonTest`,
`UseSaveSettings`, `UseDeferredLoadDiagnostics`, `Report*Diagnostic`,
`get_SupportRijChannel`, `get_SupportUseNewtonJsonChannel` (серверные
эксперимент-каналы), `AfterUploadGame*`, `DealRijAndJsonTest`,
`RijUserAutoOpenJsonTestIfSupport`, `Try/Do/CloseRijTest`,
`SaveAllData`, `Try/Do/CloseNewtonJsonTest`, `RecordDataToLocal`.

Файловый слой — `Abo.FileUtil`: `get_InternalPersistentPath`, `WriteFile`,
`ReadFile`, `WriteByte/ReadByte`, `WriteObject/ReadObject`, `Exists`,
`CreateDir`, `CompressFiles`, `SearchFilesInDirectoryWithSuffix`,
`WriteBytesToFileForLua`, `CheckAndTryUploadBrokenSaveData` (битые сейвы
(`BrokenFileSuffix`) не удаляются, а отправляются на сервер — делиться сломанным
сейвом с разработчиками = делиться своим прогрессом).

Сериализация — `Abo.JsonUtil` (27 методов): `ToJson` ×3, `ParseJson`,
`TryParseJson`, `ParseJson` ×2, `SaveJson`, `SaveStr`, `LoadJson` ×2,
`SaveJsonWithCrypt` ×2, `LoadJsonWithCryptStr`, `LoadJsonWithCrypt` ×2,
`ToJsonWithUnicode`, `SaveJsonWithUnicode`, `ParseJsonWithUnicode` ×2,
`ParseJsonWithUnicodeOrThrow`, `LoadJsonWithUnicode` ×2, `EscapeString`,
`UnescapeString`, `IsHex`, `ParseUnicode`, `ParseSingleChar`, `SaveBrokenData`.
Семейство `*WithUnicode` — это UTF-16LE с `\uXXXX`-эскейпами: **тот самый формат
облачного снапшота**, который мы уже правили (`7b 00 22 00`).

## 4. Названия полей (полные списки)

Полная выжимка — `tools/sk870_save_classes.json`; ниже — дословно из метадаты.
Служебные поля: `_data` (ссылка на загруженный объект), `<Name>Load`
(состояние `SaveDataState`), `version`/`AppVersion` — в JSON присутствуют, но
геймплея не касаются.

### 4.1 AboGameData (`game.data`, секция `gameData`) — 41 поле

```
skinLock, currentSkinDatas, heroUnlock, heroLevel, heroSkill, heroSkillUnlock,
petUnlock, roomObjectLevel, usedSecretKeys, promotions2RewardVersion,
unlockRoomDecorate, certificates, normalModePassCertificates,
bossRushPassCertificate, ironTidePassCertificate, aramPassCertificate,
currentRoomDecorate,
gems, lastGems, varGems, cumulativeGems, cumulativeConsumeGems,
rebornCard, hasTutorial, firstInRoom, roomLevel, fishChip, gameVersion,
lastPlayTime, lastPlayTimeWithNetworkTime, totalIndex, totalGameTime,
generalSeed, tutorialEndTime, checkUnlockWorkShopTime, fontSettingValue,
superHeroInHeroRoom, isWaveMachineUpgraded, retiredKnightGotAdItemTime,
lastTalentAdRefreshDay, activeShopGemReceiptMarkers
```

Методы: `CreateFromPlayerPref`, `CoverPlayerPref`, `CoverPlayerPrefSafely`,
`Merge`, `GetProgressScope`, `IsHeroCharacter`. Леджер алмазов тот же, что мы
правили в облаке: `gems` / `lastGems` / `varGems`.

### 4.2 ItemData (`item_data.data`) — 114 полей

```
_data, MagicNames, AppVersion, FertilizeName, TryTicketName, RebornCard,
Plantpot_1, Plantpot_2, Plantpot_3, Plantpot_4, GemName, FishName, BpPointName,
SeasonCoinName, ComboGunSparkCoinName, SeasonTreasureBoxName,
SeasonTreasureBoxName1, SeasonTreasureBoxName2, SeasonTreasureBoxName3,
SeasonTreasureBoxTicketName2, WeaponBlueprintFragmentUnidentified,
FeatureMoreSelectSeat, FeatureMoreBagCapacity, CellName, GearName, WoodName,
IronName, BatteryName, TokenEquipment, TokenHero, HopperTicket, PostmanUpgrade,
plants, sellerRefreshTime, sellerWeaponRefreshTime, cumulativeFishCount,
commodities, itemUnlock, forgeWeapons, skinTrialEntries, _deletingForgeItems,
mechInRoom, blueprints, seeds, materials, trainingJourney,
activeShopReceiptMarkers, tokenTickets, restoreKeys, alreadyGetRealNameAward,
BugFeatureRedeemRecords, SchoolRedeemRecords, SchoolRedeemRecords202508,
DreamRedeemRecords, Christmas2024RedeemRecords, FireRedeemRecords,
ActivityRedeemRecords, SelectRewardClaimRecords, jewelryData,
jewelryBlueprints, jewelryPlayerData, mythicWeapons, lastBuyBeginnerPkgId,
lastRewardBeginnerPkgId, lastBuyReturnPlayerPkgId,
lastRewardReturnPlayerPkgId, UnlockNewPackageTime, _AllMythicWeaponIds,
CanNotSaleWeapons, specialTickets, hopperEnteredModes, fishChipConfigId,
seasonStoreConfigId, bossrush_time, eggMachineUniqueSeed, eggMachineTimes,
eggMachineUseTimes, eggMachineMode, EggMachineGuaranteeRewardTimes,
easterEggTempPets, fusionWeaponPathUnlock, todayTriggerActivityNpcCount,
fishChipStorePurchaseHistory, fishChipStorePurchaseHistoryIndexed,
fishChipStoreAllPurchaseHistoryIndexed, lastFishChipStoreAllPurchaseHistoryIndexed,
seasonStorePurchaseHistoryIndexed, seasonStoreAllPurchaseHistoryIndexed,
lastSeasonStoreAllPurchaseHistoryIndexed, RandomSeedMap, got_bossrush_ticket,
roomDecorateDic, roomDecorateCurSkinDic, hasGetAchievementCategoryRewardList,
hasGetAchievementGroupRewardList, TodayStatueTimes,
hasGetRewardQuestionnaireList, hasGetH5YearlyStatsReward,
todayPlantMaterialAdTimes, todayTvRandomGiftAdTimes, tvHasGuarantee,
todayEggMachineAdTimes, todayTvGoldAdTimes, todaySkinTrialAdTimes,
adMallRandomWeaponFragmentName, adMallNextRefreshTime, magicWellFishedTimes,
evolutionWeaponBossRatesIndex, todayTalentRerollTimes, meetWeaponCollectorCount,
windowHeroListShowDrawing, handStyleIndexList, SeedNameAliasMap, ItemLoad
```

### 4.3 SettingData (`setting.data`) — 47 полей

```
_data, SettingLoad, MaxLevel, shakeLevel, sandboxFlyable,
enableCharacterSelectOutline, showInitWeapon, heroRoomFoldButtonsExpanded,
doubleClickSelectBuff, promotionIndex, promotionID, bgmName, movePosition,
skillPosition, skillSubPosition, weaponPosition, attackPosition, specialPosition,
emoticonPosition, fishingPosition, mountPosition, inventoryPosition,
specialSeasonPosition, firingPosition, seasonMovePosition, seasonSkillPosition,
seasonSkillSubPosition, seasonWeaponPosition, seasonAttackPosition,
seasonEmoticonPosition, seasonFishingPosition, seasonKeyboardPositions,
customKeyboardLayoutVersion, CurrentCustomKeyboardLayoutVersion,
lastSendEmailTime, useNewLayout,
UseRijData, UseNewtonJsonData,
MovePosition2, SkillPosition2, SkillSubPosition2, WeaponPosition2,
AttackPosition2, SpecialPosition2, EmoticonPosition2, FishingPosition2,
ChillyUid
```

`UseRijData` / `UseNewtonJsonData` — **флаги формата прямо в сейве**: миграция
на Rijndael и Newtonsoft включается per-user и запоминается (см. §6 и
`DealRijAndJsonTest` / `RijUserAutoOpenJsonTestIfSupport`).

### 4.4 StatisticData (`statistic.data`) — 168 полей (курсивно главное)

```
HandbookCollectionUserPropertiesRecordedEvent,
HandbookSkinGoldenCollectionUserPropertiesRecordedEvent,
zongzi2026ActivityData, StatisticsData, object2PassLevel, object2PassTime,
hero2PurpleFramePassTime, skin2PurpleFramePassTime, weapon2PurpleFramePassTime,
handbookHonoraryTitleTrackedLevels, handbookEnemyCollectionTrackedLevels,
handbookWeaponCollectionTrackedLevels, handbookSeasonTitleTrackedLevels,
handbookCollectionUserPropertiesInitialized, goldframeShopSaveData,
KrppVerifiedUsdCents, KrppCountedOrderIds, _uiOpenTimes, PaySignInRecords,
_enemyKilledCount, _usedEmoji, object2ObtainTime, firePassDifficultyLevel,
fireExtraReplaceHeroSeatCount, firePassDifficultyLevel2,
fireExtraReplaceHeroSeatCount2, legendClaimOutBuffs, legendWing, legendWingPray,
legendKnife, legendKnifePray, legendLight, legendLightPray, legendSharedPray,
legendMagicReloadCount, event2Count, recordedEvents, passInfo, season2Prizes,
c02_skill_times, explode_kill_enemy, one_pounch_kill_boss_count,
hand_cut_index, manual_change_hand_cut_index, buy_good_times, ...
```
(полный список из 168 — в `tools/sk870_save_classes.json`)

### 4.5 Остальные секции (кратко)

* **SeasonData** (78): `coin`, `gainedCoinToday/ThisWeek/InSeason`,
  `cumulativeCoinCount`, `buyCoin5/15/30/68`, `treasureBox1OpenBoxCount`,
  `chipSchemes`, `chips`, `comboGunData`, troop2*/artifacts2*/aram2*
  (задачи/трофеи/наёмники), `SeasonDataLoad`...
* **MonsterRiseData** (23): `TeamSize`, `version`, `lastCharacterIdx`,
  `maxClearLevel`, `monsterTeams`, `CollectedMonsters`, `CollectedEmblems`,
  `weeklyExchange*`, `monsterChestOpenTimes*`...
* **MallReloadData** (28): `mallPurchaseCountHistoryIndexed`,
  `blindBoxBuyCountDict`, `mallGoodStateDict`, `packageRestoreCountDict`,
  `buyGemCount800/5000/12000/25000/35000/76000/115000`,
  `buyFishCount5/10/20/45/68/128`...
* **MiscData** (19): `returningEvent*`, `returnPlayer*`, `lastCheckInDay`,
  `unlockedKillEffectIds`, `equippedKillEffectIdByScene`...
* **PvpData** (12): `isFirstPvpPlay`, `pvpLobbyOpenCount`, `last_match_mode`,
  `last_match_type`, `pvpTaskData`, `pvpSkinSelectionByHero`...
* **EscapeSeasonData** (42): `money`, `playerBag`, `safeSlots`, `warehouse`,
  `deathBox`, `buildings`, `upgradeBlueprints`, `selectedHero`...
* **EscapeRunSaveData** (33), **WeaponEvolutionData** (6): `weapons`,
  `blindBoxOpenCount0`, `favorWeapons`; **BattlePassSaveData** (5):
  `seasonData`, `bpShopOthersPurchaseCounts`...
* **RGScript.Data.TaskData** (10): `type`, `enemyId`, `total`, `count`,
  `isCompleted`, `isRewarded`, `battleFactors`, `rewards`, `excludeFactors`.

## 5. YearReview (файлы во внешнем storage)

`YearReview*.json` (в `Android/data/.../files/`) — телеметрия «итогов года»,
НЕ сейвы: классы `RGScript.Data.YearReview.RecordData/RecordDataList/RecordDataDic`
(`recordStrDic/recordFloatDic/recordIntDic/recordLongDic` и их List-варианты).
Пишутся красиво отформатированным JSON (Newtonsoft `Formatting.Indented`),
литералы `[YearReview] Async saved snapshot to file: ...` и т.п. Именованные
словари (`OnlineTime`, `Active`, `LatestGameTime`) — пример того, что игра
**умеет Dictionary в JSON**, а значит для этих файлов JsonUtility исключён.
`msg.db` (в `files/<uid>/`) — SQLite внутриигрового чата
(`MessageModel/ContactModel/IMMeta`), к сейвам отношения не имеет.

## 6. Поведение при отсутствующих полях — доказательства

Вопрос: «может быть такое, что значения поля заданная строка или параметр не
(равен 0), а просто незаписан?» Ответ по коду 8.7.0:

1. **Какой сериализатор.** В сейв-слое есть явный переключатель сериализатора:
   `SaveUtil._openNewtonJsonTestOverride`, `TryOpenNewtonJsonTest` и
   серверный канал `SupportUseNewtonJsonChannel`; факт текущего режима
   сохраняется в сейве (`SettingData.UseNewtonJsonData`). Старый путь —
   Unity `JsonUtility` (LitJson в сборках нет — строки `LitJson` в метадате
   отсутствуют). Оба читают один и тот же JSON с одними и теми же именами
   полей (переключение режимов не меняет схему).
2. **Отсутствующее поле.** И `JsonUtility`, и Newtonsoft.Json при
   десериализации НЕ требуют присутствия поля: отсутствующее поле просто
   сохраняет значение, которое ему дал конструктор/инициализатор класса
   (`0` для int/long/float, `false` для bool, `null` для ссылок и списков).
   Ошибки нет, сейв не «битый». Поэтому старые сейвы открываются в новых
   версиях игры: новые поля классов появляются с дефолтами.
3. **«Незаписан» vs «равен 0».** После загрузки различий нет: поле с
   отсутствующим значением **равно дефолту класса**, и при следующем сохранении
   оно будет записано уже явно (со своим дефолтом или актуальным значением).
   Т.е. «не записан» — временное состояние только внутри JSON-файла.
   Единственное место, где игра различает «не было» и «ноль», — служебные
   `SaveDataState` (`UnKnown/NotExist/Exist`) — но это про существование
   файла секции, а не про поля внутри него.
4. **Лишнее/устаревшее поле** в JSON игнорируется молча
   (`UnknownMemberHandling.Ignore` у Newtonsoft; JsonUtility пропускает
   неизвестное) — формат forward-compat.
5. **null вместо значения** — для ссылочных полей валиден (в облачном дампе
   `escapeSeasonData: null`, и сейв грузится); для значимых типов (int)
   Newtonsoft материализует дефолт `0`.
6. **Следствия для редактора** (уточнение REVERSING.md §3.3):
   * числовое поле можно **добавлять** в JSON — загрузится как записанное;
   * **удалять** существующее поле можно, но при следующем Save игра
     материализует его дефолтом — правка «исчезнет», а не сохранится;
     надёжнее ставить 0/пустое значение на месте;
   * **списки** лучше не превращать в null без проверки кода: отдельные
     consuming-места ожидают ненулевые (`LoadData` → NRE → секция уедет в
     `SaveBrokenData`/`BrokenFileSuffix`);
   * `serialzedVersion: 1` — версия схемы для миграций, не трогать;
   * `ChillyUid` (SettingData) и `cloudSaveId` — маркеры принадлежности
     профиля; при переносе сейва на другой аккаунт менять оба (см. REVERSING.md §3).

## 6.1. Рулбук: root-pull недостающих файлов (LDPlayer, Windows)

Для полного доступа ко всем секциям (game.data, .aes/Rijndael-ключи, .data.new)
нужно внутреннее хранилище — оно читается только под root. LDPlayer рутован:

```bat
:: 1) playerprefs.xml — ключи Rijndael/AES + cloudSaveId + флаги OpenRijTest_
D:\LDPlayer\LDPlayer14\adb.exe exec-out "su -c 'cat /data/data/com.ChillyRoom.DungeonShooter/shared_prefs/com.ChillyRoom.DungeonShooter.v2.playerprefs.xml'" > prefs.xml

:: 2) внутренние файлы сейвов (game.data, .aes, .data.new, battles.data и т.д.)
D:\LDPlayer\LDPlayer14\adb.exe exec-out "su -c 'tar -C /data/data/com.ChillyRoom.DungeonShooter/files -cf - .'" > internal_files.tar
mkdir files_internal && tar -xf internal_files.tar -C files_internal

:: 3) все дальнейшее делает редактор (см. EDITOR.md):
python tools\sk_save_editor.py pull
python tools\sk_save_editor.py currencies
python tools\sk_save_editor.py preset gems 50000 --yes
python tools\sk_save_editor.py push
```

Что это открывает:

1. **Rijndael-ключи** `DataRijndael128Key`/`DataRijndaelIV` из prefs.xml → расшифровка
   `monsrise_data_*.data.new` и всех `.aes`/`.rij`-файлов (редактор делает мульти-догадку
   автоматически при `--prefs prefs.xml`).
2. **game.data** (AboGameData, XOR) → правка валют (`gems`/`fishChip`), героев, комнаты.
3. Если `su` недоступен — пробовать `adb root` (пересборка adbd), либо взять файлы из
   бэкапа наоборот: сначала `python tools\sk_save_editor.py pull` — он сам перебирает
   su/прямой доступ.

### 6.2. Итог pull'а 12.11.2026: prefs.xml снят, ключей в нём НЕТ

`v2.playerprefs.xml` получен с реального устройства (2 105 ключей, 183 паттерна).
Полный разбор — [PREFS-870.md](PREFS-870.md). Ключевые следствия для этого документа:

* `OpenRijTest_<UID>=1` — Rijndael-канал на устройстве **включён**, при этом ключей
  `DataRijndael128Key`/`DataRijndaelIV`/`DataAES256Key` в файле **нет** ⇒ гипотеза
  «ключи в playerprefs» опровергнута; источник ключа ищем в серверном конфиге
  канала / константах кода (`rij_decrypt`) / отдельном файле (PREFS-870.md §7).
* prefs — вторая половина локального сэйва: зеркала `<UID>_*/LOCAL*` (править оба,
  регистр bool различается), кэш леджера гемов, флаги `has_*_data` по секциям.
* Правка prefs — `tools/sk_prefs.py` (хирургические правки, бэкап, round-trip).

## 7. Инструменты и статус

| Артефакт | Что делает |
|---|---|
| **`tools/sk_save_editor.py`** | **флагманский редактор** (см. [EDITOR.md](EDITOR.md)): pull/push профиля по adb (root), автодетект всех криптосхем (JSON/UTF-16LE/XOR/DES/Rijndael-AES по ключам playerprefs), пресеты (`gems`, `unlock-heroes`, `research-all`, `all-materials` и др.), правки по путям, бэкапы + round-trip-верификация, защита служебных полей, selftest (28 проверок) |
| `tools/sk870_save_fields.py` | экстрактор классов/полей/методов из декодированной метадаты (v31, счётчики typeDef: method/prop/**field**/event/nested/vtable/if/ifoff — порядок подтверждён тайлингом таблиц, 0 перекрытий) |
| `tools/sk870_save_classes.json` | выжимка полей 19 ключевых классов сейвов (в репо; редактор грузит её автоматически) |
| `tools/sk_local_save.py` | легаси-крипто `.data`: XOR game.data, DES `crst1`/`iambo`, вытягивание `cloudSaveId` (совместим с редактором: расшифровывает его вывод) |
| `tools/sk870_metadata_decode.py` / `sk870_metadata_dump.py` | снятие маски метадаты и дамп имён |
| `tools/sk870_v29_*` | набор для метадаты **устройства** (v29): decode/dump/schema — факты §1.1 извлечены ими |
| — | Rijndael-128/AES-256 путь: механика ясна (`SaveDataWithRijndael`, суффикс `.aes`, ключи из playerprefs `DataRijndael128Key`/`DataRijndaelIV`/`DataAES256Key`); **реализован в редакторе** как мульти-догадка (raw/b64 × CBC/ECB × ключи b64/hex/utf8/sha256) — точная схема подтвердится первым `pull` живого `.aes` (чеклист RE-NOTES §Чеклист) |
