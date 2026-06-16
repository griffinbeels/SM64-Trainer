# GitHub repo + in-app auto-update — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish the project to `github.com/griffinbeels/SM64-Trainer` and give the frozen exe an in-app auto-updater (check GitHub's latest release → SHA-256-verified download → swap the running exe → restart), plus a one-command release tool.

**Architecture:** Five single-purpose pieces riding existing machinery (`spawn_replacement`, `wait_port_free`, the per-process `data_root` split): `core/version.py` (the one version constant), `core/updater.py` (pure helpers + a thin stateful `UpdateService`), `server/update_api.py` (`/api/update/*`), `ui/components/update.js` (the popup), `tools/release.py` (bump→tag→build→checksum→publish). The check runs backend-side and is guarded on `is_frozen()` so dev is inert. The exe swap uses the Windows "rename a running exe" trick.

**Tech Stack:** Python 3.12 (stdlib `urllib`/`hashlib`/`os.replace`), FastAPI router, Preact/htm UI, `gh` CLI for publishing, pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-06-16-github-repo-and-auto-update-design.md`

**Refinement vs. spec:** download progress is exposed via the existing `/api/update/status` poll (an in-memory `progress`/`state` on `UpdateService`), NOT a new WebSocket `update_progress` event — avoids a thread→event-loop async bridge for identical UX.

---

## File map

| File | New/Mod | Responsibility |
|---|---|---|
| `src/sm64_events/core/version.py` | New | `__version__` — the one runtime version constant |
| `src/sm64_events/core/paths.py` | Mod | add `update_state_path()` |
| `src/sm64_events/core/updater.py` | New | pure helpers (`parse_version`…`exe_dir_writable`) + `UpdateService` |
| `src/sm64_events/server/update_api.py` | New | `GET /api/update/status`, `POST /api/update/apply`, `POST /api/update/skip` |
| `src/sm64_events/server/app.py` | Mod | `create_app(..., updater=None)` includes the router + wires restart |
| `src/sm64_events/main.py` | Mod | build the `UpdateService`, run `cleanup_old_exe()`, pass to `create_app` |
| `src/sm64_events/ui/components/update.js` | New | the update popup (notes + Update/Skip/Later) |
| `src/sm64_events/ui/app.js` | Mod | mount `<UpdatePopup/>` at app root |
| `src/sm64_events/ui/index.html` | Mod | modal + progress CSS |
| `tools/release.py` | New | one-command release |
| `tests/test_updater.py` | New | pure helpers + service |
| `tests/test_update_api.py` | New | endpoints |
| `tests/test_release.py` | New | release pure helpers |
| `.gitignore` | Mod | ignore `internal_notes/` |
| `internal_notes/` | New (ignored) | holds the `.psd` + `design_log.md` |

---

## Task 1: Repo housekeeping + first push

**Files:**
- Create: `internal_notes/` (gitignored)
- Modify: `.gitignore`

- [ ] **Step 1: Move the loose files into a gitignored folder**

Both files are UNtracked (git status showed `?? assets/sm64_tracker.psd` and `?? design_log.md`), so a plain move is correct — no `git mv`. Run via the **Bash tool** (POSIX sh):

```bash
mkdir -p internal_notes
mv assets/sm64_tracker.psd internal_notes/
mv design_log.md internal_notes/
ls internal_notes/
```
Expected: `internal_notes/` contains `sm64_tracker.psd` and `design_log.md`. (Adjust the source path if a file has since moved.)

- [ ] **Step 2: Gitignore the folder**

Add this line to `.gitignore` after the `_dbg/` line:

```
internal_notes/
```

- [ ] **Step 3: Verify nothing sensitive is staged**

```bash
git status --porcelain
git check-ignore internal_notes/design_log.md data/tracker.db replays logs
```
Expected: `internal_notes/...`, `data/tracker.db`, `replays`, `logs` all print (they're ignored). `git status` shows only `.gitignore` modified.

- [ ] **Step 4: Commit the ignore change**

```bash
git add .gitignore
git commit -m "chore: gitignore internal_notes/ (source art + design log stay local)"
```

- [ ] **Step 5: Add the remote and push master**

```bash
git remote add origin https://github.com/griffinbeels/SM64-Trainer.git
git remote -v
git push -u origin master
```
Expected: remote `origin` listed; `master` pushed and tracking set. If the repo was created with a README/license, run `git pull --rebase origin master` first, then push.

---

## Task 2: Version source of truth

**Files:**
- Create: `src/sm64_events/core/version.py`
- Test: `tests/test_updater.py` (first test)

- [ ] **Step 1: Write the failing test**

Create `tests/test_updater.py`:

```python
import re

from sm64_events.core.version import __version__


def test_version_is_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__
```

- [ ] **Step 2: Run it — expect failure**

Run: `uv run pytest tests/test_updater.py::test_version_is_semver -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.version'`.

- [ ] **Step 3: Create the version module**

Create `src/sm64_events/core/version.py`:

```python
# src/sm64_events/core/version.py
"""THE runtime version constant. Read by the app (update check baseline), the
build, and tools/release.py (which rewrites it on each release). The frozen exe
can't read pyproject.toml, so this in-package constant is authoritative;
release.py keeps pyproject.toml [project].version in sync for tooling."""
__version__ = "0.1.0"
```

- [ ] **Step 4: Run it — expect pass**

Run: `uv run pytest tests/test_updater.py::test_version_is_semver -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/version.py tests/test_updater.py
git commit -m "feat(version): add core/version.py __version__ (update-check baseline)"
```

---

## Task 3: `update_state_path()` in paths.py

**Files:**
- Modify: `src/sm64_events/core/paths.py`
- Test: `tests/test_paths.py` (create if absent, else append)

- [ ] **Step 1: Write the failing test**

Create/append `tests/test_paths.py`:

```python
from sm64_events.core.paths import update_state_path


def test_update_state_path_lives_under_data():
    p = update_state_path()
    assert p.name == "update_state.json"
    assert p.parent.name == "data"
```

- [ ] **Step 2: Run it — expect failure**

