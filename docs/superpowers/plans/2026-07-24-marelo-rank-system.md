# MARELO Rank & Progression System — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the trainer one 0–100 rating ("MARELO") per scope — overall, per course, per route — derived from the rank standards already in the app, expressed as a tier + division, with history, celebration, and a "you are here" marker on the standards table.

**Architecture:** Three new pure modules under `ranks/` (curve, scope aggregation, history replay) with no I/O, a single bridge in `tracking/marelo.py` that turns attempts into per-entity scores, REST endpoints in the existing `server/ranks_api.py`, and four UI components. Nothing is stored: scores derive from attempts + standards; exclusions and celebration watermarks are `ui_state` KVs. Spec: `docs/superpowers/specs/2026-07-24-marelo-rank-system-design.md`.

**Tech Stack:** Python 3.12 via **uv** (never pip), FastAPI, pytest. Frontend is Preact + htm, no build step (`ui/components/*.js`, vendored Preact).

## Global Constraints

- `uv run pytest -q` must pass before any merge. Run commands through the **Bash** tool, never PowerShell piping of native exes (exit-code honesty rule).
- Times are compared in **displayed centiseconds** via `classify.display_cs` — never raw frames (project rule 7 / Usamune IGT clock).
- `SCORE_ANCHORS = {Mario 95, Grandmaster 90, Master 80, Diamond 70, Platinum 60, Gold 45, Silver 25, Bronze 10}` — verbatim from sm64-xcams' player bands plus a Bronze anchor. Iron carries no anchor; it is the implicit floor, exactly as in `classify.RANK_NAMES`.
- `DIVISIONS_PER_TIER = 5`, numerals `V IV III II I` with **V at the bottom** of the tier.
- **The invariant:** `tier_from_score(score_for(L, t), defined_tiers(L)) == classify.rank_for(L, t)` for every ladder and time. Any change that breaks it is wrong.
- **No DB migration.** Exclusions → `ui_state` KV `rank_excluded`; celebration watermarks → `ui_state` KV `marelo_watermarks`.
- Rank ORDER and `RANK_MODES` stay owned by `ranks/classify.py`; score anchors and divisions are owned by `ranks/scoring.py`. Neither duplicates the other.
- **Naming collision to avoid:** `ui/store.js` already has a `scope` state meaning session-vs-lifetime. MARELO's scope is always named `rankScope` in the UI and `scope_id` on the wire.
- 100-coin stars are **out of scope** — a parallel session owns them (spec §12). Do not touch `tools/scrape_ranks.py`, `data/rank_standards.seed.json`, `tracking/segments.py`, `tools/corpus_*.py`, or `data/defaults.seed.json`.
- Commit messages explain WHY, in the style of `git log`.

---

## File Structure

| File | Responsibility | Owner task |
|---|---|---|
| `src/sm64_events/ranks/scoring.py` (new) | The 0–100 curve, best-possible ladder, tier/division from a score. Pure, no I/O. | T1 |
| `src/sm64_events/ranks/scopes.py` (new) | Rankable corpus, scope → entity groups, aggregation, available gain, celebration delta. Pure. | T3 |
| `src/sm64_events/ranks/history.py` (new) | Chronological replay of successes → MARELO series. Pure. | T4 |
| `src/sm64_events/tracking/marelo.py` (new) | The only attempts→scores bridge: per-entity basis under the active rank mode. | T5 |
| `src/sm64_events/tracking/views.py` (modify) | Promote `_grading_basis`→`grading_basis`, `_valid_frames`→`valid_frames`; add the entity-level medal to every section. | T6 |
| `src/sm64_events/tracking/service.py` (modify) | `set_rank_excluded`, `ack_celebration`; broadcast `marelo_changed`. | T7 |
| `src/sm64_events/server/ranks_api.py` (modify) | `/api/marelo`, `/api/marelo/history`, `/api/marelo/scopes`, exclusion + ack. | T8 |
| `src/sm64_events/ui/components/marelo.js` (new) | Crest + header bar. | T9 |
| `src/sm64_events/ui/components/rankpage.js` (new) | Rank tab: scope picker, card, history chart, breakdown. | T10 |
| `src/sm64_events/ui/components/celebrate.js` (new) | Rank-up overlay. | T11 |
| `src/sm64_events/ui/components/standards.js` (modify) | "You are here" column marker. | T12 |
| `src/sm64_events/ui/components/practice.js`, `ranks.js` (modify) | Second (entity) medal on section headers. | T13 |
| `src/sm64_events/ui/app.js`, `store.js` (modify) | Rank sidebar entry, `rankScope` state, mount the overlay. | T14 |
| `.claude/rules/ranks.md`, `.claude/rules/ui.md`, `README.md` | Change-map + API-surface docs. | T15 |

**Waves (for `parallel-worktree`):**

- **Wave 0 — foundation, serialized:** T1, T2
- **Wave 1 — parallel:** T3, T4, T5, T12
- **Wave 2 — serialized (shared files):** T6, T7, T8
- **Wave 3 — parallel UI:** T9, T10, T11, T13
- **Wave 4 — serialized:** T14, T15

---

## Task 1: The score curve

**Files:**
- Create: `src/sm64_events/ranks/scoring.py`
- Test: `tests/test_ranks_scoring.py`

**Interfaces:**
- Consumes: `ranks.classify.RANK_NAMES`, `ranks.classify.rank_for`
- Produces:
  - `SCORE_ANCHORS: dict[str, float]`
  - `DIVISIONS_PER_TIER: int` = 5, `DIVISION_NUMERALS: list[str]`
  - `defined_tiers(ladder_cs: dict) -> list[str]` — hardest-first, Iron excluded
  - `best_ladder(ladders: dict[str, dict[str, float]]) -> dict[str, int]` — seconds in, centiseconds out
  - `score_for(ladder_cs: dict[str, int], time_cs: int) -> float | None`
  - `tier_from_score(score: float, defined: list[str] | None = None) -> str`
  - `tier_band(tier: str, defined: list[str] | None = None) -> tuple[float, float]`
  - `division_for(score: float, defined: list[str] | None = None) -> tuple[str, str]`
  - `progression_key(tier: str, numeral: str) -> int` — monotone, higher is better
  - `next_tier_target(score: float, defined: list[str] | None = None) -> float`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ranks_scoring.py
from sm64_events.ranks.classify import rank_for
from sm64_events.ranks.scoring import (
    SCORE_ANCHORS, best_ladder, defined_tiers, division_for, next_tier_target,
    progression_key, score_for, tier_band, tier_from_score)

# centiseconds, hardest -> easiest (the SSL "Nuts Pless" ladder)
NUTS = {"Mario": 1293, "Grandmaster": 1303, "Master": 1316, "Diamond": 1336,
        "Platinum": 1416, "Gold": 1566, "Silver": 1676}


def test_score_at_each_cutoff_is_that_tiers_anchor():
    for tier, cs in NUTS.items():
        assert score_for(NUTS, cs) == SCORE_ANCHORS[tier]


def test_score_interpolates_linearly_between_cutoffs():
    # midway between Platinum (1416 -> 60) and Gold (1566 -> 45)
    assert score_for(NUTS, 1491) == 52.5


def test_faster_than_the_hardest_tier_extrapolates_and_caps_at_100():
    assert score_for(NUTS, 1283) > 95.0          # 0.10s under the Mario cutoff
    assert score_for(NUTS, 0) == 100.0           # capped, never above


def test_iron_tail_decays_toward_zero_without_reaching_it():
    slow = score_for(NUTS, 5000)
    assert 0.0 < slow < SCORE_ANCHORS["Silver"]
    assert score_for(NUTS, 50000) < slow         # monotone: slower scores less
    assert score_for(NUTS, 10 ** 9) > 0.0        # asymptotic, never 0


def test_empty_ladder_has_no_score():
    assert score_for({}, 1300) is None


def test_defined_tiers_is_hardest_first_and_drops_iron():
    assert defined_tiers({"Gold": 10, "Mario": 5, "Iron": 99}) == ["Mario", "Gold"]


def test_best_ladder_is_the_pointwise_minimum_over_strategies():
    ladders = {"SS":       {"Mario": 12.93, "Gold": 15.66},
               "Leftside": {"Mario": 13.39, "Gold": 15.10, "Silver": 17.0}}
    assert best_ladder(ladders) == {"Mario": 1293, "Gold": 1510, "Silver": 1700}


def test_tier_from_score_only_names_tiers_the_ladder_defines():
    sparse = {"Grandmaster": 1303, "Diamond": 1336}      # no Master
    defined = defined_tiers(sparse)
    # a time between the two cutoffs interpolates through the 80-90 range;
    # a full-table lookup would wrongly call that "Master".
    # 1310, not 1320: the interpolation crosses the Master anchor (80.0) at
    # exactly 1319.5cs, so a probe just past it lands in Diamond on BOTH
    # lookups and proves nothing. This one sits mid-Master (85.76).
    score = score_for(sparse, 1310)
    assert 70.0 < score < 90.0
    assert tier_from_score(score, defined) == "Diamond"
    assert tier_from_score(score) == "Master"           # full table, for aggregates


def test_score_and_medal_never_disagree():
    """THE invariant (spec section 4.4)."""
    for ladder in (NUTS, {"Grandmaster": 1303, "Diamond": 1336}, {"Gold": 1566}):
        defined = defined_tiers(ladder)
        for time_cs in range(1200, 2400, 7):
            assert tier_from_score(score_for(ladder, time_cs), defined) == \
                rank_for(ladder, time_cs)


def test_tier_band_spans_to_the_next_harder_defined_tier():
    assert tier_band("Gold") == (45.0, 60.0)
    assert tier_band("Mario") == (95.0, 100.0)
    assert tier_band("Iron") == (0.0, 10.0)
    assert tier_band("Diamond", ["Grandmaster", "Diamond"]) == (70.0, 90.0)


def test_divisions_slice_the_band_with_five_at_the_bottom():
    assert division_for(45.0) == ("Gold", "V")
    assert division_for(59.9) == ("Gold", "I")
    assert division_for(48.0) == ("Gold", "IV")
    assert division_for(0.0) == ("Iron", "V")
    assert division_for(100.0) == ("Mario", "I")       # clamped, not out of range


def test_progression_key_is_monotone_across_tiers_and_divisions():
    assert progression_key("Gold", "V") < progression_key("Gold", "I")
    assert progression_key("Gold", "I") < progression_key("Platinum", "V")
    assert progression_key("Iron", "V") == 0


def test_next_tier_target_is_the_harder_anchor_and_100_at_the_top():
    assert next_tier_target(50.0) == 60.0             # Gold -> Platinum
    assert next_tier_target(96.0) == 100.0            # already Mario
    assert next_tier_target(75.0, ["Grandmaster", "Diamond"]) == 90.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ranks_scoring.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.ranks.scoring'`

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/ranks/scoring.py
"""The MARELO 0-100 curve (spec 2026-07-24-marelo-rank-system-design section 4).

Score anchors are sm64-xcams' own player bands, read from their shipped bundle:
Mario >=95, Grandmaster >=90 ... Silver >=25 (they define no Bronze threshold,
so ours is a 10 chosen to leave the Iron tail a decade of its own). Reusing
their exact values is not cosmetic -- it is what makes THE invariant hold:

    tier_from_score(score_for(L, t), defined_tiers(L)) == classify.rank_for(L, t)

i.e. the number and the medal can never disagree, because the score passes
through each anchor exactly at that tier's cutoff time. `defined_tiers` is a
REQUIRED argument for entity-level lookups: a ladder with no Master still
interpolates through the 80-90 range, and a full-table lookup would report
Master for a time rank_for calls Diamond. Aggregates (no ladder) use the full
table by omitting it.

Pure: no I/O, no db, no standards store."""
from sm64_events.ranks.classify import RANK_NAMES

# hardest -> easiest; Iron is the implicit floor and carries NO anchor, exactly
# as it carries no threshold in classify.
SCORE_ANCHORS = {"Mario": 95.0, "Grandmaster": 90.0, "Master": 80.0,
                 "Diamond": 70.0, "Platinum": 60.0, "Gold": 45.0,
                 "Silver": 25.0, "Bronze": 10.0}
TOP_SCORE = 100.0

DIVISIONS_PER_TIER = 5
DIVISION_NUMERALS = ["V", "IV", "III", "II", "I"]   # index 0 = bottom of the tier

_TIERS = [r for r in RANK_NAMES if r != "Iron"]      # hardest -> easiest


def defined_tiers(ladder_cs: dict) -> list[str]:
    """The ladder's tiers, hardest first, Iron excluded. Same order classify
    iterates, so the invariant's two sides walk the ladder identically."""
    return [r for r in _TIERS if r in ladder_cs]


def best_ladder(ladders: dict) -> dict:
    """{strat: {rank: SECONDS}} -> {rank: CENTISECONDS}, pointwise minimum.

    'The best time achievable at this tier by any known strategy' -- which is
    what an entity score must grade against, so that mastering a slow strategy
    maxes the strat score without maxing the star. The min of monotone ladders
    is monotone, so the result is always a valid ladder."""
    out = {}
    for ladder in ladders.values():
        for rank, seconds in ladder.items():
            cs = int(round(seconds * 100))
            if rank not in out or cs < out[rank]:
                out[rank] = cs
    return out


