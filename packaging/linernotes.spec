# PyInstaller spec for the linernotes editor.
#
# Driven by build.py, which sets the environment variables read below. Not meant
# to be run by hand — use `python build.py`.
#
# PyInstaller cannot cross-compile: this produces a macOS .app when run on macOS
# and a Windows .exe when run on Windows, from the same file.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

ROOT = Path(os.environ["LINERNOTES_ROOT"])
ONEFILE = os.environ.get("LINERNOTES_ONEFILE") == "1"
VERSION = os.environ["LINERNOTES_VERSION"]

MACOS = sys.platform == "darwin"
WINDOWS = sys.platform == "win32"

# ReportLab keeps the Type 1 metrics for its built-in faces (Times, Helvetica)
# alongside the package. Those are the fallback fonts every booklet starts from,
# so they have to travel with the app.
datas = collect_data_files("reportlab")

icon = None
for candidate in ("icon.icns" if MACOS else "icon.ico",):
    path = ROOT / "packaging" / candidate
    if path.exists():
        icon = str(path)

analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    # Trimming what the GUI never opens: tkinter is used, but its test suite,
    # the interactive shell machinery and the stdlib's own tests are dead weight.
    excludes=["tkinter.test", "test", "unittest", "pydoc_data", "idlelib"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)

exe_args = dict(
    name="linernotes",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # windowed app: no console behind it on Windows
    disable_windowed_traceback=False,
    icon=icon,
)

if ONEFILE:
    exe = EXE(
        pyz,
        analysis.scripts,
        analysis.binaries,
        analysis.datas,
        [],
        runtime_tmpdir=None,
        **exe_args,
    )
else:
    exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, **exe_args)
    coll = COLLECT(
        exe,
        analysis.binaries,
        analysis.datas,
        strip=False,
        upx=False,
        name="linernotes",
    )

if MACOS and not ONEFILE:
    app = BUNDLE(
        coll,
        name="linernotes.app",
        icon=icon,
        bundle_identifier="com.linernotes.editor",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            # Without this the app renders through the 2x upscaler and every
            # preview panel looks soft.
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            "CFBundleDocumentTypes": [
                {
                    "CFBundleTypeName": "linernotes album",
                    "CFBundleTypeRole": "Editor",
                    "LSHandlerRank": "Owner",
                    "LSItemContentTypes": ["public.yaml"],
                    "CFBundleTypeExtensions": ["yaml", "yml"],
                }
            ],
        },
    )
