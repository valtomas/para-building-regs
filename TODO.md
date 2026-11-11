# To do at the workstation

Things from the review that need your machine, a key or an account. Everything else is done on the branch
`ccr-990cc406-2vnrqi` (not yet a pull request). Tick them off and delete this file when it's empty.

## 1. Check what was changed blind

These were written and tested against the fake engine only.

- [ ] **OpenAI's `max_tokens`.** Settings → online AI service → OpenAI, model `gpt-5-mini`, Length on Normal. Ask one
      question. It should answer; if it fails with a 400, send me the error text: `llm.py` looks for the words
      `max_tokens` in it before retrying with `max_completion_tokens`.
- [ ] **The llama.cpp pin.** `para/models.json` now says `"tag": "b12078"` (the build `docs/model-tests.md` names).
      With an empty app folder (`PARA_APP_DIR=/tmp/para-pin python3 -m para --browser`), download the smallest
      built-in model on Linux, Windows and the Mac and ask a question. If a platform's file is missing from that
      release, pick the newest build that has all seven and test with that.
- [ ] **Ollama's context.** With Ollama at its default context, ask a question that needs two or three reads and look
      at `usage.prompt_tokens` in the reply. If it's the cut-down count (about 4,096), the app can warn when a prompt
      was truncated; tell me and I'll add it. Also check the README's wording: I wrote *Context length* and
      `OLLAMA_CONTEXT_LENGTH=32768` from memory, and "16,000 tokens or more" is an estimate.

## 2. The built-in models (your own rule: test before listing)

- [ ] Unload Lemonade's models first (`lemonade unload`), then run `tests/ask_models.py` through the built-in engine
      for `gpt-oss-20b`, `qwen3-8b` and `qwen3-30b-a3b-2507`. Add the rows to `docs/model-tests.md`.
- [ ] Take out of `para/models.json` any that don't answer with citations.
- [ ] If Qwen3 8B does well, consider recommending it from 16 GB up: the 4B got 5/7 and answered the higher-risk
      building question from memory.

## 3. The new features, with a real model and the real window

- [ ] **Figure note.** Run the test questions (now 36: `tests/ask_models.py`, about 20 minutes a model) and score
      with `--answers`. The table has two new columns, *Figures flagged* and *Extract-only citations*, and each
      answer lists what was flagged. Read every flag: it should fire only for a figure that really isn't in the cited
      passage or in a diagram or table that passage names. Expect one on the `flight` question (2600 mm is worked
      out, not in the table). Against the real library, with right and wrong answers I wrote by hand, it gave no
      false alarms in 24 and missed none of 8; what real models write is the test that counts. False alarms to look for: a unit the check
      doesn't know, a figure in a neighbouring passage, a figure worked out from two others. The units and the
      conversions are `_UNIT` and `SAME_AS` in `para/core.py`.
- [ ] **Extract only.** See how often real models cite from a search result without reading. If it's most answers
      from a model, the prompt may need a firmer "read before you cite" (then rerun the model tests).
- [ ] **Copy with references.** In each packaged window (Windows, Mac, Linux Qt), press it and paste into Word and
      into an email. It should paste formatted, with linked references. I could only test it in Chromium; the Qt
      window is the one most likely to refuse the clipboard.
- [ ] **Changed in this edition.** Install last week's library (from the previous release), start the app, install
      the library update, then open Library. Check the list is believable in size: a splitter change in the build
      would show as hundreds of "changed" paragraphs.
- [ ] **Old chats.** Before that update, ask a couple of questions; after it, reopen those chats and click the
      citations. They should open the same paragraphs as before.
- [ ] **Rename, Find in chats, Ask again.** Try each in the window, at phone width too (the pencil and cross only
      show on hover on a desktop).

## Garbled passages found while writing the questions

Two-column pages read across the columns, so the sentences are interleaved. The figures are still there, but a model
quoting them would quote nonsense. For `splitter.unweave_columns`, with a rebuild:

- [ ] **ADC 2.38**: "Gas control measures for non-domestic 3% to 10% of homes and full radon protective buildings
      use the same principles…"
- [ ] **ADG 2.3**: "The estimated consumption of Secretary of State of 125 litres/person/day of wholesome water of a
      new dwelling should wholesome water or 110 litres/person/day be no more than…"
- [ ] ADG's passages have no section headings at all, which makes its citations harder to read ("ADG 2.3" with no
      heading in the Cited list).

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

## 5. Before releasing

- [ ] Regenerate the screenshots (`docs/screenshots/`): the answer now has actions under it, the sidebar has Find in
      chats, and there are new states to show (extract only, the figure note, Changed in this edition).
- [ ] Run `python3 tests/test_app.py`, `python3 tests/test_check.py` and `python3 -m pyflakes para library tests`.
- [ ] Open a pull request from the branch and merge it. The version is already `0.2.0` in `para/__init__.py`,
      and the website has its new sentence (it publishes when main changes).
- [ ] Tag `v0.2.0` on main: that builds the library and the apps and publishes the release. Do the llama.cpp pin
      check (section 1) first, because `models.json` goes out to installed apps with the release.

## Not done, on purpose

- Warning about a truncated prompt (waits on the Ollama check above).
- Chats saved by earlier versions have no fingerprints. Their citations are matched by document and reference
  instead, which finds most; one that can't be matched shows as no longer in the library.
