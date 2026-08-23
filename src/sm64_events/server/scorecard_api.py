"""REST for the goal-vs-you scorecard. Endpoint contracts are `docs/api.md`'s
`GET /api/scorecard`, `PUT /api/scorecard/goal` and `GET /api/scorecard/column`
rows; router behavior is `tests/test_scorecard_api.py`.

Its own router rather than a block in `ranks_api.py` for the same reason
`import_api.py` split off: that file is already +170 lines for the
leaderboard branch, and this needs a different pair of injected pieces
(the tracker service for PBs and standards; `library`/`adoptions` are
accepted now and stay unused until Task 6 wires the runner goal's
resolver, so a fetch order the leaderboard branch settles does not gate
this one).

One persisted choice drives the whole card -- the ui_state KV
`"scorecard_goal"`, `{"kind":"division","tier":...,"division":...}` |
`{"kind":"runner","runner":...}` | `None` -- server-side so the browser and
the desktop GUI read the same goal (`.claude/rules/import.md`'s reasoning
for why an imported time lands server-side applies here too: two clients,
one KV). `GET /api/scorecard` re-derives the whole card from it on every
request; nothing about the card itself is stored.

`GET /api/scorecard/column` is the reverse of `POST /api/import/sheet` --
that door reads a runner's column IN as PBs, this one writes YOUR PBs OUT as
a column (`library/export_column.py`), formatted the way the sheet itself
wants a time typed, ready to paste back in next to everyone else's.
"""
import logging

from fastapi import APIRouter, Body, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sm64_events.library.export_column import column_lines
from sm64_events.library.sheet import read_rows
from sm64_events.library.source import fetch
from sm64_events.library.store import build_and_stamp
from sm64_events.ranks.classify import RANK_NAMES, display_cs
from sm64_events.ranks.scorecard import build_card, card_keys, division_goal_cs
from sm64_events.ranks.scoring import DIVISION_NUMERALS, best_ladder, best_ladder_owners
from sm64_events.server.import_api import sheet_row_placer

_log = logging.getLogger("sm64.scorecard")
_GOAL_KEY = "scorecard_goal"
_VALID_TIERS = [tier for tier in RANK_NAMES if tier != "Iron"]


class GoalBody(BaseModel):
    kind: str
    tier: str | None = None
    division: str | None = None
    runner: str | None = None


def _fetch_column_source(overrides):
    """Off the event loop, together: `read_rows` and `build_and_stamp`
    (`library/store.py` -- the SAME bytes-to-payload step `LibraryStore.
    refresh()` runs, minus the ladder fit `column_lines` never reads) must
    see the identical bytes `fetch()` just returned, or the two walks fall
    out of step (a target-opening row lines up against the wrong target).
    Both are real CPU work over a ~5.6 MB document (`server/import_api.py`
    notes the same concern for its own refresh). Not persisted -- this is a
    read, not a library refresh."""
    data = fetch()
    return read_rows(data), build_and_stamp(data, overrides)


def _column_resolve(service):
    """Your current PB, strategy-blind lookup narrowed to one strategy --
    `centiseconds | None`, `None` when there is no PB, or when there is one
    but it was set on the other ROM (`row.version` is only ever non-None on
    a row the sheet itself declares a version for; an unversioned row never
    checks)."""
    def resolve(entity_key, strat_tag, timer_mode, version):
        kind, _, rest = entity_key.partition(":")
        if kind == "star":
            course_id, star_id = rest.split(":")
            pb = service.db.current_pb(int(course_id), int(star_id), timer_mode,
                                       strat_tag=strat_tag)
        elif kind == "segment":
            pb = service.db.current_pb(None, None, timer_mode,
                                       segment_id=int(rest), strat_tag=strat_tag)
        else:
            return None
        if pb is None:
            return None
        if version is not None:
            ranks = getattr(service, "ranks", None)
            pb_version = pb.get("game_version") or (
                ranks.grading_version if ranks is not None else None)
            if pb_version != version:
                return None
        return display_cs(pb["frames"])
    return resolve