def score_for(ladder_cs: dict, time_cs: int) -> float | None:
    """0..100 for a displayed time against one ladder; None if empty.

    Piecewise linear in TIME through the anchors, so equal time savings inside
    a tier are equal score. Faster than the hardest tier extrapolates that
    tier's slope (capped at 100); slower than the easiest decays asymptotically
    so a bad run trends toward 0 without ever being a zero -- score 0 is
    reserved for 'no time at all', which is the coverage penalty."""
    points = [(ladder_cs[r], SCORE_ANCHORS[r]) for r in defined_tiers(ladder_cs)]
    if not points:
        return None
    hardest_cs, hardest_score = points[0]
    if time_cs <= hardest_cs:
        if len(points) == 1:
            return hardest_score
        next_cs, next_score = points[1]
        slope = (next_score - hardest_score) / (next_cs - hardest_cs)
        return min(TOP_SCORE, hardest_score + slope * (time_cs - hardest_cs))
    for (faster_cs, faster_score), (slower_cs, slower_score) in zip(points, points[1:]):
        if time_cs <= slower_cs:
            span = slower_cs - faster_cs
            if span <= 0:
                return slower_score
            return faster_score + (slower_score - faster_score) * (time_cs - faster_cs) / span
    easiest_cs, easiest_score = points[-1]
    return easiest_score * easiest_cs / time_cs


def tier_from_score(score: float, defined: list[str] | None = None) -> str:
    """Hardest tier in `defined` whose anchor the score reaches; Iron below all.
    Omit `defined` only for aggregates, which have no ladder."""
    for tier in (defined if defined is not None else _TIERS):
        if score >= SCORE_ANCHORS[tier]:
            return tier
    return "Iron"


def tier_band(tier: str, defined: list[str] | None = None) -> tuple[float, float]:
    """(low, high) score range the tier occupies. The top defined tier runs to
    100; Iron runs from 0 up to the easiest defined anchor."""
    present = [r for r in (defined if defined is not None else _TIERS)
               if r in SCORE_ANCHORS]
    if tier == "Iron" or not present:
        return 0.0, (SCORE_ANCHORS[present[-1]] if present else TOP_SCORE)
    index = present.index(tier)
    high = SCORE_ANCHORS[present[index - 1]] if index > 0 else TOP_SCORE
    return SCORE_ANCHORS[tier], high


def division_for(score: float, defined: list[str] | None = None) -> tuple[str, str]:
    """(tier, numeral) -- five equal score-width slices of the tier's band,
    V at the bottom. Band edges come from `defined`, so a division can never
    name a tier the ladder does not define."""
    tier = tier_from_score(score, defined)
    low, high = tier_band(tier, defined)
    span = high - low
    if span <= 0:
        return tier, DIVISION_NUMERALS[-1]
    index = int((score - low) / span * DIVISIONS_PER_TIER)
    return tier, DIVISION_NUMERALS[max(0, min(DIVISIONS_PER_TIER - 1, index))]


def progression_key(tier: str, numeral: str) -> int:
    """Monotone rank position (higher is better) for comparing two ranks --
    THE ordering the celebration watermark stores. Iron V is 0."""
    tier_index = len(RANK_NAMES) - 1 - RANK_NAMES.index(tier)
    return tier_index * DIVISIONS_PER_TIER + DIVISION_NUMERALS.index(numeral)


def next_tier_target(score: float, defined: list[str] | None = None) -> float:
    """The score that reaching the next harder tier requires; 100 at the top,
    so a top-tier entity still shows a remaining gain instead of dropping off
    the 'what should I practice' list."""
    return tier_band(tier_from_score(score, defined), defined)[1]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ranks_scoring.py -q`
Expected: PASS (13 tests)

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ranks/scoring.py tests/test_ranks_scoring.py
git commit -F- <<'MSG'
feat(ranks): score a time 0-100 so the number and the medal agree

xcams' own 0-100 is a leaderboard percentile we cannot reproduce (no player
pool), but their player bands are a score->tier map. Passing our curve through
those exact anchors at each ladder cutoff makes tier_from_score and rank_for
agree by construction, which is why defined_tiers is a required argument: a
ladder with no Master still crosses the 80-90 range, and a full-table lookup
would name a tier the ladder does not have.
MSG
```

---

## Task 2: Pin the invariant against every shipped ladder

**Files:**
- Test: `tests/test_ranks_scoring_seed.py`

**Interfaces:**
- Consumes: `ranks.scoring` (T1), `core.paths.bundled_rank_seed` if present, else the repo path.
- Produces: nothing importable — this is the regression gate.

**Why its own task:** T1's invariant test uses three hand-written ladders. This one runs it against all 278 real strategy ladders in the bundled seed, where malformed and ragged ladders actually live. A reviewer can reject this without rejecting T1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ranks_scoring_seed.py
"""The invariant, against every ladder the app actually ships.

Hand-written ladders in test_ranks_scoring.py are well-formed by construction.
The seed has 278 of them, ragged (missing tiers) and occasionally odd, which is
where a curve/medal disagreement would really appear."""
import json
from pathlib import Path

import pytest

from sm64_events.ranks.classify import rank_for
from sm64_events.ranks.scoring import (
    best_ladder, defined_tiers, score_for, tier_from_score)

SEED = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "data" / \
    "rank_standards.seed.json"


def _ladders():
    entities = json.loads(SEED.read_text())["entities"]
    for key, entity in entities.items():
        for strat, ladder in entity.get("strategies", {}).items():
            cs = {rank: int(round(seconds * 100)) for rank, seconds in ladder.items()}
            if cs:
                yield f"{key}/{strat}", cs


def test_seed_has_ladders_to_check():
    assert sum(1 for _ in _ladders()) > 200


@pytest.mark.parametrize("name,ladder", list(_ladders()))
def test_score_and_medal_agree_at_every_boundary(name, ladder):
    """At each cutoff, one cs either side of it, and across the whole span."""
    probes = set()
    for cutoff in ladder.values():
        probes.update({cutoff - 1, cutoff, cutoff + 1})
    lo, hi = min(ladder.values()), max(ladder.values())
    probes.update(range(max(1, lo - 500), hi + 500, 13))
    defined = defined_tiers(ladder)
    for time_cs in sorted(probes):
        if time_cs <= 0:
            continue
        assert tier_from_score(score_for(ladder, time_cs), defined) == \
            rank_for(ladder, time_cs), f"{name} @ {time_cs}cs"


def test_every_seeded_entity_yields_a_usable_best_ladder():
    entities = json.loads(SEED.read_text())["entities"]
    for key, entity in entities.items():
        strategies = entity.get("strategies", {})
        if not strategies:
            continue
        best = best_ladder(strategies)
        assert best, key
        times = [best[r] for r in defined_tiers(best)]
        assert times == sorted(times), f"{key} best ladder is not monotone"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ranks_scoring_seed.py -q`
Expected: FAIL — the file does not exist yet; after creating it, it must PASS. If any parametrised case fails, the curve is wrong — **fix `scoring.py`, never the assertion**.

- [ ] **Step 3: Run and read the failures**

Run: `uv run pytest tests/test_ranks_scoring_seed.py -q 2>&1 | tail -30`
Expected: PASS. If a ladder is non-monotone in the seed, that is upstream data — record the entity key in the commit message and `pytest.xfail` that single ladder with the key in the reason, do not weaken the invariant.

- [ ] **Step 4: Commit**

```bash
git add tests/test_ranks_scoring_seed.py
git commit -F- <<'MSG'
test(ranks): run the score/medal invariant over all 278 shipped ladders

Hand-written ladders are well-formed by construction; the seed's are ragged.
A curve that disagrees with the medal on a real ladder is the one bug this
whole system cannot survive, so it gets a gate of its own.
MSG
```

---

## Task 3: Scope resolution and aggregation

**Files:**
- Create: `src/sm64_events/ranks/scopes.py`
- Test: `tests/test_ranks_scopes.py`

**Interfaces:**
- Consumes: `ranks.scoring` (T1)
- Produces:
  - `rankable_entities(ladders_by_entity: dict, excluded=()) -> list[str]`
  - `entity_groups(scope_id, *, rankable, routes, segment_courses) -> list[dict] | None` — each group `{"need": int, "candidates": [entity_key]}`
  - `scope_list(*, routes, courses) -> list[dict]` — `{"id","label","kind"}`
  - `aggregate(scores: dict[str, float], groups) -> dict`
  - `gain_for(score: float | None, n: int) -> float`
  - `celebration_delta(tier, numeral, watermark: int | None) -> dict | None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ranks_scopes.py
from sm64_events.ranks.scopes import (
    aggregate, celebration_delta, entity_groups, gain_for, rankable_entities,
    scope_list)
from sm64_events.ranks.scoring import progression_key

LADDERS = {"star:1:0": {"Standard": {"Mario": 45.4}},
           "star:1:1": {"Standard": {"Mario": 30.0}},
           "star:2:0": {"Standard": {"Mario": 20.0}},
           "segment:5": {"Standard": {"Mario": 10.0}},
           "segment:99": {}}                       # has an entry but no ladder

ROUTES = [{"id": 3, "name": "16 Star", "steps": [
    {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 0}]},
    {"need": 1, "candidates": [{"type": "star", "course": 1, "star": 1},
                               {"type": "star", "course": 2, "star": 0}]},
    {"need": 1, "candidates": [{"type": "segment", "segment_id": 42}]},
]}]
SEGMENT_COURSES = {5: 16}


def test_rankable_skips_ladderless_entities_and_exclusions():
    assert rankable_entities(LADDERS) == [
        "star:1:0", "star:1:1", "star:2:0", "segment:5"]
    assert "star:1:1" not in rankable_entities(LADDERS, excluded={"star:1:1"})


