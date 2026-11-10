# DESIGN.md

How Para looks and behaves, and the rules for changing it. Screenshots are in `docs/screenshots/` (regenerate them
when the interface changes).

## Principles

1. **The citation is the product.** An answer is only as useful as the paragraph behind it, so citations sit
   inline where a claim is made, open the source in one click, and show the document's own reference
   ("ADK Table 1.1", "BR 2010 reg 7").
2. **Show the work, quietly.** What the AI searched, read and reasoned is always available, but collapsed. The
   answer comes first.
3. **Honest states.** Say plainly when an answer doesn't come from the library, when a citation was removed, when
   the engine isn't private, and when the answer was cut short. Never hide these to look cleaner.
4. **Calm and professional.** It's a reference tool used at work, styled like a well-set printed reference:
   parchment pages, an oxblood sidebar, one clay accent, a plain sans-serif for reading and a monospace for labels.
   No animation beyond the typing cursor, no illustrations, no emoji.
5. **Works without explanation.** Someone who has never used an AI tool should get from install to a cited answer
   without reading anything.

## Layout

```
┌───────────────┬──────────────────────────────────┬───────────────────────┐
│ ¶ Para        │  YOUR QUESTION                   │  Reader (overlay,     │
│ PROJECT ▾     │  Question in display type        │  when open)           │
│ New chat   +  │                                  │                       │
│ RECENT        │  ──────────────────────────────  │   eyebrow · title  ✕  │
│ chat list     │   + Show reasoning               │   find · Original ↗   │
│               │   + Looked up: 2 searches, …     │   passages, cited one │
│               │   answer with [ADK Table 1.1]    │   highlighted         │
│               │   CITED: ref ······ heading      │                       │
│ Library    64 ├──────────────────────────────────┤   licence             │
│ Your files  3 │  [ question box         Send ]   │                       │
│ Settings      │  REASONING · LENGTH  disclaimer  │                       │
│ ● Private     │                                  │                       │
└───────────────┴──────────────────────────────────┴───────────────────────┘
   264 px              flexible, text ≤ 840 px          min(500 px, 46vw)
```

The reader is an overlay on the right edge. Above 1240 px the conversation and question box make room for it;
below that it covers part of the conversation. Below 900 px the sidebar becomes a full-screen drawer opened from a
menu button in a 56 px top bar, and the reader and dialogs fill the screen.

