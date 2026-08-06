# src/sm64_events/server/api.py
"""REST command/query surface for the tracker UI (spec §7).

Error taxonomy (service exception types are part of the contract):
LookupError -> 404 (no such attempt), ValueError -> 409 (exists but not
saveable: bad mode, non-success, cleared, missing clock, or — for pb/undo —
not the current PB), RuntimeError -> 503 (database unavailable / degraded
mode)."""
import dataclasses
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sm64_events.core.paths import user_icons_dir
from sm64_events.links import star_links
from sm64_events.memory.addresses import node_label
from sm64_events.ranks.standards import entity_key
from sm64_events.stats.registry import (registry_meta, selection_id,
                                        selection_order)
from sm64_events.tracking import topology
from sm64_events.tracking.backtest import backtest
from sm64_events.tracking.eventlabel import label_event
from sm64_events.tracking.lint import lint_definition
from sm64_events.tracking.segments import (SegmentDef, clause_sentence,
                                           origin_taxonomy,
                                           validate_definition, vocab)
from sm64_events.tracking.synthesize import (clause_for, suggest_name,
                                             synthesize, walked_steps)
from sm64_events.tracking.views import (build_entity_ranks,
                                        build_entity_strategies,
                                        build_route_view, build_run_history,
                                        build_run_view, build_session_view,
                                        stamp_origins)


class TargetBody(BaseModel):
    kind: str = "star"
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None
    strat_tag: str | None = None


class ClearBody(BaseModel):
    reason: str | None = None


class PbBody(BaseModel):
    attempt_id: int
    timer_mode: str


class LandmarkNameBody(BaseModel):
    key: str          # a landmark key, or `kind:<behaviour>` to name a family
    name: str = ""    # blank erases the name rather than storing one


class WipeBody(BaseModel):
    kind: str                      # "star" | "segment" | "all"
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None
    scope: str = "session"         # "session" (active) | "lifetime"


class SessionBody(BaseModel):
    label: str | None = None


class ContinueBody(BaseModel):
    session_id: int


class StratBody(BaseModel):
    # kind-dispatched like TargetBody: star (default) needs course_id+star_id,
    # segment needs segment_id
    kind: str = "star"
    course_id: int | None = Field(default=None, ge=0)
    star_id: int | None = Field(default=None, ge=0)
    segment_id: int | None = None
    strat_tag: str | None = None


class AttemptStratBody(BaseModel):
    # null is meaningful, not missing: it unlabels the attempt
    strat_tag: str | None = None


class IconBody(BaseModel):
    # kind-dispatched like StratBody; icon = a star_icons stem ("wf5"),
    # null resets the entity to its default art
    kind: str = "star"
    course_id: int | None = Field(default=None, ge=0)
    star_id: int | None = Field(default=None, ge=0)
    segment_id: int | None = None
    icon: str | None = None


class OriginBody(BaseModel):
    # null clears the override and returns the segment to its derived origin
    origin: str | None = None


# The bundled selector-icon set (ui/assets/star_icons), resolved relative to
# the package so it works from source AND frozen (build_exe's --add-data
# keeps the ui/ tree at the same relative spot). Globbed per call: the set
# only changes with the install, but a fresh listing keeps a dev's newly
# dropped icon visible without a restart (index.html is served the same way).
_ICON_DIR = Path(__file__).resolve().parents[1] / "ui" / "assets" / "star_icons"


def _icon_stems() -> list[str]:
    try:
        return sorted(p.stem for p in _ICON_DIR.glob("*.png"))
    except OSError:
        return []


# Course portrait art (ui/assets/course_icons), resolved like _ICON_DIR above
# and globbed per call for the same reason: a dropped file shows up without a
# restart.
_COURSE_ICON_DIR = Path(__file__).resolve().parents[1] / "ui" / "assets" / "course_icons"

# Windows drops a Thumbs.db into any folder whose thumbnails get previewed, and
# the staging copy of this art already has one. Listing it would invent a
# "Thumbs" course whose portrait 404s, so the map takes image files only.
_IMAGE_SUFFIXES = frozenset({".png", ".webp", ".jpg", ".jpeg", ".gif", ".avif"})


def _course_icon_map(directory: Path) -> dict[str, str]:
    """stem -> filename for every image in a course-portrait directory."""
    if not directory.is_dir():
        return {}
    return {path.stem: path.name for path in sorted(directory.iterdir())
            if path.is_file() and path.suffix.lower() in _IMAGE_SUFFIXES}


# User-uploaded icons (spec addendum 2026-07-24): any image file, stored in
# the DATA dir (core/paths.user_icons_dir — survives app updates), referenced
# in overrides as "user:<filename>" so they can never collide with bundled
# stems. No server-side resize (stdlib-only) — the picker states the
# preferred shape (square ~100x100) and the UI's object-fit covers the rest.
_USER_ICON_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
_USER_ICON_MAX_BYTES = 2_000_000


def _user_icon_names() -> list[str]:
    try:
        return sorted(f"user:{p.name}" for p in user_icons_dir().iterdir()
                      if p.suffix.lower() in _USER_ICON_EXTS)
    except OSError:
        return []


def _safe_user_icon_file(name: str) -> Path:
    """Resolve a user-icon filename, refusing traversal and odd extensions.
    The name is later interpolated into img srcs and joined onto the data
    dir, so this is the single choke point for both."""
    if name != Path(name).name or name.startswith("."):
        raise HTTPException(400, "bad icon filename")
    if Path(name).suffix.lower() not in _USER_ICON_EXTS:
        raise HTTPException(400, "unsupported icon file type "
                                 f"(use one of {sorted(_USER_ICON_EXTS)})")
    return user_icons_dir() / name


def _icon_exists(stem: str) -> bool:
    if stem.startswith("user:"):
        return stem in _user_icon_names()
    return stem in _icon_stems()


def _origin_nodes() -> set[str]:
    """Every node key the origin taxonomy offers — the override allowlist."""
    return {place["key"]
            for group in origin_taxonomy() if group["key"] is not None
            for place in group["children"]}


class StatSelection(BaseModel):
    key: str
    params: dict = {}


class StatMenuBody(BaseModel):
    selections: list[StatSelection]


class Marker(BaseModel):
    frames: int = Field(ge=0)
    label: str

    @field_validator("label")
    @classmethod
    def _trim_label(cls, v: str) -> str:
        v = v.strip()
        if not 1 <= len(v) <= 60:
            raise ValueError("label must be 1-60 chars after trimming")
        return v


