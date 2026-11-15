# Plan: attaching files in the question box

Status: proposed (14 November 2026), not started. Read AGENTS.md and DESIGN.md first. The image half (stage 2)
overlaps with [project-drawings.md](project-drawings.md) and should be done as part of it.

## Goal

Someone can attach a file to the question they're writing, as in other chat apps, without opening *Your files* or
the project dialog first: "what does this fire strategy say about the stair?" with the PDF attached.

- **In a project**: the file goes into the project's folder and stays in its library, so every later chat in the
  project can search it too. That is what *Project → files* does today, reached from the question box.
- **Not in a project**: the file belongs to that chat only. It isn't in the files folder, other chats don't search
  it, and it goes when the chat is deleted.

## Stage 1: documents (PDF, DOCX, EPUB, HTML, text)

### Interface

- An **Attach** button in the question box (`#composer`), an outline pill with a mono label to the left of Send. No
  paperclip icon: DESIGN.md allows no illustrations, and the other buttons are words.
- A file can also be dropped on the question box, or pasted into it. Today a drop anywhere on the window adds the
  file to the current project or to *Your files* (`app.js`, the document drop handler). A drop **on the question
  box** now means "attach to this chat". A drop anywhere else stays as it is.
- Attached files show as small chips above the text: name, size in mono, a cross to take it off, and the reading
  state while it's being indexed ("Reading…", then nothing). Send waits until every chip has been read; a file that
  can't be read shows the reason in danger text and doesn't block the question.
- Under the question in the conversation (`.msg.user`), a mono line lists what was attached ("Attached:
  fire-strategy.pdf"). In a project the name opens the file like a citation to it does. In a chat without a project
  it opens the chat's own copy.
- The question box's placeholder doesn't change. An empty chat in no project gets one more line under "Try one":
  "You can attach a document to your question."

### Where files go

- **In a project**: the existing `POST /api/files` with `X-Project`, so `myfiles.add` saves the file in the project
  folder and indexes it. Nothing new on the server. The chip says "Added to <project>" once it's read.
- **No project**: a new `POST /api/attachments?chat=<id>` (raw body and `X-Filename`, like `/api/files`).
  - The file is saved under the app data folder, `attachments/<chat id>/<name>`, not in the files folder. A
    temporary attachment must not appear in *Your files* or be searched by other chats.
  - It's indexed into the same files index (`files.sqlite`), so passages get `f…` ids and everything downstream
    (reader, citation chips, `core._check_citations`, `anchors`) works unchanged. `myfiles.sync` already leaves
    alone keys outside the files folder (`project_of` is None), and `myfiles.keys()` already leaves them out of
    every chat's scope, so they're invisible until added back.
  - A chat that hasn't been saved yet has no id. The question box creates the chat (as asking does) before it
    uploads, or the upload creates it and returns the id.
- **The chat's scope**: `chat["attachments"]` holds the keys. `server.py` builds the scope as
  `Scope(library, lambda: myfiles.keys(project) | set(chat.get("attachments", [])))`, so an attachment is
  searched only in its own chat. The same key list is what the reader and `/api/open` may open: add it to the
  "files already in the files index" rule in `Handler` rather than opening any path, so the security rule in
  AGENTS.md holds.
- **Deleting the chat** deletes `attachments/<chat id>/` and removes those keys from the index
  (`myfiles.remove(key, delete_file=True)` doesn't apply, since it only deletes inside the files folder: add a
  small `remove_attachment` that may delete only under `attachments/`).
- **Moving a chat to a project** isn't possible today (a chat's project is fixed when it's created), so there's no
  case where a temporary attachment has to become a project file. Later, a "Keep in Your files" link on the chip
  could copy it into the files folder. Leave that for now.

### What the model is told

- The turn's user message gets a short note before the question: "The person attached: fire-strategy.pdf (42
  passages). Search it with search_library; its passages start with f." That's added in `core.py`, where the turn's
  messages are built, and it's added after the person's own instructions, so it works with replaced instructions.
