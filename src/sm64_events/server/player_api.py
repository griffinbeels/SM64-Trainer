"""Loopback player pages and explicit saved-PB publication controls."""
import asyncio
import hashlib
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from sm64_events.community.config import CommunityError
from sm64_events.community.library import checked_page
from sm64_events.community.progress_snapshot import checked_snapshot
from sm64_events.community.progress_state import checked_uuid
from sm64_events.community.names import incoming_name
from sm64_events.ghost.portable import checked_hash
from urllib.parse import urlsplit
from sm64_events.ghost.community_library import decorate_page
from sm64_events.ghost.sharing_targets import canonical
from sm64_events.server.community_api import _private_response
from sm64_events.server.player_comparison import compare

MESSAGES = {
    "revision_conflict": "Publication changed. Refresh and review the current publishing profile.",
    "publisher_conflict": "Another Trainer profile publishes this account. Review it before switching.",
    "progress_consent_required": "Review and accept what will be shared first.",
    "progress_disabled": "PB publication is paused. Enable it from its preview to publish changes.",
    "progress_unavailable": "This player has not shared a saved-PB snapshot.",
    "snapshot_too_large": "This profile exceeds the publication limit; no partial snapshot was sent.",
    "invalid_progress": "These saved PBs could not be published. Refresh the preview and try again.",
    "invalid_profile": "Use a bio up to 1,000 bytes and HTTPS links without sign-in information.",
    "storage_limit": "Community publication storage is full. Your local PBs remain saved.",
    "not_found": "This player page is unavailable.",
    "invalid_cursor": "These results changed or the page expired. Refresh the list to continue.",
}


def http_error(error):
    status = 409 if error.code in ("revision_conflict", "publisher_conflict", "invalid_cursor") else error.status
    status = status if status in (400, 401, 403, 404, 409, 413, 422, 429) else 503
    return HTTPException(status, MESSAGES.get(error.code, error.public_message))


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["enable", "disable", "withdraw", "switch"]
    expected_revision: StrictInt = Field(ge=0)
    notice_version: Literal["progress-1"] | None = None


