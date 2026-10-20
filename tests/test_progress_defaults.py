"""Default PB sharing initializes virgin accounts without undoing choices."""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from sm64_events.community import CommunityError
from sm64_events.community import progress_state as state
from sm64_events.community.progress_sync import ProgressSync
from sm64_events.server.community_api import create_community_router
from test_progress_sync import Database, INSTALLATION, OTHER, OWNER, PUBLISHER, content, digest, publication, setup


def virgin():
    return publication(enabled=False, revision=0)


async def settled(sync, predicate):
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(.01)
    assert predicate()


def test_active_virgin_worker_enables_and_publishes_once_then_restart_reuses_ack_without_http():
    async def scenario():
        sync, db, account = setup(acknowledged=virgin())
        await sync.start()
        try:
            await settled(sync, lambda: account.control["visible"] and not sync._local(OWNER)["dirty"])
            assert account.control["enabled"] and account.control["content_hash"] == digest(db.content)
            controls = [kwargs["json"] for _method, path, kwargs in account.calls if path.endswith("/control")]
            assert len(controls) == 1 and controls[0]["action"] == "enable"
            assert controls[0]["expected_revision"] == 0 and controls[0]["notice_version"] == state.NOTICE
        finally:
            await sync.stop()
        calls = len(account.calls)
        restarted = ProgressSync(sync.service, account, installation=INSTALLATION,
                                 project=lambda frozen: deepcopy(frozen.db.content), period=.001)
        await restarted.start()
        try:
            await settled(restarted, lambda: not restarted._local(OWNER)["dirty"])
            assert len(account.calls) == calls
        finally:
            await restarted.stop()
    asyncio.run(scenario())


def test_get_status_reports_default_on_pending_without_initializing_remote_sharing():
    async def scenario():
        sync, _db, account = setup(acknowledged=virgin())
        value = await sync.status()
        assert not value["publication"]["enabled"] and value["local"]["share_pbs"] and value["local"]["default_pending"]
        assert all(method == "GET" for method, _path, _kwargs in account.calls)
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["account", "database", "auth_epoch"])
def test_default_initialization_stops_when_owner_profile_or_auth_session_changes_during_lookup(change):
    async def scenario():
        sync, db, account = setup(acknowledged=virgin())
        original = account.request_json
        async def changed(method, path, **kwargs):
            value = await original(method, path, **kwargs)
            if method == "GET" and path == "/v2/account/progress":
                if change == "account": account.state["uid"] = OTHER
                elif change == "database": sync.service.db = Database()
                else: account._epoch += 1
            return value
        account.request_json = changed
        with pytest.raises(CommunityError) as error: await sync.initialize_defaults()
        assert error.value.code == "canceled" and all(method == "GET" for method, _path, _kwargs in account.calls)
        assert state.default_choice(db, OWNER) is None
    asyncio.run(scenario())


@pytest.mark.parametrize("kind", ["paused", "withdrawn", "other_publisher", "already_selected"])
def test_existing_server_choices_and_publishers_are_never_initialized_again(kind):
    async def scenario():
        value = publication(enabled=kind in ("other_publisher", "already_selected"), revision=4)
        if kind != "withdrawn":
            value["publisher"] = deepcopy(PUBLISHER)
        if kind == "other_publisher":
            value["publisher"]["profile_id"] = OTHER
        sync, _db, account = setup(acknowledged=value)
        result = await sync.initialize_defaults()
        assert result == value and account.control == value
        assert all(method == "GET" for method, _path, _kwargs in account.calls)
        # A second local DB with no remembered choice sees the same global fence.
        sync.service.db = Database()
        assert await sync.initialize_defaults() == value
        assert all(method == "GET" for method, _path, _kwargs in account.calls)
    asyncio.run(scenario())


