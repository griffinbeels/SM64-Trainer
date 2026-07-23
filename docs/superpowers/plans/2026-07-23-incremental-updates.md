# Incremental Updates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the 220 MB full-exe self-update with per-file manifest sync: onedir packaging, Range-fetch of only changed files from the release zip, a journaled crash-safe swap, and a bootstrap installer that migrates existing onefile installs.

**Architecture:** A pure contracts module (`core/update_plan.py`) defines the manifest schema and diff planning; `core/update_fetch.py` downloads planned files via coalesced HTTP Range requests (full-zip fallback); `core/update_apply.py` swaps files crash-safely with journal + rollback; `core/updater.py` keeps its UpdateService shell (injectable http, cached check, skip overlay) but drives the new pipeline. Release tooling zips the onedir build and emits the manifest; a tiny stdlib-only bootstrap (`bootstrap/installer.py`), published under the load-bearing asset name `SM64Trainer.exe`, is both the migration vehicle for old clients and the new-user installer.

**Tech Stack:** Python 3.12 stdlib only (urllib, zipfile, zlib, hashlib, tkinter for the bootstrap UI), PyInstaller (onedir + onefile), FastAPI (existing), pytest.

**Spec:** `docs/superpowers/specs/2026-07-23-incremental-updates-design.md`

## Global Constraints

- Python via **uv** only: `uv run pytest -q`, `uv run python …` — never pip.
- All new core modules are stdlib-only; network access ONLY through an injected `http` opener (`urllib.request.urlopen` signature), matching `core/updater.py`'s existing seam. No test touches the network.
- The bootstrap module may import ONLY stdlib + `sm64_events.core.update_plan` (which must stay stdlib-only: json/hashlib/dataclasses/pathlib).
- Asset names are defined ONCE in `core/update_plan.py`: `ZIP_ASSET = "SM64Trainer-full.zip"`, `MANIFEST_ASSET = "manifest.json"`, `BOOTSTRAP_ASSET = "SM64Trainer.exe"`, `INSTALLED_MANIFEST = "installed_manifest.json"`. Every other file imports them.
- `BOOTSTRAP_ASSET` must stay exactly `"SM64Trainer.exe"` — already-shipped updaters can only install an asset with that name (see spec §2/§4).
- Manifest paths are forward-slash relative; reject absolute/`..`/backslash paths on parse (network input).
- Timestamps/UTC rules, updater inert when not frozen (`is_frozen()`), and the `SM64_UPDATE_FAKE=1` dev preview all keep working exactly as today.
- File edits via the Edit tool or `Write` with LF content only — never a Python `open('w')` whole-file rewrite (CRLF churn).
- Commit after every task; run `uv run pytest -q` before each commit.

## Waves (for parallel-worktree execution)

| Wave | Tasks | Parallel? | File ownership |
|---|---|---|---|
| 0 — contracts | 1, 2, 3 | serialized on `feature/incremental-updates` | `core/update_plan.py`, `core/paths.py` |
| 1 — fan-out | 4 / 5 / 6 / 7+8 / 9 | **5 independent tracks** | A: `core/update_fetch.py` · B: `core/update_apply.py` · C: `tools/make_manifest.py` · D: `bootstrap/` + `bootstrap_entry.py` · E: `tools/build_exe.py` |
| 2 — integration | 10, 11, 12, 13, 14 | serialized | `core/updater.py` + tests, `ui/components/update.js`, `main.py`/`desktop/app.py`, `tools/release.py`, docs |

Wave-1 tracks share NOTHING but the frozen Wave-0 contracts. Task 10 is the first to import across tracks.

---

### Task 1: Manifest schema (`core/update_plan.py`, part 1)

**Files:**
- Create: `src/sm64_events/core/update_plan.py`
- Test: `tests/test_update_plan.py`

**Interfaces:**
- Produces (frozen contract for ALL later tasks):
  - constants `SCHEMA = 1`, `ZIP_ASSET`, `MANIFEST_ASSET`, `BOOTSTRAP_ASSET`, `INSTALLED_MANIFEST` (values in Global Constraints)
  - `@dataclass(frozen=True) ManifestEntry(path: str, sha256: str, size: int, zip_offset: int, zip_csize: int, zip_method: int)` — `path` forward-slash relative; `sha256` lowercase hex of the UNCOMPRESSED content; `zip_offset` absolute byte offset of the entry's compressed data in the zip; `zip_method` 0=stored, 8=deflate
  - `@dataclass(frozen=True) Manifest(version: str, files: tuple[ManifestEntry, ...])`
  - `parse_manifest(text: str) -> Manifest` (raises `ValueError` on any malformed/unsafe input)
  - `manifest_to_json(m: Manifest) -> str` (round-trips through `parse_manifest`)
  - `file_sha256(path: Path) -> str`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_update_plan.py
import json

import pytest

from sm64_events.core.update_plan import (BOOTSTRAP_ASSET, INSTALLED_MANIFEST,
                                          MANIFEST_ASSET, ZIP_ASSET, Manifest,
                                          ManifestEntry, file_sha256,
                                          manifest_to_json, parse_manifest)


def _entry(**kw):
    base = dict(path="a/b.txt", sha256="ab" * 32, size=3,
                zip_offset=100, zip_csize=5, zip_method=8)
    base.update(kw)
    return base


def _doc(*entries):
    return json.dumps({"schema": 1, "version": "1.4.0",
                       "files": [_entry(**e) for e in entries]})


def test_asset_names_are_the_registry():
    assert ZIP_ASSET == "SM64Trainer-full.zip"
    assert MANIFEST_ASSET == "manifest.json"
    assert BOOTSTRAP_ASSET == "SM64Trainer.exe"   # load-bearing: old updaters
    assert INSTALLED_MANIFEST == "installed_manifest.json"


def test_parse_manifest_happy_path():
    m = parse_manifest(_doc({}))
    assert m.version == "1.4.0"
    assert m.files == (ManifestEntry(path="a/b.txt", sha256="ab" * 32, size=3,
                                     zip_offset=100, zip_csize=5, zip_method=8),)


def test_parse_manifest_lowercases_sha():
    m = parse_manifest(_doc({"sha256": "AB" * 32}))
    assert m.files[0].sha256 == "ab" * 32


def test_parse_manifest_rejects_bad_schema():
    with pytest.raises(ValueError):
        parse_manifest(json.dumps({"schema": 99, "version": "1", "files": []}))


def test_parse_manifest_rejects_non_json():
    with pytest.raises(ValueError):
        parse_manifest("not json {")


@pytest.mark.parametrize("bad", ["../x", "/abs", "a\\b", "a//b", "", "a/../b"])
def test_parse_manifest_rejects_unsafe_paths(bad):
    with pytest.raises(ValueError):
        parse_manifest(_doc({"path": bad}))


def test_parse_manifest_rejects_missing_field():
    doc = json.loads(_doc({}))
    del doc["files"][0]["sha256"]
    with pytest.raises(ValueError):
        parse_manifest(json.dumps(doc))


def test_manifest_json_round_trips():
    m = parse_manifest(_doc({}, {"path": "c.dll", "zip_offset": 500}))
    assert parse_manifest(manifest_to_json(m)) == m


