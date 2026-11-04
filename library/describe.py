"""Descriptions of the diagrams, written by a vision model from each image and checked by a person.

The AI that answers questions can't see the drawings, only their caption and the words on them. A description lets
it (and someone using a screen reader) know what a drawing shows. A model writes it from the image; a person checks
it on the review page; only checked descriptions go into the library, labelled as what they are.

python3 library/describe.py run [--model M] [--base-url URL] [--only ADB1,ADK] [--limit N]
python3 library/describe.py review [--port 8790]      (opens the review page)
python3 library/describe.py status
python3 library/describe.py pending [--markdown]     (the drawings that need a model or a person; no AI)
python3 library/describe.py auto [--offline]          (weekly on a computer with an engine: draft, pull request)

Each description is library/descriptions/<key>.json, the key naming the figure ("ADB1_Diagram-3.8_p40"). It records
the model, the date and the image it was written from: its SHA-256, and a 16 by 16 grey thumbnail with its size. The
library is built on GitHub and on people's own computers, whose PDF renderers can differ by a shade at the edges of
lines; the thumbnail lets the same picture match though its bytes differ, while a new crop doesn't. A description of
the same image is never written again, and a reviewed one is never overwritten. If a reviewed description's image
changes (a crop is fixed), it stays out of the library until a person approves it again against the new image.
"""
import argparse
import base64
import datetime
import hashlib
import http.server
import json
import os
import re
import sqlite3
import subprocess
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from para.llm import Client, EngineError  # noqa: E402

DESC_DIR = os.path.join(HERE, "descriptions")
LIBRARY = os.path.join(HERE, "dist", "regs.sqlite")
LABEL = "Description of the drawing (written by AI from the image, checked by a person):"
DEFAULT_ENGINE = "http://127.0.0.1:13305/api/v1"
DEFAULT_MODEL = "Qwen3.8-27B-GGUF"  # with thinking on: see docs/model-tests.md, "Describing drawings"

PROMPT = """You describe drawings from the Approved Documents (the building regulations guidance for England) for
people who can't see them, and for an AI that answers questions about them but sees only text.

Describe only what this drawing shows: what it depicts (a plan, a section, an elevation, a chart), its parts, what
each label, dimension and symbol refers to, and how the parts relate to each other. Use the labels' exact words and
figures. If the drawing has separate parts (a., b., c.), describe each in turn.

Don't state requirements or explain the regulations, and don't guess at anything you can't see. If a part is unclear,
say so. If the image is cut off or shows mostly running text rather than a drawing, say only that. Plain British
English, no headings, 60 to 200 words."""


CHECK_PROMPT = """You check descriptions of drawings from the Approved Documents (the building regulations guidance
for England) against the drawing itself. A person reviews each description before it is used; your notes tell them
where to look.

Compare the description with the image, statement by statement. Note every statement the image contradicts or
doesn't support, for example: a dimension said to measure the wrong thing; items miscounted (doors, storeys, steps);
a label attached to the wrong part; left and right, above and below confused; text that isn't on the drawing (a
heading from the page, a remark about the image). Note anything important the description leaves out. Don't note
style. Quote the description's words in each note, and say what the drawing shows instead.

Answer with JSON only: {"ok": true, "notes": []} when everything matches, otherwise
{"ok": false, "notes": ["one sentence per problem", ...]}."""


def figure_key(short, label, page):
    """A name for a figure that stays the same between builds (passage rowids don't): "ADB1_Diagram-3.8_p40"."""
    def slug(text):
        return re.sub(r"[^A-Za-z0-9.]+", "-", text).strip("-")
    return f"{slug(short)}_{slug(label)}_p{page}"


def image_hash(png):
    return hashlib.sha256(png).hexdigest()


def path_of(key):
    return os.path.join(DESC_DIR, key + ".json")


