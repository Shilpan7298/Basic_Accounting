# PyInstaller spec — one binary containing the API, the templates and the
# built React app.
#
# Run from the repository root:
#   pyinstaller packaging/urjapod.spec --noconfirm
#
# The frontend must be built into backend/app/static first; the CI workflow
# does that step before invoking PyInstaller.

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
APP = ROOT / "backend" / "app"

datas = [
    # Document templates and prompts are read from disk at runtime, so they
    # have to travel with the binary.
    (str(APP / "templates"), "app/templates"),
    (str(APP / "extractors" / "prompts"), "app/extractors/prompts"),
]
if (APP / "static").is_dir():
    datas.append((str(APP / "static"), "app/static"))
if (ROOT / "backend" / "alembic").is_dir():
    datas.append((str(ROOT / "backend" / "alembic"), "alembic"))

# WeasyPrint ships fonts/CSS as package data and pulls its native stack in
# dynamically; without these it imports but fails on the first render.
for package in ("weasyprint", "tinycss2", "cssselect2", "pyphen", "fontTools"):
    try:
        datas += collect_data_files(package)
    except Exception:
        pass

hiddenimports = [
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    "sqlalchemy.dialects.sqlite", "sqlalchemy.dialects.postgresql",
    "app.main", "app.desktop",
]
hiddenimports += collect_submodules("weasyprint")
hiddenimports += collect_submodules("docxtpl")

a = Analysis(
    [str(APP / "desktop.py")],
    pathex=[str(ROOT / "backend")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pytest", "reportlab"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="urjapod",
    console=True,          # the server prints its LAN address; hiding it helps nobody
    icon=str(ROOT / "packaging" / ("windows/urjapod.ico" if sys.platform == "win32" else "macos/urjapod.icns")) if (ROOT / "packaging").is_dir() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="urjapod")

if sys.platform == "darwin":
    app_bundle = BUNDLE(
        coll,
        name="Urjapod.app",
        icon=str(ROOT / "packaging" / "macos" / "urjapod.icns"),
        bundle_identifier="in.urjapod.invoicing",
        info_plist={
            "CFBundleName": "Urjapod",
            "CFBundleDisplayName": "Urjapod Orders & Invoicing",
            "CFBundleShortVersionString": "0.2.0",
            "NSHighResolutionCapable": True,
            # No network server entitlement issues: it binds locally.
            "LSMinimumSystemVersion": "12.0",
        },
    )