class MarkersBody(BaseModel):
    segment_id: int | None = None
    course_id: int | None = Field(default=None, ge=0)
    star_id: int | None = Field(default=None, ge=0)
    strat_tag: str | None = None
    markers: list[Marker] = Field(max_length=30)


class SegmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    start_triggers: list[dict]
    end_triggers: list[dict]
    guards: list[dict] = []
    enabled: bool = True
    waypoints: list = []
    category: str | None = None
    match_mode: str = "loose"
    # The entity this is a SUBSECTION of; None (the default) = a top-level
    # segment, which is what every definition was before task 0087.
    parent: str | None = None


class SegmentPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    start_triggers: list[dict] | None = None
    end_triggers: list[dict] | None = None
    guards: list[dict] | None = None
    enabled: bool | None = None
    # None = untouched (excluded from the patch below); [] is a valid EXPLICIT
    # clear and must round-trip distinctly from "field omitted" — mirrors
    # guards/start_triggers above. A `list = []` default here would make
    # model_dump() always include waypoints, wiping it on every unrelated
    # PATCH (e.g. just flipping `enabled`).
    waypoints: list | None = None
    category: str | None = None
    # None = untouched, exactly like waypoints above.
    match_mode: str | None = None
    # None = untouched. There is deliberately NO way to clear a parent
    # through this patch shape: promoting a subsection back to a top-level
    # segment changes what it IS, and nothing in the builder asks for it.
    parent: str | None = None


class SegmentSplitBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The shared boundary the two halves meet at -- an any-of clause-set,
    # same shape as start_triggers/end_triggers (tracking/segments.py::
    # split_definition's `mid`). Typically the segment's own single waypoint,
    # promoted to a full stop, but the pure op accepts any clause-set the
    # caller supplies.
    mid: list[dict]
    first_name: str
    second_name: str


class SegmentMergeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_id: int
    second_id: int
    name: str


class BacktestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Reuses SegmentBody wholesale rather than a second definition shape --
    # the whole point is previewing exactly what POST/PUT /api/segments would
    # accept, before committing to it (tracking/backtest.py). Malformed JSON
    # (wrong types, a missing required field) never reaches the handler at
    # all: Pydantic 422s on it here, same as it would on the real save.
    definition: SegmentBody
    # The segment definition this candidate would REPLACE, if any -- None for
    # a brand-new, not-yet-saved definition. When set, the response's
    # pb_before/pb_after/gained/lost compare the candidate against that
    # definition's own real history (tracking/backtest.py's CANDIDATE_ID
    # trap: `current`'s id is real and safe to run under directly).
    replaces: int | None = None


class LintBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Same shape POST /api/segments validates against -- reused wholesale,
    # like BacktestBody, rather than inventing a second definition shape.
    definition: SegmentBody
    # The definition being EDITED, if any -- None for a brand-new,
    # not-yet-saved definition. lint_definition's `duplicate` rule excludes a
    # definition from the comparison BY ID (tracking/lint.py), so without this
    # an in-progress edit that hasn't changed its start/end/waypoints/guards
    # yet would report itself as a duplicate of its OWN on-disk row on every
    # keystroke. Unlike backtest's `replaces`, this is never used to fetch
    # anything (lint_definition's only use of `d.id` is the equality check
    # above) -- so an id naming no real definition doesn't 404, it just fails
    # to exclude anything, which is harmless.
    segment_id: int | None = None


class TimeFilterBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    min_frames: int = Field(ge=0)                 # 0 = no floor
    max_frames: int | None = Field(default=None, ge=1)  # None = no ceiling


class RouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    steps: list[dict]
    start_condition: dict | None = None
    category: str | None = None


class RoutePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    steps: list[dict] | None = None
    start_condition: dict | None = None
    category: str | None = None


class ImportBody(BaseModel):
    payload: dict


class RunStartBody(BaseModel):
    route_id: int

class RunSettingsBody(BaseModel):
    start_offset_ms: int


class RouteSelectBody(BaseModel):
    route_id: int | None = None


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))  # RuntimeError: degraded mode


# GET /api/segments/timeline's default "steps" view membership rule: a type
# clears the bar if it is ever a SEEDED segment definition's ONLY route in or
# out -- the definition has no other trigger clause that could record it, so
# excluding the type would make that definition unrecordable through the
# default view. "Sole" is a PER-DEFINITION property, not a raw use-count: a
# type that backs several definitions but always as one of several
# OR-alternative start/end clauses is not sole for any of them, because the
# alternative already covers it.
#
# Measured directly against all 84 definitions in src/sm64_events/data/
# defaults.seed.json (2026-07-28; re-derived independently twice after an
# earlier pass miscounted by reading only each definition's FIRST start/end
# clause and missing OR-alternatives -- attempt_anchor is never first, so
# that method undercounted it as 0/1 instead of the 7 real uses below):
#
#   trigger type     sole START for     sole END for
#   area_enter       1 (BitS Entry)     4 (BoB/BBH/SL -> Basement,
#                                           Bowser 2 -> Upstairs)
#   attempt_anchor   0                  0  -- all 7 uses (LBLJ, the 3 pipe
#                                           entries, Bowser 1/2/3) are the
#                                           SECOND start clause behind a
#                                           level_enter; every one of those
#                                           definitions is already reachable
#                                           by entering the level normally,
#                                           so attempt_anchor is an F1-retry
#                                           echo, never the only way in
#   spawned          1 (Lakitu Skip)    0
#
# level_changed/star_collected/warp_entered/key_grabbed (the base four) cover
# 63/65 starts and 61/65 ends (~95%) on their own and are never excluded
# regardless of this table -- they are the foundation this rule sits on top
# of, not a case it decides.
#
# area_changed clears the bar (5 sole uses) despite dominating raw volume
# (1,678 of 18,656 real events, 2026-07-28) and is unconditionally included --
# every area_changed row is a real castle-region crossing.
#
# spawned also clears the bar (Lakitu Skip's only start), but the raw type is
# 1,164 events, almost all ordinary respawns after a death or reset that no
# definition needs. Lakitu Skip's clause (`{"type": "spawned", "level": 16}`)
# does not itself distinguish them, but every spawned event also carries a
# `kind` the matcher doesn't check (detectors/spawn.py): "intro" (edge out of
# the file-select cutscene) or "spawn" (an ordinary respawn-in). Measured
# against the real journal (2026-07-28): of 1,164 spawned events, 28 are
# kind="intro" and 1,136 are kind="spawn"; of the 27 kind="intro" spawns at
# level 16 -- exactly what Lakitu Skip's clause matches -- ALL 27 are
# kind="intro", never an ordinary respawn. So the default view includes a
# spawned row only when kind == "intro" (`_is_default_timeline_row` below),
# not the raw type -- narrower than the type-level criterion strictly asks
# for, but it is what the criterion's own need actually is.
#
# attempt_anchor (practice_reset/state_loaded) and game_reset (0 sole uses
# each) stay excluded, reachable only via `view=all`.
#
# Property this rule protects: no seeded definition in defaults.seed.json is
# unrecordable from the default view. tests/test_api.py derives the
# sole-route table above straight from the seed file (never hard-codes it)
# and fails in EITHER direction: a future corpus edit that makes an excluded
# type sole-route without this file being updated, or this file including a
# type the corpus doesn't back.
_TIMELINE_STEP_TYPES = frozenset(
    {"level_changed", "star_collected", "warp_entered", "key_grabbed",
     "area_changed", "moment_reached"})


