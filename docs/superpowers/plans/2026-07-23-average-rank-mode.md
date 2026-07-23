# Average Rank Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A global `rank_mode` setting (PB / Avg 10 / Avg 50 / Best 10 / Best 50 / Lifetime) that switches every entity-level rank display from grading the saved per-strategy PB to grading the mean of valid runs.

**Architecture:** A mode registry + pure averaging helper in `ranks/classify.py` (foundation), one shared `_grading_basis` resolver in `tracking/views.py` that all three entity-level grading call sites route through, a `set_rank_mode` service command + `PUT /api/ranks/mode` endpoint persisting to the `ui_state` KV with a broadcast-only `rank_mode_changed` notice, and a header dropdown + banner basis line in the UI. Per-attempt medals (`_attempt_rank`) are untouched.

**Tech Stack:** Python 3.12 + FastAPI (uv, pytest), Preact/htm UI served from `ui/`.

**Spec:** `docs/superpowers/specs/2026-07-23-average-rank-mode-design.md`

## Global Constraints

- Run everything via `uv` (`uv run pytest -q`), never pip. Full suite MUST pass before merge (baseline: 1191 passed).
- Mode keys are exactly: `pb` (default), `avg10`, `avg50`, `best10`, `best50`, `lifetime`.
- Valid run = `outcome == "success"` AND `not cleared` AND `strat_tag` equals the graded strategy AND a non-None time on the grading clock AND NOT (clock == "rta" and frames == 0) (reset-race junk rows).
- Mean computed in frames, `round()`ed, graded via existing `display_cs` → `rank_for`.
- Clock per display site mirrors today exactly: section banner = view clock; `rank_by_star` + route star candidates = `"igt"`; segments = `"rta"` everywhere.
- `rank_mode` is stored in the `ui_state` KV (`db.get_state`/`set_state`), never journaled. Unknown stored values read back as `pb`.
- Error taxonomy: ValueError → 409, RuntimeError → 503 (same as ranks_api today).
- Browser ↔ GUI parity: all UI work in `ui/` only; no desktop/ changes.
- All paths below are relative to the worktree root; `src/sm64_events/` prefix omitted in prose but explicit in Files lists.

## File Structure / Wave Map

| Wave | Task | Owns (no other task touches these) |
|---|---|---|
| 1 foundation | 1 | `src/sm64_events/ranks/classify.py`, `tests/test_ranks_classify.py` |
| 2 fan-out | 2 (views) | `src/sm64_events/tracking/views.py`, `tests/test_views.py` |
| 2 fan-out | 3 (service+API) | `src/sm64_events/tracking/service.py`, `src/sm64_events/server/ranks_api.py`, `tests/test_ranks_api.py` |
| 2 fan-out | 4 (UI) | `src/sm64_events/ui/components/ranks.js`, `src/sm64_events/ui/components/header.js`, `src/sm64_events/ui/store.js` |
| 3 integration | 5 | `CLAUDE.md`, `README.md`, full-suite gate |

Tasks 2, 3, 4 are mutually independent once Task 1 lands (they build against the frozen contracts below). Task 3's service edit is deliberately minimal/additive because uncommitted WIP on main also touches `tracking/service.py` (see `.planning/average-rank-mode/intent.md` collision notes).

**Frozen contracts (defined in Task 1, consumed everywhere):**

```python
# ranks/classify.py
RANK_MODES: dict[str, dict]   # key -> {"label": str, "window": int|None, "order": "recent"|"top"|None}
DEFAULT_RANK_MODE = "pb"
def average_frames(frames_list: list[int], window: int | None, order: str) -> tuple[int, int] | None
```

```text
Session view payload:  view["rank_mode"] = "pb" | "avg10" | ...
Banner payload:        sec["rank"] gains "mode": <key> always; in non-pb modes with a gradeable
                       average also "basis": {"frames", "display", "count", "window"};
                       sentinels become {"rank": None, "reason": ..., "mode": <key>}.
REST:                  PUT /api/ranks/mode  body {"mode": "avg10"} -> 200 {"ok": true} | 409
WS:                    broadcast-only event type "rank_mode_changed", payload {"mode": <key>}
```

---

### Task 1: Mode registry + averaging helper (`ranks/classify.py`)

**Files:**
- Modify: `src/sm64_events/ranks/classify.py` (append at end)
- Test: `tests/test_ranks_classify.py` (append at end)

**Interfaces:**
- Consumes: nothing.
- Produces: `RANK_MODES`, `DEFAULT_RANK_MODE`, `average_frames(frames_list, window, order) -> (mean_frames, count) | None` — exactly as in the frozen contracts block.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_ranks_classify.py`:

```python
# -- rank modes (average rank mode spec) ---------------------------------------

