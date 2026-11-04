# Model tests

Real models asked the seven test questions through a running Para (`tests/ask_models.py`), on a Strix Halo
machine with 122 GB of shared memory. Lemonade (`http://127.0.0.1:13305/api/v1`) served every model except
`bundled:qwen3-4b-2507`, which ran on the built-in engine (llama.cpp b12078, Vulkan). November 2026.

## The questions

| Key | Question | A right answer | The passage it rests on |
|---|---|---|---|
| travel | Maximum travel distance in a single-direction escape route from a flat entrance door | 7.5 m | ADB1 Table 3.1 |
| stair | Maximum rise, minimum going and maximum pitch for a private stair | 220 mm, 220 mm, 42° | ADK Table 1.1 |
| guarding | Guarding height for a balcony in a dwelling | 1100 mm | ADK Diagram 3.1 / para 3.2 |
| uvalue | Limiting U-value for a new external wall in a new dwelling | 0.26 W/m²K | ADL1 Table 4.1 |
| hrb | When is a building a higher-risk building during design and construction? | 18 m or 7 storeys with at least 2 residential units, or a hospital or care home | HRB criteria (new build), BSA 2022 s 65 |
| pd | What must a principal designer be competent to do? | skills, knowledge, experience and behaviours to plan, manage and monitor design work | BR 2010 reg 11G |
| bathroom | Does a bathroom in a flat need a fire door? | No, if it is separated by fire-resisting construction | ADB1 Diagram 3.2 note |

**Right figures** is an automatic check for the figures in the answer. **Right passage cited** means a citation
opens the passage in the last column. Every answer was also read by hand; the notes say where the two checks
mislead.

## Results

Every model searched before answering (except where noted), every citation it kept opens a passage it read, and
**no made-up citation had to be removed** in any run. The images attached to the five passages above were checked:
each shows the right table or diagram (the crops of ADK Table 1.1 and ADL1 Table 4.1 also take in the next
heading).

Run A (library at `3373a86`) and run B (library at `11c2924`, with the figure, page-furniture and citation fixes
below). Times are the median over the seven questions, including searching and reading.

| Model | Size | Run | Right figures | Right passage cited | Median time | Notes |
|---|---|---|---|---|---|---|
| NVIDIA Nemotron 3.5 Lightning 30B-A3B | 23.8 GB | B | 7/7 | 7/7 | 29 s | Clean on every question; one search each, three for the bathroom |
| Qwen3.5-35B-A3B | 21.6 GB | B | 7/7 | 7/7 | 30 s | Clean; found Diagram 3.2 for the bathroom |
| Tiel-Coder-35B-A3B (Q6_K_XL) | | A | 7/7 | 7/7 | 16 s | Coding fine-tune |
| KAT-Coder V2.5 (Q5_K_M) | | A | 7/7 | 7/7 | 23 s | Coding fine-tune |
| Qwen3.8-27B | | A | 7/7 | 7/7 | 72 s | Dense: slow |
| Qwen3.8-27B (UD-Q4_K_XL) | | A | 7/7 | 7/7 | 99 s | |
| Qwen3.8-27B-HRX | | A | 7/7 | 7/7 | 103 s | |
| Muse-Glimmer-30B | | A | 7/7 | 7/7 | 205 s | Up to 12 searches a question; in an earlier run Lemonade failed to parse its tool call |
| Qwen3.6-35B-A3B | | A | 7/7 | 6/7 | 37 s | Bathroom: right conclusion from the ADB FAQ, not Diagram 3.2 |
| Qwen3.6-35B-A3B-MTP | | A | 7/7 | 6/7 | 25 s | Guarding: right, but wrote "ADK Diagram 3.1" in plain text, which isn't linked |
| Dirk-Qwen3.8-27B (UD-Q4_K_S) | | A | 7/7 | 6/7 | 95 s | Bathroom: wrote "(ADB1, para 3.7d)", which didn't link (fixed since) |
| Nemotron 3 Nano 30B-A3B | 22.8 GB | B | 6/7 | 6/7 | 36 s | Bathroom from the ADB FAQ (written about houses); cited ADO next to ADK for guarding |
| Gemma 4 26B-A4B | 18.1 GB | B | 6/7 | 6/7 | 33 s | Bathroom: "no", but reasoned from the ADB FAQ |
| Gemma 4 E4B | 5.6 GB | A | 7/7 | 6/7 | 15 s | Guarding: 1.1 m taken from ADO Table 3.1 (overheating), not ADK, in both runs it was tested |
| Gemma 4 12B | 7.3 GB | B | 5/7 | 6/7 | 28 s | Guarding: no height, only "see Diagram 3.1"; bathroom muddled (house guidance applied to flats) |
| Qwen3 4B 2507, built-in | 2.5 GB | B | 5/7 | 4/7 | 8 s | HRB answered from memory and wrong ("more than 10 storeys") despite the nudge; guarding and bathroom thin |

In run A the built-in 4B didn't load: Lemonade and two downloads had taken almost all memory, llama.cpp couldn't fit
the model, and every later question got "503 Loading model" (fixed, below). Run B is its retry.

### Before the fixes

The first pass, on the library and code at the start of this work:

| Model | Right figures | Cited |
|---|---|---|
| Qwen3.6-35B-A3B | 7/7 | 5/7 |
| Gemma 4 E4B | 5/7 | 7/7 |
| Qwen3 4B 2507, built-in | 4/7 | 4/7 |

## What was wrong and what changed

Library first (`splitter.py`, `build.py`, `figures.py`), then the tool loop and citations (`core.py`):

