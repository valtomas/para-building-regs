"""The AI engine: either a server the person already runs (any OpenAI-compatible API), or one the app installs and
runs itself (llama.cpp's llama-server with a GGUF model from Hugging Face)."""
import atexit
import hashlib
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

from . import paths
from .llm import Client, EngineError

CATALOG_PATH = os.path.join(paths.CODE_DIR, "models.json")
USER_AGENT = f"{paths.APP_SLUG}/1"


def catalog():
    """models.json: the copy downloaded with an update check if there is one, else the shipped one."""
    for path in (os.path.join(paths.app_dir(), "models.json"), CATALOG_PATH):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("models"):
                return data
        except (OSError, ValueError):
            continue
    return {"models": [], "llama_cpp": {}}


def _windows_memory():
    import ctypes

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong)]
    stat = MEMORYSTATUSEX()
    stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
    return stat


def total_ram_gb():
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip()) / 2**30
        if os.name == "nt":
            return _windows_memory().ullTotalPhys / 2**30
        with open("/proc/meminfo") as f:
            return int(re.search(r"MemTotal:\s+(\d+)", f.read()).group(1)) / 2**20
    except Exception:  # noqa: BLE001
        return 0.0


def available_ram_gb():
    """Memory that can be used without pushing other programs out (0 when unknown). Integrated GPUs, such as Apple
    silicon and AMD's Strix Halo, take a model's memory from this too."""
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["vm_stat"], capture_output=True, text=True, timeout=5).stdout
            page = int(re.search(r"page size of (\d+)", out).group(1))
            pages = sum(int(re.search(rf"{k}:\s+(\d+)", out).group(1))
                        for k in ("Pages free", "Pages inactive", "Pages speculative", "Pages purgeable"))
            return pages * page / 2**30
        if os.name == "nt":
            return _windows_memory().ullAvailPhys / 2**30
        with open("/proc/meminfo") as f:
            return int(re.search(r"MemAvailable:\s+(\d+)", f.read()).group(1)) / 2**20
    except Exception:  # noqa: BLE001
        return 0.0


def free_vram_gb():
    """Free memory on graphics cards with their own memory, in GB: 0 when there are none, None when there may be
    one that can't be read (AMD and Intel cards on Windows). An integrated GPU's small reserved slice (Strix Halo:
    512 MB) doesn't count: such GPUs load models into system memory, which available_ram_gb covers."""
    total = 0.0
    try:
        if shutil.which("nvidia-smi"):
            out = subprocess.run(["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=0x08000000 if os.name == "nt" else 0).stdout
            total += sum(int(x) for x in re.findall(r"\d+", out)) / 1024
        if sys.platform.startswith("linux"):
            for card in os.listdir("/sys/class/drm"):
                base = f"/sys/class/drm/{card}/device/mem_info_vram_"
                if re.fullmatch(r"card\d+", card) and os.path.exists(base + "total"):
                    with open(base + "total") as f, open(base + "used") as g:
                        vram, used = int(f.read()), int(g.read())
                    if vram >= 2 * 2**30:  # a card's own memory, not an integrated GPU's reserved slice
                        total += (vram - used) / 2**30
            return total
        if sys.platform == "darwin":
            return 0.0  # Apple silicon shares system memory
        return total or None  # Windows: an AMD or Intel card's memory can't be read without extra libraries
    except Exception:  # noqa: BLE001
        return None


def memory_needed_gb(model):
    """About what a model takes once loaded: its file, plus the context and the engine's working memory."""
    return model.get("size_gb", 0) * 1.1 + 1.5


def _process_command(pid):
    """The command line of a running process, or "" when there is none (or it can't be read)."""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=10, creationflags=0x08000000).stdout
            return out if f'"{pid}"' in out else ""
        if os.path.exists(f"/proc/{pid}/cmdline"):
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                return f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        return subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True,
                              timeout=10).stdout
    except Exception:  # noqa: BLE001
        return ""


