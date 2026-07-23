# Attempt Time Thresholds + Last-Star Guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auto-ignore out-of-range "successes" (implicit 0.5 s min, per-star/segment overrides, optional max) by reusing the `cleared` discipline, and add two arm-time segment guards gating on the last star grabbed/attempted.

**Architecture:** One filtering site — the Projector stamps out-of-bounds successes `cleared=True, cleared_reason="auto: …"` at close time (manual clear/restore history always wins; threshold changes reproject retroactively). Segment bounds are declared as close-phase `min_time`/`max_time` guard rows (storage + builder UI only; the engine FSM is untouched); star bounds live in the `time_filters` ui_state KV. Two new arm-phase guards read last-star state the Projector tracks onto `MatchContext`.

**Tech Stack:** Python 3.12 via uv (NEVER pip), FastAPI, pytest; vendored Preact + htm UI (no build step, no JS tests).

**Spec:** `docs/superpowers/specs/2026-07-23-attempt-time-thresholds-design.md`

## Global Constraints

- Work on branch `feature/time-thresholds` off local `main` HEAD; commit per task; merge with `--no-ff` only after the full suite passes.
- Run commands from repo root. Tests: `uv run pytest tests/<file> -q` per task; FULL `uv run pytest -q` in Tasks 6, 9, and 12.
- All stored/wire times are integer FRAMES at 30 fps (domain rule 7). `DEFAULT_MIN_FRAMES = 15` (0.5 s). Seconds appear ONLY in UI inputs and reason strings.
- Reason string format is a contract: `auto: below {lo/30:.2f}s min` / `auto: above {hi/30:.2f}s max` (e.g. `auto: below 6.00s min`). The `auto: ` prefix distinguishes auto from manual reasons.
- Only successes are ever auto-flagged. Failures, AFK/no-op discards, castle discards: all unchanged.
- Never edit `core/events.py`, `core/snapshot.py`, `memory/addresses.py`, `main.py` — this feature doesn't need them.
- Multiple Claude sessions may share this checkout: before EVERY commit run `git branch --show-current` and `git diff --cached --name-only`; stage explicit paths only.
- The commit steps show multi-line `-m` messages in POSIX form — run them with the Bash tool, or in PowerShell 5.1 write the message to a temp file and use `git commit -F <file>` (multi-line `-m` breaks PS 5.1 quoting).
- The pre-existing test `test_segments.py::test_vocab_lists_triggers_guards_and_level_enum` asserts the EXACT guard key set — Tasks 1 and 2 each extend it.
- The new implicit 0.5 s floor can flag successes in PRE-EXISTING tests that used tiny frame gaps. If the full suite shows such a failure, fix the TEST by widening its frame gap / igt to a realistic value (≥ 15 frames) — do not weaken the default.

---

### Task 1: Close-phase time guards in the registry (`min_time` / `max_time` + `phase` + `time_bounds`)

**Files:**
- Modify: `src/sm64_events/tracking/segments.py` (GuardType ~line 364; GUARDS ~line 373; arm-loop ~line 734; vocab() ~line 431)
- Test: `tests/test_segments.py`

**Interfaces:**
- Produces: `GuardType.phase: str = "arm"`; `GUARDS["min_time"]`/`GUARDS["max_time"]` with `phase="close"`, param `frames` (kind `"seconds"`, stored int frames); `time_bounds(guards: list) -> tuple[int | None, int | None]`; `vocab()["guards"][i]["phase"]`.
- Consumed by: Task 4 (Projector segment bounds), Task 8 (views), Task 10 (builder input).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_segments.py`:

```python
def test_time_guards_validate_and_ship_phase_in_vocab():
    validate_definition({
        "name": "WF->Basement",
        "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "warp_entered", "level": 16}],
        "guards": [{"type": "min_time", "frames": 180},
                   {"type": "max_time", "frames": 600}]})  # no raise
    v = vocab()
    by_key = {g["key"]: g for g in v["guards"]}
    assert by_key["min_time"]["phase"] == "close"
    assert by_key["max_time"]["phase"] == "close"
    assert by_key["prev_level"]["phase"] == "arm"
    assert by_key["min_time"]["params"]["frames"]["kind"] == "seconds"


def test_min_time_requires_frames_param():
    with pytest.raises(ValueError, match="min_time"):
        validate_definition({
            "name": "x",
            "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "spawned"}],
            "guards": [{"type": "min_time"}]})


def test_close_phase_guards_do_not_gate_arming():
    eng = SegmentEngine([SegmentDef(
        id=1, name="s", enabled=True,
        start_triggers=[{"type": "spawned"}],
        end_triggers=[{"type": "warp_entered", "level": 16}],
        guards=[{"type": "min_time", "frames": 180}])])
    eng.feed(jev(1, "spawned", 1000, {"level": 16}),
             MatchContext(level=16, prev_level=None, num_stars=None))
    assert eng.armed_ids() == {1}


def test_time_bounds_reads_guard_rows():
    from sm64_events.tracking.segments import time_bounds
    assert time_bounds([]) == (None, None)
    assert time_bounds([{"type": "min_time", "frames": 180}]) == (180, None)
    assert time_bounds([{"type": "min_time", "frames": 0},
                        {"type": "max_time", "frames": 600},
                        {"type": "prev_level", "level": 16}]) == (0, 600)
