"""The person's own documents: read, split into passages and indexed with the library, kept on this computer.

Files live in a visible folder (~/Para Files by default). Files dropped into it, changed or
removed there are picked up by sync(), which the server runs at start and every half minute.

A project is a folder inside it: its files are searched only in the project's chats, while files at the top of the
folder are searched in every chat. A folder made by hand is a project too. Project notes are kept in the app folder
(projects.json), not in the person's folder."""
import html.parser
import json
import os
import re
import shutil
import threading
import zipfile

from . import paths
from .splitter import split

TYPES = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm", ".epub", ".csv", ".rtf"}
BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')  # not allowed in a folder name on Windows, or a path


def project_name(name):
    """A name that's safe as a folder name, or ValueError saying why not."""
    name = re.sub(r"\s+", " ", str(name or "")).strip().rstrip(".")
    if not name:
        raise ValueError("Give the project a name.")
    if BAD_NAME.search(name) or name.startswith(".") or name.startswith("~$"):
        raise ValueError('A project name can\'t start with a dot or contain < > : " / \\ | ? *')
    if len(name) > 80:
        raise ValueError("Keep the project name under 80 characters.")
    return name


class _Text(html.parser.HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "section", "article", "table",
             "blockquote", "pre"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "nav", "head"):
            self.skip += 1
        elif tag in self.BLOCK:
            self.out.append("\n\n")
            if tag in ("h1", "h2", "h3", "h4"):
                self.out.append("#" * int(tag[1]) + " ")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "head"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCK:
            self.out.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(markup):
    p = _Text()
    p.feed(markup)
    return re.sub(r"\n{3,}", "\n\n", "".join(p.out))


