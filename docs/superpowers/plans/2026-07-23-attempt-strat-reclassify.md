# Per-Attempt Strategy Reclassification Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the user change any recorded attempt's strategy after the fact, from a dropdown in the attempt list, with unlabeled attempts explicitly reading `— no strategy —`.

**Architecture:** A new journaled event `attempt_strat_set` is folded into the attempt projection by a `strat_overrides()` pre-pass — the same compensating-event pattern `attempt_cleared`/`attempt_restored` already use. Nothing writes the derived `attempts` row directly. The UI reuses the existing `StratPicker` component with an injected `submit` callback, so the star card, the segment card, and every attempt row share one dropdown.

**Tech Stack:** Python 3.12 (uv, pytest, FastAPI, SQLite), Preact + htm (no build step, vendored).

**Spec:** `docs/superpowers/specs/2026-07-23-attempt-strat-reclassify-design.md`

## Global Constraints

- Run tests with `uv run pytest -q` from the repo root. **Never** use `pip`.
- Attempts are a *derived* cache. Any correction to a recorded attempt is journaled and folded in on replay — never an `UPDATE` of the `attempts` table.
- Attempt ids are the journal id of the attempt's **first** event (for an anchored success that is the anchor's id, not the grab's). Segment attempt ids carry `segments.SEGMENT_ATTEMPT_OFFSET`.
- Star ↔ segment parity (CLAUDE.md domain rule 11): a feature ships for both kinds in the same change. `tests/test_ui_section_parity.py` enforces the UI half.
- Every `/api` route must be documented in `README.md` or `docs/api.md` — `tests/test_docs_cover_api.py` fails otherwise.
- Commit messages explain WHY, imperative mood, ending with:
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
- This checkout is shared with other Claude sessions. Before **every** commit run `git diff --cached --name-only` and confirm only your task's files are staged (`git reset HEAD -- <foreign>` if not). Stage explicit paths; `git add -A` is hook-blocked.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/sm64_events/tracking/projection.py` | `strat_overrides()` pre-pass + applying it at the two `strat_tag` stamping sites | 1 |
| `src/sm64_events/storage/db.py` | `retag_pbs_for_attempt()` — follow a reclassification into saved PB rows | 2 |
| `src/sm64_events/tracking/service.py` | `set_attempt_strat()` command (journal → register → retag PB → reproject) | 3 |
| `src/sm64_events/server/api.py` + `docs/api.md` | `POST /api/attempts/{id}/strat` + its doc row | 4 |
| `src/sm64_events/ui/components/stratpicker.js` | optional `submit` / `blankLabel` / `highlightUnset` props | 5 |
| `src/sm64_events/ui/components/practice.js` + `CLAUDE.md` | attempt-row dropdown + module-map update | 6 |

## Waves (parallel fan-out)

- **Wave 1 (parallel):** Task 1, Task 2 — disjoint files, no shared contract.
- **Wave 2:** Task 3 — consumes both.
- **Wave 3 (parallel):** Task 4, Task 5 — disjoint files.
- **Wave 4:** Task 6 — consumes 4 and 5.

---

### Task 1: Projection folds in the strat override

**Files:**
- Modify: `src/sm64_events/tracking/projection.py` (docstring caveats; after `touched_ids` ~line 227; `Projector.__init__` ~line 233; `feed()` segment stamp ~line 335; `_build()` ~line 586; `replay()` ~line 648)
- Test: `tests/test_projection.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `strat_overrides(events) -> dict[int, str | None]` — module-level pure function.
  - `Projector.__init__(..., strat_overrides: dict[int, str | None] | None = None)` — new keyword-only-in-practice arg appended after `touched`.
  - Journal event type `attempt_strat_set` with payload `{"attempt_id": int, "strat_tag": str | None}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_projection.py`:

```python
# -- attempt strat reclassification (spec 2026-07-23) --------------------------

