# src/sm64_events/server/ghost_api.py
"""Ghost preferences, saved runs and collections over the native session."""
import json
from typing import Literal
from fastapi import APIRouter, HTTPException, Query

from sm64_events.ghost import settings


def no_session_state() -> dict:
    """What GET /api/ghost says in a window that has no ghost session: the
    agreed shape, switched off, with the reason as a sentence. Opacity and the
    colour treatment are None on purpose: nobody chose them here, and the card
    shows unknown as unknown."""
    return {"enabled": False, "opacity": None, "tint": None,
            "nudge_frames": 0, "limits": settings.limits(), "target": None,
            "selection": {"ghosts": {"pb": True, "attempts": []}, "missing": [],
                          "playing": [], "pb": None, "candidates": []},
            "source": {"available": False, "reason": "no_session",
                       "reason_text": "Ghost playback is not available in "
                                      "this window.",
                       "template_id": None, "attempt_id": None,
                       "exactness": None, "ghosts": []},
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


def create_ghost_router(session=None) -> APIRouter:
    """Mount ghost routes even before a session is available.

    Reads return an explicit unavailable state; writes need a live session.
    The session owns library resolution and database attachment.
    """
    router = APIRouter(prefix="/api")

    def _session():
        if session is None:
            raise HTTPException(503, "ghost playback is not available in "
                                     "this window")
        return session

    @router.get("/ghost")
    async def ghost_state():
        """Everything the switch needs in one read. Not a forced re-resolve
        (86 ms, and every page load asks): the session re-resolves whenever
        anything its answer reads has moved -- a deleted template bumps the
        store's generation -- so the answer here is already current.

        With no session the READ still answers, in the same shape: every page
        that mounts the practice card asks, and an error status lands in the
        page's own problem list (it turned 40 unrelated browser tests red)."""
        if session is None:
            return no_session_state()
        return await session.refresh(resolve=False)

    _mount_saved_ghosts(router, session, _session)
    @router.get("/ghost/rivals/search")
    async def search_rivals(target: str = Query(max_length=512),
                            player: str = Query(default="",max_length=80),
                            cursor: str | None = Query(default=None,max_length=512),
                            scope: Literal["strategy","overall"] | None = None):
        """Bounded target-scoped player lookup; provider owns indexed pagination."""
        try:
            parsed = json.loads(target)
            if not isinstance(parsed,dict):
                raise ValueError("Choose a star or segment before searching rivals.")
            after = json.loads(cursor) if cursor is not None else None
            if scope is None:
                return await _session().search_rivals(parsed,player,after)
            return await _session().search_rivals(parsed,player,after,scope=scope)
        except (ValueError,LookupError) as error:
            raise HTTPException(422,str(error)) from error
    from sm64_events.server.ghost_sharing_api import mount_collection_routes
    mount_collection_routes(router, _session)
    from sm64_events.server.community_library_api import mount_community_library_routes
    mount_community_library_routes(router, session, _session)

    @router.put("/ghost")
    async def set_ghost(body: dict):
        """422 with ONE sentence he can read, not a field-by-field error
        list — this lands on a practice card, not in a developer console."""
        try:
            return await _session().update(body)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    return router


def mount_ghost_routes(app, *, session=None) -> None:
    """The composition surface mounts the session and collection routes."""
    app.include_router(create_ghost_router(session=session))
