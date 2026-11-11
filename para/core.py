"""One turn of a conversation: the model searches and reads the regulations library with tools, then answers with
citations to the exact passages it used. Yields plain dicts so the server (and tests) can show progress.

Works with any OpenAI-compatible chat server. Models that can't call tools get the best passages attached to the
question instead.
"""
import datetime
import json
import re

from .llm import EngineError

MAX_TOOL_ROUNDS = 8
LENGTH_LIMITS = {"normal": 6000, "long": 24000, "none": 0}
REASONING_CHAR_CAP = 40000  # ~10k tokens of reasoning in one round before it is told to answer

BASE_PROMPT = """You are Para, an assistant for building regulations in England, used by architects, engineers, surveyors, contractors and building control. You run on the person's own computer.

You have a library: search it with search_library and read results with read_passage. It holds:
{library}{files}

How to answer:
- Search the library before answering any question about requirements, guidance, dimensions, procedures or definitions, even if you think you know, so the person can check where it came from. Use the documents' own terms (e.g. "means of escape", "travel distance", "protected stairway", "guarding", "going", "U-value", "purge ventilation"). If the first search misses, try other wording or name the Part. Read the most relevant passage before answering.
- Lead with the direct answer, then the detail. Quote figures exactly as written, with units and any conditions or exceptions that change them.
- Cite as you go: after each statement taken from the library, put the id of the passage it came from in square brackets, like [r1234] or [f56]. Cite only ids from your own search_library or read_passage results; the app turns them into links to the passage. Also name the document and paragraph, table or diagram in words (e.g. "ADB Vol 1, para 2.13"), because people quote those.
- Ids starting r are the regulations library; ids starting f are the person's own files. Say something comes from their files or notes only when it does.
- Diagrams and tables are drawings in the documents. Their passages give the caption and the words on the drawing, and the person can see the drawing itself in the reader. When an answer depends on a diagram, cite its passage and say which diagram to look at; don't describe details of a drawing you can't see in the text.
- Some diagram passages also have a "Description of the drawing (written by AI from the image, checked by a person)". Use it to understand what the drawing shows, but it comes second: the document's own text, tables, notes and the words on the drawing take precedence, and a requirement must come from them. If the description and the document disagree, follow the document and say so.
- Never invent paragraph numbers, figures or document names. If the library doesn't cover something, say so plainly, then give what you know marked "Not from the library:".
- Approved Documents are statutory guidance on ways to meet the requirements in Schedule 1 of the Building Regulations 2010; other solutions can comply. Say whether something is a legal requirement or guidance when it matters.
- When the answer depends on the building (dwelling or not, height, storeys, use, new build or work to an existing building, higher-risk building), say which case you are answering, or ask.
- The library covers England. Wales, Scotland and Northern Ireland have different regulations: say so if asked about them.
- Note edition dates and transitional arrangements when a document has recently changed.
- If the person asks to see a passage or document, open it with show_passage.
- Write in British English. Be concise; no disclaimers about consulting a professional.

Today is {today}.{notes}"""

TOOLS = [
    {"type": "function", "function": {
        "name": "search_library",
        "description": "Keyword search over the building regulations library (Approved Documents, the Building "
                       "Regulations 2010, the Building Safety Act and its guidance) and the person's own files. "
                       "Returns the best passages with ids for read_passage. Use specific keywords; naming a Part "
                       "(e.g. 'Part B travel distance flats') narrows it to that Approved Document.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "Keywords, e.g. 'Part K stair maximum rise private'"}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_passage",
        "description": "Read a passage and what follows it (about 6,000 characters), from a search result's id.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "An id from search_library, e.g. 'r1234'"}},
            "required": ["id"]}}},
    {"type": "function", "function": {
        "name": "show_passage",
        "description": "Open a passage on the person's screen in the document reader. Use when they ask to see it.",
        "parameters": {"type": "object", "properties": {
            "id": {"type": "string", "description": "An id from search_library or read_passage"}},
            "required": ["id"]}}},
]

FINAL_NUDGE = ("(Note from the app, not the person: you have used all your lookups. Answer now from what you have "
               "read, with citations. Don't call any more tools.)")