- **Index pages** were split into passages and their entries cropped as "figures" (about 90 fake ones). They are
  skipped now, and wrapped cross-references ("…as shown in\nDiagram 4.1") no longer start a figure passage.
- **Two-column pages** were read across both columns, so the fire doorset definition picked up "…a kitchen, but not
  a bathroom" from the habitable room definition next to it, and two models quoted it. Columns are read one at a
  time now; passages with a paragraph reference went up in ADM2 (130 → 155), ADE (175 → 227), ADG (58 → 106) and
  ADH (60 → 101).
- **"door" didn't find "doorset"**, so a search for "fire door bathroom flat" missed ADB1 Diagram 3.2. "door" is
  searched as a prefix now.
- **Figure crops stopped at a drawing's own long labels** ("The central door may be omitted if…") and part titles
  ("a. Corridor access without dead ends"), so ADB1 Diagram 3.8 lost its top half and its labels landed in the
  Diagram 3.7 passage. Drawing text is smaller than running text, and that's what tells them apart now; 31 crops
  take in more of their drawing and 9 diagrams that had none have an image.
- **Page headers and footers** were left in 624 passages (a footer padded past a length limit, headers with the
  section tab beside them). 161 are left.
- **Citations written inside the model's own brackets** (`[r12, r15]`, `(r3–r5)`) and **references written in
  words** (`[ADB Vol 1, Table 3.1]`, `(ADB1, para 3.7d)`) link to the passage, but only to one the model saw and
  only when exactly one matches. Plain text such as "ADK Diagram 3.1" is never linked.
- **Answers from memory**: if a model answers the first time without searching and cites nothing it read, the
  answer is set aside and the model is asked once to search. This fixed three of the 4B's answers; it still
  ignored it for the HRB question.

## Built-in engine

- `para/models.json`: every repo and file exists on Hugging Face with the listed size (checked with `HEAD` and
  `X-Linked-Size`), except gpt-oss, whose file is `gpt-oss-20b-MXFP4.gguf` (fixed).
- llama.cpp's "latest" release (v0.8.0) has no binaries; builds are published as pre-releases. The engine now
  takes the newest release with a binary for the platform (b12078 here).
- A real download and start with the smallest model worked: Qwen3-4B-Instruct-2507 Q4_K_M, 2,497,281,120 bytes,
  checksum verified, answered through the app with citations.
- A model load that timed out left the stuck server running, so every later question got "503 Loading model". It
  is stopped now, and the message says the computer is probably short of memory.
- **Not tested yet**: gpt-oss-20b, Qwen3 8B and Qwen3 30B-A3B through the built-in engine.

## Choosing defaults

For the built-in list, the candidates that did best and come from the models' own publishers:

- **32 GB or more**: Nemotron 3.5 Lightning 30B-A3B or Qwen3.5-35B-A3B (both 7/7, about 30 s). Qwen3.6-35B-A3B is
  close (6/7 plain, 7/7 figures with MTP).
- **16 GB**: Gemma 4 12B was weaker than its size suggests here; gpt-oss-20b and Qwen3 8B are still to be tested.
- **8 GB**: Qwen3 4B is fast but answers from memory sometimes; Gemma 4 E4B is better at searching but picked the
  wrong document for guarding.

The coding and community fine-tunes (Tiel-Coder, KAT-Coder, Muse-Glimmer, Dirk, HRX) scored well but are less
suited as defaults. Qwen and gpt-oss are Apache 2.0; Gemma and Nemotron have their own licence terms, to be read
before listing them.

## Repeating the tests

```bash
PARA_APP_DIR=/tmp/para-test HOME=/tmp/para-test python3 -m para --browser --port 8765
python3 tests/ask_models.py ask http://127.0.0.1:8765 out.jsonl Qwen3.5-35B-A3B-GGUF bundled:qwen3-4b-2507
python3 tests/ask_models.py score out.jsonl --answers
```

Read the answers, not only the scores: a right figure from the wrong document, or a right answer the check misses,
only shows there.

## Describing drawings

`library/describe.py` has a vision model describe each diagram, and a second reading check each draft against its
image for the person who reviews it. Tested on 2026-11-04 on Lemonade (Strix Halo), with nine drawings whose content
had been checked by hand: ADB Vol 1 Diagrams 2.2, 2.4 and 2.6, ADK 1.3 and 1.9, ADM Vol 1 1.2 and 1.5, ADM Vol 2 11
and 19.

| Model | Thinking | Time a drawing | Descriptions | Checking a faulty description |
|---|---|---|---|---|
| Gemma 4 26B-A4B | off | ~5 s | Fluent but wrong on most: dimensions read as the wrong thing, items miscounted | About half the real errors found, plus wrong claims of its own |
| Qwen3.8 27B | off | ~15–35 s | As wrong as Gemma; invented details (a "basement") | Mostly wrong claims; missed two misread dimensions |
| Qwen3.6 27B | off | ~20–30 s | As wrong; "green dots", a seat height read as a width | Some real errors, some wrong; once wandered ("wait, looking closer…") |
| Qwen3.6 27B | on | ~4–6 min | not tried | ADK 1.3 and ADM 1.5: every error found, nothing invented |
| Qwen3.8 27B | on | ~1–3 min | ADK 1.3 right throughout; ADM 1.5 right but one dimension's start; ADM 11 cautious, one slip | Same as Qwen3.6, in half the time |

What a dimension line measures is the hard part, and only thinking gets it right. Both models ran out of room on the
lift diagram (ADM Vol 2 Diagram 11) with a 12,000-token limit; the limit is now 32,000. The default is Qwen3.8 27B
with thinking on: about 40 drawings drafted and checked a night. Even so, every description is checked by a person:
the model's check is where to look first, not a verdict.
