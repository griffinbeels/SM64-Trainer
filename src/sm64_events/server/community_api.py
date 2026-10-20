"""Token-free local account controls; remote work stays outside game locks."""
import asyncio
import logging
from typing import Literal
import webbrowser

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..community.names import checked_name

log = logging.getLogger("sm64.community")

# The desktop shell asks the server to open the hosted sign-in page in the
# user's default browser (round 39): the shell hands its own new windows to
# the OS as URLs, so the UI's blank pre-opened tab became a Windows prompt for
# an app to open an 'about' link. Module attribute so tests can observe it.
open_in_browser = webbrowser.open


def _open_browser(url: str) -> bool:
    try:
        return bool(open_in_browser(url))
    except Exception:  # noqa: BLE001 -- the fallback link in the modal still works
        log.warning("The default browser could not be opened for sign-in", exc_info=True)
        return False


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SignIn(_Body):
    # His decision (round 41): Twitch or Discord only.
    provider: Literal["discord", "twitch"]
    open_browser: bool = False


class Cancel(_Body):
    attempt_id: str = Field(min_length=1, max_length=128)


class _Named(_Body):
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        return checked_name(value)


class Profile(_Named):
    terms_version: str = Field(min_length=1, max_length=80)
    privacy_version: str = Field(min_length=1, max_length=80)
    sharing: bool = False
    sheet_runner: str | None = Field(default=None, max_length=128)
    share_pbs: bool | None = None


class Rename(_Named):
    pass


class Sharing(_Body):
    enabled: bool


class Confirmation(_Body):
    confirmation: Literal["WITHDRAW", "DELETE"]


async def _call(work):
    try:
        return await work()
    except HTTPException:
        raise
    except (ValueError, LookupError) as error:
        # Input validators use fixed public messages. Do not forward arbitrary
        # OAuth/HTTP exception strings, which may contain credentials or URLs.
        public = getattr(error, "public_message", None)
        raise HTTPException(400, public or "This account action could not be completed.") from None
    except Exception as error:  # noqa: BLE001 -- redact auth transport exceptions
        public = getattr(error, "public_message", None)
        raise HTTPException(503, public or "Community service unavailable. Your local practice is still saved.") from None


def _private_response(response: Response):
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'


def create_community_router(account, progress=None):
    router = APIRouter(prefix="/api/community", tags=["community"], dependencies=[Depends(_private_response)])
    pending = {"attempt_id": None}

    @router.get("/account")
    def status():
        return account.snapshot() if account is not None else {
            "enabled": False, "authenticated": False, "status": "signed_out"}

    def require_account():
        if account is None or not account.snapshot().get("enabled"):
            raise HTTPException(503, "Community testing is not configured for this build.")
        return account

    _mount_auth(router, require_account, pending)
    _mount_account(router, require_account, pending, progress)
    return router


def _mount_auth(router, require_account, pending):

    @router.post("/auth/start")
    async def start(body: SignIn):
        active = require_account()
        if pending["attempt_id"]:
            active.cancel_sign_in(pending["attempt_id"])
        result = await _call(lambda: active.begin_sign_in(body.provider))
        pending["attempt_id"] = result["attempt_id"]
        # Off the event loop: os.startfile can block while the browser launches.
        opened = await asyncio.to_thread(_open_browser, result["url"]) if body.open_browser else False
        return {**result, "opened": opened}

    @router.post("/auth/cancel")
    def cancel(body: Cancel):
        active = require_account()
        active.cancel_sign_in(body.attempt_id)
        if pending["attempt_id"] == body.attempt_id:
            pending["attempt_id"] = None
        return {"cancelled": True}

    @router.get("/auth/callback", include_in_schema=False)
    async def callback(code: str = "", state: str = "", error: str = ""):
        active = require_account()
        attempt_id = pending["attempt_id"]
        if error or not attempt_id or not code or len(code) > 4096 or not state or len(state) > 256:
            raise HTTPException(400, "Sign-in cancelled or expired. Return to the trainer and try again.")
        await _call(lambda: active.complete_sign_in(attempt_id, code, state))
        pending["attempt_id"] = None
        return HTMLResponse("<!doctype html><html lang='en'><meta charset='utf-8'>"
                            "<meta name='viewport' content='width=device-width'>"
                            "<title>SM64 Trainer sign-in</title><body>"
                            "<h1>Sign-in complete</h1><p>Return to SM64 Trainer. You can close this tab.</p></body></html>",
                            headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                     "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'"})