def test_rank_modes_registry_complete():
    from sm64_events.ranks.classify import DEFAULT_RANK_MODE, RANK_MODES
    assert DEFAULT_RANK_MODE == "pb" and "pb" in RANK_MODES
    assert list(RANK_MODES) == ["pb", "avg10", "avg50", "best10", "best50",
                                "lifetime"]
    for mode_def in RANK_MODES.values():
        assert set(mode_def) == {"label", "window", "order"}
    assert RANK_MODES["pb"]["order"] is None
    assert RANK_MODES["avg10"] == {"label": "Avg 10", "window": 10,
                                   "order": "recent"}
    assert RANK_MODES["best50"] == {"label": "Best 50", "window": 50,
                                    "order": "top"}
    assert RANK_MODES["lifetime"] == {"label": "Lifetime", "window": None,
                                      "order": "recent"}


def test_average_frames_recent_takes_the_last_window():
    from sm64_events.ranks.classify import average_frames
    # last 2 of [300, 310, 320, 330] are 320+330 -> mean 325
    assert average_frames([300, 310, 320, 330], 2, "recent") == (325, 2)


def test_average_frames_top_takes_the_fastest_window():
    from sm64_events.ranks.classify import average_frames
    # fastest 2 are 300+310 -> mean 305, regardless of position
    assert average_frames([330, 300, 320, 310], 2, "top") == (305, 2)


def test_average_frames_under_window_and_lifetime():
    from sm64_events.ranks.classify import average_frames
    # fewer than window -> mean of what exists (count reports actual)
    assert average_frames([300, 310], 10, "recent") == (305, 2)
    assert average_frames([300, 310], 10, "top") == (305, 2)
    # window None -> every entry (Lifetime)
    assert average_frames([300, 301, 302], None, "recent") == (301, 3)


def test_average_frames_empty_is_none():
    from sm64_events.ranks.classify import average_frames
    assert average_frames([], 10, "recent") is None
    assert average_frames([], None, "recent") is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ranks_classify.py -q`
Expected: FAIL / ERROR with `ImportError: cannot import name 'RANK_MODES'`

- [ ] **Step 3: Implement** — append to `src/sm64_events/ranks/classify.py`:

```python
# Rank-mode registry (average rank mode spec): HOW an entity-level rank
# display picks the time it grades. order None = the saved per-strategy PB
# row (no averaging); "recent" = the last `window` valid runs; "top" = the
# `window` fastest ever; window None = every valid run. Adding a mode is one
# row here (ui/components/ranks.js RANK_MODE_OPTIONS mirrors the labels).
RANK_MODES = {
    "pb":       {"label": "PB",       "window": None, "order": None},
    "avg10":    {"label": "Avg 10",   "window": 10,   "order": "recent"},
    "avg50":    {"label": "Avg 50",   "window": 50,   "order": "recent"},
    "best10":   {"label": "Best 10",  "window": 10,   "order": "top"},
    "best50":   {"label": "Best 50",  "window": 50,   "order": "top"},
    "lifetime": {"label": "Lifetime", "window": None, "order": "recent"},
}
DEFAULT_RANK_MODE = "pb"


def average_frames(frames_list: list[int], window: int | None,
                   order: str) -> tuple[int, int] | None:
    """(mean_frames, count_used) over the selected slice of `frames_list`
    (chronological), or None when empty. order "recent" keeps the last
    `window` entries, "top" the fastest `window`; window None takes all.
    Fewer than `window` entries -> mean of what exists (count tells)."""
    if not frames_list:
        return None
    if order == "top":
        chosen = sorted(frames_list)[:window] if window else sorted(frames_list)
    else:
        chosen = frames_list[-window:] if window else list(frames_list)
    return round(sum(chosen) / len(chosen)), len(chosen)
