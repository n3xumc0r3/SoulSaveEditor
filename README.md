# SoulSaveEditor

Open-source save editor for **Soul Knight** (Android, root required).

Based on **SKDEditor 3.8** by [HTCheater](https://github.com/HTCheater) — original project: [HTCheater/SKDEditor](https://github.com/HTCheater/SKDEditor) (GPL-2.0). The original repository was archived by its author in March 2026; this fork continues it as an independent, community-maintained tool. The original README is preserved at [`docs/ORIGINAL_README.md`](docs/ORIGINAL_README.md).

## Changes vs SKDEditor 3.8

- Rebranded: application ID `com.n3xumc0r3.soulsaveeditor`, app name `SoulSaveEditor`, version `4.0.0-fork.1` (upstream was `com.chichar.skdeditor` / `3.8`)
- Upgraded Gradle configuration for current build environments
- Everything else is inherited from upstream 3.8 as-is (see `docs/ORIGINAL_README.md` for features: file manager, backup manager, spoofer)

## Research

Reverse-engineering notes and tools for Soul Knight 8.7.0 save/cloud systems live in [`docs/`](docs/) and [`tools/`](tools/):

- [`docs/REVERSING.md`](docs/REVERSING.md) — metadata decode, local save crypto, JSON-parser defaults, CRDE field map
- [`docs/LOCAL-SAVE-870.md`](docs/LOCAL-SAVE-870.md) — how 8.7.0 stores/parses local saves: section file map, full field lists of 19 save classes, missing-field behavior
- [`docs/PREFS-870.md`](docs/PREFS-870.md) — playerprefs as the second half of the local save (real-device dump): 2105 keys / 183 patterns, dual `<UID>_`/`LOCAL` namespaces with bool-casing rule, gems ledger sync, URL-escaping, has_*_data flags, and the proven absence of Rijndael keys in prefs
- [`docs/CLOUD-SAVE.md`](docs/CLOUD-SAVE.md) — cloud save architecture (GPGS legacy + ChillyRoom cloud), ban/risk system
- [`docs/CLOUD-DUMP-870.md`](docs/CLOUD-DUMP-870.md) — actual GPGS snapshot dump: cloud save is **plain UTF-16LE JSON**; cloud **write path proven** and **first real edit committed** (sha-verified, 11.11.2026)
- [`docs/EDITOR.md`](docs/EDITOR.md) — quickstart for the flagship save editor
- [`docs/GUIDE-TOP-SAVE.md`](docs/GUIDE-TOP-SAVE.md) — step-by-step cookbook: full top-save patch (both halves, exact commands, longplay-tier values)
- [`tools/sk_save_editor.py`](tools/sk_save_editor.py) — **flagship save editor**: adb pull/push of the game profile (root), auto-detects every crypto layer (plain JSON / UTF-16LE / legacy XOR game.data / DES `crst1`+`iambo` / SDK-DES / Rijndael-AES-256 with per-install keys from playerprefs), curated presets (gems with consistent ledger, unlock heroes/pets, research blueprints, max materials…), path-based edits, per-write backups + round-trip verification, protected-profile-field guards, built-in `selftest`
- [`tools/sk_prefs.py`](tools/sk_prefs.py) — playerprefs (prefs.xml) parser/editor: schema/mirror/blob tools, surgical text edits with backup + round-trip verify, gems ledger, unlock-heroes with per-namespace bool casing
- [`tools/`](tools/) — frida snapshot dump/read/write scripts, cloud-save JSON patcher, local-save decrypt/encrypt CLI (`sk_local_save.py`), decryptors, dump analyzers

Personal save data (dumps, decodes) is **never** committed — see `.gitignore`.

## License

This project is licensed under **GPL-2.0**, same as the original. See [LICENSE](LICENSE). Original copyright (c) HTCheater; modifications (c) 2026 n3xumc0r3.

## Disclaimer

This tool edits local game save files for personal backup purposes. It is not affiliated with or endorsed by ChillyRoom. Use at your own risk.
