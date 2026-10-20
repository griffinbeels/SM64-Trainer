"""Publication races are tested at deferred projection and acknowledgment edges."""
import asyncio
from copy import deepcopy
import hashlib
import threading
from types import SimpleNamespace

import pytest

from sm64_events.community import CommunityError
from sm64_events.community import progress_state as state
from sm64_events.community.progress_sync import ProgressSync
from sm64_events.ghost.sharing_targets import canonical

OWNER = "10000000-0000-4000-8000-000000000001"
OTHER = "10000000-0000-4000-8000-000000000002"
INSTALLATION = "20000000-0000-4000-8000-000000000001"
PROFILE = "30000000-0000-4000-8000-000000000001"
PUBLISHER = {"installation_id": INSTALLATION, "profile_id": PROFILE, "label": "This Trainer profile"}
STAMP = "2026-10-19T12:34:56Z"


def content(frames=300):
    return {"schema": 1, "default_rom": "us", "rows": [{"frames": frames}],
            "coverage": {"total_rows": 1, "shared_rows": 1, "withheld_rows": 0, "unsupported_rows": 0, "complete": True}}


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def publication(*, enabled=True, revision=1, value=None):
    return {"v": 2, "uid": OWNER, "revision": revision, "consent_revision": revision,
            "enabled": enabled, "visible": value is not None, "publisher": deepcopy(PUBLISHER) if enabled else None,
            "notice_version": state.NOTICE, "content_hash": digest(value) if value else None,
            "captured_utc": STAMP if value else None, "synced_utc": STAMP if value else None,
            "limits": {"rows": 4096, "bytes": 1048576}}


class Database:
    def __init__(self):
        self._lock = threading.RLock()
        self.values = {state.PROFILE_KEY: PROFILE}
        self.content = content()
        self.writes = []
    def get_state(self, key, default=None):
        return deepcopy(self.values.get(key, default))
    def set_state(self, key, value):
        self.values[key] = deepcopy(value)
        self.writes.append((key, deepcopy(value)))


class Account:
    def __init__(self, control=None):
        self.state = {"uid": OWNER, "status": "active", "authenticated": True, "stored": True, "enabled": True}
        self._epoch = 1
        self.control = deepcopy(control or publication())
        self.profile = {"v": 2, "uid": OWNER, "revision": 0, "bio": "", "socials": []}
        self.calls = []
        self.fail_snapshot_ack = self.fail_control_ack = False
        self.snapshot_entered = self.snapshot_release = self.control_applied = None
    def snapshot(self):
        return dict(self.state)
    async def request_json(self, method, path, **kwargs):
        self.calls.append((method, path, deepcopy(kwargs)))
        body = kwargs.get("json", {})
        if path == "/v2/account/player-profile":
            if method == "PUT":
                self.profile.update(bio=body["bio"], socials=body["socials"], revision=self.profile["revision"] + 1)
            return deepcopy(self.profile)
        if method == "GET":
            return deepcopy(self.control)
        if body["expected_revision"] != self.control["revision"]:
            raise CommunityError("revision_conflict", status=409)
        if path.endswith("/control"):
            action = body["action"]
            self.control.update(revision=self.control["revision"] + 1, consent_revision=self.control["consent_revision"] + 1,
                                enabled=action in ("enable", "switch"))
            if action in ("enable", "switch"):
                self.control["publisher"] = deepcopy(body["publisher"])
            if action in ("withdraw", "switch"):
                self.control.update(visible=False, content_hash=None, captured_utc=None, synced_utc=None)
            if self.control_applied:
                self.control_applied.set()
            if self.fail_control_ack:
                self.fail_control_ack = False
                raise CommunityError("network_unavailable")
            return deepcopy(self.control)
        assert path.endswith("/snapshot")
        assert self.control["enabled"] and state.same_publisher(body["publisher"], self.control["publisher"])
        assert body["consent_revision"] == self.control["consent_revision"]
        self.control.update(revision=self.control["revision"] + 1, content_hash=body["content_hash"],
                            visible=True, captured_utc=body["captured_utc"], synced_utc=STAMP)
        result = deepcopy(self.control)
        if self.snapshot_entered:
            self.snapshot_entered.set()
            if self.snapshot_release:
                release, self.snapshot_release = self.snapshot_release, None
                await release.wait()
        if self.fail_snapshot_ack:
            self.fail_snapshot_ack = False
            raise CommunityError("network_unavailable")
        return result


def setup(*, acknowledged=None):
    db = Database()
    account = Account(acknowledged)
    svc = SimpleNamespace(db=db, ranks=SimpleNamespace(grading_version="us"), effective_game_version=lambda: "us")
    sync = ProgressSync(svc, account, installation=INSTALLATION, project=lambda frozen: deepcopy(frozen.db.content), period=.001)
    if acknowledged:
        db.set_state(state.KEY, {"owner": OWNER, "dirty": True, "publication": deepcopy(acknowledged), "last_error": None})
    sync._profile = OWNER, deepcopy(account.profile)
    return sync, db, account


