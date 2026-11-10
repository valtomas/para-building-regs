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


def _is_caption(ln, lines=None):
    """(label, continued) if the line starts a caption, else None. Given the page's lines, a label must also stand as
    a caption among them: not a mention in a table row ("B3 | Table B2 | Minimum periods…"), a drawing's label ("Table
    11 Column (B)") or a sentence carried over from the line above ("…set out in / Table G3 – this is…"), and a bare
    label must have its title beside it (ADB)."""
    m = CAPTION.match(ln.text)
    if not m:
        return None
    rest = m.group(3).strip()
    if lines is not None:
        same_line = [x for x in lines if x is not ln and abs(x.y0 - ln.y0) < 3]
        if not rest and not any(ln.x1 <= x.x0 < ln.x1 + 30 for x in same_line):
            return None
        if any(x.x1 <= ln.x0 + 1 and ln.x0 - x.x1 < 120 and len(x.text) <= 6 for x in same_line):
            return None
        above = [x for x in lines if x.block == ln.block and 0 < ln.y0 - x.y0 < 18 and abs(x.x0 - ln.x0) < 10]
        if any(x.text.rstrip()[-1:].isalpha() and not CAPTION.match(x.text) for x in above):
            return None
    if re.match(r"(Column|Row)\b", rest):
        return None
    # "Diagram 4.2 or 4.3." wrapped in a sentence, or a contents entry ending in a page number
    # not "Diagram 1.12 (for buildings other…" continuing a sentence, nor an index or contents entry, nor a list of
    # references in a table cell ("Figure 2 Diagram 6 and")
    if rest and (rest[0].islower() or rest[0] in ").,;:" or re.match(r"\(\s*[a-z]", rest)
                 or re.match(r"(Diagram|Figure|Table)\s+\d", rest)
                 or re.search(r"\.{3,}|\s\d{1,3}$|;|\d\.\d+,", rest)):
        return None
    if rest.lower().startswith(("continued", "(continued")):
        return f"{m.group(1)} {m.group(2)}", True
    return f"{m.group(1)} {m.group(2)}", False


def _words(ln):
    return len(re.findall(r"[A-Za-z]{2,}", ln.text))


def body_height(lines, least=1):
    """The height of a line of running text: the commonest among lines of four words or more (0 with fewer than
    `least` of them)."""
    heights = collections.Counter(round(ln.y1 - ln.y0) for ln in lines if _words(ln) >= 4)
    return heights.most_common(1)[0][0] if sum(heights.values()) >= least else 0


def _is_body(ln, column, left=None, body_h=0):
    """Running text, as opposed to a label in a drawing: a long line of words, or the start of a paragraph (at the
    left margin: a table cell such as "1.6 or Window Energy Rating" isn't paragraph 1.6). A drawing's text is
    smaller than the running text, so a long label such as "The central door may be omitted if…", or a part's title
    such as "a. Corridor access without dead ends", is not running text."""
    if ln.y1 - ln.y0 < 0.8 * body_h:
        return False
    words = _words(ln)
    margins = left if isinstance(left, tuple) else (left, None)
    at_margin = (margins[0] is None or ln.x0 <= margins[0] + 40
                 or margins[1] is not None and margins[1] - 5 <= ln.x0 <= margins[1] + 40)
    return (ln.x1 - ln.x0 >= 0.5 * column and words >= 7) or bool(PARA_START.match(ln.text) and words >= 5
                                                                    and at_margin)


