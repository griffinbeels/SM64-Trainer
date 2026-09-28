"""REST for the goal-vs-you scorecard. Endpoint contracts are `docs/api.md`'s
`GET /api/scorecard`, `PUT /api/scorecard/goal` and `GET /api/scorecard/column`
rows, plus the per-entry goal doors (`/entry_goals`, `/entry_goal_options`,
`PUT /entry_goal`, `tests/test_scorecard_entry_goals.py`); router behavior is
`tests/test_scorecard_api.py`.

Its own router rather than a block in `ranks_api.py` for the same reason
`import_api.py` split off: that file is already +170 lines for the
leaderboard branch, and this needs a different pair of injected pieces
(the tracker service for PBs and standards; `library`/`adoptions` also
back a runner goal -- `library.ratings.runner_times`, the SAME reader
`library/board.py`'s leaderboard grades runners with, read straight off the
CACHED snapshot like the column export's record row below, never a live
fetch).

The `scorecard_goal` KV stores one rank plus any players/custom sets, using
single/multi shapes. Automatic stores no calculated tier; each read resolves
it from the current scope's MARELO. Named custom sets live in the separate
`scorecard_custom_goals` KV. `scorecard_goals.py` owns normalization and writes;
`scorecard_standards.py` chooses each rank target's canonical Library row.

`GET /api/scorecard/column` is the reverse of `POST /api/import/sheet` --
that door reads a runner's column IN as PBs, this one writes YOUR PBs OUT as
a column (`library/export_column.py`), formatted the way the sheet itself
wants a time typed, ready to paste back in next to everyone else's.
"""
import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, StrictBool, StrictInt

from sm64_events.library.examples import sheet_best
from sm64_events.library.export_column import column_cells, sheet_time
from sm64_events.library.sheet import read_rows
from sm64_events.library.source import fetch
from sm64_events.library.store import build_and_stamp
from sm64_events.core.events import Event
from sm64_events.core.modes import platform_of
from sm64_events.ranks.classify import display_cs
from sm64_events.ranks.scorecard import (
    build_card, rows_for_course, rows_for_route, template_rows, without_keys)
from sm64_events.tracking.views import (latest_pbs_by_strategy,
                                        segment_courses)
from sm64_events.server.import_api import sheet_row_placer
from sm64_events.server.jobs import JobBoard
from sm64_events.server.scorecard_goals import prepare_goal
from sm64_events.server.scorecard_resolve import (
    CUSTOM_KEY, GOAL_KEY, REGIONS_KEY, VALID_REGIONS, GoalResolver, entry_goal,
    pin_of, require_entry_key)
from sm64_events.server.ranks_api import absorb_after_regrade

_log = logging.getLogger("sm64.scorecard")
# Round 29 item 2: how the pasted column is COLOURED. A time set on the
# emulator and one set on a console wear different fills in the sheet
# (Raisn's convention, and his ask: "The user should be able to select a color
# for N64 times, and select a color for EMU times... I guess you should also be
# able to choose the font color"), plus the font the cells are typed in. One
# stored preference, edited in Settings with a live preview -- an inspector,
# never a guessed constant -- and these defaults are only its starting point:
# a blue / orange pair "darker than Raisn's setup" (his A5A9F1 / FF6D01), white
# text on both, and Trebuchet MS (round 30, his call: "Default should be
# Trebuchet MS").
_SHEET_STYLE_KEY = "sheet_style"
# Round 31 darkened two of them on sight: "The orange should also be a
# default darker orange, and the white text should be a darker offwhite."
DEFAULT_SHEET_STYLE = {"emu_fill": "#4F7BE0", "n64_fill": "#C45E1C",
                       "font_color": "#F2ECE4", "font_family": "Trebuchet MS"}
_HEX_COLOUR = re.compile(r"^#[0-9A-Fa-f]{6}$")


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


