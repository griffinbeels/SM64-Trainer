"""The [[Rank board]] and the [[Runner page]]'s data, read off
`library.ratings` (spec 2026-08-20-ranked-leaderboard, Task 3).

One object, `RatedSheet`, holds every community runner's rating for the
current sheet/adoptions/standards/version, and every reading the UI draws is
a method on it: `leaderboard` (every runner + the user, one scope, ranked),
`runner_breakdown` (one runner per entity, widened with the user's own
numbers) and `runner_summary` (one runner's chip row). A new reading -- the
ghost, "who is directly above me" -- is one more method here and one route
in `server/ranks_api.py`, nothing else.

`RatingsCache` is the one impure piece: it holds the current `RatedSheet`
between requests, because rating 448 runners costs ~29ms
(`ratings.rate_runners`) and a board fetch happens far more often than the
things that change it. It rebuilds only when one of FOUR inputs moves: the
library's sheet revision, the user's adoptions map, the grading version, and
a fingerprint of the standards ladders. The last one is the trap:
`ranks_store.to_json()["version"]` is the bundled SEED's version (a literal
`1`, `ranks/standards.py::_seed_version`) and never moves -- not on a
threshold edit, a JP overlay, a new strategy, or a reset. Keying on it would
serve a stale board forever after any standards edit, and stale-but-plausible
is the failure mode nobody reports. `_standards_fingerprint` hashes the whole
vetted store instead, the same dict `set_threshold`/`create_strategy`/
`clear_jp`/`reset_entity` all mutate -- proved in `tests/test_library_board.py`
by editing one threshold and asserting the board's numbers change.

Known gap, same shape `ranks/standards.py::_reconcile` already tolerates: a
ROUTE-based scope's memoized rows can go stale if the route's own steps are
edited without any of the four inputs moving. Noted, not guessed at."""
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


def _row(agg: dict, *, runner: str | None, you: bool) -> dict:
    """One board row. The user's own row is `runner=None, you=True` -- the
    sentinel `leaderboard.js` turns into "You"."""
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


class RatedSheet:
    """Every runner's times and scores off one (sheet, adoptions, standards,
    version) -- built once per change by `RatingsCache`, then read by the
    three methods below. Per-scope board rows memoize on the instance, so
    the memo can never outlive the ratings it was computed from."""

    def __init__(self, rated: ratings.RatedRunners, ranks_store):
        self.times = rated.times
        self.scores = rated.scores
        self._ranks_store = ranks_store
        self._rows_by_scope: dict[str, tuple[list[dict], int]] = {}

    def _scope_rows(self, scope_id: str, groups: list[dict]
                    ) -> tuple[list[dict], int]:
        """(rows, omitted) for one scope, unranked and without the user.

        `omitted` counts the runners who are rated SOMEWHERE on the sheet
        but have no time for anything THIS scope covers -- they get no row
        here rather than a tail of hundreds tied at 0.0 (338 of 448 on Wing
        Mario Over the Rainbow), but the drop is never silent: a board that
        hides most of the sheet without a count reads as "this is everyone".
        A runner with no time ANYWHERE is not in `scores` at all and so is
        counted in neither. On `overall` the count is always 0, since every
        rankable entity is its own group there."""
        if scope_id not in self._rows_by_scope:
            rows, omitted = [], 0
            for runner, by_entity in self.scores.items():
                agg = scopes.aggregate(by_entity, groups)
                if agg["practiced"]:
                    rows.append(_row(agg, runner=runner, you=False))
                else:
                    omitted += 1
            self._rows_by_scope[scope_id] = (rows, omitted)
        return self._rows_by_scope[scope_id]

    def leaderboard(self, scope_id: str, groups: list[dict], *,
                    you_aggregate: dict) -> tuple[list[dict], int]:
        """(rows, omitted): every runner who has practiced something in
        this scope plus the user's own row, MARELO-descending and
        competition-ranked. The user's row is always present, even at 0 --
        it is the one row he came to find. `omitted` is `_scope_rows`'s
        count, and the caller must show it."""
        rows, omitted = self._scope_rows(scope_id, groups)
        rows = [*rows, _row(you_aggregate, runner=None, you=True)]
        return _ranked(rows), omitted

    def runner_summary(self, runner_name: str, scope_specs
                       ) -> list[dict] | None:
        """[{scope_id, label, tier, division, marelo, n, practiced}] over
        every (scope_id, groups, label) in `scope_specs` -- the same chip
        shape `/api/marelo/summary` returns, so the runner page's chip row
        is the existing component with a different source. `None` when the
        sheet has never heard of this runner."""
        runner_scores = self.scores.get(runner_name)
        if runner_scores is None:
            return None
        chips = []
        for scope_id, groups, label in scope_specs:
            agg = scopes.aggregate(runner_scores, groups)
            chips.append({"scope_id": scope_id, "label": label,
                          "tier": agg["tier"], "division": agg["division"],
                          "marelo": agg["marelo"], "n": agg["n"],
                          "practiced": agg["practiced"]})
        return chips

    def runner_breakdown(self, runner_name: str, groups: list[dict], *,
                         you_scores: dict, you_times: dict, label_of
                         ) -> dict | None:
        """One runner's scoped rating per entity, each row widened with the
        user's own score/time/tier/division on the SAME entity -- the same
        field set `/api/marelo` returns plus `runner`, so `Breakdown` and
        `CoverageStrip` render either source unchanged. `None` when the
        sheet has never heard of this runner.

        `excluded` is always False: the caller resolved `groups` with no
        exclusion filter, because the user's exclusions shape the scope for
        HIM and must not shrink the denominator every runner is judged on."""
        runner_scores = self.scores.get(runner_name)
        if runner_scores is None:
            return None
        runner_times = self.times.get(runner_name, {})
        agg = scopes.aggregate(runner_scores, groups)
        ladders = marelo_bridge.entity_ladders(
            self._ranks_store, [entity["key"] for entity in agg["entities"]])
        entities = []
        for entity in agg["entities"]:
            key = entity["key"]
            # Every key in `groups` cleared `rankable_entities`' non-empty-
            # ladder bar, so the `{}` default never fires in practice.
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
                "time_cs": runner_times.get(key),
                "you": {"score": you_scores.get(key),
                        "time_cs": you_times.get(key),
                        "tier": you_graded["tier"],
                        "division": you_graded["division"]}})
        return {"runner": runner_name, "marelo": agg["marelo"],
                "mastery": agg["mastery"], "coverage": agg["coverage"],
                "tier": agg["tier"], "division": agg["division"],
                "next_division_at": agg["next_division_at"],
                "division_progress": agg["division_progress"],
                "n": agg["n"], "practiced": agg["practiced"],
                "entities": entities}


class RatingsCache:
    """Holds the current `RatedSheet` between requests and rebuilds it only
    when one of the four tracked inputs moves (module docstring). One
    instance per running app, created in `create_ranks_router` -- never a
    module-level singleton, which would leak one test's cached board into
    an unrelated test with identical-looking inputs."""

    def __init__(self):
        self._key = None
        self._sheet: RatedSheet | None = None

    def current(self, library, adoptions_rows: dict, ranks_store, *,
                version: str) -> RatedSheet:
        key = (library.revision, dict(adoptions_rows), version,
               _standards_fingerprint(ranks_store))
        if key != self._key or self._sheet is None:
            rated = ratings.rate_runners(library.payload, ranks_store,
                                         adoptions_rows, version=version)
            self._sheet = RatedSheet(rated, ranks_store)
            self._key = key
        return self._sheet


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
