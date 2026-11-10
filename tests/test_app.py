"""End-to-end checks with a stand-in model: no real engine or downloaded library needed.

python3 tests/test_app.py
"""
import datetime
import importlib.util
import json
import os
import re
import sqlite3
import struct
import sys
import tempfile
import threading
import urllib.request
import zlib

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
server.Handler.hosts = server.local_hosts("127.0.0.1", httpd.server_address[1])  # as main() sets it
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

check({h["name"] for h in st["hosted"]} >= {"Anthropic", "Mistral", "OpenAI", "OpenRouter"} and
      all(h["base_url"].startswith("https://") for h in st["hosted"]), "hosted services are offered by address")
models = json.loads(call("/api/models", {"base_url": fake_url}))["models"]
check(models and models[0] == "fake-model", "models are listed by POST, so a typed key never sits in a URL")


def saved_engine():
    with open(os.path.join(os.environ["PARA_APP_DIR"], "settings.json"), encoding="utf-8") as f:
        return json.load(f)["engine"]


call("/api/settings", {"engine": {"mode": "external", "base_url": fake_url, "api_key": "key-one", "model": ""}})
call("/api/settings", {"engine": {"mode": "external", "base_url": fake_url + "/", "model": ""}})
check(saved_engine()["api_key"] == "key-one", "the saved key stays for the same server")
call("/api/settings", {"engine": {"mode": "external", "base_url": "https://api.mistral.ai/v1", "model": "m"}})
st = json.loads(call("/api/state"))
check(saved_engine()["api_key"] == "" and not st["settings"]["private"],
      "a key saved for one server is never kept for another, and a hosted service isn't private")
call("/api/settings", {"engine": {"mode": "external", "base_url": fake_url, "model": ""}})

hits = json.loads(call("/api/search?q=" + urllib.parse.quote("Part K private stair rise")))["results"]
check(hits and hits[0]["ref"].startswith("1.1"), "direct search finds the paragraph")

ev = ask("What is the maximum rise of a private stair?")
types = [e["type"] for e in ev]
check("search" in types and "read" in types and "thinking" in types, "searches, reads, streams reasoning")
results = [r for e in ev if e["type"] == "search" for r in e["results"]]
check(results and all(r["id"] and r["source"] and "ref" in r and "heading" in r for r in results),
      "search results carry what a citation's label needs while the answer streams")
nearby = [n for e in ev if e["type"] == "read" for n in e.get("nearby", [])]
check(nearby and all(n["id"] and n["source"] and "ref" in n for n in nearby),
      "a read carries the labels of the passages read along with it, which may be cited too")
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
ids = [h["id"] for h in server.library.search("sprinkler")]
server.myfiles.sync()
check(ids and [h["id"] for h in server.library.search("sprinkler")] == ids,
      "an unchanged file isn't read again, so its passage ids (and citations of them) stay")
try:
    call("/api/files", raw=b"x", headers={"X-Filename": "photo.jpg"})
    check(False, "refuses unsupported files")
except urllib.error.HTTPError as e:
    check(e.code == 400, "refuses unsupported files")

# projects: a folder of files searched only in the project's chats, with its own notes
st = json.loads(call("/api/projects", {"name": "Mill Lane", "notes": "3-storey house, new build"}))
check(st["project"] == "Mill Lane" and st["projects"][0]["notes"] == "3-storey house, new build", "creates a project")
for bad in ("", "../up", "a/b", ".hidden"):
    try:
        call("/api/projects", {"name": bad})
        check(False, f"refuses the project name {bad!r}")
    except urllib.error.HTTPError as e:
        check(e.code == 400, f"refuses the project name {bad!r}")
