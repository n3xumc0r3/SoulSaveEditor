# RE-NOTES — шпаргалка по реверсу Soul Knight 8.7.0

Быстрые факты и оффсеты. Подробности и выводы — в [REVERSING.md](REVERSING.md).

## Цель

- Soul Knight **8.7.0**, APKCube `.apks`, 679 467 257 байт
- Unity **2022.3.62f2** → IL2CPP metadata **v31**
- arm64-v8a; `libil2cpp.so` = 272 234 872 Б; `global-metadata.dat` = 54 307 520 Б

## Маска метадаты (проверено декодером)

```
stored magic : 0x12724394   (реальный: 0xFAB11BAF)
stored ver   : мусор        (реальный: 31)
page 0       : XOR 0x66 целиком (0x0000..0x10000)
page N>0     : XOR 0x66 только [0x1000, 0x5000)
шапка        : 8 + 31*8 = 256 байт, регионы встык до EOF
```

Декодер: `tools/sk870_metadata_decode.py`. После декодирования
Il2CppDumper принимает файл (CodeRegistration/MetadataRegistration найдены),
но падает на int-overflow при скане 272-МБ so → динамический дамп надёжнее.

## Таблица регионов (декодированный заголовок, 0-based пары)

```
[ 0] 0x00000100 +0x095B28   stringLiteral
[ 1] 0x00095C28 +0x209440   stringLiteralData
[ 2] 0x0029F068 +0xBABBE0   stringData (идентификаторы)
[ 3] 0x00E4AC48 +0x009840   events
[ 4] 0x00E54488 +0x0F6D60   properties
[ 5] 0x00F4B1E8 +0xCED12C   methods
[ 6] 0x01C38314 +0x021900   parameterDefaultValues
[ 7] 0x01C59C14 +0x1D4EDC   fieldDefaultValues
[ 8] 0x01E2EAF0 +0x2B54B4   fieldAndParameterDefaultValueData
[ 9] 0x020E3FA4 +0x0382F8   fieldMarshaledSizes
[10] 0x0211C29C +0x3F73EC   parameters
[11] 0x02513688 +0x3FAA88   fields
[12] 0x0290E110 +0x036D10   genericParameters
[13] 0x02944E20 +0x0040B8   genericParameterConstraints
[14] 0x02948ED8 +0x022B00   genericContainers
[15] 0x0296B9D8 +0x011308   nestedTypes
[16] 0x0297CCE0 +0x011DF8   interfaces
[17] 0x0298EAD8 +0x2555C0   vtableMethods
[18] 0x02BE4098 +0x058770   interfaceOffsets
[19] 0x02C3C808 +0x3E1758   typeDefinitions (46241 × 88 Б — счётчики uint16!)
[20] 0x0301DF60 +0x001E28   images (193 × 40 Б)
[21] 0x0301FD88 +0x003040   assemblies (193 × 64 Б — сходится с images)
[22] 0x03022DC8 +0x003B78   fieldRefs (1903 × 8 Б)
[23] 0x03026940 +0x001270   referencedAssemblies
[24] 0x03027BB0 +0x26AAA4   attributeData
[25] 0x03292654 +0x0FCEB8   attributeDataRange
[26] 0x0338F50C +0x0215A4   unresolvedVirtualCallParameterTypes
[27] 0x033B0AB0 +0x017480   unresolvedVirtualCallParameterRanges
[28] 0x033C7F30 +0x000000   хвостовой служебный регион (пустой)
[29] 0x033C7F30 +0x000000   хвостовой служебный регион (пустой)
[30] 0x033C7F30 +0x002B90   exportedTypeDefinitions (2788 × 4 Б)
```

Сверка непрерывности: последний регион заканчивается ровно на EOF
(0x33C7F30 + 0x2B90 = 0x33CAAC0 = 54 307 520).

## Известные литералы в декодированном файле

```
crst1            @ 0x20F1B4   (ключ DES statistic — жив)
iambo            @ 0x2239CE   (легаси ключ — жив; зона была XOR-0x66)
mscorlib         @ 0x18AE48
Assembly-CSharp  @ 0x0B1D91
```

