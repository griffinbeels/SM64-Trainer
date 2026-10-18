"""Community integration boundaries using fictional data and injected IO only."""
import asyncio
from contextlib import suppress
from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from sm64_events.community import CachedPopulation, CommunityError, CommunitySync
from sm64_events.community.sync import checked_page
from sm64_events.ghost.adaptive import checked_options, rank_frames, select_rivals
from sm64_events.ghost.community_rivals import CommunityRivals
from sm64_events.ghost.session import GhostSession
from test_community_sync import Account, IDENTITY as CACHE_IDENTITY, Local, page, recording as remote_recording
from test_ghost_adaptive import IDENTITY, Standards, basis, ranked, recording
from test_ghost_rivals import CONTROLS, TARGET, controller


class Cloud:
    """No HTTP: record the synchronous offers and simulate an async outage."""
    def __init__(self, status="active"):
        self.account = SimpleNamespace(snapshot=lambda: {"status": status})
        self.pools, self.assets, self.searches = [], [], []
        self.last_error = None

    def request_pool(self, identity, **options):
        self.pools.append((identity, options))

    def request_assets(self, identifiers):
        self.assets.append(tuple(identifiers))

    def pool_status(self, _identity):
        return None

    def refused_assets(self):
        return frozenset()

    async def search(self, identity, prefix, **options):
        self.searches.append((identity, prefix, options))
        raise CommunityError("network_unavailable")


def test_pending_download_obeys_a_reduced_visible_slot_count():
    previous = tuple({"id": f"{index + 1:064x}", "slot": index, "ghost": object(),
        "provenance": "community-unverified"} for index in range(4))
    desired = ({"id": "f" * 64, "slot": 0, "ghost": object(), "provenance": "community-unverified"},)
    cloud = Cloud()
    population = SimpleNamespace(is_ready=lambda identifier: identifier != "f" * 64, keep=lambda _ids: None)
    staging = CommunityRivals(cloud, population)
    assert [row["pending"] for row in staging.stage(previous)] == [False] * 4
    staged = staging.stage(desired)
    assert [(row["id"], row["slot"], row["pending"]) for row in staged] == [("f" * 64, 0, True)]
    assert cloud.assets == [("f" * 64,)] and staging.downloading


@pytest.mark.parametrize("change", ["rom", "clock"])
def test_identity_changes_revoke_old_native_rows_before_new_downloads(tmp_path, monkeypatch, change):
    rivals, tracker, native, population, _ = controller(tmp_path, monkeypatch)
    cloud = Cloud()
    population.is_ready = lambda _identifier: False
    population.coverage = lambda _identity: {"complete": True}
    rivals.community = CommunityRivals(cloud, population)
    commands = []
    native.reconcile = lambda **command: commands.append(command)
    old_ids = {row["id"] for row in rivals.rows}
    assert old_ids
    if change == "rom":
        rivals.version_of = lambda: "jp"
        changed = replace(IDENTITY, game_version="jp")
    else:
        tracker.ranks.clock = "rta"
        changed = replace(IDENTITY, clock="rta")
    population.rows = [recording("f" * 64, frames=600, clock=changed.clock,
        game_version=changed.game_version, certified=False,
        provenance="community-unverified", structurally_valid=True)]
    try:
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert rivals.identity == changed
        assert any(command["enabled"] is False for command in commands)
        # The new seat shows at once, pending; native playback waits for its bytes.
        assert [(row["id"], row["pending"]) for row in rivals.rows] == [("f" * 64, True)]
        assert native.preparations[-1][1] == []
        assert cloud.assets == [("f" * 64,)]
    finally:
        tracker.db.close()


def test_pool_requests_lease_remote_pins_and_preserve_private_choices():
    remote, local, imported, missing = (f"{index:064x}" for index in range(1, 5))
    rows = {remote: {"provenance": "community-unverified"},
        local: {"certified": True}, imported: {"manual_only": True}}
    def get(identifier):
        if identifier not in rows:
            raise LookupError(identifier)
        return rows[identifier]
    pins = {str(index): identifier for index, identifier in enumerate((remote, local, imported, missing))}
    before = dict(pins)
    cloud = Cloud()
    staging = CommunityRivals(cloud, SimpleNamespace(get=get))
    staging.request(Standards(), IDENTITY, pins)
    assert cloud.pools and cloud.pools[0][1]["pinned_ids"] == (remote,)
    assert pins == before


