"""Updates the app itself: downloads the new release's file for the way this copy was installed, and puts it in place.

Each kind of install is replaced differently. The Windows installer runs silently over the old copy; an AppImage
swaps its own file; a Mac app swaps its bundle; a .deb needs an administrator, so the system asks for the
password (pkexec) and apt installs it. The software centre can't do it: it won't upgrade an installed package.

Copies that can't replace themselves (the Windows zip, the Linux folder, running from source, a Mac app macOS runs
from a hidden copy) have no kind, and the page links to the release instead.
"""
import os
import shutil
import subprocess
import sys
import tempfile

from . import paths

ASSETS = {"windows": "Para-windows-setup.exe", "appimage": "Para-linux.AppImage", "deb": "Para-linux.deb",
          "mac": "Para-mac.zip"}


def mac_bundle(exe):
    """The Para.app folder the running program is inside, or None."""
    p = exe
    while p and p != os.path.dirname(p):
        if p.endswith(".app"):
            return p
        p = os.path.dirname(p)
    return None


def kind():
    """How this copy was installed, if it can update itself: "windows", "appimage", "deb", "mac" or None."""
    if not getattr(sys, "frozen", False):
        return None
    exe = os.path.realpath(sys.executable)
    if os.name == "nt":  # the installer leaves its uninstaller beside the app; the zip has none
        return "windows" if os.path.exists(os.path.join(os.path.dirname(exe), "unins000.exe")) else None
    if sys.platform == "darwin":
        app = mac_bundle(exe)
        # run straight from Downloads, macOS runs a read-only copy from AppTranslocation: nothing to replace
        ok = app and "/AppTranslocation/" not in app and os.access(os.path.dirname(app), os.W_OK)
        return "mac" if ok else None
    image = os.environ.get("APPIMAGE")
    if image and os.access(os.path.dirname(image), os.W_OK):
        return "appimage"
    return "deb" if exe.startswith("/opt/para/") else None


def offer(assets):
    """What the page needs to update this copy from a release's assets (by name), or None."""
    k = kind()
    a = assets.get(ASSETS.get(k, ""))
    if not a:
        return None
    digest = a.get("digest") or ""  # GitHub publishes "sha256:<hex>" for each release file
    return {"kind": k, "url": a["browser_download_url"], "size": a.get("size"),
            "sha256": digest[7:] if digest.startswith("sha256:") else None}


def download_path(update):
    """Where to download to. An AppImage goes beside the old one, so swapping them is a rename on one disk."""
    if update["kind"] == "appimage":
        return os.environ["APPIMAGE"] + ".new"
    return paths.data_path("updates", ASSETS[update["kind"]])


def clean_env():
    """The environment as it was before the packaged app changed it, for programs started from here: PyInstaller
    points LD_LIBRARY_PATH at its own libraries, and an AppImage sets variables of its own, which would confuse a
    new copy of Para or the software centre."""
    env = dict(os.environ)
    if "LD_LIBRARY_PATH_ORIG" in env:
        env["LD_LIBRARY_PATH"] = env.pop("LD_LIBRARY_PATH_ORIG")
    else:
        env.pop("LD_LIBRARY_PATH", None)
    for name in ("APPIMAGE", "APPDIR", "ARGV0", "OWD", "QTWEBENGINE_DISABLE_SANDBOX"):
        env.pop(name, None)
    return env


def swap_appimage(new, image):
    os.chmod(new, 0o755)
    os.replace(new, image)


def swap_mac(zip_path, app):
    """Unzips the new Para.app beside the old one and swaps them; returns the old one's new name to delete later.
    A file the app downloads itself isn't quarantined, so macOS doesn't ask to Open Anyway again."""
    parent = os.path.dirname(app)
    tmp = tempfile.mkdtemp(prefix=".Para-update-", dir=parent)
    try:
        subprocess.run(["ditto", "-x", "-k", zip_path, tmp], check=True)
        new = next(os.path.join(tmp, n) for n in os.listdir(tmp) if n.endswith(".app"))
        old = app + ".old"
        shutil.rmtree(old, ignore_errors=True)
        os.rename(app, old)
        os.rename(new, app)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return old


def start_later(script, *args):
    """Runs a shell line once this app has had time to quit, outliving it."""
    subprocess.Popen(["sh", "-c", "sleep 2; " + script, *args], env=clean_env(), start_new_session=True,
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def install(update, path):
    """Puts the downloaded file in place. Returns (what to tell the person, whether the app should quit now)."""
    k = update["kind"]
    if k == "windows":
        # Silent shows only a progress window; the installer closes nothing of ours, since we quit first, and
        # opens Para again when it's done.
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        subprocess.Popen([path, "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], creationflags=flags, close_fds=True)
        return "Installing. Para will close and open again in a moment.", True
    if k == "appimage":
        image = os.environ["APPIMAGE"]
        swap_appimage(path, image)
        start_later('exec "$0"', image)
        return "Updated. Para will close and open again in a moment.", True
    if k == "mac":
        app = mac_bundle(os.path.realpath(sys.executable))
        old = swap_mac(path, app)
        start_later('rm -rf "$1"; open "$0"', app, old)
        return "Updated. Para will close and open again in a moment.", True
    install_deb(path)
    start_later('exec "$0"', "/opt/para/Para")
    return "Updated. Para will close and open again in a moment.", True


def install_deb(path):
    """Installs a .deb as administrator: pkexec shows the system's password dialog and waits for it. Files the
    running app uses are replaced as new files, so it carries on until it restarts."""
    if not shutil.which("pkexec"):
        raise RuntimeError(f"Can't ask for the administrator password here. Install the update with: "
                           f"sudo apt install {path}")
    r = subprocess.run(["pkexec", "apt-get", "install", "-y", path], env=clean_env(), capture_output=True, text=True)
    if r.returncode in (126, 127):  # the dialog was cancelled, or the password wasn't accepted
        raise RuntimeError("The update wasn't installed: it needs your password. Press Update now to try again.")
    if r.returncode:
        last = (r.stderr.strip() or r.stdout.strip()).splitlines()[-1:] or ["apt-get failed"]
        raise RuntimeError(f"The update wasn't installed ({last[0]}).")
