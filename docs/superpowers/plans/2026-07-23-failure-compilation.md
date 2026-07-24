# Failure Compilation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-star/segment "Generate failure compilation" button that stitches every non-purged failure (windowed X-before/Y-after, ordered by how far into the run it happened) plus the fastest available successful run, into one MP4 with a Reveal-in-Explorer link.

**Architecture:** A pure planner (`tracking/compilation.py`) picks and orders the clips from attempt data + ring coverage; a builder (`replay/compilation.py`) reuses the existing `ClipExtractor` per window then runs one concat-filter ffmpeg pass to normalize+join; a job service (same file) mirrors `CompareService` for async progress; a thin router + kind-dispatched body serves both stars and segments; a shared UI component mounts on both practice cards.

**Tech Stack:** Python 3.12 (uv), FastAPI, ffmpeg (bundled), Preact + htm (vendored), pytest.

**Spec:** `docs/superpowers/specs/2026-07-23-failure-compilation-design.md`

## Global Constraints

- Python 3.12+ via **uv** (never pip). Full suite: `uv run pytest -q` MUST pass before merge.
- **Reuse `ClipExtractor.extract` unchanged** — do NOT edit `replay/extract.py`.
- Error taxonomy (all routers/services): `LookupError→404`, `ValueError→409`, `RuntimeError→503`.
- All timestamps **UTC**; ordering key is elapsed real time `ended_utc − started_utc`.
- **Star↔segment parity**: the feature ships on both practice cards in the same change (`tests/test_ui_section_parity.py` enforces it).
- **Browser↔GUI parity**: everything lands in `ui/` + server (no `desktop/` changes).
- Failure outcomes = `{reset, hard_reset, abandoned, death}`; include only `not cleared`.
- Output MP4 → `save_root/compilations/` so the existing `POST /api/replay/reveal` path-check permits opening it.
- Failures use **ring** coverage only; the finale (fastest success) may use a **saved** clip directly.

---

### Task 1: `compilations_dir()` path helper

**Files:**
- Modify: `src/sm64_events/core/paths.py` (add one function near `replays_root`)
- Test: `tests/test_paths.py` (create if absent, else append)

**Interfaces:**
- Produces: `compilations_dir() -> Path` = `replays_root() / "compilations"`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_paths.py` (create the file with this content if it doesn't exist):

```python
from sm64_events.core.paths import compilations_dir, replays_root


def test_compilations_dir_is_under_replays_root():
    d = compilations_dir()
    assert d.name == "compilations"
    assert d.parent == replays_root()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_paths.py::test_compilations_dir_is_under_replays_root -q`
Expected: FAIL with `ImportError: cannot import name 'compilations_dir'`.

- [ ] **Step 3: Add the function**

In `src/sm64_events/core/paths.py`, directly after the `replays_root()` function (around line 104), add:

```python
def compilations_dir() -> Path:
    # Generated failure compilations. Lives UNDER replays_root() (== the
    # ReplayService save_root) on purpose: the existing /api/replay/reveal
    # path-check only opens files inside save_root, so this dir is revealable
    # with no new endpoint. Filenames start with "compilation_" so the saved-
    # clip glob (attempt_*.mp4) never mistakes one for a saved attempt clip.
    return replays_root() / "compilations"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_paths.py::test_compilations_dir_is_under_replays_root -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/core/paths.py tests/test_paths.py
git commit -m "feat(paths): compilations_dir() under save_root for failure compilations"
```

---

### Task 2: Pure compilation planner (`tracking/compilation.py`)

**Files:**
- Create: `src/sm64_events/tracking/compilation.py`
- Test: `tests/test_compilation.py`

**Interfaces:**
- Produces:
  - `EntityRef(course_id=None, star_id=None, segment_id=None)` with `.matches(attempt) -> bool`.
  - `ClipSpec(attempt_id:int, kind:str, source:str, span_start:datetime|None, span_end:datetime|None, time_frames:int|None=None)`.
  - `CompilationPlan(specs:list[ClipSpec], failure_count:int, aged_out:int, no_finale:bool, finale_frames:int|None)`.
  - `plan_compilation(attempts, coverage, saved_ids, identity, x_before, y_after, pre_pad, post_pad) -> CompilationPlan` where `coverage` is `(datetime,datetime)|None` and `saved_ids` is a `set[int]`.
- Consumes: attempt objects with fields `id, course_id, star_id, segment_id, outcome, cleared, igt_frames, rta_frames, started_utc, ended_utc` (the `tracking.projection.Attempt` shape).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_compilation.py`:

```python
from datetime import datetime, timezone
from types import SimpleNamespace

from sm64_events.tracking.compilation import (EntityRef, plan_compilation)

STAR = EntityRef(course_id=1, star_id=0)


def _utc(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


# Coverage wide enough to contain every span in these tests.
WIDE = (_utc("2026-07-23T00:00:00Z"), _utc("2026-07-23T02:00:00Z"))


def att(**kw):
    base = dict(id=1, course_id=1, star_id=0, segment_id=None, outcome="death",
                cleared=False, igt_frames=None, rta_frames=None,
                started_utc="2026-07-23T00:10:00Z",
                ended_utc="2026-07-23T00:10:05Z")
    base.update(kw)
    return SimpleNamespace(**base)


def plan(attempts, coverage=WIDE, saved=frozenset(), x=5, y=3,
         identity=STAR):
    return plan_compilation(attempts, coverage, set(saved), identity, x, y,
                            pre_pad=3.0, post_pad=2.0)


def test_failures_ordered_by_elapsed_into_the_run():
    early = att(id=1, started_utc="2026-07-23T00:10:00Z",
                ended_utc="2026-07-23T00:10:05Z")   # 5 s in
    late = att(id=2, started_utc="2026-07-23T00:20:00Z",
               ended_utc="2026-07-23T00:20:25Z")    # 25 s in
    p = plan([late, early])
    assert [s.attempt_id for s in p.specs] == [1, 2]
    assert all(s.kind == "failure" for s in p.specs)


def test_ties_break_by_attempt_id():
    a = att(id=7, started_utc="2026-07-23T00:10:00Z",
            ended_utc="2026-07-23T00:10:05Z")
    b = att(id=3, started_utc="2026-07-23T00:30:00Z",
            ended_utc="2026-07-23T00:30:05Z")   # same 5 s elapsed
    p = plan([a, b])
    assert [s.attempt_id for s in p.specs] == [3, 7]


def test_only_failure_outcomes_and_uncleared_included():
    death = att(id=1, outcome="death")
    reset = att(id=2, outcome="reset")
    abandoned = att(id=3, outcome="abandoned")
    cleared = att(id=4, outcome="death", cleared=True)
    success = att(id=5, outcome="success", igt_frames=600)
    p = plan([death, reset, abandoned, cleared, success])
    fails = [s.attempt_id for s in p.specs if s.kind == "failure"]
    assert set(fails) == {1, 2, 3}


def test_entity_filtering_star_vs_segment():
    mine = att(id=1)
    other_star = att(id=2, star_id=1)
    a_segment = att(id=3, course_id=None, star_id=None, segment_id=9)
    p = plan([mine, other_star, a_segment])
    assert [s.attempt_id for s in p.specs] == [1]


def test_failure_outside_coverage_counts_as_aged_out():
    covered = att(id=1)
    tight = (_utc("2026-07-23T00:10:00Z"), _utc("2026-07-23T00:10:06Z"))
    # window is [end-5, end+3] = 00:10:00 .. 00:10:08 — end past coverage
    p = plan([covered], coverage=tight)
    assert p.specs == []
    assert p.aged_out == 1


def test_finale_is_fastest_available_success_in_full():
    slow = att(id=1, outcome="success", igt_frames=900,
               started_utc="2026-07-23T00:40:00Z",
               ended_utc="2026-07-23T00:40:30Z")
    fast = att(id=2, outcome="success", igt_frames=600,
               started_utc="2026-07-23T00:50:00Z",
               ended_utc="2026-07-23T00:50:20Z")
    p = plan([slow, fast])
    assert p.specs[-1].kind == "finale"
    assert p.specs[-1].attempt_id == 2
    assert p.specs[-1].source == "ring"
    assert p.finale_frames == 600
    assert p.no_finale is False


def test_finale_falls_back_to_saved_when_ring_missing():
    # fast run out of coverage but saved; slow run in coverage
    fast = att(id=2, outcome="success", igt_frames=600,
               started_utc="2025-01-01T00:00:00Z",
               ended_utc="2025-01-01T00:00:20Z")
    p = plan([fast], saved={2})
    assert p.specs[-1].source == "saved"
    assert p.specs[-1].attempt_id == 2
    assert p.finale_frames == 600


def test_no_finale_when_no_success_available():
    fail = att(id=1)
    p = plan([fail])
    assert p.no_finale is True
    assert p.finale_frames is None
    assert p.specs[-1].kind == "failure"


def test_success_without_a_time_is_not_a_finale():
    timeless = att(id=1, outcome="success", igt_frames=None, rta_frames=None)
    p = plan([timeless])
    assert p.no_finale is True
    assert p.specs == []


def test_segment_finale_uses_rta_frames():
    seg = EntityRef(segment_id=9)
    run = att(id=1, course_id=None, star_id=None, segment_id=9,
              outcome="success", igt_frames=None, rta_frames=450,
              started_utc="2026-07-23T00:10:00Z",
              ended_utc="2026-07-23T00:10:15Z")
    p = plan([run], identity=seg)
    assert p.finale_frames == 450
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_compilation.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sm64_events.tracking.compilation'`.

- [ ] **Step 3: Write the module**

Create `src/sm64_events/tracking/compilation.py`:

```python
"""Pure planner for a failure compilation (spec 2026-07-23).

Given one entity's attempts plus what footage is reachable right now, decide
WHICH clips go into the compilation and IN WHAT ORDER — no ffmpeg, no
filesystem — so the whole selection/ordering contract is unit-tested on plain
data. The builder (replay/compilation.py) turns the plan into a video.

Ordering (spec §3.2): failures play in the order they'd occur during a run —
by elapsed real time from the run's start anchor (ended_utc - started_utc), a
metric defined for every failure type (unlike IGT, which resets/deaths often
leave None). The finale is the fastest available successful run, in full, last.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta

FAILURE_OUTCOMES = frozenset({"reset", "hard_reset", "abandoned", "death"})


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass(frozen=True)
class EntityRef:
    """Which practiced thing to compile. Star sets course_id+star_id (segment
    None); segment sets segment_id. matches() mirrors projection's attempt
    identity."""
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None

    def matches(self, a) -> bool:
        if self.segment_id is not None:
            return a.segment_id == self.segment_id
        return (a.segment_id is None and a.course_id == self.course_id
                and a.star_id == self.star_id)


@dataclass(frozen=True)
class ClipSpec:
    attempt_id: int
    kind: str                     # "failure" | "finale"
    source: str                   # "ring" | "saved"
    span_start: datetime | None   # None for a saved finale (use the whole file)
    span_end: datetime | None
    time_frames: int | None = None   # finale only: displayed time for the summary


@dataclass(frozen=True)
class CompilationPlan:
    specs: list                   # ordered ClipSpec; finale (if any) is last
    failure_count: int            # included failures (excludes aged-out)
    aged_out: int                 # failures with no footage in the ring
    no_finale: bool
    finale_frames: int | None


def _time_of(a) -> int | None:
    return a.igt_frames if a.igt_frames is not None else a.rta_frames


def _elapsed_s(a) -> float:
    return (_parse_utc(a.ended_utc) - _parse_utc(a.started_utc)).total_seconds()


def _covered(coverage, start: datetime, end: datetime) -> bool:
    """Ring outer envelope contains [start, end]. Interior coverage holes are
    handled at extract time (the builder drops a window that fails to cut)."""
    if coverage is None:
        return False
    cov_start, cov_end = coverage
    return cov_start <= start and end <= cov_end


def plan_compilation(attempts, coverage, saved_ids, identity: EntityRef,
                     x_before: float, y_after: float,
                     pre_pad: float, post_pad: float) -> CompilationPlan:
    ours = [a for a in attempts if identity.matches(a)]

    failures = [a for a in ours
                if a.outcome in FAILURE_OUTCOMES and not a.cleared]
    specs: list[ClipSpec] = []
    aged_out = 0
    for a in sorted(failures, key=lambda a: (_elapsed_s(a), a.id)):
        end = _parse_utc(a.ended_utc)
        span_start = end - timedelta(seconds=x_before)
        span_end = end + timedelta(seconds=y_after)
        if _covered(coverage, span_start, span_end):
            specs.append(ClipSpec(attempt_id=a.id, kind="failure",
                                  source="ring", span_start=span_start,
                                  span_end=span_end))
        else:
            aged_out += 1

    finale = None
    successes = [a for a in ours
                 if a.outcome == "success" and not a.cleared
                 and _time_of(a) is not None]
    for a in sorted(successes, key=_time_of):
        full_start = _parse_utc(a.started_utc) - timedelta(seconds=pre_pad)
        full_end = _parse_utc(a.ended_utc) + timedelta(seconds=post_pad)
        if _covered(coverage, full_start, full_end):
            finale = ClipSpec(attempt_id=a.id, kind="finale", source="ring",
                              span_start=full_start, span_end=full_end,
                              time_frames=_time_of(a))
            break
        if a.id in saved_ids:
            finale = ClipSpec(attempt_id=a.id, kind="finale", source="saved",
                              span_start=None, span_end=None,
                              time_frames=_time_of(a))
            break

    ordered = list(specs)
    if finale is not None:
        ordered.append(finale)
    return CompilationPlan(specs=ordered, failure_count=len(specs),
                           aged_out=aged_out, no_finale=finale is None,
                           finale_frames=finale.time_frames if finale else None)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_compilation.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/compilation.py tests/test_compilation.py
git commit -m "feat(tracking): pure failure-compilation planner (elapsed order + fastest-run finale)"
```

