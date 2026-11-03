"""Split document text into passages at the document's own paragraph numbers, so a citation points at "para 2.13"
or "Table 3.1" rather than at an arbitrary chunk. Used for the regulations library and for the person's files."""
import collections
import re

PARA = re.compile(r"""^(?:para(?:graph)?\s+)?(\d{1,2}\.\d{1,3})\.?\s+(?=[A-Za-z(\[•‘“'"])(?!(?:m|mm|kg|kN|kW|W|m2|m²|m3|s|l|litres|metres|per)\b)""")
FIGURE = re.compile(r"^(Table|Diagram|Figure)\s+(\d{1,2}[.\-]\d{1,3}[a-z]?|[A-Z]?\d{1,2}[a-z]?)\b", re.I)
HEADING = re.compile(r"^(?:#{1,6}\s+.+|(?:Section|Chapter|Appendix|Annex|Schedule|Part)\s+[\dA-Z]{1,3}(?::|\s[-–—]|\.)?\s+[A-Z].{2,110}|"
                     r"Requirement\s+[A-T]\d+:.{0,80})$")
TRAILING_NO = re.compile(r"^(.{2,80}?)\s+(\d{1,2}\.\d{1,3})$")
CONTENTS_LINE = re.compile(r"(?:\.{3,}|\s)\d{1,3}$")
PAGE_NO = re.compile(r"^(?:page\s+)?\d{1,4}$|^\d{1,4}\s*(?:of\s+\d{1,4})?$", re.I)
SPACE = re.compile(r"[ \t ]+")


TABLE_GAP = re.compile(r"\S {3,}\S")


def _norm(line):
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", line.strip().lower()))


def clean_pages(pages):
    """pages: list of page texts. Drops running headers/footers (lines on many pages) and page numbers (a bare number
    among the first or last few lines of a page; bare numbers elsewhere are table cells)."""
    counts = collections.Counter()
    split_lines = []
    for text in pages:
        lines = [ln.rstrip().replace("\u00a0", " ") for ln in (text or "").splitlines()]
        split_lines.append(lines)
        counts.update({_norm(ln) for ln in lines if ln.strip()})
    threshold = max(3, len(pages) * 0.3)
    out = []
    for lines in split_lines:
        filled = [i for i, ln in enumerate(lines) if ln.strip()]
        edges = set(filled[:3] + filled[-3:])
        kept = []
        for i, ln in enumerate(lines):
            st = ln.strip()
            if st and counts[_norm(ln)] >= threshold and len(st) < 160:
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
    return short_num / len(lines) > 0.45 and len(re.findall(r"\bsee\b", text, re.I)) >= 2


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
        if m and nxt and not PARA.match(nxt) and not m.group(1).rstrip().endswith((",", "paragraph", "para", "see",
                                                                                     "and", "to", "Table", "Diagram")) \
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
            if len(s) < 40 and not PARA.match(s) and not FIGURE.match(s) and not HEADING.match(s):
                table[-1] += " " + s
                continue
            flush_table()
        s = SPACE.sub(" ", s)
        starts_new = PARA.match(s) or FIGURE.match(s) or HEADING.match(s) or re.match(r"^([•\-–*]|\(?[a-z]\)|\d+\))\s", s)
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

    for page_no, page in enumerate(clean_pages(pages), start=1):
        if is_contents(page):
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
            m_para, m_fig = PARA.match(b), FIGURE.match(b)
            label = None
            if m_para and plausible(m_para.group(1), state):
                label = m_para.group(1)
            elif m_para:
                m_para = None
            elif m_fig:
                label = f"{m_fig.group(1).title()} {m_fig.group(2)}"
            fig_open = bool(cur_refs) and FIGURE.match(cur_refs[0])
            if m_fig or (label and (size >= min_chars or fig_open)) or (size and size + len(b) > max_chars):
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
