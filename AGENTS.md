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
| `para/engine.py` | Built-in engine: downloads llama.cpp's `llama-server` and GGUF models, starts and stops it (memory check, idle stop, leftover clean-up); `Engine.client()` picks the configured engine |
| `para/models.json` | Built-in model catalogue (also published with each release and fetched by installed apps) |
| `para/store.py` | SQLite FTS5 store (sources, passages, BM25 search, read, document), used for both libraries |
| `para/library.py` | The regulations library plus the person's files, searched together; ids `r…` and `f…` |
| `para/splitter.py` | Splits text into passages at paragraph numbers, tables, headings; skips contents and index pages |
| `para/myfiles.py` | Reads the person's files (PDF, DOCX, EPUB, HTML, text) and keeps the files index in sync with their folder |
| `para/paths.py` | App data folders, settings (`settings.json`) |
| `para/static/` | The interface: `index.html`, `app.css`, `app.js` |
| `library/build.py` | Builds `library/dist/regs.sqlite` from gov.uk (content API + PDFs) and legislation.gov.uk (CLML XML) |
| `library/figures.py` | Finds diagrams and tables in the PDFs from their captions, renders them to PNG, and takes drawing labels out of the running text |
| `library/describe.py` | Descriptions of the diagrams: a vision model writes them (`run`) and a second reading leaves notes (`check`), a person approves them on a local review page (`review`, `library/review.html`); `pending` lists what's left, `auto` drafts overnight and opens a pull request; kept in `library/descriptions/` |
| `library/legislation.py` | Turns legislation XML into passages cited as `reg 7`, `s 120D`, `Sch 1 Pt B` |
| `library/sources.json` | What goes into the library |
| `library/check.py` | Compares a new library with the published one and fails the build if a document is missing or much smaller, so a broken rebuild never reaches installed apps |
| `packaging/` | PyInstaller spec and launcher; `packaging/linux/install.sh` adds the Linux app to the applications menu |
| `para/qtprobe.py` | Linux: checks in a separate process that Qt can open a window (Qt aborts instead of raising), so the app falls back to the browser |
| `site/` | The website, pararegs.co.uk: one static page in the app's look; download buttons read the latest GitHub release |
| `.github/workflows/descriptions.yml` | After each library build: the "descriptions" issue of drawings to draft or review; optional drafting with a hosted model |
| `.github/workflows/pages.yml` | Publishes `site/` to GitHub Pages, adding the app's fonts and `docs/screenshots/answer-reader.png` |
| `.github/workflows/release.yml` | Tests on every push; on a `v*` tag: library, Mac and Windows apps, GitHub release; weekly library refresh |
| `tests/` | `test_app.py` (end to end through the HTTP API) and `fake_llm.py` (a stand-in OpenAI-compatible server) |
| `tests/ask_models.py` | Asks real models the test questions through a running app and scores them; results in `docs/model-tests.md` |

## Run and test

```bash
python3 tests/test_app.py                  # must pass before every commit; no engine or network needed
python3 tests/test_check.py                # the publishing gate in library/check.py
python3 -m pyflakes para library tests     # keep it clean
python3 -m para --browser                  # run from source in the browser
python3 library/build.py                   # build the library (needs poppler-utils; Pillow shrinks images)
                                           # --offline reuses library/cache
```

Use `PARA_APP_DIR` (settings, chats, indexes), `PARA_LIBRARY` (a library file) and `HOME` (the files folder) to
run against throwaway folders. Never test against the person's real app folder.

Testing with a real engine: Lemonade (`http://127.0.0.1:13305/api/v1`; older versions `:8000`), Ollama
(`http://127.0.0.1:11434/v1`) and LM Studio (`http://127.0.0.1:1234/v1`) are found by *Find engines on this
computer*. When you change prompts or the tool loop, ask real models the test questions (`tests/ask_models.py`) and
check the citations open the right paragraphs. Unload the external engine's models (`lemonade unload`) before
testing the built-in engine, and stop the app normally (not `kill -9`): two engines holding models can exhaust
memory, and on a Strix Halo a GPU load that didn't fit has crashed the whole system.

## Conventions

- **Python**: 3.10+, standard library first, 120-character lines, docstrings that say why. Small functions; no
  classes where a function will do. Catch broad exceptions only at boundaries (a file the person added, a network
  call, a background thread) and say so in a comment.
- **Library quality beats model cleverness.** If answers are wrong because a passage is garbled, fix
  `splitter.py` or `build.py`, rebuild, and look at the passages (`sqlite3 library/dist/regs.sqlite`). Check tables
  keep their numbers and paragraph refs stay in sequence (`splitter.plausible`). Two-column pages are read a column
  at a time (`splitter.unweave_columns`) and contents and index pages are skipped (`splitter.skipped_pages`): when
  you change either, compare passage counts and refs per document before and after.