data = b"# Stair schedule\n\n4.1 The private stair rise is 190 mm in the Part K stair schedule."
call("/api/files", raw=data, headers={"X-Filename": "Stairs.md", "X-Project": "Mill%20Lane"})
st = json.loads(call("/api/state"))
mine = [f for f in st["files"] if f["project"] == "Mill Lane"]
check(len(mine) == 1 and st["projects"][0]["files"] == 1, "adds a file to a project")
check(os.path.dirname(mine[0]["key"]) == os.path.join(TMP, "Para Files", "Mill Lane"), "into the project's folder")
outside = ask("What is the maximum rise of a private stair?")
found_out = [r["id"] for e in outside if e["type"] == "search" for r in e["results"]]
check(not any(i.startswith("f") for i in found_out), "a chat outside the project doesn't search its files")
# the project is set when the chat is created, so ask a new chat with it
evs = []
for line in call("/api/ask", {"question": "What is the maximum rise of a private stair?",
                              "project": "Mill Lane"}).decode().split("\n\n"):
    if line.startswith("data: "):
        evs.append(json.loads(line[6:]))
found = [r["id"] for e in evs if e["type"] == "search" for r in e["results"]]
check(any(i.startswith("f") for i in found), "a project's chat searches its files")
cid_p = evs[0]["id"]
check(json.loads(call("/api/chat?id=" + cid_p))["project"] == "Mill Lane", "the chat belongs to the project")
from para import core  # noqa: E402
prompt = core.system_prompt(server.library, {"project": server.myfiles.project("Mill Lane")})
check('project "Mill Lane"' in prompt and "3-storey house" in prompt, "the project's notes go in the prompt")
check("its 1 file is searched" in prompt, "the prompt says how many files the project has")
empty = core.system_prompt(server.library, {"project": {"name": "Empty", "notes": "", "files": 0}})
check("no files of its own" in empty and "searched with the library" not in empty,
      "a project with no files isn't said to have files searched")
st = json.loads(call("/api/projects/update", {"name": "Mill Lane", "new_name": "Mill Lane 2", "notes": "Flats"}))
moved = [f for f in st["files"] if f["project"] == "Mill Lane 2"]
check(st["project"] == "Mill Lane 2" and moved and moved[0]["note"] == "" and st["projects"][0]["notes"] == "Flats"
      and os.path.exists(moved[0]["key"]), "renames a project, moving its files")
check(json.loads(call("/api/chat?id=" + cid_p))["project"] == "Mill Lane 2", "its chats move with it")
try:
    call("/api/projects?name=" + urllib.parse.quote("Mill Lane 2"), method="DELETE")
    check(False, "won't delete a project that still has files")
except urllib.error.HTTPError as e:
    check(e.code == 400, "won't delete a project that still has files")
call("/api/files?key=" + urllib.parse.quote(moved[0]["key"]), method="DELETE")
st = json.loads(call("/api/projects?name=" + urllib.parse.quote("Mill Lane 2"), method="DELETE"))
check(not st["projects"] and json.loads(call("/api/chat?id=" + cid_p))["project"] == "",
      "deletes an empty project; its chats stay, in no project")

# a page on another site can't drive the app
try:
    call("/api/settings", {"project_notes": "x"}, headers={"Content-Type": "application/json",
                                                           "Origin": "https://evil.example"})
    check(False, "blocks cross-site posts")
except urllib.error.HTTPError as e:
    check(e.code == 403, "blocks cross-site posts")

# nor can a page that points its own domain at this computer (DNS rebinding): the Host isn't the app's
for what, path, body in (("read", "/api/state", None), ("change", "/api/settings", {"project_notes": "x"})):
    try:
        call(path, body, headers={"Content-Type": "application/json", "Host": "evil.example:80",
                                  "Origin": "http://evil.example:80"})
        check(False, f"a page under another name can't {what} anything")
    except urllib.error.HTTPError as e:
        check(e.code == 403, f"a page under another name can't {what} anything")
check(server.local_hosts("0.0.0.0", 80) is None, "shared on the network, the app answers to any name")

# private means an address on this computer or network, not a name that starts like one
from para.llm import is_local  # noqa: E402

check(all(is_local(u) for u in ("http://127.0.0.1:11434/v1", "http://localhost:1234/v1", "http://192.168.1.20/v1",
                                "http://10.0.0.5:8080/v1", "http://172.20.1.1/v1", "http://[::1]:8080/v1",
                                "http://studio.local:1234/v1")), "local and network addresses are private")