@pytest.mark.parametrize("enabled", [True, False])
def test_chosen_remote_pin_is_requested_after_cache_and_controller_restart(tmp_path, monkeypatch, enabled):
    from sm64_events.ghost.rivals import Rivals
    rivals, tracker, native, local, _ = controller(tmp_path, monkeypatch)
    local.close = lambda: None
    account = Account()
    cloud = Cloud()
    cloud.account = account
    root = tmp_path / "cache"
    population = CachedPopulation(local, root=root, account=account)
    metadata = remote_recording("f" * 64, frames=1800)
    metadata["target"] = dict(TARGET)
    population.renew_metadata(metadata)
    rivals.population = population
    rivals.community = CommunityRivals(cloud, population)
    try:
        rivals.apply(rivals.checked({"action": "add", "id": metadata["id"]}, TARGET))
        rivals.refresh(TARGET, CONTROLS, enabled=enabled, force=True)
        if enabled:
            assert cloud.pools[-1][1]["pinned_ids"] == (metadata["id"],)
        else:
            assert not cloud.pools
        population.close()
        restored_population = CachedPopulation(local, root=root, account=account)
        with pytest.raises(LookupError):
            restored_population.get(metadata["id"])
        restored = Rivals(tracker, restored_population, native, lambda: "us", community=cloud)
        restored.refresh(TARGET, CONTROLS, enabled=True)
        assert restored.pins[restored._key(TARGET)] == rivals.pins[rivals._key(TARGET)]
        assert cloud.pools[-1][1]["pinned_ids"] == (metadata["id"],)
        assert not restored_population.is_ready(metadata["id"])
    finally:
        population.close()
        if 'restored_population' in locals():
            restored_population.close()
        tracker.db.close()


@pytest.mark.parametrize("change", ["legacy", "owner", "signed_out", "private_import", "local", "invalid"])
def test_persisted_remote_provenance_never_promotes_private_or_other_account_choices(tmp_path, monkeypatch, change):
    from sm64_events.ghost.rivals import Rivals, STATE_KEY
    rivals, tracker, native, population, _ = controller(tmp_path, monkeypatch)
    account = Account()
    population.coverage = lambda _: {"complete": True}
    identifier = "f" * 64 if change != "invalid" else "g" * 64
    cloud = Cloud()
    cloud.account = account
    state = {"version": 4, "options": rivals.options, "pins": {rivals._key(TARGET): {"0": identifier}},
             "remote_choices": {identifier: account.state["uid"]}}
    sharing = None
    if change == "legacy":
        state["version"] = 3
    elif change == "owner":
        account.state["uid"] = "22222222-2222-4222-8222-222222222222"
    elif change == "signed_out":
        account.state.update(status="signed_out", authenticated=False)
    elif change == "local":
        population.rows.append(recording(identifier, frames=1800))
    elif change == "private_import":
        ghost = SimpleNamespace(id=identifier, kind=TARGET["kind"], entity_key=TARGET["entity_key"],
            strat_tag=TARGET["strat"], author="Private Runner", frames=1800, origin="import:" + identifier)
        sharing = SimpleNamespace(generation=0, get=lambda _: ghost,
            portable_entry=lambda _: {"clock": "igt", "game_version": "us"})
    try:
        tracker.db.set_state(STATE_KEY, state)
        restored = Rivals(tracker, population, native, lambda: "us", community=cloud)
        restored.refresh(TARGET, CONTROLS, enabled=True, sharing=sharing)
        if change == "signed_out":
            assert not cloud.pools  # Signed out asks the cloud nothing.
        else:
            assert cloud.pools and cloud.pools[-1][1]["pinned_ids"] == ()
        if change in ("legacy", "invalid"):
            assert restored.remote_choices == {}
    finally:
        tracker.db.close()


def test_remote_choice_state_is_versioned_bounded_and_pruned_with_pins(tmp_path, monkeypatch):
    from sm64_events.ghost import rivals as module
    rivals, tracker, native, population, _ = controller(tmp_path, monkeypatch)
    owner = Account().state["uid"]
    identifiers = [f"{index:064x}" for index in range(module.MAX_REMOTE_CHOICES + 5)]
    pins = {f"target:{index}": {"0": identifier} for index, identifier in enumerate(identifiers)}
    tracker.db.set_state(module.STATE_KEY, {"version": 4, "pins": pins,
        "remote_choices": {identifier: owner for identifier in identifiers}})
    try:
        restored = module.Rivals(tracker, population, native, lambda: "us")
        restored._load()
        assert len(restored.remote_choices) == module.MAX_REMOTE_CHOICES
        survivor = identifiers[-1]
        restored.remote_choices[survivor] = owner
        restored._store()
        assert len(restored.remote_choices) == module.MAX_REMOTE_CHOICES
        assert survivor in restored.remote_choices and identifiers[0] not in restored.remote_choices
        restored.pins = {"target:0": {"0": survivor}}
        restored._store()
        state = tracker.db.get_state(module.STATE_KEY, {})
        assert state["version"] == 4 and state["remote_choices"] == {survivor: owner}
    finally:
        tracker.db.close()


