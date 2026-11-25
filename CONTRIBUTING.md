# Contributing to TSMP

Thanks for helping. Bug reports, reports of how a game went, translations and
pull requests are all welcome. [Коротко по-русски](#коротко-по-русски).

## Two rules first

These keep the project safe to publish:

- **No code or assets of the game in the repository.** Never commit decompiled
  or extracted game code, game files or game art. `/decompiled/` is in
  `.gitignore` for local research only, and `/captures/` (captured traffic)
  stays on your computer too. Findings about the game go into
  [`docs/internals.md`](docs/internals.md) as descriptions, with how to
  reproduce them.
- **Nothing that unlocks content a player does not own.** TSMP only switches
  packs off, through the game's own `-disablepacks:` option.

## Reporting

Open an issue with one of the [templates](https://github.com/Rxflex/TSMP/issues/new/choose):
a bug, a report of how a game went, or an idea. Reports from real games help
most, especially for the parts so far tried only in tests: playing over the
internet, families of their own, Create-a-Sim for the guest and different
lots.

Security problems go privately to the
[advisories](https://github.com/Rxflex/TSMP/security/advisories/new); see
[SECURITY.md](SECURITY.md).

## Building and testing

    cargo test                               # everything except the UI itself
    cargo build --release -p tsmp-launcher   # -> target/release/tsmp.exe
    cargo run -p tsmp-cli -- info            # show the detected game and packs
    cargo build -p tsmp-server --example live_pair   # lets the mod tests use the real relay and server
    cargo build --release -p tsmp-server     # -> target/release/tsmp-server
    python -m unittest discover -s mod/tests
    python mod/build.py                      # -> dist/tsmp.ts4script

`mod/build.py` needs a Python 3.7 interpreter, because the game only loads
3.7 bytecode. Unpack the official embeddable Python 3.7 zip into
`.tools/python37/`, or pass `--python37 <exe>`.

Before a pull request, run `cargo test` and
`python -m unittest discover -s mod/tests`. If the change touches what
happens in the game, try it in the game too and say how in the pull request:
one or two computers, host or guest, local network or session server, and the
game version.

## The mod

The script mod in `mod/` runs inside the game, which has its own rules:

- **Python 3.7.** The game ships Python 3.7 and loads only its bytecode. Do
  not use anything newer: no `:=`, no positional-only parameters, no
  `list[int]` or `dict | dict`, no `match`, no `str.removeprefix`. The
  release workflow runs the mod tests on Python 3.7.
- **Never block the simulation thread.** The mod's code runs on the game's
  simulation thread, once per tick (see
  [`docs/internals.md`](docs/internals.md#client---simulation-traffic)).
  Anything that waits stops the game: no blocking sockets, no `time.sleep`,
  no waiting for a file or for the launcher. Do work in small steps across
  ticks, the way `tsmp/bridge.py` pumps a non-blocking socket. Do not touch
  the simulation from another thread.
- **Keep the mod thin.** Discovery, file transfer and the link between the
  two computers belong in the launcher, in Rust.

## Game versions and mappings

Branches are named after the game version they were built and tried
against. Send pull requests to the current default branch.

Build mode sync depends on addresses inside `Simulation_x64.dll`, which move
with every build of the game. They are data: one file per game version,
`mappings/<version>.json`, with the build it describes and the function each
hooked slot must point to. The launcher carries the files known when it was
built and fetches `mappings/<version>.json` from the default branch for any
other version, so a new file there reaches players without a new launcher.

A pull request that adds a mapping should say how the addresses were found
and that building together was tried with it in two games. Never include
bytes or code copied from the game, only the addresses.

## Translations

See [Translations](README.md#translations) in the README. Each language is
one Fluent file in `crates/tsmp-launcher/locales/`;
`cargo test -p tsmp-launcher` checks that no message or variable is missing.
Corrections by native speakers are especially welcome.

## Commits

- A short subject in the imperative, without a trailing full stop: "Keep the
  guest's sims off a host lot that loads again".
- A body that explains why, and what changes for players, in plain
  sentences, wrapped at about 72 characters.
- One change per commit where you can.

## Releases

Releases are made by the maintainers:

1. Describe the release in [CHANGELOG.md](CHANGELOG.md) and
   [CHANGELOG.ru.md](CHANGELOG.ru.md), in a section `## v<version> — <title>`.
2. Set the same version in `crates/tsmp-launcher/Cargo.toml`.
3. Push a tag `v<version>`.

`.github/workflows/release.yml` refuses a tag that does not match the
launcher's version or that the changelogs do not describe, then tests and
builds everything and publishes the release. Running launchers find it and
update. Started by hand from the Actions tab, the workflow does everything but
publish, and keeps the files and the page with the run.

## Коротко по-русски

Мы рады любой помощи: сообщениям об ошибках, рассказам о том, как прошла
игра, переводам и пул-реквестам. Писать можно по-русски.

- **Никакого кода и ресурсов игры.** Не добавляйте в репозиторий
  декомпилированный или извлечённый код игры, её файлы и графику. Папки
  `/decompiled/` и `/captures/` (записанный трафик) есть в `.gitignore` и
  остаются только на вашем компьютере. Находки об игре описывайте словами в
  `docs/internals.md` и объясняйте, как их воспроизвести.
- **Ничего, что открывает контент, которого у игрока нет.** TSMP только отключает дополнения
  штатным параметром `-disablepacks:`.
- **Сборка и тесты** — команды в разделе [Building and testing](#building-and-testing)
  выше. Перед пул-реквестом запустите `cargo test` и
  `python -m unittest discover -s mod/tests`, а если изменение касается игры,
  проверьте его в игре и напишите, как именно.
- **Мод работает на Python 3.7** и выполняется в потоке симуляции игры.
  Возможности Python новее 3.7 не подойдут. Код не должен ничего ждать: ни
  блокирующих сокетов, ни `time.sleep`, ни ожидания файла, иначе встанет вся
  игра. Работу нужно делить на короткие шаги по тикам.
- **Адреса для строительства** лежат по одному файлу на версию игры в
  `mappings/<версия>.json`. В пул-реквесте с новым файлом расскажите, как
  нашли адреса, и подтвердите, что строительство вдвоём с ним проверено в
  двух играх.
- **Коммиты:** короткий заголовок в повелительном наклонении (по-английски,
  как в истории проекта) и тело, объясняющее, зачем это нужно.
- **Выпуски** делают сопровождающие: описание в `CHANGELOG.md` и
  `CHANGELOG.ru.md`, версия в `crates/tsmp-launcher/Cargo.toml`, затем тег
  `v<версия>`.

Об уязвимостях сообщайте приватно, через
[advisories](https://github.com/Rxflex/TSMP/security/advisories/new).
