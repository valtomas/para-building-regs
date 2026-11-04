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

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
