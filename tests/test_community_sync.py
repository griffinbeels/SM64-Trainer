import asyncio
import hashlib
import time

import pytest

from sm64_events.community import CachedPopulation, CommunityError, CommunitySync
from sm64_events.community.cache import checked_metadata
from sm64_events.community.sync import checked_catalog, checked_page, frame_band
from sm64_events.community import codec
from test_community_codec import native
from test_community_account import UID

TARGET = {"kind": "star", "entity_key": "1-0", "strat": "Standard"}
IDENTITY = {"kind": "star", "entity_key": "1-0", "clock": "igt", "game_version": "us", "strategy": "Standard"}


class Local:
    generation = 0
    closed = False
    def metadata_snapshot(self, *args, **kwargs):
        return ({"id": "local"},)
    def get(self, identifier):
        raise LookupError(identifier)
    def search(self, *args, **kwargs):
        return {"records": [], "next_cursor": None}
    def resolve(self, *args):
        raise LookupError()
    def accept_verified(self, *args, **kwargs):
        return args, kwargs
    def close(self):
        self.closed = True


def recording(identifier="1" * 64, *, frames=3):
    raw = native()
    encoded = codec.encode(raw)
    return {"id": identifier, "player_id": UID, "player_name": "Runner", "target": dict(TARGET),
        "strategy": "Standard", "clock": "igt", "game_version": "us", "frames": frames,
        "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_bytes": len(raw),
        "encoded_sha256": hashlib.sha256(encoded).hexdigest(), "encoded_bytes": len(encoded),
        "codec": "ghc1", "generation": "g1", "step": 2, "provenance": "community-unverified",
        "structurally_valid": True, "full_start": True}


def catalog():
    cutoffs = [{"step": 0, "frames": 1000}, {"step": 1, "frames": 500}, {"step": 2, "frames": 200}]
    return {"v": 1, "generation": "g1", "entries": [{"target": dict(TARGET), "clock": "igt", "game_version": "us",
        "strategies": [{"name": "Standard", "cutoffs": cutoffs}], "overall_cutoffs": cutoffs}]}


def page(rows=(), *, complete=True, cursor=None):
    return {"v": 1, "recordings": list(rows), "coverage": {"complete": complete, "min_step": 0,
        "max_step": 2, "base_step": 0, "generation": "g1"}, "next_cursor": cursor}


