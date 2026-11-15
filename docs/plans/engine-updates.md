# Plan: updating the built-in engine

Status: done. Step 1 (engine code, page and tests) is built; step 2 is Run H in `docs/model-tests.md`; step 3 moved the pin to `b12359` (no `min_app`: the flags older apps pass mean the same). Written for an agent to carry out. Read `AGENTS.md` and `DESIGN.md` first.

## Why

The built-in engine (llama.cpp's `llama-server`) is downloaded once, the first time someone installs a model, and never
again. `para/models.json` pins the build (`llama_cpp.tag`, now `b12078`), but changing the pin only reaches new
installs: `Bundled._install` downloads the engine only when `server_path()` finds none. People who installed months ago
stay on the build they got then. llama.cpp has reached 1.0.0, and we want installed apps to follow the build we have
tested, without ever running one we haven't.

## What was decided, and why

1. **The pin stays.** Not `"tag": "latest"`: llama.cpp publishes builds many times a day and the pin is the mark that
   we tested this one with the recommended models. `tests/test_app.py` already checks the tag isn't `latest`. Keeping
   current means bumping the pin after a test run, as often as we like.
2. **The update is offered, not taken.** `AGENTS.md`: nothing leaves the computer except the chosen engine, update
   checks and "downloads they start". So an engine update is a third thing `server.check_updates` can find, beside
   the app and the library, and the person installs it with a button. No download at app start or before a question.
3. **A pin can say which app it needs.** `models.json` is fetched by every installed app whatever its version, so a
   pin can reach app code older than the engine it names. `llama_cpp.min_app` (a version, optional) makes an app
   older than that ignore the update and keep its engine.
4. **The old engine stays until the new one has run.** Download to its own folder, check it, switch, and delete the
   old folder only once the new build has started and loaded a model. If it fails to start, go back to the old one and
   remember the tag that failed, so it isn't offered again.

## What to build

### `para/engine.py`

- **Which engine is in use.** Today `server_path()` walks `engine/` and returns the first `llama-server` it meets; with
  two builds on disk that is whichever the walk reaches first. Record the chosen build in a small file in the app
  folder (say `engine/current.json`: `{"tag": "b12078"}`), written by `_install_server`. `server_path()` looks in
  that tag's folder first and falls back to today's walk when the file is missing (every install made before this
  change), so nothing breaks for existing installs. The engine's folder is already `engine/<tag_name>/`.
- **`installed_tag()`**: the tag in use: from `current.json`, else the name of the folder `server_path()` found the
  binary under, else `None`.
- **`engine_update()`**: what `check_updates` asks. Returns `None` unless all of: an engine is installed; the
  catalogue's `llama_cpp.tag` differs from `installed_tag()`; this app is at least `llama_cpp.min_app` (compare with
  `VERSION` the way `server._newer` does; move that helper somewhere both can use if needed); the tag isn't in the
  failed list. Otherwise `{"tag": ..., "size": ...}` (size from the release asset, if the release can be reached;
  the offer may be made without a size).
- **`update_engine(gpu=True)`**: a background `Job` like `install`, refused while another download runs. It
  downloads the pinned build into its own folder with the checksum check `_install_server` already does (share the
  code: `_install_server` should take the folder work as it is and not assume it's the only build), checks the
  binary runs (`llama-server --version`, short timeout, output to the engine log), and only then writes
  `current.json` with the new tag and `"previous": <old tag>`. The running engine is stopped first, under
  `self.lock`, and nothing is switched while a model is loading. A failed download or check leaves `current.json` and
  the old folder alone and removes the partial new folder.
- **Falling back.** In `start`, if the process exits while loading (the existing "stopped while loading the model"
  branch) or never becomes healthy, and `current.json` has a `previous` whose folder still exists: put `previous`
  back as the tag in use, add the failed tag to `current.json`'s `"failed"` list, delete the failed folder, and start
  once more with the old build. The person's question should still be answered. Say what happened in the engine log
  and in the status the page reads, so Settings can show "The engine update didn't work on this computer, so the
  earlier one is still in use."
  Careful: a model that doesn't fit also fails here. Only blame the engine when the same model started with the
  previous build before; simplest is to fall back only on the *first* start after an update (a `"proven": false`
  flag in `current.json`, set true after the first healthy start).
- **Clearing up.** After the first healthy start on the new build, delete the previous build's folder and drop
  `previous`. `stop_leftover` matches a leftover process by `self.engine_dir in command`, which still holds.
- Keep to the memory rules: never two engines running, nothing loaded during the swap.

### `para/models.json`

- Add `"min_app"` to the `llama_cpp` block only when a pin needs it. Don't change `tag` in this piece of work (see
  "Order of work").
