"""Public-client OAuth and cached account state, independent of the game loop."""
import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import secrets
import threading
import time
from urllib.parse import urlencode
from uuid import UUID, uuid4

import httpx
from authlib.integrations.httpx_client import AsyncOAuth2Client

from .config import CommunityError
from .names import checked_name
from .vault import RefreshTokenStore


def _retry_after(value):
    """A bounded delay is safe to retain; never retain a response description."""
    try:
        delay = float(value) if str(value).strip().isdigit() else (
            parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()
        return min(3600, max(1, delay))
    except (TypeError, ValueError, OverflowError):
        return None


async def bounded_request(http, method, url, *, limit=2 << 20, deadline=120, **kwargs):
    """Bound actual streamed bytes; never surface an HTTP exception's token URL."""
    try:
        kwargs.pop("follow_redirects", None)
        # httpx read timeouts reset for every chunk. A drip-fed response must
        # also have an overall deadline so it cannot occupy the IO worker forever.
        async with asyncio.timeout(deadline), http.stream(method, url, follow_redirects=False, **kwargs) as response:
            data = bytearray()
            async for part in response.aiter_bytes():
                if len(data) + len(part) > limit:
                    raise CommunityError("response_too_large")
                data.extend(part)
            # aiter_bytes() already decodes HTTP compression. Reusing the wire
            # encoding header would make Response decode the clear bytes again.
            headers = response.headers.copy()
            for name in ("content-encoding", "content-length", "transfer-encoding"):
                headers.pop(name, None)
            result = httpx.Response(response.status_code, headers=headers,
                                    content=bytes(data), request=response.request)
    except (httpx.HTTPError, OSError, TimeoutError):
        raise CommunityError("network_unavailable") from None
    if not 200 <= result.status_code < 300:
        # Codes are allowlisted. Server descriptions and request representations
        # may contain credentials or user input and are deliberately not copied.
        code = "request_failed"
        try:
            candidate = result.json().get("error", {}).get("code")
            if candidate in {"not_invited", "rename_required", "suspended", "deleted", "unauthorized",
                             "name_unavailable", "policy_required", "consent_required", "rate_limited", "budget_exhausted",
                             "unsupported_identity", "not_found", "grant_expired", "upload_expired", "invite_unavailable", "generation_changed",
                             "revision_conflict", "publisher_conflict", "progress_consent_required", "progress_disabled",
                             "progress_unavailable", "snapshot_too_large", "invalid_progress", "invalid_profile", "storage_limit", "invalid_cursor"}:
                code = candidate
        except (ValueError, AttributeError, TypeError):
            pass
        raise CommunityError(code, status=result.status_code,
                             retry_after=_retry_after(result.headers.get("Retry-After")))
    return result


class _OAuth(AsyncOAuth2Client):
    """Authlib supplies PKCE/token forms and public client authentication.

    Only HTTP dispatch is adapted so tests and production share the injected
    bounded httpx client; no private httpx transport attributes are accessed.
    """
    def __init__(self, config, http):
        super().__init__(client_id=config.oauth_client_id, redirect_uri=config.callback_url,
                         token_endpoint_auth_method="none", code_challenge_method="S256",
                         scope="email profile", trust_env=False)
        self._injected = http

    async def request(self, method, url, **kwargs):
        kwargs.pop("withhold_token", None)
        return await bounded_request(self._injected, method, url, limit=32768, **kwargs)


def _json_limit(method, path):
    if method.upper() == "GET" and path == "/v1/catalog":
        return 4 << 20
    if method.upper() == "GET" and path == "/v2/account/player-export":
        return 2 << 20  # Snapshot plus bounded owner profile/control metadata.
    return 1 << 20


def _profile(value):
    required = {"v", "uid", "name", "status", "sharing", "terms_version", "privacy_version",
                "sheet_runner", "sheet_verified"}
    if not isinstance(value, dict) or set(value) != required or type(value["v"]) is not int or value["v"] != 1:
        raise CommunityError("invalid_response")
    try:
        if str(UUID(value["uid"])) != value["uid"]:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise CommunityError("invalid_response") from None
    if (value["status"] not in {"new", "active", "rename_required", "suspended", "deleted", "not_invited"}
            or type(value["sharing"]) is not bool or type(value["sheet_verified"]) is not bool
            or any(value[key] is not None and (not isinstance(value[key], str) or len(value[key]) > 256)
                   for key in ("name", "sheet_runner", "terms_version", "privacy_version"))):
        raise CommunityError("invalid_response")
    return {key: value[key] for key in required - {"v"}}


class CommunityAccount:
    def __init__(self, config, *, http=None, token_store=None):
        self.config = config
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(10), trust_env=False,
                                              limits=httpx.Limits(max_connections=4))
        self._owns_http = http is None
        self._store = token_store or RefreshTokenStore(config)
        self._lock = threading.RLock()
        self._auth_lock = asyncio.Lock()
        self._attempt = None
        self._inflight = None
        self._epoch = 0
        self._access = None
        self._expires = 0
        self._profile = None
        self._sharing_disabled = False
        self._sharing_epoch = 0
        self._provider = None
        self._policies = {}
        self._policies_until = 0
        self._error = None
        self._stored = None  # Whether the Credential Manager holds a refresh token; None until read.
        self.on_contribution_cancel = None

    def _credentials_stored(self):
        """A refresh token in the Credential Manager: this profile has an
        account even while offline, so the sign-in gate must not reappear.

        Read once, then kept current by save, load and clear: every status
        check would otherwise copy the refresh token out of the vault.
        """
        if self._stored is None:
            try:
                self._stored = bool(self._store.load())
            except Exception:  # noqa: BLE001 -- a store failure reads as no stored account
                self._stored = False
        return self._stored

    def _load_refresh(self):
        refresh = self._store.load()
        self._stored = bool(refresh)
        return refresh

    def snapshot(self):
        with self._lock:
            snapshot = {"enabled": self.config.enabled, "authenticated": bool(self._access),
                    "stored": self.config.enabled and self._credentials_stored(),
                    "status": "signed_out", "sharing": False, "terms_version": None,
                    "privacy_version": None, **(self._profile or {}), "provider": self._provider,
                    **self._policies, **({"error": self._error} if self._error else {})}
            if self._sharing_disabled:
                snapshot["sharing"] = False
            if snapshot["status"] == "active" and any(
                    snapshot.get(kind + "_version_current") is not None and
                    snapshot.get(kind + "_version") != snapshot[kind + "_version_current"]
                    for kind in ("terms", "privacy")):
                snapshot["status"] = "consent_required"
            return snapshot

    def policy_snapshot(self):
        with self._lock:
            return dict(self._policies)

    def _enabled(self):
        if not self.config.enabled:
            raise CommunityError("disabled", "Community accounts are unavailable in this build.")

    async def _public_config(self):
        if self._policies_until > time.monotonic():
            return
        value = await self.request_json("GET", "/v1/config", authenticated=False)
        # Public config has service-owned limits/auth/catalog fields. Copy only
        # the policy fields understood by the local UI.
        output = {}
        for kind in ("terms", "privacy"):
            version = value.get(kind + "_version")
            href = value.get(kind + "_url")
            if isinstance(version, str) and 1 <= len(version) <= 128:
                output[kind + "_version_current"] = version
            if isinstance(href, str) and (href.startswith(self.config.public_url + "/") or
                                           href in ("/terms", "/privacy")):
                output[kind + "_url"] = self.config.public_url + href if href.startswith("/") else href
        with self._lock:
            self._policies = output
            self._policies_until = time.monotonic() + 60

    async def begin_sign_in(self, provider):
        self._enabled()
        # His decision (round 41): Twitch or Discord only. Google and email are
        # not offered; the hosted page still accepts them until it is redeployed.
        if provider not in ("discord", "twitch"):
            raise CommunityError("invalid_provider")
        await self._public_config()
        attempt, state, verifier = secrets.token_urlsafe(24), secrets.token_urlsafe(32), secrets.token_urlsafe(64)
        async with _OAuth(self.config, self._http) as oauth:
            url, _ = oauth.create_authorization_url(self.config.supabase_url + "/auth/v1/oauth/authorize",
                                                    state=state, code_verifier=verifier)
        with self._lock:
            self._epoch += 1
            self._attempt = (attempt, state, verifier, time.monotonic() + 300, provider, self._epoch)
        return {"attempt_id": attempt, "url": self.config.portal_url + "?" +
                urlencode({"provider": provider, "native_authorize": url})}

    def cancel_sign_in(self, attempt_id):
        with self._lock:
            pending = self._attempt or self._inflight
            if pending and secrets.compare_digest(pending[0], str(attempt_id)):
                self._attempt = None
                self._inflight = None
                self._epoch += 1

    async def complete_sign_in(self, attempt_id, code, state):
        self._enabled()
        with self._lock:
            pending = self._attempt
            if (not pending or not isinstance(state, str) or not isinstance(attempt_id, str)
                    or not secrets.compare_digest(pending[0], attempt_id)
                    or not secrets.compare_digest(pending[1], state) or pending[3] <= time.monotonic()
                    or not isinstance(code, str) or not 1 <= len(code) <= 4096):
                raise CommunityError("invalid_callback", "That sign-in attempt expired or was canceled.")
            self._attempt = None  # Only the matching, current callback consumes its one-use attempt.
            self._inflight = pending
        try:
            async with self._auth_lock:
                async with _OAuth(self.config, self._http) as oauth:
                    try:
                        token = await oauth.fetch_token(self.config.supabase_url + "/auth/v1/oauth/token",
                                                        code=code, code_verifier=pending[2])
                    except Exception:  # noqa: BLE001 -- provider/library failures may embed OAuth credentials
                        raise CommunityError("sign_in_failed") from None
                await self._install(token, epoch=pending[5])
                self._provider = pending[4]
            # A short-lived token may need rotation. Do not reacquire the auth
            # lock from inside the exchange's existing critical section.
            return await self.refresh()
        finally:
            with self._lock:
                if self._inflight == pending:
                    self._inflight = None

    async def _install(self, token, *, epoch):
        if (not isinstance(token, dict) or not isinstance(token.get("access_token"), str)
                or not 1 <= len(token["access_token"]) <= 16384
                or not isinstance(token.get("refresh_token"), str) or not 1 <= len(token["refresh_token"]) <= 2560
                or str(token.get("token_type", "")).lower() != "bearer"
                or type(token.get("expires_in")) not in (int, float) or not 0 < token["expires_in"] <= 86400):
            raise CommunityError("invalid_token")
        with self._lock:
            if epoch != self._epoch:
                raise CommunityError("canceled")
            # A failed rotation never installs a token whose refresh secret was
            # not durably stored. The old memory token is discarded as well.
            self._access = None
            self._store.save(token["refresh_token"])
            self._stored = True
            self._access = token["access_token"]
            self._expires = time.monotonic() + token["expires_in"]
            self._error = None

    async def restore(self):
        if not self.config.enabled:
            return self.snapshot()
        async with self._auth_lock:
            refresh = self._load_refresh()
            if not refresh:
                return self.snapshot()
            await self._rotate(refresh)
        await self._public_config()
        return await self.refresh()

    async def _rotate(self, refresh):
        epoch = self._epoch
        async with _OAuth(self.config, self._http) as oauth:
            try:
                token = await oauth.refresh_token(self.config.supabase_url + "/auth/v1/oauth/token",
                                                  refresh_token=refresh)
            except Exception:  # noqa: BLE001 -- provider/library failures may embed refresh credentials
                self._access = None
                raise CommunityError("refresh_failed") from None
        await self._install(token, epoch=epoch)

    async def _bearer(self):
        self._enabled()
        if self._access and self._expires > time.monotonic() + 30:
            return self._access
        async with self._auth_lock:
            if self._access and self._expires > time.monotonic() + 30:
                return self._access
            refresh = self._load_refresh()
            if not refresh:
                raise CommunityError("signed_out", "Sign in to use community ghosts.")
            await self._rotate(refresh)
        return self._access

    async def request_json(self, method, path, *, authenticated=True, http=None, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            if not any(str(key).lower() == "idempotency-key" for key in headers):
                headers["Idempotency-Key"] = str(uuid4())
        response = await self.request_bytes(method, path, authenticated=authenticated, http=http,
                                            limit=_json_limit(method, path),
                                            headers=headers, **kwargs)
        try:
            value = json.loads(response)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise CommunityError("invalid_response") from None
        version = 2 if path.startswith("/v2/") else 1
        if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != version:
            raise CommunityError("invalid_response")
        return value

    async def request_bytes(self, method, path, *, authenticated=True, http=None, limit=2 << 20, **kwargs):
        self._enabled()
        if not isinstance(path, str) or not path.startswith(("/v1/", "/v2/")) or "?" in path or "#" in path or ".." in path:
            raise CommunityError("invalid_route")
        headers = {"Accept": "application/json", **dict(kwargs.pop("headers", {}) or {})}
        if authenticated:
            headers["Authorization"] = "Bearer " + await self._bearer()
        if "content" in kwargs:
            headers["Content-Type"] = "application/octet-stream"
        epoch = self._epoch
        try:
            result = await bounded_request(http or self._http, method, self.config.public_url + path,
                                           limit=limit, headers=headers, **kwargs)
        except CommunityError as error:
            with self._lock:
                if authenticated and epoch == self._epoch:
                    self._error = error.code
                    status = "consent_required" if error.code in ("policy_required", "consent_required") else error.code
                    if status == 'consent_required':
                        self._policies_until = 0  # Fetch the new public policy once at this gate transition.
                    if status in ("rename_required", "consent_required", "suspended", "deleted", "not_invited") and self._profile:
                        self._profile = {**self._profile, "status": status}
            raise
        return result.content

    async def refresh(self):
        epoch = self._epoch
        await self._public_config()
        value = _profile(await self.request_json("GET", "/v1/account"))
        if value["status"] == "not_invited" and self.config.friend_invitation:
            # The one-use build capability is submitted only after real OAuth
            # authentication. It never enters snapshots, URLs or log messages.
            await self.request_json("POST", "/v1/account/invitation",
                                    json={"token": self.config.friend_invitation})
            value = _profile(await self.request_json("GET", "/v1/account"))
        with self._lock:
            if epoch != self._epoch:
                raise CommunityError("canceled")
            self._profile = value
        return self.snapshot()

    async def complete_profile(self, name, *, terms_version, privacy_version, sharing=False, sheet_runner=None):
        name = checked_name(name)
        if type(sharing) is not bool:
            raise CommunityError("invalid_profile")
        epoch = self._epoch
        value = _profile(await self.request_json("PUT", "/v1/account", json={"name": name, "sharing": sharing,
            "terms_version": terms_version, "privacy_version": privacy_version, "sheet_runner": sheet_runner}))
        with self._lock:
            if epoch != self._epoch:
                raise CommunityError("canceled")
            self._profile = value
        return self.snapshot()

    async def rename(self, name):
        name = checked_name(name)
        epoch = self._epoch
        value = _profile(await self.request_json("PUT", "/v1/account/name", json={"name": name}))
        with self._lock:
            if epoch != self._epoch:
                raise CommunityError("canceled")
            self._profile = value
        return self.snapshot()

    async def check_name(self, name):
        """Whether `name` is free for this account (round 41: the live check
        while typing). `available` is None when the service does not offer the
        check yet; the claim at submit stays the authority either way."""
        from .names import checked_name
        name = checked_name(name)
        try:
            value = await self.request_json("GET", "/v1/names/check", params={"name": name})
        except CommunityError as error:
            if error.status == 404:
                return {"name": name, "available": None}
            raise
        return {"name": name, "available": value.get("available") is True}

    async def set_sharing(self, enabled):
        if type(enabled) is not bool:
            raise CommunityError("invalid_profile")
        with self._lock:
            epoch = self._epoch
            self._sharing_epoch += 1
            sharing_epoch = self._sharing_epoch
        if not enabled:
            with self._lock:
                self._sharing_disabled = True
                owner = (self._profile or {}).get("uid")
                if self._profile is not None:
                    self._profile = {**self._profile, "sharing": False}
            if self.on_contribution_cancel is not None:
                await self.on_contribution_cancel(owner)
        if epoch != self._epoch or sharing_epoch != self._sharing_epoch:
            raise CommunityError("canceled")
        value = _profile(await self.request_json("PUT", "/v1/account/sharing", json={"enabled": enabled}))
        with self._lock:
            if epoch != self._epoch or sharing_epoch != self._sharing_epoch:
                raise CommunityError("canceled")
            self._profile = value
            self._sharing_disabled = not enabled
        return self.snapshot()

    async def sign_out(self):
        owner = self.snapshot().get("uid")
        with self._lock:
            self._epoch += 1
            self._attempt = self._inflight = self._access = self._profile = self._provider = None
            self._sharing_disabled = False
            self._expires = 0
            self._store.clear()
            self._stored = False
        if self.on_contribution_cancel is not None:
            await self.on_contribution_cancel(owner)
        return self.snapshot()

    async def close(self):
        with self._lock:
            self._epoch += 1
            self._attempt = self._inflight = self._access = None
        if self._owns_http:
            await self._http.aclose()
