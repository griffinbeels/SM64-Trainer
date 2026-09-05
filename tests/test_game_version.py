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
        return await super().publish(event)      # the seq the journal stamps


def make(tmp_path, start=True):
    """`start=False` for the API path: the app's lifespan starts the service
    itself, and a second start resets the journal's seq counter."""
    db = Database(tmp_path / "t.db")
    ranks = RankStandards(tmp_path / "rs.json", bundled_rank_standards(),
                          bundled_sheet_ladders())
    ranks.load()
    broadcaster = _SpyBroadcaster()
    svc = TrackerService(db, broadcaster, ranks=ranks)
    if start:
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
    db, svc, broadcaster = make(tmp_path, start=False)
    # An empty library (round 33): this test's world is the two ladders it
    # writes, and the bundled library's fitted rows would grade beside them.
    app = create_app(Poller(OfflineMemory(), [], svc), broadcaster, service=svc,
                     mode_path=tmp_path / "tracker_mode.json",
                     library_path=tmp_path / "sheet_library.json.gz",
                     library_bundled_path=tmp_path / "no-library.json.gz")
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
        assert us["strategies_us"]["Mine"]["Mario"] == 30
        assert us["clearable_jp_strategies"] == ["Mine"]


def test_clearable_jp_is_the_users_overlay_only(tmp_path):
    """A sheet-fitted JP ladder is not his to clear; a typed JP time on that
    same strategy IS -- and the flag follows the overlay, not the base."""
    client, svc = make_client(tmp_path)
    ek, strat = next((ek, strat) for ek, layers in svc.ranks._sheet_jp.items()
                     for strat in layers)
    with client:
        before = client.get("/api/ranks/standards", params={"entity": ek}).json()
        assert strat in before["jp_strategies"]
        assert strat not in before["clearable_jp_strategies"]
        client.put(f"/api/ranks/standards/{ek}/{strat}/Mario",
                   params={"version": "jp"}, json={"seconds": 1})
        after = client.get("/api/ranks/standards", params={"entity": ek}).json()
        assert strat in after["clearable_jp_strategies"]
        client.delete(f"/api/ranks/standards/{ek}/{strat}/jp")
        cleared = client.get("/api/ranks/standards", params={"entity": ek}).json()
        assert strat not in cleared["clearable_jp_strategies"]
        assert strat in cleared["jp_strategies"]        # the fitted layer stays


# ---- a version flip is not a rank-up ----------------------------------------

def _ev(type_, frame, payload=None):
    from datetime import datetime, timezone
    from sm64_events.core.events import Event
    return Event(type=type_, frame=frame, timestamp_utc=datetime.now(timezone.utc),
                 payload=payload or {})


def _saved_pb_on_a_versioned_ladder(client, svc):
    """star:8:2 / Mine: US Silver 30 / Gold 20 / Mario 10, JP more lenient
    (Silver 40 / Gold 30 / Mario 20); a saved 25 s PB is Silver on US and
    Gold on JP, so a flip to JP RAISES the rank without a run."""
    asyncio.run(svc.publish(_ev("practice_reset", 1000, {"igt_frames_before": 0})))
    asyncio.run(svc.publish(_ev("star_collected", 1750,
                                {"course_id": 8, "star_id": 2, "igt_frames": 750})))
    for rank, us, jp in (("Mario", 10, 20), ("Gold", 20, 30), ("Silver", 30, 40)):
        client.put(f"/api/ranks/standards/star:8:2/Mine/{rank}", json={"seconds": us})
        client.put(f"/api/ranks/standards/star:8:2/Mine/{rank}",
                   params={"version": "jp"}, json={"seconds": jp})
    asyncio.run(svc.set_strat(8, 2, "Mine"))
    svc.db._conn.execute("UPDATE attempts SET strat_tag='Mine' WHERE course_id=8")
    svc.db._conn.commit()
    aid = next(a.id for a in svc.db.attempts() if a.course_id == 8)
    asyncio.run(svc.save_pb(aid, "igt"))


def test_flipping_the_version_absorbs_the_new_rank_instead_of_celebrating(tmp_path, monkeypatch):
    """Whole-branch review, 2026-08-15: a PUT /api/mode that re-graded a
    scope UP fired the full-screen MARELO takeover for a rank he never ran
    for, and again on every flip. The flip is his gesture; the claim "you
    ranked up" is not true. Arriving absorbs -- the same rule as a scope
    switch -- applied to every watermarked scope at the moment of the flip."""
    # test_ranks_api's tiny seed (one other entity), so ONE saved PB moves
    # the overall tier -- under the full bundled seed coverage dilutes every
    # scope to Iron V in both versions and the flip proves nothing.
    from test_ranks_api import make_client as make_small_client
    from sm64_events.core import modes
    monkeypatch.setattr(modes, "mode_settings_path", lambda: tmp_path / "tracker_mode.json")
    client, svc = make_small_client(tmp_path)
    with client:
        _saved_pb_on_a_versioned_ladder(client, svc)
        us = client.get("/api/marelo").json()            # arrive on US; seeds the watermark
        assert us["celebration"] is None
        client.put("/api/mode", json={"version": "jp"})
        jp = client.get("/api/marelo").json()
        assert (jp["tier"], jp["division"]) != (us["tier"], us["division"]), (us, jp)
        assert jp["celebration"] is None, jp["celebration"]
        from sm64_events.ranks import scoring
        assert svc.marelo_watermarks()["overall"] == scoring.progression_key(
            jp["tier"], jp["division"])
        # ...and back: the watermark follows the drop, so a REAL later rise
        # still celebrates from the right floor.
        client.put("/api/mode", json={"version": "us"})
        back = client.get("/api/marelo").json()
        assert back["celebration"] is None
        assert svc.marelo_watermarks()["overall"] == scoring.progression_key(
            back["tier"], back["division"])
        svc.db.set_state("marelo_watermarks", {"overall": svc.marelo_watermarks()["overall"] - 1})
        assert client.get("/api/marelo").json()["celebration"] is not None