---

### Task 3: Builder + job service (`replay/compilation.py`)

**Files:**
- Create: `src/sm64_events/replay/compilation.py`
- Test: `tests/test_compilation_builder.py`, `tests/test_compilation_service.py`

**Interfaces:**
- Consumes: `plan_compilation`, `EntityRef`, `ClipSpec` (Task 2); `ClipExtractor.extract(ring, start, end, out)`; `video_quality_args`, `CLIP_MAXRATE` (config); `_slug` (replay.service); `format_igt` (core.timefmt); `course_name`/`star_name` (memory.addresses).
- Produces:
  - `CompilationBuilder(extractor, codec:str, fps:int, ffmpeg:str|None=None)` with `.build(specs, ring, tmp_dir, out_path, resolve_saved, progress_cb) -> CompilationResult`.
  - `CompilationResult(path:Path, clip_count:int, skipped_runtime:int)`.
  - `CompilationService(replay, tracker, builder, out_dir)` with `.start(identity, x_before, y_after) -> str` and `.status(job_id) -> dict`.
  - `_entity_slug(tracker, identity) -> str`.

- [ ] **Step 1: Write the failing builder tests**

Create `tests/test_compilation_builder.py`:

```python
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sm64_events.replay import compilation as comp
from sm64_events.tracking.compilation import ClipSpec


def _spec(aid, kind="failure", source="ring"):
    now = datetime.now(timezone.utc)
    return ClipSpec(attempt_id=aid, kind=kind, source=source,
                    span_start=now, span_end=now)


class FakeExtractor:
    """Writes a stub file where a real cut would go; can be told to fail one."""
    def __init__(self, fail_ids=()):
        self.calls = []
        self._fail = set(fail_ids)

    def extract(self, ring, start, end, out_path):
        self.calls.append(out_path)
        # attempt id is embedded by the builder via part_NNN — use call order
        if len(self.calls) - 1 in self._fail:
            raise ValueError("no footage overlaps the requested span")
        Path(out_path).write_bytes(b"stub")
        return None


def _builder(extractor, runner):
    b = comp.CompilationBuilder(extractor, codec="libx264", fps=60,
                                ffmpeg="ffmpeg")
    b._run = runner            # override the raw subprocess call (see impl)
    return b


def test_multi_clip_runs_concat_with_all_inputs(tmp_path, monkeypatch):
    calls = {}

    def fake_run(args):
        if "-filter_complex" in args:
            calls["concat"] = args
            Path(args[-1]).write_bytes(b"out")
        else:                                   # probe
            calls["probe"] = args
        return subprocess.CompletedProcess(args, 0, b"", b"Video: h264, 1280x960")

    b = _builder(FakeExtractor(), fake_run)
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1), _spec(2, kind="finale")], ring=None,
                  tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: None, progress_cb=lambda f, m: None)
    assert res.clip_count == 2 and res.skipped_runtime == 0
    assert "-filter_complex" in calls["concat"]
    assert calls["concat"].count("-i") == 2      # both clips fed
    assert "concat=n=2" in " ".join(calls["concat"])
    assert out.exists()


def test_single_clip_is_copied_not_concatenated(tmp_path):
    def fake_run(args):
        raise AssertionError("ffmpeg should not run for a single clip")

    b = _builder(FakeExtractor(), fake_run)
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1)], ring=None, tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: None, progress_cb=lambda f, m: None)
    assert res.clip_count == 1
    assert out.read_bytes() == b"stub"


def test_runtime_extract_failure_is_skipped(tmp_path):
    def fake_run(args):
        Path(args[-1]).write_bytes(b"out")
        return subprocess.CompletedProcess(args, 0, b"", b"Video: 1280x960")

    b = _builder(FakeExtractor(fail_ids={0}), fake_run)   # first spec fails
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1), _spec(2), _spec(3, kind="finale")], ring=None,
                  tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: None, progress_cb=lambda f, m: None)
    assert res.skipped_runtime == 1
    assert res.clip_count == 2


def test_saved_finale_uses_resolved_path(tmp_path):
    saved = tmp_path / "pb.mp4"
    saved.write_bytes(b"pb")

    def fake_run(args):
        Path(args[-1]).write_bytes(b"out")
        return subprocess.CompletedProcess(args, 0, b"", b"Video: 1280x960")

    b = _builder(FakeExtractor(), fake_run)
    out = tmp_path / "c.mp4"
    res = b.build([_spec(1), _spec(9, kind="finale", source="saved")],
                  ring=None, tmp_dir=tmp_path / "t", out_path=out,
                  resolve_saved=lambda i: saved if i == 9 else None,
                  progress_cb=lambda f, m: None)
    assert res.clip_count == 2


def test_concat_failure_unlinks_output(tmp_path):
    out = tmp_path / "c.mp4"

    def fake_run(args):
        if "-filter_complex" in args:
            raise subprocess.CalledProcessError(1, args, b"", b"boom")
        return subprocess.CompletedProcess(args, 0, b"", b"Video: 1280x960")

    b = _builder(FakeExtractor(), fake_run)
    with pytest.raises(RuntimeError):
        b.build([_spec(1), _spec(2)], ring=None, tmp_dir=tmp_path / "t",
                out_path=out, resolve_saved=lambda i: None,
                progress_cb=lambda f, m: None)
    assert not out.exists()


def test_empty_after_all_skipped_raises(tmp_path):
    def fake_run(args):
        return subprocess.CompletedProcess(args, 0, b"", b"")

    b = _builder(FakeExtractor(fail_ids={0}), fake_run)
    with pytest.raises(ValueError):
        b.build([_spec(1)], ring=None, tmp_dir=tmp_path / "t",
                out_path=tmp_path / "c.mp4", resolve_saved=lambda i: None,
                progress_cb=lambda f, m: None)
```