def read_pages(path):
    """The file's text, as a list of pages (one item for formats without pages)."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(path)
        return [(page.extract_text() or "") for page in reader.pages]
    if ext == ".docx":
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"<w:pStyle w:val=\"Heading(\d)\"/>", lambda m: f"<w:t>{'#' * int(m.group(1))} </w:t>", xml)
        xml = re.sub(r"</w:p>", "\n\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        return [html.unescape(text)]
    if ext in (".html", ".htm"):
        with open(path, encoding="utf-8", errors="replace") as f:
            return [html_text(f.read())]
    if ext == ".epub":
        out = []
        with zipfile.ZipFile(path) as z:
            for name in sorted(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm"))):
                out.append(html_text(z.read(name).decode("utf-8", "replace")))
        return out
    if ext == ".rtf":
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        text = re.sub(r"\\par[d]?", "\n", raw)
        text = re.sub(r"\\[a-z]+-?\d* ?|[{}]", "", text)
        return [text]
    with open(path, encoding="utf-8", errors="replace") as f:
        return [f.read()]


class MyFiles:
    def __init__(self, library):
        self.library = library
        self.lock = threading.Lock()
        self.status = {}  # path -> "reading" while busy

    @property
    def folder(self):
        folder = paths.files_dir()
        os.makedirs(folder, exist_ok=True)
        return folder

    def project_folder(self, project=""):
        """The folder a project's files go in; the files folder itself for files shared by every chat."""
        if not project:
            return self.folder
        return os.path.join(self.folder, project_name(project))

    def project_of(self, key):
        """The project a file belongs to ("" for the shared files at the top of the folder), or None if it's in
        neither (a folder deeper down, or elsewhere)."""
        rel = os.path.relpath(os.path.dirname(os.path.abspath(key)), os.path.abspath(self.folder))
        if rel == ".":
            return ""
        return rel if os.sep not in rel and rel != ".." and not rel.startswith("..") else None

    def keys(self, project=""):
        """Keys of the files a chat in this project searches: the shared files plus the project's own."""
        return {s["key"] for s in self.library.files.sources() if self.project_of(s["key"]) in ("", project)}

    def add(self, name, data, project=""):
        """Save an uploaded file into the folder (or a project's folder) and index it. Returns its key."""
        name = os.path.basename(name).strip() or "file"
        if os.path.splitext(name)[1].lower() not in TYPES:
            raise ValueError(f"Can't read {os.path.splitext(name)[1] or 'that kind of'} files yet. "
                             "Try PDF, Word (.docx), EPUB, HTML or text.")
        folder = self.project_folder(project)
        os.makedirs(folder, exist_ok=True)
        dest = os.path.join(folder, name)
        base, ext = os.path.splitext(dest)
        n = 2
        while os.path.exists(dest):
            dest = f"{base} ({n}){ext}"
            n += 1
        with open(dest, "wb") as f:
            f.write(data)
        self.index(dest)
        return dest

    def add_path(self, src):
        dest = os.path.join(self.folder, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dest):
            shutil.copy2(src, dest)
        self.index(dest)
        return dest

    def index(self, path):
        st = os.stat(path)
        key = os.path.abspath(path)
        title = os.path.splitext(os.path.basename(path))[0]
        self.status[key] = "reading"
        try:
            pages = read_pages(path)
            passages = split(pages)
            status = "ok" if passages else "empty"
        except Exception as e:  # noqa: BLE001 - a broken file shows as such in the list
            passages, status = [], f"error: {e.__class__.__name__}: {str(e)[:120]}"
        finally:
            self.status.pop(key, None)
        old = {s["key"]: s for s in self.library.files.sources()}.get(key) or {}
        self.library.files.add_source(key, title, title, "file", passages, path=path, mtime=st.st_mtime,
                                      size=st.st_size, status=status, note=old.get("note", ""))

    def remove(self, key, delete_file=True):
        self.library.files.remove_source(key)
        if delete_file and os.path.exists(key) and self.project_of(key) is not None:
            os.remove(key)

    def set_note(self, key, note):
        with self.library.files.lock, self.library.files.db:
            self.library.files.db.execute("UPDATE sources SET note=? WHERE key=?", (note[:300], key))

    def _folders(self):
        """The folders sync looks in: the files folder and each project folder in it."""
        out = [self.folder]
        for entry in os.scandir(self.folder):
            if entry.is_dir() and not entry.name.startswith((".", "~$")):
                out.append(entry.path)
        return out

    def sync(self):
        """Index new or changed files in the folder and its project folders; forget ones that are gone."""
        if not self.lock.acquire(blocking=False):
            return
        try:
            known = {s["key"]: s for s in self.library.files.sources()}
            present = set()
            for folder in self._folders():
                for entry in os.scandir(folder):
                    if not entry.is_file() or entry.name.startswith((".", "~$")):
                        continue
                    if os.path.splitext(entry.name)[1].lower() not in TYPES:
                        continue
                    key = os.path.abspath(entry.path)
                    present.add(key)
                    st = entry.stat()
                    old = known.get(key)
                    if not old or abs((old.get("mtime") or 0) - st.st_mtime) > 1 or old.get("size") != st.st_size:
                        self.index(entry.path)
            for key, s in known.items():
                if key not in present and self.project_of(key) is not None:
                    self.library.files.remove_source(key)
        finally:
            self.lock.release()

    def list(self):
        out = []
        for s in self.library.files.sources():
            out.append({k: s[k] for k in ("key", "title", "note", "status", "passages", "size")})
        for key in list(self.status):
            if key not in {o["key"] for o in out}:
                out.append({"key": key, "title": os.path.basename(key), "status": "reading", "passages": 0})
        for o in out:
            o["project"] = self.project_of(o["key"]) or ""
        return out

    # ----- projects -----
    @property
    def _notes_path(self):
        return paths.data_path("projects.json")

    def _notes(self):
        try:
            with open(self._notes_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_notes(self, data):
        tmp = self._notes_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        os.replace(tmp, self._notes_path)

    def projects(self):
        notes = self._notes()
        counts = {}
        for s in self.library.files.sources():
            p = self.project_of(s["key"])
            counts[p] = counts.get(p, 0) + 1
        out = []
        for folder in self._folders()[1:]:
            name = os.path.basename(folder)
            out.append({"name": name, "notes": (notes.get(name) or {}).get("notes", ""), "files": counts.get(name, 0)})
        return sorted(out, key=lambda p: p["name"].lower())

    def project(self, name):
        return next((p for p in self.projects() if p["name"] == name), None)

    def create_project(self, name, notes=""):
        name = project_name(name)
        folder = self.project_folder(name)
        if os.path.exists(folder):
            raise ValueError(f"There's already a project or folder called {name}.")
        os.makedirs(folder)
        self.set_project_notes(name, notes)
        return name

    def set_project_notes(self, name, notes):
        data = self._notes()
        data.setdefault(name, {})["notes"] = str(notes or "")[:4000]
        self._save_notes(data)

    def rename_project(self, old, new):
        """Renames the folder and moves its files' index entries with it, so their notes and passages stay."""
        new = project_name(new)
        if new == old:
            return new
        src, dest = self.project_folder(old), self.project_folder(new)
        if not os.path.isdir(src):
            raise ValueError("No such project.")
        if os.path.exists(dest) and os.path.normcase(src) != os.path.normcase(dest):
            raise ValueError(f"There's already a project or folder called {new}.")
        with self.lock:  # keep sync from forgetting the files while they move
            os.rename(src, dest)
            store = self.library.files
            with store.lock, store.db:
                for s in store.sources():
                    if os.path.dirname(s["key"]) == os.path.abspath(src):
                        key = os.path.join(os.path.abspath(dest), os.path.basename(s["key"]))
                        store.db.execute("UPDATE sources SET key=?, path=? WHERE id=?", (key, key, s["id"]))
        data = self._notes()
        if old in data:
            data[new] = data.pop(old)
            self._save_notes(data)
        return new

    def delete_project(self, name):
        """Only an empty project can be deleted here: deleting someone's files is for them to do in their folder."""
        folder = self.project_folder(name)
        if not os.path.isdir(folder):
            raise ValueError("No such project.")
        left = [e for e in os.listdir(folder) if not e.startswith(".") and e.lower() != "desktop.ini"]
        if left:
            raise ValueError("Remove the project's files first. Its folder still has "
                             f"{len(left)} item{'s' if len(left) != 1 else ''} in it.")
        shutil.rmtree(folder)
        data = self._notes()
        if data.pop(name, None) is not None:
            self._save_notes(data)