- A short file (say under 6 passages, or under ~3,000 words) is attached whole, the way the rescue path attaches
  passages for a model without tools. Its passages then count as **read** (`cites[id]["read"]`), since the model
  had the full text. A longer one is searched like any other file.
- Nothing about citation checking changes: the model can cite only passages it searched or was given.

### Limits and edge cases

- The same types as *Your files* (`myfiles.TYPES`); anything else is refused on the chip with the list of types.
- A size limit per file, the same as `/api/files` (check whether it has one; add it to both if not), with the
  reason shown on the chip.
- A PDF of a scan has no text. The chip says "No text found in this PDF (a scan?)", and the file is kept but not
  searched. This is the same message *Your files* should give.
- Asking again (`Ask again`) keeps the attachments. Editing the chat's name doesn't touch them.
- Saved chats record their attachment keys and the passages' fingerprints like any `f…` citation, so
  `anchors.reanchor` finds them again if the file is read again.

### Tests (`tests/test_app.py`)

- Attach to a chat with no project: the file isn't in `/api/files`, isn't searched from another chat, is searched
  and cited in its own chat, opens through `/api/open`, and is gone (folder and index) after the chat is deleted.
- Attach in a project: the file lands in the project folder and is listed in the project's files.
- `/api/attachments` refuses a chat id with path characters, and `/api/open` still refuses a path outside the index.
- A turn with an attachment tells the model about it, with the instructions replaced as well.

### Docs

- DESIGN.md: the Attach button, chips, the "Attached:" line, and the drop rule. Add a screenshot
  (`composer-attach.png`) to `docs/take_screenshots.py`.
- AGENTS.md: `attachments/` in the map and the `/api/open` rule.

## Stage 2: images (photos, screenshots, drawings)

Only once [project-drawings.md](project-drawings.md) tier 2 has been measured. An image can't be searched or cited
by paragraph, so it has to be shown to a model that can see, and what the model says about it can't be checked the
way a citation is.

- **Which models can see.** None of Para's built-in models can today. `models.json` has no vision field, and
  llama.cpp needs a separate projector file (`mmproj`) for each model, which the built-in engine doesn't download. Some of
  the families may have vision versions (Qwen3.5 and Gemma 4 perhaps), but that hasn't been checked. Adding
  this means a `vision` flag and an `mmproj` entry per model in `models.json`, downloading the projector with the
  model, passing `--mmproj` to `llama-server`, and testing each with real drawings before listing it.
- **External and online engines**: there's no standard way to ask an OpenAI-compatible server whether a model can
  see. A setting, "This model can see images", off by default; with it on, images are sent as `image_url` parts
  in the standard `/chat/completions` format (`llm.py` already passes messages through unchanged). A server that
  refuses them gets a plain error naming the setting.
- **Without a vision model**: the chip says "Images need a model that can see; this one can't" and the image isn't
  sent. Never fall back to describing it from the file name.
- **Honesty**: what the model says about an image gets a note under the answer: "Read from your image by AI, not
  checked against a passage." A citation to the image (if there is one) uses the warm file style with the image as
  its "passage", and opens the image in the reader.
- **Privacy**: an image goes to the engine like any passage from the person's files. The remote warning in
  Settings already says so; check its wording covers images.

## Order of work

1. Stage 1 in a project (no new endpoints: the button, chips and drop rule on top of `/api/files`).
2. Stage 1 without a project (`/api/attachments`, the chat scope, deletion).
3. What the model is told, and attaching short files whole; run `tests/ask_models.py` with a question about an
   attached file.
4. Stage 2, as part of project-drawings tier 2.

## Open questions

- Should a temporary attachment have a "Keep in Your files" link, or is attaching it in a project enough?
- Is ~3,000 words the right size to attach whole, given the small models' context (32K in `models.json`)?
- Paste: pasting a file is clear, but pasting a long block of text could also become an attachment. Leave pasted
  text as text for now.