def test_new_account_false_is_saved_before_activation_and_creates_disabled_server_choice_for_other_devices():
    sync, db, account = setup(acknowledged=virgin())
    account.state["status"] = "new"
    completed = []
    async def complete_profile(**body):
        assert state.default_choice(db, OWNER) is False
        assert "share_pbs" not in body
        completed.append(body)
        account.state.update(body, status="active")
        await asyncio.sleep(0)  # A worker may run as soon as the account becomes active.
    account.complete_profile = complete_profile
    @asynccontextmanager
    async def life(_app):
        await sync.start()
        try: yield
        finally: await sync.stop()
    app = FastAPI(lifespan=life)
    app.include_router(create_community_router(account, sync))
    with TestClient(app) as http:
        result = http.put('/api/community/account', json={"name": "NewRunner", "terms_version": "t1", "privacy_version": "p1",
                                                         "sharing": False, "share_pbs": False})
        assert result.status_code == 200, result.text
    assert completed and state.default_choice(db, OWNER) is False
    assert account.control["revision"] == 1 and not account.control["enabled"] and account.control["publisher"] is None
    assert all(kwargs.get("json", {}).get("action") != "enable" and not path.endswith("/snapshot") for _method, path, kwargs in account.calls)
    async def another_device():
        other_sync = ProgressSync(sync.service, account, installation=INSTALLATION,
                                  project=lambda frozen: deepcopy(frozen.db.content))
        other_sync.service.db = Database()
        await other_sync.initialize_defaults()
        assert not account.control["enabled"] and account.control["revision"] == 1
    asyncio.run(another_device())


def test_explicit_new_false_survives_failed_profile_completion_and_omitted_field_on_retry():
    sync, db, account = setup(acknowledged=virgin())
    account.state["status"] = "new"
    attempts = []
    async def complete_profile(**body):
        attempts.append(body)
        assert "share_pbs" not in body and state.default_choice(db, OWNER) is False
        if len(attempts) == 1:
            raise CommunityError("network_unavailable")
        account.state.update(body, status="active")
    account.complete_profile = complete_profile
    app = FastAPI()
    app.include_router(create_community_router(account, sync))
    body = {"name": "NewRunner", "terms_version": "t1", "privacy_version": "p1", "sharing": False, "share_pbs": False}
    with TestClient(app) as http:
        assert http.put('/api/community/account', json=body).status_code >= 400
        assert account.state["status"] == "new" and state.default_choice(db, OWNER) is False
        body.pop("share_pbs")
        result = http.put('/api/community/account', json=body)
        assert result.status_code == 200, result.text
    assert not account.control["enabled"] and account.control["revision"] == 1


@pytest.mark.parametrize("choice", [None, True])
def test_new_account_completion_defaults_on_without_extending_cloud_v1_profile_schema(choice):
    sync, _db, account = setup(acknowledged=virgin())
    account.state["status"] = "new"
    async def complete_profile(**body):
        assert "share_pbs" not in body
        account.state.update(body, status="active")
    account.complete_profile = complete_profile
    app = FastAPI()
    app.include_router(create_community_router(account, sync))
    body = {"name": "NewRunner", "terms_version": "t1", "privacy_version": "p1", "sharing": False}
    if choice is not None:
        body["share_pbs"] = choice
    with TestClient(app) as http:
        result = http.put('/api/community/account', json=body)
    assert result.status_code == 200, result.text
    assert account.control["enabled"] and account.control["visible"]


@pytest.mark.parametrize("stage", ["queued", "projection"])
def test_newer_local_optout_cancels_stale_default_enable_before_any_control_request(stage):
    async def scenario():
        sync, _db, account = setup(acknowledged=virgin())
        entered, release = asyncio.Event(), asyncio.Event()
        if stage == "queued":
            await sync._control_lock.acquire()
        else:
            async def deferred():
                entered.set()
                await release.wait()
                return content()
            sync._content = deferred
        task = asyncio.create_task(sync.initialize_defaults())
        if stage == "queued":
            await asyncio.sleep(0)
        else:
            await entered.wait()
        sync.remember_choice(False)
        if stage == "queued": sync._control_lock.release()
        else: release.set()
        with pytest.raises(CommunityError) as error: await task
        assert error.value.code == "canceled"
        assert not any(method == "PUT" for method, _path, _kwargs in account.calls)
        await sync.initialize_defaults()
        assert not account.control["enabled"] and account.control["revision"] == 1
    asyncio.run(scenario())


