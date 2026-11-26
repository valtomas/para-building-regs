# To do

0.2.0 to 0.2.2 were released on 12 November 2026. 0.2.1 made models with a strict chat template work (Mistral's),
added IBM Granite 4.2 3B for 8 and 12 GB computers and put each built-in model's test score on its row
(`docs/model-tests.md`, run F). 0.2.2 fixed the packaged Linux app closing on Copy with references and opening
nothing from its links, and reworked closing dialogs and the empty chat. 0.2.3 (13 November) put the built-in engine
first with one recommended model, added "What is a model?", stopped offering Gemma 4 E4B and Qwen3 4B, and made the
engine stop when the app is killed or crashes: that last one is not yet tried on Windows (End task in the Task
Manager with a model loaded, then check llama-server is gone).
0.2.4 (14 November) is the first version that can update its own engine (offered in Settings, never installed
unasked) and the first to run llama.cpp's CUDA builds for NVIDIA cards, with Vulkan as the fallback. 0.2.5
(15 November) suggests the biggest model that fits on the graphics card's own memory, and lists every downloaded model
file under Settings → Downloaded to this computer, with a Delete button.
0.2.6 (16 November) is for fresh Windows computers: downloads use the system's certificates, the Visual C++ runtime
comes with the app, the AI engine downloads itself when the model is there and it isn't, a CUDA build that can't start
no longer rules CUDA out, and the note under an answer names the citations made from an extract.
0.2.7 (16 November) suggests Granite 4.2 8B only from a graphics card of about 11 GB (it split between the card and the
processor on an 8 GB Quadro RTX 4000, where the 3B ran fully on the card at about 80 tokens a second), and queues a
download pressed while another is running.
0.2.8 (25 November) adds Inkling 2 Mini (34 to 36 of 36 in four runs; suggested from 32 GB of memory or a 24 GB graphics
card), moves the AI engine to llama.cpp b12611, the first build that reads it, and stops suggesting Qwen3.5 35B-A3B.
0.2.9 (26 November) brings a library with descriptions of 424 diagrams, each checked by a person against its image and
shown under the drawing in the reader, and recrops 19 drawings that took in their neighbour (mostly ADE).
The checks below were left open at 0.2.0. Delete this file when it's empty.

## Done at the workstation

- **Checks and library**: the app tests, the publishing gate and pyflakes pass; the library builds; all 36 test
  questions find their passages.
- **The llama.cpp pin**: now b12359 (llama.cpp v1.0.0), not b12078. Run H (`docs/model-tests.md`) tested it on the
  Strix Halo computer: the app updated b12078 to b12359 itself, and the answers were no worse (252 answers, none lost to
  an engine error). The `v1.0.0` tag has no binaries of its own, so the pin is `b12359`.
- **Built-in models**: gpt-oss 20B, Qwen3 8B and Qwen3 30B-A3B were each downloaded by the app and asked the 36
  questions. All three answer with citations. Qwen3 8B was taken out of the list all the same: it did no better
  than the 4B, at five times the wait, and made up passage ids.
- **The built-in list was rewritten from the tests** (run E): Nemotron 3.5 Lightning and Qwen3.5 35B-A3B, the two
  best on Lemonade, were run through the built-in engine and replace Qwen3 30B-A3B. IBM Granite 4.2 8B got 35 of 36 from a
  5.3 GB file and is the suggestion for 16 GB, though slow. Granite 4.2 3B (29 to 31) is now the one for 8 and 12 GB.
  Gemma 4 E4B and Qwen3 4B were retired in 0.2.3 (`"retired": true` in `para/models.json`; run F).
- **The 36 questions on Lemonade** (Nemotron 3.5, Qwen3.5-35B, Gemma 4 E4B), every answer read, every flagged figure
  judged: 20 true, 4 false alarms from two causes, both fixed with tests.
- **The library**: two-column pages are now read from where their lines are (`library/columns.py`). By
  `library/order.py` 39 passages don't read in the PDF's order, from 473 on main; none is a numbered paragraph. ADM
  Vol 1 page 59 and ADH page 25 read correctly. ADE Table 0.2, ADH Table 13, ADJ Table 1 and ADG Table 2.1 have every
  value (ADJ Table 1 didn't before).
- **Old chats**: asked against the published library and reopened on the rebuilt one, 31 citations in ten chats each
  opened the same paragraph, or one marked changed where its text differs. One unnumbered passage was rightly shown as
  gone.
- **Screenshots and the demo** are retaken (`docs/take_screenshots.py`, `docs/record_demo.py`), from 0.2.0. They
  predate 0.2.4 and 0.2.5; see "Still to check".

## Decide

- [ ] **Granite everywhere?** Granite 4.2 8B got 35 and 33 of 36 in earlier runs, and 34 in run H, at a median of 68 s
      an answer here against 56 to 62 s before. The suggestion now goes by the graphics card's memory (0.2.5), so the
      question is what it suggests on a 16 GB laptop. Time it there before deciding: if an answer takes several minutes,
      Gemma may be the better suggestion at 16 GB and Granite the one to offer on faster computers.
- [ ] **Read before you cite.** Qwen3.5-35B wrote 21 of 36 answers without opening a passage in run E (65 of its 110
      citations came from search extracts). Run H made it 40 extract-only citations; the MTP build 62 and 68. The answers
      were right and each such citation says so. If that's too many, make the prompt firmer in `para/core.py` and run the
      model tests again.
- [ ] **Qwen3.5 35B-A3B MTP build: list it or not?** Run H: it scores the same as the plain model and is about 20 %
      quicker (10 to 11 s against 13 s), and it runs on b12078 too. But its file has the same name as the plain
      model's, so a person can't have both in the models folder (`model_path` is the file name). Needs a different
      file name, or a decision to leave it out. Its `args` need `--spec-type draft-mtp`.
- [ ] **The first library with the new reader** will list 30 documents under *Changed in this edition*: 659
      paragraphs changed, 306 added, 140 removed against the one published on 10 November. Nearly all of it is the
      correction to how pages are read, which the note there allows for. Say so in the release notes, if they haven't
      been written already.

## Still to check

- [ ] **OpenAI's `max_tokens`.** Settings → online AI service → OpenAI, model `gpt-5-mini`, Length on Normal. Ask one
      question. If it fails with a 400, the error text is what `llm.py` looks for before retrying with
      `max_completion_tokens`. Needs your key.
- [ ] **Ollama's context.** Ollama isn't installed on the workstation, so this wasn't done. With Ollama at its default
      context, ask a question that needs two or three reads and look at `usage.prompt_tokens`. The README's *Context
      length* and `OLLAMA_CONTEXT_LENGTH=32768` are still from memory.
- [ ] **The pin on Windows and the Mac** (b12359): install the smallest built-in model from an empty app folder on each.
      Run H didn't test the Windows or Mac builds (only their contents were read), the CPU build under the app, a fresh
      install of an app up to 0.2.3 on b12359, or Check for updates, which overwrites a hand-written pin.
- [ ] **The CUDA builds have run on one NVIDIA card** (Quadro RTX 4000, Windows, CUDA 13, 16 November): it listed the
      card and IBM Granite 4.2 3B answered at about 80 tokens a second. The 36 questions are still to run on one
      (AGENTS.md: "Graphics-card builds") before saying NVIDIA is supported, and Linux has not been tried.
- [ ] **Graphics-card memory (0.2.5)**: the suggestion reads the card's memory from nvidia-smi, the kernel's files for
      AMD and Intel on Linux, and the display driver's registry entry on Windows. Checked on the workstation only. Try it
      on a Windows NVIDIA machine, an AMD card and a Mac (where it falls back to system memory).
- [ ] **Downloaded to this computer (0.2.5)**: delete a model from it on each packaged app, and check a running model
      stops first.
- [ ] **Copy with references.** In each packaged window (Windows, Mac, Linux Qt), press it and paste into Word and
      into an email. Only tested in Chromium.
- [ ] **Rename, Find in chats, Ask again** in the packaged window, at phone width too.
- [ ] **`reader-description.png`** is still the old picture. `library/descriptions/` was started again from none
      (commit 3bf7be8), so no drawing's description is approved yet. Nothing true to photograph until a person approves one.
- [ ] **`welcome.png`** is dated 11 November, before the empty chat was reworked in 0.2.2. Retake it.
- [ ] **Screenshots for 0.2.4 and 0.2.5**: none shows Connect, the recommended-models dialog, the graphics-card line
      and "fits on the graphics card", Downloaded to this computer, or the engine update row. Retake after the above
      is settled.
- [ ] **Packaged Linux, after 0.2.2**: press Copy with references and paste into a document; follow Original from the
      reader (both were checked piece by piece, not in a built app). Ask a project's own questions from the empty chat
      with a real model and read the answers.

## Known and left

- **Nemotron 3.5 Super (120B)** isn't listed: its GGUF comes in three parts (87 GB at Q4) and the engine downloads
  one file a model. On the workstation the graphics side can address 66 GB, so part of it would run on the
  processor.
- **A Mac's memory**: `engine.recommend` treats a Mac's memory as shared with its graphics, so it goes by system
  memory (no card is counted). A 32 GB Mac may be suggested a model that is too big to run comfortably. The memory check
  stops a load that won't fit, but a suggestion that is too big is still a bad suggestion. Revisit with a Mac to test.
- **39 passages out of order** (`python3 library/order.py --list`): covers, copyright pages, lists of other approved
  documents and of standards. A few running-header fragments are still left in passages ("N EBUILDINGS VERSION").
- **Figure boxes**: `figures.py` gives ADE Table 0.2 the whole page and ADH Table A1 the full width. The text is read
  correctly round them now, but their images are wrong. Changing a crop takes its description out of the library until
  a person approves it again.
- **ADG has no section headings** on its passages: they are only in its running header. ADG's own PDF is the one
  properly tagged document (headings, tables, captions), the others aren't, so the tags weren't used.
- **ADM Vol 2 "Diagram 3"** is a passage of paragraph 1.26's list, not of the diagram.
- Warning about a truncated prompt (waits on the Ollama check).
- Chats saved by earlier versions have no fingerprints. Their citations are matched by document and reference
  instead, which finds most; one that can't be matched shows as no longer in the library.

## Signed installers

Unsigned, Windows shows SmartScreen and macOS needs "Open Anyway"; in a firm with managed computers that often
means the app can't be installed at all.

Signing needs money (an Apple Developer Program membership, and a Windows certificate or Trusted Signing). Decide
whether to pay before doing any of this; the steps below are for when that's settled.

**Mac**
- [ ] Join the Apple Developer Program and create a *Developer ID Application* certificate.
- [ ] In `release.yml`: import the certificate from a secret, sign `Para.app` with the hardened runtime
      (`codesign --deep --options runtime`; PyInstaller apps usually need an entitlements file allowing unsigned
      executable memory), zip, `xcrun notarytool submit --wait`, then `xcrun stapler staple`.
- [ ] Secrets: the certificate (.p12, base64) and its password, and an App Store Connect API key or an
      app-specific password with your team id.
- [ ] Check the downloaded llama.cpp still starts from a notarised app, and that Update now still works
      (`updater.swap_mac`).
- [ ] Then take the "Open Anyway" steps out of the README and the website.

**Windows**
- [ ] Choose how to sign: Azure Trusted Signing (cheap, signs in CI with no hardware token; check that you're
      eligible as an individual in the UK) or an OV certificate from a certificate authority (now on a hardware
      token or a cloud HSM).
- [ ] In `release.yml`: sign `Para.exe` before Inno Setup packs it, then sign `Para-windows-setup.exe`.
- [ ] SmartScreen warnings fade only as the signed file builds a reputation; say so in the README until they do.

**Linux** needs nothing: the AppImage and .deb aren't signed, and the app checks GitHub's checksum when it updates.