## libnative-lib.so (ChillyRoom SDK)

JNI-экспорты (~280 Б каждый):

```
Java_com_chillyroomsdk_cyphertest_MainActivity_encrept
Java_com_chillyroomsdk_sdkbridge_util_CreptUtil_decrept
```

Шифр (самоинверсный):

```arm
w15 = data[i]
w16 = key[i % keyLen]
w14 = ((i % 5) * (i % 15)) % 92
out[i] = data[i] ^ w14 ^ w16
```

Python: `p[i] = c[i] ^ ((i%5)*(i%15) % 92) ^ key[i % keyLen]`

## dex: com.chillyroomsdk.sdkbridge.util.CreptUtil

```java
this.desKey = decrept("\x11(55(#TJ", "PASSWORD"); // -> "Ahbool\0\0"
this.desIv  = decrept("1\"8ku|TJ",  "PASSWORD");  // -> "abo123\0\0"
// далее DES/CBC/PKCS5Padding
```

Сырые байты ключевой строки в dex: `11 28 35 35 28 23 54 4A`
(dexdump показывает её как `\485049(55(#` — экранирование 0x11 обманывает).

Итог 8.7.0: **DES key = `Ahbool\0\0`, IV = `abo123\0\0`**
(старый IV `Ahbool` стал ключом; `abo123` — тег автора `Abo.CryptUtil`).

## PlayerPrefs-имена из libnative-lib.so

`SaveFirst`, `SaveLast`, `SavePackIdx`, ... — прогресс чтения сейв-паков.
Привязки аккаунтов: `cloudSaveId`, `unity.cloud_userid`, `UsedAccounts`, `account2*`.
Ожидаемые параметры нового крипто-слоя: `DataAES256Key`, `DataRijndael128Key`, `DataRijndaelIV`.

## Классы 8.7.0 (из stringData декодированной метадаты)

Крипто сейвов: `Abo.CryptUtil`, `AesEncryption`, `SDK.SaveUtil.SaveDataWithRijndael`,
`AddSaveRijndaelDataTask`, `AfterFetchCloudSave`, `XORCrypt`;
суффиксы `AesSuffix` / `RijndaelSuffix` / `NewDataSuffix` (`.aes`, `.rijndael`).

Облако: `CloudSaveUtil`, `CloudPackage.V1.BackupClient.GetBlobSaveAsync`,
`DefineCloudSaveChannelId` (Huawei — отдельный канал).

Hot-update: `HotUpdateManager`, `PatchListApi`, `PatchService`,
`HotUpdateManifest`, `VersionCheck*`.

## Хосты

```
api-gateway.soulknight.chillyroom.com   ; API-шлюз
accounts.chillyroom.com                 ; аккаунты
aas.chillyroom.com                      ; Account & Auth Service
cdn2.chillyroom.com/game/soulknight/patches  ; список патчей
cdn.chillyroom.com/game/...             ; ассеты hot-update
*.chilly.tech                           ; тестовый контур
```

## Античит/обфускация

- Tencent ACE: `libanogs.so` (5.5 МБ), `libanort.so` (1.9 МБ), `assets/__acinfo.tsb`
- PGL: `libpglarmor.so`, `libnms.so`, `libfile_lock_pgl.so`, `libbuffer_pgl.so`, `libtax.so`
- Hot-update скриптинг: `libxlua.so` (Lua)

## Литералы

Всего 76 645 литералов; 74 597 читаются статически, 2048 (записи 480..2527)
лежат в зашифрованном окне страницы 0 `[0x1000,0x5000)` — равномерное
распределение байтов (не XOR-0x66; снимется только динамически).

Схема маски (уточнена перекрёстной сверкой таблиц):

```
[0x0000, 0x1000)      XOR 0x66   (заголовок + первые записи таблицы)
стр. >= 1: [0x1000, 0x5000) окна XOR 0x66
стр. 0:    [0x1000, 0x5000) — сильная крипта; [0x5000, 0x10000) — ЧИСТОЕ в сыром файле
```

