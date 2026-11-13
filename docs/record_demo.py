"""Records the website's demo video: a real question answered by a real model, with the wait shortened.

A browser drives the app as a person would (picks a project, types the question, sends it, opens the cited diagram,
enlarges it and closes it again, and scrolls the reader) while Chrome's
screencast saves every frame with the time it arrived. The frames are then laid out again: the time the model spends
searching and writing is shortened, everything a person does plays at its real speed, and a badge in the corner says
how long the shortened part really took. Nothing is staged: the answer is whatever the model says on the day, and
the project's earlier chats in the sidebar are real ones, asked of the same model before recording starts.

    uv run --with playwright --with imageio-ffmpeg python3 docs/record_demo.py

Needs an OpenAI-compatible engine with the model loaded (Lemonade by default; --base-url, --model) and the built
library (library/dist/regs.sqlite). Uses the system Chromium if there is one, otherwise Playwright's own
(`playwright install chromium`). Writes docs/screenshots/demo.mp4, demo.webm and demo.jpg (the poster) for the
website. The answer differs each run: it prints it, so run it again until the take is a good one.
"""
import argparse
import asyncio
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
PROJECT = "14 Elm Road loft"
NOTES = "Loft conversion of a two-storey semi-detached house: a new second floor with one bedroom and a shower room."
# asked before recording, so the project has a history in the sidebar (oldest first)
EARLIER = ["Does the new loft bedroom need an escape window?",
           "Fire doors to the rooms off the existing stair",
           "Headroom over the new loft stair"]
# answered from ADK Diagram 3.1, a drawing worth showing in the reader. The question gives the facts the answer
# turns on, rather than leaving the model to guess them from the project's name and notes.
QUESTION = ("There's a new landing at the top of the loft stair, open on one side to the stairwell below. "
            "What height does the guarding need to be?")
WIDTH, HEIGHT, SCALE = 1280, 800, 1.5  # frames are 1920 x 1200
FPS = 30
WAIT_SHOWN = 2.5    # seconds of video for the model searching and reading, however long it took
WRITE_SHOWN = 4.0   # at most this long for the answer being written

# A pointer that follows the mouse, since a screencast doesn't draw one and a click with no pointer looks like magic.
CURSOR = """
addEventListener('DOMContentLoaded', () => {
  const c = document.createElement('div');
  c.innerHTML = '<svg width="22" height="22" viewBox="0 0 22 22"><path d="M3 2 L3 18 L7.5 14 L10.5 20.5 L13 19.4 '
    + 'L10.1 13 L16 13 Z" fill="#2b1d16" stroke="#fffdf8" stroke-width="1.5" stroke-linejoin="round"/></svg>';
  c.style.cssText = 'position:fixed;left:-40px;top:-40px;z-index:99999;pointer-events:none;width:22px;height:22px';
  document.body.appendChild(c);
  // an open dialog is drawn over everything else on the page, so the pointer goes inside it while it's open
  addEventListener('mousemove', (e) => {
    const home = document.querySelector('dialog[open]') || document.body;
    if (c.parentNode !== home) home.appendChild(c);
    c.style.left = e.clientX - 3 + 'px'; c.style.top = e.clientY - 2 + 'px';
  }, true);
});
"""