def mark(sync):
    sync.observe(SimpleNamespace(type="pb_saved"))


def test_mutation_between_projection_capture_and_resume_cannot_be_lost_by_unchanged_fastpath():
    async def scenario():
        sync, db, account = setup(acknowledged=publication(value=content()))
        entered, release = asyncio.Event(), asyncio.Event()
        first = True
        async def deferred():
            nonlocal first
            captured = deepcopy(db.content)
            if first:
                first = False
                entered.set()
                await release.wait()
            return captured
        sync._content = deferred
        task = asyncio.create_task(sync.synchronize())
        await entered.wait()
        db.content = content(500)
        mark(sync)
        release.set()
        await task
        assert sync._local(OWNER)["dirty"] and not account.calls
        await sync.synchronize()
        assert account.control["content_hash"] == digest(content(500)) and not sync._local(OWNER)["dirty"]
    asyncio.run(scenario())


def test_mutation_during_final_projection_leaves_dirty_even_when_old_result_matches_ack():
    async def scenario():
        sync, db, account = setup()
        entered, release = asyncio.Event(), asyncio.Event()
        reads = 0
        async def deferred():
            nonlocal reads
            reads += 1
            captured = deepcopy(db.content)
            if reads == 2:
                entered.set()
                await release.wait()
            return captured
        sync._content = deferred
        task = asyncio.create_task(sync.synchronize())
        await entered.wait()
        db.content = content(700)
        mark(sync)
        release.set()
        await task
        assert sync._local(OWNER)["dirty"]
        await sync.synchronize()
        assert account.control["content_hash"] == digest(content(700)) and not sync._local(OWNER)["dirty"]
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["account", "profile", "epoch", "fence"])
def test_projection_cannot_clean_or_publish_after_owner_profile_session_or_control_changes(change):
    async def scenario():
        sync, db, account = setup(acknowledged=publication(value=content()))
        entered, release = asyncio.Event(), asyncio.Event()
        async def deferred():
            entered.set()
            await release.wait()
            return content()
        sync._content = deferred
        task = asyncio.create_task(sync.synchronize())
        await entered.wait()
        if change == "account": account.state["uid"] = OTHER
        elif change == "profile": sync.service.db = Database()
        elif change == "epoch": account._epoch += 1
        else: sync._fence += 1
        release.set()
        with pytest.raises(CommunityError) as error: await task
        assert error.value.code == "canceled" and not account.calls
        assert db.get_state(state.KEY)["dirty"]
        if change == "profile": assert not sync.service.db.writes
    asyncio.run(scenario())


def test_lost_snapshot_ack_reconciles_hash_without_republishing_old_body():
    async def scenario():
        sync, db, account = setup()
        account.fail_snapshot_ack = True
        with pytest.raises(CommunityError): await sync.synchronize()
        accepted = account.control["revision"]
        await sync.synchronize()
        assert sync._local(OWNER)["publication"]["revision"] == accepted
        assert sum(path.endswith("/snapshot") for _method, path, _kwargs in account.calls) == 1
        assert not sync._local(OWNER)["dirty"]
        assert '"content":' not in canonical(db.values).decode() and '"frames":' not in canonical(db.values).decode()
    asyncio.run(scenario())


def test_old_snapshot_ack_after_withdraw_and_reenable_cannot_clobber_new_control_state():
    async def scenario():
        sync, db, account = setup()
        account.snapshot_entered, account.snapshot_release = asyncio.Event(), asyncio.Event()
        release = account.snapshot_release
        old = asyncio.create_task(sync.synchronize())
        await account.snapshot_entered.wait()
        await sync.control("withdraw", account.control["revision"])
        db.content = content(800)
        account.control_applied = asyncio.Event()
        enabled = asyncio.create_task(sync.control("enable", account.control["revision"], state.NOTICE))
        await account.control_applied.wait()
        release.set()
        with pytest.raises(CommunityError) as error: await old
        assert error.value.code == "canceled"
        await enabled
        assert account.control["content_hash"] == digest(content(800))
        revisions = [value["publication"]["revision"] for key, value in db.writes if key == state.KEY and value.get("publication")]
        assert revisions == sorted(revisions)
        assert sync._local(OWNER)["publication"]["revision"] == account.control["revision"]
    asyncio.run(scenario())


def test_startup_reconciles_lost_enable_ack_even_when_old_local_state_was_disabled():
    async def scenario():
        sync, _db, account = setup(acknowledged=publication(enabled=False, revision=0))
        account.fail_control_ack = True
        with pytest.raises(CommunityError): await sync.control("enable", 0, state.NOTICE)
        assert sync._local(OWNER)["needs_refresh"] and not sync._local(OWNER)["publication"]["enabled"]
        await sync.start()
        try:
            for _ in range(100):
                if not sync._local(OWNER)["dirty"]: break
                await asyncio.sleep(.01)
            assert not sync._local(OWNER)["dirty"] and account.control["content_hash"] == digest(content())
            assert sum(path.endswith("/control") for _method, path, _kwargs in account.calls) == 1
        finally: await sync.stop()
    asyncio.run(scenario())


