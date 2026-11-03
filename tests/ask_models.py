"""Ask real models the test questions through a running Para and score what they did. Results: docs/model-tests.md.

python3 -m para --browser --port 8765                                  (throwaway PARA_APP_DIR and HOME)
python3 tests/ask_models.py ask http://127.0.0.1:8765 out.jsonl MODEL [MODEL…]   (bundled:<id> for the built-in engine)
python3 tests/ask_models.py score out.jsonl [--answers]

The scores are checks for the expected figures and the expected passage, so read the answers too: a right figure from
the wrong document, or a right answer the check misses, only shows there.
"""
import json
import re
import sys
import time
import urllib.request

# key, question, the passage(s) a right answer cites: (source, ref or None for any part of it)
QUESTIONS = [
    ("travel", "What is the maximum travel distance in a single-direction escape route from a flat entrance door?",
     [("ADB1", "Table 3.1")]),
    ("stair", "Maximum rise, minimum going and maximum pitch for a private stair", [("ADK", "Table 1.1")]),
    ("guarding", "Guarding height for a balcony in a dwelling", [("ADK", "Diagram 3.1"), ("ADK", "3.2")]),
    ("uvalue", "Limiting U-value for a new external wall in a new dwelling", [("ADL1", "Table 4.1")]),
    ("hrb", "When is a building a higher-risk building during design and construction?",
     [("HRB criteria (new build)", None), ("Building Safety Act 2022", "s 65"), ("HRB Descriptions Regs 2023", None)]),
    ("pd", "What must a principal designer be competent to do?", [("BR 2010", "reg 11G")]),
    ("bathroom", "Does a bathroom in a flat need a fire door?", [("ADB1", "Diagram 3.2")]),
]


def right_facts(key, a, cited_right):
    """The figures a right answer gives."""
    if key == "travel":
        return bool(re.search(r"7\.5\s?m", a))
    if key == "stair":
        return bool(re.search(r"220\s?mm", a)) and "42" in a
    if key == "guarding":
        return bool(re.search(r"1,?100\s?mm|1\.1\s?m\b", a))
    if key == "uvalue":
        return "0.26" in a
    if key == "hrb":
        return bool(re.search(r"18\s?m", a)) and bool(re.search(r"\b7\s+storeys|seven storeys", a)) and \
            bool(re.search(r"\b2\b|two", a)) and bool(re.search(r"hospital|care home", a, re.I))
    if key == "pd":
        return bool(re.search(r"skills, knowledge,? (and )?experience", a, re.I)) and ("11G" in a or cited_right)
    if key == "bathroom":
        return bool(re.search(r"(not|n't)\s+(need|require)", a, re.I)) and \
            bool(re.search(r"fire[- ]resisting construction", a, re.I))
    return False


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=1800)


def _ask(base, question):
    t0, events, done, error = time.time(), [], None, None
    with _post(base, "/api/ask", {"question": question}) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                ev = json.loads(line[5:])
                if ev["type"] in ("search", "read", "show", "notice", "retry"):
                    events.append(ev)
                elif ev["type"] == "done":
                    done = ev
                elif ev["type"] == "error":
                    error = ev["text"]
    return round(time.time() - t0, 1), events, done or {}, error


def _opened(base, pid):
    """What the reader shows for a citation: the focused passage's document and ref."""
    with urllib.request.urlopen(f"{base}/api/passage?id={pid}", timeout=60) as r:
        doc = json.load(r)
    part = next((p for p in doc["parts"] if p["id"] == doc["focus"]), {})
    return {"source": doc["source"], "ref": part.get("ref") or ""}


def ask(base, out, models, engine="http://127.0.0.1:13305/api/v1"):
    for model in models:
        if model.startswith("bundled:"):
            settings = {"mode": "bundled", "bundled_model": model.split(":", 1)[1]}
        else:
            settings = {"mode": "external", "base_url": engine, "model": model}
        _post(base, "/api/settings", {"engine": settings}).read()
        for key, question, _right in QUESTIONS:
            seconds, events, done, error = _ask(base, question)
            cites = done.get("cites") or {}
            rec = {"model": model, "q": key, "seconds": seconds, "error": error, "answer": done.get("answer", ""),
                   "searches": [e["query"] for e in events if e["type"] == "search"],
                   "reads": [f"{e['source']} {e.get('ref') or ''}".strip() for e in events if e["type"] == "read"],
                   "retries": [e["why"] for e in events if e["type"] == "retry"],
                   "cites": {k: f"{v['source']} {v.get('ref') or ''}".strip() for k, v in cites.items()},
                   "opened": {pid: _opened(base, pid) for pid in cites}, "dropped": done.get("dropped", [])}
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{model[:40]:40s} {key:9s} {seconds}s cites={list(rec['cites'].values())[:4]} "
                  f"dropped={rec['dropped']} error={error}", flush=True)


QUESTIONS_BY_KEY = {k: right for k, _q, right in QUESTIONS}


def cited_right(rec):
    """Does one of its citations open the passage a right answer rests on?"""
    for o in rec["opened"].values():
        for source, ref in QUESTIONS_BY_KEY[rec["q"]]:
            if o.get("source") == source and (ref is None or ref in o.get("ref", "").split(", ")
                                              or o.get("ref", "").startswith(ref)):
                return True
    return False


def score(path, answers=False):
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    print("| Model | Right figures | Searched | Cited | Right passage cited | Made-up ids removed | Median time |")
    print("|---|---|---|---|---|---|---|")
    for model in dict.fromkeys(r["model"] for r in rows):
        rs = [r for r in rows if r["model"] == model]
        times = sorted(r["seconds"] or 0 for r in rs)
        n = len(rs)
        print(f"| {model} | {sum(right_facts(r['q'], r['answer'], cited_right(r)) for r in rs)}/{n} | "
              f"{sum(bool(r['searches']) for r in rs)}/{n} | {sum(bool(r['cites']) for r in rs)}/{n} | "
              f"{sum(cited_right(r) for r in rs)}/{n} | {sum(len(r['dropped']) for r in rs)} | "
              f"{times[len(times) // 2]:.0f} s |")
    if answers:
        for r in rows:
            print(f"\n===== {r['model']} / {r['q']} {r['seconds']}s searches={r['searches']} reads={r['reads']} "
                  f"cites={list(r['cites'].values())} dropped={r['dropped']} retries={r.get('retries')} "
                  f"error={r['error']}\n{r['answer']}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["ask"]:
        ask(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif sys.argv[1:2] == ["score"]:
        score(sys.argv[2], "--answers" in sys.argv)
    else:
        print(__doc__)