def start_app(base_url, model):
    """The app in this process against throwaway folders, set up for the engine; returns its address."""
    tmp = tempfile.mkdtemp(prefix="para-demo-")
    os.environ["PARA_APP_DIR"] = os.path.join(tmp, "app")
    os.environ["PARA_LIBRARY"] = os.path.join(ROOT, "library", "dist", "regs.sqlite")
    os.environ["HOME"] = tmp  # an empty files folder
    os.makedirs(os.environ["PARA_APP_DIR"])
    with open(os.path.join(os.environ["PARA_APP_DIR"], "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"engine": {"mode": "external", "base_url": base_url, "model": model}, "theme": "light",
                   "check_updates": False}, f)
    sys.path.insert(0, ROOT)
    from para import server
    httpd = server.Server(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/"


def seed(url):
    """The project and its earlier chats, asked through the app like any other. This also loads the model, so the
    time on the badge is answering, not loading."""
    def post(path, body):
        req = urllib.request.Request(url + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.read()
    post("api/projects", {"name": PROJECT, "notes": NOTES})
    for question in EARLIER:
        print(f"Asking earlier question: {question}", flush=True)
        post("api/ask", {"question": question, "project": PROJECT})


def chromium():
    for path in (os.environ.get("PARA_CHROMIUM"), "/usr/lib/chromium/chromium", shutil.which("chromium"),
                 shutil.which("google-chrome")):
        if path and os.path.isfile(path):
            return path
    return None  # Playwright's own


async def record(url, frames_dir):
    """Drives the app and saves the screencast; returns [(time, file)] and the times of what happened."""
    from playwright.async_api import async_playwright
    frames, marks = [], {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chromium())
        page = await browser.new_page(viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=SCALE)
        await page.add_init_script(CURSOR)
        await page.goto(url)
        await page.wait_for_selector("#welcome")
        await page.wait_for_timeout(800)  # fonts and the chat list

        async def point(locator, steps=20, click=True):
            box = await locator.bounding_box()
            await page.mouse.move(box["x"] + min(box["width"] / 2, 60), box["y"] + box["height"] / 2, steps=steps)
            if click:
                await page.wait_for_timeout(250)
                await page.mouse.click(box["x"] + min(box["width"] / 2, 60), box["y"] + box["height"] / 2)
        cdp = await page.context.new_cdp_session(page)

        def frame(ev):
            name = os.path.join(frames_dir, f"{len(frames):05d}.png")
            with open(name, "wb") as f:
                f.write(base64.b64decode(ev["data"]))
            frames.append((time.time(), name))
            asyncio.ensure_future(cdp.send("Page.screencastFrameAck", {"sessionId": ev["sessionId"]}))

        cdp.on("Page.screencastFrame", frame)
        await cdp.send("Page.startScreencast", {"format": "png", "maxWidth": int(WIDTH * SCALE),
                                                "maxHeight": int(HEIGHT * SCALE), "everyNthFrame": 1})
        marks["start"] = time.time()
        await page.mouse.move(WIDTH * .62, HEIGHT * .45)
        await page.wait_for_timeout(1200)
        await point(page.locator("#projectBtn"), steps=25)
        await page.wait_for_timeout(700)
        await point(page.locator(f'#projectMenu [data-project="{PROJECT}"]'), steps=12)
        await page.wait_for_timeout(1500)  # the project's chats in Recent
        await point(page.locator("#question"), steps=25)
        await page.keyboard.type(QUESTION, delay=55)
        await page.wait_for_timeout(500)
        await point(page.locator("#send"), steps=15)
        marks["sent"] = time.time()
        await page.wait_for_function("document.querySelector('.msg.assistant .answer')?.textContent.trim()",
                                     timeout=600_000, polling=50)
        marks["writing"] = time.time()
        await page.wait_for_function("!document.querySelector('#send').hidden", timeout=600_000, polling=50)
        marks["done"] = time.time()
        print(await page.locator(".msg.assistant .answer").inner_text(), flush=True)  # to judge the take
        await page.wait_for_timeout(1500)
        cites = page.locator(".msg.assistant .answer .cite")
        if not await cites.count():
            raise SystemExit("The answer cited nothing; run it again.")
        diagram = cites.filter(has_text="Diagram")
        cite = diagram.first if await diagram.count() else cites.first
        await cite.scroll_into_view_if_needed()
        await point(cite, steps=30)
        await page.wait_for_selector("#reader:not([hidden]) .passage")
        figure = page.locator("#reader .passage.focus img.figure").first
        if await figure.count():  # point at the drawing, not at whatever the pointer happens to be over
            box = await figure.bounding_box()
            x, y = box["x"] + box["width"] * .45, box["y"] + box["height"] * .45
            await page.mouse.move(x, y, steps=25)
            await page.wait_for_timeout(1500)
            # enlarge it to the window, look, and close it with the cross
            await page.mouse.click(x, y)
            await page.wait_for_selector("#dlg-figure[open]")
            await page.mouse.move(x + 1, y + 1)  # brings the pointer into the dialog
            await page.wait_for_timeout(2800)
            await point(page.locator("#dlg-figure form button"), steps=25)
            await page.wait_for_selector("#dlg-figure:not([open])", state="attached")  # closed, so not visible
            await page.mouse.move(x, y, steps=20)  # back over the reader, so the wheel scrolls it
        await page.wait_for_timeout(1500)
        # read on down the document to the next passage, in wheel steps as a person would
        distance = await page.evaluate("""() => {
          const next = document.querySelector('#reader .passage.focus + .passage');
          const top = document.querySelector('#readerBody').getBoundingClientRect().top;
          return next ? next.getBoundingClientRect().top - top - 12 : 300;
        }""")
        steps = max(1, round(distance / 60))
        for _ in range(steps):
            await page.mouse.wheel(0, distance / steps)
            await page.wait_for_timeout(110)
        await page.wait_for_timeout(3000)
        marks["end"] = time.time()
        await cdp.send("Page.stopScreencast")
        await badge(browser, url, marks)
        await browser.close()
    return frames, marks


async def badge(browser, url, marks):
    """The "shortened" badge, drawn by the app's own stylesheet so it's in its fonts and colours."""
    page = await browser.new_page(device_scale_factor=SCALE)
    await page.goto(url)
    real = round(marks["done"] - marks["sent"])
    shown = round(shown_wait(marks))
    await page.evaluate("""async ([real, shown]) => {
      document.documentElement.setAttribute('data-theme', 'light');
      document.documentElement.style.background = document.body.style.background = 'transparent';
      document.body.innerHTML = `<div id="badge" style="display:inline-block;margin:8px;padding:7px 14px;
        border-radius:999px;background:var(--panel);border:1.5px solid var(--ink);color:var(--accent);
        font:500 12px/1 'IBM Plex Mono',monospace;letter-spacing:.14em;text-transform:uppercase">
        Sped up: ${real} s shown in ${shown} s</div>`;
      await document.fonts.load("500 12px 'IBM Plex Mono'");
    }""", [real, shown])
    await page.locator("#badge").screenshot(path=os.path.join(OUT, ".badge.png"), omit_background=True)
    await page.close()


def shown_wait(marks):
    return WAIT_SHOWN + min(WRITE_SHOWN, marks["done"] - marks["writing"])


def video_time(t, marks):
    """Where a moment of the recording lands in the video: the model's work is shortened, the rest is real time."""
    sent, writing, done = marks["sent"], marks["writing"], marks["done"]
    write = min(WRITE_SHOWN, done - writing)
    if t <= sent:
        return t - marks["start"]
    base = sent - marks["start"]
    if t <= writing:
        return base + (t - sent) / (writing - sent) * WAIT_SHOWN
    if t <= done:
        return base + WAIT_SHOWN + (t - writing) / max(done - writing, 1e-6) * write
    return base + WAIT_SHOWN + write + (t - done)


def encode(frames, marks, ffmpeg):
    frames = [(video_time(t, marks), f) for t, f in frames if marks["start"] - 1 <= t <= marks["end"]]
    end = video_time(marks["end"], marks)
    listing = os.path.join(os.path.dirname(frames[0][1]), "frames.txt")
    with open(listing, "w", encoding="utf-8") as f:
        for (t, name), (t2, _) in zip(frames, frames[1:] + [(end, None)]):
            f.write(f"file '{name}'\nduration {max(t2 - max(t, 0), 0):.4f}\n")
        f.write(f"file '{frames[-1][1]}'\n")
    on, off = video_time(marks["sent"], marks), video_time(marks["done"], marks)
    # top right of the main column, stopping where the reader begins (it's 500 px wide), as the model may open it
    right = int((WIDTH - min(500, WIDTH * .46) - 20) * SCALE)
    graph = (f"[0:v]fps={FPS}[v];[v][1:v]overlay=x={right}-w:y={int(14 * SCALE)}:shortest=1:"
             f"enable='between(t,{on:.2f},{off:.2f})',format=yuv420p")
    inputs = [ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", listing,
              "-loop", "1", "-i", os.path.join(OUT, ".badge.png"), "-filter_complex", graph, "-t", f"{end:.2f}"]
    subprocess.run(inputs + ["-c:v", "libx264", "-preset", "slow", "-crf", "24", "-tune", "animation",
                             "-movflags", "+faststart", os.path.join(OUT, "demo.mp4")], check=True)
    subprocess.run(inputs + ["-c:v", "libvpx-vp9", "-crf", "38", "-b:v", "0", "-row-mt", "1",
                             os.path.join(OUT, "demo.webm")], check=True)
    os.remove(os.path.join(OUT, ".badge.png"))
    # the poster is the last frame, the answer with its table open: what's seen before the video loads, and
    # instead of it when the device asks for less motion
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-i", frames[-1][1], "-q:v", "3",
                    os.path.join(OUT, "demo.jpg")], check=True)
    return end


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default="http://127.0.0.1:13305/api/v1")
    # Qwen3.5-35B-A3B is faster but added wrong asides to this question (1100 mm for purge ventilation under ADO)
    ap.add_argument("--model", default="NVIDIA-Nemotron-3.5-Lightning-30B-A3B-GGUF")
    args = ap.parse_args()
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg = shutil.which("ffmpeg") or sys.exit("Needs ffmpeg (or the imageio-ffmpeg package).")
    url = start_app(args.base_url, args.model)
    seed(url)
    with tempfile.TemporaryDirectory(prefix="para-frames-") as frames_dir:
        frames, marks = asyncio.run(record(url, frames_dir))
        length = encode(frames, marks, ffmpeg)
    for name in ("demo.mp4", "demo.webm"):
        print(f"{name}: {os.path.getsize(os.path.join(OUT, name)) / 1e6:.1f} MB", flush=True)
    print(f"{len(frames)} frames, {length:.1f} s of video; the model took {marks['done'] - marks['sent']:.0f} s, "
          f"shown in {shown_wait(marks):.1f} s", flush=True)


if __name__ == "__main__":
    main()
