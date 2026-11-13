# Plan: a version that runs on the website

Status: proposed (13 November 2026), nothing built. Read AGENTS.md and DESIGN.md first; this plan follows their
rules, in particular "private by default" and "nothing the person sees may claim a source the model didn't read".

## Why

Asking someone to download and install an app is a lot for people who aren't comfortable with computers, which is
most of the people Para is for. The installers aren't signed, so Windows and macOS also warn about them. A page
that works when opened removes both.

## What it shouldn't be

Not the app with a small model squeezed into a browser tab as the first thing people meet. That would show the
weakest model at its slowest: a download of 2 GB or more before the first answer, a minute or more per answer on a
laptop without a real graphics card (an estimate, not measured), and nothing at all on phones and locked-down office
computers. The desktop app stays the proper version.

Not a model we host, either. It would cost money per question and passages from people's questions would leave
their computer, which is the one thing Para says it doesn't do.

## Three steps, each useful on its own

### 1. The library on the website, no AI

Browse the documents by Part, search them, open a paragraph with its reference, figures and link to the original:
what the Library dialog and the reader do in the app now. Instant, works on a phone, nothing to download.

- The interface is already plain HTML, CSS and JS (`para/static/`). The reader, the Library and the welcome list of
  Parts carry over; the chat, Settings, Your files and projects are left out.
- Search runs in the page: SQLite compiled to WebAssembly with FTS5, reading `regs.sqlite`. Either the whole file is
  downloaded once and kept (it's the same file each release publishes as `regs.sqlite.gz`), or it's read in pieces
  over HTTP range requests so the first search doesn't wait for all of it. Measure the file and the figures first:
  the images may need to be served as separate files, not from inside the database.
- Each paragraph gets an address that can be shared (`/library/ADB1#2.13`), by document key and reference, never by
  rowid (rowids change between builds).
- Hosting stays static (GitHub Pages now). No server, no accounts, no analytics.
- Every page says what edition of the library it shows, as the app does.

This is worth doing even if the next two steps never happen: a link to the exact paragraph is useful to anyone.

### 2. Measure a model in the browser before building anything

One day's work to decide whether step 3 is worth it.

- Load the smallest model worth offering through WebGPU (wllama or WebLLM) on an ordinary laptop with integrated
  graphics, not the development machine. Give it a real Para prompt: the instructions, the tools, and three or four
  passages (several thousand tokens).
- Record: time to download, time to load, time to the first word, words per second, memory used, whether the tab
  survives.
- Go on only if a cited answer arrives in well under a minute on that laptop.

Which model: at the last count Granite 4.2 3B (2.2 GB, 29 to 31 of 36 test questions) is the smallest that's
listed and suggested. Models kept at one or two bits per weight were tried on 13 November (run G in
`docs/model-tests.md`): a 3.8 GB one matched Granite 3B's score but took a minute and a half per answer on a fast
desktop, and the 1.2 GB one got 20 of 36. A model of 1 to 1.5 GB that scores like Granite 3B would change this plan
a good deal; none has turned up yet.

### 3. "Ask here", as an opt-in on the library page

Only where the browser has WebGPU and enough memory, and only after the person chooses it: "Ask a question here
(downloads 2 GB, kept in this browser)". Everyone else sees the library and a link to the desktop app.

- **One copy of the rules.** The tool loop and citation checking (`para/core.py`, `store.py`, `library.py`,
  `anchors.py`) are standard-library Python. Run them in the page with Pyodide rather than rewriting them in JS, so
  citation checking can't drift between the two versions. To check first: that Pyodide's SQLite has FTS5, and that
  the model can be reached from Python as an OpenAI-style call (a small JS shim standing in for `llm.py`'s HTTP
  client). If Pyodide won't do, the port to JS needs the citation tests from `tests/test_app.py` run against it.
- **The same tests.** A browser model is listed only after the 36 questions, like any other, run through the page.
- **Honest states.** The page says the model is small and slower than the app's, shows the download's size before
  it starts, and says the browser may delete the model to save space. It keeps the notes about extract-only
  citations and figures not found.
- **Private.** Nothing is sent anywhere: the model, the library and the question stay in the tab. The privacy badge
  says so. Chats are kept in the browser only, with a line saying that clearing site data removes them.
- **Left out at first:** Your files, projects, drawings, online AI services (a key typed into a web page is a
  different promise from one saved on your own computer).

## What to settle before starting

- How big `regs.sqlite` is with and without its images, and how long a first search takes over range requests.
- Whether the website stays one page in the app's look with the library added, or the library gets its own
  address (`pararegs.co.uk/library`).
- Whether shared paragraph links should show a preview (title and reference) when pasted into email or chat. That
  needs a page per paragraph generated at build time, not only a script in one page.

## Order and size

Step 1 is a week or two and has no unknowns. Step 2 is a day. Step 3 is a few weeks if Pyodide works, more if the
loop has to be ported, and shouldn't start until step 2 says yes.
