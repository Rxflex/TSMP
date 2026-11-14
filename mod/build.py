"""Package the mod as dist/tsmp.ts4script (a zip of the compiled `tsmp` package).

Usage:
    python mod/build.py                      build only
    python mod/build.py --install <ModsDir>  build and copy into a Mods folder

The game only looks for `.pyc` files inside a script archive (see
docs/internals.md), and they must be Python 3.7 bytecode. The build therefore
needs a Python 3.7 interpreter, searched for in this order:

    --python37 <exe>, $TSMP_PYTHON37, .tools/python37/python.exe,
    the interpreter running this script.

The official "embeddable" Python 3.7 zip unpacked into .tools/python37 is
enough.
"""

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PACKAGE = os.path.join(HERE, "tsmp")
DIST = os.path.join(ROOT, "dist")
ARTIFACT = "tsmp.ts4script"

# Runs under Python 3.7. Arguments are (source, target, name-in-archive) triples.
_COMPILE = (
    "import sys, py_compile\n"
    "args = sys.argv[1:]\n"
    "for src, dst, name in zip(args[0::3], args[1::3], args[2::3]):\n"
    "    py_compile.compile(src, cfile=dst, dfile=name, doraise=True)\n"
)


def _is_python37(executable):
    try:
        out = subprocess.check_output(
            [executable, "-c", "import sys; print(sys.version_info[:2] == (3, 7))"]
        )
    except (OSError, subprocess.CalledProcessError):
        return False
    return out.strip() == b"True"


def find_python37(explicit=None):
    candidates = [
        explicit,
        os.environ.get("TSMP_PYTHON37"),
        os.path.join(ROOT, ".tools", "python37", "python.exe"),
        sys.executable,
    ]
    for candidate in candidates:
        if candidate and _is_python37(candidate):
            return candidate
    return None


def _sources():
    for directory, _, files in os.walk(PACKAGE):
        for name in sorted(files):
            if name.endswith(".py"):
                full = os.path.join(directory, name)
                yield full, os.path.relpath(full, HERE).replace(os.sep, "/")


def build(python37):
    if not os.path.isdir(DIST):
        os.makedirs(DIST)
    target = os.path.join(DIST, ARTIFACT)
    with tempfile.TemporaryDirectory() as tmp:
        compiled = []
        argv = [python37, "-c", _COMPILE]
        for index, (full, name) in enumerate(_sources()):
            pyc = os.path.join(tmp, "{}.pyc".format(index))
            compiled.append((pyc, name + "c"))
            argv += [full, pyc, name]
        subprocess.check_call(argv)
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for pyc, name in compiled:
                archive.write(pyc, name)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--install", metavar="MODS_DIR", help="copy the built mod into this Mods folder")
    parser.add_argument("--python37", metavar="EXE", help="Python 3.7 interpreter used to compile the mod")
    args = parser.parse_args()

    python37 = find_python37(args.python37)
    if python37 is None:
        parser.error("Python 3.7 not found; see the module docstring")

    target = build(python37)
    print("built", target)
    if args.install:
        if os.path.basename(os.path.normpath(args.install)).lower() != "mods":
            parser.error("--install expects the game's Mods folder")
        destination = os.path.join(args.install, ARTIFACT)
        shutil.copyfile(target, destination)
        print("installed", destination)


if __name__ == "__main__":
    main()
