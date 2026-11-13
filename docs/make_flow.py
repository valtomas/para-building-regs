"""Draws the README's diagram of how a question is answered: docs/flow.svg and docs/flow-dark.svg.

The colours are the app's tokens (DESIGN.md) and the fonts its own, subset to the letters used and embedded, because
GitHub shows an SVG as an image, which can't load fonts from anywhere else. Run it again when the flow changes:

    uv run --with fonttools --with brotli docs/make_flow.py
"""

import base64
import io
from pathlib import Path
from xml.sax.saxutils import escape

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "para" / "static" / "fonts"

LIGHT = {
    "bg": "#f2ebdf", "panel": "#faf5ec", "ink": "#2b1d16", "muted": "#5c4a3e", "muted2": "#7a6656",
    "frame": "#c9b8a1", "accent": "#8a3324", "sidebar": "#6b2419", "sidebar_text": "#f4ead9",
    "sidebar_muted": "#d7a98f", "gold": "#e9b872", "cite": "#ecdcc0", "cite_text": "#8a3324",
}
DARK = {
    "bg": "#1c1411", "panel": "#261b17", "ink": "#efe4d3", "muted": "#b8a593", "muted2": "#8a7868",
    "frame": "#5c4a3e", "accent": "#d9876b", "sidebar": "#3a120c", "sidebar_text": "#efe4d3",
    "sidebar_muted": "#b58a73", "gold": "#e9b872", "cite": "#3d2620", "cite_text": "#f0b9a0",
}

W, H = 960, 540
CW, CH = 240, 150  # card size

# (x, y, number, eyebrow, title, body lines, dark card)
CARDS = {
    "question": (36, 116, "01", "Your question", "Ask in plain words",
                 ["About the Approved Documents,", "the regulations or your own", "files."], False),
    "model": (330, 116, "02", "The AI model", "Searches and reads",
              ["The built-in engine, one you", "already run, or an online", "service if you choose one."], False),
    "library": (672, 116, "03", "The library", "Every paragraph",
                ["Approved Documents and", "legislation, plus your own", "files, searched together."], False),
    "check": (330, 330, "04", "Para checks", "Every citation",
              ["Removes any it never looked up,", "marks those from an extract,", "flags figures not in the text."],
              True),
    "answer": (36, 330, "05", "The answer", "Cited where it's said",
               [], False),
    "reader": (672, 330, "06", "The reader", "Opens the paragraph",
               ["The cited paragraph, its", "diagrams and a link to the", "page of the original."], False),
}


def font_face(family, file, weight, text):
    """An @font-face with the font cut down to the characters drawn, so each SVG stays small."""
    font = TTFont(FONTS / file)
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = ["kern", "liga"]
    sub = subset.Subsetter(options)
    sub.populate(text="".join(sorted(set(text))))
    sub.subset(font)
    out = io.BytesIO()
    font.flavor = "woff2"
    font.save(out)
    data = base64.b64encode(out.getvalue()).decode()
    return (f"@font-face{{font-family:'{family}';font-weight:{weight};"
            f"src:url(data:font/woff2;base64,{data}) format('woff2')}}")


def text(x, y, s, cls, anchor="start"):
    return f'<text x="{x}" y="{y}" class="{cls}" text-anchor="{anchor}">{escape(s)}</text>'


def arrow(points, dashed=False):
    """A line through the points, with an arrowhead at the end."""
    d = "M" + " L".join(f"{x},{y}" for x, y in points)
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    return f'<path d="{d}" class="line"{dash} marker-end="url(#head)"/>'


def card(key):
    x, y, num, eyebrow, title, lines, dark = CARDS[key]
    cls = "dark" if dark else "card"
    out = [f'<rect x="{x}" y="{y}" width="{CW}" height="{CH}" rx="14" class="{cls}"/>',
           text(x + 20, y + 30, num, "num-d" if dark else "num"),
           text(x + 46, y + 30, eyebrow.upper(), "eye-d" if dark else "eye"),
           text(x + 20, y + 62, title, "title-d" if dark else "title")]
    for i, line in enumerate(lines):
        out.append(text(x + 20, y + 92 + i * 19, line, "body-d" if dark else "body"))
    return "\n".join(out)


def answer_chips():
    """The answer card's sample sentence with two citations as the app draws them."""
    x, y = CARDS["answer"][:2]
    return "\n".join([
        text(x + 20, y + 92, "A stair in a home: each rise", "body"),
        text(x + 20, y + 111, "at most 220 mm", "body"),
        f'<rect x="{x + 126}" y="{y + 98}" width="96" height="18" rx="4" class="chip"/>',
        text(x + 174, y + 111, "ADK Table 1.1", "chip-t", "middle"),
    ])