def load(key):
    try:
        with open(path_of(key), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save(rec):
    os.makedirs(DESC_DIR, exist_ok=True)
    tmp = path_of(rec["key"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, indent=1, ensure_ascii=False)
        f.write("\n")
    os.replace(tmp, path_of(rec["key"]))


def image_sig(png):
    """The image's size and a 16 by 16 grey thumbnail, for telling the same picture rendered by another version of
    the PDF renderer from a different one. None without Pillow (then only the exact hash counts)."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return None
    try:
        im = Image.open(io.BytesIO(png)).convert("L")
    except (OSError, ValueError):  # an image Pillow can't read: only the exact hash counts
        return None
    thumb = im.resize((16, 16), Image.Resampling.BOX).tobytes()
    return {"w": im.width, "h": im.height, "grey": base64.b64encode(thumb).decode()}


def same_image(rec, png):
    """Was this description written (or approved) for this image? The same bytes, or failing that the same size
    within 2 pixels and a thumbnail that differs by under 2 grey levels on average: a renderer's anti-aliasing moves
    a thumbnail by a fraction of a level, a crop moved or cut differently changes the size or the picture."""
    if rec.get("image_sha256") == image_hash(png):
        return True
    a, b = rec.get("image_sig"), image_sig(png)
    if not a or not b or abs(a["w"] - b["w"]) > 2 or abs(a["h"] - b["h"]) > 2:
        return False
    x, y = base64.b64decode(a["grey"]), base64.b64decode(b["grey"])
    return len(x) == len(y) and sum(abs(p - q) for p, q in zip(x, y)) < 2 * len(x)


def needs_writing(rec, png):
    """Should a model write this figure's description? Not if a person has reviewed it (even for a changed image:
    that's for the person to look at again), and not if the image is the one it was written from."""
    return rec is None or (not rec.get("reviewed") and not same_image(rec, png))


def stale(rec, png):
    """Reviewed, but against an image that has changed since."""
    return bool(rec and rec.get("reviewed") and not same_image(rec, png))


def usable(key, png):
    """The description the library may carry for this image: reviewed, and of this very image. Else None."""
    rec = load(key)
    if rec and rec.get("reviewed") and rec.get("description") and same_image(rec, png):
        return rec["description"].strip()
    return None


def state(rec, png):
    """Where a figure's description stands: missing, draft (written, not yet looked at), redraft (an unreviewed one
    whose image changed), reviewed, changed (reviewed against an older image) or rejected."""
    if rec is None:
        return "missing"
    if rec.get("reviewed"):
        return "reviewed" if same_image(rec, png) else "changed"
    if not same_image(rec, png):
        return "redraft"
    return "rejected" if rec.get("rejected") else "draft"


def figures_in(library=LIBRARY, only=None):
    """The diagrams in a built library: [{key, source, label, page, caption, drawing_text, png}]. Tables are left
    out: their text is already their content."""
    db = sqlite3.connect(f"file:{library}?mode=ro", uri=True)
    try:
        rows = db.execute("SELECT f.key, f.label, s.short, f.page, p.text, f.png FROM figures f "
                          "JOIN passages p ON p.id = f.passage_id JOIN sources s ON s.id = p.source_id "
                          "WHERE f.key IS NOT NULL ORDER BY f.id").fetchall()
    except sqlite3.OperationalError:
        raise SystemExit("This library has no figure keys: rebuild it with library/build.py first.")
    finally:
        db.close()
    wanted = [o.strip() for o in (only or "").split(",") if o.strip()]
    out = []
    for key, label, short, page, text, png in rows:
        if not label.startswith(("Diagram", "Figure")):
            continue
        if wanted and not any(short == w or key.startswith(w) for w in wanted):
            continue
        drawing = re.search(r"Text in the drawing: (.*)", text)
        out.append({"key": key, "source": short, "label": label, "page": page,
                    "caption": text.split("\n\n", 1)[0].strip(),
                    "drawing_text": drawing.group(1).strip() if drawing else "", "png": png})
    return out


def _ask(client, messages, think):
    """The model's answer. With thinking on, a 27B model reads what each dimension line measures far better (a check
    of ADK Diagram 1.3 found both misread dimensions with it, and invented others without), at a few minutes a
    drawing: fine overnight. The thinking needs room, hence the large limit."""
    out = []
    for ev in client.stream(messages, max_tokens=32000 if think else 1500, reasoning="on" if think else "off"):
        if ev["type"] == "content":
            out.append(ev["text"])
    return "".join(out).strip()


def write_description(client, fig, think=True):
    """One description, from the image (with the caption and the drawing's words to read small print by)."""
    url = "data:image/png;base64," + base64.b64encode(fig["png"]).decode()
    words = f"\n\nWords found on the drawing (may be out of order): {fig['drawing_text']}" if fig["drawing_text"] else ""
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": [{"type": "text", "text": f"{fig['source']}: {fig['caption']}{words}"},
                                             {"type": "image_url", "image_url": {"url": url}}]}]
    text = _ask(client, messages, think)
    if not text:
        raise EngineError("The model wrote nothing.")
    return text


