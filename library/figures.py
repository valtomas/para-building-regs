"""Diagrams and tables in the Approved Document PDFs, as images.

The Approved Documents draw their diagrams as vector graphics, so text extraction only finds the caption and the
labels scattered around the drawing ("Kitchen | Bath | x. 9m ma"), and those labels land in the middle of the
running text. This module uses pdftotext's word positions to find each drawing's area from its caption, renders that
area to a PNG with pdftoppm, and returns the label words so the builder can take them out of the running text and
keep them, in reading order, with the diagram's own passage.
"""
import collections
import html
import re
import subprocess

CAPTION = re.compile(r"^(Diagram|Figure|Table)\s+(\d{1,2}(?:\.\d{1,3})?[a-z]?|[A-Z]\d{1,2})\b(.*)$")
LINE = re.compile(r'<line xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</line>', re.S)
BLOCK = re.compile(r"<block [^>]*>(.*?)</block>", re.S)
PAGE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.S)
WORD = re.compile(r"<word [^>]*>([^<]*)</word>")
NOTE = re.compile(r"^\[?\(?(NOTES?|Notes?|Key)\b")
HEADING = re.compile(r"^(Section|Appendix|Part|Requirement)\s+[\dA-Z]{1,3}\b")
PARA_START = re.compile(r"^(\d{1,2}\.\d{1,3}|[a-z]\.|\(?[ivx]+\)|[A-Z]\d{1,2})\s+[A-Za-z]")
DPI = 110


class Line:
    __slots__ = ("x0", "y0", "x1", "y1", "text", "block")

    def __init__(self, x0, y0, x1, y1, text, block):
        self.x0, self.y0, self.x1, self.y1, self.text, self.block = x0, y0, x1, y1, text, block


def page_lines(pdf):
    """[(width, height, [Line])] for every page, from pdftotext -bbox-layout."""
    raw = subprocess.run(["pdftotext", "-bbox-layout", "-enc", "UTF-8", pdf, "-"], capture_output=True,
                         check=True).stdout.decode("utf-8", "replace")
    pages = []
    for pm in PAGE.finditer(raw):
        w, h, body = float(pm.group(1)), float(pm.group(2)), pm.group(3)
        lines = []
        for bno, bm in enumerate(BLOCK.finditer(body)):
            for lm in LINE.finditer(bm.group(1)):
                text = " ".join(html.unescape(t) for t in WORD.findall(lm.group(5))).strip()
                if text:
                    lines.append(Line(*(float(lm.group(i)) for i in range(1, 5)), text, bno))
        pages.append((w, h, sorted(lines, key=lambda ln: (ln.y0, ln.x0))))
    return pages


def _is_caption(ln):
    m = CAPTION.match(ln.text)
    if not m:
        return None
    rest = m.group(3).strip()
    # "Diagram 4.2 or 4.3." wrapped in a sentence, or a contents entry ending in a page number
    # not "Diagram 1.12 (for buildings other…" continuing a sentence, nor an index or contents entry
    if rest and (rest[0].islower() or rest[0] in ").,;:" or re.match(r"\(\s*[a-z]", rest)
                 or re.search(r"\.{3,}|\s\d{1,3}$|;|\d\.\d+,", rest)):
        return None
    if rest.lower().startswith(("continued", "(continued")):
        return f"{m.group(1)} {m.group(2)}", True
    return f"{m.group(1)} {m.group(2)}", False


def _is_body(ln, column, left=None):
    """Running text, as opposed to a label in a drawing: a long line of words, or the start of a paragraph (at the
    left margin: a table cell such as "1.6 or Window Energy Rating" isn't paragraph 1.6)."""
    words = len(re.findall(r"[A-Za-z]{2,}", ln.text))
    at_margin = left is None or ln.x0 <= left + 40
    return (ln.x1 - ln.x0 >= 0.5 * column and words >= 7) or bool(PARA_START.match(ln.text) and words >= 5
                                                                    and at_margin)


