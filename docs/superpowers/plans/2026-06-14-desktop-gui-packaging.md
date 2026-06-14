# Desktop GUI + One-Click Portable Build — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the existing browser tracker as a single double-clickable Windows `.exe` — a native resizable `pywebview` window over the unchanged FastAPI/uvicorn server, packaged with PyInstaller, storing data under `%LOCALAPPDATA%` when frozen, with a user-facing single-instance takeover dialog and a one-click "Restart server" button.

**Architecture:** A thin, additive `desktop/` shell calls `main.build()` (now import-side-effect-free) under uvicorn in a daemon thread, waits for `/health`, then opens a webview window at `127.0.0.1:8064` — the same UI the browser shows (automatic parity). `core/paths.py` resolves runtime paths (`Path(".")` from source = identical to today; `%LOCALAPPDATA%\sm64_tracker` frozen). Localhost `/api/admin/shutdown` + `/api/admin/restart` endpoints (+ a pidfile + `core/relaunch.py`) power the takeover dialog and a full-process restart that picks up edited backend code.

**Tech Stack:** Python 3.12 + uv, FastAPI/uvicorn (unchanged), `pywebview` (Edge WebView2, preinstalled on Win11), `pystray` (tray), `pyinstaller` (build), bundled `ffmpeg.exe`.

**Coordination (shared checkout):** A concurrent session has uncommitted edits to `CLAUDE.md`, `README.md`, `memory/addresses.py`, `detectors/anchors.py`, `tracking/segments.py` and their tests. This plan touches `main.py` (a "never edit in two branches at once" contract per CLAUDE.md — currently clean) and `README.md`/`CLAUDE.md`. **Before each task that edits a shared file, re-check the branch, ensure foreign work has landed, and rebase/merge cleanly.** Stage explicit paths on every commit (`git add -A` is hook-blocked here).

---

## File structure

| File | Responsibility |
|---|---|
| `src/sm64_events/core/paths.py` (new) | THE runtime-path resolver (frozen vs source) + bundled-ffmpeg lookup |
| `src/sm64_events/core/relaunch.py` (new) | full-process relaunch primitives (`server_alive`, `wait_port_free`, `spawn_replacement`) |
| `src/sm64_events/main.py` (modify) | lazy `app` (no build/lock at import); db/lock paths via `core.paths`; bundled ffmpeg; `SM64_RESTART` wait in `run()` |
| `src/sm64_events/replay/config.py` (modify) | `ReplayConfig` path defaults via `core.paths` factories |
| `src/sm64_events/server/app.py` (modify) | pidfile in lifespan; `POST /api/admin/shutdown` + `/api/admin/restart` |
| `src/sm64_events/desktop/__init__.py` (new) | package marker |
| `src/sm64_events/desktop/server_runner.py` (new) | run uvicorn in a thread; ready-probe; deterministic stop |
| `src/sm64_events/desktop/single_instance.py` (new) | detect running instance + graceful/force takeover |
| `src/sm64_events/desktop/window.py` (new) | pywebview window + geometry persistence |
| `src/sm64_events/desktop/tray.py` (new) | system tray icon (pystray) |
| `src/sm64_events/desktop/app.py` (new) | desktop composition root: `main()` + restart/quit wiring |
| `src/sm64_events/desktop/__main__.py` (new) | `python -m sm64_events.desktop` → `app.main()` |
| `gui_entry.py` (new, repo root) | PyInstaller entry point → `app.main()` |
| `src/sm64_events/ui/components/header.js` (modify) | "Restart server" button |
| `tools/rthook_comtypes.py` (new) | runtime hook: writable comtypes gen dir when frozen |
| `tools/make_placeholder_icon.py` (new) | generate `assets/ukiki.ico` placeholder |
| `tools/build_exe.py` (new) | one-command PyInstaller build |
| `assets/ukiki.ico` (new) | app/window/tray/exe icon |
| `tests/test_paths.py` (new) | path resolution + bundled ffmpeg |
| `tests/test_relaunch.py` (new) | relaunch primitives |
| `tests/test_server_runner.py` (new) | runner lifecycle |
| `tests/test_single_instance.py` (new) | detect + takeover logic |
| `tests/test_app.py` (modify) | admin shutdown + restart endpoints |
| `tests/test_composition.py` (modify) | lazy `app` (no eager build) |
| `README.md` (rewrite) · `docs/api.md` (new) · `CLAUDE.md` (modify) | docs front door + reference + parity rule |
| `.gitignore` (modify) | `dist/`, `build/`, `*.spec`, `server.pid`, `window.json` |

---

## Task 1: `core/paths.py` — runtime path resolver

**Files:**
- Create: `src/sm64_events/core/paths.py`
- Test: `tests/test_paths.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_paths.py
"""Runtime path resolution: cwd-relative from source (identical to the
historical layout), %LOCALAPPDATA% when frozen into an exe."""
import sys
from pathlib import Path

from sm64_events.core import paths


def test_source_paths_match_historical_relative_layout(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.db_path() == Path("data") / "tracker.db"
    assert paths.instance_lock_path() == Path("data") / "tracker.lock"
    assert paths.replay_scratch_dir() == Path("data") / "replay_buffer"
    assert paths.replays_root() == Path("replays")
    assert paths.replay_settings_path() == Path("data") / "replay_settings.json"


def test_frozen_paths_live_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "sm64_tracker"
    assert paths.data_root() == root
    assert paths.db_path() == root / "data" / "tracker.db"
    assert paths.instance_lock_path() == root / "data" / "tracker.lock"
    assert paths.replays_root() == root / "replays"
    assert paths.pidfile_path() == root / "server.pid"
    assert paths.window_state_path() == root / "window.json"


def test_bundled_ffmpeg_none_from_source(monkeypatch):
    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    assert paths.bundled_ffmpeg() is None


def test_bundled_ffmpeg_found_when_frozen(monkeypatch, tmp_path):
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    (tmp_path / "ffmpeg.exe").write_text("x")
    assert paths.bundled_ffmpeg() == str(tmp_path / "ffmpeg.exe")
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_paths.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.paths'`.

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/core/paths.py
"""THE single source of truth for where runtime state lives.

From source (dev/tests) every path is `Path(".")`-relative — byte-identical
to the historical layout (the project has always "run from the repo root").
Frozen into a PyInstaller exe (``sys.frozen``) everything moves under
``%LOCALAPPDATA%\\sm64_tracker`` so a double-clicked exe needs no working
directory and a new release can replace the exe while the user keeps their
history / PBs / saved replays.

Every path the server or desktop shell persists to MUST come from here."""
import os
import sys
from pathlib import Path