check(not any(is_local(u) for u in ("https://10.evil.example/v1", "https://192.168.evil.example/v1",
                                    "https://127.0.0.1.evil.example/v1", "https://api.openai.com/v1",
                                    "https://172.15.0.1/v1", "https://8.8.8.8/v1")),
      "a name that starts like a private address isn't private")

# the person's own instructions keep what the placeholders they left out would have carried
own = core.system_prompt(server.library, {"instructions": "Answer briefly and cite.",
                                          "project_notes": "6-storey block, sprinklered"})
check(own.startswith("Answer briefly and cite.") and "6-storey block, sprinklered" in own and "ADK" in own
      and "Today is" in own, "custom instructions without placeholders keep the notes, library list and date")
own = core.system_prompt(server.library, {"instructions": "Library:\n{library}\nBe brief.{notes}",
                                          "project_notes": "sprinklered"})
check(own.count("sprinklered") == 1 and own.count(server.library.summary()) == 1,
      "placeholders that are there aren't filled in twice")
check("sprinklered" in core.system_prompt(server.library, {"instructions": "Use {braces} freely",
                                                           "project_notes": "sprinklered"}),
      "stray braces in custom instructions don't lose the notes")

# a chat carried over a library update: ids in its old results name other passages now, so they're forgotten
stale = next(e for e in ask("What is the maximum rise for a private stair?") if e["type"] == "chat")["id"]
chat_file = os.path.join(server.chats_dir(), stale + ".json")
with open(chat_file, encoding="utf-8") as f:
    saved = json.load(f)
check(saved["library"] == "2026.11.03" and any(re.search(r"\[r\d+\]", m["content"]) for m in saved["messages"]
                                               if m["role"] == "tool"), "a chat records its library's edition")
check(not any(e["type"] == "notice" for e in ask("And the going?", stale)), "same edition: lookups are kept")
saved["library"] = "2026.10.27"
with open(chat_file, "w", encoding="utf-8") as f:
    json.dump(saved, f)
events = ask("And the pitch?", stale)
with open(chat_file, encoding="utf-8") as f:
    after = json.load(f)
old_results = [m["content"] for m in after["messages"][:len(saved["messages"])] if m["role"] == "tool"]
check(any(e["type"] == "notice" and "updated" in e["text"] for e in events) and old_results
      and all(c == core.STALE_LOOKUP for c in old_results) and after["library"] == "2026.11.03",
      "after a library update, a chat's old lookups are forgotten and the person is told")
check(any(e["type"] == "done" and e["cites"] for e in events), "and it looks things up afresh, with citations")

# a project's files are chosen before the best matches are taken, not after
many = Store(os.path.join(TMP, "many.sqlite"), "f")
for n in range(210):
    many.add_source(f"/other/{n}.txt", f"Other {n}", f"Other {n}", "file",
                    [{"text": "Balustrade balustrade balustrade: the balustrade schedule."}])
many.add_source("/mine/spec.txt", "Spec", "Spec", "file",
                [{"text": "The stair has an oak handrail on steel posts, with a glass balustrade to the landing "
                          "and a painted softwood string, all as the joiner's drawings and the schedule of finishes."}])
found = many.search("balustrade", source_keys={"/mine/spec.txt"})
check([h["source"] for h in found] == ["Spec"], "a project's file is found among many better matches elsewhere")
check(len(many.search("balustrade")) == 8, "and a search of everything is as before")

# the chat list reads a chat's file again only when it has changed
listed = {c["id"]: c for c in server.list_chats()}
check(stale in listed and listed[stale]["title"].startswith("What is the maximum rise"), "the chat list has each chat")
real_load, reads = server.load_chat, []
server.load_chat = lambda cid: reads.append(cid) or real_load(cid)
server.list_chats()
check(not reads, "an unchanged chat isn't read again for the list")
after["title"] = "Stairs at Mill Lane"
server.save_chat(after)
check({c["id"]: c for c in server.list_chats()}[stale]["title"] == "Stairs at Mill Lane" and reads == [stale],
      "a changed chat is, and only that one")
server.load_chat = real_load
call("/api/chat?id=" + stale, method="DELETE")
check(stale not in {c["id"] for c in server.list_chats()}, "a deleted chat leaves the list")

