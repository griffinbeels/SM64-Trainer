# SM64 Trainer

A practice tracker for **Super Mario 64 — Usamune v1.93u (US)** running in
**Project64 1.6** on Windows.

Play normally. It watches the emulator's memory and does the rest: every star
grab timed to Usamune's own clock, every reset and death, your PBs, your
ranks, and an instant video replay of the attempt you just finished.

Follow the guided setup once, then open it and practise.

---

## Install

1. **Download `SM64Trainer.exe`** from the [Releases page](../../releases) —
   a small installer, about 11 MB.
2. **Run it.** Windows SmartScreen warns because the app is unsigned: click
   **More info → Run anyway**. It installs to
   `%LOCALAPPDATA%\Programs\SM64Trainer`, puts an **SM64 Trainer** shortcut on
   your Desktop, and launches. **That shortcut is your launcher from now on** —
   it keeps working no matter how many updates land.
3. **Follow the setup wizard.** Select Emulator, open Project64 1.6 so the
   trainer can find it, close it to install Practice Replay, then reopen it
   and load Usamune v1.93u (US), **windowed**. Setup checks the game, replay
   pictures and inputs before **Ready to practice!** takes you to Practice.

That's it — no Python, nothing else to install.

> Prefer a portable copy? Grab `SM64Trainer-full.zip` instead, extract it
> anywhere, and run the exe inside. It self-updates in place wherever it lives.

**Updates are automatic and small.** On launch the app checks GitHub; if
there's a newer release you get a popup with the patch notes and the exact
download size. Only the files that actually changed come down — usually ~25 MB,
sometimes a few kilobytes, never the whole app — each one SHA-256 verified,
with automatic rollback if anything is interrupted. Then it restarts. Your
history and PBs are untouched. **Skip this version** silences one release;
**Later** dismisses until next launch. (In-app updates don't trip SmartScreen —
only the first manual download does.)

### What you need