THINK_NUDGE = ("(Note from the app, not the person: you have reasoned long enough. Answer now from what you have, "
               "briefly, with citations.)")
LOOKUP_NUDGE = ("(Note from the app, not the person: search the library with search_library before you answer, and "
                "cite the passages you use. If the message isn't about building regulations or guidance, just reply.)")
RESCUE_NOTE = ("(Note from the app, not the person: you can't look anything else up. Answer the question now from "
               "these library excerpts, citing their ids in square brackets.)")
STALE_LOOKUP = ("(Note from the app: this result came from an earlier edition of the library and its ids no longer "
                "apply. Search again before citing anything from it.)")
UNSEEN_ID = "No search result has that id. Use an id from your search_library results."
BAD_ID = "That isn't a passage id. Ids look like r1234 or f56."

# A bracket or parenthesis holding passage ids, alone ("[r12]", "[r12, r34]") or with the model's own words
# ("[BR 2010, reg 11G(1), r4412]", "(ADB Vol 1, Table 3.1, r272)", "[r4122–r4124]").
CITE_GROUPS = (re.compile(r"\[([^\[\]\n]{0,300})\]"), re.compile(r"\(([^()\n]{0,300})\)"))
# a reference in words inside such a bracket: "Table 3.1", "Diagram 3.2", "reg 11G(1)", "para 3.27"
LABEL_REF = re.compile(r"(?i)\b(table|diagram|figure)\s+([a-z]?\d{1,2}(?:\.\d{1,3})?[a-z]?)\b|\b(reg(?:ulation)?)\.?\s+"
                       r"(\d{1,3}[a-z]{0,2})\b|(?:\bpara(?:graph)?\.?\s+|(?<=[\s,]))"
                       r"(\d{1,2}\.\d{1,3})(?:[a-z]\b|\([a-z]\)|\b)")  # "3.7d", "3.7(d)": an item in 3.7
ID_TOKEN = re.compile(r"(?<![\w/.#\x00])([rf])(\d+)(?:\s*[–-]\s*[rf]?(\d+))?(?![\w\x00])")
ID_IN_TEXT = re.compile(r"\[([rf]\d+)\]")
TOOL_MARKUP = ("<tool_call", "<function", "[TOOL_CALLS]", "<|tool_call")
TEXT_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def _notes(settings):
    """The person's notes for the prompt: their general notes and, in a project's chat, the project's own."""
    general = (settings.get("project_notes") or "").strip()
    project = settings.get("project") or {}
    if not project.get("name"):
        return f"\n\nThe person's notes about their project (take these into account):\n{general}" if general else ""
    # only say there are files to search when there are: told so with none, a model credits library passages to
    # "your project files"
    files = project.get("files") or 0
    out = (f"\n\nThis chat is about the person's project \"{project['name']}\"; " +
           (f"its {files} file{'s are' if files != 1 else ' is'} searched with the library (ids starting f)." if files
            else "it has no files of its own."))
    if (project.get("notes") or "").strip():
        out += f" Their notes about it (take these into account):\n{project['notes'].strip()}"
    if general:
        out += f"\n\nThe person's general notes:\n{general}"
    return out


def system_prompt(library, settings):
    custom = (settings.get("instructions") or "").strip()
    template = custom or BASE_PROMPT
    files = library.files_summary()
    values = {
        "library": library.summary(),
        "files": ("\n\nThe person's own files (results with ids starting f) are also searched:\n" + files) if files
        else "",
        "today": f"{datetime.date.today().day} {datetime.date.today():%B %Y}",
        "notes": _notes(settings),
    }
    if not custom:
        return template.format(**values)
    # The person's own instructions may leave out any of the placeholders. What they'd have carried is added after,
    # so rewriting the instructions never silently drops the project notes or the list of what can be searched.
    try:
        text, missing = custom.format(**values), [k for k in values if "{" + k + "}" not in custom]
    except (KeyError, IndexError, ValueError):  # stray braces: nothing was filled in
        text, missing = custom, list(values)
    added = {"library": "\n\nThe library holds:\n" + values["library"], "files": values["files"],
             "today": f"\n\nToday is {values['today']}.", "notes": values["notes"]}
    return text + "".join(added[k] for k in missing)


