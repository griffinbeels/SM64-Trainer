# sm64_tracker

A practice-stats tracker for **Super Mario 64 — Usamune v1.93u (US)** running
in **Project64 1.6** on Windows. It reads the emulator's memory live, detects
star grabs (with exact Usamune timing), resets, deaths, segments and more,
and shows it all in a tracker UI — attempt history, PBs, timelines, and
instant-replay video of every attempt.

## Quick start (just want to use it)

1. **Download** the latest `sm64_tracker.exe` from the
   [Releases page](../../releases).
2. **Run it.** First launch shows a Windows SmartScreen notice (the app is
   unsigned) — click **More info → Run anyway**. A window opens.
3. **Start Project64 1.6** with **Usamune v1.93u (US)**, **windowed** (replay
   can't capture exclusive fullscreen). The tracker attaches automatically.

That's it — no install, no Python, nothing else to set up.

### Requirements / assumptions

- **Windows 11** 64-bit (Windows 10 works if the
  [Edge WebView2 runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
  is installed — preinstalled on Win11).
- **Project64 1.6** (other versions not supported — addresses are 1.6-specific).
- **Usamune v1.93u — US**. The **JP** version is **untested and unsupported**.
- For replay video: run PJ64 **windowed** (the released exe bundles ffmpeg).
- Your data lives in `%LOCALAPPDATA%\sm64_tracker\` — history and PBs survive
  upgrading the exe.

## Run from source (developers)

```
uv sync
uv run python -m sm64_events.desktop    # the desktop GUI
# or, headless / browser-only:
uv run python -m sm64_events.main        # then open http://127.0.0.1:8064/
```

Run from the repo root (from source, `data/` is created relative to cwd).
`uv run pytest -q` must pass before any merge. The **↻ restart server** button
in the header relaunches the backend to pick up code changes.

## Build the portable exe

```
uv run python tools/build_exe.py
```

→ `dist\sm64_tracker.exe` (one self-contained onefile build; ffmpeg on PATH is
bundled automatically — pass `--ffmpeg PATH` to point at a specific binary).
See `tools/build_exe.py` for what gets bundled.

> The released exe bundles **ffmpeg** (FFmpeg, https://ffmpeg.org) for replay
> encoding. FFmpeg is licensed under the GPL/LGPL; it ships as a separate
> binary within the exe and is used unmodified.

## What it does

- Live star-grab detection with exact Usamune IGT, resets, deaths, level/area
  changes, Bowser keys, dustless tricks, and user-defined **segments**.
- A practice tracker UI: per-star attempt history, PBs, timelines, progress
  graphs, and a one-click stage quick-select.
- **Instant replay**: records the PJ64 window + game audio and lets you watch
  (and save) the video of any attempt.

## More

- **API / event reference:** [`docs/api.md`](docs/api.md)
- **Developing here:** `CLAUDE.md` (module map, domain rules, recipes)
- **Deep domain reference:** `docs/architecture.md`
