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
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import uuid
import webbrowser

from . import VERSION, paths, updater
from .core import BASE_PROMPT, chat_events
from .engine import Engine, Job, catalog, recommend, _download
from .library import Library, Scope
from .llm import HOSTED, Client, EngineError, is_local, probe_known, same_host
from .myfiles import TYPES, MyFiles

UPDATE_REPO = os.environ.get("PARA_UPDATE_REPO", "valtomas/para-building-regs")
FEEDBACK = "mailto:hello@pararegs.co.uk?subject=Para%20feedback"  # the only mailto /api/open will open

settings = paths.Settings()
library = Library()
myfiles = MyFiles(library)
engine = Engine()
stops = {}  # chat id -> Event
update_info = {}
library_job = {}
app_job = {}


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


def save_chat(chat, touch=True):
    if touch:
        chat["updated"] = time.time()
    tmp = os.path.join(chats_dir(), chat["id"] + ".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(chat, f)
    os.replace(tmp, os.path.join(chats_dir(), chat["id"] + ".json"))


def list_chats_all():
    for name in os.listdir(chats_dir()):
        if name.endswith(".json"):
            c = load_chat(name[:-5])
            if c:
                yield c


def list_chats():
    """The latest 200 chats of each project (and of chats in none), newest first."""
    out, per_project = [], {}
    for c in sorted(list_chats_all(), key=lambda c: -c.get("updated", 0)):
        project = c.get("project", "")
        per_project[project] = per_project.get(project, 0) + 1
        if per_project[project] <= 200:
            out.append({"id": c["id"], "title": c.get("title", ""), "updated": c.get("updated", 0),
                        "project": project})
    return out


def move_chats(old, new):
    """Point a project's chats at its new name ("" when it's deleted: they become ordinary chats)."""
    for c in list_chats_all():
        if c.get("project") == old:
            c["project"] = new
            save_chat(c, touch=False)


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
                       "notes": (rel.get("body") or "")[:2000], "update": updater.offer(assets)}
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


def install_app_update():
    """Downloads the new version, checks it against GitHub's checksum, puts it in place and, where the new copy
    starts by itself, quits so it can."""
    app = update_info.get("app") or {}
    update = app.get("update")
    if not update:
        raise EngineError("This copy of Para can't update itself: download the new version instead.")
    if app_job.get("job") and app_job["job"].state == "running":
        return app_job["job"].view()
    job = Job("app", f"Para {app['version']}")
    app_job["job"] = job

    def run():
        try:
            dest = updater.download_path(update)
            _download(update["url"], dest, job, expect_size=update.get("size"), sha256=update.get("sha256"))
            if update["kind"] == "deb":
                job.note = "Enter your password in the window that has opened to install the update."
            job.note, quit_now = updater.install(update, dest)
            job.state = "done"
            if quit_now:
                time.sleep(2)  # time for the page to show what's happening
                quit_app()
        except Exception as e:  # noqa: BLE001 - a background thread: report it on the page
            job.state, job.error = "error", str(e)

    threading.Thread(target=run, daemon=True).start()
    return job.view()


def quit_app():
    """Closes the window, which ends the app as if the person had closed it (stopping the built-in engine); in the
    browser there's no window, so the engine is stopped here and the process ends."""
    engine.bundled.stop()
    try:
        import webview
        for w in list(webview.windows):
            w.destroy()
    except Exception:  # noqa: BLE001 - no window toolkit: nothing to close
        pass
    threading.Timer(5, os._exit, [0]).start()  # if closing the window didn't end it