def _args(call):
    args = call.get("function", {}).get("arguments") or {}
    if isinstance(args, str):
        try:
            args = json.loads(args or "{}")
        except ValueError:
            m = re.search(r"[rf]\d+", args)
            args = {"id": m.group(0), "query": args} if m else {"query": args}
    return args if isinstance(args, dict) else {}


def _norm_id(value):
    s = str(value or "").strip().strip("[]").lower()
    if re.fullmatch(r"\d+", s):
        return "r" + s  # a bare number: models drop the prefix
    return s if re.fullmatch(r"[rf]\d+", s) else None


def run_tool(library, name, args, known):
    """(text for the model, event for the screen or None)."""
    if name == "search_library":
        query = str(args.get("query") or args.get("q") or "").strip()
        hits = library.search(query)
        event = {"type": "search", "query": query, "results": [_brief(h) for h in hits]}
        if not hits:
            return "No results. Try fewer or different keywords, or the documents' own terms.", event
        known.update(h["id"] for h in hits)
        lines = []
        for h in hits:
            where = " · ".join(x for x in (h["ref"], h["heading"]) if x)
            lines.append(f"[{h['id']}] {h['source']}{' — ' + where if where else ''}\n{h['snippet']}")
        return "\n\n".join(lines), event
    if name in ("read_passage", "show_passage", "read_article", "show_article"):
        pid = _norm_id(args.get("id") or args.get("passage_id"))
        if not pid:
            return BAD_ID, None
        if pid not in known:
            return UNSEEN_ID, None
        if name.startswith("show"):
            info = library.describe(pid)
            if not info:
                return UNSEEN_ID, None
            return f"Opened {info['source']} {info['ref'] or ''} on screen.", {"type": "show", **_brief(info)}
        doc = library.read(pid)
        if not doc:
            return UNSEEN_ID, None
        ids = ID_IN_TEXT.findall(doc["text"])
        known.update(ids)
        if doc.get("next_id"):
            known.add(doc["next_id"])
        more = f"\n\n[More follows: read_passage(id=\"{doc['next_id']}\")]" if doc.get("next_id") else ""
        return f"{_head(doc)}\n\n{doc['text']}{more}", {"type": "read", **_brief(doc), "nearby": _nearby(library, ids, pid)}
    return f"Unknown tool {name}. Use search_library or read_passage.", None


def _brief(item):
    """What the page and a saved chat keep of a passage. key and hash name it apart from its id, which changes
    when the library is rebuilt or a file is read again (server.reanchor)."""
    return {k: item.get(k) for k in ("id", "source", "title", "ref", "heading", "page", "kind", "key", "hash", "url")}


def _head(doc):
    """What a read passage is from: the document and, in an Approved Document, the requirement it's under, which the
    passage's own heading doesn't say ("Section 3: Protection from falling" is under Requirement K2)."""
    head = f"{doc['title']} ({doc['source']})"
    return head + (f", under {doc['requirement']}" if doc.get("requirement") else "")


def _nearby(library, ids, pid):
    """A read passage's text holds the passages around it, which may be cited too: their labels go with the read
    event, so a citation shows as "ADK Diagram 3.1" while the answer streams, not as its id."""
    return [_brief(info) for info in map(library.describe, dict.fromkeys(ids)) if info and info["id"] != pid]


def _text_calls(content):
    """Tool calls written out as text (Hermes/Qwen style) by servers that don't parse them."""
    calls = []
    for i, m in enumerate(TEXT_CALL.finditer(content or "")):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        args = data.get("arguments") or data.get("parameters") or {}
        calls.append({"id": f"text_{i}", "type": "function",
                      "function": {"name": data.get("name", ""), "arguments": json.dumps(args)}})
    return calls


def trim(messages, keep_recent=2):
    """Shorten tool results from earlier questions so long chats fit: keep their first lines."""
    users = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if len(users) <= keep_recent:
        return messages
    cut = users[-keep_recent]
    out = []
    for i, m in enumerate(messages):
        if i < cut and m.get("role") == "tool" and len(m.get("content") or "") > 400:
            m = {**m, "content": m["content"][:400] + "\n[…shortened; search again if needed]"}
        out.append(m)
    return out


