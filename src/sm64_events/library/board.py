"""Serves the two surfaces built on top of `library.ratings` (spec
2026-08-20-ranked-leaderboard, Task 3): an ordered leaderboard for a scope --
every community runner scored the same way MARELO scores the user, plus the
user's own row -- and one runner's scoped rating widened for comparison
against the user's own numbers on the same entities.

Pure over its INPUTS, `ranks/scopes.py`'s own discipline (no db, no file I/O,
no network), except for the one thing a leaderboard cannot be pure about:
`RunnerScoreCache` holds the {runner: {entity: score}} map between requests,
because building it costs ~29ms over 448 runners (`library.ratings.
runner_scores`) and a leaderboard fetch happens far more often than the
things that actually change it.

It rebuilds only when one of FOUR inputs moves: the library's sheet
revision, the user's adoptions map, the grading version, and a fingerprint
of the standards ladders. The last one is the trap this task was warned
about: `ranks_store.to_json()["version"]` is the bundled SEED's version (a
literal `1`, `ranks/standards.py::_seed_version`) and never moves -- not on
a threshold edit, a JP overlay, a new strategy, or a reset. Keying the cache
on it would serve a stale board forever after any standards edit, and
stale-but-plausible is the failure mode nobody reports. `_standards_fingerprint`
hashes the whole vetted store instead (`ranks_store.to_json()`, the same
dict `set_threshold`/`create_strategy`/`clear_jp`/`reset_entity` all mutate
before their own `save()`), which is what actually moves -- proved in
`tests/test_library_board.py` by editing one threshold and asserting the
board's numbers change on the next fetch.

Per-scope AGGREGATES memoize on top of that map too: for an unchanged score
map, a repeat request for the SAME scope reuses its ranked runner rows
rather than re-running `scopes.aggregate` over all 448 runners again (46ms
measured for Overall). Known gap, same shape this project already tolerates
elsewhere (`ranks/standards.py::_reconcile`'s own docstring calls one out
the same way): a ROUTE-based scope can go stale mid-key if the route's own
steps are edited without any of the four tracked inputs moving. Nobody has
asked for that yet; it is noted rather than guessed at."""
import hashlib
import json

from sm64_events.library import ratings
from sm64_events.ranks import scopes
from sm64_events.ranks.classify import display_cs
from sm64_events.ranks.standards import entity_key
from sm64_events.tracking import marelo as marelo_bridge
from sm64_events.tracking.views import current_pbs_by_strat


def _standards_fingerprint(ranks_store) -> str:
    """A hash that moves whenever the VETTED store changes -- see the module
    docstring for why `to_json()["version"]` cannot serve this instead."""
    return hashlib.sha256(
        json.dumps(ranks_store.to_json(), sort_keys=True).encode()).hexdigest()


class RunnerScoreCache:
    """One instance per running app (created once in
    `server/ranks_api.py::create_ranks_router`, like the module's
    process-lifetime `dead_videos` set) -- never a module-level singleton,
    which would leak one test's cached board into an unrelated test's
    identical-looking inputs."""

    def __init__(self):
        self._key = None
        self._scores: dict[str, dict[str, float]] = {}
        self._times: dict[str, dict[str, int]] = {}
        self._board_rows: dict[str, tuple[list[dict], int]] = {}

    def refresh(self, library, adoptions_rows: dict, ranks_store, *,
                version: str):
        """Rebuilds the runner score/time maps iff one of the four tracked
        inputs moved since the last call; always returns the current
        (scores, times) either way. `library.payload` and `ratings.
        runner_scores`/`runner_times` are Task 2's own contract -- this
        never re-grades a time itself, only caches what they compute."""
        key = (library.revision, dict(adoptions_rows), version,
               _standards_fingerprint(ranks_store))
        if key != self._key:
            self._times = ratings.runner_times(library.payload, adoptions_rows,
                                               version=version)
            self._scores = ratings.runner_scores(library.payload, ranks_store,
                                                 adoptions_rows, version=version)
            self._board_rows = {}          # stale with the map that made them
            self._key = key
        return self._scores, self._times

    def board_rows(self, scope_id: str, groups: list[dict],
                   scores: dict[str, dict[str, float]]
                   ) -> tuple[list[dict], int]:
        """(rows, omitted) for `scope_id` -- every runner's own row,
        practiced entities only, UNRANKED (`leaderboard()` adds the user's
        row and ranks the two together, which is why this stops short of a
        `position`), plus a COUNT of the runners left out because they have
        never practiced anything in this scope. The count exists because the
        omission itself must never be silent: a board that drops most of the
        sheet without saying so reads as "this is everyone" when it is not
        (his ruling on this exact question -- keep dropping them, but never
        silently)."""
        if scope_id not in self._board_rows:
            rows, omitted = [], 0
            for runner, by_entity in scores.items():
                agg = scopes.aggregate(by_entity, groups)
                if agg["practiced"]:
                    rows.append(_row(agg, runner=runner, you=False))
                else:
                    omitted += 1
            self._board_rows[scope_id] = (rows, omitted)
        return self._board_rows[scope_id]