Dialogs (native `<dialog>`): **Settings** (tabs: AI engine, Instructions, Notes, Updates & about),
**Project** (name, notes, the project's files, Delete project and Save), **Your files** (the files shared by every chat), **Library** (browse documents and search without AI), and the **permission** prompt shown the first
time someone adds a file.

## Screens

| Screen | Screenshot |
|---|---|
| First run: choose an engine | `setup-engine.png` |
| Built-in engine: model cards, recommended one marked | `setup-builtin.png` |
| Empty chat with example questions | `welcome.png` |
| Answer with inline citations, activity expanded | `answer.png` |
| Answer with the reader open at the cited table | `answer-reader.png`, `answer-reader-dark.png` |
| Reader showing a diagram, and enlarged | `reader-diagram.png`, `reader-diagram-zoom.png` |
| Reader: a diagram's description, checked by a person (opened) | `reader-description.png` |
| Library: documents by group, direct search | `library.png`, `library-search.png` |
| Your files: permission prompt, file list with notes | `files-permission.png`, `files.png` |
| Phone width | `narrow.png` |
| Website demo: question, cited answer, reader (video) | `demo.mp4`, `demo.webm`, poster `demo.jpg` |

## Tokens

Defined on `:root` in `para/static/app.css`, redefined for dark mode under `:root[data-theme=dark]`. The theme is
chosen at the foot of the sidebar (Auto, Light, Dark; setting `theme`); Auto follows the system. A script in the
page head sets `data-theme` before the page is drawn, from a copy of the setting in `localStorage`.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--bg` | `#f2ebdf` | `#1c1411` | page, main column |
| `--panel` / `--panel-2` | `#faf5ec` / `#eadfcd` | `#261b17` / `#30231e` | reader, dialogs, question box / hover rows, reasoning |
| `--field` | `#fffdf8` | `#2a1e19` | inputs, text areas |
| `--sidebar` / `--sidebar-hover` / `--sidebar-line` | `#6b2419` / `#7d2e22` / `#8a3c2f` | `#3a120c` / `#4d1a12` / `#5a2219` | sidebar, phone top bar and drawer |
| `--sidebar-text` / `--sidebar-muted` | `#f4ead9` / `#d7a98f` | `#efe4d3` / `#b58a73` | sidebar text / labels, counts |
| `--ink` (`--text`) | `#2b1d16` | `#efe4d3` | body text, strong rules, outline buttons |
| `--muted` / `--muted-2` / `--faint` | `#5c4a3e` / `#7a6656` / `#9a8a7a` | `#b8a593` / `#8a7868` / `#6b5a4e` | secondary text / meta, mono labels / placeholders, dotted leaders |
| `--border` / `--border-field` | `#d6c7b2` / `#c9b8a1` | `#3d2c25` / `#5c4a3e` | hairlines / input borders |
| `--line-strong` | `--ink` | `#5c4a3e` | outline of dialogs, reader and question box |
| `--accent` / `--accent-hover` | `#8a3324` / `#6b2419` | `#d9876b` / `#e0927a` | primary buttons, links, eyebrows, focus, selected |
| `--accent-2` | `#f3e4cf` | `#3a2620` | selected cards, drag-over, 3 px focus ring |
| `--gold` | `#e9b872` | `#e9b872` | pilcrow, Recommended tag, remote badge, dark-mode New chat |
| `--cite` / `--cite-text` | `#ecdcc0` / `#8a3324` | `#3d2620` / `#f0b9a0` | regulation citations (hover: accent with cream text) |
| `--mark` | `#f0dfb6` | `#4a3a17` | the cited passage in the reader, search highlight |
| `--warn` / `--warn-bg` | `#5e3a0a` / `#f5e6c8` | `#f0c98a` / `#3a2a14` | notes, remote warning; your-file citations use `#7a4a0c` with a `#c99a4a` border |
| `--danger` / `--danger-bg` | `#a3291b` (text `#7d1d12`) / `#f6dcd3` | `#f08a7a` / `#3a1a14` | errors, Remove, too-big models |
| `--ok` | `#4a6420` (dot `#a9c27a`) | `#a9c27a` | private badge, success lines, Installed |
| `--none` | `#e07a5f` | `#e07a5f` | "No AI engine set up" badge |
| `--track` | `#e6d9c4` | `#3d2c25` | progress track (fill is `--accent`) |

Backdrop `rgba(43,29,22,.55)`; dialog shadow `0 30px 80px rgba(20,10,6,.35)`; reader shadow
`-24px 0 48px rgba(43,29,22,.18)`.

Type, bundled in `para/static/fonts/` so the app works offline (all SIL Open Font License):

- **Display**, Space Grotesk 500 (the brand 600), tracked tight: welcome heading `clamp(40px, 5vw, 62px)` at -.035em,
  the question `clamp(28px, 3.4vw, 42px)` at -.025em,
  dialog titles 32–40 px, reader title 26 px, group titles 22–24 px, sidebar links 19 px, the brand 40 px.
- **Body**, IBM Plex Sans 400–600 and italic: answers 17.5 px / 1.6, dialog text 15–17 px, reader 16 px. Italic for quiet
  notes (disclaimer, hints, "Nothing found.", "No chats yet.").
- **Labels**, IBM Plex Mono 400–500: eyebrows 11 px uppercase with .22–.24em tracking in `--accent`; button labels
  11–12 px uppercase; citations 12 px; sizes, dates and page refs 11–12.5 px.

Radius: dialogs 20 px, the question box 18 px, cards 14 px, notes and errors 12 px, inputs 10 px, citations 4 px,
buttons and pills 999 px, close buttons a 34 px circle. Spacing in multiples of 2 px, mostly 8, 10, 14, 22, 28.

## Components

- **Question** (`.msg.user`): a heading, not a bubble: "Your question" eyebrow, the question in display type, an
  ink rule under it. Each turn repeats it.
- **Citation** (`.cite`): inline mono tag, `--cite` background, label `<source> <ref>`, tooltip with the heading.
  Your files use the warm, bordered variant (`.cite.file`) so people can tell their documents from the regulations.
- **Cited list** (`.sources`, `.source-chip`): under the answer, a "Cited" (or "Cited and read") eyebrow over an
  ink rule, then one ruled row per passage: the ref in display type, a dotted leader, the heading in mono.
- **Activity** and **reasoning**: `<details>`, collapsed by default, a mono summary line with "+" or "−" in accent.
  Expanded activity is a bordered mono list; expanded reasoning is italic on `--panel-2`.
- **Buttons**: primary is an accent pill with a cream mono uppercase label; secondary an ink outline pill; danger a
  red outline pill; disabled at 40 % opacity.
- **Notes** (`.note`): warm background for honest-state messages; **errors** (`.error`) in danger colour, with a
  button to the setting that fixes them.
- **Project picker**: at the top of the sidebar, a "Project" label over a native select set in display type on a
  sidebar rule ("No project", each project, "New project…"), with a mono "Notes and files" link under it when a
  project is chosen. Recent shows the chosen project's chats only; the question box says "Ask a question about
  <project>…". Files dropped on the window go to the chosen project.
- **Theme**: a mono row under the privacy badge, "Theme" then Auto, Light and Dark; the chosen one in sidebar text
  with a gold underline.
- **Privacy badge**: bottom of the sidebar, a bordered box with a coloured dot. Green "Private" for the built-in
  engine or a local or LAN address, gold with the host name for anything else, coral "No AI engine set up" (opens
  Settings). On phones the dot alone sits at the right of the top bar.
- **Engine choice**: three radio cards in Settings → AI engine: an engine already running, the built-in engine, or an
  online AI service ("hosted" in the code). It shows a pill per service (Anthropic, Mistral, OpenAI, OpenRouter; the
  chosen one in `--accent-2`), a line saying where it's based with "Get an API key ↗", then the same address, key and model fields
  as a local engine. The model is typed or picked from the service's list. The warm warning names the host and says
  passages from the person's own files go there too. A hosted engine is saved as an external one; the card follows
  from the address.
- **Model rows**: ruled rows with a radio, name, Recommended tag (gold pill), description, mono size and memory line,
  and an action (Download, or Installed and Delete). Models too big for this computer are faded with the size line
  in red.
- **Library list**: a three-column grid (short name, title with the edition in italic under it, date) so long
  edition text can't squeeze the titles.
- **Reader**: document short name and full title, find-in-document filter, link to the original (gov.uk or
  legislation.gov.uk) or "Open file" for the person's files, licence line in the footer. For PDFs the link opens
  the page of the cited passage ("Original, page 34 ↗") and each passage header has its own "PDF page N ↗".
- **Figures**: diagrams and tables are shown as images above their passage's text, full reader width on a white
  card (the drawings are designed on white, so they stay white in dark mode). Click to enlarge to the window
  (a dialog with a close button); Escape or a click anywhere closes it. When the reader opens at a passage with a figure, it scrolls to the top of the figure.
  The words printed in a drawing ("Text in the drawing:", there for search and the AI) sit collapsed at the end of
  its passage, styled like the description, with a note that they aren't a description of the drawing.
- **Description of a drawing** (`.figure-desc`): when a diagram has a description checked by a person, it sits
  directly under the image, collapsed like the activity list: a mono "+ Description of the drawing" line. Opened, a
  2 px accent rule on the left sets off the description in body text, then an italic note in `--muted`, "Written by
  AI from the image, checked by a person. The document's text comes first." It's there to check what the AI was told
  about the drawing; the image's alt text carries it too, for screen readers. Its copy in the passage text (there
  for search and for the AI) isn't shown again.
