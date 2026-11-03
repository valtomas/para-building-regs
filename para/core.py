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
- Diagrams and tables are drawings in the documents. Their passages give the caption and the words on the drawing, and the person can see the drawing itself in the reader. When an answer depends on a diagram, cite its passage and say which diagram to look at; don't describe details of a drawing you can't see in the text.
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
RESCUE_NOTE = ("(Note from the app, not the person: you can't look anything else up. Answer the question now from "
               "these library excerpts, citing their ids in square brackets.)")
UNSEEN_ID = "No search result has that id. Use an id from your search_library results."
BAD_ID = "That isn't a passage id. Ids look like r1234 or f56."

# A bracket or parenthesis holding passage ids, alone ("[r12]", "[r12, r34]") or with the model's own words
# ("[BR 2010, reg 11G(1), r4412]", "(ADB Vol 1, Table 3.1, r272)", "[r4122–r4124]").
CITE_GROUPS = (re.compile(r"\[([^\[\]\n]{0,300})\]"), re.compile(r"\(([^()\n]{0,300})\)"))
# a reference in words inside such a bracket: "Table 3.1", "Diagram 3.2", "reg 11G(1)", "para 3.27"
LABEL_REF = re.compile(r"(?i)\b(table|diagram|figure)\s+([a-z]?\d{1,2}(?:\.\d{1,3})?[a-z]?)\b|\b(reg(?:ulation)?)\.?\s+"
                       r"(\d{1,3}[a-z]{0,2})\b|(?:\bpara(?:graph)?\.?\s+|(?<=[\s,]))(\d{1,2}\.\d{1,3})\b")
ID_TOKEN = re.compile(r"(?<![\w/.#\x00])([rf])(\d+)(?:\s*[–-]\s*[rf]?(\d+))?(?![\w\x00])")
ID_IN_TEXT = re.compile(r"\[([rf]\d+)\]")
TOOL_MARKUP = ("<tool_call", "<function", "[TOOL_CALLS]", "<|tool_call")
TEXT_CALL = re.compile(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", re.S)


def system_prompt(library, settings):
    custom = (settings.get("instructions") or "").strip()
    template = custom or BASE_PROMPT
    files = library.files_summary()
    notes = (settings.get("project_notes") or "").strip()
    values = {
        "library": library.summary(),
        "files": ("\n\nThe person's own files (results with ids starting f) are also searched:\n" + files) if files
        else "",
        "today": f"{datetime.date.today().day} {datetime.date.today():%B %Y}",
        "notes": f"\n\nThe person's notes about their project (take these into account):\n{notes}" if notes else "",
    }
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):  # custom instructions with stray braces
        return template + "\n\n" + values["library"] + values["notes"]


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
        known.update(ID_IN_TEXT.findall(doc["text"]))
        if doc.get("next_id"):
            known.add(doc["next_id"])
        more = f"\n\n[More follows: read_passage(id=\"{doc['next_id']}\")]" if doc.get("next_id") else ""
        head = f"{doc['title']} ({doc['source']})"
        return f"{head}\n\n{doc['text']}{more}", {"type": "read", **_brief(doc)}
    return f"Unknown tool {name}. Use search_library or read_passage.", None


def _brief(item):
    return {k: item.get(k) for k in ("id", "source", "title", "ref", "heading", "page", "kind")}


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


def chat_events(client, library, history, question, settings, supports_tools=True):
    """history: earlier messages of this chat in OpenAI form (no system message). Yields events and ends with a
    "done" event carrying the messages to save."""
    messages = [{"role": "system", "content": system_prompt(library, settings)}] + trim(history) + \
        [{"role": "user", "content": question}]
    known = set()
    for m in history:
        if m.get("role") == "tool":
            known.update(ID_IN_TEXT.findall(m.get("content") or ""))
    read, seen = {}, {}
    reasoning = settings.get("reasoning", "auto")
    limit = LENGTH_LIMITS.get(settings.get("length", "normal"), 6000)
    tools = TOOLS if supports_tools else None
    if not tools:
        yield from _attach_excerpts(library, messages, read, known)
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
                yield from _attach_excerpts(library, messages, read, known)
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
                for h in event.get("results", []):
                    seen.setdefault(h["id"], h)
                yield event
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "name": name, "content": result})
        round_no += 1

    if tools and (not answer.strip() or answer.lstrip().startswith(TOOL_MARKUP)):
        answer = yield from _rescue(client, library, messages, question, read, seen, known)
        messages[-1] = {"role": "assistant", "content": answer}

    answer, cites, dropped = _check_citations(answer, known, library)
    if messages and messages[-1].get("role") == "assistant":
        messages[-1]["content"] = answer
    yield {"type": "done", "answer": answer, "cites": cites, "dropped": dropped, "cut_off": cut_off,
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


def _attach_excerpts(library, messages, read, known):
    """Without tools: put the top passages into the question."""
    question = messages[-1]["content"]
    hits = library.search(question, limit=5)
    blocks = []
    for h in hits:
        doc = library.read(h["id"], max_chars=2500)
        if doc:
            known.update(ID_IN_TEXT.findall(doc["text"]))
            blocks.append(f"{doc['title']} ({doc['source']}):\n{doc['text']}")
            read.setdefault(h["id"], {"type": "read", **_brief(h)})
            yield {"type": "read", **_brief(h)}
    if blocks:
        messages[-1] = {"role": "user", "content": question + "\n\nLibrary excerpts (cite their ids in square "
                                                              "brackets):\n\n" + "\n\n".join(blocks)}


def _rescue(client, library, messages, question, read, seen, known):
    """It looked things up but wrote no answer: give it the best passages and ask once more without tools."""
    ids = list(read)[:3] or list(seen)[:3]
    if not ids:
        ids = [h["id"] for h in library.search(question, limit=3)]
    blocks = []
    for pid in ids:
        doc = library.read(pid, max_chars=2500)
        if doc:
            known.update(ID_IN_TEXT.findall(doc["text"]))
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