def forget_lookups(messages):
    """Earlier messages with their search and read results blanked. Passage ids are rowids, which change when the
    library is rebuilt: an id from a result of an older edition now names some other passage, and a citation to it
    would pass the check under the wrong label. Without the results the ids aren't known, so they can't be cited."""
    return [{**m, "content": STALE_LOOKUP} if m.get("role") == "tool" else m for m in messages]


def chat_events(client, library, history, question, settings, supports_tools=True):
    """history: earlier messages of this chat in OpenAI form (no system message). Yields events and ends with a
    "done" event carrying the messages to save."""
    messages = [{"role": "system", "content": system_prompt(library, settings)}] + trim(history) + \
        [{"role": "user", "content": question}]
    known = set()
    for m in history:
        if m.get("role") == "tool":
            known.update(ID_IN_TEXT.findall(m.get("content") or ""))
    # ids of passages whose whole text the model was given, as against a search result's extract
    full = set()
    for m in history:
        if m.get("role") == "tool" and str(m.get("name", "")).startswith("read"):
            full.update(ID_IN_TEXT.findall(m.get("content") or ""))
    read, seen = {}, {}
    reasoning = settings.get("reasoning", "auto")
    limit = LENGTH_LIMITS.get(settings.get("length", "normal"), 6000)
    tools = TOOLS if supports_tools else None
    if not tools:
        yield from _attach_excerpts(library, messages, read, known, full)
    usage_total, cut_off, nudge, round_no, answer = 0, False, None, 0, ""
    while True:
        use_tools = tools if round_no < MAX_TOOL_ROUNDS and nudge != THINK_NUDGE else None
        if tools and not use_tools and not nudge:
            nudge = FINAL_NUDGE
        sent = nudge
        if nudge:
            messages.append({"role": "user", "content": nudge})
            nudge = None
        content, held, calls, thought, over, finish = [], [], [], 0, False, None
        try:
            stream = client.stream(messages, tools=use_tools, max_tokens=limit,
                                   reasoning="off" if sent == THINK_NUDGE else reasoning)
            for ev in stream:
                if ev["type"] == "reasoning":
                    thought += len(ev["text"])
                    yield {"type": "thinking", "text": ev["text"]}
                    if thought > REASONING_CHAR_CAP and not content and sent != THINK_NUDGE:
                        over = True
                        break
                elif ev["type"] == "content":
                    content.append(ev["text"])
                    text = "".join(content).lstrip()
                    if text.startswith(TOOL_MARKUP):
                        yield {"type": "tick"}
                    elif any(t.startswith(text) for t in TOOL_MARKUP):
                        held.append(ev["text"])
                    else:
                        yield {"type": "token", "text": "".join(held) + ev["text"]}
                        held = []
                elif ev["type"] == "tool_calls":
                    calls = ev["calls"]
                elif ev["type"] == "end":
                    finish = ev["finish"]
                    usage_total += (ev.get("usage") or {}).get("completion_tokens") or 0
                else:
                    yield {"type": "tick"}
            if hasattr(stream, "close"):
                stream.close()
        except EngineError as e:
            if tools and round_no == 0 and re.search(r"tool|function", str(e), re.I):
                # The server or model can't take tools: attach the best passages instead.
                tools = None
                if sent:
                    messages.pop()
                yield {"type": "notice", "text": "This model can't use the search tools, so the app attached the "
                                                 "best-matching passages to your question instead."}
                yield from _attach_excerpts(library, messages, read, known, full)
                continue
            raise
        if sent:
            messages.pop()
        if over:
            yield {"type": "retry", "why": "think_limit"}
            nudge = THINK_NUDGE
            continue
        text = "".join(content)
        if not calls and tools and text.lstrip().startswith(TOOL_MARKUP):
            calls = _text_calls(text)
            if calls:
                text = TEXT_CALL.sub("", text).strip()
        if held and not calls:
            yield {"type": "token", "text": "".join(held)}
        cut_off = cut_off or finish == "length"
        if not use_tools and calls:
            calls = []  # last round is for answering
        entry = {"role": "assistant", "content": text}
        if calls:
            entry["tool_calls"] = calls
        messages.append(entry)
        if not calls and use_tools and round_no == 0 and sent is None and text.strip() and \
                not any(i in known for i in ID_IN_TEXT.findall(text)):
            # It answered from memory without looking anything up: once, ask it to search first.
            messages.pop()
            yield {"type": "retry", "why": "lookup"}
            nudge, round_no = LOOKUP_NUDGE, 1
            continue
        if not calls:
            answer = text
            break
        if text.strip():
            yield {"type": "retry", "why": "lookup"}  # what streamed was a preamble before a lookup
        for call in calls:
            name = call.get("function", {}).get("name", "")
            result, event = run_tool(library, name, _args(call), known)
            if event:
                if event["type"] == "read" or event["type"] == "show":
                    read.setdefault(event["id"], event)
                if event["type"] == "read":
                    full.update([event["id"]] + [n["id"] for n in event.get("nearby", [])])
                for h in event.get("results", []):
                    seen.setdefault(h["id"], h)
                yield event
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name, "content": result})
        round_no += 1

    if tools and (not answer.strip() or answer.lstrip().startswith(TOOL_MARKUP)):
        answer = yield from _rescue(client, library, messages, question, read, seen, known, full)
        messages[-1] = {"role": "assistant", "content": answer}

    answer, cites, dropped = _check_citations(answer, known, library)
    for pid, info in cites.items():
        info["read"] = pid in full  # False: cited from a search result's extract alone
    given = " ".join([question, settings.get("project_notes") or "", (settings.get("project") or {}).get("notes") or ""]
                     + [m.get("content") or "" for m in history if m.get("role") == "user"])
    if messages and messages[-1].get("role") == "assistant":
        messages[-1]["content"] = answer
    yield {"type": "done", "answer": answer, "cites": cites, "dropped": dropped, "cut_off": cut_off,
           "figures": check_figures(answer, cites, library, given),
           "read": list(read.values()), "seen": [s for k, s in seen.items() if k not in read][:12],
           "tokens": usage_total, "messages": messages[1:]}