class Account:
    def __init__(self):
        self.state = {"authenticated": True, "status": "active", "uid": UID, "sharing": True}
        self.calls = []
        self.pages = [page([recording()])]
        self.grant = {"v": 1, "disposition": "upload", "upload_id": "grant-1", "expires_at": int(time.time()) + 60}
    def snapshot(self):
        return dict(self.state)
    async def request_json(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        if path == "/v1/catalog":
            return catalog()
        if path in ("/v1/pool", "/v1/search"):
            return self.pages.pop(0)
        if path == "/v1/uploads":
            return self.grant
        if path.startswith("/v1/uploads/"):
            return {"v": 1, "disposition": "published", "recording_id": "1" * 64}
        if path.startswith("/v1/recordings/"):
            return {"v": 1, **recording(path.split("/")[-1])}
        return {"v": 1}
    async def request_bytes(self, method, path, **kwargs):
        self.calls.append((method, path, kwargs))
        return codec.encode(native())


def test_canonical_inclusive_bins_ties_and_unrelated_local_steps():
    generation, entries = checked_catalog(catalog())
    cutoffs = next(iter(entries.values()))
    assert frame_band(cutoffs, [(500, 500)]) == (1, 1, 1)
    assert frame_band(cutoffs, [(501, 501)]) == (0, 0, 0)
    assert frame_band(cutoffs, [(None, 200)]) == (2, 2, 2)
    assert frame_band(((0, 500), (1, 500), (2, 200)), [(500, 500)]) == (1, 1, 1)
    with pytest.raises(CommunityError):
        frame_band(cutoffs, [(True, 200)])


def test_remote_provenance_strict_metadata_does_not_claim_certification():
    row = recording()
    assert checked_metadata(row)["provenance"] == "community-unverified"
    for bad in ({**row, "certified": True}, {**row, "game_version": None}, {**row, "full_start": False}, {**row, "frames": True}):
        with pytest.raises(CommunityError):
            checked_metadata(bad)


def test_cache_only_metadata_unready_visibility_ready_tamper_and_owner_change(tmp_path):
    local, account = Local(), Account()
    population = CachedPopulation(local, root=tmp_path, account=account)
    row = recording()
    population.remember_page(IDENTITY, checked_page(page([row])))
    rows = population.metadata_snapshot("star", "1-0", clock="igt", game_version="us", strategy="Standard")
    assert rows[0]["id"] == "local" and rows[1]["id"] == row["id"]
    assert "certified" not in rows[1]
    assert not population.is_ready(row["id"])
    assert population.coverage(IDENTITY)["complete"]  # Membership is known before selected bytes download.
    with pytest.raises(LookupError):
        population.resolve(rows[1]["ghost"], {"kind": "star", "entity_key": "1-0", "strat": "Standard"})
    population.store_asset(row, codec.encode(native()), native())
    assert population.is_ready(row["id"]) and population.coverage(IDENTITY)["complete"]
    path, manifest = population.resolve(rows[1]["ghost"], {"kind": "star", "entity_key": "1-0", "strat": "Standard"})
    assert manifest["sha256"] == row["raw_sha256"]
    path.write_bytes(b"tampered")
    assert not population.is_ready(row["id"])
    with pytest.raises(LookupError):
        population.resolve(rows[1]["ghost"], {"kind": "star", "entity_key": "1-0", "strat": "Standard"})
    assert population.accept_verified("attempt", player_id="local") == (("attempt",), {"player_id": "local"})
    assert population.owned_player_ids("local") == {"local", UID}
    account.state["uid"] = "22222222-2222-4222-8222-222222222222"
    assert len(population.metadata_snapshot("star", "1-0", clock="igt", game_version="us")) == 1
    assert not account.calls
    population.close()
    population.close()
    assert local.closed


def test_cache_two_forms_caps_evict_together(tmp_path, monkeypatch):
    import sm64_events.community.cache as module
    row = recording()
    monkeypatch.setattr(module, "CACHE_RAW", row["raw_bytes"] + 1)
    population = CachedPopulation(Local(), root=tmp_path, account=Account())
    population.store_asset(row, codec.encode(native()), native())
    next_row = recording("2" * 64)
    population.store_asset(next_row, codec.encode(native()), native())
    assert not (tmp_path / (row["id"] + ".ghost")).exists()
    assert not (tmp_path / (row["id"] + ".ghc1")).exists()
    assert population.is_ready(next_row["id"])


def test_pool_canonical_key_capped_partial_and_explicit_assets(tmp_path):
    async def scenario():
        account = Account()
        account.pages = [page([recording(str(index) * 64)], complete=False, cursor="next" + str(index)) for index in range(1, 5)]
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        assert sync.request_pool(IDENTITY, frame_windows=[(1, 500)])
        assert not account.calls  # caller does no IO
        await sync.start()
        for _ in range(100):
            if len([call for call in account.calls if call[1] == "/v1/pool"]) == 4:
                break
            await asyncio.sleep(0.01)
        assert len([call for call in account.calls if call[1] == "/v1/pool"]) == 4
        assert not population.coverage(IDENTITY)["complete"]
        assert not any("/asset" in call[1] for call in account.calls)
        query = next(call[2]["params"] for call in account.calls if call[1] == "/v1/pool")
        assert query["min_step"] == 1 and query["max_step"] == 2 and "frames" not in query
        sync.request_assets(["1" * 64])
        for _ in range(100):
            if population.is_ready("1" * 64):
                break
            await asyncio.sleep(0.01)
        assert population.is_ready("1" * 64)
        await sync.stop()
        assert sync._task is None and not sync._local_jobs
    asyncio.run(scenario())


def test_upload_offer_faster_coalescing_refuses_unknown_and_expired_grant(tmp_path):
    async def scenario():
        raw = native()
        source = tmp_path / "native.ghost"
        source.write_bytes(raw)
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path / "cache", account=account)
        sync = CommunitySync(account, population)
        offer = {"target": TARGET, "clock": "igt", "game_version": "us", "strategy": "Standard", "frames": 300,
            "path": source, "raw_sha256": hashlib.sha256(raw).hexdigest(), "full_start": True, "valid": True}
        assert not sync.offer_source({**offer, "game_version": None})
        assert sync.offer_source(offer)
        assert not account.calls
        await sync.start()
        for _ in range(100):
            if any(call[1] == "/v1/uploads/grant-1" for call in account.calls):
                break
            await asyncio.sleep(0.01)
        put = next(call for call in account.calls if call[1] == "/v1/uploads/grant-1")
        assert codec.decode(put[2]["content"]) == raw
        await sync.stop()
        expired = CommunitySync(account, population)
        account.calls.clear()
        account.grant["expires_at"] = 0
        await expired._offer({**offer, "path": str(source), "owner": UID})
    with pytest.raises(CommunityError) as found:
        asyncio.run(scenario())
    assert found.value.code == "grant_expired"


