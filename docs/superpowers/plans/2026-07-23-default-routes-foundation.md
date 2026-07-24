# Default Routes — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the tracker the engine + storage capabilities the Usamune default-route corpus needs — multi-step (waypoint) segments, active-route-gated arming, a shared category field, and a seed/reconcile with editable defaults + reset — without touching the concurrent UI redesign's files.

**Architecture:** Generalize `SegmentEngine` from a start/end pair to an ordered waypoint sequence (empty waypoints = today's behaviour byte-for-byte). Scope arming with a journaled `route_selected` event + an opt-in `in_active_route` guard read declaratively by the arm gate. Deliver defaults through a rank-standards-style seed reconcile keyed on a per-row `seed_key`, with a `seed_dirty` flag protecting user edits. All changes are backend/data + vocab; the UI consumes new capabilities as additive payload fields.

**Tech Stack:** Python 3.12 (via **uv**, never pip), FastAPI, SQLite (sync, migration-versioned), pytest. Zero-build Preact/HTM UI is out of scope for this plan.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-23-default-routes-foundation-design.md`.
- **Empty `waypoints` ⇒ existing segment behaviour is byte-for-byte unchanged.** The 10 existing defs carry no waypoints and no `in_active_route` guard; every existing `tests/test_segments.py` case must still pass untouched.
- **Edit no `ui/components/*.js` markup** (concurrent `sm64-uiux` redesign owns it). UI reaches new capability only via additive payload/vocab fields.
- Run everything with `uv run pytest -q`; it MUST pass before any merge.
- Timestamps UTC; game frames (30 fps) are the primary clock. Read-only w.r.t. emulator memory (no new addresses in this plan).
- `storage/ + stats/ + tracking/` share the `Attempt` contract — keep this whole plan on ONE branch (`feature/default-routes-foundation`, already created). Contract tasks (Wave 1–2) merge to `main` before Spec #2 / the waypoints-editor UI slice.
- Migrations are append-only and sequential; the current head is **v10**. New migrations are v11+.
- Cancel semantics (decision): an off-sequence major action on a waypoint segment is a **silent abandon** — no attempt row.
- Off-sequence "major action" set = `{star_collected, key_grabbed, level_changed (real edge)}`.

---

## Wave 1 — Contracts (serial; land on main first)

### Task 1: Schema — new columns + backfill + db plumbing

**Files:**
- Modify: `src/sm64_events/storage/db.py` (MIGRATIONS append; `segment_defs`/`insert_segment_def`/`update_segment_def`; `routes`/`insert_route`/`update_route`; new `mark_seed_dirty`/`set_seed_dirty`)
- Test: `tests/test_db.py`

**Interfaces:**
- Produces: `db.segment_defs()` rows gain keys `waypoints: list`, `category: str|None`, `seed_key: str|None`, `seed_dirty: int`. `db.routes()` rows gain `category`, `seed_key`, `seed_dirty`. `db.insert_segment_def(..., waypoints=[], category=None, seed_key=None)`; `db.insert_route(..., category=None, seed_key=None)`. `db.update_segment_def`/`db.update_route` accept `waypoints`/`category`/`seed_key`/`seed_dirty`. `db.set_seed_dirty(table: str, row_id: int, dirty: int)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_db.py — append
def test_segment_def_round_trips_waypoints_category_seed(tmp_path):
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    sid = db.insert_segment_def(
        "SL->HMC", [{"type": "level_exit", "from": 10}],
        [{"type": "level_enter", "to": 7}], [], "2026-07-23T00:00:00Z",
        waypoints=[[{"type": "level_enter", "to": 10}]],
        category="Castle Movement", seed_key="seg:sl->hmc")
    row = next(r for r in db.segment_defs() if r["id"] == sid)
    assert row["waypoints"] == [[{"type": "level_enter", "to": 10}]]
    assert row["category"] == "Castle Movement"
    assert row["seed_key"] == "seg:sl->hmc"
    assert row["seed_dirty"] == 0
    db.set_seed_dirty("segment_defs", sid, 1)
    assert next(r for r in db.segment_defs() if r["id"] == sid)["seed_dirty"] == 1


def test_route_round_trips_category_seed(tmp_path):
    from sm64_events.storage.db import Database
    db = Database(tmp_path / "t.db")
    rid = db.insert_route("16 LBLJ", [], "2026-07-23T00:00:00Z",
                          category="Main Categories", seed_key="route:16-lblj")
    row = next(r for r in db.routes() if r["id"] == rid)
    assert row["category"] == "Main Categories"
    assert row["seed_key"] == "route:16-lblj"
    assert row["seed_dirty"] == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_db.py -k "waypoints_category_seed or route_round_trips_category" -q`
Expected: FAIL (`insert_segment_def() got an unexpected keyword 'waypoints'`).

- [ ] **Step 3: Add the migration entries**

In `src/sm64_events/storage/db.py`, append to the `MIGRATIONS` list (after the v10 comparisons entry):

```python
    # v11 — sequence segments + shared category + seed provenance
    # (spec 2026-07-23-default-routes-foundation). waypoints = ordered middle
    # steps (empty = today's start/end pair). category groups routes AND
    # segments. seed_key/seed_dirty back the editable-defaults reconcile:
    # seed_key is the stable identity a bundled default is matched on;
    # seed_dirty=1 means the user edited a seeded row, so reconcile leaves it.
    """
    ALTER TABLE segment_defs ADD COLUMN waypoints TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE segment_defs ADD COLUMN category TEXT;
    ALTER TABLE segment_defs ADD COLUMN seed_key TEXT;
    ALTER TABLE segment_defs ADD COLUMN seed_dirty INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE routes ADD COLUMN category TEXT;
    ALTER TABLE routes ADD COLUMN seed_key TEXT;
    ALTER TABLE routes ADD COLUMN seed_dirty INTEGER NOT NULL DEFAULT 0;
    """,
    # v12 — adopt the 10 pre-seed segments into the reconcile by name, so a
    # newer bundled seed can refresh them (they predate seed_key). Guarded on
    # seed_key IS NULL so a re-run never clobbers a user rename.
    """
    UPDATE segment_defs SET seed_key='seg:lblj'         WHERE name='LBLJ'            AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:mips-clip'     WHERE name='MIPS Clip'       AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:lakitu-skip'   WHERE name='Lakitu Skip'     AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bits-entry'    WHERE name='BitS Entry'      AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bitdw-pipe'    WHERE name='BitDW Pipe Entry' AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bitfs-pipe'    WHERE name='BitFS Pipe Entry' AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bits-pipe'     WHERE name='BitS Pipe Entry'  AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bowser-1'      WHERE name='Bowser 1'        AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bowser-2'      WHERE name='Bowser 2'        AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bowser-3'      WHERE name='Bowser 3'        AND seed_key IS NULL;
    """,
```

- [ ] **Step 4: Extend `segment_defs()` read**

Replace the dict comprehension in `segment_defs` (currently ends at the `created_utc` key) with:

```python
        return [{"id": r["id"], "name": r["name"],
                 "enabled": bool(r["enabled"]),
                 "start_triggers": json.loads(r["start_triggers"]),
                 "end_triggers": json.loads(r["end_triggers"]),
                 "waypoints": json.loads(r["waypoints"]),
                 "guards": json.loads(r["guards"]),
                 "category": r["category"],
                 "seed_key": r["seed_key"], "seed_dirty": r["seed_dirty"],
                 "created_utc": r["created_utc"]} for r in rows]
```

- [ ] **Step 5: Extend `insert_segment_def` / `update_segment_def`**

`insert_segment_def` — add params and columns:

```python
    def insert_segment_def(self, name: str, start_triggers: list,
                           end_triggers: list, guards: list,
                           created_utc: str, enabled: bool = True,
                           waypoints: list | None = None,
                           category: str | None = None,
                           seed_key: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO segment_defs (name, enabled, start_triggers,"
                " end_triggers, waypoints, guards, category, seed_key,"
                " created_utc) VALUES (?,?,?,?,?,?,?,?,?)",
                (name, int(enabled), json.dumps(start_triggers),
                 json.dumps(end_triggers), json.dumps(waypoints or []),
                 json.dumps(guards), category, seed_key, created_utc))
            self._conn.commit()
            return cur.lastrowid
```

`update_segment_def` — extend the `cols` map:

```python
        cols = {"name": lambda v: v, "enabled": int,
                "start_triggers": json.dumps, "end_triggers": json.dumps,
                "waypoints": json.dumps, "guards": json.dumps,
                "category": lambda v: v, "seed_key": lambda v: v,
                "seed_dirty": int}
```

- [ ] **Step 6: Extend `routes()` / `insert_route` / `update_route`**

`routes()` dict gains `category`, `seed_key`, `seed_dirty`:

```python
        return [{"id": r["id"], "name": r["name"],
                 "steps": json.loads(r["steps"]),
                 "start_condition": json.loads(r["start_condition"]),
                 "category": r["category"],
                 "seed_key": r["seed_key"], "seed_dirty": r["seed_dirty"],
                 "created_utc": r["created_utc"],
                 "updated_utc": r["updated_utc"]} for r in rows]
```

`insert_route` — add params + columns:

```python
    def insert_route(self, name: str, steps: list, created_utc: str,
                     start_condition: dict | None = None,
                     category: str | None = None,
                     seed_key: str | None = None) -> int:
        sc = start_condition if start_condition is not None else {"type": "reset_game"}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO routes (name, steps, start_condition, category,"
                " seed_key, created_utc, updated_utc) VALUES (?,?,?,?,?,?,?)",
                (name, json.dumps(steps), json.dumps(sc), category, seed_key,
                 created_utc, created_utc))
            self._conn.commit()
            return cur.lastrowid
```

`update_route` — extend the `cols` map:

```python
        cols = {"name": lambda v: v, "steps": json.dumps,
                "start_condition": json.dumps, "category": lambda v: v,
                "seed_key": lambda v: v, "seed_dirty": int,
                "updated_utc": lambda v: v}
```

- [ ] **Step 7: Add `set_seed_dirty` helper**

Add near `update_route` in `db.py`:

```python
    def set_seed_dirty(self, table: str, row_id: int, dirty: int) -> None:
        """Flip the seed_dirty flag (1 = user-edited, protected from reconcile;
        0 = pristine/reset). `table` is 'segment_defs' or 'routes'."""
        if table not in ("segment_defs", "routes"):
            raise ValueError(f"bad table {table!r}")
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET seed_dirty=? WHERE id=?",
                               (dirty, row_id))
            self._conn.commit()
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_db.py -q`
Expected: PASS (new tests + all existing db tests).

- [ ] **Step 9: Commit**

```bash
git add src/sm64_events/storage/db.py tests/test_db.py
git commit -m "feat(storage): waypoints/category/seed columns + db plumbing (v11/v12)"
```

---

### Task 2: segments.py contracts — SegmentDef, MatchContext, in_active_route guard, validation, vocab

**Files:**
- Modify: `src/sm64_events/tracking/segments.py` (`SegmentDef`, `MatchContext`, `GUARDS`, `validate_definition`, `vocab`)
- Test: `tests/test_segments.py`

**Interfaces:**
- Consumes: `db.segment_defs()` rows carry `waypoints` (Task 1).
- Produces: `SegmentDef` gains `waypoints: list = []`. `MatchContext` gains `route_segments: frozenset[int] | None = None` and `target_segment: int | None = None`. `GUARDS["in_active_route"]` exists (phase `"arm"`, stub check). `validate_definition` accepts/validates `waypoints`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_segments.py — append
from sm64_events.tracking.segments import (SegmentDef, MatchContext, GUARDS,
                                           validate_definition, vocab)

def test_segmentdef_defaults_empty_waypoints():
    d = SegmentDef(id=1, name="x", enabled=True,
                   start_triggers=[{"type": "spawned", "level": 16}],
                   end_triggers=[{"type": "level_enter", "to": 6}], guards=[])
    assert d.waypoints == []

def test_matchcontext_defaults_route_fields_none():
    ctx = MatchContext(level=6, prev_level=16, num_stars=0)
    assert ctx.route_segments is None and ctx.target_segment is None

def test_in_active_route_guard_registered_and_validates():
    assert "in_active_route" in GUARDS
    validate_definition({"name": "m",
        "start_triggers": [{"type": "level_exit", "from": 10}],
        "end_triggers": [{"type": "level_enter", "to": 7}],
        "waypoints": [[{"type": "level_enter", "to": 10}]],
        "guards": [{"type": "in_active_route"}]})   # must not raise

def test_vocab_exposes_in_active_route():
    assert any(g["key"] == "in_active_route" for g in vocab()["guards"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_segments.py -k "waypoints or route_fields_none or in_active_route or vocab_exposes" -q`
Expected: FAIL (`SegmentDef.__init__() ... waypoints` / `in_active_route` not in GUARDS).

- [ ] **Step 3: Add `waypoints` to `SegmentDef`**

In the `@dataclass(frozen=True) class SegmentDef`, add after `end_triggers`:

```python
    end_triggers: list
    waypoints: list  # ordered middle steps; [] = plain start/end pair
    guards: list
```

Because `_load_segment_defs` builds `SegmentDef(**{field: row[field]})` over the dataclass fields, `waypoints` must be non-default here (Task 1 guarantees the db row supplies it). Update every in-repo `SegmentDef(...)` construction in tests/fixtures that omits `waypoints` to pass `waypoints=[]` (search `SegmentDef(` across `tests/`).

- [ ] **Step 4: Add `route_segments` + `target_segment` to `MatchContext`**

In `@dataclass(frozen=True) class MatchContext`, add after `last_star_attempted`:

```python
    # Active-route scoping (spec 2026-07-23-default-routes-foundation): the
    # journaled route_selected member set, and the standalone segment target.
    # An in_active_route-guarded def arms only if its id is in one of these.
    # None/empty = no active route.
    route_segments: frozenset | None = None
    target_segment: int | None = None
```

- [ ] **Step 5: Register the `in_active_route` guard**

Add to the `GUARDS` list (after `last_star_attempted`):

```python
    # Arm-gate scoping (spec 2026-07-23-default-routes-foundation): a stub-check
    # guard READ DECLARATIVELY by the engine's arm gate (see
    # SegmentEngine._route_allows), exactly as min_time/max_time are read
    # declaratively by projection — the standard check() never gates arming
    # (it can't see the def id). A def carrying this arms only inside the
    # active route or as the standalone segment target. Opt-in: the 10 existing
    # defs omit it and are unaffected.
    GuardType("in_active_route", "Only in the active route",
              {}, "", lambda p, ctx: True, phase="arm"),
```

- [ ] **Step 6: Validate `waypoints`**

In `validate_definition`, after the `end_triggers` loop and before the guards loop, add:

```python
    waypoints = d.get("waypoints") or []
    if not isinstance(waypoints, list):
        raise ValueError("waypoints must be a list")
    for step in waypoints:
        if not isinstance(step, list) or not step:
            raise ValueError("each waypoint must be a non-empty list of triggers")
        for clause in step:
            _check_clause(clause, TRIGGERS, "waypoints")
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run pytest tests/test_segments.py -q`
Expected: PASS (new tests + existing segment tests, after fixture `waypoints=[]` updates).

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py
git commit -m "feat(tracking): SegmentDef.waypoints + in_active_route guard + MatchContext scope fields"
```

---

## Wave 2 — Engine behaviour (serial after Wave 1)

### Task 3: waypoint sequence matcher in SegmentEngine

**Files:**
- Modify: `src/sm64_events/tracking/segments.py` (`_Arm.progress`, `SegmentEngine.feed` armed branch, helpers)
- Test: `tests/test_segments.py`

**Interfaces:**
- Consumes: `SegmentDef.waypoints` (Task 2).
- Produces: waypoint-bearing defs advance/complete/cancel per the spec; empty-waypoints defs unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_segments.py — append. Reuse this file's existing event/ctx
# fixtures; `mkctx`/`ev` below stand for whatever the file already uses to
# build a MatchContext and an EventRow — match the surrounding helpers.
def _sl_hmc_def():
    return SegmentDef(id=99, name="SL->HMC", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 10}],
        waypoints=[[{"type": "level_enter", "to": 10}],
                   [{"type": "level_exit", "from": 10}]],
        end_triggers=[{"type": "level_enter", "to": 7}], guards=[])

def test_waypoint_sequence_spans_reentry_single_success(seg_events):
    # exit SL -> enter SL -> exit SL -> enter HMC == ONE success, no other rows
    eng, feed = seg_events([_sl_hmc_def()])
    feed("level_changed", frm=10, to=10, payload={"from": 10, "to": 16})  # arm: exit SL
    closed1 = feed("level_changed", payload={"from": 16, "to": 10})       # waypoint 1: re-enter SL
    closed2 = feed("level_changed", payload={"from": 10, "to": 16})       # waypoint 2: exit SL
    closed3 = feed("level_changed", payload={"from": 16, "to": 7})        # end: enter HMC
    assert closed1 == [] and closed2 == []
    assert len(closed3) == 1 and closed3[0].outcome == "success"

def test_waypoint_cancel_on_midsequence_star_is_silent(seg_events):
    eng, feed = seg_events([_sl_hmc_def()])
    feed("level_changed", payload={"from": 16, "to": 10})  # NOT the arm; ensure armed via exit
    feed("level_changed", payload={"from": 10, "to": 16})  # arm exit SL
    feed("level_changed", payload={"from": 16, "to": 10})  # waypoint 1
    closed = feed("star_collected", payload={"course_id": 10, "star_id": 0, "num_stars": 1})
    assert closed == []                       # silent abandon: no row
    assert 99 not in eng.armed_ids()          # disarmed

def test_waypoint_cancel_on_wrong_level(seg_events):
    eng, feed = seg_events([_sl_hmc_def()])
    feed("level_changed", payload={"from": 16, "to": 10})
    feed("level_changed", payload={"from": 10, "to": 16})  # arm
    feed("level_changed", payload={"from": 16, "to": 10})  # waypoint 1
    closed = feed("level_changed", payload={"from": 10, "to": 8})  # exit to SSL, not HMC
    assert closed == [] and 99 not in eng.armed_ids()

def test_waypoint_death_still_fatal(seg_events):
    eng, feed = seg_events([_sl_hmc_def()])
    feed("level_changed", payload={"from": 16, "to": 10})
    feed("level_changed", payload={"from": 10, "to": 16})  # arm
    closed = feed("death", payload={"cause": "quicksand"})
    assert len(closed) == 1 and closed[0].outcome == "death"
```

> Note for the implementer: `seg_events` is a small helper you add at the top of the new test block — it constructs a `SegmentEngine`, threads a running `MatchContext` (tracking `level`/`prev_level` from each `level_changed` payload), and returns `(engine, feed)` where `feed(type, **kw)` builds an `EventRow`, calls `engine.feed(ev, ctx)`, and returns the closed list. Model it on the existing engine-driving helper already in `tests/test_segments.py` (search for where `SegmentEngine(` is constructed in tests and reuse that scaffolding rather than inventing a new one).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_segments.py -k waypoint -q`
Expected: FAIL (`closed3` empty / sequence not tracked — end fires on the first matching event or waypoints ignored).

- [ ] **Step 3: Add `progress` to `_Arm`**

In `@dataclass(frozen=True) class _Arm`, add after `required_area`:

```python
    required_area: int | None = None
    progress: int = 0   # index of the next waypoint to match; == len(waypoints) => awaiting end
```

- [ ] **Step 4: Add the major-action + advance helpers**

Add module-level helper near `_at_arm_position`:

```python
_MAJOR_EVENT_TYPES = ("star_collected", "key_grabbed")

def _is_major_action(ev) -> bool:
    """Off-sequence events that CANCEL a waypoint segment (spec: a task switch
    or a misroute). A minor event (area_changed, warp, spawn) stays transparent."""
    return (ev.type in _MAJOR_EVENT_TYPES
            or (ev.type == "level_changed" and _real_edge(ev)))
```

- [ ] **Step 5: Branch the armed handling on `d.waypoints`**

In `SegmentEngine.feed`, the per-def block currently reads `if arm is not None:` then the success/relocation/anchor/death chain. Wrap that chain so waypoint-bearing defs take a dedicated path and empty-waypoints defs are untouched:

```python
            if arm is not None and d.waypoints:
                closed_here = self._feed_waypoint(Attempt, d, arm, ev, ctx, notices)
                closed.extend(closed_here)
            elif arm is not None:
                # ... existing chain, VERBATIM (success / area relocation /
                # anchor echo / anchor relocation / anchor reset+re-arm /
                # death / game_reset / silent level_changed disarm) ...
```

Then add the new method on `SegmentEngine`:

```python
    def _feed_waypoint(self, Attempt, d, arm, ev, ctx, notices) -> list:
        """Ordered-sequence matcher for a waypoint-bearing def. Precedence:
        end (only when all waypoints consumed) > death/game_reset > echo
        (invisible) > real anchor (rewind+re-arm at the anchor) > next waypoint
        (advance) > major action (silent cancel) > transparent."""
        closed = []
        complete = arm.progress >= len(d.waypoints)
        if complete and self._matches(d.end_triggers, ev, ctx):
            a = self._close(Attempt, d, arm, ev, "success", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "death":
            a = self._close(Attempt, d, arm, ev, "death", ev.payload.get("cause"))
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type == "game_reset":
            a = self._close(Attempt, d, arm, ev, "hard_reset", None)
            if a:
                closed.append(a)
            self._disarm(d, ev, notices)
            return closed
        if ev.type in _ANCHOR_TYPES:
            # echo (arm-frame or event-level) is invisible; a real reset rewinds
            # the sequence and re-arms in place (retry loop). Relocation nuance
            # is a VERIFY item at the live gate; rewind-in-place is the safe
            # default and pins the retry test.
            if ev.frame == arm.start_frame or self._anchor_echo(ev):
                return closed
            self._armed[d.id] = replace(
                arm, progress=0, start_frame=ev.frame,
                started_utc=ev.wall_time_utc, jid=ev.id,
                anchor_type=ev.type, session_id=ev.session_id,
                level=ctx.level if ctx.level is not None else arm.level,
                area=ctx.area if ctx.area is not None else arm.area)
            return closed
        if not complete and self._matches(d.waypoints[arm.progress], ev, ctx):
            self._armed[d.id] = replace(arm, progress=arm.progress + 1)
            return closed
        if _is_major_action(ev):
            self._disarm(d, ev, notices)   # silent cancel, no row
            return closed
        return closed   # transparent
```

- [ ] **Step 6: Extract the echo test the branch reuses**

The event-level echo classification (`anchor_is_echo`) is computed inline in `feed`. Extract the boolean into a helper `self._anchor_echo(ev)` (move the existing expression verbatim into a method returning that bool) and call it from BOTH the original inline site and `_feed_waypoint`. This keeps one echo definition. Verify existing echo tests still pass.

- [ ] **Step 7: Suppress start-refire re-arm for armed waypoint defs**

In the arm/re-arm phase at the bottom of `feed`, a waypoint def that is already armed must NOT re-arm on a start-clause refire (the sequence owns progression via `progress`). Guard the arm block:

```python
            if starts and (not echo_invisible or relocation_arm) \
                    and not (d.waypoints and d.id in self._armed) \
                    and _route_allows(d, ctx) \
                    and all(GUARDS[g["type"]].check(g, ctx)
                            for g in d.guards
                            if GUARDS[g["type"]].phase == "arm"):
```

(`_route_allows` lands in Task 4; add it there. For Task 3 alone, omit the `_route_allows(d, ctx) and` clause and add it in Task 4.)

- [ ] **Step 8: Run tests to verify they pass**

Run: `uv run pytest tests/test_segments.py -q`
Expected: PASS (waypoint tests + every pre-existing segment test).

- [ ] **Step 9: Commit**

```bash
git add src/sm64_events/tracking/segments.py tests/test_segments.py
git commit -m "feat(tracking): waypoint sequence matcher (multi-step segments, silent off-sequence cancel)"
```

---

### Task 4: route-scoped arming — projection threading + arm gate

**Files:**
- Modify: `src/sm64_events/tracking/segments.py` (`_route_allows` + arm-gate wiring)
- Modify: `src/sm64_events/tracking/projection.py` (`_route_segments` state, `route_selected` handling, `MatchContext` build)
- Test: `tests/test_segments.py`, `tests/test_projection.py`

**Interfaces:**
- Consumes: `MatchContext.route_segments`/`target_segment` (Task 2), `in_active_route` guard (Task 2).
- Produces: a def with `in_active_route` arms only when in `ctx.route_segments` or `== ctx.target_segment`; `Projector` consumes `route_selected {route_id, segment_ids}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_segments.py — append
def _guarded_move():
    return SegmentDef(id=42, name="CCM->BitDW", enabled=True,
        start_triggers=[{"type": "level_exit", "from": 5}],
        waypoints=[], end_triggers=[{"type": "level_enter", "to": 17}],
        guards=[{"type": "in_active_route"}])

def test_guarded_def_does_not_arm_without_route(seg_events):
    eng, feed = seg_events([_guarded_move()], route_segments=None)
    feed("level_changed", payload={"from": 5, "to": 16})  # exit CCM
    assert 42 not in eng.armed_ids()

def test_guarded_def_arms_when_in_active_route(seg_events):
    eng, feed = seg_events([_guarded_move()], route_segments=frozenset({42}))
    feed("level_changed", payload={"from": 5, "to": 16})
    assert 42 in eng.armed_ids()

def test_guarded_def_arms_as_target_segment(seg_events):
    eng, feed = seg_events([_guarded_move()], route_segments=None, target_segment=42)
    feed("level_changed", payload={"from": 5, "to": 16})
    assert 42 in eng.armed_ids()

def test_unguarded_def_ignores_route_state(seg_events):
    d = replace(_guarded_move(), guards=[])
    eng, feed = seg_events([d], route_segments=None)
    feed("level_changed", payload={"from": 5, "to": 16})
    assert 42 in eng.armed_ids()
```

```python
# tests/test_projection.py — append
def test_route_selected_threads_into_matchcontext(project_events):
    # A guarded seeded segment arms only after route_selected includes it.
    ... build a Projector with the guarded def, feed a route_selected event
        with segment_ids=[42], then the arming level_changed, assert armed ...
```

> The `seg_events` helper from Task 3 gains optional `route_segments=`/`target_segment=` kwargs that it stamps onto every `MatchContext` it builds. `project_events` mirrors the existing projection-test driver.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_segments.py -k "guarded or ignores_route" tests/test_projection.py -k route_selected -q`
Expected: FAIL (guarded def arms unconditionally; `route_selected` unhandled).

- [ ] **Step 3: Add `_route_allows` and wire the arm gate**

In `segments.py`, add module-level:

```python
def _route_allows(d, ctx) -> bool:
    """in_active_route gate, read declaratively by the arm phase (the standard
    guard check can't see the def id). Unguarded defs always pass."""
    if not any(g.get("type") == "in_active_route" for g in d.guards):
        return True
    return (d.id in (ctx.route_segments or frozenset())
            or d.id == ctx.target_segment)
```

Add the `_route_allows(d, ctx) and` clause into the arm-gate condition (the Task 3 Step 7 block) so it reads `... and not (d.waypoints and d.id in self._armed) and _route_allows(d, ctx) and all(...)`.

- [ ] **Step 4: Thread route/target state in the Projector**

In `projection.py` `Projector.__init__`, add:

```python
        self._route_segments: frozenset | None = None
```

In `feed`, before the `ctx = MatchContext(...)` construction, add handling and pass the new fields:

```python
        if ev.type == "route_selected":
            ids = ev.payload.get("segment_ids") or []
            self._route_segments = frozenset(ids) if ids else None
        target_seg = self.target[1] if (self.target and self.target[0] == "segment") else None
        ctx = MatchContext(level=self._level, prev_level=prev_level,
                           num_stars=self._num_stars, area=self._area,
                           last_star_grabbed=self._last_star_grabbed,
                           last_star_attempted=self._last_star_attempted,
                           route_segments=self._route_segments,
                           target_segment=target_seg)
```

`route_selected` closes no attempt and needs no `_dispatch` branch (it falls through to the default no-op). It is not in `ANCHOR_EVENT_TYPES`/`BOUNDARY_EVENT_TYPES`, so nothing else reacts.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_segments.py tests/test_projection.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/tracking/segments.py src/sm64_events/tracking/projection.py tests/test_segments.py tests/test_projection.py
git commit -m "feat(tracking): route-scoped arming via journaled route_selected + in_active_route gate"
```

---

## Wave 3 — Surfaces (one branch; ordered — service/api/views are shared)

### Task 5: service.select_route + API endpoint + re-emit on edit

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (`select_route`; re-emit in `update_route`)
- Modify: `src/sm64_events/server/api.py` (`RouteSelectBody`, `POST /api/route/select`)
- Test: `tests/test_service.py`, `tests/test_api.py`

**Interfaces:**
- Produces: `service.select_route(route_id: int | None)` journals `route_selected {route_id, segment_ids}`; `POST /api/route/select {route_id}`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service.py — append
async def test_select_route_journals_member_ids(service_with_route):
    svc, rid, seg_ids = service_with_route   # a route whose steps reference seg_ids
    await svc.select_route(rid)
    ev = svc.db.events()[-1]
    assert ev.type == "route_selected"
    assert ev.payload["route_id"] == rid
    assert set(ev.payload["segment_ids"]) == set(seg_ids)

async def test_select_none_clears(service_with_route):
    svc, rid, _ = service_with_route
    await svc.select_route(None)
    ev = svc.db.events()[-1]
    assert ev.payload["route_id"] is None and ev.payload["segment_ids"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_service.py -k select_route -q`
Expected: FAIL (`select_route` missing).

- [ ] **Step 3: Implement `select_route`**

In `service.py` routes section, add:

```python
    def _route_member_segments(self, db, route_id: int) -> list[int]:
        route = next((r for r in db.routes() if r["id"] == route_id), None)
        if route is None:
            raise LookupError(f"route {route_id} not found")
        ids = []
        for step in route["steps"]:
            for c in step["candidates"]:
                if c.get("type") == "segment" and c["segment_id"] not in ids:
                    ids.append(c["segment_id"])
        return ids

    async def select_route(self, route_id: int | None) -> None:
        """Journal the active-route scope (spec 2026-07-23-default-routes-
        foundation). Snapshots member segment ids so replay reconstructs which
        route was active at each event WITHOUT reading the mutable routes table.
        None = clear scope (only standalone segment targets arm)."""
        db = self._require_db()
        seg_ids = self._route_member_segments(db, route_id) if route_id is not None else []
        await self.publish(Event(type="route_selected", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"route_id": route_id,
                                          "segment_ids": seg_ids}))
```

- [ ] **Step 4: Re-emit on active-route edit**

In `update_route`, after the existing `_arm_run(..., void_active=True)` block, add:

```python
        if "steps" in d and self._projector._route_segments is not None \
                and route_id in self._active_route_ids():
            await self.select_route(route_id)   # refresh member snapshot
```

Add a tiny helper `_active_route_ids` that returns the currently-selected route id set — or simpler, track the selected id on the service. To avoid reaching into `_projector` privates, add `self._active_route: int | None = None` in `__init__`, set it in `select_route`, and re-emit when `d has steps and self._active_route == route_id`. Replace the block above with:

```python
        if "steps" in d and self._active_route == route_id:
            await self.select_route(route_id)
```

- [ ] **Step 5: Add the API endpoint**

In `api.py`, add a body model near `RunStartBody`:

```python
class RouteSelectBody(BaseModel):
    route_id: int | None = None
```

And an endpoint after the routes block:

```python
    @router.post("/route/select")
    async def route_select(body: RouteSelectBody):
        try:
            await service.select_route(body.route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

- [ ] **Step 6: API test**

```python
# tests/test_api.py — append
def test_route_select_endpoint(client_with_route):
    client, rid = client_with_route
    r = client.post("/api/route/select", json={"route_id": rid})
    assert r.status_code == 200 and r.json()["ok"] is True
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_service.py tests/test_api.py -k "select_route or route_select or select_none" -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/tracking/service.py src/sm64_events/server/api.py tests/test_service.py tests/test_api.py
git commit -m "feat(tracking): select_route command + /api/route/select, re-emit on active-route edit"
```

---

### Task 6: category passthrough (routes + segments)

**Files:**
- Modify: `src/sm64_events/server/api.py` (`RouteBody`/`RoutePatch`/`SegmentBody`/`SegmentPatch` gain `category`)
- Modify: `src/sm64_events/tracking/service.py` (`create_route`/`update_route`/`create_segment`/`update_segment` pass `category`)
- Test: `tests/test_service.py`

**Interfaces:**
- Produces: creating/updating a route or segment with `category="X"` persists it (round-trips via `db.routes()`/`db.segment_defs()`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service.py — append
async def test_route_category_persists(service):
    rid = await service.create_route({"name": "r", "steps": [],
                                      "category": "Main Categories"})
    assert next(r for r in service.db.routes() if r["id"] == rid)["category"] == "Main Categories"

async def test_segment_category_persists(service):
    sid = await service.create_segment({"name": "s",
        "start_triggers": [{"type": "spawned", "level": 16}],
        "end_triggers": [{"type": "level_enter", "to": 6}],
        "category": "Tricks"})
    assert next(s for s in service.db.segment_defs() if s["id"] == sid)["category"] == "Tricks"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_service.py -k category_persists -q`
Expected: FAIL (`category` dropped).

- [ ] **Step 3: Add `category` to the API bodies**

`api.py` — add `category: str | None = None` to `RouteBody`, `RoutePatch`, `SegmentBody`, `SegmentPatch` (all already `extra="forbid"`, so the field must be declared to be accepted).

- [ ] **Step 4: Pass `category` through the service**

`create_route`: `db.insert_route(d["name"], d["steps"], _iso(_now()), start_condition=d.get("start_condition"), category=d.get("category"))`.

`update_route`: extend the passthrough set — `**{k: d[k] for k in ("name", "steps", "start_condition", "category") if k in d}`.

`create_segment`: `db.insert_segment_def(..., enabled=d.get("enabled", True), waypoints=d.get("waypoints", []), category=d.get("category"))`. (Add `waypoints` to `SegmentBody`/`SegmentPatch` here too — `list[dict] | list[list[dict]]`; declare as `list = []` to satisfy `extra="forbid"`.)

`update_segment`: extend its passthrough — `for k in ("name", "enabled", "start_triggers", "end_triggers", "waypoints", "guards", "category")`.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_service.py -k "category_persists" tests/test_segments.py tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/server/api.py src/sm64_events/tracking/service.py tests/test_service.py
git commit -m "feat(api): category + waypoints on route/segment create+update"
```

---

### Task 7: seed loader + reconcile + defaults.seed.json + main wiring

**Files:**
- Create: `src/sm64_events/tracking/defaults.py` (reconcile)
- Create: `src/sm64_events/data/defaults.seed.json` (mechanism proof: the 10 existing segments; corpus is Spec #2)
- Modify: `src/sm64_events/core/paths.py` (`bundled_defaults_seed()`)
- Modify: `src/sm64_events/main.py` (call reconcile after db open, before service)
- Modify: `src/sm64_events/storage/db.py` (`get_state`/`set_state` already exist for the version marker)
- Test: `tests/test_seed_reconcile.py` (new)

**Interfaces:**
- Consumes: Task 1 columns.
- Produces: `reconcile_defaults(db, seed: dict) -> None` — inserts missing seeded rows, refreshes `seed_dirty=0` rows, leaves `seed_dirty=1` rows and user-created rows; resolves route candidate `seed_key` → local `segment_id`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_seed_reconcile.py (new)
import json
from sm64_events.storage.db import Database
from sm64_events.tracking.defaults import reconcile_defaults

SEED_V1 = {"seed_version": 1,
    "segments": [{"seed_key": "seg:demo", "name": "Demo", "enabled": True,
        "start_triggers": [{"type": "spawned", "level": 16}],
        "end_triggers": [{"type": "level_enter", "to": 6}],
        "waypoints": [], "guards": [], "category": "Tricks"}],
    "routes": [{"seed_key": "route:demo", "name": "Demo Route",
        "category": "Main Categories", "start_condition": {"type": "reset_game"},
        "steps": [{"need": 1, "candidates": [{"type": "segment",
                                              "seed_key": "seg:demo"}]}]}]}

def test_reconcile_inserts_seed_rows(tmp_path):
    db = Database(tmp_path / "t.db")
    reconcile_defaults(db, SEED_V1)
    seg = next(s for s in db.segment_defs() if s["seed_key"] == "seg:demo")
    route = next(r for r in db.routes() if r["seed_key"] == "route:demo")
    # route candidate resolved seed_key -> the new local segment id
    assert route["steps"][0]["candidates"][0]["segment_id"] == seg["id"]

def test_reconcile_refreshes_untouched_but_not_dirty(tmp_path):
    db = Database(tmp_path / "t.db")
    reconcile_defaults(db, SEED_V1)
    seg = next(s for s in db.segment_defs() if s["seed_key"] == "seg:demo")
    seed2 = json.loads(json.dumps(SEED_V1)); seed2["seed_version"] = 2
    seed2["segments"][0]["name"] = "Demo v2"
    reconcile_defaults(db, seed2)
    assert next(s for s in db.segment_defs() if s["id"] == seg["id"])["name"] == "Demo v2"
    # now dirty it, bump again -> left alone
    db.update_segment_def(seg["id"], name="Mine"); db.set_seed_dirty("segment_defs", seg["id"], 1)
    seed3 = json.loads(json.dumps(SEED_V1)); seed3["seed_version"] = 3
    seed3["segments"][0]["name"] = "Demo v3"
    reconcile_defaults(db, seed3)
    assert next(s for s in db.segment_defs() if s["id"] == seg["id"])["name"] == "Mine"

def test_reconcile_leaves_user_rows(tmp_path):
    db = Database(tmp_path / "t.db")
    uid = db.insert_segment_def("User", [{"type": "spawned", "level": 16}],
        [{"type": "level_enter", "to": 6}], [], "2026-07-23T00:00:00Z")
    reconcile_defaults(db, SEED_V1)
    assert any(s["id"] == uid and s["seed_key"] is None for s in db.segment_defs())
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_seed_reconcile.py -q`
Expected: FAIL (`tracking.defaults` missing).

- [ ] **Step 3: Implement `reconcile_defaults`**

Create `src/sm64_events/tracking/defaults.py`:

```python
"""Editable-defaults reconcile (spec 2026-07-23-default-routes-foundation).

Mirrors ranks/standards._reconcile: a bundled seed refreshes rows the user
never touched (seed_dirty=0), leaves edited (seed_dirty=1) and user-created
(seed_key IS NULL) rows alone, and inserts anything missing. Segments come
first so route candidates can resolve seed_key -> local segment_id."""
import json
from datetime import datetime, timezone


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def reconcile_defaults(db, seed: dict) -> None:
    if not isinstance(seed, dict):
        return
    seg_by_key = {s["seed_key"]: s for s in db.segment_defs()
                  if s.get("seed_key")}
    key_to_id: dict[str, int] = {}
    for srow in seed.get("segments", []):
        key = srow["seed_key"]
        existing = seg_by_key.get(key)
        if existing is None:
            sid = db.insert_segment_def(
                srow["name"], srow["start_triggers"], srow["end_triggers"],
                srow.get("guards", []), _now_iso(),
                enabled=srow.get("enabled", True),
                waypoints=srow.get("waypoints", []),
                category=srow.get("category"), seed_key=key)
            key_to_id[key] = sid
        else:
            key_to_id[key] = existing["id"]
            if not existing["seed_dirty"]:
                db.update_segment_def(existing["id"], name=srow["name"],
                    enabled=srow.get("enabled", True),
                    start_triggers=srow["start_triggers"],
                    end_triggers=srow["end_triggers"],
                    waypoints=srow.get("waypoints", []),
                    guards=srow.get("guards", []),
                    category=srow.get("category"))
    route_by_key = {r["seed_key"]: r for r in db.routes() if r.get("seed_key")}
    for rrow in seed.get("routes", []):
        steps = _resolve_steps(rrow["steps"], key_to_id)
        key = rrow["seed_key"]
        existing = route_by_key.get(key)
        if existing is None:
            db.insert_route(rrow["name"], steps, _now_iso(),
                            start_condition=rrow.get("start_condition"),
                            category=rrow.get("category"), seed_key=key)
        elif not existing["seed_dirty"]:
            db.update_route(existing["id"], updated_utc=_now_iso(),
                            name=rrow["name"], steps=steps,
                            start_condition=rrow.get("start_condition",
                                                     {"type": "reset_game"}),
                            category=rrow.get("category"))


def _resolve_steps(steps: list, key_to_id: dict) -> list:
    """Rewrite seed route candidates ({type:segment, seed_key}) to persisted
    ({type:segment, segment_id}). An unresolved key -> segment_id -1 (renders
    as a broken step, never a crash)."""
    out = []
    for step in steps:
        cands = []
        for c in step["candidates"]:
            if c.get("type") == "segment" and "seed_key" in c:
                cands.append({"type": "segment",
                              "segment_id": key_to_id.get(c["seed_key"], -1)})
            else:
                cands.append(dict(c))
        new = {"need": step.get("need", 1), "candidates": cands}
        if step.get("label") is not None:
            new["label"] = step["label"]
        out.append(new)
    return out
```

- [ ] **Step 4: Seed file (mechanism proof) + paths + main wiring**

Create `src/sm64_events/data/defaults.seed.json` with `seed_version: 1`, a `segments` block carrying the 10 existing defs (same `seed_key`s the v12 migration backfilled — `seg:lblj`, `seg:mips-clip`, …, `seg:bowser-3`; copy each def's current `start_triggers`/`end_triggers`/`guards`/`waypoints:[]` verbatim from the v4 seed with the v5/v6 corrections folded in), and an empty `routes: []`. (Spec #2 fills the corpus.)

`core/paths.py`: add `bundled_defaults_seed()` mirroring `bundled_rank_standards()` (return the packaged `data/defaults.seed.json` path).

`main.py`: after `db` is resolved (line ~96) and before `service = TrackerService(...)`:

```python
    if db is not None:
        from sm64_events.tracking.defaults import reconcile_defaults
        from sm64_events.core.paths import bundled_defaults_seed
        try:
            seed = json.loads(bundled_defaults_seed().read_text())
            reconcile_defaults(db, seed)
        except (OSError, ValueError):
            logging.getLogger("sm64.tracker").warning("defaults seed unavailable")
```

(Add `import json` at the top of `main.py` if not present.)

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_seed_reconcile.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/tracking/defaults.py src/sm64_events/data/defaults.seed.json src/sm64_events/core/paths.py src/sm64_events/main.py tests/test_seed_reconcile.py
git commit -m "feat(tracking): editable-defaults seed reconcile (seed_key/seed_dirty) + main wiring"
```

---

### Task 8: reset-to-default (service + API)

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (`reset_route`/`reset_segment`; set `seed_dirty=1` on user edits)
- Modify: `src/sm64_events/server/api.py` (`POST /api/routes/{id}/reset`, `POST /api/segments/{id}/reset`)
- Modify: `src/sm64_events/core/paths.py` (reuse `bundled_defaults_seed`)
- Test: `tests/test_service.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: Task 7 seed + `db.set_seed_dirty`.
- Produces: `service.reset_route(id)`/`reset_segment(id)` restore the seed row by its `seed_key` and clear `seed_dirty`; `LookupError` if the row has no `seed_key`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service.py — append
async def test_reset_segment_restores_seed_and_clears_dirty(service_with_seed):
    svc = service_with_seed
    seg = next(s for s in svc.db.segment_defs() if s["seed_key"] == "seg:lblj")
    await svc.update_segment(seg["id"], {"name": "My LBLJ"})
    assert next(s for s in svc.db.segment_defs() if s["id"] == seg["id"])["seed_dirty"] == 1
    await svc.reset_segment(seg["id"])
    row = next(s for s in svc.db.segment_defs() if s["id"] == seg["id"])
    assert row["name"] == "LBLJ" and row["seed_dirty"] == 0

async def test_reset_user_created_segment_raises(service):
    sid = await service.create_segment({"name": "U",
        "start_triggers": [{"type": "spawned", "level": 16}],
        "end_triggers": [{"type": "level_enter", "to": 6}]})
    with pytest.raises(LookupError):
        await service.reset_segment(sid)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_service.py -k "reset_segment" -q`
Expected: FAIL (`reset_segment` missing).

- [ ] **Step 3: Mark seed_dirty on user edits**

In `update_segment`, after `db.update_segment_def(...)` and before `_segments_changed()`:

```python
        if current.get("seed_key"):
            db.set_seed_dirty("segment_defs", segment_id, 1)
```

In `update_route`, after `db.update_route(...)`:

```python
        if current.get("seed_key"):
            db.set_seed_dirty("routes", route_id, 1)
```

(reconcile calls `db.update_*` directly and never flips dirty — the flag is a user-edit signal only.)

- [ ] **Step 4: Implement reset**

Add a shared seed loader + reset commands in `service.py`:

```python
    def _defaults_seed(self) -> dict:
        from sm64_events.core.paths import bundled_defaults_seed
        try:
            return json.loads(bundled_defaults_seed().read_text())
        except (OSError, ValueError):
            return {"segments": [], "routes": []}

    async def reset_segment(self, segment_id: int) -> None:
        db = self._require_db()
        row = next((s for s in db.segment_defs() if s["id"] == segment_id), None)
        if row is None:
            raise LookupError(f"segment {segment_id} not found")
        if not row.get("seed_key"):
            raise LookupError(f"segment {segment_id} is not a default")
        srow = next((s for s in self._defaults_seed().get("segments", [])
                     if s["seed_key"] == row["seed_key"]), None)
        if srow is None:
            raise LookupError(f"no seed for {row['seed_key']}")
        db.update_segment_def(segment_id, name=srow["name"],
            enabled=srow.get("enabled", True),
            start_triggers=srow["start_triggers"],
            end_triggers=srow["end_triggers"],
            waypoints=srow.get("waypoints", []),
            guards=srow.get("guards", []), category=srow.get("category"))
        db.set_seed_dirty("segment_defs", segment_id, 0)
        await self._segments_changed()

    async def reset_route(self, route_id: int) -> None:
        db = self._require_db()
        row = next((r for r in db.routes() if r["id"] == route_id), None)
        if row is None:
            raise LookupError(f"route {route_id} not found")
        if not row.get("seed_key"):
            raise LookupError(f"route {route_id} is not a default")
        rrow = next((r for r in self._defaults_seed().get("routes", [])
                     if r["seed_key"] == row["seed_key"]), None)
        if rrow is None:
            raise LookupError(f"no seed for {row['seed_key']}")
        from sm64_events.tracking.defaults import _resolve_steps
        key_to_id = {s["seed_key"]: s["id"] for s in db.segment_defs() if s.get("seed_key")}
        db.update_route(route_id, updated_utc=_iso(_now()), name=rrow["name"],
                        steps=_resolve_steps(rrow["steps"], key_to_id),
                        start_condition=rrow.get("start_condition", {"type": "reset_game"}),
                        category=rrow.get("category"))
        db.set_seed_dirty("routes", route_id, 0)
        await self._routes_changed()
```

- [ ] **Step 5: API endpoints**

`api.py`, after the routes/segments blocks:

```python
    @router.post("/routes/{route_id}/reset")
    async def reset_route(route_id: int):
        try:
            await service.reset_route(route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/segments/{segment_id}/reset")
    async def reset_segment(segment_id: int):
        try:
            await service.reset_segment(segment_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

(`/segments/{id}/reset` is a distinct path segment from `/segments/vocab`; declaration order is fine because `reset` never collides with the `vocab` literal or an int id.)

- [ ] **Step 6: API test**

```python
# tests/test_api.py — append
def test_reset_endpoints(client_with_seed):
    client = client_with_seed
    seg = next(s for s in client.get("/api/segments").json() if s["seed_key"] == "seg:lblj")
    assert client.post(f"/api/segments/{seg['id']}/reset").status_code == 200
```

- [ ] **Step 7: Run tests**

Run: `uv run pytest tests/test_service.py tests/test_api.py -k "reset" -q`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/tracking/service.py src/sm64_events/server/api.py tests/test_service.py tests/test_api.py
git commit -m "feat(api): reset-to-default for seeded routes/segments; mark seed_dirty on user edits"
```

---

### Task 9: view payloads — active_route, category, seeded, route waypoints

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (`build_session_view` gains `active_route`; segment/route descriptors carry `category` + `seeded`; `build_route_view` resolves seeded flag)
- Test: `tests/test_views.py`

**Interfaces:**
- Produces: session view has `active_route: {id, name, segment_ids} | None`; segment/route descriptors expose `category` and `seeded: bool`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_views.py — append
def test_session_view_carries_active_route(db_with_selected_route, service):
    view = build_session_view(db_with_selected_route, service, clock="igt", scope="session")
    assert view["active_route"]["id"] is not None
    assert isinstance(view["active_route"]["segment_ids"], list)

def test_segment_descriptor_has_category_and_seeded(db_with_seed, service):
    view = build_session_view(db_with_seed, service, clock="igt", scope="session")
    seg = next(s for s in view["segments"] if s.get("seeded"))
    assert "category" in seg and seg["seeded"] is True
```

> `active_route` comes from the projector's `_route_segments` + the service's `_active_route` id/name. Expose a read on the service (`service.active_route()` → `{id, name, segment_ids} | None`) so the view does not reach into projector privates; build it from `self._active_route` and `db.routes()`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_views.py -k "active_route or category_and_seeded" -q`
Expected: FAIL (keys absent).

- [ ] **Step 3: Implement**

Add `service.active_route()`:

```python
    def active_route(self) -> dict | None:
        if self._active_route is None or self.db is None:
            return None
        route = next((r for r in self.db.routes() if r["id"] == self._active_route), None)
        if route is None:
            return None
        return {"id": route["id"], "name": route["name"],
                "segment_ids": self._route_member_segments(self.db, route["id"])}
```

In `build_session_view`, add `"active_route": service.active_route()` to the returned dict, and add `"category": <row category>` + `"seeded": bool(<row seed_key>)` to each segment descriptor (and route descriptor where the view emits routes). In `build_route_view`, add `"seeded": bool(route["seed_key"])` to the returned route dict.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_views.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py src/sm64_events/tracking/service.py tests/test_views.py
git commit -m "feat(views): active_route + category/seeded descriptors for the UI redesign to consume"
```

---

### Task 10: route export/import carries waypoints

**Files:**
- Modify: `src/sm64_events/tracking/routes.py` (`export_route` embed + `_segment_matches` + `resolve_import`)
- Test: `tests/test_routes.py`

**Interfaces:**
- Produces: an exported route embeds each segment's `waypoints`; `resolve_import` round-trips them and reuses an exact local match (incl. `waypoints`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_routes.py — append
def test_export_import_round_trips_waypoints():
    from sm64_events.tracking.routes import export_route, resolve_import
    defs = {7: {"name": "SL->HMC",
        "start_triggers": [{"type": "level_exit", "from": 10}],
        "end_triggers": [{"type": "level_enter", "to": 7}],
        "waypoints": [[{"type": "level_enter", "to": 10}]], "guards": []}}
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 7}]}]
    payload = export_route("R", steps, defs)
    emb = payload["steps"][0]["candidates"][0]["segment"]
    assert emb["waypoints"] == [[{"type": "level_enter", "to": 10}]]
    # exact local match (same waypoints) is reused, not recreated
    local = [{"id": 5, "name": "SL->HMC",
        "start_triggers": [{"type": "level_exit", "from": 10}],
        "end_triggers": [{"type": "level_enter", "to": 7}],
        "waypoints": [[{"type": "level_enter", "to": 10}]], "guards": []}]
    resolved = resolve_import(payload, local)
    assert resolved["steps"][0]["candidates"][0] == {"type": "segment", "segment_id": 5}
    assert resolved["to_create"] == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_routes.py -k round_trips_waypoints -q`
Expected: FAIL (`waypoints` not embedded / match ignores it).

- [ ] **Step 3: Implement**

In `export_route`, extend the embedded segment dict:

```python
                    cands.append({"type": "segment", "segment": {
                        "name": d["name"], "start_triggers": d["start_triggers"],
                        "end_triggers": d["end_triggers"],
                        "waypoints": d.get("waypoints", []),
                        "guards": d["guards"]}})
```

In `_segment_matches`, add the waypoints comparison:

```python
    return (existing["name"] == emb["name"]
            and existing["start_triggers"] == emb["start_triggers"]
            and existing["end_triggers"] == emb["end_triggers"]
            and existing.get("waypoints", []) == emb.get("waypoints", [])
            and existing.get("guards", []) == emb.get("guards", []))
```

In `resolve_import`, add `"waypoints": emb.get("waypoints", [])` to the `emb_def` dict.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/routes.py tests/test_routes.py
git commit -m "feat(tracking): route export/import carries segment waypoints"
```

---

## Final verification

- [ ] **Full suite:** `uv run pytest -q` — expected: PASS.
- [ ] **Parity guard:** `uv run pytest tests/test_ui_section_parity.py -q` — expected: PASS (this plan adds no UI markup, so parity is unchanged).
- [ ] **Docs:** update CLAUDE.md module map (segments.py waypoints + `route_selected` + `in_active_route`; `tracking/defaults.py` seed reconcile; reset endpoints), README (new endpoints + payload fields: `/api/route/select`, `/api/{routes,segments}/{id}/reset`, `active_route`/`category`/`seeded`/`waypoints`), and `docs/architecture.md` (sequence-matcher + seed-reconcile rationale). Commit.
- [ ] **Whole-branch review** via `superpowers:requesting-code-review` before merge (catches cross-cutting engine bugs the per-task reviews can't).

## Self-review notes (author)

- **Spec coverage:** §4 waypoints → Tasks 2–3; §5 route arming → Tasks 2,4,5; §6 categories → Tasks 1,6,9; §7 seed/reconcile+reset → Tasks 1,7,8; §8 data contracts → Tasks 6,9; §9 UI sequencing → honoured (no `ui/components/*.js` edits). §7's route→segment `seed_key` resolution → Task 7 `_resolve_steps`.
- **VERIFY at live gate (not code-provable):** waypoint-def behaviour for a real practice_reset mid-movement (Task 3 uses rewind-in-place; relocation nuance deferred) — mirror the existing segment engine's live-gate discipline before shipping Spec #2's movement corpus.
- **Type consistency:** `waypoints: list[list[dict]]` everywhere; `route_segments: frozenset | None`; `seed_dirty` is an int (0/1) at the db boundary, `seeded` is the bool projection of `seed_key` at the view boundary.
