"""Build the regulations library (library/dist/regs.sqlite) from gov.uk and legislation.gov.uk.

python3 library/build.py             fetch what changed (cached in library/cache/) and build
python3 library/build.py --offline   build from the cache only
python3 library/build.py --release   also write library/dist/regs.sqlite.gz and library.json for a GitHub release

Needs pdftotext (poppler-utils) for the PDFs. Everything else is the standard library.
"""
import argparse
import datetime
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

from para.myfiles import html_text  # noqa: E402
from para.splitter import split  # noqa: E402
from para.store import Store  # noqa: E402
import legislation  # noqa: E402

CACHE = os.path.join(HERE, "cache")
DIST = os.path.join(HERE, "dist")
UA = {"User-Agent": "para-library-builder/1 (+https://github.com/valtomas/para-building-regs)"}
OFFLINE = False


def fetch(url, name=None, max_age_days=7):
    """Download url into the cache (reused for max_age_days, or always when offline)."""
    name = name or hashlib.sha1(url.encode()).hexdigest()[:16] + "_" + re.sub(r"[^\w.]+", "_", url.rsplit("/", 1)[-1])[-60:]
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and (OFFLINE or time.time() - os.path.getmtime(path) < max_age_days * 86400):
        return path
    if OFFLINE:
        raise FileNotFoundError(f"not cached: {url}")
    os.makedirs(CACHE, exist_ok=True)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=120) as r, \
                    open(path + ".part", "wb") as f:
                shutil.copyfileobj(r, f)
            os.replace(path + ".part", path)
            return path
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(2 ** attempt)


def content(base_path):
    with open(fetch("https://www.gov.uk/api/content" + base_path, "api_" + base_path.strip("/").replace("/", "_")
                    + ".json", max_age_days=1), encoding="utf-8") as f:
        return json.load(f)


def pdf_pages(path):
    out = subprocess.run(["pdftotext", "-layout", "-enc", "UTF-8", path, "-"], capture_output=True, check=True).stdout
    return out.decode("utf-8", "replace").split("\f")


def date_of(item):
    return (item.get("public_updated_at") or item.get("first_published_at") or "")[:10]


# ----- Approved Documents -----
AD_LETTER = re.compile(r"Approved Document ([A-T7])\b")


def ad_short(pub_title, att_title):
    m = AD_LETTER.search(pub_title) or AD_LETTER.search(att_title)
    if "Manual to the Building Regulations" in att_title:
        return "Manual"
    letter = m.group(1) if m else "?"
    vol = re.search(r"volume\s+(\d)", att_title, re.I)
    base = f"AD{letter}" + (vol.group(1) if vol and not re.search(r"volume 1 and volume 2", att_title, re.I) else "")
    low = att_title.lower()
    if "frequently asked" in low:
        return f"AD{letter} FAQ"
    if low.startswith("corrections"):
        return f"AD{letter} corrections"
    year = re.search(r"(20\d\d) amendment", att_title)
    if "amendment" in low and not low.startswith("approved document"):
        return f"AD{letter} amendments {year.group(1) if year else ''}".strip()
    return base


def approved_documents(sources_cfg, add):
    coll = content(sources_cfg["approved_documents"])
    for doc in coll["links"]["documents"]:
        if doc["base_path"] in sources_cfg.get("skip_publications", []):
            continue
        pub = content(doc["base_path"])
        main_titles = " ".join(a.get("title", "") for a in pub["details"].get("attachments", [])
                               if (a.get("title") or "").lower().startswith(("approved document", "manual")))
        for a in pub["details"].get("attachments", []):
            title = (a.get("title") or "").strip()
            low = title.lower()
            if "no longer current" in low or "merged approved documents" in low:
                continue
            year = re.search(r"(20\d\d) amendment", title)
            if year and not low.startswith(("approved document", "manual")) and year.group(1) in main_titles:
                continue  # already incorporated in the main volume
            url = a.get("url") or ""
            if url.startswith("//"):
                url = "https:" + url
            short = ad_short(pub["title"], title)
            if a.get("content_type") == "application/pdf":
                path = fetch(url)
                kind = "approved-document"
                add(key=short.replace(" ", "_"), title=title, short=short, kind=kind, url=url,
                    published=_edition(title) or date_of(pub), passages=split(pdf_pages(path)))
            elif a.get("attachment_type") in ("html", "external") and "frequently asked" in low:
                page = urllib.parse.urlparse(url).path if "gov.uk" in url or url.startswith("/") else None
                if page:
                    guidance_page(page, add, short=short)


