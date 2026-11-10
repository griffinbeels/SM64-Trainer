"""Local friends-room controls. Reads never start identity or capture."""
import asyncio

from fastapi import APIRouter, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field

from sm64_events.multiplayer.room import RoomError

_HEADERS = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_ERRORS = {
    "invalid_identity": 400,
    "already_in_room": 409, "invitation_expired": 409, "cannot_invite": 409,
    "cannot_invite_self": 409, "cannot_remove_player": 409, "cannot_retry_player": 409, "invalid_room_action": 409,
    "room_closed": 409, "room_busy": 409,
    "transport_unavailable": 503, "transport_timeout": 503, "room_timeout": 503,
    "room_operation_failed": 503, "room_cleanup_pending": 503,
}


class _PrivateRoute(APIRoute):
    def get_route_handler(self):
        handle = super().get_route_handler()

        async def private(request):
            try:
                response = await handle(request)
            except RequestValidationError:
                # Do not echo a pasted identity or arbitrary input in errors.
                raise HTTPException(422, "invalid_room_request", headers=_HEADERS) from None
            response.headers.update(_HEADERS)
            return response

        return private


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class InviteBody(_Body):
    code: str = Field(min_length=1, max_length=64)


class InvitationBody(_Body):
    invite_id: str = Field(min_length=1, max_length=64)


class MemberBody(_Body):
    member_id: str = Field(min_length=1, max_length=64)


def unavailable_state():
    return {"available": False, "unavailable_reason": "Friends isn’t set up in this build.",
            "phase": "idle", "room": None, "invitations": [],
            "source": {"state": "off", "sharing": False, "paused": False, "source_attached": False, "reason": None},
            "error": None, "can_invite": False}


async def _call(service, method, *arguments):
    if service is None:
        raise HTTPException(503, "transport_unavailable", headers=_HEADERS)
    try:
        return await asyncio.to_thread(getattr(service, method), *arguments)
    except RoomError as error:
        reason = str(error)
        if reason not in _ERRORS:
            reason = "room_operation_failed"
        raise HTTPException(_ERRORS[reason], reason, headers=_HEADERS) from None
    except Exception:  # noqa: BLE001 -- provider exceptions may contain private identities
        raise HTTPException(503, "room_operation_failed", headers=_HEADERS) from None


def create_multiplayer_router(service=None):
    # The app's LocalAccessMiddleware guards every mutation with the same
    # loopback origin/request capability as the other local API routers.
    router = APIRouter(prefix="/api/multiplayer", tags=["multiplayer"], route_class=_PrivateRoute)

    @router.get("")
    def status():
        return service.snapshot() if service is not None else unavailable_state()

    @router.post("/identity")
    async def identity(body: _Body | None = None):
        return {"code": await _call(service, "identity")}

    @router.post("/host")
    async def host(body: _Body | None = None):
        return await _call(service, "host")

    @router.post("/invite")
    async def invite(body: InviteBody):
        return await _call(service, "invite", body.code)

    @router.post("/join")
    async def join(body: InvitationBody):
        return await _call(service, "join", body.invite_id)

    @router.post("/decline")
    async def decline(body: InvitationBody):
        return await _call(service, "decline", body.invite_id)

    @router.post("/leave")
    async def leave(body: _Body | None = None):
        return await _call(service, "leave")

    @router.post("/kick")
    async def kick(body: MemberBody):
        return await _call(service, "kick", body.member_id)

    @router.post("/retry")
    async def retry(body: _Body | None = None):
        return await _call(service, "retry")

    @router.post("/retry_peer")
    async def retry_peer(body: MemberBody):
        return await _call(service, "retry_peer", body.member_id)

    return router