def find(pdf, skip_pages=()):
    """Figures in a PDF: [{label, continued, page (1-based), box (x0, y0, x1, y1) in points, labels: [text],
    words: Counter of words inside the drawing}]."""
    pages = page_lines(pdf)
    found = []
    votes = collections.Counter()
    for pno, (w, h, lines) in enumerate(pages, start=1):
        if pno in skip_pages or not lines:
            continue
        column = max((ln.x1 for ln in lines), default=w) - min((ln.x0 for ln in lines), default=0)
        xs = sorted(ln.x0 for ln in lines)
        left = xs[len(xs) // 10]
        top_limit, bottom_limit = h * 0.08, h * 0.93
        body_or_caption = [ln for ln in lines if (_is_body(ln, column, left) or _is_caption(ln))
                           and top_limit < ln.y0 < bottom_limit and not NOTE.match(ln.text)]
        # Going down from a caption (tables, and diagrams captioned above), long lines may be table rows or notes,
        # so only a numbered paragraph, a heading or another caption ends the figure.
        starts = [ln for ln in lines if (_is_caption(ln) or HEADING.match(ln.text) or
                                         (PARA_START.match(ln.text) and _is_body(ln, column, left)))
                  and top_limit < ln.y0 < bottom_limit]
        for i, ln in enumerate(lines):
            cap = _is_caption(ln)
            if not cap or not (top_limit < ln.y0 < bottom_limit):
                continue
            # the caption's title may wrap onto the next line(s) of the same block
            cap_lines = [ln] + [x for x in lines if x.block == ln.block and x is not ln and 0 < x.y0 - ln.y0 < 40]
            cap_bottom = max(x.y1 for x in cap_lines)
            above = max([x.y1 for x in body_or_caption if x.y1 <= ln.y0 - 1 and x not in cap_lines]
                        or [top_limit])
            below = min([x.y0 for x in body_or_caption if x.y0 >= cap_bottom + 1 and x not in cap_lines]
                        or [bottom_limit])
            below_strict = min([x.y0 for x in starts if x.y0 >= cap_bottom + 1 and x not in cap_lines]
                               or [bottom_limit])
            gap_above, gap_below = ln.y0 - above, below - cap_bottom
            found.append({"label": cap[0], "continued": cap[1], "page": pno, "kind": cap[0].split()[0],
                          "cap": (ln.y0, cap_bottom), "above": above, "below": below_strict, "w": w, "h": h,
                          "lines": lines, "cap_lines": cap_lines})
            # Tables have their caption above; whether diagram captions sit above or below their drawing differs
            # between documents, so each document votes with the captions where only one side has room.
            if cap[0].startswith(("Diagram", "Figure")):
                if gap_above > 60 and gap_below < 25:
                    votes["below"] += 1
                elif gap_below > 60 and gap_above < 25:
                    votes["above"] += 1
    caption_below = votes["below"] > votes["above"]
    out = []
    for f in found:
        if f["kind"] == "Table" or not caption_below:
            y0, y1 = f["cap"][0] - 4, f["below"] - 3  # caption, then the drawing or table under it
        else:
            y0, y1 = f["above"] + 3, f["cap"][1] + 4  # the drawing, then its caption
        if y1 - y0 < 45:
            continue
        inside = [x for x in f["lines"] if x.y0 >= y0 - 1 and x.y1 <= y1 + 1]
        xs = [x.x0 for x in inside] + [x.x1 for x in inside]
        x0 = max(0, min(xs + [f["w"] * 0.08]) - 8)
        x1 = min(f["w"], max(xs + [f["w"] * 0.92]) + 8)
        labels = _labels([x for x in inside if x not in f["cap_lines"]])
        words = collections.Counter(w for x in inside if x not in f["cap_lines"] for w in x.text.split())
        out.append({"label": f["label"], "continued": f["continued"], "kind": f["kind"], "page": f["page"],
                    "box": (x0, max(0, y0), x1, min(f["h"], y1)), "labels": labels, "words": words,
                    "caption": " ".join(x.text for x in f["cap_lines"])})
    return out


def _labels(lines):
    """The drawing's text, a block at a time (pdftotext groups words that belong together into blocks). Single
    characters and stray fragments go: they only make sense on the drawing."""
    blocks = collections.OrderedDict()
    for ln in sorted(lines, key=lambda x: (x.block, x.y0, x.x0)):
        blocks.setdefault(ln.block, []).append(ln.text)
    out = []
    for texts in blocks.values():
        text = " ".join(texts)
        if len(re.findall(r"[A-Za-z]{2,}", text)) >= 2 or re.search(r"\d+(\.\d+)?\s?(m|mm)\b", text):
            out.append(re.sub(r"\s+", " ", text).strip())
    return out


def render(pdf, page, box, out_path, dpi=DPI):
    """Render the box (points) of a page to a PNG."""
    scale = dpi / 72
    x0, y0, x1, y1 = box
    base = out_path[:-4]
    subprocess.run(["pdftoppm", "-png", "-r", str(dpi), "-f", str(page), "-l", str(page), "-singlefile",
                    "-x", str(int(x0 * scale)), "-y", str(int(y0 * scale)), "-W", str(int((x1 - x0) * scale)),
                    "-H", str(int((y1 - y0) * scale)), pdf, base], check=True, capture_output=True)
    return out_path


def shrink(png_bytes, colours=48):
    """A smaller PNG: the drawings use a handful of flat colours, so a 48-colour palette loses nothing visible and
    saves about two thirds. Needs Pillow; without it the image is kept as it is."""
    try:
        import io
        from PIL import Image
    except ImportError:
        return png_bytes
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    q = im.quantize(colors=colours, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    out = io.BytesIO()
    q.save(out, "PNG", optimize=True)
    return out.getvalue() if len(out.getvalue()) < len(png_bytes) else png_bytes


def strip_labels(page_text, words):
    """Remove lines made of a drawing's label words from the page's running text."""
    if not words:
        return page_text
    left = collections.Counter(words)
    kept = []
    for line in page_text.split("\n"):
        tokens = line.split()
        if not tokens:
            kept.append(line)
            continue
        hits = sum(1 for t in tokens if left.get(t, 0) > 0)
        if hits / len(tokens) >= 0.7 and not PARA_START.match(line.strip()):
            for t in tokens:
                if left.get(t, 0) > 0:
                    left[t] -= 1
            continue
        kept.append(line)
    return "\n".join(kept)
