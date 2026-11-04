# Plan: Scotland, Wales, Northern Ireland and British Standards

Status: proposed (4 November 2026). Read AGENTS.md and DESIGN.md first; this plan follows their rules, in particular
"the bundled library may contain only material that can be redistributed".

## Goal

Para answers for England only. Make it answer for a project in any UK nation, from that nation's own regulations and
guidance, without mixing them up, and make British Standards usable without bundling them.

The hard part isn't fetching more documents. It's that the same words mean different things in each nation: "Part B"
is fire safety in England and Wales but materials and workmanship in Northern Ireland, and Scotland has no Parts
at all. Every answer has to come from the right nation's documents, and say which nation it's for.

## What each nation has, and whether we can bundle it

Licences must be checked on each publisher's own copyright page before anything goes into `sources.json`. Record what
you found in `sources.json` (`license` per source) and in README's library table.

| Nation | Law | Guidance | Where | Licence |
|---|---|---|---|---|
| England | Building Regulations 2010 (done) | Approved Documents (done) | gov.uk | OGL v3.0 (done) |
| Wales | Building Regulations 2010 as they apply in Wales (already in our BR 2010 XML: Welsh provisions are marked by extent, e.g. "PART 2B - WALES"); Building (Higher-Risk Buildings Procedures) (Wales) Regulations 2025 | Approved Documents for use in Wales (Wales has its own editions of several Parts, e.g. Part L 2022 vols 1 and 2, its own Part B amendments; others are shared with England) | gov.wales/building-regulations; legislation.gov.uk | Welsh Government content is generally OGL. Check |
| Scotland | Building (Scotland) Act 2003; Building (Scotland) Regulations 2004 (`ssi/2004/406`); procedure regulations | Building Standards Technical Handbooks, domestic and non-domestic. The current editions took effect on 6 April 2026 (check). gov.scot publishes them as HTML pages, which are easier to split than the PDFs | gov.scot/publications/building-standards-list-of-guidance; legislation.gov.uk | gov.scot content is generally OGL. Check |
| Northern Ireland | Building Regulations (Northern Ireland) 2012 (`nisr/2012/192`) | Technical Booklets (B, C, D, E, F1, F2, G, H, J, K, L, N, P, R, V) and their amendment booklets (e.g. AMD 7, 2022) | finance-ni.gov.uk/articles/building-regulations-technical-booklets; legislation.gov.uk | **Unconfirmed.** Pages say Crown copyright; one library record says OGL. Ask the Department of Finance (Building Standards Branch) if we can't find a clear statement. Until then, bundle only the legislation and link to the booklets |