- **Windows 11** 64-bit. Windows 10 works if the
  [Edge WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
  is installed; it's preinstalled on 11.
- **Project64 1.6** — other versions are not supported, the memory addresses
  are 1.6-specific.
- **Usamune v1.93u (US)**. Setup recognizes **JP** and lets you finish the
  available installation, with a warning that JP tracking is not supported yet. The rank
  standards already know both, and **Settings → Game → Game version**
  (Auto-detect / JP / US) picks which one grades you, live — but every memory
  address the trainer reads is kept per ROM version, and JP's are discovered
  by playing through `tools/sync_version.py --version jp` with the JP ROM
  loaded while `/ui/sync.html` shows US beside JP filling in live. Until that
  run has been done and its report promoted, JP reads nothing. The runbook:
  [`docs/version-sync.md`](docs/version-sync.md).
- **Windowed**, not exclusive fullscreen, or replay capture can't see the game.
- Your data lives in `%LOCALAPPDATA%\SM64Trainer\` and survives every update.

**Setup and Practice Replay.** Each page shows the current action, with
optional help for getting Project64, setting up your own Usamune ROM, and
configuring the game/controller. Pages advance after a visible acknowledgment;
Back lets you review earlier steps. **Not now** postpones unfinished setup
for the current app session. Open it again from **Settings → Setup**.

Already set up? The trainer compares the installed wrapper's SHA-256 hash with
this build and checks its selected graphics configuration. A matching installation
skips automatic onboarding, including in a fresh worktree. A stale or incomplete
installation opens its repair step directly. Only Project64 installation metadata
is shared across checkouts; your practice data stays in its existing location.

Installing Practice Replay adds a **capture layer** to Project64's plugin
folder and selects it as the `Graphics Dll`. The wrapper forwards drawing to
your existing graphics plugin and supplies replay pictures stamped with the
game's frame counter and inputs. Install while Project64 is closed; open
Usamune once afterward to check it. **Manage Practice Replay → Remove Practice
Replay** restores the previous graphics selection, also with Project64 closed.
The trainer can still use desktop capture when you postpone installation.
N64 setup currently explains that console tracking is in development.

In an attempt's replay drawer, **Save as template** keeps a named example for
future attempts. **Import inputs** accepts a text file or pasted document;
**Templates** lets you filter, select, copy, export or remove saved examples.
**Copy inputs** copies a compact document you can paste into a message; its name
autofills when imported. Files use the same text format. The
amber template overlays your blue inputs on the same rows and direction dials,
aligned at frame zero. **Template overlay rows** remembers which rows you want
to compare. Input files and player credit stay local; community browsing and
hosted video sharing are future features.

> **No sound on a saved replay?** Windows has routed the app's audio — and the
> embedded browser's (`msedgewebview2.exe`) — to a different output device.
> Open **Settings → System → Sound → Volume mixer** and point both at your
> speakers. (A WebView2 quirk in the current build.)

---

## What it does

**Practice.** Every star grab, reset, death, level change, Bowser key and
dustless trick is detected live. Each star and segment gets its own card:
attempt history, PB, a timeline of what happened, and a completion-time graph
you can click to jump straight to an attempt — or open its replay.

**Segments.** Not just stars: define any repeatable piece of the run (LBLJ,
a pipe entry, a Bowser fight, a castle movement) and it becomes a first-class
practice target with the same history, PBs, ranks and replays a star gets.

**Bring your times with you.** Practised for years before finding this? Two
ways in: type a time straight onto any star's card, or pick your name in
Settings if you have a column on the Ultimate Sheet — every star time lands,
the Bowser course and fight rows land on their movements, and a subsection
lands the moment you link its Library row to a segment you built. A time only
lands when it beats the best you already hold for that star and strategy, so
importing twice costs nothing. Each one becomes a real row in that star's
practice log — clear it, undo it, move it to another strategy like any run —
and every import can be undone in one click. Anything it could not use is
listed back at you by name, under the reason, rather than quietly skipped.
Recording links come with those times. Open a replay to watch the embedded
video, then choose Download to enable full replay features. The Library and
Compare reuse that local copy. Add or change a recording link directly in the
replay panel, with Undo after saving.

**Score yourself against a goal.** The Rank tab's scorecard lines every star
and castle movement up as a tile against a goal, showing your gap tile by tile
and a summed gap per course. By default the goal follows your scope's MARELO
rank plus one subdivision (up to Mario 1), using each entry's Library standards.
Rank Goal chooses one division or Automatic. Player Goal adds any number of
runners, and Custom Goals saves named sets of the stars and segments you edit.
Enable or remove a whole set together. Manual choices stay across routes;
Automatic follows the route even with players or custom sets selected.
Copy your own times as a sheet column ready to paste
beside everyone else's, including each time's public recording hyperlink,
or as a full CSV.

**Ranks.** Every attempt, banner and route step wears a rank badge, graded
against community standards — **per strategy**, so a fast time on one strat
never flatters another. A header picker switches what the badges grade: your
PB, or the mean of your last 10/50, or your best 10/50, or your lifetime
average.

**MARELO.** One 0–100 rating that rolls up everything you practise, per scope:
Overall, a single course, or any route. It has its own tab with a ladder, a
history chart, and a per-entity breakdown of where your next rank is coming
from.

**Living Overall ranks.** Each star or segment blends progression through
compatible strategies with the community's submitted best times. Valid Sheet
refreshes update Strategy standards, Overall ranks, and MARELO automatically;
saved times stay unchanged. Strategy and Overall cutoffs have separate edits.
See [ranking calibration and tuning](docs/ranking-calibration.md).

**Routes.** Build an ordered route of stars and segments — including
"complete K of N" group steps — and see per-step and cumulative success rates.
Pick one as active and the Practice tab focuses on just that route. Export any
route as JSON to share.

**Run mode.** Run the whole game as a forgiving RTA. A per-route start
condition arms the clock (default F1 reset), retries roll up into each step's
split, and you get live ± against your PB, gold splits, pause/reset, a calming
Focus mode, and a saved run history with a PB-progression graph.

**Instant replay.** The PJ64 window and game audio are always being recorded
into a rolling buffer, so you can watch the video of any attempt right after
it happens — and save the ones worth keeping, forever. Audio is captured from
Project64 alone, so a Discord call, music or a video playing in the background
never ends up in a clip you upload.

**Library.** Browse the whole community spreadsheet without leaving the app —
every star and segment's proven ways to do it, laid out beginner to expert
with real recorded times filed by the rank and subdivision they'd earn
against your own standards, down to "Wario 3", with a JP/US switch at the top
that re-files everything under the other version's standards (the same switch
sits on every Rank standards panel — a way to look, not a change to how you
are graded). Opens straight on whatever you last practiced, and
a search box finds any star or movement as you type — by its own name or by
the name of a way of doing it, so "LBLJ" lands on the one that documents it.
Runs with video play in place — YouTube, Twitch, X, Bluesky, Streamable,
Drive, or a raw clip file — and runs without one still count as times. Dock
clips to the tray to watch several side by side, or send the tray to Compare
— pulled from YouTube, a local file, or a browser upload — for one
frame-accurate transport driving them all in lockstep. Every Sheet strategy
and subsection receives all 45 rank subdivisions, even with one submitted
time. Narrow ranges extend slower one frame per subdivision; rows without
submissions show estimates with their source explained. See
[how Sheet standards are derived](docs/sheet-rank-standards.md). Library and Practice
share the same standards and update together when you edit or refresh them.
Missing movements and subsections become manual practice entries under their
parent. You can import times immediately and configure detection in Segments.
Every star, Bowser course
and named movement (LBLJ, Lakitu Skip, MIPS…) wears a small Ukiki face beside
its name — Ukikipedia's own icon — that opens its RTA Guide page; it appears
only where the wiki actually has a page, so it never leads to a 404.

**Debug report.** Something behaving oddly? **Settings → Debug report** writes
a single file capturing what the trainer was doing (logs, recent events,
health) and shows it in Explorer — attach it to your bug report.

---

## Develop

### Run from source

```
uv sync
uv run python -m sm64_events.desktop    # the desktop app (window + tray)
uv run python -m sm64_events.main       # headless — then open http://127.0.0.1:8065/
```

Run from the repo root: from source, `data/` is created relative to cwd.
`uv` only — never pip. The dev server binds **8065**; the packaged exe binds
**8064**, so the two never collide. Set `SM64_PORT` to override.

The **↻ restart server** button in the app header relaunches the backend to
pick up code changes. UI files are served per request — edit and refresh, no
restart needed.

### Test

Use Node 24.13+ on the 24.x line (or Node 26+) for browser-free component tests.
Install their locked dev dependencies once per checkout:

```
npm ci --prefix tests/frontend --ignore-scripts
```

```
uv run python tools/run_tests.py tests/test_ui_components.py  # focused, no browser
uv run python tools/run_tests.py                             # full integration gate
```

Use [the testing guide](docs/testing.md) to choose a focused check and share
the machine budget across worktrees. The full suite must pass before integration;
an identical tested tree reuses its evidence. Two live-only gates need PJ64 running:

```
uv run python tools/verify_addresses.py     # every memory address, against the real game
uv run python tools/dedupe_journal.py data/tracker.db   # scan for double-journaled events
```

`run-test-server.bat` starts a server from the latest committed `main` on port
8066 — safe to run alongside your real trainer — and prints every dev page it
hosts with the port already filled in.

### Build

```
uv run python tools/build_exe.py            # --mode app|bootstrap|all
```

Produces `dist\SM64Trainer\` (the onedir app) and `dist\SM64TrainerSetup.exe`
(the tiny bootstrap installer, published as the `SM64Trainer.exe` release
asset). ffmpeg is picked up from PATH and bundled automatically — pass
`--ffmpeg PATH` to point at a specific binary. The build re-execs itself with a
fixed `PYTHONHASHSEED` and `SOURCE_DATE_EPOCH` so unchanged files hash
identically between releases, which is what keeps update downloads small.
`build.bat` is the double-click version.

> The released exe bundles **ffmpeg** (https://ffmpeg.org) for replay encoding.
> FFmpeg is licensed under the GPL/LGPL; it ships as a separate binary within
> the app and is used unmodified.

### Release (maintainer)

```
uv run python tools/release.py 1.1.0        # or --notes-file NOTES.md
```

One command. It refuses unless you're on `main` with a clean tree and `gh` is
authenticated, runs the full suite, bumps the version, **builds before
tagging** (so a broken build aborts with nothing pushed), then publishes six
assets: the app zip, the per-file update manifest, the bootstrap installer, and
a `.sha256` for each. **Prereqs:** ffmpeg on PATH and `gh auth login`.
`--dry-run` builds and checksums without publishing.

The in-app updater needs all four of the zip/manifest assets and their
checksums — a release published without them is simply never offered to users,
which is the intended failure mode: no unverified bytes ever get applied.

---

## Working on this with Claude

This codebase is written almost entirely by Claude, and it is *organised* for
that. If you're pointing an agent at it, the important thing to know is that
the guidance is **path-scoped**, not one giant file:

| Where | What it is |
|---|---|
| `CLAUDE.md` | The index. Commands, the zone map, the domain rules, definition of done. Read first. |
| `.claude/rules/*.md` | "Where to change what" per zone. Claude Code loads one **automatically** when you open a file it owns, so a rank change never pays for the replay subsystem's history. |
| `AGENTS.md` | The same routing for Codex, which can't auto-load a scoped rule and has to open them by hand. |
| `docs/architecture.md` | Cross-cutting domain knowledge — the facts that were hard to win, kept with their evidence. |
| `docs/api.md` | The full REST/WebSocket surface. |
| `.claude/skills/` | Procedures: cutting a release, UI/UX work, building a tuning inspector. |

Four conventions do most of the work, and they're worth knowing before you
change anything:

1. **Tests are the spec.** `tests/test_<module>.py` mirrors every module. Read
   the test before the source; it says what the contract actually is.
2. **A rule that matters is a test, not a sentence.** "Don't repeat yourself"
   can't fail a build. So the things that must not drift have guards that can:
   `test_single_source.py` (one derivation, one door),
   `test_cross_language_parity.py` (the four values defined in both Python and
   JS), `test_docs_cover_api.py` (every route documented),
   `test_docs_links_resolve.py` (no pointer into a private directory),
   `test_rule_files.py` (every rule file still reaches someone).
   New guards are **proved by mutation** — break the thing, watch it fail,
   restore — because a scan that matches nothing is green forever.
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

SM64 Trainer is free software: [GPL-3.0-or-later](LICENSE). Fork it, build on
it, learn from it — but if you distribute it or anything derived from it, you
must keep it open source under the same license. The SM64 community's tools
and knowledge stay free.