APP_DIR_NAME = "sm64_tracker"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def data_root() -> Path:
    """Base directory for all persisted state.

    Source: ``Path(".")`` — joins collapse the leading dot, so
    ``data_root()/"data"/"tracker.db" == Path("data")/"tracker.db"`` exactly
    as before. Frozen: ``%LOCALAPPDATA%\\sm64_tracker``.
    """
    if is_frozen():
        base = os.environ.get("LOCALAPPDATA") or str(
            Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    return Path(".")


def db_path() -> Path:
    return data_root() / "data" / "tracker.db"


def instance_lock_path() -> Path:
    # Matches the historical `DB_PATH.with_suffix(".lock")` → data/tracker.lock
    return db_path().with_suffix(".lock")


def replay_scratch_dir() -> Path:
    return data_root() / "data" / "replay_buffer"


def replays_root() -> Path:
    return data_root() / "replays"


def replay_settings_path() -> Path:
    return data_root() / "data" / "replay_settings.json"


def pidfile_path() -> Path:
    return data_root() / "server.pid"


def window_state_path() -> Path:
    return data_root() / "window.json"


def bundled_ffmpeg() -> str | None:
    """Absolute path to the ffmpeg.exe bundled beside a frozen exe, else None.
    PyInstaller unpacks --add-binary files into ``sys._MEIPASS``."""
    if is_frozen():
        cand = Path(getattr(sys, "_MEIPASS", "")) / "ffmpeg.exe"
        if cand.exists():
            return str(cand)
    return None
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_paths.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/paths.py tests/test_paths.py
git commit -m "feat(paths): single source of truth for runtime data locations

Source stays cwd-relative (identical to today); frozen exes store under
%LOCALAPPDATA%\\sm64_tracker so a portable exe needs no working dir and new
releases keep user history."
```

---

## Task 2: `main.py` — lazy `app` + paths via `core.paths`

The desktop shell must call `build()` AFTER the single-instance takeover, but today `app = build()` runs at *import* (acquiring the instance lock as a side effect). Make `app` lazy so importing `build` has no side effects, and route db/lock paths through `core.paths`.

**Files:**
- Modify: `src/sm64_events/main.py` (imports, `DB_PATH`, `build()` body, the `app = build()` line, `run()`)
- Test: `tests/test_composition.py` (add lazy-app tests; existing tests stay green)

- [ ] **Step 1: Confirm nothing depends on eager `main.app` in code**

Run: `uv run python -c "import subprocess,sys; sys.exit(0)"` then search:
Use Grep for `main import app` and `main\.app` across `src/` and `tests/`.
Expected: no hits (only `sm64_events.main:app` appears in docs as the uvicorn CLI string — that keeps working via `__getattr__`). If a code hit exists, switch it to `get_app()`.

- [ ] **Step 2: Add the failing lazy-app tests** (append to `tests/test_composition.py`)

```python
def test_app_is_lazy_not_built_at_import():
    src = (Path(sm64_events.__file__).parent / "main.py").read_text(
        encoding="utf-8")
    # No eager module-level build (which would acquire the instance lock);
    # the app is provided lazily via module __getattr__.
    assert "\napp = build()" not in src
    assert "__getattr__" in src


def test_get_app_builds_once(monkeypatch):
    import importlib

    import sm64_events.main as main_mod
    importlib.reload(main_mod)

    calls = []

    def fake_build():
        from fastapi import FastAPI
        calls.append(True)
        return FastAPI()

    monkeypatch.setattr(main_mod, "build", fake_build)
    a1 = main_mod.get_app()
    a2 = main_mod.get_app()
    assert a1 is a2
    assert calls == [True]
```

- [ ] **Step 3: Run to verify failure**

Run: `uv run pytest tests/test_composition.py -k "lazy or get_app" -q`
Expected: FAIL — `app = build()` still present / `get_app` undefined.

- [ ] **Step 4: Edit imports**

In `src/sm64_events/main.py`, after `from pathlib import Path`, add:
```python
from sm64_events.core.paths import bundled_ffmpeg, db_path, instance_lock_path
```

- [ ] **Step 5: Replace the `DB_PATH` constant + the `build()` path lines**

Remove the module constant:
```python
DB_PATH = Path("data") / "tracker.db"
```
In `build()`, replace:
```python
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_instance_lock(DB_PATH.with_suffix(".lock"))
```
with:
```python
    db_file = db_path()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    lock = acquire_instance_lock(instance_lock_path())
```
and replace `db = Database(DB_PATH)` with `db = Database(db_file)`. Update the two log lines that referenced `DB_PATH` to use `db_file`.

- [ ] **Step 6: Make `app` lazy**

Replace the line:
```python
app = build()
```
with:
```python
_app = None


def get_app():
    """Build the app once, lazily. Importing this module must NOT build it —
    build() acquires the instance lock, and the desktop shell needs to call
    build() AFTER its single-instance takeover. Only serving the app builds
    it: `uvicorn sm64_events.main:app` (attribute access via __getattr__) or
    run()."""
    global _app
    if _app is None:
        _app = build()
    return _app


def __getattr__(name):
    if name == "app":
        return get_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
```

- [ ] **Step 7: Point `run()` at `get_app()`**

In `run()`, change `uvicorn.run(app, ...)` to:
```python
    uvicorn.run(get_app(), host="127.0.0.1", port=8064,
                timeout_graceful_shutdown=3)
```

- [ ] **Step 8: Run the affected suites**

Run: `uv run pytest tests/test_composition.py tests/test_storage.py -q`
Expected: PASS (lazy-app tests pass; existing detector-order/replay-wiring tests still pass; paths resolve to the same `data/tracker.db` from source).

- [ ] **Step 9: Commit**

```bash
git add src/sm64_events/main.py tests/test_composition.py
git commit -m "refactor(main): lazy app + paths via core.paths

Importing build() no longer builds/locks (the desktop shell builds AFTER its
single-instance takeover); uvicorn main:app and run() still build via
get_app()/__getattr__. DB+lock paths route through core.paths (identical from
source)."
```

---

## Task 3: Route `ReplayConfig` path defaults through `core/paths`

**Files:**
- Modify: `src/sm64_events/replay/config.py:26-32`
- Test: `tests/test_replay_config.py` (existing — must stay green)

- [ ] **Step 1: Add the import**

At the top of `src/sm64_events/replay/config.py` (after `from pathlib import Path`):
```python
from sm64_events.core.paths import (replay_scratch_dir, replay_settings_path,
                                     replays_root)
```

- [ ] **Step 2: Switch the three path fields to factories**

Replace:
```python
    save_root: Path = field(default=Path("replays"))
    scratch_dir: Path = field(default=Path("data") / "replay_buffer")
```
with:
```python
    save_root: Path = field(default_factory=replays_root)
    scratch_dir: Path = field(default_factory=replay_scratch_dir)
```
and replace:
```python
    settings_path: Path = field(default=Path("data") / "replay_settings.json")
```
with:
```python
    settings_path: Path = field(default_factory=replay_settings_path)
```

- [ ] **Step 3: Run the replay config + service suites**

Run: `uv run pytest tests/test_replay_config.py tests/test_replay_service.py -q`
Expected: PASS (`replays_root()` from source == `Path("replays")`; `replay_scratch_dir()` == `Path("data")/"replay_buffer"`).

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/replay/config.py
git commit -m "refactor(replay): ReplayConfig paths via core.paths factories

Frozen builds keep the replay buffer + saved replays under %LOCALAPPDATA%;
from source the defaults are byte-identical to before."
```

---

## Task 4: Prefer bundled ffmpeg in `main.build()`

**Files:**
- Modify: `src/sm64_events/main.py` (the `_shutil.which("ffmpeg")` line, ~line 85)
- Test: covered by `tests/test_paths.py::test_bundled_ffmpeg_*` (Task 1) + the composition test.

- [ ] **Step 1: Use the bundled path first**

In `src/sm64_events/main.py`, replace:
```python
        _ffmpeg = _shutil.which("ffmpeg")
```
with:
```python
        _ffmpeg = bundled_ffmpeg() or _shutil.which("ffmpeg")
```
(`bundled_ffmpeg` was imported in Task 2.)

- [ ] **Step 2: Run the composition suite**

Run: `uv run pytest tests/test_composition.py -q`
Expected: PASS (from source `bundled_ffmpeg()` is None → falls back to PATH, unchanged behavior).

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/main.py
git commit -m "feat(replay): prefer the bundled ffmpeg.exe when frozen

Packaged exes ship ffmpeg beside them (_MEIPASS); from source nothing
changes (which('ffmpeg'))."
```

---

## Task 5: `core/relaunch.py` + restart wait in `main.run()`

**Files:**
- Create: `src/sm64_events/core/relaunch.py`
- Modify: `src/sm64_events/main.py` (`run()`)
- Test: `tests/test_relaunch.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_relaunch.py
"""Full-process relaunch primitives for the one-click restart."""
import subprocess
import sys

from sm64_events.core import relaunch


def test_wait_port_free_true_when_alive_goes_false():
    seq = iter([True, True, False])
    assert relaunch.wait_port_free(
        timeout_s=1.0, poll_s=0.01, alive=lambda: next(seq, False)) is True


def test_wait_port_free_times_out_when_always_alive():
    assert relaunch.wait_port_free(
        timeout_s=0.05, poll_s=0.01, alive=lambda: True) is False


def test_spawn_replacement_relaunches_orig_argv(monkeypatch):
    captured = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: captured.update(argv=argv,
                                                           env=kw.get("env")))
    monkeypatch.setattr(sys, "frozen", False, raising=False)
    monkeypatch.setattr(sys, "orig_argv",
                        [sys.executable, "-m", "sm64_events.desktop"])
    relaunch.spawn_replacement()
    assert captured["argv"] == [sys.executable, "-m", "sm64_events.desktop"]
    assert captured["env"]["SM64_RESTART"] == "1"


