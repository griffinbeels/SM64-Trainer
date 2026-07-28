# src/sm64_events/server/api.py
"""REST command/query surface for the tracker UI (spec §7).

Error taxonomy (service exception types are part of the contract):
LookupError -> 404 (no such attempt), ValueError -> 409 (exists but not
saveable: bad mode, non-success, cleared, missing clock, or — for pb/undo —
not the current PB), RuntimeError -> 503 (database unavailable / degraded
mode)."""
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from sm64_events.core.paths import user_icons_dir
from sm64_events.links import star_links
from sm64_events.ranks.standards import entity_key
from sm64_events.stats.registry import (registry_meta, selection_id,
                                        selection_order)
from sm64_events.tracking.segments import origin_taxonomy, vocab
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

    @router.post("/segments")
    async def create_segment(body: SegmentBody):
        try:
            sid = await service.create_segment(body.model_dump())
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True, "id": sid}

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
