"""Write the page of a release: its picture, buttons to download the
launcher, how to start, what is new from the changelog, in English and in
Russian, the files to download with their checksums, and every change since
the last release, grouped by the part of the project it touched.

    python .github/release/notes.py v0.0.1 DIST_DIR NOTES.md TITLE.txt

DIST_DIR holds the files the release publishes. Fails when the changelog
has no section for the version, so that no release goes out undescribed.
Once the version's tag exists, the changes are those up to the tag, so the
page of a published release can be written again. Pictures are linked at
the commit ASSETS_REF names, the checked out commit when it is not set.
Runs on the Python the release job has (3.7 or newer), with git and nothing
else.
"""

import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# What each published file is, in the order they are listed: its icon in
# docs/assets/download/ and what it is, in plain words.
FILES = (
    ("tsmp.exe", "icon-launcher.png",
     "<b>The one to download.</b> The program for Windows that sets everything up, with the mod inside."),
    ("tsmp.ts4script", "icon-mod.png",
     "The mod alone, for the Mods folder by hand. Not needed with tsmp.exe."),
    ("tsmp-server-linux-x86_64", "icon-server.png",
     "For playing over the internet: the session server, to run on any x86_64 Linux computer."),
    ("SHA256SUMS.txt", "icon-checksum.png",
     "Checksums, to check that the files downloaded whole."),
)

# Parts of the project, by the paths their files are under. A change belongs
# to the part most of its files are in.
AREAS = (
    ("Mod", ("mod/",)),
    ("Launcher", ("crates/tsmp-launcher/",)),
    ("Network and session server", ("crates/tsmp-net/", "crates/tsmp-server/")),
    ("Game detection and tools", ("crates/tsmp-core/", "crates/tsmp-cli/", "mappings/")),
    ("Documentation", ("docs/", "README", "CHANGELOG")),
    ("Releases and repository", (".github/", "Cargo.", "LICENSE", ".gitignore")),
)


def git(*args):
    return subprocess.check_output(("git",) + args, cwd=ROOT).decode("utf-8")


def tagged(version):
    """Whether the tag of the version exists already."""
    with open(os.devnull, "w") as quiet:
        return subprocess.call(("git", "rev-parse", "--verify", "--quiet", "refs/tags/" + version),
                               cwd=ROOT, stdout=quiet, stderr=quiet) == 0


class Changelog:
    """One file of release descriptions: `## v<version> — <title>` sections."""

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            self.text = f.read()

    def section(self, version):
        """(title, body) of the version's section, None when it has none."""
        match = re.search(r"^## {}(?: [—-] ([^\n]*))?\n(.*?)(?=^## |\Z)".format(re.escape(version)), self.text,
                          re.M | re.S)
        if match is None:
            return None
        return (match.group(1) or "").strip(), unwrap(match.group(2).strip())


def unwrap(markdown):
    """Join the lines a paragraph or list item is wrapped over: a release page
    keeps every line break, where a README would join them."""
    lines = []
    for line in markdown.split("\n"):
        continues = line.startswith("  ") and not line.lstrip().startswith(("- ", "* "))
        starts = re.match(r"\s*(?:[-*] |\d+\. |#|\||<|>|```)", line)
        if lines and lines[-1].strip() and line.strip() and (continues or not starts):
            lines[-1] += " " + line.strip()
        else:
            lines.append(line)
    return "\n".join(lines)


class Change:
    def __init__(self, sha, subject, paths):
        self.sha = sha
        self.subject = subject
        self.area = self._area(paths)

    @staticmethod
    def _area(paths):
        counts = [sum(1 for path in paths if path.startswith(prefixes)) for _, prefixes in AREAS]
        best = max(range(len(AREAS)), key=lambda index: counts[index])
        return AREAS[best][0] if counts[best] else AREAS[-1][0]


class History:
    """The changes since the previous release: up to the version's tag once
    it exists, so that the page of a published release can be written again
    later, else up to the commit being released."""

    def __init__(self, version):
        self.version = version
        self.previous = self._previous(version)
        self.end = version if tagged(version) else "HEAD"
        span = "{}..{}".format(self.previous, self.end) if self.previous else self.end
        self.changes = self._changes(span)

    @staticmethod
    def _previous(version):
        """The newest version tag before `version`, None for the first."""
        def key(tag):
            return tuple(int(part) for part in tag[1:].split("."))

        # Only releases count: a tag such as v0.2.0-rc.1 is not one.
        final = re.compile(r"^v\d+(\.\d+)*$")
        tags = [tag for tag in git("tag", "--list", "v*").split()
                if final.match(tag) and tag != version and (not final.match(version) or key(tag) < key(version))]
        return max(tags, key=key) if tags else None

    @staticmethod
    def _changes(span):
        changes = []
        log = git("log", "--no-merges", "--format=%x00%H%x09%s", "--name-only", span)
        for entry in log.split("\0")[1:]:
            head, _, files = entry.partition("\n")
            sha, _, subject = head.partition("\t")
            changes.append(Change(sha, subject, [line for line in files.splitlines() if line.strip()]))
        return changes


