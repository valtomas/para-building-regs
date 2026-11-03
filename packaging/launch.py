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
    import multiprocessing
    multiprocessing.freeze_support()
    _log_to_file()
    os.environ.setdefault("PYWEBVIEW_LOG", "warning")
    from para.server import main
    main()