def _column(cap_lines, lines, w, h, body_h):
    """The column a caption sits in, (x0, x1), when running text sits beside it in the other column: then the figure
    is set in one column, and the text beside it mustn't cut it short. None for a figure across the page. Only lines
    of running text count (two or more, within 150 points of the caption), not the cells of a table across the page;
    so does a second figure's caption beside this one, for two drawings set side by side (ADE)."""
    mid = w / 2
    top, bottom = min(x.y0 for x in cap_lines), max(x.y1 for x in cap_lines)
    cx0, cx1 = min(x.x0 for x in cap_lines), max(x.x1 for x in cap_lines)

    def running(ln):
        return ln.x1 - ln.x0 > 0.25 * w and _words(ln) >= 4 and ln.y1 - ln.y0 >= 0.9 * body_h

    def beside(ln, mine):
        """Running text or another figure's caption beside this caption (the old test), or, the text beside the
        caption being a box or a list, two lines of running text beside the figure within 150 points: lines with
        none in this figure's own half at the same height, which would be two columns of text above or below a
        figure across the page."""
        if not 0.08 * h < ln.y0 < 0.93 * h:
            return False
        if _is_caption(ln, lines):  # two drawings side by side, their captions not always level (ADE)
            return ln.y1 > top - 60 and ln.y0 < bottom + 60
        if ln.x1 - ln.x0 > 0.35 * w and ln.y1 - ln.y0 >= 0.9 * body_h:
            return ln.y1 > top - 6 and ln.y0 < bottom + 40
        return (running(ln) and ln.y1 > top - 150 and ln.y0 < bottom + 150
                and not any(mine(x) and running(x) and abs(x.y0 - ln.y0) < 8 for x in lines))
    if cx0 > mid - 10:
        mine = lambda x: x.x0 > mid - 10  # noqa: E731
        other = [ln for ln in lines if ln.x1 < mid + 10 and beside(ln, mine)]
        right = [x for x in lines if x.x0 > mid]  # starting at its own margin, past the ends of the other's lines
        col = min([x.x0 for x in right] + [cx0]) - 6, max([x.x1 for x in right] + [cx1]) + 8
    elif cx1 < mid + 10:
        mine = lambda x: x.x1 < mid + 10  # noqa: E731
        other = [ln for ln in lines if ln.x0 > mid - 10 and beside(ln, mine)]
        # up to the middle, or where the other column starts if that's sooner (ADE's gutter straddles the middle)
        col = (min([x.x0 for x in lines if x.x1 < mid] + [cx0]) - 8,
               min([mid] + [x.x0 - 4 for x in other if x.x0 > cx1]))
    else:
        return None
    near = [ln for ln in other if ln.y1 > top - 60 and ln.y0 < bottom + 60 and (_is_caption(ln, lines) or
                                                                             ln.y1 > top - 6 and ln.y0 < bottom + 40)]
    # a figure across the page is captioned at the left margin: one captioned past the middle is in the right column
    return col if near or len(other) >= 2 or cx0 > mid + 10 else None