- [ ] **Step 2: Run builder tests to verify they fail**

Run: `uv run pytest tests/test_compilation_builder.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sm64_events.replay.compilation'`.

- [ ] **Step 3: Write the module (builder half)**

Create `src/sm64_events/replay/compilation.py` with the imports, `CompilationResult`, and `CompilationBuilder`:

```python
"""Build a failure compilation and run it as a background job.

Reuses ClipExtractor.extract per window (it already cuts one A/V-synced,
coverage-honouring clip), then ONE concat-filter ffmpeg pass scales+pads every
clip to a common canvas and re-encodes to a single MP4 — clips span sessions
and window resizes, so a `-c copy` concat would corrupt on a size mismatch
(the same frame-size hazard extract.py guards). The re-encode uses the shared
constant-quality target so the compilation is never softer than its source.

The job registry mirrors CompareService: start() returns a job id immediately,
a daemon thread does plan+build updating progress/message/state, status() is
polled. The output lives under save_root/compilations so the existing
/api/replay/reveal opens it with no new endpoint.
"""
import logging
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.replay.config import CLIP_MAXRATE, video_quality_args
from sm64_events.replay.service import _slug
from sm64_events.tracking.compilation import EntityRef, plan_compilation

log = logging.getLogger("sm64.compilation")

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_DIMS_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


@dataclass(frozen=True)
class CompilationResult:
    path: Path
    clip_count: int
    skipped_runtime: int


class CompilationBuilder:
    """extract each spec -> concat/normalize into one mp4. ffmpeg-bound; the
    extractor + codec/fps are injected so tests can drive it without video."""

    def __init__(self, extractor, codec: str, fps: int, ffmpeg: str | None = None):
        self._extractor = extractor
        self._codec = codec
        self._fps = fps
        self._ffmpeg = ffmpeg or bundled_ffmpeg() or shutil.which("ffmpeg")

    def _run(self, args) -> subprocess.CompletedProcess:
        """The one raw subprocess call — a seam so tests stub ffmpeg. Raises
        CalledProcessError on non-zero (callers that must fail-hard pass check
        semantics by inspecting returncode; concat relies on this)."""
        return subprocess.run(args, check=True, capture_output=True,
                              creationflags=_NO_WINDOW)

    def build(self, specs, ring, tmp_dir: Path, out_path: Path,
              resolve_saved, progress_cb) -> CompilationResult:
        if not self._ffmpeg:
            raise RuntimeError("ffmpeg binary not available for compilation")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []
        finale_clip: Path | None = None
        skipped = 0
        total = len(specs)
        for i, spec in enumerate(specs):
            progress_cb(i / max(1, total), f"cutting {i + 1}/{total}")
            if spec.source == "saved":
                part = resolve_saved(spec.attempt_id)
                if part is None:
                    skipped += 1
                    continue
            else:
                part = tmp_dir / f"part_{i:03d}.mp4"
                try:
                    self._extractor.extract(ring, spec.span_start,
                                            spec.span_end, part)
                except ValueError:
                    skipped += 1        # ring shifted since planning — drop it
                    continue
            clips.append(part)
            if spec.kind == "finale":
                finale_clip = part
        if not clips:
            raise ValueError("nothing to compile — no footage available")
        progress_cb(0.95, "stitching")
        if len(clips) == 1:
            shutil.copy2(clips[0], out_path)     # single clip: no concat needed
        else:
            canvas = self._probe_dims(finale_clip or clips[0]) or (1280, 960)
            self._concat_normalize(clips, canvas, out_path)
        return CompilationResult(path=out_path, clip_count=len(clips),
                                 skipped_runtime=skipped)

    def _probe_dims(self, path: Path):
        """(w, h) via ffmpeg's own probe (no ffprobe dependency): `-i` with no
        output exits non-zero and prints the stream line to stderr — expected,
        we only read the text. Returns None if unparseable (falls back to a
        default canvas)."""
        out = subprocess.run([self._ffmpeg, "-hide_banner", "-i", str(path)],
                             capture_output=True, creationflags=_NO_WINDOW)
        m = _DIMS_RE.search(out.stderr.decode("utf-8", "replace"))
        return (int(m.group(1)), int(m.group(2))) if m else None

    def _concat_normalize(self, clips, canvas, out_path: Path) -> None:
        w, h = canvas
        inputs: list[str] = []
        for p in clips:
            inputs += ["-i", str(p)]
        parts, labels = [], ""
        for idx in range(len(clips)):
            parts.append(
                f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={self._fps},"
                f"format=yuv420p[v{idx}]")
            parts.append(f"[{idx}:a]aresample=async=1[a{idx}]")
            labels += f"[v{idx}][a{idx}]"
        parts.append(f"{labels}concat=n={len(clips)}:v=1:a=1[v][a]")
        args = [self._ffmpeg, "-hide_banner", "-loglevel", "error", *inputs,
                "-filter_complex", ";".join(parts),
                "-map", "[v]", "-map", "[a]",
                "-c:v", self._codec,
                *video_quality_args(self._codec, "offline", CLIP_MAXRATE),
                "-c:a", "aac", "-ar", "48000",
                "-movflags", "+faststart", "-y", str(out_path)]
        try:
            self._run(args)
        except subprocess.CalledProcessError as exc:
            out_path.unlink(missing_ok=True)
            raise RuntimeError(
                "ffmpeg compilation failed: "
                + exc.stderr.decode("utf-8", "replace")[-500:]) from exc
```

> Note: tests override `builder._run` with a fake and pre-empt `_probe_dims` via the fake's stderr. `_concat_normalize` calls `self._run(args)`; `_probe_dims` calls `subprocess.run` directly (a probe, not the failure-critical path) — the fake in `test_multi_clip...` records it but the builder tolerates any/None result.

- [ ] **Step 4: Run builder tests to verify they pass**

