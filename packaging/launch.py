"""Entry point for the packaged app (PyInstaller)."""
import os
import sys


def _log_to_file():
    """A windowed app has no console: keep a log in the app folder instead."""
    if getattr(sys, "frozen", False):
        from para import paths
        log = open(paths.data_path("logs", "app.log"), "a", buffering=1, encoding="utf-8")
        sys.stdout = sys.stderr = log


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--qt-probe":  # Linux: can Qt open a window here? (see para/qtprobe.py)
        from para.qtprobe import probe
        sys.exit(probe())
    if sys.platform.startswith("linux") and getattr(sys, "frozen", False):
        # Qt's web engine sandbox needs user namespaces, which some distributions (Ubuntu 24.04+) restrict for
        # unpackaged apps. The window only shows Para's own page from 127.0.0.1; links open in the browser.
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    import multiprocessing
    multiprocessing.freeze_support()
    _log_to_file()
    os.environ.setdefault("PYWEBVIEW_LOG", "warning")
    from para.server import main
    main()