def recommend(models=None, ram=None):
    """The best catalogue model for this computer's memory (Macs share memory with the GPU: ~75% usable): the
    biggest that fits. A model with "suggest": false is listed but never the suggestion, and a "retired" one is no
    longer offered at all (it stays in the catalogue so a copy someone downloaded keeps working): a smaller one did
    better in our tests, so needing more memory doesn't make it the better choice."""
    models = models if models is not None else catalog()["models"]
    ram = total_ram_gb() if ram is None else ram
    fits = [m for m in models if m.get("min_ram_gb", 0) <= ram + 0.5 and m.get("suggest", True)
            and not m.get("retired")]
    return max(fits, key=lambda m: m.get("min_ram_gb", 0)) if fits else None


def platform_key(gpu=True):
    machine = platform.machine().lower()
    if sys.platform == "darwin":
        return "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x86_64"
    if os.name == "nt":
        if machine in ("arm64", "aarch64"):
            return "windows-arm64"
        return "windows-gpu" if gpu else "windows-cpu"
    return "linux-gpu" if gpu else "linux-cpu"


def free_disk_gb(path):
    os.makedirs(path, exist_ok=True)
    return shutil.disk_usage(path).free / 2**30


class Job:
    """A download running in the background, with progress the page polls."""

    def __init__(self, kind, label):
        self.kind, self.label = kind, label
        self.done = self.total = 0
        self.state, self.error, self.note, self.started = "running", "", "", time.time()
        self.cancel = threading.Event()

    def view(self):
        return {"kind": self.kind, "label": self.label, "done": self.done, "total": self.total,
                "state": self.state, "error": self.error, "note": self.note}


def _fetch(url, part, have, job, expect_size):
    """Appends to part from byte have on (the whole file when the server ignores the range)."""
    headers = {"User-Agent": USER_AGENT}
    if have:
        headers["Range"] = f"bytes={have}-"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=60) as r:
        if have and r.status != 206:
            have = 0  # the server ignored the range: start again
        length = int(r.headers.get("Content-Length") or 0)
        job.total = (have + length) if length else (expect_size or 0)
        job.done = have
        with open(part, "ab" if have else "wb") as f:
            while True:
                if job.cancel.is_set():
                    raise EngineError("Cancelled")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                job.done += len(chunk)


def _download(url, dest, job, expect_size=None, sha256=None):
    """Download with resume to dest (via dest.part)."""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    if expect_size and have > expect_size:  # left from another version of the file: it can't be carried on
        os.remove(part)
        have = 0
    if not (expect_size and have == expect_size):  # else it's all here, and only the checks below are left
        try:
            _fetch(url, part, have, job, expect_size)
        except urllib.error.HTTPError as e:
            if e.code != 416 or not have:
                raise
            os.remove(part)  # the server has nothing after what's here: the part isn't of this file
            _fetch(url, part, 0, job, expect_size)
    if expect_size and os.path.getsize(part) != expect_size:
        got = os.path.getsize(part)
        if got > expect_size:  # more would never make it right; a short one is carried on next time
            os.remove(part)
        raise EngineError(f"The download is incomplete ({got} of {expect_size} bytes). Try again.")
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(1 << 22), b""):
                h.update(block)
        if h.hexdigest() != sha256.lower():
            os.remove(part)
            raise EngineError("The download didn't match its checksum, so it was deleted. Try again.")
    os.replace(part, dest)


LOG_KEEP = 1 << 20


def _trim_log(path, limit=5 << 20):
    """Keeps the end of a log that has grown past limit: llama-server writes a lot, and the file is only ever
    read for the last thing that went wrong."""
    try:
        if os.path.getsize(path) <= limit:
            return
        with open(path, "rb") as f:
            f.seek(-LOG_KEEP, os.SEEK_END)
            tail = f.read()
        with open(path, "wb") as f:
            f.write(tail[tail.find(b"\n") + 1:])
    except OSError:  # no log yet, or it can't be rewritten: carry on with it as it is
        pass