Run: `uv run pytest tests/test_paths.py::test_update_state_path_lives_under_data -q`
Expected: FAIL — `ImportError: cannot import name 'update_state_path'`.

- [ ] **Step 3: Add the function**

In `src/sm64_events/core/paths.py`, after `replay_settings_path()` (around line 67), add:

```python
def update_state_path() -> Path:
    # Skipped-update version lives here (a JSON overlay like replay_settings.json,
    # keeps the updater DB-free).
    return data_root() / "data" / "update_state.json"
```

- [ ] **Step 4: Run it — expect pass**

Run: `uv run pytest tests/test_paths.py::test_update_state_path_lives_under_data -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/paths.py tests/test_paths.py
git commit -m "feat(paths): add update_state_path() for the skipped-version overlay"
```

---

## Task 4: Updater pure helpers — version compare

**Files:**
- Create: `src/sm64_events/core/updater.py`
- Test: `tests/test_updater.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_updater.py`:

```python
from sm64_events.core.updater import is_newer, parse_version


def test_parse_version_strips_v_and_splits():
    assert parse_version("v1.2.3") == (1, 2, 3)
    assert parse_version("1.2.3") == (1, 2, 3)


def test_parse_version_stops_at_non_numeric_suffix():
    assert parse_version("1.2.3-beta") == (1, 2, 3)


def test_is_newer_compares_numerically():
    assert is_newer("1.2.10", "1.2.9") is True   # not lexicographic
    assert is_newer("1.0.0", "0.9.9") is True
    assert is_newer("1.0.0", "1.0.0") is False
    assert is_newer("0.9.9", "1.0.0") is False
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_updater.py -q -k "parse_version or is_newer"`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.updater'`.

- [ ] **Step 3: Create updater.py with the header + compare helpers**

Create `src/sm64_events/core/updater.py`:

```python
# src/sm64_events/core/updater.py
"""Self-update for the frozen exe: check the GitHub 'latest' release, download
the new exe, verify its SHA-256, and swap it in over the running process using
the Windows rename-a-running-exe trick (the OS forbids DELETING a running exe
but ALLOWS renaming one). The restart rides core/relaunch.spawn_replacement.

Pure helpers (parse_version … exe_dir_writable) take an injected HTTP opener and
operate on explicit paths so tests never touch the network or a real exe. The
stateful UpdateService orchestrates them, caches the check, tracks download
progress, and persists the 'skipped' version. Everything is guarded on
is_frozen(): from source it is inert (update_available is always False) so a dev
tree is never swapped."""
import hashlib
import json
import logging
import os
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sm64_events.core.paths import is_frozen, update_state_path

log = logging.getLogger("sm64.updater")

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
EXE_NAME = "sm64_tracker.exe"
_UA = "sm64_tracker-updater"
_CHECK_TTL_S = 3600.0


def parse_version(tag: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' -> (1, 2, 3). A non-numeric piece stops the parse, so
    '1.2.3-beta' compares as (1, 2, 3)."""
    out: list[int] = []
    for part in tag.lstrip("vV").split("."):
        num = ""
        for ch in part:
            if ch.isdigit():
                num += ch
            else:
                break
        if num == "":
            break
        out.append(int(num))
    return tuple(out)


def is_newer(candidate: str, current: str) -> bool:
    return parse_version(candidate) > parse_version(current)
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_updater.py -q -k "parse_version or is_newer"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/updater.py tests/test_updater.py
git commit -m "feat(updater): version parse/compare helpers"
```

---

## Task 5: Updater pure helpers — GitHub check

**Files:**
- Modify: `src/sm64_events/core/updater.py`
- Test: `tests/test_updater.py`

- [ ] **Step 1: Write the failing tests (with a fake HTTP opener)**

Append to `tests/test_updater.py`:

```python
import io
import json as _json

from sm64_events.core.updater import UpdateInfo, check_for_update


class _Resp(io.BytesIO):
    def __init__(self, data: bytes, headers: dict | None = None):
        super().__init__(data)
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _fake_http(routes: dict):
    """routes: url -> bytes. Raises for an unmapped url."""
    def opener(req):
        url = req.full_url if hasattr(req, "full_url") else req
        if url not in routes:
            raise OSError(f"unmapped url {url}")
        body = routes[url]
        return _Resp(body, {"Content-Length": str(len(body))})
    return opener


def _release_json(tag, assets):
    return _json.dumps({
        "tag_name": tag, "body": "notes here",
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [{"name": n, "browser_download_url": u}
                   for n, u in assets.items()],
    }).encode()


LATEST = "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases/latest"


def test_check_returns_info_when_newer_with_asset():
    http = _fake_http({LATEST: _release_json("v2.0.0", {
        "sm64_tracker.exe": "https://dl/exe",
        "sm64_tracker.exe.sha256": "https://dl/sha",
    })})
    info = check_for_update("1.0.0", http=http)
    assert isinstance(info, UpdateInfo)
    assert info.version == "2.0.0"
    assert info.asset_url == "https://dl/exe"
    assert info.sha256_url == "https://dl/sha"


def test_check_none_when_not_newer():
    http = _fake_http({LATEST: _release_json("v1.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_when_no_exe_asset():
    http = _fake_http({LATEST: _release_json("v2.0.0", {"notes.txt": "u"})})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_on_http_error():
    def boom(req):
        raise OSError("network down")
    assert check_for_update("1.0.0", http=boom) is None
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_updater.py -q -k check`
Expected: FAIL — `ImportError: cannot import name 'UpdateInfo'`.

- [ ] **Step 3: Add `UpdateInfo` + `check_for_update`**

In `src/sm64_events/core/updater.py`, after `is_newer`, add:

```python
@dataclass
class UpdateInfo:
    version: str
    notes: str
    html_url: str
    asset_url: str
    sha256_url: str


def _get(http, url: str, *, accept: str | None = None):
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    return http(req)


def check_for_update(current: str, *, http=urllib.request.urlopen,
                     repo: str = DEFAULT_REPO,
                     api_base: str = GITHUB_API) -> "UpdateInfo | None":
    """GET the latest release; return UpdateInfo iff it is strictly newer AND
    carries an EXE_NAME asset. Best-effort: any error -> None (no popup)."""
    try:
        url = f"{api_base}/repos/{repo}/releases/latest"
        with _get(http, url, accept="application/vnd.github+json") as r:
            rel = json.loads(r.read().decode("utf-8"))
        tag = rel.get("tag_name") or ""
        if not is_newer(tag, current):
            return None
        assets = {a.get("name"): a.get("browser_download_url")
                  for a in rel.get("assets", [])}
        asset_url = assets.get(EXE_NAME)
        if not asset_url:
            return None
        return UpdateInfo(
            version=tag.lstrip("vV"),
            notes=rel.get("body") or "",
            html_url=rel.get("html_url") or "",
            asset_url=asset_url,
            sha256_url=assets.get(EXE_NAME + ".sha256") or "")
    except Exception:
        log.info("update check failed", exc_info=True)
        return None
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_updater.py -q -k check`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/updater.py tests/test_updater.py
git commit -m "feat(updater): check_for_update against GitHub latest release"
```

---

## Task 6: Updater pure helpers — download/verify, swap, cleanup, writability

**Files:**
- Modify: `src/sm64_events/core/updater.py`
- Test: `tests/test_updater.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_updater.py`:

```python
import hashlib as _hashlib

import pytest

from sm64_events.core.updater import (apply_update, cleanup_old,
                                      download_and_stage, exe_dir_writable)


def test_download_stage_verifies_good_hash(tmp_path):
    payload = b"new exe bytes"
    digest = _hashlib.sha256(payload).hexdigest()
    info = UpdateInfo("2.0.0", "n", "h", "https://dl/exe", "https://dl/sha")
    http = _fake_http({"https://dl/exe": payload,
                       "https://dl/sha": (digest + "  sm64_tracker.exe").encode()})
    seen = []
    staged = download_and_stage(info, tmp_path, http=http,
                                progress=seen.append)
    assert staged.read_bytes() == payload
    assert staged.name == "sm64_tracker.exe.new"
    assert seen and seen[-1] == 1.0


def test_download_stage_rejects_bad_hash(tmp_path):
    info = UpdateInfo("2.0.0", "n", "h", "https://dl/exe", "https://dl/sha")
    http = _fake_http({"https://dl/exe": b"corrupt",
                       "https://dl/sha": (("0" * 64) + "  x").encode()})
    with pytest.raises(ValueError):
        download_and_stage(info, tmp_path, http=http)
    assert not (tmp_path / "sm64_tracker.exe.new").exists()


def test_apply_update_swaps_running_exe(tmp_path):
    current = tmp_path / "sm64_tracker.exe"
    current.write_text("OLD")
    staged = tmp_path / "sm64_tracker.exe.new"
    staged.write_text("NEW")
    apply_update(staged, current)
    assert current.read_text() == "NEW"
    assert (tmp_path / "sm64_tracker.exe.old").read_text() == "OLD"


def test_cleanup_old_removes_old_files(tmp_path):
    (tmp_path / "sm64_tracker.exe.old").write_text("x")
    cleanup_old(tmp_path)
    assert not (tmp_path / "sm64_tracker.exe.old").exists()


def test_exe_dir_writable(tmp_path):
    assert exe_dir_writable(tmp_path) is True
    assert exe_dir_writable(tmp_path / "does-not-exist") is False
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_updater.py -q -k "download_stage or apply_update or cleanup_old or exe_dir"`
Expected: FAIL — `ImportError: cannot import name 'download_and_stage'`.

- [ ] **Step 3: Add the four helpers**

In `src/sm64_events/core/updater.py`, after `check_for_update`, add:

```python
def download_and_stage(info: "UpdateInfo", exe_dir: Path, *,
                       http=urllib.request.urlopen, progress=None) -> Path:
    """Stream the new exe to <exe_dir>/sm64_tracker.exe.new, verify SHA-256
    against the published .sha256, return the staged path. Raises ValueError on
    a hash mismatch (caller keeps the current exe)."""
    staged = exe_dir / (EXE_NAME + ".new")
    h = hashlib.sha256()
    with _get(http, info.asset_url) as r:
        total = int((r.headers or {}).get("Content-Length") or 0)
        done = 0
        with open(staged, "wb") as f:
            while True:
                chunk = r.read(1 << 16)
                if not chunk:
                    break
                f.write(chunk)
                h.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
    if info.sha256_url:
        with _get(http, info.sha256_url) as r:
            published = r.read().decode("utf-8").split()[0].strip().lower()
        if published and published != h.hexdigest():
            staged.unlink(missing_ok=True)
            raise ValueError("update checksum mismatch")
    return staged


def apply_update(staged: Path, current_exe: Path, *, retries: int = 5,
                 sleep=time.sleep) -> None:
    """Swap `staged` in for the running exe via two renames (Windows allows
    renaming a running exe). Bounded retry: AV can briefly lock the new file."""
    old = current_exe.parent / (current_exe.name + ".old")
    for attempt in range(retries):
        try:
            old.unlink(missing_ok=True)
            os.replace(current_exe, old)
            os.replace(staged, current_exe)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            sleep(0.5)


def cleanup_old(exe_dir: Path) -> None:
    """Delete a leftover *.old from a prior update (now unlocked)."""
    for p in exe_dir.glob("*.old"):
        try:
            p.unlink()
        except OSError:
            pass  # still locked (rare) -> retry next launch


def exe_dir_writable(exe_dir: Path) -> bool:
    probe = exe_dir / ".sm64_update_probe"
    try:
        probe.write_text("x")
        probe.unlink()
        return True
    except OSError:
        return False
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_updater.py -q -k "download_stage or apply_update or cleanup_old or exe_dir"`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/updater.py tests/test_updater.py
git commit -m "feat(updater): download+SHA256 verify, running-exe swap, cleanup, writability"
```

---

## Task 7: `UpdateService` orchestrator

**Files:**
- Modify: `src/sm64_events/core/updater.py`
- Test: `tests/test_updater.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_updater.py`:

```python
from sm64_events.core.updater import UpdateService


def _svc(tmp_path, http, *, frozen=True):
    exe = tmp_path / "sm64_tracker.exe"
    exe.write_text("OLD")
    return UpdateService(current_version="1.0.0", http=http, exe_path=exe,
                         state_path=tmp_path / "update_state.json",
                         frozen=frozen)


def test_status_inert_from_source(tmp_path):
    svc = _svc(tmp_path, _fake_http({}), frozen=False)
    st = svc.status()
    assert st["frozen"] is False
    assert st["update_available"] is False


def test_status_reports_available(tmp_path):
    http = _fake_http({LATEST: _release_json("v2.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})
    svc = _svc(tmp_path, http)
    st = svc.status()
    assert st["update_available"] is True
    assert st["latest"] == "2.0.0"
    assert st["writable"] is True          # tmp dir is writable


def test_skip_persists_and_round_trips(tmp_path):
    http = _fake_http({LATEST: _release_json("v2.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})
    svc = _svc(tmp_path, http)
    svc.skip("2.0.0")
    assert svc.status()["skipped"] == "2.0.0"


def test_run_apply_swaps_and_calls_on_success(tmp_path):
    payload = b"NEWEXE"
    digest = _hashlib.sha256(payload).hexdigest()
    http = _fake_http({
        LATEST: _release_json("v2.0.0", {
            "sm64_tracker.exe": "https://dl/exe",
            "sm64_tracker.exe.sha256": "https://dl/sha"}),
        "https://dl/exe": payload,
        "https://dl/sha": (digest + "  sm64_tracker.exe").encode()})
    svc = _svc(tmp_path, http)
    info = svc._check(force=True)
    restarted = []
    svc._run_apply(info, lambda: restarted.append(True))
    assert (tmp_path / "sm64_tracker.exe").read_bytes() == payload
    assert restarted == [True]


def test_begin_apply_errors_when_no_update(tmp_path):
    http = _fake_http({LATEST: _release_json("v1.0.0", {
        "sm64_tracker.exe": "https://dl/exe"})})
    svc = _svc(tmp_path, http)
    assert svc.begin_apply(lambda: None)["state"] == "error"
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_updater.py -q -k "status or skip or run_apply or begin_apply"`
Expected: FAIL — `ImportError: cannot import name 'UpdateService'`.

- [ ] **Step 3: Add `UpdateService`**

In `src/sm64_events/core/updater.py`, append at the end of the file:

```python
class UpdateService:
    """Stateful orchestrator the REST layer talks to: caches the check, tracks
    download progress/state, persists the skipped version. Inert unless frozen.

    SM64_UPDATE_FAKE=1 makes status() report a synthetic update (writable forced
    False, so the only action is the GitHub link) — lets the popup be verified
    in dev WITHOUT cutting a real release."""

    def __init__(self, *, current_version: str, repo: str = DEFAULT_REPO,
                 exe_path: "Path | None" = None,
                 http=urllib.request.urlopen,
                 state_path: "Path | None" = None,
                 frozen: "bool | None" = None):
        self.current = current_version
        self.repo = repo
        self._http = http
        self._frozen = is_frozen() if frozen is None else frozen
        self._exe = Path(exe_path) if exe_path else Path(sys.executable)
        self._state_path = state_path or update_state_path()
        self._lock = threading.Lock()
        self._state = "idle"        # idle | downloading | installing | error
        self._progress = 0.0
        self._cache: "UpdateInfo | None" = None
        self._checked_at = 0.0      # monotonic of last real check; 0 = never

    # --- skipped-version overlay ---
    def _skipped(self) -> "str | None":
        try:
            return json.loads(self._state_path.read_text()).get("skipped")
        except Exception:
            return None

    def skip(self, version: str) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(json.dumps({"skipped": version}))

    # --- cached check ---
    def _fake(self) -> "UpdateInfo | None":
        if not os.environ.get("SM64_UPDATE_FAKE"):
            return None
        return UpdateInfo(
            version="9.9.9",
            notes="## Demo release\n- **New:** sample patch notes\n"
                  "- Open the release page for the real thing",
            html_url=f"https://github.com/{self.repo}/releases",
            asset_url="", sha256_url="")

    def _check(self, force: bool) -> "UpdateInfo | None":
        fake = self._fake()
        if fake is not None:
            return fake
        if not self._frozen:
            return None
        now = time.monotonic()
        if not force and self._checked_at and (now - self._checked_at) < _CHECK_TTL_S:
            return self._cache
        self._cache = check_for_update(self.current, http=self._http,
                                       repo=self.repo)
        self._checked_at = now
        return self._cache

    def status(self, force: bool = False) -> dict:
        info = self._check(force)
        writable = bool(self._frozen and info is not None
                        and not os.environ.get("SM64_UPDATE_FAKE")
                        and exe_dir_writable(self._exe.parent))
        return {
            "current": self.current,
            "frozen": self._frozen,
            "update_available": info is not None,
            "latest": info.version if info else None,
            "notes": info.notes if info else "",
            "html_url": info.html_url if info else "",
            "skipped": self._skipped(),
            "writable": writable,
            "state": self._state,
            "progress": self._progress,
        }

    # --- apply (off-thread; UI polls status for progress) ---
    def begin_apply(self, on_success) -> dict:
        with self._lock:
            if self._state in ("downloading", "installing"):
                return {"state": self._state}
            info = self._check(force=False)
            if not self._frozen or info is None:
                return {"state": "error", "error": "no update available"}
            if not exe_dir_writable(self._exe.parent):
                return {"state": "error", "error": "exe folder not writable"}
            self._state = "downloading"
            self._progress = 0.0
        threading.Thread(target=self._run_apply, args=(info, on_success),
                         daemon=True, name="update-apply").start()
        return {"state": "downloading"}

    def _run_apply(self, info: "UpdateInfo", on_success) -> None:
        try:
            staged = download_and_stage(info, self._exe.parent, http=self._http,
                                        progress=self._set_progress)
            self._state = "installing"
            apply_update(staged, self._exe)
            on_success()
        except Exception:
            log.exception("update apply failed")
            self._state = "error"

    def _set_progress(self, frac: float) -> None:
        self._progress = max(0.0, min(1.0, frac))

    def cleanup_old_exe(self) -> None:
        if self._frozen:
            cleanup_old(self._exe.parent)
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_updater.py -q`
Expected: PASS (all ~20 tests in the file).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/updater.py tests/test_updater.py
git commit -m "feat(updater): UpdateService (cached check, skip persistence, off-thread apply)"
```

---

## Task 8: Update REST surface + wiring

**Files:**
- Create: `src/sm64_events/server/update_api.py`
- Modify: `src/sm64_events/server/app.py` (signature + include + restart glue)
- Modify: `src/sm64_events/main.py` (build the service, cleanup, pass in)
- Test: `tests/test_update_api.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_update_api.py`:

```python
import threading
import time

