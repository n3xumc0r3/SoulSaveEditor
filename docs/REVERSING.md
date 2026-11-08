# Soul Knight 8.7.0 — заметки реверс-инжиниринга

> Исследование выполнено для **SoulSaveEditor** — легального GPL-2.0 форка
> [SKDEditor](https://github.com/rosstonovsky/SKDEditor) автора **HTCheater (rosstonovsky)**.
> Цель — интероперабельность редактора сохранений с актуальной версией игры.
> Никакой монетизации и обхода защиты от читов в мультиплеере: игра однопользовательская,
> сейвы лежат на устройстве игрока.

Дата исследования: 2026-11. Объект: **Soul Knight 8.7.0** (APKCube `.apks`,
679 467 257 байт), **arm64-v8a**, **Unity 2022.3.62f2**, **IL2CPP metadata v31**.

---

## 1. Что внутри APK

`.apks` — это zip из split-APK: `base.apk`, `split_config.arm64_v8a.apk`,
`split_UnityDataAssetPack.apk`. Весь нативный код — в arm64-сплите:

| Библиотека | Размер | Роль |
|---|---:|---|
| `libil2cpp.so` | 272,2 МБ | скомпилированный C# (IL2CPP) |
| `libunity.so` | 22,1 МБ | Unity-рантайм (2022.3.62f2) |
| `libunitydatachannel.so` | 10,8 МБ | канал данных Unity |
| **`libanogs.so`** | 5,5 МБ | **Tencent ACE — античит** |
| `libanort.so` | 1,9 МБ | ACE-компонент |
| `libgilzoide-sqlite-net.so` | 5,4 МБ | SQLite (локальные БД) |
| **`libnative-lib.so`** | 1,3 МБ | **собственный SDK ChillyRoom (крипто!)** |
| `libxlua.so` | 1,0 МБ | **Lua — механика hot-update** |
| `libpglarmor.so`, `libnms.so`, `libtax.so`, `libu8t.so` | — | обфускация/анти-тампер (PGL) |
| аналитика/реклама | — | Bugly, AppLovin, APMInsight, tt_ugen и пр. |

В `base.apk` также лежит `assets/__acinfo.tsb` — конфиг Tencent ACE.
При динамических экспериментах на эмуляторе ACE может мешать: учитывай это.

Метаданные IL2CPP: `assets/bin/Data/Managed/Metadata/global-metadata.dat`
(54 307 520 байт) — **защищены анти-дамп маской**, см. раздел 2.

## 2. Защита `global-metadata.dat` — взломана

Файл **не зашифрован целиком** — применена дешёвая маска XOR-0x66 с заменённой магией:

* в шапке лежит `0x12724394` вместо `0xFAB11BAF`, поле version заполнено мусором;
* **байты `[0x0000, 0x1000)` страницы 0 XOR-0x66** — это заголовок + первые записи таблицы литералов;
* **каждая страница начиная с 1-й: XOR-0x66 только в окне `[0x1000, 0x5000)`** (16 КиБ),
  остальное — открытый текст;
* **исключение:** окно `[0x1000, 0x5000)` именно страницы 0 защищено сильнее
  (равномерное распределение байтов — настоящая крипта, статически не снимается);
  там лежат записи таблицы литералов 480..2527 — теряем ~2048 из 76 645 литералов (2,7%);
* после снятия маски магия и версию нужно **дописать руками**:
  `0xFAB11BAF` + **version 31** (соответствует Unity 2022.3.62f2).

Корректность схемы сверена перекрёстно: все 31 регион встык до EOF, все таблицы
делятся на размер записи без остатка (76 645×8 литералов, 376 499×36 методов,
46 241×88 typedef’ов, 193×40 images = 193×64 assemblies), все 46 241 тип имеют
валидные диапазоны методов/полей. Первая версия декодера (страница 0 целиком)
незаметно портила `[0x5000, 0x10000)` — это поймала сверка таблицы литералов.

Настоящий заголовок: магия (4) + версия (4) + **31 пара offset/size = 256 байт**.
Все регионы идут **встык**, последний заканчивается ровно на EOF — структура целая,
игра не перемещала секции, только маскировала байты.

Декодер: [`tools/sk870_metadata_decode.py`](../tools/sk870_metadata_decode.py)
(проверен на реальном файле: `crst1` @ `0x20F1B4`, `iambo` @ `0x2239CE`,
`mscorlib`, `Assembly-CSharp` — всё на месте; полная таблица регионов в
[RE-NOTES.md](RE-NOTES.md)).

**Статус статики:** Il2CppDumper принимает декодированный файл и находит
CodeRegistration/MetadataRegistration, но падает (integer overflow) при сканировании
272-МБ `libil2cpp.so`. Полный `dump.cs` надёжнее снять **динамически**:
`frida-il2cpp-bridge` или `Zygisk-Il2CppDumper` прямо на эмуляторе — строки и классы
из декодированной метадаты уже дают всю наводку (см. разделы 4–6).

## 3. Формат сохранений (как игра парсит сейвы)

Профиль игры: `Android/data/com.ChillyRoom.DungeonShooter/files/` + `shared_prefs/`.
Облачная система (GPV2/GPV4-каналы, миграция 5.5.2, SafeCheck) — в
[CLOUD-SAVE.md](CLOUD-SAVE.md).

| Файл | Формат | Статус на 8.7.0 |
|---|---|---|
| `shared_prefs/*.v2.playerprefs.xml` | открытый XML | без изменений; здесь же привязки аккаунтов и (ожидаемо) ключи Rijndael |
| `files/battles.data` | открытый JSON | без изменений |
| `files/game.data` | повторяющийся XOR | легаси-ключ не подтверждён на 8.7.0 — нужна живая копия |
| `statistic*.data` и прочие `*.data` | `Base64(DES-CBC-PKCS5(JSON))` | ключ **`crst1\0\0\0` жив** (литерал в метадате); IV предположительно легаси `Ahbool\0\0` |
| сейвы персонажей (новое) | **Rijndael-128 (AES)** | миграция в 8.7.0, см. ниже |

### 3.1 Миграция на Rijndael-128 (главное новое в 8.7.0)

В метадате однозначно виден новый крипто-слой сейвов:

* классы `Abo.CryptUtil`, `AesEncryption`, `SDK.SaveUtil.SaveDataWithRijndael`,
  `AddSaveRijndaelDataTask` (миграция старых сейвов), `AfterFetchCloudSave`;
* параметры ключей в конфиге: **`DataAES256Key`**, **`DataRijndael128Key`**, **`DataRijndaelIV`**;
* суффиксы файлов: `AesSuffix` / `RijndaelSuffix` / `NewDataSuffix`
  (в коде фигурируют `.aes` / `.rijndael`);
* рядом живёт и `XORCrypt` (для чего именно — уточнить по дампу).

Значения ключей **не захардкожены** — они per-install и ожидаются в
`shared_prefs/*.v2.playerprefs.xml`. Это задача №1 для эмулятора: забрать XML
и вычитать `DataRijndael128Key`/`DataRijndaelIV` (или `DataAES256Key`).

### 3.2 Легаси-схемы (для старых сейвов и SDK-строк)

Утилита со всеми схемами: `download/sk870_crypto.py` (selftest проходит),
в репо — [`tools/sk870_crept_crypto.py`](../tools/sk870_crept_crypto.py).

| Схема | Параметры | Статус |
|---|---|---|
| DES statistic | key `crst1\0\0\0`, IV `Ahbool\0\0`, CBC/PKCS5, Base64 | ключ подтверждён, IV — легаси-наследие |
| DES default | key `iambo\0\0\0`, IV `Ahbool\0\0` | `iambo` жив как литерал |
| XOR game.data | `slcz}guc\x7fWmlkJ_` | не подтверждено на 8.7.0 |
| SDK poly-XOR | см. раздел 5 | **подтверждено дизасемблером** |
| SDK DES | key `Ahbool\0\0`, IV `abo123\0\0` | **подтверждено** (выводится из dex) |

Забавная деталь: старый IV `Ahbool` в 8.7.0 **повышен до ключа** SDK-DES,
а новый IV — `abo123` («Abo» — тег автора крипто-классов в C#).

## 4. Нативная крипто SDK (`libnative-lib.so`) — снята полностью

JNI-экспорты (по ~280 байт каждый), дизассемблированы:

```
Java_com_chillyroomsdk_cyphertest_MainActivity_encrept
Java_com_chillyroomsdk_sdkbridge_util_CreptUtil_decrept
```

Алгоритм — самоинверсный байтовый шифр:

```
w15 = data[i]
w16 = key[i % keyLen]
w14 = ((i % 5) * (i % 15)) % 92      ; позиционный полином
out[i] = data[i] ^ w14 ^ w16
```

**`p[i] = c[i] ^ ((i%5)*(i%15) % 92) ^ key[i % keyLen]`** — это не DES и не AES:
простой XOR с полиномиальным байтом и повторяющимся ключом.

На Java-стороне `com.chillyroomsdk.sdkbridge.util.CreptUtil` в конструкторе
выводит DES-параметры из зашифрованных строк dex тем же шифром с ключом `PASSWORD`:

```
decrept("\x11(55(#TJ", "PASSWORD")  ->  b"Ahbool\x00\x00"   ; DES-ключ
decrept("1\"8ku|TJ",   "PASSWORD")  ->  b"abo123\x00\x00"   ; DES-IV
```

(в dexdump первая строка видна как `\485049(55(#` — экранирование непечатаемого
байта `0x11` сбивает с толку; сырые байты: `11 28 35 35 28 23 54 4A`).

Далее `CreptUtil` использует `DES/CBC/PKCS5Padding` для строк SDK.

Также в `libnative-lib.so` найдены PlayerPrefs-ключи `SaveFirst`, `SaveLast`,
`SavePackIdx` и др. — маркеры прогресса загрузки сейв-паков.

## 5. Обновления (как APK подтягивает контент)

* Сам APK обновляется через магазин (у APKCube-сборки свой канал).
* Игровой контент — **hot-update поверх Unity** (потому и `libxlua.so`):
  `HotUpdateManager`, `PatchListApi`, `PatchService`, `HotUpdateManifest`,
  `VersionCheck*`.
* Список патчей: `https://cdn2.chillyroom.com/game/soulknight/patches`;
  ассеты: `cdn.chillyroom.com/game/...`.
  Версии/манифесты подтягиваются при старте, недостающее докачивается в
  кэш Unity.

Практический вывод для редактора сейвов: hot-update может менять версии
геймплейных данных, но **формат сейв-файлов лежит в APK**, а не в патчах.

## 6. Облачная синхронизация (ChillyRoom-аккаунты)

* Аккаунты: `accounts.chillyroom.com`, `aas.chillyroom.com`
  (Account & Auth Service), шлюз `api-gateway.soulknight.chillyroom.com`;
  тестовый контур `*.chilly.tech`.
* Хранилище сейвов — blob: **`CloudPackage.V1.BackupClient.GetBlobSaveAsync`**.
* Каналы синхронизации зависят от магазина установки: `DefineCloudSaveChannelId`,
  у Huawei отдельный путь.
* Привязки аккаунтов в playerprefs: `cloudSaveId`, `unity.cloud_userid`,
  `UsedAccounts`, `account2*` — **`IdStripper` остаётся актуальным** для
  переноса сейвов между устройствами/аккаунтами.

## 7. Защита и что это значит для форка

* **Tencent ACE** (libanogs/libanort) — античит рантайма; статический
  разбор APK он не видит, но на эмуляторе может ругаться. Для работы
  редактора сохранений он препятствием не является (сейвы — локальные файлы).
* PGL-обёртки (libpglarmor/libnms) — анти-тампер нативных библиотек.
* Метадата-маска (раздел 2) — защита от «просто дампни strings», снята.
* Крипто сейвов — DES/Rijndael с ключами на устройстве: редактирование
  локальных сейвов своего устройства легитимно; облако трогать не нужно.

## 8. План дальше (нужен эмулятор)

1. Забрать профиль: `files/*.data` (все) + `shared_prefs/*.v2.playerprefs.xml`.
2. Из XML вычитать `DataRijndael128Key` / `DataRijndaelIV` / `DataAES256Key`
   → дописать схемы в `sk870_crypto.py`, проверить на живых сейвах.
3. Подтвердить/опровергнуть легаси XOR-ключ `game.data` на 8.7.0.
4. Динамический дамп (`frida-il2cpp-bridge`/`Zygisk-Il2CppDumper`) → полный
   `dump.cs` → точные сигнатуры парсинга сейвов (какие поля читает игра).
5. Сейвы до/после первого облачного логина — понять, что меняет синхронизация.

## 9. Карта артефактов

| Путь | Что это |
|---|---|
| `tools/sk870_metadata_decode.py` | декодер метадаты (проверен) |
| `tools/sk870_metadata_dump.py` | дамп имён типов/методов/полей из метадаты (46 241 тип) |
| `tools/sk870_crept_crypto.py` | poly-XOR + SDK DES (selftest) |
| `docs/RE-NOTES.md` | шпаргалка: регионы, оффсеты, строки, классы |
| `docs/CLOUD-SAVE.md` | облачная система сохранений (GPV2) и система банов |
| `download/sk870_crypto.py` (вне репо) | CLI по всем схемам сразу |

---

*SKDEditor © HTCheater (rosstonovsky), GPL-2.0. SoulSaveEditor — форк с атрибуцией,
без монетизации. Это исследование описывает форматы данных устройства пользователя
в целях интероперабельности и не обходит защиту серверов ChillyRoom.*