def create_scorecard_router(service, library=None, adoptions=None,
                            overrides=None) -> APIRouter:
    """`library`/`adoptions` back the column export below; the goal card
    above is unaffected by either. `overrides` are the human's Library audit
    corrections, applied to a live column-export fetch exactly as a library
    refresh applies them, so a re-fetched sheet does not reintroduce a
    mistake the audit already fixed."""
    router = APIRouter(prefix="/api/scorecard", tags=["scorecard"])

    def resolve_seed(seed_key: str):
        """A Secret-row movement's seed_key -> (segment:<id>, name), or None
        when no live definition carries it (the row drops, per
        `ranks/scorecard.py`'s own contract)."""
        for row in service.db.segment_defs():
            if row.get("seed_key") == seed_key:
                return f"segment:{row['id']}", row["name"]
        return None

    def your_times(keys: list[str]) -> dict[str, int]:
        """Your strategy-blind current PB per key, on the entity's own
        clock -- igt for a star, rta for a movement -- as displayed
        centiseconds. A key with no saved PB is simply absent, which is the
        "no goal on this tile" case `_tile` already handles."""
        you = {}
        for key in keys:
            parts = key.split(":")
            if parts[0] == "star":
                row = service.db.current_pb(int(parts[1]), int(parts[2]), "igt")
            else:
                row = service.db.current_pb(None, None, "rta",
                                            segment_id=int(parts[1]))
            if row is not None:
                you[key] = display_cs(row["frames"])
        return you

    def division_goal_map(keys: list[str], tier: str, division: str) -> dict[str, int]:
        goal = {}
        for key in keys:
            ladder_cs = best_ladder(service.ranks.ladders(key))
            cs = division_goal_cs(ladder_cs, tier, division)
            if cs is not None:
                goal[key] = cs
        return goal

    def fold_choices(goal_value: dict | None) -> dict[int, int | None]:
        """Per course, the exit star BOTH sums skip: your own 100c PB's
        variant first, else the variant owning the goal tier's 100c cutoff,
        else no fold. A PB with no strategy, or a strategy that names no
        variant, falls through to the next rule rather than stopping."""
        fold = {}
        for course_id in range(1, 16):
            exit_star = None
            pb = service.db.current_pb(course_id, 6, "igt")
            if pb is not None and pb.get("strat_tag"):
                variant = service.ranks.variant_of(f"star:{course_id}:6",
                                                    pb["strat_tag"])
                if variant is not None:
                    exit_star = variant[1]
            if exit_star is None and goal_value and goal_value.get("kind") == "division":
                ladder = service.ranks.ladders(f"star:{course_id}:6")
                owners = best_ladder_owners(ladder).get(goal_value["tier"])
                if owners:
                    variant = service.ranks.variant_of(f"star:{course_id}:6",
                                                        owners[0])
                    if variant is not None:
                        exit_star = variant[1]
            if exit_star is not None:
                fold[course_id] = exit_star
        return fold

    @router.get("")
    async def get_scorecard():
        keys = card_keys(resolve_seed)
        you = your_times(keys)
        goal_value = service.db.get_state(_GOAL_KEY, None)
        ranks = service.ranks

        goal_map: dict[str, int] = {}
        fold: dict[int, int | None] = {}
        # A runner goal is accepted (PUT below) but has no resolver until
        # Task 6 -- served as an empty goal map, flagged so the UI can say
        # "goal set, not yet computed" rather than "no goal".
        goal_pending = bool(goal_value) and goal_value.get("kind") == "runner"
        if ranks is not None:
            if goal_value and goal_value.get("kind") == "division":
                goal_map = division_goal_map(keys, goal_value["tier"],
                                             goal_value["division"])
            fold = fold_choices(goal_value)

        card = build_card(you=you, goal=goal_map, fold=fold,
                          resolve_seed=resolve_seed)
        tiles = [tile for row in card["rows"] for tile in row["tiles"]]
        coverage = {"covered": sum(1 for t in tiles if t["goal_cs"] is not None),
                    "tiles": len(tiles)}
        payload = {**card, "goal": goal_value, "goal_coverage": coverage}
        if goal_pending:
            payload["goal_pending"] = True
        return payload

    @router.put("/goal")
    async def set_goal(body: GoalBody | None = Body(default=None)):
        """No broadcast: the goal picker is the only writer, and the card
        refetches on `t.mareloRev` like the rest of the Rank tab -- there is
        no second client watching this KV live the way the recorder's row
        list needs a push."""
        if body is None:
            service.db.set_state(_GOAL_KEY, None)
            return {"goal": None}
        if body.kind == "division":
            if body.tier not in _VALID_TIERS or body.division not in DIVISION_NUMERALS:
                raise HTTPException(
                    422, f"unknown tier/division {body.tier!r}/{body.division!r}")
            value = {"kind": "division", "tier": body.tier, "division": body.division}
        elif body.kind == "runner":
            if not body.runner:
                raise HTTPException(422, "runner goal needs a runner name")
            value = {"kind": "runner", "runner": body.runner}
        else:
            raise HTTPException(422, f"unknown goal kind {body.kind!r}")
        service.db.set_state(_GOAL_KEY, value)
        return {"goal": value}

    @router.get("/column")
    async def get_column():
        """Your PBs as the Ultimate Sheet's own column
        (`library/export_column.py::column_lines`) -- one line per live
        worksheet row, ready to paste back into the sheet. Always a LIVE
        fetch, never the cached snapshot: the row layout has to match the
        sheet you are about to paste into (same reasoning as `POST
        /api/import/sheet`'s own refresh)."""
        if library is None:
            raise HTTPException(503, "sheet library unavailable")
        try:
            rows, payload = await run_in_threadpool(_fetch_column_source, overrides)
        except Exception as err:
            _log.warning("column export could not read the sheet: %r", err)
            raise HTTPException(
                503, f"could not read the sheet: {err}") from err
        place = sheet_row_placer(service, adoptions)
        lines = column_lines(rows, payload, _column_resolve(service), place=place)
        return {"lines": lines, "sheet_revision": payload.get("sheet_revision"),
                "mapped": sum(1 for line in lines if line),
                "total_rows": len(lines)}

    return router
