# Desktop GUI + One-Click Portable Build

Date: 2026-06-14 · Status: approved by user (brainstorming session)

## Problem

The tracker today is a Python/uvicorn server you run from a checkout
(`uv run python -m sm64_events.main`) and view in a browser tab at
`127.0.0.1:8064`. That's friction for anyone who isn't a developer: clone,
install uv, sync deps, run from the repo root, open a browser. The goal is a
**single double-clickable `.exe`** that a SM64 practice player can download
from the GitHub releases page and run with **zero setup** — it starts the
server underneath, opens the existing UI in a native resizable window, and
"just works" against their PJ64 1.6 + Usamune v1.93u (US).

Two deliverables in this pass:

1. **Easiest-possible install docs** — a README that is a clear front door
   for both a brand-new end user (the exe) and a new engineer (run from
   source like today), with hardware/software assumptions stated explicitly.
2. **A full GUI desktop app** — the existing browser UI in a native window,
   server hidden underneath, single-instance enforced with a user-facing
   takeover dialog, packaged as one portable exe with a one-command build.

## Scope (decided with user)

- **GUI framework:** `pywebview` window over the existing UI, rendered via
  the **Edge WebView2** runtime (preinstalled on Windows 11). The window IS
  the existing `ui/` served by the same server — no UI rewrite, automatic
  browser↔GUI parity.
- **Window:** freely **resizable**, no max bound — must fill a full
  landscape OR a full portrait monitor (the user relies on a tall vertical
  layout today). `min_size ≈ 360×500`. Last geometry (size/position/maximized)
  persisted and restored.
- **Server:** runs in a background daemon thread inside the same process;
  shut down cleanly on window close. The terminal launch
  (`uv run python -m sm64_events.main`) is UNCHANGED and the browser at
  `:8064` keeps working even with the GUI window open.
- **Data location:** when frozen (exe), `%LOCALAPPDATA%\sm64_tracker\`
  (db, replay buffer, saved replays, settings, lock, window geometry). When
  run from source, cwd-relative exactly as today — dev workflow and tests
  unchanged.
- **Single instance:** native dialog on launch if another instance is
  running — "Use this window (close the other)" vs "Cancel". Takeover is a
  graceful shutdown of the running instance with a force-kill fallback.
- **Packaging:** PyInstaller `--onefile` → one `.exe`. ffmpeg bundled in for
  replay quality. Unsigned (documented SmartScreen "Run anyway" step; signing
  left open for later).
- **GUI extras for v1:** app icon + branding (exe/window/taskbar/tray) and a
  **system tray icon** (show/hide, quit). Native toast notifications are
  explicitly deferred. The single-instance dialog is included regardless.
- **Restart server:** a one-click "Restart server" button in the UI (browser
  + GUI) that fully RELAUNCHES the process — the only way CPython picks up
  edited backend code. Reuses single-instance takeover: relaunch with an
  `SM64_RESTART` flag so the fresh process waits for the old to exit and
  skips the dialog.
- **Build:** one command (`uv run python tools/build_exe.py`) driven by a
  committed `.spec`. GitHub Actions release automation is an optional later
  enhancement, not in this pass.
- **Platforms:** Windows 11 (Win10 with a WebView2 link) 64-bit; PJ64 **1.6**;
  Usamune **v1.93u US only** (JP untested → unsupported for now); PJ64
  windowed (replay can't capture exclusive fullscreen).

## Decision — Approach A (pywebview + in-process uvicorn + PyInstaller)

Chosen over two alternatives:

- *Launcher exe that opens the default browser* (no webview): simplest and
  zero packaging risk, but it's a browser tab — fails the "GUI in a window",
  app-icon, and system-tray requirements. Rejected.
- *Electron/Tauri front-end*: maximum native polish, but two toolchains, a
  150 MB+ Chromium bundle, and re-plumbing the UI's data flow — massive
  overkill when the UI already runs perfectly in a webview. Rejected.

Approach A is the only one that satisfies native window + tray + single
shareable exe while leaving the UI and server code paths untouched, which is
also what makes browser/GUI parity free going forward. The desktop shell is
purely additive.

## Design

### 1. Path resolution (`core/paths.py`, new — the one cross-cutting change)

Today `data/tracker.db` and `replays/` are cwd-relative ("must start from
repo root"). One resolver replaces the scattered relative paths:

```python
def data_root() -> Path:
    """Where all runtime state lives.
    - Frozen (PyInstaller, sys.frozen): %LOCALAPPDATA%\\sm64_tracker
    - From source (dev/tests): the current working directory (unchanged)
    """
