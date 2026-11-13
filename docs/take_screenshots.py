"""Takes the screenshots in docs/screenshots from the running interface.

    uv run --with playwright python3 docs/take_screenshots.py APP_DIR HOME_DIR [--library FILE] [--only NAME…]

APP_DIR is a throwaway app folder that already has an engine set up and chats in it: the answers shown are real
ones, whatever a real model said (ask the test questions first: tests/ask_models.py against an app started with
PARA_APP_DIR=APP_DIR HOME=HOME_DIR). Never the person's own app folder. The first-run screens are taken from a second,
empty folder made here. Nothing is staged in the page: each state is reached by clicking as a person would. A state
that the chats don't hold (no answer with a figure note, say) is skipped, and said so.

Uses the system Chromium if there is one (as docs/record_demo.py does), otherwise Playwright's own.
"""
import argparse
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "screenshots")
WIDE = {"viewport": {"width": 1280, "height": 800}, "device_scale_factor": 2}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2}
FILES = {"BC comments 2026-10-14.txt": "Building control comments, 14 October 2026\n\n1. Confirm the guarding height "
                                       "to the new landing.\n2. Provide details of the fire doors to the stair.\n",
         "Fire strategy rev C.txt": "Fire strategy, revision C\n\nThe loft bedroom is served by the existing stair, "
                                    "enclosed with fire-resisting construction and FD20 doors.\n"}


def chromium():
    for path in (os.environ.get("PARA_CHROMIUM"), "/usr/lib/chromium/chromium", shutil.which("chromium"),
                 shutil.which("google-chrome")):
        if path and os.path.isfile(path):
            return path
    return None


