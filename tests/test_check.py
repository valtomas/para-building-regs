"""Checks for library/check.py, the gate that stops a broken library being published.

python3 tests/test_check.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "library"))

from para.store import Store  # noqa: E402
import check  # noqa: E402

TMP = tempfile.mkdtemp()
PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000105e9a4a10000000049454e44ae426082")


def library(name, docs):
    """docs: {key: (passages, figures)}; plus filler documents so the library has a realistic size."""
    path = os.path.join(TMP, name + ".sqlite")
    store = Store(path, "r")
    all_docs = {f"FILL{i}": (3, 0) for i in range(check.MIN_SOURCES)}
    all_docs.update(docs)
    for key, (n, figs) in all_docs.items():
        store.add_source(key, f"Document {key}", key, "approved-document",
                         [{"ref": f"1.{i}", "heading": "", "page": 1, "text": "x" * 600} for i in range(n)])
        for i in range(figs):
            store.add_figure(store.passage_rowid(key, i), PNG, 1)
    store.close()
    return path


failures = []


def expect(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        failures.append(what)


base = library("base", {"ADB1": (400, 60), "ADK": (100, 30), "ADB_FAQ": (10, 0)})
expect(check.main([base, base]) == 0, "an unchanged library passes")
expect(check.main([base]) == 0, "the first release (nothing to compare with) passes")

small_change = library("small", {"ADB1": (380, 58), "ADK": (104, 30), "ADB_FAQ": (4, 0)})
expect(check.main([small_change, base]) == 0, "a new edition with a few changes passes; small documents may vary")

missing = library("missing", {"ADB1": (400, 60), "ADB_FAQ": (10, 0)})
expect(check.main([missing, base]) == 1, "a missing document blocks publishing")

halved = library("halved", {"ADB1": (190, 60), "ADK": (100, 30), "ADB_FAQ": (10, 0)})
expect(check.main([halved, base]) == 1, "a document losing half its passages blocks publishing")

no_figures = library("nofigs", {"ADB1": (400, 5), "ADK": (100, 30), "ADB_FAQ": (10, 0)})
expect(check.main([no_figures, base]) == 1, "a document losing its figures blocks publishing")

expect(check.main([missing, base, "--accept"]) == 0, "--accept publishes anyway")

report = os.path.join(TMP, "report.md")
check.main([missing, base, "--report", report])
with open(report, encoding="utf-8") as f:
    text = f.read()
expect("ADK is missing" in text and "Not published" in text, "the report names the problem")

# a rebuild that changed nothing keeps the published edition, so installed apps aren't offered the same library
import json  # noqa: E402
import shutil  # noqa: E402

import edition  # noqa: E402


def stamped(path, version):
    store = Store(path, "r")
    words, images = store.content_hashes()
    store.set_meta(version=version, built=version, content_hash=words, images_hash=images)
    store.close()
    return {"version": version, "content_hash": words, "images_hash": images}


def copy(src, name):
    folder = os.path.join(TMP, name)
    os.makedirs(folder)
    return shutil.copy(src, os.path.join(folder, "regs.sqlite"))


published = stamped(base, "2026.11.12")
rebuilt = copy(base, "rebuilt")
stamped(rebuilt, "2026.11.13")
kept = edition.keep(rebuilt, base)
with open(os.path.join(os.path.dirname(rebuilt), "library.json")) as f:
    described = json.load(f)
expect(kept and described["version"] == "2026.11.12" and os.path.exists(rebuilt + ".gz"),
       "a rebuild that changed nothing keeps the published edition")

installed = Store(base, "r", readonly=True)
expect(installed.same_content({**published, "version": "2026.11.13"}),
       "the app doesn't take a later date on the same words and images for an update")
expect(installed.same_content({"version": "2026.11.13", "content_hash": published["content_hash"]}),
       "a library published before images were fingerprinted is compared by its words")
expect(not installed.same_content({"version": "2026.11.13"}), "a library that doesn't say what it holds is an update")

reworded = copy(base, "reworded")
store = Store(reworded, "r")
store.db.execute("UPDATE passages SET text = text || ' amended' WHERE id = 1")
store.db.commit()
store.close()
new = stamped(reworded, "2026.11.13")
expect(not edition.keep(reworded, base) and not installed.same_content(new),
       "a reworded paragraph makes a new edition, and the app offers it")

redrawn = copy(base, "redrawn")
store = Store(redrawn, "r")
store.db.execute("UPDATE figures SET png = CAST(png || x'00' AS BLOB) WHERE id = 1")
store.db.commit()
store.close()
new = stamped(redrawn, "2026.11.13")
expect(not edition.keep(redrawn, base) and not installed.same_content(new),
       "a drawing cut again makes a new edition, though no word changed")
installed.close()

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