```

Derived helpers route through it: `db_path()` → `data_root()/data/tracker.db`,
`replay_scratch_dir()`, `replays_root()`, `replay_settings_path()`,
`instance_lock_path()`, `window_state_path()`. From source these resolve to
exactly today's locations, so **the dev workflow and the test suite see no
change**. Frozen, they all land under `%LOCALAPPDATA%`.

Touchpoints to migrate (enumerate exactly in the plan; ≈4): `main.py`
`DB_PATH`, replay config scratch/buffer dir, `replays/` save root,
`replay_settings.json` path. `paths.py` belongs in `core/` (server uses it),
not `desktop/`.

`test_paths.py`: frozen branch (monkeypatch `sys.frozen`/`sys._MEIPASS` and
`LOCALAPPDATA`) → `%LOCALAPPDATA%\sm64_tracker`; source branch → cwd-relative.

### 2. Programmatic server runner (`desktop/server_runner.py`, new)

Wraps `uvicorn.Server` (NOT `uvicorn.run`, which blocks and installs signal
handlers) so the shell can start it in a background daemon thread and stop it
deterministically:

- `start()` — build the app via the existing `main.build()`, construct
  `uvicorn.Config(app, host="127.0.0.1", port=8064, timeout_graceful_shutdown=3)`,
  run `Server.run()` in a daemon thread. uvicorn skips signal-handler install
  off the main thread (we don't want them — the GUI owns shutdown).
- `wait_until_ready(timeout)` — poll `GET /health` so the window doesn't open
  on a dead URL.
- `stop()` — set `server.should_exit = True`, join with a deadline; the
  existing bounded replay teardown + force-exit watchdog still apply inside
  the app's lifespan.

`timeout_graceful_shutdown=3` is preserved (the load-bearing CTRL+C fix — see
`main.run()` docstring); the GUI just drives `should_exit` instead of CTRL+C.

### 3. Single-instance + takeover (`desktop/single_instance.py`, new)

On launch, BEFORE starting the server:

1. Detect a running instance: try the msvcrt lock at `instance_lock_path()`
   AND probe `GET http://127.0.0.1:8064/health`.
2. If none → proceed to start.
3. If one is found → native dialog (pywebview/ctypes `MessageBox`):
   > **sm64_tracker is already running.**
   > [ Use this window (close the other) ] [ Cancel ]
   - **Use this window** → `POST /api/admin/shutdown` to the running
     instance; poll until the port + lock free (timeout → force-kill by PID
     read from a pidfile written at startup); then start here.
   - **Cancel** → exit this launcher; the other keeps running.

A pidfile (`%LOCALAPPDATA%\sm64_tracker\server.pid`) is written on start and
removed on clean stop, giving the force-kill fallback a target. The server's
existing broadcast-only lock stays as defense-in-depth, but the GUI flow
turns the silent degraded mode into an explicit choice.

`test_single_instance.py`: detection true/false against a fake health probe;
takeover issues shutdown then waits; force-kill path triggers on timeout.

### 4. Admin shutdown + restart endpoints (`server/app.py`)

Two localhost-only POSTs (the app only ever binds `127.0.0.1`), each
dispatched on a daemon thread so the request returns before the action runs
(joining the server thread from inside the handler would deadlock graceful
shutdown):

- `POST /api/admin/shutdown` → runs `app.state.request_shutdown` if set, else
  raises SIGINT (the terminal graceful path). The desktop sets
  `request_shutdown` to a FULL quit (stop server + close window + stop tray)
  — not just `should_exit` — so "close the other instance" actually closes
  it. Returns `{"shutting_down": true}`.
- `POST /api/admin/restart` → runs `app.state.request_restart` if set, else
  the fallback (`spawn_replacement()` + SIGINT). Returns `{"restarting": true}`.

### 4b. Restart = full process relaunch (`core/relaunch.py`, new)

An in-process server restart will NOT pick up edited backend modules (CPython
caches imports), so "Restart server" RELAUNCHES the process.
`core/relaunch.py` holds the primitives: `server_alive()` (/health probe),
`wait_port_free()`, and `spawn_replacement()` (re-launch this exact process
via `sys.orig_argv`, tagged with the `SM64_RESTART=1` env var).

- **GUI:** the button POSTs `/api/admin/restart`; the desktop's
  `request_restart` calls `spawn_replacement()` then the full quit. The fresh
  exe sees `SM64_RESTART`, `wait_port_free()`s for the old to exit (skipping
  the takeover dialog), then builds + opens its window.
- **Browser/terminal:** the fallback `spawn_replacement()` + SIGINT relaunches
  `python -m sm64_events.main`; `run()` sees `SM64_RESTART` and
  `wait_port_free()`s before building, so the lock hands off cleanly.
- The UI needs no special handling: the store's WebSocket auto-reconnects
  (2 s) and refetches `/api/session` on reconnect, so the page seamlessly
  rejoins the new server. The button shows a transient "Restarting…" state.