def svg(t):
    q, m, lib, chk, ans, rd = (CARDS[k] for k in ("question", "model", "library", "check", "answer", "reader"))
    mid = CH // 2
    body = [
        f'<rect width="{W}" height="{H}" class="bg"/>',
        # Where the library comes from: outside the computer, once per edition.
        f'<rect x="{lib[0]}" y="10" width="{CW}" height="32" rx="16" class="pill"/>',
        text(lib[0] + CW // 2, 31, "GOV.UK · LEGISLATION.GOV.UK", "mono", "middle"),
        arrow([(lib[0] + CW // 2, 42), (lib[0] + CW // 2, lib[1] - 4)], dashed=True),
        text(lib[0] + CW // 2 - 10, 62, "a new edition each week", "lab", "end"),
        # The computer.
        f'<rect x="14" y="74" width="{W - 28}" height="{H - 88}" rx="20" class="frame"/>',
        text(36, 100, "ON YOUR COMPUTER", "eye"),
        # 01 -> 02
        arrow([(q[0] + CW, q[1] + mid), (m[0] - 4, m[1] + mid)]),
        # 02 <-> 03, the tool loop
        arrow([(m[0] + CW, m[1] + mid - 22), (lib[0] - 4, lib[1] + mid - 22)]),
        text((m[0] + CW + lib[0]) // 2, m[1] + mid - 30, "search · read", "lab", "middle"),
        arrow([(lib[0], lib[1] + mid + 22), (m[0] + CW + 4, m[1] + mid + 22)]),
        text((m[0] + CW + lib[0]) // 2, m[1] + mid + 40, "passages", "lab", "middle"),
        # 02 -> 04
        arrow([(m[0] + CW // 2, m[1] + CH), (chk[0] + CW // 2, chk[1] - 4)]),
        text(m[0] + CW // 2 + 12, m[1] + CH + 36, "answer citing passage ids", "lab"),
        # 04 -> 05
        arrow([(chk[0], chk[1] + mid), (ans[0] + CW + 4, ans[1] + mid)]),
        # 05 -> 06, along the bottom: a click on a citation
        arrow([(ans[0] + CW // 2, ans[1] + CH), (ans[0] + CW // 2, ans[1] + CH + 22),
               (rd[0] + CW // 2, ans[1] + CH + 22), (rd[0] + CW // 2, rd[1] + CH + 4)]),
        f'<rect x="{W // 2 - 104}" y="{ans[1] + CH + 13}" width="208" height="18" class="bg"/>',
        text(W // 2, ans[1] + CH + 26, "a click on a citation", "lab", "middle"),
        # 05 -> 01, a follow-up
        arrow([(q[0] + 40, ans[1]), (q[0] + 40, q[1] + CH + 4)], dashed=True),
        text(q[0] + 52, q[1] + CH + 36, "ask a follow-up", "lab"),
    ]
    body += [card(k) for k in CARDS]
    body.append(answer_chips())
    # The pilcrow on Para's own step, as in the sidebar.
    body.append(text(chk[0] + CW - 22, chk[1] + 36, "¶", "pil", "end"))
    content = "\n".join(body)

    drawn = "".join(c for c in content if not c.isspace() or c == " ")
    faces = "\n".join([
        font_face("Space Grotesk", "space-grotesk-300-700.woff2", 500, drawn + "¶"),
        font_face("IBM Plex Sans", "ibm-plex-sans-100-700.woff2", 400, drawn),
        font_face("IBM Plex Mono", "ibm-plex-mono-500.woff2", 500, drawn),
    ])
    display = "font-family:'Space Grotesk',Helvetica,Arial,sans-serif;font-weight:500"
    sans = "font-family:'IBM Plex Sans',Helvetica,Arial,sans-serif"
    mono = "font-family:'IBM Plex Mono',Menlo,Consolas,monospace;font-weight:500"
    style = f"""{faces}
.bg{{fill:{t['bg']}}}
.frame{{fill:none;stroke:{t['frame']};stroke-width:1.5;stroke-dasharray:6 5}}
.card{{fill:{t['panel']};stroke:{t['ink']};stroke-width:1.5}}
.dark{{fill:{t['sidebar']};stroke:{t['sidebar']};stroke-width:1.5}}
.pill{{fill:{t['panel']};stroke:{t['frame']};stroke-width:1.5}}
.line{{fill:none;stroke:{t['ink']};stroke-width:1.5}}
.head{{fill:{t['ink']}}}
.chip{{fill:{t['cite']}}}
.num,.eye,.num-d,.eye-d,.mono,.lab,.chip-t{{{mono}}}
.num{{font-size:12px;fill:{t['accent']}}}
.num-d{{font-size:12px;fill:{t['gold']}}}
.eye{{font-size:11px;letter-spacing:.22em;fill:{t['accent']}}}
.eye-d{{font-size:11px;letter-spacing:.22em;fill:{t['sidebar_muted']}}}
.mono{{font-size:11px;letter-spacing:.12em;fill:{t['muted']}}}
.lab{{font-size:11.5px;fill:{t['muted2']}}}
.chip-t{{font-size:11px;fill:{t['cite_text']}}}
.title,.title-d{{{display};font-size:21px;letter-spacing:-.02em}}
.title{{fill:{t['ink']}}}
.title-d{{fill:{t['sidebar_text']}}}
.body,.body-d{{{sans};font-size:14px}}
.body{{fill:{t['muted']}}}
.body-d{{fill:{t['sidebar_text']}}}
.pil{{{display};font-size:30px;fill:{t['gold']}}}"""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img"
 aria-label="How Para answers: your question goes to the AI model, which searches and reads passages in the library on
 your computer; Para checks every citation; the answer cites paragraphs that open in the reader with one click.">
<style>
{style}
</style>
<defs><marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto">
<path d="M0,1 L9,5 L0,9 z" class="head"/></marker></defs>
{content}
</svg>
"""


if __name__ == "__main__":
    for name, tokens in (("flow.svg", LIGHT), ("flow-dark.svg", DARK)):
        path = ROOT / "docs" / name
        path.write_text(svg(tokens), encoding="utf-8")
        print(path.relative_to(ROOT), f"{path.stat().st_size // 1024} KB")