def test_queue_bounded_and_stop_cancels_http_and_joins_processing(tmp_path):
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        for index in range(100):
            sync._enqueue(index, ("assets", ()))
        assert len(sync._queue) == 32
        entered = asyncio.Event()
        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        account.request_json = blocked
        sync.request_pool(IDENTITY, frame_windows=[(1, 500)])
        await sync.start()
        await entered.wait()
        await asyncio.wait_for(sync.stop(), 1)
        assert sync._task is None and not sync._queue
    asyncio.run(scenario())


def test_stop_joins_owned_cpu_helper_before_provider_teardown(tmp_path):
    import threading
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        entered, release, finished = asyncio.Event(), threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        def helper():
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(2)
            finished.set()
            return b"fixture"
        task = asyncio.create_task(sync._cpu(helper))
        await asyncio.wait_for(entered.wait(), 2)
        task.cancel()
        release.set()
        await sync.stop()
        assert finished.is_set() and not sync._local_jobs
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())


def test_search_pagination_identity_and_typed_cursor(tmp_path):
    async def scenario():
        account = Account()
        account.pages = [page([recording()], complete=False, cursor="opaque-next")]
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        result = await sync.search(IDENTITY, "RUN")
        assert result["records"][0]["player_id"] == UID and result["next_cursor"] == "opaque-next"
        assert not population.is_ready("1" * 64)
        before = len(account.calls)
        assert population.search(IDENTITY, "RUN")["next_cursor"] == "opaque-next"
        assert len(account.calls) == before
        with pytest.raises(CommunityError):
            await sync.search(IDENTITY, "RUN", cursor=["local", 1, "1" * 64])
        wrong = recording()
        wrong["clock"] = "rta"
        account.pages = [page([wrong])]
        with pytest.raises(CommunityError):
            await sync.search(IDENTITY, "RUN")
    asyncio.run(scenario())


@pytest.mark.parametrize("remote_count", [0, 3, 20])
def test_authenticated_terminal_remote_search_retains_every_local_seed_page(tmp_path, remote_count):
    class LocalSeeds(Local):
        def search(self, _identity, _prefix, *, cursor=None, limit=20):
            offset = cursor[1] if cursor else 0
            records = [{"id": f"seed-{number}"} for number in range(offset, min(27, offset + limit))]
            return {"records": records, "next_cursor": ["griffman1212", offset + limit, "1" * 64]
                    if offset + limit < 27 else None}

    account = Account()
    population = CachedPopulation(LocalSeeds(), root=tmp_path, account=account)
    remote = [recording(f"{number:064x}") for number in range(1, remote_count + 1)]
    population.remember_page(IDENTITY, checked_page(page(remote)), prefix="griffman1212")
    answer = population.search(IDENTITY, "griffman1212")
    seen = [row["id"] for row in answer["records"]]
    while answer["next_cursor"] is not None:
        answer = population.search(IDENTITY, "griffman1212", cursor=answer["next_cursor"])
        seen.extend(row["id"] for row in answer["records"])
    assert seen == [row["id"] for row in remote] + [f"seed-{number}" for number in range(27)]
    assert not account.calls


def test_retries_are_finite_and_bad_assets_never_become_ready(tmp_path):
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        attempts = []
        async def refused(*args, **kwargs):
            attempts.append(1)
            raise CommunityError("network_unavailable")
        original = account.request_json
        account.request_json = refused
        with pytest.raises(CommunityError):
            await sync._json("GET", "/v1/catalog")
        assert len(attempts) == 3
        attempts.clear()
        with pytest.raises(CommunityError):
            await sync._json("POST", "/v1/uploads", json={})
        assert not attempts  # The exhausted connection's cooldown covers every route.
        sync._network_until = 0
        with pytest.raises(CommunityError):
            await sync._json("POST", "/v1/uploads", json={})
        assert len(attempts) == 3
        account.request_json = original
        sync._network_until = 0
        async def corrupt(*args, **kwargs):
            return b"malformed"
        account.request_bytes = corrupt
        with pytest.raises(CommunityError):
            await sync._asset("1" * 64)
        assert not population.is_ready("1" * 64)
        assert not list(tmp_path.glob("*.ghost"))
    asyncio.run(scenario())


def test_mutation_retry_reuses_key_but_new_operation_gets_new_key(tmp_path):
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        calls = []
        async def intermittent(method, path, **kwargs):
            calls.append(kwargs["headers"]["Idempotency-Key"])
            if len(calls) in (1, 2):
                raise CommunityError("network_unavailable")
            return {"v": 1}
        account.request_json = intermittent
        await sync._json("POST", "/v1/uploads", json={"fixture": True})
        assert len(calls) == 3 and len(set(calls)) == 1
        await sync._json("POST", "/v1/uploads", json={"fixture": True})
        assert calls[-1] != calls[0]
    asyncio.run(scenario())


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 2])
def test_catalog_and_pages_refuse_noninteger_protocol_version(version):
    with pytest.raises(CommunityError):
        checked_catalog({**catalog(), "v": version})
    with pytest.raises(CommunityError):
        checked_page({**page(), "v": version})


