# Plan: reading a project's drawings and checking them against the regulations

Status: proposed (10 November 2026). Builds on projects (tier 1: a folder per project, its files searched with the
library, its own notes and chats). Read AGENTS.md and DESIGN.md first; this plan follows their rules, in particular
"nothing the person sees may claim a source the model didn't read".

## Goal

Someone adds the drawings for their project (PDF exports of plans, sections, elevations, details) and asks things
like "does the stair on A-201 meet Part K?" or "check the escape distances on the ground floor plan". Para answers
from what the drawing shows and what the regulations say, citing both, and is honest about which parts it read
from a drawing and how sure that reading is.

The hard part isn't searching. It's that a drawing's meaning is in its geometry, not its text. The text in a
drawing PDF is labels, dimensions and title-block entries scattered across the sheet ("1200", "FD30S", "Bed 2"),
with nothing saying what's next to what. Answering needs a model that can see the drawing, and models misread small
dimensions, mix up which label belongs to which line, and don't know the scale. A confident answer built on a
misread digit is the failure to design against.

## Two stages

**Tier 2: the AI can look at a project's drawings.** Questions about a drawing are answered from its image.

**Tier 3: comparing a drawing with the regulations.** The model takes values off the drawing into a table the person
confirms or corrects, and only then compares the confirmed values with cited paragraphs.

Tier 3 depends on tier 2. Build and measure tier 2 first: how reliably the models we'd ship read dimensions decides
how much tier 3 can lean on the model and how much on the person.

## Tier 2: seeing drawings

### Which files are drawings

A PDF page is treated as a drawing when it has little running text and a lot of vector or image ink, or a page
size of A3 or larger, or a title block (a box in a corner with "Drawing no", "Scale", "Rev"). Record per page in the
files index (`pages` table: source, page, kind `drawing`/`text`, size in mm, scale if the title block gives one).
Let the person correct it on the file's row ("This file is drawings").

Render drawing pages to PNG once, on adding (pypdf can't render; use `pdftoppm` when present, as `library/figures.py`
does, and say in the file's row when it isn't). Keep the images in the app folder next to `files.sqlite`, keyed by
the file's key and mtime so a changed file is rendered again.

### How the model sees them: three options

1. **Look when asked** (recommended to start with). A new tool, `view_drawing(id, region)`, sends the page image, or
   a zoomed tile of it, to the model as an `image_url` content part. `id` is a drawing page's passage id; `region` is
   optional ("top-left", or a grid cell like "B3" on a 4 by 4 grid drawn on the overview), so the model can look at
   the whole sheet, then zoom in to read dimensions. Always current, and the model looks where it needs to. Costs
   about 1,000 to 3,000 tokens per image and is slow on local hardware.
2. **Describe on adding.** A vision model writes a description of each drawing page (what it shows, rooms, levels,
   key dimensions), indexed as the page's passage text so search finds the right sheet. Cheap at question time and
   works with text-only models, but nobody checks the descriptions, so they're labelled "Read from the drawing by AI,
   not checked" wherever they're shown, and the prompt treats them as a finding aid, never as a source for a figure.
3. **Both.** Descriptions find the sheet; the model then looks at the image to answer. This is where to end up.

### Engines

- Everything stays on the OpenAI-compatible API: images are `{"type": "image_url", "image_url": {"url":
  "data:image/png;base64,..."}}` parts, which Lemonade, Ollama, LM Studio and llama-server all accept for vision
  models. No engine-specific code.
- The app has to know whether the chosen model can see. Ask it once (a tiny test image with a number on it, as
  `describe.py` already does with its own prompts) and cache the answer per engine and model in settings. When it
  can't, say so plainly in the project and offer `view_drawing` to no one: "This model can't read images. Drawings
  are searched by their text only."
- Built-in engine: vision models need their projector file (`mmproj-*.gguf`) passed to llama-server with
  `--mmproj`. `models.json` gains an optional `mmproj` entry (repo, file, size), downloaded with the model and counted
  in its memory figure. Only list a vision model after testing it reads drawings through the built-in engine.
- Memory: a vision model plus large images on top of the regulations context is tight on 16 GB machines. Keep images
  to about 1,500 px on the long side per tile and drop images from older turns of the chat (`core.trim`).

### Citations to drawings

