"""Entry point for the frozen app.

PyInstaller needs a real script to analyse, and it must not be the package's own
``__main__.py`` — importing that as a top-level script would give the app a
second, half-initialised copy of ``linernotes.gui``.

macOS hands a double-clicked document to the app as ``-psn_...`` or through an
Apple Event rather than as a plain argument; anything starting with ``-psn`` is
dropped here so it isn't mistaken for a file to open.
"""

import multiprocessing
import sys

if __name__ == "__main__":
    # Harmless when nothing spawns a process, and required if anything ever
    # does: without it a frozen child re-runs the whole app instead.
    multiprocessing.freeze_support()

    sys.argv[1:] = [a for a in sys.argv[1:] if not a.startswith("-psn")]

    from linernotes.gui.app import main

    sys.exit(main())
