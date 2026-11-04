"""Can Qt open a window on this Linux system?

When a system library Qt needs is missing, Qt aborts the whole process instead of raising an error, so the app
can't catch it and fall back to the browser. So the app asks a separate process first: `qt_works()` starts this
check (the packaged app runs itself with --qt-probe) and only opens a Qt window if it succeeded.
No imports at module level beyond the standard library, so the check starts fast and has no side effects.
"""
import os
import subprocess
import sys

PROBE_FLAG = "--qt-probe"


def probe():
    """Run in the child process: create a Qt application with the web engine, then exit 0."""
    try:
        import importlib
        importlib.import_module("PyQt6.QtWebEngineWidgets")  # loads the web engine libraries too
        from PyQt6.QtWidgets import QApplication
        app = QApplication(["para-probe"])
        app.quit()
        return 0
    except Exception as e:  # noqa: BLE001 - any failure means: don't use Qt
        print(f"Qt probe failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1


def qt_works(timeout=30):
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, PROBE_FLAG]
    else:
        cmd = [sys.executable, "-c", "import sys; from para.qtprobe import probe; sys.exit(probe())"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout, env=dict(os.environ),
                                cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        tail = (result.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-3:]
        print("Qt can't open a window here; using the browser. " + " | ".join(tail), flush=True)
    return result.returncode == 0