Run: `uv run pytest tests/test_compilation_builder.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Write the failing service tests**

Create `tests/test_compilation_service.py`:

```python
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.replay import compilation as comp
from sm64_events.tracking.compilation import (ClipSpec, CompilationPlan,
                                              EntityRef)


class FakeRing:
    def coverage(self, kind):
        return None


class FakeReplay:
    def __init__(self):
        self.recorder = SimpleNamespace(ring=FakeRing())
        self.pre_pad_s = 3.0
        self.post_pad_s = 2.0

    def _saved_attempt_ids(self):
        return set()

    def find_saved(self, attempt_id):
        return None


class FakeDB:
    def attempts(self):
        return []


class FakeTracker:
    db = FakeDB()
    segment_defs = []


class FakeBuilder:
    def __init__(self):
        self.built = None

    def build(self, specs, ring, tmp_dir, out_path, resolve_saved, progress_cb):
        progress_cb(0.5, "cutting 1/1")
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        Path(out_path).write_bytes(b"x")
        self.built = specs
        return comp.CompilationResult(path=Path(out_path), clip_count=len(specs),
                                      skipped_runtime=0)


def _service(tmp_path, plan):
    svc = comp.CompilationService(FakeReplay(), FakeTracker(), FakeBuilder(),
                                  out_dir=tmp_path)
    # deterministic plan (no real attempts needed)
    import sm64_events.replay.compilation as mod
    mod.plan_compilation = lambda *a, **k: plan
    return svc


def _plan(specs, **kw):
    base = dict(failure_count=len(specs), aged_out=0, no_finale=True,
                finale_frames=None)
    base.update(kw)
    return CompilationPlan(specs=specs, **base)


def _spec(aid):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    return ClipSpec(aid, "failure", "ring", now, now)


def test_start_rejects_negative_window(tmp_path):
    svc = _service(tmp_path, _plan([_spec(1)]))
    with pytest.raises(ValueError):
        svc.start(EntityRef(course_id=1, star_id=0), -1, 3)


def test_run_job_produces_result(tmp_path):
    svc = _service(tmp_path, _plan([_spec(1)], finale_frames=600,
                                   no_finale=False))
    svc._jobs["j"] = {"state": "running", "progress": 0.0,
                      "message": "planning", "result": None}
    svc._run_job("j", EntityRef(course_id=1, star_id=0), 5.0, 3.0)
    job = svc._jobs["j"]
    assert job["state"] == "done"
    assert job["result"]["clip_count"] == 1
    assert job["result"]["finale_time"] is not None
    assert Path(job["result"]["path"]).exists()


def test_run_job_errors_on_empty_plan(tmp_path):
    svc = _service(tmp_path, _plan([]))
    svc._jobs["j"] = {"state": "running", "progress": 0.0,
                      "message": "planning", "result": None}
    svc._run_job("j", EntityRef(course_id=1, star_id=0), 5.0, 3.0)
    assert svc._jobs["j"]["state"] == "error"


def test_status_unknown_job_raises(tmp_path):
    svc = _service(tmp_path, _plan([_spec(1)]))
    with pytest.raises(LookupError):
        svc.status("nope")
```

- [ ] **Step 6: Run service tests to verify they fail**

Run: `uv run pytest tests/test_compilation_service.py -q`
Expected: FAIL with `AttributeError: module ... has no attribute 'CompilationService'`.

- [ ] **Step 7: Append the service to the module**

Append to `src/sm64_events/replay/compilation.py`:

```python
def _entity_slug(tracker, identity: EntityRef) -> str:
    if identity.segment_id is not None:
        name = next((d.name for d in tracker.segment_defs
                     if d.id == identity.segment_id),
                    f"segment-{identity.segment_id}")
        return _slug(name) or f"segment-{identity.segment_id}"
    course = course_name(identity.course_id)
    star = star_name(identity.course_id, identity.star_id)
    return "-".join(p for p in (_slug(course), _slug(star)) if p) or "star"