Sources found while planning (check they are still current):
- Scotland: [domestic handbook April 2024](https://gov.scot/publications/building-standards-technical-handbook-april-2024-domestic),
  [non-domestic April 2024](https://gov.scot/publications/building-standards-technical-handbook-april-2024-non-domestic),
  [list of guidance](https://www.gov.scot/publications/building-standards-list-of-guidance/pages/general-guidance-documents/)
  (newer editions: January 2025, then April 2026).
- Wales: [Part L guidance page](https://gov.wales/building-regulations-guidance-part-l-conservation-fuel-and-power),
  [Part L vol 1 PDF](https://gov.wales/sites/default/files/publications/2022-10/building-regulations-approved-document-l-vol-1.pdf).
  A 2025 consultation draft of Part L vol 2 exists: don't include consultation drafts.
- Northern Ireland: [Technical Booklets](https://www.finance-ni.gov.uk/articles/building-regulations-technical-booklets),
  e.g. [Technical Booklet B](https://www.finance-ni.gov.uk/publications/technical-booklet-b).

Leave out: consultation drafts, superseded editions (unless a project's transitional arrangements need them; see
open questions), Welsh-language duplicates of English text (index the English version; link to the Welsh one).

## Design

### Every source has a nation

- Add `nation` to `sources` in `para/store.py`: `england`, `wales`, `scotland`, `ni`, or `uk` (applies to more than
  one, e.g. the Building Safety Act 2022 for England and Wales). Libraries built before this have none: treat as
  `england`.
- Legislation that covers several nations (BR 2010 covers England and Wales) is split by the extent in the XML:
  `library/legislation.py` already sees `RestrictExtent` on provisions. Tag each passage with its own extent, so
  "reg 11X (Wales)" isn't offered for an English project. That means a per-passage `nation` column too.
- Short names must say the nation when it isn't England, so citations stay unambiguous: `ADB1` (England),
  `ADB1 Wales`, `Scot Domestic 2.9.1`, `NI TB E 2.3`. Pick names practitioners use; ask building control officers
  in each nation if unsure.

### The person says where the project is

- New setting `nation` (default `england`) in `paths.DEFAULTS`, shown in Project notes as "Where is the project?"
  and as a small switch next to Reasoning and Length. It goes into the system prompt and filters search.
- `search_library` searches the chosen nation's sources plus `uk` ones by default. Add an optional `nation` argument
  so the AI can compare ("how does Scotland's limit differ?") when asked.
- The system prompt names the nation and its document set. Each nation's guidance has its own status: England and
  Wales Approved Documents are statutory guidance; Scottish Technical Handbooks are guidance to the mandatory
  standards; NI Technical Booklets are "deemed to satisfy" guidance. Write one rule per nation.
- `store.query_terms` expands "Part B" to "approved document b". Make that nation-aware: in Northern Ireland "Part E"
  is fire safety (Technical Booklet E); in Scotland map common English Part names to Handbook sections (fire is
  Section 2, structure 1, environment 3, safety 4, noise 5, energy 6, sustainability 7). Keep the mapping in one
  table in `store.py`, with tests.
- The citation checker doesn't change. Add a check that an answer for one nation citing another nation's passage
  gets a note ("This cites the English Approved Document; your project is in Wales."), unless the person asked
  to compare.

### Splitting the new documents

- Scotland's handbooks number clauses three deep (`2.9.1`) under standards (`Standard 2.9`). `splitter.PARA` and
  `splitter.plausible` only know `2.13`: extend both to three levels, keeping the sequence check. Prefer the HTML
  pages on gov.scot (one page per standard) over the PDFs: parse with `myfiles.html_text` and split per clause.
- NI Technical Booklets use their own numbering; look at two booklets before writing rules.
- Wales Approved Documents look like England's: the existing PDF and figure code should work. Check a few figures.
- Run the same checks as for England: refs in sequence, tables keep their numbers, figures cropped right
  (`library/figures.py`), no running headers.

### Interface (follow the current look in DESIGN.md)

- Library dialog: group documents by nation, then type.
- Citation chips show the nation for non-England sources (the short names above do this).
- Reader: nation in the source line.
- Welcome screen: example questions for the chosen nation.

## British Standards

British Standards are BSI copyright and can't be bundled, quoted at length or (under typical subscription terms)
fed to AI tools without a licence that allows it. Don't scrape them, and don't add them to `sources.json`. But the
regulations refer to them constantly ("in accordance with BS 9991"), so make that useful:

1. **Index of references.** At build time, find every standard referenced in the library (`BS 9991`,
   `BS EN 1991-1-1`, `BS EN ISO 13370`, `PAS 24` …) with the passages that cite them. Store it in a `standards`
   table: number, title where the document gives one (Approved Documents list standards in a "Documents referred to"
   appendix), and passage ids. These are facts from OGL documents, so they can be bundled.
2. **A tool and a rule.** Add `find_standard(number)` returning the title and where the regulations refer to it.
   When an answer depends on a standard, the AI names it and says it isn't in the library unless the person has
   added it.
3. **Your own copies.** People with a BSI subscription whose terms allow it can add standards under Your files
   (the permission prompt already warns about British Standards: keep it). Detect the standard's number from the
   file name or first page and link it to the index, so the AI searches the person's copy when the regulations
   point to that standard. Show "You have this standard" or "Not in your files" next to standard references in the
   reader.
4. **Library dialog:** a "Standards referenced" list (number, title, which documents cite it, whether you have it).

Don't link to bootleg copies. Linking to the BSI shop page for a standard is fine.

## Order of work

Each step ends with tests passing, the library rebuilt and checked, and a commit.

1. `nation` on sources and passages, setting, search filter, prompt rule, nation-aware Part mapping. England-only
   library, so nothing changes for existing users. Tests for the mapping and the filter.
2. Wales: legislation extents, then Approved Documents for Wales. Ask the test questions in docs/model-tests.md for a
   Welsh project and compare with England (Part L should differ).
3. Scotland: Act and Regulations, then the two handbooks (HTML). Three-level clause numbers. Add Scottish test
   questions to docs/model-tests.md.
4. British Standards index and `find_standard`, then linking the person's own copies.
5. Northern Ireland once the booklet licence is confirmed (legislation can go in at step 1).
6. README (library table and licences), AGENTS.md (nations in the map and conventions), DESIGN.md (switch, chips).

## Open questions

- NI Technical Booklets licence: who to ask, and fall back to legislation only if no answer.
- Transitional arrangements: a project approved under an older edition still follows it. Keep superseded editions
  as separate, clearly dated sources the person can choose, or only the current ones? Current only to start.
- Wales is bilingual; does anyone need answers in Welsh? Not for now.
- Size: the library grows by roughly the same again per nation. If it passes ~60 MB compressed, consider per-nation
  downloads.
