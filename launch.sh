#!/usr/bin/env bash
#
# Start the linernotes editor, installing it first if that hasn't happened yet.
#
#   ./launch.sh                          # start empty
#   ./launch.sh examples/slow-water.yaml # open a file
#
# The install is skipped on every run after the first. It runs again only when
# the venv is missing or broken, when the entry point isn't there, or when
# pyproject.toml has changed since the last install (new dependencies).

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

VENV=.venv
PYTHON="$VENV/bin/python"
GUI="$VENV/bin/linernotes-gui"
STAMP="$VENV/.installed-from-pyproject"

needs_install() {
    # No venv, or one left behind by a python that has since moved or been upgraded.
    [ -x "$PYTHON" ] || return 0
    "$PYTHON" -c "" 2>/dev/null || return 0

    # Never installed, or the console script is missing.
    [ -x "$GUI" ] || return 0

    # Dependencies may have changed since the last install.
    [ -f "$STAMP" ] || return 0
    [ "$STAMP" -nt pyproject.toml ] || return 0

    return 1
}

if needs_install; then
    if [ -x "$PYTHON" ] && "$PYTHON" -c "" 2>/dev/null; then
        echo "linernotes: installing dependencies…"
    else
        echo "linernotes: creating $VENV…"
        rm -rf "$VENV"
        python3 -m venv "$VENV"
    fi

    "$PYTHON" -m pip install --quiet --upgrade pip
    "$PYTHON" -m pip install --quiet -e .
    touch "$STAMP"
    echo "linernotes: ready."
fi

# The editor is tkinter; a python built without it fails deep inside the import.
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo "linernotes: this Python has no tkinter, so the editor cannot start." >&2
    echo "  On macOS: brew install python-tk, or use the python.org installer." >&2
    echo "  On Debian/Ubuntu: sudo apt install python3-tk" >&2
    echo "Then delete $VENV and run this script again." >&2
    exit 1
fi

exec "$GUI" "$@"