```

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_ranks_classify.py -q`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ranks/classify.py tests/test_ranks_classify.py
git commit -m "feat(ranks): RANK_MODES registry + average_frames (rank-mode foundation)"
```

---

### Task 2: Grading resolver + view payload (`tracking/views.py`)

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (functions `_strat_rank`, `_section_banner`, `_candidate_rank`, `build_session_view`, `build_route_view`; new helpers after `_attempt_rank`)
- Test: `tests/test_views.py` (update `test_section_banner_sentinel_when_standards_but_no_strat`; append new tests)

**Interfaces:**
- Consumes: Task 1's `RANK_MODES`, `DEFAULT_RANK_MODE`, `average_frames` (import via the existing `from sm64_events.ranks import classify`).
- Produces: `view["rank_mode"]`, banner `mode`/`basis` keys, mode-aware `rank_by_star` and route step ranks — the payload contract Task 4 renders. Internal helpers `_valid_frames(history, strat, clock)` and `_grading_basis(mode, pb, history, strat, clock)`.

- [ ] **Step 1: Update the existing sentinel test + write the failing tests.**

In `tests/test_views.py`, REPLACE the body of `test_section_banner_sentinel_when_standards_but_no_strat` result assertions (the five `result*` blocks) with (signature: basis replaces pb; sentinels gain `"mode"`):

```python
    # entity HAS standards, no active strat → "pick a strat" sentinel
    result = _section_banner(ranks, "star:2:2", strat=None, basis=None, mode="pb")
    assert result == {"rank": None, "reason": "no_strat", "mode": "pb"}
    # entity HAS standards, active strat with a ladder but NO time on it yet →
    # UNRANKED (a PB on another strat must not be borrowed here)
    result2 = _section_banner(ranks, "star:2:2", strat="fast", basis=None, mode="pb")
    assert result2 == {"rank": None, "reason": "unranked", "mode": "pb"}
    # entity HAS standards, active strat has no ladder → no_ladder sentinel
    result3 = _section_banner(ranks, "star:2:2", strat="unknown_strat",
                              basis={"frames": 343, "count": 1, "window": None},
                              mode="pb")
    assert result3 == {"rank": None, "reason": "no_ladder", "mode": "pb"}
    # entity has NO standards → None (don't render banner at all)
    result4 = _section_banner(ranks, "star:8:1", strat=None, basis=None, mode="pb")
    assert result4 is None
    # ranks is None → None
    result5 = _section_banner(None, "star:2:2", strat="fast",
                              basis={"frames": 343, "count": 1, "window": None},
                              mode="pb")
    assert result5 is None
```

Then APPEND these new tests at the end of `tests/test_views.py`:

```python
# -- rank modes (average rank mode spec) ---------------------------------------

def _mode_ranks(tmp_path):
    """Ladder where the seeded PB (343f = 11.43s displayed) is Mario but the
    seeded mean (343+350 -> 346f = 11.53s) is Diamond; 'slow' has a ladder
    but will have no valid runs (attempts are tagged 'fast')."""
    import json
    from sm64_events.ranks.standards import RankStandards
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 1, "entities": {
        "star:2:2": {"clock": "igt", "strategies": {
            "fast": {"Mario": 11.44, "Diamond": 12.0, "Silver": 13.0},
            "slow": {"Mario": 11.44, "Diamond": 12.0, "Silver": 13.0}}}}}))
    s = RankStandards(p); s.load(); return s


def _seed_fast_with_pb(db, svc, tmp_path):
    seed(svc)
    svc.ranks = _mode_ranks(tmp_path)
    asyncio.run(svc.set_strat(2, 2, "fast"))
    # per-strategy ranking: attempts + the PB row must carry 'fast'
    db._conn.execute("UPDATE attempts SET strat_tag='fast' WHERE course_id=2")
    db._conn.commit()
    best_aid = next(a.id for a in db.attempts() if a.igt_frames == 343)
    asyncio.run(svc.save_pb(best_aid, "igt"))
    return best_aid


def test_rank_mode_average_grades_the_mean_not_the_pb(tmp_path):
    """pb mode grades the saved PB (343f -> Mario); avg modes grade the MEAN
    of valid runs (343+350 -> 346f -> Diamond) and ship the basis."""
    db, svc = make(tmp_path)
    _seed_fast_with_pb(db, svc, tmp_path)

    view = build_session_view(db, svc, clock="igt")     # default mode: pb
    [sec] = view["stars"]
    assert view["rank_mode"] == "pb"
    assert sec["rank"]["rank"] == "Mario" and sec["rank"]["mode"] == "pb"
    assert "basis" not in sec["rank"]
    assert view["rank_by_star"]["2:2"] == "Mario"

    db.set_state("rank_mode", "avg10")
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert view["rank_mode"] == "avg10"
    assert sec["rank"]["rank"] == "Diamond" and sec["rank"]["mode"] == "avg10"
    assert sec["rank"]["basis"] == {"frames": 346, "display": "0'11\"53",
                                    "count": 2, "window": 10}
    assert view["rank_by_star"]["2:2"] == "Diamond"
    # per-attempt medals stay per-run: the 343f attempt still reads Mario
    assert [a["rank"] for a in sec["attempts"]
            if a["outcome"] == "success"][0] == "Mario"