def _source_key(name):
    s = re.sub(r"approved document\s+([a-z])\b", r"ad\1", (name or "").lower())
    s = re.sub(r"\bvol(?:ume)?\.?\s*|^the\s+", "", s)
    return re.sub(r"[^a-z0-9]", "", s)


def _ref_keys(ref):
    """"3.23–3.25" -> {"3.23", "3.24", "3.25"}; "Table 3.1, Diagram 3.2" -> both; "reg 11G" -> {"reg 11g"}."""
    keys = set()
    for part in (ref or "").lower().split(", "):
        m = re.fullmatch(r"(\d{1,2})\.(\d{1,3})\s*[–-]\s*\1\.(\d{1,3})", part.strip())
        if m:
            keys.update(f"{m.group(1)}.{n}" for n in range(int(m.group(2)), int(m.group(3)) + 1))
        elif part.strip():
            keys.add(part.strip())
    return keys


def _label_ref(text):
    """(source part, ref key) of a reference written in words: "ADB Vol 1, Table 3.1", "BR 2010, reg 11G(1)"."""
    m = LABEL_REF.search(text)
    if not m:
        return None
    if m.group(1):
        ref = f"{m.group(1)} {m.group(2)}".lower()
    elif m.group(3):
        ref = f"reg {m.group(4)}".lower()
    else:
        ref = m.group(5)
    return re.sub(r"(?i)^\s*(?:see(?: also)?|in|from|under)\b", "", text[:m.start()]), ref


def _by_reference(text, known, library):
    """The passage the model saw whose document and reference are the ones written in text, if exactly one is."""
    label = _label_ref(text)
    if not label:
        return None
    source, ref = _source_key(label[0]), label[1]
    found = []
    for pid in known:
        info = library.describe(pid)
        if info and ref in _ref_keys(info.get("ref")) and \
                (not source or source in (_source_key(info.get("source")), _source_key(info.get("title")))):
            found.append(pid)
    return found[0] if len(found) == 1 else None