def _json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


IDLE_MINUTES = 10  # the built-in engine gives its memory back after this long without a question


class Bundled:
    """llama-server, installed and run by the app. It holds gigabytes of memory, so it never outlives the app: it is
    stopped when the app exits, a copy left behind by an app that was killed is stopped at the next start, and it
    is stopped after IDLE_MINUTES without use (the next question starts it again)."""

    def __init__(self):
        self.proc, self.port, self.model_id = None, None, None
        self.jobs, self.lock = {}, threading.Lock()
        self.log_path = paths.data_path("logs", "engine.log")
        self.pid_path = paths.data_path("engine.pid")
        self.last_used = time.time()
        self.watcher = None
        atexit.register(self.stop)
        self.stop_leftover()

    def stop_leftover(self):
        """Stop a llama-server that an earlier run of the app started and couldn't stop (it was killed, or the
        computer slept through it). Left running, it keeps its model in memory with nobody to use it."""
        try:
            with open(self.pid_path) as f:
                pid = int(f.read().split()[0])
        except (OSError, ValueError, IndexError):
            return
        command = _process_command(pid)
        if "llama-server" in command and (os.name == "nt" or self.engine_dir in command):
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=10,
                                   creationflags=0x08000000)
                else:
                    os.kill(pid, 15)  # SIGTERM
            except Exception:  # noqa: BLE001 - it may have just exited
                pass
        try:
            os.remove(self.pid_path)
        except OSError:
            pass

    def touch(self):
        """A question is using the engine: don't stop it for being idle."""
        self.last_used = time.time()

    def stop_if_idle(self, minutes=IDLE_MINUTES):
        """Stop the engine if nothing has used it for a while (not while it is being started)."""
        if self.proc is None or self.proc.poll() is not None or time.time() - self.last_used <= minutes * 60:
            return False
        if not self.lock.acquire(blocking=False):
            return False
        try:
            if time.time() - self.last_used > minutes * 60:
                self.stop()
                return True
            return False
        finally:
            self.lock.release()

    def _watch_idle(self):
        while True:
            time.sleep(30)
            self.stop_if_idle()

    # ----- what is installed -----
    @property
    def engine_dir(self):
        return os.path.join(paths.app_dir(), "engine")

    @property
    def models_dir(self):
        return os.path.join(paths.app_dir(), "models")

    def server_path(self):
        name = "llama-server.exe" if os.name == "nt" else "llama-server"
        for root, _dirs, files in os.walk(self.engine_dir):
            if name in files:
                return os.path.join(root, name)
        return None

    def model_path(self, model):
        return os.path.join(self.models_dir, model["file"])

    def installed_models(self):
        return [m["id"] for m in catalog()["models"] if os.path.exists(self.model_path(m))]

    def status(self):
        running = self.proc is not None and self.proc.poll() is None
        return {"engine_installed": bool(self.server_path()), "models": self.installed_models(),
                "running": running, "model": self.model_id if running else None,
                "jobs": {k: j.view() for k, j in self.jobs.items()},
                "ram_gb": round(total_ram_gb(), 1), "free_gb": round(free_disk_gb(paths.app_dir()), 1),
                "platform": platform_key()}

    # ----- installing -----
    def install(self, model_id, gpu=True):
        """Download llama-server (if needed) and the model, in the background."""
        model = next((m for m in catalog()["models"] if m["id"] == model_id), None)
        if not model:
            raise EngineError("Unknown model")
        if "install" in self.jobs and self.jobs["install"].state == "running":
            raise EngineError("A download is already running")
        job = Job("install", model["name"])
        self.jobs["install"] = job
        threading.Thread(target=self._install, args=(job, model, gpu), daemon=True).start()
        return job.view()

    def cancel(self):
        for j in self.jobs.values():
            j.cancel.set()

    def _install(self, job, model, gpu):
        try:
            if not self.server_path():
                job.label = "AI engine (llama.cpp)"
                self._install_server(job, gpu)
            path = self.model_path(model)
            if not os.path.exists(path):
                need = model.get("size_gb", 0) + 1
                if free_disk_gb(self.models_dir) < need:
                    raise EngineError(f"Not enough free disk space: {model['name']} needs about {need:.0f} GB.")
                job.label = model["name"]
                url = f"https://huggingface.co/{model['repo']}/resolve/main/{model['file']}?download=true"
                size, sha = _hf_file_info(model["repo"], model["file"])
                _download(url, path, job, expect_size=size, sha256=sha)
            job.state = "done"
        except Exception as e:  # noqa: BLE001
            job.state, job.error = "error", str(e) or e.__class__.__name__

    def _install_server(self, job, gpu):
        info = catalog().get("llama_cpp") or {}
        repo, tag = info.get("repo", "ggml-org/llama.cpp"), info.get("tag", "latest")
        pattern = (info.get("assets") or {}).get(platform_key(gpu))
        rel, asset = _find_release(repo, tag, pattern)
        if not asset:
            raise EngineError(f"No llama.cpp build for this computer ({platform_key(gpu)}) in release "
                              f"{rel.get('tag_name')}. Use an engine you install yourself instead.")
        os.makedirs(self.engine_dir, exist_ok=True)
        archive = os.path.join(self.engine_dir, asset["name"])
        digest = (asset.get("digest") or "").removeprefix("sha256:") or None
        _download(asset["browser_download_url"], archive, job, expect_size=asset.get("size"), sha256=digest)
        target = os.path.join(self.engine_dir, rel.get("tag_name", "llama.cpp"))
        if archive.endswith(".zip"):
            with zipfile.ZipFile(archive) as z:
                z.extractall(target)
        else:
            with tarfile.open(archive) as t:
                t.extractall(target, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(target)
        os.remove(archive)
        server = self.server_path()
        if not server:
            raise EngineError("The llama.cpp download had no llama-server in it.")
        if os.name != "nt":
            for root, _d, files in os.walk(target):
                for f in files:
                    p = os.path.join(root, f)
                    if f.startswith("llama-") or f.endswith((".dylib", ".so")) or ".so." in f:
                        os.chmod(p, 0o755)
            if sys.platform == "darwin":
                subprocess.run(["xattr", "-dr", "com.apple.quarantine", target], capture_output=True)

    def remove_model(self, model_id):
        model = next((m for m in catalog()["models"] if m["id"] == model_id), None)
        if model and self.model_id == model_id:
            self.stop()
        if model and os.path.exists(self.model_path(model)):
            os.remove(self.model_path(model))

    # ----- running -----
    def start(self, model_id, context=32768):
        with self.lock:
            self.touch()
            if self.proc and self.proc.poll() is None and self.model_id == model_id:
                return self.base_url
            self.stop()
            model = next((m for m in catalog()["models"] if m["id"] == model_id), None)
            server = self.server_path()
            if not model or not server or not os.path.exists(self.model_path(model)):
                raise EngineError("The built-in engine or its model isn't installed yet. Open Settings to set it up.")
            # A load that doesn't fit doesn't fail cleanly: it pushes everything else out of memory, and on some
            # computers the graphics driver has crashed the whole system trying.
            # llama.cpp puts what fits on the graphics card and the rest in system memory, so the two add up.
            need, ram, vram = memory_needed_gb(model), available_ram_gb(), free_vram_gb()
            if ram and vram is not None and ram + vram < need:
                raise EngineError(f"Not enough free memory to start {model['name']}: it needs about {need:.0f} GB "
                                  f"and {ram + vram:.0f} GB is free. Close other AI apps (another engine may be "
                                  "holding a model in memory), or choose a smaller model in Settings.")
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]
            ctx = min(int(context or 32768), int(model.get("context") or 32768))
            cmd = [server, "-m", self.model_path(model), "--host", "127.0.0.1", "--port", str(self.port),
                   "--jinja", "-c", str(ctx), "--alias", model["id"]]
            cmd += model.get("args", [])
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            _trim_log(self.log_path)
            with open(self.log_path, "ab") as log:  # the engine keeps its own handle; ours isn't needed after
                self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                             cwd=os.path.dirname(server), creationflags=flags)
            self.model_id = model_id
            try:
                with open(self.pid_path, "w") as f:
                    f.write(f"{self.proc.pid} {server}\n")
            except OSError:
                pass
            if self.watcher is None:
                self.watcher = threading.Thread(target=self._watch_idle, daemon=True)
                self.watcher.start()
            deadline = time.time() + 600
            while time.time() < deadline:
                if self.proc.poll() is not None:
                    raise EngineError(f"The built-in engine stopped while loading the model. See {self.log_path}.")
                try:
                    with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                        if r.status == 200:
                            return self.base_url
                except Exception:  # noqa: BLE001 - 503 while loading
                    pass
                time.sleep(0.5)
            # stop it, or the next question would find it "running" and get "Loading model" from it for ever
            self.stop()
            raise EngineError("The built-in engine took too long to load the model. This usually means the "
                              "computer is short of memory: close other AI apps and try again.")

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        if p:
            try:
                os.remove(self.pid_path)
            except OSError:
                pass


