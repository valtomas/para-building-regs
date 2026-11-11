"""Finding a saved citation's passage again. Passage ids are rowids: they change when the regulations library is
rebuilt and when one of the person's files is read again. A chat keeps, with each passage it shows, the document's
key and a fingerprint of the passage's text (core._brief), and before a chat is shown or carried on its citations are
pointed at where those passages are now. One that can't be found is said to be gone, never left pointing at whatever
has taken its id."""
import re

ID = re.compile(r"\[([rf]\d+)\]")
PARAGRAPH = re.compile(r"(\d+)\.(\d+)")


def locate(library, info, cache):
    """(the passage's id now, whether its text is the same) for a saved passage; (None, False) if it's gone.

    By its fingerprint first; failing that by its document and reference, which finds a paragraph whose wording has
    changed. Passages saved before fingerprints were kept are matched by reference and heading alone."""
    pid, key, want = info.get("id") or "", info.get("key"), info.get("hash")
    ref, heading = info.get("ref") or "", info.get("heading") or ""
    short = None if key else info.get("source")
    where = (pid[:1], key, short)
    if where not in cache:
        cache[where] = library.anchors(pid, key, short)
    rows = cache[where]
    here = next((r for r in rows if r[0] == pid), None)
    if here and (here[3] == want if want else (here[1], here[2]) == (ref, heading)):
        return pid, True
    if want:
        same = [r for r in rows if r[3] == want]
        if same:
            return next((r for r in same if r[1] == ref), same[0])[0], True
    named = [r for r in rows if r[1] == ref and (ref or r[2] == heading) and (ref or heading)]
    if len(named) > 1:
        named = [r for r in named if r[2] == heading]
    if len(named) == 1:
        return named[0][0], not want
    # A paragraph taken into a run with its neighbour, or let out of one ("1.26" is now in "1.25–1.26"): the
    # passage that holds the first paragraph the citation named, as changed.
    first = PARAGRAPH.match(ref)
    held = [r for r in rows if first and _holds(r[1], int(first.group(1)), int(first.group(2)))] if not named else []
    return (held[0][0], False) if len(held) == 1 else (None, False)


def _holds(ref, section, number):
    """Is paragraph section.number this passage's, or within its run ("2.2–2.4" holds 2.3)?"""
    m = re.fullmatch(r"(\d+)\.(\d+)(?:–(\d+)\.(\d+))?", ref or "")
    if not m or int(m.group(1)) != section or (m.group(3) and m.group(3) != m.group(1)):
        return False
    return int(m.group(2)) <= number <= int(m.group(4) or m.group(2))


def _infos(turn):
    """Every saved passage in a turn: cited, read, found by a search, and those read along with another."""
    yield from (turn.get("cites") or {}).values()
    yield from turn.get("read") or []
    yield from turn.get("seen") or []
    for ev in turn.get("events") or []:
        if ev.get("type") in ("read", "show"):
            yield ev
        yield from ev.get("results") or []
        yield from ev.get("nearby") or []


def reanchor(turn, library, version, cache):
    """Points a saved turn's passages at where they are now. Returns (moved, touched): moved when any passage has a
    new id, a changed text or is gone; touched when the turn needs saving.

    The regulations' ids are only looked at again when the library's edition has changed since the turn; the person's
    files can be read again at any time, so theirs are checked each time."""
    stale = turn.get("library") != version
    found = {}
    for info in _infos(turn):
        pid = info.get("id") or ""
        if info.get("gone") or pid in found or not re.fullmatch(r"[rf]\d+", pid) or (pid[0] == "r" and not stale):
            continue
        found[pid] = locate(library, info, cache)
    moved = {pid: where for pid, where in found.items() if where != (pid, True)}
    if stale:
        turn["library"] = version
    if not moved:
        return False, stale
    for info in _infos(turn):
        if info.get("gone") or info.get("id") not in moved:
            continue
        new, same = moved[info["id"]]
        if new is None:
            info["gone"], info["id"] = True, "g" + info["id"]  # an id nothing opens and nothing can cite
        else:
            info["id"] = new
            if not same:
                info["changed"] = True

    def now(pid):
        return moved[pid][0] or "g" + pid if pid in moved else pid

    turn["cites"] = {info["id"]: info for info in (turn.get("cites") or {}).values()}
    turn["answer"] = ID.sub(lambda m: f"[{now(m.group(1))}]", turn.get("answer") or "")
    for figure in turn.get("figures") or []:
        figure["ids"] = [now(pid) for pid in figure.get("ids") or []]
    return True, True