@pytest.mark.parametrize("other_publisher", [False, True])
def test_acknowledged_local_pause_does_not_undo_later_explicit_enable_from_another_device(other_publisher):
    async def scenario():
        sync, _db, account = setup()
        await sync.control("disable", 1)
        assert sync._local(OWNER).get("pending_pause_revision") is None
        assert state.default_choice(sync.service.db, OWNER) is False
        account.control.update(enabled=True, revision=3, consent_revision=3)
        if other_publisher:
            account.control["publisher"]["profile_id"] = OTHER
        value = await sync.status()
        assert value["local"]["share_pbs"] is True and value["local"]["conflict"] == other_publisher
        baseline = sum(path.endswith("/control") for _method, path, _kwargs in account.calls)
        await sync.start()
        try:
            if other_publisher:
                await settled(sync, lambda: sync._local(OWNER).get("last_error") == "publisher_conflict")
            else:
                await settled(sync, lambda: account.control["visible"] and not sync._local(OWNER)["dirty"])
            assert account.control["enabled"] and account.control["revision"] >= 3
            assert sum(path.endswith("/control") for _method, path, _kwargs in account.calls) == baseline
        finally:
            await sync.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["disable", "withdraw"])
def test_offline_explicit_removal_retries_original_intent_without_default_enable(action):
    async def scenario():
        sync, _db, account = setup(acknowledged=virgin())
        original = account.request_json
        async def unavailable(method, path, **kwargs):
            if path.endswith("/control"):
                raise CommunityError("network_unavailable")
            return await original(method, path, **kwargs)
        account.request_json = unavailable
        with pytest.raises(CommunityError): await sync.control(action, 0)
        assert state.default_choice(sync.service.db, OWNER) is False
        assert sync._local(OWNER)["pending_pause_revision"] == 0
        before = len(account.calls)
        await sync.synchronize()
        assert len(account.calls) == before
        account.request_json = original
        await sync.start()
        try:
            await settled(sync, lambda: account.control["revision"] == 1)
            assert not account.control["enabled"] and not account.control["visible"]
            controls = [kwargs["json"]["action"] for method, path, kwargs in account.calls if method == "PUT" and path.endswith("/control")]
            assert controls == [action]
        finally:
            await sync.stop()
    asyncio.run(scenario())


def test_default_enable_lost_ack_reconciles_current_server_state_without_second_enable():
    async def scenario():
        sync, _db, account = setup(acknowledged=virgin())
        account.fail_control_ack = True
        with pytest.raises(CommunityError): await sync.initialize_defaults()
        assert account.control["enabled"] and account.control["revision"] == 1
        await sync.start()
        try:
            await settled(sync, lambda: account.control["visible"] and not sync._local(OWNER)["dirty"])
            assert sum(path.endswith("/control") for _method, path, _kwargs in account.calls) == 1
        finally:
            await sync.stop()
    asyncio.run(scenario())


def test_offline_withdraw_keeps_intent_when_same_revision_snapshot_is_hidden_but_still_retained():
    async def scenario():
        hidden = publication(enabled=False, revision=4, value=content())
        hidden.update(visible=False, publisher=deepcopy(PUBLISHER))
        sync, _db, account = setup(acknowledged=hidden)
        original = account.request_json
        async def offline(method, path, **kwargs):
            if path.endswith("/control"):
                raise CommunityError("network_unavailable")
            return await original(method, path, **kwargs)
        account.request_json = offline
        with pytest.raises(CommunityError): await sync.control("withdraw", 4)
        account.request_json = original
        await sync.refresh()
        assert sync._local(OWNER)["pending_pause_revision"] == 4
        assert account.control["content_hash"] is not None
        await sync.start()
        try:
            await settled(sync, lambda: account.control["revision"] == 5)
            assert account.control["content_hash"] is None and not account.control["visible"]
            controls = [kwargs["json"]["action"] for method, path, kwargs in account.calls if method == "PUT" and path.endswith("/control")]
            assert controls == ["withdraw"]
        finally:
            await sync.stop()
    asyncio.run(scenario())


def test_oversized_virgin_profile_never_enables_but_can_record_an_explicit_optout():
    async def scenario():
        sync, _db, account = setup(acknowledged=virgin())
        def oversized(_frozen): raise CommunityError("progress_snapshot_too_large")
        sync._project = oversized
        with pytest.raises(CommunityError) as error: await sync.initialize_defaults()
        assert error.value.code == "snapshot_too_large"
        assert not any(method == "PUT" for method, _path, _kwargs in account.calls)
        sync.remember_choice(False)
        await sync.initialize_defaults()
        assert not account.control["enabled"] and account.control["revision"] == 1
    asyncio.run(scenario())