def test_spawn_replacement_frozen_uses_executable(monkeypatch):
    captured = {}
    monkeypatch.setattr(subprocess, "Popen",
                        lambda argv, **kw: captured.update(argv=argv))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\app\sm64_tracker.exe")
    monkeypatch.setattr(sys, "orig_argv", [r"C:\app\sm64_tracker.exe", "--x"])
    relaunch.spawn_replacement()
    assert captured["argv"] == [r"C:\app\sm64_tracker.exe", "--x"]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_relaunch.py -q`
Expected: FAIL — `ModuleNotFoundError: ... core.relaunch`.

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/core/relaunch.py
"""Full-process relaunch primitives for the one-click "Restart server".

An in-process restart can't reload edited backend modules (CPython caches
imports), so restart RELAUNCHES this exact process. server_alive /
wait_port_free let the fresh process wait for the old one to release :8064
before it binds; spawn_replacement re-launches sys.orig_argv tagged with
SM64_RESTART=1 so the fresh process knows to wait and skip the takeover
dialog."""
import os
import subprocess
import sys
import time
import urllib.request
from collections.abc import Callable

HOST = "127.0.0.1"
PORT = 8064


def server_alive(timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(
                f"http://{HOST}:{PORT}/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def wait_port_free(timeout_s: float = 10.0, poll_s: float = 0.25,
                   alive: Callable[[], bool] = server_alive) -> bool:
    """Block until the server is gone (port free), bounded by timeout_s."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not alive():
            return True
        time.sleep(poll_s)
    return not alive()


def spawn_replacement() -> None:
    """Launch a fresh copy of this exact process, tagged for restart."""
    if getattr(sys, "frozen", False):
        argv = [sys.executable, *sys.orig_argv[1:]]
    else:
        argv = list(sys.orig_argv)
    subprocess.Popen(argv, env={**os.environ, "SM64_RESTART": "1"},
                     close_fds=False)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_relaunch.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Add the restart wait to `main.run()`**

In `src/sm64_events/main.py`, change `run()` so the body becomes:
```python
    import os
    if os.environ.pop("SM64_RESTART", None):
        # A restart relaunch: the old process is exiting — wait for it to
        # free :8064 so build()'s instance-lock acquisition hands off cleanly.
        from sm64_events.core.relaunch import wait_port_free
        wait_port_free()
    import uvicorn
    uvicorn.run(get_app(), host="127.0.0.1", port=8064,
                timeout_graceful_shutdown=3)
```

- [ ] **Step 6: Run the composition suite**

Run: `uv run pytest tests/test_composition.py -q`
Expected: PASS (no `SM64_RESTART` in the env → the wait is skipped; behavior unchanged).

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/core/relaunch.py tests/test_relaunch.py src/sm64_events/main.py
git commit -m "feat(relaunch): full-process restart primitives + run() handoff

server_alive/wait_port_free/spawn_replacement; a relaunch sets SM64_RESTART
so the fresh process waits for the old to free :8064 before binding."
```

---

## Task 6: Pidfile + `POST /api/admin/shutdown` + `/api/admin/restart`

**Files:**
- Modify: `src/sm64_events/server/app.py` (lifespan + helpers + two routes)
- Test: `tests/test_app.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_app.py`; `_wait_for` already exists in this file)

```python
def test_admin_shutdown_invokes_state_callback():
    with make_client() as client:
        called: list[bool] = []
        client.app.state.request_shutdown = lambda: called.append(True)
        resp = client.post("/api/admin/shutdown")
        assert resp.status_code == 200
        assert resp.json() == {"shutting_down": True}
        _wait_for(called)
        assert called == [True]


def test_admin_shutdown_fallback_raises_sigint(monkeypatch):
    raised: list[int] = []
    monkeypatch.setattr("sm64_events.server.app.signal.raise_signal",
                        raised.append)
    with make_client() as client:
        if hasattr(client.app.state, "request_shutdown"):
            delattr(client.app.state, "request_shutdown")
        assert client.post("/api/admin/shutdown").status_code == 200
        _wait_for(raised)
        assert raised == [signal.SIGINT]


def test_admin_restart_invokes_state_callback():
    with make_client() as client:
        called: list[bool] = []
        client.app.state.request_restart = lambda: called.append(True)
        resp = client.post("/api/admin/restart")
        assert resp.status_code == 200
        assert resp.json() == {"restarting": True}
        _wait_for(called)
        assert called == [True]


def test_admin_restart_fallback_relaunches(monkeypatch):
    spawned: list[bool] = []
    monkeypatch.setattr("sm64_events.server.app.spawn_replacement",
                        lambda: spawned.append(True))
    monkeypatch.setattr("sm64_events.server.app.signal.raise_signal",
                        lambda s: None)
    with make_client() as client:
        if hasattr(client.app.state, "request_restart"):
            delattr(client.app.state, "request_restart")
        assert client.post("/api/admin/restart").status_code == 200
        _wait_for(spawned)
        assert spawned == [True]
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_app.py -k "admin" -q`
Expected: FAIL — 404 (routes do not exist yet).