def _is_default_timeline_row(row) -> bool:
    """Default (`view=steps`) membership predicate -- see the comment above
    _TIMELINE_STEP_TYPES for the sole-route criterion this encodes. Every
    type in _TIMELINE_STEP_TYPES qualifies unconditionally; `spawned` only
    qualifies when payload `kind == "intro"` (a fresh-file spawn) -- the
    narrow subset Lakitu Skip's clause actually needs. An ordinary respawn
    (`kind == "spawn"`) stays out even though the raw type clears the bar."""
    if row.type in _TIMELINE_STEP_TYPES:
        return True
    return row.type == "spawned" and row.payload.get("kind") == "intro"


def create_api_router(service) -> APIRouter:
    router = APIRouter(prefix="/api")

    @router.get("/session")
    def session(clock: str = "igt", scope: str = "session"):
        if clock not in ("igt", "rta"):
            raise HTTPException(422, "clock must be igt or rta")
        if scope not in ("session", "lifetime"):
            raise HTTPException(422, "scope must be session or lifetime")
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_session_view(service.db, service, clock=clock, scope=scope)

    @router.post("/session/new")
    async def session_new(body: SessionBody):
        try:
            sid = await service.new_session(label=body.label)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"session_id": sid}

    @router.post("/session/continue")
    async def session_continue(body: ContinueBody):
        try:
            sid = await service.continue_session(body.session_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"session_id": sid}

    @router.delete("/session/{session_id}")
    async def session_delete(session_id: int):
        try:
            await service.delete_session(session_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.put("/stars/{course_id}/{star_id}/time-filter")
    async def put_time_filter(course_id: int, star_id: int,
                              body: TimeFilterBody):
        """Override one star's validity bounds (frames); history reflags
        via reproject. min 0 disables the implicit 0.5s floor."""
        try:
            await service.set_time_filter(course_id, star_id,
                                          body.min_frames, body.max_frames)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/stars/{course_id}/{star_id}/time-filter")
    async def delete_time_filter(course_id: int, star_id: int):
        """Back to the implicit defaults (0.5s min, no max)."""
        try:
            await service.clear_time_filter(course_id, star_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/segments")
    def segments_list():
        """List all segment definitions, each stamped with its library
        origin (derived from its start rules, or the user's override);
        503 in degraded mode."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return stamp_origins(service.db.segment_defs(),
                             service.db.get_state("origin_overrides", {}))

    @router.get("/segments/vocab")
    def segments_vocab():
        """Return trigger/guard/level vocabulary for the builder GUI.

        Route is declared BEFORE /segments/{segment_id} so FastAPI matches
        the literal 'vocab' path before treating it as an id (declaration
        order wins — fastapi-patterns)."""
        return vocab()

    def _timeline_places(events) -> dict[int, str]:
        """`{event id: world node key}` — where each journal row happened.

        Two rules, both BORROWED rather than invented, and the borrowing is the
        point: `SegmentEngine.feed` and `tracking/synthesize.py::walked_steps`
        already collapse a walk into settled positions, so the recorder's cards
        and the matcher's own judgement cannot disagree about where you were.

        1. **Read `area_changed` and NOTHING else.** An area payload names the
           level AND the settled area outright, where a level payload's context
           is still the OLD level's. Reading `level_changed` too — the obvious
           reading — puts a one-frame "Castle Inside" card between the course
           you left and the basement you are standing in, for a place nobody
           was ever in.
        2. **Take the LAST candidate per FRAME, and apply it to the NEXT
           frame.** Every castle entry loads the Lobby for one poll before
           warping to the real area, all on ONE game frame; judged raw that
           transient Lobby is a card nobody visited.

        The consequence of (2) is that a row is filed under WHERE ITS FRAME
        BEGAN, which is also the reading its own sentence wants: "Exited Hazy
        Maze Cave into Castle Inside" closes HMC's card rather than opening the
        castle's.

        Rows before the first `area_changed` in the journal map to nothing —
        position genuinely unknown, not a place to invent. In practice that is
        the first frames of a fresh database only, since this walks the WHOLE
        journal while the timeline shows its last 200 rows.
        """
        places: dict[int, str] = {}
        settled: str | None = None
        pending: str | None = None
        frame = None
        for row in events:
            if row.frame != frame:
                if pending is not None:
                    settled = pending
                pending = None
                frame = row.frame
            if settled is not None:
                places[row.id] = settled
            if row.type == "area_changed":
                node = topology.node_for(row.payload.get("level"),
                                         row.payload.get("to"))
                if node is not None:
                    pending = node
        return places

    @router.get("/segments/timeline")
    def segments_timeline(limit: int = Query(default=200, ge=1, le=500),
                          view: str = "steps",
                          after_id: int | None = None):
        """The recent journal, as rows a human can point at to define a
        segment from what they just did (`GET /api/segments/timeline`) --
        the endpoint behind Task 10's `tracking/eventlabel.py::label_event`.
        Declared BEFORE /segments/{segment_id}, same declaration-order rule
        as /segments/vocab above (fastapi-patterns).

        Rows are `{id, frame, type, label, wall_time_utc}`, oldest first
        (newest last) -- ordered by the journal's own auto-increment `id`,
        NEVER by `frame`. `frame` is the raw game-frame counter and is NOT
        chronological: it runs backward across every practice reset and
        session boundary (measured against the real journal, 2026-07-28:
        469 backward jumps, landing at 0 on the 7 `game_reset`s and 159
        `session_started`s alone). An `ORDER BY frame` timeline would
        silently interleave rows across every reset with no error -- `id`
        is the one field the journal never reorders, so it is the only
        sort key this endpoint uses; `frame` still rides along as display
        data.

        `view` picks which of eventlabel.LABELLABLE_TYPES's 9 types show:
        "steps" (default, see `_TIMELINE_STEP_TYPES`/`_is_default_timeline_
        row` above for the full sole-route rationale) is level_changed/
        star_collected/warp_entered/key_grabbed (~95% of what the 84 seeded
        definitions' start/end clauses actually use) PLUS area_changed (5
        seeded definitions have no other route in/out) PLUS spawned rows
        where kind == "intro" (Lakitu Skip's only start, narrowed to the
        fresh-file-spawn subset it actually needs -- see the comment above
        _TIMELINE_STEP_TYPES). "all" adds the rest -- practice_reset/
        state_loaded (the attempt_anchor pair), spawned rows with kind ==
        "spawn", and game_reset -- no seeded definition needs any of those as
        its ONLY route in or out. 422 on an unrecognised `view`, matching
        /api/session's own clock/scope validation. `limit` caps at 500 rows
        (422 above it) and is applied AFTER filtering, to the most recent
        rows in the selected view. 503 in degraded mode.

        `after_id` is the LIVE TAIL: it drops every row at or below that id,
        so a surface already holding the list can ask for only what has
        happened since. That is what makes the recorder live without a second
        implementation of `label_event` in the browser — a broadcast event
        carries `seq`, never the journal `id` this endpoint's rows are picked
        by, so the client has to come back for the id regardless, and asking
        for the tail costs one localhost round trip instead of the whole list.

        Each row carries `igt_frames` when its own payload does — the number
        Usamune had on screen at that moment. Surfaced, never derived:
        `star_collected`, `key_grabbed`, `warp_entered` and `moment_reached`
        are all stamped from the shared `detectors/igt_clock.py` when they are
        journaled, and a type that carries none (a level change) reports null
        rather than a computed stand-in.

        FRAMES, not the payload's own pre-formatted `igt` string, even though
        several of these events carry one. Frames is the quantity; the browser
        renders it through `ui/format.js::fmtIgtShort`, which is THE display
        form every other time on screen goes through. Shipping the string
        would put a second formatter's output on the page beside it — and the
        two really do differ, since the display form drops an empty minutes
        field (`06"03`, not `0'06"03`) and the payload's does not.

        Each row also carries WHERE IT HAPPENED — `place` (a world node key),
        `place_label` and `place_level` — so the recorder can cut its list into
        one card per area (his ask, 2026-08-05: *"we should segment each of the
        events by the course / area that the event occurred in… if I move
        between HMC and LLL, both HMC and LLL get their own cards"*). This is
        DERIVED HERE and cannot be derived in the browser: most rows do not say
        where they are (`practice_reset` and `game_reset` name nothing at all),
        so position is a running total over the whole journal, and the browser
        holds only a windowed tail with no beginning to walk from. The walk is
        `_timeline_places` below."""
        if view not in ("steps", "all"):
            raise HTTPException(422, "view must be steps or all")
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        events = list(service.db.events())      # ORDER BY id -- oldest first
        places = _timeline_places(events)
        # The catalogue is read ONCE per fetch and applied at LABEL time, which
        # is what makes a rename apply backwards: every row that landmark ever
        # appeared in re-labels on the next fetch, because no row ever stored
        # the name it was drawn with.
        names = service.db.landmark_names()
        rows = []
        for row in events:
            if after_id is not None and row.id <= after_id:
                continue
            label = label_event(row, names)
            if label is None:
                continue
            landmark = (row.payload.get("landmark") or {}) if row.payload else {}
            if view == "steps" and not _is_default_timeline_row(row):
                continue
            place = places.get(row.id)
            rows.append({"id": row.id, "frame": row.frame, "type": row.type,
                        "label": label, "wall_time_utc": row.wall_time_utc,
                        "igt_frames": row.payload.get("igt_frames"),
                        "place": place,
                        "place_label": node_label(place) if place else None,
                        "place_level": (int(str(place).partition(":")[0])
                                        if place else None),
                        # What the row's rename control edits. `placed` False
                        # means the game made this object mid-play, so it has no
                        # name of its own to give -- the UI offers no pencil.
                        "landmark": landmark.get("key"),
                        "landmark_kind": landmark.get("kind_key"),
                        "landmark_name": names.get(landmark.get("key")),
                        "landmark_placed": landmark.get("placed", False)})
        return {"rows": rows[-limit:]}

    @router.post("/segments")
    async def create_segment(body: SegmentBody):
        try:
            sid = await service.create_segment(body.model_dump())
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True, "id": sid}

    @router.post("/segments/backtest")
    async def backtest_segment(body: BacktestBody):
        """Replay an UNSAVED candidate definition against the real event
        journal and report what it would have done -- the whole point is
        finding out BEFORE saving, rather than live mid-run the way every
        other SM64 autosplitter works (tracking/backtest.py). Declared
        BEFORE /segments/{segment_id} -- same declaration-order rule as
        /segments/vocab above (fastapi-patterns) -- or FastAPI would try to
        parse 'backtest' as a segment id.

        `definition` validates exactly like POST /segments: a domain-invalid
        shape (bad trigger type, an empty trigger list, ...) is 409 via the
        same `validate_definition`/`_http` path every other segment endpoint
        uses. `replaces` names the segment definition this candidate would
        replace, if any -- 404 if it names an unknown id. Read-only: no
        journal entry, no re-projection, no state change of any kind."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        definition = body.definition.model_dump()
        try:
            validate_definition(definition)
        except ValueError as e:
            raise _http(e)
        # id=0 is a placeholder only -- backtest() ALWAYS stamps its own id
        # onto the candidate before replaying it (see backtest.py's "THE
        # TRAP"), so whatever id lands here is discarded either way.
        candidate = SegmentDef(
            id=0, name=definition["name"], enabled=definition["enabled"],
            start_triggers=definition["start_triggers"],
            end_triggers=definition["end_triggers"],
            guards=definition["guards"], waypoints=definition["waypoints"],
            match_mode=definition["match_mode"])
        current = None
        if body.replaces is not None:
            row = next((r for r in service.db.segment_defs()
                       if r["id"] == body.replaces), None)
            if row is None:
                raise HTTPException(404, f"segment {body.replaces} not found")
            # Same inclusion-list construction TrackerService._load_segment_defs
            # uses: SegmentDef's own fields, not an exclusion of whatever
            # extra columns the row happens to carry (seed_key, seed_dirty,
            # default_strat, created_utc, ...).
            keys = [f.name for f in dataclasses.fields(SegmentDef)]
            current = SegmentDef(**{k: row[k] for k in keys})
        report = backtest(service.db.events(), candidate, current)
        return dataclasses.asdict(report)

    @router.post("/segments/lint")
    def lint_segment(body: LintBody):
        """Author-time findings for a NOT-YET-SAVED definition
        (`tracking/lint.py`, Task 15/16) -- advisory, checked before Save,
        never at runtime: a saved definition must keep matching whatever the
        Usamune warp menu invents forever (`tracking/segments.py`'s own
        docstring), so a finding here never gates a MATCH, only the editor's
        Save button. Declared BEFORE /segments/{segment_id} -- same
        declaration-order rule as /segments/vocab above (fastapi-patterns).

        Unlike POST /segments/backtest, this does NOT run `validate_definition`
        first and never 409s on a domain-invalid shape (an unknown trigger
        type, a clause missing a required param): the editor calls this on
        every edit, including the many in-progress states a form passes
        through before it is complete (a just-added clause with no level
        picked yet). Every rule in `lint.py` tolerates that -- an unrecognised
        type or an unset param reads as "unknown", never a crash (see that
        module's docstring). **The rules were not the whole story**: two of
        them call `segments.can_run_from`, and its `fires_from` helper kept a
        bare `trig["to"]` that raised KeyError -> 500 on an ordinary
        in-progress form (start clause with its level picked, end clause still
        the Builder's bare `{"type": "level_enter"}`). Fixed 2026-07-29, with
        the regression test that actually REACHES it -- the two written when
        this endpoint shipped both start with `level_exit`, whose `arm_level`
        is None, so they short-circuit before that rule ever runs. Anything
        this endpoint calls, not just the four rules, must tolerate partial
        input. Domain-shape problems still
        surface at Save time (POST/PUT /api/segments' own `validate_definition`,
        409) -- that check is unchanged and this endpoint doesn't repeat it.

        `all_defs` is `service.segment_defs` -- the REAL current library,
        never `[]` (passing `[]` would silently drop the `duplicate` rule
        with no symptom -- `tracking/lint.py`'s own documented trap).
        `segment_id` names the definition being edited, if any, so the
        `duplicate` rule's self-exclusion (by id) excludes the definition's
        own on-disk row instead of reporting an unmodified edit as a
        duplicate of itself; omit (or null) for a brand-new definition, where
        there is no on-disk row yet to exclude. 503 in degraded mode (no
        definition list to lint against)."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        definition = body.definition.model_dump()
        candidate = SegmentDef(
            id=body.segment_id if body.segment_id is not None else 0,
            name=definition["name"], enabled=definition["enabled"],
            start_triggers=definition["start_triggers"],
            end_triggers=definition["end_triggers"],
            guards=definition["guards"], waypoints=definition["waypoints"],
            match_mode=definition["match_mode"])
        return {"warnings": lint_definition(candidate, service.segment_defs)}

    @router.get("/segments/synthesize")
    def synthesize_from_timeline(ids: str):
        """Turn the picked `GET /api/segments/timeline` row ids into the
        clauses a new segment would be defined by, plus a suggested name and a
        plain-English sentence for each -- the hinge behind "record what I just
        did" (`tracking/synthesize.py`, Task 12) wired up for the timeline
        picker (Task 13). Declared BEFORE /segments/{segment_id} -- same
        declaration-order rule as /segments/vocab above (fastapi-patterns).

        `ids` is a comma-separated list of at least two row ids. **The SERVER
        sorts them, and that is the contract** (2026-08-05, replacing the
        `start_id`/`end_id` pair this took until then): the earliest is the
        start, the latest is the end, and everything between is a waypoint in
        journal order. His words were "select any number of the events, IN
        CHRONOLOGICAL ORDER" — and chronological is a property the events
        already have, so reading it off the click order instead would let a
        list drawn newest-first author a definition whose steps run backwards
        through a walk that only ever happened one way. 422 on fewer than two
        ids or on anything that is not a number.

        A middle id becomes a waypoint through `clause_for(row, "end")`: a
        waypoint is a place you REACH, which is the same role the end fills,
        and the ASYMMETRY in synthesize.py's docstring is exactly about a
        `level_changed` meaning two different clauses at the two ends.

        Looks every id up directly in the journal (`service.db.events()`,
        the SAME source `/segments/timeline` reads) rather than trusting a
        client-supplied payload -- the picker only ever holds row IDS, never
        the raw event. 404 when any id names no journal event.

        Because the ids are DEDUPED before they are counted, picking one
        moment twice is not a pair at all and reports the 422 above rather
        than segments.py's documented COROLLARY (a definition armed and closed
        on the identical tick) -- the same refusal, reached one step earlier
        and worded for what the person actually did.

        409 when a picked row's type carries no synthesis rule for the role it
        was picked for (`attempt_anchor`'s `practice_reset`/`state_loaded`
        source carries no level/course at all -- the matcher resolves that from
        live MatchContext, never the event, so a bare row can't supply it --
        see synthesize.py's module docstring). `synthesize()` itself can't say
        which of the two ends failed (it returns `None` either way), so on
        failure this re-checks with `clause_for` to report the specific one --
        diagnosis, not a second decision.

        Read-only: no journal entry, no state change of any kind.

        `start_sentence`/`end_sentence` render through `clause_sentence` --
        the SAME card_label/card_template machinery
        `card_waiting_for_sentence` uses for an armed segment's "waiting for"
        line, so a synthesized-but-unsaved clause reads in the identical
        voice a saved one would, not a second renderer built for this
        endpoint."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        try:
            picked_ids = sorted({int(part) for part in ids.split(",") if part})
        except ValueError:
            raise HTTPException(422, "ids must be a comma-separated list of "
                                     "timeline row ids")
        if len(picked_ids) < 2:
            raise HTTPException(422, "pick at least two moments — one to start "
                                     "on and one to finish on")
        rows_by_id = {row.id: row for row in service.db.events()}
        picked_rows = [rows_by_id.get(row_id) for row_id in picked_ids]
        if any(row is None for row in picked_rows):
            raise HTTPException(404, "unknown timeline event id")
        start_row, end_row = picked_rows[0], picked_rows[-1]
        middle_rows = picked_rows[1:-1]
        result = synthesize(start_row, end_row)
        if result is None:
            role = "start" if clause_for(start_row, "start") is None else "end"
            raise HTTPException(409,
                f"That moment can't be this segment's {role} — it doesn't "
                "carry enough information to define a trigger from (for "
                "example, a reset with no recorded place).")
        start_clause, end_clause = result
        # `steps` (2026-08-03): every place actually walked between the two
        # picked moments, so the recorder can propose the definition's ORDERED
        # STEPS instead of only its two ends. The path was always in the
        # journal; nothing was reading it, which is why a multi-step movement
        # could not be made in the app at all. Each carries the sentence its
        # clause renders as, through the same `clause_sentence` the two ends
        # use — one voice for the whole definition, not a third renderer.
        steps = [{**step, "sentence": clause_sentence(step["clause"])}
                 for step in walked_steps(rows_by_id.values(),
                                          start_row, end_row)]
        # The middles the PERSON picked, as opposed to `steps`, which is the
        # walk we derived for them. Both ship on every answer and the caller
        # chooses: picking exactly two moments leaves `picked` empty and the
        # derived walk is what fills the middle (the two-click case this tool
        # has always had), and picking more says the walk is not the answer.
        # A middle whose row carries no clause for the role is a 409 like
        # either end -- a definition cannot hold a step it cannot express.
        picked = []
        for row in middle_rows:
            clause = clause_for(row, "end")
            if clause is None:
                raise HTTPException(409,
                    f"That moment can't be a step of this segment — it "
                    "doesn't carry enough information to define a trigger "
                    "from (for example, a reset with no recorded place).")
            picked.append({"id": row.id, "clause": clause,
                           "sentence": clause_sentence(clause)})
        return {"start_clause": start_clause, "end_clause": end_clause,
                "start_sentence": clause_sentence(start_clause),
                "end_sentence": clause_sentence(end_clause),
                "steps": steps, "picked": picked,
                "name": suggest_name(start_clause, end_clause)}

    @router.post("/segments/merge")
    async def merge_segments(body: SegmentMergeBody):
        """Chain two EXISTING definitions into one meeting at their shared
        boundary, kept as a waypoint (`tracking/segments.py::
        merge_definitions`) -- non-destructive: both inputs survive
        untouched, and the merged definition is a brand-new, user-created
        row (`seed_key=None`, so `reconcile_defaults` never touches it).
        Declared as a literal path BEFORE /segments/{segment_id} -- same
        declaration-order rule as /segments/vocab above (fastapi-patterns) --
        so FastAPI never tries to parse 'merge' as a segment id.

        404 for either unknown id. 409 (the pure op's own "do not meet"
        ValueError) when the pair shares no boundary -- same `_http` path
        every other segment endpoint uses; a well-formed request that the
        matcher's own topology rules refuse is a domain refusal, not a
        malformed body.

        Response also carries `warnings` -- `tracking/lint.py` findings for
        the merged result, against the real post-merge library. INFORMATIONAL
        ONLY, never a refusal: `merge_definitions`' own "do not meet" check
        (above) is the only thing this endpoint blocks on. Measured against
        the real 84-def corpus before deciding this (Task 16, spec
        2026-07-28-multi-step-segments): of 6,345 topologically-legal merge
        pairs, 789 come back with an `unrunnable_arm_position` "error" finding
        and 6 with `unfireable` -- overwhelmingly a retry-in-place trick
        (LBLJ, MIPS Clip) merged with an unrelated movement whose concrete arm
        position genuinely can't reach the combined end by this heuristic.
        Refusing on that would block a large fraction of merges
        `merge_definitions` itself already treats as legitimate, so lint
        stays advisory here rather than a second gate."""
        try:
            new_id = await service.merge_segments(
                body.first_id, body.second_id, body.name)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        merged = next(d for d in service.segment_defs if d.id == new_id)
        return {"ok": True, "id": new_id,
                "warnings": lint_definition(merged, service.segment_defs)}

    @router.put("/segments/{segment_id}")
    async def update_segment(segment_id: int, body: SegmentPatch):
        try:
            # Only the fields the client actually SENT (exclude_unset), so
            # False/[]/null all round-trip as explicit sets. Dropping every
            # None instead made `category: null` — "move this out of its
            # group" — a silent no-op, since null is a meaningful value there
            # (2026-07-24); an omitted field is still untouched either way.
            patch = body.model_dump(exclude_unset=True)
            await service.update_segment(segment_id, patch)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/segments/{segment_id}")
    async def delete_segment(segment_id: int):
        try:
            await service.delete_segment(segment_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/segments/{segment_id}/reset")
    async def reset_segment(segment_id: int):
        """Restore a seeded definition to its bundled defaults and clear
        seed_dirty. 404 for a user-created segment or one whose seed_key
        no longer has a matching bundled row. Distinct path segment from
        the literal '/segments/vocab' and from a bare int id, so no
        declaration-order collision (fastapi-patterns)."""
        try:
            await service.reset_segment(segment_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/segments/{segment_id}/split")
    async def split_segment(segment_id: int, body: SegmentSplitBody):
        """Break an EXISTING definition into two new ones meeting at `mid`
        (`tracking/segments.py::split_definition`) -- non-destructive:
        `segment_id` itself is left completely untouched (definitions arm in
        parallel, so the whole and both halves can all record on the same
        play), and both halves are brand-new, user-created rows
        (`seed_key=None`).

        404 for an unknown `segment_id`. 409 (the pure op's own ValueError)
        when a produced half would be unfireable, or `segment_id` carries
        more than one waypoint (folding several into the single shared
        `mid` would silently drop the rest) -- same `_http` path every other
        segment endpoint uses.

        Response also carries `warnings` -- `{first: [...], second: [...]}`,
        `tracking/lint.py` findings for each new half against the real
        post-split library. INFORMATIONAL ONLY: see `POST /segments/merge`'s
        own docstring for why lint stays advisory here rather than a second
        gate (measured against the real corpus before deciding this).
        `split_definition` already refuses `unfireable` itself (reusing
        `lint_definition`'s own rule, `tracking/segments.py`), so that
        finding can never appear in either half's list here -- only
        `start_looser_than_waypoint`/`unrunnable_arm_position`/`duplicate`
        are possible in practice."""
        try:
            first_id, second_id = await service.split_segment(
                segment_id, body.mid, (body.first_name, body.second_name))
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        current = {d.id: d for d in service.segment_defs}
        return {"ok": True, "first_id": first_id, "second_id": second_id,
                "warnings": {
                    "first": lint_definition(current[first_id], service.segment_defs),
                    "second": lint_definition(current[second_id], service.segment_defs),
                }}

    @router.post("/segments/{segment_id}/origin")
    async def set_segment_origin(segment_id: int, body: OriginBody):
        """Pin a segment's library category, or clear it (origin=null) back
        to the value derived from its start rules. The node must exist in the
        vocab taxonomy — 400 otherwise, so a typo can't hide a segment in a
        group nothing renders."""
        if body.origin is not None and body.origin not in _origin_nodes():
            raise HTTPException(400, f"unknown origin: {body.origin}")
        try:
            await service.set_segment_origin(segment_id, body.origin)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

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
            return build_route_view(service.db, service, route_id)
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
            patch = body.model_dump(exclude_unset=True)   # see update_segment
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

    @router.post("/routes/{route_id}/reset")
    async def reset_route(route_id: int):
        """Segment sibling: restore a seeded route to its bundled defaults
        and clear seed_dirty. 404 for a user-created route or one whose
        seed_key no longer has a matching bundled row."""
        try:
            await service.reset_route(route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/route/select")
    async def route_select(body: RouteSelectBody):
        """Set (or clear, route_id=null) the practice-wide active route —
        the arm scope for `in_active_route`-guarded segments (spec
        2026-07-23-default-routes-foundation §5). Distinct from
        POST /run/start, which arms a route for the full-game timer."""
        try:
            await service.select_route(body.route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/run/start")
    async def run_start(body: RunStartBody):
        try:
            await service.start_run(body.route_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/run/end")
    async def run_end():
        try:
            await service.end_run()
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/run/pause")
    async def run_pause():
        try:
            await service.pause_run()
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/run/resume")
    async def run_resume():
        try:
            await service.resume_run()
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/run/reset")
    async def run_reset():
        try:
            await service.reset_run()
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/run/history")
    def run_history(route_id: int | None = None):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_run_history(service.db, route_id=route_id)

    @router.get("/run/settings")
    def run_settings_get():
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return service.run_settings()

    @router.put("/run/settings")
    async def run_settings_put(body: RunSettingsBody):
        try:
            return await service.update_run_settings(body.model_dump())
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/run")
    def run_state():
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_run_view(service.db, service)

    @router.get("/target/ranks")
    def target_ranks():
        """Lazy per-entity 'how good am I at this star' answer (the BEST
        strategy's own rank), for the practice-target picker's grid cells --
        declared before any '/target/{...}' path route, matching the
        '/segments/vocab' declaration-order rule at api.py:341, in case one
        is ever added. 503 in degraded mode, matching GET /session."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return build_entity_ranks(service.db, service)

    @router.get("/target/strategies")
    def target_strategies(entity: str):
        """Step-3 picker payload for ONE entity: every strategy it can be
        practised with, each carrying its own rank + PB -- build_entity_ranks'
        sibling, declared alongside it for the same reason (before any
        '/target/{...}' path route). 404 for an unparseable/unknown entity
        key (LookupError -> _http, matching every other kind-dispatched
        endpoint); 503 in degraded mode, matching GET /target/ranks."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        try:
            return build_entity_strategies(service.db, service, entity)
        except LookupError as e:
            raise _http(e)

    @router.post("/target")
    async def target(body: TargetBody):
        """Set the active practice target — or hold it as an intent.

        kind="segment": requires segment_id; targeting a DISABLED definition
        is allowed — disabling pauses detection without forfeiting the target;
        the section simply accrues no attempts.
        kind="star" (default): requires course_id and star_id.

        A pick the player is not STANDING IN FRONT OF is refused with 409:
        you practice what is in front of you, and picking otherwise was
        "logically inconsistent with how you would actually practice the
        game" (user, 2026-07-27 — see tracking/practicable.py). Re-picking
        what is already the target always succeeds, so a strategy edit is
        never rejected for a position the player has since left.
        """
        # strat_tag present-and-null ("(no strategy)" in the picker) clears
        # the entity's existing strat explicitly; strat_tag absent entirely
        # leaves it alone. One read of model_fields_set, both kinds.
        clear_strat = ("strat_tag" in body.model_fields_set
                       and body.strat_tag is None)
        try:
            result = await service.request_target(
                body.kind, course_id=body.course_id, star_id=body.star_id,
                segment_id=body.segment_id, strat_tag=body.strat_tag,
                clear_strat=clear_strat)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True, **result}

    @router.post("/strat")
    async def strat(body: StratBody):
        """Set an entity's active strategy without moving the target.

        Kind-dispatched exactly like /target — stars and segments are both
        practiced through the same UI card, so both must be settable here.
        """
        try:
            if body.kind == "segment":
                if body.segment_id is None:
                    raise ValueError("segment strat needs segment_id")
                await service.set_strat_segment(body.segment_id, body.strat_tag)
            else:
                if body.course_id is None or body.star_id is None:
                    raise ValueError("star strat needs course_id and star_id")
                await service.set_strat(body.course_id, body.star_id,
                                        body.strat_tag)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/icons")
    async def icons():
        """The icon picker's grid: bundled stems + uploaded user icons."""
        return {"icons": _icon_stems(), "user_icons": _user_icon_names()}

    @router.get("/icons/courses")
    async def course_icons():
        """Course portrait art: stem -> actual filename.

        The set is MIXED-extension (.webp and .png), so the client cannot
        build a URL from a stem alone — it asks for the listing, exactly as
        /api/icons does for star_icons. Consequence, and the reason for the
        endpoint: re-art or a higher-resolution rip appears by dropping the
        file in the folder, with no code change.

        Four main courses are absent on purpose — HMC, SSL, DDD and SL are not
        entered through a painting, so the game has no portrait for them. The
        UI falls back to their star-1 icon (ui/entities.js optionIcon).
        """
        return {"courses": _course_icon_map(_COURSE_ICON_DIR)}

    @router.post("/icons/upload")
    async def icon_upload(name: str, request: Request):
        """Upload a custom icon image (raw request body, like
        /api/compare/upload — no python-multipart). The filename is slugged
        and kept (re-uploading the same name replaces it); returns the
        `user:<file>` stem to pass to POST /api/icon."""
        raw = await request.body()
        if not raw:
            raise HTTPException(400, "empty upload")
        if len(raw) > _USER_ICON_MAX_BYTES:
            raise HTTPException(413, "icon file too large (2 MB max)")
        source = Path(name)
        slug = re.sub(r"[^a-z0-9_-]+", "-", source.stem.lower()).strip("-")
        if not slug:
            raise HTTPException(400, "bad icon filename")
        target = _safe_user_icon_file(f"{slug}{source.suffix.lower()}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        return {"icon": f"user:{target.name}"}

    @router.get("/icons/file/{name}")
    async def icon_file(name: str):
        """Serve one uploaded user icon (the `user:` stems' img src)."""
        path = _safe_user_icon_file(name)
        if not path.is_file():
            raise HTTPException(404, f"no user icon {name}")
        return FileResponse(path)

    @router.post("/icon")
    async def icon(body: IconBody):
        """Set/clear an entity's selector-icon override.

        Kind-dispatched exactly like /strat. `icon` must be a bundled stem
        from /api/icons or an uploaded `user:<file>` (400 otherwise — also
        the path-injection guard, the stem is later interpolated into an
        img src); null resets to default art.
        """
        if body.icon is not None and not _icon_exists(body.icon):
            raise HTTPException(400, f"unknown icon: {body.icon}")
        try:
            if body.kind == "segment":
                if body.segment_id is None:
                    raise ValueError("segment icon needs segment_id")
                ek = entity_key(None, None, body.segment_id)
            else:
                if body.course_id is None or body.star_id is None:
                    raise ValueError("star icon needs course_id and star_id")
                ek = entity_key(body.course_id, body.star_id)
            await service.set_icon(ek, body.icon)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/attempts/{attempt_id}/clear")
    async def clear(attempt_id: int, body: ClearBody):
        try:
            await service.clear_attempt(attempt_id, reason=body.reason)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/attempts/{attempt_id}/restore")
    async def restore(attempt_id: int):
        try:
            await service.restore_attempt(attempt_id)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/attempts/{attempt_id}/strat")
    async def attempt_strat(attempt_id: int, body: AttemptStratBody):
        """Reclassify ONE recorded attempt (null strat_tag = no strategy).

        Distinct from POST /strat, which sets what to practice NEXT — this
        one edits history and triggers a re-projection."""
        try:
            await service.set_attempt_strat(attempt_id, body.strat_tag)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/landmarks")
    def landmarks():
        """The catalogue: every name we know, kinds and instances in one map.

        One map rather than two endpoints because the browser resolves a row's
        label from BOTH -- the instance name if he has given one, else the kind
        name plus where it spawned -- and two fetches would let a row render
        with half the answer.
        """
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        return {"names": service.db.landmark_names()}

    @router.post("/landmark")
    def name_landmark(body: LandmarkNameBody):
        """HIS naming gesture, and it applies BACKWARDS.

        Nothing is written into the journal: a name is not something the game
        did. Every row that landmark ever appeared in re-labels because the
        browser resolves labels from this map at render time rather than
        baking a string into the row when it arrived.
        """
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        if not body.key.strip():
            raise HTTPException(422, "key is required")
        service.db.name_landmark(body.key.strip(), body.name)
        return {"names": service.db.landmark_names()}

    @router.post("/pb")
    async def save_pb(body: PbBody):
        try:
            return await service.save_pb(body.attempt_id, body.timer_mode)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/pb/undo")
    async def undo_pb(body: PbBody):
        try:
            return await service.undo_pb(body.attempt_id, body.timer_mode)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.post("/wipe")
    async def wipe(body: WipeBody):
        try:
            return await service.wipe_data(
                body.kind, course_id=body.course_id, star_id=body.star_id,
                segment_id=body.segment_id, scope=body.scope)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)

    @router.get("/stats/registry")
    def stats_registry():
        return registry_meta()

    @router.put("/statmenu")
    def put_statmenu(body: StatMenuBody):
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        seen: set[str] = set()
        deduped = []
        for s in body.selections:
            sid = selection_id(s.key, s.params)
            if sid not in seen:
                seen.add(sid)
                deduped.append(s.model_dump())
        deduped.sort(key=lambda s: selection_order(s["key"], s.get("params")))
        service.db.set_state("stat_menu", deduped)
        return {"ok": True}

    @router.put("/markers")
    async def put_markers(body: MarkersBody):
        """Replace the marker list for one identity+strategy (spec §3).

        Identity is either segment_id XOR (course_id + star_id) — providing
        both or neither raises 409.  Key format: seg:{id}:{strat} for segment
        markers, {course}:{star}:{strat} for star markers.

        async + no awaits: the read-modify-write on the timeline_markers
        dict is atomic on the event loop (same pattern as set_target's
        strategies RMW in tracking/service.py)."""
        if service.db is None:
            raise HTTPException(503, "database unavailable")
        has_seg = body.segment_id is not None
        has_star = body.course_id is not None and body.star_id is not None
        if has_seg and has_star:
            raise HTTPException(409, "provide segment_id OR course_id+star_id, not both")
        if not has_seg and not has_star:
            raise HTTPException(409, "provide segment_id OR course_id+star_id")
        if has_seg:
            key = f"seg:{body.segment_id}:{body.strat_tag or ''}"
        else:
            key = f"{body.course_id}:{body.star_id}:{body.strat_tag or ''}"
        state = service.db.get_state("timeline_markers", {})
        state[key] = sorted(
            ({"frames": m.frames, "label": m.label} for m in body.markers),
            key=lambda m: m["frames"])
        service.db.set_state("timeline_markers", state)
        return {"ok": True}

    @router.get("/links/{course_id}/{star_id}")
    def links(course_id: int, star_id: int):
        return star_links(course_id, star_id)

    return router
