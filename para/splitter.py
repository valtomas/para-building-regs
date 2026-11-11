"""Split document text into passages at the document's own paragraph numbers, so a citation points at "para 2.13"
or "Table 3.1" rather than at an arbitrary chunk. Used for the regulations library and for the person's files."""
import collections
import re

PARA = re.compile(r"""^(?:Para(?:graph)?\s+)?(\d{1,2}\.\d{1,3})\.?\s+(?=[A-Z(\[•‘“'"])(?!(?:m|mm|kg|kN|kW|W|m2|m²|m3|s|l|litres|metres|per)\b)(?!(?:and|to|or)\s+\d)""")
FIGURE = re.compile(r"^(Table|Diagram|Figure)\s+(\d{1,2}[.\-]\d{1,3}[a-z]?|[A-Z]?\d{1,2}[a-z]?)\b", re.I)
HEADING = re.compile(r"^(?:#{1,6}\s+.+|(?:Section|Chapter|Appendix|Annex|Schedule|Part)\s+[\dA-Z]{1,3}(?::|\s[-–—]|\.)?\s+[A-Z].{2,110}|"
                     r"Requirement\s+[A-T]\d+:.{0,80})$")
# where a document turns to its next requirement: the paragraph before it has ended, whatever its number
OPENS = re.compile(r"(?:This|These) (?:[Aa]pproved [Dd]ocument|section|sections) (?:deals? with the (?:following )?"
                   r"[Rr]equirements? |gives guidance on how to meet regulation )")
TRAILING_NO = re.compile(r"^(.{2,80}?)\s+(\d{1,2}\.\d{1,3})$")
CONTENTS_LINE = re.compile(r"(?:\.{3,}|\s)\d{1,3}$")
PAGE_NO = re.compile(r"^(?:page\s+)?\d{1,4}$|^\d{1,4}\s*(?:of\s+\d{1,4})?$", re.I)
SPACE = re.compile(r"[ \t ]+")


TABLE_GAP = re.compile(r"\S {3,}\S")
# what follows "Diagram 4.1" when it is a cross-reference wrapped onto a new line, not a caption: "Diagram 4.1).",
# "Diagram 1.5 d. For access…", "Diagram 1.12 (for buildings…", "Table 1.1. e. Comply…", and "Diagram 23." alone at
# the end of a sentence (a row of ADJ Table 1 ends "…as in / Diagram 23.": the rest of the table was filed under it)
NOT_CAPTION = re.compile(r"^(?:[),;]|[.:]\s*[a-z(]|\.\s*$|\(?\s*[a-z])")
INDEX_REF_END = re.compile(r"(?:\d{1,2}\.\d{1,3}(?:\s*[–-]\s*\d{1,2}\.\d{1,3})?|"
                           r"(?:Tables?|Diagrams?|Appendix)\s+[\dA-Z][\d.]*|[Pp]age\s+\d{1,3})\s*[,;&]?$")


def figure_start(text):
    """The FIGURE match if text starts with a caption ("Table 1.1 Rise and going"), None for a cross-reference."""
    m = FIGURE.match(text)
    if m and NOT_CAPTION.match(text[m.end():].lstrip(" ")):
        return None
    return m


def _norm(line):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", line.strip().lower()))


def _untabbed(line):
    """A header line without the section tab set apart at either end ("B1", "R2-C", "L1(b), L2"): the tab changes
    from section to section, the header doesn't."""
    tab = r"[A-Z]{1,2}(?:\d[\w()\-]{0,4})?(?:, [A-Z]\d[\w()]{0,3})?"  # not a word: "CONTENTS" says what a page is
    return _norm(re.sub(rf"^\s*{tab}\s{{3,}}|\s{{3,}}{tab}\s*$", "", line))


def _prose(cells):
    """Do these pieces of lines read as running text (not table cells)?"""
    cells = [c for c in cells if c.strip()]
    if len(cells) < 4:
        return False
    text = " ".join(cells)
    words = re.findall(r"[A-Za-z]{2,}", text)
    return sum(len(c.strip()) for c in cells) / len(cells) >= 18 and len(words) >= 3 * len(cells) and \
        len(re.findall(r"\d", text)) < 0.08 * len(text)