```

Also UPDATE the existing exact-set assertion in `test_vocab_lists_triggers_guards_and_level_enum`:

```python
    assert {g["key"] for g in v["guards"]} == {"prev_level",
                                               "star_count_min",
                                               "star_count_max",
                                               "min_time", "max_time"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_segments.py -q`
Expected: FAIL — `KeyError`/`ValueError: unknown guard type 'min_time'`, `ImportError: cannot import name 'time_bounds'`, and the vocab set mismatch.

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/segments.py`:

(a) Add `phase` to `GuardType` (~line 364):

```python
@dataclass(frozen=True)
class GuardType:
    key: str
    label: str
    params: dict
    template: str
    check: Callable[[dict, MatchContext], bool]
    # "arm" gates arming (checked in the engine's arm phase, re-evaluated on
    # every arm/re-arm); "close" rows are DECLARATIVE result filters — never
    # checked here, read by projection's validity-bounds stamp (spec
    # 2026-07-23). Their check is a stub so a stray call can't block arming.
    phase: str = "arm"
```

(b) Append to the GUARDS list (after `star_count_max`):

```python
    # Close-phase validity bounds (spec 2026-07-23): storage + builder UI for
    # a segment's min/max completion time. `frames` is an INT of game frames
    # (30 fps); the builder edits it in seconds (ParamInput kind "seconds").
    # frames: 0 on min_time = "no minimum" (deliberately below the implicit
    # 0.5 s default — projection.DEFAULT_MIN_FRAMES applies when absent).
    GuardType("min_time", "Takes at least",
              {"frames": {"kind": "seconds", "required": True}},
              "{frames}",
              lambda p, ctx: True, phase="close"),
    GuardType("max_time", "Takes at most",
              {"frames": {"kind": "seconds", "required": True}},
              "{frames}",
              lambda p, ctx: True, phase="close"),
```

(c) Filter the arm-phase guard check (~line 734). Replace:

```python
            if starts and (not echo_invisible or relocation_arm) \
                    and all(GUARDS[g["type"]].check(g, ctx)
                            for g in d.guards):
```

with:

```python
            if starts and (not echo_invisible or relocation_arm) \
                    and all(GUARDS[g["type"]].check(g, ctx)
                            for g in d.guards
                            if GUARDS[g["type"]].phase == "arm"):
```

(d) Ship phase in `vocab()` — in the `"guards"` list comprehension add the key:

```python
        "guards": [{"key": g.key, "label": g.label, "params": g.params,
                    "template": g.template, "phase": g.phase}
                   for g in GUARDS.values()],
```

(e) Add the resolver near `validate_definition`:

```python
def time_bounds(guards: list) -> tuple[int | None, int | None]:
    """(min_frames, max_frames) declared by a def's close-phase time guards,
    None where absent. Later rows win (the chip editor writes at most one of
    each). THE reader for projection's segment validity bounds — keep the
    guard row shape knowledge here, not in projection."""
    lo = hi = None
    for g in guards or []:
        if g.get("type") == "min_time":
            lo = g["frames"]
        elif g.get("type") == "max_time":
            hi = g["frames"]
    return lo, hi
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_segments.py -q`
Expected: PASS (all, including the updated vocab set test).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py
git commit -m "feat(segments): close-phase min_time/max_time guards + GuardType.phase

Declarative validity bounds on segment defs: storage + builder vocab only.
The engine's arm loop now evaluates arm-phase guards exclusively, so the
FSM is untouched; projection reads the rows via time_bounds()."
```

---

### Task 2: Last-star arm-phase guards + MatchContext fields

**Files:**
- Modify: `src/sm64_events/tracking/segments.py` (MatchContext ~line 208; GUARDS)
- Test: `tests/test_segments.py`

**Interfaces:**
- Produces: `MatchContext.last_star_grabbed: tuple | None = None`, `MatchContext.last_star_attempted: tuple | None = None` (each `(course_id, star_id)`); `GUARDS["last_star_grabbed"]`/`GUARDS["last_star_attempted"]` (arm-phase; params `course` required kind `course`, `star` optional kind `star`).
- Consumed by: Task 5 (Projector tracks the values and passes them into MatchContext).

- [ ] **Step 1: Write the failing tests** — append to `tests/test_segments.py`:

```python
def _ctx_ls(grabbed=None, attempted=None):
    return MatchContext(level=16, prev_level=None, num_stars=None,
                        last_star_grabbed=grabbed,
                        last_star_attempted=attempted)


def test_last_star_guards_match_course_and_optional_star():
    g = GUARDS["last_star_grabbed"]
    assert g.phase == "arm"
    assert g.check({"type": "last_star_grabbed", "course": 6},
                   _ctx_ls(grabbed=(6, 0))) is True
    assert g.check({"type": "last_star_grabbed", "course": 6, "star": 3},
                   _ctx_ls(grabbed=(6, 0))) is False
    assert g.check({"type": "last_star_grabbed", "course": 6, "star": 3},
                   _ctx_ls(grabbed=(6, 3))) is True
    # unknown history conservatively FAILS (mirrors star_count_min)
    assert g.check({"type": "last_star_grabbed", "course": 6},
                   _ctx_ls()) is False


def test_last_star_attempted_reads_its_own_field():
    g = GUARDS["last_star_attempted"]
    assert g.check({"type": "last_star_attempted", "course": 6},
                   _ctx_ls(grabbed=(6, 0))) is False   # grab != attempt field
    assert g.check({"type": "last_star_attempted", "course": 6},
                   _ctx_ls(attempted=(6, 4))) is True


def test_last_star_guards_validate_and_appear_in_vocab():
    validate_definition({
        "name": "after WFRR",
        "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "spawned"}],
        "guards": [{"type": "last_star_grabbed", "course": 6, "star": 4},
                   {"type": "last_star_attempted", "course": 6}]})  # no raise
    by_key = {g["key"]: g for g in vocab()["guards"]}
    assert by_key["last_star_grabbed"]["params"]["course"]["kind"] == "course"
    assert by_key["last_star_grabbed"]["params"]["star"]["required"] is False
```

Also EXTEND the exact-set assertion in `test_vocab_lists_triggers_guards_and_level_enum` again:

```python
    assert {g["key"] for g in v["guards"]} == {"prev_level",
                                               "star_count_min",
                                               "star_count_max",
                                               "min_time", "max_time",
                                               "last_star_grabbed",
                                               "last_star_attempted"}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_segments.py -q`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'last_star_grabbed'` and missing guard keys.

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/segments.py`:

(a) Extend `MatchContext`:

```python
@dataclass(frozen=True)
class MatchContext:
    level: int | None        # tracked level AFTER this event applied
    prev_level: int | None   # tracked level BEFORE this event
    num_stars: int | None    # last star_collected payload num_stars; None = unknown
    area: int | None = None  # tracked area AFTER this event (area_changed "to");
                             # None = unknown (legacy journals without area events)
    # (course_id, star_id) of the most recent star GRAB / attributed star
    # ATTEMPT (any outcome), tracked by the Projector from closed attempts;
    # None = unknown (fresh boot, post-game_reset, legacy journals) — the
    # last_star_* guards conservatively FAIL on None (spec 2026-07-23).
    last_star_grabbed: tuple | None = None
    last_star_attempted: tuple | None = None
```

(b) Append to GUARDS (after `max_time`):

```python
    # Arm-time history gates (spec 2026-07-23): "only arm this segment when
    # the player just came from star X" — e.g. a basement segment that only
    # makes sense right after Watch for Rolling Rocks. star None = any star
    # of the course. Unknown history (None) conservatively fails.
    GuardType("last_star_grabbed", "Last star grabbed was",
              {"course": {"kind": "course", "required": True},
               "star": {"kind": "star", "required": False}},
              "{course}, star {star}",
              lambda p, ctx: ctx.last_star_grabbed is not None
              and ctx.last_star_grabbed[0] == p["course"]
              and (p.get("star") is None
                   or ctx.last_star_grabbed[1] == p["star"])),
    GuardType("last_star_attempted", "Last star attempted was",
              {"course": {"kind": "course", "required": True},
               "star": {"kind": "star", "required": False}},
              "{course}, star {star}",
              lambda p, ctx: ctx.last_star_attempted is not None
              and ctx.last_star_attempted[0] == p["course"]
              and (p.get("star") is None
                   or ctx.last_star_attempted[1] == p["star"])),
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_segments.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py
git commit -m "feat(segments): last_star_grabbed / last_star_attempted arm guards

Gate segment arming on recent star history (course required, star
optional). MatchContext carries both values; unknown fails conservatively
like star_count_min. Projector-side tracking lands separately."
```

---

### Task 3: Star-attempt validity bounds in the Projector

**Files:**
- Modify: `src/sm64_events/tracking/projection.py` (constants ~line 163; `cleared_ids` ~line 172; `Projector.__init__` ~line 186; `_build` ~line 488; `replay`/`project` ~line 526)
- Test: `tests/test_projection.py`

**Interfaces:**
- Consumes: nothing new (star path is self-contained).
- Produces: `DEFAULT_MIN_FRAMES = 15`; `touched_ids(events) -> set[int]`; `Projector(cleared=None, segments=None, time_filters=None, touched=None)`; `replay(events, segments=None, time_filters=None)`; `project(events, segments=None, time_filters=None)`; `Projector._auto_ignored(a: Attempt) -> Attempt` (also called by Task 4). `time_filters` shape: `{"<course>:<star>": {"min_frames": int, "max_frames": int | None}}`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_projection.py`:

```python
def test_success_below_default_min_is_auto_ignored():
    # igt 10 < DEFAULT_MIN_FRAMES 15: a detection artifact, auto-cleared
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=10),
    ])
    a = attempts[0]
    assert a.outcome == "success" and a.cleared is True
    assert a.cleared_reason == "auto: below 0.50s min"


def test_star_min_override_flags_what_default_allows():
    tf = {"2:2": {"min_frames": 180, "max_frames": None}}
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=150),
    ], time_filters=tf)
    assert attempts[0].cleared is True
    assert attempts[0].cleared_reason == "auto: below 6.00s min"


def test_star_max_override_flags_slow_success():
    tf = {"2:2": {"min_frames": 15, "max_frames": 300}}
    attempts = project([star(1, 900, igt=343)], time_filters=tf)
    assert attempts[0].cleared is True
    assert attempts[0].cleared_reason == "auto: above 10.00s max"


def test_min_zero_disables_the_floor():
    tf = {"2:2": {"min_frames": 0, "max_frames": None}}
    attempts = project([star(1, 900, igt=1)], time_filters=tf)
    assert attempts[0].cleared is False


def test_exactly_at_min_counts():
    tf = {"2:2": {"min_frames": 150, "max_frames": None}}
    attempts = project([star(1, 900, igt=150)], time_filters=tf)
    assert attempts[0].cleared is False


def test_failures_are_never_auto_flagged():
    # a 2-frame reset is legitimate practice behavior (fail fast)
    attempts = project([
        star(1, 900),                                     # sets target (2,2)
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(3, "practice_reset", 1002,
            {"igt_frames_before": 2, "mario_acted": True}),
    ])
    reset_row = [a for a in attempts if a.outcome == "reset"][0]
    assert reset_row.cleared is False


def test_rta_fallback_when_igt_missing():
    # star_collected without igt_frames: judge on the wall-frame delta
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "star_collected", 1005, {"course_id": 2, "star_id": 2}),
    ])
    assert attempts[0].cleared is True          # rta 5 < 15
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "star_collected", 1350, {"course_id": 2, "star_id": 2}),
    ])
    assert attempts[0].cleared is False         # rta 350


def test_manual_restore_wins_over_auto_flag():
    # journaled clear/restore history exempts the id from the auto rule
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(2, 1350, igt=10),
        jev(3, "attempt_restored", 0, {"attempt_id": 1}),
    ])
    assert attempts[0].cleared is False


def test_auto_ignored_grab_does_not_move_target():
    _, proj = replay([
        star(1, 900),                            # valid grab: target (2,2)
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(3, 1005, course=8, star_id=1, igt=5),   # bogus grab of (8,1)
    ])
    assert proj.target == ("star", 2, 2)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_projection.py -q`
Expected: FAIL — `TypeError: project() got an unexpected keyword argument 'time_filters'` and cleared=False asserts.

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/projection.py`:

(a) Constant, after `PAUSE_DISCARD_FRAMES`:

```python
# Validity floor (spec 2026-07-23): a SUCCESS faster than this is a detection
# artifact (reset-race rows, mis-triggers), auto-cleared with an "auto: "
# reason. Applies to every star/segment unless an override says otherwise
# (stars: time_filters KV, min_frames 0 disables; segments: min_time guard).
DEFAULT_MIN_FRAMES = 15  # 0.5 s x 30 fps
```

(b) After `cleared_ids`:

```python
def touched_ids(events) -> set[int]:
    """Attempt ids with ANY journaled clear/restore history — the manual
    domain. The auto validity-bounds rule never touches them (manual always
    wins), so Restore on an auto-ignored row is a per-row exemption."""
    out: set[int] = set()
    for ev in events:
        if ev.type in ("attempt_cleared", "attempt_restored"):
            out.add(int(ev.payload["attempt_id"]))
    return out
```

(c) `Projector.__init__` — extend the signature and body:

```python
    def __init__(self, cleared: dict[int, str | None] | None = None,
                 segments: list | None = None,
                 time_filters: dict | None = None,
                 touched: set[int] | None = None):
```

and add after `self._segments = SegmentEngine(segments or [])`:

```python
        # Validity bounds (spec 2026-07-23). Stars: "<course>:<star>" ->
        # {min_frames, max_frames} from the time_filters ui_state KV.
        # Segments: derived here from each def's close-phase time guards.
        self._time_filters = time_filters or {}
        self._touched = touched if touched is not None else set()
        self._seg_bounds = {d.id: time_bounds(d.guards)
                            for d in (segments or [])}
```

Extend the segments import at the top of the file:

```python
from sm64_events.tracking.segments import (
    SEGMENT_ATTEMPT_OFFSET, MatchContext, SegmentEngine, time_bounds)
```

(d) The stamp, as a Projector method (place after `_build`):

```python
    def _auto_ignored(self, a: Attempt) -> Attempt:
        """Range/validity check (spec 2026-07-23): an out-of-bounds SUCCESS
        is auto-cleared with an "auto: " reason — excluded everywhere cleared
        is (stats, rates, PBs, graphs, runs) but still visible in the hidden
        bucket. Manual clear/restore history exempts the id entirely; only
        successes are judged (a fast reset is legitimate practice). Stars are
        judged on igt (rta fallback), segments on rta; no clock -> no flag."""
        if a.outcome != "success" or a.cleared or a.id in self._touched:
            return a
        if a.segment_id is not None:
            lo, hi = self._seg_bounds.get(a.segment_id, (None, None))
            frames = a.rta_frames
        else:
            f = self._time_filters.get(f"{a.course_id}:{a.star_id}", {})
            lo, hi = f.get("min_frames"), f.get("max_frames")
            frames = a.igt_frames if a.igt_frames is not None else a.rta_frames
        if lo is None:
            lo = DEFAULT_MIN_FRAMES
        if frames is None:
            return a
        if frames < lo:
            return replace(a, cleared=True,
                           cleared_reason=f"auto: below {lo / 30:.2f}s min")
        if hi is not None and frames > hi:
            return replace(a, cleared=True,
                           cleared_reason=f"auto: above {hi / 30:.2f}s max")
        return a
```

(e) Route every star attempt through it — `_build`'s `return Attempt(...)` becomes:

```python
        return self._auto_ignored(Attempt(
            id=first.id, session_id=first.session_id,
            course_id=course_id, star_id=star_id, strat_tag=strat,
            anchor_type=first.type if is_anchored else "none",
            anchor_frame=first.frame if is_anchored else None,
            outcome=outcome,
            outcome_detail=outcome_detail,
            igt_frames=igt_frames, rta_frames=rta,
            started_utc=first.wall_time_utc, ended_utc=close.wall_time_utc,
            cleared=first.id in self._cleared,
            cleared_reason=self._cleared.get(first.id),
            rollouts_total=self._rollouts_total,
            rollouts_dustless=self._rollouts_dustless,
            jumps_total=self._jumps_total,
            jumps_dustless=self._jumps_dustless))
```

(Because `_close_by_grab` checks `if not attempt.cleared` before moving the
target, a bogus grab now also stops moving the practice target — intended.)

(f) `replay` / `project` pass-through:

```python
def replay(events, segments=None, time_filters=None) -> tuple[list[Attempt], Projector]:
    proj = Projector(cleared_ids(events), segments=segments,
                     time_filters=time_filters, touched=touched_ids(events))
```

```python
def project(events, segments=None, time_filters=None) -> list[Attempt]:
    return replay(events, segments=segments, time_filters=time_filters)[0]
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_projection.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git commit -m "feat(projection): auto-ignore out-of-range star successes

Implicit 0.5s validity floor + per-star time_filters overrides: an
out-of-bounds success is stamped cleared with an 'auto: ' reason at close
time, so every cleared-aware consumer excludes it for free. Journaled
clear/restore history always wins (touched_ids), and a bogus grab no
longer moves the practice target."
```

---

### Task 4: Segment-attempt validity bounds (def guards → Projector)

**Files:**
- Modify: `src/sm64_events/tracking/projection.py` (`feed` seg_closed loop ~line 256)
- Test: `tests/test_projection.py`

**Interfaces:**
- Consumes: Task 1 `time_bounds` (already wired into `_seg_bounds` in Task 3), Task 3 `_auto_ignored`.
- Produces: segment attempts stamped by the same rule; auto-ignored segment successes don't auto-follow the target.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_projection.py` (add the import + helper at the top of the new block):

```python
from sm64_events.tracking.segments import SegmentDef


def seg_def(**over):
    base = dict(id=1, name="S", enabled=True,
                start_triggers=[{"type": "spawned"}],
                end_triggers=[{"type": "warp_entered", "level": 16}],
                guards=[])
    base.update(over)
    return SegmentDef(**base)


def test_segment_success_below_default_min_is_auto_ignored():
    attempts = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1005, {"level": 16}),   # rta 5 < 15
    ], segments=[seg_def()])
    [a] = [a for a in attempts if a.segment_id == 1]
    assert a.outcome == "success" and a.cleared is True
    assert a.cleared_reason == "auto: below 0.50s min"


def test_segment_min_time_guard_overrides_the_default():
    d = seg_def(guards=[{"type": "min_time", "frames": 180}])
    flagged = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1150, {"level": 16}),   # rta 150 < 180
    ], segments=[d])
    [a] = [a for a in flagged if a.segment_id == 1]
    assert a.cleared is True and a.cleared_reason == "auto: below 6.00s min"
    ok = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1200, {"level": 16}),   # rta 200 >= 180
    ], segments=[d])
    [a] = [a for a in ok if a.segment_id == 1]
    assert a.cleared is False


def test_segment_max_time_guard_flags_slow_success():
    d = seg_def(guards=[{"type": "max_time", "frames": 300}])
    attempts = project([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1400, {"level": 16}),   # rta 400 > 300
    ], segments=[d])
    [a] = [a for a in attempts if a.segment_id == 1]
    assert a.cleared is True and a.cleared_reason == "auto: above 10.00s max"


def test_auto_ignored_segment_success_does_not_follow_target():
    _, proj = replay([
        jev(1, "spawned", 1000, {"level": 16}),
        jev(2, "warp_entered", 1005, {"level": 16}),   # bogus: rta 5
    ], segments=[seg_def()])
    assert proj.target is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_projection.py -q`
Expected: FAIL — segment rows come back `cleared is False`.

- [ ] **Step 3: Implement.** In `Projector.feed`, the seg_closed loop currently reads:

```python
        for a in seg_closed:
            # same first-event-id cleared keying as _build (caveat 2/11)
            a = replace(a,
                        strat_tag=self.strat_by_segment.get(a.segment_id),
                        cleared=a.id in self._cleared,
                        cleared_reason=self._cleared.get(a.id))
```

Add the stamp immediately after the `replace` (manual state first, then the auto rule — `_auto_ignored` skips rows already cleared):

```python
            a = self._auto_ignored(a)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_projection.py -q`
Expected: PASS (the target-follow test passes because the follow branch checks `not a.cleared`).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git commit -m "feat(projection): segment successes judged by their def's time guards

min_time/max_time guard rows (falling back to the implicit 0.5s floor)
stamp out-of-range segment successes cleared at close; a bogus success no
longer auto-follows the target. Same _auto_ignored path as stars."
```

---

### Task 5: Last-star tracking feeds MatchContext

**Files:**
- Modify: `src/sm64_events/tracking/projection.py` (`__init__`, `feed` ~line 246)
- Test: `tests/test_projection.py`

**Interfaces:**
- Consumes: Task 2 `MatchContext.last_star_grabbed/last_star_attempted` + guards.
- Produces: Projector-tracked history the engine's guards read; `game_reset` clears it.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_projection.py`:

```python
def test_last_star_grabbed_guard_gates_arming():
    d = seg_def(guards=[{"type": "last_star_grabbed", "course": 2}])
    # no grab yet: unknown history conservatively fails -> no arm
    _, proj = replay([jev(1, "spawned", 1000, {"level": 16})], segments=[d])
    assert proj.armed_segment_ids() == set()
    # after grabbing (2,2) the same spawn arms
    _, proj = replay([
        star(1, 900),
        jev(2, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == {1}


def test_last_star_attempted_counts_failures_grabbed_does_not():
    dg = seg_def(id=1, guards=[{"type": "last_star_grabbed", "course": 8}])
    da = seg_def(id=2, guards=[{"type": "last_star_attempted", "course": 8}])
    _, proj = replay([
        star(1, 900),                                  # grab (2,2)
        jev(2, "target_set", 950, {"course_id": 8, "star_id": 1}),
        jev(3, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(4, "practice_reset", 1400,
            {"igt_frames_before": 380, "mario_acted": True}),  # reset on (8,1)
        jev(5, "spawned", 1500, {"level": 16}),
    ], segments=[dg, da])
    # last ATTEMPT is (8,1); last GRAB is still (2,2)
    assert proj.armed_segment_ids() == {2}


def test_game_reset_clears_last_star_memory():
    d = seg_def(guards=[{"type": "last_star_grabbed", "course": 2}])
    _, proj = replay([
        star(1, 900),
        jev(2, "game_reset", 50, {}),
        jev(3, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == set()


def test_last_star_guard_star_param_narrows():
    d = seg_def(guards=[{"type": "last_star_grabbed", "course": 2, "star": 3}])
    _, proj = replay([
        star(1, 900, star_id=2),
        jev(2, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == set()
    _, proj = replay([
        star(1, 900, star_id=3),
        jev(2, "spawned", 1000, {"level": 16}),
    ], segments=[d])
    assert proj.armed_segment_ids() == {1}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_projection.py -q`
Expected: FAIL — armed sets stay empty (ctx never carries history).

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/projection.py`:

(a) `__init__`, next to `self._num_stars`:

```python
        # (course_id, star_id) of the most recent star grab / attributed star
        # attempt — feeds MatchContext for the last_star_* guards (spec
        # 2026-07-23). Updated from closures BEFORE the engine sees the same
        # event; game_reset clears both (file can change at the title screen,
        # same rationale as _num_stars). Cleared rows still update: the grab/
        # attempt happened physically, validity is a separate judgment.
        self._last_star_grabbed: tuple[int, int] | None = None
        self._last_star_attempted: tuple[int, int] | None = None
```

(b) `feed()` — after `closed = self._dispatch(ev)` and before the existing
`num_stars` block, insert:

```python
        for a in closed:
            if a.segment_id is None and a.course_id is not None:
                self._last_star_attempted = (a.course_id, a.star_id)
                if a.outcome == "success":
                    self._last_star_grabbed = (a.course_id, a.star_id)
```

and extend the existing `game_reset` branch:

```python
        elif ev.type == "game_reset":
            self._num_stars = None  # file can change at the title screen: unknown until the next grab
            self._last_star_grabbed = None
            self._last_star_attempted = None
```

(c) Extend the ctx construction:

```python
        ctx = MatchContext(level=self._level, prev_level=prev_level,
                           num_stars=self._num_stars, area=self._area,
                           last_star_grabbed=self._last_star_grabbed,
                           last_star_attempted=self._last_star_attempted)
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_projection.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git commit -m "feat(projection): track last star grabbed/attempted for arm guards

Closed star attempts update the memory before the engine sees the same
event; game_reset clears it (file may change). The last_star_* guards now
gate arming end-to-end."
```

---

### Task 6: Cleared attempts are invisible to the run engine

**Files:**
- Modify: `src/sm64_events/tracking/runs.py` (`_apply` ~line 186)
- Test: `tests/test_runs.py`

**Interfaces:**
- Consumes: `Attempt.cleared` (already stamped by Tasks 3/4 before `RunTracker.feed` receives closures).
- Produces: rule — cleared attempts (manual or auto) never advance a step, complete a run, or count attempts/fails.

- [ ] **Step 1: Write the failing test.** In `tests/test_runs.py`, first extend the `att` helper with a `cleared` kwarg:

```python
def att(outcome="success", course=None, star=None, segment_id=None,
        cleared=False):
    return Attempt(id=1, session_id=1, course_id=course, star_id=star,
                   strat_tag=None, anchor_type="none", anchor_frame=None,
                   outcome=outcome, outcome_detail=None, igt_frames=None,
                   rta_frames=None, started_utc="t", ended_utc="t",
                   cleared=cleared, cleared_reason=None, segment_id=segment_id)
```

then append:

```python
def test_cleared_attempts_are_invisible_to_runs():
    # an auto-ignored (or manually cleared) success must not complete a step,
    # and a cleared failure must not count a fail
    rt = RunTracker()
    rt.feed(started([{"need": 1, "candidates": [STAR]}]), [], CTX)
    rt.feed(Ev("game_reset", id=100), [], CTX)
    done = rt.feed(Ev("star_collected", id=101),
                   [att(course=2, star=0, cleared=True)], CTX)
    assert done == []
    v = rt.active_run_view()
    assert v["current_step"] == 0
    assert v["steps"][0]["attempts"] == 0 and v["steps"][0]["fails"] == 0
    done = rt.feed(Ev("star_collected", id=102,
                      wall="2026-06-14T00:01:00Z"),
                   [att(course=2, star=0)], CTX)
    assert done and done[0].status == "finished"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_runs.py -q`
Expected: FAIL — the cleared success finishes the run.

- [ ] **Step 3: Implement.** In `RunTracker._apply`, add at the very top:

```python
    def _apply(self, a, ev):
        if a.cleared:
            return None   # cleared attempts (manual or auto-ignored) are invisible to runs
```

- [ ] **Step 4: Run to verify pass, then the FULL suite**

Run: `uv run pytest tests/test_runs.py -q` → PASS.
Run: `uv run pytest -q` → PASS. If any pre-existing test now fails on the 0.5 s floor, widen that test's frame gaps/igt per Global Constraints.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/runs.py tests/test_runs.py
git commit -m "fix(runs): cleared attempts are invisible to the run engine

A bogus out-of-range 'success' (auto-ignored) or a manually cleared row
must not complete a run step or count attempts/fails."
```

---

### Task 7: Service plumbing + time-filter commands

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (`__init__` line ~49; `start()` replay line ~69; `_reproject` line ~570; new commands near `clear_attempt` ~line 300)
- Test: `tests/test_tracker_service.py`

**Interfaces:**
- Consumes: Task 3 `replay(..., time_filters=)` / `Projector(..., time_filters=)`.
- Produces: `TrackerService._time_filters() -> dict`; `async set_time_filter(course_id, star_id, min_frames, max_frames)` (ValueError on bad bounds); `async clear_time_filter(course_id, star_id)`. Both reproject. KV key: `time_filters`.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_tracker_service.py`:

```python
def test_set_time_filter_reflags_history_and_clear_reverts(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350, igt=150)))     # 5.00s: fine by default
    assert db.attempts()[0].cleared is False
    asyncio.run(svc.set_time_filter(2, 2, 180, None))  # min 6s
    a = db.attempts()[0]
    assert a.cleared is True and a.cleared_reason == "auto: below 6.00s min"
    asyncio.run(svc.clear_time_filter(2, 2))
    assert db.attempts()[0].cleared is False