def test_unchanged_acknowledged_content_needs_no_http_even_at_startup():
    async def scenario():
        sync, _db, account = setup(acknowledged=publication(value=content()))
        await sync.start()
        try:
            for _ in range(100):
                if not sync._local(OWNER)["dirty"]: break
                await asyncio.sleep(.01)
            for _ in range(5): await sync.synchronize()
            assert not account.calls and not sync._local(OWNER)["dirty"]
        finally: await sync.stop()
    asyncio.run(scenario())


def test_only_selected_publisher_can_send_a_snapshot():
    async def scenario():
        other = publication()
        other["publisher"]["profile_id"] = OTHER
        sync, _db, account = setup(acknowledged=other)
        with pytest.raises(CommunityError) as error: await sync.synchronize()
        assert error.value.code == "publisher_conflict"
        assert all(method == "GET" for method, _path, _kwargs in account.calls)
    asyncio.run(scenario())


def test_restored_authorized_publisher_can_reshow_unchanged_facts_retained_by_moderation_fence():
    async def scenario():
        hidden = publication(value=content(), revision=7)
        hidden["visible"] = False  # Restore + explicit enable retains the hash but not public visibility.
        sync, _db, account = setup(acknowledged=hidden)
        await sync.synchronize()
        assert account.control["visible"] and account.control["revision"] == 8
        assert sum(path.endswith("/snapshot") for _method, path, _kwargs in account.calls) == 1
        assert not sync._local(OWNER)["dirty"]
        account.calls.clear()
        await sync.synchronize()
        assert not account.calls
    asyncio.run(scenario())


def test_signed_out_edits_dirty_existing_owner_and_publish_current_facts_after_signin():
    async def scenario():
        sync, db, account = setup(acknowledged=publication(value=content()))
        await sync.start()
        try:
            for _ in range(100):
                if not sync._local(OWNER)["dirty"]: break
                await asyncio.sleep(.01)
            assert not account.calls
            account.state.update(uid=None, authenticated=False, stored=False, status="signed_out")
            db.content = content(900)
            mark(sync)
            assert db.get_state(state.KEY)["dirty"] and not account.calls
            account.state.update(uid=OWNER, authenticated=True, stored=True, status="active")
            account._epoch += 1
            sync._wake.set()
            for _ in range(100):
                if not sync._local(OWNER)["dirty"]: break
                await asyncio.sleep(.01)
            assert not sync._local(OWNER)["dirty"] and account.control["content_hash"] == digest(content(900))
        finally: await sync.stop()
    asyncio.run(scenario())


def test_oversize_preview_does_not_block_pause_withdraw_or_bio_but_prevents_enable_and_switch():
    async def scenario():
        sync, _db, account = setup(acknowledged=publication())
        def oversized(_service): raise CommunityError("progress_snapshot_too_large")
        sync._project = oversized
        preview = await sync.status(refresh=False)
        assert preview["available"] and preview["preview"]["oversize"] and preview["preview"]["rows"] is None
        for action in ("enable", "switch"):
            before = len(account.calls)
            with pytest.raises(CommunityError) as error: await sync.control(action, account.control["revision"], state.NOTICE)
            assert error.value.code == "snapshot_too_large" and len(account.calls) == before
        assert not (await sync.control("disable", account.control["revision"]))["publication"]["enabled"]
        assert not (await sync.control("withdraw", account.control["revision"]))["publication"]["visible"]
        assert (await sync.save_profile({"expected_revision": 0, "bio": "Still editable", "socials": []}))["player_profile"]["bio"] == "Still editable"
    asyncio.run(scenario())


def test_stop_joins_its_own_projection_worker():
    async def scenario():
        sync, _db, _account = setup()
        entered, release = asyncio.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def blocked(_service):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            return content()
        sync._project = blocked
        pending = asyncio.create_task(sync._content())
        await entered.wait()
        stopping = asyncio.create_task(sync.stop())
        try:
            await asyncio.sleep(0)
            assert not stopping.done()
        finally: release.set()
        await stopping
        await pending
        assert not sync._jobs
    asyncio.run(scenario())


def test_settings_projection_uses_the_real_database_nonreentrant_lock(tmp_path):
    from sm64_events.storage.db import Database as RealDatabase

    async def scenario():
        database = RealDatabase(tmp_path / 'profile.db')
        database.set_state(state.PROFILE_KEY, PROFILE)
        database.insert_pb(1, 0, 'Standard', 'igt', 300, None, STAMP, game_version='us')
        service = SimpleNamespace(db=database, ranks=SimpleNamespace(grading_version='us'),
                                  effective_game_version=lambda: 'us')
        sync = ProgressSync(service, Account(), installation=INSTALLATION)
        try:
            result = await asyncio.wait_for(sync.status(), timeout=2)
            assert result['available'] and result['preview']['rows'] == 1
            assert result['preview']['coverage']['complete']
            assert database._lock.acquire(blocking=False)
            database._lock.release()
        finally:
            await sync.stop()
            database.close()
    asyncio.run(scenario())
