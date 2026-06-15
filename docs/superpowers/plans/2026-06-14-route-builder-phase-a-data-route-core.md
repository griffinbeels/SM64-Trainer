# Route Builder — Phase A (Data + Route Core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist routes (ordered star/segment plans with "K of N" group steps), expose CRUD + cumulative-success + self-contained import/export over the REST API — the foundation every later phase builds on.

**Architecture:** Server-authoritative and projection-consistent, mirroring `segment_defs`. A new `routes` table (config) + db CRUD; a new **pure** `tracking/routes.py` (validation, cumulative-success math, export/import resolution); thin service commands and a route-view payload; REST endpoints reusing the existing error taxonomy. No `main.py` change (the service loads routes from the db like `segment_defs`).

**Tech Stack:** Python 3.12 (uv), SQLite (sqlite3 + WAL), FastAPI, pytest. UI is deferred to Phase B.

**Source spec:** `docs/superpowers/specs/2026-06-14-route-builder-design.md` (§4 data model, §5.1/§5.3/§5.4/§5.5/§5.6, §7 import/export, §8 validation).

**Scope note:** This is plan **1 of 5**. Phases B (builder UI), C (practice focus), D (run mode + `runs` table at migration v8), and E (run history + graph) each get their own plan file. Phase A creates **only** the `routes` table (migration **v7**); the `runs` table is Phase D's migration v8.

**Convention:** every commit message in this plan ends with the repo trailer
`Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`
(omitted from the commands below for brevity). Stage explicit paths — `git add -A` is hook-blocked in this repo. Verify you are on the intended branch before each commit (shared checkout).

---

## File Structure

- **Create** `src/sm64_events/tracking/routes.py` — pure route logic: `validate_route`, `route_stats` (cumulative success), `export_route`, `resolve_import`. No db, no I/O.
- **Modify** `src/sm64_events/storage/db.py` — migration **v7** (`routes` table) + `routes()`, `insert_route()`, `update_route()`, `delete_route()`.
- **Modify** `src/sm64_events/tracking/service.py` — `create_route` / `update_route` / `delete_route` / `export_route` / `import_route` + `routes_changed` broadcast + segment-existence check.
- **Modify** `src/sm64_events/tracking/views.py` — `build_route_view(db, route_id)` (resolves names + cumulative + broken flag).
- **Modify** `src/sm64_events/server/api.py` — `/api/routes` CRUD + `/api/routes/{id}/export` + `/api/routes/import`.
- **Test** `tests/test_routes.py` (new) · `tests/test_storage.py` · `tests/test_tracker_service.py` · `tests/test_views.py` · `tests/test_api.py`.
- **Modify** `CLAUDE.md` — module-map rows for routes.

---

## Task 1: `routes` table (migration v7) + db CRUD

**Files:**
- Modify: `src/sm64_events/storage/db.py` (MIGRATIONS list; new methods after the segment-defs block ~line 341)
- Test: `tests/test_storage.py`

- [ ] **Step 1: Update existing version assertions (the v7 ripple)**

Adding a migration bumps `PRAGMA user_version` from 6 to 7. Update every assertion in `tests/test_storage.py` that pins the current max version:

- `test_migrations_set_user_version_and_create_tables`: `== 6` → `== 7`; add `"routes"` to the subset set so it reads `{"events", "sessions", "attempts", "pbs", "ui_state", "routes"} <= names`.
- `test_reopening_existing_db_is_idempotent`: both `== 6` → `== 7`.
- `test_v1_database_upgrades_in_place`: `== 6` → `== 7`.
- `test_v3_database_pb_rows_survive_v4_rebuild`: `== 6` → `== 7`.
- `test_v5_updates_existing_v4_lblj_row_with_area_anchor`: `== 6` → `== 7`.
- `test_v6_repairs_existing_bowser3_end_trigger`: `== 6` → `== 7`.
- `test_failed_migration_rolls_back_schema_and_version`: the rollback assertion `== 6` → `== 7`, and the final "fixed entry applies" assertion `== 7` → `== 8`.

- [ ] **Step 2: Write the failing test for the routes table + CRUD**

Add to `tests/test_storage.py`:

```python
# -- migration v7: routes (ordered star/segment plans) -----------------------

def test_migration_v7_creates_routes_table(tmp_path):
    db = make_db(tmp_path)
    names = {r["name"] for r in db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert "routes" in names


def test_route_crud_roundtrip(tmp_path):
    db = make_db(tmp_path)
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]},
             {"need": 2, "label": "Whomp's", "candidates": [
                 {"type": "star", "course": 2, "star": 0},
                 {"type": "star", "course": 2, "star": 1},
                 {"type": "star", "course": 2, "star": 2}]}]
    rid = db.insert_route("Standard", steps, "2026-06-14T00:00:00Z")
    [row] = db.routes()
    assert row["id"] == rid and row["name"] == "Standard"
    assert row["steps"] == steps                       # JSON round-trips
    assert row["created_utc"] == row["updated_utc"]
    db.update_route(rid, name="Standard v2", updated_utc="2026-06-14T01:00:00Z")
    row = db.routes()[0]
    assert row["name"] == "Standard v2"
    assert row["updated_utc"] == "2026-06-14T01:00:00Z"
    db.delete_route(rid)
    assert db.routes() == []


def test_update_route_unknown_field_raises(tmp_path):
    import pytest
    db = make_db(tmp_path)
    rid = db.insert_route("R", [], "2026-06-14T00:00:00Z")
    with pytest.raises(ValueError, match="unknown"):
        db.update_route(rid, bogus="x")


def test_update_delete_unknown_route_raises_lookup(tmp_path):
    import pytest
    db = make_db(tmp_path)
    with pytest.raises(LookupError):
        db.update_route(999, name="x")
    with pytest.raises(LookupError):
        db.delete_route(999)
```