def _row(agg: dict, *, runner: str | None, you: bool) -> dict:
    return {"runner": runner, "you": you, "marelo": agg["marelo"],
            "tier": agg["tier"], "division": agg["division"],
            "mastery": agg["mastery"], "practiced": agg["practiced"],
            "n": agg["n"]}


def _rank_key(marelo: float | None):
    # None (an empty scope -- no groups at all) sorts last and ties only
    # with other Nones; everything else sorts by MARELO descending.
    return (0, -marelo) if marelo is not None else (1, 0.0)


def _ranked(rows: list[dict]) -> list[dict]:
    """Competition ranking (1-2-2-4): a tie SHARES one position and the
    position after it skips by the tie's size, never 1-2-2-3."""
    ordered = sorted(rows, key=lambda row: _rank_key(row["marelo"]))
    ranked, previous_key, position = [], None, 0
    for index, row in enumerate(ordered):
        key = _rank_key(row["marelo"])
        if key != previous_key:
            position = index + 1
        ranked.append({"position": position, **row})
        previous_key = key
    return ranked


def leaderboard(cache: RunnerScoreCache, library, adoptions_rows: dict,
                ranks_store, groups: list[dict], scope_id: str, *,
                version: str, you_aggregate: dict
                ) -> tuple[list[dict], int]:
    """(rows, omitted). `rows` is every runner who has practiced at least
    one entity in this scope, plus the user's own row, MARELO-descending
    and competition-ranked.

    A runner with zero practiced entities here is left off `rows` rather
    than shown tied at 0.0 -- the sheet holds 448 people, most of whom have
    never touched most scopes, and a leaderboard is not improved by a tail
    of hundreds of zero rows. The user's own row carries no such filter: it
    is always present, even at 0, because it is the one row he came to find.

    `omitted` is how many were left off, and it is NOT optional to surface:
    a board that silently drops most of the sheet reads as "this is
    everyone" when it is not -- the caller must show this count, not just
    the rows."""
    scores, _times = cache.refresh(library, adoptions_rows, ranks_store,
                                   version=version)
    runner_rows, omitted = cache.board_rows(scope_id, groups, scores)
    rows = list(runner_rows)
    rows.append(_row(you_aggregate, runner=None, you=True))
    return _ranked(rows), omitted


