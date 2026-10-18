"""Consented own native sources recover without attributing historical runs."""
import asyncio
from copy import deepcopy
import hashlib
import json
import threading
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from sm64_events.community import CachedPopulation, CommunityAccount, CommunitySync
from sm64_events.community.config import CommunityError
from sm64_events.community.outbox import MAX_BYTES, MAX_OFFERS, RetractionOutbox, SourceOutbox
from test_community_account import Store, config, profile, UID
from test_community_sync import Account, Local, TARGET, IDENTITY, catalog, native, page, recording


def source(path, frames=3, strategy="Standard"):
    raw = native(frames)
    path.write_bytes(raw)
    return {"target": {**TARGET, "strat": strategy}, "clock": "igt", "game_version": "us", "strategy": strategy,
            "frames": frames, "path": str(path), "raw_sha256": hashlib.sha256(raw).hexdigest(), "full_start": True, "valid": True}


def test_retraction_freshness_survives_restart_and_repeated_same_strategy(tmp_path):
    path = tmp_path / "retractions.json"
    outbox = RetractionOutbox(path)
    first = {"owner": UID, "raw_sha256": hashlib.sha256(native()).hexdigest(),
             "keep_strategy": "B", "key": str(uuid4())}
    newest = {**first, "key": str(uuid4())}
    outbox.remember(first)
    outbox.remember({**first, "keep_strategy": "C", "key": str(uuid4())})
    outbox.remember(newest)
    restored = RetractionOutbox(path)
    assert restored.is_current(newest) and not restored.is_current(first)
    restored.discard(first)  # An obsolete HTTP success cannot erase the newer retry.
    assert restored.pending(UID) == (newest,)
    restored.discard(newest)
    assert not restored.is_current(newest) and RetractionOutbox(path).pending(UID) == ()


def sync_at(root, account):
    return CommunitySync(account, CachedPopulation(Local(), root=root / "cache", account=account))


async def settled(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(.01)
    assert predicate()


def test_offline_contribution_survives_close_and_uploads_after_same_owner_login(tmp_path):
    async def scenario():
        account = Account()
        async def offline(*_args, **_kwargs):
            raise CommunityError("network_unavailable", retry_after=30)
        account.request_json = offline
        first = sync_at(tmp_path, account)
        qualified = source(tmp_path / "run.ghost")
        assert await asyncio.to_thread(first.remember_source, qualified, **first.contribution_context())
        await first.start()
        await settled(lambda: bool(first._offer_retry))
        await first.stop()
        assert not first._offer_retry
        assert first._outbox.path.stat().st_size < MAX_BYTES

        online = Account()
        online.state.update(authenticated=False, status="signed_out", sharing=False, uid=None)
        second = sync_at(tmp_path, online)
        await second.start()
        try:
            await settled(lambda: second._outbox._loaded)
            assert not online.calls and second._outbox.pending(UID)
            online.state.update(authenticated=True, status="active", sharing=True, uid=UID)
            second._contribution_wake.set()
            await settled(lambda: not second._outbox.pending(UID))
            assert [path for _method, path, _kwargs in online.calls] == ["/v1/catalog", "/v1/uploads", "/v1/uploads/grant-1"]
        finally:
            await second.stop()
    asyncio.run(scenario())


def test_login_does_not_adopt_other_owner_or_packaged_historical_sources(tmp_path):
    async def scenario():
        first = sync_at(tmp_path, Account())
        qualified = source(tmp_path / "run.ghost")
        assert await asyncio.to_thread(first.remember_source, qualified, **first.contribution_context())
        other = Account()
        other.state["uid"] = "22222222-2222-4222-8222-222222222222"
        second = sync_at(tmp_path, other)
        assert not second.remember_source(qualified, owner=UID, consent_epoch=0)
        assert not second.remember_source({**qualified, "source_status": "bundled-author-recording"}, **second.contribution_context())
        await second.start()
        try:
            await settled(lambda: second._outbox._loaded)
            assert not other.calls and second._outbox.pending(UID)
        finally:
            await second.stop()
    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["opt_out", "logout"])