class Details(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: StrictInt = Field(ge=0)
    bio: str = Field(max_length=1000)
    socials: list[dict] = Field(max_length=5)


class PrivacyControl(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["disable", "withdraw"]
    expected_revision: StrictInt = Field(ge=0)


def create_player_router(service, progress=None, ghost=None, account=None):
    router = APIRouter(prefix="/api/community", dependencies=[Depends(_private_response)])
    _mount_privacy(router, account or getattr(progress, "account", None), progress)

    def require():
        if progress is None:
            raise HTTPException(503, "Player pages are unavailable in this Trainer window.")
        return progress

    async def call(operation):
        try:
            return await operation
        except CommunityError as error:
            raise http_error(error) from error

    @router.get("/progress")
    async def publication():
        return await publication_state(progress)

    @router.post("/progress/control")
    async def control(body: Control):
        return await call(require().control(body.action, body.expected_revision, body.notice_version))

    @router.post("/progress/sync")
    async def synchronize():
        active = require()
        await call(active.synchronize())
        return await call(active.status(refresh=False))

    @router.put("/player-profile")
    async def details(body: Details):
        return await call(require().save_profile(body.model_dump()))

    @router.get("/players")
    async def players(prefix: str = Query(default="", max_length=80),
                      cursor: str | None = Query(default=None, max_length=2048)):
        value = await call(require().search(prefix, cursor))
        return search_page(value)

    @router.get("/players/{uid}")
    async def player(uid: str, scope: str = Query(default="overall", max_length=128)):
        active = require()
        header = await call(active.player(uid))
        return await player_page(service, active, uid, header, scope)

    @router.get("/players/{uid}/recordings")
    async def recordings(uid: str, cursor: str | None = Query(default=None, max_length=2048)):
        active = require()
        owner = active.account.snapshot().get("uid")
        value = await call(active.recordings(uid, cursor))
        return await recording_page(value, uid, service, ghost, active, owner)

    return router


def _mount_privacy(router, account, progress):
    from sm64_events.community.progress_privacy import ProgressPrivacy
    privacy = ProgressPrivacy(account)

    @router.get("/progress/privacy")
    async def read_privacy():
        try:
            return await privacy.read()
        except CommunityError as error:
            raise http_error(error) from error

    @router.post("/progress/privacy/control")
    async def change_privacy(body: PrivacyControl):
        if progress is not None:
            progress._fence += 1
        try:
            result = await privacy.control(body.action, body.expected_revision)
        except CommunityError as error:
            raise http_error(error) from error
        if progress is not None:
            try:
                value = result["publication"]
                progress._accept(value, value["uid"], progress._database())
            except CommunityError:
                pass  # Removal does not require a writable local profile.
        return result


async def recording_page(value, uid, service, ghost, active, owner):
    try:
        if not isinstance(value, dict) or value.get("v") != 2 or value.get("uid") != uid:
            raise CommunityError("invalid_response")
        page = checked_page({key: item for key, item in {**value, "v": 1}.items() if key != "uid"},
                            summary=False, generation=value.get("generation"))
        if any(row["player_id"] != uid for row in page["recordings"]):
            raise CommunityError("invalid_response")
        result = await decorate_recordings(page, service, ghost, owner)
        if active.account.snapshot().get("uid") != owner:
            raise CommunityError("canceled", "Your account changed. Open the page again.")
        return result
    except CommunityError as error:
        raise http_error(error) from error


def search_page(value):
    if (not isinstance(value, dict) or set(value) != {"v", "players", "next_cursor", "complete"}
            or value["v"] != 2 or not isinstance(value["players"], list) or len(value["players"]) > 20
            or type(value["complete"]) is not bool
            or value["complete"] != (value["next_cursor"] is None)):
        raise HTTPException(503, "These player results could not be read.")
    cursor = value["next_cursor"]
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
        raise HTTPException(503, "This player-result page could not be read.")
    rows = [public_identity(row) for row in value["players"]]
    if len({row["uid"] for row in rows}) != len(rows):
        raise HTTPException(503, "These player identities could not be read.")
    return {**value, "players": rows}


async def decorate_recordings(page, service, ghost, owner):
    if ghost is None:
        return await asyncio.to_thread(decorate_page, page, service, None, "recordings")
    try:
        async with ghost._lock:
            return await asyncio.to_thread(ghost._library_work, decorate_page, page, service,
                                           ghost._rivals, "recordings", owner=owner)
    except (ValueError, LookupError) as error:
        raise HTTPException(422, str(error)) from error


async def publication_state(progress):
    if progress is None:
        return {"available": False, "reason": "Player-page publication is unavailable in this Trainer window."}
    try:
        return await progress.status()
    except CommunityError as error:
        if error.status in (404, 405):
            raise HTTPException(503, "Player pages need the updated community service. Try again after it updates.") from error
        raise http_error(error) from error


def public_identity(value):
    try:
        uid = checked_uuid(value["uid"])
        name = incoming_name(value["name"])
        bio, socials = value.get("bio", ""), value.get("socials", [])
        if (value["claimed_account"] is not True or not isinstance(bio, str) or len(bio.encode()) > 1000
                or not isinstance(socials, list) or len(socials) > 5
                or any(not valid_social(item) for item in socials)):
            raise CommunityError("invalid_response")
    except (KeyError, TypeError, ValueError):
        raise HTTPException(503, "This claimed player identity could not be read.") from None
    return {"uid": uid, "name": name, "claimed": True,
            "claim_explanation": "This player has claimed their signed-in Trainer account. This does not verify a performance.",
            "bio": bio, "socials": socials,
            "sheet_runner": value.get("sheet_runner") if value.get("sheet_verified") is True else None,
            "sheet_verified": value.get("sheet_verified") is True}


def valid_social(value):
    if (not isinstance(value, dict) or set(value) != {"kind", "url"}
            or value["kind"] not in ("twitch", "youtube", "website")
            or not isinstance(value["url"], str) or len(value["url"].encode()) > 1024):
        return False
    try:
        url = urlsplit(value["url"])
        return url.scheme == "https" and bool(url.hostname) and not url.username and not url.password
    except ValueError:
        return False


def public_progress(header):
    value = header.get("progress")
    if (not isinstance(value, dict) or set(value) != {"available", "revision", "content_hash", "captured_utc", "synced_utc"}
            or type(value["available"]) is not bool or type(value["revision"]) is not int or value["revision"] < 0):
        raise HTTPException(503, "This player's publication state could not be read.")
    try:
        if value["available"]:
            checked_hash(value["content_hash"])
        if any(value[key] is not None and (not isinstance(value[key], str) or len(value[key]) > 40)
               for key in ("captured_utc", "synced_utc")):
            raise ValueError()
    except (ValueError, TypeError):
        raise HTTPException(503, "This player's publication state could not be read.") from None
    return value


async def player_page(service, progress, uid, header, scope):
    if not isinstance(header, dict) or header.get("v") != 2:
        raise HTTPException(503, "This player page could not be read.")
    player = public_identity(header)
    if player["uid"] != uid:
        raise HTTPException(503, "This player identity changed. Open their page again.")
    published = public_progress(header)
    details = {"published": False, **published}
    comparison, reason = None, None
    if published.get("available"):
        try:
            value = await progress.published(uid)
            if not isinstance(value, dict) or value.get("v") != 2:
                raise CommunityError("invalid_response")
            content = checked_snapshot(value["content"])
            checked_hash(value["content_hash"])
            if value.get("uid") != uid or hashlib.sha256(canonical(content)).hexdigest() != value.get("content_hash"):
                raise CommunityError("invalid_response")
            details = {**details, **{key: value[key] for key in ("revision", "content_hash", "captured_utc", "synced_utc")},
                       "published": True, "coverage": content["coverage"], "default_rom": content["default_rom"]}
            if service is None or service.db is None or service.ranks is None:
                reason = "Open this page in a Trainer window with local rank standards to compare saved PBs."
            else:
                comparison = await asyncio.to_thread(compare, service, content, scope)
                reason = comparison["rank_reason"]
        except CommunityError as error:
            if error.code != "progress_unavailable":
                raise http_error(error) from error
            reason = "Published PBs were removed while this page loaded. Refresh to read the current page."
        except (KeyError, TypeError, ValueError):
            raise HTTPException(503, "This player's published PBs could not be read.") from None
    else:
        reason = "Saved PBs are private. Available recordings remain separate from the player's current saved PBs."
    return {"player": player, "progress": details, "comparison": comparison, "partial_reason": reason}
