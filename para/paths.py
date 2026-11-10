"""Where the app keeps its settings, library, engine, models and the person's files."""
import json
import os
import sys
import threading

APP_NAME = "Para"
APP_SLUG = "para"
CODE_DIR = os.path.dirname(os.path.abspath(__file__))


def _bundle_dir():
    """Folder holding read-only files shipped with the app (PyInstaller unpacks them to sys._MEIPASS)."""
    return getattr(sys, "_MEIPASS", None) or os.path.dirname(CODE_DIR)


BUNDLE_DIR = _bundle_dir()
STATIC_DIR = os.path.join(CODE_DIR if not getattr(sys, "_MEIPASS", None) else os.path.join(BUNDLE_DIR, "para"),
                          "static")


def app_dir():
    if os.environ.get("PARA_APP_DIR"):
        return os.environ["PARA_APP_DIR"]
    home = os.path.expanduser("~")
    if sys.platform == "darwin":
        return os.path.join(home, "Library", "Application Support", APP_NAME)
    if os.name == "nt":
        return os.path.join(os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or home, APP_NAME)
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share"), APP_SLUG)


def data_path(*parts):
    path = os.path.join(app_dir(), *parts)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def files_dir():
    """The person's own documents: a visible folder they can also drop files into."""
    chosen = Settings().get("files_dir")
    if chosen and os.path.isabs(chosen):
        return chosen
    return os.path.join(os.path.expanduser("~"), f"{APP_NAME} Files")


def library_candidates():
    """The regulations library: a downloaded update wins over the copy shipped with the app."""
    env = os.environ.get("PARA_LIBRARY")
    if env:
        return [env]
    return [os.path.join(app_dir(), "library", "regs.sqlite"),
            os.path.join(BUNDLE_DIR, "library", "dist", "regs.sqlite"),
            os.path.join(os.path.dirname(CODE_DIR), "library", "dist", "regs.sqlite")]


DEFAULTS = {
    "engine": {
        "mode": "",               # "" until set up; "external" or "bundled"
        "base_url": "",           # external: any OpenAI-compatible server, e.g. http://127.0.0.1:11434/v1
        "api_key": "",
        "model": "",
        "bundled_model": "",      # id from models.json
        "context": 32768,
    },
    "reasoning": "auto",          # auto | off | on
    "length": "normal",           # normal | long | none
    "instructions": "",           # "" = the built-in instructions
    "project_notes": "",          # the person's general notes, added to every chat (a project's own are in projects.json)
    "current_project": "",        # the project chosen in the sidebar ("" for none)
    "theme": "auto",              # auto (follow the system) | light | dark
    "files_dir": "",
    "files_warning_ok": False,
    "check_updates": True,
}


class Settings:
    """settings.json in the app folder. Small and read often, so it is re-read each time."""
    _lock = threading.Lock()

    def __init__(self):
        self.path = os.path.join(app_dir(), "settings.json")

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                saved = json.load(f)
        except (OSError, ValueError):
            saved = {}
        out = json.loads(json.dumps(DEFAULTS))
        for k, v in (saved if isinstance(saved, dict) else {}).items():
            if isinstance(out.get(k), dict) and isinstance(v, dict):
                out[k].update(v)
            else:
                out[k] = v
        return out

    def get(self, key, default=None):
        return self.load().get(key, default)

    def update(self, changes):
        with self._lock:
            data = self.load()
            for k, v in changes.items():
                if isinstance(data.get(k), dict) and isinstance(v, dict):
                    data[k].update(v)
                else:
                    data[k] = v
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, self.path)
            return data