def test_attempt_strat_set_reclassifies_a_star_attempt():
    events = [
        jev(1, "target_set", 0, {"course_id": 2, "star_id": 2,
                                 "strat_tag": "Cannonless"}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(3, 1350),
    ]
    [before] = project(events)
    # the attempt is keyed by its FIRST event — the anchor, not the grab
    assert before.id == 2 and before.strat_tag == "Cannonless"
    [after] = project(events + [
        jev(4, "attempt_strat_set", 0, {"attempt_id": 2,
                                        "strat_tag": "Slide Kick"})])
    assert after.strat_tag == "Slide Kick"
    assert after.outcome == "success"      # nothing else moved


def test_attempt_strat_set_null_unlabels_an_attempt():
    [a] = project([
        jev(1, "target_set", 0, {"course_id": 2, "star_id": 2,
                                 "strat_tag": "Cannonless"}),
        jev(2, "practice_reset", 1000, {"igt_frames_before": 0}),
        star(3, 1350),
        jev(4, "attempt_strat_set", 0, {"attempt_id": 2, "strat_tag": None}),
    ])
    assert a.strat_tag is None


def test_strat_overrides_last_write_wins():
    assert strat_overrides([
        jev(1, "attempt_strat_set", 0, {"attempt_id": 7, "strat_tag": "A"}),
        jev(2, "attempt_strat_set", 0, {"attempt_id": 7, "strat_tag": "B"}),
        jev(3, "attempt_strat_set", 0, {"attempt_id": 9, "strat_tag": None}),
    ]) == {7: "B", 9: None}


def test_attempt_strat_set_reclassifies_a_segment_attempt():
    events = [
        jev(1, "strat_set", 0, {"kind": "segment", "segment_id": 1,
                                "strat_tag": "old route"}),
        jev(2, "level_changed", 900, {"from": 16, "to": 16}),
        jev(3, "level_changed", 1000, {"from": 16, "to": 6}),    # arms LBLJ
        jev(4, "level_changed", 1085, {"from": 6, "to": 17}),    # ends it
    ]
    [before] = project(events, segments=seg_defs())
    assert before.segment_id == 1 and before.strat_tag == "old route"
    [after] = project(events + [
        jev(5, "attempt_strat_set", 0, {"attempt_id": before.id,
                                        "strat_tag": "new route"})],
        segments=seg_defs())
    assert after.strat_tag == "new route"


def test_attempt_strat_set_is_not_an_attempt_boundary():
    """It must not open, close, or discard anything — it is a pure
    annotation folded in by the pre-pass."""
    attempts = project([
        jev(1, "practice_reset", 1000, {"igt_frames_before": 0}),
        jev(2, "attempt_strat_set", 0, {"attempt_id": 1, "strat_tag": "X"}),
        star(3, 1350),
    ])
    assert len(attempts) == 1
    assert attempts[0].id == 1 and attempts[0].rta_frames == 350
    assert attempts[0].strat_tag == "X"
```

Update the import at the top of `tests/test_projection.py` (line 2):

```python
from sm64_events.tracking.projection import (
    Projector, cleared_ids, project, replay, strat_overrides)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_projection.py -k strat_set -q`
Expected: FAIL — `ImportError: cannot import name 'strat_overrides'`.

- [ ] **Step 3: Add the pre-pass function**

In `src/sm64_events/tracking/projection.py`, immediately after `touched_ids()` (~line 227):

```python
def strat_overrides(events) -> dict[int, str | None]:
    """attempt_id -> reclassified strat_tag (None = deliberately unlabeled).

    The compensating-event sibling of cleared_ids(). A strategy is declared
    BEFORE a run and is therefore often wrong after it ("I said Cannonless,
    then did something else"); the journal is append-only, so the correction
    is appended and folded in here rather than written into the derived
    attempts row. Last write wins, which makes re-picking the previous
    strategy the undo — no restore event needed."""
    out: dict[int, str | None] = {}
    for ev in events:
        if ev.type == "attempt_strat_set":
            out[int(ev.payload["attempt_id"])] = ev.payload["strat_tag"]
    return out
```

- [ ] **Step 4: Store it on the Projector**

In `Projector.__init__` (~line 233), extend the signature and store the dict:

```python
    def __init__(self, cleared: dict[int, str | None] | None = None,
                 segments: list | None = None,
                 time_filters: dict | None = None,
                 touched: set[int] | None = None,
                 strat_overrides: dict[int, str | None] | None = None):
        self._cleared = cleared if cleared is not None else {}
        # attempt_id -> reclassified strat (caveat 16); shadows the strat
        # remembered at close time.
        self._strat_overrides = (strat_overrides
                                 if strat_overrides is not None else {})
```

(Leave the rest of `__init__` untouched — `self._cleared` is the existing first line; the two new lines go directly beneath it.)

- [ ] **Step 5: Apply it at the two stamping sites**

In `_build()` (~line 586), replace `strat_tag=strat,` with:

```python
            strat_tag=self._strat_overrides.get(first.id, strat),
```

In `feed()`'s segment stamp (~line 335), replace the `strat_tag=` line:

```python
            a = replace(a,
                        strat_tag=self._strat_overrides.get(
                            a.id, self.strat_by_segment.get(a.segment_id)),
                        cleared=a.id in self._cleared,
                        cleared_reason=self._cleared.get(a.id))
```

In `replay()` (~line 648), pass the pre-pass result:

```python
    proj = Projector(cleared_ids(events), segments=segments,
                     time_filters=time_filters, touched=touched_ids(events),
                     strat_overrides=strat_overrides(events))
```

- [ ] **Step 6: Record the caveat**

In the module docstring, after caveat 15, add:

```
16. Strat reclassification: an attempt's strat_tag is the strategy remembered
    at CLOSE time (caveat 6) unless a journaled attempt_strat_set overrides
    it — strat_overrides() is the pre-pass (sibling of cleared_ids), keyed by
    the attempt's first-event id like clearing is (caveat 2), and applied at
    both stamping sites (_build for stars, the segment stamp in feed()).
    Last write wins, so re-picking the previous strategy is the undo. The
    event itself is inert in _dispatch: it opens and closes nothing.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `uv run pytest tests/test_projection.py -q`
Expected: PASS — all tests in the file, including the five new ones.

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/tracking/projection.py tests/test_projection.py
git diff --cached --name-only     # confirm ONLY those two paths
git commit -F- <<'EOF'
feat(projection): fold a journaled strat override into attempt replay

A strategy is declared before a run, so it is often wrong after it. The
journal is append-only and attempts are a derived cache, so the correction
has to be a compensating event folded in on replay — an UPDATE of the
attempts row would silently revert on the next reproject.

strat_overrides() is the exact sibling of cleared_ids(), keyed by the same
first-event id, applied at both places that stamp strat_tag so stars and
segments correct identically. Last write wins: re-picking the previous
strategy IS the undo, so no restore event is needed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 2: PB rows follow a reclassification

**Files:**
- Modify: `src/sm64_events/storage/db.py` (beside `delete_pbs_for_attempts`, ~line 620)
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Database.retag_pbs_for_attempt(attempt_id: int, strat_tag: str | None) -> None`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_storage.py`:

```python
def test_retag_pbs_for_attempt_moves_the_row_to_the_new_strategy(tmp_path):
    """A pbs row snapshots strat_tag at save time and is NOT rebuilt from the
    journal, so reclassifying an attempt must carry its PB across — otherwise
    the old strategy keeps a PB that was never achieved with it."""
    db = make_db(tmp_path)
    db.insert_pb(course_id=2, star_id=2, strat_tag="Cannonless",
                 timer_mode="igt", frames=343, attempt_id=10,
                 saved_utc="2026-06-10T12:01:00Z")
    db.insert_pb(course_id=2, star_id=2, strat_tag="Cannonless",
                 timer_mode="igt", frames=350, attempt_id=11,
                 saved_utc="2026-06-10T12:02:00Z")
    db.retag_pbs_for_attempt(10, "Slide Kick")
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick")["frames"] == 343
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless")["frames"] == 350
    # unlabeling is expressible too, and an attempt with no pb row is a no-op
    db.retag_pbs_for_attempt(10, None)
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick") is None
    db.retag_pbs_for_attempt(999, "Whatever")
    assert len(db.pbs()) == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_storage.py -k retag -q`
Expected: FAIL — `AttributeError: 'Database' object has no attribute 'retag_pbs_for_attempt'`.

- [ ] **Step 3: Write the implementation**

In `src/sm64_events/storage/db.py`, directly after `delete_pbs_for_attempts` (~line 626):

```python
    def retag_pbs_for_attempt(self, attempt_id: int,
                              strat_tag: str | None) -> None:
        """Follow an attempt's reclassification into the PBs it saved.

        A pbs row snapshots strat_tag at save time and is not derived from
        the journal, so it cannot self-heal on reproject the way the attempt
        does — without this the star's PB for the OLD strategy stays a time
        that was not achieved with it. Keyed on attempt_id, so re-picking the
        original strategy retags the row back."""
        with self._lock:
            self._conn.execute("UPDATE pbs SET strat_tag=? WHERE attempt_id=?",
                               (strat_tag, attempt_id))
            self._conn.commit()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_storage.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/storage/db.py tests/test_storage.py
git diff --cached --name-only     # confirm ONLY those two paths
git commit -F- <<'EOF'
feat(storage): retag an attempt's saved PBs when it is reclassified

Unlike the attempt itself, a pbs row is persisted rather than derived — it
snapshots strat_tag at save time and never rebuilds from the journal. Left
alone, reclassifying an attempt would leave its PB filed under a strategy
the run was not done with, which is the reported bug one layer down.

Keyed on attempt_id so the write always mirrors the attempt's current tag:
re-picking the original strategy retags the row back.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 3: The `set_attempt_strat` command

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (after `restore_attempt`, ~line 393)
- Test: `tests/test_tracker_service.py`, `tests/test_views.py`

**Interfaces:**
- Consumes: `projection.strat_overrides` (Task 1, via `replay()` inside `_reproject`); `Database.retag_pbs_for_attempt` (Task 2).
- Produces: `TrackerService.set_attempt_strat(attempt_id: int, strat_tag: str | None) -> None` (async). Raises `LookupError` for an unknown attempt id.

- [ ] **Step 1: Write the failing service tests**

Append to `tests/test_tracker_service.py`:

```python
def test_set_attempt_strat_reclassifies_and_registers(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    assert db.attempts()[0].strat_tag == "Cannonless"
    asyncio.run(svc.set_attempt_strat(aid, "Slide Kick"))
    assert db.attempts()[0].strat_tag == "Slide Kick"
    assert "attempt_strat_set" in [e.type for e in db.events()]
    # the name is registered, so it survives in the section dropdown
    assert "Slide Kick" in db.get_state("strategies", {})["2:2"]
    # the live target's strategy is untouched — this edits history only
    assert svc.strat_by_star[(2, 2)] == "Cannonless"


def test_set_attempt_strat_null_unlabels_and_is_reversible(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    asyncio.run(svc.set_attempt_strat(aid, None))
    assert db.attempts()[0].strat_tag is None
    asyncio.run(svc.set_attempt_strat(aid, "Cannonless"))
    assert db.attempts()[0].strat_tag == "Cannonless"


def test_set_attempt_strat_moves_the_saved_pb(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))
    aid = db.attempts()[0].id
    asyncio.run(svc.save_pb(aid, "igt"))
    asyncio.run(svc.set_attempt_strat(aid, "Slide Kick"))
    assert db.current_pb(2, 2, "igt", strat_tag="Slide Kick")["frames"] == 343
    assert db.current_pb(2, 2, "igt", strat_tag="Cannonless") is None


def test_set_attempt_strat_unknown_attempt_raises_lookup_error(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.set_attempt_strat(999, "Slide Kick"))
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_tracker_service.py -k set_attempt_strat -q`
Expected: FAIL — `AttributeError: 'TrackerService' object has no attribute 'set_attempt_strat'`.

- [ ] **Step 3: Write the implementation**

In `src/sm64_events/tracking/service.py`, directly after `restore_attempt` (~line 393):

```python
    async def set_attempt_strat(self, attempt_id: int,
                                strat_tag: str | None) -> None:
        """Reclassify ONE recorded attempt's strategy (None = unlabeled).

        A strategy is declared before a run, so it is routinely wrong after
        it. Journal-first like clear/restore: the correction is appended and
        folded in by projection.strat_overrides, never written into the
        derived attempts row. Editing history does NOT touch the live
        per-target strategy memory — the two are deliberately independent.
        Re-picking the previous strategy is the undo."""
        db = self._require_db()
        attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
        if attempt is None:
            raise LookupError(f"no attempt {attempt_id}")
        await self.publish(Event(type="attempt_strat_set", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"attempt_id": attempt_id,
                                          "strat_tag": strat_tag}))
        has_entity = (attempt.segment_id is not None
                      or attempt.course_id is not None)
        if strat_tag and has_entity:
            # The name would already resurface via the observed-strats union
            # once the reprojected attempt carries it; registering matters
            # because it ALSO clears the strategy's tombstone — assigning a
            # purged name to a run puts it back in use (the same un-delete
            # rule as re-creating it).
            self._register_strategy(
                db, entity_key(attempt.course_id, attempt.star_id,
                               attempt.segment_id), strat_tag)
        # A pbs row snapshots strat_tag at save time and is not derived, so
        # it cannot follow the reprojection on its own.
        db.retag_pbs_for_attempt(attempt_id, strat_tag)
        await self._reproject()
```

- [ ] **Step 4: Run the service tests to verify they pass**

Run: `uv run pytest tests/test_tracker_service.py -q`
Expected: PASS.

- [ ] **Step 5: Write the failing view test**

The point of reclassifying is that the *derived* surfaces follow. Append to `tests/test_views.py`:

```python
def test_reclassified_attempt_regrades_its_medal(tmp_path):
    """End-to-end payoff: an attempt reclassified onto a different strategy
    is graded against THAT strategy's ladder, not the one it was recorded
    under — a mislabeled run otherwise reports a rank it never earned.

    Ladder times are SECONDS in the standards file (see _ranks above); the
    seeded grab is 343 frames = 11.43 s displayed, so it is slower than the
    Cannonless Mario cutoff (5 s → Iron) and faster than the Slide Kick one
    (20 s → Mario)."""
    import json
    from sm64_events.ranks.standards import RankStandards
    db, svc = make(tmp_path)
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:2:2": {"clock": "igt", "strategies": {
            "Cannonless": {"Mario": 5.0},
            "Slide Kick": {"Mario": 20.0}}}}}))
    svc.ranks = RankStandards(p); svc.ranks.load()
    asyncio.run(svc.set_target(2, 2, strat_tag="Cannonless"))
    asyncio.run(svc.publish(ev("practice_reset", 1000,
                               {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(1350)))          # igt 343 frames
    aid = db.attempts()[0].id
    before = build_session_view(db, svc, clock="igt")["stars"][0]["attempts"][0]
    assert before["strat_tag"] == "Cannonless" and before["rank"] == "Iron"
    asyncio.run(svc.set_attempt_strat(aid, "Slide Kick"))
    after = build_session_view(db, svc, clock="igt")["stars"][0]["attempts"][0]
    assert after["strat_tag"] == "Slide Kick" and after["rank"] == "Mario"
```

This reuses `tests/test_views.py`'s existing module-level `make()`, `ev()` and
`star()` helpers and mirrors the standards-file shape of its `_ranks()` helper
(~line 996). `rank_for` returns `"Iron"` — not `None` — for a time slower than
every defined tier, which is why the "before" assertion reads `"Iron"`.

- [ ] **Step 6: Run the view test**

Run: `uv run pytest tests/test_views.py -k regrades -q`
Expected: PASS (no production change needed — `_attempt_rank` already keys off `a.strat_tag`). If it fails on the *rank values*, fix the test's threshold numbers, not the production code. If it fails because the tag did not change, the bug is real — stop and diagnose.

- [ ] **Step 7: Run the whole suite**

Run: `uv run pytest -q`
Expected: PASS, no regressions.

- [ ] **Step 8: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_tracker_service.py tests/test_views.py
git diff --cached --name-only     # confirm ONLY those three paths
git commit -F- <<'EOF'
feat(tracking): command to reclassify a recorded attempt's strategy

Journal-first like clear/restore, so the correction survives every
reproject. Two deliberate details: the live per-target strategy memory is
NOT touched (editing history and picking what to practice next are separate
intents), and registration runs for its tombstone-clearing side effect —
assigning a purged name to a run puts it back in use, the same un-delete
rule as re-creating it.

The view test pins the payoff: a reclassified attempt is graded against the
new strategy's ladder, so a mislabeled run stops reporting a rank it never
earned.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 4: REST endpoint + API docs

**Files:**
- Modify: `src/sm64_events/server/api.py` (body models ~line 51; router beside `/attempts/{attempt_id}/restore` ~line 438)
- Modify: `docs/api.md` — route table (~line 120, beside the clear/restore rows), event-payload table (~line 81), and a stale note on the markers row (~line 126)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `TrackerService.set_attempt_strat(attempt_id, strat_tag)` (Task 3).
- Produces: `POST /api/attempts/{attempt_id}/strat` with body `{"strat_tag": str | null}` → `{"ok": true}`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_api.py`:

```python
def test_attempt_strat_endpoint_reclassifies_and_404s_on_unknown(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        seed(service)
        aid = db.attempts()[0].id
        r = client.post(f"/api/attempts/{aid}/strat",
                        json={"strat_tag": "Slide Kick"})
        assert r.status_code == 200
        assert db.attempts()[0].strat_tag == "Slide Kick"
        # null is a first-class value: it unlabels the attempt
        assert client.post(f"/api/attempts/{aid}/strat",
                           json={"strat_tag": None}).status_code == 200
        assert db.attempts()[0].strat_tag is None
        assert client.post("/api/attempts/999/strat",
                           json={"strat_tag": "X"}).status_code == 404
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_api.py -k attempt_strat -q`
Expected: FAIL — the POST returns 404 for the *valid* attempt id (route not registered), so the first assertion fails.

- [ ] **Step 3: Add the request model**

In `src/sm64_events/server/api.py`, after the existing `StratBody` class (~line 60):

```python
class AttemptStratBody(BaseModel):
    # null is meaningful, not missing: it unlabels the attempt
    strat_tag: str | None = None
```

- [ ] **Step 4: Add the route**

In `create_api_router`, directly after the `/attempts/{attempt_id}/restore` handler (~line 438):

```python
    @router.post("/attempts/{attempt_id}/strat")
    async def attempt_strat(attempt_id: int, body: AttemptStratBody):
        """Reclassify ONE recorded attempt (null strat_tag = no strategy).

        Distinct from POST /strat, which sets what to practice NEXT — this
        one edits history and triggers a re-projection."""
        try:
            await service.set_attempt_strat(attempt_id, body.strat_tag)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

- [ ] **Step 5: Document the route and the event**

`docs/api.md` documents the REST surface *and* the journal event payloads, and
`tests/test_docs_cover_api.py` fails on an undocumented route. Make three edits:

**(a)** After the `POST /api/attempts/{id}/restore` row (~line 120):

```markdown
| `POST /api/attempts/{id}/strat` `{strat_tag}` | Reclassify one recorded attempt's strategy (`null` = no strategy); journaled + re-projected, and moves any PB the attempt saved. Edits history — `POST /api/strat` is the one that sets what to practice next |
```

**(b)** In the event-payload table, after the `attempt_restored` row (~line 81):

```markdown
| `attempt_strat_set` | `attempt_id, strat_tag` | One attempt reclassified onto another strategy (`strat_tag` null = no strategy); last write wins, so re-picking the previous one is the undo (triggers full re-projection; `attempts_invalidated` follows) |
```

**(c)** The `PUT /api/markers` row (~line 126) ends with a note that is now
false — the segment strat dropdown shipped on 2026-07-23. Delete this sentence
from that row, leaving the rest of the row untouched:

```
Note: segment strat tags are settable via `target_set`/`strat_set` events but the practice-page strat dropdown is star-only in v1.
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `uv run pytest tests/test_api.py tests/test_docs_cover_api.py -q`
Expected: PASS — including the doc-coverage test, which fails if the route is undocumented.

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/server/api.py docs/api.md tests/test_api.py
git diff --cached --name-only     # confirm ONLY those three paths
git commit -F- <<'EOF'
feat(api): POST /api/attempts/{id}/strat to reclassify one attempt

Sits beside clear/restore because it is the same kind of thing — a
retroactive per-attempt correction — and deliberately apart from
POST /api/strat, which sets what to practice next rather than editing
history. null strat_tag is a value, not a missing field: it unlabels.

Also drops a note on the markers row that went stale when the segment strat
dropdown shipped earlier today.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 5: `StratPicker` accepts an injected writer

**Files:**
- Modify: `src/sm64_events/ui/components/stratpicker.js` (whole file is 58 lines — read it first)
- Test: none (no JS test runner in this repo; Task 6 covers it via the parity test and the manual check)

**Interfaces:**
- Consumes: nothing.
- Produces: three new optional props on `StratPicker`:
  - `submit(tag: string | null) -> Promise` — when given, replaces the default `POST /api/strat` write. Default `undefined` = today's behaviour, unchanged.
  - `blankLabel: string` — text of the empty option. Default `"— no strat —"`.
  - `highlightUnset: boolean` — apply the red `needs-strat` class when nothing is picked. Default `true`.

- [ ] **Step 1: Rewrite the component body**

Replace the `export function StratPicker` block in `src/sm64_events/ui/components/stratpicker.js` with:

```js
export function StratPicker({ entity, identity, strategies, active, onChanged,
                              submit, blankLabel = "— no strat —",
                              highlightUnset = true }) {
  // Bumped to force the <select> to remount and re-read `active`. A native
  // <select> change updates the DOM immediately, but if the write is dropped
  // (or cancelled) `active` stays null, so its `value` prop never changes and
  // Preact won't reset the element — the dropdown would keep showing a phantom
  // pick while the border stays red, then revert on the next unrelated
  // remount. Bumping the key snaps it back to the server's truth.
  const [nonce, setNonce] = useState(0);
  const [showModal, setShowModal] = useState(false);
  const options = strategies || [];

  async function setStrat(value) {
    if (value === "__new") { setShowModal(true); return; }
    const tag = value || null;
    try {
      // `submit` lets a caller redirect the write without forking the
      // component: the practice cards set the ACTIVE strategy, an attempt row
      // reclassifies one recorded attempt. One dropdown, so the modal, the
      // dropped-write recovery, and the styling can't drift apart.
      if (submit) await submit(tag);
      else await send("POST", "/api/strat", { ...identity, strat_tag: tag });
    } catch (e) {
      // A dropped write (tracker reconnecting, or a second copy running) must
      // NOT silently leave a phantom selection that later reverts. Tell the
      // user and force the dropdown back to the real, still-unset value.
      window.alert("Couldn't save the strategy — the tracker may be reconnecting "
        + "or a second copy of it is running. Please try again.");
      setNonce((n) => n + 1);
    }
    onChanged();   // resync the dropdown to the server's truth either way
  }

  return html`<select key=${`strat-${nonce}`}
      class="meta ${!active && highlightUnset ? "needs-strat" : ""}"
      value=${active || ""}
      onchange=${(changeEvent) => setStrat(changeEvent.target.value)}>
    <option value="">${blankLabel}</option>
    ${options.map((s) => html`<option value=${s}>${s}</option>`)}
    <option value="__new">+ new strat…</option>
  </select>
  ${showModal ? html`<${StratModal} entity=${entity} existing=${options}
      onSaved=${(stratName) => { setShowModal(false); setStrat(stratName); }}
      onClose=${() => { setShowModal(false); setNonce((n) => n + 1); }} />` : null}`;
}
```

Also extend the file's header comment — after the `identity` paragraph, add:

```js
// `submit(tag)` overrides where the pick is written (default: POST /api/strat
// with `identity`). The attempt rows in practice.js pass a submit that hits
// POST /api/attempts/{id}/strat, so reclassifying a recorded run reuses this
// exact dropdown — including the "+ new strat…" modal and the dropped-write
// recovery — instead of a second, drifting copy.
```

- [ ] **Step 2: Verify the existing cards are unaffected**

Run: `uv run pytest tests/test_ui_section_parity.py -q`
Expected: PASS.

Then confirm no call site passes the new props yet (they must still use defaults):

Run: `grep -n "StratPicker" src/sm64_events/ui/components/practice.js`
Expected: the two existing section call sites, neither mentioning `submit`.

- [ ] **Step 3: Commit**

```bash
git add src/sm64_events/ui/components/stratpicker.js
git diff --cached --name-only     # confirm ONLY that path
git commit -F- <<'EOF'
refactor(ui): let StratPicker's write target be injected

The attempt list needs the same dropdown pointed at a different endpoint.
Copying it would recreate exactly the drift this component was extracted to
end last week, so the write becomes a `submit` prop instead — defaults keep
both practice cards byte-identical in behaviour.

blankLabel and highlightUnset come along for the same reason: an attempt row
wants "— no strategy —" and no red outline (the outline is a pick-before-you-
practice nudge; on historical rows it would just be noise).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

### Task 6: The attempt row's dropdown

**Files:**
- Modify: `src/sm64_events/ui/components/practice.js` (`AttemptRow`, lines ~132-176)
- Modify: `CLAUDE.md` (module map rows for `tracking/projection.py` and `ui/components/stratpicker.js`)
- Test: `tests/test_ui_section_parity.py` (must stay green — no edit expected)

**Interfaces:**
- Consumes: `POST /api/attempts/{id}/strat` (Task 4); `StratPicker`'s `submit` / `blankLabel` / `highlightUnset` props (Task 5).
- Produces: nothing downstream.

- [ ] **Step 1: Move the entity derivation above the row markup**

In `AttemptRow`, the `entity` and `strat` consts are currently computed *after* the `row` template (~lines 163-171). The strat cell now needs `entity`, so move that block to just above `const row = html\`<tr ...` (~line 132), keeping its comment verbatim:

```js
  // Star rows don't carry course/star on the attempt itself (_attempt_json
  // omits them) — derive the entity from the section for stars, from the
  // attempt for segments.
  const entity = a.segment_id != null ? `segment:${a.segment_id}`
    : (sec ? `star:${sec.course_id}:${sec.star_id}` : null);
  const strat = a.strat_tag || (sec && sec.last_strat) || null;
```

Delete those same six lines from their old position below the row (leave the `onCompare` / `expandedRow` lines that follow them exactly where they are).

- [ ] **Step 2: Replace the static strat cell**

Replace line ~149:

```js
    <td class="meta">${a.rank ? html`<${Medal} rank=${a.rank} size=${14} /> ` : ""}${a.strat_tag || ""}</td>
```

with:

```js
    <td class="meta">
      ${a.rank ? html`<${Medal} rank=${a.rank} size=${14} /> ` : ""}
      ${sec
        ? html`<${StratPicker} entity=${entity} strategies=${sec.strategies}
            active=${a.strat_tag} blankLabel="— no strategy —"
            highlightUnset=${false}
            submit=${(tag) => send("POST", `/api/attempts/${a.id}/strat`,
                                   { strat_tag: tag })}
            onChanged=${t.refresh} />`
        : html`<span>${a.strat_tag || "— no strategy —"}</span>`}
    </td>
```

The `sec` fallback is the **unassigned** attempt block, which renders `AttemptTable` without a section: those attempts have no course/star/segment, so there is no entity and no strategy list — a dropdown there would open an empty menu. They still say `— no strategy —` rather than showing a blank cell.

- [ ] **Step 3: Verify the parity test still passes**

Run: `uv run pytest tests/test_ui_section_parity.py -q`
Expected: PASS. `AttemptRow` is shared by `StarSection`, `SegmentSection` and the unassigned block, so both cards gain the feature together — that is why no second implementation exists.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS.

- [ ] **Step 5: Smoke-test in a browser**

Start a dev server (port 8065 from source, so it cannot collide with the installed exe on 8064):

```bash
uv run python -m sm64_events.main
```

Open `http://127.0.0.1:8065/ui/`, find any star or segment card with recorded attempts, and confirm:
1. Each row's strat cell is a dropdown showing that attempt's strategy.
2. A row with no strategy reads `— no strategy —` with **no** red outline.
3. Changing one row's strategy sticks after the list refreshes, and does **not** change the section header's active strategy.
4. The medal beside the changed row re-grades (or disappears) if the two strategies have different ladders.
5. The browser console is clean.

Stop the server with CTRL+C. If the tracker is not attached to PJ64, the seeded history still renders — attachment is not required for this check.

- [ ] **Step 6: Update the module map**

In `CLAUDE.md`, amend two rows.

Append to the `Attempt state machine / projection` row's description (the `tracking/projection.py` row):

```
; per-attempt strat reclassification (`strat_overrides` pre-pass, caveat 16 — journaled `attempt_strat_set`, last write wins, applied at both strat_tag stamping sites)
```

Append to the `Active-strategy picker (star + segment)` row's description (the `ui/components/stratpicker.js` row):

```
. The write target is injectable (`submit` prop) — the practice cards set the ACTIVE strat, each attempt row reclassifies THAT attempt via `POST /api/attempts/{id}/strat` (`blankLabel`/`highlightUnset` tune the row variant)
```

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/ui/components/practice.js CLAUDE.md
git diff --cached --name-only     # confirm ONLY those two paths
git commit -F- <<'EOF'
feat(ui): reclassify an attempt's strategy from its row

Declaring a strat before a run and then doing a different one used to be
permanent, and an unlabeled attempt rendered as an empty cell — so "no
strategy" looked like a rendering gap rather than a state. Both are now
visible and fixable in place.

Reuses StratPicker with an injected write, so stars, segments and the rows
share one dropdown; the unassigned block keeps plain text because those
attempts have no entity and so no strategy list to offer.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
```

---

## Post-plan verification

After Task 6, before declaring done:

1. `uv run pytest -q` — full suite green.
2. `git log --oneline -6` — six focused commits, none containing another session's files.
3. The browser smoke check in Task 6 Step 5 was actually run, with its five points confirmed.

## Not in this plan (deliberate)

- **Bulk reclassification** — one row at a time by decision (spec, "Out of scope"). Revisit only if the per-row flow proves tedious in real use.
- **A `views.py` change** — none is needed: `_attempt_json` already emits `strat_tag`, and every derived surface (medals, averages, strategy unions, stats, graphs, compare) keys off it.
- **A `store.js` change** — `_reproject()` already broadcasts `attempts_invalidated`, which is in `REFRESH_ON`.