def text_hash(text):
    return hashlib.sha256(text.strip().encode()).hexdigest()[:16]


def parse_check(answer):
    """The model's verdict as {"ok", "notes"}, from JSON that may be wrapped in prose or a code fence. A reply that
    isn't JSON becomes a single note, so nothing the model said is lost."""
    m = re.search(r"\{.*\}", answer, re.S)
    try:
        out = json.loads(m.group(0)) if m else None
    except ValueError:
        out = None
    if not isinstance(out, dict) or not isinstance(out.get("notes", []), list):
        return {"ok": False, "notes": [answer.strip()[:1500] or "The model gave no answer."]}
    notes = [str(n).strip() for n in out.get("notes", []) if str(n).strip()]
    return {"ok": bool(out.get("ok")) and not notes, "notes": notes}


def check_description(client, fig, description, think=True):
    """A second reading of the image against a description: notes for the person reviewing it. Advisory only: it
    never changes the description or whether it is approved."""
    url = "data:image/png;base64," + base64.b64encode(fig["png"]).decode()
    words = f"\n\nWords found on the drawing (may be out of order): {fig['drawing_text']}" if fig["drawing_text"] else ""
    messages = [{"role": "system", "content": CHECK_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": f"{fig['source']}: {fig['caption']}{words}\n\nDescription to check:\n"
                                             f"{description}"},
                    {"type": "image_url", "image_url": {"url": url}}]}]
    return parse_check(_ask(client, messages, think))


def needs_check(rec, png):
    """A description waiting for a person, whose text hasn't been checked yet (or has changed since)."""
    return (rec is not None and state(rec, png) == "draft" and rec.get("description")
            and (rec.get("check") or {}).get("text") != text_hash(rec["description"]))


def check(args):
    client = Client(args.base_url, os.environ.get("DESCRIBE_API_KEY", ""), args.model)
    done = failed = 0
    for fig in figures_in(args.library, args.only):
        if args.limit and done >= args.limit:
            break
        rec = load(fig["key"])
        if not needs_check(rec, fig["png"]):
            continue
        checked = rec["description"]
        try:
            verdict = check_description(client, fig, checked, think=not args.no_think)
        except EngineError as e:
            failed += 1
            print(f"failed   {fig['key']}: {e}", flush=True)
            continue
        rec = load(fig["key"])  # a person may have saved it on the review page meanwhile: the check is of `checked`
        rec["check"] = {**verdict, "model": args.model, "date": datetime.date.today().isoformat(),
                        "text": text_hash(checked)}
        save(rec)
        done += 1
        print(f"checked  {fig['key']}: " + ("no problems found" if verdict["ok"] else f"{len(verdict['notes'])} notes"),
              flush=True)
    print(f"\n{done} checked, {failed} failed")