def test_rank_mode_average_excludes_cleared_runs(tmp_path):
    """Clearing the 350f run shrinks the average to just 343f -> Mario,
    count 1 (valid = successful AND not purged)."""
    db, svc = make(tmp_path)
    _seed_fast_with_pb(db, svc, tmp_path)
    db.set_state("rank_mode", "avg10")
    slow_aid = next(a.id for a in db.attempts() if a.igt_frames == 350)
    asyncio.run(svc.clear_attempt(slow_aid, reason="test purge"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["rank"]["rank"] == "Mario"
    assert sec["rank"]["basis"]["count"] == 1
    assert sec["rank"]["basis"]["frames"] == 343


def test_rank_mode_unranked_when_strat_has_no_valid_runs(tmp_path):
    """avg mode + a strategy with a ladder but zero valid runs (all attempts
    are tagged 'fast', active strat is 'slow') -> unranked sentinel carrying
    the mode (the UI words it 'no valid runs on this strategy yet')."""
    db, svc = make(tmp_path)
    _seed_fast_with_pb(db, svc, tmp_path)
    db.set_state("rank_mode", "avg10")
    asyncio.run(svc.set_strat(2, 2, "slow"))
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert sec["rank"] == {"rank": None, "reason": "unranked", "mode": "avg10"}


def test_rank_mode_unknown_stored_value_falls_back_to_pb(tmp_path):
    db, svc = make(tmp_path)
    _seed_fast_with_pb(db, svc, tmp_path)
    db.set_state("rank_mode", "bogus")
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    assert view["rank_mode"] == "pb"
    assert sec["rank"]["rank"] == "Mario" and sec["rank"]["mode"] == "pb"


def test_rank_mode_best_order_grades_the_fastest_runs(tmp_path):
    """best10 takes the FASTEST runs ever: after adding a slow 380f success,
    avg10 (mean of all 3 = 358f -> Diamond) differs from pb (Mario); best10
    with window 10 still averages all 3 here, so distinguish orders at the
    pure level (test_ranks_classify) and verify best10 plumbs through with
    the top-order basis."""
    db, svc = make(tmp_path)
    _seed_fast_with_pb(db, svc, tmp_path)
    asyncio.run(svc.publish(ev("practice_reset", 3000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(star(3500, igt=380)))
    db._conn.execute("UPDATE attempts SET strat_tag='fast' WHERE course_id=2")
    db._conn.commit()
    db.set_state("rank_mode", "best10")
    view = build_session_view(db, svc, clock="igt")
    [sec] = view["stars"]
    # mean of [343, 350, 380] = 357.67 -> 358f = 11.93s displayed -> Diamond
    assert sec["rank"]["rank"] == "Diamond"
    assert sec["rank"]["basis"]["count"] == 3
    assert sec["rank"]["basis"]["window"] == 10


def test_route_candidate_rank_follows_rank_mode(tmp_path):
    from sm64_events.tracking.views import build_route_view
    db, svc = make(tmp_path)
    _seed_fast_with_pb(db, svc, tmp_path)
    rid = asyncio.run(svc.create_route({"name": "V", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 2}]}]}))
    assert build_route_view(db, svc, rid)["steps"][0]["rank"] == "Mario"
    db.set_state("rank_mode", "avg10")
    assert build_route_view(db, svc, rid)["steps"][0]["rank"] == "Diamond"
```

- [ ] **Step 2: Run to verify the new tests fail**

Run: `uv run pytest tests/test_views.py -q -k "rank_mode or sentinel"`
Expected: FAIL — `TypeError: _section_banner() got an unexpected keyword argument 'basis'` and `KeyError: 'rank_mode'` variants.

- [ ] **Step 3: Implement in `src/sm64_events/tracking/views.py`.**

3a. After `_attempt_rank` (currently ends line ~193), INSERT the two helpers:

```python
def _valid_frames(history, strat, clock) -> list[int]:
    """Chronological times (frames) of the runs that count toward an average
    (average rank mode spec): successful, not cleared (manual purge or
    auto-ignore), achieved WITH `strat`, with a real time on `clock` —
    excluding the rta==0 reset-race junk rows (projection.py docstring).
    `history` is journal-id ordered, so the list is chronological."""
    out = []
    for a in history:
        if a.outcome != "success" or a.cleared or a.strat_tag != strat:
            continue
        frames = a.igt_frames if clock == "igt" else a.rta_frames
        if frames is None or (clock == "rta" and frames == 0):
            continue
        out.append(frames)
    return out