def test_set_time_filter_validates_bounds(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.set_time_filter(2, 2, 300, 300))   # max must exceed min
    with pytest.raises(ValueError):
        asyncio.run(svc.set_time_filter(2, 2, -1, None))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tracker_service.py -q`
Expected: FAIL — `AttributeError: 'TrackerService' object has no attribute 'set_time_filter'`.

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/service.py`:

(a) Helper (place near `_load_segment_defs`):

```python
    def _time_filters(self) -> dict:
        """Star validity-bounds overrides (ui_state KV) for the projector;
        {} in degraded mode. Segment bounds ride the defs themselves."""
        return self.db.get_state("time_filters", {}) if self.db is not None else {}
```

(b) Thread it through all three construction sites:

- `__init__`: `self._projector = Projector(segments=self._segment_defs, time_filters=self._time_filters())`
- `start()` (~line 69): `attempts, self._projector = replay(events, segments=self._segment_defs, time_filters=self._time_filters())`
- `_reproject()` (~line 570): `attempts, projector = replay(db.events(), segments=self._segment_defs, time_filters=self._time_filters())`

(c) Commands (place after `restore_attempt`):

```python
    async def set_time_filter(self, course_id: int, star_id: int,
                              min_frames: int, max_frames: int | None) -> None:
        """Override one star's validity bounds (frames; min 0 = no floor,
        max None = no ceiling) and re-derive history. Mirrors the strategies
        KV RMW; the reproject applies the new bounds retroactively."""
        db = self._require_db()
        if min_frames < 0:
            raise ValueError("min_frames must be >= 0")
        if max_frames is not None and max_frames <= min_frames:
            raise ValueError("max_frames must exceed min_frames")
        filters = db.get_state("time_filters", {})
        filters[f"{course_id}:{star_id}"] = {"min_frames": min_frames,
                                             "max_frames": max_frames}
        db.set_state("time_filters", filters)
        await self._reproject()

    async def clear_time_filter(self, course_id: int, star_id: int) -> None:
        """Drop the star's override (back to the implicit defaults)."""
        db = self._require_db()
        filters = db.get_state("time_filters", {})
        if filters.pop(f"{course_id}:{star_id}", None) is not None:
            db.set_state("time_filters", filters)
        await self._reproject()
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_tracker_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_tracker_service.py
git commit -m "feat(service): per-star time-filter commands + projector plumbing

set/clear_time_filter RMW the time_filters KV and reproject, so threshold
changes reflag history retroactively; all three projector construction
sites now carry the KV."
```