def _bounds(lines, body_h, top_limit, bottom_limit):
    """What can end a figure, among the lines of a page or of one of its columns: (running text or captions, the
    starts of numbered paragraphs, headings and captions)."""
    column = max(ln.x1 for ln in lines) - min(ln.x0 for ln in lines)
    xs = sorted(ln.x0 for ln in lines)
    left = xs[len(xs) // 10]
    # on a page set in two columns, the second column's paragraphs start at its own left edge
    mid = (min(ln.x0 for ln in lines) + max(ln.x1 for ln in lines)) / 2
    second = sorted(ln.x0 for ln in lines if ln.x0 > mid - 20 and _words(ln) >= 4 and ln.y1 - ln.y0 >= 0.9 * body_h)
    if len(second) >= 5 and second[0] > left + 60:
        left = (left, second[len(second) // 10])
    body_or_caption = [ln for ln in lines if (_is_body(ln, column, left, body_h) or _is_caption(ln, lines))
                       and top_limit < ln.y0 < bottom_limit and not NOTE.match(ln.text)]
    # Going down from a caption (tables, and diagrams captioned above), long lines may be table rows or notes,
    # so only a numbered paragraph, a heading or another caption ends the figure. A heading is at the margin: one
    # set in from it is part of the drawing, such as a page of an approved document shown in the Manual (Figure 7.1).
    # Paragraphs and headings are set at least as big as running text: a row of a table such as "a. if it separates a
    # flat…", or a cell ending "Section 3, Diagram 3.4).", is set smaller.
    starts = [ln for ln in lines if (_is_caption(ln, lines) or
                                     (HEADING.match(ln.text) and ln.x0 <= _margin(left) + 15
                                      and ln.y1 - ln.y0 >= 0.9 * body_h) or
                                     (PARA_START.match(ln.text) and _is_body(ln, column, left, body_h)
                                      and ln.y1 - ln.y0 >= 0.9 * body_h))
              and top_limit < ln.y0 < bottom_limit]
    return body_or_caption, starts


def _margin(left):
    return left[0] if isinstance(left, tuple) else left


def _caption_lines(ln, lines):
    """A caption's lines: its title may wrap onto the next lines of the same block, or be a block of its own starting
    on the label's line to its right (ADB), and run to four lines (ADR Vol 1 Diagram 2). Followed line by line while
    each comes straight after the one before."""
    blocks = {ln.block} | {x.block for x in lines if abs(x.y0 - ln.y0) < 3 and ln.x1 <= x.x0 < ln.x1 + 30}
    out, last = [ln], ln.y0
    for x in sorted((x for x in lines if x.block in blocks and x is not ln and x.y0 >= ln.y0 - 1), key=lambda x: x.y0):
        if x.y0 - last >= 20:
            break
        out.append(x)
        last = x.y0
    return out


def find(pdf, skip_pages=()):
    """Figures in a PDF: [{label, continued, page (1-based), box (x0, y0, x1, y1) in points, labels: [text],
    words: Counter of words inside the drawing}]."""
    pages = page_lines(pdf)
    doc_body_h = body_height([ln for _w, _h, lines in pages for ln in lines])
    found = []
    votes = collections.Counter()
    for pno, (w, h, lines) in enumerate(pages, start=1):
        if pno in skip_pages or not lines:
            continue
        top_limit, bottom_limit = h * 0.08, h * 0.93
        # a page with plenty of running text has its own: pages replaced by an amendment may be set smaller
        body_h = body_height([ln for ln in lines if ln.x1 - ln.x0 >= w / 3], least=20) or doc_body_h
        bounds = {None: _bounds(lines, body_h, top_limit, bottom_limit)}
        for i, ln in enumerate(lines):
            cap = _is_caption(ln, lines)
            # a caption may sit higher than running text can: at the top of a page given to the figure, just under
            # the running header (ADA, ADC, ADE, ADH, ADJ)
            if not cap or not (h * 0.055 < ln.y0 < bottom_limit):
                continue
            cap_lines = _caption_lines(ln, lines)
            cap_bottom = max(x.y1 for x in cap_lines)
            col = _column(cap_lines, lines, w, h, body_h)
            # a line in the other column may reach a little past the middle of the page
            beside = (lambda x: x.x1 > col[0] + 15 and x.x0 < col[1] - 15) if col else (lambda x: True)
            if col not in bounds:
                bounds[col] = _bounds([x for x in lines if beside(x)], body_h, top_limit, bottom_limit)
            body_or_caption, starts = bounds[col]
            above = max([x.y1 for x in body_or_caption if x.y1 <= ln.y0 - 1 and x not in cap_lines]
                        or [top_limit])
            below = min([x.y0 for x in body_or_caption if x.y0 >= cap_bottom + 1 and x not in cap_lines]
                        or [bottom_limit])
            # a table's own rows ("a. Thermal elements…") start like paragraphs, and where a table fills the page
            # they set the page's running text size too: only a paragraph set bigger than the figure's own text,
            # just under its caption (not beside it, in a column the figure was not found to be set apart from), ends it
            cx0, cx1 = min(x.x0 for x in cap_lines) - 10, max(x.x1 for x in cap_lines) + 10
            own = sorted(x.y1 - x.y0 for x in lines if beside(x) and cap_bottom < x.y0 < cap_bottom + 60
                         and x.x1 > cx0 and x.x0 < cx1)
            own_h = own[len(own) // 2] if own else 0
            # and a caption in one half of the page isn't ended by a paragraph wholly in the other (ADA Diagram 10),
            # though another figure's caption ends it wherever it is
            half = ((lambda x: x.x1 > w / 2 + 20) if cx0 + 10 > w / 2 - 10 else
                    (lambda x: x.x0 < w / 2 - 20) if cx1 - 10 < w / 2 + 10 else (lambda x: True))
            below_strict = min([x.y0 for x in starts if x.y0 >= cap_bottom + 1 and x not in cap_lines
                                and (_is_caption(x, lines) or half(x) and x.y1 - x.y0 > own_h + 0.5)]
                               or [bottom_limit])
            gap_above, gap_below = ln.y0 - above, below - cap_bottom
            found.append({"label": cap[0], "continued": cap[1], "page": pno, "kind": cap[0].split()[0],
                          "cap": (ln.y0, cap_bottom), "above": above, "below": below_strict, "w": w, "h": h,
                          "lines": [x for x in lines if beside(x)], "cap_lines": cap_lines, "col": col,
                          "body_h": body_h})
            # Tables have their caption above; whether diagram captions sit above or below their drawing differs
            # between documents, and in ADA between its Diagrams and its Figures, so each kind in each document votes
            # with the captions where only one side has room. A figure with parts captioned on the same page
            # ("Figure 3" under "Figure 3a" and "Figure 3b") has their captions for neighbours, which says nothing.
            parts = any(_is_caption(x, lines) and _is_caption(x, lines)[0][:-1] == cap[0] for x in lines if x is not ln)
            if cap[0].startswith(("Diagram", "Figure")) and not parts:
                if gap_above > 60 and (gap_below < 25 or gap_below < 50 and gap_above > 4 * gap_below):
                    votes[cap[0].split()[0], "below"] += 1
                elif gap_below > 60 and (gap_above < 25 or gap_above < 50 and gap_below > 4 * gap_above):
                    votes[cap[0].split()[0], "above"] += 1
    out, greys = [], {}
    for f in found:
        kind_votes = [votes[f["kind"], "below"], votes[f["kind"], "above"]]
        if not any(kind_votes):  # too few of this kind to tell: as the document's other figures
            kind_votes = [sum(n for (_k, side), n in votes.items() if side == s) for s in ("below", "above")]
        caption_below = kind_votes[0] > kind_votes[1] and f["kind"] != "Table"
        if f["page"] not in greys:
            greys[f["page"]] = page_grey(pdf, f["page"])
        sides = {False: (f["cap"][0] - 4, f["below"] - 3),  # caption, then the drawing or table under it
                 True: (f["above"] + 3, f["cap"][1] + 4)}  # the drawing, then its caption
        if f["kind"] != "Table" and (_drawing_rows(greys[f["page"]], f, *sides[caption_below]) < 15
                                     and _drawing_rows(greys[f["page"]], f, *sides[not caption_below]) > 60):
            caption_below = not caption_below  # nothing drawn on the side the vote says, plenty on the other
        f["y"] = sides[caption_below]
        f["down"] = not caption_below
    for f in found:
        y0, y1 = f["y"]
        if f["down"]:  # a table's notes can run straight on into a drawing captioned under it (ADB Vol 2 Table 4.1)
            for g in found:
                if not (g is not f and g["page"] == f["page"] and not g["down"] and _overlap(f, g)
                        and f["cap"][0] < g["y"][0] < y1):
                    continue
                gap = g["y"][0] <= f["cap"][1] + 8 and _first_gap(greys[f["page"]], f, f["cap"][1], g["cap"][0])
                if gap:  # the drawing claims the whole table: they part at the first blank band under the caption
                    y1, g["y"] = gap[0], (gap[1], g["y"][1])
                else:
                    y1 = g["y"][0] - 1
        if y1 - y0 < 45:
            continue
        inside = [x for x in f["lines"] if x.y0 >= y0 - 1 and x.y1 <= y1 + 1]
        xs = [x.x0 for x in inside] + [x.x1 for x in inside]
        if f["col"]:
            x0, x1 = f["col"]
        else:
            x0 = max(0, min(xs + [f["w"] * 0.08]) - 8)
            x1 = min(f["w"], max(xs + [f["w"] * 0.92]) + 8)
        # then down to where the drawing's ink is, with the running text (text as big as the body's) taken out
        # including a line only partly in the box: the top of the page's footer, under a drawing at the foot of a page
        running = [x for x in f["lines"] if x.y1 > y0 and x.y0 < y1 and x.y1 - x.y0 >= 0.9 * f["body_h"]]
        tight = ink_box(greys[f["page"]], (x0, max(0, y0), x1, min(f["h"], y1)), running, f["cap_lines"])
        if tight:
            x0, y0, x1, y1 = tight
            inside = [x for x in inside if x.y0 >= y0 - 1 and x.y1 <= y1 + 1]
        labels = _labels([x for x in inside if x not in f["cap_lines"]])
        words = collections.Counter(w for x in inside if x not in f["cap_lines"] for w in x.text.split())
        out.append({"label": f["label"], "continued": f["continued"], "kind": f["kind"], "page": f["page"],
                    "box": (x0, max(0, y0), x1, min(f["h"], y1)), "labels": labels, "words": words,
                    "caption": " ".join(x.text for x in f["cap_lines"])})
    return out


def _overlap(f, g):
    """Do two figures share a column (or is either across the page)?"""
    if not f["col"] or not g["col"]:
        return True
    return f["col"][0] < g["col"][1] and g["col"][0] < f["col"][1]


def _ink_rows(grey, f, y0, y1):
    """The rows between y0 and y1 with ink once the running text and the caption are whited out."""
    w, h, px = grey
    xs = [x.x0 for x in f["cap_lines"]] + [x.x1 for x in f["cap_lines"]]
    lo, hi = (int(v) for v in (f["col"] if f["col"] else (min(xs) - 8, max(xs) + 8)))
    lo, hi = max(0, lo), min(w, hi)
    out = [x for x in f["lines"] if x.y1 > y0 and x.y0 < y1 and x.y1 - x.y0 >= 0.9 * f["body_h"]]
    cap0, cap1 = f["cap"]
    rows = []
    for y in range(max(0, int(y0)), min(h, int(y1))):
        if cap0 - 6 <= y <= cap1 + 6:
            continue
        line = bytearray(px[y * w + lo:y * w + hi])
        for ln in out:
            if ln.y0 - 1 <= y <= ln.y1 + 1:
                a, b = max(0, int(ln.x0) - 1 - lo), max(0, min(hi, int(ln.x1) + 2) - lo)
                line[a:b] = b"\xff" * max(0, b - a)
        if bytes(line).translate(INK).find(1) >= 0:
            rows.append(y)
    return rows


def _drawing_rows(grey, f, y0, y1):
    """How many rows between y0 and y1 are drawn on: a figure at the foot of a page, with its caption under it and
    nothing below (ADL, ADO, ADP), votes for neither side, so a side where nothing is drawn tells the vote is wrong
    for it."""
    return len(_ink_rows(grey, f, y0, y1)) if y1 - y0 >= 45 else 0


def _first_gap(grey, f, y0, y1, least=12):
    """The first blank band at least `least` points high between y0 and y1, as (top, bottom), or None: between a
    table and a drawing captioned under it that follows straight on (ADM Vol 1 Table 3.3 and Diagram 3.8)."""
    rows = _ink_rows(grey, f, y0, y1)
    for a, b in zip(rows, rows[1:]):
        if b - a > least:
            return a + 2, b - 2
    return None


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


def page_grey(pdf, page):
    """A page rendered in grey at one pixel per point: (width, height, bytes, a row at a time)."""
    raw = subprocess.run(["pdftoppm", "-gray", "-r", "72", "-f", str(page), "-l", str(page), "-singlefile", pdf],
                         capture_output=True, check=True).stdout
    m = re.match(rb"P5\s+(\d+)\s+(\d+)\s+255\s", raw)
    w, h = int(m.group(1)), int(m.group(2))
    return w, h, bytearray(raw[m.end():m.end() + w * h])


INK = bytes(1 if v < 200 else 0 for v in range(256))  # darker than this is ink, not the white page


def ink_box(grey, box, text_lines, keep):
    """The part of a box that has ink once the running text is taken out: the drawing's lines, fills and frame, its
    small labels, and the table's rules. The text extractor can't see drawings, so a crop found from text alone runs
    on into an unnumbered paragraph or heading; on the rendered page, that text is blank once it's whited out.
    `keep` (the caption) is always kept. None when too little ink is left to go by (a table of plain text)."""
    w, h, px = grey
    x0, y0, x1, y1 = (max(0, int(box[0])), max(0, int(box[1])), min(w, int(box[2]) + 1), min(h, int(box[3]) + 1))
    px = bytearray(px)
    for ln in text_lines:
        lx0, lx1 = max(x0, int(ln.x0) - 1), min(x1, int(ln.x1) + 2)
        if lx1 > lx0:
            for y in range(max(y0, int(ln.y0)), min(y1, int(ln.y1) + 1)):
                px[y * w + lx0:y * w + lx1] = b"\xff" * (lx1 - lx0)
    rows, left, right = [], x1, x0
    for y in range(y0, y1):
        line = bytes(px[y * w + x0:y * w + x1]).translate(INK)
        first = line.find(1)
        if first >= 0:
            rows.append(y)
            left, right = min(left, x0 + first), max(right, x0 + line.rfind(1))
    if len(rows) < 30:
        return None
    k = [min(x.x0 for x in keep), min(x.y0 for x in keep), max(x.x1 for x in keep), max(x.y1 for x in keep)]
    return (max(box[0], min(left, k[0]) - 6), max(box[1], min(rows[0], k[1]) - 4),
            min(box[2], max(right, k[2]) + 6), min(box[3], max(rows[-1], k[3]) + 4))


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


def strip_labels(page_text, words, labels=()):
    """Remove lines made of a drawing's label words from the page's running text. A line that starts like a paragraph
    stays (a crop can take in running text), unless it is a lettered line word for word in the drawing's text: a
    part's title such as "a. Corridor access without dead ends"."""
    if not words:
        return page_text
    left = collections.Counter(words)
    drawing = " ".join(" ".join(labels).split())
    kept = []
    for line in page_text.split("\n"):
        tokens = line.split()
        if not tokens:
            kept.append(line)
            continue
        hits = sum(1 for t in tokens if left.get(t, 0) > 0)
        start = PARA_START.match(line.strip())
        part_title = start and not start.group(1)[0].isdigit() and " ".join(tokens) in drawing
        if hits / len(tokens) >= 0.7 and (not start or part_title):
            for t in tokens:
                if left.get(t, 0) > 0:
                    left[t] -= 1
            continue
        kept.append(line)
    return "\n".join(kept)
