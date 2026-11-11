"""Read a PDF's pages a column at a time, from where its lines are on the page.

pdftotext -layout keeps a table's rows but sets two columns side by side, and when the columns' lines sit at slightly
different heights it gives them on alternate lines. Untangling that from the text alone (splitter.unweave_columns)
goes wrong on a page with a short column, a list set in from the margin, or a column full of standard numbers. Here
the page is cut up first and each piece read on its own: the gutter is found from the lines' positions, anything set
across it (a heading, a wide table or drawing, the running header and footer) is read whole, and between those the
left column is read before the right. Nothing depends on how the PDF was made, so a new edition is read the same way.
"""
import concurrent.futures
import re
import subprocess

import figures

EDGE = 0.07     # the running header and footer are in the top and bottom of the page: never cut at the gutter
LONG = 0.22     # a line this share of the page's width is running text, not a label or a table cell


def _inside(ln, boxes):
    return any(b[0] - 1 <= ln.x0 and ln.x1 <= b[2] + 1 and b[1] - 1 <= (ln.y0 + ln.y1) / 2 <= b[3] + 1 for b in boxes)


def _columns_in(lines, box, x):
    """Does this figure's box hold a column of running text on each side of x? Then it took in the page's text (ADE
    Table 0.2 was given the whole page). Running text is lines as wide as a column, with numbered paragraphs starting
    at the column's edge; a table's cells and a drawing's notes have neither (ADB Vol 2 Tables 12.1 and C1)."""
    inside = [ln for ln in lines if _inside(ln, [box])]
    wide = [ln for ln in inside if ln.x1 - ln.x0 >= 0.32 * (box[2] - box[0])]
    left, right = [ln for ln in wide if ln.x1 <= x + 1], [ln for ln in wide if ln.x0 >= x - 1]
    if len(left) < 6 or len(right) < 6:
        return False
    edges = (min(ln.x0 for ln in left), min(ln.x0 for ln in right))
    return sum(1 for ln in inside if re.match(r"\d{1,2}\.\d{1,3}\b", ln.text) and
               any(abs(ln.x0 - e) <= 3 for e in edges)) >= 2


def _beside(w, body, boxes):
    """The gutter of a page with running text down one column and a table or drawing in the other (ADC page 15):
    between the text's edge and the figure's."""
    for b in boxes:
        level = [ln for ln in body if ln.y1 > b[1] and ln.y0 < b[3] and not _inside(ln, [b])]
        # a column's worth of text beside a figure a column wide; text across a page of one column isn't that
        if len(level) < 5 or b[2] - b[0] > w * 0.5 or max(ln.x1 - ln.x0 for ln in level) > w * 0.46:
            continue
        if all(ln.x1 <= b[0] + 3 for ln in level):
            x = (max(ln.x1 for ln in level) + b[0]) / 2
        elif all(ln.x0 >= b[2] - 3 for ln in level):
            x = (b[2] + min(ln.x0 for ln in level)) / 2
        else:
            continue
        if w * 0.38 <= x <= w * 0.62:
            return round(x)
    return None


