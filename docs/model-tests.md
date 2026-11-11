# Model tests

Real models asked the test questions (seven at first, 36 from run D) through a running Para (`tests/ask_models.py`), on a Strix Halo
machine with 122 GB of shared memory. Lemonade (`http://127.0.0.1:13305/api/v1`) served every model except
`bundled:qwen3-4b-2507`, which ran on the built-in engine (llama.cpp b12078, Vulkan). November 2026.

## The questions

The results below are for the first seven questions. There are now 36 (`QUESTIONS` in `tests/ask_models.py`): one
figure from one paragraph or table in each Part, time limits and duties from the legislation and the higher-risk
building guidance, a sum worked out from a table, and two the library can't answer (Scotland, the NHBC Standards),
where a right answer says so and cites nothing. Each run also records what the app itself said about the answer:
figures it couldn't find in the passage cited, and citations made from a search extract. `ask_models.py check
<library>` confirms the passages the questions rest on are in a library; run it after a build.

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

### Run C: a question in a project, and requirements in reads

Recording the website's video turned up two slips on "What height of guarding do I need at the edge of the new
landing?", asked in a project with notes and no files (Qwen3.5-35B-A3B):

- **"As noted in your project files"**, about headroom it had found in the library. The prompt told every project
  chat that "its files are searched with the library", even with none. It now says how many files there are, or that
  there are none, and that ids starting r are the library and f the person's files.
- **"Requirement K3"** for guarding. Reading ADK para 3.2 ran on into the opening of K3 (vehicle barriers), while
  the K2 heading sat in a passage before Section 3, so the read never showed it. A read now says which requirement
  its passage is under ("…(ADK), under Requirement K2: Protection from falling") and stops where the next one
  starts. Requirements are found from the documents' own openings ("This approved document deals with the following
  requirement from Part K…"): ADB Vol 1 and 2, ADK (with K5.1 to K5.4), ADP and ADQ have them; the others get no
  label rather than a guess.

The landing question is now in the tests, asked in a project ("Loft test"), and the score counts answers that credit
the person's files or notes without citing one. Run C (library 2026.11.06, 10 November 2026):

| Model | Right figures | Searched | Cited | Right passage cited | Made-up ids removed | Credits their files wrongly | Median time |
|---|---|---|---|---|---|---|---|
| Qwen3.5-35B-A3B | 8/8 | 8/8 | 8/8 | 7/8 | 0 | 0 | 16 s |
| Nemotron 3.5 Lightning 30B-A3B | 8/8 | 8/8 | 7/8 | 6/8 | 0 | 0 | 22 s |

On the landing question both gave 900 mm from ADK Diagram 3.1; Qwen, having read para 3.2, named Requirement K2
correctly. Nemotron answered the travel distance (7.5 m) without a citation, and cited ADK 3.3 rather than
Diagram 3.1 for the balcony. One run each, so a slip that comes one time in four may not show yet.

### Run D: 36 questions, the built-in models, and the app's own notes

All 36 questions (`tests/ask_models.py`), on the library built on 11 November 2026 after the column reader was
replaced (`library/columns.py`), one model at a time. The built-in models ran on llama.cpp b12078 (Vulkan), installed
by the app into an empty folder; the others on Lemonade. Every answer was read.

| Model | Engine | Right figures | Searched | Cited | Right passage cited | Made-up ids removed | Figures flagged | Extract-only citations | Median time |
|---|---|---|---|---|---|---|---|---|---|
| NVIDIA Nemotron 3.5 Lightning 30B-A3B | Lemonade | 34/36 | 36/36 | 35/36 | 31/36 | 0 | 3 | 16 of 91 | 24 s |
| Qwen3.5-35B-A3B | Lemonade | 34/36 | 36/36 | 35/36 | 31/36 | 0 | 5 | 65 of 110 | 15 s |
| Qwen3 30B-A3B Instruct 2507 | built-in | 28/36 | 33/36 | 33/36 | 27/36 | 1 | 5 | 49 of 85 | 9 s |
| gpt-oss 20B | built-in | 27/36 | 36/36 | 33/36 | 28/36 | 0 | 3 | 9 of 53 | 12 s |
| Qwen3 8B | built-in | 25/36 | 32/36 | 32/36 | 25/36 | 6 | 4 | 103 of 103 | 31 s |
| Gemma 4 E4B | Lemonade | 22/36 | 36/36 | 36/36 | 22/36 | 0 | 1 | 46 of 73 | 19 s |
| Qwen3 4B Instruct 2507 | built-in | 20/36 | 31/36 | 29/36 | 22/36 | 0 | 3 | 63 of 63 | 6 s |

What reading the answers adds:

- **Nemotron and Qwen3.5** each missed the same two by the checks: the cladding question (both said the class depends
  on height and boundary, from ADB Table 10.1, rather than A2-s1, d0 or A1 for a relevant building) and the NHBC
  question (both said the Standards aren't in the library, then offered ADA's wall tie spacing with citations, which
  the check counts as citing for a question the library can't answer). Nemotron answered radon without a citation;
  Qwen3.5 the gigabit cost cap.
- **Qwen3.5 cites from search extracts far more than Nemotron**: 21 of its 36 answers were written without opening
  a passage, against 2 of Nemotron's. Its figures were right all the same, and the app says so on each such citation.
  If that is too many, the prompt needs a firmer "read before you cite", and these tests run again.
- **gpt-oss 20B** reads before it cites (9 extract-only citations in 53). It wrote its citations as `【r4264】`, which
  the app didn't take for citations: ten answers showed as resting on no passage until that was fixed. It ended two
  questions with no answer at all (the higher-risk approval period and the fire door checks); the app now says so.
- **The Qwen3 2507 models answer from memory first.** The 30B was told to look it up on all 36 questions and the 4B on
  29; after the nudge the 30B is the best of the built-in four. Neither the 4B nor the 8B ever opened a passage: every
  citation of theirs is from a search extract.
- **Qwen3 8B is not better than the 4B.** Over two runs each the 4B got 25 and 20 right, the 8B 22 and 25; the 8B
  takes five times as long, and it is the only model that made up passage ids (7 and 6 removed). In the first run it
  gave 9 m for the travel distance from a flat, from ADB Vol 2's table. On this evidence it shouldn't be recommended
  over the 4B. It was taken out of `para/models.json` after this run; the 4B is the suggestion up to 24 GB.

**The figure note.** 24 figures were flagged in these seven runs. Each was checked against the passage cited:

| Model | Flags | True | False alarm | Extract-only citations |
|---|---|---|---|---|
| NVIDIA Nemotron 3.5 Lightning 30B-A3B | 3 | 1 | 2 | 16 |
| Qwen3.5-35B-A3B | 5 | 4 | 1 | 65 |
| Gemma 4 E4B | 1 | 1 | 0 | 46 |
| gpt-oss 20B, built-in | 3 | 2 | 1 | 9 |
| Qwen3 30B-A3B Instruct 2507, built-in | 5 | 5 | 0 | 49 |
| Qwen3 8B, built-in | 4 | 4 | 0 | 103 |
| Qwen3 4B Instruct 2507, built-in | 3 | 3 | 0 | 63 |

- **True flags** are figures the cited passage doesn't hold: made up ("a maximum pitch of 35°" for a general access
  stair, "43°C" for showers, a second handrail "at 350mm to 600mm", "36 metres" for an office), right but cited to the
  wrong paragraph (45 dB cited to ADE para 0.9, 0.26 W/m²K to ADL1 Table C1, 7 kW to two diagrams), or worked out
  (2,600 mm for the flight of 13 risers, flagged where the model put a citation on the sum; Nemotron and Qwen3.5
  left the sum uncited, so nothing was flagged, rightly).
- **False alarms, two causes, both fixed with a test.** Three were figures cited to ADM Vol 2 Diagram 3, a graph: the
  figures are in Table 1's note, which says "see Diagram 3". A figure cited to a diagram or table now also counts if
  the passage that names it holds it. One was gpt-oss writing "1 000 mm" with a narrow space, read as "000 mm"; the
  same answer's "1 100 mm" for dwellings is a true flag.
- After the fixes the same answers give 20 flags, none of them a false alarm.

### Run E: the Lemonade models through the built-in engine, and the list rewritten

The two models that did best on Lemonade had never been run on the engine the app installs, so weren't listed. Each
was downloaded by the app (llama.cpp b12078) and asked the 36 questions, with Gemma 4 E4B as a small candidate:

| Model | File | Right figures | Searched | Cited | Right passage cited | Made-up ids removed | Figures flagged | Extract-only citations | Median time |
|---|---|---|---|---|---|---|---|---|---|
| Qwen3.5 35B-A3B | 22.2 GB | 33/36 | 36/36 | 34/36 | 29/36 | 1 | 4 | 56 of 125 | 18 s |
| NVIDIA Nemotron 3.5 Lightning 30B-A3B | 25.5 GB | 31/36 | 36/36 | 33/36 | 29/36 | 0 | 3 | 11 of 81 | 23 s |
| Gemma 4 E4B | 5.0 GB | 22/36 | 36/36 | 36/36 | 24/36 | 0 | 2 | 35 of 71 | 22 s |

- **Both big models hold up**: 33 and 31 right against 28 for Qwen3 30B-A3B Instruct 2507, which they replace in the
  list. They are level on accuracy over the two engines (34 and 34 on Lemonade); Nemotron is suggested first because
  it opens the passage before citing it (11 extract-only citations against 56). On this run Qwen3.5 gave 8 weeks for
  the higher-risk approval period (it is 12) and quarterly checks for flat entrance doors (annual): both wrong, both
  with a citation.
- **Gemma 4 E4B is the safer of the two small models.** It and Qwen3 4B get about the same number right, but they fail
  differently: Gemma searches and cites every time, and most of its misses are an incomplete answer or "I couldn't
  find it"; the 4B never opens a passage and gave wrong figures from memory (150 litres a day, a window U-value of
  1.4, 40 dB). Gemma did give two wrong figures for sound insulation (55 and 58 dB), and the figure note caught both.
  It needs about 7 GB loaded, so the 4B stays as the only one for 8 GB computers.
- All nine flags in this run are true.
- **Licences**: Qwen3.5, Gemma 4, gpt-oss and Qwen3 are Apache 2.0. Nemotron 3.5 Lightning is under OpenMDW 1.1
  (NVIDIA's model card says so; the "NVIDIA Nemotron Open Model License" is for other models). Both allow use and
  redistribution with the licence kept alongside. The app doesn't redistribute any of them: it downloads each from
  Hugging Face onto the person's computer.

The list in `para/models.json` after this run: Nemotron 3.5 Lightning from 48 GB, Qwen3.5 35B-A3B from 32 GB,
gpt-oss 20B from 24 GB, Gemma 4 E4B from 12 GB, Qwen3 4B from 8 GB.

## Built-in engine

- `para/models.json`: every repo and file exists on Hugging Face with the listed size (checked with `HEAD` and
  `X-Linked-Size`), except gpt-oss, whose file is `gpt-oss-20b-MXFP4.gguf` (fixed).
- llama.cpp's "latest" release (v0.8.0) has no binaries; builds are published as pre-releases. The engine now
  takes the newest release with a binary for the platform (b12078 here).
- A real download and start with the smallest model worked: Qwen3-4B-Instruct-2507 Q4_K_M, 2,497,281,120 bytes,
  checksum verified, answered through the app with citations.
- A model load that timed out left the stuck server running, so every later question got "503 Loading model". It
  is stopped now, and the message says the computer is probably short of memory.
- gpt-oss-20b, Qwen3 8B and Qwen3 30B-A3B were each downloaded by the app and asked the 36 questions through the
  built-in engine (run D). All three answer with citations; Qwen3 8B was dropped from the list afterwards (see run D). The pin: `para/models.json` names llama.cpp build b12078,
  and all seven platform files its patterns ask for are in that release. A copy of the app that already has a
  llama.cpp build keeps it: the pin is for new installs.

## Choosing defaults

For the built-in list, the candidates that did best and come from the models' own publishers:

What the built-in list is now, and why, is at the end of run E. The earlier notes, from the seven questions:

- **32 GB or more**: Nemotron 3.5 Lightning 30B-A3B or Qwen3.5-35B-A3B (both 7/7, about 30 s). Qwen3.6-35B-A3B is
  close (6/7 plain, 7/7 figures with MTP).
- **16 to 24 GB**: Gemma 4 12B was weaker than its size suggests here.
- **8 GB**: Qwen3 4B is fast but answers from memory sometimes; Gemma 4 E4B is better at searching but picked the
  wrong document for guarding.

The coding and community fine-tunes (Tiel-Coder, KAT-Coder, Muse-Glimmer, Dirk, HRX) scored well but are less
suited as defaults.

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