def test_actual_export_catalog_fits_route_bound_and_generation_digest():
    import json
    from pathlib import Path
    import httpx
    from sm64_events.community import CommunityAccount
    from sm64_events.ghost.sharing_targets import canonical
    from test_community_account import config, Store
    path = Path(__file__).resolve().parents[1] / 'cloud/community/catalog.json'
    data = Path(path).read_bytes()
    value = json.loads(data)
    assert 0 < len(data) <= 4 << 20
    assert hashlib.sha256(canonical(value["entries"])).hexdigest() == value["generation"]
    generation, entries = checked_catalog(value)
    assert generation == value["generation"] and entries
    async def scenario():
        bounded_body = data + b' ' * max(0, (1 << 20) + 1 - len(data))
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:
                httpx.Response(200, content=bounded_body, headers={"Content-Type": "application/json"}))) as http:
            account = CommunityAccount(config(), http=http, token_store=Store())
            account._access, account._expires = "fixture", 10**20
            parsed = await account.request_json("GET", "/v1/catalog")
            assert parsed["generation"] == generation
            with pytest.raises(CommunityError) as found:
                await account.request_json("GET", "/v1/account/export")
            assert found.value.code == "response_too_large"
    asyncio.run(scenario())


@pytest.mark.parametrize("next_owner", [UID, "22222222-2222-4222-8222-222222222222"])
def test_pool_refreshes_after_sign_out_and_account_return(tmp_path, next_owner):
    async def scenario():
        account = Account()
        account.pages = [page([recording()]), page([recording()])]
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        try:
            await sync._pool(IDENTITY, [(1, 500)], ())
            assert population.coverage(IDENTITY)["complete"]
            account.state.update(authenticated=False, status="signed_out", uid=None)
            assert not population.coverage(IDENTITY)["complete"]
            account.state.update(authenticated=True, status="active", uid=next_owner)
            await sync._pool(IDENTITY, [(1, 500)], ())
            assert len([call for call in account.calls if call[1] == "/v1/pool"]) == 2
            assert population.coverage(IDENTITY)["complete"]
        finally:
            population.close()
    asyncio.run(scenario())


def test_unavailable_asset_does_not_requery_ready_rivals_in_a_loop(tmp_path):
    from sm64_events.ghost.community_rivals import CommunityRivals
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        rows = [recording(f"{index:064x}") for index in range(1, 5)]
        for row in rows[:3]:
            population.store_asset(row, codec.encode(native()), native())
        population.remember_page(IDENTITY, checked_page(page(rows)))
        original = account.request_json
        async def respond(method, path, **kwargs):
            if path == "/v1/recordings/" + rows[-1]["id"]:
                account.calls.append((method, path, kwargs))
                raise CommunityError("not_found", status=404)
            return await original(method, path, **kwargs)
        account.request_json = respond
        staging = CommunityRivals(sync, population)
        desired = tuple({**row, "slot": index} for index, row in enumerate(rows))
        await sync.start()
        try:
            for _ in range(6):
                staging.stage(desired, ())
                await asyncio.sleep(0)
            metadata_reads = [call[1] for call in account.calls if call[1].startswith("/v1/recordings/")]
            assert metadata_reads == ["/v1/recordings/" + rows[-1]["id"]]
            assert sync.last_error and not population.is_ready(rows[-1]["id"])
            assert all(population.is_ready(row["id"]) for row in rows[:3])
        finally:
            await sync.stop()
            population.close()
    asyncio.run(scenario())