def _check_citations(answer, known, library):
    """Keep citations to passages it saw; drop made-up ids. A reference written in words in brackets
    ("[ADB1, Table 3.1]") links to the passage it names only if the model saw that very passage. Returns (answer,
    {id: info}, [dropped ids])."""
    cites, dropped = {}, []

    def fix(m):
        inner = m.group(1)
        tokens = list(ID_TOKEN.finditer(inner))
        if not tokens:
            pid = _by_reference(inner, known, library) if len(inner) < 120 else None
            if not pid:
                return m.group(0)
            cites[pid] = _brief(library.describe(pid))
            return f"\x00{pid}\x00" if m.group(0).startswith("[") else f"{m.group(0)} \x00{pid}\x00"
        ids = []
        for t in tokens:  # a range "r4122–r4124" counts as its two ends: what lies between wasn't named
            ids += [t[1] + t[2]] + ([t[1] + t[3]] if t[3] else [])
        good = []
        for i in dict.fromkeys(ids):
            info = library.describe(i) if i in known else None
            if info:
                cites[i] = _brief(info)
                good.append(i)
            else:
                dropped.append(i)
        words = re.sub(r"(?:\s*(?:[,;]|\band\b|&)\s*)+", ", ", ID_TOKEN.sub(" ", inner)).strip(" ,;:")
        if re.fullmatch(r"(?i)see(?: also)?|cf\.?|source:?|from", words):
            words = ""
        chips = "".join(f"\x00{i}\x00" for i in good)  # marked so the second pass leaves them alone
        return f"({words}) {chips}".rstrip() if words else chips

    answer = (answer or "").replace("\x00", "")
    for group in CITE_GROUPS:  # square brackets first: "(ADB Vol 1, para 2.13 [r12])"
        answer = group.sub(fix, answer)
    answer = re.sub(r"\x00([rf]\d+)\x00", r"[\1]", answer)
    answer = re.sub(r"[ \t]+([.,;:])", r"\1", answer)
    return answer, cites, dropped


# ----- figures -----
# A figure is a number with its unit: "220 mm", "7.5m", "30-minute", "0.26 W/m²K", "150-220 mm". Bare numbers are
# left alone: most in an answer are paragraph and table numbers.
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_UNIT = (r"W/\(?m[²2][·.\s]?K\)?|kWh|kW|kN/m[²2]|kN|kPa|Pa|l/s|litres?(?:/s)?|m[³3]/h|mm[²2]?|cm|km|m[²2³3]?|metres?|"
         r"millimetres?|°\s?C|°|degrees?|%|per ?cent|minutes?|mins?|hours?|hrs?|seconds?|storeys?|kg|lux|dB|years?|"
         r"months?|weeks?|days?")
FIGURE = re.compile(rf"(?<![\w.,/])({_NUM})(?:\s?[–-]\s?({_NUM}))?[\s-]?({_UNIT})(?![A-Za-z])")
NUMBER = re.compile(rf"(?<![\w.])(?:{_NUM})")
NUMBER_WORDS = {w: n for n, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())} | {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "ninety": 90}
# the same quantity in the unit the document may use instead: metres and millimetres, hours and minutes
SAME_AS = {"m": (1000,), "metre": (1000,), "metres": (1000,), "mm": (.001,), "millimetre": (.001,),
           "millimetres": (.001,), "cm": (10, .01), "km": (1000,), "hour": (60,), "hours": (60,), "hr": (60,),
           "hrs": (60,), "minute": (1 / 60,), "minutes": (1 / 60,), "min": (1 / 60,), "mins": (1 / 60,), "kW": (1000,)}
SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[^\s\[(])")  # a citation after the full stop stays with its sentence
LIST_ITEM = re.compile(r"\s*(?:[-*•]|\d+[.)])\s+|\s*\|")


def _numbers(text):
    """Every number in a text, as written in figures or (up to twenty, and the tens) in words."""
    found = {round(float(n.replace(",", "")), 6) for n in NUMBER.findall(text or "")}
    return found | {float(NUMBER_WORDS[w]) for w in re.findall(r"[a-z]+", (text or "").lower()) if w in NUMBER_WORDS}


def _cited_segments(answer, cites):
    """(text, [cited ids]) for each stretch of the answer that a citation covers: its sentence, its list item or
    table row, and the list under a line ending in a colon that carries the citation ("ADK Table 1.1 [r5] gives:")."""
    lead = []
    for line in (answer or "").split("\n"):
        ids_in_line = list(dict.fromkeys(i for i in ID_IN_TEXT.findall(line) if i in cites))
        if LIST_ITEM.match(line):
            if not ids_in_line and lead:
                yield line, lead
                continue
        elif line.strip():
            lead = ids_in_line if ID_IN_TEXT.sub("", line).rstrip(" *_").endswith(":") else []
        for part in SENTENCE_END.split(line):
            ids = list(dict.fromkeys(i for i in ID_IN_TEXT.findall(part) if i in cites))
            if ids:
                yield part, ids


def check_figures(answer, cites, library, given=""):
    """Figures the answer gives beside a citation that aren't in the passage cited: [{"figure": "225 mm", "ids"}].

    Numbers only, so it can't tell a right figure used wrongly; what it catches is a figure the cited passage doesn't
    contain at all, which is either made up, worked out, or cited to the wrong paragraph. Figures the person gave
    themselves (in their questions or notes) aren't the passage's to bear out and are left alone."""
    out, numbers, own = [], {}, _numbers(given)
    for text, ids in _cited_segments(answer, cites):
        for pid in ids:
            if pid not in numbers:
                info = cites[pid]
                numbers[pid] = _numbers(" ".join([library.text(pid), info.get("ref") or "", info.get("heading") or ""]))
        there = set().union(*(numbers[pid] for pid in ids))
        for m in FIGURE.finditer(ID_IN_TEXT.sub(" ", text)):
            for raw in filter(None, (m.group(1), m.group(2))):
                value = float(raw.replace(",", ""))
                same = {round(value, 6)} | {round(value * f, 6) for f in SAME_AS.get(m.group(3), ())}
                figure = f"{raw} {m.group(3)}"
                if not same & there and round(value, 6) not in own and \
                        not any(f["figure"] == figure and f["ids"] == ids for f in out):
                    out.append({"figure": figure, "ids": ids})
    return out


def _attach_excerpts(library, messages, read, known, full):
    """Without tools: put the top passages into the question."""
    question = messages[-1]["content"]
    hits = library.search(question, limit=5)
    blocks = []
    for h in hits:
        doc = library.read(h["id"], max_chars=2500)
        if doc:
            ids = ID_IN_TEXT.findall(doc["text"])
            known.update(ids)
            full.update(ids)
            blocks.append(f"{_head(doc)}:\n{doc['text']}")
            read.setdefault(h["id"], {"type": "read", **_brief(h)})
            yield {"type": "read", **_brief(h), "nearby": _nearby(library, ids, h["id"])}
    if blocks:
        messages[-1] = {"role": "user", "content": question + "\n\nLibrary excerpts (cite their ids in square "
                                                              "brackets):\n\n" + "\n\n".join(blocks)}


def _rescue(client, library, messages, question, read, seen, known, full):
    """It looked things up but wrote no answer: give it the best passages and ask once more without tools."""
    ids = list(read)[:3] or list(seen)[:3]
    if not ids:
        ids = [h["id"] for h in library.search(question, limit=3)]
    blocks = []
    for pid in ids:
        doc = library.read(pid, max_chars=2500)
        if doc:
            known.update(ID_IN_TEXT.findall(doc["text"]))
            full.update(ID_IN_TEXT.findall(doc["text"]))
            blocks.append(doc["text"])
    msgs = [m for m in messages if m.get("role") in ("system", "user")][:1] + \
        [{"role": "user", "content": question + "\n\n" + RESCUE_NOTE + "\n\n" + "\n\n".join(blocks)}]
    yield {"type": "retry", "why": "rescue"}
    out = []
    for ev in client.stream(msgs, reasoning="off", max_tokens=4000):
        if ev["type"] == "content":
            out.append(ev["text"])
            yield {"type": "token", "text": ev["text"]}
    return "".join(out)