from fastapi.testclient import TestClient

from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


class FakeUpdater:
    def __init__(self):
        self.skipped = None
        self.applied_with = None

    def status(self, force=False):
        return {"current": "1.0.0", "frozen": True, "update_available": True,
                "latest": "2.0.0", "notes": "n", "html_url": "h",
                "skipped": self.skipped, "writable": True,
                "state": "idle", "progress": 0.0, "force": force}

    def begin_apply(self, on_success):
        self.applied_with = on_success
        on_success()                 # simulate immediate success
        return {"state": "downloading"}

    def skip(self, version):
        self.skipped = version


def _client(updater):
    poller = Poller(OfflineMemory(), [StarGrabDetector()], Broadcaster())
    app = create_app(poller, Broadcaster(), updater=updater)
    return TestClient(app)


def _wait(flag, timeout=2.0):
    end = time.monotonic() + timeout
    while not flag and time.monotonic() < end:
        time.sleep(0.01)


def test_status_returns_service_payload():
    with _client(FakeUpdater()) as c:
        body = c.get("/api/update/status").json()
        assert body["update_available"] is True
        assert body["latest"] == "2.0.0"


def test_status_passes_force():
    with _client(FakeUpdater()) as c:
        assert c.get("/api/update/status?force=1").json()["force"] is True


def test_skip_records_version():
    up = FakeUpdater()
    with _client(up) as c:
        resp = c.post("/api/update/skip", json={"version": "2.0.0"})
        assert resp.status_code == 200
        assert up.skipped == "2.0.0"


def test_apply_triggers_restart_callback():
    up = FakeUpdater()
    with _client(up) as c:
        called: list[bool] = []
        c.app.state.request_restart = lambda: called.append(True)
        resp = c.post("/api/update/apply")
        assert resp.status_code == 200
        assert resp.json()["state"] == "downloading"
        _wait(called)
        assert called == [True]      # on_success -> app.state.request_restart
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_update_api.py -q`
Expected: FAIL — `create_app() got an unexpected keyword argument 'updater'`.

- [ ] **Step 3: Create the router**

Create `src/sm64_events/server/update_api.py`:

```python
# src/sm64_events/server/update_api.py
"""Self-update REST surface. status() and skip() are cheap; apply() kicks the
download+verify+swap off-thread (UpdateService owns the worker) and, on success,
fires the same full-process restart the admin endpoint uses."""
from fastapi import APIRouter
from pydantic import BaseModel


class SkipBody(BaseModel):
    version: str


def create_update_router(updater, restart) -> APIRouter:
    router = APIRouter(prefix="/api/update")

    @router.get("/status")
    def status(force: bool = False):
        return updater.status(force=force)

    @router.post("/apply")
    def apply():
        return updater.begin_apply(on_success=restart)

    @router.post("/skip")
    def skip(body: SkipBody):
        updater.skip(body.version)
        return {"skipped": body.version}

    return router
```

- [ ] **Step 4: Wire into `create_app`**

In `src/sm64_events/server/app.py`, change the signature (line ~214):

```python
def create_app(poller: Poller, broadcaster: Broadcaster,
               service=None, replay=None, updater=None,
               debug_hooks: bool = False) -> FastAPI:
```

Then, right after the existing `if replay is not None: ... include_router(create_replay_router(replay))` block (around line 320), add:

```python
    if updater is not None:
        from sm64_events.server.update_api import create_update_router

        def _restart():
            # Same path as /api/admin/restart: full GUI relaunch in the desktop
            # shell, spawn_replacement()+SIGINT fallback from a terminal launch.
            _dispatch(getattr(app.state, "request_restart", None)
                      or _fallback_restart)

        app.include_router(create_update_router(updater, _restart))
```

- [ ] **Step 5: Run — expect pass**

Run: `uv run pytest tests/test_update_api.py -q`
Expected: PASS (4 tests).

- [ ] **Step 6: Wire into `main.py` build()**

In `src/sm64_events/main.py`, add imports near the other `core` imports (line ~7):

```python
from sm64_events.core.updater import UpdateService
from sm64_events.core.version import __version__
```

Then change the final return in `build()` (line ~132-133) from:

```python
    poller = Poller(memory, detectors, service)  # service IS the event sink
    return create_app(poller, broadcaster, service=service, replay=replay)
```
to:

```python
    poller = Poller(memory, detectors, service)  # service IS the event sink
    updater = UpdateService(current_version=__version__)
    updater.cleanup_old_exe()   # delete a *.old left by a prior self-update
    return create_app(poller, broadcaster, service=service, replay=replay,
                      updater=updater)
```

- [ ] **Step 7: Full suite green**

Run: `uv run pytest -q`
Expected: PASS (existing + new). `test_app.py` still passes (its `make_client` passes no `updater`, so the router stays absent).

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/server/update_api.py src/sm64_events/server/app.py src/sm64_events/main.py tests/test_update_api.py
git commit -m "feat(update-api): /api/update status|apply|skip; wire UpdateService into app+main"
```

---

## Task 9: Update popup UI

**Files:**
- Create: `src/sm64_events/ui/components/update.js`
- Modify: `src/sm64_events/ui/app.js`
- Modify: `src/sm64_events/ui/index.html` (CSS)

No JS unit-test harness exists in this repo; this task is verified live in the browser (Step 5) with `SM64_UPDATE_FAKE`.

- [ ] **Step 1: Create the component**

Create `src/sm64_events/ui/components/update.js`:

```js
// src/sm64_events/ui/components/update.js — auto-update popup.
// Self-contained: polls /api/update/status, shows notes + Update/Skip/Later.
// Notes are GitHub-release markdown rendered by a tiny safe pass (escape first).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function esc(s) {
  return (s || "").replace(/[&<>"]/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}
function inline(s) {
  return s
    .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
    .replace(/\[(.+?)\]\((https?:\/\/[^\s)]+)\)/g,
             '<a href="$2" target="_blank">$1</a>')
    .replace(/(^|[^"(>])(https?:\/\/[^\s<]+)/g,
             '$1<a href="$2" target="_blank">$2</a>');
}
function renderNotes(md) {
  const lines = esc(md).split(/\r?\n/);
  let out = "", inList = false;
  for (const ln of lines) {
    const li = ln.match(/^\s*[-*]\s+(.*)$/);
    if (li) { if (!inList) { out += "<ul>"; inList = true; }
              out += "<li>" + inline(li[1]) + "</li>"; continue; }
    if (inList) { out += "</ul>"; inList = false; }
    const hd = ln.match(/^\s*#{1,6}\s+(.*)$/);
    if (hd) { out += "<b>" + inline(hd[1]) + "</b><br>"; continue; }
    out += ln.trim() === "" ? "<br>" : inline(ln) + "<br>";
  }
  if (inList) out += "</ul>";
  return out;
}

export function UpdatePopup() {
  const [st, setSt] = useState(null);
  const [dismissed, setDismissed] = useState(false);
  const [applying, setApplying] = useState(false);

  const refresh = (force) =>
    getJSON("/api/update/status" + (force ? "?force=1" : ""))
      .then(setSt).catch(() => {});

  useEffect(() => { refresh(false); }, []);
  useEffect(() => {
    if (!applying) return;
    const id = setInterval(() => refresh(false), 700);
    return () => clearInterval(id);
  }, [applying]);

  if (!st || !st.update_available || dismissed) return null;
  if (!applying && st.skipped && st.skipped === st.latest) return null;

  const onUpdate = async () => {
    setApplying(true);
    try { await send("POST", "/api/update/apply"); } catch (e) { /* poll shows error */ }
  };
  const onSkip = async () => {
    try { await send("POST", "/api/update/skip", { version: st.latest }); }
    catch (e) { /* ignore */ }
    setDismissed(true);
  };
  const pct = Math.round((st.progress || 0) * 100);

  return html`
    <div class="modal-backdrop">
      <div class="modal">
        <h2>Update available — v${st.latest}</h2>
        <div class="meta">You're on v${st.current}.</div>
        <div class="update-notes"
             dangerouslySetInnerHTML=${{ __html: renderNotes(st.notes) }}></div>
        <p><a href=${st.html_url} target="_blank">View this release on GitHub →</a></p>
        ${applying
          ? html`
            <div class="meta">${st.state === "error"
              ? "Update failed — keeping the current version. Try again or download from GitHub."
              : "Installing… the app will restart automatically."}</div>
            <div class="progress"><div class="progress-bar"
                 style=${{ width: pct + "%" }}></div></div>`
          : html`
            <div class="modal-actions">
              ${st.writable
                ? html`<button onclick=${onUpdate}>Update now</button>`
                : html`<a class="btnlink" href=${st.html_url}
                          target="_blank">Download from GitHub</a>`}
              <button onclick=${onSkip}>Skip this version</button>
              <button onclick=${() => setDismissed(true)}>Later</button>
            </div>`}
      </div>
    </div>`;
}
```

- [ ] **Step 2: Mount it at the app root**

In `src/sm64_events/ui/app.js`, add the import after the other component imports:

```js
import { UpdatePopup } from "./components/update.js";
```

Then add `<${UpdatePopup} />` just before the closing backtick of the returned template (after the `.pane` `</div>`):

```js
    <div class="pane">
      ${tab === "Practice" ? html`<${Practice} t=${t} />`
        : tab === "Segments" ? html`<${Segments} t=${t} />`
        : tab === "Routes" ? html`<${Routes} t=${t} />`
        : tab === "Run" ? html`<${Run} t=${t} />`
        : html`<${Feed} t=${t} />`}
    </div>
    <${UpdatePopup} />`;