- [ ] **Step 3: Add imports + module-level helpers**

In `src/sm64_events/server/app.py`, add near the top imports:
```python
from sm64_events.core.paths import pidfile_path
from sm64_events.core.relaunch import spawn_replacement
```
After the imports (module level, before `create_app`), add:
```python
def _dispatch(fn) -> None:
    """Run a shutdown/restart action OFF the request thread: blocking inside
    the handler (joining the server thread) would deadlock graceful
    shutdown."""
    threading.Thread(target=fn, daemon=True).start()


def _fallback_shutdown() -> None:
    signal.raise_signal(signal.SIGINT)


def _fallback_restart() -> None:
    spawn_replacement()
    signal.raise_signal(signal.SIGINT)
```

- [ ] **Step 4: Write/remove the pidfile in the lifespan**

In `lifespan`, just after `install_force_exit_watchdog()`, add:
```python
        try:
            pf = pidfile_path()
            pf.parent.mkdir(parents=True, exist_ok=True)
            pf.write_text(str(os.getpid()))
        except Exception:
            log.warning("could not write pidfile", exc_info=True)
```
At the very end of `lifespan` (after the final `await task`), add:
```python
        with suppress(Exception):
            pidfile_path().unlink()
```

- [ ] **Step 5: Add the two endpoints**

In `create_app`, after `set_pause`, add:
```python
    @app.post("/api/admin/shutdown")
    def admin_shutdown():
        """Localhost-only graceful shutdown — the 'close the other instance'
        takeover path. The desktop sets app.state.request_shutdown to a FULL
        GUI quit; a terminal launch has none, so fall back to SIGINT."""
        _dispatch(getattr(app.state, "request_shutdown", None)
                  or _fallback_shutdown)
        return {"shutting_down": True}

    @app.post("/api/admin/restart")
    def admin_restart():
        """Localhost-only full-process relaunch (the 'Restart server'
        button) — picks up edited backend code. The desktop sets
        app.state.request_restart; a terminal launch falls back to
        spawn_replacement() + SIGINT (run() waits for the port via
        SM64_RESTART)."""
        _dispatch(getattr(app.state, "request_restart", None)
                  or _fallback_restart)
        return {"restarting": True}
```

- [ ] **Step 6: Run to verify pass**

Run: `uv run pytest tests/test_app.py -q`
Expected: PASS (all, including the four new ones).

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/server/app.py tests/test_app.py
git commit -m "feat(server): pidfile + /api/admin/shutdown + /api/admin/restart

Localhost-only; dispatched off-thread to avoid deadlocking graceful
shutdown. Drives the GUI takeover dialog and the one-click restart; terminal
launches fall back to SIGINT / relaunch."
```

---

## Task 7: Add desktop + build dependencies

**Files:**
- Modify: `pyproject.toml`, `.gitignore`

- [ ] **Step 1: Add runtime + dev deps**

In `pyproject.toml`, add to `dependencies` (after `"pillow>=12.2.0",`):
```toml
    "pywebview>=5.0",
    "pystray>=0.19",
```
and change the dev group to:
```toml
[dependency-groups]
dev = ["pytest>=8.0", "httpx>=0.27", "pyinstaller>=6.0"]
```

- [ ] **Step 2: Sync**

Run: `uv sync`
Expected: resolves and installs `pywebview`, `pystray`, `pyinstaller` (+ pywebview's WebView2 binding deps on Windows).

- [ ] **Step 3: Ignore build + runtime-state artifacts**

Append to `.gitignore`:
```
# desktop build + runtime state
/dist/
/build/
*.spec
/server.pid
/window.json
```

- [ ] **Step 4: Smoke-check imports**

Run: `uv run python -c "import webview, pystray; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .gitignore
git commit -m "build: add pywebview + pystray (runtime) and pyinstaller (dev)"
```

---

## Task 8: `desktop/server_runner.py`

**Files:**
- Create: `src/sm64_events/desktop/__init__.py`, `src/sm64_events/desktop/server_runner.py`
- Test: `tests/test_server_runner.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_server_runner.py
"""ServerRunner runs the app under uvicorn in a daemon thread and stops it
deterministically (the GUI owns shutdown, not CTRL+C)."""
import socket

from fastapi import FastAPI

from sm64_events.desktop.server_runner import ServerRunner


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_runner_wires_request_shutdown_on_app_state():
    app = FastAPI()
    ServerRunner(app, port=_free_port())
    assert callable(app.state.request_shutdown)


def test_runner_starts_serves_and_stops():
    app = FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    runner = ServerRunner(app, port=_free_port())
    runner.start()
    try:
        assert runner.wait_until_ready(timeout_s=10) is True
    finally:
        runner.stop()
    assert runner._thread is not None and not runner._thread.is_alive()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_server_runner.py -q`
Expected: FAIL — `ModuleNotFoundError: ... desktop.server_runner`.

- [ ] **Step 3: Write the package marker + implementation**

```python
# src/sm64_events/desktop/__init__.py
"""Desktop shell: wraps the existing server/UI in a native window. Additive
— the server and UI code paths are unchanged (browser↔GUI parity)."""
```

```python
# src/sm64_events/desktop/server_runner.py
"""Run the FastAPI app under uvicorn in a background daemon thread with a
deterministic start/stop. uvicorn skips signal-handler install off the main
thread, which is exactly what we want — the GUI drives shutdown via
``should_exit`` (window close / tray quit / admin endpoint), never CTRL+C.
``timeout_graceful_shutdown=3`` is preserved (the load-bearing CTRL+C fix)."""
import threading
import time
import urllib.request

import uvicorn