class CompilationService:
    """Async job registry over CompilationBuilder (mirrors CompareService).

    Composes the ReplayService for its ring / extractor pads / saved-clip
    lookup so the compilation and a normal replay clip always cut from the same
    live buffer with the same padding.
    """

    def __init__(self, replay, tracker, builder: CompilationBuilder,
                 out_dir: Path):
        self.replay = replay
        self.tracker = tracker
        self.builder = builder
        self.out_dir = out_dir
        self._jobs: dict[str, dict] = {}

    def start(self, identity: EntityRef, x_before: float, y_after: float) -> str:
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        if x_before < 0 or y_after < 0:
            raise ValueError("x_before and y_after must be >= 0")
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"state": "running", "progress": 0.0,
                              "message": "planning", "result": None}
        threading.Thread(
            target=self._run_job, name="compilation", daemon=True,
            args=(job_id, identity, float(x_before), float(y_after))).start()
        return job_id

    def _run_job(self, job_id, identity, x_before, y_after) -> None:
        job = self._jobs[job_id]

        def progress(frac, msg):
            job["progress"] = frac
            job["message"] = msg

        try:
            ring = self.replay.recorder.ring
            plan = plan_compilation(
                list(self.tracker.db.attempts()), ring.coverage("video"),
                self.replay._saved_attempt_ids(), identity, x_before, y_after,
                self.replay.pre_pad_s, self.replay.post_pad_s)
            if not plan.specs:
                raise ValueError(
                    "nothing to compile — no failures with footage and no "
                    "available successful run")
            slug = _entity_slug(self.tracker, identity)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            out_path = self.out_dir / f"compilation_{slug}_{stamp}.mp4"
            tmp_dir = self.out_dir / f".build_{job_id}"
            res = self.builder.build(plan.specs, ring, tmp_dir, out_path,
                                     self.replay.find_saved, progress)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            job["result"] = {
                "path": str(out_path),
                "clip_count": res.clip_count,
                "skipped": plan.aged_out + res.skipped_runtime,
                "no_finale": plan.no_finale,
                "finale_time": (format_igt(plan.finale_frames)
                                if plan.finale_frames is not None else None)}
            job["progress"] = 1.0
            job["message"] = "done"
            job["state"] = "done"
        except Exception as e:
            log.exception("compilation failed")
            job["state"] = "error"
            job["message"] = str(e)

    def status(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise LookupError("no such compilation job")
        return dict(job)   # shallow copy: callers never mutate live job state
```

- [ ] **Step 8: Run service tests to verify they pass**

Run: `uv run pytest tests/test_compilation_service.py tests/test_compilation_builder.py -q`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/sm64_events/replay/compilation.py tests/test_compilation_builder.py tests/test_compilation_service.py
git commit -m "feat(replay): CompilationBuilder + CompilationService (extract windows -> concat mux, async job)"
```

---

### Task 4: REST surface + wiring (`compilation_api.py`, `app.py`, `main.py`)

**Files:**
- Create: `src/sm64_events/server/compilation_api.py`
- Modify: `src/sm64_events/server/app.py` (create_app signature + router mount, ~line 247 and ~366)
- Modify: `src/sm64_events/main.py` (build the service, pass to create_app)
- Test: `tests/test_compilation_api.py`

**Interfaces:**
- Consumes: `CompilationService.start/status` (Task 3), `EntityRef` (Task 2), `compilations_dir` (Task 1).
- Produces: `create_compilation_router(service) -> APIRouter` with `POST /api/compilation` (body below) and `GET /api/compilation/{job_id}`.
  - Body: `{star:{course_id,star_id}} | {segment_id}`, `x_before:float=5.0`, `y_after:float=3.0`.

- [ ] **Step 1: Write the failing API test**

Create `tests/test_compilation_api.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sm64_events.server.compilation_api import create_compilation_router
from sm64_events.tracking.compilation import EntityRef


class FakeService:
    def __init__(self):
        self.started = None

    def start(self, identity, x_before, y_after):
        if x_before < 0 or y_after < 0:
            raise ValueError("x_before and y_after must be >= 0")
        self.started = (identity, x_before, y_after)
        return "job123"

    def status(self, job_id):
        if job_id != "job123":
            raise LookupError("no such compilation job")
        return {"state": "done", "result": {"path": "x.mp4"}}


def _client(svc):
    app = FastAPI()
    app.include_router(create_compilation_router(svc))
    return TestClient(app)


def test_star_body_dispatches_to_star_identity():
    svc = FakeService()
    r = _client(svc).post("/api/compilation",
                          json={"star": {"course_id": 1, "star_id": 0},
                                "x_before": 5, "y_after": 3})
    assert r.status_code == 200
    assert r.json()["job_id"] == "job123"
    assert svc.started[0] == EntityRef(course_id=1, star_id=0)


def test_segment_body_dispatches_to_segment_identity():
    svc = FakeService()
    r = _client(svc).post("/api/compilation",
                          json={"segment_id": 9, "x_before": 4, "y_after": 2})
    assert r.status_code == 200
    assert svc.started[0] == EntityRef(segment_id=9)


def test_both_kinds_is_409():
    r = _client(FakeService()).post(
        "/api/compilation",
        json={"star": {"course_id": 1, "star_id": 0}, "segment_id": 9})
    assert r.status_code == 409


def test_neither_kind_is_409():
    r = _client(FakeService()).post("/api/compilation", json={})
    assert r.status_code == 409


def test_negative_window_is_409():
    r = _client(FakeService()).post(
        "/api/compilation",
        json={"segment_id": 9, "x_before": -1, "y_after": 2})
    assert r.status_code == 409


def test_status_known_and_unknown():
    c = _client(FakeService())
    assert c.get("/api/compilation/job123").json()["state"] == "done"
    assert c.get("/api/compilation/nope").status_code == 404
```

- [ ] **Step 2: Run the API test to verify it fails**

Run: `uv run pytest tests/test_compilation_api.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'sm64_events.server.compilation_api'`.

- [ ] **Step 3: Write the router**

Create `src/sm64_events/server/compilation_api.py`:

```python
# src/sm64_events/server/compilation_api.py
"""Failure-compilation REST surface. Same taxonomy as replay_api.py:
LookupError->404, ValueError->409, RuntimeError->503. Generation is a polled
job (dozens of ffmpeg cuts + a concat pass). Reveal reuses /api/replay/reveal
— the output lives under save_root, so no compilation-specific reveal exists.

Kind-dispatched body ({star:{...}} XOR {segment_id}) matches the app's other
star<->segment endpoints, so one path serves both and can't drift."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.tracking.compilation import EntityRef


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


class StarRef(BaseModel):
    course_id: int
    star_id: int


class CompileBody(BaseModel):
    star: StarRef | None = None
    segment_id: int | None = None
    x_before: float = 5.0
    y_after: float = 3.0


def create_compilation_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.post("/compilation")
    def start(body: CompileBody):
        if (body.star is None) == (body.segment_id is None):
            raise HTTPException(409, "provide exactly one of star or segment_id")
        identity = (EntityRef(segment_id=body.segment_id) if body.star is None
                    else EntityRef(course_id=body.star.course_id,
                                   star_id=body.star.star_id))
        try:
            job_id = service.start(identity, body.x_before, body.y_after)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"job_id": job_id}

    @router.get("/compilation/{job_id}")
    def status(job_id: str):
        try:
            return service.status(job_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    return router
```

- [ ] **Step 4: Run the API test to verify it passes**

Run: `uv run pytest tests/test_compilation_api.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Wire into `create_app`**

In `src/sm64_events/server/app.py`, change the `create_app` signature (line ~247-249) to add `compilation=None`:

```python
def create_app(poller: Poller, broadcaster: Broadcaster,
               service=None, replay=None, updater=None, compare=None,
               compilation=None, db_retry=None, debug_hooks: bool = False) -> FastAPI:
```

Then directly after the `compare` router block (after line ~368, before the `if updater is not None:` block), add:

```python
    if compilation is not None:
        from sm64_events.server.compilation_api import create_compilation_router
        app.include_router(create_compilation_router(compilation))
```

- [ ] **Step 6: Build the service in `main.py`**

In `src/sm64_events/main.py`, add imports near the other replay imports (after line 31 `from sm64_events.replay.service import ReplayService`):

```python
from sm64_events.replay.compilation import CompilationBuilder, CompilationService
```

Add `compilations_dir` to the `core.paths` import (line 8-10 block):

```python
from sm64_events.core.paths import (bundled_ffmpeg, compare_cache_dir,
                                    compilations_dir, db_path,
                                    instance_lock_path, migrate_legacy_data_dir,
                                    server_port)
```

After the `compare` block (after line ~158, before the `# Order is load-bearing` detectors comment), add:

```python
    # Failure compilations reuse the replay ring + extractor; only built when
    # replay AND the db are available (needs attempts + footage).
    compilation = None
    if replay is not None and db is not None:
        compilation = CompilationService(
            replay=replay, tracker=service,
            builder=CompilationBuilder(extractor=replay.extractor, codec=codec,
                                       fps=replay_cfg.fps),
            out_dir=compilations_dir())
```

Change the final `return create_app(...)` (line ~182) to pass `compilation`:

```python
    return create_app(poller, broadcaster, service=service, replay=replay,
                      updater=updater, compare=compare, compilation=compilation,
                      db_retry=db_retry)
```

- [ ] **Step 7: Verify the full suite still imports/passes**

Run: `uv run pytest tests/test_compilation_api.py -q && uv run python -c "import sm64_events.main"`
Expected: tests PASS; import prints nothing and exits 0 (no wiring typo).

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/server/compilation_api.py src/sm64_events/server/app.py src/sm64_events/main.py tests/test_compilation_api.py
git commit -m "feat(server): /api/compilation router + wire CompilationService into the app"
```

---

### Task 5: UI component + practice-card mounts + parity

**Files:**
- Create: `src/sm64_events/ui/components/failcomp.js`
- Modify: `src/sm64_events/ui/components/practice.js` (import + mount in `StarSection` and `SegmentSection` detail drawers)
- Modify: `tests/test_ui_section_parity.py` (add the explicit assertion)

**Interfaces:**
- Consumes: `POST /api/compilation`, `GET /api/compilation/{job_id}`, `POST /api/replay/reveal` (Task 4 + existing).
- Produces: `FailureCompilation({identity})` where `identity` is `{course_id, star_id}` (star) or `{segment_id}` (segment).

- [ ] **Step 1: Add the failing parity assertion**

In `tests/test_ui_section_parity.py`, after `test_both_cards_offer_a_strategy_picker` (line ~64), add:

```python
def test_both_cards_offer_a_failure_compilation():
    """Failure compilation must ship on stars AND segments (spec 2026-07-23)."""
    source = PRACTICE_JS.read_text(encoding="utf-8")
    for name in ("StarSection", "SegmentSection"):
        assert "FailureCompilation" in _components(_body(source, name)), \
            f"{name} is missing the failure-compilation control"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_ui_section_parity.py::test_both_cards_offer_a_failure_compilation -q`
Expected: FAIL — `StarSection is missing the failure-compilation control`.

- [ ] **Step 3: Write the component**

Create `src/sm64_events/ui/components/failcomp.js`:

```javascript
// src/sm64_events/ui/components/failcomp.js
// Shared "Generate failure compilation" control for a star OR segment practice
// card (star<->segment parity — tests/test_ui_section_parity.py). Posts to
// /api/compilation, polls the job, then shows the output path with a
// Reveal-in-Explorer button (reuses /api/replay/reveal — the output lives under
// save_root). Identity dispatch mirrors the server body: {segment_id} vs {star}.
import { h } from "preact";
import { useEffect, useRef, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";

const html = htm.bind(h);

function stored(key, fallback) {
  const v = parseFloat(localStorage.getItem(key));
  return Number.isFinite(v) ? v : fallback;
}

export function FailureCompilation({ identity }) {
  const [xBefore, setXBefore] = useState(() => stored("sm64.failcomp.xBefore", 5));
  const [yAfter, setYAfter] = useState(() => stored("sm64.failcomp.yAfter", 3));
  const [job, setJob] = useState(null);   // {state, progress, message, result}
  const pollRef = useRef(null);
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  function setX(v) { setXBefore(v); localStorage.setItem("sm64.failcomp.xBefore", v); }
  function setY(v) { setYAfter(v); localStorage.setItem("sm64.failcomp.yAfter", v); }

  async function generate() {
    setJob({ state: "running", progress: 0, message: "starting" });
    const target = identity.segment_id != null
      ? { segment_id: identity.segment_id }
      : { star: { course_id: identity.course_id, star_id: identity.star_id } };
    let r;
    try {
      r = await send("POST", "/api/compilation",
        { x_before: xBefore, y_after: yAfter, ...target });
    } catch (e) { setJob({ state: "error", message: String(e.message || e) }); return; }
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const s = await getJSON(`/api/compilation/${r.job_id}`);
        setJob(s);
        if (s.state === "done" || s.state === "error") {
          clearInterval(pollRef.current); pollRef.current = null;
        }
      } catch (e) {
        clearInterval(pollRef.current); pollRef.current = null;
        setJob({ state: "error", message: String(e.message || e) });
      }
    }, 800);
  }

  async function reveal(path) {
    try { await send("POST", "/api/replay/reveal", { path }); } catch { /* best effort */ }
  }

  const running = job && job.state === "running";
  const res = job && job.state === "done" && job.result;
  return html`<div class="failcomp">
    <div class="failcomp-row">
      <label>Before <input type="number" min="0" step="0.5" value=${xBefore}
        onchange=${(e) => setX(parseFloat(e.target.value))} /> s</label>
      <label>After <input type="number" min="0" step="0.5" value=${yAfter}
        onchange=${(e) => setY(parseFloat(e.target.value))} /> s</label>
      <button class="quiet-button" disabled=${running} onclick=${generate}>
        ${running ? "Generating…" : "Generate failure compilation"}</button>
    </div>
    ${running && html`<div class="meta">${job.message || "working…"}</div>`}
    ${job && job.state === "error"
      && html`<div class="danger-text">${job.message}</div>`}
    ${res && html`<div class="failcomp-result">
      <div class="meta">${res.clip_count} clips${res.finale_time
        ? ` · fastest run ${res.finale_time}` : ""}${res.skipped
        ? ` · ${res.skipped} skipped (aged out)` : ""}${res.no_finale
        ? " · no successful run in buffer" : ""}</div>
      <code class="failcomp-path">${res.path}</code>
      <button class="quiet-button" onclick=${() => reveal(res.path)}>
        Reveal in Explorer</button>
    </div>`}
  </div>`;
}
```

- [ ] **Step 4: Import and mount in both practice cards**

In `src/sm64_events/ui/components/practice.js`, add the import after line 13 (`import { StratPicker } ...`):

```javascript
import { FailureCompilation } from "./failcomp.js";
```

In `StarSection`, inside the `<details class="practice-card detail-drawer">`, after the `<div class="detail-tools">…</div>` block (after line ~464, before the `<div class="chips">`), add:

```javascript
      <${FailureCompilation}
          identity=${{ course_id: sec.course_id, star_id: sec.star_id }} />
