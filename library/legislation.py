"""Legislation from legislation.gov.uk (Crown copyright, Open Government Licence) as passages: one per regulation,
section or article, cited as "reg 7", "s 120D", "art 9" or "Sch 1 para 3". Reads the consolidated CLML XML."""
import re
import xml.etree.ElementTree as ET

NS = "{http://www.legislation.gov.uk/namespaces/legislation}"
SKIP = {"Commentary", "Commentaries", "CommentaryRef", "Citation", "Metadata", "Contents", "Resources",
        "ChangeRef", "FootnoteRef", "Footnotes", "MarginNote", "MarginNotes"}
BLOCK = {"P", "Para", "P1para", "P2para", "P3para", "P4para", "P5para", "Text", "ListItem", "tr", "Title", "Pnumber",
         "BlockText", "BlockAmendment", "Tabular", "SignedSection"}
ITEM_PREFIX = {"P2": "({})", "P3": "({})", "P4": "({})", "P5": "({})"}


def tag(e):
    return e.tag[len(NS):] if e.tag.startswith(NS) else e.tag.split("}")[-1]


def render(e, out):
    t = tag(e)
    if t in SKIP:
        return
    if t in ITEM_PREFIX:
        num = e.find(NS + "Pnumber")
        label = ITEM_PREFIX[t].format("".join(num.itertext()).strip()) if num is not None else ""
        out.append("\n" + "  " * (int(t[1]) - 2) + label + " ")
        for child in e:
            if tag(child) != "Pnumber":
                render(child, out)
        if e.tail:
            out.append(e.tail)
        return
    if t in ("td", "th"):
        out.append(" | ")
    elif t == "ListItem":
        out.append("\n- ")
    elif t in BLOCK and t not in ("Text",):
        out.append("\n")
    if e.text and t not in ("Pnumber",):
        out.append(e.text)
    for child in e:
        render(child, out)
    if t in ("Text", "Para") :
        out.append("\n")
    if e.tail:
        out.append(e.tail)


CONTAINERS = {"Number", "Title", "Subtitle", "P1", "P1group", "Part", "Chapter", "Pblock", "PsubBlock", "Schedule",
              "ScheduleBody", "Reference"}


def text_of(e):
    out = []
    for child in e:
        if tag(child) not in ("Pnumber", "Title"):
            render(child, out)
    return _tidy("".join(out))


def _tidy(s):
    s = re.sub(r"(?m)^[ \t]*\|[ \t]*$", "", s)
    s = re.sub(r"(?m)^[ \t]*\|[ \t]*", "", s)
    s = re.sub(r"[ \t]+", " ", s)
    s = re.sub(r" *\n *", "\n", s)
    s = re.sub(r"\n{2,}", "\n", s)
    s = re.sub(r"^(\s*(?:\([\w.]+\)|-))\n", r"\1 ", s, flags=re.M)
    s = re.sub(r"^(\s*(?:\([\w.]+\)|-))\n", r"\1 ", s, flags=re.M)  # nested: "(1) (a)"
    return s.strip()


def ref_from_uri(uri, kind):
    path = re.sub(r"^.*?/(?:uksi|ukpga|ukla|wsi|nisr)/\d{4}/\d+/", "", uri or "")
    path = re.sub(r"/\d{4}-\d{2}-\d{2}$", "", path)
    m = re.match(r"(schedule)/([\w]+)(?:/part/([\w]+))?(?:/paragraph/([\w]+))?", path)
    if m:
        ref = f"Sch {m.group(2)}"
        if m.group(3):
            ref += f" Pt {m.group(3)}"
        if m.group(4):
            ref += f" para {m.group(4)}"
        return ref
    m = re.match(r"(regulation|section|article|rule)/([\w]+)", path)
    if m:
        return {"regulation": "reg", "section": "s", "article": "art", "rule": "r"}[m.group(1)] + " " + m.group(2)
    return path.replace("/", " ")


def passages(xml_bytes, kind="regulation", max_chars=2400):
    root = ET.fromstring(xml_bytes)
    out = []
    heading_stack = {}

    def title_of(e):
        t = e.find(NS + "Title")
        return " ".join("".join(t.itertext()).split()) if t is not None else ""

    def walk(e, part_title="", base_ref=""):
        t = tag(e)
        if t in SKIP:
            return
        if e.get("DocumentURI") and t != "P1":
            base_ref = ref_from_uri(e.get("DocumentURI"), kind) or base_ref
        if t in ("Part", "Chapter", "Schedule", "Pblock", "PsubBlock"):
            num = e.find(NS + "Number")
            label = " ".join("".join(num.itertext()).split()) if num is not None else ""
            title = title_of(e)
            if t == "Schedule":
                part_title = f"{label} {title}".strip()
            else:
                part_title = (part_title.split(" › ")[0] + " › " if part_title and t != "Part" else "") + \
                    f"{label} {title}".strip()
        if t == "P1":
            heading = part_title
            group = heading_stack.get(id(e))
            if group:
                heading = f"{heading} › {group}" if heading else group
            num = e.find(NS + "Pnumber")
            ref = ref_from_uri(e.get("DocumentURI") or e.get("IdURI"), kind) or \
                ("".join(num.itertext()).strip() if num is not None else "")
            text = text_of(e)
            if text:
                out.extend(_chunks(ref, heading, text, e, max_chars))
            return
        if t == "P1group":
            title = title_of(e)
            for p1 in e.iter(NS + "P1"):
                heading_stack[id(p1)] = title
        for child in e:
            ct = tag(child)
            if ct not in CONTAINERS and ct not in SKIP and child.find(".//" + NS + "P1") is None and \
                    t in ("Part", "Chapter", "Schedule", "ScheduleBody", "Pblock", "PsubBlock"):
                # content outside numbered provisions, e.g. Schedule 1's tables of requirements
                bits = []
                render(child, bits)
                text = _tidy("".join(bits))
                if len(text) > 40:
                    sub = title_of(child)
                    ref = base_ref + (f" {_part_label(sub)}" if _part_label(sub) else "")
                    head = f"{part_title} › {sub}" if sub else part_title
                    out.extend(_chunks(ref.strip() or part_title, head, text, e, max_chars))
                continue
            walk(child, part_title, base_ref)

    for section in (root.find(f".//{NS}Body"), root.find(f".//{NS}Schedules")):
        if section is not None:
            walk(section)
    return out


def _part_label(title):
    m = re.match(r"PART\s+([A-Z0-9]+)\b", title or "")
    return f"Pt {m.group(1)}" if m else ""


def _chunks(ref, heading, text, e, max_chars):
    status = e.get("Status") or ""
    if status.lower() == "prospective" or e.get("Match") == "false":
        heading = (heading + " (prospective: not yet in force)").strip()
    if len(text) <= max_chars:
        return [{"ref": ref, "heading": heading, "page": None, "text": f"{ref}\n{text}"}]
    out, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) > max_chars:
            out.append(cur)
            cur = ""
        cur += ("\n" if cur else "") + line
    if cur:
        out.append(cur)
    return [{"ref": ref + (f" (part {i + 1})" if len(out) > 1 else ""), "heading": heading, "page": None,
             "text": f"{ref}\n{c}"} for i, c in enumerate(out)]


def metadata(xml_bytes):
    root = ET.fromstring(xml_bytes)
    dc = "{http://purl.org/dc/elements/1.1/}"
    title = root.find(f".//{dc}title")
    modified = root.find(f".//{dc}modified")
    return {"title": title.text if title is not None else "", "modified": modified.text if modified is not None else ""}
