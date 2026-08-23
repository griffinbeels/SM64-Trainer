"""REST for the goal-vs-you scorecard (spec docs/superpowers/specs/
2026-08-23-scorecard-design.md).

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
"""
from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

from sm64_events.ranks.classify import RANK_NAMES, display_cs
from sm64_events.ranks.scorecard import build_card, card_keys, division_goal_cs
from sm64_events.ranks.scoring import DIVISION_NUMERALS, best_ladder, best_ladder_owners

_GOAL_KEY = "scorecard_goal"
_VALID_TIERS = [tier for tier in RANK_NAMES if tier != "Iron"]


class GoalBody(BaseModel):
    kind: str
    tier: str | None = None
    division: str | None = None
    runner: str | None = None


def create_scorecard_router(service, library=None, adoptions=None) -> APIRouter:
    """`library`/`adoptions` are accepted now and unused -- Task 6's runner
    goal resolves through the same leaderboard pieces `import_api.py`
    already threads (`library/ratings.py::runner_times`), so this router's
    signature is settled before that resolver exists rather than growing a
    parameter later."""
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

    return router