---

### Task 8: Effective bounds on every section (views)

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (imports ~line 20; helper near `_markers_for` ~line 232; star sections ~line 425; segment sections ~line 460)
- Test: `tests/test_views.py`

**Interfaces:**
- Consumes: Task 1 `time_bounds`, Task 3 `DEFAULT_MIN_FRAMES`, Task 7 KV shape.
- Produces: every star AND segment section carries `"time_filter": {"min_frames": int, "max_frames": int | None, "is_default": bool}` (effective values after defaults) — the UI chip's data.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_views.py`:

```python
def test_sections_carry_time_filter_with_defaults(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["time_filter"] == {"min_frames": 15, "max_frames": None,
                                  "is_default": True}


def test_star_time_filter_override_is_reflected(tmp_path):
    db, svc = make(tmp_path)
    seed(svc)
    asyncio.run(svc.set_time_filter(2, 2, 180, 600))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["time_filter"] == {"min_frames": 180, "max_frames": 600,
                                  "is_default": False}


def test_segment_section_time_filter_reads_def_guards(tmp_path):
    db, svc = make(tmp_path)
    sid = asyncio.run(svc.create_segment({
        "name": "TF", "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "warp_entered", "level": 16}],
        "guards": [{"type": "min_time", "frames": 180},
                   {"type": "max_time", "frames": 600}]}))
    asyncio.run(svc.publish(ev("spawned", 1000, {"level": 16})))
    asyncio.run(svc.publish(ev("warp_entered", 1200, {"level": 16})))
    view = build_session_view(db, svc, clock="igt")
    sec = next(s for s in view["segments"] if s["segment_id"] == sid)
    assert sec["time_filter"] == {"min_frames": 180, "max_frames": 600,
                                  "is_default": False}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_views.py -q`