- Apps up to 0.2.3 read this file too and know nothing of `min_app` or updating. They use `tag` and `assets` only for
  a first install. So a new pin always reaches *fresh installs of old apps*: before any bump, check the flags
  `Bundled.start` passes in those versions (`-m --host --port --jinja -c --alias`, plus a model's `args`) still mean
  the same in the new build.

### `para/server.py`

- `check_updates`: after the model list is saved (so the new pin is the one read), add `info["engine"] =
  bundled.engine_update()`. Only when the engine setting is the built-in one or an engine is installed; external
  engines look after themselves.
- A new endpoint beside `/api/updates/library`: `POST /api/updates/engine` starts `update_engine` and returns the
  job; its progress is read the way the library's is. Return JSON through `_send`. Keep the Origin and Host checks.

### `para/static/` (see `DESIGN.md`)

- Settings → Updates & about (`renderAbout` in `app.js`): a row like the library's: "A newer AI engine (llama.cpp
  1.0.0, 38 MB) is available. [Install]", with progress under it, then "Installed. It will be used from the next
  question." "You're up to date." must also wait on `u.engine`.
- The sidebar's update note (`#updateNote`, near line 106 of `app.js`): the app first, then the library, then
  "Engine update available".
- If the fallback happened, a warm note (`.note`) in that tab says so in plain words. British English, sentence case,
  no jargon: say "AI engine", and name llama.cpp once as the row above does.
- Add a short entry to `DESIGN.md` under Components, and take a screenshot only if `docs/take_screenshots.py` can
  make the state without a real update.

### Tests (`tests/test_app.py`)

No network: the tests import the engine in-process (`eng`), so stand in for `_find_release` and `_download` (a tiny
archive holding a fake `llama-server` script that answers `--version`, and one that exits 1 for the failure cases).
Check, through the HTTP API where there is an endpoint:

- with `b1` installed and the catalogue pinning `b2`, `check_updates` offers the engine; with the same tag it doesn't;
- `min_app` above `VERSION` means no offer;
- a tag in the failed list isn't offered;
- after `update_engine`, `server_path()` is the new build's, with both folders on disk;
- a download that fails its checksum, or a binary that won't run, leaves the old build in use and no partial folder;
- first start on a build that exits: back on the old build, the tag recorded as failed, the question still answered
  (use `tests/fake_llm.py` as the "engine" if that's the easiest way to have a healthy process);
- first healthy start removes the previous folder;
- an install with no `current.json` (made before this change) still finds its engine and is offered the update;
- the pin still isn't `latest`.

Then `python3 tests/test_app.py`, `python3 tests/test_check.py`, `python3 -m pyflakes para library tests`. Add a row
for anything new to the map in `AGENTS.md` and a line under "Memory" or "Adding things" on how to bump the engine.

## Order of work

1. **This plan: the code, with the pin unchanged.** It can be merged and released by itself: nothing is offered
   until the pin moves.
2. **Test llama.cpp 1.0.0** on a computer with a GPU (the maintainer's Framework PC; a cloud session has no engine):
   read the release notes and asset names against the `llama_cpp.assets` patterns, run the 36 questions
   (`tests/ask_models.py`) through the built-in engine on 1.0.0 for the recommended models (Granite 4.2 8B and 3B,
   Qwen3.5 35B-A3B, Nemotron if it fits) and compare with `b12078` in `docs/model-tests.md`. One model loaded at a
   time; unload Lemonade first.
3. **Bump the pin** in a separate commit, with `min_app` set to the first release that has step 1 if the notes show
   anything the older code can't work with. The maintainer decides this; don't do it as part of step 1.

## Not in this plan

- Updating the engine without being asked, or at app start.
- Choosing between Vulkan, ROCm and CUDA builds: the `assets` patterns stay as they are unless 1.0.0 renamed files.
  (Since done for CUDA, separately: an NVIDIA card gets a CUDA build, Vulkan kept to go back to; see `AGENTS.md`,
  "Graphics-card builds". ROCm is still not offered.)
- External engines (Lemonade, Ollama, LM Studio): they update themselves.
- Multi-token-prediction (MTP) models in the built-in list: a separate question, answered by the test run in step 2.
