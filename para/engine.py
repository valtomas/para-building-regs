"""The AI engine: either a server the person already runs (any OpenAI-compatible API), or one the app installs and
runs itself (llama.cpp's llama-server with a GGUF model from Hugging Face)."""
import atexit
import contextlib
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

from . import VERSION, newer, paths
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


# A graphics card's own memory counts from this size. Smaller is usually an integrated GPU's reserved slice of system
# memory (Strix Halo: 512 MB), and too little for a model and its context anyway.
MIN_CARD_GB = 5.5  # a "6 GB" card can report a little under 6
# what the desktop and other programs keep on the card, so a model sized to the last gigabyte doesn't spill over
CARD_RESERVE_GB = 0.5
_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}


def _nvidia_cards():
    if not shutil.which("nvidia-smi"):
        return []
    out = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.free", "--format=csv,noheader,nounits"],
                         capture_output=True, text=True, timeout=10,
                         creationflags=0x08000000 if os.name == "nt" else 0).stdout
    cards = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.rsplit(",", 2)]
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
            cards.append({"name": parts[0], "vendor": "NVIDIA", "total_gb": int(parts[1]) / 1024,
                          "free_gb": int(parts[2]) / 1024})
    return cards


def _linux_cards():
    """AMD and Intel graphics, from the kernel's driver files (NVIDIA's driver doesn't write these: nvidia-smi does).
    An Intel GPU whose driver gives no memory size is listed with none: it shares system memory."""
    cards = []
    for card in sorted(os.listdir("/sys/class/drm")):
        dev = f"/sys/class/drm/{card}/device"
        if not re.fullmatch(r"card\d+", card):
            continue
        try:
            with open(dev + "/vendor") as f:
                vendor = _VENDORS.get(f.read().strip(), "")
        except OSError:
            vendor = ""
        if os.path.exists(dev + "/mem_info_vram_total"):
            with open(dev + "/mem_info_vram_total") as f, open(dev + "/mem_info_vram_used") as g:
                total, used = int(f.read()) / 2**30, int(g.read()) / 2**30
        elif vendor == "Intel":
            total = used = 0.0
        else:
            continue
        cards.append({"name": f"{vendor} graphics" if vendor else "Graphics", "vendor": vendor,
                      "total_gb": total, "free_gb": total - used})
    return cards


def _windows_cards(skip_nvidia):
    """AMD and Intel cards on Windows, from the display drivers' registry entries: their size is there, but how much
    is free can't be read without the vendor's libraries, so free_gb is None."""
    import winreg
    cards, cls = [], r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, cls) as root:
        for i in range(winreg.QueryInfoKey(root)[0]):
            sub = winreg.EnumKey(root, i)
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as k:
                    name = str(winreg.QueryValueEx(k, "DriverDesc")[0])
                    try:
                        size = winreg.QueryValueEx(k, "HardwareInformation.qwMemorySize")[0]
                    except OSError:  # older drivers only give the 32-bit value, which stops at 4 GB
                        size = winreg.QueryValueEx(k, "HardwareInformation.MemorySize")[0]
            except OSError:  # an adapter without these values (a remote display, a disabled card)
                continue
            if isinstance(size, bytes):
                size = int.from_bytes(size, "little")
            if skip_nvidia and "nvidia" in name.lower():
                continue
            vendor = "AMD" if re.search(r"amd|radeon", name, re.I) else "Intel" if "intel" in name.lower() else ""
            cards.append({"name": name, "vendor": vendor, "total_gb": int(size) / 2**30, "free_gb": None})
    return cards


def all_graphics():
    """Every graphics adapter found, with its own memory if it has any: [{name, vendor, total_gb, free_gb}], free_gb
    None when it can't be read. Apple silicon isn't listed: it always shares system memory."""
    if sys.platform == "darwin":
        return []
    cards = []
    try:  # a boundary: drivers and their tools vary, and an unreadable card shouldn't stop the app
        cards += _nvidia_cards()
    except Exception:  # noqa: BLE001
        pass
    try:
        if sys.platform.startswith("linux"):
            cards += _linux_cards()
        elif os.name == "nt":
            cards += _windows_cards(skip_nvidia=bool(cards))
    except Exception:  # noqa: BLE001
        pass
    return cards


def graphics_cards(adapters=None):
    """Graphics cards with their own memory, which is where a model runs fastest."""
    return [c for c in (all_graphics() if adapters is None else adapters) if c["total_gb"] >= MIN_CARD_GB]


# Integrated graphics reserve this much or less of system memory for themselves (Strix Halo: 512 MB by default);
# a graphics card has more of its own.
SHARED_MAX_GB = 2