def test_explicit_cancellation_is_durable_and_cannot_resurrect_at_next_login(tmp_path, action):
    async def scenario():
        def respond(request):
            assert request.url.path == "/v1/account/sharing"
            return httpx.Response(200, json=profile(sharing=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            account._profile = {key: value for key, value in profile(sharing=True).items() if key != "v"}
            sync = sync_at(tmp_path, account)
            qualified = source(tmp_path / "run.ghost")
            assert await asyncio.to_thread(sync.remember_source, qualified, **sync.contribution_context())
            queued = next(iter(sync._queue.values()))[1]
            if action == "opt_out":
                await account.set_sharing(False)
            else:
                await account.sign_out()
            assert not sync._outbox.pending(UID) and not sync._source_active(queued)
            restored = sync_at(tmp_path, Account())
            await restored._recover_sources()
            assert not restored._outbox.pending(UID) and not restored.account.calls
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["delete", "change", "opt_out"])
def test_removed_changed_or_no_longer_shared_source_is_not_replayed(tmp_path, change):
    async def scenario():
        account = Account()
        first = sync_at(tmp_path, account)
        qualified = source(tmp_path / "run.ghost")
        assert await asyncio.to_thread(first.remember_source, qualified, **first.contribution_context())
        if change == "delete":
            (tmp_path / "run.ghost").unlink()
        elif change == "change":
            (tmp_path / "run.ghost").write_bytes(b"changed")
        else:
            account.state["sharing"] = False
        restored = sync_at(tmp_path, account)
        await restored.start()
        try:
            await settled(lambda: restored._outbox._loaded and not restored._outbox.pending(UID))
            assert not any(path.startswith("/v1/uploads") for _method, path, _kwargs in account.calls)
        finally:
            await restored.stop()
    asyncio.run(scenario())


def test_restart_coalesces_fastest_per_canonical_bin_and_preserves_mutation_keys(tmp_path):
    async def scenario():
        account = Account()
        first = sync_at(tmp_path, account)
        sources = [source(tmp_path / f"run-{frames}.ghost", frames) for frames in (5, 4, 3)]
        for item in sources:
            assert await asyncio.to_thread(first.remember_source, item, **first.contribution_context())
        # A previously sent grant retains its immutable key through restart.
        remembered = {**sources[-1], "owner": UID, "mutation_generation": "g1",
                      "mutation_keys": {"grant": "33333333-3333-4333-8333-333333333333"}}
        await asyncio.to_thread(first._outbox.update, remembered)
        restored = sync_at(tmp_path, account)
        await restored.start()
        try:
            await settled(lambda: restored._outbox._loaded and not restored._outbox.pending(UID))
            grants = [kwargs for _method, path, kwargs in account.calls if path == "/v1/uploads"]
            assert len(grants) == 1 and grants[0]["json"]["frames"] == 3
            assert grants[0]["headers"]["Idempotency-Key"] == remembered["mutation_keys"]["grant"]
        finally:
            await restored.stop()
    asyncio.run(scenario())


def test_recovery_batch_and_metadata_file_have_fixed_bounds(tmp_path):
    async def scenario():
        account = Account()
        original = account.request_json
        async def all_strategies(method, path, **kwargs):
            if path == "/v1/catalog":
                value = catalog()
                entry = value["entries"][0]
                entry["strategies"] = [{"name": f"Strategy {index}", "cutoffs": entry["overall_cutoffs"]} for index in range(16)]
                return value
            return await original(method, path, **kwargs)
        account.request_json = all_strategies
        first = sync_at(tmp_path, account)
        for index in range(16):
            item = source(tmp_path / f"run-{index}.ghost", strategy=f"Strategy {index}")
            assert await asyncio.to_thread(first.remember_source, item, **first.contribution_context())
        restored = sync_at(tmp_path, account)
        await restored._recover_sources()
        assert len(restored._queue) == 8 and len(restored._outbox.pending(UID)) == 16
        outbox = SourceOutbox(tmp_path / "bounded.json")
        value = {**source(tmp_path / "bounds.ghost"), "owner": UID}
        for index in range(MAX_OFFERS + 3):
            outbox.remember({**value, "frames": index + 1})
        assert len(outbox.pending(UID)) == MAX_OFFERS and outbox.path.stat().st_size <= MAX_BYTES
    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", ["oversize", "malformed", "owner", "seed", "version"])
def test_corrupt_or_injected_outbox_metadata_cannot_authorize_contributions(tmp_path, mutation):
    path = tmp_path / "pending-contributions.json"
    value = {"v": 1, "offers": [{**source(tmp_path / "run.ghost"), "owner": UID}]}
    if mutation == "owner":
        value["offers"][0]["owner"] = "other user"
    elif mutation == "seed":
        value["offers"][0]["source_status"] = "bundled-author-recording"
    elif mutation == "version":
        value["v"] = 2
    encoded = json.dumps(deepcopy(value)).encode()
    path.write_bytes(b"x" * (MAX_BYTES + 1) if mutation == "oversize" else b"{" if mutation == "malformed" else encoded)
    outbox = SourceOutbox(path)
    outbox.load()
    assert outbox.load_error and not outbox.pending(UID)


def test_cancel_during_catalog_recovery_cannot_attach_a_new_consent_epoch(tmp_path):
    async def scenario():
        first = sync_at(tmp_path, Account())
        qualified = source(tmp_path / "run.ghost")
        consent = first.contribution_context()
        assert await asyncio.to_thread(first.remember_source, qualified, **consent)
        account = Account()
        entered, resume = asyncio.Event(), asyncio.Event()
        original = account.request_json
        async def delayed(method, path, **kwargs):
            if path == "/v1/catalog":
                entered.set()
                await resume.wait()
            return await original(method, path, **kwargs)
        account.request_json = delayed
        restored = sync_at(tmp_path, account)
        recovery = asyncio.create_task(restored._recover_sources())
        await asyncio.wait_for(entered.wait(), 2)
        await restored.cancel_contributions(UID)
        # The same UID is active again, but the earlier consent is canceled.
        assert restored.contribution_context()["consent_epoch"] == 1
        resume.set()
        await recovery
        assert not restored._queue and not restored._outbox.pending(UID)
        assert not restored.remember_source(qualified, **consent)
        assert not any(path.startswith("/v1/uploads") for _method, path, _kwargs in account.calls)
    asyncio.run(scenario())


def test_logout_before_account_profile_load_clears_previous_owner_intents(tmp_path):
    async def scenario():
        first = sync_at(tmp_path, Account())
        qualified = source(tmp_path / "run.ghost")
        assert await asyncio.to_thread(first.remember_source, qualified, **first.contribution_context())
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500))) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._store.token = "restored-without-profile"
            restored = sync_at(tmp_path, account)
            assert account.snapshot().get("uid") is None and not restored._outbox._loaded
            await account.sign_out()
            assert not restored._outbox.pending(UID)
        next_login = sync_at(tmp_path, Account())
        await next_login._recover_sources()
        assert not next_login._outbox.pending(UID) and not next_login.account.calls
    asyncio.run(scenario())