- [ ] **Step 3: Run to verify it fails**

Run: `uv run pytest tests/test_storage.py -q`
Expected: FAIL — `no such table: routes` / `AttributeError: 'Database' object has no attribute 'insert_route'`.

- [ ] **Step 4: Add migration v7**

Append to the `MIGRATIONS` list in `src/sm64_events/storage/db.py` (after the v6 entry):

```python
    # v7 — routes: ordered star/segment practice plans (spec 2026-06-14).
    # Config like segment_defs (NOT history); steps is JSON, see
    # tracking/routes.py for the shape. The runs table arrives in v8 (Phase D).
    """
    CREATE TABLE IF NOT EXISTS routes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      steps TEXT NOT NULL,
      created_utc TEXT NOT NULL,
      updated_utc TEXT NOT NULL
    );
    """,
```

- [ ] **Step 5: Add the CRUD methods**

In `src/sm64_events/storage/db.py`, after `delete_segment_def` (before the `# -- pbs` section), add:

```python
    # -- routes (config) -----------------------------------------------------
    def routes(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routes ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "steps": json.loads(r["steps"]),
                 "created_utc": r["created_utc"],
                 "updated_utc": r["updated_utc"]} for r in rows]

    def insert_route(self, name: str, steps: list, created_utc: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO routes (name, steps, created_utc, updated_utc)"
                " VALUES (?,?,?,?)",
                (name, json.dumps(steps), created_utc, created_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_route(self, route_id: int, **fields) -> None:
        cols = {"name": lambda v: v, "steps": json.dumps,
                "updated_utc": lambda v: v}
        if set(fields) - set(cols):
            raise ValueError(f"unknown fields {sorted(set(fields) - set(cols))}")
        sets, vals = [], []
        for k, conv in cols.items():
            if k in fields:
                sets.append(f"{k}=?"); vals.append(conv(fields[k]))
        if not sets:
            return
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE routes SET {','.join(sets)} WHERE id=?",
                (*vals, route_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"route {route_id} not found")

    def delete_route(self, route_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM routes WHERE id=?",
                                     (route_id,))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"route {route_id} not found")
```

- [ ] **Step 6: Run to verify it passes**

Run: `uv run pytest tests/test_storage.py -q`
Expected: PASS (all storage tests, including the version-bump updates).

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/storage/db.py tests/test_storage.py
git commit -m "feat(storage): routes table (migration v7) + CRUD"
```

---

## Task 2: `validate_route` (pure structural validation)

**Files:**
- Create: `src/sm64_events/tracking/routes.py`
- Test: `tests/test_routes.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_routes.py`:

```python
import pytest

from sm64_events.tracking.projection import Attempt
from sm64_events.tracking.routes import (export_route, resolve_import,
                                         route_stats, validate_route)


def att(**o):
    """Attempt factory: defaults to a segment success, override as needed."""
    d = dict(id=1, session_id=1, course_id=None, star_id=None, strat_tag=None,
             anchor_type="practice_reset", anchor_frame=0, outcome="success",
             outcome_detail=None, igt_frames=300, rta_frames=300,
             started_utc="t", ended_utc="t", cleared=False,
             cleared_reason=None, segment_id=None)
    d.update(o)
    return Attempt(**d)


def test_validate_route_accepts_minimal():
    validate_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_accepts_group_and_label():
    validate_route({"name": "R", "steps": [
        {"need": 2, "label": "Whomp's", "candidates": [
            {"type": "star", "course": 2, "star": 0},
            {"type": "segment", "segment_id": 5}]}]})