def you_times_by_entity(pb_rows, ranks_store, keys) -> dict[str, int]:
    """The user's own best DISPLAYED time (centiseconds) per entity in
    `keys` -- the raw number behind `tracking.marelo.entity_scores`'s
    pb-mode score, which returns only the score. Same PB source
    (`current_pbs_by_strat`) and the same clock filter that function
    applies (`row["timer_mode"] == ranks_store.clock_for(key)`).

    MIN frames and MAX score always pick the same strategy here: every
    strategy on one entity grades against that entity's SAME best-possible
    ladder (`scoring.best_ladder`, pointwise across strategies), and
    `scoring.progress_for_time` is monotone in time against a fixed ladder,
    so the fastest raw time is always the highest-scoring one."""
    wanted = set(keys)
    best: dict[str, int] = {}
    for row in current_pbs_by_strat(list(pb_rows)).values():
        key = entity_key(row["course_id"], row["star_id"], row["segment_id"])
        if key not in wanted or row["timer_mode"] != ranks_store.clock_for(key):
            continue
        cs = display_cs(row["frames"])
        if key not in best or cs < best[key]:
            best[key] = cs
    return best


def runner_breakdown(cache: RunnerScoreCache, library, adoptions_rows: dict,
                     ranks_store, groups: list[dict], runner_name: str, *,
                     version: str, you_scores: dict, you_times: dict,
                     label_of) -> dict | None:
    """The one runner's scoped rating, per entity, each row widened with the
    user's own score/time/tier/division on the SAME entity -- what Tasks 4
    and 5 draw side by side. `None` (the caller's 404) when the sheet has
    never heard of this runner at all.

    `excluded` is always False: the user's exclusions shape the scope for
    HIM (`_groups(..., excluded=set())` upstream already resolved this
    scope with no exclusion filter), and must not silently shrink the
    denominator every runner is judged on."""
    scores, times = cache.refresh(library, adoptions_rows, ranks_store,
                                  version=version)
    runner_scores_map = scores.get(runner_name)
    if runner_scores_map is None:
        return None
    runner_times_map = times.get(runner_name, {})
    agg = scopes.aggregate(runner_scores_map, groups)
    ladders = marelo_bridge.entity_ladders(
        ranks_store, [entity["key"] for entity in agg["entities"]])
    entities = []
    for entity in agg["entities"]:
        key = entity["key"]
        ladder = ladders.get(key, {})
        graded = marelo_bridge.classify_entity(ladder, entity["score"], agg["n"])
        you_graded = marelo_bridge.classify_entity(
            ladder, you_scores.get(key), agg["n"])
        entities.append({
            "key": key, "label": label_of(key), "score": entity["score"],
            "tier": graded["tier"], "division": graded["division"],
            "next_tier": graded["next_tier"],
            "next_division": graded["next_division"],
            "gain": graded["gain"], "excluded": False,
            "time_cs": runner_times_map.get(key),
            "you": {"score": you_scores.get(key),
                    "time_cs": you_times.get(key),
                    "tier": you_graded["tier"],
                    "division": you_graded["division"]}})
    return {"runner": runner_name, "marelo": agg["marelo"],
            "mastery": agg["mastery"], "coverage": agg["coverage"],
            "tier": agg["tier"], "division": agg["division"],
            "next_division_at": agg["next_division_at"],
            "division_progress": agg["division_progress"],
            "n": agg["n"], "practiced": agg["practiced"], "entities": entities}


def runner_summary(cache: RunnerScoreCache, library, adoptions_rows: dict,
                   ranks_store, scope_specs, runner_name: str, *,
                   version: str) -> list[dict] | None:
    """[{scope_id, label, tier, division, marelo, n, practiced}] over every
    (scope_id, groups, label) in `scope_specs` -- the same chip shape
    `/api/marelo/summary` returns, sourced from this runner instead of the
    user, so the runner page's scope chip row is the existing component
    with a different source. `None` when the sheet has never heard of this
    runner at all."""
    scores, _times = cache.refresh(library, adoptions_rows, ranks_store,
                                   version=version)
    runner_scores_map = scores.get(runner_name)
    if runner_scores_map is None:
        return None
    chips = []
    for scope_id, groups, label in scope_specs:
        agg = scopes.aggregate(runner_scores_map, groups)
        chips.append({"scope_id": scope_id, "label": label,
                      "tier": agg["tier"], "division": agg["division"],
                      "marelo": agg["marelo"], "n": agg["n"],
                      "practiced": agg["practiced"]})
    return chips
