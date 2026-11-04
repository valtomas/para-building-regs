"""End-to-end checks with a stand-in model: no real engine or downloaded library needed.

python3 tests/test_app.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
TMP = tempfile.mkdtemp()
os.environ["PARA_APP_DIR"] = os.path.join(TMP, "app")
os.environ["PARA_LIBRARY"] = os.path.join(TMP, "regs.sqlite")
os.environ["HOME"] = TMP  # the files folder goes under it

from para.splitter import split  # noqa: E402
from para.store import Store  # noqa: E402

K_TEXT = ["APPROVED DOCUMENT K\nSection 1: Stairs and ladders\n1.1 Stairs should have level steps.\n"
          "1.2 For a private stair the maximum rise is 220 mm and the minimum going is 220 mm.\n3",
          "APPROVED DOCUMENT K\n1.3 Headroom over a stair should be at least 2.0 m.\n4",
          "APPROVED DOCUMENT K\nSection 3: Guarding\n3.2 Guarding to a balcony in a dwelling should be 1100 mm high.\n5"]
lib = Store(os.environ["PARA_LIBRARY"], "r")
lib.add_source("ADK", "Approved Document K: Protection from falling, collision and impact", "ADK",
               "approved-document", split(K_TEXT), url="https://example.gov.uk/adk.pdf", license="OGL v3.0",
               published="2013 edition")
TINY_PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                         "1f15c4890000000d49444154789c6360000002000105e9a4a10000000049454e44ae426082")
lib.add_figure(lib.passage_rowid("ADK", 0), TINY_PNG, 3)
lib_pid3 = f"r{lib.passage_rowid('ADK', 1)}"
lib.add_figure(lib.passage_rowid("ADK", 1), TINY_PNG, 5, "ADK_Diagram-3.1_p5", "Diagram 3.1",
               "A section through the edge of a balcony with guarding 1100 mm high.")
lib.set_meta(version="2026.11.03", built="test")
lib.close()

from tests.fake_llm import start  # noqa: E402
from para import server  # noqa: E402

fake, fake_url = start()
httpd = server.Server(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{httpd.server_address[1]}"


def call(path, body=None, method=None, headers=None, raw=None):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers=headers or {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def ask(question, cid=None):
    events = []
    for line in call("/api/ask", {"question": question, "id": cid}).decode().split("\n\n"):
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        failures.append(what)


st = json.loads(call("/api/state"))
check(st["library"]["version"] == "2026.11.03" and len(st["library"]["sources"]) == 1, "library loads")
check(st["settings"]["engine"]["mode"] == "", "no engine until set up")
ev = ask("stair rise?")
check(ev[-1]["type"] == "error" and "No AI engine" in ev[-1]["text"], "asks to set up an engine")

call("/api/settings", {"engine": {"mode": "external", "base_url": fake_url, "model": ""}})
st = json.loads(call("/api/state"))
check(st["settings"]["private"] is True, "local engine counts as private")
t = json.loads(call("/api/test-engine", {}))
check(t["ok"] and t["reply"] == "OK" and t["model"] == "fake-model", "test engine picks the first model")

hits = json.loads(call("/api/search?q=" + urllib.parse.quote("Part K private stair rise")))["results"]
check(hits and hits[0]["ref"].startswith("1.1"), "direct search finds the paragraph")

ev = ask("What is the maximum rise of a private stair?")
types = [e["type"] for e in ev]
check("search" in types and "read" in types and "thinking" in types, "searches, reads, streams reasoning")
done = ev[-1]
check(done["type"] == "done", "finishes")
check("220 mm" in done["answer"] and "checking" not in done["answer"], "answer text, <think> split off")
check(len(done["cites"]) == 1 and done["dropped"] == ["r99999"] and "[r99999]" not in done["answer"],
      "keeps the real citation, drops the made-up one")
cid = ev[0]["id"]
chat = json.loads(call("/api/chat?id=" + cid))
check(len(chat["turns"]) == 1 and chat["messages"][-1]["role"] == "assistant", "chat saved")
pid = list(done["cites"])[0]
doc = json.loads(call("/api/passage?id=" + pid))
check(doc["focus"] == pid and len(doc["parts"]) >= 2 and doc["license"] == "OGL v3.0", "reader opens the passage")
fig = doc["parts"][0]["figures"]
check(len(fig) == 1 and fig[0]["width"] == 1, "passages list their figures")
check(call(f"/api/figure?id={fig[0]['id']}") == TINY_PNG, "figure image served")
check(fig[0]["description"] == "", "a figure without a checked description has none")
try:
    call("/api/figure?id=../etc")
    check(False, "bad figure id refused")
except urllib.error.HTTPError as e:
    check(e.code == 404, "bad figure id refused")

ev2 = ask("And headroom?", cid)
check(ev2[-1]["type"] == "done", "follow-up question in the same chat")

ev3 = ask("Answer from memory: what is a higher-risk building?")
check(any(e["type"] == "retry" for e in ev3) and any(e["type"] == "search" for e in ev3) and ev3[-1]["cites"],
      "an answer from memory is sent back to search first")

# the person's files
data = b"# Fire strategy\n\n2.1 The building has a sprinkler system to BS 9251.\n\n2.2 Travel distance is 7.5 m."
call("/api/files", raw=data, headers={"X-Filename": "Fire%20strategy.md"})
files = json.loads(call("/api/state"))["files"]
check(len(files) == 1 and files[0]["status"] == "ok" and files[0]["passages"] >= 1, "adds a file")
hits = json.loads(call("/api/search?q=sprinkler"))["results"]
check(hits and hits[0]["id"].startswith("f"), "files are searched")
try:
    call("/api/files", raw=b"x", headers={"X-Filename": "photo.jpg"})
    check(False, "refuses unsupported files")
except urllib.error.HTTPError as e:
    check(e.code == 400, "refuses unsupported files")

# a page on another site can't drive the app
try:
    call("/api/settings", {"project_notes": "x"}, headers={"Content-Type": "application/json",
                                                           "Origin": "https://evil.example"})
    check(False, "blocks cross-site posts")
except urllib.error.HTTPError as e:
    check(e.code == 403, "blocks cross-site posts")

# engines without tools: excerpts attached
from para import core  # noqa: E402
from para.library import Library  # noqa: E402
from para.llm import Client  # noqa: E402

out = list(core.chat_events(Client(fake_url, "", "fake-model"), Library(), [], "private stair rise",
                            server.settings.load(), supports_tools=False))
check(out[-1]["type"] == "done" and out[-1]["cites"], "no-tools mode cites attached excerpts")

# citations written with the model's own words, or as a range, still link; unseen ids still go
class StubLibrary:
    def describe(self, pid):
        return {"id": pid, "source": "BR 2010", "ref": "reg 11G"}


text, cites, dropped = core._check_citations("Competence [BR 2010, reg 11G(1), r12]. Duties (r20–r22). Not [r99].",
                                             {"r12", "r20", "r22"}, StubLibrary())
check(text == "Competence (BR 2010, reg 11G(1)) [r12]. Duties [r20][r22]. Not." and set(cites) == {"r12", "r20", "r22"}
      and dropped == ["r99"], "citations inside the model's own brackets link; made-up ones still dropped")



class RefLibrary:
    PASSAGES = {"r1": ("ADB1", "Table 3.1"), "r2": ("ADB1", "3.20–3.22"), "r3": ("ADK", "Diagram 3.1")}

    def describe(self, pid):
        if pid not in self.PASSAGES:
            return None
        source, ref = self.PASSAGES[pid]
        return {"id": pid, "source": source, "title": "", "ref": ref}


text, cites, _ = core._check_citations("A [ADB1, Table 3.1]. B (ADB Vol 1, para 3.21). C [ADK, Diagram 3.1]. "
                                       "D [ADB1, Table 3.2]. E (ADB1, para 3.20d).", {"r1", "r2"}, RefLibrary())
check(text == "A [r1]. B (ADB Vol 1, para 3.21) [r2]. C [ADK, Diagram 3.1]. D [ADB1, Table 3.2]. "
      "E (ADB1, para 3.20d) [r2]." and
      set(cites) == {"r1", "r2"}, "references written in words link only to passages the model saw")

# splitting: cross-references wrapped onto a new line are not captions; index pages are skipped
from para.splitter import skipped_pages  # noqa: E402

refs = [p["ref"] for p in split(["4.1 Provide barriers where vehicles have access, as shown in\nDiagram 4.1).\n\n"
                                 "Diagram 4.1 Barrier siting\n\n4.2 Barriers should be provided in accordance with "
                                 "all of the following.\n\nTable 1.1. e. Comply with paragraph 1.9b."])]
check("Diagram 4.1" in refs and refs.count("Diagram 4.1") == 1 and "Table 1.1" not in refs,
      "cross-references don't start figure passages")
index = "Index\n" + "\n".join(f"Topic {i} 1.{i}, Diagram {i}.1" for i in range(12))
more = "\n".join(f"Other topic {i} Table {i}.2; 3.{i}" for i in range(12))
check(skipped_pages(["1.1 Text.", index, more, "2.1 Body text again."]) == {2, 3}, "index pages skipped")

# page furniture: a footer padded across the page, a header with the section's tab beside it (on left-hand pages only)
from para.splitter import clean_pages  # noqa: E402

footer = "Building Regulations 2010" + " " * 70 + "Approved Document B Volume 1, 2019 edition" + " " * 20 + "{}"
tabs = ["B1", "B2", "B3", "B4", "B5"]
words = "stairs ramps guarding doors windows walls floors roofs lifts lobbies".split()
pages = [(f"{tabs[n // 4]}      ONLINE VERSION\n" if n % 2 else "ONLINE VERSION\n") + f"{n}.1 Paragraph about "
         f"{words[n % 10]} and {words[n // 2]}.\n" + footer.format(n) for n in range(20)]
pages[3] = "CONTENTS      ONLINE VERSION\n" + pages[3]
cleaned = clean_pages(pages)
check(all("ONLINE" not in p.replace("CONTENTS      ONLINE VERSION", "") and "edition" not in p for p in cleaned)
      and "CONTENTS" in cleaned[3] and all(f"{n}.1 Paragraph about" in p for n, p in enumerate(cleaned)),
      "page headers and footers are removed, page text and a Contents heading stay")

# descriptions of drawings: written by a model from the image, in the library only once a person has checked them
sys.path.insert(0, os.path.join(ROOT, "library"))
import describe  # noqa: E402

doc3 = json.loads(call("/api/passage?id=" + lib_pid3))
check(doc3["parts"][next(i for i, p in enumerate(doc3["parts"]) if p["id"] == lib_pid3)]["figures"][0]["description"]
      .startswith("A section through"), "the reader gets a figure's checked description")
with open(os.path.join(ROOT, "para", "static", "app.js"), encoding="utf-8") as f:
    app_js = f.read()
check(describe.LABEL == "Description of the drawing (written by AI from the image, checked by a person):"
      and f'"{describe.LABEL}"' in app_js, "descriptions are labelled as AI-written and checked, the same in the reader")
check(describe.figure_key("ADB1", "Diagram 3.8", 40) == "ADB1_Diagram-3.8_p40" and
      describe.figure_key("ADM amendments 2024", "Diagram 1.2", 5) == "ADM-amendments-2024_Diagram-1.2_p5",
      "figure keys name the document, the figure and the page")
describe.DESC_DIR = os.path.join(TMP, "descriptions")
other_png = TINY_PNG + b"x"
h = describe.image_hash(TINY_PNG)
describe.save({"key": "K_Diagram-1_p1", "image_sha256": h, "reviewed": False, "description": "A plan."})
rec = describe.load("K_Diagram-1_p1")
check(not describe.needs_writing(rec, h) and describe.needs_writing(rec, describe.image_hash(other_png))
      and describe.needs_writing(None, h), "a description is written again only when its image changed")
check(describe.usable("K_Diagram-1_p1", TINY_PNG) is None, "an unreviewed description stays out of the library")
fig1 = {"key": "K_Diagram-1_p1", "png": TINY_PNG}
describe.approve(fig1, "A plan of a flat.")
rec = describe.load("K_Diagram-1_p1")
check(rec["reviewed"] and rec["description"] == "A plan of a flat." and rec["model_description"] == "A plan."
      and describe.usable("K_Diagram-1_p1", TINY_PNG) == "A plan of a flat.",
      "approving keeps the person's correction and what the model wrote")
check(not describe.needs_writing(rec, describe.image_hash(other_png)) and describe.stale(rec, describe.image_hash(other_png))
      and describe.usable("K_Diagram-1_p1", other_png) is None,
      "a reviewed description is never overwritten, and leaves the library if its image changes")

describe.save({"key": "K_Diagram-2_p2", "image_sha256": h, "reviewed": False, "description": "Running text."})
describe.approve({"key": "K_Diagram-2_p2", "png": TINY_PNG}, None, approved=True, rejected=True)
rec = describe.load("K_Diagram-2_p2")
check(rec["rejected"] and not rec["reviewed"] and describe.usable("K_Diagram-2_p2", TINY_PNG) is None
      and not describe.needs_writing(rec, h) and describe.needs_writing(rec, describe.image_hash(other_png)),
      "a rejected description stays out, and is written again only for a new image")
model_text = describe.write_description(
    Client(fake_url, "", "fake-model"),
    {"source": "ADK", "caption": "Diagram 1.1 Rise and going", "drawing_text": "Rise; Going", "png": TINY_PNG})
check(model_text.startswith("A drawing"), "a description is written from the image through the engine's API")

# figures: a drawing's text is smaller than the running text, so a long label doesn't cut the drawing short
import figures  # noqa: E402

body = figures.Line(57, 100, 540, 114, "2.9 A non-powered manually operated entrance door, fitted with a closer", 0)
label = figures.Line(141, 254, 405, 263, "The central door may be omitted if maximum travel distance is not more", 1)
part = figures.Line(141, 118, 264, 127, "a. Corridor access without dead ends", 2)
check(figures._is_body(body, 483, 57, 14) and not figures._is_body(label, 483, 57, 14)
      and not figures._is_body(part, 483, 57, 14), "a drawing's labels are not running text")

# figures: on a page in two columns, a figure in one column isn't cut short by the text beside it (ADM2 Diagram 3)
L = figures.Line
cap = [L(332, 388, 512, 402, "Diagram 3 Relationship of ramp", 22), L(393, 402, 527, 416, "gradient to the going", 22)]
beside = [L(71, 380 + 14 * i, 298, 394 + 14 * i, "to negotiate a ramp than a stair. In addition, adverse", 10)
          for i in range(4)]
table_head = [L(300, 395, 520, 406, "Minimum fire resistance of doorset in terms of integrity", 17)]
check(figures._column(cap, cap + beside, 595, 842, 14) == (326, 535)
      and figures._column([L(65, 394, 248, 408, "Table C1 Provisions for fire doorsets", 15)],
                          table_head, 595, 842, 14) is None,
      "a figure set in one column of two is cropped to that column; a table across the page is not")

# built-in engine: llama.cpp's "latest" release can have no binaries; builds are pre-releases
from para import engine  # noqa: E402

releases = [{"tag_name": "v0.8.0", "assets": [{"name": "nightly-tag.txt"}]},
            {"tag_name": "b2", "prerelease": True, "assets": [{"name": "llama-b2-bin-ubuntu-x64.tar.gz"}]}]
engine._json = lambda url: releases
rel, asset = engine._find_release("ggml-org/llama.cpp", "latest", r"bin-ubuntu-x64\.(zip|tar\.gz)$")
check(rel["tag_name"] == "b2" and asset["name"].startswith("llama-b2"), "finds the newest llama.cpp build")

# built-in engine memory: no load that won't fit, no engine left behind, none kept running idle
if os.name != "nt":
    import subprocess  # noqa: E402
    import time  # noqa: E402

    bundled = engine.Bundled()
    fake_dir = os.path.join(bundled.engine_dir, "b0")
    os.makedirs(fake_dir, exist_ok=True)
    fake_server = os.path.join(fake_dir, "llama-server")
    with open(fake_server, "w") as f:
        f.write("#!/bin/sh\nexec sleep 60\n")
    os.chmod(fake_server, 0o755)
    small = engine.catalog()["models"][-1]
    os.makedirs(bundled.models_dir, exist_ok=True)
    open(bundled.model_path(small), "wb").close()
    real_ram, real_vram = engine.available_ram_gb, engine.free_vram_gb
    engine.available_ram_gb, engine.free_vram_gb = (lambda: 1.0), (lambda: 0.0)
    try:
        bundled.start(small["id"])
        refused = ""
    except engine.EngineError as e:
        refused = str(e)
    check("Not enough free memory" in refused and bundled.proc is None, "a model that won't fit isn't loaded")
    engine.free_vram_gb = lambda: 64.0  # a graphics card's own memory counts too
    with open(fake_server, "w") as f:
        f.write("#!/bin/sh\nexit 1\n")  # past the memory check, the stand-in stops at once
    try:
        bundled.start(small["id"])
        started = ""
    except engine.EngineError as e:
        started = str(e)
    check("stopped while loading" in started, "free memory on a graphics card counts towards a model")
    with open(fake_server, "w") as f:
        f.write("#!/bin/sh\nexec sleep 60\n")
    engine.available_ram_gb, engine.free_vram_gb = real_ram, real_vram

    left = subprocess.Popen([fake_server])  # an engine an earlier run left behind
    with open(bundled.pid_path, "w") as f:
        f.write(f"{left.pid} {fake_server}\n")
    engine.Bundled()
    try:
        left.wait(10)
    except subprocess.TimeoutExpired:
        left.kill()
    check(left.returncode is not None and left.returncode != 0 and not os.path.exists(bundled.pid_path),
          "an engine left behind by an earlier run is stopped")

    bundled.proc = subprocess.Popen([fake_server])
    bundled.last_used = time.time()
    busy = bundled.stop_if_idle(minutes=1)
    bundled.last_used = time.time() - 120
    check(not busy and bundled.stop_if_idle(minutes=1) and bundled.proc is None, "an idle engine is stopped")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