def run(args):
    client = Client(args.base_url, os.environ.get("DESCRIBE_API_KEY", ""), args.model)
    done = skipped = failed = 0
    for fig in figures_in(args.library, args.only):
        if args.limit and done >= args.limit:
            break
        h = image_hash(fig["png"])
        rec = load(fig["key"])
        if rec and rec.get("image_sha256") == h and "image_sig" not in rec and image_sig(fig["png"]):
            rec["image_sig"] = image_sig(fig["png"])  # written before thumbnails were kept: the very same image
            save(rec)
        if not needs_writing(rec, fig["png"]):
            skipped += 1
            if stale(rec, fig["png"]):
                print(f"changed  {fig['key']}: reviewed against an older image; review it again", flush=True)
            continue
        try:
            text = write_description(client, fig, think=not args.no_think)
        except EngineError as e:
            failed += 1
            print(f"failed   {fig['key']}: {e}", flush=True)
            continue
        save({"key": fig["key"], "source": fig["source"], "label": fig["label"], "page": fig["page"],
              "caption": fig["caption"], "model": args.model, "date": datetime.date.today().isoformat(),
              "image_sha256": h, "image_sig": image_sig(fig["png"]), "reviewed": False, "description": text})
        done += 1
        print(f"written  {fig['key']} ({len(text.split())} words)", flush=True)
    print(f"\n{done} written, {skipped} already done, {failed} failed")


NAMES = {"missing": "missing", "redraft": "image changed, not reviewed", "draft": "to review",
         "changed": "changed since review", "reviewed": "reviewed", "rejected": "rejected"}


def status(args):
    figs = figures_in(args.library)
    counts = dict.fromkeys(NAMES, 0)
    for fig in figs:
        counts[state(load(fig["key"]), fig["png"])] += 1
    print(f"{len(figs)} diagrams: " + ", ".join(f"{v} {NAMES[k]}" for k, v in counts.items() if v or k != "redraft"))


def pending(figs):
    """The figures that need a model (missing, redraft) or a person (draft, changed), with where each stands."""
    out = []
    for fig in figs:
        st = state(load(fig["key"]), fig["png"])
        if st in ("missing", "redraft", "draft", "changed"):
            out.append({"key": fig["key"], "source": fig["source"], "label": fig["label"], "page": fig["page"],
                        "state": st})
    return out


def pending_markdown(items, total):
    """The body of the "descriptions" issue: what needs drafting and what needs a person, document by document."""
    if not items:
        return f"Every one of the {total} diagrams has a description a person has checked, or rejected.\n"
    groups = [("changed", "Reviewed, but the image has changed since: check again on the review page"),
              ("draft", "Drafted, waiting for a person on the review page"),
              ("redraft", "Drafted, but the image has changed since: to draft again"),
              ("missing", "No description yet: to draft")]
    lines = [f"{len(items)} of the {total} diagrams in the library need a description drafted or checked.", "",
             "Drafting: `python3 library/describe.py auto` on a computer with a vision model (or the hosted model in "
             "the *Drawing descriptions* workflow, if set up). Checking: `python3 library/describe.py review`. "
             "Only descriptions a person approves go into the library.", ""]
    for st, title in groups:
        these = [it for it in items if it["state"] == st]
        if not these:
            continue
        lines += [f"### {title} ({len(these)})", ""]
        by_doc = {}
        for it in these:
            by_doc.setdefault(it["source"], []).append(f"{it['label']} (p {it['page']})")
        lines += [f"- **{doc}**: " + ", ".join(labels) for doc, labels in by_doc.items()] + [""]
    return "\n".join(lines)


def show_pending(args):
    figs = figures_in(args.library)
    items = pending(figs)
    if args.markdown:
        print(pending_markdown(items, len(figs)))
    else:
        for it in items:
            print(f"{NAMES[it['state']]:<28} {it['key']}")
        print(f"\n{len(items)} of {len(figs)} diagrams need drafting or checking")


# ----- review page -----

def review_items(figs):
    """The descriptions written so far, for the review page (figs: {key: figure})."""
    items = []
    for key, fig in figs.items():
        rec = load(key)
        if rec is not None:
            chk = rec.get("check")
            items.append({**rec, "stale": stale(rec, fig["png"]),
                          "check_current": bool(chk and chk.get("text") == text_hash(rec.get("description") or ""))})
    return items


