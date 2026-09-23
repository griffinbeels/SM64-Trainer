# src/sm64_events/server/ghost_api.py
"""Ghost REST surface: a thin skin over `ghost/source.py`.

No logic lives here. Exception taxonomy as everywhere else in this layer:
LookupError -> 404, ValueError -> 409, RuntimeError -> 503.

**"There is no ghost here" is a 200, not an error.** The practice card's
switch has to show a disabled control with a readable reason, and a body it
can only get by parsing an error is indistinguishable from the network
failing. A 503 is reserved for the cases where the server genuinely cannot
tell — no recordings this run, or a boot that has not attached its database
yet — because disabling the switch for a reason that is false is worse than
saying so.
"""
from fastapi import APIRouter, HTTPException, Query

from sm64_events.ghost import settings
from sm64_events.ghost.session import template_store
from sm64_events.ghost.source import missing_ghost, resolve_ghost


def journal_door(replay):
    """`(started_utc, ended_utc) -> the journal rows in that span`, or None.

    The narrowest read-only read of the journal there is (`storage/db.py::
    events_between`, one wall-clock slice, oldest first). The ghost needs it
    for ONE number — the frame the star was touched on, which lives on the grab
    event's payload and is never copied onto the attempt row. It is reached
    through the replay service because that is the object already resolving
    this attempt against the same database; a boot that has not attached one
    yet simply has no door, and the span falls back to the landing.
    """
    db = getattr(getattr(replay, "tracker", None), "db", None)
    return getattr(db, "events_between", None)


def _http(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    if isinstance(error, ValueError):
        return HTTPException(409, str(error))
    return HTTPException(503, str(error))


def no_session_state() -> dict:
    """What GET /api/ghost says in a window that has no ghost session: the
    agreed shape, switched off, with the reason as a sentence. Opacity and the
    colour treatment are None on purpose: nobody chose them here, and the card
    shows unknown as unknown."""
    return {"enabled": False, "opacity": None, "tint": None, "view": None,
            "nudge_frames": 0, "limits": settings.limits(), "target": None,
            "selection": {"ghosts": {"pb": True, "templates": []}, "missing": [],
                          "pb": None, "candidates": []},
            "source": {"available": False, "reason": "no_session",
                       "reason_text": "The ghost overlay is not available in "
                                      "this window.",
                       "template_id": None, "attempt_id": None,
                       "exactness": None, "mario_boxes_available": False,
                       "mario_boxes_reason": None,
                       "mario_boxes_reason_text": None},
            "window": {"running": False, "visible": False}}


def _mount_saved_ghosts(router, session, _session) -> None:
    """The drawer's Save as ghost doors (`ghost/saved.py`)."""
    @router.get("/saved-ghosts/{attempt_id}")
    def saved_ghost(attempt_id: int):
        """Whether this run is one of his saved ghosts (the drawer's button).
        Answers without a session too, as `/api/ghost` does: every drawer asks,
        and an error status would land in the page's own problem list."""
        if session is None:
            return {"attempt_id": attempt_id, "saved": False, "available": False}
        return {"attempt_id": attempt_id, "saved": session.is_saved_ghost(attempt_id), "available": True}
    
    @router.post("/saved-ghosts/{attempt_id}")
    async def save_ghost(attempt_id: int):
        """Save this run as a ghost for its star or segment and tick it."""
        try:
            return await _session().save_ghost(attempt_id)
        except LookupError as error:
            raise HTTPException(404, "That attempt no longer exists.") from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
    
    @router.delete("/saved-ghosts/{attempt_id}")
    async def remove_ghost(attempt_id: int):
        return await _session().remove_ghost(attempt_id)


def create_ghost_router(inputs, replay, session=None) -> APIRouter:
    """`inputs` is the captured-input service or a callable returning it —
    resolved per request for the same reason `create_inputs_router` does it,
    a database that arrives after boot. `replay` may be None when recordings
    are off this run; every read then answers 503 rather than guessing.

    `session` is the app's one `GhostSession`. The two `/api/ghost` doors are
    registered whether or not there is one, because a router that was never
    mounted 404s forever and a 404 reads to the practice card exactly like a
    feature that does not exist (the same reason compare and compilation mount
    on a db-less boot)."""
    router = APIRouter(prefix="/api")

    @router.get("/ghost/source")
    def source(kind: str, entity_key: str, strat: str | None = None,
               template_id: int | None = Query(default=None, gt=0)):
        if replay is None:
            raise HTTPException(503, "recordings are unavailable")
        try:
            # Resolved once per request (the database can arrive after boot),
            # then asked for BOTH of its doors: the template store says which
            # run the ghost is, and `attempt` hands over that run's own frames
            # so the span can be exact rather than read off the timer's shape.
            # `template_store` stays the one answer to "what is the store".
            service = inputs() if callable(inputs) else inputs
            chosen = None
            if template_id is None and session is not None:
                chosen, missing = session.selected_ghost(
                    {"kind": kind, "entity_key": entity_key, "strat": strat})
                if missing:
                    return missing_ghost(kind, entity_key).payload()
            templates = (session.library(service) if session is not None
                         else template_store(service))
            return resolve_ghost(kind, entity_key, strat,
                                 templates=templates,
                                 replay=replay,
                                 attempts=getattr(service, "attempt", None),
                                 template_id=template_id, template=chosen,
                                 events=journal_door(replay)).payload()
        except HTTPException:
            raise
        except Exception as error:
            raise _http(error) from error

    def _session():
        if session is None:
            raise HTTPException(503, "the ghost overlay is not available in "
                                     "this window")
        return session

    @router.get("/ghost")
    async def ghost_state():
        """Everything the switch needs in one read, re-resolved on the spot:
        a stale `available` is what would leave the control enabled over a
        template he has just deleted.

        With no session the READ still answers, in the same shape: every page
        that mounts the practice card asks, and an error status lands in the
        page's own problem list (it turned 40 unrelated browser tests red)."""
        if session is None:
            return no_session_state()
        return await session.refresh()

    _mount_saved_ghosts(router, session, _session)

    @router.put("/ghost")
    async def set_ghost(body: dict):
        """422 with ONE sentence he can read, not a field-by-field error
        list — this lands on a practice card, not in a developer console."""
        try:
            return await _session().update(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router


def mount_ghost_routes(app, *, inputs, replay, session=None, clock=None) -> None:
    """THE one call the app factory makes for everything ghost: the source
    door, the session doors, and the clock socket the overlay page follows.

    It is also where the session meets the clock, because the nudge travels on
    that socket rather than in the overlay page's URL — a URL change is a page
    LOAD, and he changes the nudge by holding a key down. This function is the
    one place already handed both, so the join costs the composition root
    nothing and the app factory keeps its single ghost call.
    """
    app.include_router(create_ghost_router(inputs, replay, session=session))
    if clock is not None:
        if session is not None:
            session.attach_clock(clock)
        from sm64_events.server.ghost_clock import create_ghost_clock_router
        app.include_router(create_ghost_clock_router(clock))