def test_active_manual_pin_outside_pool_renews_before_metadata_expiry(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sm64_events.community import cache as cache_module, sync as sync_module
    now = [time.monotonic()]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(cache_module, "time", clock)
    monkeypatch.setattr(sync_module, "time", clock)

    async def scenario():
        account = Account()
        empty = page()
        empty["coverage"].update(min_step=1, base_step=1)
        account.pages = [empty, empty, empty, empty]
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        pin = recording("f" * 64, frames=700)
        pin["step"] = 0  # This chosen rival is outside the automatic 1–2 band.
        original = account.request_json

        async def respond(method, path, **kwargs):
            if path == "/v1/recordings/" + pin["id"]:
                account.calls.append((method, path, kwargs))
                return {"v": 1, **pin}
            return await original(method, path, **kwargs)

        account.request_json = respond
        population.store_asset(pin, codec.encode(native()), native())
        try:
            start = now[0]
            for elapsed in (0, 120, 240):
                now[0] = start + elapsed
                await sync._pool(IDENTITY, [(1, 500)], (pin["id"],))
                await sync._pool(IDENTITY, [(1, 500)], (pin["id"],))
            assert len([call for call in account.calls if call[1] == "/v1/recordings/" + pin["id"]]) == 1
            now[0] = start + 600
            await sync._pool(IDENTITY, [(1, 500)], (pin["id"],))
            now[0] = start + 720
            row = population.get(pin["id"])
            path, _ = population.resolve(row["ghost"], TARGET)
            assert path.is_file()
            assert len([call for call in account.calls if call[1] == "/v1/pool"]) == 4
            assert len([call for call in account.calls if call[1] == "/v1/recordings/" + pin["id"]]) == 2
            assert not any(call[1].endswith("/asset") for call in account.calls)
        finally:
            population.close()
    asyncio.run(scenario())


def test_rate_limit_has_one_attempt_and_owner_scoped_cooldown(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sm64_events.community import sync as sync_module
    now = [1000]
    monkeypatch.setattr(sync_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    async def scenario():
        account = Account()
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path, account=account))
        async def limited(method, path, **kwargs):
            account.calls.append((method, path, kwargs))
            raise CommunityError("rate_limited", status=429, retry_after=60)
        account.request_json = limited
        for _ in range(8):
            with pytest.raises(CommunityError):
                await sync._json("GET", "/v1/pool")
        assert len(account.calls) == 1
        now[0] += 60
        with pytest.raises(CommunityError):
            await sync._json("GET", "/v1/pool")
        assert len(account.calls) == 2
        account.state["uid"] = "22222222-2222-4222-8222-222222222222"
        with pytest.raises(CommunityError):
            await sync._json("GET", "/v1/pool")
        assert len(account.calls) == 3
    asyncio.run(scenario())


def test_failed_publication_retries_without_another_pb_and_reuses_mutation_keys(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sm64_events.community import sync as sync_module
    now = [1000]
    monkeypatch.setattr(sync_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(sync_module.random, "uniform", lambda low, high: low)
    async def scenario():
        raw = native()
        path = tmp_path / "source.ghost"
        path.write_bytes(raw)
        account = Account()
        original = account.request_json
        publish_keys, grant_keys = [], []
        async def respond(method, url, **kwargs):
            if url == "/v1/uploads":
                grant_keys.append(kwargs["headers"]["Idempotency-Key"])
            if url == "/v1/uploads/grant-1":
                publish_keys.append(kwargs["headers"]["Idempotency-Key"])
                if len(publish_keys) == 1:
                    raise CommunityError("request_failed", status=503, retry_after=30)
            return await original(method, url, **kwargs)
        account.request_json = respond
        population = CachedPopulation(Local(), root=tmp_path / "cache", account=account)
        sync = CommunitySync(account, population)
        assert sync.offer_source({"target": TARGET, "clock": "igt", "game_version": "us", "strategy": "Standard",
            "frames": 3, "path": path, "raw_sha256": hashlib.sha256(raw).hexdigest(), "full_start": True, "valid": True})
        await sync.start()
        try:
            for _ in range(100):
                if sync._offer_retry:
                    break
                await asyncio.sleep(.01)
            assert sync._offer_retry and len(publish_keys) == 1
            now[0] += 31
            sync._contribution_wake.set()
            for _ in range(100):
                if len(publish_keys) == 2:
                    break
                await asyncio.sleep(.01)
            assert len(publish_keys) == len(grant_keys) == 2
            assert len(set(publish_keys)) == len(set(grant_keys)) == 1
            assert not sync._offer_retry and sync.last_error is None
        finally:
            await sync.stop()
    asyncio.run(scenario())


def test_hard_publication_lease_expires_offline_even_when_asset_bytes_remain(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sm64_events.community import cache as cache_module
    now = [1000]
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    account = Account()
    population = CachedPopulation(Local(), root=tmp_path, account=account)
    row = recording()
    population.store_asset(row, codec.encode(native()), native())
    now[0] += 599
    assert population.is_ready(row["id"])
    now[0] += 1
    assert not population.is_ready(row["id"])
    assert (tmp_path / (row["id"] + ".ghost")).is_file()
    assert population.metadata_snapshot("star", "1-0", clock="igt", game_version="us") == ({"id": "local"},)


def test_same_clip_reclassified_during_retry_keeps_distinct_admission_identity(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sm64_events.community import sync as sync_module
    now = [1000]
    monkeypatch.setattr(sync_module, "time", SimpleNamespace(monotonic=lambda: now[0]))

    async def scenario():
        raw = native()
        path = tmp_path / "source.ghost"
        path.write_bytes(raw)
        source = {"target": TARGET, "clock": "igt", "game_version": "us", "strategy": "Standard",
            "frames": 3, "path": path, "raw_sha256": hashlib.sha256(raw).hexdigest(), "full_start": True, "valid": True}
        account = Account()
        original = account.request_json
        admissions = {}
        fail_first = [True]

        async def respond(method, url, **kwargs):
            if url == "/v1/catalog":
                result = catalog()
                result["entries"][0]["strategies"].append({"name": "Alternate", "cutoffs":
                    result["entries"][0]["strategies"][0]["cutoffs"]})
                return result
            if url == "/v1/uploads":
                key = kwargs["headers"]["Idempotency-Key"]
                body = sync_module.canonical(kwargs["json"])
                if key in admissions and admissions[key] != body:
                    raise CommunityError("idempotency_conflict", status=409)
                admissions[key] = body
                if fail_first.pop() if fail_first else False:
                    raise CommunityError("request_failed", status=503, retry_after=1)
            return await original(method, url, **kwargs)

        account.request_json = respond
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path / "cache", account=account))
        assert sync.offer_source(source)
        key, operation = sync._queue.popitem()
        with pytest.raises(CommunityError):
            await sync._offer(operation[1])
        sync._offer_retry[key] = operation, now[0] + 1, 1
        assert sync.offer_source({**source, "target": {**TARGET, "strat": "Alternate"}, "strategy": "Alternate"})
        assert key in sync._offer_retry
        new_key, new_operation = sync._queue.popitem()
        assert new_key != key and "mutation_keys" not in new_operation[1]
        now[0] += 2
        await sync._offer(new_operation[1])
        await sync._offer(operation[1])
        assert len(admissions) == 2
        assert operation[1]["mutation_keys"]["grant"] != new_operation[1]["mutation_keys"]["grant"]
    asyncio.run(scenario())


def cadence_transport(calls, encoded):
    import httpx
    from test_community_account import profile
    def respond(request):
        path = request.url.path
        calls.append((request.method, path))
        if path == "/v1/config":
            body = {"v": 1}
        elif path == "/v1/account":
            body = profile(sharing=True)
        elif path == "/v1/catalog":
            body = catalog()
        elif path == "/v1/pool":
            body = page()
        elif path == "/v1/uploads":
            body = {"v": 1, "disposition": "upload", "upload_id": "grant-1", "expires_at": int(time.time()) + 60}
        elif path.startswith("/v1/uploads/"):
            body = {"v": 1, "disposition": "published"}
        elif path.endswith("/asset"):
            return httpx.Response(200, content=encoded)
        elif path.startswith("/v1/recordings/"):
            body = {"v": 1, **recording(path.split("/")[-1])}
        else:
            assert path == "/v1/pins"
            body = {"v": 1}
        return httpx.Response(200, json=body)
    return httpx.MockTransport(respond)


def test_another_account_pending_faster_run_cannot_discard_current_offer(tmp_path):
    async def scenario():
        account = Account()
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path / "cache", account=account))
        source = {"target": TARGET, "clock": "igt", "game_version": "us", "strategy": "Standard",
            "frames": 3, "path": tmp_path / "old.ghost", "raw_sha256": hashlib.sha256(native()).hexdigest(),
            "full_start": True, "valid": True}
        source["path"].write_bytes(native())
        assert sync.offer_source(source)
        old_key, old_operation = sync._queue.popitem()
        sync._offer_retry[old_key] = old_operation, time.monotonic() + 30, 1
        account.state["uid"] = "22222222-2222-4222-8222-222222222222"
        path = tmp_path / "current.ghost"
        path.write_bytes(native(4))
        assert sync.offer_source({**source, "frames": 4, "path": path, "raw_sha256": hashlib.sha256(native(4)).hexdigest()})
        _, operation = sync._queue.popitem()
        await sync._offer(operation[1])
        assert [url for _method, url, _kwargs in account.calls][-2:] == ["/v1/uploads", "/v1/uploads/grant-1"]
    asyncio.run(scenario())


def test_eight_hours_with_four_chosen_rivals_and_twenty_uploads_fits_account_budget(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sm64_events.community import cache as cache_module, sync as sync_module
    now = [1000]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(cache_module, "time", clock)
    monkeypatch.setattr(sync_module, "time", clock)
    async def scenario():
        import httpx
        from sm64_events.community import CommunityAccount
        from test_community_account import config, profile, Store
        calls = []
        raw = native()
        http = httpx.AsyncClient(transport=cadence_transport(calls, codec.encode(raw)))
        account = CommunityAccount(config(), http=http, token_store=Store())
        account._access, account._expires = "fixture", 10**20
        account._profile = {key: value for key, value in profile(sharing=True).items() if key != "v"}
        population = CachedPopulation(Local(), root=tmp_path / "cache", account=account)
        sync = CommunitySync(account, population)
        pins = tuple(f"{index:064x}" for index in range(1, 5))
        source = tmp_path / "source.ghost"
        source.write_bytes(raw)
        for elapsed in range(0, 8 * 3600, 120):
            now[0] = 1000 + elapsed
            await sync._pool(IDENTITY, [(1, 1000)], pins)
            if elapsed % 3600 == 0:
                await account.refresh()  # Eight deliberate account-panel opens.
            # The same IO-free request cadence used while the user plays must
            # not multiply actual network calls while its coverage is fresh.
            await sync._pool(IDENTITY, [(1, 1000)], pins)
            if elapsed < 20 * 120:
                await sync._offer({"target": TARGET, "clock": "igt", "game_version": "us", "strategy": "Standard",
                    "frames": 3, "path": str(source), "raw_sha256": hashlib.sha256(raw).hexdigest(), "owner": UID})
        assert len([call for call in calls if call[1] == "/v1/pool"]) == 240
        assert len([call for call in calls if call[1] == "/v1/catalog"]) == 8
        assert len([call for call in calls if call[1] == "/v1/pins"]) == 1
        assert len([call for call in calls if call[1].startswith("/v1/recordings/") and not call[1].endswith("/asset")]) == 192
        assert len([call for call in calls if call[1].endswith("/asset")]) == 4
        assert len([call for call in calls if call[1].startswith("/v1/uploads")]) == 40
        assert len([call for call in calls if call[1] == "/v1/account"]) == 8
        # The public config route precedes authentication/ticketing in the
        # service. OAuth provider calls never enter this Worker counter.
        assert len([call for call in calls if call[1] != "/v1/config"]) == 493 < 500
        await http.aclose()
    asyncio.run(scenario())


def test_new_catalog_generation_invalidates_hour_lease_before_retrying_pool(tmp_path):
    async def scenario():
        account = Account()
        newer = page()
        newer["coverage"]["generation"] = "g2"
        account.pages = [newer, newer]
        sync = CommunitySync(account, CachedPopulation(Local(), root=tmp_path, account=account))
        with pytest.raises(CommunityError) as found:
            await sync._pool(IDENTITY, [(1, 1000)], ())
        assert found.value.code == "generation_changed" and sync._catalog_until == 0
        original = account.request_json
        async def refreshed(method, path, **kwargs):
            if path == "/v1/catalog":
                account.calls.append((method, path, kwargs))
                return {**catalog(), "generation": "g2"}
            return await original(method, path, **kwargs)
        account.request_json = refreshed
        await sync._pool(IDENTITY, [(1, 1000)], ())
        assert sync._catalog_generation == "g2"
        assert len([call for call in account.calls if call[1] == "/v1/catalog"]) == 2
    asyncio.run(scenario())


def test_fresh_metadata_can_reuse_validated_bytes_but_not_another_hash(tmp_path):
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        row = recording()
        population.store_asset(row, codec.encode(native()), native())
        population.renew_metadata({**row, "raw_sha256": "f" * 64})
        assert not population.is_ready(row["id"])
        sync = CommunitySync(account, population)
        await sync._asset(row["id"])
        assert population.is_ready(row["id"])
        assert not any(call[1].endswith("/asset") for call in account.calls)
    asyncio.run(scenario())


def test_pinned_asset_reloads_once_after_memory_only_cache_restart(tmp_path):
    async def scenario():
        account = Account()
        identifier = "f" * 64
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        population.store_asset(recording(identifier), codec.encode(native()), native())
        population.close()
        restored = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, restored)
        empty = page()
        empty["coverage"].update(min_step=1, base_step=1)
        account.pages = [empty]
        try:
            assert not restored.is_ready(identifier)
            await sync._pool(IDENTITY, [(1, 500)], (identifier,))
            assert restored.is_ready(identifier)
            row = restored.get(identifier)
            path, _ = restored.resolve(row["ghost"], TARGET)
            assert path.is_file()
            await sync._pool(IDENTITY, [(1, 500)], (identifier,))
            assert len([call for call in account.calls if call[1] == "/v1/pool"]) == 1
            assert len([call for call in account.calls if call[1].endswith("/asset")]) == 1
        finally:
            restored.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["target", "clock", "rom", "strategy", "owner", "closing"])