def approve(fig, description, approved=True, rejected=False):
    """A person checked (and maybe corrected) a description against the figure's current image, or rejected it (a
    crop that is cut short, a description that can't be put right). A rejected one stays out of the library and
    isn't written again until the image changes."""
    rec = load(fig["key"])
    if rec is None:
        return None
    text = description.strip() if description is not None else rec["description"]
    if text != rec["description"]:
        rec.setdefault("model_description", rec["description"])  # what the model wrote, kept for the record
        rec["description"] = text
    rec["reviewed"] = bool(approved) and not rejected
    rec["rejected"] = bool(rejected)
    if approved or rejected:
        rec["image_sha256"], rec["image_sig"] = image_hash(fig["png"]), image_sig(fig["png"])
        rec["reviewed_date"] = datetime.date.today().isoformat()
    save(rec)
    return rec


class ReviewHandler(http.server.BaseHTTPRequestHandler):
    figs = {}  # key: figure, read once when the page starts

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path, _, query = self.path.partition("?")
        if path == "/":
            with open(os.path.join(HERE, "review.html"), "rb") as f:
                return self._send(200, f.read(), "text/html; charset=utf-8")
        if path.startswith("/fonts/") and re.fullmatch(r"/fonts/[\w.-]+\.woff2", path):
            font = os.path.join(os.path.dirname(HERE), "para", "static", path.lstrip("/"))
            if os.path.exists(font):
                with open(font, "rb") as f:
                    return self._send(200, f.read(), "font/woff2")
        if path == "/api/items":
            return self._send(200, review_items(self.figs))
        if path == "/image":
            key = dict(p.split("=", 1) for p in query.split("&") if "=" in p).get("key", "")
            if key in self.figs:
                return self._send(200, self.figs[key]["png"], "image/png")
        return self._send(404, {"error": "Not found"})

    def do_POST(self):
        host = self.headers.get("Host", "")
        if self.headers.get("Origin") not in (f"http://{host}",):  # only the review page itself may save
            return self._send(403, {"error": "Forbidden"})
        if self.path != "/api/save":
            return self._send(404, {"error": "Not found"})
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        fig = self.figs.get(str(body.get("key", "")))
        rec = approve(fig, body.get("description"), approved=bool(body.get("approve")),
                      rejected=bool(body.get("reject"))) if fig else None
        return self._send(200 if rec else 404, rec or {"error": "No such description"})


