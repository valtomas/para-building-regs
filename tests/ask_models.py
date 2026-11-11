"""Ask real models the test questions through a running Para and score what they did. Results: docs/model-tests.md.

python3 -m para --browser --port 8765                                  (throwaway PARA_APP_DIR and HOME)
python3 tests/ask_models.py ask http://127.0.0.1:8765 out.jsonl MODEL [MODEL…]   (bundled:<id>: built-in engine)
python3 tests/ask_models.py score out.jsonl [--answers]
python3 tests/ask_models.py check library/dist/regs.sqlite             (the passages the questions rest on are there)

The scores are checks for the expected figures and the expected passage, so read the answers too: a right figure from
the wrong document, or a right answer the check misses, only shows there.
"""
import json
import re
import sys
import time
import urllib.error
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
    # asked in a project with notes and no files, as in the website's video: a model has credited library text to
    # "your project files", and called guarding requirement K3 after reading on into K3's opening
    ("landing", "What height of guarding do I need at the edge of the new landing?",
     [("ADK", "Diagram 3.1"), ("ADK", "3.2")]),
    # one figure from one paragraph or table, across the Parts
    ("sprinklers", "At what height does a block of flats need sprinklers?", [("ADB1", "7.4")]),
    ("office", "Maximum travel distance in an office where escape is possible in one direction only",
     [("ADB2", "Table 2.1")]),
    ("window", "Minimum size of an emergency escape window in a house", [("ADB1", "2.10"), ("ADB1", "3.6")]),
    ("alarm", "What standard of fire alarm system does a new house need?", [("ADB1", "1.1"), ("ADB FAQ", None)]),
    ("extract", "Minimum extract rates for a kitchen and a bathroom with intermittent fans", [("ADF1", "Table 1.1")]),
    ("water", "How much water may a new dwelling be designed to use per person?", [("BR 2010", "reg 36"), ("ADG", "2.3")]),
    ("bath", "Maximum hot water temperature at a bath in a new dwelling", [("ADG", "3.65")]),
    ("handrail", "What height should a handrail on a stair be?", [("ADK", "1.33–1.34")]),
    ("headroom", "What headroom does a stair in a loft conversion need?", [("ADK", "Diagram 1.4"), ("ADK", "1.13")]),
    ("airtight", "What is the worst air permeability allowed for a new dwelling?", [("ADL1", "Table 4.1")]),
    ("glazing", "Limiting U-value for windows in a new dwelling", [("ADL1", "Table 4.1")]),
    ("ramp", "Maximum gradient for a ramp with a 5 m going to a building other than a dwelling",
     [("ADM2", "Table 1"), ("ADK", "Diagram 2.1")]),
    ("charger", "Minimum rated output of an electric vehicle charge point", [("ADS", "6.2")]),
    ("hearth", "How thick must a constructional hearth be?", [("ADJ", "2.24")]),
    ("sound", "What airborne sound insulation must a separating wall between new houses achieve?",
     [("ADE", "Table 0.1a")]),
    ("gigabit", "What is the cost cap for a gigabit broadband connection to a new dwelling?", [("ADR1", "2.2")]),
    ("radon", "What is the radon action level?", [("ADC", "2.38")]),
    ("security", "What standard should the front door of a new dwelling meet for security?", [("ADQ", "1.2")]),
    # legislation and the higher-risk building procedures: time limits and duties
    ("cladding", "What reaction-to-fire class must the external wall materials of a relevant building achieve?",
     [("BR 2010", "reg 7"), ("ADB1", "10."), ("ADB2", "12.")]),
    ("gateway2", "How long does the regulator have to decide a building control approval application for a new "
                 "higher-risk building?", [("HRB Procedures Regs 2023", "reg 5"), ("HRB building control approval", None)]),
    ("change", "How long does the regulator have to decide a change control application?",
     [("HRB Procedures Regs 2023", "reg 22"), ("HRB changes to project", None)]),
    ("completion", "How long does the regulator have to decide a completion certificate application for a "
                   "higher-risk building?", [("HRB Procedures Regs 2023", "reg 41"), ("HRB completion certificate", None)]),
    ("doors", "How often must the flat entrance fire doors be checked in a block of flats over 11 m?",
     [("Fire Safety (England) Regs 2022", "reg 10")]),
    ("occurrence", "How long do I have to submit a mandatory occurrence report?",
     [("Mandatory occurrence notice", None), ("Mandatory occurrence reporting", None), ("HRB Procedures Regs 2023", None)]),
    ("electrics", "Which electrical work in a dwelling has to be notified to building control?",
     [("ADP", "2.5"), ("BR 2010", "reg 12")]),
    # a figure the passage doesn't hold, worked out from it: the figure note should name the sum and nothing else
    ("flight", "A flight in a house has 13 risers of 200 mm each. What is its total rise, and is a 200 mm rise allowed?",
     [("ADK", "Table 1.1")]),
    # not in the library: a right answer says so, and cites no passage as if it answered the question
    ("scotland", "What is the maximum rise of a stair in a house in Scotland?", []),
    ("nhbc", "What do the NHBC Standards say about the spacing of cavity wall ties?", []),
]
# what a right answer says, for the questions without their own check in right_facts: every pattern must be found
FACTS = {
    "sprinklers": [r"11\s?m"], "office": [r"18\s?m"], "window": [r"0\.33\s?m", r"450\s?mm"],
    "alarm": [r"D2", r"LD3"], "extract": [r"30\s?l/s|30 litres", r"15\s?l/s|15 litres"],
    "water": [r"125\s?(l|litres)"], "bath": [r"48\s?°\s?C|48 degrees"], "handrail": [r"900\s?mm", r"1,?000\s?mm|1\s?m\b"],
    "headroom": [r"1\.9\s?m|1,?900\s?mm", r"1\.8\s?m|1,?800\s?mm"], "airtight": [r"\b8(\.0)?\s?m", r"50\s?Pa"],
    "glazing": [r"1\.6"], "ramp": [r"1\s?:\s?15|1 in 15"], "charger": [r"7\s?kW"], "hearth": [r"125\s?mm"],
    "sound": [r"45\s?dB"], "gigabit": [r"£\s?2,?000"], "radon": [r"200\s?Bq"], "security": [r"PAS 24"],
    "cladding": [r"A2-s1,\s?d0", r"\bA1\b"], "gateway2": [r"12 weeks|twelve weeks"], "change": [r"6 weeks|six weeks"],
    "completion": [r"8 weeks|eight weeks"], "doors": [r"12 months|annual|every year|once a year"],
    "occurrence": [r"10 (calendar )?days|ten (calendar )?days"],
    "electrics": [r"new circuit", r"consumer unit", r"special location"],
    "flight": [r"2,?600\s?mm|2\.6\s?m", r"220\s?mm"],
    "scotland": [r"England", r"(?i)Scot(land|tish)[^.]*(different|own|not cover|separate|doesn't|does not)|"
                             r"(not cover|doesn't cover|does not cover|only covers?)[^.]*"],
    "nhbc": [r"(?i)not (in|part of|included in|covered by) the library|library (doesn't|does not) (cover|include|hold)|"
             r"Not from the library"],
}
NO_PASSAGE = {key for key, _q, right in QUESTIONS if not right}
PROJECT = {"name": "Loft test", "notes": "Loft conversion of a two-storey semi-detached house: a new second floor "
                                         "with one bedroom and a shower room."}