- **Diagrams and tables**: `library/figures.py` finds each one from its caption and crops it from the page. Whether
  a document puts diagram captions above or below the drawing is decided per document by a vote; tables are always
  captioned above. On a page set in two columns, a figure with running text or another figure's caption beside its
  caption is cropped to its own column (ADH, ADE, ADJ, ADM Vol 2); one across the page is not. The text extractor
  can't see the drawings, so the crop found from the text is then narrowed to where the rendered page has ink once
  the running text is whited out (`figures.ink_box`): that stops a crop running on into an unnumbered paragraph.
  When you change it, render a few figures from ADB, ADE, ADH, ADK, ADL and ADM and look at them: a crop that cuts a
  drawing short or swallows the next paragraph is easy to miss in the numbers. Any change to a figure's image takes
  its reviewed description out of the library until a person approves it again. The text the AI reads for a
  diagram is its caption plus "Text in the drawing:" (the labels); never present that as a description of the
  drawing.
- **Descriptions of drawings**: `library/describe.py run` asks a vision model (`DEFAULT_MODEL`, Qwen3.8 27B with thinking on, on Lemonade) to
  describe each diagram from its image, and saves `library/descriptions/<key>.json` (model, date, the image's SHA-256
  and a 16 by 16 grey thumbnail, `reviewed: false`). `describe.py check` has a model read each draft against its image
  and leave notes for the reviewer (`check` in the file); it's advisory and never changes a description or approves
  one. `describe.py review` opens a page where a person approves, corrects or rejects each one (reject a crop that is
  cut short: it isn't described again until the image changes). The build adds only reviewed descriptions of the
  same image, after the passage text and labelled "Description of the drawing (written by AI from the image, checked
  by a person):"; the reader shows them under the image, and the prompt says they come second to the document's
  text. "The same image" is the same bytes, or the same size and nearly the same thumbnail (`describe.same_image`),
  because GitHub's PDF renderer and a person's own can differ by a shade; a moved or recut crop doesn't match.
  Rules: never mark a description reviewed yourself (only a person, on the review page, may); you may write a
  corrected draft for a person to review (record it in `corrected_by`, keep `model_description`); never regenerate
  one whose image is unchanged; never overwrite a reviewed one (if its image changes, it leaves the library until a
  person approves it again). The key (`ADB1_Diagram-3.8_p40`; a table continued on the same page adds `_2`) names
  the figure across builds, because rowids don't. Tables are included only where their image holds more than their
  text (ticks, filled and empty circles, shading, a drawing): `run` asks the model once per table, with thinking off,
  and records a plain table as `not_needed` until its image changes.
- **The descriptions loop**: after each library build, the *Drawing descriptions* workflow lists what needs a model
  or a person (`describe.py pending`, no AI) in one issue labelled "descriptions". Drafting runs overnight on a
  computer with a vision model, every night from 01:30 (`describe.py auto --until 05:30`, a systemd user timer on
  the maintainer's PC): it builds the library in a worktree of the latest main, writes and checks one drawing after
  another, stops in time, and opens a pull request of drafts, or adds to the one still open, so a week of nights
  goes into one review. The same
  workflow can draft with a hosted model if `DESCRIBE_BASE_URL`, `DESCRIBE_MODEL` (repository variables) and
  `DESCRIBE_API_KEY` (secret) are set; it's off otherwise.
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
- **Code licence**: Para is AGPL-3.0-or-later (`LICENSE`). Only add dependencies, fonts or copied code under
  licences compatible with it (MIT, BSD, Apache-2.0, LGPL, GPL-3.0, OFL for fonts); check before adding. Keep the
  licence notice and source link in Settings → Updates & about.
- **Security**: keep the Origin check in `Handler._allowed`. `/api/open` may only open http(s) URLs, the files
  folder, or files already in the files index. Don't add endpoints that read arbitrary paths.
- **Commits**: small, with a message that says why. Run the tests first.
- **Memory**: the built-in engine holds gigabytes, so it must never outlive the app or load what won't fit.
  `Bundled.start` checks free system memory plus free graphics-card memory (`available_ram_gb`, `free_vram_gb`)
  and leaves the GPU/CPU split to llama.cpp's `--fit` (don't pass `-ngl`); the engine stops after
  `IDLE_MINUTES` unused, on SIGTERM/SIGHUP, and a copy left by a killed app is stopped at the next start
  (`engine.pid`). External engines manage their own memory: don't add engine-specific unload calls.

## Adding things

- **A new document**: add it to `library/sources.json` (legislation: `path` on legislation.gov.uk; gov.uk: a
  collection or page path), run `library/build.py`, check its passages, and add a short name in `build.SHORT` if
  the automatic one is clumsy.
- **A new built-in model**: add it to `para/models.json` with the exact Hugging Face repo and file, size and memory
  needed. Test it answers with citations through the built-in engine before listing it.
- **A new API endpoint**: in `server.Handler`, return JSON through `_send`, and add a check to `tests/test_app.py`.
- **A new setting**: add the default to `paths.DEFAULTS` (only keys there can be saved), then the UI.
