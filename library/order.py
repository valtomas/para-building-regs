"""Check that passages read in the order the PDF's columns do.

python3 library/order.py [library/dist/regs.sqlite] [--list] [DOC…]

The build reads pages with pdftotext -layout, which keeps tables but gives two columns side by side, and
splitter.unweave_columns puts them back in order. This reads each page another way (the left part of the page, then
the right, cut at several places, without -layout) and says how much of each passage's words turn up in that order.
A passage whose columns are still woven together scores about a half. It is a check, not proof: a low score can
also be a passage that runs over three pages, or one beside a table. Read the low ones against the PDF.

Prints, per document, the passages of running text, the median score and how many are under 0.9; --list names them.
Run it before and after any change to the column reader, with the comparison of passages, refs and numbers.
"""
import concurrent.futures
import difflib
import glob
import os
import re
import sqlite3
import subprocess
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
CUTS = (0.5, 0.46, 0.54, 0.42, 0.58, 1.0)  # where the gutter may be, as a share of the page's width; 1.0: one column


def words(text):
    return re.findall(r"[A-Za-z]{2,}|\d+(?:\.\d+)?", text.lower())


def crop(path, page, x, width, height):
    out = subprocess.run(["pdftotext", "-enc", "UTF-8", "-f", str(page), "-l", str(page), "-x", str(x), "-y", "0",
                          "-W", str(width), "-H", str(height), path, "-"], capture_output=True).stdout
    return words(out.decode("utf-8", "replace"))


def columns(path):
    """{(page, cut): the page's words, left of the cut then right of it}"""
    info = subprocess.run(["pdfinfo", path], capture_output=True, check=True).stdout.decode()
    w, h = (int(float(n)) for n in re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info).groups())
    keys = [(p, cut) for p in range(1, int(re.search(r"Pages:\s+(\d+)", info).group(1)) + 1) for cut in CUTS]
    with concurrent.futures.ThreadPoolExecutor(16) as pool:
        return dict(zip(keys, pool.map(lambda k: crop(path, k[0], 0, int(w * k[1]), h) +
                                       crop(path, k[0], int(w * k[1]), w - int(w * k[1]), h), keys)))


def score(passage, page, cols):
    """The share of the passage's words found, in order, in its page read a column at a time (and the pages either
    side: a passage starts on one page and may end on the next)."""
    best = 0
    for cut in CUTS:
        there = cols.get((page - 1, cut), [])[-150:] + cols.get((page, cut), []) + cols.get((page + 1, cut), [])
        blocks = difflib.SequenceMatcher(None, there, passage, autojunk=False).get_matching_blocks()
        best = max(best, sum(b.size for b in blocks) / len(passage))
        if best > 0.95:
            break
    return best


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    library = args[0] if args and args[0].endswith(".sqlite") else os.path.join(HERE, "dist", "regs.sqlite")
    only = [a for a in args if not a.endswith(".sqlite")]
    db = sqlite3.connect(f"file:{library}?mode=ro", uri=True)
    total = 0
    for sid, short, url in db.execute("SELECT id, short, url FROM sources WHERE url LIKE '%.pdf' ORDER BY id").fetchall():
        found = glob.glob(os.path.join(HERE, "cache", "*_" + os.path.basename(urllib.parse.unquote(url))))
        if not found or (only and short not in only):
            continue
        cols, scores = columns(found[0]), []
        for ref, page, text in db.execute("SELECT ref, page, text FROM passages WHERE source_id = ? ORDER BY seq", (sid,)):
            text = text.split("Text in the drawing:")[0].split("Description of the drawing")[0]
            if not page or re.match(r"(Table|Diagram|Figure)\b", ref or "") or len(words(text)) < 40:
                continue  # a table's cells and a drawing's labels have no reading order to check
            scores.append((score(words(text), page, cols), ref or "(no number)", page))
        low = sorted(s for s in scores if s[0] < 0.9)
        total += len(low)
        if scores:
            print(f"{short:22s} {len(scores):4d} passages  median {sorted(scores)[len(scores) // 2][0]:.2f}  "
                  f"under 0.9: {len(low)}")
        if "--list" in sys.argv:
            for got, ref, page in low:
                print(f"    {got:.2f}  {ref}, page {page}")
    print(f"{total} passages under 0.9")


if __name__ == "__main__":
    main()