def _staggered_gutter(lines, width):
    """The gutter of a two-column page whose columns' lines sit at slightly different heights, so that pdftotext
    gives them on alternate lines: a line of the left column, then one of the right, few with both (ADC, ADG). There
    is no run of lines split at the gutter to find it by. It is the place where many lines' text starts well into
    the page, nearly every line is blank, and both sides have a column's worth of lines."""
    text = [ln for ln in lines if ln.strip()]
    if len(text) < 20:
        return None

    def clear(ln, x):
        return not ln[x - 1:x + 2].strip()

    def starts(x):
        return sum(1 for ln in text if clear(ln, x) and ln[x + 2:x + 3].strip())  # right-column lines starting here

    # The place most lines start at can be the indent of a list in the right column ("a. well compacted…" then its
    # lines set in under it, ADC page 26), which the lines above it cross: take the best place that is a gutter.
    for x in sorted(range(int(width * 0.35), int(width * 0.65)), key=starts, reverse=True):
        if starts(x) < 8:
            return None
        left = sum(1 for ln in text if ln[:x].strip() and not ln[x:].strip())
        right = sum(1 for ln in text if ln[x:].strip() and not ln[:x].strip())
        crossing = sum(1 for ln in text if not clear(ln, x))
        if left >= 8 and right >= 8 and crossing <= 0.15 * len(text):
            return x
    return None


def unweave_columns(page):
    """Text of a two-column page from pdftotext -layout comes out a line of each column side by side ("2.2 Where it
    is not possible…   d. there is a level landing…"). Find the gutter, and where both sides of a run of lines read
    as prose, put the left column before the right. Full-width lines and tables stay as they are."""
    lines = page.split("\n")
    long = [ln for ln in lines if len(ln.strip()) > 40]
    if len(long) < 10:
        return page
    width = max(len(ln) for ln in long)

    def splits_at(ln, x):
        return len(ln) > x + 2 and ln[x - 1:x + 2] == "   " and ln[:x].strip() and ln[x:].strip()

    best, best_n = None, 0
    for x in range(int(width * 0.35), int(width * 0.65)):
        n = sum(1 for ln in long if splits_at(ln, x))
        if n > best_n:
            best, best_n = x, n
    staggered = best is None or best_n < 0.6 * len(long)
    if staggered:
        best = _staggered_gutter(lines, width)
        if best is None:
            return page
    out, run = [], []

    def flush():
        if run:
            left = [ln[:best].rstrip() for ln in run]
            right = [ln[best:].strip() for ln in run]
            if _prose(left) and _prose(right):
                out.extend([ln.strip() for ln in left] + [""] + right)
            else:
                out.extend(run)
            run.clear()

    def take(ln):
        # a run of column text starts at a line with both columns on it; on a staggered page few lines have, so
        # there a line of either column starts one
        going = bool(run) or staggered
        if splits_at(ln, best) or (not ln.strip() and run) or (ln.strip() and len(ln) <= best and going):
            run.append(ln)  # both columns, a blank line, or a line with text only in the left column
        elif ln.strip() and ln[:best].strip() == "" and going:
            run.append(ln)  # text only in the right column
        else:
            flush()
            out.append(ln)

    def across(ln):
        """Text on both sides of the gutter, or over it: a row of something as wide as the page."""
        return bool(ln[:best].strip() and ln[best:].strip())

    # On a staggered page few lines of running text have both columns on them, so a table as wide as the page stands
    # out: its caption, then rows with text on both sides. Its rows are kept whole; cut at the gutter, a table loses
    # its right-hand columns to the end of the page ("Walls | 40" becomes "Walls"). It ends where, after a gap,
    # three lines in a row keep to one column again (or six without a gap). A table set in one column has no such rows and is read with its column.
    in_table, held = False, []
    for i, ln in enumerate(lines):
        if in_table:
            if not ln.strip() or not across(ln):
                held.append(ln)
                kept = sum(1 for h in held if h.strip())
                # column headings stacked over one side come straight after the caption or a row: they aren't the end
                if kept >= 6 or (kept >= 3 and not held[0].strip()):
                    in_table = False
                    for h in held:
                        take(h)
                    held = []
                continue
            out.extend(held + [ln])
            held = []
            continue
        if staggered and ln[:best].strip() and re.match(r"Table\b", ln.strip()) and figure_start(ln.strip()) and \
                any(across(nxt) for nxt in [x for x in lines[i + 1:i + 8] if x.strip()][:3]):
            flush()
            out.append(ln)
            in_table = True
            continue
        take(ln)
    out.extend(held)
    flush()
    return "\n".join(out)


