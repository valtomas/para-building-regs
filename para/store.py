"""SQLite full-text store of passages, used for both the regulations library and the person's own files.

One file holds:
  sources   one row per document (an Approved Document, an Act, a guide, one of the person's files)
  passages  the document split at its own paragraph numbers, each up to ~1,800 characters
  fts       an FTS5 index over passages (porter stemming, BM25 ranking)
  meta      key/value facts about the library (version, built date)
"""
import re
import sqlite3
import threading

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, title TEXT, short TEXT, kind TEXT, url TEXT, license TEXT,
  published TEXT, note TEXT, path TEXT, mtime REAL, size INTEGER, status TEXT DEFAULT 'ok');
CREATE TABLE IF NOT EXISTS passages(
  id INTEGER PRIMARY KEY, source_id INTEGER, seq INTEGER, ref TEXT, heading TEXT, page INTEGER, doc TEXT, text TEXT);
CREATE INDEX IF NOT EXISTS passages_source ON passages(source_id, seq);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  doc, ref, heading, text, content='passages', content_rowid='id', tokenize='porter unicode61');
"""

STOPWORDS = set("""a about above after again against all am an and any are as at be because been before being below
between both but by can could did do does doing down during each few for from further had has have having he her here
hers him his how i if in into is it its itself just me more most my no nor not now of off on once only or other our
ours out over own same she should so some such than that the their theirs them then there these they this those
through to too under until up very was we were what when where which while who whom why will with would you your
yours need needs required requirement requirements regs regulation regulations approved document documents tell
explain""".split())

# Words people use for a document, mapped to how the library names it, so "Part B" or "ADB" finds Approved Document B.
PART_WORD = re.compile(r"\b(?:part|approved\s+document)\s+([a-t])(?![a-z])|\bad\s?([a-t])(?![a-z])", re.I)


def query_terms(query):
    """FTS5 terms for a query: words minus stopwords; "2.5" or "B1" kept as phrases."""
    terms = []
    for m in PART_WORD.finditer(query):
        letter = (m.group(1) or m.group(2)).lower()
        terms.append(f'"approved document {letter}"')
    query = PART_WORD.sub(" ", query)
    for raw in re.findall(r"[\w][\w.\-/]*", query.lower()):
        raw = raw.strip(".-/")
        if not raw or raw in STOPWORDS or (len(raw) < 2 and not raw.isdigit()):
            continue
        parts = [p for p in re.split(r"[.\-/]", raw) if p]
        term = '"' + " ".join(parts) + '"'
        if term not in terms:
            terms.append(term)
    return terms[:14]


def snippet(text, terms, width=320):
    flat = " ".join(line.strip() for line in text.splitlines() if line.strip())
    low = flat.lower()
    hits = [low.find(t.strip('"').split(" ")[0][:6]) for t in terms]
    hits = [h for h in hits if h >= 0]
    start = max(0, min(hits) - 60) if hits else 0
    out = flat[start:start + width]
    return ("…" if start else "") + out + ("…" if start + width < len(flat) else "")


class Store:
    def __init__(self, path, prefix, readonly=False):
        self.path, self.prefix, self.readonly = path, prefix, readonly
        self.lock = threading.RLock()
        if readonly:
            self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        else:
            self.db = sqlite3.connect(path, check_same_thread=False)
            self.db.executescript(SCHEMA)

    # ----- ids -----
    def pid(self, rowid):
        return f"{self.prefix}{rowid}"

    def owns(self, pid):
        return isinstance(pid, str) and pid.startswith(self.prefix) and pid[len(self.prefix):].isdigit()

    def rowid(self, pid):
        return int(pid[len(self.prefix):])

    # ----- facts -----
    def meta(self):
        with self.lock:
            try:
                return dict(self.db.execute("SELECT key, value FROM meta"))
            except sqlite3.OperationalError:
                return {}

    def sources(self):
        with self.lock:
            rows = self.db.execute(
                "SELECT s.id, s.key, s.title, s.short, s.kind, s.url, s.license, s.published, s.note, s.path, "
                "s.size, s.status, (SELECT COUNT(*) FROM passages p WHERE p.source_id = s.id) FROM sources s "
                "ORDER BY s.kind, s.short").fetchall()
        keys = ("id", "key", "title", "short", "kind", "url", "license", "published", "note", "path", "size",
                "status", "passages")
        return [dict(zip(keys, r)) for r in rows]

    # ----- writing (builder and the person's files) -----
    def add_source(self, key, title, short, kind, passages, url="", license="", published="", note="", path="",
                   mtime=0.0, size=0, status="ok"):
        """passages: list of dicts with ref, heading, page, text. Replaces any source with the same key."""
        with self.lock, self.db:
            self.remove_source(key, commit=False)
            cur = self.db.execute(
                "INSERT INTO sources(key, title, short, kind, url, license, published, note, path, mtime, size, status)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (key, title, short, kind, url, license, published, note, path, mtime, size, status))
            sid = cur.lastrowid
            doc = f"{short} {title}"
            for seq, p in enumerate(passages):
                cur = self.db.execute(
                    "INSERT INTO passages(source_id, seq, ref, heading, page, doc, text) VALUES (?,?,?,?,?,?,?)",
                    (sid, seq, p.get("ref", ""), p.get("heading", ""), p.get("page"), doc, p["text"]))
                self.db.execute("INSERT INTO fts(rowid, doc, ref, heading, text) VALUES (?,?,?,?,?)",
                                (cur.lastrowid, doc, p.get("ref", ""), p.get("heading", ""), p["text"]))
            return sid

    def remove_source(self, key, commit=True):
        with self.lock:
            row = self.db.execute("SELECT id FROM sources WHERE key=?", (key,)).fetchone()
            if not row:
                return False
            for rid, doc, ref, heading, text in self.db.execute(
                    "SELECT id, doc, ref, heading, text FROM passages WHERE source_id=?", (row[0],)).fetchall():
                self.db.execute("INSERT INTO fts(fts, rowid, doc, ref, heading, text) VALUES('delete',?,?,?,?,?)",
                                (rid, doc, ref, heading, text))
            self.db.execute("DELETE FROM passages WHERE source_id=?", (row[0],))
            self.db.execute("DELETE FROM sources WHERE id=?", (row[0],))
            if commit:
                self.db.commit()
            return True

    def set_meta(self, **kv):
        with self.lock, self.db:
            for k, v in kv.items():
                self.db.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (k, str(v)))

    # ----- reading -----
    def search(self, query, limit=8, source_keys=None):
        terms = query_terms(query)
        if not terms:
            return []
        hits = []
        with self.lock:
            for joiner in (" ", " OR "):
                try:
                    hits = self.db.execute(
                        "SELECT rowid, bm25(fts, 0.6, 4.0, 2.0, 1.0) AS score FROM fts WHERE fts MATCH ? "
                        "ORDER BY score LIMIT 200", (joiner.join(terms),)).fetchall()
                except sqlite3.OperationalError:
                    hits = []
                if len(hits) >= 4 or len(terms) == 1:
                    break
            if not hits:
                return []
            score = dict(hits)
            marks = ",".join("?" * len(score))
            rows = self.db.execute(
                "SELECT p.id, p.ref, p.heading, p.page, p.text, s.key, s.short, s.title, s.kind FROM passages p "
                f"JOIN sources s ON s.id = p.source_id WHERE p.id IN ({marks})", list(score)).fetchall()
        out, per_source = [], {}
        for r in sorted(rows, key=lambda r: score[r[0]]):
            if source_keys and r[5] not in source_keys:
                continue
            if per_source.get(r[5], 0) >= 3:  # leave room for other documents
                continue
            per_source[r[5]] = per_source.get(r[5], 0) + 1
            out.append({"id": self.pid(r[0]), "ref": r[1], "heading": r[2], "page": r[3], "source": r[6],
                        "title": r[7], "kind": r[8], "score": score[r[0]], "snippet": snippet(r[4], terms)})
            if len(out) >= limit:
                break
        return out

    def _row(self, rowid):
        return self.db.execute(
            "SELECT p.source_id, p.seq, p.ref, p.heading, p.page, s.short, s.title, s.url, s.license, s.kind, s.key, "
            "s.path FROM passages p JOIN sources s ON s.id = p.source_id WHERE p.id=?", (rowid,)).fetchone()

    def describe(self, pid):
        with self.lock:
            r = self._row(self.rowid(pid))
        if not r:
            return None
        return {"id": pid, "ref": r[2], "heading": r[3], "page": r[4], "source": r[5], "title": r[6], "url": r[7],
                "kind": r[9]}

    def read(self, pid, max_chars=6000):
        """The passage and those after it in the same document, about max_chars long."""
        with self.lock:
            r = self._row(self.rowid(pid))
            if not r:
                return None
            sid, seq = r[0], r[1]
            parts, size, next_id = [], 0, None
            for rowid, ref, heading, text in self.db.execute(
                    "SELECT id, ref, heading, text FROM passages WHERE source_id=? AND seq>=? ORDER BY seq LIMIT 40",
                    (sid, seq)):
                if parts and size + len(text) > max_chars:
                    next_id = self.pid(rowid)
                    break
                label = " · ".join(x for x in (heading, ref) if x)
                parts.append((f"[{self.pid(rowid)}] {label}\n" if label else f"[{self.pid(rowid)}]\n") + text)
                size += len(text)
        return {"id": pid, "ref": r[2], "heading": r[3], "page": r[4], "source": r[5], "title": r[6], "url": r[7],
                "kind": r[9], "text": "\n\n".join(parts), "next_id": next_id}

    def document(self, pid):
        """The whole document holding a passage, as passages, for the reader panel."""
        with self.lock:
            r = self._row(self.rowid(pid))
            if not r:
                return None
            parts = [{"id": self.pid(i), "ref": ref, "heading": h, "page": pg, "text": t}
                     for i, ref, h, pg, t in self.db.execute(
                         "SELECT id, ref, heading, page, text FROM passages WHERE source_id=? ORDER BY seq", (r[0],))]
        return {"focus": pid, "source": r[5], "title": r[6], "url": r[7], "license": r[8], "kind": r[9],
                "key": r[10], "path": r[11], "parts": parts}

    def first_passage(self, key):
        with self.lock:
            row = self.db.execute("SELECT p.id FROM passages p JOIN sources s ON s.id=p.source_id WHERE s.key=? "
                                  "ORDER BY p.seq LIMIT 1", (key,)).fetchone()
        return self.pid(row[0]) if row else None

    def close(self):
        with self.lock:
            self.db.close()