def test_pin_metadata_refresh_refuses_mismatched_identity_and_late_publication(tmp_path, change):
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        identifier = "f" * 64
        row = recording(identifier)
        if change == "target":
            row["target"]["entity_key"] = "2-1"
        elif change == "clock":
            row["clock"] = "rta"
        elif change == "rom":
            row["game_version"] = "jp"
        elif change == "strategy":
            row["strategy"] = row["target"]["strat"] = "Other"
        original = account.request_json

        async def respond(method, path, **kwargs):
            if path == "/v1/recordings/" + identifier:
                account.calls.append((method, path, kwargs))
                if change == "owner":
                    account.state["uid"] = "22222222-2222-4222-8222-222222222222"
                elif change == "closing":
                    sync._closing = True
                return {"v": 1, **row}
            return await original(method, path, **kwargs)

        account.request_json = respond
        empty = page()
        empty["coverage"].update(min_step=1, base_step=1)
        account.pages = [empty]
        try:
            if change in ("owner", "closing"):
                await sync._pool(IDENTITY, [(1, 500)], (identifier,))
            else:
                with pytest.raises(CommunityError) as found:
                    await sync._pool(IDENTITY, [(1, 500)], (identifier,))
                assert found.value.code == "asset_unavailable"
            assert not population.is_ready(identifier)
            assert not any(call[1].endswith("/asset") for call in account.calls)
            assert not list(tmp_path.glob("*.ghost"))
        finally:
            population.close()
    asyncio.run(scenario())


