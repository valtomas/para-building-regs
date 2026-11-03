# PyInstaller spec: pyinstaller packaging/app.spec  (run from the repository root)
# Builds dist/Para.app on macOS and dist/Para/ on Windows and Linux.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
NAME = "Para"
sys.path.insert(0, ROOT)
from para import VERSION  # noqa: E402

datas = [
    (os.path.join(ROOT, "para", "static"), "para/static"),
    (os.path.join(ROOT, "para", "models.json"), "para"),
]
library = os.path.join(ROOT, "library", "dist", "regs.sqlite")
if os.path.exists(library):
    datas.append((library, "library/dist"))
else:
    print("WARNING: no library/dist/regs.sqlite: the app will offer to download the library on first run")

icon = None
for candidate in ("icon.icns" if sys.platform == "darwin" else "icon.ico",):
    path = os.path.join(SPECPATH, candidate)
    if os.path.exists(path):
        icon = path

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["para.server", "pypdf", "truststore"],
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    icon=icon,
    target_arch=os.environ.get("TARGET_ARCH") or None,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME)
if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=icon,
        bundle_identifier="io.github.valtomas.para",
        version=VERSION,
        info_plist={
            "CFBundleShortVersionString": VERSION,
            "NSHighResolutionCapable": True,
            "LSMinimumSystemVersion": "12.0",
            "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        },
    )
