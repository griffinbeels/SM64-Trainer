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
`{"kind":"runner","runner":...}` | `{"kind":"custom","name":...}` | `None` --
server-side so the browser and the desktop GUI read the same goal
(`.claude/rules/import.md`'s reasoning for why an imported time lands
server-side applies here too: two clients, one KV). `GET /api/scorecard`
re-derives the whole card from it on every request; nothing about the card
itself is stored. A **custom** goal is the one kind that carries its own
data: hand-typed per-entity times a player saved under a name, in a second
KV (`"scorecard_custom_goals"`, `{name: {entity_key: goal_cs}}`) that
`_GOAL_KEY` only ever points at by name -- so picking a saved custom goal is
the same one-line write as picking a division, and every OTHER saved custom
goal survives switching away from it.

`GET /api/scorecard/column` is the reverse of `POST /api/import/sheet` --
that door reads a runner's column IN as PBs, this one writes YOUR PBs OUT as
a column (`library/export_column.py`), formatted the way the sheet itself
wants a time typed, ready to paste back in next to everyone else's.
"""
import csv
import io
import logging
import threading
import uuid

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from sm64_events.library.examples import sheet_best
from sm64_events.tracking.activestrat import ActiveStrats
from sm64_events.library.export_column import column_lines, sheet_time
from sm64_events.library.ratings import runner_times
from sm64_events.library.sheet import read_rows
from sm64_events.library.source import fetch
from sm64_events.library.store import build_and_stamp
from sm64_events.core.timefmt import attainable_cs
from sm64_events.ranks.classify import RANK_NAMES, display_cs
from sm64_events.ranks.scorecard import (
    FIGHTS_LABEL, build_card, card_keys, division_goal_cs, rows_for_course,
    rows_for_route, template_rows, without_keys)
from sm64_events.ranks.scoring import DIVISION_NUMERALS, best_ladder
from sm64_events.tracking.views import segment_courses
from sm64_events.server.import_api import sheet_row_placer

_log = logging.getLogger("sm64.scorecard")
_GOAL_KEY = "scorecard_goal"
# Named hand-authored goals -- {name: {entity_key: goal_cs}}. A division or
# runner goal RESOLVES per entity every request; a custom one is typed once
# (round-tripped through the card's own live edit, never re-derived) and
# simply looked up here, which is why it needs no resolver of its own below.
_CUSTOM_KEY = "scorecard_custom_goals"
# Which ROM regions a RUNNER goal is allowed to offer a time from -- round 24,
# his mechanism verbatim: "we should define their scorecard based on their
# detected region (e.g., US in my case). BUT it should be a deliberate choice
# ... There must be at least one region enabled at all times. This gives 3
# valid states: US only, JP only, or JP and US combined. When combined for the
# scorecard, we simply take the faster time across both regions."
#
# Server-side beside the goal for the same reason `_GOAL_KEY` is: two clients
# (browser + desktop GUI) reading one choice. ABSENT means "follow the
# detected region", which is not the same as `["us"]` -- an untouched
# scorecard keeps tracking `ranks.grading_version` as a mode flip or a
# detection moves it, and only a deliberate pick freezes it.
_REGIONS_KEY = "scorecard_regions"
_VALID_REGIONS = ("us", "jp")
_VALID_TIERS = [tier for tier in RANK_NAMES if tier != "Iron"]
# How many finished column-export jobs to keep addressable. One client polls
# one job to completion and never looks again, so this only has to outlive a
# burst of clicks -- it is a leak guard, not a cache.
_MAX_COLUMN_JOBS = 8


class GoalBody(BaseModel):
    kind: str
    tier: str | None = None
    division: str | None = None
    runner: str | None = None
    # `custom` only. `name` identifies the saved goal; `times` is present
    # only on a SAVE (create or overwrite) -- selecting an already-saved
    # custom goal from the picker sends `name` alone and the server looks up
    # what was saved last time, the same way a division/runner goal is never
    # re-sent by the picker either.
    name: str | None = None
    times: dict[str, int] | None = None
    # `multi` only: the picked goals themselves, each in the SAME shape a
    # single goal has ({"kind":"division",...} / {"kind":"runner",...} /
    # {"kind":"custom","name":...}). Structured rather than the picker's
    # "division:Bronze:I" value strings on purpose -- a string would need
    # the same parser written in JS and again in Python, and the one thing
    # this project does not allow is a second door onto one rule.
    sources: list[dict] | None = None


class RegionsBody(BaseModel):
    regions: list[str]


def _fetch_column_source(overrides, step=None):
    """Off the event loop, together: `read_rows` and `build_and_stamp`
    (`library/store.py` -- the SAME bytes-to-payload step `LibraryStore.
    refresh()` runs, minus the ladder fit `column_lines` never reads) must
    see the identical bytes `fetch()` just returned, or the two walks fall
    out of step (a target-opening row lines up against the wrong target).
    Both are real CPU work over a ~5.6 MB document (`server/import_api.py`
    notes the same concern for its own refresh). Not persisted -- this is a
    read, not a library refresh.

    `step(fraction, sentence)` is optional and is called BETWEEN the three
    pieces of work, never inside them -- round 26 wanted a status line that
    reports where the copy actually is, and these are the only boundaries
    this function genuinely has. Guessing sub-progress inside the fetch would
    be a timer wearing a measurement's clothes."""
    if step:
        step(0.05, "Reading the Ultimate Sheet…")
    data = fetch()
    if step:
        step(0.45, "Reading the worksheet rows…")
    rows = read_rows(data)
    if step:
        step(0.65, f"Building the library from {len(rows)} rows…")
    payload = build_and_stamp(data, overrides)
    return rows, payload


