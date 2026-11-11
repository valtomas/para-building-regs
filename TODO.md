# To do before releasing 0.2.0

Everything is on the branch `ccr-990cc406-2vnrqi` (not yet a pull request). The first three sections of the old list
were worked through at the workstation on 11 November 2026 with real models and the real window; what was found is in
`docs/model-tests.md` (run D) and in the commit messages. Delete this file when it's empty.

## Done at the workstation

- **Checks and library**: the app tests, the publishing gate and pyflakes pass; the library builds; all 36 test
  questions find their passages.
- **The llama.cpp pin**: b12078 has all seven platform files. An empty app folder installed it from the pin with the
  smallest model and answered with citations, on Linux.
- **Built-in models**: gpt-oss 20B, Qwen3 8B and Qwen3 30B-A3B were each downloaded by the app and asked the 36
  questions. All three answer with citations. Qwen3 8B was taken out of the list all the same: it did no better
  than the 4B, at five times the wait, and made up passage ids.
- **The 36 questions on Lemonade** (Nemotron 3.5, Qwen3.5-35B, Gemma 4 E4B), every answer read, every flagged figure
  judged: 20 true, 4 false alarms from two causes, both fixed with tests.
- **The library**: two-column pages are now read from where their lines are (`library/columns.py`). By
  `library/order.py` 39 passages don't read in the PDF's order, from 473 on main; none is a numbered paragraph. ADM
  Vol 1 page 59 and ADH page 25 read correctly. ADE Table 0.2, ADH Table 13, ADJ Table 1 and ADG Table 2.1 have every
  value (ADJ Table 1 didn't before).
- **Old chats**: asked against the published library and reopened on the rebuilt one, 31 citations in ten chats each
  opened the same paragraph, or one marked changed where its text differs. One unnumbered passage was rightly shown as
  gone.
- **Screenshots and the demo** are retaken (`docs/take_screenshots.py`, `docs/record_demo.py`).

## Decide

- [ ] **Read before you cite.** Qwen3.5-35B wrote 21 of 36 answers without opening a passage (65 of its 110
      citations are from search extracts), Nemotron 2. The answers were right and each such citation says so. If
      that's too many, make the prompt firmer in `para/core.py` and run the model tests again.
- [ ] **The first library with the new reader** will list 30 documents under *Changed in this edition*: 659
      paragraphs changed, 306 added, 140 removed against the one published on 10 November. Nearly all of it is the
      correction to how pages are read, which the note there allows for. Say so in the release notes.

## Still to check

- [ ] **OpenAI's `max_tokens`.** Settings → online AI service → OpenAI, model `gpt-5-mini`, Length on Normal. Ask one
      question. If it fails with a 400, the error text is what `llm.py` looks for before retrying with
      `max_completion_tokens`. Needs your key.
- [ ] **Ollama's context.** Ollama isn't installed on the workstation, so this wasn't done. With Ollama at its default
      context, ask a question that needs two or three reads and look at `usage.prompt_tokens`. The README's *Context
      length* and `OLLAMA_CONTEXT_LENGTH=32768` are still from memory.
- [ ] **The pin on Windows and the Mac**: install the smallest built-in model from an empty app folder on each.
- [ ] **Copy with references.** In each packaged window (Windows, Mac, Linux Qt), press it and paste into Word and
      into an email. Only tested in Chromium.
- [ ] **Rename, Find in chats, Ask again** in the packaged window, at phone width too.
- [ ] **`reader-description.png`** is the old picture: no drawing's description has been checked by a person in this
      library, so there was nothing true to photograph.

## Known and left

- **Someone who installed Qwen3 8B** keeps the 5 GB file in their app folder, but the app no longer lists it and
  asks them to choose a model again. Nothing removes the file for them.
- **39 passages out of order** (`python3 library/order.py --list`): covers, copyright pages, lists of other approved
  documents and of standards. A few running-header fragments are still left in passages ("N EBUILDINGS VERSION").
- **Figure boxes**: `figures.py` gives ADE Table 0.2 the whole page and ADH Table A1 the full width. The text is read
  correctly round them now, but their images are wrong. Changing a crop takes its description out of the library until
  a person approves it again.
- **ADG has no section headings** on its passages: they are only in its running header. ADG's own PDF is the one
  properly tagged document (headings, tables, captions), the others aren't, so the tags weren't used.
- **ADM Vol 2 "Diagram 3"** is a passage of paragraph 1.26's list, not of the diagram.
- **A copy of the app that already has a llama.cpp build keeps it**: the pin is for new installs.
- Warning about a truncated prompt (waits on the Ollama check).
- Chats saved by earlier versions have no fingerprints. Their citations are matched by document and reference
  instead, which finds most; one that can't be matched shows as no longer in the library.

## 4. Signed installers

Unsigned, Windows shows SmartScreen and macOS needs "Open Anyway"; in a firm with managed computers that often
means the app can't be installed at all.

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

## 5. Releasing

- [ ] Open a pull request from the branch and merge it. The version is already `0.2.0` in `para/__init__.py`, and
      the website has its new sentence (it publishes when main changes).
- [ ] Tag `v0.2.0` on main: that builds the library and the apps and publishes the release, with `models.json` (the
      pin) for installed apps.