class EntryGoalBody(BaseModel):
    key: str
    goal: dict | None = None
    scope: str = "overall"


class ForgiveBody(BaseModel):
    key: str
    attempt_id: StrictInt
    undo: StrictBool = False
    scope: str = "overall"


class RegionsBody(BaseModel):
    regions: list[str]


class SheetStyleBody(BaseModel):
    emu_fill: str
    n64_fill: str
    font_color: str
    font_family: str


def sheet_style(db) -> dict:
    """The stored sheet style over the defaults -- a missing or corrupt KV,
    or one field of it, reads as the default for that field alone."""
    stored = db.get_state(_SHEET_STYLE_KEY, None) if db is not None else None
    style = dict(DEFAULT_SHEET_STYLE)
    if isinstance(stored, dict):
        for key in style:
            value = stored.get(key)
            if isinstance(value, str) and value:
                style[key] = value
    return style


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


async def _call_soon(fn):
    """Run a plain callable on the event loop -- the shape
    `run_coroutine_threadsafe` needs to hand a thread's work to the loop."""
    return fn()


def _column_identity(entity_key):
    """`(course_id, star_id, segment_id)` for a star or segment key; None
    for anything else."""
    kind, _, rest = entity_key.partition(":")
    if kind == "star":
        course_id, star_id = rest.split(":")
        return int(course_id), int(star_id), None
    if kind == "segment":
        return None, None, int(rest)
    return None


def _column_resolve(service):
    """Your current PB on ONE named strategy -- `centiseconds | None`,
    `None` when there is no PB on that strategy, or when there is one
    but it was set on the other ROM (`row.version` is only ever non-None on
    a row the sheet itself declares a version for; an unversioned row never
    checks).

    With `strat_tag=None` it is the LEFTOVERS ask (round 28): the fastest
    current PB on the entity filed under no name in `excluding` -- the
    names the block's other rows claim -- on the row's ROM. Fastest,
    not latest, because a row that names the thing prints "your best on
    it however you set it" (round 25), and the latest save on a star is
    whichever strategy he happened to run last. A PB with no strategy is
    unclaimed by every row and so always eligible."""
    ranks = getattr(service, "ranks", None)
    latest_rows = []

    def set_on(pb, version):
        """Was this PB set on `version`? An unversioned row grades on the
        running ROM, so it counts as that one and no other."""
        if version is None:
            return True
        pb_version = pb.get("game_version") or (
            ranks.grading_version if ranks is not None else None)
        return pb_version == version

    def leftovers(identity, timer_mode, version, excluding):
        if not latest_rows:
            # Latest row per (entity, clock, strategy) wins -- the one
            # reading `views.latest_pbs_by_strategy` is; read once per
            # column rather than once per worksheet row.
            latest_rows.append(latest_pbs_by_strategy(service.db.pbs()))
        course_id, star_id, segment_id = identity
        best = None
        for (course, star, segment, mode, strat, _rom), pb in latest_rows[0].items():
            if (course, star, segment, mode) != (course_id, star_id,
                                                segment_id, timer_mode):
                continue
            if strat in excluding or not set_on(pb, version):
                continue
            if best is None or pb["frames"] < best["frames"]:
                best = pb
        return best

    def answer(pb):
        if pb is None:
            return None
        time = (display_cs(pb["frames"]), pb.get("platform"))
        # The chosen PB's exact attempt supplies the public URL. A faster
        # unlinked attempt must never inherit the previous PB's recording.
        attempt_id = pb.get("attempt_id")
        video = (service.recording_link(attempt_id)["url"]
                 if attempt_id is not None else None)
        return (*time, video) if video else time

    def resolve(entity_key, strat_tag, timer_mode, version, *, excluding=()):
        identity = _column_identity(entity_key)
        if identity is None:
            return None
        # `(cs, platform)`: the PB's own stamp, through its attempt (v27),
        # so the column can colour a cell by the machine that set the time
        # (round 29 item 2). Left unresolved here -- `platform_of` runs once,
        # in `_column_body`, so no second reader grows its own emulator default.
        if strat_tag is None:
            pb = leftovers(identity, timer_mode, version, frozenset(excluding))
            return answer(pb)
        course_id, star_id, segment_id = identity
        pb = service.db.current_pb(course_id, star_id, timer_mode,
                                   segment_id=segment_id, strat_tag=strat_tag,
                                   game_version=version)
        if pb is None or not set_on(pb, version):
            return None
        return answer(pb)
    return resolve