Expected: FAIL — `KeyError: 'time_filter'`.

- [ ] **Step 3: Implement.** In `src/sm64_events/tracking/views.py`:

(a) Imports:

```python
from sm64_events.tracking.projection import DEFAULT_MIN_FRAMES, journal_id
from sm64_events.tracking.segments import time_bounds
```

(b) Helper (place after `_markers_for`):

```python
def _time_filter_json(override: dict | None,
                      seg_guards: list | None = None) -> dict:
    """Effective validity bounds for one section (chip data). Stars pass the
    time_filters KV entry (None = no override); segments pass their def's
    guard rows (deleted def -> [] -> defaults). is_default drives the chip's
    dimmed state."""
    if seg_guards is not None:
        lo, hi = time_bounds(seg_guards)
    else:
        lo = (override or {}).get("min_frames")
        hi = (override or {}).get("max_frames")
    is_default = lo is None and hi is None
    return {"min_frames": DEFAULT_MIN_FRAMES if lo is None else lo,
            "max_frames": hi, "is_default": is_default}
```

(c) In `build_session_view`, next to the other `db.get_state` reads:

```python
    time_filters_state = db.get_state("time_filters", {})
```

(d) Star section dict — add alongside `"markers_by_strat"`:

```python
            "time_filter": _time_filter_json(
                time_filters_state.get(f"{course_id}:{star_id}")),
```