class ServerRunner:
    def __init__(self, app, host: str = "127.0.0.1", port: int = 8064):
        self.host = host
        self.port = port
        self._server = uvicorn.Server(uvicorn.Config(
            app, host=host, port=port, log_config=None,
            timeout_graceful_shutdown=3))
        self._thread: threading.Thread | None = None
        # Default: the admin shutdown endpoint stops the server. The desktop
        # composition overrides this with a FULL quit (app.py).
        app.state.request_shutdown = self.request_stop

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.run, name="uvicorn", daemon=True)
        self._thread.start()

    def wait_until_ready(self, timeout_s: float = 15.0) -> bool:
        deadline = time.monotonic() + timeout_s
        url = f"http://{self.host}:{self.port}/health"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1) as r:
                    if r.status == 200:
                        return True
            except Exception:
                pass
            time.sleep(0.1)
        return False

    def request_stop(self) -> None:
        self._server.should_exit = True

    def stop(self, timeout_s: float = 20.0) -> None:
        self.request_stop()
        if self._thread is not None:
            self._thread.join(timeout_s)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_server_runner.py -q`
Expected: PASS (2 passed; the lifecycle test starts and stops uvicorn in <2 s).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/desktop/__init__.py src/sm64_events/desktop/server_runner.py tests/test_server_runner.py
git commit -m "feat(desktop): ServerRunner runs uvicorn in a thread with clean stop"
```

---

## Task 9: `desktop/single_instance.py`

**Files:**
- Create: `src/sm64_events/desktop/single_instance.py`
- Test: `tests/test_single_instance.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_single_instance.py
"""Single-instance detection + takeover. The graceful/force logic is pure
and injectable; the HTTP probe / native dialog are exercised live."""
from sm64_events.desktop import single_instance as si


def test_instance_running_uses_injected_probe():
    assert si.instance_running(probe=lambda: True) is True
    assert si.instance_running(probe=lambda: False) is False


def test_take_over_graceful_when_port_frees():
    calls = {"shutdown": 0, "force": 0}
    freed = {"v": False}

    def shutdown():
        calls["shutdown"] += 1
        freed["v"] = True

    ok = si.take_over(
        shutdown=shutdown, port_free=lambda: freed["v"],
        force_kill=lambda: calls.__setitem__("force", calls["force"] + 1),
        timeout_s=1.0, poll_s=0.01)
    assert ok is True
    assert calls == {"shutdown": 1, "force": 0}


def test_take_over_force_kills_on_timeout():
    state = {"free": False, "force": 0}

    def force():
        state["force"] += 1
        state["free"] = True

    ok = si.take_over(shutdown=lambda: None, port_free=lambda: state["free"],
                      force_kill=force, timeout_s=0.05, poll_s=0.01)
    assert ok is True
    assert state["force"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_single_instance.py -q`
Expected: FAIL — `ModuleNotFoundError: ... desktop.single_instance`.

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/desktop/single_instance.py
"""Detect a running instance and offer a takeover. Detection rides the
server's /health (via core.relaunch.server_alive); takeover asks it to shut
down gracefully (POST /api/admin/shutdown) and force-kills by pidfile only on
timeout. The native dialog lives in app.py (verified live, not here)."""
import os
import signal
import time
import urllib.request
from collections.abc import Callable

from sm64_events.core.paths import pidfile_path
from sm64_events.core.relaunch import HOST, PORT, server_alive


def instance_running(probe: Callable[[], bool] = server_alive) -> bool:
    return probe()


def _post_shutdown(timeout: float = 2.0) -> None:
    req = urllib.request.Request(
        f"http://{HOST}:{PORT}/api/admin/shutdown", method="POST")
    try:
        urllib.request.urlopen(req, timeout=timeout).close()
    except Exception:
        pass  # the connection may drop as the server tears down — expected


def _force_kill_pidfile() -> None:
    try:
        pid = int(pidfile_path().read_text().strip())
    except Exception:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass


def take_over(*, shutdown: Callable[[], None] = _post_shutdown,
              port_free: Callable[[], bool] | None = None,
              force_kill: Callable[[], None] = _force_kill_pidfile,
              timeout_s: float = 8.0, poll_s: float = 0.25) -> bool:
    """Free the port for a fresh start: graceful shutdown first, force-kill on
    timeout. Returns True once the port is free."""
    if port_free is None:
        port_free = lambda: not server_alive()
    shutdown()
    if _wait(port_free, timeout_s, poll_s):
        return True
    force_kill()
    return _wait(port_free, timeout_s, poll_s)


def _wait(pred: Callable[[], bool], timeout_s: float, poll_s: float) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(poll_s)
    return pred()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_single_instance.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/desktop/single_instance.py tests/test_single_instance.py
git commit -m "feat(desktop): single-instance detection + graceful takeover"
```

---

## Task 10: `desktop/window.py` + `desktop/tray.py`

**Files:**
- Create: `src/sm64_events/desktop/window.py`, `src/sm64_events/desktop/tray.py`

> These wrap version-sensitive `pywebview` / `pystray` APIs and are verified live (Tasks 11–12), not unit-tested — matching this repo's live-gate culture. If the installed library version names an event/attribute differently, adjust at live-verify time.

- [ ] **Step 1: Write `window.py`**

```python
# src/sm64_events/desktop/window.py
"""pywebview window over the running server, with geometry persistence so a
full-portrait or maximized layout reopens where you left it.

The window is freely resizable with no max bound (the user fills a full
vertical monitor); the content is the same responsive UI the browser serves."""
import json
import logging

import webview

from sm64_events.core.paths import window_state_path

log = logging.getLogger("sm64.desktop")
URL = "http://127.0.0.1:8064/"
_DEFAULT = {"w": 480, "h": 900, "x": None, "y": None}


def _load_geometry() -> dict:
    try:
        saved = json.loads(window_state_path().read_text())
        return {**_DEFAULT, **saved}
    except Exception:
        return dict(_DEFAULT)


def _save_geometry(win) -> None:
    try:
        state = {"w": int(win.width), "h": int(win.height),
                 "x": int(win.x), "y": int(win.y)}
        p = window_state_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state))
    except Exception:
        log.debug("could not persist window geometry", exc_info=True)


def create(on_closed) -> "webview.Window":
    g = _load_geometry()
    win = webview.create_window(
        "sm64_tracker", url=URL,
        width=g["w"], height=g["h"], x=g["x"], y=g["y"],
        resizable=True, min_size=(360, 500))
    win.events.resized += lambda *a: _save_geometry(win)
    win.events.moved += lambda *a: _save_geometry(win)
    win.events.closed += lambda: on_closed()
    return win


def run() -> None:
    """Blocks on the main thread until the last window closes."""
    webview.start()
```

- [ ] **Step 2: Write `tray.py`**

```python
# src/sm64_events/desktop/tray.py
"""System tray icon (pystray): Show / Quit. Shell-only — no browser
equivalent, so it never touches ui/. Runs on its own daemon thread."""
import logging
import sys
import threading
from pathlib import Path

import pystray
from PIL import Image

log = logging.getLogger("sm64.desktop")


def _icon_image() -> "Image.Image":
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "."))
    else:
        # src/sm64_events/desktop/tray.py -> repo root / assets
        base = Path(__file__).resolve().parents[3] / "assets"
    try:
        return Image.open(base / "ukiki.ico")
    except Exception:
        return Image.new("RGB", (64, 64), (120, 72, 36))


def create(on_show, on_quit) -> "pystray.Icon":
    menu = pystray.Menu(
        pystray.MenuItem("Show", lambda icon, item: on_show()),
        pystray.MenuItem("Quit", lambda icon, item: on_quit()))
    return pystray.Icon("sm64_tracker", _icon_image(), "sm64_tracker", menu)