def _edition(title):
    m = re.search(r"(\d{4} edition[^)]*?)(?:\)|$)", title)
    return m.group(1).strip() if m else ""


# ----- gov.uk guidance pages -----
def guidance_page(base_path, add, short=None):
    item = content(base_path)
    parts = []
    if item["details"].get("body"):
        parts.append(item["details"]["body"])
    for p in item["details"].get("parts", []):  # "guide" format: several pages
        parts.append(f"<h2>{p.get('title', '')}</h2>" + (p.get("body") or ""))
    if not parts:
        return
    text = html_text("\n".join(parts))
    short = short or _short_title(item["title"], base_path)
    add(key=base_path.strip("/").replace("/", "_"), title=item["title"], short=short, kind="guidance",
        url="https://www.gov.uk" + base_path, published=date_of(item), passages=split([text], max_chars=1600))


SHORT = {
    "/guidance/building-control-approval-for-higher-risk-buildings": "HRB building control approval",
    "/guidance/preparing-information-for-a-building-control-approval-application": "HRB Gateway 2 application",
    "/guidance/manage-a-building-control-application-for-a-higher-risk-building": "HRB manage application",
    "/guidance/making-changes-to-a-higher-risk-building-project": "HRB changes to project",
    "/guidance/applying-for-a-completion-certificate": "HRB completion certificate",
    "/guidance/design-and-building-work-meeting-building-requirements": "Meeting building requirements",
    "/guidance/keeping-information-about-a-higher-risk-building-the-golden-thread": "Golden thread",
    "/guidance/operating-a-mandatory-occurrence-reporting-system": "Mandatory occurrence reporting",
    "/guidance/submit-a-mandatory-occurrence-notice-and-report": "Mandatory occurrence notice",
    "/guidance/the-building-safety-act": "BSA overview",
    "/guidance/transitional-building-control-approval-for-new-higher-risk-buildings": "HRB transitional (new)",
    "/guidance/transitional-building-control-approval-for-work-on-existing-higher-risk-buildings":
        "HRB transitional (existing)",
    "/building-regulations-approval": "Building regs approval guide",
}


def _short_title(title, base_path=""):
    if base_path in SHORT:
        return SHORT[base_path]
    if "higher-risk building" in title and title.startswith("Criteria"):
        stage = "occupation" if "occupation" in title else "existing" if "existing" in title else "new build"
        return f"HRB criteria ({stage})"
    t = re.sub(r"^(Guidance:|Criteria for (determining whether )?)", "", title).strip()
    return (t[:48] + "…") if len(t) > 50 else t


# ----- legislation -----
def legislation_items(cfg, add):
    for item in cfg["legislation"]:
        url = f"https://www.legislation.gov.uk{item['path']}/data.xml"
        path = fetch(url, "leg_" + item["key"] + ".xml", max_age_days=7)
        with open(path, "rb") as f:
            xml = f.read()
        meta = legislation.metadata(xml)
        kind = "ukpga" if "/ukpga/" in item["path"] else "uksi"
        add(key=item["key"], title=item["title"], short=item["short"], kind="legislation",
            url=f"https://www.legislation.gov.uk{item['path']}", published=f"up to date to {meta['modified']}"
            if meta.get("modified") else "", passages=legislation.passages(xml, kind=kind))