async def _complete_profile(active, body, progress):
    if progress is not None:
        progress.prepare_profile_choice(body.share_pbs)
    elif body.share_pbs is False and active.snapshot().get("status") == "new":
        raise HTTPException(503, "Open a local Trainer profile before choosing saved-PB sharing.")
    await active.complete_profile(**body.model_dump(exclude={"share_pbs"}))
    if progress is not None:
        await progress.profile_completed()
    return active.snapshot()


def _mount_account(router, require_account, pending, progress=None):
    @router.get("/names/check")
    async def check_name(name: str = Query(min_length=1, max_length=128)):
        """The live availability check while a name is typed (round 41).
        `available` is None when the service has no check yet; the claim at
        submit is the authority either way."""
        active = require_account()
        return await _call(lambda: active.check_name(name))

    @router.post("/account/refresh")
    async def refresh():
        active = require_account()
        await _call(active.refresh)
        return active.snapshot()

    @router.put("/account")
    async def profile(body: Profile):
        active = require_account()
        return await _call(lambda: _complete_profile(active, body, progress))

    @router.put("/account/name")
    async def rename(body: Rename):
        active = require_account()
        await _call(lambda: active.rename(body.name))
        return active.snapshot()

    @router.put("/account/sharing")
    async def sharing(body: Sharing):
        active = require_account()
        await _call(lambda: active.set_sharing(body.enabled))
        return active.snapshot()

    @router.post("/auth/sign-out")
    async def sign_out():
        active = require_account()
        if pending["attempt_id"]:
            active.cancel_sign_in(pending["attempt_id"])
            pending["attempt_id"] = None
        await _call(active.sign_out)
        return active.snapshot()

    @router.get("/account/export")
    async def export():
        return await export_account(require_account())

    @router.post("/account/withdraw")
    async def withdraw(body: Confirmation):
        if body.confirmation != "WITHDRAW":
            raise HTTPException(400, "Confirm shared ghost removal.")
        active = require_account()
        return await _call(lambda: active.request_json("POST", "/v1/account/withdraw",
            json={'confirmation': 'WITHDRAW MY GHOSTS'}))

    @router.post("/account/delete")
    async def delete(body: Confirmation):
        if body.confirmation != "DELETE":
            raise HTTPException(400, "Confirm account deletion.")
        active = require_account()
        result = await _call(lambda: active.request_json("POST", "/v1/account/delete",
            json={'confirmation': 'DELETE MY ACCOUNT'}))
        await _call(active.sign_out)
        return result


def _export_owner(account, owner, epoch):
    if not owner or account.snapshot().get("uid") != owner or getattr(account, "_epoch", None) != epoch:
        raise HTTPException(409, "Your account changed. Export again from the account you want to download.")


async def export_account(active):
    from sm64_events.community.config import CommunityError
    owner, epoch = active.snapshot().get("uid"), getattr(active, "_epoch", None)
    result = await _call(lambda: active.request_json("GET", "/v1/account/export"))
    _export_owner(active, owner, epoch)
    if not isinstance(result.get("account"), dict) or result["account"].get("uid") != owner:
        raise HTTPException(503, "The account export identity could not be confirmed.")
    try:
        player = await active.request_json("GET", "/v2/account/player-export")
    except CommunityError as error:
        _export_owner(active, owner, epoch)
        if error.status not in (404, 405):
            raise HTTPException(503, "The saved-PB export could not be reached. Try again before downloading.") from error
        player = {"available": False, "reason": "This service does not support player-page export yet."}
    _export_owner(active, owner, epoch)
    if player.get("available") is not False and player.get("uid") != owner:
        raise HTTPException(503, "The player-page export identity could not be confirmed.")
    return {**result, "player_page": player}