def _held_lookup(service):
    """`held(row_key, version) -> (cs, platform) | None` over the db's HELD
    TIMES -- the cells an import kept aside for rows with no home (round
    28), each with the platform its column's legend named (round 29 item 2,
    None when it did not). A versioned worksheet row wants the cell set on
    that ROM; an unversioned one takes whatever the row holds (a JP-stamped
    target's rows carry no version of their own but their cells do). Later
    holds win, as pbs do."""
    by_row = {}
    for cell in service.db.held_times():
        time = (int(cell["time_cs"]), cell.get("platform"))
        by_row.setdefault(cell["row_key"], {})[cell.get("game_version")] = (
            (*time, cell["video"]) if cell.get("video") else time)

    def held(key, version):
        cells = by_row.get(key)
        if not cells:
            return None
        if version is not None:
            return cells.get(version)
        return next(iter(cells.values()))
    return held


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
    Record, over exactly the tiles that carry one -- the same each-side-
    sums-its-own-lines rule `row["sum"]`'s You and Goal follow since round
    29, computed here because Record is sheet-derived and never enters the
    card's tiles. `None` when the row's tiles carry no Record at all, never
    a sum that silently drops one."""
    values = [record_of(tile["key"]) for tile in tiles]
    values = [value for value in values if value is not None]
    return sum(values) if values else None


def _csv_tile_row(course_label: str, tile: dict, record_cs: int | None) -> list[str]:
    return [course_label, tile["label"], _csv_time(record_cs),
            _csv_time(tile["goal_cs"]), _csv_time(tile["you_cs"]),
            _csv_signed_time(tile["delta_cs"])]


def _csv_sum_row(course_label: str, star_label: str, tiles: list[dict],
                 sum_obj: dict, record_of) -> list[str]:
    """Record, Goal and You each sum their own lines (`None` -> an empty
    cell); Delta is `_sum_tiles`' shared-set gap, so it is blank whenever
    the two columns share no line."""
    return [course_label, star_label, _csv_time(_record_sum(tiles, record_of)),
            _csv_time(sum_obj["goal_cs"]), _csv_time(sum_obj["you_cs"]),
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


def _stamped(goals_by_key: dict) -> dict:
    """Each EntryGoal with `resolved_utc`, taken AFTER resolving, so every
    attempt the goals counted ended before it: the goal plate celebrates a run
    only against the goal it showed when that run ended
    (goalplate.js::goalShownAt)."""
    resolved_utc = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {key: {**goal, "resolved_utc": resolved_utc} for key, goal in goals_by_key.items()}


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
    _column_jobs = JobBoard()
    goals = GoalResolver(service, library, adoptions)

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
        resolved = goals.resolve_entries(rows_spec, scope_id)
        card = build_card(rows_spec, you=resolved["you"], goal=resolved["goal"])
        for row in card["rows"]:
            for tile in row["tiles"]:
                key = tile["key"]
                pinned = key in resolved["pins"]
                tile["strat"] = resolved["strategies"].get(key)
                # A pin is not a legend source: it draws no dot (`goal_source`
                # null) and says what it is through `goal_pinned` instead.
                tile["goal_source"] = None if pinned else resolved["owner"].get(key)
                tile["goal_pinned"] = pinned
                tile["player_goal_cs"] = resolved["players"].get(key)
        return card, resolved["goal_value"]

    def entry_rows(scope_id: str, keys: list[str]) -> list[dict]:
        for key in keys:
            require_entry_key(key)
        _require_db()
        return goals.entry_spec(keys, without_keys(scope_rows(scope_id),
                                                   service.rank_excluded()))

    def entry_goals(scope_id: str, keys: list[str]) -> dict:
        rows_spec = entry_rows(scope_id, keys)
        resolved = goals.resolve_entries(rows_spec, scope_id)
        clocks = {key: clock for key, _label, clock in rows_spec[0]["entries"]}
        resolved["progress"] = goals.entry_progress(resolved, clocks)
        return _stamped({key: entry_goal(key, clock, resolved)
                         for key, clock in clocks.items()})

    async def goal_changed(key: str, scope_id: str) -> dict:
        """The key's EntryGoal after a write, and the `goal_changed` broadcast
        every open Practice card and Scorecard refetches on."""
        answer = await run_in_threadpool(entry_goals, scope_id, [key])
        await service.broadcaster.publish(Event(
            type="goal_changed", frame=0, timestamp_utc=datetime.now(timezone.utc),
            payload={"key": key}))
        return answer[key]

    @router.get("")
    def get_scorecard(scope: str = "overall"):
        # Scope scoring reads the full attempt history. Threadpool this read
        # like /marelo so automatic goals never occupy the recorder's loop.
        card, goal_value = current_card(scope)
        tiles = [tile for row in card["rows"] for tile in row["tiles"]]
        coverage = {"covered": sum(1 for t in tiles if t["goal_cs"] is not None),
                    "tiles": len(tiles)}
        # Every saved set's name, so Custom Goals needs no second request. The
        # active one, if any, is already carried in `goal` above.
        custom_names = sorted(goals.custom_goal_store().keys())
        ranks = service.ranks
        return {**card, "scope": scope, "goal": goal_value,
                "goal_coverage": coverage, "custom_goals": custom_names,
                "regions": goals.scorecard_regions(ranks),
                "detected_region": (ranks.grading_version
                                    if ranks is not None else "us")}

    @router.put("/goal")
    async def set_goal(body: GoalBody | None = Body(default=None)):
        """Save one normalized selection and any explicit custom-set patches.

        No broadcast: this client refetches its current scope through the
        ordered write queue. Computed automatic tiers never enter the KV.
        """
        _require_db()
        value, store = prepare_goal(body.model_dump(exclude_none=True)
                                    if body is not None else None, goals.custom_goal_store())
        service.db.set_state(CUSTOM_KEY, store)
        service.db.set_state(GOAL_KEY, value)
        return {"goal": value}

    @router.get("/entry_goals")
    def get_entry_goals(scope: str = "overall", keys: str | None = None):
        """Each requested entry's goal exactly as its Scorecard tile resolves
        it (`resolve_entries`); `keys` omitted means every pinned entry.
        Plain `def` so the scope rating runs on the threadpool, like the card."""
        _require_db()
        wanted = ([key for key in keys.split(",") if key] if keys is not None
                  else list(goals.entry_pins()))
        return {"scope": scope,
                "goals": entry_goals(scope, list(dict.fromkeys(wanted)))}

    @router.get("/entry_goal_options")
    def get_entry_goal_options(key: str, scope: str = "overall"):
        """What a Practice card can pin on one entry: every finite division
        on its strategy ladder and every runner with a time on it in the
        card's regions, each fastest first."""
        rows_spec = entry_rows(scope, [key])
        resolved = goals.resolve_entries(rows_spec, scope)
        return {"key": key, "clock": rows_spec[0]["entries"][0][2],
                "you_cs": resolved["you"].get(key),
                **goals.entry_options(key, resolved["strategies"].get(key))}

    @router.put("/entry_goal")
    async def set_entry_goal(body: EntryGoalBody):
        """Pin one entry's goal, or unpin it with `goal: null`, then broadcast
        `goal_changed` so every open Practice card and Scorecard refetches."""
        _require_db()
        require_entry_key(body.key)
        pin = pin_of(body.goal)
        scope_rows(body.scope)            # an unknown scope 404s before the write
        goals.set_pin(body.key, pin)
        return await goal_changed(body.key, body.scope)

    @router.post("/entry_goal/forgive")
    async def forgive_attempt(body: ForgiveBody):
        """Forgive one attempt on a repeat goal (it neither counts nor breaks
        the streak), or with `undo: true` take the forgiveness back."""
        _require_db()
        require_entry_key(body.key)
        scope_rows(body.scope)
        goals.forgive(body.key, body.attempt_id, undo=body.undo)
        return await goal_changed(body.key, body.scope)

    @router.put("/regions")
    async def set_regions(body: RegionsBody):
        """Which regions a runner goal may offer times from. At least one --
        an empty list is a 422 rather than a silent fall back to the detected
        region, because "show me nothing" and "follow my ROM" are different
        intentions and the control never sends the first. Stored in
        `VALID_REGIONS` order so the KV cannot hold two spellings of the
        same choice."""
        _require_db()
        picked = [region for region in VALID_REGIONS if region in body.regions]
        unknown = [region for region in body.regions
                   if region not in VALID_REGIONS]
        if unknown:
            raise HTTPException(422, f"unknown region(s) {unknown!r}")
        if not picked:
            raise HTTPException(422, "at least one region stays on")
        service.db.set_state(REGIONS_KEY, picked)
        return {"regions": picked}

    @router.get("/sheet_style")
    async def get_sheet_style():
        """How a pasted column is coloured -- `style` is what applies now,
        `defaults` what Reset returns to."""
        return {"style": sheet_style(service.db), "defaults": dict(DEFAULT_SHEET_STYLE)}

    @router.put("/sheet_style")
    async def put_sheet_style(body: SheetStyleBody):
        """Three colours as `#RRGGBB` and a font family name (1-64 characters,
        no CSS punctuation: it is written straight into an inline style)."""
        _require_db()
        colours = {"emu_fill": body.emu_fill, "n64_fill": body.n64_fill,
                   "font_color": body.font_color}
        bad = [name for name, value in colours.items() if not _HEX_COLOUR.match(value)]
        if bad:
            raise HTTPException(422, f"{', '.join(bad)}: a colour is #RRGGBB")
        family = body.font_family.strip()
        if not family or len(family) > 64 or any(ch in family for ch in ';:"\'{}<>'):
            raise HTTPException(422, "font_family: a font name, 1-64 characters")
        style = {**{name: value.upper() for name, value in colours.items()},
                 "font_family": family}
        service.db.set_state(_SHEET_STYLE_KEY, style)
        return {"style": style, "defaults": dict(DEFAULT_SHEET_STYLE)}

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
        cells = column_cells(rows, payload, _column_resolve(service),
                             place=place, held=_held_lookup(service), legend=True)
        return cells, payload

    def _column_body(cells, payload):
        """The one shape both column doors answer with -- the synchronous GET
        and the job's `result` -- so a client can be moved from one to the
        other without learning a second payload. `lines` is the column as
        text; `cells` (round 29 item 2) pairs each line with the machine that
        set its time -- `platform_of` resolves an unstamped PB to the
        emulator HERE, once, so every timed cell names "emu" or "n64" and an
        empty cell names nothing."""
        lines = [cell["text"] for cell in cells]
        return {"lines": lines, "sheet_revision": payload.get("sheet_revision"),
                "cells": [{"text": cell["text"],
                           "platform": platform_of(cell["platform"]) if cell["text"] else None,
                           **({"video": cell["video"]} if cell.get("video") else {}),
                           **({"legend": True} if cell.get("legend") else {})}
                          for cell in cells],
                # The two legend cells are not times.
                "mapped": sum(1 for cell in cells if cell["text"] and not cell.get("legend")),
                "total_rows": len(lines)}

    def _column_work(step, resync=None):
        """The same work `get_column` does, on the job board's thread,
        reporting where it is. Steps are the function's REAL boundaries
        (fetch, parse, build, resolve) -- see `_fetch_column_source`. The
        closing sentence answers his other question in the same breath: how
        many rows, which worksheet rows they cover, and how many carry a
        time -- the BUILD's sentence, not the copy's, since round 29 holds
        the column on the page and the clipboard write is the button's own
        gesture (it may not have happened yet, if he was tabbed away).

        Round 33: the bytes this job downloads are the freshest sheet the
        app has seen, so it also REFRESHES the library from them (kept only
        if newer, like every refresh) and re-derives the sheet-fitted rank
        standards -- his ask: "any time we pull in the spreadsheet, we
        should probably do a quick rank standards update... whenever we
        import a runner's times, or copy sheet column." `resync` runs on the
        event loop, since the standards store is read there."""
        try:
            rows, payload = _fetch_column_source(overrides, step=step)
        except Exception as err:
            raise RuntimeError(f"could not read the sheet: {err}") from err
        if library is not None and resync is not None:
            try:
                if library.absorb(payload).get("applied"):
                    resync()
            except Exception as err:                    # noqa: BLE001
                _log.info("library not refreshed from the column export: %r", err)
        step(0.85, "Matching your times to the sheet's rows…")
        cells, payload = _resolve_column(rows, payload)
        body = _column_body(cells, payload)
        return body, (f"{body['total_rows']} rows ready (sheet rows 2–"
                      f"{body['total_rows'] + 1}) · {body['mapped']} carry a time")

    @router.post("/column")
    async def start_column():
        """Start a column export and report it as it goes -- round 26, his
        ask: "a status line that updates at every step of the process...
        Right now it feels like lag, but I know that's just how long it takes
        to confirm things."

        A second door beside the synchronous GET rather than a replacement:
        that URL is documented, browser-reachable and separately tested, and
        the honest answer to a wait is to narrate it, not to move it. The job
        shape (`{state, progress, message}` + a background thread + a status
        GET) is `server/jobs.py`'s, shared with the sheet import since round
        29, because a second progress vocabulary is a second thing to learn."""
        _require_db()
        if library is None:
            raise HTTPException(503, "sheet library unavailable")
        loop = asyncio.get_running_loop()

        def resync():
            # `Adoptions.load()` re-derives the sheet-fitted ladders from
            # the payload the absorb just installed, then the re-grade is
            # absorbed so no celebration fires for a rank he did not run
            # for (`import_api.py::finish` does the same after a landing).
            async def on_loop():
                absorb_after_regrade(service)
                await service._rank_standards_changed()
            asyncio.run_coroutine_threadsafe(on_loop(), loop).result(timeout=30)

        return {"job_id": _column_jobs.start(
            "scorecard-column", lambda step: _column_work(step, resync=resync))}

    @router.get("/column/{job_id}")
    async def column_status(job_id: str):
        job = _column_jobs.status(job_id)
        if job is None:
            raise HTTPException(404, "no such column export job")
        return job

    @router.get("/export.csv")
    async def export_csv(scope: str = "overall"):
        """The card itself, flattened to the template sheet's own
        Course,Star,Record,Goal,You,Delta layout -- one browser-reachable
        URL (`docs/api.md`), never a download the desktop shell's WebView2
        has to support; the card's own Copy buttons fetch this and copy the
        text instead of navigating here."""
        card, _goal_value = await run_in_threadpool(current_card, scope)
        body = await run_in_threadpool(_csv_body, card, library, adoptions, service)
        return Response(content=body, media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition":
                                 'attachment; filename="scorecard.csv"'})

    return router