**Required main.py refactor (root cause):** today `app = build()` runs at
module import, which acquires the instance lock as a side effect. The desktop
must call `build()` AFTER the single-instance takeover, so importing it must
be side-effect-free. main.py changes to a lazy module-level `app` (via module
`__getattr__`) so `from sm64_events.main import build` no longer builds/locks;
only `uvicorn sm64_events.main:app` (attribute access) or `run()` builds. This
also fixes a latent "import acquires the lock" footgun.

### 5. Window (`desktop/window.py`, new)

- `webview.create_window(title, url="http://127.0.0.1:8064/",
  resizable=True, min_size=(360, 500), width/height/x/y from saved geometry)`.
- No max size — fills landscape or portrait. WebView2 backend on Win11.
- Geometry persistence: subscribe to resize/move events, debounce-write
  `window.json` (`{w, h, x, y, maximized}`) to `window_state_path()`; restore
  on next launch. Missing/corrupt file → sensible default size.
- `on_closing` → trigger `server_runner.stop()` then allow close.

### 6. System tray (`desktop/tray.py`, new)

`pystray` icon (image built from the Ukiki `.ico` via Pillow, already a dep):
menu = Show/Hide window, Quit. Runs on its own thread; Quit drives the same
clean shutdown as window close. Tray is GUI-shell-only and has no browser
equivalent — it never touches `ui/`.

### 7. Entry point (`desktop/__main__.py`, new)

Composition for the GUI, runnable from source as
`uv run python -m sm64_events.desktop` (fast iteration, no packaging) and as
the frozen exe's entry:

```
single_instance.ensure_single()      # dialog/takeover or exit
server_runner.start(); wait_until_ready()
tray.start()
window.open()                          # blocks on main thread (pywebview)
# on window/tray quit: server_runner.stop(); tray.stop()
```

### 8. ffmpeg + native-dep bundling (`tools/build_exe.py` + `sm64_tracker.spec`)

- **ffmpeg.exe bundled** as a binary; ffmpeg discovery (currently
  `shutil.which("ffmpeg")` in `main.build()`) gains a bundled-path check
  (`sys._MEIPASS/ffmpeg.exe` when frozen, preferred over PATH). Replay still
  works without it (in-process PyAV fallback), but releases ship with it.
  ffmpeg is a separate binary in the bundle — credit its source/license.
- **The `ui/` folder is added as data** — it is read from disk at runtime
  (`server/app.py` `_UI_INDEX = Path(__file__)…/ui/index.html`), not imported,
  so it must be collected; the `__file__`-relative path resolves into
  `sys._MEIPASS` when the package layout is preserved in the bundle.
- **Risky native deps** get explicit collection / hidden imports: PyAV (`av`),
  `windows-capture`, `pyaudiowpatch`, `pycaw`/`comtypes`, `pymem`, `numpy`.
  `comtypes` generates cache modules at runtime → point its gen dir at a
  writable temp when frozen (known PyInstaller gotcha).
- **Icon:** `assets/ukiki.ico` (multi-res) on the exe + window + taskbar +
  tray. Original/stylized Ukiki art (not ripped Nintendo assets); a simple
  placeholder if needed this pass.
- **One command:** `uv run python tools/build_exe.py` (wraps PyInstaller with
  the committed spec, stamps version) → `dist/sm64_tracker.exe`. Optional
  `build.ps1` one-liner.

This is the highest-risk slice: every native dep must load from the frozen
onefile, verified by launching the exe and exercising the **full replay
path**, not just startup.

### 9. README restructure + going-forward parity rule

- README becomes a front door: what it is → **End-user quick start**
  (download exe, double-click, one-time SmartScreen "More info → Run anyway",
  "have PJ64 1.6 + Usamune v1.93u **US** running") → **Dev setup** (the
  existing `uv` flow) → **Build the exe** → feature overview + links.
- The exhaustive event-schema and HTTP-API tables (currently ~95% of the
  README) move to `docs/api.md`; README links to it. One fact, one place.
- **Assumptions documented explicitly:** Windows 11 (Win10 + WebView2 link)
  64-bit; PJ64 1.6; Usamune v1.93u **US only** (JP untested/unsupported);
  PJ64 windowed for replay.
- **Parity rule (added to `CLAUDE.md`):** features land in `ui/` + server so
  they appear in BOTH the browser and the window; the desktop shell adds only
  native chrome (window/tray/icon) and must never fork the UI. New module-map
  rows for the `desktop/` package, `core/paths.py`, and the admin endpoint.

## Data flow

