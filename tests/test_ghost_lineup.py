"""The practice card's lineup over the real cloud cache and sync, with fake HTTP.

His design (2026-10-16): seats keep their player and colour; a PB change
re-picks locally; a target with no strategy asks nobody for anything; and the
data path makes as few cloud and database requests as it can.
"""
import asyncio
from types import SimpleNamespace

import pytest

from sm64_events.community import CachedPopulation, CommunityError, CommunitySync
from sm64_events.ghost import rivals as module
from sm64_events.ghost.adaptive import PracticeIdentity, rank_frames
from sm64_events.ghost.rivals import Rivals
from sm64_events.storage.db import Database
from test_community_sync import TARGET as CLOUD_TARGET, Account, Local, page, recording
from test_ghost_adaptive import Standards
from test_ghost_rivals import CONTROLS, Native

STANDARD = {"kind": "star", "entity_key": "1-0", "strat": "Standard"}
OTHER = {**STANDARD, "strat": "Other"}
NONE = {**STANDARD, "strat": None}
FRAMES = (900, 870, 840, 810, 780, 750, 720, 690, 660, 630, 600, 570)


def player(number):
    return f"{number:08x}-1111-4111-8111-111111111111"


def cloud_row(number, frames, strategy="Standard", owner=None):
    return {**recording(f"{number:064x}", frames=frames), "player_id": owner or player(number),
            "strategy": strategy, "target": {**CLOUD_TARGET, "strat": strategy}}


class Cloud(Account):
    """Answers each list for the strategy it names; can hold answers back or fail."""

    def __init__(self, rows=()):
        super().__init__()
        self.rows = list(rows)
        self.gate = None
        self.failure = None
        self.complete = True

    def lists(self, strategy=None):
        return [call for call in self.calls if call[1] == "/v1/pool"
                and (strategy is None or call[2]["params"].get("strategy") == strategy)]

    async def request_json(self, method, path, **kwargs):
        if path == "/v1/catalog":
            self.calls.append((method, path, kwargs))
            cutoffs = [{"step": 0, "frames": 1000}, {"step": 1, "frames": 500}, {"step": 2, "frames": 200}]
            return {"v": 1, "generation": "g1", "entries": [{"target": dict(CLOUD_TARGET), "clock": "igt",
                "game_version": "us", "overall_cutoffs": cutoffs,
                "strategies": [{"name": "Standard", "cutoffs": cutoffs}, {"name": "Other", "cutoffs": cutoffs}]}]}
        if path == "/v1/pool":
            self.calls.append((method, path, kwargs))
            if self.gate is not None:
                await self.gate.wait()
            if self.failure is not None:
                raise self.failure
            query = kwargs["params"]
            return page([row for row in self.rows
                         if query["scope"] == "overall" or row["strategy"] == query.get("strategy")],
                        complete=self.complete)
        if path.startswith("/v1/recordings/") and not path.endswith("/asset"):
            self.calls.append((method, path, kwargs))
            found = next((row for row in self.rows if row["id"] == path.split("/")[-1]), None)
            if found is None:
                raise CommunityError("not_found", status=404)
            return {"v": 1, **found}
        return await super().request_json(method, path, **kwargs)


def lineup(tmp_path, cloud, *, scope="strategy"):
    tracker = SimpleNamespace(db=Database(tmp_path / "lineup.db"), ranks=Standards())
    population = CachedPopulation(Local(), root=tmp_path / "cache", account=cloud)
    sync = CommunitySync(cloud, population)
    native = Native()
    rivals = Rivals(tracker, population, native, lambda: "us", community=sync)
    if scope != "strategy":
        rivals.scopes[rivals._entity_key(STANDARD)] = scope
    return rivals, tracker, population, sync, native


async def settle(sync, rivals=None, target=None, *, enabled=True, cycles=200):
    """Run watch cycles until three in a row leave the sync queue idle."""
    quiet = 0
    for _ in range(cycles):
        if rivals is not None:
            rivals.refresh(target, CONTROLS, enabled=enabled)
        await asyncio.sleep(0.01)
        quiet = quiet + 1 if not sync._queue and sync._running is None else 0
        if quiet >= 3:
            return
    raise AssertionError("the lineup never settled")


def run(tmp_path, cloud, scenario, *, scope="strategy"):
    rivals, tracker, population, sync, native = lineup(tmp_path, cloud, scope=scope)

    async def main():
        await sync.start()
        try:
            await scenario(rivals, tracker, population, sync, native)
        finally:
            await sync.stop()
    try:
        asyncio.run(main())
    finally:
        population.close()
        tracker.db.close()


def pb_at(frames):
    identity = PracticeIdentity("star", "1-0", "igt", "us", "Standard")
    return {**rank_frames(Standards(), identity, frames), "frames": frames, "current_strategy": "Standard",
            "game_version": "us"}


