#!/usr/bin/env python3
"""Compile the editor into a native app for whichever system this runs on.

    python build.py              # macOS -> dist/linernotes.app
                                 # Windows -> dist/linernotes/linernotes.exe
                                 # Linux  -> dist/linernotes/linernotes
    python build.py --onefile    # a single executable instead of a folder
    python build.py --clean      # discard cached analysis first

PyInstaller freezes the interpreter it is running under, together with the
packages installed beside it. It cannot cross-compile, so a Windows .exe has to
be built on Windows and a macOS .app on macOS — run this script on each machine
you want to ship for.

Like launch.sh, this sets up .venv on first run and reuses it afterwards.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
WINDOWS = sys.platform == "win32"
MACOS = sys.platform == "darwin"


def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def run(args: list[str], **kwargs) -> None:
    subprocess.run([str(a) for a in args], check=True, cwd=ROOT, **kwargs)


def ensure_venv() -> Path:
    """Make sure .venv exists and has the project plus PyInstaller in it."""
    python = venv_python()
    if not python.exists():
        print(f"linernotes: creating {VENV.name}…")
        run([sys.executable, "-m", "venv", str(VENV)])
        python = venv_python()

    # Cheap to re-run and it keeps the venv honest when pyproject.toml changes.
    print("linernotes: installing build dependencies…")
    run([python, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "--quiet", "-e", ".[build]"])

    # The editor is tkinter, and a Python built without it fails deep inside the
    # freeze rather than at startup, which is a much worse error to read.
    if subprocess.run([str(python), "-c", "import tkinter"]).returncode != 0:
        sys.exit(
            "linernotes: this Python has no tkinter, so the app cannot be built.\n"
            "  On macOS: brew install python-tk, or use the python.org installer.\n"
            "  On Debian/Ubuntu: sudo apt install python3-tk\n"
            f"Then delete {VENV} and run this script again."
        )
    return python


def version(python: Path) -> str:
    out = subprocess.run(
        [str(python), "-c", "import linernotes; print(linernotes.__version__)"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    return out.stdout.strip() or "0.0.0"


def built_path(onefile: bool) -> Path:
    dist = ROOT / "dist"
    if MACOS and not onefile:
        return dist / "linernotes.app"
    exe = "linernotes.exe" if WINDOWS else "linernotes"
    return dist / exe if onefile else dist / "linernotes" / exe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--onefile",
        action="store_true",
        help="produce a single executable; starts slower, since it unpacks itself on every launch",
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="remove build/ and dist/ and PyInstaller's cache before building",
    )
    args = parser.parse_args()

    python = ensure_venv()

    if args.clean:
        for directory in (ROOT / "build", ROOT / "dist"):
            shutil.rmtree(directory, ignore_errors=True)

    env = {
        **os.environ,
        "LINERNOTES_ROOT": str(ROOT),
        "LINERNOTES_ONEFILE": "1" if args.onefile else "0",
        "LINERNOTES_VERSION": version(python),
    }

    print(f"linernotes: building for {sys.platform}…")
    command = [python, "-m", "PyInstaller", "--noconfirm", "packaging/linernotes.spec"]
    if args.clean:
        command.append("--clean")
    run(command, env=env)

    result = built_path(args.onefile)
    if not result.exists():
        sys.exit(f"linernotes: build finished but {result} is missing")
    print(f"\nlinernotes: built {result.relative_to(ROOT)}")
    if MACOS and not args.onefile:
        print("  Unsigned, so the first launch needs right-click -> Open.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