Double-click exe → `single_instance.ensure_single()` (no other instance) →
`server_runner.start()` builds the app, paths resolve under `%LOCALAPPDATA%`,
uvicorn serves `:8064` on a daemon thread → `wait_until_ready` passes →
tray icon appears → pywebview window opens on `:8064/` showing the existing
UI → user resizes to full portrait, practices, replay records (bundled
ffmpeg) → close window → `server_runner.stop()` flips `should_exit` → bounded
replay/db teardown → process exits, lock + pidfile released. A second
double-click while running → dialog → "Use this window" → POST shutdown to
the first → wait for port free → start here.

## Testing

- `test_paths.py` (new) — frozen vs source resolution.
- `test_server_runner.py` (new) — start/ready/stop lifecycle against the real
  app on an ephemeral check (or a stub app).
- `test_single_instance.py` (new) — detect, graceful takeover, force-kill
  fallback against a fake server/health probe.
- `test_app.py` — `POST /api/admin/shutdown` sets the flag (localhost-only).
- Existing suite must stay green (path refactor is invisible from source).
- **Live checks with the human** (the established pattern — window/tray/frozen
  paths aren't unit-testable): launch the built exe; window resizes to full
  portrait and landscape and remembers geometry; replay records and plays;
  single-instance dialog + takeover works; data lands in `%LOCALAPPDATA%`;
  SmartScreen "Run anyway" path documented works; browser at `:8064` still
  works alongside the window.

## Files

New: `core/paths.py` · `core/relaunch.py` · `desktop/__init__.py` ·
`desktop/__main__.py` · `desktop/server_runner.py` · `desktop/single_instance.py`
· `desktop/window.py` · `desktop/tray.py` · `tools/build_exe.py` ·
`tools/rthook_comtypes.py` · `gui_entry.py` · `assets/ukiki.ico` · tests for
the above.
Changed: `main.py` (paths + bundled-ffmpeg + lazy `app` + restart wait in
`run()`) · `server/app.py` (`/api/admin/shutdown` + `/api/admin/restart` +
pidfile) · replay config (paths) · `ui/components/header.js` (Restart server
button) · `pyproject.toml` (`pywebview`, `pystray` runtime; `pyinstaller` dev)
· `README.md` (restructure) · `docs/api.md` (new home for the API reference) ·
`CLAUDE.md` (module-map rows + parity rule).

## Phasing (one effort, natural order)

1. **Path refactor** (`core/paths.py`) + tests — unblocks everything,
   invisible from source.
2. **Desktop shell** (server_runner → window → single-instance → tray),
   runnable from source (`uv run python -m sm64_events.desktop`) for
   seconds-fast iteration before any packaging.
3. **Packaging** (spec + build script + ffmpeg + icon) — the build-and-test
   loop against the frozen exe.
4. **README/docs restructure** + `CLAUDE.md` parity rule.

## Risks / coordination

- **Frozen native deps are the main risk.** PyAV, windows-capture,
  pyaudiowpatch, pycaw/comtypes, pymem must all load from the onefile bundle.
  Mitigation: iterative build-and-test, `--collect-all` for the stubborn
  ones, comtypes-gen-dir fix, and exercising the full replay path in the
  frozen exe — not just startup.
- **Unsigned exe friction.** SmartScreen "unrecognized app" + occasional AV
  false-positives on PyInstaller onefile. Can't fully remove without a
  signing cert (~$200+/yr). Documented in the README; signing left open.
- **WebView2 on Windows 10.** Preinstalled on Win11; Win10 users may need the
  Evergreen runtime. README carries the one-line installer link.
- **Shared checkout, concurrent sessions.** Per `CLAUDE.md`, `main.py` is a
  "never edit in two branches at once" contract and this touches it (paths +
  ffmpeg discovery + the lazy-`app` refactor). The implementation session must
  re-check the branch and merge cleanly before editing it.
- **main.py lazy-`app` refactor.** Making `app` lazy (module `__getattr__`) is
  a structural change to the composition root. It must keep ALL existing
  launch modes working: `uvicorn sm64_events.main:app` (attribute access
  builds), `python -m sm64_events.main` (`run()` builds), and `test_composition`
  (reload no longer eager-builds; explicit `build()` still works). Verified by
  the composition suite + a live CTRL+C check on the canonical launch.
- **Restart relaunch is launch-mode-sensitive.** It relaunches `sys.orig_argv`,
  so the canonical launches (`python -m sm64_events.desktop`, the frozen exe,
  `python -m sm64_events.main`) are fully supported; an exotic launch (bare
  `uvicorn` CLI) relaunches without the `SM64_RESTART` port-free wait and may
  briefly race the lock (degrading to broadcast-only until the next restart).
  Documented, not fixed.
- **onefile startup latency.** Self-extract adds a few seconds to first
  launch; acceptable, documented. (onedir/zip remains a fallback if it ever
  becomes a problem.)
