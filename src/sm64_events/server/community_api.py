"""Token-free local account controls; remote work stays outside game locks."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..community.names import checked_name


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SignIn(_Body):
    provider: Literal["discord", "google", "twitch", "email"]


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


def create_community_router(account):
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
    _mount_account(router, require_account, pending)
    return router


def _mount_auth(router, require_account, pending):

    @router.post("/auth/start")
    async def start(body: SignIn):
        active = require_account()
        if pending["attempt_id"]:
            active.cancel_sign_in(pending["attempt_id"])
        result = await _call(lambda: active.begin_sign_in(body.provider))
        pending["attempt_id"] = result["attempt_id"]
        return result

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

def _mount_account(router, require_account, pending):
    @router.post("/account/refresh")
    async def refresh():
        active = require_account()
        await _call(active.refresh)
        return active.snapshot()

    @router.put("/account")
    async def profile(body: Profile):
        active = require_account()
        await _call(lambda: active.complete_profile(**body.model_dump()))
        return active.snapshot()

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
        active = require_account()
        return await _call(lambda: active.request_json("GET", "/v1/account/export"))

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
