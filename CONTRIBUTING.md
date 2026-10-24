# Contributing to SM64 Trainer

Thanks for helping. Bug reports, ideas and pull requests are all welcome;
[open an issue](https://github.com/griffinbeels/SM64-Trainer/issues) first for
anything large, so we can agree on the shape before you build it.

## Run it from source

You need Windows 10 or 11, [uv](https://docs.astral.sh/uv/) (Python 3.12+),
and Node 24 for the interface tests. To play against it you also need
Project64 1.6 with your own Usamune ROM; without them the app still opens and
you can work on the interface.

```
git clone https://github.com/griffinbeels/SM64-Trainer.git
cd SM64-Trainer
uv sync
npm ci --prefix tests/frontend --ignore-scripts
npm ci --prefix tools/verification --ignore-scripts
uv run python -m sm64_events.desktop    # the desktop app (window + tray)
uv run python -m sm64_events.main       # headless; then open http://127.0.0.1:8065/
```

Run from the repo root: from source, `data/` is created relative to cwd.
`uv` only, never pip. The dev server binds **8065**; the packaged exe binds
**8064**, so the two never collide. Set `SM64_PORT` to override.

The **↻ restart server** button in the app header relaunches the backend to
pick up code changes. UI files are served per request: edit and refresh, no
restart needed.

The three native DLLs (the capture wrapper, the GPU encoder and the replay
renderer) are build outputs, never committed. `uv run python tools/build_natives.py`
builds whichever are out of date with the MSVC toolchain. The renderer also
needs three prebuilt libraries that are not in git
([renderer/README.md](renderer/README.md)); without them a source checkout
runs fine, and the setup screen reports the replay capture layer as
unavailable.

For maintainer segment testing, `run-test-server.bat` makes **Save for everyone**
the primary editor/recorder action. It saves default fixes into release source;
released apps keep edits personal. See [saving default segment fixes](docs/default-segment-authoring.md).
It starts a server from the latest committed `main` on port 8066, safe to run
alongside your real trainer, and prints every dev page it hosts.

## Test

```
uv run python tools/run_tests.py tests/test_ui_components.py  # focused, never queues
uv run python tools/run_tests.py                             # the merge check: this change's blast radius
uv run python tools/full_run.py status                       # the full run on GitHub
```

Locally only the tests a change can affect run; they must pass before
integration, and an identical tested tree reuses its evidence. The whole
suite runs on GitHub Actions on every push to main and must be green before a
release. [The testing guide](docs/testing.md) explains how the radius is
chosen and the machine budget. Two live-only gates need Project64 running:

```
uv run python tools/verify_addresses.py     # every memory address, against the real game
uv run python tools/dedupe_journal.py data/tracker.db   # scan for double-journaled events
```

## Build

```
uv run python tools/build_exe.py            # --mode app|bootstrap|all
```

Produces `dist\SM64Trainer\` (the onedir app) and `dist\SM64TrainerSetup.exe`
(the tiny bootstrap installer, published as the `SM64Trainer.exe` release
asset). ffmpeg is picked up from PATH and bundled automatically; pass
`--ffmpeg PATH` to point at a specific binary. The build re-execs itself with a
fixed `PYTHONHASHSEED` and `SOURCE_DATE_EPOCH` so unchanged files hash
identically between releases, which is what keeps update downloads small.
`build.bat` is the double-click version.

The released exe bundles **ffmpeg** (https://ffmpeg.org) for replay encoding:
gyan.dev's full build, which is GPL-3.0, shipped unmodified as a separate
program. Every bundled component's license, and where the GPL parts' source
is, ships in the app's `_internal\licenses` folder; the index is
[licenses/README.md](licenses/README.md).

## Release (maintainer)

```
uv run python tools/release.py 1.1.0        # or --notes-file NOTES.md
```

One command. It refuses unless you're on `main` with a clean tree and `gh` is
authenticated, runs the full suite, bumps the version, **builds before
tagging** (so a broken build aborts with nothing pushed), then publishes six
assets: the app zip, the per-file update manifest, the bootstrap installer, and
a `.sha256` for each. **Prereqs:** ffmpeg on PATH and `gh auth login`.
`--dry-run` builds and checksums without publishing. Betas go through
[the release pipeline](docs/release-pipeline.md).

The in-app updater needs all four of the zip/manifest assets and their
checksums; a release published without them is simply never offered to users,
which is the intended failure mode: no unverified bytes ever get applied.

## The README's pictures

The app screenshots in [docs/readme/](docs/readme/) come from the isolated UI
fixture, not a live session: `uv run python tools/readme_media.py` regenerates
every one after an interface change. The in-game clips are captured by hand
from Project64. Keep the README's words about outcomes ("every attempt is
timed") rather than controls ("click Save as PB"), so a redesign changes a
picture, not a paragraph.

## Working on this with an AI agent

This codebase is written almost entirely by Claude, and it is *organised* for
that. If you're pointing an agent at it, the important thing to know is that
the guidance is **path-scoped**, not one giant file:

| Where | What it is |
|---|---|
| `CLAUDE.md` | The index. Commands, the zone map, the domain rules, definition of done. Read first. |
| `.claude/rules/*.md` | "Where to change what" per zone. Claude Code loads one **automatically** when you open a file it owns, so a rank change never pays for the replay subsystem's history. |
| `AGENTS.md` | The same routing for Codex, which can't auto-load a scoped rule and has to open them by hand. |
| `docs/architecture.md` | Cross-cutting domain knowledge: the facts that were hard to win, kept with their evidence. |
| `docs/api.md` | The full REST/WebSocket surface. |
| `.claude/skills/` | Procedures: cutting a release, UI/UX work, building a tuning inspector. |

Four conventions do most of the work, and they're worth knowing before you
change anything:

1. **Tests are the spec.** `tests/test_<module>.py` mirrors every module. Read
   the test before the source; it says what the contract actually is.
2. **A rule that matters is a test, not a sentence.** "Don't repeat yourself"
   can't fail a build. So the things that must not drift have guards that can:
   `test_single_source.py` (one derivation, one door),
   `test_cross_language_parity.py` (the values defined in both Python and
   JS), `test_docs_cover_api.py` (every route documented),
   `test_docs_links_resolve.py` (no pointer into a private directory),
   `test_rule_files.py` (every rule file still reaches someone).
   New guards are **proved by mutation**: break the thing, watch it fail,
   restore, because a scan that matches nothing is green forever.
3. **UI changes are verified by rendering**, never by unit tests plus
   `node --check`. That combination once shipped an invisible feature, and
   `node --check` returns 0 on JS that cannot even be imported.
4. **Anything judged by feel gets an inspector, not a guess.** Timings, easing,
   transitions: build the tuning page first, tune it live, and let SAVE write
   the numbers back into the repo as the new defaults. `/ui/tune.html` is the
   worked example.

Everything a session learns that the next one would otherwise re-derive goes
back into one of the files above, next to the code it's about. Stale
documentation here is treated as a broken build.

Building your own client instead? Speak only to the API: `ws://…/ws/events`,
`GET /state` for initial state, `GET /health` for liveness. Schemas in
[`docs/api.md`](docs/api.md).

## License

By contributing you agree your work is released under the project's
[GPL-3.0-or-later](LICENSE) license.