@pytest.mark.parametrize("status", ["signed_out", "active", "consent_required", "not_invited"])
def test_local_search_survives_account_gates_and_remote_outages(tmp_path, monkeypatch, status):
    rivals, tracker, _, _, _ = controller(tmp_path, monkeypatch)
    cloud = Cloud(status)
    session = GhostSession(tracker=tracker, inputs=None, replay=None, broadcaster=None, community=cloud)
    session._rivals = rivals
    expected = rivals.search(TARGET, "Runner")
    remote_search = cloud.search
    async def unlocked_search(*args, **kwargs):
        assert not session._lock.locked(), "remote IO entered the native selection lock"
        return await remote_search(*args, **kwargs)
    cloud.search = unlocked_search
    async def scenario():
        result = await session.search_rivals(TARGET, "Runner")
        assert result == expected and result["records"]
        assert len(cloud.searches) == int(status == "active")
    try:
        asyncio.run(scenario())
    finally:
        tracker.db.close()


@pytest.mark.parametrize("change", ["target", "scope"])
def test_stale_search_is_rejected_before_remote_io(tmp_path, monkeypatch, change):
    rivals, tracker, _, _, _ = controller(tmp_path, monkeypatch)
    cloud = Cloud()
    session = GhostSession(tracker=tracker, inputs=None, replay=None, broadcaster=None, community=cloud)
    session._rivals = rivals
    target = {**TARGET, "entity_key": "2-2"} if change == "target" else TARGET
    scope = "overall" if change == "scope" else None
    async def scenario():
        with pytest.raises(ValueError):
            await session.search_rivals(target, "Runner", scope=scope)
        assert not cloud.searches
    try:
        asyncio.run(scenario())
    finally:
        tracker.db.close()


def _shutdown_session(events):
    async def stop():
        events.append("community-stop")
        raise CommunityError("shutdown_timeout")
    async def close():
        events.append("account-close")
    cloud = SimpleNamespace(stop=stop, account=SimpleNamespace(close=close))
    population = SimpleNamespace(close=lambda: events.append("provider-close"))
    native = SimpleNamespace(stop=lambda: events.append("native-stop"))
    return GhostSession(tracker=SimpleNamespace(db=None, ranks=None), inputs=None,
        replay=None, broadcaster=None, population=population, native=native, community=cloud)


def test_community_stop_timeout_still_closes_account_native_and_provider():
    events = []
    session = _shutdown_session(events)
    async def scenario():
        with suppress(CommunityError):
            await session.stop()
    asyncio.run(scenario())
    assert events == ["community-stop", "account-close", "native-stop", "provider-close"]


def test_app_lifespan_continues_all_teardown_after_community_timeout(tmp_path, monkeypatch):
    from sm64_events.server import app as module
    from sm64_events.server import pollsupervisor
    events = []
    session = _shutdown_session(events)
    async def noop(*_args):
        pass
    async def running(label):
        try:
            await asyncio.Event().wait()
        finally:
            events.append(label)
    class Supervisor:
        def __init__(self, _poller):
            pass
        async def run(self):
            await running("poller-joined")
    async def maintain(*_args):
        await running("recovery-joined")
    async def stop_replay(_replay):
        events.append("replay-stop")
    monkeypatch.setattr(module, "_start_app_service", noop)
    monkeypatch.setattr(module, "_start_app_replay", lambda _replay: None)
    monkeypatch.setattr(module, "_stop_replay_bounded", stop_replay)
    monkeypatch.setattr(module, "pidfile_path", lambda: tmp_path / "fictional.pid")
    monkeypatch.setattr(pollsupervisor, "PollSupervisor", Supervisor)
    monkeypatch.setattr(pollsupervisor, "maintain_components", maintain)
    monkeypatch.setattr(session, "start", noop)
    poller = SimpleNamespace(on_stop=lambda: events.append("inputs-flushed"))
    monitor = SimpleNamespace(run=lambda: running("monitor-joined"))
    capture = SimpleNamespace(close=lambda: events.append("capture-close"))
    lifespan = module._create_lifespan(poller, None, object(), monitor, None, False,
        capture_layer=capture, ghost=session)
    async def scenario():
        async with lifespan(FastAPI()):
            await asyncio.sleep(0)  # Let each owned coroutine enter its cancellation boundary.
    asyncio.run(scenario())
    assert set(events) == {"community-stop", "account-close", "native-stop", "provider-close",
        "monitor-joined", "recovery-joined", "capture-close", "replay-stop", "poller-joined", "inputs-flushed"}
    assert events.index("native-stop") < events.index("replay-stop") < events.index("inputs-flushed")


