"""The game version setting reaches grading, the session view and the API.

One knob: `service.set_game_version(cfg)` flips the standards store's
grading version to the EFFECTIVE version (core/modes.py::effective_version)
and broadcasts `game_version_changed`, so every open client refetches and
every rank surface re-grades -- live, no restart (his ruling 2026-08-15:
"the user should be able to freely swap between roms as they please")."""
import asyncio
import json

from fastapi.testclient import TestClient

from sm64_events.core.modes import GameVersion, ModeConfig
from sm64_events.core.paths import bundled_rank_standards, bundled_sheet_ladders
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import build_session_view


class OfflineMemory:
    attached = False

    def attach(self):
        return False

    def detach(self):
        pass


class _SpyBroadcaster(Broadcaster):
    def __init__(self):
        super().__init__()
        self.published = []

    async def publish(self, event):
        self.published.append(event)
        await super().publish(event)


def make(tmp_path):
    db = Database(tmp_path / "t.db")
    ranks = RankStandards(tmp_path / "rs.json", bundled_rank_standards(),
                          bundled_sheet_ladders())
    ranks.load()
    broadcaster = _SpyBroadcaster()
    svc = TrackerService(db, broadcaster, ranks=ranks)
    asyncio.run(svc.start())
    return db, svc, broadcaster


def _annotated(ranks):
    for ek in ranks.graded_entities():
        for strat in ranks.strategies(ek):
            if ranks.jp_deltas(ek, strat):
                return ek, strat
    raise AssertionError("no annotated strategy in the bundled seeds")


def test_boot_default_is_auto_graded_as_us(tmp_path):
    _db, svc, _b = make(tmp_path)
    assert svc.game_version() == {"setting": "auto", "effective": "us"}
    assert svc.ranks.grading_version == "us"


def test_set_game_version_regrades_live_and_broadcasts(tmp_path):
    db, svc, broadcaster = make(tmp_path)
    ek, strat = _annotated(svc.ranks)
    us = svc.ranks.ladder_cs(ek, strat)
    out = asyncio.run(svc.set_game_version(ModeConfig(version=GameVersion.JP)))
    assert out == {"setting": "jp", "effective": "jp"}
    assert svc.ranks.grading_version == "jp"
    assert svc.ranks.ladder_cs(ek, strat) == svc.ranks.ladder_cs(ek, strat, "jp") != us
    last = broadcaster.published[-1]
    assert last.type == "game_version_changed" and last.payload == out
    view = build_session_view(db, svc, "igt")
    assert view["game_version"] == {"setting": "jp", "effective": "jp"}


def test_auto_resolves_back_to_us(tmp_path):
    _db, svc, _b = make(tmp_path)
    asyncio.run(svc.set_game_version(ModeConfig(version=GameVersion.JP)))
    out = asyncio.run(svc.set_game_version(ModeConfig(version=GameVersion.AUTO)))
    assert out == {"setting": "auto", "effective": "us"}
    assert svc.ranks.grading_version == "us"


def test_the_view_carries_the_game_version(tmp_path):
    db, svc, _b = make(tmp_path)
    assert build_session_view(db, svc, "igt")["game_version"] == \
        {"setting": "auto", "effective": "us"}


# ---- /api/mode ----------------------------------------------------------------

def make_client(tmp_path):
    db, svc, broadcaster = make(tmp_path)
    app = create_app(Poller(OfflineMemory(), [], svc), broadcaster, service=svc,
                     mode_path=tmp_path / "tracker_mode.json")
    return TestClient(app), svc


def test_mode_get_defaults_then_put_flips_effective_and_persists(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        r = client.get("/api/mode")
        assert r.status_code == 200
        assert r.json() == {"mode": "emu", "version": "auto", "effective": "us",
                            "unsupported": False}
        r = client.put("/api/mode", json={"version": "jp"})
        assert r.status_code == 200
        assert r.json() == {"mode": "emu", "version": "jp", "effective": "jp",
                            "unsupported": True}
        assert svc.ranks.grading_version == "jp"
        assert json.loads((tmp_path / "tracker_mode.json").read_text())["version"] == "jp"
        assert client.get("/api/mode").json()["version"] == "jp"
        r = client.put("/api/mode", json={"version": "pal"})
        assert r.status_code == 400
        # US is never "unsupported" (it is the emulator's own version)
        assert client.put("/api/mode", json={"version": "us"}).json()["unsupported"] is False


def test_the_standards_payload_resolves_on_the_asked_version(tmp_path):
    client, svc = make_client(tmp_path)
    ek, strat = _annotated(svc.ranks)
    with client:
        us = client.get("/api/ranks/standards", params={"entity": ek}).json()
        jp = client.get("/api/ranks/standards", params={"entity": ek, "version": "jp"}).json()
        assert us["version"] == "us" and jp["version"] == "jp"
        assert us["grading_version"] == "us" and jp["grading_version"] == "us"
        assert us["strategies"][strat] != jp["strategies"][strat]
        assert us["strategies_jp"] == jp["strategies"]
        assert strat in us["jp_strategies"] and us["jp_strategies"] == jp["jp_strategies"]
        assert client.get("/api/ranks/standards",
                          params={"entity": ek, "version": "pal"}).status_code == 400
        # no version asked = the grading version, which the setting moves
        client.put("/api/mode", json={"version": "jp"})
        graded = client.get("/api/ranks/standards", params={"entity": ek}).json()
        assert graded["version"] == "jp" and graded["grading_version"] == "jp"
        assert graded["strategies"] == jp["strategies"]


def test_jp_threshold_round_trip_through_the_api(tmp_path):
    client, svc = make_client(tmp_path)
    with client:
        assert client.put("/api/ranks/standards/star:9:1/Mine/Mario",
                          json={"seconds": 30}).status_code == 200
        assert client.put("/api/ranks/standards/star:9:1/Mine/Mario",
                          params={"version": "jp"}, json={"seconds": 29}).status_code == 200
        jp = client.get("/api/ranks/standards", params={"entity": "star:9:1", "version": "jp"}).json()
        us = client.get("/api/ranks/standards", params={"entity": "star:9:1"}).json()
        assert jp["strategies"]["Mine"]["Mario"] == 29 and us["strategies"]["Mine"]["Mario"] == 30
        assert us["jp_strategies"] == ["Mine"]
        assert client.delete("/api/ranks/standards/star:9:1/Mine/jp").status_code == 200
        after = client.get("/api/ranks/standards", params={"entity": "star:9:1"}).json()
        assert after["jp_strategies"] == [] and after["strategies_jp"]["Mine"]["Mario"] == 30
        assert client.put("/api/ranks/standards/star:9:1/Mine/Mario",
                          params={"version": "pal"}, json={"seconds": 1}).status_code == 400
