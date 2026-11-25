<!-- English or Russian, whichever is easier for you. / Можно по-английски или по-русски. -->

## What changed

<!-- What this does and why. Link the issue if there is one: "Fixes #12". -->

## How it was tested

- [ ] `cargo test`
- [ ] `python -m unittest discover -s mod/tests`
- [ ] Tried in the game (say how below: one or two computers, host or guest, local network or session server, game version)
- [ ] Not needed: documentation or translations only

<!-- What you did in the game and what you saw. -->

## Checklist

- [ ] No code or assets of the game: nothing decompiled or extracted from it, and nothing from `/decompiled/` or `/captures/`.
- [ ] Nothing that unlocks content a player does not own.
- [ ] Code in `mod/` still runs on Python 3.7 and does not block the game's simulation thread.
- [ ] New findings about the game are described in `docs/internals.md`, with how to reproduce them.