def test_overall_scope_is_one_group_per_entity():
    groups = entity_groups("overall", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    assert groups == [{"need": 1, "candidates": [k]}
                      for k in rankable_entities(LADDERS)]


def test_course_scope_takes_its_stars_and_its_segments():
    groups = entity_groups("course:16", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    assert groups == [{"need": 1, "candidates": ["segment:5"]}]
    groups = entity_groups("course:1", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    assert [g["candidates"][0] for g in groups] == ["star:1:0", "star:1:1"]


def test_route_scope_keeps_k_of_n_and_drops_unrankable_candidates():
    groups = entity_groups("route:3", rankable=rankable_entities(LADDERS),
                           routes=ROUTES, segment_courses=SEGMENT_COURSES)
    # step 3's segment 42 has no standards -> the whole step drops
    assert groups == [
        {"need": 1, "candidates": ["star:1:0"]},
        {"need": 1, "candidates": ["star:1:1", "star:2:0"]},
    ]


def test_unknown_scope_is_none():
    assert entity_groups("route:999", rankable=[], routes=ROUTES,
                         segment_courses={}) is None
    assert entity_groups("nonsense", rankable=[], routes=ROUTES,
                         segment_courses={}) is None


def test_scope_list_offers_overall_then_routes_then_courses():
    scopes = scope_list(routes=ROUTES, courses={1: "Bob-omb Battlefield"})
    assert scopes[0] == {"id": "overall", "label": "Overall", "kind": "overall"}
    assert {"id": "route:3", "label": "16 Star", "kind": "route"} in scopes
    assert {"id": "course:1", "label": "Bob-omb Battlefield",
            "kind": "course"} in scopes


def test_aggregate_counts_unpracticed_as_zero_in_the_denominator():
    groups = [{"need": 1, "candidates": ["a"]}, {"need": 1, "candidates": ["b"]}]
    out = aggregate({"a": 60.0}, groups)
    assert out["n"] == 2 and out["practiced"] == 1
    assert out["mastery"] == 60.0
    assert out["coverage"] == 0.5
    assert out["marelo"] == 30.0                     # mastery * coverage


def test_aggregate_takes_the_best_k_of_a_group():
    groups = [{"need": 1, "candidates": ["a", "b"]}]
    out = aggregate({"a": 20.0, "b": 80.0}, groups)
    assert out["n"] == 1 and out["marelo"] == 80.0
    assert [e["key"] for e in out["entities"]] == ["b"]


def test_aggregate_of_an_empty_scope_is_none_not_zero():
    out = aggregate({}, [])
    assert out["marelo"] is None and out["n"] == 0 and out["entities"] == []


def test_aggregate_reports_tier_and_division():
    # Gold spans 45-60 in 5 equal-width divisions (V..I): 50.0 lands in the
    # 48-51 slice, i.e. IV -- pinned independently by test_ranks_scoring.py's
    # division_for(48.0) == ("Gold", "IV").
    out = aggregate({"a": 50.0}, [{"need": 1, "candidates": ["a"]}])
    assert out["tier"] == "Gold" and out["division"] == "IV"


def test_aggregate_reports_progress_through_the_current_division():
    """The header track needs depth into the CURRENT division, and only the
    server knows the band edges -- duplicating the math in JS would let the
    two drift."""
    # Gold spans 45-60, so division III is 51-54: 52.5 is halfway through it.
    out = aggregate({"a": 52.5}, [{"need": 1, "candidates": ["a"]}])
    assert out["next_division_at"] == 54.0
    assert out["division_progress"] == 0.5
    # bottom of a division reads 0, not 1
    assert aggregate({"a": 51.0}, [{"need": 1, "candidates": ["a"]}])[
        "division_progress"] == 0.0


def test_gain_is_the_marelo_the_next_tier_on_this_entity_is_worth():
    assert gain_for(50.0, 10) == (60.0 - 50.0) / 10   # Gold -> Platinum
    assert gain_for(None, 10) == 45.0 / 10            # unpracticed -> reach Gold
    assert gain_for(96.0, 10) == (100.0 - 96.0) / 10  # top tier still has room


def test_celebration_fires_only_on_a_rise_and_reports_the_span():
    below = progression_key("Silver", "II")
    out = celebration_delta("Gold", "V", below)
    assert out["from"] == {"tier": "Silver", "division": "II"}
    assert out["to"] == {"tier": "Gold", "division": "V"}
    assert out["tiers_gained"] == 1
    assert celebration_delta("Silver", "II", below) is None      # unchanged
    assert celebration_delta("Bronze", "I", below) is None        # a drop


def test_first_ever_rank_is_not_a_celebration():
    assert celebration_delta("Gold", "V", None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ranks_scopes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.ranks.scopes'`

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/ranks/scopes.py
"""Scopes and aggregation (spec section 3 and section 5).

A scope is a named SET of rankable entities, and all three kinds are derived --
there is no scope registry to maintain. Every route in the library, including
user-created ones, is therefore automatically a rating with its own history.

Entities are resolved into GROUPS ({"need": k, "candidates": [...]}) rather
than a flat list, because a route's K-of-N step must contribute k slots scored
by its best k candidates -- the same best-K convention tracking/routes.py
already uses for success rates.

Absent vs zero is load-bearing: an entity with no ladder is ABSENT (in neither
numerator nor denominator), while a rankable entity you have not practiced
scores ZERO. Pure: no db, no I/O."""
from sm64_events.ranks import scoring

_UNPRACTICED_TARGET = scoring.SCORE_ANCHORS["Gold"]


def rankable_entities(ladders_by_entity: dict, excluded=()) -> list[str]:
    """Entity keys with at least one ladder, minus the user's exclusions.
    `ladders_by_entity` is {entity_key: {strat: {rank: seconds}}}."""
    excluded = set(excluded or ())
    return [key for key, ladders in ladders_by_entity.items()
            if ladders and key not in excluded]


def _candidate_key(candidate: dict) -> str | None:
    if candidate.get("type") == "segment":
        return f"segment:{candidate['segment_id']}"
    if candidate.get("type") == "star":
        return f"star:{candidate['course']}:{candidate['star']}"
    return None


def entity_groups(scope_id: str, *, rankable, routes, segment_courses):
    """Resolve a scope id into groups, or None if the scope does not exist."""
    rankable = list(rankable)
    ranked = set(rankable)
    if scope_id == "overall":
        return [{"need": 1, "candidates": [key]} for key in rankable]

    kind, _, rest = scope_id.partition(":")
    if kind == "course" and rest.isdigit():
        course = int(rest)
        members = [key for key in rankable
                   if (key.startswith(f"star:{course}:")
                       or (key.startswith("segment:")
                           and segment_courses.get(int(key.split(":")[1])) == course))]
        return [{"need": 1, "candidates": [key]} for key in members]

    if kind == "route" and rest.isdigit():
        route = next((r for r in routes if r["id"] == int(rest)), None)
        if route is None:
            return None
        groups = []
        for step in route.get("steps", []):
            candidates = [key for key in
                          (_candidate_key(c) for c in step.get("candidates", []))
                          if key in ranked]
            if not candidates:
                continue          # nothing rankable here -> the step is absent
            groups.append({"need": min(step.get("need", 1), len(candidates)),
                           "candidates": candidates})
        return groups
    return None


def scope_list(*, routes, courses) -> list[dict]:
    """Every pickable scope, overall first, then routes, then courses."""
    out = [{"id": "overall", "label": "Overall", "kind": "overall"}]
    out += [{"id": f"route:{r['id']}", "label": r["name"], "kind": "route"}
            for r in routes]
    out += [{"id": f"course:{cid}", "label": name, "kind": "course"}
            for cid, name in sorted(courses.items())]
    return out


def aggregate(scores: dict, groups) -> dict:
    """MARELO for one scope. `scores` holds PRACTICED entities only; a member
    missing from it contributes 0 to the numerator and 1 to the denominator --
    that is the coverage penalty, and it is why MARELO == mastery * coverage."""
    total, slots, practiced, entities = 0.0, 0, 0, []
    for group in groups:
        rows = sorted(((scores.get(key), key) for key in group["candidates"]),
                      key=lambda row: -(row[0] if row[0] is not None else -1.0))
        for score, key in rows[:min(group["need"], len(rows))]:
            total += score or 0.0
            slots += 1
            if score is not None:
                practiced += 1
            entities.append({"key": key, "score": score})
    if slots == 0:
        return {"marelo": None, "mastery": None, "coverage": None,
                "tier": None, "division": None, "n": 0, "practiced": 0,
                "entities": []}
    marelo = total / slots
    tier, division = scoring.division_for(marelo)
    for entity in entities:
        entity["gain"] = gain_for(entity["score"], slots)
    next_at, progress = _division_progress(marelo)
    return {"marelo": marelo,
            "mastery": (total / practiced) if practiced else 0.0,
            "coverage": practiced / slots,
            "tier": tier, "division": division,
            "next_division_at": next_at, "division_progress": progress,
            "n": slots, "practiced": practiced, "entities": entities}


def _division_progress(marelo: float) -> tuple[float, float]:
    """(score the next division begins at, 0..1 depth through the current one).

    Computed here, not in the UI: only this side knows the band edges, and a
    second copy of the arithmetic in JS is a drift waiting to happen."""
    tier, _ = scoring.division_for(marelo)
    low, high = scoring.tier_band(tier)
    width = (high - low) / scoring.DIVISIONS_PER_TIER
    if width <= 0:
        return scoring.TOP_SCORE, 1.0
    step = int((marelo - low) / width)
    division_low = low + step * width
    return (min(scoring.TOP_SCORE, division_low + width),
            max(0.0, min(1.0, (marelo - division_low) / width)))


def gain_for(score: float | None, slot_count: int) -> float:
    """The MARELO that reaching this entity's next tier is worth. Unpracticed
    entities target Gold rather than Iron, so they read as the real quests they
    are; a top-tier entity targets 100 so it never drops off the list."""
    if slot_count <= 0:
        return 0.0
    if score is None:
        return _UNPRACTICED_TARGET / slot_count
    return (scoring.next_tier_target(score) - score) / slot_count


def celebration_delta(tier: str, numeral: str, watermark) -> dict | None:
    """A rank-up worth celebrating, or None. Fires ONLY on a rise; a drop is
    handled by the caller lowering the watermark silently, so re-climbing
    celebrates again. A first-ever rank (watermark None) is not a rank-UP."""
    if watermark is None:
        return None
    current = scoring.progression_key(tier, numeral)
    if current <= watermark:
        return None
    was_tier, was_numeral = _from_key(watermark)
    return {"from": {"tier": was_tier, "division": was_numeral},
            "to": {"tier": tier, "division": numeral},
            "tiers_gained": scoring.RANK_NAMES.index(was_tier)
            - scoring.RANK_NAMES.index(tier),
            "key": current}


def _from_key(key: int) -> tuple[str, str]:
    tier_index, division_index = divmod(key, scoring.DIVISIONS_PER_TIER)
    tier = scoring.RANK_NAMES[len(scoring.RANK_NAMES) - 1 - tier_index]
    return tier, scoring.DIVISION_NUMERALS[division_index]
```

Note: `_from_key` needs `scoring.RANK_NAMES`. **Task 1 already added that
`__all__` re-export** — open `ranks/scoring.py`, confirm the block below is
present, and move on. Do NOT add a second one.

```python
__all__ = ["SCORE_ANCHORS", "TOP_SCORE", "DIVISIONS_PER_TIER",
           "DIVISION_NUMERALS", "RANK_NAMES", "defined_tiers", "best_ladder",
           "score_for", "tier_from_score", "tier_band", "division_for",
           "progression_key", "next_tier_target"]
```

This task otherwise creates `ranks/scopes.py` only — `ranks/scoring.py` is
Task 1's file and needs no edit here.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ranks_scopes.py tests/test_ranks_scoring.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ranks/scopes.py src/sm64_events/ranks/scoring.py tests/test_ranks_scopes.py
git commit -F- <<'MSG'
feat(ranks): derive scopes so every route is automatically a rating

A route already IS a set of things to be good at, so 16 Star, 120 Star, every
Stage RTA route and anything the user builds become rated categories with no
authoring and no registry to maintain. Entities resolve into GROUPS rather than
a flat list because a K-of-N step must contribute k slots scored by its best k
candidates, matching routes.py's existing best-K convention.

MARELO = mastery * coverage is one number decomposed, not two: an entity with
no ladder is absent from both halves of the fraction, while a rankable entity
you have not practiced scores zero and still occupies a denominator slot.
MSG
```

---

## Task 4: History replay

**Files:**
- Create: `src/sm64_events/ranks/history.py`
- Test: `tests/test_ranks_history.py`

**Interfaces:**
- Consumes: `ranks.scopes.aggregate` (T3), `ranks.classify.average_frames`, `ranks.classify.RANK_MODES`
- Produces: `history_series(successes, groups, entity_scorer, mode, max_points=300) -> list[dict]` where each point is `{"utc","marelo","tier","division","practiced"}`; `successes` is chronological `[{"utc","key","strat","frames"}]`; `entity_scorer` is `(entity_key, frames) -> float | None`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ranks_history.py
from sm64_events.ranks.history import history_series

GROUPS = [{"need": 1, "candidates": ["a"]}, {"need": 1, "candidates": ["b"]}]


def scorer(key, frames):
    """Fake curve: faster frames score higher, capped at 100."""
    return max(0.0, min(100.0, 6000.0 / frames))


def s(utc, key, frames, strat="Standard"):
    return {"utc": utc, "key": key, "strat": strat, "frames": frames}


def test_one_point_per_success_in_order():
    series = history_series([s("t1", "a", 100), s("t2", "b", 200)],
                            GROUPS, scorer, "pb")
    assert [p["utc"] for p in series] == ["t1", "t2"]


def test_marelo_climbs_as_coverage_and_mastery_grow():
    series = history_series([s("t1", "a", 200), s("t2", "b", 200),
                             s("t3", "a", 100)], GROUPS, scorer, "pb")
    assert series[0]["marelo"] < series[1]["marelo"] < series[2]["marelo"]
    assert series[0]["practiced"] == 1 and series[1]["practiced"] == 2


def test_pb_mode_keeps_the_best_time_not_the_latest():
    series = history_series([s("t1", "a", 100), s("t2", "a", 400)],
                            GROUPS, scorer, "pb")
    assert series[1]["marelo"] == series[0]["marelo"]   # a worse run cannot lower a PB


def test_avg_mode_uses_a_rolling_window_per_strategy():
    runs = [s("t1", "a", 100), s("t2", "a", 300), s("t3", "a", 300)]
    series = history_series(runs, GROUPS, scorer, "avg10")
    # the mean of 100,300 is worse than 100 alone; adding another 300 is worse again
    assert series[0]["marelo"] > series[1]["marelo"] > series[2]["marelo"]


def test_strategies_are_averaged_separately_and_the_best_one_wins():
    runs = [s("t1", "a", 300, "Slow"), s("t2", "a", 100, "Fast"),
            s("t3", "a", 320, "Slow")]
    series = history_series(runs, GROUPS, scorer, "avg10")
    # the Slow strat degrading must not drag down a better Fast average
    assert series[1]["marelo"] == series[2]["marelo"]


def test_points_carry_tier_and_division():
    series = history_series([s("t1", "a", 100)], GROUPS, scorer, "pb")
    assert series[0]["tier"] and series[0]["division"]


def test_successes_outside_the_scope_are_ignored():
    series = history_series([s("t1", "zzz", 100)], GROUPS, scorer, "pb")
    assert series == []


def test_long_histories_are_decimated_but_keep_the_last_point():
    runs = [s(f"t{i}", "a", 100 + i) for i in range(1000)]
    series = history_series(runs, GROUPS, scorer, "pb", max_points=50)
    assert len(series) <= 50
    assert series[-1]["utc"] == "t999"


def test_empty_history_is_empty():
    assert history_series([], GROUPS, scorer, "pb") == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ranks_history.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.ranks.history'`

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/ranks/history.py
"""MARELO over time, recomputed rather than stored (spec section 6).

Score at a moment is a function of the attempts up to it, so a scope's history
is a chronological replay: maintain each (entity, strategy)'s frame list, apply
the active rank mode's window to get that strategy's basis, take the best
strategy per entity, and re-aggregate after every success. No new storage, and
every scope gets its own curve for free.

Two consequences the UI must state rather than hide: history is recomputed
against CURRENT standards (a seed bump reshapes the past), and editing a route
or excluding an entity retroactively rewrites that scope's curve.

Pure: the caller injects `entity_scorer`, so this module never touches the
standards store."""
from sm64_events.ranks import scopes
from sm64_events.ranks.classify import RANK_MODES, average_frames


def history_series(successes, groups, entity_scorer, mode,
                   max_points: int = 300) -> list[dict]:
    """[{utc, marelo, tier, division, practiced}] in chronological order.

    `successes` is [{utc, key, strat, frames}] already in order; anything whose
    key is not in `groups` is ignored, so callers may pass the whole journal."""
    members = {key for group in groups for key in group["candidates"]}
    mode_def = RANK_MODES.get(mode) or RANK_MODES["pb"]
    frames_by_strat: dict[tuple[str, str], list[int]] = {}
    scores: dict[str, float] = {}
    points = []

    for run in successes:
        key = run["key"]
        if key not in members or run["frames"] is None:
            continue
        frames_by_strat.setdefault((key, run["strat"] or ""), []).append(run["frames"])
        best = None
        for (entity, _strat), frames in frames_by_strat.items():
            if entity != key:
                continue
            basis = _basis(frames, mode_def)
            if basis is None:
                continue
            score = entity_scorer(key, basis)
            if score is not None and (best is None or score > best):
                best = score
        if best is None:
            continue
        scores[key] = best
        rolled = scopes.aggregate(scores, groups)
        points.append({"utc": run["utc"], "marelo": rolled["marelo"],
                       "tier": rolled["tier"], "division": rolled["division"],
                       "practiced": rolled["practiced"]})
    return _decimate(points, max_points)


def _basis(frames: list[int], mode_def: dict) -> int | None:
    """The frame count this mode grades for one (entity, strategy)."""
    if mode_def["order"] is None:                      # pb mode: the best ever
        return min(frames) if frames else None
    averaged = average_frames(frames, mode_def["window"], mode_def["order"])
    return averaged[0] if averaged else None


def _decimate(points: list[dict], max_points: int) -> list[dict]:
    """Thin a long series for display, ALWAYS keeping the newest point -- the
    current rank must be the one the chart ends on."""
    if max_points <= 0 or len(points) <= max_points:
        return points
    stride = len(points) / (max_points - 1)
    kept = [points[int(i * stride)] for i in range(max_points - 1)]
    kept.append(points[-1])
    return kept
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ranks_history.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ranks/history.py tests/test_ranks_history.py
git commit -F- <<'MSG'
feat(ranks): recompute rank history instead of storing it

Score at a moment is a pure function of the attempts up to it, and attempts
already carry ended_utc -- so a chronological replay gives every scope its own
curve with no new table, no migration, and no risk of a stored series drifting
from what the live number says. The cost is that history follows current
standards and current route membership; both are documented rather than hidden.
MSG
```

---

## Task 5: The attempts → scores bridge

**Files:**
- Create: `src/sm64_events/tracking/marelo.py`
- Test: `tests/test_marelo_bridge.py`

**Interfaces:**
- Consumes: `ranks.scoring` (T1), `ranks.classify.RANK_MODES`/`average_frames`
- Produces:
  - `entity_ladders(ranks_store, keys) -> dict[str, dict]` — `{entity_key: best_ladder_cs}`
  - `entity_scores(attempts, ranks_store, keys, mode) -> dict[str, float]`
  - `successes_for(attempts, clock_of) -> list[dict]` — the chronological feed `history_series` expects

**Note for the implementer:** `Attempt` is a dataclass in `tracking/projection.py` with fields `course_id, star_id, segment_id, strat_tag, outcome, cleared, igt_frames, rta_frames, ended_utc`. Stars are graded on `igt_frames`, segments on `rta_frames` (`ranks.standards.RankStandards.clock_for`). An `rta_frames == 0` row is reset-race junk and must be skipped — see `tracking/views.py::_valid_frames`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_marelo_bridge.py
from sm64_events.tracking.marelo import (
    entity_ladders, entity_scores, successes_for)
from sm64_events.tracking.projection import Attempt


def att(**over):
    d = dict(id=1, session_id=1, course_id=1, star_id=0, strat_tag="Standard",
             anchor_type="practice_reset", anchor_frame=0, outcome="success",
             outcome_detail=None, igt_frames=1500, rta_frames=1500,
             started_utc="t", ended_utc="t", cleared=False,
             cleared_reason=None, segment_id=None)
    d.update(over)
    return Attempt(**d)


class FakeRanks:
    """Stands in for ranks.standards.RankStandards."""
    def __init__(self, data):
        self._data = data

    def ladders(self, key):
        return self._data.get(key, {})

    def clock_for(self, key):
        return "rta" if key.startswith("segment:") else "igt"


RANKS = FakeRanks({
    "star:1:0": {"Fast": {"Mario": 45.0, "Gold": 60.0},
                 "Slow": {"Mario": 50.0, "Gold": 65.0}},
    "segment:5": {"Standard": {"Mario": 10.0, "Gold": 20.0}}})


def test_entity_ladders_are_the_pointwise_best_across_strategies():
    assert entity_ladders(RANKS, ["star:1:0"]) == {
        "star:1:0": {"Mario": 4500, "Gold": 6000}}


def test_unpracticed_entities_are_absent_from_scores_not_zero():
    scores = entity_scores([], RANKS, ["star:1:0"], "pb")
    assert scores == {}          # absent; aggregate() supplies the zero


def test_a_star_is_graded_on_igt_against_the_best_possible_ladder():
    # 1350 frames -> 45.00s displayed -> exactly the best Mario cutoff
    scores = entity_scores([att(igt_frames=1350)], RANKS, ["star:1:0"], "pb")
    assert scores["star:1:0"] == 95.0


def test_mastering_a_slow_strategy_does_not_max_the_entity():
    # 50.00s is Mario on "Slow" but only ~Gold-ish on the best-possible ladder
    scores = entity_scores([att(igt_frames=1500, strat_tag="Slow")],
                           RANKS, ["star:1:0"], "pb")
    assert scores["star:1:0"] < 95.0


def test_the_best_strategy_wins_the_entity_score():
    runs = [att(id=1, igt_frames=1800, strat_tag="Slow"),
            att(id=2, igt_frames=1350, strat_tag="Fast")]
    assert entity_scores(runs, RANKS, ["star:1:0"], "pb")["star:1:0"] == 95.0


def test_segments_are_graded_on_rta():
    run = att(course_id=None, star_id=None, segment_id=5,
              igt_frames=None, rta_frames=300)
    assert entity_scores([run], RANKS, ["segment:5"], "pb")["segment:5"] == 95.0


def test_cleared_failed_and_untagged_runs_never_score():
    for bad in (att(cleared=True), att(outcome="reset"), att(strat_tag=None)):
        assert entity_scores([bad], RANKS, ["star:1:0"], "pb") == {}


def test_avg_modes_grade_the_window_not_the_pb():
    runs = [att(id=1, igt_frames=1350), att(id=2, igt_frames=1650)]
    pb = entity_scores(runs, RANKS, ["star:1:0"], "pb")["star:1:0"]
    avg = entity_scores(runs, RANKS, ["star:1:0"], "avg10")["star:1:0"]
    assert avg < pb


def test_successes_for_emits_a_chronological_feed_with_entity_keys():
    runs = [att(id=1, ended_utc="t1"),
            att(id=2, ended_utc="t2", course_id=None, star_id=None,
                segment_id=5, rta_frames=300)]
    feed = successes_for(runs, RANKS.clock_for)
    assert [(f["utc"], f["key"], f["frames"]) for f in feed] == [
        ("t1", "star:1:0", 1500), ("t2", "segment:5", 300)]


def test_successes_for_skips_the_rta_zero_reset_race_rows():
    run = att(course_id=None, star_id=None, segment_id=5,
              igt_frames=None, rta_frames=0)
    assert successes_for([run], RANKS.clock_for) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_marelo_bridge.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'sm64_events.tracking.marelo'`

- [ ] **Step 3: Write the implementation**

```python
# src/sm64_events/tracking/marelo.py
"""THE attempts -> per-entity-score bridge for MARELO (spec section 4.6).

The only place that decides which of a user's times an entity is graded on:
per STRATEGY we take the active rank mode's basis (PB row in pb mode, the
window's mean in avg modes), then the entity takes the BEST strategy. Pooling
attempts across strategies before averaging would conflate different skills --
an Avg-10 mixing two strats measures neither.

An entity with no gradeable time is ABSENT from the returned map, never zero:
scopes.aggregate() supplies the zero, because only it knows the denominator."""
from sm64_events.ranks import scoring
from sm64_events.ranks.classify import RANK_MODES, average_frames, display_cs
from sm64_events.ranks.standards import entity_key


def _key_of(attempt) -> str:
    return entity_key(attempt.course_id, attempt.star_id, attempt.segment_id)


def _frames_of(attempt, clock: str):
    frames = attempt.igt_frames if clock == "igt" else attempt.rta_frames
    if frames is None or (clock == "rta" and frames == 0):
        return None          # rta==0 is reset-race junk (projection docstring)
    return frames


def entity_ladders(ranks_store, keys) -> dict:
    """{entity_key: best-possible ladder in centiseconds} for the given keys."""
    out = {}
    for key in keys:
        ladder = scoring.best_ladder(ranks_store.ladders(key))
        if ladder:
            out[key] = ladder
    return out


def entity_scores(attempts, ranks_store, keys, mode: str) -> dict:
    """{entity_key: 0..100} for entities with a gradeable time. Keys with no
    time are omitted -- absent, not zero."""
    ladders = entity_ladders(ranks_store, keys)
    if not ladders:
        return {}
    mode_def = RANK_MODES.get(mode) or RANK_MODES["pb"]
    wanted = set(ladders)
    by_strat: dict[tuple[str, str], list[int]] = {}
    for attempt in attempts:
        if attempt.outcome != "success" or attempt.cleared or not attempt.strat_tag:
            continue
        key = _key_of(attempt)
        if key not in wanted:
            continue
        frames = _frames_of(attempt, ranks_store.clock_for(key))
        if frames is not None:
            by_strat.setdefault((key, attempt.strat_tag), []).append(frames)

    out = {}
    for (key, _strat), frames in by_strat.items():
        basis = (min(frames) if mode_def["order"] is None
                 else (average_frames(frames, mode_def["window"],
                                      mode_def["order"]) or [None])[0])
        if basis is None:
            continue
        score = scoring.score_for(ladders[key], display_cs(basis))
        if score is not None and (key not in out or score > out[key]):
            out[key] = score
    return out


def successes_for(attempts, clock_of) -> list[dict]:
    """The chronological feed ranks.history.history_series consumes.
    `attempts` must already be journal-id ordered (db.attempts() is)."""
    feed = []
    for attempt in attempts:
        if attempt.outcome != "success" or attempt.cleared or not attempt.strat_tag:
            continue
        key = _key_of(attempt)
        frames = _frames_of(attempt, clock_of(key))
        if frames is None:
            continue
        feed.append({"utc": attempt.ended_utc, "key": key,
                     "strat": attempt.strat_tag, "frames": frames})
    return feed
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_marelo_bridge.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/marelo.py tests/test_marelo_bridge.py
git commit -F- <<'MSG'
feat(tracking): one bridge from attempts to per-entity MARELO scores

Per STRATEGY we take the rank mode's basis, then the entity takes the best
strategy -- pooling attempts across strategies before averaging would make an
Avg-10 that mixes two strats measure neither. Entities with no gradeable time
are absent from the map rather than zero, because only the aggregator knows the
denominator that makes a zero meaningful.
MSG
```

---

## Task 6: Views — promote the basis resolver, add the entity medal

**Files:**
- Modify: `src/sm64_events/tracking/views.py`
- Test: `tests/test_views_marelo.py`

**Interfaces:**
- Consumes: `tracking.marelo` (T5), `ranks.scoring` (T1)
- Produces: `views.grading_basis(...)` and `views.valid_frames(...)` (public renames of the `_`-prefixed originals); every star and segment section payload gains `entity_rank`:

```python
{"score": 61.4, "tier": "Platinum", "division": "IV"}   # or None
```

**This task owns every `views.py` edit in the plan.** Do not modify `views.py` in any other task.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_views_marelo.py
"""The section payload's entity-level rank -- the star/segment number that is
graded on the best-possible ladder rather than the active strategy's."""
from sm64_events.tracking import views


class FakeRanks:
    def __init__(self, data):
        self._data = data

    def ladders(self, key):
        return self._data.get(key, {})

    def clock_for(self, key):
        return "rta" if key.startswith("segment:") else "igt"


RANKS = FakeRanks({"star:1:0": {"Fast": {"Mario": 45.0, "Gold": 60.0},
                                "Slow": {"Mario": 50.0, "Gold": 65.0}}})


def test_grading_basis_and_valid_frames_are_public():
    assert callable(views.grading_basis)
    assert callable(views.valid_frames)


def test_entity_rank_grades_the_best_possible_ladder():
    out = views.entity_rank(RANKS, "star:1:0", 1350)     # 45.00s
    assert out["tier"] == "Mario" and out["score"] == 95.0
    assert out["division"]


def test_entity_rank_of_a_slow_strat_time_is_below_mario():
    out = views.entity_rank(RANKS, "star:1:0", 1500)     # 50.00s
    assert out["tier"] != "Mario"


def test_entity_rank_is_none_without_standards_or_without_a_time():
    assert views.entity_rank(RANKS, "star:9:9", 1350) is None
    assert views.entity_rank(RANKS, "star:1:0", None) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_views_marelo.py -q`
Expected: FAIL — `AttributeError: module 'sm64_events.tracking.views' has no attribute 'grading_basis'`

- [ ] **Step 3: Rename the two resolvers**

In `src/sm64_events/tracking/views.py`, rename `_valid_frames` → `valid_frames` and `_grading_basis` → `grading_basis` at their definitions (around lines 225 and 242) and at **every** call site. Find them all first:

```bash
grep -n "_valid_frames\|_grading_basis" src/sm64_events/tracking/views.py
```

Update each hit. They become public because `tracking/marelo.py` and the REST layer need the same "which time does this grade?" answer, and importing an underscore-private across modules hides a real contract. Add to the `grading_basis` docstring:

```
    Public because MARELO grades the same basis (tracking/marelo.py): there is
    exactly ONE answer to "which of my times counts", and it lives here.
```

- [ ] **Step 4: Add the entity-rank helper**

Insert after `_strat_rank` in `views.py`:

```python
def entity_rank(ranks, ek, frames) -> dict | None:
    """The star/segment's OWN rank: the time graded against the entity's
    best-possible ladder (pointwise best across every strategy) rather than
    the active strategy's. THE number MARELO aggregates.

    This is why a mastered slow strategy reads Mario on the left of the
    section header and honestly less on the right: the strat score asks 'how
    well do I run this strat', the entity score asks 'how close is this to the
    fastest this star can be'. None when the entity has no standards or there
    is no time to grade."""
    if ranks is None or frames is None:
        return None
    ladder = scoring.best_ladder(ranks.ladders(ek))
    if not ladder:
        return None
    score = scoring.score_for(ladder, classify.display_cs(frames))
    if score is None:
        return None
    tier, division = scoring.division_for(score, scoring.defined_tiers(ladder))
    return {"score": round(score, 1), "tier": tier, "division": division}
```

Add the import at the top of `views.py`, next to the existing `from sm64_events.ranks import classify`:

```python
from sm64_events.ranks import scoring
```

- [ ] **Step 4b: Add the strategy-level score to the section banner**

`_section_banner` already resolves the active strategy's ladder and the grading basis, so it is the only place that can hand the UI a score for the column the standards table is actually showing. Without it, `ui/components/standards.js` has to re-implement the whole curve in JavaScript — which it currently does (Task 12), including the asymptotic Iron tail we changed on 2026-07-25. Two copies of that algorithm WILL drift.

In `_section_banner`, after `out = classify.band(...)`, add:

```python
    # The score for the ACTIVE strategy's own ladder — the column the
    # standards table renders. Sent so the UI never re-derives the curve:
    # a JS copy of score_for would silently disagree the next time the
    # Python side changes (the Iron tail moved on 2026-07-25).
    out["score"] = scoring.score_for(ladder, classify.display_cs(basis["frames"]))
```

Add a test asserting the banner carries a `score` that matches `scoring.score_for` for the same ladder and basis.

- [ ] **Step 5: Wire it into both section builders**

Today both section builders call `grading_basis(...)` inline inside the `"rank"` value. Hoist that call to a local **before** the dict literal and pass the local to both keys, so the basis is computed once per section.

Star section (near line 557):

```python
        star_basis = grading_basis(
            rank_mode, pbs_by_strat.get((c, s, clock, live_strat)),
            in_section, live_strat, clock)
        ...
            "rank": _section_banner(service.ranks, ek, live_strat,
                                    star_basis, rank_mode),
            "entity_rank": entity_rank(service.ranks, ek,
                                       star_basis and star_basis["frames"]),
```

Segment section (near line 623): identical shape with `seg_basis`, `seg_ek`, the segment's active strat, its history, and `"rta"` as the clock.

Both keys are required — rule 11 (star↔segment parity), pinned by T13's parity test.

- [ ] **Step 6: Run the tests**

Run: `uv run pytest tests/test_views_marelo.py tests/test_views.py tests/test_ui_section_parity.py -q`
Expected: PASS. Then the full suite: `uv run pytest -q` — the rename must not have missed a call site.

- [ ] **Step 7: Commit**

```bash
git add src/sm64_events/tracking/views.py tests/test_views_marelo.py
git commit -F- <<'MSG'
feat(views): give every section its entity-level rank beside the strat rank

Two questions, two numbers: the strat rank asks "how well do I run this strat"
and the entity rank asks "how close is this to the fastest this star can be".
Mastering a slow strategy should max the first and honestly not the second, so
the entity rank grades the pointwise-best ladder across all strategies.

grading_basis/valid_frames lose their underscore because MARELO must grade the
same basis; there is exactly one answer to "which of my times counts" and
importing it as a private across modules would have hidden that contract.
MSG
```

---

## Task 7: Service — exclusions, watermarks, broadcast

**Files:**
- Modify: `src/sm64_events/tracking/service.py`
- Test: `tests/test_service_marelo.py`

**Interfaces:**
- Consumes: `ranks.scopes.celebration_delta` (T3)
- Produces on `TrackerService`:
  - `rank_excluded() -> set[str]`
  - `async set_rank_excluded(entity_key: str, excluded: bool) -> None`
  - `marelo_watermarks() -> dict[str, int]`
  - `async ack_celebration(scope_id: str, key: int) -> None`
  - `sync_watermark(scope_id: str, key: int) -> None` — lowers a watermark silently on a drop, never raises it

**Note:** follow the existing KV pattern in this file (`db.get_state` / `db.set_state` with JSON round-trip, then `await self._broadcast(...)`). Read how `set_rank_mode` does it and mirror it exactly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_service_marelo.py
import json

import pytest


@pytest.mark.asyncio
async def test_exclusion_round_trips_and_broadcasts(service):
    assert service.rank_excluded() == set()
    await service.set_rank_excluded("star:1:0", True)
    assert service.rank_excluded() == {"star:1:0"}
    await service.set_rank_excluded("star:1:0", False)
    assert service.rank_excluded() == set()


@pytest.mark.asyncio
async def test_excluding_twice_is_idempotent(service):
    await service.set_rank_excluded("star:1:0", True)
    await service.set_rank_excluded("star:1:0", True)
    assert service.rank_excluded() == {"star:1:0"}


@pytest.mark.asyncio
async def test_ack_raises_the_watermark(service):
    assert service.marelo_watermarks() == {}
    await service.ack_celebration("overall", 21)
    assert service.marelo_watermarks()["overall"] == 21


@pytest.mark.asyncio
async def test_ack_never_lowers_a_watermark(service):
    await service.ack_celebration("overall", 21)
    await service.ack_celebration("overall", 5)
    assert service.marelo_watermarks()["overall"] == 21


def test_sync_lowers_a_watermark_on_a_drop_so_reclimbing_celebrates(service):
    service.sync_watermark("overall", 21)
    service.sync_watermark("overall", 9)
    assert service.marelo_watermarks()["overall"] == 9


def test_sync_never_raises_a_watermark(service):
    service.sync_watermark("overall", 9)
    service.sync_watermark("overall", 30)
    assert service.marelo_watermarks()["overall"] == 9


def test_sync_on_an_unknown_scope_does_nothing(service):
    service.sync_watermark("route:7", 12)
    assert "route:7" not in service.marelo_watermarks()


def test_seed_writes_a_first_watermark_but_never_overwrites(service):
    """A scope's FIRST rank is not a rank-up: seeding it silently is what
    stops the whole backlog celebrating at once the first time it is viewed."""
    service.seed_watermark("route:7", 12)
    assert service.marelo_watermarks()["route:7"] == 12
    service.seed_watermark("route:7", 30)
    assert service.marelo_watermarks()["route:7"] == 12
```

Add a `service` fixture to `tests/conftest.py` **only if one does not already exist** — check first with `grep -n "def service" tests/conftest.py`. If absent, copy the construction used at the top of `tests/test_service.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_service_marelo.py -q`
Expected: FAIL — `AttributeError: 'TrackerService' object has no attribute 'rank_excluded'`

- [ ] **Step 3: Write the implementation**

Add to `TrackerService` in `src/sm64_events/tracking/service.py`:

```python
    # ---- MARELO: exclusions + celebration watermarks ----
    def rank_excluded(self) -> set:
        """Entity keys the user has taken out of ranking entirely -- they leave
        the numerator AND denominator of every scope. User preference, so a KV
        rather than the standards store (that file is community data)."""
        if self.db is None:
            return set()
        return set(json.loads(self.db.get_state("rank_excluded", "[]")))

    async def set_rank_excluded(self, entity_key: str, excluded: bool) -> None:
        if self.db is None:
            raise RuntimeError("no database attached")
        current = self.rank_excluded()
        current.add(entity_key) if excluded else current.discard(entity_key)
        self.db.set_state("rank_excluded", json.dumps(sorted(current)))
        await self._broadcast({"type": "marelo_changed"})

    def marelo_watermarks(self) -> dict:
        """{scope_id: progression_key} -- the highest rank each scope has been
        SEEN at. Celebrations fire only above it."""
        if self.db is None:
            return {}
        return json.loads(self.db.get_state("marelo_watermarks", "{}"))

    async def ack_celebration(self, scope_id: str, key: int) -> None:
        """Raise a watermark after the UI has actually shown the rank-up.
        Deliberately NOT written when the payload is built: a client that
        fetches and never renders would otherwise swallow the celebration."""
        if self.db is None:
            raise RuntimeError("no database attached")
        marks = self.marelo_watermarks()
        if key > marks.get(scope_id, -1):
            marks[scope_id] = int(key)
            self.db.set_state("marelo_watermarks", json.dumps(marks))

    def sync_watermark(self, scope_id: str, key: int) -> None:
        """Follow a rank DOWN silently, so re-climbing celebrates again. Never
        raises, and never creates a watermark -- only ack_celebration raises,
        only seed_watermark creates."""
        if self.db is None:
            return
        marks = self.marelo_watermarks()
        if scope_id in marks and key < marks[scope_id]:
            marks[scope_id] = int(key)
            self.db.set_state("marelo_watermarks", json.dumps(marks))

    def seed_watermark(self, scope_id: str, key: int) -> None:
        """Record a scope's FIRST observed rank without celebrating it. A
        first rank is not a rank-UP; without this, the first view of any scope
        would fire a celebration for every tier the user ever earned."""
        if self.db is None:
            return
        marks = self.marelo_watermarks()
        if scope_id not in marks:
            marks[scope_id] = int(key)
            self.db.set_state("marelo_watermarks", json.dumps(marks))
```

If `import json` is not already at the top of `service.py`, add it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_service_marelo.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/tracking/service.py tests/test_service_marelo.py tests/conftest.py
git commit -F- <<'MSG'
feat(tracking): store rank exclusions and celebration watermarks as KVs

Exclusions are user preference, not community data, so they belong in ui_state
rather than the standards file the seed reconcile overwrites.

The watermark is raised only on ACK, never when the payload is built: a client
that fetches and never renders would otherwise swallow a rank-up. It follows a
rank DOWN silently so re-climbing celebrates again, and because it is stored
rather than derived, a journal re-projection cannot replay the whole history of
rank-ups at once.
MSG
```

---

## Task 8: REST surface

**Files:**
- Modify: `src/sm64_events/server/ranks_api.py`
- Test: `tests/test_ranks_api_marelo.py`

**Interfaces:**
- Consumes: T3, T4, T5, T7
- Produces:
  - `GET /api/marelo?scope=<id>` → `{scope_id, label, marelo, mastery, coverage, tier, division, next_division_at, n, practiced, entities:[{key,label,score,tier,division,gain,excluded}], celebration}`
  - `GET /api/marelo/history?scope=<id>` → `{scope_id, points:[{utc,marelo,tier,division}]}`
  - `GET /api/marelo/scopes` → `{scopes:[{id,label,kind}], active}`
  - `POST /api/marelo/exclude` body `{entity, excluded}` → `{"ok": true}`
  - `POST /api/marelo/ack` body `{scope, key}` → `{"ok": true}`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_ranks_api_marelo.py
def test_scopes_lists_overall_first(client):
    body = client.get("/api/marelo/scopes").json()
    assert body["scopes"][0]["id"] == "overall"
    assert body["active"] in {s["id"] for s in body["scopes"]}


def test_marelo_defaults_to_overall(client):
    body = client.get("/api/marelo").json()
    assert body["scope_id"] == "overall"
    assert body["n"] >= 0
    assert set(body) >= {"marelo", "mastery", "coverage", "tier", "division",
                         "entities", "celebration"}


def test_unknown_scope_is_404(client):
    assert client.get("/api/marelo?scope=route:999999").status_code == 404
    assert client.get("/api/marelo?scope=garbage").status_code == 404


def test_entities_carry_a_display_label_and_exclusion_state(client):
    body = client.get("/api/marelo").json()
    if body["entities"]:
        entity = body["entities"][0]
        assert set(entity) >= {"key", "label", "score", "gain", "excluded"}
        assert isinstance(entity["label"], str) and entity["label"]


def test_exclusion_removes_an_entity_from_the_denominator(client):
    before = client.get("/api/marelo").json()
    if not before["entities"]:
        return
    key = before["entities"][0]["key"]
    assert client.post("/api/marelo/exclude",
                       json={"entity": key, "excluded": True}).status_code == 200
    after = client.get("/api/marelo").json()
    assert after["n"] == before["n"] - 1
    assert key not in {e["key"] for e in after["entities"]}
    client.post("/api/marelo/exclude", json={"entity": key, "excluded": False})


def test_history_returns_points_for_a_valid_scope(client):
    body = client.get("/api/marelo/history?scope=overall").json()
    assert body["scope_id"] == "overall"
    assert isinstance(body["points"], list)


def test_history_of_an_unknown_scope_is_404(client):
    assert client.get("/api/marelo/history?scope=route:999999").status_code == 404


def test_ack_is_accepted(client):
    assert client.post("/api/marelo/ack",
                       json={"scope": "overall", "key": 3}).status_code == 200
```

Reuse the existing `client` fixture — check `grep -rn "def client" tests/conftest.py tests/test_api.py` and copy whichever construction the rank tests already use.

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_ranks_api_marelo.py -q`
Expected: FAIL — 404 on `/api/marelo/scopes` (route not registered)

- [ ] **Step 3: Write the implementation**

Add to `src/sm64_events/server/ranks_api.py`, inside `create_ranks_router`, before `return router`:

```python
    @router.get("/marelo/scopes")
    def marelo_scopes():
        if service.ranks is None or service.db is None:
            raise HTTPException(503, "rank standards unavailable")
        return {"scopes": scopes.scope_list(routes=service.db.routes(),
                                            courses=COURSE_NAMES),
                "active": _active_scope(service)}

    @router.get("/marelo")
    def marelo(scope: str | None = None):
        return _build_marelo(service, scope or _active_scope(service))

    @router.get("/marelo/history")
    def marelo_history(scope: str | None = None):
        scope_id = scope or _active_scope(service)
        groups = _groups(service, scope_id)
        mode = _rank_mode(service)
        keys = [key for group in groups for key in group["candidates"]]
        ladders = marelo_bridge.entity_ladders(service.ranks, keys)

        def scorer(key, frames):
            ladder = ladders.get(key)
            return None if ladder is None else scoring.score_for(
                ladder, classify.display_cs(frames))

        feed = marelo_bridge.successes_for(service.db.attempts(),
                                           service.ranks.clock_for)
        return {"scope_id": scope_id,
                "points": history.history_series(feed, groups, scorer, mode)}

    @router.post("/marelo/exclude")
    async def marelo_exclude(body: ExcludeBody):
        try:
            await service.set_rank_excluded(body.entity, body.excluded)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/marelo/ack")
    async def marelo_ack(body: AckBody):
        try:
            await service.ack_celebration(body.scope, body.key)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}
```

Add above `create_ranks_router`:

```python
class ExcludeBody(BaseModel):
    entity: str
    excluded: bool


class AckBody(BaseModel):
    scope: str
    key: int


def _active_scope(service) -> str:
    """The focus route IS the scope (spec section 3.4) -- there is no second
    control. No route selected means Overall."""
    active = service.active_route()
    return f"route:{active['id']}" if active else "overall"


def _rank_mode(service) -> str:
    mode = service.db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    return mode if mode in classify.RANK_MODES else classify.DEFAULT_RANK_MODE


def _groups(service, scope_id: str):
    """Resolve a scope or 404. Segment->course comes from each definition's
    start levels, the same source the stage banner uses."""
    if service.ranks is None or service.db is None:
        raise HTTPException(503, "rank standards unavailable")
    ladders = {key: service.ranks.ladders(key)
               for key in service.ranks.to_json()["entities"]}
    rankable = scopes.rankable_entities(ladders, service.rank_excluded())
    groups = scopes.entity_groups(
        scope_id, rankable=rankable, routes=service.db.routes(),
        segment_courses=segment_courses(service.db))
    if groups is None:
        raise HTTPException(404, f"unknown scope {scope_id!r}")
    return groups


def _build_marelo(service, scope_id: str) -> dict:
    groups = _groups(service, scope_id)
    keys = [key for group in groups for key in group["candidates"]]
    scored = marelo_bridge.entity_scores(service.db.attempts(), service.ranks,
                                         keys, _rank_mode(service))
    out = scopes.aggregate(scored, groups)
    excluded = service.rank_excluded()
    for entity in out["entities"]:
        entity["label"] = entity_label(service.db, entity["key"])
        entity["excluded"] = entity["key"] in excluded
        if entity["score"] is None:
            entity["tier"] = entity["division"] = None
        else:
            entity["tier"], entity["division"] = scoring.division_for(
                entity["score"])
    out["scope_id"] = scope_id
    out["label"] = _scope_label(service, scope_id)
    out["celebration"] = None
    if out["tier"]:
        key = scoring.progression_key(out["tier"], out["division"])
        service.sync_watermark(scope_id, key)          # follow a drop down
        out["celebration"] = scopes.celebration_delta(
            out["tier"], out["division"],
            service.marelo_watermarks().get(scope_id))
        # A scope's FIRST rank is not a rank-up. Seeding it silently is what
        # stops the first view of a scope celebrating the user's whole
        # history at once. seed_watermark is a no-op once the key exists.
        service.seed_watermark(scope_id, key)
    return out


def _scope_label(service, scope_id: str) -> str:
    for scope in scopes.scope_list(routes=service.db.routes(),
                                   courses=COURSE_NAMES):
        if scope["id"] == scope_id:
            return scope["label"]
    return scope_id
```

Add the imports at the top of `ranks_api.py`:

```python
from sm64_events.memory.addresses import COURSE_NAMES
from sm64_events.ranks import classify, history, scopes, scoring
from sm64_events.tracking import marelo as marelo_bridge
from sm64_events.tracking.views import entity_label, segment_courses
```

`entity_label` and `segment_courses` do not exist yet — add both to `tracking/views.py` (this task may touch `views.py` **only** to add these two functions; T6 owns everything else there):

```python
def segment_courses(db) -> dict:
    """{segment_id: course_id} from each definition's start levels -- the same
    resolution the stage quick-select banner uses, so a segment lands in the
    course scope the user practices it from. A castle-interior segment (LBLJ,
    MIPS clip) maps to no course and is simply absent: it belongs to `overall`
    and to routes, never to a course scope (spec section 3.3)."""
    from sm64_events.memory.addresses import COURSE_BY_LEVEL
    out = {}
    for d in db.segment_defs():
        for level in _segment_start_levels(d["start_triggers"]):
            course = COURSE_BY_LEVEL.get(level)
            if course is not None:
                out[d["id"]] = course
                break
    return out


def entity_label(db, ek: str) -> str:
    """Human name for an entity key, for the MARELO breakdown list."""
    from sm64_events.memory.addresses import COURSE_NAMES, STAR_NAMES
    kind, _, rest = ek.partition(":")
    if kind == "segment":
        name = next((d["name"] for d in db.segment_defs()
                     if str(d["id"]) == rest), None)
        return name or f"segment {rest}"
    course, _, star = rest.partition(":")
    cid, sid = int(course), int(star)
    return f"{COURSE_NAMES.get(cid, cid)} — " \
           f"{STAR_NAMES.get(cid, {}).get(sid) or f'star {sid + 1}'}"
```

**Verified shapes** (probe again if anything fails):
`COURSE_BY_LEVEL` is `{level_id: course_id}` — note the name, it is not `LEVEL_COURSES`.
`_segment_start_levels` takes the **triggers list**, not the def dict, and `db.segment_defs()` returns dicts whose `start_triggers` is already JSON-decoded.
`STAR_NAMES` is `{course_id: {star_id: name}}` — confirm with:

```bash
uv run python -c "from sm64_events.memory.addresses import STAR_NAMES as S; print(S[1])"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_ranks_api_marelo.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/sm64_events/server/ranks_api.py src/sm64_events/tracking/views.py tests/test_ranks_api_marelo.py
git commit -F- <<'MSG'
feat(api): serve MARELO per scope, with history and exclusions

The focus route IS the scope, so /api/marelo with no argument answers for
whatever the user is practicing and there is no second control to keep in sync.
An unknown scope 404s rather than silently falling back to Overall -- a stale
route id in a client must read as gone, not as a different rating.

Celebrations are computed here but the watermark is only SYNCED down; raising
it needs an explicit ack, so a fetch that never renders cannot swallow a rank-up.
MSG
```

---

## Task 9: Crest and header bar

**Files:**
- Create: `src/sm64_events/ui/components/marelo.js`
- Modify: `src/sm64_events/ui/components/header.js`

**Interfaces:**
- Consumes: `GET /api/marelo` (T8), `ui/components/ranks.js` `rankColor`
- Produces: `Crest({tier, division, size})`, `MareloBar({marelo, onOpen})`, `fmtScore(n)`

- [ ] **Step 1: Write the component**

```javascript
// src/sm64_events/ui/components/marelo.js — MARELO crest + header bar.
// Mirrors ranks/scoring.py's division numerals; the tier palette is
// ranks.js RANK_COLORS (one registry, mirrored once).
import { h } from "preact";
import htm from "htm";
import { rankColor } from "./ranks.js";
const html = htm.bind(h);

export const fmtScore = (n) => (n == null ? "–" : n.toFixed(1));

// A crest, not a medal: the section medals are per-strat and per-entity, and
// an aggregate that looked identical to them would read as "just another
// star's rank" in the header.
export function Crest({ tier, division, size = 34 }) {
  const c = rankColor(tier);
  return html`<span class="marelo-crest" title=${tier ? `${tier} ${division}` : "unranked"}
      style=${`--crest:${c};width:${size}px;height:${size}px`}>
    <b style=${`font-size:${Math.round(size * 0.34)}px`}>${division || "–"}</b>
  </span>`;
}

export function MareloBar({ marelo, onOpen }) {
  if (!marelo) return null;
  const { tier, division, label, mastery, coverage, n, practiced } = marelo;
  const score = marelo.marelo;
  // Endowed progress (spec section 2.4): the track shows how far into the
  // CURRENT division you are, so there is a near goal even at Iron V. The
  // server computes it (it owns the band edges) -- do not re-derive it here.
  const fill = Math.round((marelo.division_progress || 0) * 100);
  return html`<button type="button" class="marelo-bar" onclick=${onOpen}
      title=${`${label}: mastery ${fmtScore(mastery)} x coverage ${practiced}/${n}`}>
    <${Crest} tier=${tier} division=${division} />
    <span class="marelo-bar-text">
      <b>${tier ? `${tier} ${division}` : "Unranked"}</b>
      <span class="meta">${label} · ${fmtScore(score)}</span>
    </span>
    <span class="marelo-track"><i style=${`width:${fill}%;background:${rankColor(tier)}`}></i></span>
    <span class="meta marelo-split">M ${fmtScore(mastery)} · C ${
      n ? Math.round((coverage || 0) * 100) : 0}%</span>
  </button>`;
}
```

- [ ] **Step 2: Add the CSS**

In `src/sm64_events/ui/index.html`, inside the design-system CSS block, after the `.rank-progress-track` rules:

```css
.marelo-bar{display:flex;align-items:center;gap:10px;height:44px;padding:0 12px;
  border:1px solid var(--line);border-radius:10px;background:var(--panel);
  cursor:pointer;flex:0 0 auto}
.marelo-bar:hover{border-color:var(--gold)}
.marelo-crest{display:inline-flex;align-items:center;justify-content:center;
  border-radius:6px;background:var(--crest);color:#0d1220;flex:0 0 auto;
  box-shadow:0 0 0 2px rgba(255,255,255,.18) inset}
.marelo-bar-text{display:flex;flex-direction:column;line-height:1.15;text-align:left}
.marelo-track{width:96px;height:6px;border-radius:3px;background:rgba(255,255,255,.12);
  overflow:hidden;flex:0 0 auto}
.marelo-track>i{display:block;height:100%}
.marelo-split{white-space:nowrap}
@media (max-width:900px){.marelo-track,.marelo-split{display:none}}
```

**Fixed 44px height is deliberate** — the header must not reflow while OBS is capturing it (project design rule).

- [ ] **Step 3: Mount it in the header**

In `src/sm64_events/ui/components/header.js`, import `MareloBar`, fetch `/api/marelo` on mount and on the `marelo_changed` / `attempt_completed` / `rank_mode_changed` / `route_selected` events, and render `<${MareloBar} marelo=${marelo} onOpen=${() => setTab("Rank")} />` in the control bar. `setTab` must be threaded from `app.js` (T14 does that wiring — until then, pass a no-op and the bar still renders).

- [ ] **Step 4: Verify by rendering**

Unit tests plus `node --check` once shipped an invisible feature in this project, so a render is mandatory. Build a harness page per the `verify-ui-effects-with-harness-page` technique:

```bash
node --check src/sm64_events/ui/components/marelo.js
```

then mount `MareloBar` in a throwaway `src/sm64_events/ui/_harness_marelo.html` with a stubbed `fetch` returning a fixed payload and the real `index.html` CSS, serve it on a free port with `python -m http.server`, and screenshot:

```bash
chrome --headless=new --screenshot=marelo.png --window-size=1280,200 http://127.0.0.1:<port>/ui/_harness_marelo.html
```

Check stderr for `CONSOLE` errors. **Kill the server in the same session** and delete the harness file before committing (dev-process rule: no orphaned processes).

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/marelo.js src/sm64_events/ui/components/header.js src/sm64_events/ui/index.html
git commit -F- <<'MSG'
feat(ui): always-visible MARELO bar in the header

A crest rather than a medal: the section medals are per-strat and per-entity,
and an aggregate that looked identical would read as just another star's rank.
The track shows depth into the CURRENT division, not distance to the next tier,
so there is a near goal even at Iron V. Fixed 44px height so a rank-up cannot
reflow a layout somebody is capturing in OBS.
MSG
```

---

## Task 10: The Rank tab

**Files:**
- Create: `src/sm64_events/ui/components/rankpage.js`

**Interfaces:**
- Consumes: `GET /api/marelo`, `/api/marelo/history`, `/api/marelo/scopes` (T8); `Crest`/`fmtScore` (T9); `Medal`/`rankColor` (`ranks.js`)
- Produces: `RankPage({ t })` — default export mounted by `app.js` in T14

- [ ] **Step 1: Write the component**

```javascript
// src/sm64_events/ui/components/rankpage.js — the Rank tab: scope picker,
// rank card, history chart, and the per-entity breakdown (which IS the route
// performance view when the scope is a route).
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { getJSON, send } from "../api.js";
import { rankColor, RANK_NAMES } from "./ranks.js";
import { Crest, fmtScore } from "./marelo.js";
const html = htm.bind(h);

const ANCHORS = { Mario: 95, Grandmaster: 90, Master: 80, Diamond: 70,
  Platinum: 60, Gold: 45, Silver: 25, Bronze: 10, Iron: 0 };

function HistoryChart({ points }) {
  if (!points || points.length < 2)
    return html`<p class="meta">Not enough history yet — finish a few more runs.</p>`;
  const W = 720, H = 220;
  const xs = (i) => (i / (points.length - 1)) * W;
  const ys = (v) => H - (Math.max(0, Math.min(100, v)) / 100) * H;
  const line = points.map((p, i) => `${i ? "L" : "M"}${xs(i).toFixed(1)},${ys(p.marelo).toFixed(1)}`).join(" ");
  return html`<svg class="rank-chart" viewBox=${`0 0 ${W} ${H}`} role="img"
      aria-label="MARELO over time">
    ${RANK_NAMES.map((tier) => html`<g>
      <line x1="0" x2=${W} y1=${ys(ANCHORS[tier])} y2=${ys(ANCHORS[tier])}
        stroke=${rankColor(tier)} stroke-opacity=".28" stroke-dasharray="3 4" />
      <text x="4" y=${ys(ANCHORS[tier]) - 3} fill=${rankColor(tier)}
        font-size="9">${tier}</text></g>`)}
    <path d=${line} fill="none" stroke="var(--gold)" stroke-width="2" />
  </svg>`;
}

function Breakdown({ data, routeOrder, onToggle }) {
  const [byGain, setByGain] = useState(!routeOrder);
  const rows = byGain
    ? [...data.entities].sort((a, b) => b.gain - a.gain)
    : data.entities;
  return html`<div class="rank-breakdown">
    <div class="rank-breakdown-head">
      <b>${routeOrder ? "Route order" : "Everything in scope"}</b>
      <button type="button" class="chip" onclick=${() => setByGain(!byGain)}>
        ${byGain ? "Sort: biggest gain" : "Sort: route order"}</button>
    </div>
    <table class="rank-table"><tbody>
      ${rows.map((e) => html`<tr class=${e.score == null ? "unpracticed" : ""}>
        <td class="rank-cell-name">${e.label}</td>
        <td>${e.tier ? html`<${Crest} tier=${e.tier} division=${e.division} size=${22} />` : "–"}</td>
        <td class="meta">${fmtScore(e.score)}</td>
        <td class="meta rank-cell-gain">+${e.gain.toFixed(2)}</td>
        <td><button type="button" class="chip"
          onclick=${() => onToggle(e.key, !e.excluded)}
          title="Exclude this from every rating">${e.excluded ? "Include" : "Ignore"}</button></td>
      </tr>`)}
    </tbody></table>
  </div>`;
}

export function RankPage() {
  const [scopes, setScopes] = useState(null);
  const [scopeId, setScopeId] = useState(null);
  const [data, setData] = useState(null);
  const [points, setPoints] = useState([]);
  const [err, setErr] = useState(null);

  useEffect(() => {
    getJSON("/api/marelo/scopes").then((s) => {
      setScopes(s.scopes);
      // The focus route IS the scope (spec 3.4): follow it until the user
      // deliberately browses elsewhere.
      setScopeId((cur) => cur ?? s.active);
    }).catch((e) => setErr(e.message));
  }, []);

  useEffect(() => {
    if (!scopeId) return;
    const q = `?scope=${encodeURIComponent(scopeId)}`;
    getJSON(`/api/marelo${q}`).then(setData).catch((e) => setErr(e.message));
    getJSON(`/api/marelo/history${q}`).then((h) => setPoints(h.points))
      .catch(() => setPoints([]));
  }, [scopeId]);

  const toggle = async (key, excluded) => {
    await send("POST", "/api/marelo/exclude", { entity: key, excluded });
    const q = `?scope=${encodeURIComponent(scopeId)}`;
    setData(await getJSON(`/api/marelo${q}`));
  };

  if (err) return html`<p class="error">${err}</p>`;
  if (!data || !scopes) return html`<p class="meta">Loading…</p>`;
  const pct = Math.round((data.coverage || 0) * 100);
  return html`<div class="rank-page">
    <div class="card rank-card">
      <select value=${scopeId} onchange=${(e) => setScopeId(e.target.value)}>
        ${scopes.map((s) => html`<option value=${s.id}>${s.label}</option>`)}
      </select>
      <div class="rank-card-main">
        <${Crest} tier=${data.tier} division=${data.division} size=${64} />
        <div>
          <h2>${data.tier ? `${data.tier} ${data.division}` : "Unranked"}</h2>
          <p class="meta">MARELO ${fmtScore(data.marelo)} · next division at
            ${fmtScore(data.next_division_at)}</p>
        </div>
      </div>
      <div class="rank-factors">
        <label>Mastery <i style=${`width:${data.mastery || 0}%`}></i>
          <span class="meta">${fmtScore(data.mastery)} over ${data.practiced} practiced</span></label>
        <label>Coverage <i style=${`width:${pct}%`}></i>
          <span class="meta">${data.practiced}/${data.n}</span></label>
      </div>
      ${data.n < 5 && html`<p class="meta">Small scope — ${data.n} rated ${
        data.n === 1 ? "entry" : "entries"}.</p>`}
    </div>
    <div class="card">
      <h3>Progress</h3>
      <${HistoryChart} points=${points} />
      <p class="meta">Recomputed from your attempts against current standards —
        editing this route or ignoring an entry rewrites the curve.</p>
    </div>
    <div class="card">
      <${Breakdown} data=${data} routeOrder=${scopeId.startsWith("route:")}
        onToggle=${toggle} />
    </div>
  </div>`;
}
```

- [ ] **Step 2: Add the CSS**

Append to the design-system block in `index.html`:

```css
.rank-page{display:flex;flex-direction:column;gap:14px}
.rank-card-main{display:flex;align-items:center;gap:16px;margin:10px 0}
.rank-card-main h2{margin:0;font-size:24px}
.rank-factors label{display:block;margin:6px 0;font-size:12px;color:var(--dim)}
.rank-factors i{display:block;height:8px;border-radius:4px;background:var(--gold);
  margin:3px 0;max-width:100%}
.rank-chart{width:100%;height:auto;max-height:240px}
.rank-breakdown-head{display:flex;justify-content:space-between;align-items:center;
  margin-bottom:8px}
.rank-table{width:100%;border-collapse:collapse}
.rank-table td{padding:5px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
.rank-table tr.unpracticed .rank-cell-name{opacity:.55}
.rank-cell-gain{color:var(--gold);text-align:right;white-space:nowrap}
```

- [ ] **Step 3: Verify by rendering**

```bash
node --check src/sm64_events/ui/components/rankpage.js
```

then the harness-page + headless-Chrome screenshot from Task 9 Step 4, with a stubbed `fetch` returning a scope list, a 30-point history, and ~20 breakdown rows including at least one unpracticed and one excluded. Confirm the chart draws tier bands, the breakdown sorts both ways, and nothing overflows at 900px and 1400px widths. Kill the server; delete the harness.

- [ ] **Step 4: Commit**

```bash
git add src/sm64_events/ui/components/rankpage.js src/sm64_events/ui/index.html
git commit -F- <<'MSG'
feat(ui): Rank tab — one scoped view instead of three screens

Route performance, per-course averages and overall progress are the same view
under different scopes, so they share one component and one scope picker rather
than three near-duplicate pages. A route scope defaults to route order (the
weak steps are the point); everything else defaults to biggest-gain-first,
which turns unpracticed entries into a practice queue instead of dead weight.

The chart's caption states that history follows current standards and route
membership -- derived data that silently rewrites itself is worse than derived
data that says so.
MSG
```

---

## Task 11: Rank-up celebration

**Files:**
- Create: `src/sm64_events/ui/components/celebrate.js`

**Interfaces:**
- Consumes: `marelo.celebration` from `GET /api/marelo` (T8); `POST /api/marelo/ack`
- Produces: `RankUpOverlay({ celebration, scopeId, onDone })`, `celebrationsEnabled()`

- [ ] **Step 1: Write the component**

```javascript
// src/sm64_events/ui/components/celebrate.js — the rank-up overlay.
// Server decides WHETHER (it owns the watermark); this decides how it looks
// and acks when the user has actually seen it.
import { h } from "preact";
import { useEffect, useState } from "preact/hooks";
import htm from "htm";
import { send } from "../api.js";
import { RANK_NAMES, rankColor } from "./ranks.js";
import { Crest } from "./marelo.js";
const html = htm.bind(h);

const PREF = "sm64.celebrate";
export const celebrationsEnabled = () => localStorage.getItem(PREF) !== "0";
export const setCelebrationsEnabled = (on) =>
  localStorage.setItem(PREF, on ? "1" : "0");

const STEP_MS = 850;

export function RankUpOverlay({ celebration, scopeId, onDone }) {
  // Every tier between old and new, so a multi-tier jump is climbed rather
  // than teleported -- the climb is the reward.
  const [step, setStep] = useState(0);
  const climb = celebration ? RANK_NAMES.slice(
    RANK_NAMES.indexOf(celebration.to.tier),
    RANK_NAMES.indexOf(celebration.from.tier) + 1).reverse() : [];

  useEffect(() => {
    if (!celebration) return undefined;
    if (step >= climb.length - 1) {
      const done = setTimeout(finish, 1600);
      return () => clearTimeout(done);
    }
    const next = setTimeout(() => setStep((s) => s + 1), STEP_MS);
    return () => clearTimeout(next);
  }, [celebration, step]);

  async function finish() {
    try { await send("POST", "/api/marelo/ack", { scope: scopeId, key: celebration.key }); }
    finally { onDone(); }
  }

  if (!celebration || !celebrationsEnabled()) {
    if (celebration) finish();          // acked without showing: pref is off
    return null;
  }
  const tier = climb[step] || celebration.to.tier;
  const last = step >= climb.length - 1;
  return html`<div class="rankup" role="status" onclick=${finish}
      style=${`--tier:${rankColor(tier)}`}>
    <div class=${`rankup-card ${last ? "final" : ""}`}>
      <span class="meta">RANK UP</span>
      <${Crest} tier=${tier} division=${last ? celebration.to.division : "I"} size=${96} />
      <h2>${tier}${last ? ` ${celebration.to.division}` : ""}</h2>
      <span class="meta">click to dismiss</span>
    </div>
  </div>`;
}
```

- [ ] **Step 2: Add the CSS**

```css
/* Fixed overlay, pointer-events only on the card: a celebration must never
   swallow a click meant for the game or reflow an OBS capture. */
.rankup{position:fixed;inset:0;display:flex;align-items:center;justify-content:center;
  z-index:60;background:radial-gradient(closest-side,color-mix(in srgb,var(--tier) 35%,transparent),transparent 70%);
  animation:rankup-in .25s ease-out}
.rankup-card{pointer-events:auto;text-align:center;padding:26px 34px;border-radius:16px;
  background:var(--panel);border:2px solid var(--tier);
  box-shadow:0 0 60px color-mix(in srgb,var(--tier) 55%,transparent)}
.rankup-card h2{margin:8px 0 2px;font-size:30px;color:var(--tier)}
.rankup-card.final{animation:rankup-pop .5s cubic-bezier(.2,1.6,.4,1)}
@keyframes rankup-in{from{opacity:0}to{opacity:1}}
@keyframes rankup-pop{from{transform:scale(.86)}to{transform:scale(1)}}
@media (prefers-reduced-motion:reduce){
  .rankup,.rankup-card.final{animation:none}}
```

- [ ] **Step 3: Add the settings toggle**

In `header.js`'s settings drawer, next to the existing "Star icons" control, add a checkbox bound to `celebrationsEnabled()` / `setCelebrationsEnabled()` labelled "Celebrate rank-ups".

- [ ] **Step 4: Verify by rendering**

```bash
node --check src/sm64_events/ui/components/celebrate.js
```

Harness page mounting `RankUpOverlay` with a **three-tier** jump (`from: Bronze I`, `to: Diamond V`) and a stubbed `send`. Screenshot at 0 ms, 900 ms and 2600 ms; confirm the crest climbs Bronze→Silver→Gold→Platinum→Diamond and the final card pops. Confirm `prefers-reduced-motion` kills the animation:

```bash
chrome --headless=new --force-prefers-reduced-motion --screenshot=rankup-rm.png http://127.0.0.1:<port>/ui/_harness_rankup.html
```

Kill the server; delete the harness.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/celebrate.js src/sm64_events/ui/components/header.js src/sm64_events/ui/index.html
git commit -F- <<'MSG'
feat(ui): climb through every tier gained instead of teleporting to the new one

A three-tier jump that snaps straight to the result throws away the part that
feels earned, so the crest walks Bronze->Silver->Gold->Platinum->Diamond one
step at a time and only the last card pops.

The ack fires when the overlay is dismissed, not when the payload arrives, so
a client that never renders cannot swallow the rank-up. Pointer events stay on
the card alone and the overlay is fixed-position -- it must never eat a click
meant for the game or reflow a capture. Honours prefers-reduced-motion; the
settings toggle defaults ON.
MSG
```

---

## Task 12: "You are here" on the standards table

**Files:**
- Modify: `src/sm64_events/ui/components/standards.js`

**Interfaces:**
- Consumes: `ranks/scoring.py`'s anchors, mirrored in JS (T1); the section's active strat + basis frames, already in the section payload as `sec.rank.basis` / `sec.pb`.
- Produces: a marker row inside the active strategy's column.

**Independent of every other task** — it needs only the score curve's shape, so it can land in Wave 1.

- [ ] **Step 1: Add the position helper**

At the top of `standards.js`:

```javascript
// Your time is essentially never AT a cutoff, so "you are here" is not a
// cell: it is a point BETWEEN two rows in one column. This returns that
// point as a 0..1 fraction of the gap between the two cutoffs it falls
// between, which is also exactly your division within the tier.
export function markerPosition(ladderSeconds, timeCs) {
  const rows = Object.entries(ladderSeconds)
    .map(([tier, s]) => [tier, Math.round(s * 100)])
    .sort((a, b) => a[1] - b[1]);                      // fastest first
  if (!rows.length || timeCs == null) return null;
  if (timeCs <= rows[0][1]) return { above: null, below: rows[0][0], frac: 1 };
  for (let i = 0; i < rows.length - 1; i += 1) {
    const [fastTier, fastCs] = rows[i], [slowTier, slowCs] = rows[i + 1];
    if (timeCs <= slowCs) {
      const span = slowCs - fastCs;
      return { above: fastTier, below: slowTier,
        frac: span > 0 ? (slowCs - timeCs) / span : 1 };
    }
  }
  return { above: rows[rows.length - 1][0], below: null, frac: 0 };
}
```

- [ ] **Step 2: Render the marker**

In the table body, after rendering the row for tier `below`, insert a marker row when `markerPosition(...)` names that tier — spanning only the active strategy's column:

```javascript
${marker && marker.below === tier && html`<tr class="std-marker-row">
  ${columns.map((strat) => (strat === activeStrat
    ? html`<td class="std-marker"><span style=${`bottom:${(marker.frac * 100).toFixed(0)}%`}>
        ◀ you · ${fmtIgt(basisFrames)} · ${fmtScore(entityScore)}</span></td>`
    : html`<td></td>`))}
</tr>`}
```

Tint the cells already beaten in that column by adding `class="std-beaten"` to any cell in `activeStrat`'s column whose tier is harder than `marker.below`.

- [ ] **Step 3: Add the CSS**

```css
.std-marker-row td{padding:0;border:0}
.std-marker{position:relative;display:block;height:0}
.std-marker>span{position:absolute;left:0;transform:translateY(50%);
  white-space:nowrap;font-size:11px;font-weight:600;color:var(--gold);
  text-shadow:0 0 6px rgba(0,0,0,.8);pointer-events:none}
.std-beaten{background:color-mix(in srgb,var(--gold) 8%,transparent)}
```

- [ ] **Step 4: Verify by rendering**

```bash
node --check src/sm64_events/ui/components/standards.js
```

Harness page with an 8-tier ladder across 4 strategies and a time landing mid-Platinum. Screenshot at 900px and 1400px. **Acceptance:** the marker sits between the Platinum and Diamond rows, only in the active column, and does not overlap the times on either side. If it does at 900px, fall back to the spec's stated alternative — mark the column header plus the two bracketing cells — and say so in the commit. Kill the server; delete the harness.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/standards.js src/sm64_events/ui/index.html
git commit -F- <<'MSG'
feat(ui): mark where you actually are in the standards table

A cell highlight would be a lie: your time is essentially never AT a cutoff,
so you are at a point BETWEEN two rows in one column. The marker sits at the
interpolated position, which shows depth into the tier -- your division and
the exact remaining gap -- for free, since it is the same interpolation the
score already computes.
MSG
```

---

## Task 13: Second medal on section headers

**Files:**
- Modify: `src/sm64_events/ui/components/ranks.js`, `src/sm64_events/ui/components/practice.js`
- Test: `tests/test_ui_section_parity.py`

**Interfaces:**
- Consumes: `sec.entity_rank` from T6 — `{score, tier, division}` or `null`
- Produces: `EntityRankTag({ entityRank })` in `ranks.js`

- [ ] **Step 1: Add the component**

In `ranks.js`:

```javascript
// The star's OWN rank, beside the strategy's. Two questions, two numbers:
// the strat medal says how well you run THIS strat, this one says how close
// that is to the fastest the star can be. Absent (not "–") when the entity
// has no standards, so a segment without a ladder shows nothing rather than
// implying it was graded and failed.
export function EntityRankTag({ entityRank }) {
  if (!entityRank) return null;
  return html`<span class="entity-rank" title=${`Star rank — best strategy possible · score ${entityRank.score}`}>
    <${Medal} rank=${entityRank.tier} size=${18} />
    <b>${entityRank.tier} ${entityRank.division}</b>
  </span>`;
}
```

- [ ] **Step 2: Render it in both cards**

In `practice.js`, in **both** the star section header and the segment section header, render `<${EntityRankTag} entityRank=${sec.entity_rank} />` immediately after the existing `RankBanner`. Both call sites are required — rule 11 (star↔segment parity).

- [ ] **Step 2b: Wire the standards marker's props (Task 12 depends on this)**

Task 12 added `sectionRank` / `sectionPb` props to `StandardsPanel`, but they default to `null` and **nothing passes them yet — so the "you are here" marker does not render in the live app at all.** This is the invisible-feature failure this repo has a rule about; it is not optional polish.

At **both** `StandardsPanel` call sites in `practice.js` (star ≈ line 480, segment ≈ line 624) add:

```javascript
        sectionRank=${sec.rank} sectionPb=${sec.pb}
```

Then verify by rendering (see Step 4) that the marker actually appears on a card whose entity has standards and a time — not merely that the props are passed.

- [ ] **Step 3: Extend the parity test**

In `tests/test_ui_section_parity.py`, add:

```python
def test_entity_rank_tag_is_rendered_for_both_kinds():
    """Rule 11: a feature built for one kind ships for both in the same change."""
    source = (UI / "components" / "practice.js").read_text()
    assert source.count("EntityRankTag") >= 2


def test_both_section_builders_emit_entity_rank():
    source = (SRC / "tracking" / "views.py").read_text()
    assert source.count('"entity_rank"') >= 2
```

Match the module-level `UI` / `SRC` path constants already used in that file; if they are named differently, use the existing names.

- [ ] **Step 4: Run tests and verify by rendering**

Run: `uv run pytest tests/test_ui_section_parity.py -q` → PASS
Then screenshot a practice card via the harness technique; confirm both medals read side by side and the row does not wrap under 900px.

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/components/ranks.js src/sm64_events/ui/components/practice.js tests/test_ui_section_parity.py
git commit -F- <<'MSG'
feat(ui): show the star's own rank beside the strategy's

Mastering a slow strat should feel like mastery AND read honestly: the left
medal climbs to Mario on the strat you chose, the right one stays where the
best possible strategy puts you. Rendered on star and segment cards in the
same change, with the parity test extended so the next asymmetry fails loudly.
MSG
```

---

## Task 14: Wire the Rank tab and the overlay into the app

**Files:**
- Modify: `src/sm64_events/ui/app.js`, `src/sm64_events/ui/store.js`

- [ ] **Step 1: Add the sidebar entry**

In `app.js`, add `["Rank", "rank"]` to the `NAV_GROUPS` `"Play"` group after `["Run", "run"]`, import `RankPage`, and add the branch:

```javascript
          : tab === "Rank" ? html`<div class="view-pane"><${RankPage} t=${t} /></div>`
```

If `ui/components/icons.js` has no `rank` icon, add one (a simple chevron-up-in-shield path) — an unknown icon name renders blank.

- [ ] **Step 2: Mount the overlay at app root**

Browser↔GUI parity (rule 10) means the overlay mounts in `app.js`, not inside a tab. Hold `marelo` in `store.js` (refetched on the `REFRESH_ON` events plus `marelo_changed` and `route_selected`), pass `marelo.celebration` into `RankUpOverlay`, and clear it locally on `onDone`.

Add `"marelo_changed"` and `"route_selected"` to `REFRESH_ON` in `store.js`.

- [ ] **Step 2b: Keep the Rank tab live (found during Task 10)**

`RankPage` fetches on mount and on scope change only, so it goes **stale while open during play** — finish a run and the rating, chart and breakdown keep showing pre-run numbers with nothing to indicate they are old. Since the whole point of the feature is watching the number move, that is a real defect, not polish.

Give `RankPage` a refresh trigger from the same WS events the header bar uses (`attempt_completed`, `marelo_changed`, `rank_mode_changed`, `route_selected`). Simplest shape that fits the existing store: expose a monotonically-increasing counter (e.g. `marelo Rev`) bumped in the WS handler, and include it in `RankPage`'s effect dependencies so all three fetches re-run.

Verify by rendering: with the page mounted, dispatch the store's WS handler for `attempt_completed` with a changed stub payload and confirm from a screenshot that the card, chart and breakdown all update — not merely that a fetch fired.

- [ ] **Step 3: Verify**

Run: `node --check src/sm64_events/ui/app.js src/sm64_events/ui/store.js`
Then start a dev server on the source port and screenshot the Rank tab.

**Do not start `python -m sm64_events.main` if the user may be playing** — the recorder lock is the only thing protecting their recording (dev-process rule). Ask first, or use a harness page.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/sm64_events/ui/app.js src/sm64_events/ui/store.js
git commit -m "feat(ui): mount the Rank tab and rank-up overlay at app root

The overlay lives at the root rather than inside a tab so a rank-up earned on
the Practice page still celebrates, and so it appears in the desktop GUI and
the browser identically (rule 10)."
```

---

## Task 15: Documentation

**Files:**
- Modify: `.claude/rules/ranks.md`, `.claude/rules/ui.md`, `.claude/rules/server.md`, `README.md`

- [ ] **Step 1: Extend the ranks change-map**

Add rows to `.claude/rules/ranks.md`:

| To change... | Edit |
|---|---|
| The 0–100 curve, divisions, best-possible ladder | `ranks/scoring.py` — `SCORE_ANCHORS` are xcams' player bands verbatim (+ Bronze 10); THE invariant is `tier_from_score(score_for(L,t), defined_tiers(L)) == classify.rank_for(L,t)`, pinned over all 278 seeded ladders by `tests/test_ranks_scoring_seed.py`. `defined_tiers` is REQUIRED for entity lookups — a ladder missing a tier still crosses that tier's score range |
| Scopes + aggregation | `ranks/scopes.py` — a scope is a derived SET; every route is automatically one. Entities resolve to GROUPS so a K-of-N step contributes k slots scored by its best k. `MARELO = mastery × coverage`; absent (no ladder) vs zero (rankable, unpracticed) is load-bearing |
| Rank history | `ranks/history.py` — recomputed, never stored; follows current standards and route membership by design |
| Attempts → scores | `tracking/marelo.py` — per-strategy basis under the active rank mode, then the best strategy wins the entity |
| MARELO REST + exclusions + celebration ack | `server/ranks_api.py` — `/api/marelo*`; the focus route IS the scope; the watermark is raised only by an explicit ack |

- [ ] **Step 2: Extend the UI change-map**

Add to `.claude/rules/ui.md`'s table: `ui/components/marelo.js` (crest + header bar, fixed 44px for OBS), `rankpage.js` (Rank tab — scope picker/card/chart/breakdown; route scopes default to route order), `celebrate.js` (climbs every tier gained; acks on dismiss; `sm64.celebrate` localStorage pref), and the `standards.js` marker.

- [ ] **Step 3: Extend the server change-map and README**

Add the `/api/marelo*` endpoints to `.claude/rules/server.md`'s ranks row and to the README's API surface section (the README documents the consumer-facing surface).

- [ ] **Step 4: Commit**

```bash
git add .claude/rules/ranks.md .claude/rules/ui.md .claude/rules/server.md README.md
git commit -m "docs: map the MARELO modules into the per-zone change rules

Stale rule files are a broken build in this repo -- a future session finds
where to change things here or not at all."
```

---

## Self-Review

**Spec coverage.** §3 corpus/exclusion/scopes → T3, T8. §3.4 focus-route-is-scope → T8 `_active_scope`, T10. §4.1 two scores → T5, T6, T13. §4.2 best ladder → T1. §4.3 curve → T1. §4.4 invariant → T1, T2. §4.5 divisions → T1. §4.6 rank mode → T5. §5 aggregation + gains → T3. §6 history → T4, T8, T10. §7.1 both ranks → T13. §7.2 header bar → T9. §7.3 Rank tab → T10, T14. §7.4 celebration → T7, T8, T11. §7.5 marker → T12. §8 file map → all. §9 REST → T8. §10 testing → T1–T8 unit, T9–T13 render, T13 parity. §11 empty/tiny scope → T3 + T10's small-scope note; celebration-on-replay → T7's ack design. §12 100c → global constraints (excluded). No gaps.

**Type consistency.** `entity_groups` returns `[{"need", "candidates"}]` and is consumed with those keys in T3/T4/T8. `aggregate` returns `marelo/mastery/coverage/tier/division/next_division_at/n/practiced/entities` — every consumer (T8, T9, T10) reads only those. `entity_scorer` is `(key, frames) -> float|None` in T4's tests and T8's `scorer`. `celebration_delta` returns `from/to/tiers_gained/key`; T11 reads `to.tier`, `to.division`, `from.tier`, `key` — all present. `progression_key(tier, numeral)` — T3 and T8 both call it with that order.

**Two follow-ups folded in during review.** T3's `_from_key` needs `RANK_NAMES` on the `scoring` module, so T3 Step 3 adds the `__all__` re-export rather than leaving a broken import. T8's `entity_label`/`segment_courses` depend on `addresses` shapes I have not verified, so that step carries a `uv run python -c` probe and an explicit "do not guess" instruction instead of invented constant names.

---

**Plan complete and saved to `docs/superpowers/plans/2026-07-24-marelo-rank-system.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