# the engine's log keeps its end, not everything since the app was installed
from para import engine as eng  # noqa: E402

log_file = os.path.join(TMP, "engine.log")
with open(log_file, "wb") as f:
    f.write(b"old line\n" * 700000 + b"the last thing that went wrong\n")
eng._trim_log(log_file)
with open(log_file, "rb") as f:
    kept = f.read()
check(len(kept) <= eng.LOG_KEEP and kept.endswith(b"the last thing that went wrong\n") and kept.startswith(b"old line"),
      "a long engine log is cut to whole lines at its end")

# downloads: a part left over that can't be carried on is thrown away, not retried for ever
import hashlib  # noqa: E402
import http.server  # noqa: E402

PAYLOAD = bytes(range(256)) * 40


class Files(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        start = int((self.headers.get("Range") or "bytes=0-")[6:].rstrip("-"))
        if start >= len(PAYLOAD):
            self.send_response(416)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(PAYLOAD) - start))
        self.end_headers()
        self.wfile.write(PAYLOAD[start:])


files_srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Files)
threading.Thread(target=files_srv.serve_forever, daemon=True).start()
file_url = f"http://127.0.0.1:{files_srv.server_address[1]}/model.gguf"
digest = hashlib.sha256(PAYLOAD).hexdigest()


def fetch(part, **kw):
    dest = os.path.join(TMP, "model.gguf")
    for path in (dest, dest + ".part"):
        if os.path.exists(path):
            os.remove(path)
    if part is not None:
        with open(dest + ".part", "wb") as f:
            f.write(part)
    eng._download(file_url, dest, eng.Job("install", "test"), **kw)
    with open(dest, "rb") as f:
        return f.read() == PAYLOAD


check(fetch(PAYLOAD[:1000], expect_size=len(PAYLOAD), sha256=digest), "a download carries on from where it stopped")
check(fetch(b"x" * (len(PAYLOAD) + 5), expect_size=len(PAYLOAD), sha256=digest),
      "a leftover part bigger than the file is started again")
check(fetch(b"x" * (len(PAYLOAD) + 5)), "so is one the server has nothing more for, when the size isn't known")
check(fetch(PAYLOAD, expect_size=len(PAYLOAD), sha256=digest), "a part that's already whole is only checked")
files_srv.shutdown()

# a service that refuses max_tokens by name gets the limit as max_completion_tokens, and is remembered
from para.llm import Client  # noqa: E402
from tests import fake_llm  # noqa: E402

strict = Client(fake_url, model="strict-model")
said = "".join(e["text"] for e in strict.stream([{"role": "user", "content": "Reply with just: OK"}], max_tokens=50,
                                                 reasoning="off") if e["type"] == "content")
last = fake_llm.SEEN[-1]
check(said == "OK" and last.get("max_completion_tokens") == 50 and "max_tokens" not in last
      and "chat_template_kwargs" not in last, "a refused max_tokens is sent again as max_completion_tokens")
before = len(fake_llm.SEEN)
list(strict.stream([{"role": "user", "content": "Reply with just: OK"}], max_tokens=50, reasoning="off"))
check(len(fake_llm.SEEN) == before + 1, "and the next request is right first time")

# the built-in engine is a build that was tested, not whichever is newest
check(eng.catalog()["llama_cpp"]["tag"] != "latest", "the llama.cpp build is pinned")

# updating the app itself: only a copy that can replace itself is offered it, with GitHub's checksum
from para import updater  # noqa: E402

check(updater.kind() is None, "running from source, the app doesn't update itself")
release = {"Para-linux.AppImage": {"browser_download_url": "https://example.org/Para-linux.AppImage", "size": 9,
                                   "digest": "sha256:" + "ab" * 32}}
check(updater.offer(release) is None, "no update offered to a copy that can't replace itself")
real_kind, updater.kind = updater.kind, lambda: "appimage"
offered = updater.offer(release)
updater.kind = real_kind
check(offered == {"kind": "appimage", "url": "https://example.org/Para-linux.AppImage", "size": 9,
                  "sha256": "ab" * 32}, "an AppImage is offered the new AppImage and its checksum")