def test_validate_route_rejects_empty_name():
    with pytest.raises(ValueError, match="name"):
        validate_route({"name": "  ", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_rejects_empty_steps():
    with pytest.raises(ValueError, match="steps"):
        validate_route({"name": "R", "steps": []})


def test_validate_route_rejects_need_out_of_range():
    with pytest.raises(ValueError, match="need"):
        validate_route({"name": "R", "steps": [
            {"need": 2, "candidates": [{"type": "star", "course": 2, "star": 0}]}]})


def test_validate_route_rejects_bad_candidate_type():
    with pytest.raises(ValueError):
        validate_route({"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "banana"}]}]})


def test_validate_route_rejects_star_without_ints():
    with pytest.raises(ValueError):
        validate_route({"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "star", "course": 2}]}]})
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.tracking.routes'`.

- [ ] **Step 3: Create `routes.py` with the envelope constants + validation**

Create `src/sm64_events/tracking/routes.py`:

```python
"""Route model + cumulative success + import/export resolution (spec 2026-06-14).

A route is an ordered list of STEPS; each step is a "complete K of N" group
(a single item is need=1 with one candidate). Steps reference segments by
LOCAL id; portability is handled at export (segment defs embedded) / import
(reconciled against the local segment list). Pure functions only — no db, no
I/O — so the service/view layers wire it and pytest covers the math directly.

No-data rule (user decision 2026-06-14): a step with no logged attempts has a
success rate of 0.0, which zeroes the cumulative product from that step down.
Group rate = product of the BEST-K candidate rates (K=1 'pick one' = the most
reliable option's rate)."""
from sm64_events.stats.registry import compute_stat

ROUTE_EXPORT_KIND = "sm64-route"
ROUTE_EXPORT_VERSION = 1


def _is_int(x) -> bool:
    # bool is an int subclass; reject it so True/False can't pose as ids
    return isinstance(x, int) and not isinstance(x, bool)


def _validate_item(item) -> None:
    if not isinstance(item, dict):
        raise ValueError("each candidate must be an object")
    kind = item.get("type")
    if kind == "star":
        if not (_is_int(item.get("course")) and _is_int(item.get("star"))):
            raise ValueError("star candidate needs integer course and star")
    elif kind == "segment":
        if not _is_int(item.get("segment_id")):
            raise ValueError("segment candidate needs an integer segment_id")
    else:
        raise ValueError(f"unknown candidate type {kind!r}")


def validate_route(d: dict) -> None:
    """Raise ValueError on the first structural problem (API maps it to 409).
    Structural only — segment_id EXISTENCE is checked in the service, where the
    db is available."""
    if not str(d.get("name", "")).strip():
        raise ValueError("name is required")
    steps = d.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError("each step must be an object")
        cands = step.get("candidates")
        if not isinstance(cands, list) or not cands:
            raise ValueError("each step needs a non-empty candidates list")
        need = step.get("need")
        if not _is_int(need) or need < 1 or need > len(cands):
            raise ValueError("need must be an integer in 1..len(candidates)")
        for c in cands:
            _validate_item(c)
```

- [ ] **Step 4: Run to verify validation tests pass**

Run: `uv run pytest tests/test_routes.py -q`
Expected: the seven `validate_route` tests PASS. (`route_stats`/`export_route`/`resolve_import` imports resolve but their tests come in Tasks 3–5.)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/routes.py tests/test_routes.py
git commit -m "feat(routes): pure route definition validation"
```

---

## Task 3: `route_stats` — per-step + cumulative success

**Files:**
- Modify: `src/sm64_events/tracking/routes.py`
- Test: `tests/test_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes.py`:

```python
def test_route_stats_single_step_uses_item_success_rate():
    # segment 1: 2 success + 1 reset -> 2/3
    attempts = [att(segment_id=1, outcome="success"),
                att(segment_id=1, outcome="success"),
                att(segment_id=1, outcome="reset")]
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]}]
    [s] = route_stats(steps, attempts)
    assert abs(s["step_rate"] - 2 / 3) < 1e-9
    assert abs(s["cumulative"] - 2 / 3) < 1e-9


def test_route_stats_no_data_is_zero_and_zeroes_downstream():
    attempts = [att(segment_id=1, outcome="success")]  # only step 1 has data
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]},
             {"need": 1, "candidates": [{"type": "star", "course": 9, "star": 9}]}]
    s1, s2 = route_stats(steps, attempts)
    assert s1["step_rate"] == 1.0 and s1["cumulative"] == 1.0
    assert s2["step_rate"] == 0.0 and s2["cumulative"] == 0.0


def test_route_stats_group_uses_best_k_product():
    # seg1 = 100% (1/1), seg2 = 50% (1 success, 1 reset), seg3 = 0% (no data)
    # need 2 -> best two rates = 1.0 * 0.5 = 0.5
    attempts = [att(segment_id=1, outcome="success"),
                att(segment_id=2, outcome="success"),
                att(segment_id=2, outcome="reset")]
    steps = [{"need": 2, "candidates": [
        {"type": "segment", "segment_id": 1},
        {"type": "segment", "segment_id": 2},
        {"type": "segment", "segment_id": 3}]}]
    [s] = route_stats(steps, attempts)
    assert abs(s["step_rate"] - 0.5) < 1e-9