@pytest.mark.parametrize("unavailable", [False, True])
def test_opt_out_blocks_new_contributions_before_slow_remote_write(tmp_path, unavailable):
    async def scenario():
        entered, resume = asyncio.Event(), asyncio.Event()
        async def respond(request):
            assert request.url.path == "/v1/account/sharing"
            entered.set()
            await resume.wait()
            return httpx.Response(503 if unavailable else 200, json=profile(sharing=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            account._profile = {key: value for key, value in profile(sharing=True).items() if key != "v"}
            sync = sync_at(tmp_path, account)
            qualified = source(tmp_path / "run.ghost")
            old_consent = sync.contribution_context()
            assert await asyncio.to_thread(sync.remember_source, qualified, **old_consent)
            opt_out = asyncio.create_task(account.set_sharing(False))
            await asyncio.wait_for(entered.wait(), 2)
            assert not account.snapshot()["sharing"] and sync.contribution_context() is None
            assert not sync.remember_source(qualified, **old_consent)
            assert not sync._outbox.pending(UID)
            resume.set()
            if unavailable:
                with pytest.raises(CommunityError):
                    await opt_out
            else:
                await opt_out
            assert not account.snapshot()["sharing"]
    asyncio.run(scenario())


def test_expired_persisted_grant_uses_fresh_key_and_finishes_after_restart(tmp_path, monkeypatch):
    from sm64_events.community import sync as sync_module
    monkeypatch.setattr(sync_module.random, "uniform", lambda *_: .001)
    async def scenario():
        qualified = source(tmp_path / "run.ghost")
        old_key = "33333333-3333-4333-8333-333333333333"
        first = sync_at(tmp_path, Account())
        assert await asyncio.to_thread(first.remember_source, qualified, **first.contribution_context())
        first._outbox.update({**qualified, "owner": UID, "mutation_generation": "g1", "mutation_keys": {"grant": old_key}})
        grants = []
        def respond(request):
            if request.url.path == "/v1/catalog":
                return httpx.Response(200, json=catalog())
            if request.url.path == "/v1/uploads":
                key = request.headers["Idempotency-Key"]
                grants.append(key)
                if key == old_key:
                    return httpx.Response(409, json={"v": 1, "error": {"code": "upload_expired"}})
                return httpx.Response(200, json=Account().grant)
            assert request.url.path == "/v1/uploads/grant-1"
            return httpx.Response(200, json={"v": 1, "disposition": "published"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            account._profile = {key: value for key, value in profile(sharing=True).items() if key != "v"}
            restored = sync_at(tmp_path, account)
            await restored.start()
            try:
                await settled(lambda: restored._outbox._loaded and not restored._outbox.pending(UID))
                assert len(grants) == 2 and grants[0] == old_key and grants[1] != old_key
            finally:
                await restored.stop()
    asyncio.run(scenario())


def test_recovery_backlog_and_blocked_upload_do_not_delay_rival_downloads(tmp_path, monkeypatch):
    async def scenario():
        first = sync_at(tmp_path, Account())
        for index in range(12):
            qualified = source(tmp_path / f"run-{index}.ghost", strategy=f"Strategy {index}")
            assert await asyncio.to_thread(first.remember_source, qualified, **first.contribution_context())
        account = Account()
        account.pages = [page([recording("1" * 64)]), page([recording("2" * 64)])]
        entered, resume = asyncio.Event(), asyncio.Event()
        original = account.request_json
        async def delayed(method, path, **kwargs):
            if path == "/v1/catalog":
                value = await original(method, path, **kwargs)
                entry = value["entries"][0]
                entry["strategies"] += [{"name": f"Strategy {index}", "cutoffs": entry["overall_cutoffs"]} for index in range(12)]
                return value
            if path == "/v1/uploads":
                account.calls.append((method, path, kwargs))
                entered.set()
                await resume.wait()
                return account.grant
            return await original(method, path, **kwargs)
        account.request_json = delayed
        restored = sync_at(tmp_path, account)
        write_allowed = threading.Event()
        store_asset = restored.population.store_asset

        def delayed_store(*args, **kwargs):
            assert write_allowed.wait(5)
            return store_asset(*args, **kwargs)

        monkeypatch.setattr(restored.population, "store_asset", delayed_store)
        assert restored.request_pool(IDENTITY, frame_windows=[(1, 1000)])
        assert restored.request_assets(("1" * 64,))
        await restored.start()
        try:
            await asyncio.wait_for(entered.wait(), 2)
            # Upload entry does not imply another thread finished publishing
            # the rival file. Prove it can finish while the upload stays held.
            assert not restored.population.is_ready("1" * 64)
            write_allowed.set()
            await settled(lambda: restored.population.is_ready("1" * 64))
            assert not resume.is_set()
            paths = [path for _method, path, _kwargs in account.calls]
            assert paths.index("/v1/recordings/" + "1" * 64 + "/asset") < paths.index("/v1/uploads")
            assert restored.request_pool(IDENTITY, frame_windows=[(1, 500)])
            assert restored.request_assets(("2" * 64,))
            await settled(lambda: restored.population.is_ready("2" * 64))
            assert not resume.is_set()
            assert len([path for _method, path, _kwargs in account.calls if path == "/v1/catalog"]) == 1
            assert restored.population.get("2" * 64)["raw_sha256"] == hashlib.sha256(native()).hexdigest()
        finally:
            write_allowed.set()
            await restored.stop()
        assert restored._contribution_task is None and not restored._local_jobs
        assert restored._outbox.pending(UID)
    asyncio.run(scenario())


def test_stale_profile_refresh_cannot_reverse_local_opt_out(tmp_path):
    async def scenario():
        getting, putting = asyncio.Event(), asyncio.Event()
        resume_get, resume_put = asyncio.Event(), asyncio.Event()
        async def respond(request):
            if request.url.path == "/v1/config":
                return httpx.Response(200, json={"v": 1})
            if request.url.path == "/v1/account":
                getting.set()
                await resume_get.wait()
                return httpx.Response(200, json=profile(sharing=True))
            assert request.url.path == "/v1/account/sharing"
            putting.set()
            await resume_put.wait()
            return httpx.Response(200, json=profile(sharing=False))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            account._profile = {key: value for key, value in profile(sharing=True).items() if key != "v"}
            sync = sync_at(tmp_path, account)
            refresh = asyncio.create_task(account.refresh())
            await asyncio.wait_for(getting.wait(), 2)
            opt_out = asyncio.create_task(account.set_sharing(False))
            await asyncio.wait_for(putting.wait(), 2)
            resume_get.set()
            await refresh
            assert not account.snapshot()["sharing"] and sync.contribution_context() is None
            resume_put.set()
            await opt_out
            assert not account.snapshot()["sharing"]
    asyncio.run(scenario())


@pytest.mark.parametrize("second_owner", [UID, None])
def test_queued_completed_run_cannot_be_adopted_by_later_login(tmp_path, second_owner):
    from sm64_events.ghost.rival_recording import PopulationRecorder
    async def scenario():
        account = Account()
        sync = sync_at(tmp_path, account)
        qualified = source(tmp_path / "run.ghost")
        entered, resume = threading.Event(), threading.Event()
        class Admission:
            accepted = 0
            def qualified_source(self, *_args, **_kwargs):
                entered.set()
                assert resume.wait(2)
                return qualified
            def accept_verified(self, *_args, **_kwargs): self.accepted += 1
        class Database:
            def get_state(self, key, default): return "local" if key == "ghost_player_id" else default
        admission = Admission()
        recorder = PopulationRecorder(admission, object(), SimpleNamespace(db=Database()),
            lambda _attempt: SimpleNamespace(clock="igt", game_version="us"), community=sync)
        recorder.start()
        first_run = SimpleNamespace(id=1, outcome="success", valid=True, cleared=False)
        recorder.enqueue(first_run)
        await settled(entered.is_set)
        account.state.update(uid=second_owner, status="active" if second_owner else "signed_out", sharing=bool(second_owner))
        recorder.enqueue(SimpleNamespace(id=2, outcome="success", valid=True, cleared=False))
        account.state.update(uid="22222222-2222-4222-8222-222222222222", status="active", sharing=True)
        resume.set()
        await asyncio.wait_for(recorder.queue.join(), 2)
        await recorder.stop()
        assert admission.accepted == 2
        assert not sync._queue and not sync._outbox.pending(UID) and not sync._outbox.pending(account.state["uid"])
    asyncio.run(scenario())