(e) Segment section dict — add alongside its `"markers_by_strat"` (uses the
existing `d = seg_defs.get(seg_id)`, a `SegmentDef` or None):

```python
            "time_filter": _time_filter_json(
                None, seg_guards=d.guards if d else []),
```

- [ ] **Step 4: Run to verify pass, then the FULL suite**

Run: `uv run pytest tests/test_views.py -q` → PASS.
Run: `uv run pytest -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat(views): sections carry effective time_filter bounds

Stars resolve the time_filters KV, segments their def's min/max_time
guard rows; is_default lets the chip render dimmed on the implicit 0.5s
floor."
```

---

### Task 9: REST endpoints — PUT/DELETE star time-filter

**Files:**
- Modify: `src/sm64_events/server/api.py` (models ~line 89; routes near `/segments` block ~line 180)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: Task 7 `service.set_time_filter` / `clear_time_filter`.
- Produces: `PUT /api/stars/{course_id}/{star_id}/time-filter` body `{"min_frames": int>=0, "max_frames": int>=1|null}` → `{"ok": true}`; `DELETE /api/stars/{course_id}/{star_id}/time-filter` → `{"ok": true}`. ValueError → 409 (existing `_http` taxonomy), pydantic bounds → 422.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_api.py`:

```python
def test_time_filter_put_reflags_and_delete_reverts(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)                                   # success at igt 343
        r = client.put("/api/stars/2/2/time-filter",
                       json={"min_frames": 400, "max_frames": None})
        assert r.status_code == 200
        assert db.attempts()[0].cleared is True
        assert db.attempts()[0].cleared_reason == "auto: below 13.33s min"
        assert client.delete("/api/stars/2/2/time-filter").status_code == 200
        assert db.attempts()[0].cleared is False


