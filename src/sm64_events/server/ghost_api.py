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
from fastapi import APIRouter, HTTPException

from sm64_events.ghost.session import template_store
from sm64_events.ghost.source import resolve_ghost


def _http(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    if isinstance(error, ValueError):
        return HTTPException(409, str(error))
    return HTTPException(503, str(error))


def no_session_state() -> dict:
    """What GET /api/ghost says in a window that has no ghost session: the
    agreed shape, switched off, with the reason as a sentence. Opacity is
    None on purpose: nobody chose one here, and the card shows unknown as
    unknown."""
    return {"enabled": False, "opacity": None, "nudge_frames": 0,
            "target": None,
            "source": {"available": False, "reason": "no_session",
                       "reason_text": "The ghost overlay is not available in "
                                      "this window.",
                       "template_id": None, "attempt_id": None,
                       "exactness": None},
            "window": {"running": False, "visible": False}}


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
    def source(kind: str, entity_key: str, strat: str | None = None):
        if replay is None:
            raise HTTPException(503, "recordings are unavailable")
        try:
            # Resolved once per request (the database can arrive after boot),
            # then asked for BOTH of its doors: the template store says which
            # run the ghost is, and `attempt` hands over that run's own frames
            # so the span can be exact rather than read off the timer's shape.
            # `template_store` stays the one answer to "what is the store".
            service = inputs() if callable(inputs) else inputs
            return resolve_ghost(kind, entity_key, strat,
                                 templates=template_store(service),
                                 replay=replay,
                                 attempts=getattr(service, "attempt", None),
                                 ).payload()
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
