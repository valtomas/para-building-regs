# AGENTS.md

Guidance for AI coding agents (and people) working on Para. Read this before changing code. For the interface,
read [DESIGN.md](DESIGN.md).

## What Para is

A desktop app that answers questions about the building regulations in England with citations to the exact
paragraph. A local or remote AI model searches a bundled library with tools, reads passages, and answers citing
passage ids; the app checks every citation. It must stay:

1. **Truthful about sources.** Nothing the person sees may claim a source the model didn't read. Citation checking
   in `core._check_citations` is load-bearing: don't weaken it to make a model look better.
2. **Private by default.** The server listens on 127.0.0.1. Nothing leaves the computer except calls to the
   engine the person chose, update checks (GitHub releases) and downloads they start.
3. **Engine-agnostic.** Everything goes through the OpenAI-compatible `/chat/completions` API in `para/llm.py`. No
   engine-specific code paths except settings sent as optional extras that servers may ignore.
4. **Light.** Python standard library only, plus `pypdf` (reading PDFs the person adds), `pywebview` (the window)
   and `truststore` (system certificates). Both optional ones must degrade gracefully. No frontend build step: plain
   HTML, CSS and JS in `para/static/`.

## Map

| Path | What it does |
|---|---|
| `para/server.py` | HTTP server (stdlib `ThreadingHTTPServer`), JSON API, SSE chat stream, chats on disk, update checks, window start-up |
| `para/core.py` | One chat turn: system prompt, tool loop (`search_library`, `read_passage`, `show_passage`), nudges, rescue, citation checking |
| `para/llm.py` | OpenAI-compatible streaming client; splits `<think>` and `reasoning_content`; probes known local servers |
| `para/engine.py` | Built-in engine: downloads llama.cpp's `llama-server` and GGUF models, starts and stops it; `Engine.client()` picks the configured engine |
| `para/models.json` | Built-in model catalogue (also published with each release and fetched by installed apps) |
| `para/store.py` | SQLite FTS5 store (sources, passages, BM25 search, read, document), used for both libraries |
| `para/library.py` | The regulations library plus the person's files, searched together; ids `r…` and `f…` |
| `para/splitter.py` | Splits text into passages at paragraph numbers, tables, headings; skips contents and index pages |
| `para/myfiles.py` | Reads the person's files (PDF, DOCX, EPUB, HTML, text) and keeps the files index in sync with their folder |
| `para/paths.py` | App data folders, settings (`settings.json`) |
| `para/static/` | The interface: `index.html`, `app.css`, `app.js` |
| `library/build.py` | Builds `library/dist/regs.sqlite` from gov.uk (content API + PDFs) and legislation.gov.uk (CLML XML) |
| `library/figures.py` | Finds diagrams and tables in the PDFs from their captions, renders them to PNG, and takes drawing labels out of the running text |
| `library/legislation.py` | Turns legislation XML into passages cited as `reg 7`, `s 120D`, `Sch 1 Pt B` |
| `library/sources.json` | What goes into the library |
| `packaging/` | PyInstaller spec and launcher |
| `.github/workflows/release.yml` | Tests on every push; on a `v*` tag: library, Mac and Windows apps, GitHub release; weekly library refresh |
| `tests/` | `test_app.py` (end to end through the HTTP API) and `fake_llm.py` (a stand-in OpenAI-compatible server) |

## Run and test

```bash
python3 tests/test_app.py                  # must pass before every commit; no engine or network needed
python3 -m pyflakes para library tests     # keep it clean
python3 -m para --browser                  # run from source in the browser
python3 library/build.py                   # build the library (needs poppler-utils; Pillow shrinks images)
                                           # --offline reuses library/cache
```

Use `PARA_APP_DIR` (settings, chats, indexes), `PARA_LIBRARY` (a library file) and `HOME` (the files folder) to
run against throwaway folders. Never test against the person's real app folder.

Testing with a real engine: Lemonade (`http://127.0.0.1:13305/api/v1`; older versions `:8000`), Ollama (`http://127.0.0.1:11434/v1`) and
LM Studio (`http://127.0.0.1:1234/v1`) are found by *Find engines on this computer*. When you change prompts or the
tool loop, ask a real model a few questions from different Parts and check the citations open the right
paragraphs.

## Conventions

- **Python**: 3.10+, standard library first, 120-character lines, docstrings that say why. Small functions; no
  classes where a function will do. Catch broad exceptions only at boundaries (a file the person added, a network
  call, a background thread) and say so in a comment.
- **Library quality beats model cleverness.** If answers are wrong because a passage is garbled, fix
  `splitter.py` or `build.py`, rebuild, and look at the passages (`sqlite3 library/dist/regs.sqlite`). Check tables
  keep their numbers and paragraph refs stay in sequence (`splitter.plausible`).
- **Diagrams and tables**: `library/figures.py` finds each one from its caption and crops it from the page. Whether
  a document puts diagram captions above or below the drawing is decided per document by a vote; tables are always
  captioned above. When you change it, render a few figures from ADB, ADK, ADL and ADM and look at them: a crop
  that cuts a drawing short or swallows the next paragraph is easy to miss in the numbers. The text the AI reads for
  a diagram is its caption plus "Text in the drawing:" (the labels); never present that as a description of the
  drawing.
- **Ids**: regulations passages are `r<rowid>`, the person's files `f<rowid>`. Rowids change between library
  builds, so saved chats can point at a different passage after an update. Don't build features that assume they
  are stable across versions.
- **Prompts** live in `core.py` (`BASE_PROMPT`, nudges). People can replace the instructions in Settings, so the
  app must still work (citations checked, tools offered) with any instructions.
- **Writing**: British English in everything the person reads. Plain words, no marketing. Name documents the way
  practitioners do ("ADB Vol 1, para 2.13").
- **Licensing**: the bundled library may contain only material that can be redistributed (Open Government
  Licence). Don't add NHBC, LABC, BSI or other copyrighted material to `sources.json`; people add their own copies
  under Your files.
- **Security**: keep the Origin check in `Handler._allowed`. `/api/open` may only open http(s) URLs, the files
  folder, or files already in the files index. Don't add endpoints that read arbitrary paths.
- **Commits**: small, with a message that says why. Run the tests first.

## Adding things

- **A new document**: add it to `library/sources.json` (legislation: `path` on legislation.gov.uk; gov.uk: a
  collection or page path), run `library/build.py`, check its passages, and add a short name in `build.SHORT` if
  the automatic one is clumsy.
- **A new built-in model**: add it to `para/models.json` with the exact Hugging Face repo and file, size and memory
  needed. Test it answers with citations through the built-in engine before listing it.
- **A new API endpoint**: in `server.Handler`, return JSON through `_send`, and add a check to `tests/test_app.py`.
- **A new setting**: add the default to `paths.DEFAULTS` (only keys there can be saved), then the UI.