image = os.path.join(TMP, "Para.AppImage")
for path, text in ((image, "old"), (image + ".new", "new")):
    with open(path, "w") as f:
        f.write(text)
updater.swap_appimage(image + ".new", image)
with open(image) as f:
    check(f.read() == "new" and os.access(image, os.X_OK) and not os.path.exists(image + ".new"),
          "the new AppImage takes the old one's place, ready to run")
try:
    call("/api/updates/app", {})
    check(False, "won't update a copy that can't replace itself")
except urllib.error.HTTPError as e:
    check(e.code == 502 and "can't update itself" in json.load(e)["error"],
          "won't update a copy that can't replace itself")
check("app_job" in json.loads(call("/api/jobs")), "the page can follow an update's download")

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


def grey_png(w, h, shade=lambda x, y: 255):
    """A greyscale PNG, so the tests need no image library."""
    rows = b"".join(b"\x00" + bytes(shade(x, y) for x in range(w)) for y in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


other_png = grey_png(3, 2)
h = describe.image_hash(TINY_PNG)
describe.save({"key": "K_Diagram-1_p1", "image_sha256": h, "reviewed": False, "description": "A plan."})
rec = describe.load("K_Diagram-1_p1")
check(not describe.needs_writing(rec, TINY_PNG) and describe.needs_writing(rec, other_png)
      and describe.needs_writing(None, TINY_PNG), "a description is written again only when its image changed")
check(describe.usable("K_Diagram-1_p1", TINY_PNG) is None, "an unreviewed description stays out of the library")
fig1 = {"key": "K_Diagram-1_p1", "png": TINY_PNG}
describe.approve(fig1, "A plan of a flat.")
rec = describe.load("K_Diagram-1_p1")
check(rec["reviewed"] and rec["description"] == "A plan of a flat." and rec["model_description"] == "A plan."
      and describe.usable("K_Diagram-1_p1", TINY_PNG) == "A plan of a flat.",
      "approving keeps the person's correction and what the model wrote")
check(not describe.needs_writing(rec, other_png) and describe.stale(rec, other_png)
      and describe.usable("K_Diagram-1_p1", other_png) is None,
      "a reviewed description is never overwritten, and leaves the library if its image changes")

describe.save({"key": "K_Diagram-2_p2", "image_sha256": h, "reviewed": False, "description": "Running text."})
describe.approve({"key": "K_Diagram-2_p2", "png": TINY_PNG}, None, approved=True, rejected=True)
rec = describe.load("K_Diagram-2_p2")
check(rec["rejected"] and not rec["reviewed"] and describe.usable("K_Diagram-2_p2", TINY_PNG) is None
      and not describe.needs_writing(rec, TINY_PNG) and describe.needs_writing(rec, other_png),
      "a rejected description stays out, and is written again only for a new image")
model_text = describe.write_description(
    Client(fake_url, "", "fake-model"),
    {"source": "ADK", "caption": "Diagram 1.1 Rise and going", "drawing_text": "Rise; Going", "png": TINY_PNG})
check(model_text.startswith("A drawing"), "a description is written from the image through the engine's API")

# descriptions: the same picture rendered by another PDF renderer still matches; a different crop doesn't
if importlib.util.find_spec("PIL"):
    drawing = grey_png(60, 40, lambda x, y: 0 if 10 < x < 50 and y in (10, 30) else 255)
    shaded = grey_png(60, 40, lambda x, y: 0 if 10 < x < 50 and y in (10, 30) else 250 if y in (9, 11) else 255)
    taller = grey_png(60, 48, lambda x, y: 0 if 10 < x < 50 and y in (10, 30) else 255)
    rec = {"image_sha256": describe.image_hash(drawing), "image_sig": describe.image_sig(drawing)}
    check(describe.same_image(rec, shaded) and not describe.same_image(rec, taller),
          "a description follows its drawing across PDF renderers, but not to a different crop")
else:
    print("skip renderer tolerance (no Pillow)")

states = {}
for key, rec, png in [("K_Diagram-3_p3", None, TINY_PNG),
                      ("K_Diagram-4_p4", {"image_sha256": h, "reviewed": False, "description": "x"}, TINY_PNG),
                      ("K_Diagram-5_p5", {"image_sha256": h, "reviewed": False, "description": "x"}, other_png),
                      ("K_Diagram-6_p6", {"image_sha256": h, "reviewed": True, "description": "x"}, other_png),
                      ("K_Diagram-7_p7", {"image_sha256": h, "reviewed": True, "description": "x"}, TINY_PNG)]:
    if rec:
        describe.save({"key": key, **rec})
    states[key] = (describe.state(describe.load(key), png), png)
figs = [{"key": k, "kind": "diagram", "source": "ADK", "label": "Diagram " + k.split("-")[1].split("_")[0],
         "page": 1, "png": png}
        for k, (_st, png) in states.items()]
pend = describe.pending(figs)
md = describe.pending_markdown(pend, len(figs))
check([s for s, _ in states.values()] == ["missing", "draft", "redraft", "changed", "reviewed"]
      and [p["state"] for p in pend] == ["missing", "draft", "redraft", "changed"]
      and "4 of the 5 diagrams and tables" in md and "**ADK**: Diagram 6 (p 1)" in md,
      "the drawings needing a model or a person are listed for the issue, reviewed ones left out")

ramp = {"source": "ADM2", "caption": "Diagram 3 Ramp gradient", "drawing_text": "", "png": TINY_PNG}
fake_client = Client(fake_url, "", "fake-model")
check(describe.correct_description(fake_client, ramp, "The second point is at 4m.") == "The second point is at 5m."
      and describe.correct_description(fake_client, ramp, "At 5m.") == "At 5m.",
      "a second reading puts right what the image doesn't bear out, and leaves the rest")
describe.save({"key": "K_Diagram-8_p8", "image_sha256": h, "reviewed": False, "description": "At 4m."})
first = describe.needs_second_reading(describe.load("K_Diagram-8_p8"), TINY_PNG)
describe.save({**describe.load("K_Diagram-8_p8"), "first_draft": "At 4m.", "description": "At 5m."})
after = describe.needs_second_reading(describe.load("K_Diagram-8_p8"), TINY_PNG)
end = describe.deadline("05:30")
check(end.strftime("%H:%M") == "05:30" and datetime.timedelta(0) < end - datetime.datetime.now()
      <= datetime.timedelta(days=1) and describe.out_of_time(datetime.datetime.now() + datetime.timedelta(minutes=5))
      and not describe.out_of_time(datetime.datetime.now() + datetime.timedelta(hours=1))
      and not describe.out_of_time(None), "a night's run stops in time to finish by the hour given")

lib = os.path.join(TMP, "describe-lib.sqlite")
db = sqlite3.connect(lib)
db.executescript("CREATE TABLE sources (id INTEGER PRIMARY KEY, short TEXT);"
                 "CREATE TABLE passages (id INTEGER PRIMARY KEY, source_id INT, text TEXT);"
                 "CREATE TABLE figures (id INTEGER PRIMARY KEY, passage_id INT, key TEXT, label TEXT, page INT, png BLOB);"
                 "INSERT INTO sources VALUES (1, 'ADM2');"
                 "INSERT INTO passages VALUES (1, 1, 'Diagram 3 Ramp gradient'), (2, 1, 'Diagram 4 Steps');")
db.executemany("INSERT INTO figures (passage_id, key, label, page, png) VALUES (?, ?, ?, 2, ?)",
               [(1, "ADM2_Diagram-3_p2", "Diagram 3", grey_png(4, 4)), (2, "ADM2_Diagram-4_p2", "Diagram 4", grey_png(5, 4))])
db.commit()
db.close()
describe.save({"key": "ADM2_Diagram-4_p2", "image_sha256": describe.image_hash(grey_png(5, 4)), "reviewed": True,
               "description": "Steps."})
describe.main(["--library", lib, "run", "--base-url", fake_url, "--model", "fake-model"])
drafted, kept = describe.load("ADM2_Diagram-3_p2"), describe.load("ADM2_Diagram-4_p2")
check(drafted["description"].startswith("A drawing") and not drafted["reviewed"] and "first_draft" in drafted
      and kept["description"] == "Steps." and "first_draft" not in kept,
      "an overnight run drafts what's missing and reads each draft again, and leaves approved descriptions alone")

db = sqlite3.connect(lib)
db.execute("INSERT INTO passages VALUES (3, 1, 'Table 1 Ramps | 1:12 | 1:15'), (4, 1, 'Table 2 Uses | \uf0fc | \uf0fc')")
db.executemany("INSERT INTO figures (passage_id, key, label, page, png) VALUES (?, ?, ?, 2, ?)",
               [(3, "ADM2_Table-1_p2", "Table 1", grey_png(6, 4)), (4, "ADM2_Table-2_p2", "Table 2", grey_png(7, 4))])
db.commit()
db.close()
describe.main(["--library", lib, "run", "--base-url", fake_url, "--model", "fake-model"])
plain, ticks = describe.load("ADM2_Table-1_p2"), describe.load("ADM2_Table-2_p2")
figs_now = {f["key"]: f for f in describe.figures_in(lib)}
check(plain["not_needed"] and "description" not in plain and ticks["description"].startswith("A drawing")
      and describe.state(plain, figs_now["ADM2_Table-1_p2"]["png"], table=True) == "not needed"
      and describe.state(None, b"", table=True) == "unsorted"
      and [i["key"] for i in describe.review_items(figs_now) if i["key"].startswith("ADM2_Table")] == ["ADM2_Table-2_p2"],
      "tables whose text says it all are set aside; a table of ticks is described and reviewed like a drawing")

db = sqlite3.connect(lib)
db.execute("INSERT INTO passages VALUES (5, 1, 'Diagram 5 Handrail')")
db.execute("INSERT INTO figures (passage_id, key, label, page, png) VALUES (5, 'ADM2_Diagram-5_p2', 'Diagram 5', 2, ?)",
           (grey_png(8, 4),))
db.commit()
db.close()
cut = describe.figures_in(lib, "ADM2_Diagram-5")[0]
describe.mark_crop(cut, True)
describe.main(["--library", lib, "run", "--base-url", fake_url, "--model", "fake-model"])
recut = {**cut, "png": grey_png(8, 12)}
crops_now = {c["key"]: c for c in describe.crop_items({cut["key"]: cut})}
check(describe.load("ADM2_Diagram-5_p2") is None and crops_now["ADM2_Diagram-5_p2"]["bad"]
      and [p["state"] for p in describe.pending([cut])] == ["bad crop"]
      and describe.crop_items({cut["key"]: recut})[0]["recut"] and not describe.bad_crop(describe.crop_marks(), recut),
      "a crop marked bad isn't described until it's recut, and then shows for a person to look at again")
describe.mark_crop(cut, False)
check("ADM2_Diagram-5_p2" not in describe.crop_marks(), "a crop mark can be taken back")

check(first and not after and not describe.load("K_Diagram-8_p8")["reviewed"],
      "a draft is read again once, and the second reading never approves it")

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

# figures: a caption's title in a block of its own beside the label keeps all its lines (ADB1 Diagram 3.5)
cap_label = L(65, 388, 120, 402, "Diagram 3.5", 20)
cap_title = [L(132, 388, 491, 402, "Multi-storey flat with alternative exits from each habitable room, except at", 23),
             L(133, 402, 199, 416, "entrance level", 23)]
far = L(62, 462, 103, 472, "See para 3.21", 23)
check(figures._caption_lines(cap_label, [cap_label, *cap_title, far]) == [cap_label, *cap_title],
      "a caption's title set beside its label keeps its second line, and nothing further down")

# figures: a heading at the margin ends a figure; one set in from it is inside the drawing (Manual Figure 7.1)
page_lines = [L(71, 100 + 14 * i, 520, 114 + 14 * i, "the guidance then explains one or more ways to show", 1)
              for i in range(10)]
inner, outer = L(97, 366, 451, 389, "Requirement K4: Protection against impact", 2), L(71, 700, 300, 714,
                                                                                     "Section 2 Stairs", 3)
_, starts = figures._bounds(page_lines + [inner, outer], 14, 60, 780)
check(outer in starts and inner not in starts, "a heading set in from the margin doesn't cut a figure short")
check(figures._is_caption(L(65, 58, 400, 72, "Diagram 1\u2002\x07 Size and proportion of buildings", 4))
      and not figures._is_caption(L(182, 108, 260, 120, "Figure 2 Diagram 6 and", 5)),
      "a caption is a caption at the top of a page; a list of references in a table cell isn't")
row = [L(60, 358, 75, 370, "B3", 20), L(164, 358, 197, 370, "Table B2", 21),
       L(230, 358, 400, 370, "Minimum periods of fire resistance", 22)]
bare = L(228, 125, 254, 133, "Table 11", 3)
said = [L(37, 365, 262, 377, "G10 Corrosion resistance classes are set out in", 32),
        L(37, 377, 262, 389, "Table G3 – this is fuel dependant and expressed", 32)]
titled = [L(65, 388, 120, 402, "Diagram 3.5", 20), L(132, 388, 491, 402, "Multi-storey flat with alternative exits", 23)]
check(not figures._is_caption(row[1], row) and not figures._is_caption(bare, [bare])
      and not figures._is_caption(said[1], said) and figures._is_caption(titled[0], titled)
      and not figures._is_caption(L(291, 596, 352, 604, "Table 11 Column (B)", 8)),
      "a mention of a figure in a table row, a drawing or a sentence isn't its caption")
right_cap = [L(332, 555, 481, 569, "Diagram 2.17 Wall type 2.3", 30)]
check(figures._column(right_cap, right_cap, 595, 842, 12) is not None,
      "a figure captioned past the middle of the page is set in the right column")

# figures: a crop is narrowed to the drawing's ink, so a paragraph swallowed below the drawing is left out
page = bytearray(b"\xff" * 200 * 300)
for y in list(range(20, 120)) + list(range(200, 212)):  # a drawing, then a line of running text
    page[y * 200 + 30:y * 200 + 170] = b"\x00" * 140
para = L(30, 200, 170, 212, "The next paragraph, which doesn't start with a paragraph number", 9)
caption = L(30, 8, 120, 18, "Diagram 1 A drawing", 1)
tight = figures.ink_box((200, 300, page), (10, 5, 190, 290), [para], [caption])
check(tight is not None and 115 <= tight[3] <= 130 and tight[1] <= 8,
      "a figure's crop stops where its drawing does, not at the next numbered paragraph")

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
        f.write("#!/bin/sh\nsleep 60\n")  # not exec: its command line must still say llama-server
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
        f.write("#!/bin/sh\nsleep 60\n")  # not exec: its command line must still say llama-server
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

# Approved Documents: which requirement a passage is under, from the document's own "deals with the following
# requirement" openings, also when one is run into the end of the section before
reqs = Store(os.path.join(TMP, "requirements.sqlite"), "r")
reqs.add_source("ADZ", "Approved Document Z", "ADZ", "approved-document", split([
    "APPROVED DOCUMENT Z\nRequirement Z1: Walls\nThis approved document deals with the following requirement from "
    "Part Z of Schedule 1 to the Building Regulations 2010.\nSection 1: Walls\n1.1 Walls should stand up.\n"
    "1.2 Walls should keep the wind out.\n3",
    "APPROVED DOCUMENT Z\nRequirement Z2: Roofs\nThis approved document deals with the following requirement from "
    "Part Z of Schedule 1 to the Building Regulations 2010.\nSection 2: Roofs\n2.1 Roofs should keep the rain out."
    "\n4"]))
walls = reqs.read(f"r{reqs.passage_rowid('ADZ', 1)}")
check(walls["requirement"] == "Requirement Z1: Walls" and "1.1 Walls" in walls["text"],
      "a read says which requirement its passage is under")
check("2.1 Roofs" not in walls["text"] and walls["next_id"] == f"r{reqs.passage_rowid('ADZ', 2)}",
      "a read stops where the next requirement starts")
check(reqs.read(f"r{reqs.passage_rowid('ADZ', 2)}")["requirement"] == "Requirement Z2: Roofs",
      "a requirement run into the section before starts at its own section")

print(f"\n{len(failures)} failed" if failures else "\nall passed")
sys.exit(1 if failures else 0)
