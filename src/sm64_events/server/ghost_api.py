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

from sm64_events.ghost.source import resolve_ghost


def _http(error: Exception) -> HTTPException:
    if isinstance(error, LookupError):
        return HTTPException(404, str(error))
    if isinstance(error, ValueError):
        return HTTPException(409, str(error))
    return HTTPException(503, str(error))


def create_ghost_router(inputs, replay) -> APIRouter:
    """`inputs` is the captured-input service or a callable returning it —
    resolved per request for the same reason `create_inputs_router` does it,
    a database that arrives after boot. `replay` may be None when recordings
    are off this run; every read then answers 503 rather than guessing."""
    router = APIRouter(prefix="/api")

    def templates():
        if inputs is None:
            raise RuntimeError("captured input is unavailable")
        try:
            service = inputs() if callable(inputs) else inputs
        except Exception as error:
            raise _http(error) from error
        return service.templates

    @router.get("/ghost/source")
    def source(kind: str, entity_key: str, strat: str | None = None):
        if replay is None:
            raise HTTPException(503, "recordings are unavailable")
        try:
            return resolve_ghost(kind, entity_key, strat,
                                 templates=templates(), replay=replay).payload()
        except HTTPException:
            raise
        except Exception as error:
            raise _http(error) from error

    return router