def test_time_filter_rejects_bad_bounds(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.put("/api/stars/2/2/time-filter",
                       json={"min_frames": 300, "max_frames": 200})
        assert r.status_code == 409                     # ValueError taxonomy
        r = client.put("/api/stars/2/2/time-filter",
                       json={"min_frames": -1, "max_frames": None})
        assert r.status_code == 422                     # pydantic ge=0
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_api.py -q`
Expected: FAIL — 404/405 on the new paths.

- [ ] **Step 3: Implement.** In `src/sm64_events/server/api.py`:

(a) Model (after `SegmentPatch`):

```python
class TimeFilterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_frames: int = Field(ge=0)                 # 0 = no floor
    max_frames: int | None = Field(default=None, ge=1)  # None = no ceiling
```

(b) Routes (place next to the other star-scoped routes, e.g. before the
`/segments` block):

```python
    @router.put("/stars/{course_id}/{star_id}/time-filter")
    async def put_time_filter(course_id: int, star_id: int,
                              body: TimeFilterBody):
        """Override one star's validity bounds (frames); history reflags
        via reproject. min 0 disables the implicit 0.5s floor."""
        try:
            await service.set_time_filter(course_id, star_id,
                                          body.min_frames, body.max_frames)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/stars/{course_id}/{star_id}/time-filter")
    async def delete_time_filter(course_id: int, star_id: int):
        """Back to the implicit defaults (0.5s min, no max)."""
        try:
            await service.clear_time_filter(course_id, star_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

- [ ] **Step 4: Run to verify pass**

Run: `uv run pytest tests/test_api.py -q`
Expected: PASS. (400/30 = 13.333… → reason reads `below 13.33s min`.)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/server/api.py tests/test_api.py
git commit -m "feat(api): PUT/DELETE /api/stars/{c}/{s}/time-filter

Star validity-bounds override endpoints riding the service commands;
same LookupError/ValueError/RuntimeError taxonomy as the rest of api.py."
```

---

### Task 10: Builder renders time guards in seconds

**Files:**
- Modify: `src/sm64_events/ui/components/segments.js` (`ParamInput`, before the number-input fallback ~line 46)

**Interfaces:**
- Consumes: Task 1 vocab (`params.frames.kind === "seconds"`).
- Produces: the min_time/max_time (and any future seconds-kind) guard rows edit as decimal seconds, storing int frames.

No JS test harness exists — verification is the Task 12 browser smoke test.

- [ ] **Step 1: Implement.** In `ParamInput`, insert before the final `return html\`<input type="number" ...\`` fallback:

```javascript
  if (schema.kind === "seconds") {
    // Stored as FRAMES (30 fps int — the project's primary clock); edited as
    // decimal seconds. "" stays null so a cleared input doesn't become 0
    // (0 is meaningful: "no minimum").
    return html`<input type="number" min="0" step="0.1" style="width:5rem"
        value=${value == null ? "" : value / 30}
        placeholder="seconds"
        onchange=${(e) => onChange(e.target.value === ""
          ? null : Math.round(Number(e.target.value) * 30))} />`;
  }
```

- [ ] **Step 2: Sanity-run the suite (server-side untouched)**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/segments.js
git commit -m "feat(ui): seconds param kind in the segment builder

min_time/max_time guards edit as decimal seconds, stored as int frames."
```

---

### Task 11: Section-header time-filter chip + auto-reason badge

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js` (new `TimeFilterChip` component near `PbTag` ~line 253; chip mounted in the `StarSection` shead ~line 338 and `SegmentSection` shead ~line 424; reason badge in `AttemptRow`'s outcome cell ~line 139)

**Interfaces:**
- Consumes: Task 8 `sec.time_filter`, Task 9 endpoints, existing `send`/`getJSON` from `../api.js` (both already imported in practice.js).
- Produces: `⏱` chip on every star/segment section; auto-ignored rows show their reason in the hidden bucket.

No JS test harness — verification is the Task 12 browser smoke test.

- [ ] **Step 1: Implement the chip.** Add after `PbTag`:

```javascript
// Validity-bounds chip (spec 2026-07-23): the section's effective min/max
// completion time — successes outside the range are auto-ignored server-side
// (auto-cleared into the hidden bucket; stats/PBs/graphs/runs skip them).
// Dimmed while on the implicit 0.5s default. Edited in SECONDS, stored as
// frames (x30). Stars persist via PUT/DELETE /api/stars/{c}/{s}/time-filter;
// segments rewrite their def's min_time/max_time guard rows through
// PUT /api/segments/{id} — both paths reproject, so history reflags
// immediately. Blank min = the 0.5s default; typed 0 = no minimum; blank
// max = no max.
function TimeFilterChip({ sec, t }) {
  const [open, setOpen] = useState(false);
  const [minS, setMinS] = useState("");
  const [maxS, setMaxS] = useState("");
  const tf = sec.time_filter;
  if (!tf) return null;
  const isSeg = sec.segment_id != null;
  const fmtS = (f) => (f % 30 === 0 ? String(f / 30) : (f / 30).toFixed(2));
  const label = tf.max_frames != null
    ? `⏱ ${fmtS(tf.min_frames)}–${fmtS(tf.max_frames)}s`
    : `⏱ ≥ ${fmtS(tf.min_frames)}s`;

  function openEditor() {
    setMinS(fmtS(tf.min_frames));
    setMaxS(tf.max_frames != null ? fmtS(tf.max_frames) : "");
    setOpen(true);
  }

  async function putSegGuards(minF, maxF) {
    // RMW the def's guard list: time rows replaced, other guards untouched
    const defs = await getJSON("/api/segments");
    const d = defs.find((x) => x.id === sec.segment_id);
    if (!d) return;
    const guards = (d.guards || []).filter(
      (g) => g.type !== "min_time" && g.type !== "max_time");
    if (minF != null) guards.push({ type: "min_time", frames: minF });
    if (maxF != null) guards.push({ type: "max_time", frames: maxF });
    await send("PUT", `/api/segments/${sec.segment_id}`, { guards });
  }

  async function save() {
    const minF = minS === "" ? null : Math.round(Number(minS) * 30);
    const maxF = maxS === "" ? null : Math.round(Number(maxS) * 30);
    if (isSeg) await putSegGuards(minF, maxF);
    // 15 mirrors projection.DEFAULT_MIN_FRAMES (blank min = keep the default)
    else await send("PUT",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`,
      { min_frames: minF == null ? 15 : minF, max_frames: maxF });
    setOpen(false);
    t.refresh();
  }

  async function reset() {
    if (isSeg) await putSegGuards(null, null);
    else await send("DELETE",
      `/api/stars/${sec.course_id}/${sec.star_id}/time-filter`);
    setOpen(false);
    t.refresh();
  }

  if (!open) return html`<button class="meta" style=${tf.is_default ? "opacity:.55" : ""}
      title="valid-time bounds — successes outside this range are ignored"
      onclick=${openEditor}>${label}</button>`;
  return html`<span class="meta">
    min <input type="number" min="0" step="0.1" style="width:4rem"
      value=${minS} oninput=${(e) => setMinS(e.target.value)} />s
    max <input type="number" min="0" step="0.1" style="width:4rem"
      value=${maxS} placeholder="∞" oninput=${(e) => setMaxS(e.target.value)} />s
    <button onclick=${save}>save</button>
    <button onclick=${reset} title="back to the 0.5s default">reset</button>
    <button onclick=${() => setOpen(false)}>cancel</button>
  </span>`;
}
```

- [ ] **Step 2: Mount it.** In `StarSection`'s `.shead`, directly after the `PbTag` line:

```javascript
      <${TimeFilterChip} sec=${sec} t=${t} />