IN_PROJECT = {"landing"}


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
    if key == "landing":
        return bool(re.search(r"900\s?mm", a)) and not re.search(r"\bK3\b", a)
    return key in FACTS and all(re.search(pattern, a) for pattern in FACTS[key])


def credits_their_files(rec):
    """Says something comes from the person's files or notes while citing none of their files (ids f…)."""
    return bool(re.search(r"\b(your|their) (own |project )?(files?|notes|documents?)\b", rec["answer"], re.I)) and \
        not any(pid.startswith("f") for pid in rec["cites"])


def _post(base, path, body):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    return urllib.request.urlopen(req, timeout=1800)


def _ask(base, question, project=""):
    t0, events, done, error = time.time(), [], None, None
    with _post(base, "/api/ask", {"question": question, "project": project}) as r:
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
        try:
            _post(base, "/api/projects", PROJECT).read()
        except urllib.error.HTTPError:  # made by an earlier model's run
            pass
        for key, question, _right in QUESTIONS:
            seconds, events, done, error = _ask(base, question, PROJECT["name"] if key in IN_PROJECT else "")
            cites = done.get("cites") or {}
            rec = {"model": model, "q": key, "seconds": seconds, "error": error, "answer": done.get("answer", ""),
                   "searches": [e["query"] for e in events if e["type"] == "search"],
                   "reads": [f"{e['source']} {e.get('ref') or ''}".strip() for e in events if e["type"] == "read"],
                   "retries": [e["why"] for e in events if e["type"] == "retry"],
                   "cites": {k: f"{v['source']} {v.get('ref') or ''}".strip() for k, v in cites.items()},
                   "opened": {pid: _opened(base, pid) for pid in cites}, "dropped": done.get("dropped", []),
                   # the app's own notes about the answer, to be read against it: figures it couldn't find in the
                   # passage cited, and citations made from a search extract without reading the passage
                   "figures": [f"{f['figure']} ({', '.join(cites[i]['source'] + ' ' + (cites[i].get('ref') or '') for i in f['ids'] if i in cites)})"
                               for f in done.get("figures") or []],
                   "extract_only": [f"{v['source']} {v.get('ref') or ''}".strip() for v in cites.values()
                                    if v.get("read") is False]}
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
            print(f"{model[:40]:40s} {key:9s} {seconds}s cites={list(rec['cites'].values())[:4]} "
                  f"dropped={rec['dropped']} error={error}", flush=True)


