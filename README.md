# SoulSaveEditor

Open-source save editor for **Soul Knight** (Android, root required).

Based on **SKDEditor 3.8** by [HTCheater](https://github.com/HTCheater) — original project: [HTCheater/SKDEditor](https://github.com/HTCheater/SKDEditor) (GPL-2.0). The original repository was archived by its author in March 2026; this fork continues it as an independent, community-maintained tool. The original README is preserved at [`docs/ORIGINAL_README.md`](docs/ORIGINAL_README.md).

## Changes vs SKDEditor 3.8

- Rebranded: application ID `com.n3xumc0r3.soulsaveeditor`, app name `SoulSaveEditor`, version `4.0.0-fork.1` (upstream was `com.chichar.skdeditor` / `3.8`)
- Upgraded Gradle configuration for current build environments
- Everything else is inherited from upstream 3.8 as-is (see `docs/ORIGINAL_README.md` for features: file manager, backup manager, spoofer)

## Research

Reverse-engineering notes and tools for Soul Knight 8.7.0 save/cloud systems live in [`docs/`](docs/) and [`tools/`](tools/):

- [`docs/REVERSING.md`](docs/REVERSING.md) — metadata decode, local save crypto
- [`docs/CLOUD-SAVE.md`](docs/CLOUD-SAVE.md) — cloud save architecture (GPGS legacy + ChillyRoom cloud), ban/risk system
- [`docs/CLOUD-DUMP-870.md`](docs/CLOUD-DUMP-870.md) — actual GPGS snapshot dump: cloud save is **plain UTF-16LE JSON**
- [`tools/`](tools/) — frida snapshot dump/read script, decryptors, dump analyzers

Personal save data (dumps, decodes) is **never** committed — see `.gitignore`.

## License

This project is licensed under **GPL-2.0**, same as the original. See [LICENSE](LICENSE). Original copyright (c) HTCheater; modifications (c) 2026 n3xumc0r3.

## Disclaimer

This tool edits local game save files for personal backup purposes. It is not affiliated with or endorsed by ChillyRoom. Use at your own risk.
