# Para

**Building regulations, cited.** A free, open-source AI assistant for the building regulations in England that runs on your own computer. Ask it a question
("maximum travel distance from a flat entrance door in a single-stair block?") and it searches a library of the
Approved Documents, the Building Regulations 2010, the Building Safety Act and its guidance, reads the relevant
paragraphs, and answers with a link to each one, so you can check exactly where it came from.

![Para answering a question about stair rise, with the cited table open in the reader](docs/screenshots/answer-reader.png)

- **Cites the paragraph.** Answers cite passages such as *ADB Vol 1 Table 3.1* or *BR 2010 reg 7*. Click one to
  open the document at that paragraph. The app checks every citation against what the AI actually looked up and
  removes any it made up.
- **Any engine you like.** Use an AI engine you already run: Ollama, LM Studio, Lemonade, llama.cpp, Jan, vLLM or
  any other OpenAI-compatible server, on this computer or your network. Or let the app download and run one itself
  (llama.cpp with a model suited to your computer's memory).
- **Private.** With a local engine, your questions and files never leave your computer. The app shows whether
  it's private, and warns you if your engine is somewhere else.
- **Your own documents.** Add specifications, fire strategies, building control comments or manufacturers'
  guidance (PDF, Word, EPUB, HTML, text) and they're searched along with the library. The app asks you to confirm
  you're allowed to use each document this way first, because many publishers' licences (some British Standards,
  warranty providers' technical manuals) don't allow it.
- **Project notes.** Tell it about the project ("6-storey block of flats, top storey 17 m, sprinklered") and every
  answer takes it into account.
- **Kept up to date.** The library is rebuilt every week from gov.uk and legislation.gov.uk, and the app offers
  the new edition when the documents change.

## What's in the library

All Crown copyright, published under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/),
which allows redistribution with attribution:

| | |
|---|---|
| Approved Documents | A, B (vol 1 and 2), C, D, E, F (vol 1 and 2), G, H, J, K, L (vol 1 and 2), M (vol 1 and 2), O, P, Q, R (vol 1 and 2), S, T, 7; current amendment booklets and corrections; the gov.uk FAQs; the Manual to the Building Regulations |
| Legislation (consolidated, from legislation.gov.uk) | Building Regulations 2010; Building Act 1984; Building Safety Act 2022; Building (Higher-Risk Buildings Procedures) (England) Regulations 2023; Higher-Risk Buildings (Descriptions and Supplementary Provisions) Regulations 2023; Regulatory Reform (Fire Safety) Order 2005; Fire Safety (England) Regulations 2022 |
| Government guidance (gov.uk) | Criteria for higher-risk buildings; building control approval, changes, completion certificates and transitional arrangements for higher-risk buildings; the golden thread; mandatory occurrence reporting; meeting building requirements (dutyholders); the Building Safety Act overview |

The list is in [library/sources.json](library/sources.json). NHBC Standards, LABC Warranty's technical manual,
British Standards and similar are free to read online in some cases but are not licensed for redistribution or AI
use, so they're not included; if your licence allows it, add your copy under **Your files**.

The library covers **England**. Wales, Scotland and Northern Ireland have their own regulations.

## Install

**Mac** (Apple Silicon, macOS 12 or newer)
1. Download `Para-mac.zip` from the [latest release](../../releases/latest) and double-click it.
   Move **Para** to Applications.
2. The first time you open it, macOS says it can't verify the app (it isn't from the App Store). Open
   **System Settings › Privacy & Security**, scroll to the message about Para and click
   **Open Anyway**. You only do this once.

**Windows** (10 or 11, 64-bit)
1. Download `Para-windows.zip`, right-click it and choose **Extract All**.
2. Open the extracted folder and double-click **Para.exe**. If Windows SmartScreen appears,
   click **More info**, then **Run anyway**.