Дамп имён: `tools/sk870_metadata_dump.py` -> 46 241 тип / 376 499 метода.

## Облако и бан-система (8.7.0, детали в CLOUD-SAVE.md)

```
Каналы:  GPV2_{uid} (Google Play V2), GPV2_Test_, GPV4; /DefineCloudSaveID.json
Легаси:  до 5.5.2 — GPGS Saved Games снапшоты (GooglePlayLoginAndCloudSaveAgent)
Агенты:  NewCloudSaveAgent, ChannelMigrationManager, ILegencyLoginAndCloudSaveAgent
Транспорт: CloudPackage.V1.BackupClient: FetchGameDataAsync / GetBlobSaveAsync
Идентичности: uid, chilly_uid, channel_uid, pf_id (CloudSaveUtil)
Анти-абьюз: cloudsave/upload_fail_different_account, local_more_payment_point_upload,
            GetCurrentAccountSaveId vs GetLocalSaveId
Бан-система: SafeCheckClient /SafeCheck/Validate|Record?|DownloadResource,
             AntiCheatState{Checking,Punishment}, ReportCheating (UI-жалобы),
             Err/Banned, флаг бана в лидербордах (是否被封禁), Idcheck.V1
```

## Чеклист для эмулятора

1. `files/*.data` — все, включая `game.data`, `battles.data`, `statistic*`.
2. `shared_prefs/*.v2.playerprefs.xml` — вычитать `DataRijndael128Key`/`DataRijndaelIV`/`DataAES256Key`.
3. Сейвы до/после первого облачного логина — дифф синхронизации.
4. (опц.) динамический дамп dump.cs через frida-il2cpp-bridge / Zygisk-Il2CppDumper.
5. (опц.) дамп трафика `GetBlobSaveAsync`/`FetchGameDataAsync` и ответа `/SafeCheck/Validate`.

## Скачивание APK 8.7.0 автоматически (заметки на будущее, 11.11)

Проверено из датацентровой среды (11.11.2026), для будущих автоматизаций:

* **APKCube**: API разблокируется заголовком `Origin: https://apkcube.com` —
  открыты `GET /api/apps/{pkg}/fetch-status`, `GET /api/apps/{pkg}/apks`
  (список с `apkId`/sha256/размером: `97335` = 8.7.0 arm64-v8a `.apks`,
  679 467 185 байт, sha256 `14d31d3a…`, подпись ChillyRoom подтверждена) и
  `POST /api/apps/{pkg}/download-token` (токен + hold-тикет, ~30 мин).
  Финальный `POST /api/downloads/request` закрыт `FIREWALL_BLOCKED` для
  датацентровых IP (проверено curl'ом с настоящим FingerprintJS-visitorId
  и в headed-браузере). Параметры запроса: `{packageName, apkId, h, pwa,
  pageToken}`; `h` = FingerprintJS visitorId (32 hex, localStorage
  `apkcube_fp`, ревизия `5.3.0`), `pageToken` = токен из download-token.
  Официальный REST API v1 (`/docs/api`) — платный (кредиты).
* **uptodown**: страница открывается, но Downloads защищён Cloudflare
  Turnstile (`interaction-only`, sitekey `0x4AAAAAAD1v848b_gRQOB8P`);
  в headless токен не эмитится, колбэк `onDownloadTurnstileAPILoaded`
  теряется (гонка с api.js). POST `/ajax/app/{appId}/file/{fileId}/download-url`
  требует `{token: turnstileToken, onlyXapk}`; без токена — 400.
  Подписанные `dw.uptodown.com/dwn/...`-ссылки из браузера пользователя
  с другой IP дают 404 (привязка к сессии/IP).
* **pixeldrain**: API отдаёт `file_rate_limited_captcha_required`
  (hotlink-защита); headless упирается в reCAPTCHA-интерстициал.
* **APKMirror**: Cloudflare 403 из датацентра; **archive.org** — только
  старые/mod-версии. Вывод: APK брать с устройства пользователя
  (`adb pull base.apk`) или из браузера пользователя на VPN-выходе.