def _grading_basis(mode, pb, history, strat, clock) -> dict | None:
    """THE one 'which time does this rank grade?' resolver. Returns
    {"frames", "count", "window"} or None when nothing is gradeable.
    'pb' mode wraps the saved per-strategy PB row (count 1) — byte-for-byte
    today's grading; avg modes grade attempt history via classify.average_frames,
    so a run never saved as PB still counts."""
    mode_def = classify.RANK_MODES.get(mode) or classify.RANK_MODES["pb"]
    if mode_def["order"] is None:
        return ({"frames": pb["frames"], "count": 1, "window": None}
                if pb else None)
    averaged = classify.average_frames(_valid_frames(history, strat, clock),
                                       mode_def["window"], mode_def["order"])
    if averaged is None:
        return None
    mean_frames, count = averaged
    return {"frames": mean_frames, "count": count,
            "window": mode_def["window"]}
```

3b. REPLACE `_strat_rank` (parameter `pb` becomes `basis`; docstring updated):

```python
def _strat_rank(ranks, ek, strat, basis) -> str | None:
    """Rank NAME for an entity graded under `strat` at its grading basis
    (_grading_basis output: PB row in pb mode, mean of valid runs in avg
    modes), or None when ungradeable (no ranks loaded, no active strat, no
    basis, or the strat has no ladder). THE single grading path shared by
    route candidates and the stage quick-select star grid (view's
    rank_by_star) — keep it one place so a medal never disagrees with the
    section banner / attempt medals."""
    if ranks is None or not strat or basis is None:
        return None
    ladder = ranks.ladder_cs(ek, strat)
    if not ladder:
        return None
    return classify.rank_for(ladder, classify.display_cs(basis["frames"]))
```

3c. REPLACE `_section_banner` (pb → basis + mode; every return except the
no-standards None carries `"mode"`; non-pb modes attach `"basis"`):

```python
def _section_banner(ranks, ek, strat, basis, mode) -> dict | None:
    """Rank banner for a section: the grading basis (PB in pb mode, mean of
    valid runs in avg modes — _grading_basis) graded under the ACTIVE strat.

    Returns None when the entity has NO standards (RankBanner not rendered).
    Otherwise the entity HAS standards; a {"rank": None, "reason": ...}
    sentinel says why it can't be graded so the UI can word it correctly:
      - "no_strat"  : no active strategy selected.
      - "no_ladder" : the active strategy has no rank thresholds defined.
      - "unranked"  : the strategy has a ladder but nothing gradeable — no
                      saved PB (pb mode) / no valid runs (avg modes) on THIS
                      strategy (another strategy's times never count).
    Every payload carries "mode"; non-pb modes with a gradeable basis also
    carry "basis" {frames, display, count, window} — what the rank is based
    on (drives the banner's 'avg of N' line)."""
    if ranks is None:
        return None
    has_standards = bool(ranks.ladders(ek))
    if not has_standards:
        return None
    if not strat:
        return {"rank": None, "reason": "no_strat", "mode": mode}
    ladder = ranks.ladder_cs(ek, strat)
    if not ladder:
        return {"rank": None, "reason": "no_ladder", "mode": mode}
    if basis is None:
        return {"rank": None, "reason": "unranked", "mode": mode}
    out = classify.band(ladder, classify.display_cs(basis["frames"]))
    out["mode"] = mode
    if mode != "pb":
        out["basis"] = {"frames": basis["frames"],
                        "display": format_igt(basis["frames"]),
                        "count": basis["count"], "window": basis["window"]}
    return out
```

3d. In `build_session_view`, after the `time_filters_state = ...` line add:

```python
    rank_mode = db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    if rank_mode not in classify.RANK_MODES:   # forward-safe: junk reads as pb
        rank_mode = classify.DEFAULT_RANK_MODE
```

3e. REPLACE the star section's `"rank":` entry:

```python
            "rank": _section_banner(
                service.ranks, entity_key(course_id, star_id),
                (star_strat := service.strat_by_star.get((course_id, star_id))),
                _grading_basis(
                    rank_mode,
                    pbs_by_strat.get((course_id, star_id, clock, star_strat)),
                    history, star_strat, clock),
                rank_mode),
```

3f. REPLACE the segment section's `"rank":` entry:

```python
            "rank": _section_banner(
                service.ranks, entity_key(None, None, seg_id),
                (seg_strat := service.strat_by_segment.get(seg_id)),
                _grading_basis(
                    rank_mode,
                    pbs_by_strat.get(("segment", seg_id, "rta", seg_strat)),
                    history, seg_strat, "rta"),
                rank_mode),
```

3g. REPLACE the `"rank_by_star"` entry in the return dict (grid stays igt):

```python
        "rank_by_star": {
            f"{c}:{s}": rank
            for (c, s), strat in service.strat_by_star.items()
            if (rank := _strat_rank(
                service.ranks, entity_key(c, s), strat,
                _grading_basis(
                    rank_mode, pbs_by_strat.get((c, s, "igt", strat)),
                    [a for a in all_attempts
                     if a.course_id == c and a.star_id == s],
                    strat, "igt")))},