def saved_key_for(base_url):
    """The saved API key, but only for the server it was saved for: switching from one hosted service to another
    must never send the first one's key to the second."""
    e = settings.load()["engine"]
    return e.get("api_key", "") if same_host(base_url, e.get("base_url", "")) else ""


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
            "files": myfiles.list(), "files_dir": myfiles.folder, "projects": myfiles.projects(), "file_types": sorted(TYPES),
            "bundled": engine.bundled.status(), "catalog": catalog()["models"],
            "recommended": (recommend() or {}).get("id"), "hosted": HOSTED, "updates": update_info,
            "library_job": library_job["job"].view() if library_job.get("job") else None,
            "app_job": app_job["job"].view() if app_job.get("job") else None,
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
            if path == "/api/figure":
                png = library.figure(q.get("id", ""))
                if not png:
                    return self._send(404, {"error": "No such figure"})
                return self._send(200, png, "image/png")
            if path == "/api/search":
                keys = myfiles.keys(q["project"]) if "project" in q else None
                return self._send(200, {"results": library.search(q.get("q", ""), limit=30, file_keys=keys)})
            if path == "/api/engines":
                return self._send(200, {"found": probe_known()})
            if path == "/api/jobs":
                return self._send(200, {"bundled": engine.bundled.status(),
                                        "library_job": library_job["job"].view() if library_job.get("job") else None,
                                        "app_job": app_job["job"].view() if app_job.get("job") else None})
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
        if url.path == "/api/projects":
            try:
                myfiles.delete_project(q.get("name", ""))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            move_chats(q.get("name", ""), "")
            return self._send(200, state())
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
            if path == "/api/models":
                # POST so an API key typed in Settings never sits in a URL
                body = self._json_body()
                url = body.get("base_url", "")
                c = Client(url, body.get("api_key") or saved_key_for(url))
                return self._send(200, {"models": c.models(timeout=8)})
            if path == "/api/settings":
                body = self._json_body()
                allowed = {k: v for k, v in body.items() if k in paths.DEFAULTS}
                if "engine" in allowed:
                    allowed["engine"] = {k: v for k, v in allowed["engine"].items() if k in paths.DEFAULTS["engine"]}
                    if "base_url" in allowed["engine"] and "api_key" not in allowed["engine"]:
                        allowed["engine"]["api_key"] = saved_key_for(allowed["engine"]["base_url"])
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
                project = urllib.parse.unquote(self.headers.get("X-Project", ""))
                try:
                    if project and not myfiles.project(project):
                        raise ValueError("That project doesn't exist any more.")
                    myfiles.add(name, data, project)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                return self._send(200, {"files": myfiles.list()})
            if path == "/api/files/note":
                body = self._json_body()
                myfiles.set_note(body.get("key", ""), str(body.get("note", "")))
                return self._send(200, {"files": myfiles.list()})
            if path == "/api/projects":
                body = self._json_body()
                try:
                    name = myfiles.create_project(body.get("name", ""), body.get("notes", ""))
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                return self._send(200, {**state(), "project": name})
            if path == "/api/projects/update":
                body = self._json_body()
                name = body.get("name", "")
                if not myfiles.project(name):
                    return self._send(404, {"error": "No such project"})
                try:
                    if "new_name" in body:
                        new = myfiles.rename_project(name, body["new_name"])
                        if new != name:
                            move_chats(name, new)
                            name = new
                except (ValueError, OSError) as e:  # OSError: the folder is open elsewhere (Windows)
                    return self._send(400, {"error": str(e)})
                if "notes" in body:
                    myfiles.set_project_notes(name, body["notes"])
                return self._send(200, {**state(), "project": name})
            if path == "/api/files/sync":
                myfiles.sync()
                return self._send(200, {"files": myfiles.list()})
            if path == "/api/open":
                body = self._json_body()
                if body.get("url", "").startswith(("https://", "http://")) or body.get("url") == FEEDBACK:
                    webbrowser.open(body["url"])
                elif body.get("folder") == "files":
                    project = body.get("project") or ""
                    open_path(myfiles.project_folder(project) if myfiles.project(project) else myfiles.folder)
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
            if path == "/api/updates/app":
                return self._send(200, install_app_update())
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
        chat = load_chat(body.get("id"))
        if not chat:
            project = body.get("project") or ""
            chat = {"id": str(uuid.uuid4()), "title": question[:80], "messages": [], "turns": [],
                    "created": time.time(), "project": project if myfiles.project(project) else ""}
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
            # A project's chat searches its files and the shared ones; any other chat only the shared ones.
            project = chat.get("project", "")
            s["project"] = myfiles.project(project) if project else None
            scope = Scope(library, lambda: myfiles.keys(project if s["project"] else ""))
            if s["engine"].get("mode") == "bundled" and not engine.bundled.status()["running"]:
                send({"type": "status", "text": "Starting the AI engine…"})
            client = engine.client(s)
            send({"type": "status", "text": "", "model": client.model})
            answer = []
            for ev in chat_events(client, scope, chat["messages"], question, s):
                engine.bundled.touch()  # a long answer isn't idle time
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


def _gui_choice():
    """On Linux, use Qt for the window: the packaged app bundles it, while GTK/WebKit differ between distributions."""
    if sys.platform.startswith("linux"):
        import importlib.util
        if importlib.util.find_spec("qtpy") is None:  # the Qt backend isn't installed
            return {}
        from .qtprobe import qt_works
        if not qt_works():
            raise RuntimeError("Qt can't open a window on this system")
        return {"gui": "qt"}
    return {}


def main(argv=None):
    ap = argparse.ArgumentParser(prog=paths.APP_SLUG)
    ap.add_argument("--browser", action="store_true", help="open in the web browser instead of a window")
    ap.add_argument("--host", default="127.0.0.1", help="address to serve on (0.0.0.0 to share on the network)")
    ap.add_argument("--port", type=int, default=0)
    args = ap.parse_args(argv)
    # Being killed (a terminal closed, a service stopped) exits like closing the window, so the built-in engine
    # is stopped and doesn't keep its model in memory with nobody to use it.
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: sys.exit(0))
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
            try:
                webview.start(private_mode=False, storage_path=paths.data_path("webview", "x")[:-2],
                              **_gui_choice())
            except Exception as e:  # noqa: BLE001 - no usable window toolkit (e.g. Linux without WebEngine libraries)
                print(f"Can't open a window ({e.__class__.__name__}: {e}); opening the browser instead.", flush=True)
                window = None
        if window is None:
            webbrowser.open(url)
            while True:
                time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        engine.bundled.stop()
        httpd.shutdown()