def shared_graphics(adapters=None, apple=None):
    """The name of graphics built into the processor that run models from system memory, when there's no graphics
    card to run them instead: Apple silicon, AMD's (Strix Halo, Ryzen AI) or Intel's. None otherwise."""
    apple = (sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64")) if apple is None else apple
    if apple:
        return "Apple silicon"
    adapters = all_graphics() if adapters is None else adapters
    if graphics_cards(adapters):
        return None
    built_in = [c for c in adapters if c.get("vendor") in ("AMD", "Intel") and c["total_gb"] < SHARED_MAX_GB]
    return built_in[0]["name"] if built_in else None


_seen = []


def _adapters_seen():
    """all_graphics(), read once: what's fitted doesn't change while the app runs, and nvidia-smi is slow to ask."""
    if not _seen:
        _seen.append(all_graphics())
    return _seen[0]


def known_cards():
    return graphics_cards(_adapters_seen())


def known_shared():
    return shared_graphics(_adapters_seen())


def card_memory_gb(cards=None):
    """The memory a model can use on this computer's graphics cards: their total, less what the desktop keeps."""
    cards = known_cards() if cards is None else cards
    return sum(max(c["total_gb"] - CARD_RESERVE_GB, 0) for c in cards)


def free_vram_gb():
    """Free memory on graphics cards with their own memory, in GB: 0 when there are none, None when there's one whose
    free memory can't be read (AMD and Intel cards on Windows)."""
    cards = graphics_cards()
    if any(c["free_gb"] is None for c in cards):
        return None
    return sum(c["free_gb"] for c in cards)


def memory_needed_gb(model):
    """About what a model takes once loaded: its file, plus the context and the engine's working memory."""
    return model.get("size_gb", 0) * 1.1 + 1.5


def _windows_job(proc):
    """A Windows job that kills `proc` when its last handle closes, which Windows does when the app ends in any way
    (End task included). Returns the job's handle, to be kept open for as long as the engine should live."""
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class Extended(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", ctypes.c_ulonglong * 6),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    job = k.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not job or not k.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)) \
            or not k.AssignProcessToJobObject(job, int(proc._handle)):
        raise OSError(ctypes.get_last_error())
    return job


def guard(proc):
    """Make sure `proc` (the engine) can't outlive the app, however the app ends: killed outright, ended from the
    Task Manager, crashed. A normal exit stops the engine itself; this is for the exits that run no code of ours,
    which would leave gigabytes of model in memory until the app is next started.

    Windows: a job that closes with the app. Elsewhere: a small shell that waits on a pipe only the app holds, so
    it reads end-of-file the moment the app is gone, and then stops the engine. (Not PR_SET_PDEATHSIG: that fires
    when the thread that started the engine ends, and a request's thread ends long before the app.)
    Returns what `unguard` needs, or None if it couldn't be set up; the engine runs either way."""
    try:
        if os.name == "nt":
            return _windows_job(proc)
        return subprocess.Popen(["/bin/sh", "-c", 'read _; kill "$1" 2>/dev/null', "sh", str(proc.pid)],
                                stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)  # its own session: what kills the app's group spares it
    except Exception:  # noqa: BLE001 - a boundary: no shell, or a Windows that refuses the job; the engine still runs
        return None


def unguard(g):
    """The engine has been stopped the normal way: its guard isn't needed and mustn't act on a reused pid."""
    try:
        if g is None:
            return
        if os.name == "nt":
            import ctypes
            ctypes.WinDLL("kernel32").CloseHandle(ctypes.c_void_p(g))
        else:
            g.terminate()
            g.stdin.close()
            g.wait(5)
    except Exception:  # noqa: BLE001 - nothing to do about a guard that's already gone
        pass


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


def fits_card(model, card_gb):
    """Whether the whole model, with its context, fits in this much graphics card memory."""
    return bool(card_gb) and memory_needed_gb(model) <= card_gb


def recommend(models=None, ram=None, card=None):
    """The best catalogue model for this computer: the biggest that fits on its graphics card, since a model run
    there is many times faster than one split with the processor; only when none fits, the biggest that fits system
    memory (Macs and integrated GPUs, such as Strix Halo, run models from system memory on the GPU). A model with
    "suggest": false is listed but never the suggestion, and a "retired" one is no longer offered at all (it stays in
    the catalogue so a copy someone downloaded keeps working): a smaller one did better in our tests, so needing more
    memory doesn't make it the better choice."""
    models = [m for m in (models if models is not None else catalog()["models"])
              if m.get("suggest", True) and not m.get("retired")]
    card = card_memory_gb() if card is None else card
    on_card = [m for m in models if fits_card(m, card)]
    if on_card:
        return max(on_card, key=memory_needed_gb)
    ram = total_ram_gb() if ram is None else ram
    fits = [m for m in models if m.get("min_ram_gb", 0) <= ram + 0.5]
    return max(fits, key=lambda m: m.get("min_ram_gb", 0)) if fits else None


def platform_key(gpu=True):
    """The general build for this computer: Vulkan, which runs on NVIDIA, AMD and Intel graphics cards alike, or the
    CPU build. An NVIDIA card can have a CUDA build instead (Bundled._build_key)."""
    machine = platform.machine().lower()
    arm = machine in ("arm64", "aarch64")
    if sys.platform == "darwin":
        return "darwin-arm64" if arm else "darwin-x86_64"
    if os.name == "nt":
        if arm:
            return "windows-arm64"
        return "windows-gpu" if gpu else "windows-cpu"
    if arm:
        return "linux-arm64-gpu" if gpu else "linux-arm64-cpu"
    return "linux-gpu" if gpu else "linux-cpu"


def cuda_kind(smi_output):
    """Which CUDA build an NVIDIA card can run, from `nvidia-smi --query-gpu=driver_version,compute_cap`: "cuda-13"
    needs driver 580 or later and a card from 2018 on (compute capability 7.5, Turing); "cuda-12" runs on older
    cards with driver 528 or later; None means none will (an older driver: Vulkan is used). With several cards the
    oldest decides."""
    drivers, caps = [], []
    for line in smi_output.splitlines():
        m = re.match(r"\s*(\d+)(?:\.\d+)*\s*,\s*(\d+\.\d+|\[N/A\]|N/A)?", line)
        if m:
            drivers.append(int(m.group(1)))
            caps.append(float(m.group(2)) if m.group(2) and m.group(2)[0].isdigit() else 0.0)
    if not drivers:
        return None
    driver, cap = min(drivers), min(caps)
    if driver >= 580 and cap >= 7.5:
        return "cuda-13"
    return "cuda-12" if driver >= 528 else None


_NVIDIA = []  # nvidia_cuda's answer, asked once: the page reads the update offer every few seconds


def nvidia_cuda():
    """The CUDA build this computer's NVIDIA card can run ("cuda-12", "cuda-13"), or None: no NVIDIA card, an old
    driver, or a Mac or ARM computer (llama.cpp's CUDA builds for those aren't offered)."""
    if _NVIDIA:
        return _NVIDIA[0]
    kind = None
    try:
        if sys.platform != "darwin" and platform.machine().lower() in ("x86_64", "amd64") \
                and shutil.which("nvidia-smi"):
            out = subprocess.run(["nvidia-smi", "--query-gpu=driver_version,compute_cap", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=10,
                                 creationflags=0x08000000 if os.name == "nt" else 0)
            kind = cuda_kind(out.stdout) if out.returncode == 0 else None
    except Exception:  # noqa: BLE001 - a program of the driver's at the edge: no answer means the general build
        kind = None
    _NVIDIA.append(kind)
    return kind


def _tag_of(folder):
    """The llama.cpp tag of a build's folder: an NVIDIA build's folder is the tag with "-cuda" after it, so that it
    can sit beside the general build of the same tag."""
    return folder[:-len("-cuda")] if folder and folder.endswith("-cuda") else folder


def _kept(state):
    """What current.json keeps whichever build is in use: the builds that failed here, and whether a CUDA one did."""
    return {"failed": state.get("failed", []), **({"no_cuda": True} if state.get("no_cuda") else {})}


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


class _LoadFailed(EngineError):
    """The engine process ran but never served the model (it exited, or didn't become healthy)."""


IDLE_MINUTES = 10  # the built-in engine gives its memory back after this long without a question


class Bundled:
    """llama-server, installed and run by the app. It holds gigabytes of memory, so it never outlives the app: it is
    stopped when the app exits, a copy left behind by an app that was killed is stopped at the next start, and it
    is stopped after IDLE_MINUTES without use (the next question starts it again)."""

    def __init__(self):
        self.proc, self.port, self.model_id = None, None, None
        self.guard = None
        self.jobs, self.lock = {}, threading.Lock()
        self.log_path = paths.data_path("logs", "engine.log")
        self.pid_path = paths.data_path("engine.pid")
        self.last_used = time.time()
        self.users, self.users_lock = 0, threading.Lock()  # questions being answered now (begin, end)
        self.watcher = None
        self.gpu_layers = None
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

    def begin(self):
        """A question is about to use the engine, until end(). Counted, not timed: a model reading a long prompt
        sends nothing for a minute or more, and an engine update must not take that for idleness."""
        with self.users_lock:
            self.users += 1

    def end(self):
        with self.users_lock:
            self.users = max(0, self.users - 1)
        self.touch()

    @contextlib.contextmanager
    def using(self):
        self.begin()
        try:
            yield
        finally:
            self.end()

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

    # engine/current.json: {"tag": the folder of the build in use (its llama.cpp tag, with "-cuda" after it for an
    # NVIDIA build), "previous": the one it replaced and is kept until the new one has started, "proven": whether the
    # build in use has started a model, "failed": folders of builds that wouldn't work on this computer, "gpu":
    # graphics card or processor, "build": the release file's kind ("linux-cuda-12"), "no_cuda": a CUDA build failed
    # here, so the general one is used from now on, "fell_back": the build that was gone back from}. Installs made
    # before it have none: the first llama-server found under engine/ is theirs.
    @property
    def state_path(self):
        return os.path.join(self.engine_dir, "current.json")

    def _state(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, state):
        os.makedirs(self.engine_dir, exist_ok=True)
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=1)
        os.replace(tmp, self.state_path)

    def _binary_in(self, folder):
        name = "llama-server.exe" if os.name == "nt" else "llama-server"
        for root, _dirs, files in os.walk(folder):
            if name in files:
                return os.path.join(root, name)
        return None

    def _tag_dir(self, tag):
        """The folder of a build, or None for a tag that isn't a plain folder name."""
        return os.path.join(self.engine_dir, tag) if tag and os.path.basename(tag) == tag and tag not in (".", "..") \
            else None

    def server_path(self):
        """llama-server of the build in use: the tag in current.json, else (an install made before that file)
        the first one found."""
        folder = self._tag_dir(self._state().get("tag"))
        found = self._binary_in(folder) if folder and os.path.isdir(folder) else None
        return found or self._binary_in(self.engine_dir)

    def installed_tag(self):
        """The llama.cpp tag of the build in use, or None."""
        return _tag_of(self._current())

    def _current(self):
        """The folder of the build in use: from current.json, else the folder llama-server is found in, else None."""
        tag = self._state().get("tag")
        folder = self._tag_dir(tag)
        if folder and os.path.isdir(folder) and self._binary_in(folder):
            return tag
        found = self._binary_in(self.engine_dir)
        if not found:
            return None
        rel = os.path.relpath(found, self.engine_dir)
        return rel.split(os.sep)[0] if os.sep in rel else None

    def model_path(self, model):
        return os.path.join(self.models_dir, model["file"])

    def installed_models(self):
        return [m["id"] for m in catalog()["models"] if os.path.exists(self.model_path(m))]

    def downloaded(self):
        """Every model file in the models folder, with its size: the catalogue's, and also ones a newer catalogue no
        longer lists and unfinished downloads (.part), which take gigabytes and have no row of their own."""
        by_file = {m["file"]: m for m in catalog()["models"]}
        running = self.proc is not None and self.proc.poll() is None
        try:
            names = sorted(os.listdir(self.models_dir))
        except OSError:
            return []
        out = []
        for name in names:
            path = os.path.join(self.models_dir, name)
            base = name[:-len(".part")] if name.endswith(".part") else name
            if not base.endswith(".gguf") or not os.path.isfile(path):
                continue
            m = by_file.get(base)
            out.append({"file": name, "gb": round(os.path.getsize(path) / 2**30, 1), "id": m and m["id"],
                        "name": m["name"] if m else base, "partial": name != base,
                        "in_use": bool(running and m and m["id"] == self.model_id)})
        return out

    def remove_file(self, name):
        """Deletes one of downloaded()'s files: only a name it lists, so nothing outside the models folder can be."""
        entry = next((d for d in self.downloaded() if d["file"] == name), None)
        if not entry:
            raise EngineError("That file isn't among the downloaded models.")
        if entry["partial"] and self._downloading():
            raise EngineError("A download is running. Cancel it first, then delete what it left.")
        if entry["in_use"]:
            if self.users:
                raise EngineError("The AI engine is answering with this model. Delete it once the answer is done.")
            self.stop()
        os.remove(os.path.join(self.models_dir, entry["file"]))

    def status(self):
        running = self.proc is not None and self.proc.poll() is None
        return {"engine_installed": bool(self.server_path()), "models": self.installed_models(),
                "running": running, "model": self.model_id if running else None,
                "jobs": {k: j.view() for k, j in self.jobs.items()},
                "ram_gb": round(total_ram_gb(), 1), "free_gb": round(free_disk_gb(paths.app_dir()), 1),
                "platform": platform_key(), "engine_tag": self.installed_tag(),
                "engine_cuda": (self._current() or "").endswith("-cuda"),
                "engine_fell_back": self._state().get("fell_back"),
                "downloaded": self.downloaded(),
                "cards": [{"name": c["name"], "gb": round(c["total_gb"], 1)} for c in known_cards()],
                "shared_gpu": known_shared(),
                # which version of llama.cpp: the one installed, and the one a first install with the graphics card
                # ticked would fetch ("windows-cuda-12", "linux-gpu", …), so the tick box can say which
                "engine_build": self._state().get("build"), "build_for_card": self._build_key(True),
                "on_card": [m["id"] for m in catalog()["models"] if fits_card(m, card_memory_gb())],
                # where the running model's layers went, from llama.cpp's log: [on the card, of all]
                "gpu_layers": self.gpu_layers if running else None}

    # ----- installing -----
    def install(self, model_id, gpu=True):
        """Download llama-server (if needed) and the model, in the background."""
        model = next((m for m in catalog()["models"] if m["id"] == model_id), None)
        if not model:
            raise EngineError("Unknown model")
        if self._downloading():
            raise EngineError("A download is already running")
        job = Job("install", model["name"])
        self.jobs["install"] = job
        threading.Thread(target=self._install, args=(job, model, gpu), daemon=True).start()
        return job.view()

    def _downloading(self):
        return any(j.state == "running" for j in self.jobs.values())

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
        """The first install of the engine: the pinned build. An NVIDIA card gets the CUDA build, with the general one
        beside it to go back to if the CUDA one won't load a model (38 MB next to its 600); if the CUDA build won't
        even run, the general one alone."""
        key = self._build_key(gpu)
        keep = _kept(self._state())
        if "cuda" in key:
            try:
                cuda = self._fetch_build(job, key)
            except EngineError as e:
                if job.cancel.is_set():
                    raise
                self._no_cuda(e)
                keep["no_cuda"], cuda = True, None
            if cuda:
                self._save_state({"tag": cuda, "proven": True, "gpu": gpu, "build": key, **keep})
                try:
                    back = self._fetch_build(job, platform_key(gpu))
                except EngineError:  # the CUDA build is installed and runs: it goes on without one to go back to
                    if job.cancel.is_set():
                        raise
                    return
                self._save_state({"tag": cuda, "previous": back, "proven": False, "gpu": gpu, "build": key, **keep})
                return
            key = platform_key(gpu)
        tag = self._fetch_build(job, key)
        self._save_state({"tag": tag, "proven": True, "gpu": gpu, "build": key, **keep})

    def _build_key(self, gpu):
        """Which kind of build to fetch (a key of llama_cpp.assets): a CUDA build where there's an NVIDIA card that
        can run one, the catalogue lists it and no CUDA build has failed here; else the general build."""
        key = platform_key(gpu)
        if gpu and key in ("windows-gpu", "linux-gpu") and not self._state().get("no_cuda"):
            kind = nvidia_cuda()
            cuda = f"{key.split('-')[0]}-{kind}" if kind else None
            if cuda and ((catalog().get("llama_cpp") or {}).get("assets") or {}).get(cuda):
                return cuda
        return key

    def _no_cuda(self, why):
        """A CUDA build wouldn't work here: say so in the engine log, and use the general build from now on."""
        self._save_state({**self._state(), "no_cuda": True})
        with open(self.log_path, "ab") as log:
            log.write(f"\nThe NVIDIA (CUDA) build of the AI engine won't be used on this computer: {why}\n".encode())

    def _fetch_build(self, job, key, verify=False):
        """Downloads the pinned build of this kind into engine/<tag>/ (engine/<tag>-cuda/ for CUDA), next to any
        other build, and returns that folder's name. With verify the binary must also run (--version); a CUDA build
        must always find the card (--list-devices). A build that fails is removed again; nothing else is touched."""
        info = catalog().get("llama_cpp") or {}
        repo, tag = info.get("repo", "ggml-org/llama.cpp"), info.get("tag", "latest")
        patterns = (info.get("assets") or {}).get(key)
        patterns = list(patterns) if isinstance(patterns, list) else [patterns]
        cuda = "cuda" in key
        rel, assets = _find_assets(repo, tag, patterns)
        if not assets:
            raise EngineError(f"No llama.cpp build for this computer ({key}) in release "
                              f"{rel.get('tag_name')}. Use an engine you install yourself instead.")
        os.makedirs(self.engine_dir, exist_ok=True)
        archives = [os.path.join(self.engine_dir, a["name"]) for a in assets]
        target = os.path.join(self.engine_dir, rel.get("tag_name", "llama.cpp") + ("-cuda" if cuda else ""))
        runtime = target + ".runtime"
        if os.path.basename(target) == self._current():
            raise EngineError("That build of the AI engine is already installed.")
        began = False  # a download that fails has touched nothing: a build kept to go back to may be this one
        try:
            for asset, archive in zip(assets, archives):
                digest = (asset.get("digest") or "").removeprefix("sha256:") or None
                _download(asset["browser_download_url"], archive, job, expect_size=asset.get("size"), sha256=digest)
            began = True
            for folder, archive in zip([target] + [runtime] * (len(archives) - 1), archives):
                if folder == target:
                    shutil.rmtree(target, ignore_errors=True)  # what an earlier attempt left of this build
                _unpack(archive, folder)
                os.remove(archive)
            server = self._binary_in(target)
            if not server:
                raise EngineError("The llama.cpp download had no llama-server in it.")
            # The CUDA runtime comes in an archive of its own (on Linux in a folder of its own); its libraries go
            # beside llama-server, where the CUDA library looks for them ($ORIGIN, or the program's folder).
            for root, _d, files in os.walk(runtime):
                for f in files:
                    os.replace(os.path.join(root, f), os.path.join(os.path.dirname(server), f))
            shutil.rmtree(runtime, ignore_errors=True)
            if os.name != "nt":
                for root, _d, files in os.walk(target):
                    for f in files:
                        p = os.path.join(root, f)
                        if f.startswith("llama-") or f.endswith((".dylib", ".so")) or ".so." in f:
                            os.chmod(p, 0o755)
                if sys.platform == "darwin":
                    subprocess.run(["xattr", "-dr", "com.apple.quarantine", target], capture_output=True)
            if cuda:
                self._check_cuda(server)
            elif verify:
                self._check_runs(server)
        except BaseException:  # a half-built folder would be found by the walk: take it away, then say why
            if began:
                shutil.rmtree(target, ignore_errors=True)
            shutil.rmtree(runtime, ignore_errors=True)
            for archive in archives:
                if os.path.exists(archive) and not os.path.exists(archive + ".part"):
                    os.remove(archive)
            raise
        return os.path.basename(target)

    def _check_cuda(self, server):
        """A CUDA build loads its CUDA library only as it starts, and where that can't load (no NVIDIA driver, one
        too old, a card it wasn't built for) it runs on the processor instead without a word. So it must list a
        CUDA device, or the general build is better."""
        flags = 0x08000000 if os.name == "nt" else 0
        try:
            done = subprocess.run([server, "--list-devices"], capture_output=True, stdin=subprocess.DEVNULL,
                                  timeout=60, cwd=os.path.dirname(server), creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise EngineError(f"the NVIDIA build won't run ({e.__class__.__name__})") from e
        out = done.stdout + done.stderr
        with open(self.log_path, "ab") as log:
            log.write(out)
        if done.returncode != 0 or not re.search(rb"^\s*CUDA\d+:", out, re.M):
            raise EngineError("the NVIDIA build doesn't find the graphics card")

    def _check_runs(self, server):
        """The binary starts and says its version, with a time limit: a build that can't run here (a missing
        library, the wrong kind of processor) shows up now and not at someone's next question."""
        flags = 0x08000000 if os.name == "nt" else 0
        try:
            with open(self.log_path, "ab") as log:
                done = subprocess.run([server, "--version"], stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                      timeout=60, cwd=os.path.dirname(server), creationflags=flags)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise EngineError(f"The new AI engine won't run on this computer ({e.__class__.__name__}). "
                              f"The earlier one is still in use. See {self.log_path}.") from e
        if done.returncode != 0:
            raise EngineError("The new AI engine won't run on this computer, so the earlier one is still in use. "
                              f"See {self.log_path}.")

    # ----- updating -----
    def _gpu(self):
        """Which kind of build is installed, so an update fetches the same: someone on the CPU build chose it
        because the graphics-card one wouldn't start. From current.json; an install made before that file is told
        by its files, since only the Vulkan build has a Vulkan library (libggml-vulkan.so, ggml-vulkan.dll)."""
        gpu = self._state().get("gpu")
        if isinstance(gpu, bool):
            return gpu
        server = self.server_path()
        if not server or sys.platform == "darwin":  # the Mac has one kind of build
            return True
        rel = os.path.relpath(server, self.engine_dir)
        folder = os.path.join(self.engine_dir, rel.split(os.sep)[0]) if os.sep in rel else self.engine_dir
        return any(k in f.lower() for _r, _d, files in os.walk(folder) for f in files for k in ("vulkan", "cuda"))

    def update_wanted(self):
        """The pinned tag if installing it would be an update of this computer's engine, else None."""
        info = catalog().get("llama_cpp") or {}
        tag = info.get("tag")
        if not tag or tag == "latest" or not self.server_path() or tag == self.installed_tag():
            return None
        folder = tag + ("-cuda" if "cuda" in self._build_key(self._gpu()) else "")
        if folder in self._state().get("failed", []):
            return None
        if info.get("min_app") and newer(info["min_app"], VERSION):  # the pin is for a later app than this one
            return None
        return tag

    def engine_update(self):
        """What an update check reports: {"tag", "size"} when the catalogue pins another build of llama.cpp than the
        one installed, else None. The size is None where the release can't be reached."""
        tag = self.update_wanted()
        if not tag:
            return None
        info = catalog().get("llama_cpp") or {}
        key = self._build_key(self._gpu())
        patterns = (info.get("assets") or {}).get(key)
        size = None
        try:
            _rel, assets = _find_assets(info.get("repo", "ggml-org/llama.cpp"), tag,
                                        list(patterns) if isinstance(patterns, list) else [patterns])
            size = sum(a.get("size") or 0 for a in assets) or None
        except Exception:  # noqa: BLE001 - a network call at the edge: the offer can be made without a size
            pass
        return {"tag": tag, "size": size, "cuda": "cuda" in key}

    def update_engine(self, gpu=None):
        """Installs the pinned build beside the one in use, in the background. It is used from the next start; the
        earlier build is kept until the new one has started a model (see start)."""
        if self._downloading():
            raise EngineError("A download is already running")
        if not self.update_wanted():
            raise EngineError("There is no AI engine update to install.")
        job = Job("engine", "AI engine (llama.cpp)")
        self.jobs["engine"] = job
        threading.Thread(target=self._update, args=(job, self._gpu() if gpu is None else gpu),
                         daemon=True).start()
        return job.view()

    def _update(self, job, gpu):
        target = None
        try:
            old = self._current()
            key = self._build_key(gpu)
            try:
                tag = self._fetch_build(job, key, verify=True)
            except EngineError as e:  # a CUDA build that won't run here: the general one instead, in the same job
                if "cuda" not in key or job.cancel.is_set():
                    raise
                self._no_cuda(e)
                key = platform_key(gpu)
                tag = self._fetch_build(job, key, verify=True)
            target = self._tag_dir(tag)
            job.label, job.done, job.total = "Switching to the new AI engine", 0, 0
            deadline = time.time() + 600
            while True:
                with self.lock:  # waits for a model that is loading
                    if not self.users:  # never in the middle of an answer, however long the model is silent
                        self.stop()
                        state = self._state()
                        self._save_state({"tag": tag, "previous": old, "proven": False, "gpu": gpu, "build": key,
                                          **_kept(state)})
                        break
                if job.cancel.is_set() or time.time() > deadline:
                    raise EngineError("Cancelled" if job.cancel.is_set() else
                                      "The AI engine was busy answering, so the update wasn't installed. Try again.")
                time.sleep(1)
            job.note = "Installed. It will be used from the next question."
            job.state = "done"
        except Exception as e:  # noqa: BLE001 - a background thread: report it on the page
            if target and self._current() != os.path.basename(target):
                shutil.rmtree(target, ignore_errors=True)
            job.state, job.error = "error", str(e) or e.__class__.__name__

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
            if not model:
                raise EngineError("No model is chosen for the built-in engine yet. Open Settings to choose one.")
            if not os.path.exists(self.model_path(model)):
                # the page offers the download under this (app.js, `error`): keep "isn't downloaded yet"
                raise EngineError(f"{model['name']}, the model chosen in Settings, isn't downloaded yet "
                                  f"({model.get('size_gb', '?')} GB).")
            if not server:
                raise EngineError(f"The built-in engine that runs {model['name']} isn't downloaded yet.")
            # A load that doesn't fit doesn't fail cleanly: it pushes everything else out of memory, and on some
            # computers the graphics driver has crashed the whole system trying.
            # llama.cpp puts what fits on the graphics card and the rest in system memory, so the two add up.
            need, ram, vram = memory_needed_gb(model), available_ram_gb(), free_vram_gb()
            if ram and vram is not None and ram + vram < need:
                raise EngineError(f"Not enough free memory to start {model['name']}: it needs about {need:.0f} GB "
                                  f"and {ram + vram:.0f} GB is free. Close other AI apps (another engine may be "
                                  "holding a model in memory), or choose a smaller model in Settings.")
            ctx = min(int(context or 32768), int(model.get("context") or 32768))
            try:
                url = self._launch(server, model, ctx)
            except EngineError as e:
                if not isinstance(e, _LoadFailed) or not self._can_fall_back():
                    raise
                url = self._fall_back(model, ctx, e)
            self._prove()
            return url

    def _launch(self, server, model, ctx):
        """Starts llama-server on a free port and waits until it has loaded the model."""
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            self.port = s.getsockname()[1]
        cmd = [server, "-m", self.model_path(model), "--host", "127.0.0.1", "--port", str(self.port),
               "--jinja", "-c", str(ctx), "--alias", model["id"]]
        cmd += model.get("args", [])
        flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
        _trim_log(self.log_path)
        self.gpu_layers = None
        log_from = os.path.getsize(self.log_path) if os.path.exists(self.log_path) else 0
        with open(self.log_path, "ab") as log:  # the engine keeps its own handle; ours isn't needed after
            self.proc = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                                         cwd=os.path.dirname(server), creationflags=flags)
        self.guard = guard(self.proc)
        self.model_id = model["id"]
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
                self.stop()  # nothing left to stop, but its guard and pid file go
                raise _LoadFailed(f"The built-in engine stopped while loading the model. See {self.log_path}.")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if r.status == 200:
                        self.gpu_layers = _offloaded(self.log_path, log_from)
                        return self.base_url
            except Exception:  # noqa: BLE001 - 503 while loading
                pass
            time.sleep(0.5)
        # stop it, or the next question would find it "running" and get "Loading model" from it for ever
        self.stop()
        raise _LoadFailed("The built-in engine took too long to load the model. This usually means the "
                          "computer is short of memory: close other AI apps and try again.")

    # ----- after an update -----
    def _can_fall_back(self):
        """Only the first start on a build that replaced another: a model that doesn't fit fails on any build, so
        a build that has already started once is not blamed."""
        state = self._state()
        prev = self._tag_dir(state.get("previous"))
        return bool(state.get("previous") and not state.get("proven", True) and prev and self._binary_in(prev))

    def _fall_back(self, model, ctx, failure):
        """The new build wouldn't load the model: start the earlier one instead, so the question is still answered.
        Only if that works is the new build blamed: it is then forgotten (so it isn't offered again) and removed. If
        the earlier build fails too, the model is the problem, and nothing is changed."""
        state = self._state()
        failed, back = state["tag"], state["previous"]
        try:
            url = self._launch(self._binary_in(self._tag_dir(back)), model, ctx)
        except EngineError:
            raise failure from None
        cuda = failed.endswith("-cuda") and not back.endswith("-cuda")  # a CUDA build that never worked here
        self._save_state({"tag": back, "proven": True, "gpu": self._gpu(), **_kept(state),
                          "failed": sorted(set(state.get("failed", [])) | {failed}),
                          **({"no_cuda": True} if cuda else {}),
                          "fell_back": {"tag": _tag_of(failed), "cuda": cuda, "at": time.strftime("%Y-%m-%d %H:%M")}})
        shutil.rmtree(self._tag_dir(failed), ignore_errors=True)
        with open(self.log_path, "ab") as log:
            log.write(f"\nThe AI engine build {failed} didn't start {model['id']}; went back to {back}.\n".encode())
        return url

    def _prove(self):
        """The build in use has started a model: the one it replaced is no longer needed."""
        state = self._state()
        if state.get("proven", True) and not state.get("previous"):
            return
        prev = self._tag_dir(state.pop("previous", None))
        state["proven"] = True
        self._save_state(state)
        if prev:
            shutil.rmtree(prev, ignore_errors=True)

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self):
        p, self.proc = self.proc, None
        g, self.guard = self.guard, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        unguard(g)
        if p:
            try:
                os.remove(self.pid_path)
            except OSError:
                pass


def _unpack(archive, folder):
    if archive.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(folder)
    else:
        with tarfile.open(archive) as t:
            t.extractall(folder, filter="data") if hasattr(tarfile, "data_filter") else t.extractall(folder)


def _find_assets(repo, tag, patterns):
    """(release, [one asset per pattern]), all from the same release: a CUDA build comes with its runtime in a file of
    its own. The list is empty if any is missing."""
    rel, first = _find_release(repo, tag, patterns[0] if patterns else None)
    if not first:
        return rel, []
    found = [first]
    for pattern in patterns[1:]:
        _rel, asset = _find_release(repo, rel.get("tag_name", tag), pattern)
        if not asset:
            return rel, []
        found.append(asset)
    return rel, found


def _offloaded(log_path, start):
    """How many of the model's layers llama.cpp put on the graphics card, from its log ("offloaded 49/49 layers to
    GPU"): [on the card, of all], or None when it didn't say (an older or newer build words it differently)."""
    try:
        with open(log_path, "rb") as f:
            f.seek(start)
            found = re.findall(rb"offloaded (\d+)/(\d+) layers to GPU", f.read())
        return [int(found[-1][0]), int(found[-1][1])] if found else None
    except OSError:
        return None


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