def clean_pages(pages):
    """pages: list of page texts. Drops running headers/footers (lines on many pages) and page numbers (a bare number
    among the first or last few lines of a page; bare numbers elsewhere are table cells)."""
    counts, tabbed = collections.Counter(), collections.Counter()
    split_lines = []
    for text in pages:
        lines = [ln.rstrip().replace("\u00a0", " ") for ln in (text or "").splitlines()]
        split_lines.append(lines)
        counts.update({_norm(ln) for ln in lines if ln.strip()})
        tabbed.update({_untabbed(ln) for ln in lines if ln.strip()})
    threshold = max(3, len(pages) * 0.3)
    edge_threshold = max(3, len(pages) * 0.15)  # at the top or bottom: a header that's only on left-hand pages
    out = []
    for lines in split_lines:
        filled = [i for i, ln in enumerate(lines) if ln.strip()]
        edges = set(filled[:3] + filled[-3:])
        kept = []
        for i, ln in enumerate(lines):
            st = ln.strip()
            n = counts[_norm(ln)]
            # the length without the layout's padding: a footer spread across the page is still short
            untabbed = _untabbed(ln)  # needs two words left: "2.1      Introduction" untabbed is nothing
            edge = i in edges and max(n, tabbed[untabbed] if len(re.findall(r"[a-z]{2,}", untabbed)) >= 2
                                      else 0) >= edge_threshold
            if st and (n >= threshold or edge) and len(_norm(ln)) < 160:
                continue
            if st and i in edges and PAGE_NO.match(st):
                continue
            kept.append(ln)
        out.append("\n".join(kept))
    return out


def is_contents(text):
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 8:
        return False
    hits = sum(1 for ln in lines if CONTENTS_LINE.search(ln) and not PARA.match(ln))
    if hits / len(lines) > 0.35 and re.search(r"\bcontents\b|\bindex\b|\bsection\s+\d", text, re.I):
        return True
    # a back-of-book index: many short lines ending in page numbers, "see" cross-references
    short_num = sum(1 for ln in lines if len(ln) < 70 and re.search(r"\d{1,3}(?:,\s*\d{1,3})*$", ln))
    if short_num / len(lines) > 0.45 and len(re.findall(r"\bsee\b", text, re.I)) >= 2:
        return True
    # an Approved Document's index: headed "Index", entries ending in paragraph, table or diagram numbers
    if any(ln.lower() == "index" for ln in lines[:3]):
        return _index_like(lines)
    return False


def _index_like(lines):
    return sum(1 for ln in lines if INDEX_REF_END.search(ln)) / len(lines) > 0.3


def skipped_pages(pages):
    """Page numbers (1-based) of contents and index pages in cleaned pages. An index's later pages have no "Index"
    heading, so a page that follows one and still reads like index entries is skipped too."""
    skip = set()
    for n, text in enumerate(pages, start=1):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if is_contents(text) or (n - 1 in skip and len(lines) >= 8 and _index_like(lines)):
            skip.add(n)
    return skip


def plausible(label, state):
    """Is label (e.g. "2.13") a believable next paragraph number after the last one seen? Table values such as
    U-values ("0.16") or distances also look like paragraph numbers."""
    try:
        major, minor = (int(x) for x in label.split("."))
    except ValueError:
        return False
    last = state.get("last")
    if last is None:
        ok = minor <= 3
    else:
        lm, ln = last
        ok = (major == lm and ln < minor <= ln + 6) or (lm < major <= lm + 3 and minor <= 3)
    if ok:
        state["last"] = (major, minor)
    return ok


def _unwrap_numbers(lines, state=None):
    """pdftotext often puts a paragraph number at the end of the line before its text ("General provisions 1.1"):
    move it to the start of the next line."""
    out = []
    lines = list(lines)
    for i, ln in enumerate(lines):
        s = ln.strip()
        m = TRAILING_NO.match(s)
        j = i + 1
        while j < len(lines) and not lines[j].strip() and j - i < 3:
            j += 1
        nxt = lines[j].strip() if j < len(lines) else ""
        # not the number a caption ends with ("Diagram 2.37 Wall type 4.1", then a heading: ADE lost Section 3's
        # numbers to that "4.1")
        if m and nxt and not PARA.match(nxt) and not figure_start(s) and not nxt[:1].islower() and \
                not m.group(1).rstrip().endswith((",", "paragraph", "para", "see", "and", "to", "Table", "Diagram",
                                                  "type", "types")) \
                and state is not None and plausible(m.group(2), dict(state)):
            out.append(m.group(1))
            out.append(m.group(2) + " " + nxt)
            lines[j] = ""
            continue
        if re.fullmatch(r"\d{1,2}\.\d{1,3}", s) and nxt and state is not None and plausible(s, dict(state)):
            out.append(s + " " + nxt)
            lines[j] = ""
            continue
        if s:
            out.append(s)
        elif not s and ln == "":
            out.append("")
    return out