A drawing citation is a page and, optionally, a region: `[d12]` for a whole drawing page, `[d12:B3]` for a cell.
Ids `d…` are drawing pages of the person's files. Citation checking (`core._check_citations`) accepts a `d` id only
if the model viewed that page (and that cell, or the whole page) this turn, the same rule as for passages.

The reader shows a drawing citation as the page image with the region outlined, zoomable like figures, with "Open
file" to the PDF at that page. The citation chip uses the your-files style with a drawing mark, so people can tell a
reading of their drawing from a paragraph of a document.

Text-based checks can't confirm what an image says. Answers that use drawings carry a note: "Read from your drawings
by AI. Check the dimensions on the drawing before relying on them."

### Privacy

With a remote engine, drawing images leave the computer. Say so in the project ("Your drawings are sent to <host>
when the AI looks at them") the first time a remote engine is used with a project that has drawings.

### Measure before going further

Add drawing questions to `tests/ask_models.py`: a handful of real (redistributable or our own) drawings with known
answers: stair rise and going on a section, door widths on a plan, a guarding height on a detail, a travel distance
on a plan with a scale bar. Score each model on reading the right number from the right place. Record results in
`docs/model-tests.md`. If the local models we'd ship get dimensions wrong often, tier 3 leans on the person entering
values and the model only finds where they are.

## Tier 3: checking a drawing against the regulations

Framed as "help me check", never "check my drawing for compliance". The model does the finding and the comparing;
the person confirms what the drawing says.

### Flow

1. The person asks something like "Check the stair on A-201 against Part K".
2. The model works out which values matter (from the regulations: rise, going, pitch, headroom, width, guarding
   height, handrail height) and reads each off the drawing with `view_drawing`.
3. Instead of answering at once, it calls a new tool, `propose_values`, with a list of rows: what (Stair rise), value
   (175 mm), where ([d12:B3]), how sure (read / measured from scale / not shown). The app shows this as a **values
   table** in the answer: one ruled row per value, the value editable, the drawing citation opening the region, and a
   "Not shown" option.
4. The person confirms or corrects each row and presses Check. Corrected rows record who changed them.
5. The app sends the confirmed table back as the next message, and the model compares each value with the
   requirement it cites: "Rise 175 mm (confirmed): within the 220 mm maximum for a private stair (ADK Table 1.1)
   [r…]". A value the person marked "Not shown" is listed as something to check, never assumed.

The comparison answer cites both sides: the drawing value (confirmed by the person) and the paragraph. A row the
person didn't confirm can't be used in a comparison; the citation check drops claims built on it, with a note.

### Why a table and not free text

It puts the one step the app can't verify (reading the drawing) in front of a person, keeps each claim tied to one
value and one paragraph, and gives something to save with the project: the confirmed values become project facts
the model can use in later chats ("Stair: rise 175 mm, going 250 mm, confirmed 10 Nov 2026, A-201 rev C").

### Saved checks

A project keeps its checks (`checks/<id>.json` in the project's app data: the drawing, its revision if the title
block gives one, the values and who confirmed them, the paragraphs compared against, the outcome). When the drawing
file changes, its checks are marked "drawing changed since checked" rather than deleted.

### What it isn't

Not a building control submission and not a sign-off. The interface says so once, plainly, on the first check in a
project, and in the saved check's header. No compliance badges, no green ticks on a whole drawing.

## Order of work

1. Render drawing pages and show them in the reader with page citations (no vision yet).
2. Vision check per engine and model; `view_drawing` with whole-page images; `d` ids and citation checking.
3. Tiles and regions; drawing questions in `tests/ask_models.py`; measure local models. Decide from the results how
   far to rely on the model reading values.
4. Descriptions on adding, labelled as unchecked, used for finding sheets.
5. Built-in engine: `mmproj` downloads and a tested vision model in `models.json`.
6. Tier 3: `propose_values`, the values table, confirmed comparison, saved checks.

## Open questions

- DWG, IFC and other CAD or BIM files: out of scope. PDF exports cover what most people have. IFC is text and could
  be read for quantities later, as its own plan.
- Scale: reading a dimension written on the drawing is far more reliable than measuring with a scale bar. Measured
  values should always be marked as measured and need confirming.
- Multi-sheet questions (a travel distance across a plan and a section) need the model to hold several images; test
  how many fit in the context of the models we ship.
