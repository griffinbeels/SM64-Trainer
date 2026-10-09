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
    assert not population.coverage(IDENTITY)["complete"]
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
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def helper():
            entered.set()
            assert release.wait(2)
            finished.set()
            return b"fixture"
        task = asyncio.create_task(sync._cpu(helper))
        while not entered.is_set():
            await asyncio.sleep(0.001)
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
        assert len(attempts) == 1
        account.request_json = original
        async def corrupt(*args, **kwargs):
            return b"malformed"
        account.request_bytes = corrupt
        with pytest.raises(CommunityError):
            await sync._asset("1" * 64)
        assert not population.is_ready("1" * 64)
        assert not list(tmp_path.glob("*.ghost"))
    asyncio.run(scenario())