```

3h. Add `"rank_mode": rank_mode,` to the same return dict (next to `"stage"`).

3i. REPLACE `_candidate_rank` (gains `mode` + `attempts`; builds a basis):

```python
def _candidate_rank(db, service, c, mode, attempts) -> str | None:
    """Rank for one route candidate under its active strat, graded by the
    rank-mode basis (per-strategy: another strat's times never count)."""
    if service.ranks is None:
        return None  # skip the lookups entirely when nothing can be graded
    if c["type"] == "segment":
        ek = entity_key(None, None, c["segment_id"])
        strat = service.strat_by_segment.get(c["segment_id"])
        clock = "rta"
        history = [a for a in attempts if a.segment_id == c["segment_id"]]
        pb = (db.current_pb(None, None, "rta", segment_id=c["segment_id"],
                            strat_tag=strat) if strat else None)
    else:
        ek = entity_key(c["course"], c["star"])
        strat = service.strat_by_star.get((c["course"], c["star"]))
        clock = "igt"
        history = [a for a in attempts
                   if a.course_id == c["course"] and a.star_id == c["star"]]
        pb = (db.current_pb(c["course"], c["star"], "igt", strat_tag=strat)
              if strat else None)
    return _strat_rank(service.ranks, ek, strat,
                       _grading_basis(mode, pb, history, strat, clock))
```

3j. In `build_route_view`, after `attempts = db.attempts()` add the same
two-line `rank_mode` read as 3d, and change the `ranks_here` line to:

```python
        ranks_here = [_candidate_rank(db, service, c, rank_mode, attempts)
                      for c in step["candidates"]]
```

- [ ] **Step 4: Run the module's tests**

Run: `uv run pytest tests/test_views.py -q`
Expected: PASS (all, including the updated sentinel test and pre-existing rank tests — pb default preserves every old grading result)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat(views): rank-mode grading basis — banner/grid/route medals follow rank_mode"
```

---

### Task 3: Service command + REST endpoint

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (one import + one method after `clear_rank_video`)
- Modify: `src/sm64_events/server/ranks_api.py` (one body model + one route)
- Test: `tests/test_ranks_api.py` (append)

**Interfaces:**
- Consumes: Task 1's `RANK_MODES` (`from sm64_events.ranks.classify import RANK_MODES`).
- Produces: `await service.set_rank_mode(mode: str)` (ValueError on unknown mode, RuntimeError when db-less), `PUT /api/ranks/mode` `{"mode": str}` → `{"ok": true}` / 409 / 503, broadcast-only `Event(type="rank_mode_changed", payload={"mode": mode})`.

**Collision note:** uncommitted WIP on main also edits `tracking/service.py` — keep this edit strictly additive (one import line, one method) so the eventual merge is a clean text merge.

- [ ] **Step 1: Write the failing tests** — append to `tests/test_ranks_api.py`:

```python
# -- rank mode (average rank mode spec) ----------------------------------------

def test_put_rank_mode_persists_and_validates(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        r = client.put("/api/ranks/mode", json={"mode": "avg10"})
        assert r.status_code == 200 and r.json() == {"ok": True}
        assert svc.db.get_state("rank_mode", "pb") == "avg10"
        # every registry key round-trips
        for mode in ["pb", "avg50", "best10", "best50", "lifetime"]:
            assert client.put("/api/ranks/mode",
                              json={"mode": mode}).status_code == 200
        # junk -> 409, stored value untouched
        r = client.put("/api/ranks/mode", json={"mode": "bogus"})
        assert r.status_code == 409
        assert svc.db.get_state("rank_mode", "pb") == "lifetime"


def test_set_rank_mode_broadcasts_rank_mode_changed(tmp_path):
    import asyncio
    client, svc = make_client(tmp_path)
    seen = []

    async def capture(event):
        seen.append(event)

    svc.broadcaster.publish = capture
    asyncio.run(svc.set_rank_mode("best10"))
    assert [e.type for e in seen] == ["rank_mode_changed"]
    assert seen[0].payload == {"mode": "best10"}
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_ranks_api.py -q`
Expected: FAIL — 404/405 on `/api/ranks/mode`, `AttributeError: ... no attribute 'set_rank_mode'`

- [ ] **Step 3: Implement.**

3a. `src/sm64_events/tracking/service.py` — add to the imports block:

```python
from sm64_events.ranks.classify import RANK_MODES
```