def _column_resolve(service):
    """Your current PB on ONE named strategy -- `centiseconds | None`,
    `None` when there is no PB on that strategy, or when there is one
    but it was set on the other ROM (`row.version` is only ever non-None on
    a row the sheet itself declares a version for; an unversioned row never
    checks)."""
    def resolve(entity_key, strat_tag, timer_mode, version):
        kind, _, rest = entity_key.partition(":")
        if kind == "star":
            course_id, star_id = rest.split(":")
            pb = service.db.current_pb(int(course_id), int(star_id), timer_mode,
                                       strat_tag=strat_tag,
                                       game_version=version)
        elif kind == "segment":
            pb = service.db.current_pb(None, None, timer_mode,
                                       game_version=version,
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
    """The Stage-RTA/Upstairs-RTA Record cell: the sum of every tile's own
    Record, over exactly the tiles that carry one. Record is
    sheet-derived and, like a tile's own Record cell, does not wait on
    whether You/Goal are also present -- so this is an independent sum, not
    a second read of `row["sum"]`'s you/goal-gated `counted` set. `None`
    when the row's tiles carry no Record at all, never a sum that silently
    drops one."""
    values = [record_of(tile["key"]) for tile in tiles]
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


def _csv_body(card: dict, library, adoptions, service) -> bytes:
    """Off the event loop -- `_csv_rows` walks `_record_lookup` once per
    tile (measured 80-86ms over the full card), the same reason `get_column`
    below threadpools its own sheet walk."""
    return _csv_bytes(_csv_rows(card, _record_lookup(library, adoptions, service)))


def create_scorecard_router(service, library=None, adoptions=None,
                            overrides=None) -> APIRouter:
    """`library`/`adoptions` back the column export below; the goal card
    above is unaffected by either. `overrides` are the human's Library audit
    corrections, applied to a live column-export fetch exactly as a library
    refresh applies them, so a re-fetched sheet does not reintroduce a
    mistake the audit already fixed."""
    router = APIRouter(prefix="/api/scorecard", tags=["scorecard"])
    # Column-export jobs, per router (so a test's app cannot see another's).
    # `compare/service.py`'s shape verbatim: {state, progress, message} plus
    # the finished body under `result`.
    _column_jobs: dict[str, dict] = {}

    def _require_db():
        """`service.db` is `None` on a genuinely BROADCAST-ONLY instance --
        one that lost the db lock -- and every route below reads it. Called
        at the entry of every route that touches `service.db`, directly or
        through `current_card()`, so a bare `AttributeError` never reaches
        the client as an opaque 500 (`ranks_api.py:88`'s own shape). This is
        a DIFFERENT degraded state from `service.ranks is None` (no live
        standards), which the card still answers 200 for, empty goal map and
        all -- only a missing DATABASE is unanswerable."""
        if service.db is None:
            raise HTTPException(503, "scorecard unavailable")

    def _fight_segments() -> list[tuple[str, str, str | None]]:
        """[(entity_key, name, seed_key)] for every Bowser fight -- BY
        CATEGORY, the same fact `ranks/scopes.py::ranks_by_default` keys on,
        so the card and the default-ranking rule can never disagree about
        what counts as a fight (round 9: "all 120 stars, plus the bowser
        fights"). The seed key is what pairs a fight with its Bowser's reds
        star on the Bowser card (round 21)."""
        return [(f"segment:{row['id']}", row["name"], row.get("seed_key"))
                for row in service.db.segment_defs()
                if row.get("category") == "Bowser Fights"]

    def _fight_ids() -> dict[int, str | None]:
        return {row["id"]: row.get("seed_key") for row in service.db.segment_defs()
                if row.get("category") == "Bowser Fights"}

    def _stamp_strats(card: dict) -> None:
        """Each tile's ACTIVE strategy name (or None) -- the card's library
        links land on the approach the player actually practises: "We
        should try to match the user's strategy (if they have one
        selected)" (round 11). Resolved through the same `ActiveStrats`
        every other surface asks, never a second reading of the KV."""
        active = ActiveStrats.from_db(service.db, service.strat_by_star,
                                      service.strat_by_segment)
        for row in card["rows"]:
            for tile in row["tiles"]:
                parts = tile["key"].split(":")
                if parts[0] == "star":
                    tile["strat"] = active.for_star(int(parts[1]), int(parts[2]))
                elif parts[0] == "segment":
                    tile["strat"] = active.for_segment(int(parts[1]))
                else:
                    tile["strat"] = None

    def scope_rows(scope_id: str) -> list[dict]:
        """The scope's row spec, or a 404 for a scope that does not exist --
        the same deliberate 404 `/api/marelo` gives a stale route id, so a
        deleted route reads as GONE rather than silently becoming a
        different card (round 6: "whatever is in the scope is what we
        generate a scorecard for")."""
        if scope_id == "overall":
            return template_rows(fight_segments=_fight_segments())
        kind, _, rest = scope_id.partition(":")
        if kind == "course" and rest.isdigit():
            try:
                return rows_for_course(int(rest))
            except LookupError:
                raise HTTPException(404, f"no scorecard for {scope_id!r}")
        if kind == "route" and rest.isdigit():
            route = next((row for row in service.db.routes()
                          if row["id"] == int(rest)), None)
            if route is not None:
                labels = {row["id"]: row["name"]
                          for row in service.db.segment_defs()}
                return rows_for_route(
                    route, segment_labels=labels,
                    segment_courses=segment_courses(service.db),
                    fight_segment_ids=_fight_ids())
        raise HTTPException(404, f"unknown scope {scope_id!r}")

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

    # `runner_times` walks EVERY target, approach, subsection and entry on the
    # sheet and answers for every runner at once, of which a runner goal keeps
    # one map. Measured 2026-09-01 on a snapshot of his own db through the real
    # endpoint (TestClient, median of 5): GET /api/scorecard costs 7.6 ms with
    # no goal, 10.8 ms against a division, 23.8 ms against one runner, 49.6 ms
    # against his stored goal (a division + three runners) and 68.9 ms against
    # four runners -- a multi goal walked the sheet once per runner SOURCE.
    # Since round 20 that fetch runs on every completed attempt, on the same
    # process as the 30 fps poller, so the walk is memoised: one per (payload,
    # adoptions, grading version), reused across sources and across fetches.
    # The memo HOLDS the payload object, so its id cannot be recycled under
    # the key while it is cached; a sheet refresh replaces the object, an
    # adoption changes the rows tuple, a version flip changes the string.
    _runner_times_memo: dict = {}

    def runner_times_for(version: str) -> dict[str, dict[str, int]]:
        payload = library.payload
        adopted_rows = adoptions.rows() if adoptions is not None else {}
        key = (id(payload), payload.get("sheet_revision"), payload.get("fetched_at"),
               version, tuple(sorted(adopted_rows.items())))
        if _runner_times_memo.get("key") != key:
            _runner_times_memo.update(
                key=key, payload=payload,
                value=runner_times(payload, adopted_rows, version=version))
        return _runner_times_memo["value"]

    def scorecard_regions(ranks) -> list[str]:
        """The regions a runner goal may offer times from, in `_VALID_REGIONS`
        order and never empty. A missing or corrupt KV reads as "follow the
        detected region" -- `ranks.grading_version`, the same value
        `core/modes.py::effective_version` feeds `GET /api/mode` as
        `effective` -- rather than as a hard-coded US, so an untouched
        scorecard tracks a mode flip the way it always has."""
        stored = service.db.get_state(_REGIONS_KEY, None)
        picked = [region for region in (stored or [])
                  if region in _VALID_REGIONS] if isinstance(stored, list) else []
        if not picked:
            detected = ranks.grading_version if ranks is not None else "us"
            picked = [detected if detected in _VALID_REGIONS else "us"]
        return [region for region in _VALID_REGIONS if region in picked]

    def runner_goal_map(runner: str, regions: list[str]) -> dict[str, int]:
        """Every entity key `runner` has a sheet time for -> that time,
        already centiseconds -- straight off `library.ratings.runner_times`,
        the exact shape `_tile`'s `goal.get(key)` lookup wants (a key the
        card never asks about costs nothing to carry). Absent, never zero,
        the same rule the reader itself follows: a runner with no time on an
        entity is simply not a key here, which is what keeps `goal_coverage`
        honest about how much of the card this goal actually reaches.

        With BOTH regions on, the FASTER of the two wins per entity -- his
        round-24 words, "we simply take the faster time across both regions"
        -- which is the same minimise-then-merge shape `resolve_multi`
        already applies across goal SOURCES, one level further in. Read once
        per region (the memo below is keyed by region, so the second read is
        free after the first fetch), never by widening `ratings.py`'s own
        single-region contract: `_visible_entries` answers "what does this
        row look like on THIS ROM", and a row's JP ladder is a different
        ladder, not a longer list."""
        if library is None:
            return {}
        best: dict[str, int] = {}
        for region in regions:
            for key, time_cs in runner_times_for(region).get(runner, {}).items():
                if key not in best or time_cs < best[key]:
                    best[key] = time_cs
        return best

    def custom_goal_store() -> dict[str, dict[str, int]]:
        # A corrupt KV (wrong type, from a schema this store never wrote)
        # reads as absent rather than 500ing every route that touches it --
        # the same "treat garbage as no goal" rule `current_card()` applies
        # to `_GOAL_KEY` below.
        store = service.db.get_state(_CUSTOM_KEY, {})
        return store if isinstance(store, dict) else {}

    def custom_goal_map(keys: list[str], name: str) -> dict[str, int]:
        """A saved custom goal's own times, filtered to keys the card still
        carries -- a stale key (its segment definition since deleted) drops
        silently, same as every other "no goal on this tile" case, rather
        than surfacing a key `_tile` would never look up anyway."""
        saved = custom_goal_store().get(name, {})
        return {key: cs for key, cs in saved.items() if key in keys}

    def resolve_goal(goal_value, keys: list[str], ranks) -> dict[str, int]:
        """One goal value -> {entity key: goal centiseconds}.

        A custom goal is typed data with no ladder lookup at all, so it
        resolves independent of `ranks` -- a broadcast-only instance can
        still grade against a hand-picked target even though it can never
        grade against a division or a runner.

        A MULTI goal resolves each source through this same function and
        keeps the FASTEST offer per entity -- his round-16 correction of
        round 14's reading ("I meant we should take the FASTEST time from
        all of the runners / all of the options provided"), verified
        against the Library: on Scale the Mountain his card showed Toad 1's
        19"02 while ikori has 16"53, because the merge had been taking the
        slowest source. Two levels, both his words: WITHIN an option, the
        fastest that option has on that star across every strategy
        (`ratings.best_entries` already minimises over every approach and
        subsection mapping to the entity -- RONC3NA's 17"50 on "Log firsty"
        beats his own 17"83 on the plain row); ACROSS options, the fastest
        of those. Coverage is still the UNION, so a runner who never
        entered a star is covered by whoever did ("if you are tracking
        multiple different runners, then you should have 100% coverage
        across all stars")."""
        if not goal_value:
            return {}
        kind = goal_value.get("kind")
        if kind == "custom":
            return custom_goal_map(keys, goal_value["name"])
        if kind == "multi":
            merged, _ = resolve_multi(goal_value, keys, ranks)
            return merged
        if ranks is None:
            return {}
        if kind == "division":
            return division_goal_map(keys, goal_value["tier"],
                                     goal_value["division"])
        if kind == "runner":
            return runner_goal_map(goal_value["runner"], scorecard_regions(ranks))
        return {}

    def resolve_multi(goal_value, keys: list[str], ranks):
        """A multi goal -> `({key: cs}, {key: source index})`.

        WHICH source won is recorded where the comparison happens, not
        re-derived later: the card's attribution -- his round-15 ask, a
        coloured dot after each star naming the pick that set it ("that
        clearly tells us that player two is the reason that the goal is
        that time") -- has to agree with the number beside it, and the only
        way two surfaces cannot disagree is for one of them to never
        compute it. Ties keep the EARLIER source, so the legend order is
        also the tie-break and the attribution never depends on dict
        iteration order."""
        merged: dict[str, int] = {}
        owner: dict[str, int] = {}
        for index, source in enumerate(goal_value.get("sources") or []):
            if not isinstance(source, dict) or source.get("kind") == "multi":
                continue                     # never nest; a corrupt KV is empty
            for key, cs in resolve_goal(source, keys, ranks).items():
                if key not in merged or cs < merged[key]:
                    merged[key] = cs
                    owner[key] = index
        return merged, owner

    def current_card(scope_id: str = "overall"):
        """`(card, goal_value)` -- the one door both `GET /api/scorecard`
        and the CSV export build from, so a downloaded row can never
        disagree with the card the browser is looking at. Scope-driven
        since round 6: the rows are whatever `scope_id` contains."""
        _require_db()
        # The SAME exclusion set the scope's own RATING drops (`_score_scope`
        # filters `service.rank_excluded()` over `scopes.default_excluded`) --
        # round 7: "This should match the route include/ignores logic... those
        # are already ignored in the route ranking list, so we should ignore
        # them here as well." Read, never re-derived: a card grading a
        # movement its own rank ignores would be scoring a different route
        # than the number printed beside it.
        rows_spec = without_keys(scope_rows(scope_id), service.rank_excluded())
        keys = card_keys(rows_spec)
        you = your_times(keys)
        goal_value = service.db.get_state(_GOAL_KEY, None)
        if not isinstance(goal_value, dict):
            goal_value = None                # a corrupt KV reads as no goal
        ranks = service.ranks

        # A MULTI goal also answers WHO: the source index that set each
        # tile, so the card can attribute every number to the pick behind
        # it (round 15). Any other kind has one source and needs no legend.
        if goal_value and goal_value.get("kind") == "multi":
            goal_map, goal_owner = resolve_multi(goal_value, keys, ranks)
        else:
            goal_map, goal_owner = resolve_goal(goal_value, keys, ranks), {}

        card = build_card(rows_spec, you=you, goal=goal_map)
        _stamp_strats(card)
        for row in card["rows"]:
            for tile in row["tiles"]:
                tile["goal_source"] = goal_owner.get(tile["key"])
        return card, goal_value

    @router.get("")
    async def get_scorecard(scope: str = "overall"):
        card, goal_value = current_card(scope)
        tiles = [tile for row in card["rows"] for tile in row["tiles"]]
        coverage = {"covered": sum(1 for t in tiles if t["goal_cs"] is not None),
                    "tiles": len(tiles)}
        # Every saved custom goal's NAME, so the picker can list them all
        # (grouped ahead of Divisions) without a second round trip -- the
        # active one, if any, is already carried in `goal` above.
        custom_names = sorted(custom_goal_store().keys())
        ranks = service.ranks
        return {**card, "scope": scope, "goal": goal_value,
                "goal_coverage": coverage, "custom_goals": custom_names,
                "regions": scorecard_regions(ranks),
                "detected_region": (ranks.grading_version
                                    if ranks is not None else "us")}

    @router.put("/goal")
    async def set_goal(body: GoalBody | None = Body(default=None)):
        """No broadcast: the goal picker is the only writer, and the card
        refetches on `t.mareloRev` like the rest of the Rank tab -- there is
        no second client watching this KV live the way the recorder's row
        list needs a push.

        `kind: "custom"` is two operations behind one shape, same distinction
        `PUT /api/ranks/standards` already draws between writing a value and
        selecting an existing one: `times` present SAVES (creating a new name
        or overwriting one already used -- the store is a plain dict keyed by
        name, so "overwrite" needs no separate branch, just a second write to
        the same key), `times` absent SELECTS a goal the picker already knows
        about, 404 if that name was never saved. Either way the KV ends up
        naming just the goal (`{"kind":"custom","name":...}`) -- the actual
        times live in `_CUSTOM_KEY`, not duplicated into `_GOAL_KEY`."""
        _require_db()
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
        elif body.kind == "custom":
            name = (body.name or "").strip()
            if not name:
                raise HTTPException(422, "a custom goal needs a name")
            store = custom_goal_store()
            if body.times is not None:
                # Snap every typed time onto the displayable set through THE
                # existing door (core/timefmt.attainable_cs, the same rule the
                # import's hand-entry field applies) -- only 30 of every 100
                # centisecond values can appear on the timer, and the field
                # already shows the snapped value, so storing the raw one
                # would make a hand-posted payload disagree with every cell
                # the UI draws (round 5, 2026-08-24: "leverage the existing
                # time entry validation system").
                store[name] = {key: attainable_cs(int(cs))
                               for key, cs in body.times.items()}
                service.db.set_state(_CUSTOM_KEY, store)
            elif name not in store:
                raise HTTPException(404, f"no saved custom goal named {name!r}")
            value = {"kind": "custom", "name": name}
        elif body.kind == "multi":
            # Every source is validated by the SAME rules a single goal of
            # that kind is, so "several goals at once" can never smuggle in
            # a tier or a runner that a single pick would have refused. A
            # custom source must already be saved -- a multi pick names
            # existing goals, it never creates one.
            sources = body.sources or []
            if not sources:
                raise HTTPException(422, "a multi goal needs at least one source")
            store = custom_goal_store()
            cleaned = []
            for source in sources:
                kind = (source or {}).get("kind")
                if kind == "division":
                    if (source.get("tier") not in _VALID_TIERS
                            or source.get("division") not in DIVISION_NUMERALS):
                        raise HTTPException(
                            422, f"unknown tier/division in {source!r}")
                    cleaned.append({"kind": "division", "tier": source["tier"],
                                    "division": source["division"]})
                elif kind == "runner":
                    if not source.get("runner"):
                        raise HTTPException(422, "a runner source needs a name")
                    cleaned.append({"kind": "runner", "runner": source["runner"]})
                elif kind == "custom":
                    name = (source.get("name") or "").strip()
                    if name not in store:
                        raise HTTPException(
                            404, f"no saved custom goal named {name!r}")
                    cleaned.append({"kind": "custom", "name": name})
                else:
                    raise HTTPException(422, f"unknown source kind {kind!r}")
            value = {"kind": "multi", "sources": cleaned}
        else:
            raise HTTPException(422, f"unknown goal kind {body.kind!r}")
        service.db.set_state(_GOAL_KEY, value)
        return {"goal": value}

    @router.put("/regions")
    async def set_regions(body: RegionsBody):
        """Which regions a runner goal may offer times from. At least one --
        an empty list is a 422 rather than a silent fall back to the detected
        region, because "show me nothing" and "follow my ROM" are different
        intentions and the control never sends the first. Stored in
        `_VALID_REGIONS` order so the KV cannot hold two spellings of the
        same choice."""
        _require_db()
        picked = [region for region in _VALID_REGIONS if region in body.regions]
        unknown = [region for region in body.regions
                   if region not in _VALID_REGIONS]
        if unknown:
            raise HTTPException(422, f"unknown region(s) {unknown!r}")
        if not picked:
            raise HTTPException(422, "at least one region stays on")
        service.db.set_state(_REGIONS_KEY, picked)
        return {"regions": picked}

    @router.get("/column")
    async def get_column():
        """Your PBs as the Ultimate Sheet's own column
        (`library/export_column.py::column_lines`) -- one line per live
        worksheet row, ready to paste back into the sheet. Always a LIVE
        fetch, never the cached snapshot: the row layout has to match the
        sheet you are about to paste into (same reasoning as `POST
        /api/import/sheet`'s own refresh)."""
        _require_db()
        if library is None:
            raise HTTPException(503, "sheet library unavailable")
        try:
            rows, payload = await run_in_threadpool(_fetch_column_source, overrides)
        except Exception as err:
            _log.warning("column export could not read the sheet: %r", err)
            raise HTTPException(
                503, f"could not read the sheet: {err}") from err
        return _column_body(*_resolve_column(rows, payload))

    def _resolve_column(rows, payload):
        place = sheet_row_placer(service, adoptions)
        lines = column_lines(rows, payload, _column_resolve(service), place=place)
        return lines, payload

    def _column_body(lines, payload):
        """The one shape both column doors answer with -- the synchronous GET
        and the job's `result` -- so a client can be moved from one to the
        other without learning a second payload."""
        return {"lines": lines, "sheet_revision": payload.get("sheet_revision"),
                "mapped": sum(1 for line in lines if line),
                "total_rows": len(lines)}

    def _run_column_job(job_id: str):
        """The same work `get_column` does, on a thread, reporting where it
        is. Steps are the function's REAL boundaries (fetch, parse, build,
        resolve) -- see `_fetch_column_source`. The final message is the one
        that answers his other question in the same breath: how many rows,
        which worksheet rows they cover, and how many carry a time."""
        job = _column_jobs[job_id]

        def step(fraction, message):
            job["progress"] = fraction
            job["message"] = message

        try:
            rows, payload = _fetch_column_source(overrides, step=step)
            step(0.85, "Matching your times to the sheet's rows…")
            lines, payload = _resolve_column(rows, payload)
            body = _column_body(lines, payload)
            job["result"] = body
            job["progress"] = 1.0
            job["message"] = (
                f"Copied {body['total_rows']} rows (sheet rows 2–"
                f"{body['total_rows'] + 1}) · {body['mapped']} carry a time")
            job["state"] = "done"
        except Exception as err:                        # noqa: BLE001
            _log.warning("column export could not read the sheet: %r", err)
            job["state"] = "error"
            job["message"] = f"could not read the sheet: {err}"

    @router.post("/column")
    async def start_column():
        """Start a column export and report it as it goes -- round 26, his
        ask: "a status line that updates at every step of the process...
        Right now it feels like lag, but I know that's just how long it takes
        to confirm things."

        A second door beside the synchronous GET rather than a replacement:
        that URL is documented, browser-reachable and separately tested, and
        the honest answer to a wait is to narrate it, not to move it. The job
        shape is `compare/service.py`'s, verbatim in structure
        (`{state, progress, message}` + a background thread + a status GET),
        because a second progress vocabulary is a second thing to learn."""
        _require_db()
        if library is None:
            raise HTTPException(503, "sheet library unavailable")
        job_id = uuid.uuid4().hex
        # Bounded: a session that copies all day must not grow this map
        # forever, and a finished job nobody polled is of no use to anyone.
        for stale in list(_column_jobs)[:-_MAX_COLUMN_JOBS]:
            _column_jobs.pop(stale, None)
        _column_jobs[job_id] = {"state": "running", "progress": 0.0,
                                "message": "Starting…", "result": None}
        threading.Thread(target=_run_column_job, name="scorecard-column",
                         daemon=True, args=(job_id,)).start()
        return {"job_id": job_id}

    @router.get("/column/{job_id}")
    async def column_status(job_id: str):
        job = _column_jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such column export job")
        return dict(job)          # a shallow copy: callers never mutate it

    @router.get("/export.csv")
    async def export_csv(scope: str = "overall"):
        """The card itself, flattened to the template sheet's own
        Course,Star,Record,Goal,You,Delta layout -- one browser-reachable
        URL (`docs/api.md`), never a download the desktop shell's WebView2
        has to support; the card's own Copy buttons fetch this and copy the
        text instead of navigating here."""
        card, _goal_value = current_card(scope)
        body = await run_in_threadpool(_csv_body, card, library, adoptions, service)
        return Response(content=body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 'attachment; filename="scorecard.csv"'})

    return router