def ligature_fixer(texts):
    """Some PDFs lose their fi/fl/ff ligatures in extraction ("fre resisting foor"). Words that are rare in the whole
    library but common with "fi"/"fl"/"ffi"/"ffl" in place of an "f" are put back."""
    import collections
    import unicodedata
    counts = collections.Counter()
    for t in texts:
        counts.update(w.lower() for w in re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKC", t)))
    fixes = {}
    for w, n in counts.items():
        if "f" not in w:
            continue
        best = None
        for i, ch in enumerate(w):
            if ch != "f":
                continue
            for ins in ("i", "l", "fi", "fl", "f"):
                cand = w[:i + 1] + ins + w[i + 1:]
                if counts.get(cand, 0) >= max(3, n * 3) and (not best or counts[cand] > counts[best]):
                    best = cand
        if best:
            fixes[w] = best

    def fix(text):
        text = unicodedata.normalize("NFKC", text)

        def sub(m):
            w = m.group(0)
            new = fixes.get(w.lower())
            if not new:
                return w
            return new.capitalize() if w[0].isupper() and not w.isupper() else new.upper() if w.isupper() else new
        return re.sub(r"[A-Za-z]+", sub, text)
    return fix


def main():
    global OFFLINE
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--only", nargs="*", help="approved | guidance | legislation")
    args = ap.parse_args()
    OFFLINE = args.offline
    with open(os.path.join(HERE, "sources.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, "regs.sqlite")
    tmp = out + ".building"
    if os.path.exists(tmp):
        os.remove(tmp)
    store = Store(tmp, "r")
    report = []

    pending = []

    def add(**kw):
        pending.append(kw)

    def store_one(**kw):
        passages = [p for p in kw.pop("passages") if p["text"].strip()]
        if not passages:
            report.append(f"EMPTY  {kw['short']}: {kw['title']}")
            return
        store.add_source(passages=passages, license=cfg["license"], **kw)
        chars = sum(len(p["text"]) for p in passages)
        refs = sum(1 for p in passages if p.get("ref"))
        report.append(f"{len(passages):5d} passages {chars // 1000:5d}k chars {100 * refs // len(passages):3d}% "
                      f"with refs  {kw['short']}: {kw['title'][:70]}")
        print(report[-1], flush=True)

    only = set(args.only or ["approved", "guidance", "legislation"])
    if "approved" in only:
        approved_documents(cfg, add)
    if "guidance" in only:
        seen = set()
        for c in cfg["guidance_collections"]:
            for d in content(c)["links"].get("documents", []):
                if d["base_path"] not in seen:
                    seen.add(d["base_path"])
                    guidance_page(d["base_path"], add)
        for p in cfg["guidance_pages"]:
            if p not in seen:
                guidance_page(p, add)
    if "legislation" in only:
        legislation_items(cfg, add)

    fix = ligature_fixer([p["text"] for kw in pending for p in kw["passages"]])
    for kw in pending:
        for p in kw["passages"]:
            p["text"], p["heading"] = fix(p["text"]), fix(p.get("heading") or "")
        store_one(**kw)

    version = datetime.date.today().strftime("%Y.%m.%d")
    digest = hashlib.sha256()
    for row in store.db.execute("SELECT s.key, s.title, p.ref, p.text FROM passages p JOIN sources s ON s.id = "
                                "p.source_id ORDER BY s.key, p.seq"):
        digest.update("\x1f".join(str(x) for x in row).encode())
    store.set_meta(content_hash=digest.hexdigest()[:16])
    store.set_meta(version=version, built=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                   license=cfg["license"])
    store.db.execute("INSERT INTO fts(fts) VALUES('optimize')")
    store.db.commit()
    store.db.execute("VACUUM")
    store.close()
    os.replace(tmp, out)
    print(f"\n{out}: {os.path.getsize(out) / 2**20:.1f} MB, version {version}")
    if args.release:
        gz = out + ".gz"
        h = hashlib.sha256()
        with open(out, "rb") as src, gzip.open(gz, "wb", compresslevel=9) as dst:
            for block in iter(lambda: src.read(1 << 20), b""):
                h.update(block)
                dst.write(block)
        with open(os.path.join(DIST, "library.json"), "w") as f:
            json.dump({"version": version, "file": "regs.sqlite.gz", "sha256": h.hexdigest(),
                       "content_hash": digest.hexdigest()[:16],
                       "size": os.path.getsize(out)}, f, indent=1)
        print(f"{gz}: {os.path.getsize(gz) / 2**20:.1f} MB")


if __name__ == "__main__":
    main()
