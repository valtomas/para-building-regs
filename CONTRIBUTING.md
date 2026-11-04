# Contributing to Para

Thank you for helping. Para is free software under the GNU Affero General Public License, version 3 or later
(see [LICENSE](LICENSE)).

Before you start, read [AGENTS.md](AGENTS.md) (how the code fits together, conventions, how to test) and
[DESIGN.md](DESIGN.md) (the interface).

## Licence of contributions

By opening a pull request you agree that:

1. your contribution is your own work, or you have the right to submit it;
2. it is licensed under the GNU Affero General Public License, version 3 or later; and
3. the project's maintainer (valtomas) may also distribute your contribution under other licences, for example to
   offer Para as a hosted service or to relicense the project in future.

Point 3 keeps the project able to change course. If you don't agree to it, say so in your pull request before it is
merged.

## What helps most

- Reports of wrong answers or wrong citations, with the question, the model and what the right paragraph is.
- Fixes to how documents are split into passages (`para/splitter.py`, `library/`), checked against the PDFs.
- Checking descriptions of diagrams on the review page (`python3 library/describe.py review`).

Run `python3 tests/test_app.py` and `python3 -m pyflakes para library tests` before opening a pull request.