@pytest.mark.parametrize("scope", ["strategy", "overall"])
def test_no_strategy_never_raises_and_asks_nobody(tmp_path, monkeypatch, scope):
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])
    reads = []
    monkeypatch.setattr(module, "saved_pb_basis", lambda *args: reads.append(args))

    async def scenario(rivals, tracker, population, sync, native):
        tracker.db.set_state(module.STATE_KEY, {"version": 4, "options": {}, "scopes": {},
            "pins": {rivals._strategy_key(STANDARD): {"0": "f" * 64}}})
        before = tracker.db.get_state(module.STATE_KEY, {})
        for _ in range(5):
            answer = rivals.refresh(NONE, CONTROLS, enabled=True, force=True)
            payload = rivals.payload()
            await asyncio.sleep(0.01)
        assert answer["reason"] == "no_strategy" and answer["ghosts"] == []
        assert (payload["state"], payload["reason"], payload["rows"], payload["pb"]) == ("no_strategy", None, [], None)
        assert payload["coverage"] is None and payload["strategy_name"] is None
        assert cloud.calls == [] and native.preparations == [] and reads == []
        # No pin is read into the lineup or created for the target without a strategy.
        with pytest.raises(ValueError, match="strategy"):
            rivals.checked({"action": "add", "id": "e" * 64}, NONE)
        assert not rivals.remember_saved(NONE, SimpleNamespace(id=1))
        rivals.apply({"scope": scope})
        assert tracker.db.get_state(module.STATE_KEY, {})["pins"] == before["pins"]
    run(tmp_path, cloud, scenario, scope=scope)


def test_switching_strategies_asks_each_list_once_and_never_mixes_them(tmp_path):
    cloud = Cloud([*(cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)),
                   *(cloud_row(number + 100, frames, "Other") for number, frames in enumerate(FRAMES, 1))])

    async def scenario(rivals, tracker, population, sync, native):
        # A -> B -> A before any answer: one request each, held back.
        cloud.gate = asyncio.Event()
        for target in (STANDARD, OTHER, STANDARD):
            for _ in range(3):
                rivals.refresh(target, CONTROLS, enabled=True)
                await asyncio.sleep(0.01)
        cloud.gate.set()
        await settle(sync, rivals, STANDARD)
        assert {row["strategy"] for row in rivals.rows} == {"Standard"} and len(rivals.rows) == 4
        for target in (OTHER, STANDARD, OTHER, STANDARD):
            await settle(sync, rivals, target)
            assert {row["strategy"] for row in rivals.rows} == {target["strat"]}
        assert len(cloud.lists("Standard")) == 1 and len(cloud.lists("Other")) == 1
        # Each recording downloads once, however often the lineup returns to it.
        assets = [call[1] for call in cloud.calls if call[1].endswith("/asset")]
        assert len(assets) == len(set(assets)) == 8
    run(tmp_path, cloud, scenario)


def test_a_pb_change_asks_no_list_and_keeps_surviving_seats(tmp_path, monkeypatch):
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])
    current = [pb_at(930)]
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: current[0])

    async def scenario(rivals, tracker, population, sync, native):
        await settle(sync, rivals, STANDARD)
        before = {row["slot"]: (row["id"], row["hue_degrees"], row["player_id"]) for row in rivals.rows}
        lists, generation = len(cloud.lists()), rivals.generation
        assert len(before) == 4 and lists == 1
        current[0] = pb_at(860)  # passes the slowest seat only
        rivals.pb_changed(STANDARD)
        await settle(sync, rivals, STANDARD)
        after = {row["slot"]: (row["id"], row["hue_degrees"], row["player_id"]) for row in rivals.rows}
        assert len(cloud.lists()) == lists
        survivors = [slot for slot, seat in before.items() if seat in after.values()]
        assert len(survivors) == 3 and all(after[slot] == before[slot] for slot in survivors)
        assert rivals.generation > generation and all(not row["pending"] for row in rivals.rows)
    run(tmp_path, cloud, scenario)


def test_pending_seats_show_at_once_but_only_downloaded_ones_race(tmp_path, monkeypatch):
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: pb_at(930))
    gate = asyncio.Event()
    original = cloud.request_bytes

    async def held(method, path, **kwargs):
        await gate.wait()
        return await original(method, path, **kwargs)
    cloud.request_bytes = held

    async def scenario(rivals, tracker, population, sync, native):
        for _ in range(20):
            rivals.refresh(STANDARD, CONTROLS, enabled=True)
            await asyncio.sleep(0.01)
            if rivals.rows:
                break
        payload = rivals.payload()
        assert payload["state"] == "ready" and len(payload["rows"]) == 4
        assert all(row["pending"] for row in payload["rows"]) and payload["status"] == "Downloading rivals…"
        assert all(not prepared[1] for prepared in native.preparations)
        gate.set()
        await settle(sync, rivals, STANDARD)
        payload = rivals.payload()
        assert [row["pending"] for row in payload["rows"]] == [False] * 4
        assert len(native.preparations[-1][1]) == 4
    run(tmp_path, cloud, scenario)


