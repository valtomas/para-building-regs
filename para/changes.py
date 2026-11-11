"""What differs between two editions of the regulations library, paragraph by paragraph, so that after an update the
person can see which paragraphs to read again. Worked out on their computer when an update is installed, from the two
library files; nothing is fetched for it."""
import json
import os

from . import paths


def _by_name(rows):
    """{a passage's name in its document: [(id, ref, heading, hash), ...]}: its reference where it has one, else its
    heading. A list, because a few names are used more than once in a document."""
    out = {}
    for row in rows:
        out.setdefault(row[1] or ("", row[2]), []).append(row)
    return out


def diff(old, new, limit=60):
    """old, new: Stores. {"from", "to", "documents": [...], "removed": [...]}; a document appears only if something in
    it differs. Each list holds at most limit passages, with the full count beside it."""
    was = {s["key"]: s for s in old.sources()}
    documents = []
    for s in new.sources():
        before, after = _by_name(old.anchors(s["key"])), _by_name(new.anchors(s["key"]))
        entry = {"key": s["key"], "short": s["short"], "title": s["title"], "published": s.get("published") or "",
                 "new": s["key"] not in was}
        if not entry["new"]:
            earlier = was[s["key"]].get("published") or ""
            entry["was"] = earlier if earlier != entry["published"] else ""
            lists = {
                "changed": [rows[0] for name, rows in after.items()
                            if name in before and [r[3] for r in rows] != [r[3] for r in before[name]]],
                "added": [rows[0] for name, rows in after.items() if name not in before],
                "removed": [rows[0] for name, rows in before.items() if name not in after],
            }
            if not any(lists.values()) and not entry["was"]:
                continue
            for kind, rows in lists.items():
                entry[kind + "_count"] = len(rows)
                # a removed passage has no id to open in the new edition
                entry[kind] = [{"id": r[0] if kind != "removed" else None, "ref": r[1], "heading": r[2]}
                               for r in rows[:limit]]
        documents.append(entry)
    gone = [{"short": s["short"], "title": s["title"]} for key, s in was.items()
            if key not in {s["key"] for s in new.sources()}]
    return {"from": old.meta().get("version", ""), "to": new.meta().get("version", ""), "documents": documents,
            "removed": gone}


def _path():
    return paths.data_path("library", "changes.json")


def save(changes):
    tmp = _path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(changes, f)
    os.replace(tmp, _path())


def load(version):
    """The saved comparison, if it's of the library now in use."""
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("to") == version and version else None
