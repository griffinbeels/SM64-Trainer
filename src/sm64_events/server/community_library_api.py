"""Community Library adapters. Reads never select a live Practice target."""
import json
from typing import Literal

from fastapi import HTTPException, Query

from sm64_events.community.config import CommunityError


def unavailable(session):
    community = getattr(session, "_community", None)
    if community is None:
        return "Community ghosts are unavailable in this Trainer window."
    state = community.account.snapshot()
    if state.get("enabled") is False:
        return "Community ghosts are unavailable in this build."
    if state.get("status") != "active":
        return "Sign in and complete your community profile to browse shared ghosts."
    return None


def cloud_error(error):
    if error.code == "invalid_cursor":
        return HTTPException(409, "These ghosts changed or this page expired. Reload results to continue.")
    if error.status in (404, 405):
        return HTTPException(503, "Community ghost browsing needs a newer community service. Try again after it updates.")
    status = error.status if error.status in (401, 403, 409, 429) else 503
    return HTTPException(status, error.public_message)


def mount_community_library_routes(router, session, require_session):
    @router.get("/library/community")
    async def summary(cursor: str | None = Query(default=None, max_length=2048),
                      entity: str | None = Query(default=None, max_length=128),
                      rom: Literal["us", "jp"] | None = None):
        reason = unavailable(session)
        if reason:
            return {"available": False, "reason": reason, "entries": [],
                    "next_cursor": None, "complete": False}
        try:
            return await session.library_ghost_summary(cursor=cursor, entity=entity, version=rom)
        except CommunityError as error:
            raise cloud_error(error) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.get("/library/community/recordings")
    async def recordings(*, target: str = Query(max_length=2048),
                         clock: Literal["igt", "rta"], rom: Literal["us", "jp"],
                         strategy: str = Query(min_length=1, max_length=256),
                         cursor: str | None = Query(default=None, max_length=2048)):
        reason = unavailable(session)
        if reason:
            return {"available": False, "reason": reason, "recordings": [],
                    "next_cursor": None, "complete": False}
        try:
            descriptor = json.loads(target)
            return await session.library_ghost_recordings(descriptor, clock=clock, version=rom,
                                                          strategy=strategy, cursor=cursor)
        except CommunityError as error:
            raise cloud_error(error) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/ghost/rivals/saved")
    async def saved_rival(body: dict):
        if set(body) - {"target", "action", "id", "slot", "scope", "version"}:
            raise HTTPException(422, "Choose a recording and a Lineup seat.")
        try:
            target = body["target"]
            changes = {key: value for key, value in body.items() if key not in ("target", "version")}
            return await require_session().save_library_rival(target, changes, version=body.get("version"))
        except CommunityError as error:
            raise cloud_error(error) from error
        except (KeyError, ValueError, LookupError) as error:
            raise HTTPException(422, str(error)) from error
