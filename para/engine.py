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


def total_ram_gb():
    try:
        if sys.platform == "darwin":
            out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=5).stdout
            return int(out.strip()) / 2**30
        if os.name == "nt":
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
            return stat.ullTotalPhys / 2**30
        with open("/proc/meminfo") as f:
            return int(re.search(r"MemTotal:\s+(\d+)", f.read()).group(1)) / 2**20
    except Exception:  # noqa: BLE001
        return 0.0


def recommend(models=None, ram=None):
    """The best catalogue model for this computer's memory (Macs share memory with the GPU: ~75% usable)."""
    models = models if models is not None else catalog()["models"]
    ram = total_ram_gb() if ram is None else ram
    fits = [m for m in models if m.get("min_ram_gb", 0) <= ram + 0.5]
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
        self.state, self.error, self.started = "running", "", time.time()
        self.cancel = threading.Event()

    def view(self):
        return {"kind": self.kind, "label": self.label, "done": self.done, "total": self.total,
                "state": self.state, "error": self.error}


def _download(url, dest, job, expect_size=None, sha256=None):
    """Download with resume to dest (via dest.part)."""
    part = dest + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
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
    if expect_size and os.path.getsize(part) != expect_size:
        raise EngineError(f"The download is incomplete ({os.path.getsize(part)} of {expect_size} bytes). Try again.")
    if sha256:
        h = hashlib.sha256()
        with open(part, "rb") as f:
            for block in iter(lambda: f.read(1 << 22), b""):
                h.update(block)
        if h.hexdigest() != sha256.lower():
            os.remove(part)
            raise EngineError("The download didn't match its checksum, so it was deleted. Try again.")
    os.replace(part, dest)


def _json(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


class Bundled:
    """llama-server, installed and run by the app."""

    def __init__(self):
        self.proc, self.port, self.model_id = None, None, None
        self.jobs, self.lock = {}, threading.Lock()
        self.log_path = paths.data_path("logs", "engine.log")
        atexit.register(self.stop)

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
            if self.proc and self.proc.poll() is None and self.model_id == model_id:
                return self.base_url
            self.stop()
            model = next((m for m in catalog()["models"] if m["id"] == model_id), None)
            server = self.server_path()
            if not model or not server or not os.path.exists(self.model_path(model)):
                raise EngineError("The built-in engine or its model isn't installed yet. Open Settings to set it up.")
            with socket.socket() as s:
                s.bind(("127.0.0.1", 0))
                self.port = s.getsockname()[1]
            ctx = min(int(context or 32768), int(model.get("context") or 32768))
            cmd = [server, "-m", self.model_path(model), "--host", "127.0.0.1", "--port", str(self.port),
                   "--jinja", "-c", str(ctx), "-ngl", "999", "--alias", model["id"]]
            cmd += model.get("args", [])
            log = open(self.log_path, "ab")
            flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                         cwd=os.path.dirname(server), creationflags=flags)
            self.model_id = model_id
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