```

In `SegmentSection`, inside its `<details class="practice-card detail-drawer">`, after its `<div class="detail-tools">…</div>` block (after line ~596, before the `<div class="chips">`), add:

```javascript
      <${FailureCompilation} identity=${{ segment_id: sec.segment_id }} />
```

- [ ] **Step 5: Run the parity tests to verify they pass**

Run: `uv run pytest tests/test_ui_section_parity.py -q`
Expected: PASS (3 tests — the new one plus the two existing).

- [ ] **Step 6: Smoke-check the JS parses (no bundler in this project)**

Run: `uv run python -c "p=open('src/sm64_events/ui/components/failcomp.js',encoding='utf-8').read(); assert 'FailureCompilation' in p and p.count('{')==p.count('}'); print('ok')"`
Expected: prints `ok` (brace balance sanity — full visual check is the human audit in Task 6).

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/ui/components/failcomp.js src/sm64_events/ui/components/practice.js tests/test_ui_section_parity.py
git commit -m "feat(ui): failure-compilation control on both practice cards (parity-enforced)"
```

---

### Task 6: Docs + full-suite gate + human audit

**Files:**
- Modify: `CLAUDE.md` (module map rows)
- Modify: `README.md` (REST surface)

- [ ] **Step 1: Update the CLAUDE.md module map**

