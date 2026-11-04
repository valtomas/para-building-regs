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

binaries = []
hiddenimports = ["para.server", "para.qtprobe", "pypdf", "truststore"]
if sys.platform.startswith("linux"):
    # Qt's X11 plugin needs these small X helper libraries, which some distributions don't install by default.
    # Bundle them from the build machine (libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 ... installed in CI).
    import glob
    for lib in ("libxcb-cursor.so.0", "libxcb-icccm.so.4", "libxcb-keysyms.so.1", "libxcb-image.so.0",
                "libxcb-render-util.so.0", "libxkbcommon-x11.so.0"):
        found = glob.glob(f"/usr/lib/*-linux-gnu/{lib}") + glob.glob(f"/usr/lib64/{lib}")
        if found:
            binaries.append((found[0], "."))
        else:
            print(f"WARNING: {lib} not found on the build machine; it won't be bundled")
    hiddenimports += ["webview.platforms.qt", "qtpy", "PyQt6.QtWebEngineWidgets", "PyQt6.QtWebEngineCore"]

a = Analysis(
    [os.path.join(SPECPATH, "launch.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["tkinter", "matplotlib", "numpy", "PIL", "pytest", "gi", "webview.platforms.gtk", "PySide6", "PyQt5"],
)
if sys.platform.startswith("linux"):
    # PyInstaller collects all of Qt (3D, multimedia, QML styles…). Keep what the web view needs: these libraries,
    # the platform plugins, and the web engine's English locale. Cuts the app from ~590 MB to about a third.
    KEEP_QT = {"Core", "DBus", "Gui", "Network", "OpenGL", "Positioning", "PrintSupport", "Qml", "QmlMeta",
               "QmlModels", "QmlWorkerScript", "Quick", "QuickWidgets", "WaylandClient", "WaylandEglClientHwIntegration",
               "WebChannel", "WebEngineCore", "WebEngineWidgets", "Widgets", "XcbQpa", "Svg", "EglFSDeviceIntegration",
               "EglFsKmsSupport"}
    KEEP_MODULES = {"QtCore", "QtGui", "QtWidgets", "QtNetwork", "QtWebChannel", "QtWebEngineCore",
                    "QtWebEngineWidgets", "QtPrintSupport", "sip"}

    def wanted(dest):
        d = dest.replace("\\", "/")
        name = os.path.basename(d)
        if "/Qt6/lib/" in d and name.startswith("libQt6"):
            return name[len("libQt6"):].split(".so")[0] in KEEP_QT
        if d.startswith("PyQt6/") and d.count("/") == 1 and name.endswith(".so"):
            return name.split(".")[0] in KEEP_MODULES
        if "/Qt6/qml/" in d:
            return False
        if "/Qt6/translations/" in d:
            return "qtwebengine_locales/en-" in d
        if "/Qt6/plugins/" in d:
            return not any(x in d for x in ("/multimedia/", "/sceneparsers/", "/geometryloaders/", "/renderers/",
                                             "/assetimporters/", "/qmltooling/", "/sqldrivers/", "/designer/",
                                             "/platformthemes/libqgtk3"))
        return True

    a.binaries = [b for b in a.binaries if wanted(b[0])]
    a.datas = [d for d in a.datas if wanted(d[0])]

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