```

In `SegmentSection`'s `.shead`, directly after its `PbTag` line (skip for broken defs):

```javascript
      ${!sec.broken && html`<${TimeFilterChip} sec=${sec} t=${t} />`}
```

- [ ] **Step 3: Reason badge.** In `AttemptRow`'s outcome `<td>` (after the dustless-jumps span), add:

```javascript
      ${a.cleared && a.cleared_reason
        ? html` <span class="meta">(${a.cleared_reason})</span>` : ""}
```

- [ ] **Step 4: Sanity-run the suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/practice.js
git commit -m "feat(ui): time-filter chip on sections + cleared-reason badge

Inline min/max editor in seconds on every star/segment section (stars ->
time-filter endpoints, segments -> guard-row RMW via PUT /api/segments);
hidden rows now show WHY they were cleared, including the auto reasons."
```

---

### Task 12: Docs, live smoke test, final verify

**Files:**
- Modify: `README.md` (API endpoint list + session payload docs)
- Modify: `CLAUDE.md` (module-map rows for `tracking/segments.py`, `tracking/projection.py`, `tracking/runs.py`)

- [ ] **Step 1: README.** In the REST endpoint documentation, add under the star-scoped endpoints:

```markdown
- `PUT /api/stars/{course_id}/{star_id}/time-filter` — body
  `{"min_frames": int, "max_frames": int|null}` (frames, 30 fps; min 0 = no
  floor). Successes outside a star/segment's validity bounds are
  **auto-ignored**: recorded but flagged cleared with an `auto: …` reason,
  excluded from stats/PBs/graphs/runs, visible in the hidden bucket.
  Default for every star and segment: min 0.5 s, no max. Segments declare
  overrides as `min_time`/`max_time` guard rows on their definition.
- `DELETE /api/stars/{course_id}/{star_id}/time-filter` — revert to defaults.
```

and in the session-view payload description note that every star/segment
section carries `time_filter: {min_frames, max_frames, is_default}`.

- [ ] **Step 2: CLAUDE.md module map** — extend three existing rows (append to the cell text, don't restructure):
  - `tracking/segments.py` row: append “; GuardType.phase — close-phase `min_time`/`max_time` rows are declarative validity bounds read by projection (`time_bounds`), arm-phase `last_star_grabbed`/`last_star_attempted` gate on MatchContext's last-star memory”.
  - `tracking/projection.py` row: append “; auto-ignores out-of-range successes (DEFAULT_MIN_FRAMES 0.5s + star `time_filters` KV / segment time guards → cleared with `auto:` reason; journaled clear/restore wins via touched_ids); tracks last star grabbed/attempted for the last_star_* guards (game_reset clears)”.
  - `tracking/runs.py` row: append “; cleared attempts (manual or auto-ignored) are invisible to runs”.

- [ ] **Step 3: Full verify**

Run: `uv run pytest -q`
Expected: PASS, zero failures.

- [ ] **Step 4: Browser smoke test** (frontend-smoke-test skill; server: `uv run python -m sm64_events.main`, then http://localhost:8065):
  - a star section shows the dimmed `⏱ ≥ 0.5s` chip; editing min to 6 s reflags/unflags rows and the reason badge reads `auto: below 6.00s min`; reset reverts.
  - a segment section chip writes guard rows (confirm in the Segments tab builder: “Takes at least 6” appears, editable in seconds).
  - the builder offers all four new guards; console clean.

- [ ] **Step 5: Commit**

```bash
git add README.md CLAUDE.md
git commit -m "docs: time thresholds + last-star guards (README API, module map)"
```

Then hand off to superpowers:finishing-a-development-branch (merge `feature/time-thresholds` into main with `--no-ff`, full suite on the merged result, delete the branch, then the create-artifacts skill).