def _find_release(repo, tag, pattern):
    """(release, asset) for this computer. llama.cpp publishes its builds (b1234) as pre-releases, and its "latest"
    release can be one with no binaries at all, so "latest" means the newest release that has a matching build."""
    if tag != "latest":
        rels = [_json(f"https://api.github.com/repos/{repo}/releases/tags/{tag}")]
    else:
        rels = _json(f"https://api.github.com/repos/{repo}/releases?per_page=20")
    for rel in rels:
        if rel.get("draft"):
            continue
        asset = next((a for a in rel.get("assets", []) if pattern and re.search(pattern, a["name"])), None)
        if asset:
            return rel, asset
    return (rels[0] if rels else {}), None


def _hf_file_info(repo, filename):
    """(size, sha256) of a file on Hugging Face, from its metadata (None, None when unknown)."""
    try:
        req = urllib.request.Request(f"https://huggingface.co/{repo}/resolve/main/{filename}", method="HEAD",
                                     headers={"User-Agent": USER_AGENT})

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None

        opener = urllib.request.build_opener(NoRedirect)
        try:
            r = opener.open(req, timeout=30)
            headers = r.headers
        except urllib.error.HTTPError as e:  # the 302 to the CDN carries the metadata
            headers = e.headers
        size = int(headers.get("X-Linked-Size") or 0) or None
        etag = (headers.get("X-Linked-ETag") or "").strip('"')
        return size, (etag if re.fullmatch(r"[0-9a-f]{64}", etag) else None)
    except Exception:  # noqa: BLE001
        return None, None


class Engine:
    """Whichever engine the settings choose, as a Client."""

    def __init__(self):
        self.bundled = Bundled()
        self.lock = threading.Lock()

    def client(self, settings):
        e = settings["engine"]
        if e.get("mode") == "bundled":
            with self.lock:
                base = self.bundled.start(e.get("bundled_model"), e.get("context"))
            return Client(base, "", e.get("bundled_model"))
        if e.get("mode") == "external":
            if not e.get("base_url"):
                raise EngineError("No engine address set. Open Settings.")
            client = Client(e["base_url"], e.get("api_key", ""), e.get("model", ""))
            if not client.model:
                models = client.models()
                if not models:
                    raise EngineError("The engine has no models loaded.")
                client.model = models[0]
            return client
        raise EngineError("No AI engine is set up yet. Open Settings to choose one.")