@pytest.mark.parametrize("owner_change", ["same", "new", "signed-out"])
def test_asset_publication_keeps_loop_live_joins_fsync_and_rechecks_owner(tmp_path, monkeypatch, owner_change):
    from sm64_events.community import cache as cache_module
    account, local = Account(), Local()
    population = CachedPopulation(local, root=tmp_path, account=account)
    sync = CommunitySync(account, population)
    identifier = "1" * 64
    entered, release, flushed = asyncio.Event(), threading.Event(), threading.Event()
    event_loop = None
    thread_ids = []
    real_fsync = cache_module.os.fsync
    def delayed_fsync(descriptor):
        thread_ids.append(threading.get_ident())
        event_loop.call_soon_threadsafe(entered.set)
        if not release.wait(2):
            raise TimeoutError("Fictional cache fsync release did not arrive")
        real_fsync(descriptor)
        flushed.set()
    monkeypatch.setattr(cache_module.os, "fsync", delayed_fsync)
    async def scenario():
        nonlocal event_loop
        event_loop = asyncio.get_running_loop()
        loop_thread = threading.get_ident()
        await sync.start()
        sync.request_assets((identifier,))
        stopping = None
        try:
            await asyncio.wait_for(entered.wait(), 1)
            assert thread_ids and all(value != loop_thread for value in thread_ids)
            # These heartbeats happen while the real cache publication is held.
            for _ in range(5):
                await asyncio.sleep(.002)
                assert not flushed.is_set()
            if owner_change == "new":
                account.state["uid"] = "20000000-0000-4000-8000-000000000002"
            elif owner_change == "signed-out":
                account.state.update(authenticated=False, status="signed_out", uid=None)
            stopping = asyncio.create_task(sync.stop())
            await asyncio.sleep(.01)
            assert not stopping.done(), "stop returned while cache publication still owned a live worker"
            release.set()
            await asyncio.wait_for(stopping, 1)
            assert flushed.is_set()
            assert (population.is_ready(identifier), bool(list(tmp_path.glob('*.ghost')))) == (
                owner_change == 'same', owner_change == 'same')
            assert not list(tmp_path.glob("*.part"))
        finally:
            release.set()
            if stopping is not None:
                await asyncio.gather(stopping, return_exceptions=True)
            await sync.stop()
            population.close()
    asyncio.run(scenario())
    assert local.closed


def test_complete_membership_does_not_require_downloading_every_recording(tmp_path):
    account = Account()
    population = CachedPopulation(Local(), root=tmp_path, account=account)
    rows = [remote_recording(f"{index:064x}") for index in range(1, 6)]
    try:
        population.remember_page(CACHE_IDENTITY, checked_page(page(rows)))
        assert all(not population.is_ready(row["id"]) for row in rows)
        assert population.coverage(CACHE_IDENTITY)["complete"] is True
        population.remember_page(CACHE_IDENTITY, checked_page(page(rows, complete=False, cursor="more")))
        assert population.coverage(CACHE_IDENTITY)["complete"] is False
    finally:
        population.close()


def test_incomplete_membership_cannot_prove_a_same_rank_fallback():
    coverage = {"complete": False, "generation": "catalog"}
    cloud = Cloud()
    staging = CommunityRivals(cloud, SimpleNamespace(coverage=lambda _identity: coverage))
    peer = ranked(10, "same-rank", frames=990)
    pb = basis(10, 1000)
    assert select_rivals([peer], pb, {"count": 1}, {}, allow_fastest=staging.complete(IDENTITY)) == ()
    coverage["complete"] = True
    selected = select_rivals([peer], pb, {"count": 1}, {}, allow_fastest=staging.complete(IDENTITY))
    assert [row["id"] for row in selected] == [peer["id"]]
    coverage["complete"] = False
    chosen = select_rivals([peer], pb, {"count": 1}, {"0": peer["id"]},
        allow_fastest=staging.complete(IDENTITY))
    assert chosen[0]["id"] == peer["id"] and chosen[0]["pinned"]


def test_rivals_controller_uses_membership_completion_for_fallback(tmp_path, monkeypatch):
    from sm64_events.ghost import rivals as module
    rivals, tracker, _, population, _ = controller(tmp_path, monkeypatch)
    frames = 600
    progress = rank_frames(tracker.ranks, IDENTITY, frames)
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: {
        **progress, "frames": frames, "current_strategy": "Standard"})
    population.rows = [recording("f" * 64, frames=frames - 1,
        certified=False, provenance="community-unverified", structurally_valid=True)]
    assert rank_frames(tracker.ranks, IDENTITY, frames - 1)["step"] == progress["step"]
    coverage = {"complete": False, "generation": "catalog"}
    population.coverage = lambda _identity: coverage
    population.is_ready = lambda _identifier: True
    rivals.community = CommunityRivals(Cloud(), population)
    rivals.options = checked_options({"count": 1})
    try:
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert rivals.rows == ()
        coverage["complete"] = True
        rivals.refresh(TARGET, CONTROLS, enabled=True, force=True)
        assert [row["id"] for row in rivals.rows] == ["f" * 64]
    finally:
        tracker.db.close()
