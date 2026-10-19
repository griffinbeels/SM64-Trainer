"""Consent-scoped current PB publication. Persist acknowledgments, never old bodies."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import hashlib
import logging
import time
from uuid import uuid4
from types import SimpleNamespace

from sm64_events.ghost.sharing_targets import canonical

from .config import CommunityError
from .cache import account_owner
from . import progress_state as state

MUTATIONS = frozenset({"pb_saved", "pb_undone", "time_imported", "attempt_strat_set", "strat_set",
                       "attempt_cleared", "attempt_restored", "attempts_invalidated", "data_wiped",
                       "session_started", "game_version_changed", "segments_changed"})
log = logging.getLogger("sm64.progress")


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ProgressSync:
    def __init__(self, service, account, *, installation=None, project=None, period=2):
        self.service, self.account = service, account
        self._installation, self._project, self._period = installation, project, period
        self._task, self._wake = None, None
        self._closing, self._serial, self._fence = False, 0, 0
        self._sync_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._profile = None
        self._cooldown = 0
        self._jobs = set()
        self._last_context = None

    def _owner(self):
        value = self.account.snapshot()
        if account_owner(value) is None or value.get("enabled") is False:
            raise CommunityError("sign_in_required", "Sign in to browse player pages and manage published PBs.")
        return state.checked_uuid(value.get("uid"))

    def _database(self):
        if self.service is None or self.service.db is None:
            raise CommunityError("local_profile_unavailable", "This window cannot publish a local practice profile.")
        return self.service.db

    def local_publisher(self):
        if self._installation is None:
            from sm64_events.core.paths import progress_installation_path
            self._installation = state.installation_id(progress_installation_path())
        return state.publisher(self._database(), self._installation)

    def _local(self, owner):
        value = self._database().get_state(state.KEY, {})
        if not isinstance(value, dict) or value.get("owner") != owner:
            return {"owner": owner, "dirty": True, "publication": None, "last_error": None}
        return value

    def _store(self, value):
        database = self._database()
        if database.get_state(state.KEY, None) != value:
            database.set_state(state.KEY, value)

    def _context(self):
        return self._owner(), self._database(), self._fence, getattr(self.account, "_epoch", None)

    def _guard(self, context):
        try:
            current = self._context()
        except CommunityError:
            current = None
        if (self._closing or current is None or current[0] != context[0] or current[1] is not context[1]
                or current[2:] != context[2:]):
            raise CommunityError("canceled", "Your account, local profile or publication changed. Review its current settings.")

    def _finish(self, context, serial, *, clean):
        self._guard(context)
        owner = context[0]
        dirty = not clean or serial != self._serial
        self._store({**self._local(owner), "dirty": dirty, "last_error": None})
        if dirty and self._wake is not None:
            self._wake.set()

    def observe(self, event):
        # Some notices precede the PB insert. The completed HTTP mutation and
        # periodic local projection below close that timing gap.
        if event.type not in MUTATIONS or self._closing:
            return
        self.invalidate()

    def invalidate(self):
        """Mark current facts dirty without a snapshot queue or network IO."""
        if self._closing:
            return
        self._serial += 1
        try:
            try:
                value = self._local(self._owner())
            except CommunityError:
                # Local edits while signed out still dirty a previously
                # owned publication. They grant no right to send anything.
                value = self._database().get_state(state.KEY, {})
                if not isinstance(value, dict):
                    return
                state.checked_uuid(value.get("owner"))
            if not value["dirty"]:
                self._store({**value, "dirty": True})
            if self._wake is not None:
                self._wake.set()
        except CommunityError:
            return

    async def start(self):
        if self._task is None or self._task.done():
            self._closing = False
            self._wake = asyncio.Event()
            try:
                local = self._local(self._owner())
                self._store({**local, "dirty": True})
            except CommunityError:
                pass
            self._wake.set()
            self._task = asyncio.create_task(self._run(), name="progress-publication")

    async def stop(self):
        self._closing = True
        self._fence += 1
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._jobs:
            await asyncio.gather(*self._jobs, return_exceptions=True)

    async def _run(self):
        while not self._closing:
            with suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=30)
            self._wake.clear()
            await asyncio.sleep(self._period)
            try:
                context = self._context()
                owner = context[0]
                value = self._local(owner)
                if (self._last_context is None or self._last_context[0] != owner
                        or self._last_context[1] is not context[1] or self._last_context[2] != context[3]):
                    self._last_context = owner, context[1], context[3]
                    if not value["dirty"]:
                        value = {**value, "dirty": True}
                        self._store(value)
                publication = value.get("publication") or {}
                if time.monotonic() >= self._cooldown:
                    if value.get("needs_refresh") or not publication:
                        publication = await self.refresh()
                    if publication.get("enabled"):
                        await self.synchronize()
            except CommunityError as error:
                if error.code in ("sign_in_required", "local_profile_unavailable"):
                    self._last_context = None
                self._remember_error(error)

            except Exception:  # noqa: BLE001 -- publication cannot stop local practice
                self._remember_error(CommunityError("progress_unavailable"))

    def _remember_error(self, error):
        if error.code == "canceled":
            return
        try:
            owner = self._owner()
            value = self._local(owner)
            self._store({**value, "dirty": True, "last_error": error.code})
            self._cooldown = time.monotonic() + min(3600, max(2, error.retry_after or (3600 if error.status in (404, 405) else 30)))
        except CommunityError:
            pass
        except Exception:  # noqa: BLE001 -- failure to persist an error is not a second failure
            log.warning("Saved-PB publication state could not be updated; local practice continues.")

    def _request_guard(self, owner, epoch, context):
        if context is not None:
            self._guard(context)
        try:
            current = self._owner()
        except CommunityError:
            current = None
        if self._closing or current != owner or getattr(self.account, "_epoch", None) != epoch:
            raise CommunityError("canceled", "Your account changed. Open the page again.")

    async def _request(self, method, path, *, owner=None, context=None, **kwargs):
        owner = self._owner() if owner is None else owner
        epoch = getattr(self.account, "_epoch", None)
        self._request_guard(owner, epoch, context)
        if method != "GET":
            kwargs["headers"] = {"Idempotency-Key": str(uuid4()), **kwargs.get("headers", {})}
        try:
            value = await self.account.request_json(method, path, **kwargs)
        except CommunityError:
            self._request_guard(owner, epoch, context)
            raise
        self._request_guard(owner, epoch, context)
        return value

    def _accept(self, value, owner, database, *, context=None):
        if context is not None:
            self._guard(context)
        if database is not self._database():
            raise CommunityError("canceled", "Your local profile changed. Review its publication settings.")
        value = state.checked_control(value, owner)
        local = self._local(owner)
        previous = local.get("publication")
        if previous and previous["revision"] > value["revision"]:
            raise CommunityError("revision_conflict", "Publication changed. Refresh before trying again.")
        self._store({**local, "publication": value, "needs_refresh": False, "last_error": None})
        return value

    async def refresh(self, *, context=None):
        context = self._context() if context is None else context
        owner, database = context[:2]
        value = await self._request("GET", "/v2/account/progress", owner=owner, context=context)
        return self._accept(value, owner, database, context=context)

    async def _content(self):
        if self._project is None:
            from .progress_snapshot import project
        else:
            project = self._project
        database = self._database()
        frozen = SimpleNamespace(db=database, ranks=self.service.ranks,
                                 effective_game_version=self.service.effective_game_version)

        def read():
            # Database query methods own a non-reentrant Lock. Taking it here
            # deadlocks pbs()/segment_defs()/attempt(). Their main PB query
            # snapshots current rows; serial/context guards fence later edits.
            return project(frozen)

        task = asyncio.create_task(asyncio.to_thread(read))
        self._jobs.add(task)
        task.add_done_callback(self._jobs.discard)
        return await asyncio.shield(task)

    async def _read_content(self, context):
        serial = self._serial
        try:
            content = await self._content()
        except CommunityError as error:
            self._guard(context)
            if error.code == "progress_snapshot_too_large":
                raise CommunityError("snapshot_too_large", "This profile exceeds the publication limit; no partial snapshot was sent.") from None
            raise
        self._guard(context)
        return content, serial

    @staticmethod
    def _check_size(content, body):
        if len(content["rows"]) > 4096 or len(canonical(body)) > 1048576:
            raise CommunityError("snapshot_too_large", "This profile exceeds the publication limit; no partial snapshot was sent.")

    async def _preview(self, context, publication):
        try:
            content, _serial = await self._read_content(context)
            size = len(canonical(self._envelope(publication, content, context[0])))
            return {"rows": len(content["rows"]), "bytes": size, "coverage": content["coverage"],
                    "default_rom": content["default_rom"], "oversize": size > 1048576 or len(content["rows"]) > 4096}
        except CommunityError as error:
            if error.code != "snapshot_too_large":
                raise
            return {"rows": None, "bytes": None, "coverage": None, "default_rom": None,
                    "oversize": True, "reason": error.public_message}

    async def status(self, *, refresh=True):
        try:
            context = self._context()
            owner = context[0]
            local = self._local(owner)
            publication = await self.refresh(context=context) if refresh or not local.get("publication") else local["publication"]
            if refresh or self._profile is None or self._profile[0] != owner:
                profile = await self._request("GET", "/v2/account/player-profile", owner=owner, context=context)
                self._profile = owner, profile
            preview = await self._preview(context, publication)
            self._guard(context)
            publisher = self.local_publisher()
            local = self._local(owner)
            conflict = bool(publication["publisher"] and not state.same_publisher(publication["publisher"], publisher))
            return {"available": True, "reason": None, "uid": owner, "publication": publication,
                    "local": {"publisher": publisher, "dirty": local["dirty"],
                              "last_error": state.display_error(local.get("last_error")), "conflict": conflict},
                    "preview": preview,
                    "consent": {"version": state.NOTICE, "text": state.NOTICE_TEXT},
                    "player_profile": self._profile[1]}
        except CommunityError as error:
            if error.code not in ("sign_in_required", "local_profile_unavailable"):
                raise
            return {"available": False, "reason": error.public_message}

    def _envelope(self, publication, content, owner):
        publisher = self.local_publisher()
        return {"uid": owner, "expected_revision": publication["revision"],
                "consent_revision": publication["consent_revision"],
                "publisher": {key: publisher[key] for key in ("installation_id", "profile_id")},
                "captured_utc": utc_now(), "content_hash": hashlib.sha256(canonical(content)).hexdigest(),
                "content": content}

    async def control(self, action, revision, notice=None):
        if action not in ("enable", "disable", "withdraw", "switch") or type(revision) is not int or revision < 0:
            raise CommunityError("invalid_progress", "Choose a publication action from its current preview.")
        if action in ("enable", "switch") and notice != state.NOTICE:
            raise CommunityError("progress_consent_required", "Review and accept what will be shared first.")
        async with self._control_lock:
            context = self._context()
            owner, database = context[:2]
            if action in ("enable", "switch"):
                content, serial = await self._read_content(context)
                self._check_size(content, self._envelope(self._local(owner).get("publication") or
                                                       {"revision": revision, "consent_revision": 0}, content, owner))
                if serial != self._serial:
                    raise CommunityError("invalid_progress", "Saved PBs changed while the preview loaded. Review it again.")
            self._fence += 1
            context = self._context()
            # Persist only uncertainty about a control result, never a body.
            # A lost enable acknowledgment must reconcile even if the last
            # locally acknowledged state said publication was disabled.
            self._store({**self._local(owner), "dirty": True, "needs_refresh": True})
            value = await self._request("PUT", "/v2/account/progress/control", owner=owner, context=context, json={
                "expected_revision": revision, "action": action,
                "publisher": self.local_publisher() if action in ("enable", "switch") else None,
                "notice_version": notice if action in ("enable", "switch") else None})
            self._accept(value, owner, database, context=context)
            local = self._local(owner)
            self._store({**local, "dirty": True})
            if self._wake is not None:
                self._wake.set()
        if action in ("enable", "switch"):
            try:
                await self.synchronize()
            except CommunityError as error:
                self._remember_error(error)
        return await self.status(refresh=False)

    async def synchronize(self):
        async with self._sync_lock:
            context = self._context()
            owner, database = context[:2]
            content, serial = await self._read_content(context)
            if serial != self._serial:
                self._finish(context, serial, clean=False)
                return
            acknowledged = self._local(owner).get("publication")
            if (acknowledged and not self._local(owner).get("needs_refresh") and acknowledged["enabled"] and acknowledged["visible"]
                    and state.same_publisher(acknowledged["publisher"], self.local_publisher())
                    and hashlib.sha256(canonical(content)).hexdigest() == acknowledged["content_hash"]):
                self._finish(context, serial, clean=True)
                return  # Unchanged facts need no cloud read or write.
            publication = await self.refresh(context=context)
            if not publication["enabled"]:
                return
            if not state.same_publisher(publication["publisher"], self.local_publisher()):
                raise CommunityError("publisher_conflict", "Another Trainer profile publishes this account. Review it before switching.")
            body = self._envelope(publication, content, owner)
            self._check_size(content, body)
            if serial != self._serial:
                self._finish(context, serial, clean=False)
                return
            if body["content_hash"] != publication["content_hash"] or not publication["visible"]:
                value = await self._request("PUT", "/v2/account/progress/snapshot", owner=owner, context=context, json=body)
                publication = self._accept(value, owner, database, context=context)
            latest, serial = await self._read_content(context)
            self._finish(context, serial, clean=hashlib.sha256(canonical(latest)).hexdigest() == publication["content_hash"])

    async def save_profile(self, body):
        context = self._context()
        owner = context[0]
        value = await self._request("PUT", "/v2/account/player-profile", owner=owner, context=context, json=body)
        self._profile = owner, value
        return await self.status(refresh=False)

    async def search(self, prefix, cursor=None):
        return await self._request("GET", "/v2/players", params={"prefix": prefix, **({"cursor": cursor} if cursor else {})})

    async def player(self, identifier):
        state.checked_uuid(identifier)
        return await self._request("GET", "/v2/players/" + identifier)

    async def published(self, identifier):
        state.checked_uuid(identifier)
        return await self._request("GET", "/v2/players/" + identifier + "/progress")

    async def recordings(self, identifier, cursor=None):
        state.checked_uuid(identifier)
        return await self._request("GET", "/v2/players/" + identifier + "/recordings",
                                   params={"cursor": cursor} if cursor else {})