def start(icon) -> None:
    threading.Thread(target=icon.run, name="tray", daemon=True).start()


def stop(icon) -> None:
    try:
        icon.stop()
    except Exception:
        log.debug("tray stop failed", exc_info=True)
```

- [ ] **Step 3: Import-smoke the modules**

Run: `uv run python -c "from sm64_events.desktop import window, tray; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/desktop/window.py src/sm64_events/desktop/tray.py
git commit -m "feat(desktop): resizable pywebview window + pystray tray

Window has no max bound (fills full portrait/landscape) and persists
geometry; tray offers Show/Quit. Both are shell-only; UI is untouched."
```

---

## Task 11: `desktop/app.py` + `__main__.py` (composition, restart wiring, live verify)

**Files:**
- Create: `src/sm64_events/desktop/app.py`, `src/sm64_events/desktop/__main__.py`

> Composition glue; verified live. The single-instance dialog uses a native Win32 `MessageBoxW` (no extra dep). `request_shutdown`/`request_restart` are wired to a FULL GUI quit so the admin endpoints really close/relaunch the app.

- [ ] **Step 1: Write `app.py`**

```python
# src/sm64_events/desktop/app.py
"""Desktop composition root: single-instance dialog -> server -> tray ->
window, with one-click restart. Runnable from source as
``uv run python -m sm64_events.desktop`` for fast iteration."""
import ctypes
import logging
import os

from sm64_events.core.logging_setup import configure_logging
from sm64_events.core.paths import data_root
from sm64_events.core.relaunch import spawn_replacement, wait_port_free
from sm64_events.desktop import single_instance, tray, window
from sm64_events.desktop.server_runner import ServerRunner
from sm64_events.main import build

log = logging.getLogger("sm64.desktop")

_MB_YESNO = 0x4
_MB_ICONQUESTION = 0x20
_MB_ICONERROR = 0x10
_IDYES = 6


def _ask_takeover() -> bool:
    """Native yes/no: Yes = close the other instance and run here."""
    return ctypes.windll.user32.MessageBoxW(
        None,
        "sm64_tracker is already running.\n\n"
        "Use THIS window and close the other instance?",
        "sm64_tracker", _MB_YESNO | _MB_ICONQUESTION) == _IDYES


def _error(msg: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, msg, "sm64_tracker", _MB_ICONERROR)


def main() -> None:
    configure_logging()
    data_root().mkdir(parents=True, exist_ok=True)

    if os.environ.pop("SM64_RESTART", None):
        # Restart relaunch: the old process is exiting — wait for the port,
        # no dialog.
        wait_port_free()
    elif single_instance.instance_running():
        if not _ask_takeover():
            return  # keep the other instance; quit this one
        if not single_instance.take_over():
            _error("Could not close the other instance. It may still be "
                   "running.")
            return

    app = build()
    runner = ServerRunner(app)
    runner.start()
    if not runner.wait_until_ready():
        _error("The tracker server did not start. Check the logs.")
        runner.stop()
        return

    state = {"quit": False, "tray": None}

    def quit_all():
        if state["quit"]:
            return
        state["quit"] = True
        runner.stop()
        if state["tray"] is not None:
            tray.stop(state["tray"])
        for w in _windows():
            try:
                w.destroy()
            except Exception:
                pass

    def do_restart():
        spawn_replacement()
        quit_all()

    # Admin endpoints drive a FULL GUI quit / relaunch (not just the server),
    # so "close the other instance" and "Restart server" really do.
    app.state.request_shutdown = quit_all
    app.state.request_restart = do_restart

    win = window.create(on_closed=quit_all)
    state["tray"] = tray.create(on_show=win.show, on_quit=quit_all)
    tray.start(state["tray"])

    window.run()    # blocks until the window closes
    quit_all()      # idempotent backstop for the normal close path


def _windows():
    import webview
    return list(webview.windows)
```

- [ ] **Step 2: Write `__main__.py`**

```python
# src/sm64_events/desktop/__main__.py
from sm64_events.desktop.app import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Import-smoke (does not open a window)**

Run: `uv run python -c "import sm64_events.desktop.app as a; print(callable(a.main))"`
Expected: prints `True`.

- [ ] **Step 4: LIVE VERIFY with the human (from source — no packaging yet)**

Run: `uv run python -m sm64_events.desktop`
Confirm with the human:
- A native window opens showing the tracker UI (same as the browser).
- The window resizes freely — drag it to fill a full **portrait** monitor and a full **landscape** monitor; close + reopen restores the last size/position.
- Tray icon appears; "Show" focuses the window; "Quit" closes cleanly (process exits, prompt returns).
- The browser at `http://127.0.0.1:8064/` still works while the window is open.
- Launch a SECOND `uv run python -m sm64_events.desktop` → the takeover dialog appears; "Yes" closes the first and runs here; "No" quits the second and leaves the first running.
- With PJ64 + Usamune running: a star grab / replay records as before.

Fix any pywebview/pystray API mismatches surfaced here, then re-verify.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/desktop/app.py src/sm64_events/desktop/__main__.py
git commit -m "feat(desktop): composition root, single-instance dialog, restart wiring

python -m sm64_events.desktop launches the full GUI from source. Admin
endpoints drive a full quit/relaunch. Live-verified."
```

---

## Task 12: "Restart server" button in the UI

**Files:**
- Modify: `src/sm64_events/ui/components/header.js`

> UI has no JS unit-test harness here (tests are pytest) — the endpoint is covered by Task 6; the button is verified live. Single-click by design (the user asked for "one click"); the disabled "restarting…" state prevents double-fires, and the store's WS auto-reconnect (store.js, 2 s) rejoins the new server with no page reload.

- [ ] **Step 1: Add the handler + state in `Header`**

In `src/sm64_events/ui/components/header.js`, inside `Header`, after the existing `const [managing, setManaging] = useState(false);` line, add:
```javascript
  const [restarting, setRestarting] = useState(false);

  async function restartServer() {
    if (restarting) return;
    setRestarting(true);
    try {
      await send("POST", "/api/admin/restart");
    } catch (e) {
      console.error(e);   // endpoint may drop the connection as it restarts
    }
    // The WS drops and auto-reconnects (store.js); clear the flag after a beat.
    setTimeout(() => setRestarting(false), 8000);
  }
```

- [ ] **Step 2: Add the button next to the pause button**

In the returned markup, immediately after the closing `</button>` of the pause button (the one bound to `t.togglePause`, before `<${RecordingDot} />`), insert:
```javascript
    <button onclick=${restartServer} disabled=${restarting}
            title="Relaunch the underlying server to pick up backend changes">
      ${restarting ? "↻ restarting…" : "↻ restart server"}</button>