def review(args):
    ReviewHandler.figs = {f["key"]: f for f in figures_in(args.library)}
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), ReviewHandler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/"
    print(f"Review page: {url}  (Ctrl+C to stop)", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def auto(args):
    """Overnight on a computer with a vision model: draft and check descriptions for the drawings that need them, on a
    branch of the latest main, and open a pull request for a person to review on the review page. Works in a git
    worktree of its own, so the checkout it's started from is left as it is. Never approves anything."""
    root = os.path.dirname(HERE)
    client = Client(args.base_url, os.environ.get("DESCRIBE_API_KEY", ""), args.model)
    try:
        if args.model not in client.models(timeout=10):
            raise SystemExit(f"The engine at {args.base_url} has no model {args.model!r}.")
    except EngineError as e:
        raise SystemExit(f"No engine at {args.base_url}: {e}")
    _git("fetch", "-q", "origin", cwd=root)
    open_prs = subprocess.run(["gh", "pr", "list", "--state", "open", "--json", "headRefName", "-q",
                               '.[] | select(.headRefName | startswith("descriptions/")) | .headRefName'],
                              cwd=root, capture_output=True, text=True).stdout.split()
    if open_prs:
        print(f"A pull request of drafts is still open ({', '.join(open_prs)}): review and merge it first, so the same "
              "drawings aren't drafted twice.")
        return
    date = datetime.date.today().isoformat()
    branch, wt = f"descriptions/{date}", os.path.join(root, ".worktrees", f"descriptions-{date}")
    _git("worktree", "add", "-q", "-B", branch, wt, "origin/main", cwd=root)
    try:
        cache = os.path.join(root, "library", "cache")
        if os.path.isdir(cache) and not os.path.exists(os.path.join(wt, "library", "cache")):
            os.symlink(cache, os.path.join(wt, "library", "cache"))  # the PDFs already downloaded
        py = [sys.executable, "library/build.py"] + (["--offline"] if args.offline else [])
        subprocess.run(py, cwd=wt, check=True)
        model = (["--base-url", args.base_url, "--model", args.model] + (["--limit", str(args.limit)] if args.limit else [])
                 + (["--no-think"] if args.no_think else []))
        subprocess.run([sys.executable, "library/describe.py", "run", *model], cwd=wt, check=True)
        subprocess.run([sys.executable, "library/describe.py", "check", *model], cwd=wt, check=True)
        _git("add", "library/descriptions", cwd=wt)
        changed = _git("diff", "--cached", "--name-only", cwd=wt).split()
        if not changed:
            print("Nothing to draft or check.")
            return
        _git("commit", "-q", "-m", f"Descriptions: {len(changed)} drafted or checked by {args.model}, to review", cwd=wt)
        _git("push", "-q", "-u", "origin", branch, cwd=wt)
        body = (f"Drafted and checked overnight by `{args.model}` with `describe.py auto`. Nothing here is approved: "
                "check each one against its drawing on the review page (`python3 library/describe.py review`), where "
                "the AI check's notes show which parts to look at first. Only approved descriptions go into the "
                "library.\n\n" + "\n".join(f"- `{os.path.basename(c)[:-5]}`" for c in changed))
        subprocess.run(["gh", "pr", "create", "--base", "main", "--head", branch, "--title",
                        f"Descriptions to review: {len(changed)} drawings ({date})", "--body", body], cwd=wt, check=True)
    finally:
        subprocess.run(["git", "worktree", "remove", "--force", wt], cwd=root, capture_output=True)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="describe.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", default=LIBRARY)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="write descriptions for diagrams that need one")
    r.add_argument("--base-url", default=DEFAULT_ENGINE)
    r.add_argument("--model", default=DEFAULT_MODEL, help="a model that reads images")
    r.add_argument("--only", help="documents or key prefixes, comma-separated (ADB1,ADK)")
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--no-think", action="store_true", help="answer straight away (much faster, far less accurate)")
    v = sub.add_parser("review", help="open the review page")
    v.add_argument("--port", type=int, default=8790)
    v.add_argument("--no-browser", action="store_true")
    sub.add_parser("status", help="how many diagrams have descriptions, and how many are reviewed")
    c = sub.add_parser("check", help="a second model reading: notes on drafts for the person reviewing them")
    for p in (c,):
        p.add_argument("--base-url", default=DEFAULT_ENGINE)
        p.add_argument("--model", default=DEFAULT_MODEL, help="a model that reads images")
        p.add_argument("--only", help="documents or key prefixes, comma-separated (ADB1,ADK)")
        p.add_argument("--limit", type=int, default=0)
        p.add_argument("--no-think", action="store_true", help="answer straight away (much faster, far less accurate)")
    n = sub.add_parser("pending", help="the drawings that need drafting or checking (no AI)")
    n.add_argument("--markdown", action="store_true", help="as the body of the GitHub issue")
    a = sub.add_parser("auto", help="draft and check on a branch of main, and open a pull request")
    a.add_argument("--base-url", default=DEFAULT_ENGINE)
    a.add_argument("--model", default=DEFAULT_MODEL, help="a model that reads images")
    a.add_argument("--limit", type=int, default=40, help="drawings to draft, and to check, in one night")
    a.add_argument("--no-think", action="store_true", help="answer straight away (much faster, far less accurate)")
    a.add_argument("--offline", action="store_true", help="build the library from library/cache")
    args = ap.parse_args(argv)
    {"run": run, "review": review, "status": status, "check": check, "pending": show_pending,
     "auto": auto}[args.cmd](args)


if __name__ == "__main__":
    main()
