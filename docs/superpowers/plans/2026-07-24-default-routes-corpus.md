# Default Routes Corpus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the standard Usamune route corpus — 13 main-category routes, 37 Stage RTA routes, 55 shared castle-movement segments — as generated seed data against spec #1's frozen contracts.

**Architecture:** Compact Python tables in `tools/corpus_*.py` are expanded by `tools/build_defaults_seed.py` into `src/sm64_events/data/defaults.seed.json`, which the existing `tracking/defaults.reconcile_defaults` already loads at startup. No engine changes. Correctness of ~700 blind-authored steps is established by three simulation layers plus a star-count invariant per route, not by inspection.

**Tech Stack:** Python 3.12 via uv, pytest. Stdlib only — no new dependencies.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-07-24-default-routes-corpus-design.md`. **Sources:** `docs/superpowers/specs/2026-07-24-default-routes-corpus-sources.md`. Both are committed; they are the authoritative content and this plan references them by section rather than duplicating ~700 steps.
- **No `src/sm64_events/ui/**` edits.** A concurrent session owns the UI redesign.
- **No engine edits.** `tracking/segments.py`, `tracking/projection.py`, `tracking/runs.py`, `core/events.py` are frozen for this branch.
- `uv run pytest -q` must pass. Worktree baseline: **1373 passed**.
- Star ids are **0-based**; the 100-coin star is star id 6 (courses 1–15). Castle-secret stars are course 0, ids 0–4.
- Movements: `enabled: true`, `category: "Castle Movement"`, `guards: [{"type": "in_active_route"}]`.
- Seed JSON is written with `indent=2`, LF newlines, trailing newline. Never write it with Python text-mode default newlines (CRLF churn on Windows).
- Commit messages explain WHY, imperative mood, following `git log` style.

---

## Wave 1 — Foundation (serial; each lands before the fan-out)

### Task 1: Castle-secret star names (`STAR_NAMES[0]`)

**Files:**
- Modify: `src/sm64_events/memory/addresses.py` (the `STAR_NAMES` dict, ~line 487)
- Test: `tests/test_addresses.py` (existing assertion at line 33 must change)

**Interfaces:**
- Produces: `star_name(0, 0..4)` returns real names; `star_count(0) == 5`. Every later task's route tables depend on course-0 stars being nameable and countable.

- [ ] **Step 1: Update the failing assertion and add the new one**

In `tests/test_addresses.py`, replace the line `assert A.star_count(0) == 0    # Castle Secret: no named stars` inside `test_star_count_owns_the_seven_star_rule` with:

```python
    assert A.star_count(0) == 5    # Castle Secret: 3 Toad + 2 MIPS, no 100-coin
```

Then append a new test to the same file:

```python
def test_castle_secret_star_names_match_the_decomp_flag_order():
    """Ids come from include/save_file.h's SAVE_FLAG_COLLECTED_TOAD_STAR_1..
    _MIPS_STAR_2 under SAVE_FLAG_TO_STAR_FLAG's >>24, cross-checked against
    behaviors/mips.inc.c spawning STAR_INDEX_ACT_4 + oBhvParams2ndByte."""
    assert A.star_name(0, 0) == "Toad Star (Basement)"
    assert A.star_name(0, 1) == "Toad Star (Upstairs)"
    assert A.star_name(0, 2) == "Toad Star (Tippy)"
    assert A.star_name(0, 3) == "MIPS 1st Star"
    assert A.star_name(0, 4) == "MIPS 2nd Star"
    # course 0 has no 100-coin star: the 7-star rule is main-courses-only
    assert A.star_name(0, 6) == "Star 7"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_addresses.py -q`
Expected: FAIL — `assert 0 == 5` and `assert 'Star 1' == 'Toad Star (Basement)'`.

- [ ] **Step 3: Add the course-0 row to `STAR_NAMES`**

In `src/sm64_events/memory/addresses.py`, insert as the FIRST entry of the `STAR_NAMES` dict (immediately after `STAR_NAMES = {`):

```python
    # Castle secret stars (COURSE_NONE / gCurrCourseNum 0) — the Toad and MIPS
    # stars, which belong to no course. Ids are the save-file star-flag bit
    # order from the decomp: include/save_file.h defines
    # SAVE_FLAG_COLLECTED_TOAD_STAR_1..3 as (1 << 24..26) and _MIPS_STAR_1/2 as
    # (1 << 27..28), and SAVE_FLAG_TO_STAR_FLAG(x) = (x >> 24) & 0x7F, so the
    # star indices are Toad 0/1/2 and MIPS 3/4. Cross-checked independently:
    # behaviors/mips.inc.c spawns STAR_INDEX_ACT_4 + oBhvParams2ndByte, i.e.
    # 3 + {0, 1} — the same two ids. (Both files fetched from n64decomp/sm64
    # master 2026-07-23.)
    # VERIFY (live gate): WHICH Toad carries which index. The binding below
    # follows the flag order together with the 12/25/35-star spawn thresholds
    # (basement Toad spawns first). A live grab of any Toad star settles it —
    # the journal had zero course-0 grabs when this shipped.
    0: ("Toad Star (Basement)", "Toad Star (Upstairs)", "Toad Star (Tippy)",
        "MIPS 1st Star", "MIPS 2nd Star"),
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_addresses.py tests/test_segments.py -q`
Expected: PASS. (`test_segments.py` is included because `vocab()["stars"]["0"]` changes from `[]` to five names.)

- [ ] **Step 5: Full suite, then commit**

Run: `uv run pytest -q` → expected 1374 passed.

```bash
git add src/sm64_events/memory/addresses.py tests/test_addresses.py
git commit -m "feat(addresses): name the course-0 castle-secret stars

The Toad and MIPS stars belong to no course, so star_grab.py reports them
as course 0 — but STAR_NAMES had no course-0 row, which meant star_count(0)
was 0, no picker offered them, and star_name rendered 'Star 4'. The route
corpus needs them as ordinary star candidates.

Ids are decomp-derived twice over (save_file.h's flag order and mips.inc.c's
STAR_INDEX_ACT_4 + bp2 agree on MIPS = 3, 4); which Toad holds which index is
marked VERIFY for the live gate."
```

---

### Task 2: Harden `reconcile_defaults` + rename `resolve_steps`

**Files:**
- Modify: `src/sm64_events/tracking/defaults.py` (whole file)
- Modify: `src/sm64_events/main.py:114-123` (log the returned problems)
- Test: `tests/test_seed_reconcile.py` (append)

**Interfaces:**
- Consumes: `validate_definition` (`tracking/segments.py`), `validate_route` (`tracking/routes.py`).
- Produces: `reconcile_defaults(db, seed) -> list[str]` — human-readable problems for SKIPPED rows; good rows still land. `resolve_steps(steps, key_to_id) -> list` (renamed from `_resolve_steps`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_seed_reconcile.py`:

```python
def test_reconcile_skips_a_malformed_row_and_keeps_the_good_ones(tmp_path):
    """One bad seed row must not cost the whole corpus refresh (spec #2 §10)."""
    db = Database(tmp_path / "t.db")
    seed = json.loads(json.dumps(SEED_V1))
    seed["segments"].insert(0, {"seed_key": "seg:bad", "name": "Bad",
                                "start_triggers": [{"type": "nope"}],
                                "end_triggers": [], "waypoints": [],
                                "guards": [], "category": "Tricks"})
    problems = reconcile_defaults(db, seed)
    assert len(problems) == 1 and "seg:bad" in problems[0]
    assert not any(s["seed_key"] == "seg:bad" for s in db.segment_defs())
    assert any(s["seed_key"] == "seg:demo" for s in db.segment_defs())


def test_reconcile_skips_a_row_with_no_seed_key(tmp_path):
    db = Database(tmp_path / "t.db")
    seed = json.loads(json.dumps(SEED_V1))
    seed["routes"].append({"name": "Keyless", "steps": []})
    problems = reconcile_defaults(db, seed)
    assert len(problems) == 1 and "seed_key" in problems[0]
    assert len(db.routes()) == 1


def test_reconcile_skips_a_structurally_wrong_row_shape(tmp_path):
    """A JSON-valid but wrong-shaped seed used to raise KeyError/TypeError out
    of reconcile; it must now be a skipped row, not an aborted refresh."""
    db = Database(tmp_path / "t.db")
    seed = json.loads(json.dumps(SEED_V1))
    seed["segments"].insert(0, "not a dict")
    problems = reconcile_defaults(db, seed)
    assert len(problems) == 1
    assert any(s["seed_key"] == "seg:demo" for s in db.segment_defs())


def test_reconcile_returns_no_problems_for_the_real_bundled_seed():
    """The shipped corpus must be clean by its own validator."""
    import sqlite3, tempfile, pathlib
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(pathlib.Path(tmp) / "t.db")
        seed = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
        assert reconcile_defaults(db, seed) == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_seed_reconcile.py -q`
Expected: FAIL — `reconcile_defaults` returns `None` (`TypeError: object of type 'NoneType' has no len()`) and raises on the malformed rows.

- [ ] **Step 3: Rewrite `tracking/defaults.py`**

Replace the whole file with:

```python
"""Editable-defaults reconcile (spec 2026-07-23-default-routes-foundation).

Mirrors ranks/standards._reconcile: a bundled seed refreshes rows the user
never touched (seed_dirty=0), leaves edited (seed_dirty=1) and user-created
(seed_key IS NULL) rows alone, and inserts anything missing. Segments come
first so route candidates can resolve seed_key -> local segment_id.

Every row is validated and applied INDIVIDUALLY (hardening 2026-07-24, spec #2
§10): the corpus is now ~90 rows, so one malformed row must not cost the whole
refresh. A bad row is skipped and described in the returned problem list; good
rows still land. Callers log the problems — reconcile itself never raises on
seed content."""
import json
from datetime import datetime, timezone

from sm64_events.tracking.routes import validate_route
from sm64_events.tracking.segments import validate_definition

_SEED_ERRORS = (ValueError, TypeError, KeyError, AttributeError, IndexError)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _seed_key(row, kind: str) -> str:
    if not isinstance(row, dict):
        raise ValueError(f"{kind} row is not an object")
    key = row.get("seed_key")
    if not isinstance(key, str) or not key.strip():
        raise ValueError(f"{kind} row is missing its seed_key")
    return key


def reconcile_defaults(db, seed: dict) -> list[str]:
    """Apply the bundled seed to the db. Returns a list of human-readable
    problems for rows that were SKIPPED; an empty list means a clean seed."""
    problems: list[str] = []
    if not isinstance(seed, dict):
        return ["seed is not an object"]
    seg_by_key = {s["seed_key"]: s for s in db.segment_defs()
                  if s.get("seed_key")}
    key_to_id: dict[str, int] = {}
    for srow in seed.get("segments") or []:
        try:
            key = _seed_key(srow, "segment")
            validate_definition(srow)
        except _SEED_ERRORS as exc:
            problems.append(f"segment {_describe(srow)}: {exc}")
            continue
        try:
            existing = seg_by_key.get(key)
            if existing is None:
                key_to_id[key] = db.insert_segment_def(
                    srow["name"], srow["start_triggers"], srow["end_triggers"],
                    srow.get("guards", []), _now_iso(),
                    enabled=srow.get("enabled", True),
                    waypoints=srow.get("waypoints", []),
                    category=srow.get("category"), seed_key=key)
            else:
                key_to_id[key] = existing["id"]
                if not existing["seed_dirty"]:
                    db.update_segment_def(
                        existing["id"], name=srow["name"],
                        enabled=srow.get("enabled", True),
                        start_triggers=srow["start_triggers"],
                        end_triggers=srow["end_triggers"],
                        waypoints=srow.get("waypoints", []),
                        guards=srow.get("guards", []),
                        category=srow.get("category"))
        except _SEED_ERRORS as exc:
            problems.append(f"segment {key}: {exc}")
    route_by_key = {r["seed_key"]: r for r in db.routes() if r.get("seed_key")}
    for rrow in seed.get("routes") or []:
        try:
            key = _seed_key(rrow, "route")
            steps = resolve_steps(rrow["steps"], key_to_id)
            start_condition = rrow.get("start_condition") or {"type": "reset_game"}
            validate_route({"name": rrow.get("name"), "steps": steps,
                            "start_condition": start_condition})
        except _SEED_ERRORS as exc:
            problems.append(f"route {_describe(rrow)}: {exc}")
            continue
        try:
            existing = route_by_key.get(key)
            if existing is None:
                db.insert_route(rrow["name"], steps, _now_iso(),
                                start_condition=start_condition,
                                category=rrow.get("category"), seed_key=key)
            elif not existing["seed_dirty"]:
                db.update_route(existing["id"], updated_utc=_now_iso(),
                                name=rrow["name"], steps=steps,
                                start_condition=start_condition,
                                category=rrow.get("category"))
        except _SEED_ERRORS as exc:
            problems.append(f"route {key}: {exc}")
    return problems


def _describe(row) -> str:
    """Identify a bad row in a problem message without dumping the whole thing."""
    if isinstance(row, dict):
        return str(row.get("seed_key") or row.get("name") or "<unnamed>")
    return f"<{type(row).__name__}>"


def resolve_steps(steps: list, key_to_id: dict) -> list:
    """Rewrite seed route candidates ({type:segment, seed_key}) to persisted
    ({type:segment, segment_id}). An unresolved key -> segment_id -1 (renders
    as a broken step, never a crash). Public: the corpus generator's tests
    resolve the same way to check route/segment agreement."""
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

- [ ] **Step 4: Log the problems at startup**

In `src/sm64_events/main.py`, replace the body of the reconcile `try` block (the `if seed_path is not None:` clause) with:

```python
            if seed_path is not None:
                seed = json.loads(seed_path.read_text(encoding="utf-8"))
                for problem in reconcile_defaults(db, seed):
                    logging.getLogger("sm64.tracker").warning(
                        "defaults seed row skipped: %s", problem)
```

Leave the surrounding `except (OSError, ValueError, KeyError, TypeError)` in place — it still guards a missing or non-JSON file.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_seed_reconcile.py tests/test_main.py -q`
Expected: PASS.

- [ ] **Step 6: Full suite, then commit**

Run: `uv run pytest -q` → expected 1378 passed.

```bash
git add src/sm64_events/tracking/defaults.py src/sm64_events/main.py tests/test_seed_reconcile.py
git commit -m "fix(defaults): validate and apply seed rows individually

Reconcile applied the seed row-by-row but validated nothing, so a malformed
row raised out of the loop and every row after it was lost. main.py caught
the exception, so the symptom was a silently stale corpus, not a crash. With
the corpus going from 10 rows to ~90 that trade is wrong: skip the bad row,
describe it, and let the rest land.

_resolve_steps becomes public resolve_steps — the corpus tests resolve seed
keys the same way to prove routes and segments agree."
```

---

### Task 3: Generator skeleton — reproduce today's seed exactly

**Files:**
- Create: `tools/corpus_vocab.py`, `tools/corpus_legacy.py`, `tools/build_defaults_seed.py`
- Create (empty tables): `tools/corpus_movements.py`, `tools/corpus_routes_main.py`, `tools/corpus_routes_stage.py`
- Modify: `src/sm64_events/data/defaults.seed.json` (regenerated — content unchanged)
- Test: `tests/test_build_defaults_seed.py` (new)

**Interfaces:**
- Produces, from `tools/corpus_vocab.py`: `exit_level(level, to=None)`, `enter_level(level, frm=None)`, `enter_area(area, frm=None)`, `grab_star(course, star)`, `star(course, star_id, label)`, `stars(course, star_ids, label, need=None)`, `segment(seed_key, label)`, `route(seed_key, name, category, steps, start_condition=None)`, and the constants `MAIN`, `STAGE_RTA`, `CASTLE_MOVEMENT`, `LOBBY`, `UPSTAIRS`, `BASEMENT`.
- Produces, from `tools/build_defaults_seed.py`: `build() -> dict`, `render(seed) -> str`, `OUT` (the seed path), `SEED_VERSION`.
- Produces: `tools/corpus_movements.MOVEMENTS` (list of dicts with keys `seed_key`, `name`, `start`, `via`, `end`), `tools/corpus_routes_main.ROUTES`, `tools/corpus_routes_stage.ROUTES` — all three empty lists at the end of this task.

- [ ] **Step 1: Write the failing test**

Create `tests/test_build_defaults_seed.py`:

```python
"""The generator is how the corpus seed is AUTHORED; the JSON is the artifact
the app reads. These tests pin that the two never disagree (drift guard) and
that the generator reproduces the ten pre-existing seeded defs byte-identically
in meaning — a live install's LBLJ/Bowser rows must not be rewritten."""
import importlib.util
import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)


def test_generated_seed_matches_the_checked_in_file():
    """Drift guard: `python tools/build_defaults_seed.py --check` must be clean.
    If this fails, someone edited defaults.seed.json by hand — regenerate."""
    on_disk = build_seed.OUT.read_bytes().decode("utf-8")
    assert build_seed.render(build_seed.build()) == on_disk


def test_generated_seed_is_lf_only():
    """CRLF in the seed would churn the whole file in git on every rewrite."""
    assert b"\r\n" not in build_seed.OUT.read_bytes()


def test_legacy_segments_are_carried_forward_verbatim():
    """The ten pre-existing seeded defs keep their exact triggers: reconcile
    overwrites untouched seeded rows, so a drifted trigger here would silently
    rewrite a live user's segments on the next startup."""
    seed = build_seed.build()
    by_key = {s["seed_key"]: s for s in seed["segments"]}
    lblj = by_key["seg:lblj"]
    assert lblj["start_triggers"] == [{"type": "level_enter", "to": 6, "from": 16},
                                      {"type": "attempt_anchor", "level": 6, "area": 1}]
    assert lblj["end_triggers"] == [{"type": "level_enter", "to": 17}]
    assert lblj["guards"] == [] and lblj["waypoints"] == []
    assert lblj["category"] == "Tricks"
    for key in ("seg:mips-clip", "seg:lakitu-skip", "seg:bits-entry",
                "seg:bitdw-pipe", "seg:bitfs-pipe", "seg:bits-pipe",
                "seg:bowser-1", "seg:bowser-2", "seg:bowser-3"):
        assert key in by_key, key
        assert by_key[key]["guards"] == [], f"{key} must stay unguarded"
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_build_defaults_seed.py -q`
Expected: FAIL — `tools/build_defaults_seed.py` does not exist.

- [ ] **Step 3: Create `tools/corpus_vocab.py`**

```python
"""Shared clause + step constructors for the seeded corpus tables.

ONE place that knows the seed JSON shapes, so 55 movement segments and ~50
routes cannot disagree about them. Ids are the addresses.py id spaces: LEVEL
ids for triggers, COURSE ids + 0-based star ids for star candidates.

Consumed by tools/corpus_{legacy,movements,routes_main,routes_stage}.py and
expanded by tools/build_defaults_seed.py."""

CASTLE = 6                          # LEVEL_CASTLE_INSIDE
LOBBY, UPSTAIRS, BASEMENT = 1, 2, 3  # CASTLE_AREA_NAMES ids

MAIN = "Main Categories"
STAGE_RTA = "Stage RTA"
CASTLE_MOVEMENT = "Castle Movement"
TRICKS = "Tricks"
BOWSER_FIGHTS = "Bowser Fights"

ROUTE_SCOPED = [{"type": "in_active_route"}]


# --- trigger clauses -------------------------------------------------------

def exit_level(level, to=None):
    clause = {"type": "level_exit", "from": level}
    if to is not None:
        clause["to"] = to
    return clause


def enter_level(level, frm=None):
    clause = {"type": "level_enter", "to": level}
    if frm is not None:
        clause["from"] = frm
    return clause


def enter_area(area, frm=None):
    clause = {"type": "area_enter", "level": CASTLE, "area": area}
    if frm is not None:
        clause["from"] = frm
    return clause


def grab_star(course, star_id):
    return {"type": "star_grabbed", "course": course, "star": star_id}


def enter_warp(level):
    return {"type": "warp_entered", "level": level}


def grab_key(level):
    return {"type": "key_grabbed", "level": level}


def anchor(level, area=None):
    clause = {"type": "attempt_anchor", "level": level}
    if area is not None:
        clause["area"] = area
    return clause


def spawn(level):
    return {"type": "spawned", "level": level}


# --- route steps -----------------------------------------------------------

def star(course, star_id, label):
    """One star, one step."""
    return {"label": label, "need": 1,
            "candidates": [{"type": "star", "course": course, "star": star_id}]}


def stars(course, star_ids, label, need=None):
    """A group. need defaults to ALL of them — that is the '+ 100 Coins' shape
    (both stars come from the same visit, in whichever order the coin count
    crosses). Pass need=1 for a documented either/or."""
    candidates = [{"type": "star", "course": course, "star": s} for s in star_ids]
    return {"label": label,
            "need": len(candidates) if need is None else need,
            "candidates": candidates}


def segment(seed_key, label):
    """A movement or trick step. resolve_steps rewrites seed_key -> segment_id
    at reconcile, so the seed never needs to know local autoincrement ids."""
    return {"label": label, "need": 1,
            "candidates": [{"type": "segment", "seed_key": seed_key}]}


def route(seed_key, name, category, steps, start_condition=None):
    return {"seed_key": seed_key, "name": name, "category": category,
            "start_condition": start_condition or {"type": "reset_game"},
            "steps": steps}


def movement(seed_key, name, start, end, via=()):
    """A castle-movement segment. `via` is a FLAT list of clauses; each becomes
    a single-clause waypoint (no corpus movement needs an any-of waypoint)."""
    return {"seed_key": seed_key, "name": name, "start": start,
            "via": list(via), "end": end}
```

- [ ] **Step 4: Create `tools/corpus_legacy.py`**

The ten pre-existing seeded defs, transcribed verbatim from the current `data/defaults.seed.json`:

```python
"""The ten pre-existing seeded segments, carried forward verbatim.

These predate the corpus (db.py MIGRATIONS v4, with the v5 LBLJ and v6 Bowser 3
repairs folded in) and are already installed on every live db. Reconcile
overwrites untouched seeded rows, so ANY drift here silently rewrites a real
user's segments at startup — tests/test_build_defaults_seed.py pins them and
tests/test_seed_reconcile.py proves reconcile leaves them untouched.

They stay UNGUARDED (no in_active_route): their current always-arm behaviour is
what the stage banner's Bowser-course mutual exclusion and the standalone
practice flows depend on."""
from corpus_vocab import (BOWSER_FIGHTS, CASTLE_MOVEMENT, TRICKS, UPSTAIRS,
                          anchor, enter_area, enter_level, enter_warp, exit_level,
                          grab_key, spawn)


def _seg(seed_key, name, start, end, category):
    return {"seed_key": seed_key, "name": name, "enabled": True,
            "start_triggers": start, "end_triggers": end,
            "waypoints": [], "guards": [], "category": category}


SEGMENTS = [
    _seg("seg:lblj", "LBLJ",
         [enter_level(6, frm=16), anchor(6, area=1)],
         [enter_level(17)], TRICKS),
    _seg("seg:mips-clip", "MIPS Clip",
         [exit_level(7, to=6)], [enter_level(23)], TRICKS),
    _seg("seg:lakitu-skip", "Lakitu Skip",
         [spawn(16)], [enter_level(6)], TRICKS),
    _seg("seg:bits-entry", "BitS Entry",
         [enter_area(UPSTAIRS)], [enter_level(21)], CASTLE_MOVEMENT),
    _seg("seg:bitdw-pipe", "BitDW Pipe Entry",
         [enter_level(17), anchor(17)], [enter_warp(17)], CASTLE_MOVEMENT),
    _seg("seg:bitfs-pipe", "BitFS Pipe Entry",
         [enter_level(19), anchor(19)], [enter_warp(19)], CASTLE_MOVEMENT),
    _seg("seg:bits-pipe", "BitS Pipe Entry",
         [enter_level(21), anchor(21)], [enter_warp(21)], CASTLE_MOVEMENT),
    _seg("seg:bowser-1", "Bowser 1",
         [enter_level(30), anchor(30)], [grab_key(30)], BOWSER_FIGHTS),
    _seg("seg:bowser-2", "Bowser 2",
         [enter_level(33), anchor(33)], [grab_key(33)], BOWSER_FIGHTS),
    _seg("seg:bowser-3", "Bowser 3",
         [enter_level(34), anchor(34)], [grab_key(34)], BOWSER_FIGHTS),
]
```

**Verify against the current file before moving on:** open `src/sm64_events/data/defaults.seed.json` and confirm each `start_triggers` / `end_triggers` list matches key-for-key. `seg:bits-entry` uses `area_enter level=6 area=2`; `seg:mips-clip` carries `to: 6` on its `level_exit`.

- [ ] **Step 5: Create the three empty corpus tables**

`tools/corpus_movements.py`:

```python
"""The shared castle-movement segments (spec 2026-07-24 §4.4).

Shapes are FORCED by the frozen matcher, not chosen. Read spec §4.1/§4.2
before editing ANY row:
  * a plain (via=[]) def is disarmed by any area_changed away from its arm
    position, and by any level_changed matching neither start nor end;
  * a waypoint-bearing def is SILENTLY CANCELLED by any star grab;
  * a movement may START on a star_grabbed clause but must NEVER end on one
    (run-ordering trap — spec §5.2).
"""
from corpus_vocab import (BASEMENT, UPSTAIRS, enter_area, enter_level,
                          exit_level, grab_star, movement)

MOVEMENTS = []
```

`tools/corpus_routes_main.py`:

```python
"""The 13 main-category routes (spec 2026-07-24 §7.1).

Steps MUST be in completion-event order — RunTracker only ever considers
steps[current], so a misordered step stalls a run permanently (spec §5.1).
Content is transcribed from
docs/superpowers/specs/2026-07-24-default-routes-corpus-sources.md."""
from corpus_vocab import MAIN, route, segment, star, stars

ROUTES = []
```

`tools/corpus_routes_stage.py`:

```python
"""The 37 Stage RTA routes (spec 2026-07-24 §7.2) — per-course ordered star
lists, no movement steps. start_condition is entering the course, so the run
clock starts on the painting rather than on F1."""
from corpus_vocab import STAGE_RTA, route, stars

ROUTES = []
```

- [ ] **Step 6: Create `tools/build_defaults_seed.py`**

```python
"""Generate src/sm64_events/data/defaults.seed.json from the corpus tables.

The JSON is the artifact the app reads (tracking/defaults.reconcile_defaults
loads it at startup); this tool is how it is AUTHORED. Compact Python tables in
corpus_*.py expand here into the verbose seed shape, so 55 movement segments
cannot drift from one another and a route step stays one readable line.

Mirrors tools/scrape_ranks.py -> data/rank_standards.seed.json: generated
input, checked-in artifact, reconcile at startup.

    uv run python tools/build_defaults_seed.py            # write
    uv run python tools/build_defaults_seed.py --check    # diff only, exit 1

tests/test_build_defaults_seed.py runs --check's comparison, so the checked-in
JSON can never silently drift from these tables."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import corpus_legacy        # noqa: E402
import corpus_movements     # noqa: E402
import corpus_routes_main   # noqa: E402
import corpus_routes_stage  # noqa: E402
from corpus_vocab import CASTLE_MOVEMENT, ROUTE_SCOPED  # noqa: E402

SEED_VERSION = 2
OUT = (Path(__file__).resolve().parent.parent
       / "src" / "sm64_events" / "data" / "defaults.seed.json")


def _movement_row(row: dict) -> dict:
    """Expand a compact movement into a seed segment. Every movement is
    route-scoped and Castle Movement by construction — that uniformity is the
    whole reason this table is generated rather than hand-written."""
    return {"seed_key": row["seed_key"], "name": row["name"], "enabled": True,
            "start_triggers": [row["start"]],
            "end_triggers": [row["end"]],
            "waypoints": [[clause] for clause in row["via"]],
            "guards": ROUTE_SCOPED, "category": CASTLE_MOVEMENT}


def build() -> dict:
    """The whole seed, segments before routes (reconcile resolves route
    candidates' seed_key -> local segment_id in that order)."""
    segments = list(corpus_legacy.SEGMENTS)
    segments += [_movement_row(row) for row in corpus_movements.MOVEMENTS]
    routes = list(corpus_routes_main.ROUTES) + list(corpus_routes_stage.ROUTES)
    return {"seed_version": SEED_VERSION, "segments": segments, "routes": routes}


def render(seed: dict) -> str:
    return json.dumps(seed, indent=2) + "\n"


def main(argv) -> int:
    seed = build()
    text = render(seed)
    if "--check" in argv:
        on_disk = OUT.read_bytes().decode("utf-8") if OUT.exists() else ""
        if on_disk != text:
            print(f"OUT OF DATE: {OUT}\n"
                  "  run: uv run python tools/build_defaults_seed.py")
            return 1
        print(f"up to date: {OUT}")
        return 0
    OUT.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {OUT}: {len(seed['segments'])} segments, "
          f"{len(seed['routes'])} routes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
```

- [ ] **Step 7: Regenerate the seed and run the tests**

Run: `uv run python tools/build_defaults_seed.py`
Expected: `wrote ...defaults.seed.json: 10 segments, 0 routes`

Run: `uv run pytest tests/test_build_defaults_seed.py tests/test_seed_reconcile.py -q`
Expected: PASS — in particular `test_real_bundled_seed_does_not_alter_existing_segment_defs`, which proves the regenerated file still reconciles the ten migrated rows to identical triggers.

Check the diff is content-neutral: `git diff --stat src/sm64_events/data/defaults.seed.json` should show only `seed_version` 1→2 and whitespace/key-order normalisation. If `git diff` shows the whole file changed, check for CRLF (`git ls-files --eol src/sm64_events/data/defaults.seed.json` must report `w/lf`).

- [ ] **Step 8: Full suite, then commit**

Run: `uv run pytest -q` → expected 1381 passed.

```bash
git add tools/corpus_vocab.py tools/corpus_legacy.py tools/corpus_movements.py \
        tools/corpus_routes_main.py tools/corpus_routes_stage.py \
        tools/build_defaults_seed.py tests/test_build_defaults_seed.py \
        src/sm64_events/data/defaults.seed.json
git commit -m "feat(tools): generate defaults.seed.json from corpus tables

The corpus is about to grow from 10 rows to ~90 segments and ~50 routes with
~700 route steps. Hand-writing that as literal JSON means every movement
repeats its trigger shape by hand and nothing stops two of them drifting.

This lands the generator with EMPTY corpus tables first, so the interesting
assertion is provable before any content exists: regenerating reproduces the
ten pre-existing seeded defs with identical triggers, and reconcile still
leaves a live install's LBLJ/Bowser rows untouched."
```

---

## Wave 2 — Corpus tables (parallel; one file each, no shared edits)

Tasks 4, 5 and 6 touch disjoint files and can run concurrently. **None of them regenerates `defaults.seed.json`** — Task 7 does that once, so the three cannot conflict on the artifact.

### Task 4: The 55 castle-movement segments

**Files:**
- Modify: `tools/corpus_movements.py` (the `MOVEMENTS` list only)
- Test: `tests/test_corpus_movements.py` (new)

**Interfaces:**
- Consumes: `corpus_vocab.movement/exit_level/enter_level/enter_area/grab_star`, `BASEMENT`, `UPSTAIRS`.
- Produces: `MOVEMENTS` — 55 dicts, each `{seed_key, name, start, via, end}`. Tasks 5 and 7 reference these `seed_key`s; they are frozen by spec §4.4 and must be transcribed exactly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus_movements.py`:

```python
"""Every movement row must expand into a definition the ENGINE accepts, and
must obey the two grammar invariants that the matcher forces on us
(spec 2026-07-24 §4.1/§4.2). These are cheap structural gates; the behavioural
proof is tests/test_defaults_corpus.py's simulation layer."""
import importlib.util
import sys
from pathlib import Path

from sm64_events.tracking.segments import validate_definition

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)

MOVEMENTS = build_seed.corpus_movements.MOVEMENTS


def test_movement_count():
    assert len(MOVEMENTS) == 55


def test_seed_keys_are_unique_and_prefixed():
    keys = [m["seed_key"] for m in MOVEMENTS]
    assert len(set(keys)) == len(keys)
    assert all(k.startswith("seg:") for k in keys)


def test_every_movement_expands_to_a_valid_definition():
    for row in MOVEMENTS:
        validate_definition(build_seed._movement_row(row))


def test_every_movement_is_route_scoped_and_categorised():
    for row in MOVEMENTS:
        expanded = build_seed._movement_row(row)
        assert expanded["guards"] == [{"type": "in_active_route"}], row["seed_key"]
        assert expanded["category"] == "Castle Movement", row["seed_key"]
        assert expanded["enabled"] is True, row["seed_key"]


def test_no_movement_ends_on_a_star_grab():
    """Spec §5.2: within one event `closed` is ordered stars-then-segments, so
    a movement ending on a star grab consumes the star attempt's turn and the
    star's own step can never complete — a permanent run stall."""
    for row in MOVEMENTS:
        assert row["end"]["type"] != "star_grabbed", row["seed_key"]


def test_a_waypoint_movement_never_repeats_its_start_clause_out_of_order():
    """Spec §4.2 authoring caveat: a re-entry's second waypoint IS its start
    clause, which is only safe because _feed_waypoint advances progress before
    the major-action check. If a start clause ever matched waypoint[0], the
    sequence could never begin."""
    for row in MOVEMENTS:
        if row["via"]:
            assert row["via"][0] != row["start"], row["seed_key"]


def test_star_started_movements_use_castle_secret_stars_only():
    """Only Toad/MIPS grabs (course 0) start a movement — a course star grab
    means you are still inside a stage."""
    for row in MOVEMENTS:
        if row["start"]["type"] == "star_grabbed":
            assert row["start"]["course"] == 0, row["seed_key"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_corpus_movements.py -q`
Expected: FAIL — `assert 0 == 55`.

- [ ] **Step 3: Fill in the `MOVEMENTS` table**

Replace `MOVEMENTS = []` in `tools/corpus_movements.py` with the table below. It is spec §4.4 transcribed one-for-one; level ids are `addresses.LEVEL_NAMES` keys.

```python
MOVEMENTS = [
    # --- lobby ------------------------------------------------------------
    movement("seg:castle-entry->bob", "Castle Entrance → BoB",
             enter_level(6, frm=16), enter_level(9)),
    movement("seg:bob->wf", "BoB → WF", exit_level(9), enter_level(24)),
    movement("seg:bob->pss", "BoB → PSS", exit_level(9), enter_level(27)),
    movement("seg:bob->ccm", "BoB → CCM", exit_level(9), enter_level(5)),
    movement("seg:bob->basement", "BoB → Basement",
             exit_level(9), enter_area(BASEMENT)),
    movement("seg:pss->wf", "PSS → WF", exit_level(27), enter_level(24)),
    movement("seg:wf->pss", "WF → PSS", exit_level(24), enter_level(27)),
    movement("seg:wf->ccm", "WF → CCM", exit_level(24), enter_level(5)),
    movement("seg:wf->sa", "WF → Secret Aquarium",
             exit_level(24), enter_level(20)),
    movement("seg:wf->bitdw", "WF → BitDW", exit_level(24), enter_level(17)),
    movement("seg:wf->ssl", "WF → SSL", exit_level(24), enter_level(8),
             via=[enter_area(BASEMENT)]),
    movement("seg:sa->jrb", "Secret Aquarium → JRB",
             exit_level(20), enter_level(12)),
    movement("seg:jrb->pss", "JRB → PSS", exit_level(12), enter_level(27)),
    movement("seg:pss->totwc", "PSS → TotWC", exit_level(27), enter_level(29)),
    movement("seg:totwc->pss", "TotWC → PSS", exit_level(29), enter_level(27)),
    movement("seg:totwc->bitdw", "TotWC → BitDW",
             exit_level(29), enter_level(17)),
    movement("seg:pss->bitdw", "PSS → BitDW", exit_level(27), enter_level(17)),
    movement("seg:pss->bob", "PSS → BoB", exit_level(27), enter_level(9)),
    movement("seg:ccm->bitdw", "CCM → BitDW", exit_level(5), enter_level(17)),
    movement("seg:ccm->bbh", "CCM → BBH", exit_level(5), enter_level(4),
             via=[enter_level(26)]),
    # --- out of the Bowser 1 arena ---------------------------------------
    movement("seg:bowser1->bob", "Bowser 1 → BoB",
             exit_level(30), enter_level(9)),
    movement("seg:bowser1->wf", "Bowser 1 → WF",
             exit_level(30), enter_level(24)),
    movement("seg:bowser1->ccm", "Bowser 1 → CCM",
             exit_level(30), enter_level(5)),
    movement("seg:bowser1->ssl", "Bowser 1 → SSL",
             exit_level(30), enter_level(8), via=[enter_area(BASEMENT)]),
    movement("seg:bowser1->ddd", "Bowser 1 → DDD (Crackslide)",
             exit_level(30), enter_level(23), via=[enter_area(BASEMENT)]),
    movement("seg:bowser1->bitfs", "Bowser 1 → BitFS (SBLJ / DDD Skip)",
             exit_level(30), enter_level(19), via=[enter_area(BASEMENT)]),
    # --- courtyard --------------------------------------------------------
    movement("seg:bbh->basement", "BBH → Basement",
             exit_level(4), enter_area(BASEMENT), via=[enter_level(6)]),
    movement("seg:bbh->ddd", "BBH → DDD",
             exit_level(4), enter_level(23), via=[enter_level(6)]),
    # --- basement ---------------------------------------------------------
    movement("seg:mips1->ssl", "MIPS (1st) → SSL",
             grab_star(0, 3), enter_level(8)),
    movement("seg:ssl->lll", "SSL → LLL", exit_level(8), enter_level(22)),
    movement("seg:ssl->hmc", "SSL → HMC", exit_level(8), enter_level(7)),
    movement("seg:lll->hmc", "LLL → HMC", exit_level(22), enter_level(7)),
    movement("seg:lll->ddd", "LLL → DDD", exit_level(22), enter_level(23)),
    movement("seg:hmc->lll", "HMC → LLL", exit_level(7), enter_level(22)),
    movement("seg:hmc->ddd", "HMC → DDD", exit_level(7), enter_level(23)),
    movement("seg:hmc->rr", "HMC → RR (re-entry, pause exit)",
             exit_level(7), enter_level(15),
             via=[enter_level(7), exit_level(7)]),
    movement("seg:mips2->hmc", "MIPS (2nd) → HMC",
             grab_star(0, 4), enter_level(7)),
    movement("seg:mips2->vcutm", "MIPS (2nd) → VCUtM",
             grab_star(0, 4), enter_level(18), via=[enter_level(16)]),
    movement("seg:vcutm->ccm", "VCUtM → CCM",
             exit_level(18), enter_level(5), via=[enter_level(6)]),
    movement("seg:ddd->bitfs", "DDD → BitFS (sub)",
             exit_level(23), enter_level(19)),
    movement("seg:ddd->wdw", "DDD → WDW (BitFS re-entry, pause exit)",
             exit_level(23), enter_level(11),
             via=[enter_level(19), exit_level(19)]),
    # --- out of the Bowser 2 arena ---------------------------------------
    movement("seg:bowser2->ddd", "Bowser 2 → DDD",
             exit_level(33), enter_level(23)),
    movement("seg:bowser2->wdw", "Bowser 2 → WDW",
             exit_level(33), enter_level(11), via=[enter_area(UPSTAIRS)]),
    movement("seg:bowser2->upstairs", "Bowser 2 → Upstairs",
             exit_level(33), enter_area(UPSTAIRS)),
    # --- upstairs ---------------------------------------------------------
    movement("seg:wdw->thi", "WDW → THI", exit_level(11), enter_level(13)),
    movement("seg:thi->ttm", "THI → TTM", exit_level(13), enter_level(36)),
    movement("seg:ttm->sl", "TTM → SL", exit_level(36), enter_level(10)),
    movement("seg:sl->basement", "SL → Basement (re-entry, pause exit)",
             exit_level(10), enter_area(BASEMENT),
             via=[enter_level(10), exit_level(10)]),
    movement("seg:sl->rr", "SL → RR", exit_level(10), enter_level(15)),
    movement("seg:sl->wmotr", "SL → WMotR", exit_level(10), enter_level(31)),
    movement("seg:wmotr->ttc", "WMotR → TTC", exit_level(31), enter_level(14)),
    movement("seg:rr->ttc", "RR → TTC", exit_level(15), enter_level(14)),
    movement("seg:ttc->rr", "TTC → RR", exit_level(14), enter_level(15)),
    movement("seg:rr->bits", "RR → BitS", exit_level(15), enter_level(21)),
    movement("seg:ttc->bits", "TTC → BitS", exit_level(14), enter_level(21)),
]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_corpus_movements.py -q`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/corpus_movements.py tests/test_corpus_movements.py
git commit -m "feat(corpus): the 55 shared castle-movement segments

One segment per stage pair, so every route referencing 'BoB to WF' pools its
attempts, PBs and ranks (spec #1 decision 3). Each row's shape is dictated by
the matcher, not chosen: a plain def dies on the area change, so any movement
crossing castle regions carries a waypoint; a waypoint def dies on a star
grab, so a movement spanning a Toad/MIPS star either stays plain or ends at
the region boundary and the next one starts on the grab.

The tests pin the two invariants that are easy to violate while editing —
no movement ends on a star grab, and a re-entry's first waypoint is never
its own start clause."
```

---

### Task 5: The 13 main-category routes

**Files:**
- Modify: `tools/corpus_routes_main.py` (the `ROUTES` list only)
- Test: `tests/test_corpus_routes_main.py` (new)

**Interfaces:**
- Consumes: `corpus_vocab.route/segment/star/stars`, `MAIN`; the `seed_key`s frozen in Task 4 and `corpus_legacy.SEGMENTS`.
- Produces: `ROUTES` — 13 route dicts.

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus_routes_main.py`:

```python
"""The star-count invariant is the headline gate here: a route named "70 Star"
that does not contain exactly 70 star candidates is a transcription error, and
nothing else in the system would ever notice. It caught the CCM17/CCM18 naming
(13 stars before CCM, so leaving with 17 or 18 is exactly what those names
mean) — see the sources companion."""
import importlib.util
import sys
from pathlib import Path

from sm64_events.tracking.routes import validate_route

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)

ROUTES = build_seed.corpus_routes_main.ROUTES
BY_KEY = {r["seed_key"]: r for r in ROUTES}

EXPECTED_STARS = {
    "route:16-no-lblj-standard": 16,
    "route:16-no-lblj-beginner": 16,
    "route:16-no-lblj-wf100c": 16,
    "route:16-lblj": 16,
    "route:70-hmc-late-beginner": 70,
    "route:70-hmc-late-intermediate": 70,
    "route:70-hmc-late-advanced": 70,
    "route:70-hmc-late-expert": 70,
    "route:70-hmc-early": 70,
    "route:120-non-lblj": 120,
    "route:120-lblj": 120,
    "route:1-star": 1,
    "route:0-star": 0,
}


def _star_total(route) -> int:
    """Stars the route requires: a step contributes `need` when its candidates
    are stars (need=2 for a '+ 100 Coins' pair, need=1 for an either/or)."""
    total = 0
    for step in route["steps"]:
        if all(c["type"] == "star" for c in step["candidates"]):
            total += step["need"]
    return total


def test_all_thirteen_routes_are_present():
    assert set(BY_KEY) == set(EXPECTED_STARS)
    assert len(ROUTES) == 13


def test_each_route_collects_exactly_its_category_star_count():
    for key, expected in EXPECTED_STARS.items():
        assert _star_total(BY_KEY[key]) == expected, key


def test_every_route_is_structurally_valid():
    for r in ROUTES:
        # segment candidates carry seed_key, which validate_route cannot
        # resolve — swap in a placeholder id, exactly as reconcile does.
        steps = [{"need": s["need"], "label": s.get("label"),
                  "candidates": [c if c["type"] == "star"
                                 else {"type": "segment", "segment_id": 1}
                                 for c in s["candidates"]]}
                 for s in r["steps"]]
        validate_route({"name": r["name"], "steps": steps,
                        "start_condition": r["start_condition"]})


def test_every_segment_reference_resolves_to_a_seeded_segment():
    known = {s["seed_key"] for s in build_seed.build()["segments"]}
    for r in ROUTES:
        for step in r["steps"]:
            for cand in step["candidates"]:
                if cand["type"] == "segment":
                    assert cand["seed_key"] in known, (r["seed_key"], cand)


def test_every_route_is_categorised_and_starts_on_reset():
    for r in ROUTES:
        assert r["category"] == "Main Categories", r["seed_key"]
        assert r["start_condition"] == {"type": "reset_game"}, r["seed_key"]


def test_every_step_carries_a_label():
    """160 unlabelled steps is unreadable in the Routes tab and the run view."""
    for r in ROUTES:
        for step in r["steps"]:
            assert step.get("label"), (r["seed_key"], step)


def test_no_movement_is_left_unreferenced():
    """Orphan guard: a movement no route uses is dead weight in every user's
    segment list — and usually means a route step was dropped."""
    used = {c["seed_key"] for r in ROUTES for s in r["steps"]
            for c in s["candidates"] if c["type"] == "segment"}
    for row in build_seed.corpus_movements.MOVEMENTS:
        assert row["seed_key"] in used, row["seed_key"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_corpus_routes_main.py -q`
Expected: FAIL — `assert set() == {...}` / `assert 0 == 13`.

- [ ] **Step 3: Write the first route in full — the transcription pattern**

Append to `tools/corpus_routes_main.py`. This route is the worked example; every other route uses exactly these constructors.

```python
ROUTES.append(route(
    "route:16-no-lblj-standard", "16 Star — No LBLJ (Standard)", MAIN, [
        segment("seg:lakitu-skip", "Lakitu Skip"),
        segment("seg:castle-entry->bob", "→ BoB"),
        star(1, 5, "BoB — Behind Chain Chomp's Gate"),
        segment("seg:bob->wf", "→ WF"),
        star(2, 5, "WF — Blast Away the Wall (Cannonless)"),
        star(2, 2, "WF — Shoot into the Wild Blue"),
        star(2, 0, "WF — Chip off Whomp's Block"),
        star(2, 1, "WF — To the Top of the Fortress"),
        star(2, 4, "WF — Fall onto the Caged Island (Owl Star)"),
        segment("seg:wf->ccm", "→ CCM"),
        star(4, 5, "CCM — Wall Kicks Will Work"),
        star(4, 1, "CCM — Li'l Penguin Lost"),
        segment("seg:ccm->bitdw", "→ BitDW"),
        star(16, 0, "BitDW — 8 Red Coins"),
        segment("seg:bitdw-pipe", "BitDW Pipe Entry"),
        segment("seg:bowser-1", "Bowser Battle 1"),
        segment("seg:bowser1->ssl", "→ SSL"),
        star(8, 0, "SSL — In the Talons of the Big Bird"),
        star(8, 1, "SSL — Shining Atop the Pyramid"),
        segment("seg:ssl->lll", "→ LLL"),
        star(7, 2, "LLL — 8-Coin Puzzle with 15 Pieces"),
        # HMC Toad is grabbed DURING the walk to HMC, so its step precedes the
        # movement's — the movement completes on entering HMC (spec §5.1).
        star(0, 0, "HMC Toad"),
        segment("seg:lll->hmc", "→ HMC"),
        star(6, 4, "HMC — A-Maze-Ing Emergency Exit"),
        star(6, 5, "HMC — Watch for Rolling Rocks"),
        segment("seg:mips-clip", "MIPS Clip"),
        star(9, 0, "DDD — Board Bowser's Sub"),
        segment("seg:ddd->bitfs", "→ BitFS"),
        segment("seg:bitfs-pipe", "BitFS Pipe Entry"),
        segment("seg:bowser-2", "Bowser Battle 2"),
        segment("seg:bowser2->upstairs", "→ Upstairs"),
        segment("seg:bits-entry", "Endless Staircase BLJ"),
        segment("seg:bits-pipe", "BitS Pipe Entry"),
        segment("seg:bowser-3", "Bowser Battle 3"),
    ]))
```

Run `uv run pytest tests/test_corpus_routes_main.py::test_each_route_collects_exactly_its_category_star_count -q` — it will still fail on the missing routes, but confirm no `KeyError` from the constructors.

- [ ] **Step 4: Write the remaining 12 routes**

Transcribe star content from `docs/superpowers/specs/2026-07-24-default-routes-corpus-sources.md` §"Main-category routes", using the alias glossary in the same file for the nicknames. The **movement skeletons** — the part not directly in the sources — are given here in full; insert the star steps between them per the sources.

`route:16-no-lblj-beginner` — "16 Star — No LBLJ (Beginner, no DW Reds)":
identical to the standard route except (a) drop the `star(16, 0)` BitDW reds
step, (b) the HMC block becomes, in this order and with **no** movement between
them (Emergency Exit exits the course, so the Toad grab needs no segment):
`star(6, 4)`, `star(0, 0)` HMC Toad, `star(6, 0)` Swimming Beast,
`star(6, 5)` Watch for Rolling Rocks — and `star(0, 0)` therefore moves OUT of
its pre-movement position and `segment("seg:lll->hmc")` sits directly after the
LLL star.

`route:16-no-lblj-wf100c` — "16 Star — No LBLJ + WF 100c (CCM Skip)":
the standard route with the CCM block and `seg:wf->ccm` / `seg:ccm->bitdw`
replaced by `segment("seg:wf->bitdw", "→ BitDW")`, and the WF block extended to
all seven stars in course-page order: `star(2,5)`, `star(2,4)`,
`stars(2, [3, 6], "WF — Red Coins on the Floating Isle + 100 Coins")`,
`star(2,2)`, `star(2,0)`, `star(2,1)`.
*(Judgement call, recorded here because the guide describes this variant in
prose only: it states the swap saves the CCM visit but not which stars replace
it. Two CCM stars out, two WF stars in keeps the total at 16.)*

`route:16-lblj` — "16 Star — LBLJ (Standard)":
```
seg:lakitu-skip, seg:lblj, star(16,0), seg:bitdw-pipe, seg:bowser-1,
seg:bowser1->wf, [WF: 5, 4, 2],
seg:wf->ssl, [SSL: 2, 0, 1],
seg:ssl->lll, [LLL: 2, 3, 0, then stars(7, [4, 5], "LLL — Hot-Foot-It into the
    Volcano or Elevator Tour", need=1)],
star(0,0) HMC Toad is grabbed BETWEEN HMC stars here, so: seg:lll->hmc,
    [HMC: 4, then star(0,0), then 5, 0],
seg:mips-clip, star(9,0), seg:ddd->bitfs, seg:bitfs-pipe, seg:bowser-2,
seg:bowser2->upstairs, seg:bits-entry, seg:bits-pipe, seg:bowser-3
```

`route:70-hmc-late-beginner` — "70 Star — HMC Late (Beginner)"
(no TTC100, CCM18, no Island Hop). Movement skeleton, with the star blocks from
the sources' 70★ listing inserted where marked:
```
seg:lakitu-skip, seg:castle-entry->bob, star(1,5),
seg:bob->pss, star(19,0),
seg:pss->wf, [WF all seven: 5, 4, (3,6), 2, 0, 1],
seg:wf->pss, star(19,1),
seg:pss->totwc, star(21,0),
seg:totwc->bitdw, star(16,0), seg:bitdw-pipe, seg:bowser-1,
seg:bowser1->bob, star(1,2),
seg:bob->ccm, [CCM18: 5, 1, 0, (2,6)],
seg:ccm->bbh, [BBH: 4, 2],
seg:bbh->basement, star(0,3) MIPS 1st,
seg:mips1->ssl, [SSL: 2, 0, 1],
seg:ssl->lll, [LLL all six: 3, 2, 0, 1, 4, 5],
seg:lll->ddd, [DDD: 1, 0, 4],
seg:ddd->bitfs, star(17,0), seg:bitfs-pipe, seg:bowser-2,
seg:bowser2->wdw, [WDW: (2,6), 3, 0, 1],
seg:wdw->thi, [THI: 3, 0, 1],
star(0,1) Upstairs Toad, seg:thi->ttm, [TTM: 0, 4, 5, 2, 3],
seg:ttm->sl, [SL: 0, 2, 3, 1, 4],
seg:sl->basement, star(0,4) MIPS 2nd, star(0,0) HMC Toad,
seg:mips2->hmc, [HMC: 0, 2, 4, 5],
seg:hmc->rr, [RR: 0, 4, 2, 3],
star(0,2) Tippy Toad, seg:rr->ttc, [TTC no-TTC100: 0, 1, 2, 3, 4, 5],
seg:ttc->bits, seg:bits-pipe, seg:bowser-3
```

`route:70-hmc-late-intermediate` (TTC100, CCM18, no IH): as Beginner, but the
SL block drops `star(10, 4)` Shell Shreddin' and the TTC block becomes
`stars(14, [3, 6], "TTC — Stomp on the Thwomp + 100 Coins")`, `star(14,0)`,
`star(14,1)`, `star(14,2)`, `star(14,4)`, `star(14,5)`.

`route:70-hmc-late-advanced` (TTC100, CCM17, no IH): as Intermediate, but the
CCM block becomes `star(4,5)`, `star(4,1)`,
`stars(4, [0, 6], "CCM — Slip Slidin' Away + 100 Coins")`, and the SSL block
gains `star(8, 5)` Pyramid Puzzle as its FIRST star.

`route:70-hmc-late-expert` (TTC100, CCM17, Island Hop): as Advanced, but
`star(1, 2)` Shoot to the Island moves to the opening BoB visit (before
`star(1, 5)`), and the return trip is dropped: `seg:bowser1->bob` +
`star(1,2)` + `seg:bob->ccm` collapse to `segment("seg:bowser1->ccm", "→ CCM")`.

`route:70-hmc-early` — "70 Star — HMC Early": as Beginner, but after the LLL
block go `seg:lll->hmc`, the HMC block `[0, 2, 4, 5]`, `star(0,0)` HMC Toad,
`seg:hmc->ddd`, the DDD block, then BitFS as normal; SL keeps
`star(10, 4)` Shell Shreddin'; after SL go `seg:sl->rr` straight to Tippy. The
`seg:sl->basement` / MIPS 2nd / `seg:mips2->hmc` / `seg:hmc->rr` sequence is
dropped entirely.

`route:120-non-lblj` — "120 Star — Non-LBLJ":
```
seg:lakitu-skip, seg:castle-entry->bob, star(1,5),
seg:bob->wf, [WF all seven], seg:wf->sa, star(24,0),
seg:sa->jrb, [JRB all seven: 0, 5, (3,6), 4, 2, 1],
seg:jrb->pss, star(19,0), seg:pss->totwc, star(21,0),
seg:totwc->pss, star(19,1),
seg:pss->bitdw, star(16,0), seg:bitdw-pipe, seg:bowser-1,
seg:bowser1->bob, [BoB rest: 0, 1, (3,6), 2, 4],
seg:bob->basement, star(0,3) MIPS 1st,
seg:mips1->ssl, [SSL all seven: 2, 3, 0, 4, (5,6), 1],
seg:ssl->hmc, [HMC: (1,6), then star(20,0) CotMC, then 0, 3, 2, 4, 5],
star(0,0) HMC Toad, seg:hmc->lll, [LLL all seven: 3, 2, 0, 1, (4,6), 5],
star(0,4) MIPS 2nd, seg:mips2->vcutm, star(22,0),
seg:vcutm->ccm, [CCM all seven: 5, 3, 1, 0, (2,6), 4],
seg:ccm->bbh, [BBH all seven: 0, (3,6), 5, 1, 4, 2],
seg:bbh->ddd, [DDD part 1: (2,6), 1, 0],
seg:ddd->bitfs, star(17,0), seg:bitfs-pipe, seg:bowser-2,
seg:bowser2->ddd, [DDD part 2: 4, 3, 5],
seg:ddd->wdw, [WDW all seven: 5, (4,6), 3, 1, 2, 0],
seg:wdw->thi, [THI all seven: 5, 3, (4,6), 0, 1, 2],
star(0,1) Upstairs Toad, seg:thi->ttm, [TTM all seven: 0, 1, 4, 5, (2,6), 3],
seg:ttm->sl, [SL all seven: 0, 5, (4,6), 2, 3, 1],
seg:sl->wmotr, star(23,0),
seg:wmotr->ttc, [TTC all seven: (3,6), 0, 1, 2, 4, 5],
star(0,2) Tippy Toad, seg:ttc->rr, [RR all seven: 0, (1,6), 5, 4, 2, 3],
seg:rr->bits, star(18,0), seg:bits-pipe, seg:bowser-3
```

`route:120-lblj` — "120 Star — LBLJ": as Non-LBLJ, but the opening becomes
`seg:lakitu-skip`, `seg:lblj`, `star(16,0)`, `seg:bitdw-pipe`, `seg:bowser-1`,
`seg:bowser1->wf`, then WF / SA / JRB / PSS / TotWC / PSS exactly as above,
then `seg:pss->bob` and **all six** BoB stars plus BoB 100 Coins in course-page
order `[5, 0, 1, (3,6), 4, 2]`, then `seg:bob->basement` and on as above.

`route:1-star` — "1 Star":
```
seg:lakitu-skip, seg:lblj, seg:bitdw-pipe, seg:bowser-1,
seg:bowser1->ddd, star(9,0), seg:ddd->bitfs, seg:bitfs-pipe, seg:bowser-2,
seg:bowser2->upstairs, seg:bits-entry, seg:bits-pipe, seg:bowser-3
```

`route:0-star` — "0 Star": as 1 Star, but `seg:bowser1->ddd` + `star(9,0)` +
`seg:ddd->bitfs` collapse to `segment("seg:bowser1->bitfs", "→ BitFS (DDD Skip)")`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_corpus_routes_main.py -q`
Expected: PASS (7 tests). If `test_each_route_collects_exactly_its_category_star_count` fails, the star total is the ground truth — re-read the sources listing for that category rather than adjusting the expectation.

- [ ] **Step 6: Commit**

```bash
git add tools/corpus_routes_main.py tests/test_corpus_routes_main.py
git commit -m "feat(corpus): the 13 main-category routes

Steps are in completion-event order because RunTracker only ever considers
steps[current] — a misordered step stalls a run forever. That is why a castle
star grabbed during a movement is listed BEFORE the movement whose level entry
completes it, which also happens to be exactly how the wiki reads.

The star-count test is the real gate: a route named '70 Star' containing
anything other than 70 star candidates is a transcription error nothing else
would catch. It independently reproduces the community's CCM17/CCM18 names
(13 stars precede CCM, so you leave with 17 or 18)."
```

---

### Task 6: The 37 Stage RTA routes

**Files:**
- Modify: `tools/corpus_routes_stage.py`
- Test: `tests/test_corpus_routes_stage.py` (new)

**Interfaces:**
- Consumes: `corpus_vocab.route/stars`, `STAGE_RTA`.
- Produces: `ROUTES` — 37 route dicts, `start_condition` = `{"type": "level_enter", "to": <level>}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_corpus_routes_stage.py`:

```python
"""Stage RTA routes are pure star lists for ONE course. The gates are: no
foreign course leaks in, no star is listed twice, every id is in range for its
course, and the run clock starts on entering the stage rather than on F1."""
import importlib.util
import sys
from pathlib import Path

from sm64_events.memory.addresses import COURSE_BY_LEVEL, star_count
from sm64_events.tracking.routes import validate_route

TOOLS = Path(__file__).resolve().parent.parent / "tools"
sys.path.insert(0, str(TOOLS))
_spec = importlib.util.spec_from_file_location(
    "build_defaults_seed", TOOLS / "build_defaults_seed.py")
build_seed = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_seed)

ROUTES = build_seed.corpus_routes_stage.ROUTES


def _candidates(route):
    return [c for s in route["steps"] for c in s["candidates"]]


def test_thirty_seven_stage_routes():
    assert len(ROUTES) == 37
    assert len({r["seed_key"] for r in ROUTES}) == 37


def test_every_stage_route_stays_inside_one_course():
    for r in ROUTES:
        courses = {c["course"] for c in _candidates(r)}
        assert len(courses) == 1, (r["seed_key"], courses)


def test_start_condition_enters_that_course():
    for r in ROUTES:
        cond = r["start_condition"]
        assert cond["type"] == "level_enter", r["seed_key"]
        course = next(iter({c["course"] for c in _candidates(r)}))
        assert COURSE_BY_LEVEL[cond["to"]] == course, r["seed_key"]


def test_no_duplicate_star_and_every_id_in_range():
    for r in ROUTES:
        ids = [c["star"] for c in _candidates(r)]
        assert len(set(ids)) == len(ids), r["seed_key"]
        course = next(iter({c["course"] for c in _candidates(r)}))
        assert all(0 <= i < star_count(course) for i in ids), r["seed_key"]


def test_every_stage_route_is_valid_and_categorised():
    for r in ROUTES:
        validate_route({"name": r["name"], "steps": r["steps"],
                        "start_condition": r["start_condition"]})
        assert r["category"] == "Stage RTA", r["seed_key"]
        assert all(s.get("label") for s in r["steps"]), r["seed_key"]


def test_no_stage_route_references_a_segment():
    for r in ROUTES:
        assert all(c["type"] == "star" for c in _candidates(r)), r["seed_key"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_corpus_routes_stage.py -q`
Expected: FAIL — `assert 0 == 37`.

- [ ] **Step 3: Fill in the table and the expansion loop**

Replace `ROUTES = []` in `tools/corpus_routes_stage.py` with:

```python
from sm64_events.memory.addresses import star_name

# (slug, display suffix, course_id, level_id, [star ids; a tuple = one step
# collecting both, i.e. "+ 100 Coins"; a set = a documented either/or])
STAGES = [
    ("bob-120", "BoB — 120", 1, 9, [5, 0, 1, (3, 6), 4, 2]),
    ("bob-70", "BoB — 70", 1, 9, [5, 2]),
    ("wf-120-70", "WF — 120 / 70", 2, 24, [5, 4, (3, 6), 2, 0, 1]),
    ("jrb-120", "JRB — 120", 3, 12, [0, 5, (3, 6), 4, 2, 1]),
    ("ccm-120", "CCM — 120", 4, 5, [5, 3, 1, 0, (2, 6), 4]),
    ("ccm-70-ccm17", "CCM — 70 (CCM17)", 4, 5, [5, 1, (0, 6)]),
    ("ccm-70-ccm18", "CCM — 70 (CCM18)", 4, 5, [5, 1, 0, (2, 6)]),
    ("ccm-16", "CCM — 16", 4, 5, [5, 1]),
    ("bbh-120", "BBH — 120", 5, 4, [0, (3, 6), 5, 1, 4, 2]),
    ("bbh-70", "BBH — 70", 5, 4, [4, 2]),
    ("hmc-120", "HMC — 120", 6, 7, [(1, 6), 0, 3, 2, 4, 5]),
    ("hmc-70", "HMC — 70", 6, 7, [0, 2, 4, 5]),
    ("hmc-16", "HMC — 16", 6, 7, [0, 4, 5]),
    ("lll-120", "LLL — 120", 7, 22, [3, 2, 0, 1, (4, 6), 5]),
    ("lll-70", "LLL — 70", 7, 22, [3, 2, 0, 1, 4, 5]),
    ("lll-16", "LLL — 16", 7, 22, [3, 2, 0, {4, 5}]),
    ("ssl-120", "SSL — 120", 8, 8, [2, 3, 0, 4, (5, 6), 1]),
    ("ssl-70-16", "SSL — 70 / 16", 8, 8, [2, 0, 1]),
    ("ddd-120", "DDD — 120", 9, 23, [(2, 6), 1, 0, 4, 3, 5]),
    ("ddd-70", "DDD — 70", 9, 23, [1, 0, 4]),
    ("ddd-16", "DDD — 16", 9, 23, [0]),
    ("sl-120", "SL — 120", 10, 10, [0, 5, (4, 6), 2, 3, 1]),
    ("sl-70-ttc100", "SL — 70 (HMC Late + TTC100)", 10, 10, [0, 2, 3, 1]),
    ("sl-70", "SL — 70 (HMC Early or no TTC100)", 10, 10, [0, 2, 4, 3, 1]),
    ("wdw-120", "WDW — 120", 11, 11, [5, (4, 6), 3, 1, 2, 0]),
    ("wdw-120-beginner", "WDW — 120 (Beginner)", 11, 11,
     [5, (2, 6), 3, 1, 4, 0]),
    ("wdw-70", "WDW — 70", 11, 11, [(2, 6), 3, 1, 0]),
    ("ttm-120", "TTM — 120", 12, 36, [0, 1, 4, 5, (2, 6), 3]),
    ("ttm-70", "TTM — 70", 12, 36, [0, 4, 5, 2, 3]),
    ("thi-120", "THI — 120", 13, 13, [5, 3, (4, 6), 0, 1, 2]),
    ("thi-70", "THI — 70", 13, 13, [3, 1, 0]),
    ("thi-70-reds", "THI — 70 (with THI Reds)", 13, 13, [3, 1, 4, 0]),
    ("ttc-120-70", "TTC — 120 / 70", 14, 14, [(3, 6), 0, 1, 2, 4, 5]),
    ("ttc-70-no-100", "TTC — 70 (no TTC100)", 14, 14, [0, 1, 2, 3, 4, 5]),
    ("rr-120-beginner", "RR — 120 (Beginner)", 15, 15,
     [0, (1, 6), 5, 4, 2, 3]),
    ("rr-120-expert", "RR — 120 (Expert)", 15, 15, [0, (5, 6), 1, 4, 2, 3]),
    ("rr-70", "RR — 70", 15, 15, [0, 4, 2, 3]),
]


def _step(course, entry):
    """int -> one star; tuple -> collect BOTH (the '+ 100 Coins' pair);
    set -> pick EITHER (a documented alternative)."""
    if isinstance(entry, tuple):
        label = " + ".join(star_name(course, s) for s in entry)
        return stars(course, list(entry), label)
    if isinstance(entry, set):
        ids = sorted(entry)
        label = " or ".join(star_name(course, s) for s in ids)
        return stars(course, ids, label, need=1)
    return stars(course, [entry], star_name(course, entry))


ROUTES = [route(f"route:stage-{slug}", name, STAGE_RTA,
                [_step(course, entry) for entry in order],
                start_condition={"type": "level_enter", "to": level})
          for slug, name, course, level, order in STAGES]
```

Add `stars` to the module's import from `corpus_vocab` if it is not already there.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_corpus_routes_stage.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/corpus_routes_stage.py tests/test_corpus_routes_stage.py
git commit -m "feat(corpus): 37 Stage RTA routes, one per documented star list

Per-course practice plans, so the within-stage order the main routes cannot
express (a 120 route lists all seven WF stars in run order, but a runner
drilling WF wants just that stage) has a home.

start_condition is entering the course rather than the default F1 reset, so
the run clock starts on the painting — which is what a stage RTA actually
times. Labels come from star_name, so a star rename can never leave a stale
label behind."
```

---

## Wave 3 — Integration, verification, docs (serial)

### Task 7: Regenerate the seed and pin the drift guard

**Files:**
- Modify: `src/sm64_events/data/defaults.seed.json` (regenerated with full content)
- Test: `tests/test_build_defaults_seed.py` (append)

**Interfaces:**
- Consumes: everything from Tasks 3–6.
- Produces: the shipped seed — 65 segments, 50 routes.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_build_defaults_seed.py`:

```python
def test_shipped_seed_has_the_whole_corpus():
    seed = json.loads(build_seed.OUT.read_bytes().decode("utf-8"))
    assert seed["seed_version"] == 2
    assert len(seed["segments"]) == 65          # 10 legacy + 55 movements
    assert len(seed["routes"]) == 50            # 13 main + 37 stage
    assert len({s["seed_key"] for s in seed["segments"]}) == 65
    assert len({r["seed_key"] for r in seed["routes"]}) == 50


def test_shipped_seed_reconciles_into_a_fresh_db_cleanly(tmp_path):
    """End to end: the artifact the app actually reads must apply with zero
    skipped rows and resolve every route candidate to a real segment id."""
    from sm64_events.storage.db import Database
    from sm64_events.tracking.defaults import reconcile_defaults
    db = Database(tmp_path / "t.db")
    seed = json.loads(build_seed.OUT.read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    assert len(db.segment_defs()) == 65
    routes = db.routes()
    assert len(routes) == 50
    broken = [(r["name"], c) for r in routes for s in r["steps"]
              for c in s["candidates"]
              if c["type"] == "segment" and c["segment_id"] == -1]
    assert broken == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_build_defaults_seed.py -q`
Expected: FAIL — the seed still holds 10 segments and 0 routes.

- [ ] **Step 3: Regenerate**

Run: `uv run python tools/build_defaults_seed.py`
Expected: `wrote ...defaults.seed.json: 65 segments, 50 routes`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_build_defaults_seed.py tests/test_seed_reconcile.py -q`
Expected: PASS.

Confirm the line endings survived: `git ls-files --eol src/sm64_events/data/defaults.seed.json` → `i/lf w/lf`.

- [ ] **Step 5: Full suite, then commit**

Run: `uv run pytest -q`

```bash
git add src/sm64_events/data/defaults.seed.json tests/test_build_defaults_seed.py
git commit -m "feat(data): ship the generated route corpus seed

65 segments and 50 routes. Regenerated in one place so the three corpus
tables could be authored in parallel without fighting over the artifact.

The end-to-end test is the one that matters: reconciling the shipped file
into a fresh db must skip zero rows and leave zero route candidates pointing
at segment_id -1, which is what a mistyped seed_key would produce."
```

---

### Task 8: Movement simulation — the behavioural gate

**Files:**
- Create: `tests/test_defaults_corpus.py`

**Interfaces:**
- Consumes: the shipped seed, `SegmentEngine`, `MatchContext`, `addresses.WORLD_EDGES_TWO_WAY`.
- Produces: `walk_events(from_level, to_level, ...)` — the independent world model reused by Task 9.

- [ ] **Step 1: Write the world model and the failing test**

Create `tests/test_defaults_corpus.py`:

```python
"""Behavioural gate for the seeded corpus.

The events fed here are synthesized from an INDEPENDENT world model — the
castle topology in addresses.py plus how a real walk between two levels
actually fires — NOT from the definition under test. That independence is the
whole point: a def that merely agrees with itself proves nothing.

Layer 1 (structural) and layer 2 (per-movement simulation) live here; layer 3
(whole-route RunTracker replay) is in test_defaults_corpus_routes.py."""
import json
from dataclasses import dataclass

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.memory.addresses import (LEVEL_CASTLE_INSIDE,
                                          WORLD_EDGES_TWO_WAY)
from sm64_events.tracking.segments import (MatchContext, SegmentDef,
                                           SegmentEngine, validate_definition)

SEED = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
SEGMENTS = SEED["segments"]
MOVEMENTS = [s for s in SEGMENTS if s["category"] == "Castle Movement"
             and s["guards"]]      # the 55 route-scoped ones, not the legacy 3


# --- the independent world model -------------------------------------------

def region_of(level: int) -> int | None:
    """Castle area a level is entered from, derived from the topology registry
    (NOT from any segment definition). One hop for a painting/door off a castle
    area; a second hop for a level reached via a hub (BBH via the courtyard,
    VCUtM via the grounds) or via another course (CotMC via HMC)."""
    direct, hubs = {}, {}
    for node_a, node_b in WORLD_EDGES_TWO_WAY:
        for src, dst in ((node_a, node_b), (node_b, node_a)):
            if isinstance(src, tuple) and src[0] == LEVEL_CASTLE_INSIDE \
                    and not isinstance(dst, tuple):
                direct.setdefault(dst, src[1])
            elif not isinstance(src, tuple) and not isinstance(dst, tuple):
                hubs.setdefault(dst, src)
    if level in direct:
        return direct[level]
    parent = hubs.get(level)
    return direct.get(parent) if parent is not None else None


@dataclass
class Ev:
    """Minimal journal-event stand-in — the fields SegmentEngine reads."""
    id: int
    type: str
    frame: int
    payload: dict
    wall_time_utc: str = "2026-07-24T00:00:00Z"
    session_id: int = 1


def test_region_of_matches_the_castle_layout():
    """Pins the model itself, so a wrong walk can't quietly excuse a wrong def."""
    assert region_of(9) == 1 and region_of(17) == 1      # BoB, BitDW: lobby
    assert region_of(8) == 3 and region_of(19) == 3      # SSL, BitFS: basement
    assert region_of(10) == 2 and region_of(21) == 2     # SL, BitS: upstairs
    assert region_of(4) == 1                             # BBH via the courtyard
    assert region_of(18) == 1                            # VCUtM via the grounds
```

- [ ] **Step 2: Run to verify the model test passes and nothing else exists yet**

Run: `uv run pytest tests/test_defaults_corpus.py -q`
Expected: PASS (1 test). If `test_region_of_matches_the_castle_layout` fails, fix `region_of` — the topology table is the ground truth.

- [ ] **Step 3: Add the walk generator and the structural layer**

Append to `tests/test_defaults_corpus.py`:

```python
BOWSER_EXIT_REGION = {30: 1, 33: 3, 34: 2}   # arena -> where its exit lands


def _landing_region(level: int) -> int | None:
    """Where a player stands after LEAVING `level`."""
    return BOWSER_EXIT_REGION.get(level, region_of(level))


def walk_events(steps, start_frame=100):
    """Turn a list of ('level', to, frm) / ('area', to, frm) / ('star', c, s) /
    ('warp', level) / ('key', level) moves into journal-shaped events, one
    frame apart. Callers describe the PLAYER's route; this function knows only
    how those moves look on the wire."""
    events, frame, next_id = [], start_frame, 1
    for move in steps:
        kind = move[0]
        if kind == "level":
            payload = {"to": move[1], "from": move[2]}
            events.append(Ev(next_id, "level_changed", frame, payload))
        elif kind == "area":
            events.append(Ev(next_id, "area_changed", frame,
                             {"level": LEVEL_CASTLE_INSIDE, "to": move[1],
                              "from": move[2], "from_transient": False}))
        elif kind == "star":
            events.append(Ev(next_id, "star_collected", frame,
                             {"course_id": move[1], "star_id": move[2],
                              "num_stars": 0}))
        elif kind == "warp":
            events.append(Ev(next_id, "warp_entered", frame, {"level": move[1]}))
        elif kind == "key":
            events.append(Ev(next_id, "key_grabbed", frame, {"level": move[1]}))
        else:
            raise AssertionError(f"unknown move {move!r}")
        frame += 1
        next_id += 1
    return events


def run_engine(seed_row, events, level=None, area=None):
    """Feed `events` to a one-def engine, tracking level/area exactly as the
    projector does, and return the closed attempts."""
    definition = SegmentDef(
        id=1, name=seed_row["name"], enabled=True,
        start_triggers=seed_row["start_triggers"],
        end_triggers=seed_row["end_triggers"],
        waypoints=seed_row["waypoints"], guards=[])   # guards dropped: the
        # in_active_route gate is proven in test_segments.py, not here
    engine = SegmentEngine([definition])
    closed = []
    for ev in events:
        if ev.type == "level_changed":
            level = ev.payload["to"]
        if ev.type == "area_changed":
            area = ev.payload["to"]
        ctx = MatchContext(level=level, prev_level=level, num_stars=0, area=area)
        got, _ = engine.feed(ev, ctx)
        closed.extend(got)
    return closed


# --- layer 1: structural ---------------------------------------------------

def test_every_seeded_segment_validates():
    for row in SEGMENTS:
        validate_definition(row)


def test_every_movement_is_route_scoped():
    assert len(MOVEMENTS) == 55
    for row in MOVEMENTS:
        assert row["guards"] == [{"type": "in_active_route"}], row["seed_key"]


def test_route_candidates_all_resolve():
    keys = {s["seed_key"] for s in SEGMENTS}
    for route in SEED["routes"]:
        for step in route["steps"]:
            for cand in step["candidates"]:
                if cand["type"] == "segment":
                    assert cand["seed_key"] in keys, (route["seed_key"], cand)
```

- [ ] **Step 4: Add the simulation layer**

Append to `tests/test_defaults_corpus.py`:

```python
def movement_walk(row):
    """The event stream a player produces performing this movement, built from
    the world model. Start where the movement begins, cross every region and
    hub the topology says lies on the way, end where it ends."""
    start, end = row["start_triggers"][0], row["end_triggers"][0]
    via = [step[0] for step in row["waypoints"]]
    moves, region = [], None

    if start["type"] == "level_exit":
        source = start["from"]
        landing = start.get("to", _landing_region(source) and LEVEL_CASTLE_INSIDE)
        moves.append(("level", landing or LEVEL_CASTLE_INSIDE, source))
        region = _landing_region(source)
        if region is not None and landing in (None, LEVEL_CASTLE_INSIDE):
            moves.append(("area", region, region))     # establishing edge
    elif start["type"] == "level_enter":
        moves.append(("level", start["to"], start["from"]))
        region = 1
        moves.append(("area", 1, 1))
    elif start["type"] == "star_grabbed":
        moves.append(("star", start["course"], start["star"]))
        region = 3          # both castle-secret movement starts are in the basement

    for clause in via:
        if clause["type"] == "level_enter":
            moves.append(("level", clause["to"], LEVEL_CASTLE_INSIDE
                          if region is not None else clause["to"]))
            region = None if clause["to"] != LEVEL_CASTLE_INSIDE else 1
            if clause["to"] == LEVEL_CASTLE_INSIDE:
                moves.append(("area", 1, 1))
        elif clause["type"] == "level_exit":
            moves.append(("level", LEVEL_CASTLE_INSIDE, clause["from"]))
            region = _landing_region(clause["from"])
            moves.append(("area", region, region))
        elif clause["type"] == "area_enter":
            moves.append(("area", clause["area"], region if region else 1))
            region = clause["area"]

    if end["type"] == "level_enter":
        if region is not None and region != (region_of(end["to"]) or region):
            moves.append(("area", region_of(end["to"]), region))
        moves.append(("level", end["to"], LEVEL_CASTLE_INSIDE))
    elif end["type"] == "area_enter":
        moves.append(("area", end["area"], region or 1))
    return moves


def test_every_movement_completes_exactly_once_on_its_own_walk():
    for row in MOVEMENTS:
        closed = run_engine(row, walk_events(movement_walk(row)))
        successes = [a for a in closed if a.outcome == "success"]
        assert len(successes) == 1, (row["seed_key"], [a.outcome for a in closed])
        assert len(closed) == 1, (row["seed_key"], [a.outcome for a in closed])


def test_a_movement_ignores_an_unrelated_walk():
    """Negative pass: no movement may complete on a DIFFERENT movement's walk.
    Two movements sharing a source (BoB -> WF and BoB -> CCM) is the case that
    matters — the start clause matches, the end must not."""
    for row in MOVEMENTS:
        for other in MOVEMENTS:
            if other["seed_key"] == row["seed_key"]:
                continue
            if other["end_triggers"] == row["end_triggers"]:
                continue          # same destination: completing is correct
            closed = run_engine(row, walk_events(movement_walk(other)))
            assert not [a for a in closed if a.outcome == "success"], (
                row["seed_key"], other["seed_key"])
```

- [ ] **Step 5: Run and iterate**

Run: `uv run pytest tests/test_defaults_corpus.py -q`

Expected: PASS. When a movement fails, the failure is real — decide which side is wrong:
- If `movement_walk` produced an event a player would not actually fire, fix the model.
- If the walk is right and the def never completes, the def is wrong: re-read spec §4.1 and check whether it needs a waypoint it lacks (region/hub crossing) or has a waypoint that a plain def would handle better.
- Record every def you change in the commit message — a corrected row is evidence the layer is doing its job.

- [ ] **Step 6: Commit**

```bash
git add tests/test_defaults_corpus.py
git commit -m "test(corpus): simulate every movement against an independent world model

55 movement definitions were authored blind against a matcher with two
non-obvious disarm rules. Validating their shape proves nothing about whether
they fire, so this synthesizes each one's event stream from the castle
topology — not from the definition under test — and asserts exactly one
success and no other rows, then replays every OTHER movement's walk past it
and asserts silence."
```

---

### Task 9: Route simulation — the ordering gate

**Files:**
- Create: `tests/test_defaults_corpus_routes.py`

**Interfaces:**
- Consumes: `walk_events` / `movement_walk` / `region_of` from `tests/test_defaults_corpus.py`, `RunTracker`, the shipped seed.

- [ ] **Step 1: Write the failing test**

Create `tests/test_defaults_corpus_routes.py`:

```python
"""Layer 3: replay each main-category route end to end through RunTracker.

RunTracker only ever considers steps[current], so a step listed out of
completion-event order stalls a run PERMANENTLY — and nothing in validation,
the seed, or the UI would ever say so. This is the only gate that catches it,
and it is the reason ~700 blind-authored steps can be trusted."""
import json

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.tracking.runs import RunTracker
from sm64_events.tracking.segments import MatchContext

# tests/ has no __init__.py, but pytest prepends the test file's directory to
# sys.path, so the sibling module imports by bare name (verified 2026-07-24).
from test_defaults_corpus import Ev, movement_walk, walk_events

SEED = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
SEG_BY_KEY = {s["seed_key"]: s for s in SEED["segments"]}
MAIN_ROUTES = [r for r in SEED["routes"] if r["category"] == "Main Categories"]


class FakeAttempt:
    """Only the fields RunTracker._apply reads."""
    def __init__(self, outcome, course_id=None, star_id=None, segment_id=None):
        self.outcome, self.cleared = outcome, False
        self.course_id, self.star_id, self.segment_id = course_id, star_id, segment_id


def _step_attempts(step, seg_ids):
    """The successful attempts completing this step, in the order the player
    would produce them."""
    out = []
    for cand in step["candidates"][:step["need"]]:
        if cand["type"] == "star":
            out.append(FakeAttempt("success", course_id=cand["course"],
                                   star_id=cand["star"]))
        else:
            out.append(FakeAttempt("success",
                                   segment_id=seg_ids[cand["seed_key"]]))
    return out


def _resolved(route, seg_ids):
    return [{"need": s["need"],
             "candidates": [c if c["type"] == "star"
                            else {"type": "segment",
                                  "segment_id": seg_ids[c["seed_key"]]}
                            for c in s["candidates"]]}
            for s in route["steps"]]


def test_every_main_route_finishes_when_played_in_its_own_order():
    seg_ids = {key: i + 1 for i, key in enumerate(SEG_BY_KEY)}
    for route in MAIN_ROUTES:
        steps = _resolved(route, seg_ids)
        tracker = RunTracker()
        frame = 0
        started = Ev(1, "run_started", frame, {
            "route_id": 1, "route_name": route["name"], "route_steps": steps,
            "mode": "rta", "start_offset_ms": 0,
            "start_condition": {"type": "reset_game"}})
        ctx = MatchContext(level=None, prev_level=None, num_stars=0)
        tracker.feed(started, [], ctx)
        frame += 1
        tracker.feed(Ev(2, "game_reset", frame, {}), [], ctx)

        finished = []
        for index, step in enumerate(route["steps"]):
            for attempt in _step_attempts(step, seg_ids):
                frame += 1
                ev = Ev(100 + frame, "star_collected", frame, {})
                finished += tracker.feed(ev, [attempt], ctx)
            assert tracker.active_run_view() is None or \
                tracker.active_run_view()["current_step"] > index, (
                    route["seed_key"], index, step.get("label"))

        assert len(finished) == 1, route["seed_key"]
        assert finished[0].status == "finished", route["seed_key"]
        assert finished[0].reached_step == len(steps), route["seed_key"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_defaults_corpus_routes.py -q`
Expected: FAIL — a stalled step, naming the route and step index. (The sibling import is already correct: `tests/` is not a package, but pytest prepends the test file's own directory to `sys.path`, so the bare `from test_defaults_corpus import ...` resolves. Verified in this worktree 2026-07-24 — do not add a `tests/__init__.py`, which would change how every other test file is imported.)

- [ ] **Step 3: Make it pass**

The implementation here is **the route tables from Task 5**, not new code. A failure names the exact route and step index that stalled; fix the ORDER in `tools/corpus_routes_main.py`, regenerate with `uv run python tools/build_defaults_seed.py`, and re-run. Apply spec §5.1:
- a movement step goes immediately before its destination's star block;
- a castle-secret star grabbed during a movement goes immediately before that movement's step;
- a Bowser block is `[→ course] [reds] [pipe] [fight]`.

- [ ] **Step 4: Add the ordering regression guard**

Append to `tests/test_defaults_corpus_routes.py`:

```python
def test_no_route_lists_a_star_step_after_the_movement_it_happens_during():
    """The §5.2 trap, checked statically as well as by replay: a movement
    ending on a star grab would consume that star attempt's turn, because
    `closed` is ordered stars-then-segments within one event."""
    for route in MAIN_ROUTES:
        for step in route["steps"]:
            for cand in step["candidates"]:
                if cand["type"] != "segment":
                    continue
                end = SEG_BY_KEY[cand["seed_key"]]["end_triggers"][0]
                assert end["type"] != "star_grabbed", (route["seed_key"], cand)
```

- [ ] **Step 5: Full suite, then commit**

Run: `uv run pytest -q`

```bash
git add tests/test_defaults_corpus_routes.py tools/corpus_routes_main.py \
        src/sm64_events/data/defaults.seed.json
git commit -m "test(corpus): replay every main route through RunTracker

RunTracker only ever considers steps[current], so one step in the wrong
position stalls a run forever and nothing else in the system reports it —
not validation, not the seed, not the UI. Replaying all 13 routes in their
own authored order is the only way to know ~700 steps are right."
```

---

### Task 10: Documentation

**Files:**
- Modify: `CLAUDE.md` (module map)
- Modify: `docs/architecture.md`
- Modify: `README.md` (only if the seed/tooling surface is user-facing there)

- [ ] **Step 1: Re-read CLAUDE.md before editing**

Run: `git log --oneline -3 -- CLAUDE.md`

Another session edits this file concurrently (the star-selector work). Read the current content immediately before editing and add rows rather than rewriting neighbours.

- [ ] **Step 2: Add the module-map rows**

Add to the module map table in `CLAUDE.md`:

```markdown
| Default route/segment corpus (authoring) | `tools/build_defaults_seed.py` + `tools/corpus_{vocab,legacy,movements,routes_main,routes_stage}.py` — compact Python tables expanded into `data/defaults.seed.json` (mirrors `tools/scrape_ranks.py` → `rank_standards.seed.json`). `--check` is the drift guard, pinned by `tests/test_build_defaults_seed.py`; NEVER hand-edit the JSON. Movement shapes are FORCED by the matcher (spec `2026-07-24-default-routes-corpus-design.md` §4): a plain def dies on an `area_changed` away from its arm position and on a `level_changed` matching neither end; a waypoint def dies on any star grab — so a region/hub crossing needs a waypoint, and a movement spanning a Toad/MIPS star either stays plain or ends at the region boundary while the next one STARTS on `star_grabbed`. A movement may start on a star grab but must NEVER end on one (run-ordering trap) |
| Route step ORDER (a hard contract) | `tracking/runs.py::RunTracker._apply` only ever considers `steps[current]`, and `projection.py` builds `closed` stars-then-segments within one event — so seeded route steps must be in completion-event order or a run stalls permanently. Movement step immediately before its destination's star block; a castle star grabbed mid-movement immediately before that movement. Pinned by `tests/test_defaults_corpus_routes.py` |
```

- [ ] **Step 3: Record the domain knowledge in `docs/architecture.md`**

Add a section covering: the movement grammar and *why* it is forced (with the two `segments.py` disarm rules and their line references); the run step-ordering contract and the stars-then-segments `closed` ordering that causes it; the course-0 castle-secret star ids with their decomp evidence and the outstanding Toad-binding VERIFY; and the corpus verification strategy (independent world model, not self-agreement).

- [ ] **Step 4: Full suite, then commit**

Run: `uv run pytest -q`

```bash
git add CLAUDE.md docs/architecture.md
git commit -m "docs: record the movement grammar and the route-order contract

Both facts are invisible in the data they govern. A future session editing a
seeded movement will not rediscover that a plain def dies on the area change
and a waypoint def dies on a star grab, and will not rediscover that route
steps must be in completion-event order because RunTracker only looks at
steps[current] — it will just ship a segment that never fires or a route that
stalls. The evidence and the line references go with the rules."
```

---

## Final verification

- [ ] `uv run pytest -q` — full suite green
- [ ] `uv run python tools/build_defaults_seed.py --check` — reports up to date
- [ ] `git ls-files --eol src/sm64_events/data/defaults.seed.json` — `i/lf w/lf`
- [ ] `git log --oneline main..HEAD` — every commit on `feature/default-routes-corpus`, none stray onto `main`
- [ ] Confirm no `src/sm64_events/ui/` file appears in `git diff --stat main..HEAD`
- [ ] **Mandatory whole-branch review** via `superpowers:requesting-code-review`, including the docs and fold-in commits
- [ ] Flag at merge: `memory/addresses.py` is a shared-contract file (additive `STAR_NAMES[0]` only)
- [ ] **Live-gate VERIFY items** to hand to the human: (a) which Toad carries star index 0/1/2; (b) real-anchor rewind vs relocation on `seg:sl->basement` / `seg:hmc->rr` — the first multi-level movements that can exercise it
- [ ] **Release note:** a user-*deleted* default route or segment resurrects on the next update (reconcile re-inserts any seed row missing from the db). **Disable** is the protected hide path; Delete is not.

## Self-review notes (author)

- **Spec coverage:** §4 grammar → Task 4; §4.4 inventory → Task 4; §5 ordering → Tasks 5 + 9; §6 secret stars → Task 1; §7.1 → Task 5; §7.2 → Task 6; §8 generator → Tasks 3 + 7; §9 verification → Tasks 8 + 9 (+ per-table gates in 4/5/6); §10 follow-ups → Task 2 (hardening, rename), Task 8 (`waypoints` now validated for every seeded row via `test_every_seeded_segment_validates`, which passes whole rows rather than the old four-key projection), Task 10 + Final verification (VERIFY items); §12 → Task 10 + Final verification.
- **`region_of` moved from the generator to the test** (spec §8 says the generator derives it). The movement table carries explicit clauses, so the generator needs no topology at all — and putting the derivation in the *test* is strictly better, because the world model must be independent of the thing it validates. Spec §8's sentence is now slightly ahead of the code; Task 10 should correct it when touching the docs.
- **Wave 2 parallelism:** Tasks 4/5/6 own one table module and one test file each and never regenerate the JSON, so three concurrent agents cannot conflict. Task 5 depends on Task 4's `seed_key`s, which spec §4.4 freezes — it does not need Task 4 to have landed.
- **Task 9's import of a sibling test module** is the one fragile mechanic; Step 2 names the fallback explicitly rather than assuming `tests/` is a package.