```

- [ ] **Step 3: LIVE VERIFY with the human**

- **Browser:** `uv run python -m sm64_events.main`, open `http://127.0.0.1:8064/`, click **↻ restart server**. Confirm: the connection dot goes "offline" then back to "live" within a few seconds (the server relaunched), and after restart any edit you made to a backend `.py` file is in effect.
- **GUI:** `uv run python -m sm64_events.desktop`, click the button. Confirm the window's server reconnects (the GUI relaunches: window briefly closes and a fresh one opens after the old frees the port). History/PBs survive (re-projected from the journal).

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/components/header.js
git commit -m "feat(ui): one-click 'Restart server' button

Relaunches the backend to pick up code changes; POSTs /api/admin/restart. The
store's WS auto-reconnect rejoins the new server with no page reload."
```

---

## Task 13: App icon (`assets/ukiki.ico`)

**Files:**
- Create: `tools/make_placeholder_icon.py`, `assets/ukiki.ico`

- [ ] **Step 1: Write the generator**

```python
# tools/make_placeholder_icon.py
"""Generate assets/ukiki.ico — a simple stylized monkey-head placeholder
(original art, not ripped game assets). Replace with nicer art later; the
build only needs a valid multi-res .ico to exist here."""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parents[1] / "assets" / "ukiki.ico"


def main() -> None:
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((20, 20, 236, 236), fill=(120, 72, 36, 255))      # head
    d.ellipse((40, 30, 95, 95), fill=(120, 72, 36, 255))        # ears
    d.ellipse((161, 30, 216, 95), fill=(120, 72, 36, 255))
    d.ellipse((70, 92, 186, 208), fill=(228, 200, 152, 255))    # face
    d.ellipse((98, 120, 122, 150), fill=(35, 25, 18, 255))      # eyes
    d.ellipse((134, 120, 158, 150), fill=(35, 25, 18, 255))
    d.ellipse((116, 156, 140, 178), fill=(80, 52, 30, 255))     # snout
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, sizes=[(16, 16), (32, 32), (48, 48),
                         (64, 64), (128, 128), (256, 256)])
    print("wrote", OUT)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate the icon**

Run: `uv run python tools/make_placeholder_icon.py`
Expected: prints `wrote .../assets/ukiki.ico`; the file exists.

- [ ] **Step 3: Verify it loads**

Run: `uv run python -c "from PIL import Image; Image.open('assets/ukiki.ico').verify(); print('ok')"`
Expected: prints `ok`.

- [ ] **Step 4: Commit**

```bash
git add tools/make_placeholder_icon.py assets/ukiki.ico
git commit -m "feat(desktop): Ukiki-style app icon (placeholder, original art)"
```

---

## Task 14: One-command build → portable exe

**Files:**
- Create: `gui_entry.py` (repo root), `tools/rthook_comtypes.py`, `tools/build_exe.py`

- [ ] **Step 1: Write the PyInstaller entry point**

```python
# gui_entry.py
"""PyInstaller entry point for the packaged desktop app.
(Dev runs `python -m sm64_events.desktop`; both call the same main().)"""
from sm64_events.desktop.app import main

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Write the comtypes runtime hook**

```python
# tools/rthook_comtypes.py
"""Runtime hook: comtypes (pulled in by pycaw) generates cache modules at
import time and needs a WRITABLE directory. The frozen bundle is read-only,
so point it at a temp dir before pycaw is imported."""
import os
import sys
import tempfile

if getattr(sys, "frozen", False):
    gen = tempfile.mkdtemp(prefix="ctgen_")
    os.environ.setdefault("COMTYPES_GEN_DIR", gen)
    try:
        import comtypes.client
        comtypes.client.gen_dir = gen
    except Exception:
        pass