**Then** choose an AI engine in the window that opens:
- **Use an engine I already run.** Click *Find engines on this computer*, or type the server's address
  (e.g. `http://127.0.0.1:11434/v1` for Ollama, `http://127.0.0.1:1234/v1` for LM Studio,
  `http://127.0.0.1:8000/api/v1` for Lemonade). Pick a model and press Save. Models with tool calling work best.
- **Built-in engine.** The app suggests a model for your computer's memory, downloads llama.cpp and the model from
  Hugging Face, and runs them itself.

| Your computer | Suggested model | Download |
|---|---|---|
| 32 GB memory or more | Qwen3 30B-A3B Instruct 2507 | 18.6 GB |
| 24 GB | gpt-oss 20B | 12.1 GB |
| 16 GB | Qwen3 8B | 5.0 GB |
| 8 GB | Qwen3 4B Instruct 2507 | 2.5 GB |

The model list ([para/models.json](para/models.json)) is also published with each release, so installed
copies pick up better models without reinstalling.

## Share it with a team

To run one copy for everyone in an office, start it on a machine that can reach your AI engine:

```bash
python3 -m para --host 0.0.0.0 --port 8085
```

and open `http://<that-machine>:8085` in a browser. Anyone who can reach that port can use it and change its
settings, so only do this on a trusted network.

## Run from source

```bash
pip install pypdf pywebview truststore   # pywebview and truststore are optional
python3 -m para                       # in its own window (or the browser without pywebview)
python3 -m para --browser             # in your browser
```

Without a built library the app offers to download the latest one. To build it yourself (needs `pdftotext` from
poppler-utils):

```bash
python3 library/build.py                 # library/dist/regs.sqlite, from gov.uk and legislation.gov.uk
python3 library/build.py --release       # also regs.sqlite.gz and library.json for a release
```

Tests run against a stand-in model, with no engine or library download needed:

```bash
python3 tests/test_app.py
```

## How it works

```
question ─► AI model (any OpenAI-compatible server) ─► search_library ─► SQLite FTS5 index ─► passages
                ▲                                       read_passage   ─► paragraphs in full
                └──── answer citing [r1234] ◄───────────┘ show_passage  ─► opens the reader
```

- `library/build.py` downloads the documents (gov.uk content API, legislation.gov.uk XML), splits them at their
  own paragraph numbers ("2.13", "Table 3.1", "reg 7"), keeps table rows together, repairs ligatures lost in PDF
  extraction, and indexes them with SQLite FTS5.
- `para/core.py` runs each question: the model searches and reads with tools, then answers citing passage ids.
  Citations to passages it never saw are removed. Models without tool support get the best passages attached to
  the question instead.
- `para/llm.py` talks to any OpenAI-compatible `/chat/completions` endpoint, including reasoning output from
  servers that stream it.
- `para/engine.py` installs and runs the built-in engine (llama.cpp's `llama-server`).
- `para/myfiles.py` reads your own files into a separate index in the app's data folder.
- `para/server.py` serves the page on 127.0.0.1 and opens it in a window (pywebview).
- Releases are built by [.github/workflows/release.yml](.github/workflows/release.yml): push a tag like `v0.1.0`.

Your settings, chats and the index of your files are kept in `~/Library/Application Support/Para`
(Mac), `%LOCALAPPDATA%\Para` (Windows) or `~/.local/share/para` (Linux). Your
files themselves are in `~/Para Files`.

A personal open-source project by [valtomas](https://github.com/valtomas), inspired by
[Survival LLM](https://github.com/anonnerdfrenzy/survival-llm). Contributions welcome: see [AGENTS.md](AGENTS.md) for
how the code fits together and [DESIGN.md](DESIGN.md) for the interface.

## Licence

Code: MIT ([LICENSE](LICENSE)). Library content: Crown copyright, Open Government Licence v3.0.

AI makes mistakes. Every answer shows its sources so you can check them against the documents. This is not a
substitute for professional judgement or for building control approval.