In `CLAUDE.md`, add these rows to the module-map table (place near the replay rows):

```markdown
| Failure compilation (pure plan) | `tracking/compilation.py` — `plan_compilation`: picks non-purged failures for an entity, orders them by elapsed-into-the-run (`ended_utc-started_utc`), gates each on ring coverage of its `[end-X,end+Y]` window, appends the fastest available success (ring full-run, else a saved clip) as the finale. Pure/unit-tested; `EntityRef`/`ClipSpec`/`CompilationPlan` |
| Failure compilation (build + job) | `replay/compilation.py` — `CompilationBuilder` reuses `ClipExtractor` per window then ONE concat-filter ffmpeg pass scales+pads to a common canvas (probed from the finale clip) and re-encodes to one MP4 (single clip → copy); `CompilationService` runs it as a polled daemon-thread job (mirrors `CompareService`), output → `compilations_dir()` |
| Failure compilation REST | `server/compilation_api.py` — `POST /api/compilation` (kind-dispatched `{star}`/`{segment_id}` + `x_before`/`y_after`) → `{job_id}`; `GET /api/compilation/{job_id}` polled. Reveal reuses `/api/replay/reveal`. Wired via `create_app(..., compilation=)` in `main.py` |
| Failure compilation UI | `ui/components/failcomp.js` — shared `FailureCompilation({identity})` (X/Y inputs in localStorage, Generate → poll → summary + Reveal); mounted in BOTH practice cards' detail drawer (`practice.js`), pinned by `test_ui_section_parity.py` |
```

Also add to the `core/paths.py` row's description: `+ compilations_dir() (save_root/compilations — revealable failure-compilation output)`.

- [ ] **Step 2: Update the README REST surface**

In `README.md`, in the replay/HTTP endpoints section, add:

```markdown
- `POST /api/compilation` — start a failure compilation for a star (`{"star":{"course_id":C,"star_id":S}}`) or segment (`{"segment_id":N}`), with `x_before`/`y_after` seconds around each failure. Returns `{job_id}`.
- `GET /api/compilation/{job_id}` — poll job `{state, progress, message, result}`; `result` on done: `{path, clip_count, skipped, no_finale, finale_time}`. Output MP4 lives under the replays `compilations/` dir; open it via `POST /api/replay/reveal`.
```

- [ ] **Step 3: Run the FULL suite**

Run: `uv run pytest -q`
Expected: PASS (all existing tests + the new `test_compilation*.py`, `test_paths.py`, `test_ui_section_parity.py`).

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: failure compilation module map + REST surface"
```

- [ ] **Step 5: Human audit (live)**

Frontend + real-ffmpeg behavior can't be unit-verified. Ask the human to, against a live PJ64 session with replay footage:
1. Open a star with several deaths/resets + at least one success; open its detail drawer → **Generate failure compilation**.
2. Confirm the progress line advances ("cutting N/M"), then a path + **Reveal in Explorer** appears and opens the file selected.
3. Play the MP4: failures appear in run-progress order (earliest deaths first), audio is present, and the **fastest successful run plays last in full**.
4. Repeat on a **segment** card to confirm parity.
Report the finale time shown vs the segment/star's PB, and any A/V sync or letterboxing issues.

---

## Self-Review

**1. Spec coverage:**
- §3.1 failure moment = `ended_utc`, window `[end-X,end+Y]` → Task 2 `plan_compilation` ✓
- §3.2 elapsed ordering + tie-break → Task 2 tests ✓
- §3.3 finale = fastest success, full run, best-effort → Task 2 ✓
- §3.4 availability: failures ring-only, finale ring-or-saved → Task 2 ✓
- §3.5 reuse extractor + concat-normalize pass → Task 3 `CompilationBuilder` ✓
- §3.6 async job mirroring CompareService → Task 3 `CompilationService` ✓
- §3.7 kind-dispatched body → Task 4 router ✓
- §3.8 canvas = finale dims → Task 3 `_probe_dims(finale_clip)` ✓
- §5 module layout → Tasks 1-5 create exactly those files ✓
- §6 selection/ordering → Task 2 ✓
- §7 build (single-clip copy, runtime skip, partial-file unlink) → Task 3 tests ✓
- §8 REST + reveal reuse → Task 4 ✓
- §9 UI shared + parity → Task 5 ✓
- §10 edge cases (nothing to compile, no finale, runtime skip) → Tasks 2/3 tests ✓
- §11 all five test files → Tasks 1-5 ✓
- §12 DoD (pytest, live check, docs) → Task 6 ✓

**2. Placeholder scan:** No TBD/TODO; every code step shows complete code. ✓

**3. Type consistency:** `EntityRef`/`ClipSpec`/`CompilationPlan` fields identical across Tasks 2-4; `plan_compilation` signature matches its call in Task 3; `CompilationBuilder.build(...)` params match the Task 3 test + Task 3 service call; result dict keys (`clip_count`, `skipped`, `no_finale`, `finale_time`, `path`) match the Task 5 UI reads (`res.clip_count`/`res.skipped`/`res.no_finale`/`res.finale_time`/`res.path`). ✓

**Note on parallelism:** dependencies chain (1‖2 → 3 → 4 → 5 → 6), so this runs best sequentially via subagent-driven-development rather than parallel worktrees.
