"""The app: a small web server on 127.0.0.1 and a window showing it (pywebview), or the browser as a fallback.

python -m para                      the app in its own window
python -m para --browser            in the web browser instead
python -m para --host 0.0.0.0       serve it to other computers on the network (no window)
"""
import argparse
import gzip
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser

from . import VERSION, paths
from .core import BASE_PROMPT, chat_events
from .engine import Engine, Job, catalog, recommend, _download
from .library import Library
from .llm import Client, EngineError, is_local, probe_known
from .myfiles import TYPES, MyFiles

UPDATE_REPO = os.environ.get("PARA_UPDATE_REPO", "valtomas/para-building-regs")

settings = paths.Settings()
library = Library()
myfiles = MyFiles(library)
engine = Engine()
stops = {}  # chat id -> Event
update_info = {}
library_job = {}


# ----- chats -----
def chats_dir():
    d = os.path.join(paths.app_dir(), "chats")
    os.makedirs(d, exist_ok=True)
    return d


def load_chat(cid):
    if not cid or not all(c.isalnum() or c == "-" for c in cid):
        return None
    try:
        with open(os.path.join(chats_dir(), cid + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_chat(chat):
    chat["updated"] = time.time()
    tmp = os.path.join(chats_dir(), chat["id"] + ".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(chat, f)
    os.replace(tmp, os.path.join(chats_dir(), chat["id"] + ".json"))


def list_chats():
    out = []
    for name in os.listdir(chats_dir()):
        if name.endswith(".json"):
            c = load_chat(name[:-5])
            if c:
                out.append({"id": c["id"], "title": c.get("title", ""), "updated": c.get("updated", 0)})
    return sorted(out, key=lambda c: -c["updated"])[:200]


# ----- updates -----
def _gh(path):
    req = urllib.request.Request(f"https://api.github.com/repos/{UPDATE_REPO}/{path}",
                                 headers={"User-Agent": paths.APP_SLUG, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)


def _newer(a, b):
    def parts(v):
        return [int(x) if x.isdigit() else 0 for x in str(v).lstrip("v").split(".")]
    return parts(a) > parts(b)


def check_updates():
    """Looks at the project's latest GitHub release: a newer app, a newer library, a newer model list."""
    try:
        rel = _gh("releases/latest")
    except Exception as e:  # noqa: BLE001
        update_info.update(error=str(e), checked=time.time())
        return update_info
    assets = {a["name"]: a for a in rel.get("assets", [])}
    info = {"checked": time.time(), "error": "", "app": None, "library": None}
    if _newer(rel.get("tag_name", "0"), VERSION):
        info["app"] = {"version": rel["tag_name"].lstrip("v"), "url": rel.get("html_url"),
                       "notes": (rel.get("body") or "")[:2000]}
    if "library.json" in assets:
        try:
            req = urllib.request.Request(assets["library.json"]["browser_download_url"],
                                         headers={"User-Agent": paths.APP_SLUG})
            with urllib.request.urlopen(req, timeout=20) as r:
                lib = json.load(r)
            current = library.info()["version"]
            if lib.get("version") and lib["version"] > (current or "") and lib.get("file") in assets:
                a = assets[lib["file"]]
                info["library"] = {**lib, "url": a["browser_download_url"], "size": a["size"]}
        except Exception:  # noqa: BLE001
            pass
    if "models.json" in assets:
        try:
            req = urllib.request.Request(assets["models.json"]["browser_download_url"],
                                         headers={"User-Agent": paths.APP_SLUG})
            with urllib.request.urlopen(req, timeout=20) as r:
                data = json.load(r)
            if data.get("models"):
                with open(os.path.join(paths.app_dir(), "models.json"), "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=1)
        except Exception:  # noqa: BLE001
            pass
    update_info.clear()
    update_info.update(info)
    return info


def install_library_update():
    lib = update_info.get("library")
    if not lib:
        raise EngineError("No library update to install")
    job = Job("library", f"Regulations library {lib['version']}")
    library_job["job"] = job

    def run():
        try:
            tmp = os.path.join(paths.app_dir(), "library", "download.gz")
            os.makedirs(os.path.dirname(tmp), exist_ok=True)
            _download(lib["url"], tmp, job, expect_size=lib.get("size"))
            out = tmp[:-3] + ".sqlite"
            h = hashlib.sha256()
            with gzip.open(tmp, "rb") as src, open(out, "wb") as dst:
                for block in iter(lambda: src.read(1 << 20), b""):
                    h.update(block)
                    dst.write(block)
            os.remove(tmp)
            if lib.get("sha256") and h.hexdigest() != lib["sha256"]:
                os.remove(out)
                raise EngineError("The library download didn't match its checksum.")
            library.install_regs(out)
            update_info["library"] = None
            job.state = "done"
        except Exception as e:  # noqa: BLE001
            job.state, job.error = "error", str(e)

    threading.Thread(target=run, daemon=True).start()
    return job.view()


# ----- state for the page -----
def public_settings():
    s = settings.load()
    e = dict(s["engine"])
    e["api_key_set"] = bool(e.pop("api_key", ""))
    s["engine"] = e
    s["private"] = e.get("mode") == "bundled" or (e.get("mode") == "external" and is_local(e.get("base_url", "")))
    return s


def state():
    info = library.info()
    return {"version": VERSION, "settings": public_settings(), "default_instructions": BASE_PROMPT,
            "library": {k: info[k] for k in ("version", "built", "sources")},
            "files": myfiles.list(), "files_dir": myfiles.folder, "file_types": sorted(TYPES),
            "bundled": engine.bundled.status(), "catalog": catalog()["models"],
            "recommended": (recommend() or {}).get("id"), "updates": update_info,
            "library_job": library_job["job"].view() if library_job.get("job") else None,
            "chats": list_chats(), "platform": sys.platform}


def open_path(path):
    if sys.platform == "darwin":
        subprocess.Popen(["open", path])
    elif os.name == "nt":
        os.startfile(path)  # noqa: S606
    else:
        subprocess.Popen(["xdg-open", path])


# ----- HTTP -----
class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "Para"

    def log_message(self, fmt, *args):  # quiet
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json_body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if not n:
            return {}
        try:
            return json.loads(self.rfile.read(n))
        except ValueError:
            return {}

    def _allowed(self):
        """Refuse requests from other web pages (a page in the person's browser posting to localhost)."""
        origin = self.headers.get("Origin")
        if not origin or origin == "null":
            return True
        host = urllib.parse.urlparse(origin).netloc
        return host == self.headers.get("Host")

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        path = url.path
        try:
            if path == "/" or path.startswith("/static/"):
                return self._static("index.html" if path == "/" else path[len("/static/"):])
            if path == "/api/state":
                return self._send(200, state())
            if path == "/api/chat":
                chat = load_chat(q.get("id"))
                return self._send(200 if chat else 404, chat or {"error": "No such chat"})
            if path == "/api/passage":
                doc = library.document(q.get("id", ""))
                return self._send(200 if doc else 404, doc or {"error": "No such passage"})
            if path == "/api/source":
                pid = library.first_passage(q.get("key", ""))
                doc = library.document(pid) if pid else None
                return self._send(200 if doc else 404, doc or {"error": "No such document"})
            if path == "/api/search":
                return self._send(200, {"results": library.search(q.get("q", ""), limit=30)})
            if path == "/api/engines":
                return self._send(200, {"found": probe_known()})
            if path == "/api/models":
                c = Client(q.get("base_url", ""), q.get("api_key") or settings.load()["engine"].get("api_key", ""))
                return self._send(200, {"models": c.models(timeout=8)})
            if path == "/api/jobs":
                return self._send(200, {"bundled": engine.bundled.status(),
                                        "library_job": library_job["job"].view() if library_job.get("job") else None})
            return self._send(404, {"error": "Not found"})
        except EngineError as e:
            return self._send(502, {"error": str(e)})

    def _static(self, name):
        root = os.path.realpath(paths.STATIC_DIR)
        full = os.path.realpath(os.path.join(root, name))
        if not full.startswith(root + os.sep) or not os.path.isfile(full):
            return self._send(404, {"error": "Not found"})
        ctype = {".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript",
                 ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon"}.get(
            os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as f:
            self._send(200, f.read(), ctype)

    def do_DELETE(self):
        if not self._allowed():
            return self._send(403, {"error": "Forbidden"})
        url = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(url.query))
        if url.path == "/api/chat":
            cid = q.get("id", "")
            if load_chat(cid):
                os.remove(os.path.join(chats_dir(), cid + ".json"))
            return self._send(200, {"ok": True})
        if url.path == "/api/files":
            myfiles.remove(q.get("key", ""))
            return self._send(200, {"files": myfiles.list()})
        return self._send(404, {"error": "Not found"})

    def do_POST(self):
        if not self._allowed():
            return self._send(403, {"error": "Forbidden"})
        path = urllib.parse.urlparse(self.path).path
        try:
            if path == "/api/ask":
                return self._ask(self._json_body())
            if path == "/api/stop":
                ev = stops.get(self._json_body().get("id"))
                if ev:
                    ev.set()
                return self._send(200, {"ok": True})
            if path == "/api/settings":
                body = self._json_body()
                allowed = {k: v for k, v in body.items() if k in paths.DEFAULTS}
                if "engine" in allowed:
                    allowed["engine"] = {k: v for k, v in allowed["engine"].items() if k in paths.DEFAULTS["engine"]}
                    if allowed["engine"].get("mode") != "bundled":
                        engine.bundled.stop()
                settings.update(allowed)
                return self._send(200, state())
            if path == "/api/files":
                name = urllib.parse.unquote(self.headers.get("X-Filename", "file"))
                n = int(self.headers.get("Content-Length") or 0)
                if n > 300 * 2**20:
                    return self._send(413, {"error": "That file is over 300 MB."})
                data = self.rfile.read(n)
                try:
                    myfiles.add(name, data)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                return self._send(200, {"files": myfiles.list()})
            if path == "/api/files/note":
                body = self._json_body()
                myfiles.set_note(body.get("key", ""), str(body.get("note", "")))
                return self._send(200, {"files": myfiles.list()})
            if path == "/api/files/sync":
                myfiles.sync()
                return self._send(200, {"files": myfiles.list()})
            if path == "/api/open":
                body = self._json_body()
                if body.get("url", "").startswith(("https://", "http://")):
                    webbrowser.open(body["url"])
                elif body.get("folder") == "files":
                    open_path(myfiles.folder)
                elif body.get("file"):
                    key = os.path.abspath(body["file"])
                    if key in {s["key"] for s in library.files.sources()} and os.path.exists(key):
                        open_path(key)
                return self._send(200, {"ok": True})
            if path == "/api/bundled/install":
                body = self._json_body()
                return self._send(200, engine.bundled.install(body.get("model"), gpu=body.get("gpu", True)))
            if path == "/api/bundled/cancel":
                engine.bundled.cancel()
                return self._send(200, {"ok": True})
            if path == "/api/bundled/remove":
                engine.bundled.remove_model(self._json_body().get("model"))
                return self._send(200, engine.bundled.status())
            if path == "/api/test-engine":
                return self._test_engine()
            if path == "/api/updates/check":
                return self._send(200, check_updates())
            if path == "/api/updates/library":
                return self._send(200, install_library_update())
            return self._send(404, {"error": "Not found"})
        except EngineError as e:
            return self._send(502, {"error": str(e)})

    def _test_engine(self):
        s = settings.load()
        try:
            client = engine.client(s)
            out = []
            for ev in client.stream([{"role": "user", "content": "Reply with just: OK"}], max_tokens=200,
                                    reasoning="off"):
                if ev["type"] == "content":
                    out.append(ev["text"])
            return self._send(200, {"ok": True, "model": client.model, "reply": "".join(out).strip()[:200]})
        except EngineError as e:
            return self._send(200, {"ok": False, "error": str(e)})

    def _ask(self, body):
        question = str(body.get("question", "")).strip()
        if not question:
            return self._send(400, {"error": "Empty question"})
        chat = load_chat(body.get("id")) or {"id": str(uuid.uuid4()), "title": question[:80], "messages": [],
                                              "turns": [], "created": time.time()}
        stop = stops[chat["id"]] = threading.Event()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        def send(ev):
            self.wfile.write(b"data: " + json.dumps(ev).encode() + b"\n\n")
            self.wfile.flush()

        turn = {"question": question, "answer": "", "events": [], "time": time.time()}
        try:
            send({"type": "chat", "id": chat["id"]})
            s = settings.load()
            if s["engine"].get("mode") == "bundled" and not engine.bundled.status()["running"]:
                send({"type": "status", "text": "Starting the AI engine…"})
            client = engine.client(s)
            send({"type": "status", "text": "", "model": client.model})
            answer = []
            for ev in chat_events(client, library, chat["messages"], question, s):
                if stop.is_set():
                    turn["stopped"] = True
                    break
                if ev["type"] == "done":
                    chat["messages"] = ev.pop("messages")
                    turn.update(answer=ev["answer"], cites=ev["cites"], read=ev["read"], seen=ev["seen"],
                                cut_off=ev["cut_off"], dropped=ev["dropped"])
                elif ev["type"] in ("search", "read", "show", "notice"):
                    turn["events"].append(ev)
                elif ev["type"] == "token":
                    answer.append(ev["text"])
                elif ev["type"] == "retry":
                    answer = []
                if ev["type"] != "tick":
                    send(ev)
            if turn.get("stopped"):
                turn["answer"] = "".join(answer)
                chat["messages"] = chat["messages"] + [{"role": "user", "content": question},
                                                       {"role": "assistant", "content": turn["answer"]}]
                send({"type": "stopped", "answer": turn["answer"]})
        except EngineError as e:
            turn["error"] = str(e)
            try:
                send({"type": "error", "text": str(e)})
            except OSError:
                pass
        except (BrokenPipeError, ConnectionResetError):
            turn["stopped"] = True
        except Exception as e:  # noqa: BLE001
            turn["error"] = f"{e.__class__.__name__}: {e}"
            try:
                send({"type": "error", "text": turn["error"]})
            except OSError:
                pass
        finally:
            stops.pop(chat["id"], None)
            if turn.get("answer") or turn.get("error") or turn.get("stopped"):
                chat["turns"].append(turn)
                save_chat(chat)


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def free_port(host):
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def background():
    try:
        myfiles.sync()
    except Exception:  # noqa: BLE001
        pass
    if settings.get("check_updates", True):
        threading.Thread(target=check_updates, daemon=True).start()
    while True:
        time.sleep(30)
        try:
            myfiles.sync()
        except Exception:  # noqa: BLE001
            pass


def main(argv=None):
    ap = argparse.ArgumentParser(prog=paths.APP_SLUG)
    ap.add_argument("--browser", action="store_true", help="open in the web browser instead of a window")
    ap.add_argument("--host", default="127.0.0.1", help="address to serve on (0.0.0.0 to share on the network)")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    port = args.port or free_port(args.host)
    httpd = Server((args.host, port), Handler)
    threading.Thread(target=background, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print(f"{paths.APP_NAME} {VERSION} on http://{args.host}:{port}/", flush=True)
    if args.host != "127.0.0.1":
        try:
            httpd.serve_forever()
        finally:
            engine.bundled.stop()
        return
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    window = None
    if not args.browser:
        try:
            import webview
            window = webview.create_window(paths.APP_NAME, url, width=1280, height=860, min_size=(720, 520),
                                           text_select=True)
        except Exception:  # noqa: BLE001 - no pywebview: use the browser
            window = None
    try:
        if window is not None:
            import webview
            webview.start(private_mode=False, storage_path=paths.data_path("webview", "x")[:-2])
        else:
            webbrowser.open(url)
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        engine.bundled.stop()
        httpd.shutdown()
