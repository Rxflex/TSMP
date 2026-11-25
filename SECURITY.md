# Security

[По-русски](#по-русски)

## Supported versions

Only the latest release gets fixes. The launcher updates itself to it when it
starts, unless `TSMP_NO_UPDATE` is set.

## Reporting a problem

Please do not open a public issue. Report it privately through GitHub:
**[open a security advisory](https://github.com/Rxflex/TSMP/security/advisories/new)**.
Only you and the maintainers see it. Say what you found, how to reproduce it,
and which version of the launcher, the session server or the game it was on.
The discussion and the fix happen in the advisory; it is made public once a
release with the fix is out.

## What is in scope

- **The launcher** (`tsmp.exe`): LAN discovery, the launcher-to-launcher
  protocol, and the copying of mods and saves. For example, a host that can
  make the guest's launcher write outside the Mods and save folders, or
  delete files instead of moving them to the backup folders.
- **The updater.** The launcher downloads `tsmp.exe` from this repository's
  latest GitHub release, checks that the file starts like a Windows program
  and, when GitHub publishes a SHA-256 digest for it (it does for current
  releases), that the file matches it, and only then puts it in place. Address files for new game versions are fetched from
  `mappings/` on the default branch, and the mod checks every address
  against the running game before it uses one. A way around these checks is
  in scope, and so is an update installed without the digest check.
- **The session server** (`tsmp-server`): joining a session without its
  invite, finding codes by guessing despite the limits, reading or changing
  traffic of a session you are not in, or a way to take the server down that
  its limits do not catch.
- **The mod** (`tsmp.ts4script`), where it accepts input from the other
  player's game.

## What is not

- **The host's script mods run on the guest's computer.** This is how
  sharing a Mods folder works, and the README warns about it: join only
  people you trust. A way for a host to run code on the guest *without*
  putting it in the shared Mods folder is in scope.
- **Anyone with an invite can join that session**, the same as anyone on the
  host's local network can. This is by design.
- Problems in The Sims 4 itself, the EA app or Steam.
- Release files are not code-signed, so Windows may warn about them. Every
  release lists the SHA-256 checksums of its files, and everything can be
  built from source.

## По-русски

Исправления выходят только для последнего выпуска; лаунчер сам обновляется
до него при запуске, если не задана переменная `TSMP_NO_UPDATE`.

Об уязвимости не пишите в открытый issue. Сообщите о ней приватно, через
**[security advisory](https://github.com/Rxflex/TSMP/security/advisories/new)** —
его видят только вы и сопровождающие. Опишите, что нашли, как это
воспроизвести и на какой версии лаунчера, сервера сессий или игры.

К уязвимостям относятся ошибки лаунчера (обнаружение в сети, протокол между
лаунчерами, копирование модов и сохранений), обход проверок при обновлении
(проверка того, что загруженный `tsmp.exe` похож на программу Windows и
совпадает с SHA-256, который публикует GitHub, и проверка адресов перед
использованием), а также установка обновления без сверки с SHA-256, ошибки сервера сессий (вход в сессию без приглашения,
подбор кодов, доступ к чужому трафику, отказ в обслуживании в обход
ограничений) и мода там, где он принимает данные от чужой игры.

Не относятся: то, что скриптовые моды хоста запускаются у гостя (так устроена
общая папка Mods, подключайтесь только к тем, кому доверяете), то, что по
приглашению в сессию может войти любой, у кого оно есть, ошибки самой
The Sims 4, EA app и Steam, а также отсутствие цифровой подписи у файлов
выпуска.