def start(app_dir, home, library):
    """The app as its own process on a free port; returns (process, address)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = dict(os.environ, PARA_APP_DIR=app_dir, HOME=home, PARA_LIBRARY=library, BROWSER="/bin/true",
               PARA_UPDATE_REPO="para-test/none")  # no update check: the screens shouldn't depend on the day's release
    proc = subprocess.Popen([sys.executable, "-m", "para", "--browser", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f"http://127.0.0.1:{port}/"
    for _ in range(100):
        try:
            urllib.request.urlopen(url, timeout=1).close()
            return proc, url
        except OSError:
            time.sleep(0.1)
    raise SystemExit("The app didn't start.")


def stop(proc):
    proc.terminate()  # a normal stop: the app stops its engine, if it started one
    proc.wait(timeout=30)


def chats(url):
    with urllib.request.urlopen(url + "api/state", timeout=30) as r:
        listed = json.load(r)["chats"]
    out = []
    for c in listed:
        with urllib.request.urlopen(f"{url}api/chat?id={c['id']}", timeout=30) as r:
            out.append(json.load(r))
    return out


def pick(all_chats, want):
    """The first chat whose last turn passes want(turn)."""
    for c in all_chats:
        turns = c.get("turns") or []
        if turns and want(turns[-1]):
            return c
    return None


async def shots(url, first_url, home, only):
    from playwright.async_api import async_playwright
    taken, skipped = [], []

    def wanted(name):
        return not only or name in only

    async def save(page, name):
        await page.wait_for_timeout(350)
        await page.screenshot(path=os.path.join(OUT, name + ".png"))
        taken.append(name)

    async def open_chat(page, chat):
        await page.goto(url)
        await page.wait_for_selector("#chatList", state="attached")  # in a drawer at phone width
        await page.evaluate("id => openChat(id)", chat["id"])
        await page.wait_for_selector(".msg.assistant .answer")
        await page.wait_for_timeout(400)

    async def cite(page, text):
        """Click the first citation in the answer whose label has text; True if the reader opened."""
        chips = page.locator(".msg.assistant .answer .cite").filter(has_text=text)
        if not await chips.count():
            return False
        await chips.first.scroll_into_view_if_needed()
        await chips.first.click()
        await page.wait_for_selector("#reader:not([hidden]) .passage")
        await page.wait_for_timeout(700)
        return True

    all_chats = chats(url)
    last = {c["id"]: (c.get("turns") or [{}])[-1] for c in all_chats}

    def cited(c, text):
        return any(text in f"{v.get('source', '')} {v.get('ref', '')}" for v in (last[c["id"]].get("cites") or {}).values())

    stair = next((c for c in all_chats if "private stair" in c["title"] and cited(c, "ADK Table 1.1")), None)
    balcony = next((c for c in all_chats if "balcony" in c["title"].lower() and cited(c, "Diagram 3.1")), None) or \
        next((c for c in all_chats if cited(c, "Diagram")), None)
    extract = pick(all_chats, lambda t: any(v.get("read") is False for v in (t.get("cites") or {}).values()))
    flagged = pick(all_chats, lambda t: t.get("figures"))

    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path=chromium())

        # ----- first run: an empty app folder -----
        page = await browser.new_page(**WIDE)
        if wanted("setup-engine") or wanted("setup-builtin"):
            await page.goto(first_url)
            await page.wait_for_selector("#dlg-settings[open]")
            await page.wait_for_timeout(1500)  # engines on this computer are looked for
            if wanted("setup-engine"):
                await save(page, "setup-engine")
            await page.locator('input[type=radio][value="bundled"]').check()
            await page.wait_for_selector("#modelCards >> text=Download")
            # the list starts below the fold: show it from the line about this computer, with the models' scores
            await page.evaluate("document.getElementById('machineInfo').scrollIntoView()")
            await save(page, "setup-builtin")
            if wanted("model-help"):
                await page.click("#modelHelp")
                await page.wait_for_selector("#dlg-model-help[open]")
                await save(page, "model-help")
                await page.click("#dlg-model-help button")

        # ----- the app in use -----
        await page.goto(url)
        await page.wait_for_selector("#welcome")
        await page.locator('[data-theme-pick="light"]').click()  # whatever this computer's own setting is
        if wanted("welcome"):
            await page.wait_for_timeout(600)
            await save(page, "welcome")
        if stair:
            if wanted("answer"):
                await open_chat(page, stair)
                await page.locator(".msg.assistant details.activity summary").last.click()
                await page.evaluate("const q = document.querySelector('.msg.user'); q.style.scrollMarginTop = '48px'; q.scrollIntoView()")  # the question at the top
                await save(page, "answer")
            if wanted("answer-actions"):
                await open_chat(page, stair)
                await page.locator(".msg.assistant .answer-actions").last.scroll_into_view_if_needed()
                await page.evaluate("document.querySelector('#messages').scrollBy(0, 400)")
                await save(page, "answer-actions")
            if wanted("answer-reader") or wanted("answer-reader-dark"):
                await open_chat(page, stair)
                if await cite(page, "Table 1.1"):
                    await save(page, "answer-reader")
                    await page.locator('[data-theme-pick="dark"]').click()
                    await save(page, "answer-reader-dark")
                    await page.locator('[data-theme-pick="light"]').click()
        else:
            skipped.append("answer, answer-actions, answer-reader: no chat about the private stair citing ADK Table 1.1")
        if balcony and (wanted("reader-diagram") or wanted("reader-diagram-zoom")):
            await open_chat(page, balcony)
            if await cite(page, "Diagram"):
                await save(page, "reader-diagram")
                await page.locator("#reader .passage.focus img.figure").first.click()
                await page.wait_for_selector("#dlg-figure[open]")
                await save(page, "reader-diagram-zoom")
                await page.keyboard.press("Escape")
        elif not balcony:
            skipped.append("reader-diagram: no answer citing a diagram")
        if wanted("extract-only"):
            if extract:
                await open_chat(page, extract)
                await page.locator(".msg.assistant .note").filter(has_text="extract").last.scroll_into_view_if_needed()
                await page.evaluate("document.querySelector('#messages').scrollBy(0, 300)")
                await save(page, "extract-only")
            else:
                skipped.append("extract-only: no answer with a citation made from a search extract")
        if wanted("figure-note"):
            if flagged:
                await open_chat(page, flagged)
                await page.locator(".msg.assistant .note").filter(has_text="Not found in the passage").last \
                    .scroll_into_view_if_needed()
                await page.evaluate("document.querySelector('#messages').scrollBy(0, 300)")
                await save(page, "figure-note")
            else:
                skipped.append("figure-note: no answer with a figure the cited passage doesn't hold")
        if wanted("find-in-chats"):
            await page.goto(url)
            await page.wait_for_selector("#welcome")
            await page.locator("#chatFind").fill("fire door")
            await page.wait_for_timeout(1200)
            await save(page, "find-in-chats")
            await page.locator("#chatFind").fill("")
        if wanted("library") or wanted("library-search") or wanted("library-changes"):
            target = stair or (all_chats[0] if all_chats else None)
            if target:
                await open_chat(page, target)
            await page.locator('[data-open="library"]').click()
            await page.wait_for_selector("#dlg-library[open]")
            if wanted("library"):
                await save(page, "library")
            changes = page.locator("#dlg-library details.lib-changes summary")
            if wanted("library-changes"):
                if await changes.count():
                    await changes.click()
                    await save(page, "library-changes")
                    await changes.click()
                else:
                    skipped.append("library-changes: this app folder has no comparison with an earlier edition")
            if wanted("library-search"):
                await page.locator("#libSearch").fill("travel distance flat")
                await page.wait_for_selector("#libResults [data-hit]")
                await save(page, "library-search")
            await page.locator("#dlg-library .modal-head button.icon").click()
        if wanted("files-permission") or wanted("files"):
            paths = []
            for name, text in FILES.items():
                paths.append(os.path.join(home, name))  # beside the files folder, to be added through the window
                with open(paths[-1], "w", encoding="utf-8") as f:
                    f.write(text)
            await page.locator('[data-open="files"]').click()
            await page.wait_for_selector("#dlg-files[open]")
            await page.locator("#filePick").set_input_files(paths)
            if await page.locator("#dlg-permission[open]").count() or \
                    await page.wait_for_selector("#dlg-permission[open]", timeout=3000):
                await save(page, "files-permission")
                await page.locator("#permAgree").check()
                await page.locator("#permOk").click()
            await page.wait_for_selector("#fileList [data-delfile]")
            await page.wait_for_timeout(800)
            await save(page, "files")
            await page.locator("#dlg-files .modal-head button.icon").click()
        await page.close()

        if wanted("narrow") and stair:
            phone = await browser.new_page(**PHONE)
            await open_chat(phone, stair)
            await save(phone, "narrow")
            await phone.close()
        await browser.close()
    return taken, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("app_dir")
    ap.add_argument("home")
    ap.add_argument("--library", default=os.path.join(ROOT, "library", "dist", "regs.sqlite"))
    ap.add_argument("--only", nargs="*", default=[])
    args = ap.parse_args()
    real = os.path.expanduser("~")
    if os.path.abspath(args.home) == real or os.path.abspath(args.app_dir).startswith(os.path.join(real, ".")):
        raise SystemExit("Give throwaway folders, not your own home or app folder.")
    empty = tempfile.mkdtemp(prefix="para-shots-")
    first, first_url = start(os.path.join(empty, "app"), empty, args.library)
    app, url = start(args.app_dir, args.home, args.library)
    try:
        taken, skipped = asyncio.run(shots(url, first_url, args.home, set(args.only)))
    finally:
        stop(app)
        stop(first)
        shutil.rmtree(empty, ignore_errors=True)
    print("Taken:", ", ".join(taken))
    for line in skipped:
        print("Skipped", line)


if __name__ == "__main__":
    main()