def test_route_stats_star_item_ignores_segment_attempts():
    # an attempt on (course 2, star 0) as a STAR must not be confused with a
    # segment attempt; segment_id None is the discriminator
    attempts = [att(segment_id=None, course_id=2, star_id=0, outcome="success"),
                att(segment_id=None, course_id=2, star_id=0, outcome="death")]
    steps = [{"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]
    [s] = route_stats(steps, attempts)
    assert abs(s["step_rate"] - 0.5) < 1e-9
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes.py -k route_stats -q`
Expected: FAIL — `ImportError: cannot import name 'route_stats'` (until implemented) or `AttributeError`.

- [ ] **Step 3: Implement the math**

Append to `src/sm64_events/tracking/routes.py`:

```python
def _item_attempts(item: dict, attempts):
    if item["type"] == "segment":
        sid = item["segment_id"]
        return [a for a in attempts if a.segment_id == sid]
    c, s = item["course"], item["star"]
    return [a for a in attempts
            if a.segment_id is None and a.course_id == c and a.star_id == s]


def _item_rate(item: dict, attempts) -> float:
    """Lifetime success rate for one item; no data -> 0.0.

    Reuses the registry's success_rate stat (failures = reset/hard_reset/death,
    cleared attempts excluded). success_rate ignores the clock arg."""
    rate = compute_stat("success_rate", _item_attempts(item, attempts), {}, "igt")
    return rate if rate is not None else 0.0


def _step_rate(step: dict, attempts) -> float:
    """Product of the best-K candidate rates (K = step['need'])."""
    rates = sorted((_item_rate(c, attempts) for c in step["candidates"]),
                   reverse=True)
    product = 1.0
    for r in rates[:step["need"]]:
        product *= r
    return product


def route_stats(steps: list, attempts) -> list[dict]:
    """Per-step success rate + cumulative (running product), in route order.
    attempts is the full lifetime attempt list (caller scopes nothing)."""
    out, cumulative = [], 1.0
    for step in steps:
        sr = _step_rate(step, attempts)
        cumulative *= sr
        out.append({"step_rate": sr, "cumulative": cumulative})
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_routes.py -q`
Expected: PASS (validation + route_stats tests).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/routes.py tests/test_routes.py
git commit -m "feat(routes): cumulative success math (best-K, no-data=0)"
```

---

## Task 4: `export_route` — self-contained JSON

**Files:**
- Modify: `src/sm64_events/tracking/routes.py`
- Test: `tests/test_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes.py`:

```python
def test_export_route_embeds_segment_defs_and_keeps_stars():
    segs = {1: {"name": "LBLJ", "start_triggers": [{"type": "spawned"}],
                "end_triggers": [{"type": "level_enter", "to": 6}],
                "guards": []}}
    steps = [{"need": 1, "candidates": [{"type": "segment", "segment_id": 1}]},
             {"need": 1, "label": "star step",
              "candidates": [{"type": "star", "course": 2, "star": 0}]}]
    out = export_route("R", steps, segs)
    assert out["kind"] == "sm64-route" and out["version"] == 1 and out["name"] == "R"
    seg = out["steps"][0]["candidates"][0]
    assert seg == {"type": "segment", "segment": {
        "name": "LBLJ", "start_triggers": [{"type": "spawned"}],
        "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}}
    assert out["steps"][1]["label"] == "star step"
    assert out["steps"][1]["candidates"][0] == {"type": "star", "course": 2, "star": 0}


def test_export_route_raises_on_missing_segment():
    with pytest.raises(ValueError, match="missing segment"):
        export_route("R", [{"need": 1, "candidates": [
            {"type": "segment", "segment_id": 99}]}], {})
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes.py -k export -q`
Expected: FAIL — `cannot import name 'export_route'` / not yet defined.

- [ ] **Step 3: Implement export**

Append to `src/sm64_events/tracking/routes.py`:

```python
def export_route(name: str, steps: list, segment_defs: dict) -> dict:
    """Self-contained export. Segment candidates embed their full definition
    (resolved from segment_defs: id -> {name, start_triggers, end_triggers,
    guards}); star candidates are portable as-is. Raises ValueError if a step
    references a segment id not in segment_defs."""
    out_steps = []
    for step in steps:
        cands = []
        for c in step["candidates"]:
            if c["type"] == "segment":
                d = segment_defs.get(c["segment_id"])
                if d is None:
                    raise ValueError(
                        f"route references missing segment {c['segment_id']}")
                cands.append({"type": "segment", "segment": {
                    "name": d["name"], "start_triggers": d["start_triggers"],
                    "end_triggers": d["end_triggers"], "guards": d["guards"]}})
            else:
                cands.append(dict(c))
        out_step = {"need": step["need"], "candidates": cands}
        if step.get("label") is not None:
            out_step["label"] = step["label"]
        out_steps.append(out_step)
    return {"kind": ROUTE_EXPORT_KIND, "version": ROUTE_EXPORT_VERSION,
            "name": name, "steps": out_steps}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_routes.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/routes.py tests/test_routes.py
git commit -m "feat(routes): self-contained export (embed segment defs)"
```

---

## Task 5: `resolve_import` — reconcile against local segments

**Files:**
- Modify: `src/sm64_events/tracking/routes.py`
- Test: `tests/test_routes.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_routes.py`:

```python
def _payload(*step_cands):
    return {"kind": "sm64-route", "version": 1, "name": "R",
            "steps": [{"need": 1, "candidates": list(cs)} for cs in step_cands]}


def test_resolve_import_reuses_exact_match_creates_rest():
    existing = [{"id": 7, "name": "LBLJ", "start_triggers": [{"type": "spawned"}],
                 "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}]
    payload = _payload(
        [{"type": "segment", "segment": {
            "name": "LBLJ", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}}],
        [{"type": "segment", "segment": {
            "name": "New", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 9}], "guards": []}}])
    res = resolve_import(payload, existing)
    assert res["name"] == "R"
    assert res["reused"] == ["LBLJ"] and res["created"] == ["New"]
    assert res["steps"][0]["candidates"][0] == {"type": "segment", "segment_id": 7}
    assert res["steps"][1]["candidates"][0] == {"type": "segment", "create_index": 0}
    assert len(res["to_create"]) == 1 and res["to_create"][0]["name"] == "New"


def test_resolve_import_dedupes_repeated_new_segment():
    payload = _payload(
        [{"type": "segment", "segment": {"name": "Dup", "start_triggers": [],
                                         "end_triggers": [], "guards": []}}],
        [{"type": "segment", "segment": {"name": "Dup", "start_triggers": [],
                                         "end_triggers": [], "guards": []}}])
    res = resolve_import(payload, [])
    assert len(res["to_create"]) == 1                      # created once
    assert res["steps"][0]["candidates"][0]["create_index"] == 0
    assert res["steps"][1]["candidates"][0]["create_index"] == 0


def test_resolve_import_keeps_star_candidates():
    res = resolve_import(_payload([{"type": "star", "course": 2, "star": 0}]), [])
    assert res["steps"][0]["candidates"][0] == {"type": "star", "course": 2, "star": 0}


def test_resolve_import_rejects_bad_kind_or_version():
    with pytest.raises(ValueError):
        resolve_import({"kind": "nope", "version": 1, "name": "R",
                        "steps": [{"need": 1, "candidates": []}]}, [])
    with pytest.raises(ValueError):
        resolve_import({"kind": "sm64-route", "version": 99, "name": "R",
                        "steps": [{"need": 1, "candidates": []}]}, [])
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_routes.py -k resolve -q`
Expected: FAIL — `cannot import name 'resolve_import'`.

- [ ] **Step 3: Implement import resolution**

Append to `src/sm64_events/tracking/routes.py`:

```python
def _segment_matches(emb: dict, existing: dict) -> bool:
    return (existing["name"] == emb["name"]
            and existing["start_triggers"] == emb["start_triggers"]
            and existing["end_triggers"] == emb["end_triggers"]
            and existing.get("guards", []) == emb.get("guards", []))


def resolve_import(payload: dict, existing_defs: list) -> dict:
    """Pure reconciliation of an imported route against the local segment list.

    Returns {name, steps, to_create, reused, created}:
      - steps: ready to persist EXCEPT segment candidates carry either
        {"type":"segment","segment_id":<existing id>} (exact match reused) or
        {"type":"segment","create_index": i} (service creates to_create[i] then
        rewrites these to a real segment_id).
      - to_create: unique embedded segment defs with no local exact match.
      - reused / created: segment-name lists for the dry-run preview.
    Raises ValueError on a bad envelope or malformed step."""
    if payload.get("kind") != ROUTE_EXPORT_KIND:
        raise ValueError("not an sm64-route export")
    if payload.get("version") != ROUTE_EXPORT_VERSION:
        raise ValueError(f"unsupported route version {payload.get('version')!r}")
    name = str(payload.get("name", "")).strip()
    if not name:
        raise ValueError("import is missing a route name")
    steps_in = payload.get("steps")
    if not isinstance(steps_in, list) or not steps_in:
        raise ValueError("import has no steps")

    to_create, reused, created, out_steps = [], [], [], []
    for step in steps_in:
        if not isinstance(step, dict) or not isinstance(step.get("candidates"), list):
            raise ValueError("each step needs a candidates list")
        cands = []
        for c in step["candidates"]:
            if not isinstance(c, dict):
                raise ValueError("each candidate must be an object")
            if c.get("type") == "segment":
                emb = c.get("segment")
                if not isinstance(emb, dict) or not str(emb.get("name", "")).strip():
                    raise ValueError("embedded segment is missing its definition")
                emb_def = {"name": emb["name"],
                           "start_triggers": emb.get("start_triggers", []),
                           "end_triggers": emb.get("end_triggers", []),
                           "guards": emb.get("guards", [])}
                match = next((e for e in existing_defs
                              if _segment_matches(emb_def, e)), None)
                if match is not None:
                    cands.append({"type": "segment", "segment_id": match["id"]})
                    reused.append(emb_def["name"])
                else:
                    idx = next((i for i, d in enumerate(to_create)
                                if _segment_matches(emb_def, d)), None)
                    if idx is None:
                        idx = len(to_create)
                        to_create.append(emb_def)
                        created.append(emb_def["name"])
                    cands.append({"type": "segment", "create_index": idx})
            elif c.get("type") == "star":
                cands.append({"type": "star", "course": c.get("course"),
                              "star": c.get("star")})
            else:
                raise ValueError(f"unknown candidate type {c.get('type')!r}")
        out_step = {"need": step.get("need", 1), "candidates": cands}
        if step.get("label") is not None:
            out_step["label"] = step["label"]
        out_steps.append(out_step)
    return {"name": name, "steps": out_steps, "to_create": to_create,
            "reused": reused, "created": created}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_routes.py -q`
Expected: PASS (entire `routes.py` pure suite).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/routes.py tests/test_routes.py
git commit -m "feat(routes): import resolution (reuse exact match, create rest)"
```

---

## Task 6: Service route CRUD + segment-existence check

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (imports near line 26; new methods after `delete_segment`)
- Test: `tests/test_tracker_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tracker_service.py`:

```python
# -- routes (Phase A) ---------------------------------------------------------

def test_create_route_persists_and_broadcasts(tmp_path):
    db, svc, sent = make_rec(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = asyncio.run(svc.create_route({
        "name": "Test Route", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
            {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    assert any(r["id"] == rid and r["name"] == "Test Route" for r in db.routes())
    assert any(e.type == "routes_changed" for e in sent)


def test_create_route_rejects_missing_segment(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.create_route({"name": "Bad", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": 99999}]}]}))


def test_create_route_rejects_invalid_definition(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.create_route({"name": "", "steps": []}))


def test_update_and_delete_route(tmp_path):
    db, svc = make(tmp_path)
    rid = asyncio.run(svc.create_route({"name": "R", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    asyncio.run(svc.update_route(rid, {"name": "R2"}))
    assert next(r for r in db.routes() if r["id"] == rid)["name"] == "R2"
    asyncio.run(svc.delete_route(rid))
    assert all(r["id"] != rid for r in db.routes())
    with pytest.raises(LookupError):
        asyncio.run(svc.delete_route(rid))


def test_update_route_unknown_id_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        asyncio.run(svc.update_route(999, {"name": "x"}))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_tracker_service.py -k route -q`
Expected: FAIL — `AttributeError: 'TrackerService' object has no attribute 'create_route'`.

- [ ] **Step 3: Add the imports**

In `src/sm64_events/tracking/service.py`, the existing line:

```python
from sm64_events.tracking.segments import SegmentDef, validate_definition
```

Add below it:

```python
from sm64_events.tracking import routes as route_logic
```

- [ ] **Step 4: Add the CRUD methods**

In `src/sm64_events/tracking/service.py`, after `delete_segment` (and its `_segments_changed` helper), add a new section:

```python
    # -- routes ----------------------------------------------------------------
    def _check_segment_refs(self, db: Database, steps: list) -> None:
        """Every segment candidate must reference an existing def (LookupError
        -> 404). Star candidates need no db check."""
        ids = {d["id"] for d in db.segment_defs()}
        for step in steps:
            for c in step["candidates"]:
                if c["type"] == "segment" and c["segment_id"] not in ids:
                    raise LookupError(f"segment {c['segment_id']} not found")

    async def create_route(self, d: dict) -> int:
        db = self._require_db()
        route_logic.validate_route(d)          # structural, BEFORE insert
        self._check_segment_refs(db, d["steps"])
        rid = db.insert_route(d["name"], d["steps"], _iso(_now()))
        await self._routes_changed()
        return rid

    async def update_route(self, route_id: int, d: dict) -> None:
        db = self._require_db()
        current = next((r for r in db.routes() if r["id"] == route_id), None)
        if current is None:
            raise LookupError(f"route {route_id} not found")
        merged = {**current, **d}              # partial patch validates as whole
        route_logic.validate_route(merged)
        self._check_segment_refs(db, merged["steps"])
        db.update_route(route_id, updated_utc=_iso(_now()),
                        **{k: d[k] for k in ("name", "steps") if k in d})
        await self._routes_changed()

    async def delete_route(self, route_id: int) -> None:
        db = self._require_db()
        db.delete_route(route_id)
        await self._routes_changed()

    async def _routes_changed(self) -> None:
        """Broadcast-only (like segment notices): routes are config, never
        journaled. The UI refetches the route list on this event."""
        await self.broadcaster.publish(Event(type="routes_changed", frame=0,
                                              timestamp_utc=_now(), payload={}))
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_tracker_service.py -k route -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_tracker_service.py
git commit -m "feat(service): route CRUD with segment-existence check"
```

---

## Task 7: Service export/import

**Files:**
- Modify: `src/sm64_events/tracking/service.py` (route section)
- Test: `tests/test_tracker_service.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_tracker_service.py`:

```python
def test_export_then_import_reuses_existing_segment(tmp_path):
    db, svc = make(tmp_path)
    lblj = seed_id(db, "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "Exp", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]},
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]}]}))
    payload = svc.export_route(rid)
    assert payload["kind"] == "sm64-route" and payload["version"] == 1
    assert payload["steps"][0]["candidates"][0]["segment"]["name"] == "LBLJ"
    preview = asyncio.run(svc.import_route(payload, dry_run=True))
    assert preview["reused"] == ["LBLJ"] and preview["created"] == []
    out = asyncio.run(svc.import_route(payload))
    imported = next(r for r in db.routes() if r["id"] == out["id"])
    assert imported["steps"][0]["candidates"][0] == {"type": "segment",
                                                     "segment_id": lblj}


def test_import_creates_missing_segment(tmp_path):
    db, svc = make(tmp_path)
    payload = {"kind": "sm64-route", "version": 1, "name": "Imp", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment": {
            "name": "Brand New Seg", "start_triggers": [{"type": "spawned"}],
            "end_triggers": [{"type": "level_enter", "to": 6}], "guards": []}}]}]}
    before = len(db.segment_defs())
    out = asyncio.run(svc.import_route(payload))
    assert len(db.segment_defs()) == before + 1
    new = next(d for d in db.segment_defs() if d["name"] == "Brand New Seg")
    imported = next(r for r in db.routes() if r["id"] == out["id"])
    assert imported["steps"][0]["candidates"][0]["segment_id"] == new["id"]


def test_export_unknown_route_raises(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        svc.export_route(999)


def test_import_rejects_bad_envelope(tmp_path):
    db, svc = make(tmp_path)
    with pytest.raises(ValueError):
        asyncio.run(svc.import_route({"kind": "nope", "version": 1, "name": "x",
                                      "steps": [{"need": 1, "candidates": []}]}))
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_tracker_service.py -k "export or import" -q`
Expected: FAIL — `AttributeError: ... 'export_route'`.

- [ ] **Step 3: Implement export/import on the service**

Append to the route section in `src/sm64_events/tracking/service.py`:

```python
    def export_route(self, route_id: int) -> dict:
        """Self-contained export (sync — read-only). Embeds segment defs."""
        db = self._require_db()
        route = next((r for r in db.routes() if r["id"] == route_id), None)
        if route is None:
            raise LookupError(f"route {route_id} not found")
        defs = {d["id"]: d for d in db.segment_defs()}
        return route_logic.export_route(route["name"], route["steps"], defs)

    async def import_route(self, payload: dict, dry_run: bool = False) -> dict:
        """Reconcile + (unless dry_run) create missing segments and the route.
        Preview returns the reuse/create summary without writing anything."""
        db = self._require_db()
        resolved = route_logic.resolve_import(payload, db.segment_defs())
        if dry_run:
            return {"name": resolved["name"], "reused": resolved["reused"],
                    "created": resolved["created"], "dry_run": True}
        new_ids = []
        for emb in resolved["to_create"]:
            validate_definition({**emb, "enabled": True})
            new_ids.append(db.insert_segment_def(
                emb["name"], emb["start_triggers"], emb["end_triggers"],
                emb["guards"], _iso(_now())))
        steps = self._finalize_import_steps(resolved["steps"], new_ids)
        rid = db.insert_route(resolved["name"], steps, _iso(_now()))
        await self._routes_changed()
        return {"id": rid, "name": resolved["name"], "reused": resolved["reused"],
                "created": resolved["created"], "dry_run": False}

    @staticmethod
    def _finalize_import_steps(steps: list, new_ids: list) -> list:
        """Rewrite {"create_index": i} segment candidates to real ids."""
        out = []
        for step in steps:
            cands = []
            for c in step["candidates"]:
                if c.get("type") == "segment" and "create_index" in c:
                    cands.append({"type": "segment",
                                  "segment_id": new_ids[c["create_index"]]})
                else:
                    cands.append(c)
            ns = {"need": step["need"], "candidates": cands}
            if step.get("label") is not None:
                ns["label"] = step["label"]
            out.append(ns)
        return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_tracker_service.py -q`
Expected: PASS (full service suite).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_tracker_service.py
git commit -m "feat(service): route export + import (dry-run preview)"
```

---

## Task 8: `build_route_view` — resolved payload with cumulative

**Files:**
- Modify: `src/sm64_events/tracking/views.py` (import near line 27; new function at end)
- Test: `tests/test_views.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_views.py`:

```python
def test_build_route_view_resolves_names_and_cumulative(tmp_path):
    from sm64_events.tracking.views import build_route_view
    db, svc = make(tmp_path)
    lblj = next(d["id"] for d in db.segment_defs() if d["name"] == "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "V", "steps": [
        {"need": 1, "candidates": [{"type": "star", "course": 2, "star": 0}]},
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}))
    view = build_route_view(db, rid)
    assert view["name"] == "V"
    star_cand = view["steps"][0]["candidates"][0]
    assert star_cand["display"] == "Shoot into the Wild Blue"
    assert star_cand["course_name"] == "Bob-omb Battlefield"
    seg_cand = view["steps"][1]["candidates"][0]
    assert seg_cand["display"] == "LBLJ" and seg_cand["kind"] == "segment"
    # no attempts logged -> 0% rate, cumulative 0 from the first step
    assert view["steps"][0]["step_rate"] == 0.0
    assert view["steps"][0]["cumulative"] == 0.0
    assert view["steps"][1]["broken"] is False


def test_build_route_view_marks_deleted_segment_broken(tmp_path):
    from sm64_events.tracking.views import build_route_view
    db, svc = make(tmp_path)
    lblj = next(d["id"] for d in db.segment_defs() if d["name"] == "LBLJ")
    rid = asyncio.run(svc.create_route({"name": "V", "steps": [
        {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]}))
    asyncio.run(svc.delete_segment(lblj))
    view = build_route_view(db, rid)
    assert view["steps"][0]["broken"] is True
    assert "deleted" in view["steps"][0]["candidates"][0]["display"]


def test_build_route_view_unknown_route_raises(tmp_path):
    import pytest
    from sm64_events.tracking.views import build_route_view
    db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        build_route_view(db, 999)
```

> Note: `course_name(2)` is `"Bob-omb Battlefield"` and `star_name(2, 0)` is `"Shoot into the Wild Blue"` (see `tests/test_api.py::test_session_view_roundtrip` and `addresses.py`). If your addresses table differs, read the expected strings from `addresses.COURSE_NAMES` / `star_name` and adjust these two assertions only.

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_views.py -k route -q`
Expected: FAIL — `cannot import name 'build_route_view'`.

- [ ] **Step 3: Implement the view builder**

In `src/sm64_events/tracking/views.py`, add to the existing projection import block:

```python
from sm64_events.tracking.routes import route_stats
```

Then append at the end of the file:

```python
def build_route_view(db, route_id: int) -> dict:
    """Resolve a route for display: each step's candidates get names, plus the
    per-step success rate and cumulative product (tracking/routes.route_stats).
    A candidate whose segment was deleted is marked broken (no cascade)."""
    route = next((r for r in db.routes() if r["id"] == route_id), None)
    if route is None:
        raise LookupError(f"route {route_id} not found")
    attempts = db.attempts()
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    stats = route_stats(route["steps"], attempts)
    steps = []
    for step, st in zip(route["steps"], stats):
        cands, broken = [], False
        for c in step["candidates"]:
            if c["type"] == "segment":
                name = seg_names.get(c["segment_id"])
                if name is None:
                    broken = True
                    name = f"segment {c['segment_id']} (deleted)"
                cands.append({"kind": "segment", "segment_id": c["segment_id"],
                              "display": name})
            else:
                cands.append({"kind": "star", "course": c["course"],
                              "star": c["star"],
                              "display": star_name(c["course"], c["star"]),
                              "course_name": course_name(c["course"])})
        steps.append({"label": step.get("label"), "need": step["need"],
                      "candidates": cands, "step_rate": st["step_rate"],
                      "cumulative": st["cumulative"], "broken": broken})
    return {"id": route["id"], "name": route["name"], "steps": steps}
```

- [ ] **Step 4: Run to verify it passes**

Run: `uv run pytest tests/test_views.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views.py
git commit -m "feat(views): route view payload (names + cumulative + broken)"
```

---

## Task 9: REST endpoints

**Files:**
- Modify: `src/sm64_events/server/api.py` (Pydantic bodies near line 88; routes near the segments endpoints ~line 153; import of `build_route_view`)
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py`:

```python
def _lblj(db):
    return next(d["id"] for d in db.segment_defs() if d["name"] == "LBLJ")


def test_route_crud_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        r = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": lblj}]}]})
        assert r.status_code == 200
        rid = r.json()["id"]
        assert any(x["id"] == rid for x in client.get("/api/routes").json())
        v = client.get(f"/api/routes/{rid}")
        assert v.status_code == 200
        assert v.json()["steps"][0]["broken"] is False
        assert client.put(f"/api/routes/{rid}",
                          json={"name": "R2"}).status_code == 200
        assert client.delete(f"/api/routes/{rid}").status_code == 200
        assert client.get(f"/api/routes/{rid}").status_code == 404


def test_create_route_bad_segment_is_404(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "segment_id": 99999}]}]})
        assert r.status_code == 404


def test_create_route_invalid_is_409(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        r = client.post("/api/routes", json={"name": "", "steps": []})
        assert r.status_code == 409


def test_route_export_import_endpoints(tmp_path):
    client, service, db = make_client(tmp_path)
    with client:
        lblj = _lblj(db)
        rid = client.post("/api/routes", json={"name": "R", "steps": [
            {"need": 1, "candidates": [
                {"type": "segment", "segment_id": lblj}]}]}).json()["id"]
        exp = client.get(f"/api/routes/{rid}/export").json()
        assert exp["kind"] == "sm64-route"
        prev = client.post("/api/routes/import?dry_run=true",
                           json={"payload": exp})
        assert prev.status_code == 200 and prev.json()["reused"] == ["LBLJ"]
        created = client.post("/api/routes/import", json={"payload": exp})
        assert created.status_code == 200 and "id" in created.json()
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_api.py -k route -q`
Expected: FAIL — 404 from FastAPI (`/api/routes` not registered).

- [ ] **Step 3: Add the import + Pydantic bodies**

In `src/sm64_events/server/api.py`, extend the views import:

```python
from sm64_events.tracking.views import build_route_view, build_session_view
```

Add near the other bodies (after `SegmentPatch`):

```python
class RouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    steps: list[dict]


class RoutePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    steps: list[dict] | None = None


class ImportBody(BaseModel):
    payload: dict
```

- [ ] **Step 4: Add the endpoints**

In `create_api_router`, after the segment endpoints (after `delete_segment`, before `@router.post("/target")`), add:

```python
    # routes — literal '/routes/import' declared before '/routes/{route_id}'
    # so the path segment is never parsed as an id (declaration order wins —
    # fastapi-patterns; mirrors /segments/vocab).
    @router.get("/routes")
    def routes_list():
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return service.db.routes()

    @router.post("/routes")
    async def create_route(body: RouteBody):
        try:
            rid = await service.create_route(body.model_dump())
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True, "id": rid}

    @router.post("/routes/import")
    async def import_route(body: ImportBody, dry_run: bool = False):
        try:
            return await service.import_route(body.payload, dry_run=dry_run)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/routes/{route_id}")
    def route_view(route_id: int):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        try:
            return build_route_view(service.db, route_id)
        except (LookupError, ValueError) as e:
            raise _http(e)

    @router.get("/routes/{route_id}/export")
    def export_route(route_id: int):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        try:
            return service.export_route(route_id)
        except (LookupError, ValueError) as e:
            raise _http(e)

    @router.put("/routes/{route_id}")
    async def update_route(route_id: int, body: RoutePatch):
        try:
            patch = {k: v for k, v in body.model_dump().items() if v is not None}
            await service.update_route(route_id, patch)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/routes/{route_id}")
    async def delete_route(route_id: int):
        try:
            await service.delete_route(route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

- [ ] **Step 5: Run to verify it passes**

Run: `uv run pytest tests/test_api.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite + commit**

Run: `uv run pytest -q`
Expected: PASS (entire suite green — this is the merge gate).

```bash
git add src/sm64_events/server/api.py tests/test_api.py
git commit -m "feat(api): route CRUD + export/import endpoints"
```

---

## Task 10: Docs — module map

**Files:**
- Modify: `CLAUDE.md` (the "Module map" table)

- [ ] **Step 1: Add module-map rows**

In `CLAUDE.md`'s module-map table, add rows (keep the existing column style):

```
| Route defs (ordered star/segment plans), cumulative success, import/export | `tracking/routes.py` — pure: validate_route, route_stats (best-K product, no-data=0), export_route (embeds segment defs), resolve_import (reuse exact match / create rest). Steps are a uniform `{label?, need:K, candidates:[star|segment]}` shape |
| Route view payload | `tracking/views.py::build_route_view` — resolves candidate names + per-step/cumulative success + broken flag (deleted segment) |
| Route CRUD + import/export commands | `tracking/service.py` — create/update/delete_route (segment-existence check), export_route, import_route (dry-run preview); broadcast-only `routes_changed` |
| Route storage | `storage/db.py` — `routes` table (migration v7) + routes/insert_route/update_route/delete_route |
| Route REST surface | `server/api.py` — `/api/routes` CRUD, `/api/routes/{id}/export`, `/api/routes/import?dry_run=` |
```

- [ ] **Step 2: Note the API for Phase B + the docs-home caveat**

Add a one-line note under the route rows (so Phase B and the GUI worktree both see it):

```
> Route/run API reference belongs in `docs/api.md` once the desktop-gui-packaging branch lands its README→docs/api.md move (spec §11); until then document under the README API section.
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: module-map rows for routes (Phase A)"
```

---

## Self-Review (completed during planning)

**Spec coverage (Phase A scope):**
- §4.1 routes table + step shape → Task 1, Task 2.
- §5.1 routes.py (validate, cumulative, export, import) → Tasks 2–5.
- §5.3 route view → Task 8.
- §5.4 service route commands → Tasks 6–7.
- §5.5 API endpoints + error taxonomy + declaration order → Task 9.
- §5.6 db migration v7 + CRUD → Task 1.
- §7 import/export format (kind/version, embed, reuse-or-create) → Tasks 4, 5, 7.
- §8 validation (need range, candidate shape), deleted-segment broken flag, no cascade → Tasks 2, 8.
- Deferred to later phases (correctly out of Phase A scope): runs table/migration v8, RunTracker, run endpoints, all UI, practice focus, progression graph, `routes_changed` WS handling in the store.

**Type/name consistency:** `validate_route`, `route_stats`, `export_route`, `resolve_import` (routes.py) used identically in service/views/tests; service uses `route_logic.<fn>` (module import, avoids clashing with its own `export_route`/`import_route` methods); step shape `{label?, need, candidates:[{type:"star",course,star} | {type:"segment",segment_id}]}` consistent across db/routes/service/views/api; import intermediate uses `create_index` consistently between `resolve_import` and `_finalize_import_steps`.

**Placeholder scan:** none — every step has full code and exact commands.

---

## Subsequent phases (separate plan files, written next)

Per the spec's §9 build order, each gets its own plan producing working, testable software:
- **Phase B** — Routes-tab builder UI (reorder, add star/segment/group, import/export) + `routes_changed` in the store.
- **Phase C** — Route Practice focus mode (client-side filter/order over the route + session views; current/next; click-to-retry).
- **Phase D** — Run mode: `runs` table (migration **v8**), `tracking/runs.py` `RunTracker` wired into `projection.replay()`, run lifecycle (`run_started` journaled, F1 start + configurable 1.36s offset, forgiving splits, abort/restart), PB/gold, run view, Focus mode, click-to-hide.
- **Phase E** — Run history list (finished/aborted filter) + progression graph (reusing `progress.js`).

**Live gate (Phase D):** confirm F1 in PJ64 1.6 fires `game_reset`.