```

- [ ] **Step 3: Write the build script**

```python
# tools/build_exe.py
"""One-command build: `uv run python tools/build_exe.py` -> dist/sm64_tracker.exe

Bundles Python + all native deps + the UI folder + the Ukiki icon into a
single onefile exe. Pass --ffmpeg PATH to bundle ffmpeg.exe (strongly
recommended for replay quality; without it the exe falls back to the
in-process PyAV encoder)."""
import argparse
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"
# Native/binary deps whose data files or submodules PyInstaller's auto
# analysis can miss — collect everything for each.
COLLECT = ["av", "windows_capture", "pyaudiowpatch", "pycaw", "comtypes",
           "pymem", "webview", "pystray", "numpy"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", help="path to an ffmpeg.exe to bundle in")
    args = ap.parse_args()

    import PyInstaller.__main__ as pyi

    argv = [
        str(REPO / "gui_entry.py"),
        "--name", "sm64_tracker",
        "--onefile", "--windowed", "--clean", "--noconfirm",
        "--paths", str(REPO / "src"),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
        "--runtime-hook", str(REPO / "tools" / "rthook_comtypes.py"),
        # The UI is READ FROM DISK at runtime (server/app.py _UI_INDEX), not
        # imported, so it must be collected preserving the package path.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'ui'}{SEP}sm64_events/ui",
    ]
    for pkg in COLLECT:
        argv += ["--collect-all", pkg]
    if args.ffmpeg:
        ff = Path(args.ffmpeg)
        if not ff.exists():
            print(f"ffmpeg not found: {ff}", file=sys.stderr)
            return 2
        argv += ["--add-binary", f"{ff}{SEP}."]
    else:
        print("WARNING: building without bundled ffmpeg — replay will use the "
              "in-process encoder. Pass --ffmpeg PATH for best quality.")

    pyi.run(argv)
    print("\nBuilt:", REPO / "dist" / "sm64_tracker.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Build the exe**

Obtain a static `ffmpeg.exe` (gyan.dev or BtbN Windows build) at a known path, then run:
`uv run python tools/build_exe.py --ffmpeg C:\path\to\ffmpeg.exe`
Expected: PyInstaller runs to completion; `dist/sm64_tracker.exe` exists (~150–250 MB). First builds often surface a missing hidden import — add the offending package to `COLLECT` (or `--hidden-import`) and rebuild.

- [ ] **Step 5: LIVE VERIFY the frozen exe with the human**

Run `dist\sm64_tracker.exe` from a folder OTHER than the repo (proves cwd-independence). Confirm:
- The window opens with the full UI; resizes to full portrait/landscape; geometry persists.
- Data appears under `%LOCALAPPDATA%\sm64_tracker\` (`data\tracker.db`, `server.pid`, `window.json`; `replays\` after a save).
- With PJ64 + Usamune running: star grabs fire AND **replay records and plays back** (the critical native-dep path — PyAV / windows-capture / pyaudiowpatch / pycaw all loading from the bundle; bundled ffmpeg used).
- Tray Show/Quit works; a second launch shows the takeover dialog; **the "↻ restart server" button relaunches the exe cleanly**.
- Note the one-time SmartScreen "More info → Run anyway" prompt (expected for an unsigned exe; documented in the README in Task 15).

Iterate on `build_exe.py` (`--collect-all` / `--hidden-import` / runtime hooks) until every path above works in the frozen exe.

- [ ] **Step 6: Commit**

```bash
git add gui_entry.py tools/rthook_comtypes.py tools/build_exe.py
git commit -m "build: one-command PyInstaller onefile exe

uv run python tools/build_exe.py [--ffmpeg PATH] -> dist/sm64_tracker.exe.
Bundles native deps + UI folder + ffmpeg + icon; comtypes gen-dir runtime
hook. Live-verified: window, replay, single-instance, restart, %LOCALAPPDATA%."
```

---

## Task 15: README restructure + `docs/api.md` + `CLAUDE.md`

**Files:**
- Create: `docs/api.md`
- Rewrite: `README.md`
- Modify: `CLAUDE.md` (module map + parity rule)

> **Coordination:** the other session has uncommitted `README.md` (and `CLAUDE.md`) edits. **Confirm that work has landed and pull/rebase BEFORE this task**, then restructure on top of it. Do not overwrite their changes.

- [ ] **Step 1: Move the reference into `docs/api.md`**

Create `docs/api.md` and move (verbatim) from the current README these sections: **Event schema** (envelope + the full event-type table — including `/api/admin/shutdown` + `/api/admin/restart` added to the HTTP API table), **HTTP API**, **Segments**, **Error taxonomy**, the **Replay** subsection, **Data**, **Tools**, **Behavior notes**, **Known limitations**. Top of `docs/api.md`:
```markdown
# sm64_tracker — API reference

The live event feed, HTTP API, replay endpoints, and behavior notes. New to
the project? Start with the [README](../README.md). Developing here? Read
`CLAUDE.md` and `docs/architecture.md` first.

## Admin endpoints (localhost only)

| Endpoint | Description |
|---|---|
| `POST /api/admin/shutdown` | Graceful shutdown — the desktop "close the other instance" takeover path. `{"shutting_down": true}` |
| `POST /api/admin/restart` | Full-process relaunch — the one-click "Restart server" button; picks up edited backend code. `{"restarting": true}` |
```

- [ ] **Step 2: Rewrite `README.md` as the front door**

Replace the README body with this structure (fill feature bullets from the moved content; keep it lean):
```markdown
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
uv run python tools/build_exe.py --ffmpeg C:\path\to\ffmpeg.exe
```
→ `dist\sm64_tracker.exe` (one onefile build; omit `--ffmpeg` to fall back to
the in-process encoder). See `tools/build_exe.py` for what gets bundled.

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
```

- [ ] **Step 3: Update `CLAUDE.md`**

Add to the "Module map" table:
```markdown
| Runtime data locations (db, replays, settings, lock, pidfile, window state) | `core/paths.py` — THE path resolver; cwd-relative from source (identical to historical layout), `%LOCALAPPDATA%\sm64_tracker` when frozen; also `bundled_ffmpeg()` |
| Full-process restart primitives | `core/relaunch.py` — `server_alive`/`wait_port_free`/`spawn_replacement`; backs the one-click restart + the `SM64_RESTART` handoff |
| Desktop GUI shell (window, tray, single-instance, server runner) | `desktop/` — additive wrapper over the SAME server/UI: `app.py` (composition + native takeover dialog + restart/quit wiring), `server_runner.py` (uvicorn in a thread), `single_instance.py`, `window.py` (resizable pywebview + geometry), `tray.py`; entry `python -m sm64_events.desktop` / `gui_entry.py` |
| Admin endpoints (GUI takeover + restart) | `server/app.py` `POST /api/admin/shutdown` + `/api/admin/restart` + pidfile in the lifespan; dispatched off-thread |
| One-command portable build | `tools/build_exe.py` (+ `tools/rthook_comtypes.py`, `assets/ukiki.ico`) — PyInstaller onefile |
```
Note: `main.py` row should mention the lazy `app` (`__getattr__`) so imports don't build/lock. Add a new "Domain rules" entry:
```markdown
N. **Browser ↔ GUI parity.** Every user-facing feature lands in `ui/` +
   server, so it appears in BOTH the browser tab and the desktop window. The
   `desktop/` shell adds ONLY native chrome (window, tray, icon,
   single-instance, restart) and must never fork or special-case the UI.
```

- [ ] **Step 4: Verify the suite + links**

Run: `uv run pytest -q`
Expected: full suite PASSES (docs-only changes don't affect tests).
Manually confirm `README.md` links to `docs/api.md` and back.

- [ ] **Step 5: Commit**

```bash
git add README.md docs/api.md CLAUDE.md
git commit -m "docs: README front door + docs/api.md + GUI parity rule

README becomes an approachable front door (user quickstart, dev setup, build,
restart button); the event/HTTP reference moves to docs/api.md. CLAUDE.md
gains core/paths + core/relaunch + desktop/ rows and the browser↔GUI parity
rule."
```

---

## Final verification

- [ ] Run the full suite: `uv run pytest -q` — all green.
- [ ] Re-run the live GUI verification (Task 11 Step 4), the restart verification (Task 12 Step 3), and the frozen-exe verification (Task 14 Step 5) once more on the final build.
- [ ] Confirm a clean checkout in a different folder, given only `dist\sm64_tracker.exe`, runs with zero setup against PJ64 + Usamune, and the restart button relaunches it.

---

## Self-review notes (author)

- **Spec coverage:** §1 paths→T1–T4; §2 window→T10/T11; §3 single-instance→T6/T9/T11; §4 admin endpoints→T6; §4b restart→T5/T6/T11/T12 (+ lazy-app root-cause fix in T2); §5 ffmpeg/native bundling→T4/T7/T14; §6 build+icon→T13/T14; §7 README/parity→T15; file list→all. No spec section is unaddressed.
- **Path-equivalence guarantee:** source-mode `data_root()` returns `Path(".")`, so all migrated paths are byte-identical to today's — the existing suite stays green (Tasks 2–4 run targeted suites; the full suite runs in T15/final).
- **Naming consistency:** `app.state.request_shutdown`/`request_restart` set by `ServerRunner.__init__` (default, T8) and overridden by `app.py` (T11), read by the admin endpoints (T6); `single_instance.instance_running`/`take_over` used identically in tests (T9) and `app.py` (T11); `core.relaunch.{server_alive,wait_port_free,spawn_replacement}` defined T5, used by `single_instance` (T9), `server/app.py` (T6), `main.run()` (T5), `desktop/app.py` (T11); `bundled_ffmpeg()` defined T1, used T4; `get_app()` defined T2, used by `run()` (T2/T5) and `__getattr__`.
- **Deadlock guard:** admin endpoints dispatch the action on a daemon thread (`_dispatch`) — never join the server thread inside a handler.
- **Lazy-app footgun fixed:** `from sm64_events.main import build` no longer builds/locks at import; only `get_app()` / `main:app` attribute access builds (T2). Required for the desktop to acquire the lock only AFTER the single-instance takeover.
- **Live-gated, not unit-tested (by design, per repo culture):** `window.py`, `tray.py`, `app.py`, `header.js` button, and the frozen build — all version-sensitive GUI / packaging surfaces with explicit human-verify steps.
```