def test_existing_pool_remains_throttled_while_its_coverage_is_current(tmp_path):
    async def scenario():
        account = Account()
        response = page([recording()])
        response["coverage"].update(min_step=1, max_step=2, base_step=1)
        account.pages = [response]
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        try:
            for _ in range(6):
                await sync._pool(IDENTITY, [(1, 500)], ())
            assert len([call for call in account.calls if call[1] == "/v1/pool"]) == 1
            assert population.coverage(IDENTITY)["complete"]
        finally:
            population.close()
    asyncio.run(scenario())


def test_failed_asset_does_not_block_another_and_recovers_after_cooldown(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from sm64_events.community import sync as sync_module
    now = [time.monotonic()]
    monkeypatch.setattr(sync_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    async def scenario():
        account = Account()
        population = CachedPopulation(Local(), root=tmp_path, account=account)
        sync = CommunitySync(account, population)
        first, second = "1" * 64, "2" * 64
        original = account.request_json
        unavailable = [True]
        async def respond(method, path, **kwargs):
            if path == "/v1/recordings/" + first and unavailable[0]:
                account.calls.append((method, path, kwargs))
                raise CommunityError("not_found", status=404)
            return await original(method, path, **kwargs)
        account.request_json = respond
        try:
            with pytest.raises(CommunityError):
                await sync._assets((first, second))
            assert population.is_ready(second) and not population.is_ready(first)
            before = len(account.calls)
            assert sync.request_assets((first, second)) and not sync._queue
            await sync._assets((first, second))
            assert len(account.calls) == before
            now[0] += sync_module.ASSET_RETRY_S
            unavailable[0] = False
            await sync._assets((first, second))
            assert all(population.is_ready(identifier) for identifier in (first, second))
            assert not sync._asset_retry
        finally:
            await sync.stop()
            population.close()
        assert not sync.request_assets((first, second))
    asyncio.run(scenario())


def test_a_shared_recording_with_an_unknown_input_never_enters_the_cache(tmp_path):
    """His rule (round 40): whatever the service holds, a "?" recording is not a ghost here."""
    from ghost_native_fixture import frame, producer
    from sm64_events.ghost.clip import encode_clip
    from test_community_codec import frame_from_bytes
    raw = encode_clip(tuple(frame_from_bytes(frame(index, pad=None if index == 2 else (0, 0, 0)))
                            for index in range(1, 4)), producer(), clock_kind=1,
                      clip_id="22222222-2222-4222-8222-222222222222")
    encoded = codec.encode(raw)
    row = {**recording(), "raw_sha256": hashlib.sha256(raw).hexdigest(), "raw_bytes": len(raw),
           "encoded_sha256": hashlib.sha256(encoded).hexdigest(), "encoded_bytes": len(encoded)}
    population = CachedPopulation(Local(), root=tmp_path, account=Account())
    with pytest.raises(CommunityError) as refused:
        population.store_asset(row, encoded, raw)
    assert refused.value.code == "incomplete_inputs" and "not known on every frame" in str(refused.value)
    assert not population.is_ready(row["id"]) and not list(tmp_path.glob("*.ghost"))