def blocks(text, state=None):
    """Paragraph blocks from text with hard line breaks: joins wrapped lines, keeps numbered items apart, and keeps
    table rows (columns separated by runs of spaces, from pdftotext -layout) as rows with " | " between cells."""
    out, cur, table = [], [], []

    def flush():
        if cur:
            out.append(_join(cur))
            cur.clear()

    def flush_table():
        if table:
            out.append("\n".join(table))
            table.clear()

    for ln in _unwrap_numbers(text.splitlines(), state):
        raw = ln.rstrip()
        s = raw.strip()
        if not s:
            flush()
            continue
        if TABLE_GAP.search(s) and not PARA.match(s) and len(re.split(r" {3,}", s)) >= 2:
            flush()
            table.append(" | ".join(c.strip() for c in re.split(r" {3,}", s)))
            continue
        if table:
            # a short line after table rows is usually a wrapped cell ("stair3"); anything else ends the table
            if len(s) < 40 and not PARA.match(s) and not figure_start(s) and not HEADING.match(s):
                table[-1] += " " + s
                continue
            flush_table()
        s = SPACE.sub(" ", s)
        starts_new = PARA.match(s) or figure_start(s) or HEADING.match(s) or \
            re.match(r"^([•\-–*]|\(?[a-z]\)|\d+\))\s", s)
        if starts_new and cur:
            flush()
        cur.append(s)
        if HEADING.match(s) and len(s) < 140:
            flush()
    flush()
    flush_table()
    return [b for b in out if b]


def _join(lines):
    text = ""
    for ln in lines:
        if text.endswith("-") and ln[:1].islower():
            text = text[:-1] + ln
        else:
            text = (text + " " + ln) if text else ln
    return text


def split(pages, max_chars=1800, min_chars=350):
    """pages: list of page texts (or one-item list). Returns passages: {ref, heading, page, text}."""
    passages = []
    state = {}
    heading, cur, cur_refs, cur_page = "", [], [], None
    size = 0

    def flush():
        nonlocal cur, cur_refs, size, cur_page
        if cur:
            text = "\n\n".join(cur).strip()
            if text:
                refs = cur_refs
                ref = "" if not refs else refs[0] if len(refs) == 1 else f"{refs[0]}–{refs[-1]}" if not FIGURE.match(refs[0]) \
                    else ", ".join(refs)
                passages.append({"ref": ref, "heading": heading[:160], "page": cur_page, "text": text})
        cur, cur_refs, size, cur_page = [], [], 0, None

    cleaned = clean_pages(pages)
    skip = skipped_pages(cleaned)
    for page_no, page in enumerate(cleaned, start=1):
        if page_no in skip:
            continue
        page_blocks = blocks(page, state)
        is_head = [bool(re.match(r"^#{1,6}\s+", b)) or bool(HEADING.match(b) and len(b) < 140) for b in page_blocks]
        for i in range(len(page_blocks) - 1):  # a run of "Appendix A: …", "Appendix B: …" is a list, not headings
            if is_head[i] and is_head[i + 1] and page_blocks[i].split()[0] == page_blocks[i + 1].split()[0] \
                    and not page_blocks[i].startswith("#"):
                is_head[i] = is_head[i + 1] = "list"
        for b, head in zip(page_blocks, is_head):
            md = re.match(r"^#{1,6}\s+(.*)", b)
            if head is True:
                flush()
                heading = (md.group(1) if md else b).strip()
                continue
            m_para, m_fig = PARA.match(b), figure_start(b)
            label = None
            if m_para and plausible(m_para.group(1), state):
                label = m_para.group(1)
            elif m_para:
                m_para = None
            elif m_fig:
                label = f"{m_fig.group(1).title()} {m_fig.group(2)}"
            fig_open = bool(cur_refs) and FIGURE.match(cur_refs[0])
            if m_fig or (label and (size >= min_chars or fig_open)) or (size and size + len(b) > max_chars) or \
                    (cur_refs and OPENS.match(b)):
                flush()
            while len(b) > max_chars:  # one huge block (a table read as text)
                cut = b.rfind(". ", 0, max_chars)
                cut = cut + 1 if cut > max_chars // 2 else max_chars
                if cur:
                    flush()
                cur, cur_page = [b[:cut]], page_no
                if label:
                    cur_refs.append(label)
                flush()
                b = b[cut:].strip()
            if not b:
                continue
            if cur_page is None:
                cur_page = page_no
            if label and label not in cur_refs:
                cur_refs.append(label)
            if cur and len(b) < 60 and not label and (len(cur[-1].rsplit("\n", 1)[-1]) < 60 or " | " in cur[-1]):
                cur[-1] += " | " + b  # table cells come out one per line: keep a row together
            else:
                cur.append(b)
            size += len(b)
    flush()
    return passages