- **Review page** (`library/review.html`, served by `library/describe.py review`, not part of the app): the same
  tokens and fonts; one card per drawing with the image on white, the ref in display type, the caption in italic,
  the model and dates in mono, and Approve (primary), Edit (outline) and Reject (danger outline) buttons; once
  decided, Withdraw approval or Undo rejection. A warm note says when a drawing's image has changed since its
  description was approved. The description shown is the model's second
  reading (its draft corrected against the image), and there's nothing else to compare: one text against one image.
  A correction someone other than the model made shows as "corrected by …" in the mono line. Two views, Descriptions
  and Crops, switched by pills under the title. Crops is a grid of tiles by document (the image on white in a 200 px
  box, the label and page in mono, a "Bad crop" tick box); a ticked tile has a danger-coloured border, a recut one a
  warm note, and filters show all, marked bad or recut since marked, for one document or all. A description whose
  crop is ticked carries a "Crop marked bad" tag.

## Writing in the interface

British English, sentence case, plain verbs on buttons ("Download", "Save", "Open file"). Say what happened and
what to do next ("The AI cited 1 passage it never looked up, so it was removed."). No "oops", no exclamation marks,
no emoji anywhere.

## Known gaps

- **Tables in the reader** show the table's image, then its text as pipe-separated rows
  (`Private stair | 150 | 220`). The text could be collapsed under the image, or rendered as an HTML table.
- **Figure crops** are found from captions, so a few are cut short or include the next heading, and some
  diagrams without a recognisable caption have no image. "Original, page N" is always there as a fallback.
- **Reader find** hides non-matching passages rather than highlighting matches.
- **No app icon** yet (`packaging/icon.icns`, `packaging/icon.ico`). The ¶ mark in the sidebar is the starting point.
- **Keyboard**: dialogs and citations are reachable by Tab, but there's no shortcut for New chat or focusing the
  question box.