def size(path):
    value = os.path.getsize(path)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return ("{:.0f} {}" if unit == "B" else "{:.1f} {}").format(value, unit)
        value /= 1024.0


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class Notes:
    def __init__(self, version, repository, dist):
        self.version = version
        self.repository = repository
        self.dist = dist
        english = Changelog(os.path.join(ROOT, "CHANGELOG.md")).section(version)
        if english is None:
            raise SystemExit("CHANGELOG.md has no section for {}: describe the release first".format(version))
        self.title, self.body = english
        russian = Changelog(os.path.join(ROOT, "CHANGELOG.ru.md")).section(version)
        self.russian = russian[1] if russian else ""
        self.history = History(version)
        # The pictures and the READMEs are linked at a commit, not at the
        # tag: a commit never changes, and a page written again later can
        # show pictures added after the release.
        self.ref = os.environ.get("ASSETS_REF") or git("rev-parse", "HEAD").strip()

    def url(self, path):
        return "https://github.com/{}/{}".format(self.repository, path)

    def asset(self, path):
        return "https://raw.githubusercontent.com/{}/{}/docs/assets/{}".format(self.repository, self.ref, path)

    def site(self, path=""):
        owner, _, name = self.repository.partition("/")
        return "https://{}.github.io/{}/{}".format(owner.lower(), name, path)

    def write_checksums(self):
        """SHA256SUMS.txt for the published files, beside them."""
        names = [name for name, _, _ in FILES if name != "SHA256SUMS.txt" and os.path.exists(os.path.join(self.dist, name))]
        with open(os.path.join(self.dist, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
            for name in names:
                f.write("{}  {}\n".format(sha256(os.path.join(self.dist, name)), name))

    def heading(self):
        return "TSMP {}{}".format(self.version, " — " + self.title if self.title else "")

    def render(self):
        parts = [
            '<p align="center">\n  <img src="{}" alt="TSMP: two houses at night, joined by one path" '
            'width="100%">\n</p>'.format(self.asset("social-preview.jpg")),
            self.download(),
            # Most players read Russian: their part comes first, folded, so
            # that it is one line for everyone else.
            self.russian_part(),
            self.start(),
            "## What's new\n\n" + self.body,
            self.files(),
            self.changes(),
        ]
        return "\n\n".join(parts) + "\n"

    def download(self):
        """The two big buttons, English and Russian, for the launcher."""
        link = self.url("releases/download/{}/tsmp.exe".format(self.version))
        button = '  <a href="{}"><img src="{}" alt="{}" width="300"></a>'
        exe = os.path.join(self.dist, "tsmp.exe")
        facts = ["Windows 10 and 11"] + ([size(exe)] if os.path.exists(exe) else []) + ["free"]
        return "\n".join([
            '<p align="center">',
            button.format(link, self.asset("download/download-en.png"), "Download for Windows: tsmp.exe, free"),
            "  &nbsp;",
            button.format(link, self.asset("download/download-ru.png"),
                          "Скачать для Windows: tsmp.exe, бесплатно"),
            "  <br>",
            "  {}".format(" · ".join(facts)),
            "  <br>",
            "  По-русски: инструкция в блоке ниже ↓",
            "</p>",
        ])

    def start(self):
        return ("## How to start\n\n"
                "1. **Download tsmp.exe** with the button above, on both computers. It is one file and there "
                "is nothing to install: it puts the mod in place itself.\n"
                "2. **Run it.** tsmp.exe is not code-signed, so the first time Windows may say "
                "**Windows protected your PC**: click **More info**, then **Run anyway**. Some antivirus "
                "programs are wary of new unsigned programs too.\n"
                "3. **Allow it through the firewall** when Windows asks: allow it on private networks, or the "
                "host will not be found.\n"
                "4. **One hosts, the other joins.** One of you chooses **Host a session**, the other **Join a "
                "session** and presses **Sync**. Then both press **Start game** and load the shared save. Keep "
                "TSMP open while you play. On different networks you also need a session server (a friend's "
                "or your own): the host enters its address under **Over the internet**, presses **Open** and "
                "sends the invite; the other pastes it in **Join a session** and presses **Connect**.\n\n"
                "**You need** Windows 10 or 11 and The Sims 4 on each computer: each player their own copy, "
                "both at the same game version. In the game, turn on Game Options → Other → **Enable Custom "
                "Content and Mods** and **Script Mods Allowed**, then restart the game.\n\n"
                "TSMP is experimental: **What's new** below says what was tried on two computers and what only "
                "in tests.\n\n"
                "> [!WARNING]\n"
                "> Joining runs the host's script mods on your computer. Only join people you trust.\n\n"
                "Step by step, with pictures: [the README]({}) · [the site]({}).".format(
                    self.url("blob/{}/README.md#playing".format(self.ref)), self.site()))

    def russian_part(self):
        """How to start and what is new, in Russian, folded away."""
        lines = [
            "<details>",
            "<summary><b>🇷🇺 По-русски: как начать и что нового</b></summary>",
            "",
            "### Как начать",
            "",
            "1. **Скачайте tsmp.exe** кнопкой выше, на обоих компьютерах. Это один файл, и устанавливать "
            "ничего не нужно: мод он сам положит куда нужно.",
            "2. **Запустите его.** У tsmp.exe нет цифровой подписи, поэтому в первый раз Windows может написать "
            "**«Система Windows защитила ваш компьютер»**: нажмите **Подробнее**, затем **Выполнить в любом "
            "случае**. Некоторые антивирусы тоже настороженно относятся к новым программам без подписи.",
            "3. **Разрешите доступ через брандмауэр**, когда Windows спросит: разрешите для частных сетей, "
            "иначе хост не будет найден.",
            "4. **Один создаёт сессию, второй входит в неё.** Один выбирает **Создать сессию**, второй — "
            "**Войти в сессию** и нажимает **Синхронизировать**. Потом оба нажимают **Запустить игру** и "
            "загружают общее сохранение. Не закрывайте TSMP во время игры. Из разных сетей нужен ещё сервер "
            "сессий (друга или свой): хост вводит его адрес в блоке **Через интернет**, нажимает **Открыть** и "
            "отправляет приглашение; второй вставляет его в **Войти в сессию** и нажимает **Подключиться**.",
            "",
            "**Нужно:** Windows 10 или 11 и The Sims 4 на каждом компьютере — у каждого своя копия, обе одной "
            "и той же версии. В игре включите Настройки игры → Другое → **«Включить пользовательский контент "
            "и моды»** и **«Разрешить моды-скрипты»**, затем перезапустите игру.",
            "",
            "Проект экспериментальный: что проверено на двух компьютерах, а что пока только в тестах, написано "
            "в разделе «Что нового».",
            "",
            "> [!WARNING]",
            "> При подключении скриптовые моды хоста запускаются на вашем компьютере. Подключайтесь только к "
            "тем, кому доверяете.",
            "",
            "По шагам, с картинками: [README]({}) · [сайт]({}).".format(
                self.url("blob/{}/README.ru.md#как-играть".format(self.ref)), self.site("ru/")),
        ]
        if self.russian:
            lines += ["", "### Что нового", "", self.russian]
        lines += ["", "</details>"]
        return "\n".join(lines)

    def files(self):
        rows = ["| File | What it is | Size | SHA-256 |", "| --- | --- | --- | --- |"]
        for name, icon, what in FILES:
            path = os.path.join(self.dist, name)
            if not os.path.exists(path):
                continue
            link = self.url("releases/download/{}/{}".format(self.version, name))
            digest = "" if name == "SHA256SUMS.txt" else "<code>{}…</code>".format(sha256(path)[:16])
            picture = '<img src="{}" alt="" width="32" height="32" align="absmiddle">'.format(
                self.asset("download/" + icon))
            rows.append("| {}&nbsp;[**{}**]({}) | {} | {} | {} |".format(picture, name, link, what,
                                                                        size(path).replace(" ", "&nbsp;"), digest))
        game = self.game_version()
        needs = "**Needs** Windows 10 or 11 and The Sims 4{} with script mods enabled in the game options.".format(
            " " + game if game else "")
        # Before the first release there is no launcher out there to update.
        if self.history.previous:
            needs += " Launchers already installed update themselves to this release."
        return "## Files\n\n" + "\n".join(rows) + "\n\n" + needs

    def changes(self):
        changes = self.history.changes
        since = "since {}".format(self.history.previous) if self.history.previous else "in this first release"
        lines = ["## All changes {} ({} commits)".format(since, len(changes)), ""]
        for area, _ in AREAS:
            group = [change for change in changes if change.area == area]
            if not group:
                continue
            lines.append("<details>\n<summary><b>{}</b> · {}</summary>\n".format(area, len(group)))
            for change in group:
                lines.append("- {} ([{}]({}))".format(change.subject, change.sha[:7],
                                                      self.url("commit/" + change.sha)))
            lines.append("\n</details>\n")
        if self.history.previous:
            lines.append("**Full list:** [{}...{}]({})".format(
                self.history.previous, self.version,
                self.url("compare/{}...{}".format(self.history.previous, self.version))))
        return "\n".join(lines).rstrip()

    def game_version(self):
        """The newest game version the mappings describe, those of the
        version's tag once it exists."""
        if self.history.end == "HEAD":
            names = os.listdir(os.path.join(ROOT, "mappings"))
        else:
            names = [os.path.basename(path) for path in
                     git("ls-tree", "--name-only", self.history.end, "mappings/").split()]
        names = [name[:-5] for name in names if name.endswith(".json")]
        versions = [name for name in names if re.match(r"^\d+(\.\d+)+$", name)]
        return max(versions, key=lambda name: tuple(int(part) for part in name.split("."))) if versions else ""


def main(argv):
    if len(argv) != 5:
        raise SystemExit(__doc__)
    version, dist, notes_path, title_path = argv[1:]
    repository = os.environ.get("GITHUB_REPOSITORY", "Rxflex/TSMP")
    notes = Notes(version, repository, dist)
    notes.write_checksums()
    with open(notes_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(notes.render())
    with open(title_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(notes.heading())


if __name__ == "__main__":
    main(sys.argv)