def test_file_sha256_streams(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello")
    import hashlib
    assert file_sha256(p) == hashlib.sha256(b"hello").hexdigest()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_plan.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.update_plan'`

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/core/update_plan.py
"""Manifest schema + update planning (pure, stdlib-only).

THE registry for release asset names and the per-file manifest format that
release tooling (tools/make_manifest.py) writes and the updater + bootstrap
consume. A manifest lists every installed file with the SHA-256 of its
UNCOMPRESSED content plus the byte range of its compressed data inside the
release zip — that byte range is what lets core/update_fetch.py download
only changed files via HTTP Range requests.

Manifests arrive over the network, so parse_manifest() is defensive: any
missing/mistyped field or unsafe path (absolute, ``..``, backslash, empty
segment) raises ValueError — a hostile manifest must never be able to write
outside the install root.

Stays stdlib-only (json/hashlib/dataclasses/pathlib): the bootstrap
installer imports this module and must remain a tiny PyInstaller build."""
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = 1
ZIP_ASSET = "SM64Trainer-full.zip"
MANIFEST_ASSET = "manifest.json"
# Old shipped updaters can ONLY install an asset with exactly this name —
# every release publishes the bootstrap installer under it, forever.
BOOTSTRAP_ASSET = "SM64Trainer.exe"
INSTALLED_MANIFEST = "installed_manifest.json"


@dataclass(frozen=True)
class ManifestEntry:
    path: str          # forward-slash relative path under the install root
    sha256: str        # lowercase hex of the UNCOMPRESSED file content
    size: int          # uncompressed bytes
    zip_offset: int    # absolute offset of the compressed data in the zip
    zip_csize: int     # compressed size in bytes
    zip_method: int    # 0 = stored, 8 = deflate


@dataclass(frozen=True)
class Manifest:
    version: str
    files: tuple[ManifestEntry, ...]


def _safe_path(path: str) -> bool:
    if not path or "\\" in path or path.startswith("/"):
        return False
    return all(seg not in ("", ".", "..") for seg in path.split("/"))


def parse_manifest(text: str) -> Manifest:
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as err:
        raise ValueError(f"manifest is not valid JSON: {err}") from err
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise ValueError(f"unsupported manifest schema: {doc.get('schema')!r}"
                         if isinstance(doc, dict) else "manifest is not an object")
    entries: list[ManifestEntry] = []
    for raw in doc.get("files", []):
        try:
            entry = ManifestEntry(
                path=str(raw["path"]),
                sha256=str(raw["sha256"]).lower(),
                size=int(raw["size"]),
                zip_offset=int(raw["zip_offset"]),
                zip_csize=int(raw["zip_csize"]),
                zip_method=int(raw["zip_method"]))
        except (KeyError, TypeError, ValueError) as err:
            raise ValueError(f"bad manifest entry: {raw!r}") from err
        if not _safe_path(entry.path):
            raise ValueError(f"unsafe manifest path: {entry.path!r}")
        entries.append(entry)
    return Manifest(version=str(doc.get("version", "")), files=tuple(entries))


def manifest_to_json(m: Manifest) -> str:
    return json.dumps({"schema": SCHEMA, "version": m.version,
                       "files": [asdict(entry) for entry in m.files]}, indent=1)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_plan.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/update_plan.py tests/test_update_plan.py
git commit -m "feat(update): manifest schema + asset-name registry (update_plan contracts)"
```

---

### Task 2: Update planning diff (`core/update_plan.py`, part 2)

**Files:**
- Modify: `src/sm64_events/core/update_plan.py` (append)
- Test: `tests/test_update_plan.py` (append)

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) UpdatePlan(fetch: tuple[ManifestEntry, ...], delete: tuple[str, ...], download_bytes: int)`
  - `build_plan(remote: Manifest, installed: Manifest | None, root: Path, *, verify_local: bool = False, file_hash=file_sha256) -> UpdatePlan`
- Semantics later tasks rely on: `fetch` = remote entries that are new, hash-changed vs the installed record, missing on disk, size-mismatched on disk, or (only when `verify_local=True`) content-hash-mismatched on disk. `delete` = sorted paths in `installed` but not in `remote`. `download_bytes = sum(zip_csize of fetch)`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_update_plan.py`)

```python
from pathlib import Path

from sm64_events.core.update_plan import UpdatePlan, build_plan


def _mk(path, sha, size=3, off=0, csize=10):
    return ManifestEntry(path=path, sha256=sha, size=size,
                         zip_offset=off, zip_csize=csize, zip_method=8)


def _tree(tmp_path, files):
    for rel, content in files.items():
        p = tmp_path.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return tmp_path


def test_build_plan_fresh_install_fetches_everything(tmp_path):
    remote = Manifest("2.0.0", (_mk("a.txt", "aa" * 32, csize=7),
                                _mk("b/c.dll", "bb" * 32, csize=9)))
    plan = build_plan(remote, None, tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt", "b/c.dll"]
    assert plan.delete == ()
    assert plan.download_bytes == 16


def test_build_plan_unchanged_files_skipped(tmp_path):
    import hashlib
    content = b"abc"
    sha = hashlib.sha256(content).hexdigest()
    _tree(tmp_path, {"a.txt": content})
    entry = _mk("a.txt", sha, size=3)
    remote = Manifest("2.0.0", (entry,))
    installed = Manifest("1.0.0", (entry,))
    plan = build_plan(remote, installed, tmp_path)
    assert plan.fetch == () and plan.download_bytes == 0


def test_build_plan_hash_change_fetches(tmp_path):
    _tree(tmp_path, {"a.txt": b"abc"})
    old = _mk("a.txt", "aa" * 32, size=3)
    new = _mk("a.txt", "bb" * 32, size=3)
    plan = build_plan(Manifest("2", (new,)), Manifest("1", (old,)), tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt"]


def test_build_plan_missing_or_wrong_size_refetches(tmp_path):
    import hashlib
    sha = hashlib.sha256(b"abc").hexdigest()
    entry = _mk("a.txt", sha, size=3)
    installed = Manifest("1", (entry,))
    # missing on disk
    plan = build_plan(Manifest("2", (entry,)), installed, tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt"]
    # wrong size on disk (truncated)
    _tree(tmp_path, {"a.txt": b"ab"})
    plan = build_plan(Manifest("2", (entry,)), installed, tmp_path)
    assert [e.path for e in plan.fetch] == ["a.txt"]


def test_build_plan_verify_local_catches_silent_corruption(tmp_path):
    import hashlib
    sha = hashlib.sha256(b"abc").hexdigest()
    _tree(tmp_path, {"a.txt": b"abX"})   # same size, different bytes
    entry = _mk("a.txt", sha, size=3)
    installed = Manifest("1", (entry,))
    fast = build_plan(Manifest("2", (entry,)), installed, tmp_path)
    assert fast.fetch == ()              # fast path trusts size+record
    slow = build_plan(Manifest("2", (entry,)), installed, tmp_path,
                      verify_local=True)
    assert [e.path for e in slow.fetch] == ["a.txt"]   # self-heal


def test_build_plan_deletes_removed_files(tmp_path):
    import hashlib
    sha = hashlib.sha256(b"abc").hexdigest()
    _tree(tmp_path, {"gone.dll": b"abc", "kept.txt": b"abc"})
    kept = _mk("kept.txt", sha, size=3)
    gone = _mk("gone.dll", sha, size=3)
    plan = build_plan(Manifest("2", (kept,)), Manifest("1", (kept, gone)),
                      tmp_path)
    assert plan.delete == ("gone.dll",)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_plan.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_plan'`

- [ ] **Step 3: Write the implementation** (append to `src/sm64_events/core/update_plan.py`)

```python
@dataclass(frozen=True)
class UpdatePlan:
    fetch: tuple[ManifestEntry, ...]   # download these (new/changed/corrupt)
    delete: tuple[str, ...]            # remove these (no longer shipped)
    download_bytes: int                # sum of zip_csize over fetch


def build_plan(remote: Manifest, installed: "Manifest | None", root: Path, *,
               verify_local: bool = False,
               file_hash=file_sha256) -> UpdatePlan:
    """Diff the remote manifest against the installed record + the disk.

    verify_local=True re-hashes every supposedly-unchanged file (a forced
    'Check for updates' passes it) so silent same-size corruption self-heals;
    the routine hourly check uses the cheap existence+size path."""
    installed_files = {e.path: e for e in (installed.files if installed else ())}
    fetch: list[ManifestEntry] = []
    for entry in remote.files:
        local = root.joinpath(*entry.path.split("/"))
        recorded = installed_files.get(entry.path)
        stale = (recorded is None
                 or recorded.sha256 != entry.sha256
                 or not local.is_file()
                 or local.stat().st_size != entry.size
                 or (verify_local and file_hash(local) != entry.sha256))
        if stale:
            fetch.append(entry)
    remote_paths = {e.path for e in remote.files}
    delete = tuple(sorted(p for p in installed_files if p not in remote_paths))
    return UpdatePlan(fetch=tuple(fetch), delete=delete,
                      download_bytes=sum(e.zip_csize for e in fetch))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_plan.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/update_plan.py tests/test_update_plan.py
git commit -m "feat(update): build_plan manifest diff with lazy local verification"
```

---

### Task 3: `install_root()` in `core/paths.py`

**Files:**
- Modify: `src/sm64_events/core/paths.py` (append at end)
- Test: `tests/test_paths.py` (append; create if missing)

**Interfaces:**
- Produces: `install_root() -> Path` — the directory containing the running exe (`Path(sys.executable).resolve().parent`). Only meaningful when frozen; callers gate on `is_frozen()`.

- [ ] **Step 1: Write the failing test** (append to `tests/test_paths.py`, or create the file with just this if it doesn't exist)

```python
import sys
from pathlib import Path

from sm64_events.core.paths import install_root


def test_install_root_is_exe_parent():
    assert install_root() == Path(sys.executable).resolve().parent
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py -q`
Expected: FAIL — `ImportError: cannot import name 'install_root'`

- [ ] **Step 3: Write the implementation** (append to `src/sm64_events/core/paths.py`)

```python
def install_root() -> Path:
    """Directory containing the running executable — the onedir install root
    when frozen (SM64Trainer.exe + _internal\\ live here). The updater and the
    startup update-repair operate relative to THIS, never a hardcoded install
    location, so a user who moves the folder keeps working updates."""
    return Path(sys.executable).resolve().parent
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/paths.py tests/test_paths.py
git commit -m "feat(paths): install_root() for the onedir install directory"
```

---

### Task 4 (Wave 1, Track A): Ranged fetch (`core/update_fetch.py`)

**Files:**
- Create: `src/sm64_events/core/update_fetch.py`
- Test: `tests/test_update_fetch.py`

**Interfaces:**
- Consumes: `ManifestEntry`, `UpdatePlan` from `core/update_plan.py`.
- Produces:
  - `class RangeUnsupported(RuntimeError)`
  - `@dataclass(frozen=True) Span(offset: int, length: int, entries: tuple[ManifestEntry, ...])`
  - `coalesce(entries, *, gap: int = 65536, max_span: int = 33554432) -> tuple[Span, ...]`
  - `decode_entry(entry: ManifestEntry, comp: bytes) -> bytes` (raises `ValueError` on size/hash mismatch)
  - `fetch_plan(plan: UpdatePlan, zip_url: str, staging: Path, *, http, progress=None) -> None` (raises `RangeUnsupported` → caller falls back)
  - `fetch_full_zip(plan: UpdatePlan, zip_url: str, zip_sha256: str, staging: Path, *, http, progress=None) -> None`
  - `free_disk_ok(path: Path, needed_bytes: int, *, margin: int = 52428800) -> bool`
- `http` opener contract: called with a `urllib.request.Request`; the response context-manager has `.read()`, `.headers`, and `.status` (206 expected for ranged GETs).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_update_fetch.py
import hashlib
import io
import zlib
from pathlib import Path

import pytest

from sm64_events.core.update_plan import Manifest, ManifestEntry, UpdatePlan
from sm64_events.core.update_fetch import (RangeUnsupported, Span, coalesce,
                                           decode_entry, fetch_full_zip,
                                           fetch_plan, free_disk_ok)


def _deflate(data: bytes) -> bytes:
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def _entry(path, data, offset, method=8):
    comp = _deflate(data) if method == 8 else data
    return ManifestEntry(path=path, sha256=hashlib.sha256(data).hexdigest(),
                         size=len(data), zip_offset=offset,
                         zip_csize=len(comp), zip_method=method), comp


class _Resp(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _range_http(blob: bytes, *, honor_range=True, log=None):
    def opener(req):
        rng = req.headers.get("Range")
        if log is not None:
            log.append(rng)
        if rng and honor_range:
            lo, hi = rng.removeprefix("bytes=").split("-")
            return _Resp(blob[int(lo):int(hi) + 1], status=206)
        return _Resp(blob, status=200)
    return opener


# --- coalesce ---

def _span_entry(offset, csize):
    return ManifestEntry(path=f"f{offset}", sha256="00" * 32, size=csize,
                         zip_offset=offset, zip_csize=csize, zip_method=0)


def test_coalesce_merges_adjacent_and_near_entries():
    a, b, c = _span_entry(0, 100), _span_entry(100, 50), _span_entry(200, 10)
    spans = coalesce([a, b, c], gap=64)
    assert len(spans) == 1          # 150->200 gap of 50 <= 64: all merge
    assert spans[0].offset == 0 and spans[0].length == 210
    assert spans[0].entries == (a, b, c)


def test_coalesce_splits_on_large_gap():
    a, b = _span_entry(0, 10), _span_entry(1_000_000, 10)
    spans = coalesce([a, b], gap=64)
    assert len(spans) == 2


def test_coalesce_respects_max_span():
    a, b = _span_entry(0, 100), _span_entry(100, 100)
    spans = coalesce([a, b], gap=64, max_span=150)
    assert len(spans) == 2


def test_coalesce_sorts_by_offset():
    a, b = _span_entry(500, 10), _span_entry(0, 10)
    spans = coalesce([a, b], gap=1000)
    assert spans[0].entries[0].zip_offset == 0


# --- decode_entry ---

def test_decode_entry_inflates_and_verifies():
    entry, comp = _entry("x", b"hello world", 0)
    assert decode_entry(entry, comp) == b"hello world"


def test_decode_entry_stored_passthrough():
    entry, comp = _entry("x", b"hello", 0, method=0)
    assert decode_entry(entry, comp) == b"hello"


def test_decode_entry_rejects_bad_hash():
    entry, _ = _entry("x", b"hello", 0)
    other = _deflate(b"HELLO")
    good = ManifestEntry(path=entry.path, sha256=entry.sha256, size=5,
                         zip_offset=0, zip_csize=len(other), zip_method=8)
    with pytest.raises(ValueError):
        decode_entry(good, other)


def test_decode_entry_rejects_unknown_method():
    entry = ManifestEntry(path="x", sha256="00" * 32, size=1,
                          zip_offset=0, zip_csize=1, zip_method=12)
    with pytest.raises(ValueError):
        decode_entry(entry, b"x")


# --- fetch_plan ---

def _fake_zip(entries_data):
    """entries_data: [(path, bytes)] -> (blob, [ManifestEntry]) with real offsets."""
    blob = b""
    entries = []
    for path, data in entries_data:
        entry, comp = _entry(path, data, offset=len(blob))
        blob += comp
        entries.append(entry)
    return blob, entries


def test_fetch_plan_stages_verified_files(tmp_path):
    blob, entries = _fake_zip([("a.txt", b"AAA"), ("sub/b.bin", b"B" * 100)])
    plan = UpdatePlan(fetch=tuple(entries), delete=(),
                      download_bytes=sum(e.zip_csize for e in entries))
    seen = []
    fetch_plan(plan, "https://dl/z.zip", tmp_path / "st",
               http=_range_http(blob), progress=seen.append)
    assert (tmp_path / "st" / "a.txt").read_bytes() == b"AAA"
    assert (tmp_path / "st" / "sub" / "b.bin").read_bytes() == b"B" * 100
    assert seen and seen[-1] == 1.0


def test_fetch_plan_uses_coalesced_ranges(tmp_path):
    blob, entries = _fake_zip([("a", b"A" * 10), ("b", b"B" * 10)])
    log = []
    plan = UpdatePlan(fetch=tuple(entries), delete=(),
                      download_bytes=sum(e.zip_csize for e in entries))
    fetch_plan(plan, "https://dl/z.zip", tmp_path / "st",
               http=_range_http(blob, log=log))
    assert len(log) == 1 and log[0].startswith("bytes=")


def test_fetch_plan_raises_when_range_ignored(tmp_path):
    blob, entries = _fake_zip([("a", b"AAA")])
    plan = UpdatePlan(fetch=tuple(entries), delete=(), download_bytes=1)
    with pytest.raises(RangeUnsupported):
        fetch_plan(plan, "https://dl/z.zip", tmp_path / "st",
                   http=_range_http(blob, honor_range=False))


# --- fetch_full_zip ---

def _real_zip(tmp_path, files):
    import zipfile
    zp = tmp_path / "full.zip"
    with zipfile.ZipFile(zp, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return zp.read_bytes()


def test_fetch_full_zip_extracts_planned_files(tmp_path):
    blob = _real_zip(tmp_path, {"a.txt": b"AAA", "b.txt": b"BBB"})
    sha = hashlib.sha256(blob).hexdigest()
    entry = ManifestEntry(path="a.txt",
                          sha256=hashlib.sha256(b"AAA").hexdigest(), size=3,
                          zip_offset=0, zip_csize=0, zip_method=8)
    plan = UpdatePlan(fetch=(entry,), delete=(), download_bytes=0)
    fetch_full_zip(plan, "https://dl/z.zip", sha, tmp_path / "st",
                   http=_range_http(blob, honor_range=False))
    assert (tmp_path / "st" / "a.txt").read_bytes() == b"AAA"
    assert not (tmp_path / "st" / "b.txt").exists()   # only planned files
    assert not (tmp_path / "st" / "_full.zip").exists()


def test_fetch_full_zip_rejects_bad_zip_hash(tmp_path):
    blob = _real_zip(tmp_path, {"a.txt": b"AAA"})
    entry = ManifestEntry(path="a.txt",
                          sha256=hashlib.sha256(b"AAA").hexdigest(), size=3,
                          zip_offset=0, zip_csize=0, zip_method=8)
    plan = UpdatePlan(fetch=(entry,), delete=(), download_bytes=0)
    with pytest.raises(ValueError):
        fetch_full_zip(plan, "https://dl/z.zip", "0" * 64, tmp_path / "st",
                       http=_range_http(blob, honor_range=False))


def test_free_disk_ok(tmp_path):
    assert free_disk_ok(tmp_path, 0) is True
    assert free_disk_ok(tmp_path, 1 << 62) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_fetch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.update_fetch'`

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/core/update_fetch.py
"""Download ONLY the planned files from the release zip and stage them.

GitHub release assets redirect to a CDN that honors HTTP Range requests
(the same mechanism electron-updater's differential downloads rely on), so
each changed file's compressed bytes are fetched straight out of
SM64Trainer-full.zip without downloading the rest. Adjacent entries are
coalesced into one request (many tiny ranged GETs are slower than one big
one — a known differential-download gotcha); anything that breaks Range
(proxy, redirect weirdness) raises RangeUnsupported and the caller falls
back to fetch_full_zip, which streams the whole asset and extracts the
planned files. BOTH paths verify every staged file's SHA-256 against the
manifest before it is ever eligible to be applied."""
import hashlib
import shutil
import urllib.request
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path

from sm64_events.core.update_plan import ManifestEntry, UpdatePlan

_UA = "SM64Trainer-updater"


class RangeUnsupported(RuntimeError):
    """The server ignored/mangled a Range request — use the full-zip fallback."""


@dataclass(frozen=True)
class Span:
    offset: int
    length: int
    entries: tuple[ManifestEntry, ...]


def _span(group: list[ManifestEntry]) -> Span:
    start = group[0].zip_offset
    end = group[-1].zip_offset + group[-1].zip_csize
    return Span(offset=start, length=end - start, entries=tuple(group))


def coalesce(entries, *, gap: int = 64 * 1024,
             max_span: int = 32 * 1024 * 1024) -> tuple[Span, ...]:
    """Merge byte ranges that are within `gap` of each other (paying the gap
    bytes beats another round-trip) without letting one request exceed
    `max_span`."""
    ordered = sorted(entries, key=lambda e: e.zip_offset)
    spans: list[Span] = []
    group: list[ManifestEntry] = []
    for entry in ordered:
        if group:
            end = group[-1].zip_offset + group[-1].zip_csize
            joined = entry.zip_offset + entry.zip_csize - group[0].zip_offset
            if entry.zip_offset - end <= gap and joined <= max_span:
                group.append(entry)
                continue
            spans.append(_span(group))
        group = [entry]
    if group:
        spans.append(_span(group))
    return tuple(spans)


def _ranged_get(http, url: str, offset: int, length: int) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": _UA,
        "Range": f"bytes={offset}-{offset + length - 1}"})
    with http(req) as resp:
        status = getattr(resp, "status", 206)
        if status != 206:
            raise RangeUnsupported(f"expected 206 partial content, got {status}")
        data = resp.read()
    if len(data) != length:
        raise RangeUnsupported(f"range returned {len(data)} bytes, "
                               f"wanted {length}")
    return data


def decode_entry(entry: ManifestEntry, comp: bytes) -> bytes:
    """Raw zip-entry bytes -> verified file content (ValueError on mismatch)."""
    if entry.zip_method == 0:
        data = comp
    elif entry.zip_method == 8:
        inflater = zlib.decompressobj(-15)     # raw deflate, no zlib header
        data = inflater.decompress(comp) + inflater.flush()
    else:
        raise ValueError(f"{entry.path}: unsupported zip method "
                         f"{entry.zip_method}")
    if len(data) != entry.size:
        raise ValueError(f"{entry.path}: inflated to {len(data)} bytes, "
                         f"manifest says {entry.size}")
    if hashlib.sha256(data).hexdigest() != entry.sha256:
        raise ValueError(f"{entry.path}: checksum mismatch after download")
    return data


def _stage(staging: Path, entry: ManifestEntry, data: bytes) -> None:
    dest = staging.joinpath(*entry.path.split("/"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)


def fetch_plan(plan: UpdatePlan, zip_url: str, staging: Path, *, http,
               progress=None) -> None:
    """Range-fetch every planned file into `staging` (tree mirrors install).
    Raises RangeUnsupported (fall back to fetch_full_zip) or ValueError
    (integrity failure — abort)."""
    total = max(1, plan.download_bytes)
    done = 0
    for span in coalesce(plan.fetch):
        body = _ranged_get(http, zip_url, span.offset, span.length)
        for entry in span.entries:
            start = entry.zip_offset - span.offset
            comp = body[start:start + entry.zip_csize]
            _stage(staging, entry, decode_entry(entry, comp))
            done += entry.zip_csize
            if progress:
                progress(min(1.0, done / total))


def fetch_full_zip(plan: UpdatePlan, zip_url: str, zip_sha256: str,
                   staging: Path, *, http, progress=None) -> None:
    """Fallback: stream the whole zip, verify its digest, extract the planned
    files (each still verified against its manifest hash)."""
    staging.mkdir(parents=True, exist_ok=True)
    tmp = staging / "_full.zip"
    digest = hashlib.sha256()
    req = urllib.request.Request(zip_url, headers={"User-Agent": _UA})
    try:
        with http(req) as resp:
            total = int((resp.headers or {}).get("Content-Length") or 0)
            done = 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = resp.read(1 << 16)
                    if not chunk:
                        break
                    out.write(chunk)
                    digest.update(chunk)
                    done += len(chunk)
                    if progress and total:
                        progress(min(1.0, done / total))
        if digest.hexdigest() != zip_sha256.lower():
            raise ValueError("full zip checksum mismatch")
        with zipfile.ZipFile(tmp) as zf:
            for entry in plan.fetch:
                data = zf.read(entry.path)
                if (len(data) != entry.size
                        or hashlib.sha256(data).hexdigest() != entry.sha256):
                    raise ValueError(f"{entry.path}: zip content does not "
                                     "match manifest")
                _stage(staging, entry, data)
    finally:
        tmp.unlink(missing_ok=True)


def free_disk_ok(path: Path, needed_bytes: int, *,
                 margin: int = 50 * 1024 * 1024) -> bool:
    return shutil.disk_usage(path).free >= needed_bytes + margin
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_fetch.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/update_fetch.py tests/test_update_fetch.py
git commit -m "feat(update): ranged fetch with coalescing, verification, full-zip fallback"
```

---

### Task 5 (Wave 1, Track B): Journaled swap (`core/update_apply.py`)

**Files:**
- Create: `src/sm64_events/core/update_apply.py`
- Test: `tests/test_update_apply.py`

**Interfaces:**
- Consumes: nothing beyond stdlib (operates on relative-path lists; deliberately independent of Manifest types so it stays a pure file-tree machine).
- Produces:
  - constants `BACKUP_DIR = ".update_backup"`, `STAGING_DIR = ".update_staging"`, `JOURNAL_NAME = "update_journal.json"`
  - `apply_plan(install_root: Path, staging: Path, *, replace: list[str], delete: list[str], os_replace=os.replace, retries: int = 5, sleep=time.sleep) -> None` — atomically-intentioned swap; on ANY failure rolls back everything it did, then re-raises.
  - `read_journal(install_root: Path) -> dict | None`
  - `startup_repair(install_root: Path, *, os_replace=os.replace) -> str` — returns `"none" | "rolled_back" | "cleaned"`; `"rolled_back"` means the caller must relaunch once.
  - `sweep_backup(install_root: Path, *, attempts: int = 60, sleep=time.sleep) -> bool` — removes `.update_backup` + a done/rolled_back journal with retries (old process may briefly lock its own exe/DLLs).
- Journal format (written BEFORE any file op): `{"state": "applying"|"done"|"rolled_back", "replace": [...], "delete": [...], "added": [...]}` where `added` = replace-paths that had no pre-existing live file (rollback deletes them instead of restoring a backup).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_update_apply.py
import json
import os
from pathlib import Path

import pytest

from sm64_events.core.update_apply import (BACKUP_DIR, JOURNAL_NAME,
                                           apply_plan, read_journal,
                                           startup_repair, sweep_backup)


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, content in files.items():
        p = root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return root


def _setup(tmp_path):
    install = _tree(tmp_path / "app", {
        "SM64Trainer.exe": b"OLD-EXE",
        "_internal/lib.dll": b"OLD-DLL",
        "_internal/gone.pyd": b"REMOVE-ME"})
    staging = _tree(tmp_path / "stage", {
        "SM64Trainer.exe": b"NEW-EXE",
        "_internal/newfile.dat": b"BRAND-NEW"})
    return install, staging


def test_apply_plan_swaps_deletes_and_adds(tmp_path):
    install, staging = _setup(tmp_path)
    apply_plan(install, staging,
               replace=["SM64Trainer.exe", "_internal/newfile.dat"],
               delete=["_internal/gone.pyd"])
    assert (install / "SM64Trainer.exe").read_bytes() == b"NEW-EXE"
    assert (install / "_internal/newfile.dat").read_bytes() == b"BRAND-NEW"
    assert not (install / "_internal/gone.pyd").exists()
    assert (install / "_internal/lib.dll").read_bytes() == b"OLD-DLL"
    # originals preserved in the backup tree; journal marked done
    assert (install / BACKUP_DIR / "SM64Trainer.exe").read_bytes() == b"OLD-EXE"
    assert (install / BACKUP_DIR / "_internal/gone.pyd").exists()
    assert read_journal(install)["state"] == "done"


def test_apply_plan_rolls_back_on_failure(tmp_path):
    install, staging = _setup(tmp_path)
    real = os.replace
    calls = []

    def flaky(src, dst):
        calls.append(str(src))
        # fail when placing the SECOND staged file
        if "newfile" in str(src):
            raise PermissionError("locked")
        return real(src, dst)

    with pytest.raises(PermissionError):
        apply_plan(install, staging,
                   replace=["SM64Trainer.exe", "_internal/newfile.dat"],
                   delete=["_internal/gone.pyd"],
                   os_replace=flaky, retries=2, sleep=lambda s: None)
    # everything restored
    assert (install / "SM64Trainer.exe").read_bytes() == b"OLD-EXE"
    assert (install / "_internal/gone.pyd").read_bytes() == b"REMOVE-ME"
    assert not (install / "_internal/newfile.dat").exists()


def test_apply_plan_retries_locked_files(tmp_path):
    install, staging = _setup(tmp_path)
    real = os.replace
    fails = {"n": 0}

    def flaky_once(src, dst):
        if "NEW-EXE" == Path(src).read_bytes().decode(errors="ignore") \
                and fails["n"] == 0 and str(src).endswith("SM64Trainer.exe"):
            fails["n"] = 1
            raise PermissionError("AV lock")
        return real(src, dst)

    apply_plan(install, staging, replace=["SM64Trainer.exe"], delete=[],
               os_replace=flaky_once, retries=3, sleep=lambda s: None)
    assert (install / "SM64Trainer.exe").read_bytes() == b"NEW-EXE"


def test_startup_repair_none_without_journal(tmp_path):
    install, _ = _setup(tmp_path)
    assert startup_repair(install) == "none"


def test_startup_repair_rolls_back_interrupted_apply(tmp_path):
    """Simulate a crash mid-swap: journal says applying, exe already swapped,
    delete backed up, the add was never placed."""
    install, staging = _setup(tmp_path)
    backup = install / BACKUP_DIR
    _tree(install, {})
    # manual mid-state: exe swapped (old in backup), gone.pyd backed up
    (backup / "_internal").mkdir(parents=True)
    os.replace(install / "SM64Trainer.exe", backup / "SM64Trainer.exe")
    (install / "SM64Trainer.exe").write_bytes(b"NEW-EXE")
    os.replace(install / "_internal/gone.pyd", backup / "_internal/gone.pyd")
    (install / JOURNAL_NAME).write_text(json.dumps({
        "state": "applying",
        "replace": ["SM64Trainer.exe", "_internal/newfile.dat"],
        "delete": ["_internal/gone.pyd"],
        "added": ["_internal/newfile.dat"]}))
    assert startup_repair(install) == "rolled_back"
    assert (install / "SM64Trainer.exe").read_bytes() == b"OLD-EXE"
    assert (install / "_internal/gone.pyd").read_bytes() == b"REMOVE-ME"
    assert read_journal(install)["state"] == "rolled_back"
    # repair is terminal: a second call cleans, never re-rolls
    assert startup_repair(install) == "cleaned"


def test_startup_repair_removes_placed_adds(tmp_path):
    install, _ = _setup(tmp_path)
    (install / "_internal/newfile.dat").write_bytes(b"HALF-PLACED")
    (install / JOURNAL_NAME).write_text(json.dumps({
        "state": "applying", "replace": ["_internal/newfile.dat"],
        "delete": [], "added": ["_internal/newfile.dat"]}))
    assert startup_repair(install) == "rolled_back"
    assert not (install / "_internal/newfile.dat").exists()


def test_sweep_backup_removes_backup_and_done_journal(tmp_path):
    install, staging = _setup(tmp_path)
    apply_plan(install, staging, replace=["SM64Trainer.exe"], delete=[])
    assert sweep_backup(install, attempts=1) is True
    assert not (install / BACKUP_DIR).exists()
    assert read_journal(install) is None


def test_sweep_backup_never_touches_an_applying_journal(tmp_path):
    install, _ = _setup(tmp_path)
    (install / JOURNAL_NAME).write_text(json.dumps({
        "state": "applying", "replace": [], "delete": [], "added": []}))
    assert sweep_backup(install, attempts=1) is False
    assert read_journal(install)["state"] == "applying"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_update_apply.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.core.update_apply'`

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/core/update_apply.py
"""Crash-safe application of a staged update to a live onedir install.

Windows forbids DELETING a running exe or a loaded DLL but ALLOWS renaming
them — the same trick the old single-exe updater used, generalized to N
files: every replaced/deleted file is RENAMED into .update_backup\\ (never
deleted) before its staged replacement moves in, and a journal written
BEFORE the first file op records exactly what was planned. Any failure —
including a hard crash mid-swap — is recoverable: rollback restores every
backup and removes half-placed additions, driven purely by the journal +
what exists in the backup tree, so it is idempotent and safe to re-run.

startup_repair() runs at every launch BEFORE anything else loads: an
'applying' journal means a swap was interrupted — roll it back and tell the
caller to relaunch once (the relaunch runs the restored code; the journal
flips to 'rolled_back' first so a crash loop is impossible). The backup
tree + finished journal are swept in the background after a successful
start (the OLD process may still hold its exe/DLLs for a moment — bounded
retries, like the old cleanup_old)."""
import json
import logging
import os
import shutil
import time
from pathlib import Path

log = logging.getLogger("sm64.updater")

BACKUP_DIR = ".update_backup"
STAGING_DIR = ".update_staging"
JOURNAL_NAME = "update_journal.json"


def _local(root: Path, rel: str) -> Path:
    return root.joinpath(*rel.split("/"))


def _write_journal(root: Path, doc: dict) -> None:
    (root / JOURNAL_NAME).write_text(json.dumps(doc))


def read_journal(root: Path) -> "dict | None":
    try:
        return json.loads((root / JOURNAL_NAME).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def _replace_retry(src: Path, dst: Path, *, os_replace, retries, sleep) -> None:
    for attempt in range(retries):
        try:
            os_replace(src, dst)
            return
        except PermissionError:
            if attempt == retries - 1:
                raise
            sleep(0.5)


def _rollback(root: Path, journal: dict, *, os_replace=os.replace) -> None:
    """Idempotent: restores whatever backups exist, removes half-placed adds."""
    backup = root / BACKUP_DIR
    for rel in journal.get("added", []):
        _local(root, rel).unlink(missing_ok=True)
    for rel in list(journal.get("replace", [])) + list(journal.get("delete", [])):
        bak = _local(backup, rel)
        if bak.exists():
            live = _local(root, rel)
            live.parent.mkdir(parents=True, exist_ok=True)
            try:
                os_replace(bak, live)
            except OSError:
                log.exception("rollback failed for %s", rel)


def apply_plan(install_root: Path, staging: Path, *, replace, delete,
               os_replace=os.replace, retries: int = 5,
               sleep=time.sleep) -> None:
    """Swap staged files into the install. `replace` paths must exist under
    `staging`; `delete` paths are renamed into the backup tree. Raises on
    failure AFTER rolling back every completed step."""
    replace = list(replace)
    delete = list(delete)
    backup = install_root / BACKUP_DIR
    added = [p for p in replace if not _local(install_root, p).exists()]
    journal = {"state": "applying", "replace": replace, "delete": delete,
               "added": added}
    _write_journal(install_root, journal)
    try:
        for rel in delete + [p for p in replace if p not in added]:
            bak = _local(backup, rel)
            bak.parent.mkdir(parents=True, exist_ok=True)
            _replace_retry(_local(install_root, rel), bak,
                           os_replace=os_replace, retries=retries, sleep=sleep)
        for rel in replace:
            live = _local(install_root, rel)
            live.parent.mkdir(parents=True, exist_ok=True)
            _replace_retry(_local(staging, rel), live,
                           os_replace=os_replace, retries=retries, sleep=sleep)
        journal["state"] = "done"
        _write_journal(install_root, journal)
    except Exception:
        _rollback(install_root, journal, os_replace=os_replace)
        journal["state"] = "rolled_back"
        _write_journal(install_root, journal)
        raise


def startup_repair(install_root: Path, *, os_replace=os.replace) -> str:
    """Run at launch BEFORE the server starts. 'rolled_back' => caller must
    relaunch once (the journal is already terminal — no restart loop)."""
    journal = read_journal(install_root)
    if journal is None:
        return "none"
    if journal.get("state") == "applying":
        log.warning("interrupted update detected — rolling back")
        _rollback(install_root, journal, os_replace=os_replace)
        journal["state"] = "rolled_back"
        _write_journal(install_root, journal)
        return "rolled_back"
    return "cleaned"    # 'done'/'rolled_back' journals are swept later


def sweep_backup(install_root: Path, *, attempts: int = 60,
                 sleep=time.sleep) -> bool:
    """Remove the backup tree + a FINISHED journal. Never touches an
    'applying' journal (that is startup_repair's job)."""
    journal = read_journal(install_root)
    if journal is not None and journal.get("state") == "applying":
        return False
    backup = install_root / BACKUP_DIR
    for attempt in range(attempts):
        shutil.rmtree(backup, ignore_errors=True)
        if not backup.exists():
            break
        if attempt < attempts - 1:
            sleep(1.0)
    if backup.exists():
        return False
    (install_root / JOURNAL_NAME).unlink(missing_ok=True)
    return True
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_update_apply.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/update_apply.py tests/test_update_apply.py
git commit -m "feat(update): journaled crash-safe swap with rollback + startup repair"
```

---

### Task 6 (Wave 1, Track C): Zip + manifest generation (`tools/make_manifest.py`)

**Files:**
- Create: `tools/make_manifest.py`
- Test: `tests/test_make_manifest.py`

**Interfaces:**
- Consumes: `SCHEMA`, `parse_manifest` from `core/update_plan.py` (for the self-check in tests).
- Produces:
  - `build_zip(src_dir: Path, zip_path: Path) -> None` — deterministic (sorted paths, fixed 1980 timestamps, deflate) zip of the tree.
  - `entry_spans(zip_path: Path) -> dict[str, tuple[int, int, int]]` — arcname → `(data_offset, csize, method)` read from the zip's LOCAL headers (the local extra field can differ from the central directory's — offsets must come from the local header).
  - `make_manifest(zip_path: Path, version: str) -> str` — the manifest JSON text (schema of Task 1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_make_manifest.py
import hashlib
import sys
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from make_manifest import build_zip, entry_spans, make_manifest  # noqa: E402

from sm64_events.core.update_plan import parse_manifest  # noqa: E402


def _tree(root: Path, files: dict[str, bytes]) -> Path:
    for rel, content in files.items():
        p = root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    return root


FILES = {
    "SM64Trainer.exe": b"exe-bytes" * 1000,
    "_internal/python312.dll": b"dll" * 5000,
    "_internal/sub/data.json": b'{"k": 1}',
}


def test_build_zip_is_deterministic(tmp_path):
    src = _tree(tmp_path / "src", FILES)
    build_zip(src, tmp_path / "a.zip")
    build_zip(src, tmp_path / "b.zip")
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_manifest_lists_every_file_with_correct_hashes(tmp_path):
    src = _tree(tmp_path / "src", FILES)
    zp = tmp_path / "full.zip"
    build_zip(src, zp)
    m = parse_manifest(make_manifest(zp, "1.4.0"))
    assert m.version == "1.4.0"
    assert {e.path for e in m.files} == set(FILES)
    for entry in m.files:
        assert entry.sha256 == hashlib.sha256(FILES[entry.path]).hexdigest()
        assert entry.size == len(FILES[entry.path])


def test_manifest_offsets_slice_and_inflate_back_to_content(tmp_path):
    """THE load-bearing property: for every entry, blob[offset:offset+csize]
    must decode (raw deflate / stored) to exactly the original file — this is
    the byte range the updater Range-fetches in production."""
    src = _tree(tmp_path / "src", FILES)
    zp = tmp_path / "full.zip"
    build_zip(src, zp)
    blob = zp.read_bytes()
    m = parse_manifest(make_manifest(zp, "1.4.0"))
    for entry in m.files:
        comp = blob[entry.zip_offset:entry.zip_offset + entry.zip_csize]
        if entry.zip_method == 8:
            inflater = zlib.decompressobj(-15)
            data = inflater.decompress(comp) + inflater.flush()
        else:
            assert entry.zip_method == 0
            data = comp
        assert data == FILES[entry.path], entry.path


def test_entry_spans_match_manifest(tmp_path):
    src = _tree(tmp_path / "src", FILES)
    zp = tmp_path / "full.zip"
    build_zip(src, zp)
    spans = entry_spans(zp)
    m = parse_manifest(make_manifest(zp, "1.0.0"))
    for entry in m.files:
        off, csize, method = spans[entry.path]
        assert (off, csize, method) == (entry.zip_offset, entry.zip_csize,
                                        entry.zip_method)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_make_manifest.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'make_manifest'`

- [ ] **Step 3: Write the implementation**

```python
# tools/make_manifest.py
"""Zip the onedir build deterministically + emit the update manifest.

The manifest records, per file, the SHA-256 of its content AND the byte
range of its compressed data inside the zip. Offsets are read from each
entry's LOCAL file header (header_offset + 30 + name-length + extra-length)
— NOT the central directory: the local extra field can differ in length
from the central one, and a wrong offset would make the updater
Range-fetch garbage. tests/test_make_manifest.py proves every recorded
range slices+inflates back to the exact file content.

Deterministic zip (sorted paths, fixed 1980 timestamps): two builds of
identical trees produce identical zips, so unchanged files keep identical
(offset, csize) across releases only when content before them is unchanged
— offsets are per-release anyway (the updater always reads the CURRENT
manifest), determinism just keeps diffs/debugging sane."""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from sm64_events.core.update_plan import SCHEMA  # noqa: E402


def build_zip(src_dir: Path, zip_path: Path) -> None:
    paths = sorted(p for p in src_dir.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            arc = p.relative_to(src_dir).as_posix()
            info = zipfile.ZipInfo(arc, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, p.read_bytes())


def entry_spans(zip_path: Path) -> dict[str, tuple[int, int, int]]:
    """arcname -> (absolute data offset, compressed size, method), from the
    LOCAL headers."""
    spans: dict[str, tuple[int, int, int]] = {}
    with zipfile.ZipFile(zip_path) as zf, open(zip_path, "rb") as raw:
        for info in zf.infolist():
            raw.seek(info.header_offset)
            header = raw.read(30)
            if header[:4] != b"PK\x03\x04":
                raise ValueError(f"{info.filename}: bad local header magic")
            name_len = int.from_bytes(header[26:28], "little")
            extra_len = int.from_bytes(header[28:30], "little")
            data_offset = info.header_offset + 30 + name_len + extra_len
            spans[info.filename] = (data_offset, info.compress_size,
                                    info.compress_type)
    return spans


def make_manifest(zip_path: Path, version: str) -> str:
    spans = entry_spans(zip_path)
    files = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            data = zf.read(info.filename)
            offset, csize, method = spans[info.filename]
            files.append({"path": info.filename,
                          "sha256": hashlib.sha256(data).hexdigest(),
                          "size": len(data),
                          "zip_offset": offset,
                          "zip_csize": csize,
                          "zip_method": method})
    return json.dumps({"schema": SCHEMA, "version": version, "files": files},
                      indent=1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_make_manifest.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tools/make_manifest.py tests/test_make_manifest.py
git commit -m "feat(release): deterministic zip + manifest generator with local-header offsets"
```

---

### Task 7 (Wave 1, Track D): Bootstrap installer logic (`bootstrap/installer.py`)

**Files:**
- Create: `src/sm64_events/bootstrap/__init__.py` (empty)
- Create: `src/sm64_events/bootstrap/installer.py`
- Test: `tests/test_bootstrap_installer.py`

**Interfaces:**
- Consumes: `ZIP_ASSET`, `MANIFEST_ASSET`, `INSTALLED_MANIFEST` from `core/update_plan.py`.
- Produces:
  - `DEFAULT_REPO`, `APP_EXE = "SM64Trainer.exe"`
  - `latest_release(http, repo=DEFAULT_REPO) -> tuple[str, dict[str, str]]` (tag, asset-name→url; raises `RuntimeError` if zip/sha/manifest assets are missing)
  - `fetch_text(http, url) -> str`
  - `download(http, url, dest: Path, progress=None) -> str` (streams; returns hex sha256)
  - `default_install_dir() -> Path` (`%LOCALAPPDATA%\Programs\SM64Trainer`)
  - `install_tree(zip_path: Path, manifest_text: str, install_dir: Path) -> Path` (extract→temp sibling, atomic dir swap, returns installed exe path; raises `RuntimeError` if the install dir is locked by a running app)
  - `create_desktop_shortcut(exe: Path, run=subprocess.run) -> bool`
  - `launch_app(exe: Path, own_path: Path | None, popen=subprocess.Popen) -> None` (passes `--cleanup-bootstrap <own_path>`)
  - `run_install(*, http, ui, repo=DEFAULT_REPO, install_dir=None, own_path=None) -> bool` — orchestration; `ui` duck-type: `.status(str)`, `.progress(float)`, `.error(str)`, `.done(exe: Path)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_bootstrap_installer.py
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from sm64_events.bootstrap.installer import (APP_EXE, create_desktop_shortcut,
                                             default_install_dir, download,
                                             install_tree, latest_release,
                                             launch_app, run_install)
from sm64_events.core.update_plan import INSTALLED_MANIFEST, ZIP_ASSET


class _Resp(io.BytesIO):
    def __init__(self, data, headers=None):
        super().__init__(data)
        self.status = 200
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _http(routes):
    def opener(req):
        url = req.full_url if hasattr(req, "full_url") else req
        if url not in routes:
            raise OSError(f"unmapped url {url}")
        return _Resp(routes[url])
    return opener


def _release_json(tag, assets):
    return json.dumps({
        "tag_name": tag,
        "assets": [{"name": n, "browser_download_url": u}
                   for n, u in assets.items()]}).encode()


LATEST = "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases/latest"

FULL_ASSETS = {
    "SM64Trainer-full.zip": "https://dl/full.zip",
    "SM64Trainer-full.zip.sha256": "https://dl/full.sha",
    "manifest.json": "https://dl/manifest.json",
    "manifest.json.sha256": "https://dl/manifest.sha",
}


def test_latest_release_returns_assets():
    http = _http({LATEST: _release_json("v2.0.0", FULL_ASSETS)})
    tag, assets = latest_release(http)
    assert tag == "v2.0.0"
    assert assets[ZIP_ASSET] == "https://dl/full.zip"


def test_latest_release_requires_zip_and_manifest():
    http = _http({LATEST: _release_json("v2.0.0", {"other.txt": "u"})})
    with pytest.raises(RuntimeError):
        latest_release(http)


def test_download_streams_and_hashes(tmp_path):
    payload = b"Z" * 100_000
    http = _http({"https://dl/full.zip": payload})
    seen = []
    digest = download(http, "https://dl/full.zip", tmp_path / "z.zip",
                      progress=seen.append)
    assert (tmp_path / "z.zip").read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    assert seen and seen[-1] == 1.0


def test_default_install_dir_under_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_install_dir() == tmp_path / "Programs" / "SM64Trainer"


def _zip_bytes(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            zf.writestr(rel, data)
    return buf.getvalue()


def test_install_tree_fresh_and_overwrite(tmp_path):
    zp = tmp_path / "full.zip"
    zp.write_bytes(_zip_bytes({APP_EXE: b"EXE-V1", "_internal/a.dll": b"A"}))
    target = tmp_path / "Programs" / "SM64Trainer"
    exe = install_tree(zp, '{"schema": 1}', target)
    assert exe == target / APP_EXE
    assert exe.read_bytes() == b"EXE-V1"
    assert (target / INSTALLED_MANIFEST).read_text() == '{"schema": 1}'
    # reinstall over an existing dir (repair path)
    zp.write_bytes(_zip_bytes({APP_EXE: b"EXE-V2"}))
    exe = install_tree(zp, '{"schema": 1}', target)
    assert exe.read_bytes() == b"EXE-V2"
    assert not target.with_name(target.name + ".old").exists()
    assert not target.with_name(target.name + ".new").exists()


def test_create_desktop_shortcut_invokes_powershell(tmp_path):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        class R:
            returncode = 0
        return R()

    exe = tmp_path / "SM64Trainer.exe"
    assert create_desktop_shortcut(exe, run=fake_run) is True
    joined = " ".join(calls[0])
    assert "powershell" in calls[0][0]
    assert "SM64 Trainer.lnk" in joined
    assert str(exe) in joined


def test_launch_app_passes_cleanup_arg(tmp_path):
    calls = []
    exe = tmp_path / "app" / "SM64Trainer.exe"
    exe.parent.mkdir()
    launch_app(exe, tmp_path / "bootstrap.exe",
               popen=lambda args, **kw: calls.append(args))
    assert calls[0] == [str(exe), "--cleanup-bootstrap",
                       str(tmp_path / "bootstrap.exe")]


class _UI:
    def __init__(self):
        self.errors, self.done_exe = [], None

    def status(self, msg):
        pass

    def progress(self, frac):
        pass

    def error(self, msg):
        self.errors.append(msg)

    def done(self, exe):
        self.done_exe = exe


def test_run_install_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    blob = _zip_bytes({APP_EXE: b"EXE", "_internal/a.dll": b"A"})
    manifest = '{"schema": 1, "version": "2.0.0", "files": []}'
    routes = {
        LATEST: _release_json("v2.0.0", FULL_ASSETS),
        "https://dl/full.zip": blob,
        "https://dl/full.sha": (hashlib.sha256(blob).hexdigest()
                                + "  SM64Trainer-full.zip").encode(),
        "https://dl/manifest.json": manifest.encode(),
    }
    launched = []
    monkeypatch.setattr("sm64_events.bootstrap.installer.create_desktop_shortcut",
                        lambda exe, run=None: True)
    monkeypatch.setattr("sm64_events.bootstrap.installer.launch_app",
                        lambda exe, own, popen=None: launched.append((exe, own)))
    ui = _UI()
    ok = run_install(http=_http(routes), ui=ui,
                     own_path=tmp_path / "boot.exe")
    assert ok is True and not ui.errors
    exe = tmp_path / "lad" / "Programs" / "SM64Trainer" / APP_EXE
    assert exe.read_bytes() == b"EXE"
    assert launched == [(exe, tmp_path / "boot.exe")]
    assert ui.done_exe == exe


def test_run_install_bad_zip_hash_reports_error(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lad"))
    blob = _zip_bytes({APP_EXE: b"EXE"})
    routes = {
        LATEST: _release_json("v2.0.0", FULL_ASSETS),
        "https://dl/full.zip": blob,
        "https://dl/full.sha": ("0" * 64 + "  x").encode(),
        "https://dl/manifest.json": b"{}",
    }
    ui = _UI()
    assert run_install(http=_http(routes), ui=ui) is False
    assert ui.errors
    assert not (tmp_path / "lad" / "Programs" / "SM64Trainer").exists()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bootstrap_installer.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.bootstrap'`

- [ ] **Step 3: Write the implementation**

Create empty `src/sm64_events/bootstrap/__init__.py`, then:

```python
# src/sm64_events/bootstrap/installer.py
"""Bootstrap installer: the tiny onefile exe published as SM64Trainer.exe.

Two jobs, one code path (idempotent — running it again is a repair):
1. MIGRATION: already-shipped onefile installs can only self-update to an
   asset named SM64Trainer.exe — so that asset IS this bootstrap. The old
   updater swaps it in and relaunches it; it then downloads the full zip
   (the LAST full download), installs to %LOCALAPPDATA%\\Programs\\SM64Trainer,
   creates the Desktop shortcut, launches the real app, and asks the app to
   delete this file (--cleanup-bootstrap; a running exe cannot delete itself).
2. NEW USERS: the GitHub download habit ("grab SM64Trainer.exe, double-click")
   now yields a proper installer.

Stdlib-only + core.update_plan (asset names) so the PyInstaller build stays
~25 MB. User data under %LOCALAPPDATA%\\SM64Trainer is NEVER touched — this
installs only the program directory."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET)

DEFAULT_REPO = "griffinbeels/SM64-Trainer"
GITHUB_API = "https://api.github.com"
APP_EXE = "SM64Trainer.exe"
_UA = "SM64Trainer-bootstrap"


def _request(url: str, accept: "str | None" = None) -> urllib.request.Request:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    if accept:
        req.add_header("Accept", accept)
    return req


def latest_release(http, repo: str = DEFAULT_REPO) -> tuple[str, dict]:
    url = f"{GITHUB_API}/repos/{repo}/releases/latest"
    with http(_request(url, accept="application/vnd.github+json")) as r:
        rel = json.loads(r.read().decode("utf-8"))
    assets = {a.get("name"): a.get("browser_download_url")
              for a in rel.get("assets", [])}
    for need in (ZIP_ASSET, ZIP_ASSET + ".sha256", MANIFEST_ASSET):
        if not assets.get(need):
            raise RuntimeError(f"latest release is missing asset {need}")
    return rel.get("tag_name") or "", assets


def fetch_text(http, url: str) -> str:
    with http(_request(url)) as r:
        return r.read().decode("utf-8")


def download(http, url: str, dest: Path, progress=None) -> str:
    digest = hashlib.sha256()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with http(_request(url)) as resp:
        total = int((resp.headers or {}).get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as out:
            while True:
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress and total:
                    progress(min(1.0, done / total))
    return digest.hexdigest()


def default_install_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(
        Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "SM64Trainer"


def install_tree(zip_path: Path, manifest_text: str, install_dir: Path) -> Path:
    """Extract to a temp sibling then atomically swap directories, so a
    failed download/extract can never break an existing install. Raises
    RuntimeError when the current install is locked (app running)."""
    import zipfile
    fresh = install_dir.with_name(install_dir.name + ".new")
    old = install_dir.with_name(install_dir.name + ".old")
    shutil.rmtree(fresh, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(fresh)
    (fresh / INSTALLED_MANIFEST).write_text(manifest_text)
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        if install_dir.exists():
            os.replace(install_dir, old)
        os.replace(fresh, install_dir)
    except OSError as err:
        raise RuntimeError(
            "could not replace the existing install — close SM64 Trainer "
            f"and run this installer again ({err})") from err
    shutil.rmtree(old, ignore_errors=True)
    return install_dir / APP_EXE


_SHORTCUT_PS = (
    "$desktop=[Environment]::GetFolderPath('Desktop');"
    "$ws=New-Object -ComObject WScript.Shell;"
    "$s=$ws.CreateShortcut((Join-Path $desktop 'SM64 Trainer.lnk'));"
    "$s.TargetPath='{exe}';"
    "$s.WorkingDirectory='{workdir}';"
    "$s.IconLocation='{exe},0';"
    "$s.Save()")


def create_desktop_shortcut(exe: Path, run=subprocess.run) -> bool:
    """[Environment]::GetFolderPath('Desktop') (not %USERPROFILE%\\Desktop)
    so OneDrive-redirected Desktops get the shortcut too."""
    script = _SHORTCUT_PS.format(exe=str(exe), workdir=str(exe.parent))
    try:
        run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            check=True, capture_output=True, timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return True
    except Exception:
        return False


def launch_app(exe: Path, own_path: "Path | None",
               popen=subprocess.Popen) -> None:
    args = [str(exe)]
    if own_path is not None:
        args += ["--cleanup-bootstrap", str(own_path)]
    popen(args, cwd=str(exe.parent), close_fds=True)


def run_install(*, http, ui, repo: str = DEFAULT_REPO,
                install_dir: "Path | None" = None,
                own_path: "Path | None" = None) -> bool:
    """The whole install: release -> download+verify zip -> swap in ->
    shortcut -> launch. Returns False after ui.error() on any failure."""
    target = install_dir or default_install_dir()
    try:
        ui.status("Finding the latest release…")
        tag, assets = latest_release(http, repo)
        with tempfile.TemporaryDirectory(prefix="sm64-bootstrap-") as td:
            zip_path = Path(td) / ZIP_ASSET
            ui.status(f"Downloading SM64 Trainer {tag}…")
            digest = download(http, assets[ZIP_ASSET], zip_path,
                              progress=ui.progress)
            published = fetch_text(http, assets[ZIP_ASSET + ".sha256"]).split()
            if not published or published[0].strip().lower() != digest:
                raise RuntimeError("download failed verification "
                                   "(checksum mismatch)")
            manifest_text = fetch_text(http, assets[MANIFEST_ASSET])
            ui.status("Installing…")
            exe = install_tree(zip_path, manifest_text, target)
        create_desktop_shortcut(exe)
        ui.status("Starting SM64 Trainer…")
        launch_app(exe, own_path)
        ui.done(exe)
        return True
    except Exception as err:
        ui.error(str(err))
        return False
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bootstrap_installer.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/bootstrap/__init__.py src/sm64_events/bootstrap/installer.py tests/test_bootstrap_installer.py
git commit -m "feat(bootstrap): stdlib-only installer (download, verify, atomic install, shortcut, launch)"
```

---

### Task 8 (Wave 1, Track D): Bootstrap UI + entry point

**Files:**
- Modify: `src/sm64_events/bootstrap/installer.py` (append)
- Create: `bootstrap_entry.py` (repo root, next to `gui_entry.py`)
- Test: `tests/test_bootstrap_installer.py` (append)

**Interfaces:**
- Produces: `ConsoleUI`, `TkUI`, `main(argv=None) -> int` (0 success / 1 failure). `--silent` selects ConsoleUI (no window; used by tests and automation). When frozen, `own_path = Path(sys.executable)` so the installed app can delete the bootstrap.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_bootstrap_installer.py`)

```python
from sm64_events.bootstrap import installer as boot


def test_console_ui_reports_and_swallows_output(capsys):
    ui = boot.ConsoleUI()
    ui.status("hello")
    ui.progress(0.5)
    ui.error("bad")
    ui.done(Path("x"))
    out = capsys.readouterr().out
    assert "hello" in out and "bad" in out


def test_main_silent_runs_install(monkeypatch, tmp_path):
    called = {}

    def fake_run_install(**kw):
        called.update(kw)
        return True

    monkeypatch.setattr(boot, "run_install", fake_run_install)
    assert boot.main(["--silent"]) == 0
    assert isinstance(called["ui"], boot.ConsoleUI)
    assert called["own_path"] is None      # not frozen under pytest


def test_main_silent_failure_returns_1(monkeypatch):
    monkeypatch.setattr(boot, "run_install", lambda **kw: False)
    assert boot.main(["--silent"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bootstrap_installer.py -q`
Expected: FAIL — `AttributeError: … has no attribute 'ConsoleUI'`

- [ ] **Step 3: Write the implementation** (append to `installer.py`)

```python
class ConsoleUI:
    """--silent / test UI: prints instead of windowing."""

    def status(self, msg: str) -> None:
        print(msg)

    def progress(self, frac: float) -> None:
        print(f"  {frac * 100:5.1f}%")

    def error(self, msg: str) -> None:
        print(f"ERROR: {msg}")

    def done(self, exe: Path) -> None:
        print(f"Installed: {exe}")


class TkUI:
    """Minimal tkinter progress window. All widget mutations are marshalled
    onto the Tk main thread via after(); the worker thread only calls these
    methods."""

    def __init__(self):
        import tkinter as tk
        from tkinter import ttk
        self._tk = tk
        self.root = tk.Tk()
        self.root.title("SM64 Trainer Setup")
        self.root.geometry("420x120")
        self.root.resizable(False, False)
        self.label = tk.Label(self.root, text="Starting…", anchor="w")
        self.label.pack(fill="x", padx=16, pady=(18, 6))
        self.bar = ttk.Progressbar(self.root, maximum=1000)
        self.bar.pack(fill="x", padx=16)
        self.failed = False

    def status(self, msg: str) -> None:
        self.root.after(0, lambda: self.label.config(text=msg))

    def progress(self, frac: float) -> None:
        self.root.after(0, lambda: self.bar.config(value=int(frac * 1000)))

    def error(self, msg: str) -> None:
        self.failed = True

        def show():
            from tkinter import messagebox
            messagebox.showerror("SM64 Trainer Setup", msg)
            self.root.destroy()
        self.root.after(0, show)

    def done(self, exe: Path) -> None:
        self.root.after(0, self.root.destroy)


def main(argv: "list[str] | None" = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    silent = "--silent" in args
    own_path = Path(sys.executable) if getattr(sys, "frozen", False) else None
    if silent:
        ui = ConsoleUI()
        ok = run_install(http=urllib.request.urlopen, ui=ui,
                         own_path=own_path)
        return 0 if ok else 1
    ui = TkUI()
    result = {"ok": False}

    def work():
        result["ok"] = run_install(http=urllib.request.urlopen, ui=ui,
                                   own_path=own_path)

    import threading
    threading.Thread(target=work, daemon=True).start()
    ui.root.mainloop()
    return 0 if result["ok"] and not ui.failed else 1
```

Create `bootstrap_entry.py` (repo root):

```python
# bootstrap_entry.py
"""PyInstaller entry point for the bootstrap installer (built onefile as
SM64TrainerSetup.exe, published as the SM64Trainer.exe release asset)."""
import sys

from sm64_events.bootstrap.installer import main

if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_bootstrap_installer.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/bootstrap/installer.py bootstrap_entry.py tests/test_bootstrap_installer.py
git commit -m "feat(bootstrap): tk progress UI, --silent mode, PyInstaller entry"
```

---

### Task 9 (Wave 1, Track E): Build modes (`tools/build_exe.py`)

**Files:**
- Modify: `tools/build_exe.py` (restructure)
- Test: `tests/test_build_exe_args.py` (new)

**Interfaces:**
- Produces (pure, unit-tested): `app_args(ffmpeg: str | None) -> list[str]` (onedir — NO `--onefile`), `bootstrap_args() -> list[str]` (onefile, name `SM64TrainerSetup`, entry `bootstrap_entry.py`, no COLLECT/no runtime hook), `needs_reexec(environ) -> bool` (True unless `PYTHONHASHSEED == "1"`).
- CLI: `--mode {app,bootstrap,all}` (default `all`), `--ffmpeg PATH` as before.
- Outputs: `dist/SM64Trainer/` (onedir app: `SM64Trainer.exe` + `_internal/`) and `dist/SM64TrainerSetup.exe`.
- Reproducibility: when `needs_reexec`, `main()` re-executes itself via `subprocess.run([sys.executable, __file__, *argv], env={**os.environ, "PYTHONHASHSEED": "1", "SOURCE_DATE_EPOCH": <git HEAD commit unix time>})` and returns that exit code — `.pyc`/PYZ content then hashes identically across builds of identical sources (spec §1).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_build_exe_args.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from build_exe import app_args, bootstrap_args, needs_reexec  # noqa: E402


def test_app_args_is_onedir():
    args = app_args(None)
    assert "--onefile" not in args
    assert "--windowed" in args
    assert "SM64Trainer" in args
    assert any("gui_entry.py" in a for a in args)
    assert any("rthook_comtypes" in a for a in args)   # runtime hook kept


def test_app_args_bundles_ffmpeg_when_given(tmp_path):
    ff = tmp_path / "ffmpeg.exe"
    ff.write_bytes(b"x")
    args = app_args(str(ff))
    assert "--add-binary" in args


def test_bootstrap_args_is_tiny_onefile():
    args = bootstrap_args()
    assert "--onefile" in args
    assert "SM64TrainerSetup" in args
    assert any("bootstrap_entry.py" in a for a in args)
    assert "--collect-all" not in args
    assert "--runtime-hook" not in args


def test_needs_reexec_gates_on_hash_seed():
    assert needs_reexec({}) is True
    assert needs_reexec({"PYTHONHASHSEED": "random"}) is True
    assert needs_reexec({"PYTHONHASHSEED": "1"}) is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_build_exe_args.py -q`
Expected: FAIL — `ImportError: cannot import name 'app_args'`

- [ ] **Step 3: Write the implementation** (replace `tools/build_exe.py` with this restructure — behavior of the old single-mode build is preserved by `app_args`, minus `--onefile`)

```python
# tools/build_exe.py
"""One-command build: `uv run python tools/build_exe.py` ->
dist/SM64Trainer/ (onedir app) + dist/SM64TrainerSetup.exe (bootstrap).

The app is ONEDIR (spec 2026-07-23-incremental-updates): the updater
replaces individual files under the install root, so the build must produce
a folder, not a fused exe. The bootstrap is a tiny stdlib-only ONEFILE
installer, uploaded to releases under the load-bearing asset name
SM64Trainer.exe (old shipped updaters can only install that name).

Reproducibility: PYTHONHASHSEED randomizes compiled bytecode, which would
make every .pyc/PYZ hash differently per build and bloat update deltas.
main() re-execs itself with PYTHONHASHSEED=1 + SOURCE_DATE_EPOCH=<HEAD
commit time> so unchanged sources produce identical bytes across releases.

ffmpeg is bundled automatically: --ffmpeg PATH wins, else the ffmpeg on
PATH. Releases MUST bundle ffmpeg."""
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SEP = ";" if os.name == "nt" else ":"
# Native/binary deps whose data files or submodules PyInstaller's auto
# analysis can miss — collect everything for each.
COLLECT = ["av", "windows_capture", "pyaudiowpatch", "pycaw", "comtypes",
           "pymem", "webview", "pystray", "numpy", "yt_dlp"]


def needs_reexec(environ) -> bool:
    return environ.get("PYTHONHASHSEED") != "1"


def _source_date_epoch() -> str:
    try:
        out = subprocess.run(["git", "log", "-1", "--format=%ct"], cwd=REPO,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip() or "315532800"
    except Exception:
        return "315532800"    # 1980-01-01, matches the zip timestamp floor


def app_args(ffmpeg: "str | None") -> list[str]:
    argv = [
        str(REPO / "gui_entry.py"),
        "--name", "SM64Trainer",
        "--windowed", "--clean", "--noconfirm",     # onedir: no --onefile
        "--paths", str(REPO / "src"),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
        "--runtime-hook", str(REPO / "tools" / "rthook_comtypes.py"),
        # The UI is READ FROM DISK at runtime (server/app.py _UI_INDEX), not
        # imported, so it must be collected preserving the package path.
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'ui'}{SEP}sm64_events/ui",
        # Tray + pywebview load assets/ukiki.ico at RUNTIME via _asset_path.
        "--add-data", f"{REPO / 'assets' / 'ukiki.ico'}{SEP}.",
        # Rank standards seed (bundled_rank_standards() in core/paths.py).
        "--add-data",
        f"{REPO / 'src' / 'sm64_events' / 'data' / 'rank_standards.seed.json'}{SEP}.",
    ]
    for pkg in COLLECT:
        argv += ["--collect-all", pkg]
    if ffmpeg:
        argv += ["--add-binary", f"{ffmpeg}{SEP}."]
    return argv


def bootstrap_args() -> list[str]:
    return [
        str(REPO / "bootstrap_entry.py"),
        "--name", "SM64TrainerSetup",
        "--onefile", "--windowed", "--clean", "--noconfirm",
        "--paths", str(REPO / "src"),
        "--icon", str(REPO / "assets" / "ukiki.ico"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["app", "bootstrap", "all"],
                    default="all")
    ap.add_argument("--ffmpeg",
                    help="ffmpeg.exe to bundle (default: the one on PATH)")
    args = ap.parse_args()

    if needs_reexec(os.environ):
        env = {**os.environ, "PYTHONHASHSEED": "1",
               "SOURCE_DATE_EPOCH": _source_date_epoch()}
        return subprocess.run([sys.executable, __file__, *sys.argv[1:]],
                              env=env).returncode

    import PyInstaller.__main__ as pyi

    if args.mode in ("app", "all"):
        ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
        if ffmpeg and not Path(ffmpeg).exists():
            print(f"ffmpeg not found: {ffmpeg}", file=sys.stderr)
            return 2
        if not ffmpeg:
            print("WARNING: no ffmpeg found on PATH and --ffmpeg not given — "
                  "building WITHOUT it; replay will use the in-process "
                  "encoder. Install ffmpeg for a proper release.")
        else:
            print(f"bundling ffmpeg: {ffmpeg}")
        pyi.run(app_args(ffmpeg))
        print("\nBuilt:", REPO / "dist" / "SM64Trainer")
    if args.mode in ("bootstrap", "all"):
        pyi.run(bootstrap_args())
        print("\nBuilt:", REPO / "dist" / "SM64TrainerSetup.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_build_exe_args.py -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tools/build_exe.py tests/test_build_exe_args.py
git commit -m "feat(build): onedir app + bootstrap modes with reproducible-build re-exec"
```

---

### Task 10 (Wave 2): Reshape `core/updater.py` + full-cycle integration test

**Files:**
- Modify: `src/sm64_events/core/updater.py` (major reshape)
- Modify: `tests/test_updater.py` (reshape: keep version/check/skip tests, replace exe-swap tests)
- Create: `tests/test_update_cycle.py` (integration)

**Interfaces:**
- Keeps (unchanged public surface consumed by `server/update_api.py` + `main.py`): `UpdateService(current_version=, repo=, exe_path=, http=, state_path=, frozen=)`, `.status(force)`, `.begin_apply(on_success)`, `.skip(version)`, `parse_version`, `is_newer`, `exe_dir_writable`.
- Changes:
  - `UpdateInfo` becomes `(version, notes, html_url, zip_url, zip_sha_url, manifest_url, manifest_sha_url)`.
  - `check_for_update` requires assets `ZIP_ASSET`, `ZIP_ASSET + ".sha256"`, `MANIFEST_ASSET`, `MANIFEST_ASSET + ".sha256"` — else None.
  - `_check` additionally downloads + sha-verifies + parses the manifest, reads `installed_manifest.json` from the install root, and caches `self._plan` (`build_plan(remote, installed, root, verify_local=force)`).
  - `status()` gains `"download_bytes": int | None` (None when no update / fake).
  - `_run_apply` pipeline: clear+create `install_root/.update_staging` → `free_disk_ok` (needed = sum of fetch sizes) → `fetch_plan` (on `RangeUnsupported` → `fetch_full_zip` with the published zip sha) → stage `installed_manifest.json` (the verified manifest text verbatim) → state `installing` → `apply_plan(root, staging, replace=[fetch paths]+[INSTALLED_MANIFEST], delete=plan.delete)` → remove staging → `on_success()`.
  - `cleanup_old_exe()` is REPLACED by `startup_maintenance(bootstrap_path: str | None = None)`: off-thread, frozen-gated: `sweep_backup(root)`; if `bootstrap_path`, retry-delete that file AND `<bootstrap_path>.old` (60×1 s) — the migration leftovers on the user's Desktop.
  - Removed: `download_and_stage`, `apply_update`, `cleanup_old` (single-exe era; the bootstrap replaced that path). Delete their tests.

- [ ] **Step 1: Reshape `tests/test_updater.py`**

Keep unchanged: `test_version_is_semver`, `parse_version`/`is_newer` tests, `_Resp`/`_fake_http` helpers, `test_check_none_on_http_error`, `test_status_inert_from_source`, `test_skip_persists_and_round_trips`, `test_exe_dir_writable`. Delete: every `download_and_stage` / `apply_update` / `cleanup_old` test and the old happy-path service tests. Replace the release fixture + check/service tests with:

```python
# --- new-format release fixtures (replace _release_json usages) ---
import hashlib as _hashlib
import io
import json as _json
import sys as _sys
import zipfile as _zipfile
from pathlib import Path

sys_path_tools = str(Path(__file__).resolve().parents[1] / "tools")
if sys_path_tools not in _sys.path:
    _sys.path.insert(0, sys_path_tools)
from make_manifest import build_zip, make_manifest  # noqa: E402

from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET)

LATEST = "https://api.github.com/repos/griffinbeels/SM64-Trainer/releases/latest"

FULL_ASSETS = {
    ZIP_ASSET: "https://dl/full.zip",
    ZIP_ASSET + ".sha256": "https://dl/full.sha",
    MANIFEST_ASSET: "https://dl/manifest.json",
    MANIFEST_ASSET + ".sha256": "https://dl/manifest.sha",
}


def _release_json(tag, assets):
    return _json.dumps({
        "tag_name": tag, "body": "notes here",
        "html_url": f"https://github.com/x/y/releases/tag/{tag}",
        "assets": [{"name": n, "browser_download_url": u}
                   for n, u in assets.items()]}).encode()


def _sha_line(data: bytes, name: str) -> bytes:
    return (_hashlib.sha256(data).hexdigest() + "  " + name).encode()


def _fake_release(tmp_path, tag: str, files: dict[str, bytes]) -> dict:
    """Build a real zip+manifest for `files` and return an http routes dict."""
    src = tmp_path / f"src-{tag}"
    for rel, content in files.items():
        p = src.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    zp = tmp_path / f"{tag}.zip"
    build_zip(src, zp)
    manifest = make_manifest(zp, tag.lstrip("v")).encode()
    blob = zp.read_bytes()
    return {
        LATEST: _release_json(tag, FULL_ASSETS),
        "https://dl/full.zip": blob,
        "https://dl/full.sha": _sha_line(blob, ZIP_ASSET),
        "https://dl/manifest.json": manifest,
        "https://dl/manifest.sha": _sha_line(manifest, MANIFEST_ASSET),
    }


def test_check_returns_info_when_all_assets_present(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"X"})
    info = check_for_update("1.0.0", http=_fake_http(routes))
    assert info.version == "2.0.0"
    assert info.zip_url == "https://dl/full.zip"
    assert info.manifest_url == "https://dl/manifest.json"


def test_check_none_when_missing_manifest_assets(tmp_path):
    partial = {k: v for k, v in FULL_ASSETS.items() if k != MANIFEST_ASSET}
    http = _fake_http({LATEST: _release_json("v2.0.0", partial)})
    assert check_for_update("1.0.0", http=http) is None


def test_check_none_when_not_newer(tmp_path):
    http = _fake_http({LATEST: _release_json("v1.0.0", FULL_ASSETS)})
    assert check_for_update("1.0.0", http=http) is None
```

And service-level tests (the `_svc` helper changes to build an install TREE):

```python
def _svc(tmp_path, http, *, frozen=True):
    root = tmp_path / "app"
    root.mkdir(parents=True, exist_ok=True)
    exe = root / "SM64Trainer.exe"
    if not exe.exists():
        exe.write_bytes(b"OLD")
    return UpdateService(current_version="1.0.0", http=http, exe_path=exe,
                         state_path=tmp_path / "update_state.json",
                         frozen=frozen)


def test_status_reports_available_with_download_bytes(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    svc = _svc(tmp_path, _fake_http(routes))
    st = svc.status()
    assert st["update_available"] is True
    assert st["latest"] == "2.0.0"
    assert st["download_bytes"] > 0
    assert st["writable"] is True


def test_status_manifest_tamper_means_no_update(tmp_path):
    routes = _fake_release(tmp_path, "v2.0.0", {"SM64Trainer.exe": b"NEW"})
    routes["https://dl/manifest.sha"] = ("0" * 64 + "  x").encode()
    svc = _svc(tmp_path, _fake_http(routes))
    assert svc.status()["update_available"] is False
```

- [ ] **Step 2: Write the integration test**

```python
# tests/test_update_cycle.py
"""End-to-end: fake GitHub release (real zip + manifest) -> check -> plan ->
range-fetch -> journaled apply -> installed tree matches the release.
The whole pipeline the popup's 'Update now' drives, network-free."""
import io
import threading

from pathlib import Path

from sm64_events.core.update_apply import BACKUP_DIR, read_journal
from sm64_events.core.update_plan import INSTALLED_MANIFEST
from sm64_events.core.updater import UpdateService

from test_updater import _fake_release  # reuse the fixture helpers


class _Resp(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Length": str(len(data))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False


def _range_http(routes):
    """Serves routes; honors Range on the zip asset with 206 slices."""
    def opener(req):
        url = req.full_url if hasattr(req, "full_url") else req
        body = routes[url]
        rng = getattr(req, "headers", {}).get("Range")
        if rng:
            lo, hi = rng.removeprefix("bytes=").split("-")
            return _Resp(body[int(lo):int(hi) + 1], status=206)
        return _Resp(body)
    return opener


V1 = {"SM64Trainer.exe": b"EXE-V1", "_internal/stable.dll": b"S" * 4000,
      "_internal/old.pyd": b"OLD-ONLY"}
V2 = {"SM64Trainer.exe": b"EXE-V2", "_internal/stable.dll": b"S" * 4000,
      "_internal/fresh.dat": b"NEW-FILE"}


def _install_v1(tmp_path, routes_v1):
    """Materialize a v1 install the way the bootstrap would."""
    root = tmp_path / "app"
    for rel, content in V1.items():
        p = root.joinpath(*rel.split("/"))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(content)
    (root / INSTALLED_MANIFEST).write_bytes(
        routes_v1["https://dl/manifest.json"])
    return root


def test_full_update_cycle_downloads_only_changes(tmp_path):
    routes_v1 = _fake_release(tmp_path / "r1", "v1.0.0", V1)
    routes_v2 = _fake_release(tmp_path / "r2", "v2.0.0", V2)
    root = _install_v1(tmp_path, routes_v1)
    svc = UpdateService(current_version="1.0.0",
                        http=_range_http(routes_v2),
                        exe_path=root / "SM64Trainer.exe",
                        state_path=tmp_path / "state.json", frozen=True)
    st = svc.status()
    assert st["update_available"] is True
    # only exe + fresh.dat move; the stable dll must NOT be re-downloaded
    zip_size = len(routes_v2["https://dl/full.zip"])
    assert 0 < st["download_bytes"] < zip_size
    done = threading.Event()
    assert svc.begin_apply(done.set)["state"] == "downloading"
    assert done.wait(timeout=10)
    assert (root / "SM64Trainer.exe").read_bytes() == b"EXE-V2"
    assert (root / "_internal/fresh.dat").read_bytes() == b"NEW-FILE"
    assert not (root / "_internal/old.pyd").exists()
    assert (root / "_internal/stable.dll").read_bytes() == b"S" * 4000
    assert read_journal(root)["state"] == "done"
    assert (root / BACKUP_DIR / "SM64Trainer.exe").read_bytes() == b"EXE-V1"
    # installed manifest advanced -> a re-check sees nothing to do
    svc2 = UpdateService(current_version="2.0.0",
                         http=_range_http(routes_v2),
                         exe_path=root / "SM64Trainer.exe",
                         state_path=tmp_path / "state.json", frozen=True)
    assert svc2.status()["update_available"] is False


def test_full_update_cycle_range_refused_falls_back(tmp_path):
    routes_v1 = _fake_release(tmp_path / "r1", "v1.0.0", V1)
    routes_v2 = _fake_release(tmp_path / "r2", "v2.0.0", V2)
    root = _install_v1(tmp_path, routes_v1)

    def no_range_http(req):        # always ignores Range -> status 200
        url = req.full_url if hasattr(req, "full_url") else req
        return _Resp(routes_v2[url])

    svc = UpdateService(current_version="1.0.0", http=no_range_http,
                        exe_path=root / "SM64Trainer.exe",
                        state_path=tmp_path / "state.json", frozen=True)
    done = threading.Event()
    svc.status()
    assert svc.begin_apply(done.set)["state"] == "downloading"
    assert done.wait(timeout=10)
    assert (root / "SM64Trainer.exe").read_bytes() == b"EXE-V2"
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `uv run pytest tests/test_updater.py tests/test_update_cycle.py -q`
Expected: FAIL — `UpdateInfo` has no `zip_url` / `download_bytes` missing, etc.

- [ ] **Step 4: Reshape `src/sm64_events/core/updater.py`**

Keep the module docstring's spirit but update it; keep `parse_version`, `is_newer`, `_get`, `exe_dir_writable`, the `_CHECK_TTL_S` cache, skip overlay, and `SM64_UPDATE_FAKE` exactly as they are. Replace `UpdateInfo`, `check_for_update`, and the download/apply internals:

```python
# replacing the old UpdateInfo/check_for_update/download_and_stage/apply_update/cleanup_old
import shutil

from sm64_events.core.update_apply import (STAGING_DIR, apply_plan,
                                           sweep_backup)
from sm64_events.core.update_fetch import (RangeUnsupported, fetch_full_zip,
                                           fetch_plan, free_disk_ok)
from sm64_events.core.update_plan import (INSTALLED_MANIFEST, MANIFEST_ASSET,
                                          ZIP_ASSET, Manifest, build_plan,
                                          parse_manifest)


@dataclass
class UpdateInfo:
    version: str
    notes: str
    html_url: str
    zip_url: str
    zip_sha_url: str
    manifest_url: str
    manifest_sha_url: str


def check_for_update(current: str, *, http=urllib.request.urlopen,
                     repo: str = DEFAULT_REPO,
                     api_base: str = GITHUB_API) -> "UpdateInfo | None":
    """GET the latest release; return UpdateInfo iff it is strictly newer AND
    carries the zip + manifest assets WITH their .sha256 companions (the
    'no unverified bytes ever applied' rule). Best-effort: any error ->
    None (no popup)."""
    try:
        url = f"{api_base}/repos/{repo}/releases/latest"
        with _get(http, url, accept="application/vnd.github+json") as r:
            rel = json.loads(r.read().decode("utf-8"))
        tag = rel.get("tag_name") or ""
        if not is_newer(tag, current):
            return None
        assets = {a.get("name"): a.get("browser_download_url")
                  for a in rel.get("assets", [])}
        needed = (ZIP_ASSET, ZIP_ASSET + ".sha256",
                  MANIFEST_ASSET, MANIFEST_ASSET + ".sha256")
        if not all(assets.get(n) for n in needed):
            log.info("release %s is missing update assets; not offering", tag)
            return None
        return UpdateInfo(
            version=tag.lstrip("vV"),
            notes=rel.get("body") or "",
            html_url=rel.get("html_url") or "",
            zip_url=assets[ZIP_ASSET],
            zip_sha_url=assets[ZIP_ASSET + ".sha256"],
            manifest_url=assets[MANIFEST_ASSET],
            manifest_sha_url=assets[MANIFEST_ASSET + ".sha256"])
    except Exception:
        log.info("update check failed", exc_info=True)
        return None
```

`UpdateService` — the `__init__` grows `self._manifest: Manifest | None = None`, `self._manifest_text: str | None = None`, `self._plan = None`. `_check(force)` becomes:

```python
    def _fetch_text(self, url: str) -> str:
        with _get(self._http, url) as r:
            return r.read().decode("utf-8")

    def _check(self, force: bool) -> "UpdateInfo | None":
        fake = self._fake()
        if fake is not None:
            return fake
        if not self._frozen:
            return None
        now = time.monotonic()
        if not force and self._checked_at and (now - self._checked_at) < _CHECK_TTL_S:
            return self._cache
        self._cache = None
        self._manifest = self._manifest_text = self._plan = None
        info = check_for_update(self.current, http=self._http, repo=self.repo)
        if info is not None:
            try:
                text = self._fetch_text(info.manifest_url)
                published = self._fetch_text(info.manifest_sha_url).split()
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if not published or published[0].strip().lower() != digest:
                    raise ValueError("manifest checksum mismatch")
                remote = parse_manifest(text)
                installed = self._read_installed()
                self._plan = build_plan(remote, installed, self._exe.parent,
                                        verify_local=force)
                self._manifest, self._manifest_text = remote, text
                self._cache = info
            except Exception:
                log.warning("update manifest rejected", exc_info=True)
        self._checked_at = now
        return self._cache

    def _read_installed(self) -> "Manifest | None":
        try:
            return parse_manifest(
                (self._exe.parent / INSTALLED_MANIFEST).read_text())
        except (OSError, ValueError):
            return None   # fresh/damaged record -> plan refetches everything
```

`status()` adds one key: `"download_bytes": self._plan.download_bytes if self._plan else None`.

`_run_apply` (replaces the old body; `begin_apply` unchanged apart from passing no `info`):

```python
    def begin_apply(self, on_success) -> dict:
        info = self._check(force=False)
        if not self._frozen or info is None or self._plan is None:
            return {"state": "error", "error": "no update available"}
        if os.environ.get("SM64_UPDATE_FAKE"):
            return {"state": "error", "error": "fake update"}
        with self._lock:
            if self._state in ("downloading", "installing"):
                return {"state": self._state}
            if not exe_dir_writable(self._exe.parent):
                return {"state": "error", "error": "exe folder not writable"}
            self._state = "downloading"
            self._progress = 0.0
        threading.Thread(
            target=self._run_apply,
            args=(info, self._plan, self._manifest_text, on_success),
            daemon=True, name="update-apply").start()
        return {"state": "downloading"}

    def _run_apply(self, info, plan, manifest_text, on_success) -> None:
        root = self._exe.parent
        staging = root / STAGING_DIR
        try:
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir(parents=True)
            needed = sum(e.size for e in plan.fetch)
            if not free_disk_ok(root, needed):
                raise RuntimeError("not enough free disk space for the update")
            try:
                fetch_plan(plan, info.zip_url, staging, http=self._http,
                           progress=self._set_progress)
            except RangeUnsupported:
                log.info("ranged download unavailable — full zip fallback")
                published = self._fetch_text(info.zip_sha_url).split()
                if not published:
                    raise ValueError("zip sha256 file is empty")
                fetch_full_zip(plan, info.zip_url, published[0], staging,
                               http=self._http, progress=self._set_progress)
            (staging / INSTALLED_MANIFEST).write_text(manifest_text)
            self._state = "installing"
            apply_plan(root, staging,
                       replace=[e.path for e in plan.fetch] + [INSTALLED_MANIFEST],
                       delete=list(plan.delete))
            shutil.rmtree(staging, ignore_errors=True)
            on_success()
        except Exception:
            log.exception("update apply failed")
            shutil.rmtree(staging, ignore_errors=True)
            self._state = "error"

    def startup_maintenance(self, bootstrap_path: "str | None" = None) -> None:
        """Background-reap update leftovers: the backup tree + finished
        journal from the last apply, and (post-migration) the bootstrap
        installer file + its .old sibling on whatever path the bootstrap
        handed us via --cleanup-bootstrap. Bounded retries: the previous
        process may still be exiting and holding locks."""
        if not self._frozen:
            return
        root = self._exe.parent

        def work():
            sweep_backup(root, attempts=60)
            if bootstrap_path:
                for leftover in (Path(bootstrap_path),
                                 Path(bootstrap_path + ".old")):
                    for attempt in range(60):
                        try:
                            leftover.unlink(missing_ok=True)
                            break
                        except OSError:
                            time.sleep(1.0)
        threading.Thread(target=work, daemon=True,
                         name="update-maintenance").start()
```

Also: `hashlib` is already imported; add the new imports at top; delete `download_and_stage`, `apply_update`, `cleanup_old`, `cleanup_old_exe`, and `EXE_NAME` (the name now lives in `update_plan.BOOTSTRAP_ASSET`; nothing in updater.py needs it).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS (main.py still calls `cleanup_old_exe` at this point — if that import breaks the suite, apply the ONE-LINE main.py change from Task 12 Step 3 now and note it in the commit; Task 12 still owns the rest of the wiring).

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/core/updater.py tests/test_updater.py tests/test_update_cycle.py src/sm64_events/main.py
git commit -m "feat(update): UpdateService drives manifest-sync pipeline (plan/fetch/apply)"
```

---

### Task 11 (Wave 2): Popup shows download size (`ui/components/update.js`)

**Files:**
- Modify: `src/sm64_events/ui/components/update.js`
- Verify only (no change expected): `server/update_api.py` passes `status()` straight through, so `download_bytes` already rides it.

**Interfaces:**
- Consumes: `status().download_bytes: number | null` from Task 10.

- [ ] **Step 1: Edit the actions block**

In `UpdatePopup`, in the non-applying branch (the `modal-actions` block), insert a size line ABOVE the buttons — replace:

```js
          : html`
            <div class="modal-actions">
```

with:

```js
          : html`
            ${st.download_bytes != null
              ? html`<div class="meta">Download size: ${
                  (st.download_bytes / 1048576).toFixed(1)} MB</div>`
              : ""}
            <div class="modal-actions">
```

- [ ] **Step 2: Verify by eye in dev**

Run (PowerShell): `$env:SM64_UPDATE_FAKE="1"; uv run python -m sm64_events.main` → open `http://127.0.0.1:8065` → popup renders WITHOUT a size line (fake has `download_bytes: null`) and nothing errors in the console. (The real size line is proven at the live gate; the fake never has a plan.)

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/update.js
git commit -m "feat(ui): update popup shows the exact download size"
```

---

### Task 12 (Wave 2): Startup wiring (`desktop/app.py`, `main.py`)

**Files:**
- Modify: `src/sm64_events/desktop/app.py` (startup repair before anything else)
- Modify: `src/sm64_events/main.py` (2 lines: maintenance call + cleanup arg helper)
- Test: `tests/test_desktop_repair.py` (new, small)

**Interfaces:**
- Consumes: `startup_repair` (Task 5), `install_root` (Task 3), `UpdateService.startup_maintenance` (Task 10).
- Produces: `_bootstrap_cleanup_arg(argv) -> str | None` in `main.py`; `_repair_interrupted_update() -> bool` in `desktop/app.py` (True = relaunched, caller returns immediately).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_desktop_repair.py
from sm64_events.main import _bootstrap_cleanup_arg


def test_bootstrap_cleanup_arg_parses():
    assert _bootstrap_cleanup_arg(
        ["exe", "--cleanup-bootstrap", r"C:\x\SM64Trainer.exe"]) == \
        r"C:\x\SM64Trainer.exe"
    assert _bootstrap_cleanup_arg(["exe"]) is None
    assert _bootstrap_cleanup_arg(["exe", "--cleanup-bootstrap"]) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_desktop_repair.py -q`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Wire `main.py`**

Add near the top of `main.py` (after imports):

```python
def _bootstrap_cleanup_arg(argv=None) -> "str | None":
    """--cleanup-bootstrap <path>: the bootstrap installer hands us its own
    exe path at launch; startup_maintenance deletes it (and its .old) once
    the bootstrap process has exited."""
    argv = sys.argv if argv is None else argv
    if "--cleanup-bootstrap" in argv:
        idx = argv.index("--cleanup-bootstrap")
        if idx + 1 < len(argv):
            return argv[idx + 1]
    return None
```

and replace the line `updater.cleanup_old_exe()   # delete a *.old left by a prior self-update` with:

```python
    updater.startup_maintenance(bootstrap_path=_bootstrap_cleanup_arg())
```

- [ ] **Step 4: Wire `desktop/app.py`**

Add after the imports:

```python
def _repair_interrupted_update() -> bool:
    """Roll back a crash-interrupted update swap BEFORE anything loads from
    the install tree. True => a relaunch was spawned; caller must return."""
    from sm64_events.core.paths import install_root, is_frozen
    if not is_frozen():
        return False
    from sm64_events.core.update_apply import startup_repair
    if startup_repair(install_root()) == "rolled_back":
        log.warning("interrupted update rolled back — relaunching")
        spawn_replacement()
        return True
    return False
```

and at the top of `main()`, immediately after `configure_logging()`:

```python
def main() -> None:
    configure_logging()
    if _repair_interrupted_update():
        return
```

(keep the existing body after that unchanged).

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: all PASS

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/main.py src/sm64_events/desktop/app.py tests/test_desktop_repair.py
git commit -m "feat(update): startup repair + bootstrap cleanup wiring"
```

---

### Task 13 (Wave 2): Release pipeline (`tools/release.py`)

**Files:**
- Modify: `tools/release.py`
- Test: `tests/test_release.py` (append)

**Interfaces:**
- Consumes: `build_zip`, `make_manifest` (Task 6); build outputs of Task 9.
- Produces: `write_sha(path: Path) -> Path` (writes `<name>.sha256` beside the file, returns its path); `release_assets(dist: Path) -> list[Path]` (the 6 upload paths, in order: zip, zip.sha, manifest, manifest.sha, exe, exe.sha).

- [ ] **Step 1: Write the failing tests** (append to `tests/test_release.py`)

```python
from pathlib import Path

from release import release_assets, write_sha


def test_write_sha_writes_verifiable_line(tmp_path):
    p = tmp_path / "SM64Trainer-full.zip"
    p.write_bytes(b"payload")
    side = write_sha(p)
    digest, name = side.read_text().split()
    assert name == "SM64Trainer-full.zip"
    import hashlib
    assert digest == hashlib.sha256(b"payload").hexdigest()


def test_release_assets_names_and_order(tmp_path):
    assets = release_assets(tmp_path)
    assert [a.name for a in assets] == [
        "SM64Trainer-full.zip", "SM64Trainer-full.zip.sha256",
        "manifest.json", "manifest.json.sha256",
        "SM64Trainer.exe", "SM64Trainer.exe.sha256"]
```

(If `tests/test_release.py` doesn't already import from `tools`, add at its top: `import sys; from pathlib import Path as _P; sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "tools"))`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_release.py -q`
Expected: FAIL — `ImportError: cannot import name 'release_assets'`

- [ ] **Step 3: Rework `tools/release.py`**

Replace the `EXE` constant block with:

```python
DIST = REPO / "dist"
APP_DIR = DIST / "SM64Trainer"                    # onedir build
APP_EXE = APP_DIR / "SM64Trainer.exe"
BOOTSTRAP_BUILD = DIST / "SM64TrainerSetup.exe"   # bootstrap onefile build
ZIP_PATH = DIST / "SM64Trainer-full.zip"
MANIFEST_PATH = DIST / "manifest.json"
UPLOAD_EXE = DIST / "SM64Trainer.exe"   # bootstrap copy under the asset name
```

Add helpers:

```python
def write_sha(path: Path) -> Path:
    digest = sha256_file(path)
    side = path.with_name(path.name + ".sha256")
    side.write_text(f"{digest}  {path.name}\n")
    return side


def release_assets(dist: Path) -> list[Path]:
    return [dist / "SM64Trainer-full.zip",
            dist / "SM64Trainer-full.zip.sha256",
            dist / "manifest.json", dist / "manifest.json.sha256",
            dist / "SM64Trainer.exe", dist / "SM64Trainer.exe.sha256"]
```

Replace the build/checksum section of `main()` (from `# Build first…` through `print("sha256", digest)`) with:

```python
    # Build first so a broken build aborts BEFORE any tag/push.
    _run(["uv", "run", "python", "tools/build_exe.py", "--mode", "all"])
    if not APP_EXE.exists():
        sys.exit("build did not produce dist/SM64Trainer/SM64Trainer.exe")
    if not BOOTSTRAP_BUILD.exists():
        sys.exit("build did not produce dist/SM64TrainerSetup.exe")
    import shutil as _shutil

    from make_manifest import build_zip, make_manifest
    print("zipping onedir tree…")
    build_zip(APP_DIR, ZIP_PATH)
    MANIFEST_PATH.write_text(make_manifest(ZIP_PATH, args.version))
    # The bootstrap is uploaded under the LOAD-BEARING name SM64Trainer.exe:
    # already-shipped onefile updaters can only install that asset, and it
    # migrates them to the onedir install (spec 2026-07-23).
    _shutil.copy2(BOOTSTRAP_BUILD, UPLOAD_EXE)
    for artifact in (ZIP_PATH, MANIFEST_PATH, UPLOAD_EXE):
        write_sha(artifact)
    print("assets ready:", ", ".join(a.name for a in release_assets(DIST)))
```

and the `gh release create` call becomes:

```python
    _run(["gh", "release", "create", tag,
          *[str(a) for a in release_assets(DIST)],
          "--title", tag, *notes])
```

Add `from make_manifest import …` is INSIDE `main()` (shown above) so importing `release.py` for its pure helpers needs no `sys.path` trick beyond the existing tools-dir convention. Update the module docstring's asset list accordingly.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_release.py -q` then `uv run pytest -q`
Expected: all PASS

- [ ] **Step 5: Commit**

```bash
git add tools/release.py tests/test_release.py
git commit -m "feat(release): publish 6-asset incremental-update releases"
```

---

### Task 14 (Wave 2): Documentation

**Files:**
- Modify: `README.md` (release-asset schema; update popup behavior; new-user install instructions: download SM64Trainer.exe = installer, or the zip for portable)
- Modify: `CLAUDE.md` module map — update rows: build (onedir + bootstrap modes), updater (split into updater/update_plan/update_fetch/update_apply), release (6 assets); add rows: `bootstrap/installer.py`, `tools/make_manifest.py`
- Modify: `docs/architecture.md` — add an "Incremental updates" section: the migration chain (old updater → bootstrap → onedir install), the journaled-swap invariants, and a placeholder table for the measured volatile set (filled at the live gate)

**Steps:**
- [ ] **Step 1:** Update the three docs per above. Keep CLAUDE.md rows one-line dense like their neighbors. In architecture.md, record the WHY facts with evidence pointers (spec + this plan).
- [ ] **Step 2:** `uv run pytest -q` (docs can't break tests — this is the pre-commit habit).
- [ ] **Step 3:** Commit:

```bash
git add README.md CLAUDE.md docs/architecture.md
git commit -m "docs: incremental update system (assets, module map, migration chain)"
```

---

## Live verification gate (with the human — after merge, before release)

1. `uv run python tools/build_exe.py` → confirm `dist/SM64Trainer/` runs (double-click `SM64Trainer.exe`: window, tray, PJ64 attach, replay OK) and `dist/SM64TrainerSetup.exe --silent` installs to `%LOCALAPPDATA%\Programs\SM64Trainer`, creates the Desktop shortcut, launches the app.
2. Build twice at the same commit → `manifest.json` byte-identical (reproducibility). Build at two adjacent commits → diff the manifests; record the measured volatile set + typical delta size in `docs/architecture.md`.
3. One real GitHub Range probe: `curl -H "Range: bytes=0-99" -L <any release asset url> -o probe.bin` → expect HTTP 206 + 100 bytes.
4. Cut a TEST release (`tools/release.py --dry-run` first, then a real prerelease tag on a scratch repo or the real one): old v1.3.x onefile install (the Desktop exe) sees the popup → Update → bootstrap runs → app installed + shortcut + old exe gone.
5. Cut a SECOND test release with a small code change → popup shows a small download size (~10-30 MB expected) → update applies → app relaunches → shortcut untouched → `data/` (PBs, replays, settings) untouched.
6. Kill the app mid-apply (Task Manager during "installing") → relaunch → confirm rollback + the old version still runs, then a clean re-update.