```

- [ ] **Step 3: Add the CSS**

In `src/sm64_events/ui/index.html`, add before the closing `</style>` (line ~125):

```css
  .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.6);
    display: flex; align-items: center; justify-content: center; z-index: 50; }
  .modal { background: #1b1e24; border: 1px solid #3a4150; border-radius: 10px;
    padding: 1rem 1.2rem; max-width: 540px; width: 90%; max-height: 80vh;
    overflow: auto; }
  .modal h2 { font-size: 1.05rem; margin: 0 0 .3rem; color: #ffd75f; }
  .update-notes { background: #14161a; border: 1px solid #2c3140;
    border-radius: 6px; padding: .5rem .7rem; margin: .6rem 0; font-size: .9em;
    line-height: 1.5; }
  .update-notes ul { padding-left: 1.1rem; } .update-notes li { list-style: disc; }
  .modal-actions { display: flex; gap: .5rem; flex-wrap: wrap; margin-top: .6rem; }
  .btnlink { display: inline-block; padding: .15rem .55rem; border: 1px solid #3a4150;
    border-radius: 4px; text-decoration: none; }
  .progress { background: #14161a; border: 1px solid #2c3140; border-radius: 4px;
    height: 10px; overflow: hidden; margin-top: .5rem; }
  .progress-bar { background: #6fa8ff; height: 100%; transition: width .3s; }
```

- [ ] **Step 4: Smoke-check the server starts**

Run: `uv run pytest -q`
Expected: PASS (UI change doesn't touch Python; this confirms nothing else broke).

- [ ] **Step 5: Live browser verification (human harness)**

Run the dev server with the fake-update knob:

```bash
SM64_UPDATE_FAKE=1 uv run python -m sm64_events.main
```
Open `http://127.0.0.1:8065/`. Expect the popup to appear with:
- title "Update available — v9.9.9", "You're on v0.1.0",
- rendered notes (a bold "New:" and a bullet),
- a "View this release on GitHub →" link,
- "Download from GitHub" (writable is forced False in fake mode) + "Skip this version" + "Later".

Click **Later** → popup closes. Reload → it returns. Click **Skip this version** → closes; reload → stays closed (skipped == latest). Delete `data/update_state.json` to reset. Stop the server (CTRL+C).

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/ui/components/update.js src/sm64_events/ui/app.js src/sm64_events/ui/index.html
git commit -m "feat(ui): auto-update popup (notes + Update/Skip/Later), SM64_UPDATE_FAKE dev knob"
```

---

## Task 10: Release tool

**Files:**
- Create: `tools/release.py`
- Test: `tests/test_release.py`

- [ ] **Step 1: Write the failing tests for the pure helpers**

Create `tests/test_release.py`:

```python
import hashlib
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "release", Path(__file__).resolve().parents[1] / "tools" / "release.py")
release = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(release)


def test_bump_version_py_rewrites_constant():
    src = '__version__ = "0.1.0"\n'
    out = release.bump_version_py(src, "1.2.3")
    assert '__version__ = "1.2.3"' in out
    assert "0.1.0" not in out


def test_bump_pyproject_rewrites_project_version():
    src = '[project]\nname = "x"\nversion = "0.1.0"\n'
    out = release.bump_pyproject(src, "1.2.3")
    assert 'version = "1.2.3"' in out


def test_sha256_file(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    assert release.sha256_file(f) == hashlib.sha256(b"hello").hexdigest()


def test_valid_version_accepts_semver():
    assert release.valid_version("1.2.3") is True
    assert release.valid_version("v1.2.3") is False
    assert release.valid_version("1.2") is False
```

- [ ] **Step 2: Run — expect failure**

Run: `uv run pytest tests/test_release.py -q`
Expected: FAIL — `FileNotFoundError`/load error (tools/release.py missing).

- [ ] **Step 3: Create `tools/release.py`**

Create `tools/release.py`:

```python
# tools/release.py
"""One-command release: bump -> tag -> build -> SHA-256 -> publish.

    uv run python tools/release.py 1.1.0 [--notes-file NOTES.md] [--dry-run]

Refuses unless the tree is clean, you're on master, `gh` is authed, and the
full test suite passes. Builds the self-contained exe via tools/build_exe.py
(ffmpeg must be on PATH so it gets bundled), writes a SHA-256 the in-app updater
verifies, then `gh release create` with the exe + checksum. GitHub attaches the
source zip/tar.gz to every release automatically.

Pure helpers (bump_*, sha256_file, valid_version) are unit-tested; the git/gh/
build orchestration is exercised by cutting a real release."""
import argparse
import hashlib
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VERSION_PY = REPO / "src" / "sm64_events" / "core" / "version.py"
PYPROJECT = REPO / "pyproject.toml"
EXE = REPO / "dist" / "sm64_tracker.exe"


def valid_version(v: str) -> bool:
    return bool(re.fullmatch(r"\d+\.\d+\.\d+", v))


def bump_version_py(text: str, new: str) -> str:
    out, n = re.subn(r'__version__\s*=\s*"[^"]+"',
                     f'__version__ = "{new}"', text)
    if n != 1:
        raise ValueError("could not find __version__ in version.py")
    return out


def bump_pyproject(text: str, new: str) -> str:
    out, n = re.subn(r'(?m)^version\s*=\s*"[^"]+"',
                     f'version = "{new}"', text, count=1)
    if n != 1:
        raise ValueError("could not find version in pyproject.toml")
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=REPO, check=True, **kw)


def _capture(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=REPO, check=True,
                          capture_output=True, text=True).stdout.strip()


def _preflight() -> None:
    if _capture(["git", "rev-parse", "--abbrev-ref", "HEAD"]) != "master":
        sys.exit("refusing: not on master")
    if _capture(["git", "status", "--porcelain"]):
        sys.exit("refusing: working tree is dirty")
    try:
        _run(["gh", "auth", "status"], capture_output=True)
    except Exception:
        sys.exit("refusing: `gh` is not authenticated (run `gh auth login`)")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("version", help="new version, e.g. 1.1.0")
    ap.add_argument("--notes-file", help="markdown notes (default: gh auto-notes)")
    ap.add_argument("--dry-run", action="store_true",
                    help="build + checksum but do not commit/tag/push/publish")
    args = ap.parse_args()
    if not valid_version(args.version):
        sys.exit(f"bad version {args.version!r} (want X.Y.Z)")
    tag = f"v{args.version}"

    _preflight()
    _run(["uv", "run", "pytest", "-q"])

    VERSION_PY.write_text(bump_version_py(VERSION_PY.read_text(), args.version))
    PYPROJECT.write_text(bump_pyproject(PYPROJECT.read_text(), args.version))

    # Build first so a broken build aborts BEFORE any tag/push.
    _run(["uv", "run", "python", "tools/build_exe.py"])
    if not EXE.exists():
        sys.exit("build did not produce dist/sm64_tracker.exe")
    digest = sha256_file(EXE)
    sha_path = EXE.with_name(EXE.name + ".sha256")
    sha_path.write_text(f"{digest}  {EXE.name}\n")
    print("sha256", digest)

    if args.dry_run:
        print("dry-run: built + checksummed, skipping commit/tag/publish")
        return 0

    _run(["git", "add", str(VERSION_PY), str(PYPROJECT)])
    _run(["git", "commit", "-m", f"release: {tag}"])
    _run(["git", "tag", tag])
    _run(["git", "push", "origin", "master", "--follow-tags"])

    notes = (["--notes-file", args.notes_file] if args.notes_file
             else ["--generate-notes"])
    _run(["gh", "release", "create", tag, str(EXE), str(sha_path),
          "--title", tag, *notes])
    print(f"\nReleased {tag}. Users see the update popup on next launch.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run — expect pass**

Run: `uv run pytest tests/test_release.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Dry-run the build path (optional, slow — needs ffmpeg on PATH)**

Run: `uv run python tools/release.py 1.0.0 --dry-run`
Expected: tests run, version files bumped (in working tree), exe builds, `dist/sm64_tracker.exe.sha256` written, no commit/tag/push. Then revert the working-tree bump so Task 11 starts clean: `git checkout -- src/sm64_events/core/version.py pyproject.toml`.

- [ ] **Step 6: Commit**

```bash
git add tools/release.py tests/test_release.py
git commit -m "feat(release): one-command tools/release.py (bump/tag/build/sha256/gh publish)"
```

---

## Task 11: Docs

**Files:**
- Modify: `CLAUDE.md` (module map), `README.md`, `docs/architecture.md`

- [ ] **Step 1: Add module-map rows to `CLAUDE.md`**

In the "Module map" table, add these rows (place near the desktop/relaunch rows):

```
| Runtime version constant | `core/version.py` — THE `__version__`; read by the app/build/release tool |
| Self-update (check/download/verify/swap) | `core/updater.py` — pure helpers + `UpdateService`; rename-a-running-exe swap rides `spawn_replacement`; guarded on `is_frozen()`; `SM64_UPDATE_FAKE` dev knob; skip-state in `data/update_state.json` |
| Update REST surface | `server/update_api.py` — `/api/update/status\|apply\|skip`; apply fires the admin restart path |
| Update popup | `ui/components/update.js` — notes + Update/Skip/Later, polls `/api/update/status` for progress |
| One-command release | `tools/release.py` — bump `core/version.py`+pyproject → tag → `build_exe.py` → SHA-256 → `gh release create` |
```

Also add `update_state_path()` to the `core/paths.py` row.

- [ ] **Step 2: Add the API + release surface to `README.md`**

Document the three endpoints (shape of `GET /api/update/status`) and a "Cutting a release" section: `uv run python tools/release.py X.Y.Z` (note ffmpeg must be on PATH; `gh` must be authed).

- [ ] **Step 3: Record the hard-won fact in `docs/architecture.md`**

Add a short "Self-update" subsection: the Windows "you may rename but not delete a running exe" fact, the two-`os.replace` swap, why `sys.executable` then points at the new exe (so `spawn_replacement` needs no change), and the `%LOCALAPPDATA%`/exe split that keeps PBs across an update. Note SmartScreen affects only the first manual (browser) download (exe unsigned; code signing is out of scope).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md docs/architecture.md
git commit -m "docs: module map + API + the running-exe swap fact for auto-update"
```

---

## Task 12: First release (v1.0.0) + live verification

This task runs the real publish and the end-to-end update loop. It needs ffmpeg on PATH and `gh` authed.

- [ ] **Step 1: Full suite green + clean tree**

```bash
uv run pytest -q
git status --porcelain
```
Expected: all pass; tree clean (everything committed).

- [ ] **Step 2: Cut v1.0.0**

```bash
uv run python tools/release.py 1.0.0
```
Expected: tests pass, `core/version.py` + pyproject bumped to 1.0.0 and committed as `release: v1.0.0`, tag `v1.0.0` pushed, exe built, `dist/sm64_tracker.exe.sha256` written, GitHub release `v1.0.0` created with `sm64_tracker.exe` + `.sha256` attached (source zip/tar.gz auto-attached).

- [ ] **Step 3: Verify the release on GitHub**

Open `https://github.com/griffinbeels/SM64-Trainer/releases/tag/v1.0.0`. Confirm the exe asset, the `.sha256` asset, the source downloads, and the notes are present. Download the exe and run it once (expect a SmartScreen "unrecognized app" warning → More info → Run anyway, since it's unsigned).

- [ ] **Step 4: Live-verify the update loop**

Make a trivial visible change (e.g. a one-line note in `README.md`), then:

```bash
uv run python tools/release.py 1.0.1
```
Launch the **installed v1.0.0 exe** (the one in Step 3, not the dev source). On launch the popup should offer **v1.0.1** with notes. Click **Update now** → progress bar → the app restarts onto v1.0.1. Confirm:
- the title bar / `GET /api/update/status` `current` now reads `1.0.1`,
- your PBs / saved replays / DB survived (open the Practice tab),
- `%LOCALAPPDATA%\sm64_tracker` has no leftover `sm64_tracker.exe.old` (cleaned on the new launch).

- [ ] **Step 5: Record the live result**

If anything in Step 4 is off (popup absent, swap fails, PBs lost, `.old` lingers), STOP and debug before declaring done — this is the load-bearing path. Note the live outcome in `docs/architecture.md` Self-update subsection (mirrors the project's "live-verified" discipline).

---

## Self-review (spec coverage)

- **Publish via one-command local script** → Task 10 (`tools/release.py`), Task 12 (run).
- **SHA-256 verification, no code signing** → Task 6 (`download_and_stage` verifies; mismatch raises), Task 10 (writes `.sha256`), Task 11 Step 3 (SmartScreen note).
- **Self-replace running exe (Windows rename trick) + ride restart** → Task 6 (`apply_update`), Task 7 (`_run_apply` → `on_success`), Task 8 (`_restart` glue), Task 12 Step 4 (live).
- **`is_frozen()` guard / dev inert** → Task 7 (`UpdateService` frozen flag), Task 9 Step 5 (fake knob for dev).
- **Popup: notes (from release body) + GitHub link + Skip(persist per version)/Update/Later** → Task 9; skip persistence Task 7 + `data/update_state.json` Task 3.
- **On-launch check + manual force + per-process cache (rate limit)** → Task 7 (`_check` TTL + force), Task 9 (mount on load; `?force=1`).
- **Writable-dir fallback to browser download** → Task 6 (`exe_dir_writable`), Task 7 (status `writable`), Task 9 (Download-from-GitHub branch).
- **First release v1.0.0 with updater inside** → Task 12.
- **Repo: internal_notes/ gitignored, remote, push** → Task 1.
- **Tests for updater/api/release** → Tasks 4–8, 10. **Docs** → Task 11.

Refinement noted up top: progress via status-poll, not a WS event. No placeholders; names are consistent across tasks (`UpdateInfo`, `check_for_update`, `download_and_stage`, `apply_update`, `cleanup_old`, `exe_dir_writable`, `UpdateService.status/skip/begin_apply/_run_apply/_check/cleanup_old_exe`, `create_update_router(updater, restart)`, `bump_version_py`/`bump_pyproject`/`sha256_file`/`valid_version`).