and add after `clear_rank_video` (end of the rank-standards command group):

```python
    async def set_rank_mode(self, mode: str) -> None:
        """Persist the global rank-grading mode (average rank mode spec) to
        the ui_state KV and notify. Broadcast-only like the other rank
        commands: a display preference, never journaled."""
        if mode not in RANK_MODES:
            raise ValueError(f"unknown rank mode: {mode!r}")
        if self.db is None:
            raise RuntimeError("tracking database unavailable")
        self.db.set_state("rank_mode", mode)
        await self.broadcaster.publish(Event(type="rank_mode_changed",
                                             frame=0, timestamp_utc=_now(),
                                             payload={"mode": mode}))
```

3b. `src/sm64_events/server/ranks_api.py` — add next to the other body models:

```python
class ModeBody(BaseModel):
    mode: str
```

and add this route inside `create_ranks_router` (after `put_threshold`):

```python
    @router.put("/ranks/mode")
    async def put_mode(body: ModeBody):
        try:
            await service.set_rank_mode(body.mode)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

- [ ] **Step 4: Run the module's tests**

Run: `uv run pytest tests/test_ranks_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/service.py src/sm64_events/server/ranks_api.py tests/test_ranks_api.py
git commit -m "feat(api): PUT /api/ranks/mode — persist + broadcast the global rank mode"
```

---

### Task 4: UI — header picker, banner basis line, store refetch

**Files:**
- Modify: `src/sm64_events/ui/components/ranks.js`
- Modify: `src/sm64_events/ui/components/header.js`
- Modify: `src/sm64_events/ui/store.js`

**Interfaces:**
- Consumes: `view.rank_mode`, banner `mode`/`basis`, `PUT /api/ranks/mode`, WS `rank_mode_changed` (Tasks 2+3 contracts — buildable against the frozen contracts even before those tasks merge).
- Produces: `RANK_MODE_OPTIONS` export from ranks.js (header imports it).

No JS test runner exists; the gate is Step 3's smoke check + the wave-3 full suite.

- [ ] **Step 1: `ranks.js`** — add below the existing `RANK_COLORS`/`FG` block:

```javascript
// Mirrors ranks/classify.RANK_MODES keys+labels (keep in lockstep) in
// dropdown order; the header's Rank picker renders from this.
export const RANK_MODE_OPTIONS = [["pb", "PB"], ["avg10", "Avg 10"],
  ["avg50", "Avg 50"], ["best10", "Best 10"], ["best50", "Best 50"],
  ["lifetime", "Lifetime"]];
const MODE_LABEL = Object.fromEntries(RANK_MODE_OPTIONS);
```

REPLACE the `RANK_SENTINEL` block + the sentinel line in `RankBanner` so the
unranked wording is mode-aware, and add the basis line. Full new bottom half
of the file (from `const RANK_SENTINEL`):

```javascript
// Sentinel wording (server sends {rank:null, reason, mode}): a strategy is
// ranked ONLY by times achieved with it, so "unranked" means no gradeable
// time on THIS strat yet — the saved PB in pb mode, valid runs in avg modes.
const RANK_SENTINEL = {
  unranked: "— unranked (no PB on this strategy yet)",
  unranked_avg: "— unranked (no valid runs on this strategy yet)",
  no_ladder: "— no rank standards for this strategy",
  no_strat: "— pick a strat to see your rank",
};

function sentinelMsg(banner) {
  if (!banner) return RANK_SENTINEL.no_strat;
  if (banner.reason === "unranked" && banner.mode && banner.mode !== "pb")
    return RANK_SENTINEL.unranked_avg;
  return RANK_SENTINEL[banner.reason] || RANK_SENTINEL.no_strat;
}