def test_overlay_off_downloads_nothing_even_with_a_held_list(tmp_path, monkeypatch):
    from sm64_events.community.sync import checked_page
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: pb_at(930))

    async def scenario(rivals, tracker, population, sync, native):
        identity = PracticeIdentity("star", "1-0", "igt", "us", "Standard")
        population.remember_page(identity, checked_page(page(cloud.rows)))
        await settle(sync, rivals, STANDARD, enabled=False)
        assert len(rivals.rows) == 4 and all(row["pending"] for row in rivals.rows)
        assert cloud.calls == [] and native.preparations == []
        await settle(sync, rivals, STANDARD)
        assert [row["pending"] for row in rivals.rows] == [False] * 4
        assert len([call for call in cloud.calls if call[1].endswith("/asset")]) == 4
    run(tmp_path, cloud, scenario)


def test_lineup_state_and_reason_for_each_case(tmp_path, monkeypatch):
    from sm64_events.community import sync as sync_module
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: pb_at(930))
    monkeypatch.setattr(sync_module, "random", SimpleNamespace(uniform=lambda *_: 0))  # no jitter waits
    cloud = Cloud()

    async def scenario(rivals, tracker, population, sync, native):
        # Overlay off: nothing asked, nothing to explain yet.
        rivals.refresh(STANDARD, CONTROLS, enabled=False)
        assert (rivals.payload()["state"], rivals.payload()["reason"]) == ("ready", None) and cloud.calls == []
        # In flight with nothing to show.
        cloud.gate = asyncio.Event()
        rivals.refresh(STANDARD, CONTROLS, enabled=True)
        await asyncio.sleep(0.02)
        assert (rivals.payload()["state"], rivals.payload()["reason"]) == ("loading", None)
        # The list arrived empty.
        cloud.gate.set()
        await settle(sync, rivals, STANDARD)
        assert (rivals.payload()["state"], rivals.payload()["reason"]) == ("ready", "nobody")
        # Unreachable, with no list held for this strategy: one request (three
        # immediate attempts), then the cooldown instead of a retry every cycle.
        cloud.failure = CommunityError("network_unavailable")
        await settle(sync, rivals, OTHER)
        assert (rivals.payload()["state"], rivals.payload()["reason"]) == ("ready", "offline")
        attempts = len(cloud.lists("Other"))
        await settle(sync, rivals, OTHER)
        assert attempts == 3 and len(cloud.lists("Other")) == attempts
        # Signed out with no local rows.
        cloud.state.update(authenticated=False, status="signed_out", uid=None)
        rivals.refresh(STANDARD, CONTROLS, enabled=True, force=True)
        assert (rivals.payload()["state"], rivals.payload()["reason"]) == ("ready", "signed_out")
        # A build with no cloud at all has nobody to race.
        rivals.community.sync = None
        assert rivals.lineup_state() == ("ready", "nobody")
    run(tmp_path, cloud, scenario)


def test_quiet_watch_cycles_touch_no_database(tmp_path, monkeypatch):
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])

    class Counting:
        def __init__(self, inner):
            self.inner, self.calls = inner, []

        def __getattr__(self, name):
            value = getattr(self.inner, name)
            if not callable(value):
                return value

            def counted(*args, **kwargs):
                self.calls.append(name)
                return value(*args, **kwargs)
            return counted

    async def scenario(rivals, tracker, population, sync, native):
        tracker.db = Counting(tracker.db)
        rivals.pins[rivals._key(STANDARD)] = {"3": "e" * 64}
        await settle(sync, rivals, STANDARD)
        rivals.refresh(STANDARD, CONTROLS, enabled=True)
        rivals.payload()
        tracker.db.calls.clear()
        lists = len(cloud.lists())
        for _ in range(10):
            rivals.refresh(STANDARD, CONTROLS, enabled=True)
            rivals.payload()
            await asyncio.sleep(0)
        assert tracker.db.calls == [] and len(cloud.lists()) == lists
        tracker.db = tracker.db.inner
    run(tmp_path, cloud, scenario)


