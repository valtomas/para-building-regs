"""The person's own documents: read, split into passages and indexed with the library, kept on this computer.

Files live in a visible folder (~/Para Files by default). Files dropped into it, changed or
removed there are picked up by sync(), which the server runs at start and every half minute."""
import html.parser
import os
import re
import shutil
import threading
import zipfile

from . import paths
from .splitter import split

TYPES = {".pdf", ".docx", ".txt", ".md", ".markdown", ".html", ".htm", ".epub", ".csv", ".rtf"}


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

    def add(self, name, data):
        """Save an uploaded file into the folder and index it. Returns its key."""
        name = os.path.basename(name).strip() or "file"
        if os.path.splitext(name)[1].lower() not in TYPES:
            raise ValueError(f"Can't read {os.path.splitext(name)[1] or 'that kind of'} files yet. "
                             "Try PDF, Word (.docx), EPUB, HTML or text.")
        dest = os.path.join(self.folder, name)
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
        if delete_file and os.path.exists(key) and os.path.dirname(os.path.abspath(key)) == \
                os.path.abspath(self.folder):
            os.remove(key)

    def set_note(self, key, note):
        with self.library.files.lock, self.library.files.db:
            self.library.files.db.execute("UPDATE sources SET note=? WHERE key=?", (note[:300], key))

    def sync(self):
        """Index new or changed files in the folder; forget ones that are gone."""
        if not self.lock.acquire(blocking=False):
            return
        try:
            known = {s["key"]: s for s in self.library.files.sources()}
            present = set()
            for entry in os.scandir(self.folder):
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
                if key not in present and os.path.dirname(key) == os.path.abspath(self.folder):
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
        return out