def gutter(w, h, lines, boxes=()):
    """Where the page's two columns part, in points from the left, or None if it isn't set in two columns.

    The gutter is the widest upright strip near the middle that no line of running text crosses, with a column's
    worth of lines wholly on each side of it. A page of one column has lines across every such strip."""
    body = [ln for ln in lines if ln.y0 > h * EDGE and ln.y1 < h * (1 - EDGE) and ln.x1 - ln.x0 >= w * LONG]
    if boxes:
        # without the lines in tables and drawings first: their rows say nothing about the columns of the text around
        # them. Failing that with every line: the box found for a table can take in the text under it (ADE Table 0.2
        # was given the whole page)
        x = gutter(w, h, [ln for ln in body if not _inside(ln, boxes)])
        if x is None:
            x = gutter(w, h, body)
            if x is not None and not any(_columns_in(lines, b, x) for b in boxes):
                x = None  # the two sides were a drawing and the note beside it, not columns (ADB Vol 2 Diagram 2.4)
        return x or _beside(w, body, boxes)
    if len(body) < 8:
        return None
    best, run = None, []
    for x in range(int(w * 0.38), int(w * 0.62) + 1):
        if any(ln.x0 < x - 1 and ln.x1 > x + 1 for ln in body):
            run = []
            continue
        run.append(x)
        if best is None or len(run) > len(best):
            best = list(run)
    if not best:
        # no strip is clear from top to bottom: a heading or a paragraph may run across the top of a two-column page
        def crossing(x):
            return sum(1 for ln in body if ln.x0 < x - 1 and ln.x1 > x + 1)
        x = min(range(int(w * 0.38), int(w * 0.62) + 1), key=lambda x: (crossing(x), abs(x - w / 2)))
        if crossing(x) > 0.3 * len(body):
            return None
        best = [x]
    x = best[len(best) // 2]
    left = sum(1 for ln in body if ln.x1 <= x + 1)
    right = sum(1 for ln in body if ln.x0 >= x - 1)
    return x if left >= 3 and right >= 3 else None


def bands(w, h, lines, x, boxes=()):
    """The page from top to bottom as [(across, y0, y1)]: a band read across the whole page, or one read a column at
    a time. Across are the header and footer, any line that crosses the gutter, and a table or drawing that does
    (boxes: (x0, y0, x1, y1) of the page's figures)."""
    # well into both columns: the box of a table in one column can reach a little past the gutter (ADH Table 6)
    # ...and not one with a column of running text down each side of the gutter inside it: that is the page's text,
    # taken into the table's box by mistake
    wide = []
    for b in boxes:
        if not (b[0] < x - 0.1 * w and b[2] > x + 0.1 * w):
            continue
        if _columns_in(lines, b, x):  # the table is what's above the columns of text its box took in
            text = [ln for ln in lines if _inside(ln, [b]) and ln.x1 - ln.x0 >= 0.32 * (b[2] - b[0])
                    and (ln.x1 <= x + 1 or ln.x0 >= x - 1)]
            b = (b[0], b[1], b[2], min(ln.y0 for ln in text) - 2)
        wide.append(b)

    def across(ln):
        mid = (ln.y0 + ln.y1) / 2
        return ln.y1 < h * EDGE or ln.y0 > h * (1 - EDGE) or (ln.x0 < x - 1 and ln.x1 > x + 1) or \
            any(b[1] - 1 <= mid <= b[3] + 1 for b in wide)

    runs = []  # [across, top, bottom], in order down the page
    for ln in sorted(lines, key=lambda ln: (ln.y0 + ln.y1) / 2):
        kind = across(ln)
        if runs and runs[-1][0] == kind:
            runs[-1][2] = max(runs[-1][2], ln.y1)
        else:
            runs.append([kind, ln.y0, ln.y1])
    out, top = [], 0
    for i, (kind, y0, y1) in enumerate(runs):
        # cut in the gap between this band's last line and the next band's first
        bottom = h if i + 1 == len(runs) else max(top, (y1 + runs[i + 1][1]) / 2 if runs[i + 1][1] > y1 else runs[i + 1][1])
        out.append((kind, top, bottom))
        top = bottom
    return out


def _crop(pdf, page, x0, y0, x1, y1):
    x0, y0, x1, y1 = round(x0), round(y0), round(x1), round(y1)
    if x1 <= x0 or y1 <= y0:
        return ""
    out = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", "-f", str(page), "-l", str(page), "-x", str(x0),
                          "-y", str(y0), "-W", str(x1 - x0), "-H", str(y1 - y0), pdf, "-"], capture_output=True).stdout
    return out.decode("utf-8", "replace").replace("\f", "").strip("\n")


def read(pdf, figs=()):
    """{page number (1-based): its text in reading order}, for the pages set in two columns. Other pages aren't in
    it: pdftotext -layout already reads them in order. figs: figures.find's, so a wide table keeps its rows."""
    jobs = []  # (page, order, crop box)
    for page, (w, h, lines) in enumerate(figures.page_lines(pdf), start=1):
        boxes = [f["box"] for f in figs if f["page"] == page]
        x = gutter(w, h, lines, boxes)
        if x is None:
            continue
        for n, (across, y0, y1) in enumerate(bands(w, h, lines, x, boxes)):
            if across:
                jobs.append((page, (n, 0), (0, y0, w, y1)))
            else:
                jobs.append((page, (n, 0), (0, y0, x, y1)))
                jobs.append((page, (n, 1), (x, y0, w, y1)))
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        texts = list(pool.map(lambda j: _crop(pdf, j[0], *j[2]), jobs))
    pages = {}
    for (page, _order, _box), text in zip(jobs, texts):  # jobs are in reading order
        pages.setdefault(page, [])
        if text.strip():
            pages[page].append(text)
    return {page: "\n\n".join(parts) for page, parts in pages.items()}