QUESTIONS_BY_KEY = {k: right for k, _q, right in QUESTIONS}


def _matches(ref, wanted):
    """A passage's ref ("3.65–3.66", "Table 3.1, Diagram 3.2", "reg 7 (part 1)") is the one wanted or starts with it."""
    return wanted is None or wanted in ref.split(", ") or \
        bool(re.match(re.escape(wanted) + (r"" if wanted.endswith(".") else r"(?!\d)"), ref))


def cited_right(rec):
    """Does one of its citations open the passage a right answer rests on? For a question the library can't answer,
    right is citing nothing."""
    if rec["q"] in NO_PASSAGE:
        return not rec["cites"]
    for o in rec["opened"].values():
        for source, ref in QUESTIONS_BY_KEY[rec["q"]]:
            if o.get("source") == source and _matches(o.get("ref", ""), ref):
                return True
    return False


def check(library):
    """Says which questions rest on a passage the library doesn't have (a document renamed, paragraphs split
    another way), so a model isn't marked down for the test's mistake. Run it after a library build."""
    import sqlite3
    db = sqlite3.connect(f"file:{library}?mode=ro", uri=True)
    rows = db.execute("SELECT s.short, p.ref FROM passages p JOIN sources s ON s.id = p.source_id").fetchall()
    missing = [(key, source, ref) for key, _q, right in QUESTIONS for source, ref in right
               if not any(short == source and _matches(found or "", ref) for short, found in rows)]
    for key, source, ref in missing:
        print(f"{key}: no passage {source} {ref or '(any)'}")
    print(f"{len(QUESTIONS)} questions; {len(missing)} expected passages missing")
    return not missing


def score(path, answers=False):
    rows = [json.loads(line) for line in open(path, encoding="utf-8")]
    print("| Model | Right figures | Searched | Cited | Right passage cited | Made-up ids removed "
          "| Credits their files wrongly | Figures flagged | Extract-only citations | Median time |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for model in dict.fromkeys(r["model"] for r in rows):
        rs = [r for r in rows if r["model"] == model]
        times = sorted(r["seconds"] or 0 for r in rs)
        n = len(rs)
        print(f"| {model} | {sum(right_facts(r['q'], r['answer'], cited_right(r)) for r in rs)}/{n} | "
              f"{sum(bool(r['searches']) for r in rs)}/{n} | {sum(bool(r['cites']) for r in rs)}/{n} | "
              f"{sum(cited_right(r) for r in rs)}/{n} | {sum(len(r['dropped']) for r in rs)} | "
              f"{sum(credits_their_files(r) for r in rs)} | {sum(len(r.get('figures') or []) for r in rs)} | "
              f"{sum(len(r.get('extract_only') or []) for r in rs)} | {times[len(times) // 2]:.0f} s |")
    if answers:
        for r in rows:
            print(f"\n===== {r['model']} / {r['q']} {r['seconds']}s searches={r['searches']} reads={r['reads']} "
                  f"cites={list(r['cites'].values())} dropped={r['dropped']} retries={r.get('retries')} "
                  f"error={r['error']} figures_flagged={r.get('figures')} extract_only={r.get('extract_only')}"
                  f"\n{r['answer']}")


if __name__ == "__main__":
    if sys.argv[1:2] == ["ask"]:
        ask(sys.argv[2], sys.argv[3], sys.argv[4:])
    elif sys.argv[1:2] == ["score"]:
        score(sys.argv[2], "--answers" in sys.argv)
    elif sys.argv[1:2] == ["check"]:
        sys.exit(0 if check(sys.argv[2]) else 1)
    else:
        print(__doc__)