export function RankBanner({ banner }) {
  if (!banner || !banner.rank) {
    return html`<span class="meta">${sentinelMsg(banner)}</span>`;
  }
  const c = rankColor(banner.rank);
  const gap = banner.gap_cs != null ? (banner.gap_cs / 100).toFixed(2) : null;
  const basis = banner.basis;
  return html`<div style=${`display:flex;align-items:center;gap:12px;border:1px solid ${c}55;border-radius:8px;padding:8px 12px;background:linear-gradient(90deg, ${c}33, transparent)`}>
    <${Medal} rank=${banner.rank} size=${30} />
    <div>
      <div style="font-weight:800;letter-spacing:.4px">${banner.rank.toUpperCase()}
        ${basis && html` <span class="meta" style="font-weight:400">
          ${MODE_LABEL[banner.mode] || banner.mode} · avg of ${basis.count}${basis.window ? `/${basis.window}` : ""} · ${basis.display}</span>`}
      </div>
      ${banner.next
        ? html`<div class="meta">next: <b>${banner.next}</b> −${gap}s
            <div style="height:6px;width:200px;background:#0d1117;border-radius:3px;margin-top:4px;overflow:hidden">
              <i style=${`display:block;height:100%;width:${Math.round((banner.fill || 0) * 100)}%;background:${c}`}></i>
            </div></div>`
        : html`<div class="meta">top rank</div>`}
    </div>
  </div>`;
}
```

- [ ] **Step 2: `header.js` + `store.js`.**

2a. `header.js` — extend the ranks import and add the picker. Import line:

```javascript
import { RANK_MODE_OPTIONS } from "./ranks.js";
```

Insert directly AFTER the Clock `</span>` (the `Clock:` span keeps
`margin-left:auto`; Rank sits to its right):

```javascript
    ${v && html`<span>Rank:
      <select id="rankmode-select" name="rank_mode" value=${v.rank_mode}
              title="What rank medals grade: your saved PB, or the average of your last/best N valid runs"
              onchange=${(e) => send("PUT", "/api/ranks/mode", { mode: e.target.value }).then(() => t.refresh())}>
        ${RANK_MODE_OPTIONS.map(([k, label]) => html`<option value=${k}>${label}</option>`)}
      </select>
    </span>`}
```

2b. `store.js` — add `"rank_mode_changed"` to the `REFRESH_ON` set (the
session-view refetch also re-pulls the RouteFocus route view, which keys on
`t.view`). New set:

```javascript
const REFRESH_ON = new Set(["attempt_completed", "attempts_invalidated",
  "pb_saved", "pb_undone", "session_started", "target_changed",
  "star_collected", "strat_set", "rank_standards_changed",
  "rank_mode_changed"]);
```

- [ ] **Step 3: Smoke check (no JS test runner).**

Run: `uv run pytest -q` (server suite unaffected — must stay green), then
`uv run python -m sm64_events.main` from the worktree root and open
`http://127.0.0.1:8065/ui/`:
- Rank dropdown renders next to Clock with 6 options, PB selected.
- Switching to Avg 10 PUTs (Network tab 200), the view refetches, and any
  section with rank standards shows the basis line / updated medal.
- Known limitation (accepted in spec review): the Routes TAB's medals
  refresh on its own next fetch (selection/edit), not instantly on mode
  change; the Practice tab's RouteFocus updates immediately via t.view.

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/components/ranks.js src/sm64_events/ui/components/header.js src/sm64_events/ui/store.js
git commit -m "feat(ui): rank-mode picker in header + banner basis line + live refetch"
```

---

### Task 5: Integration — docs + full-suite gate

**Files:**
- Modify: `CLAUDE.md` (module-map rows for classify.py / ranks_api.py / ranks.js+header.js)
- Modify: `README.md` (API surface: PUT /api/ranks/mode + rank_mode in the session view + rank_mode_changed event)

**Interfaces:**
- Consumes: everything above, merged onto `feature/average-rank-mode`.
- Produces: the mergeable branch.

- [ ] **Step 1: Full suite on the merged branch**

Run: `uv run pytest -q`
Expected: PASS (baseline 1191 + the new tests, 0 failures)

- [ ] **Step 2: CLAUDE.md module map** — extend three existing rows (do not add rows):
  - `Rank classification (pure)` row: append "; ALSO `RANK_MODES` (pb/avg10/avg50/best10/best50/lifetime) + `average_frames` — THE rank-mode registry (average rank mode: entity-level medals grade the mean of valid runs; views.py `_grading_basis` is the one resolver; per-attempt medals stay per-run)".
  - `Rank REST surface` row: append "; `PUT /api/ranks/mode` (global rank mode → ui_state KV, broadcast-only `rank_mode_changed`)".
  - `Rank UI (badge/banner/table/route medals)` row: append "; header Rank picker (`RANK_MODE_OPTIONS` mirror in ranks.js) + banner 'avg of N' basis line".

- [ ] **Step 3: README** — in the REST/WS surface section add:
  - `PUT /api/ranks/mode` `{"mode": "pb"|"avg10"|"avg50"|"best10"|"best50"|"lifetime"}` — what entity-level rank medals grade (PB vs mean of last/best-N/all valid runs); session view carries `rank_mode`; broadcast-only `rank_mode_changed` follows.

- [ ] **Step 4: Commit**

```bash
git add CLAUDE.md README.md
git commit -m "docs: rank-mode registry, endpoint and picker in module map + README"
```

- [ ] **Step 5: Final whole-branch review** (mandatory — superpowers:requesting-code-review), then hand to wrap-feature for merge.