def test_a_recording_that_cannot_download_gives_up_its_seat(tmp_path, monkeypatch):
    rows = [cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)]
    cloud = Cloud(rows)
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: pb_at(930))
    broken = rows[1]["id"]  # 870 frames: seated first
    original = cloud.request_bytes

    async def corrupt(method, path, **kwargs):
        if broken in path:
            cloud.calls.append((method, path, kwargs))
            return b"not a recording"
        return await original(method, path, **kwargs)
    cloud.request_bytes = corrupt

    async def scenario(rivals, tracker, population, sync, native):
        for _ in range(3):
            await settle(sync, rivals, STANDARD)
        assert broken not in {row["id"] for row in rivals.rows} and len(rivals.rows) == 4
        assert all(not row["pending"] for row in rivals.rows)
        attempts = [call for call in cloud.calls if broken in call[1] and call[1].endswith("/asset")]
        assert len(attempts) == 1
    run(tmp_path, cloud, scenario)


def test_a_held_list_renews_once_per_lease_without_moving_seats(tmp_path, monkeypatch):
    """The approved lease plan (2026-10-16): one list per ten-minute lease while
    on screen, no loading state and no reshuffle on renewal; a retracted
    recording's seat refills by the normal rules."""
    from sm64_events.community import cache as cache_module, sync as sync_module
    now = [1000.0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(cache_module, "time", clock)
    monkeypatch.setattr(sync_module, "time", clock)
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: pb_at(930))
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])

    def seats(rivals):
        return {row["slot"]: (row["id"], row["hue_degrees"]) for row in rivals.rows}

    async def scenario(rivals, tracker, population, sync, native):
        await settle(sync, rivals, STANDARD)
        before, states = seats(rivals), set()
        retracted = before[0][0]
        for minute in range(1, 61):
            now[0] += 60
            if minute == 20:
                cloud.rows = [row for row in cloud.rows if row["id"] != retracted]
            await settle(sync, rivals, STANDARD)
            states.add(rivals.payload()["state"])
            current = seats(rivals)
            assert all(current[slot] == before[slot] for slot in (1, 2, 3))
            assert current[0] == before[0] if minute < 27 else current[0][0] != retracted
        # One list at arrival, then one renewal every nine minutes: 7 in the first hour.
        assert len(cloud.lists()) == 7 and states == {"ready"}
        assert seats(rivals)[0][1] == before[0][1]  # the refilled seat keeps its colour
        assets = [call[1] for call in cloud.calls if call[1].endswith("/asset")]
        assert len(assets) == len(set(assets)) == 5  # four seats, then the refill; never twice
    run(tmp_path, cloud, scenario)


def test_one_lineups_failure_never_marks_another_unavailable(tmp_path, monkeypatch):
    from sm64_events.community import sync as sync_module
    monkeypatch.setattr(module, "saved_pb_basis", lambda *_: pb_at(930))
    monkeypatch.setattr(sync_module, "random", SimpleNamespace(uniform=lambda *_: 0))
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])
    cloud.complete = False  # a truncated list: the lineup reports its own list's standing
    missing = {**STANDARD, "strat": "Not in the catalog"}

    async def scenario(rivals, tracker, population, sync, native):
        await settle(sync, rivals, STANDARD)
        assert rivals.payload()["status"] == "Showing a partial rival list."
        await settle(sync, rivals, missing)
        assert (rivals.payload()["state"], rivals.payload()["reason"]) == ("ready", "nobody")
        assert rivals.payload()["status"] is None
        sync.last_error = "Community contributions are temporarily unavailable."  # an upload failed
        await settle(sync, rivals, STANDARD)
        payload = rivals.payload()
        assert payload["state"] == "ready" and len(payload["rows"]) == 4
        assert payload["status"] == "Showing a partial rival list."
    run(tmp_path, cloud, scenario)


def test_the_card_answers_with_no_strategy_and_still_saves_settings(tmp_path):
    """The reported break: a signed-in Strategy target with no strategy made
    GET /api/ghost answer 500 and every PUT 422 after applying it."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sm64_events.server.ghost_api import create_ghost_router
    from tests.test_ghost_session import a_session
    cloud = Cloud([cloud_row(number, frames) for number, frames in enumerate(FRAMES, 1)])
    database = Database(tmp_path / "card.db")
    session, native, tracker, _ = a_session(tmp_path, target=("star", 1, 0), strat=None, db=database)
    tracker.ranks = Standards()
    population = CachedPopulation(Local(), root=tmp_path / "cache", account=cloud)
    session._rivals = Rivals(tracker, population, native, lambda: "us", community=CommunitySync(cloud, population))
    app = FastAPI()
    app.include_router(create_ghost_router(session))
    try:
        with TestClient(app) as client:
            read = client.get("/api/ghost")
            assert read.status_code == 200
            assert (read.json()["rivals"]["state"], read.json()["rivals"]["rows"]) == ("no_strategy", [])
            saved = client.put("/api/ghost", json={"opacity": 0.3})
            assert saved.status_code == 200 and saved.json()["opacity"] == 0.3
            assert client.get("/api/ghost").json()["opacity"] == 0.3
        assert cloud.calls == []
    finally:
        population.close()
        database.close()
