"""Compare a newly built library with the one already published, and refuse to publish one that looks broken.

The weekly rebuild publishes to every installed app without anyone looking at it. If gov.uk or legislation.gov.uk
change their pages and the text comes out garbled or missing, the build itself may still succeed; this catches the
signs: a document gone, or a document, the whole library or its figures shrinking sharply.

python3 library/check.py NEW.sqlite OLD.sqlite [--report report.md] [--accept]

Exit code 0 when the new library is fine (or --accept was given), 1 when it should not be published. With no OLD
library (the first release), only basic checks on NEW are made.
"""
import argparse
import os
import sqlite3
import sys

# A document may lose this share of its passages or figures before it counts as broken. Real new editions change
# counts a little (a reworded paragraph, a merged table); a parsing failure loses most of them.
MAX_DOC_DROP = 0.25
MAX_TOTAL_DROP = 0.10
MIN_PASSAGES_TO_COMPARE = 20   # small documents (FAQs, amendment booklets) vary too much in relative terms
MIN_FIGURES_TO_COMPARE = 8
MIN_SOURCES = 40               # the England library has 64 documents; far fewer means fetching failed


def counts(path):
    """{source key: {"title", "passages", "chars", "figures"}} for a library file."""
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        has_figures = db.execute("SELECT 1 FROM sqlite_master WHERE name='figures'").fetchone() is not None
        rows = db.execute(
            "SELECT s.key, s.title, COUNT(p.id), COALESCE(SUM(LENGTH(p.text)), 0) FROM sources s "
            "LEFT JOIN passages p ON p.source_id = s.id GROUP BY s.id").fetchall()
        out = {k: {"title": t, "passages": n, "chars": c, "figures": 0} for k, t, n, c in rows}
        if has_figures:
            for key, n in db.execute("SELECT s.key, COUNT(f.id) FROM figures f JOIN passages p ON p.id = f.passage_id "
                                     "JOIN sources s ON s.id = p.source_id GROUP BY s.id"):
                out[key]["figures"] = n
        return out
    finally:
        db.close()


def dropped(old, new, minimum, limit):
    return old >= minimum and new < old * (1 - limit)


def compare(new, old):
    """(problems, notes): problems block publishing; notes are changes worth reading."""
    problems, notes = [], []
    if len(new) < MIN_SOURCES:
        problems.append(f"Only {len(new)} documents in the new library (expected at least {MIN_SOURCES}).")
    empty = [k for k, v in new.items() if v["passages"] == 0]
    if empty:
        problems.append("Documents with no passages: " + ", ".join(sorted(empty)))
    if not old:
        return problems, ["No published library to compare with."]
    for key in sorted(set(old) - set(new)):
        problems.append(f"{key} is missing (was {old[key]['passages']} passages): {old[key]['title']}")
    for key in sorted(set(new) - set(old)):
        notes.append(f"New document {key} ({new[key]['passages']} passages): {new[key]['title']}")
    for key in sorted(set(new) & set(old)):
        o, n = old[key], new[key]
        if dropped(o["passages"], n["passages"], MIN_PASSAGES_TO_COMPARE, MAX_DOC_DROP):
            problems.append(f"{key} passages fell from {o['passages']} to {n['passages']}")
        if dropped(o["chars"], n["chars"], MIN_PASSAGES_TO_COMPARE * 500, MAX_DOC_DROP):
            problems.append(f"{key} text fell from {o['chars'] // 1000}k to {n['chars'] // 1000}k characters")
        if dropped(o["figures"], n["figures"], MIN_FIGURES_TO_COMPARE, MAX_DOC_DROP):
            problems.append(f"{key} figures fell from {o['figures']} to {n['figures']}")
        if (o["passages"], o["figures"]) != (n["passages"], n["figures"]):
            notes.append(f"{key}: passages {o['passages']} → {n['passages']}, figures {o['figures']} → {n['figures']}")
    total_old = sum(v["passages"] for v in old.values())
    total_new = sum(v["passages"] for v in new.values())
    if total_old and total_new < total_old * (1 - MAX_TOTAL_DROP):
        problems.append(f"The whole library fell from {total_old} to {total_new} passages")
    return problems, notes


def report(problems, notes, accepted):
    lines = ["## Library check", ""]
    if problems:
        lines.append("**Not published: the new library looks broken.**" if not accepted
                     else "**Published anyway (accepted by hand), despite:**")
        lines += [""] + [f"- {p}" for p in problems]
        if not accepted:
            lines += ["", "If these changes are genuine (a document withdrawn or merged, a much shorter new edition), "
                      "run the workflow by hand with *accept library changes* ticked."]
    else:
        lines.append("Passed: no document missing or sharply smaller.")
    if notes:
        lines += ["", "### Changes", ""] + [f"- {n}" for n in notes[:80]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("new")
    ap.add_argument("old", nargs="?")
    ap.add_argument("--report")
    ap.add_argument("--accept", action="store_true", help="publish even if problems were found")
    args = ap.parse_args(argv)
    old = counts(args.old) if args.old and os.path.exists(args.old) else {}
    problems, notes = compare(counts(args.new), old)
    text = report(problems, notes, args.accept)
    print(text)
    if args.report:
        with open(args.report, "a", encoding="utf-8") as f:
            f.write(text)
    return 1 if problems and not args.accept else 0


if __name__ == "__main__":
    sys.exit(main())
