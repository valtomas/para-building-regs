"""Descriptions of the diagrams, written by a vision model from each image and checked by a person.

The AI that answers questions can't see the drawings, only their caption and the words on them. A description lets
it (and someone using a screen reader) know what a drawing shows. A model writes it from the image; a person checks
it on the review page; only checked descriptions go into the library, labelled as what they are.

python3 library/describe.py run [--model M] [--base-url URL] [--only ADB1,ADK] [--limit N]
python3 library/describe.py review [--port 8790]      (opens the review page)
python3 library/describe.py status

Each description is library/descriptions/<key>.json, the key naming the figure ("ADB1_Diagram-3.8_p40"). It records
the model, the date and the SHA-256 of the image it was written from. A description whose image hash is unchanged is
never written again, and a reviewed one is never overwritten. If a reviewed description's image changes (a crop is
fixed), it stays out of the library until a person approves it again against the new image.
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
import sys
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from para.llm import Client, EngineError  # noqa: E402

DESC_DIR = os.path.join(HERE, "descriptions")
LIBRARY = os.path.join(HERE, "dist", "regs.sqlite")
LABEL = "Description of the drawing (written by AI from the image, checked by a person):"
DEFAULT_ENGINE = "http://127.0.0.1:13305/api/v1"
DEFAULT_MODEL = "Gemma-4-26B-A4B-it-GGUF"

PROMPT = """You describe drawings from the Approved Documents (the building regulations guidance for England) for
people who can't see them, and for an AI that answers questions about them but sees only text.

Describe only what this drawing shows: what it depicts (a plan, a section, an elevation, a chart), its parts, what
each label, dimension and symbol refers to, and how the parts relate to each other. Use the labels' exact words and
figures. If the drawing has separate parts (a., b., c.), describe each in turn.

Don't state requirements or explain the regulations, and don't guess at anything you can't see. If a part is unclear,
say so. Plain British English, no headings, 60 to 200 words."""


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


def needs_writing(rec, png_hash):
    """Should a model write this figure's description? Not if a person has reviewed it (even for a changed image:
    that's for the person to look at again), and not if the image is the one it was written from."""
    return rec is None or (not rec.get("reviewed") and rec.get("image_sha256") != png_hash)


def stale(rec, png_hash):
    """Reviewed, but against an image that has changed since."""
    return bool(rec and rec.get("reviewed") and rec.get("image_sha256") != png_hash)


def usable(key, png):
    """The description the library may carry for this image: reviewed, and of this very image. Else None."""
    rec = load(key)
    if rec and rec.get("reviewed") and rec.get("image_sha256") == image_hash(png) and rec.get("description"):
        return rec["description"].strip()
    return None


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


def write_description(client, fig):
    """One description, from the image (with the caption and the drawing's words to read small print by)."""
    url = "data:image/png;base64," + base64.b64encode(fig["png"]).decode()
    words = f"\n\nWords found on the drawing (may be out of order): {fig['drawing_text']}" if fig["drawing_text"] else ""
    messages = [{"role": "system", "content": PROMPT},
                {"role": "user", "content": [{"type": "text", "text": f"{fig['source']}: {fig['caption']}{words}"},
                                             {"type": "image_url", "image_url": {"url": url}}]}]
    out = []
    for ev in client.stream(messages, max_tokens=1200, reasoning="off"):
        if ev["type"] == "content":
            out.append(ev["text"])
    text = "".join(out).strip()
    if not text:
        raise EngineError("The model wrote nothing.")
    return text


def run(args):
    client = Client(args.base_url, "", args.model)
    done = skipped = failed = 0
    for fig in figures_in(args.library, args.only):
        if args.limit and done >= args.limit:
            break
        h = image_hash(fig["png"])
        rec = load(fig["key"])
        if not needs_writing(rec, h):
            skipped += 1
            if stale(rec, h):
                print(f"changed  {fig['key']}: reviewed against an older image; review it again", flush=True)
            continue
        try:
            text = write_description(client, fig)
        except EngineError as e:
            failed += 1
            print(f"failed   {fig['key']}: {e}", flush=True)
            continue
        save({"key": fig["key"], "source": fig["source"], "label": fig["label"], "page": fig["page"],
              "caption": fig["caption"], "model": args.model, "date": datetime.date.today().isoformat(),
              "image_sha256": h, "reviewed": False, "description": text})
        done += 1
        print(f"written  {fig['key']} ({len(text.split())} words)", flush=True)
    print(f"\n{done} written, {skipped} already done, {failed} failed")


def status(args):
    figs = figures_in(args.library)
    counts = {"missing": 0, "to review": 0, "reviewed": 0, "changed since review": 0}
    for fig in figs:
        rec, h = load(fig["key"]), image_hash(fig["png"])
        key = ("missing" if rec is None else "changed since review" if stale(rec, h)
               else "reviewed" if rec.get("reviewed") else "to review")
        counts[key] += 1
    print(f"{len(figs)} diagrams: " + ", ".join(f"{v} {k}" for k, v in counts.items()))


# ----- review page -----

def review_items(figs):
    """The descriptions written so far, for the review page (figs: {key: figure})."""
    items = []
    for key, fig in figs.items():
        rec = load(key)
        if rec is not None:
            items.append({**rec, "stale": stale(rec, image_hash(fig["png"]))})
    return items


def approve(fig, description, approved=True):
    """A person checked (and maybe corrected) a description against the figure's current image."""
    rec = load(fig["key"])
    if rec is None:
        return None
    text = description.strip() if description is not None else rec["description"]
    if text != rec["description"]:
        rec.setdefault("model_description", rec["description"])  # what the model wrote, kept for the record
        rec["description"] = text
    rec["reviewed"] = bool(approved)
    if approved:
        rec["image_sha256"] = image_hash(fig["png"])
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
        rec = approve(fig, body.get("description"), approved=bool(body.get("approve"))) if fig else None
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


def main(argv=None):
    ap = argparse.ArgumentParser(prog="describe.py", description=__doc__.split("\n\n")[0])
    ap.add_argument("--library", default=LIBRARY)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="write descriptions for diagrams that need one")
    r.add_argument("--base-url", default=DEFAULT_ENGINE)
    r.add_argument("--model", default=DEFAULT_MODEL, help="a model that reads images")
    r.add_argument("--only", help="documents or key prefixes, comma-separated (ADB1,ADK)")
    r.add_argument("--limit", type=int, default=0)
    v = sub.add_parser("review", help="open the review page")
    v.add_argument("--port", type=int, default=8790)
    v.add_argument("--no-browser", action="store_true")
    sub.add_parser("status", help="how many diagrams have descriptions, and how many are reviewed")
    args = ap.parse_args(argv)
    {"run": run, "review": review, "status": status}[args.cmd](args)


if __name__ == "__main__":
    main()
