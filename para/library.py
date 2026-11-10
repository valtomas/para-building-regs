"""The regulations library (shipped with the app, updatable) plus the person's own files, searched together."""
import os
import shutil
import threading

from . import paths
from .store import Store

KIND_LABELS = {
    "approved-document": "Approved Documents",
    "legislation": "Legislation",
    "guidance": "Government guidance",
    "file": "Your files",
}


class Library:
    def __init__(self):
        self.lock = threading.RLock()
        self.regs = None
        self.regs_path = None
        self.files = Store(paths.data_path("files", "files.sqlite"), "f")
        self.open_regs()

    def open_regs(self):
        with self.lock:
            if self.regs:
                self.regs.close()
                self.regs = None
            for path in paths.library_candidates():
                if os.path.exists(path):
                    try:
                        self.regs, self.regs_path = Store(path, "r", readonly=True), path
                        break
                    except Exception:  # noqa: BLE001 - a broken download: fall back to the shipped copy
                        continue

    def install_regs(self, new_path):
        """Swap in a downloaded library (already checked) for the one in use."""
        target = os.path.join(paths.app_dir(), "library", "regs.sqlite")
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with self.lock:
            if self.regs:
                self.regs.close()
                self.regs = None
            shutil.move(new_path, target + ".new")
            os.replace(target + ".new", target)
            self.open_regs()

    def info(self):
        meta = self.regs.meta() if self.regs else {}
        return {"version": meta.get("version", ""), "built": meta.get("built", ""), "path": self.regs_path,
                "sources": self.regs.sources() if self.regs else [], "files": self.files.sources()}

    def summary(self):
        if not self.regs:
            return "(No regulations library is installed.)"
        groups = {}
        for s in self.regs.sources():
            groups.setdefault(s["kind"], []).append(
                f"{s['short']}: {s['title']}" + (f" ({s['published']})" if s.get("published") else ""))
        out = []
        for kind, items in groups.items():
            out.append(f"{KIND_LABELS.get(kind, kind)}:\n" + "\n".join("- " + i for i in items))
        return "\n".join(out)

    def files_summary(self, file_keys=None):
        items = [s for s in self.files.sources()
                 if s["status"] == "ok" and (file_keys is None or s["key"] in file_keys)]
        return "\n".join(f"- {s['title']}" + (f" — {s['note']}" if s.get("note") else "") for s in items[:60])

    def _store(self, pid):
        for store in (self.regs, self.files):
            if store and store.owns(pid):
                return store
        return None

    def search(self, query, limit=8, file_keys=None):
        """file_keys: the person's files to search (a project's chats search only its own and the shared ones)."""
        with self.lock:
            hits = []
            if self.regs:
                hits += self.regs.search(query, limit=limit)
            if file_keys is None or file_keys:
                hits += self.files.search(query, limit=limit, source_keys=file_keys)
        hits.sort(key=lambda h: h["score"])
        # Keep at least a couple of the person's own files in view when they match.
        mine = [h for h in hits if h["id"].startswith("f")][:2]
        missing = [h for h in mine if h not in hits[:limit]]
        return hits[:limit - len(missing)] + missing

    def read(self, pid, max_chars=6000):
        store = self._store(pid)
        return store.read(pid, max_chars) if store else None

    def describe(self, pid):
        store = self._store(pid)
        return store.describe(pid) if store else None

    def document(self, pid):
        store = self._store(pid)
        return store.document(pid) if store else None

    def figure(self, figure_id):
        """A diagram or table image from the regulations library (the person's files have none)."""
        with self.lock:
            return self.regs.figure(figure_id) if self.regs and str(figure_id).isdigit() else None

    def first_passage(self, key):
        for store in (self.regs, self.files):
            if store:
                pid = store.first_passage(key)
                if pid:
                    return pid
        return None


class Scope:
    """The library as one chat sees it: the regulations plus the person's files in file_keys (a set, or a function
    returning one, so files added during a chat are found). Passes everything else through to the library."""

    def __init__(self, library, file_keys):
        self.library, self._keys = library, file_keys

    @property
    def file_keys(self):
        return self._keys() if callable(self._keys) else self._keys

    def files_summary(self):
        return self.library.files_summary(self.file_keys)

    def search(self, query, limit=8):
        return self.library.search(query, limit=limit, file_keys=self.file_keys)

    def __getattr__(self, name):
        return getattr(self.library, name)
