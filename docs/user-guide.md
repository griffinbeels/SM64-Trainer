# SM64 Trainer user guide

The detailed reference for installing and using the trainer. The
[README](../README.md) is the overview; the app's own screens explain
themselves, and this page is where a behavior gets its full description.

## Install

1. **Download `SM64Trainer.exe`** from the [Releases page](https://github.com/griffinbeels/SM64-Trainer/releases) —
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
- **Usamune v1.93u (US) or v1.93j (JP)**. The trainer detects the loaded ROM
  and follows US↔JP swaps automatically, including tracking, inputs and ghosts.
  Ghosts and practice PBs are shared where a strategy plays the same on both
  versions; strategies with regional differences keep separate ghosts and PBs.
  **Settings → Game → Game version** (Auto-detect / JP / US) controls grading;
  Auto-detect follows the loaded ROM. MARELO remains your overall rating, with
  each PB graded against its own region's standards. Scorecard **You** follows
  the Scorecard's US/JP filter. Unverified ROM builds cannot record attempts,
  PBs or publishable ghosts. Address verification for future builds is covered
  in [`docs/version-sync.md`](version-sync.md).
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
folder and selects it as the `Graphics Dll`: the trainer's own renderer
(`sm64_trainer_gfx.renderer`, LINK's GLideN64 v4.2 with the capture hooks,
GPL-2.0, credits in its About box) and the capture wrapper (`SM64 Trainer
v1.0`) that loads it and supplies replay pictures stamped with the game's
frame counter and inputs. Project64's plugin list shows only `SM64 Trainer
v1.0`; an older install's second entry goes away when the trainer updates
it. Your existing graphics plugin stays in the folder
untouched. Install while Project64 is closed; open Usamune once afterward to
check it. **Manage Practice Replay → Remove Practice Replay** selects your
previous graphics plugin again, also with Project64 closed.
Practice tooling runs only on the Usamune practice ROM. Load vanilla SM64,
another hack or another game for a real run and the same plugin draws as
plain GLideN64, while the trainer neither tracks nor records.
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


## What it does

**Practice.** Every star grab, reset, death, level change, Bowser key and
dustless trick is detected live. Each star and segment gets its own card:
attempt history, PB, a timeline of what happened, and a completion-time graph
you can click to jump straight to an attempt — or open its replay. The first
success after moving to a different star or segment returns Practice to the
top of the page; repeat successes leave your scroll position alone.
Choosing a strategy also classifies pending attempts and automatically saves
the fastest eligible success as GOLD. Correcting the latest attempt's strategy
saves it when it beats that strategy's PB, with the usual rank celebration.

**Segments.** Not just stars: define any repeatable piece of the run (LBLJ,
a pipe entry, a Bowser fight, a castle movement) and it becomes a first-class
practice target with the same history, PBs, ranks and replays a star gets.

**Ghost Overlay.** Race recorded Mario poses in the game window, following the
live camera and terrain through the trainer's GLideN64 renderer. Adjust colour,
visibility, sound and the route drawn ahead. Your current PB supplies the default
ghost; saved local recordings and imported ghosts can race alongside it. When
that PB has no recorded Mario poses (an older or imported PB), your fastest
recorded run races in its place, shown as **Best recorded run**; your PB stays
the rank you chase. The ghosts picked for you come from every strategy on that
star, starting just above your PB, and each one's strategy shows under its name.

**Share ghosts.** Open **Ghost Library · Import / Export** from a star or segment.
Export that target's overall best, each strategy's best, all your personal bests,
or a custom selection. The app remembers your player name and can save a search
for next time. Import previews a collection before you confirm: choose all new
ghosts, comparable ghosts faster than yours, or a smaller selection. Browsing
the library leaves your practice target unchanged.

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
See [ranking calibration and tuning](ranking-calibration.md).

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

Frame-exact replay, where every picture carries the input that drew it, needs
an NVIDIA GPU: the trainer's graphics plugin captures inside Project64 and an
NVENC encoder records the pictures ([how it works](replay-gpu-runtime.md)).
Until the plugin is set up, replays record the Project64 window without that
input match, using NVIDIA, AMD or Intel hardware encoding when a startup check
passes and the CPU otherwise.

Mark an attempt as a PB to save its replay automatically. PB selection stays
manual. Unsaved footage shares a modest storage budget and is discarded when
the session ends; storage pressure removes the oldest unsaved footage first.
Explicitly saved replays remain on disk. See [replay storage](replay-storage.md).

On an RTX 40-series or newer card, the trainer records in AV1 and a saved
replay is already about a third the size the moment you save it, with nothing
left to do afterwards. It asks your card at the start of each recording and
records H.264 when the card has no AV1 encoder.

Saving is instant, and for an H.264 recording the trainer makes the saved
replay smaller in the background afterwards — around a third of its original
size, in the same file, with the picture, the sound and the frame-by-frame
input match all unchanged.
It checks the smaller version against the original before replacing it, and
keeps the original if anything differs. Nothing waits on this: you can watch
a replay, keep playing, or close the app, and an unfinished one is finished
the next time you open the trainer. Closing while it is still working says
what is left and offers to wait. The recording panel lists the same progress
at any time.

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
[how Sheet standards are derived](sheet-rank-standards.md). Library and Practice
share the same standards and update together when you edit or refresh them.
Missing movements and subsections become manual practice entries under their
parent. You can import times immediately and configure detection in Segments.
Every star, Bowser course
and named movement (LBLJ, Lakitu Skip, MIPS…) wears a small Ukiki face beside
its name — Ukikipedia's own icon — that opens its RTA Guide page; it appears
only where the wiki actually has a page, so it never leads to a 404.

**Automatic error reports.** Builds configured for Sentry send technical failures
in the background. **Settings → Error reports** turns collection off and clears
pending uploads and breadcrumbs. Errors include bounded capture, setup and ghost
playback evidence, and the first error of each kind carries the same filtered
Debug report you can export. Reports filter private paths and credentials;
account identities, unrelated applications, screenshots and recordings are excluded.
See the [data inventory and retention limits](error-reporting.md).

**Debug report.** Something behaving oddly? **Settings → Debug report** writes
a single file capturing what the trainer was doing (logs, recent events,
health, recognized native log records and ghost playback state) and shows it in
Explorer. Custom labels and private paths are omitted. Exports expire after a
day, with at most three bounded files retained; attach the file to your bug report.
