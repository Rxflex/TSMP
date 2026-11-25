"""Write the page of a release: its picture, what is new from the changelog,
the files to download with their checksums, and every change since the last
release, grouped by the part of the project it touched.

    python .github/release/notes.py v0.0.1 DIST_DIR NOTES.md TITLE.txt

DIST_DIR holds the files the release publishes. Fails when the changelog
has no section for the version, so that no release goes out undescribed.
Runs on the Python the release job has (3.7 or newer), with git and nothing
else.
"""

import hashlib
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# What each published file is, in the order they are listed.
FILES = (
    ("tsmp.exe", "The launcher for Windows, with the mod inside. This is the one to download."),
    ("tsmp.ts4script", "The mod alone, for putting into the Mods folder by hand."),
    ("tsmp-server-linux-x86_64", "The session server for players on different networks, for Linux."),
    ("SHA256SUMS.txt", "The checksums of the files above."),
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
        return (match.group(1) or "").strip(), match.group(2).strip()


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
    """The changes since the previous release."""

    def __init__(self, version):
        self.version = version
        self.previous = self._previous(version)
        span = "{}..HEAD".format(self.previous) if self.previous else "HEAD"
        self.changes = self._changes(span)

    @staticmethod
    def _previous(version):
        """The newest version tag before `version`, None for the first."""
        def key(tag):
            return tuple(int(part) for part in re.findall(r"\d+", tag))

        tags = [tag for tag in git("tag", "--list", "v*").split() if tag != version and key(tag) < key(version)]
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

    def url(self, path):
        return "https://github.com/{}/{}".format(self.repository, path)

    def write_checksums(self):
        """SHA256SUMS.txt for the published files, beside them."""
        names = [name for name, _ in FILES if name != "SHA256SUMS.txt" and os.path.exists(os.path.join(self.dist, name))]
        with open(os.path.join(self.dist, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
            for name in names:
                f.write("{}  {}\n".format(sha256(os.path.join(self.dist, name)), name))

    def heading(self):
        return "TSMP {}{}".format(self.version, " — " + self.title if self.title else "")

    def render(self):
        parts = [
            '<p align="center">\n  <img src="https://raw.githubusercontent.com/{}/{}/docs/assets/social-preview.jpg" '
            'alt="TSMP: two houses at night, joined by one path" width="100%">\n</p>'.format(self.repository,
                                                                                            self.version),
        ]
        parts.append("## What's new\n\n" + self.body)
        if self.russian:
            parts.append("<details>\n<summary><b>По-русски</b></summary>\n\n{}\n\n</details>".format(self.russian))
        parts.append(self.downloads())
        parts.append(self.start())
        parts.append(self.changes())
        return "\n\n".join(parts) + "\n"

    def downloads(self):
        rows = ["| File | What it is | Size | SHA-256 |", "| --- | --- | --- | --- |"]
        for name, what in FILES:
            path = os.path.join(self.dist, name)
            if not os.path.exists(path):
                continue
            link = self.url("releases/download/{}/{}".format(self.version, name))
            digest = "" if name == "SHA256SUMS.txt" else "<code>{}…</code>".format(sha256(path)[:16])
            rows.append("| [**{}**]({}) | {} | {} | {} |".format(name, link, what, size(path), digest))
        game = self.game_version()
        needs = ("**Needs** Windows and The Sims 4{} with script mods enabled in the game options. Launchers "
                 "already installed update themselves to this release.").format(" " + game if game else "")
        return "## Download\n\n" + "\n".join(rows) + "\n\n" + needs

    def start(self):
        return ("## Getting started\n\n"
                "1. Both players download **tsmp.exe** and run it. Windows asks once to let it through the "
                "firewall: allow it on private networks.\n"
                "2. One chooses **Host a session**, the other **Join a session**; over the internet the host "
                "opens the session on a session server and sends the invite.\n"
                "3. Both press **Start game**. The launcher says **Live. Both games are joined.** once you play "
                "together.\n\n"
                "More in the [README]({}) ([по-русски]({})).".format(
                    self.url("blob/{}/README.md#playing".format(self.version)),
                    self.url("blob/{}/README.ru.md#как-играть".format(self.version))))

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

    @staticmethod
    def game_version():
        """The newest game version the mappings describe."""
        names = [name[:-5] for name in os.listdir(os.path.join(ROOT, "mappings")) if name.endswith(".json")]
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
