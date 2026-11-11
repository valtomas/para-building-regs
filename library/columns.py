"""Read a PDF's pages a column at a time, from where its lines are on the page.

pdftotext -layout keeps a table's rows but sets two columns side by side, and when the columns' lines sit at slightly
different heights it gives them on alternate lines. Untangling that from the text alone (splitter.unweave_columns)
goes wrong on a page with a short column, a list set in from the margin, or a column full of standard numbers. Here
the page is cut up first and each piece read on its own: the gutter is found from the lines' positions, anything set
across it (a heading, a wide table or drawing, the running header and footer) is read whole, and between those the
left column is read before the right. Nothing depends on how the PDF was made, so a new edition is read the same way.
"""
import concurrent.futures
import subprocess

import figures

EDGE = 0.07     # the running header and footer are in the top and bottom of the page: never cut at the gutter
LONG = 0.22     # a line this share of the page's width is running text, not a label or a table cell


def gutter(w, h, lines, boxes=()):
    """Where the page's two columns part, in points from the left, or None if it isn't set in two columns.

    The gutter is the widest upright strip near the middle that no line of running text crosses, with a column's
    worth of lines wholly on each side of it. A page of one column has lines across every such strip."""
    def figure(ln):  # in a table or drawing: its rows say nothing about the columns of the text around it
        return any(b[0] - 1 <= ln.x0 and ln.x1 <= b[2] + 1 and b[1] - 1 <= (ln.y0 + ln.y1) / 2 <= b[3] + 1 for b in boxes)

    body = [ln for ln in lines if ln.y0 > h * EDGE and ln.y1 < h * (1 - EDGE) and ln.x1 - ln.x0 >= w * LONG
            and not figure(ln)]
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
    wide = [b for b in boxes if b[0] < x - 0.1 * w and b[2] > x + 0.1 * w]

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
