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
4. **Calm and professional.** It's a reference tool used at work: neutral colours, one accent, no animation beyond
   the typing cursor, no illustrations.
5. **Works without explanation.** Someone who has never used an AI tool should get from install to a cited answer
   without reading anything.

## Layout

```
┌───────────────┬──────────────────────────────────┬───────────────────────┐
│ ¶ Para        │  conversation                    │  Reader (when open)   │
│ + New chat    │   question (right, tinted)       │   document · title    │
│ chat list     │   ▸ Show reasoning               │   find · Original ↗   │
│               │   ▸ Looked up: 2 searches, …     │   passages, cited one │
│               │   answer with [ADK Table 1.1]    │   highlighted         │
│               │   Cited: chips                   │                       │
│ Library       ├──────────────────────────────────┤   licence             │
│ Your files    │  question box                    │                       │
│ Settings      │  Reasoning · Length      [Send]  │                       │
│ 🔒 Private    │  disclaimer                      │                       │
└───────────────┴──────────────────────────────────┴───────────────────────┘
   250 px              flexible, text ≤ 860 px          min(520 px, 45vw)
```

Dialogs (native `<dialog>`): **Settings** (tabs: AI engine, Instructions, Project notes, Updates & about),
**Your files**, **Library** (browse documents and search without AI), and the **permission** prompt shown the first
time someone adds a file.

## Screens

| Screen | Screenshot |
|---|---|
| First run: choose an engine | `setup-engine.png` |
| Built-in engine: model cards, recommended one marked | `setup-builtin.png` |
| Empty chat with example questions | `welcome.png` |
| Answer with inline citations, activity expanded | `answer.png` |
| Answer with the reader open at the cited table | `answer-reader.png`, `answer-reader-dark.png` |
| Library: documents by group, direct search | `library.png`, `library-search.png` |
| Your files: permission prompt, file list with notes | `files-permission.png`, `files.png` |
| Phone width | `narrow.png` |

## Tokens

Defined on `:root` in `para/static/app.css`, redefined for dark mode under `prefers-color-scheme: dark`.

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--bg` | `#f6f6f8` | `#111116` | page |
| `--panel` / `--panel-2` | `#ffffff` / `#f0f0f4` | `#1a1a22` / `#23232d` | sidebar, cards, inputs / hover, code |
| `--border` | `#e2e2e8` | `#2e2e3a` | all hairlines |
| `--text` / `--muted` | `#1d1d22` / `#6b6b76` | `#e6e6ea` / `#9a9aa6` | body / secondary |
| `--accent` | `#4f46e5` | `#8b8ef8` | primary buttons, links, focus, selected |
| `--accent-2` | `#eef0ff` | `#262848` | question bubble, selected cards |
| `--cite` / `--cite-text` | `#eef0ff` / `#3730a3` | `#2a2c52` / `#c7c9ff` | regulation citations |
| `--warn` / `--warn-bg` | `#b45309` / `#fff7e6` | `#f0b35a` / `#33281a` | notes, your-file citations, remote engine |
| `--ok` / `--danger` | `#15803d` / `#c0392b` | `#4ade80` / `#f87171` | private badge / errors, delete |
| `--mark` | `#fff3a3` | `#5a4d10` | the cited passage in the reader |

Type: the system UI font stack at 15 px / 1.55; 12–13 px for secondary text; headings are weight 700, never larger
than 26 px. Radius: 10 px (cards, dialogs 14 px, chips 6 px or pill). Spacing in multiples of 2 px, mostly 8, 10,
14, 24.

## Components

- **Citation** (`.cite`): inline button, `--cite` background, label `<source> <ref>`, tooltip with the heading.
  Your files use the warm variant (`.cite.file`) so people can tell their documents from the regulations.
- **Source chips** (`.source-chip`): under the answer, "Cited" then "read" passages, pill outline.
- **Activity** and **reasoning**: `<details>`, collapsed by default, 13 px muted.
- **Notes** (`.note`): warm background for honest-state messages; **errors** (`.error`) in danger colour, with a
  button to the setting that fixes them.
- **Privacy badge**: bottom of the sidebar. Green "Private" for the built-in engine or a local or LAN address,
  amber with the host name for anything else, red "No AI engine set up" (opens Settings).
- **Model cards**: radio, name, Recommended tag, one-line description with download size and memory, action
  button (Download / Delete).
- **Reader**: document short name and full title, find-in-document filter, link to the original (gov.uk or
  legislation.gov.uk) or "Open file" for the person's files, licence line in the footer.

## Writing in the interface

British English, sentence case, plain verbs on buttons ("Download", "Save", "Open file"). Say what happened and
what to do next ("The AI cited 1 passage it never looked up, so it was removed."). No "oops", no exclamation marks,
no emoji in messages (icons in the sidebar and activity list are the only exception).

## Known gaps

- **Phone width**: the sidebar is hidden below 900 px with no way to open it, so chats, Library, Your files and
  Settings are unreachable there. Needs a menu button. (Matters only when serving to a network with `--host`.)
- **Tables in the reader** show as pipe-separated rows (`Private stair | 150 | 220`). Render rows containing ` | `
  as an HTML table.
- **Icons** are emoji and look different on each platform. Replace with a small inline SVG set.
- **Reader find** hides non-matching passages rather than highlighting matches.
- **No app icon** yet (`packaging/icon.icns`, `packaging/icon.ico`). The ¶ mark in the sidebar is the starting point.
- **Keyboard**: dialogs and citations are reachable by Tab, but there's no shortcut for New chat or focusing the
  question box.
