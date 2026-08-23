"""REST for the goal-vs-you scorecard. Endpoint contracts are `docs/api.md`'s
`GET /api/scorecard`, `PUT /api/scorecard/goal` and `GET /api/scorecard/column`
rows; router behavior is `tests/test_scorecard_api.py`.

Its own router rather than a block in `ranks_api.py` for the same reason
`import_api.py` split off: that file is already +170 lines for the
leaderboard branch, and this needs a different pair of injected pieces
(the tracker service for PBs and standards; `library`/`adoptions` also
back a runner goal -- `library.ratings.runner_times`, the SAME reader
`library/board.py`'s leaderboard grades runners with, read straight off the
CACHED snapshot like the column export's record row below, never a live
fetch).

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
import csv
import io
import logging

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sm64_events.library.examples import sheet_best
from sm64_events.library.export_column import column_lines, sheet_time
from sm64_events.library.ratings import runner_times
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


_CSV_HEADER = ["Course", "Star", "Record", "Goal", "You", "Delta"]


def _csv_time(cs: int | None) -> str:
    return sheet_time(cs) if cs is not None else ""


def _csv_signed_time(delta_cs: int | None) -> str:
    """`sheet_time` carries no sign -- a Delta column needs one, so this
    prepends it rather than teaching the sheet's own notation about a
    concept the sheet itself never prints."""
    if delta_cs is None:
        return ""
    sign = "-" if delta_cs < 0 else "+"
    return f"{sign}{sheet_time(abs(delta_cs))}"


def _record_lookup(library, adoptions, service):
    """entity_key -> the fastest centiseconds anybody has recorded on the
    Ultimate Sheet for it, across every strategy -- `library/examples.py::
    sheet_best`'s own three-door walk, the SAME one `ranks_api.py`'s "Sheet
    Best" standards row reads. Reads the library's CACHED snapshot, never a
    live fetch (`/column` above is the one door that has to match the sheet
    you are about to paste into; a CSV export does not), so this costs no
    network call. Answers `None` for every entity when there is no library
    or no standards to grade on (a broadcast-only instance)."""
    if library is None or service.ranks is None:
        return lambda entity_key: None
    rows = adoptions.rows() if adoptions is not None else {}
    payload = library.payload
    has_jp_ladder = service.ranks.has_jp_ladder

    def lookup(entity_key: str) -> int | None:
        best = sheet_best(payload, rows, entity_key, has_jp_ladder)
        if not best:
            return None
        return min(entry["time_cs"] for entry in best.values())
    return lookup


def _record_sum(tiles: list[dict], record_of) -> int | None:
    """The Stage-RTA/Upstairs-RTA Record cell: the sum of every UNFOLDED
    tile's own Record, over exactly the tiles that carry one. Record is
    sheet-derived and, like a tile's own Record cell, does not wait on
    whether You/Goal are also present -- so this is an independent sum, not
    a second read of `row["sum"]`'s you/goal-gated `counted` set. `None`
    when the row's tiles carry no Record at all, never a sum that silently
    drops one."""
    values = [record_of(tile["key"]) for tile in tiles if not tile["folded"]]
    values = [value for value in values if value is not None]
    return sum(values) if values else None


def _csv_tile_row(course_label: str, tile: dict, record_cs: int | None) -> list[str]:
    return [course_label, tile["label"], _csv_time(record_cs),
            _csv_time(tile["goal_cs"]), _csv_time(tile["you_cs"]),
            _csv_signed_time(tile["delta_cs"])]


def _csv_sum_row(course_label: str, star_label: str, tiles: list[dict],
                 sum_obj: dict, record_of) -> list[str]:
    counted = sum_obj["counted"] > 0
    return [course_label, star_label, _csv_time(_record_sum(tiles, record_of)),
            _csv_time(sum_obj["goal_cs"] if counted else None),
            _csv_time(sum_obj["you_cs"] if counted else None),
            _csv_signed_time(sum_obj["delta_cs"])]


def _csv_rows(card: dict, record_of) -> list[list[str]]:
    """The whole export, in card order: every tile row, a `Stage RTA` sum
    row closing each COURSE row (never the Secret row -- it is not a stage),
    then one `Upstairs RTA` row summing the entire card, mirroring the
    Ultimate Sheet template's own layout (`docs/api.md`'s own row for this
    route)."""
    rows = [_CSV_HEADER]
    for row in card["rows"]:
        for tile in row["tiles"]:
            rows.append(_csv_tile_row(row["label"], tile, record_of(tile["key"])))
        if row["course_id"] is not None:
            rows.append(_csv_sum_row(row["label"], "Stage RTA", row["tiles"],
                                     row["sum"], record_of))
    all_tiles = [tile for row in card["rows"] for tile in row["tiles"]]
    rows.append(_csv_sum_row("", "Upstairs RTA", all_tiles, card["total"], record_of))
    return rows


def _csv_bytes(rows: list[list[str]]) -> bytes:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\r\n").writerows(rows)   # RFC 4180 CRLF
    return buf.getvalue().encode("utf-8")


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

    def runner_goal_map(runner: str, version: str) -> dict[str, int]:
        """Every entity key `runner` has a sheet time for -> that time,
        already centiseconds -- straight off `library.ratings.runner_times`,
        the exact shape `_tile`'s `goal.get(key)` lookup wants (a key the
        card never asks about costs nothing to carry). Absent, never zero,
        the same rule the reader itself follows: a runner with no time on an
        entity is simply not a key here, which is what keeps `goal_coverage`
        honest about how much of the card this goal actually reaches."""
        if library is None:
            return {}
        adopted_rows = adoptions.rows() if adoptions is not None else {}
        return runner_times(library.payload, adopted_rows,
                            version=version).get(runner, {})

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

    def current_card():
        """`(card, goal_value)` -- the one door both `GET /api/scorecard`
        and the CSV export build from, so a downloaded row can never
        disagree with the card the browser is looking at."""
        keys = card_keys(resolve_seed)
        you = your_times(keys)
        goal_value = service.db.get_state(_GOAL_KEY, None)
        ranks = service.ranks

        goal_map: dict[str, int] = {}
        fold: dict[int, int | None] = {}
        if ranks is not None:
            if goal_value and goal_value.get("kind") == "division":
                goal_map = division_goal_map(keys, goal_value["tier"],
                                             goal_value["division"])
            elif goal_value and goal_value.get("kind") == "runner":
                goal_map = runner_goal_map(goal_value["runner"],
                                           ranks.grading_version)
            fold = fold_choices(goal_value)

        card = build_card(you=you, goal=goal_map, fold=fold,
                          resolve_seed=resolve_seed)
        return card, goal_value

    @router.get("")
    async def get_scorecard():
        card, goal_value = current_card()
        tiles = [tile for row in card["rows"] for tile in row["tiles"]]
        coverage = {"covered": sum(1 for t in tiles if t["goal_cs"] is not None),
                    "tiles": len(tiles)}
        return {**card, "goal": goal_value, "goal_coverage": coverage}

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

    @router.get("/export.csv")
    async def export_csv():
        """The card itself, flattened to the template sheet's own
        Course,Star,Record,Goal,You,Delta layout -- one browser-reachable
        URL (`docs/api.md`), never a download the desktop shell's WebView2
        has to support; the card's own Copy buttons fetch this and copy the
        text instead of navigating here."""
        card, _goal_value = current_card()
        body = _csv_bytes(_csv_rows(card, _record_lookup(library, adoptions, service)))
        return Response(content=body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 'attachment; filename="scorecard.csv"'})

    return router
