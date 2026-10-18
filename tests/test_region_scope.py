"""Region scope (task 0064): whether a target's strategy plays the same on US
and JP, decided from the rank standards and nothing he sets.

His words: "if the stars are the same for US/JP (no difference), then ALL
ghosts are valid for that star ... if the star is differently timed ... the
ghosts should be region locked to only your version's ghosts", and "the user
should NEVER have to think about this manually; it should just work."
"""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import sm64_events
from sm64_events.core.events import Event
from sm64_events.core.modes import GameVersion, ModeConfig
from sm64_events.ghost.adaptive import PracticeIdentity, rank_recordings
from sm64_events.ghost.community_rivals import pools
from sm64_events.ghost.rivals import Rivals
from sm64_events.library.adoptions import library_ladders
from sm64_events.ranks.region_scope import REGIONAL, SHARED, scope_of, serves, widest_scope
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.mode_api import create_mode_router
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService
from sm64_events.tracking.views import current_pbs_by_strat, foreign_rom
from tests.test_ghost_adaptive import attempt, recording, save

DATA = Path(sm64_events.__file__).parent / "data"
BOB_SUMMIT = "star:1:0"       # vetted JP times differ: a dialogue star
TTC_REDS = "star:14:3"        # "Stop Time for Red Coins": no JP difference anywhere
TOAD = "star:0:0"             # no standards at all
LADDER = {"Mario": 10.0, "Grandmaster": 10.5, "Master": 11.0, "Diamond": 11.5,
          "Platinum": 12.0, "Gold": 13.0, "Silver": 14.0, "Bronze": 15.0}


@pytest.fixture
def store(tmp_path):
    """The shipped seed and Sheet layer, as a fresh install has them."""
    standards = RankStandards(tmp_path / "rank_standards.json", DATA / "rank_standards.seed.json",
                              DATA / "sheet_ladders.seed.json")
    standards.load()
    return standards


# ---- the answer -------------------------------------------------------------

def test_a_star_with_jp_times_is_regional_on_every_strategy(store):
    assert store.jp_deltas(BOB_SUMMIT, "Standard"), "the seed's own evidence"
    assert {strat: scope_of(BOB_SUMMIT, strat, store) for strat in store.ladders(BOB_SUMMIT, "us")} \
        == {"Standard": REGIONAL, "Left Side": REGIONAL}


def test_a_ttc_star_with_no_jp_difference_is_shared(store):
    assert store.ladders(TTC_REDS, "us")["Standard"] and not store.jp_deltas(TTC_REDS, "Standard")
    assert scope_of(TTC_REDS, "Standard", store) == SHARED
    assert widest_scope(TTC_REDS, store) == SHARED


def test_an_entity_with_no_standards_is_regional(store):
    assert store.ladders(TOAD, "us") == {}
    assert scope_of(TOAD, "Standard", store) == REGIONAL
    assert widest_scope(TOAD, store) == REGIONAL


def test_a_custom_strategy_follows_its_entity(store):
    store.create_strategy(TTC_REDS, "My Way")
    store.create_strategy(BOB_SUMMIT, "My Way")
    assert scope_of(TTC_REDS, "My Way", store) == SHARED
    assert scope_of(BOB_SUMMIT, "My Way", store) == REGIONAL


def test_a_typed_jp_time_makes_the_strategy_regional_at_once(store):
    """The answer is cached per standards revision, so an edit must reach it."""
    assert scope_of(TTC_REDS, "Standard", store) == SHARED
    store.set_threshold(TTC_REDS, "Standard", "Gold", 30.0, version="jp")
    assert scope_of(TTC_REDS, "Standard", store) == REGIONAL
    store.clear_jp(TTC_REDS, "Standard")
    assert scope_of(TTC_REDS, "Standard", store) == SHARED


def test_a_sheet_row_timed_on_one_rom_only_is_regional(store):
    target = {"section": "TTC", "entity_key": TTC_REDS, "label": "Stop Time for Red Coins",
              "subsections": [], "approaches": [
                  {"name": "Clock trick (JP)", "ids": ["a"], "ladder": LADDER,
                   "entries": [{"version": "jp", "time_cs": 1200}, {"version": "jp", "time_cs": 1300}]},
                  {"name": "Both trick", "ids": ["b"], "ladder": LADDER,
                   "entries": [{"version": None, "time_cs": 1200}, {"version": "us", "time_cs": 1300}]}]}
    mapping = library_ladders({"targets": [target]}, {})
    assert mapping[TTC_REDS]["versions"] == {"Clock trick (JP)": "jp"}
    store.apply_sheet_ladders(mapping)
    assert store.single_version_strategies(TTC_REDS) == {"Clock trick (JP)": "jp"}
    assert scope_of(TTC_REDS, "Clock trick (JP)", store) == REGIONAL
    assert scope_of(TTC_REDS, "Both trick", store) == SHARED
    assert widest_scope(TTC_REDS, store) == SHARED


def test_an_unknown_rom_serves_only_a_shared_scope():
    assert serves(SHARED, "us", None) and serves(SHARED, "us", "jp") and serves(SHARED, "us", "us")
    assert serves(REGIONAL, "us", "us")
    assert not serves(REGIONAL, "us", "jp") and not serves(REGIONAL, "us", None)


# ---- which ghosts serve -----------------------------------------------------

def _graded_ids(store, ek, entity_key, strategy, rows, scope="strategy"):
    frames = round(store.ladder_cs(ek, strategy, "us")["Gold"] * 30 / 100)
    rows = [recording(identity, frames, entity_key=entity_key, strategy=strategy, **values)
            for identity, values in rows]
    identity = PracticeIdentity("star", entity_key, "igt", "us", strategy, scope=scope)
    return {row["id"] for row in rank_recordings(rows, store, identity)}


ROWS = [("us", {"game_version": "us"}), ("jp", {"game_version": "jp"}),
        ("unknown", {"game_version": "us", "source_status": "bundled-author-recording",
                     "structurally_valid": True, "certified": False, "full_start": None,
                     "recorded_game_version": None})]


def test_a_shared_star_serves_both_roms_and_unknown_recordings(store):
    assert _graded_ids(store, TTC_REDS, "14-3", "Standard", ROWS) == {"us", "jp", "unknown"}


def test_a_regional_star_serves_only_the_running_rom(store):
    assert _graded_ids(store, BOB_SUMMIT, "1-0", "Standard", ROWS) == {"us"}
    assert _graded_ids(store, BOB_SUMMIT, "1-0", "Standard", ROWS, scope="overall") == {"us"}


def test_the_rival_identity_carries_what_providers_fetch(tmp_path, store):
    tracker = SimpleNamespace(db=Database(tmp_path / "practice.db"), ranks=store)
    try:
        rivals = Rivals(tracker, None, None, lambda: "us")
        shared = rivals.identity_for({"kind": "star", "entity_key": "14-3", "strat": "Standard"})
        regional = rivals.identity_for({"kind": "star", "entity_key": "1-0", "strat": "Standard"})
        assert (shared.region_scope, regional.region_scope) == (SHARED, REGIONAL)
        # The other ROM's community pool is requested only where it may serve.
        assert [pool.game_version for pool in pools(shared)] == ["us", "jp"]
        assert [pool.game_version for pool in pools(regional)] == ["us"]
    finally:
        tracker.db.close()


# ---- which PB counts --------------------------------------------------------

def test_a_regional_strategy_keeps_one_pb_per_rom(tmp_path, store):
    database = Database(tmp_path / "pbs.db")
    us, jp = attempt(1, 1400, course_id=1, star_id=0), attempt(2, 1300, course_id=1, star_id=0, game_version="jp")
    try:
        database.replace_attempts([us, jp])
        save(database, us)
        save(database, jp)
        key = (1, 0, "igt", "Standard")
        assert current_pbs_by_strat(database.pbs(), running="us", ranks=store)[key]["attempt_id"] == 1
        assert current_pbs_by_strat(database.pbs(), running="jp", ranks=store)[key]["attempt_id"] == 2
        assert current_pbs_by_strat(database.pbs())[key]["attempt_id"] == 2, "no ROM named: latest save"
        for rom, owner in (("us", 1), ("jp", 2)):
            assert database.current_pb(1, 0, "igt", strat_tag="Standard", game_version=rom,
                                       region_scope=REGIONAL)["attempt_id"] == owner
    finally:
        database.close()


def test_a_shared_strategy_keeps_one_pb_and_the_faster_rom_wins(tmp_path, store):
    database = Database(tmp_path / "pbs.db")
    jp = attempt(3, 850, course_id=14, star_id=3, game_version="jp")
    us = attempt(4, 900, course_id=14, star_id=3)
    try:
        database.replace_attempts([jp, us])
        save(database, jp)
        save(database, us)  # saved later, but slower
        key = (14, 3, "igt", "Standard")
        for rom in ("us", "jp"):
            assert current_pbs_by_strat(database.pbs(), running=rom, ranks=store)[key]["attempt_id"] == 3
            assert database.current_pb(14, 3, "igt", strat_tag="Standard", game_version=rom,
                                       region_scope=SHARED)["attempt_id"] == 3
    finally:
        database.close()


def test_a_pb_naming_no_rom_counts_for_both_but_a_played_one_knows_its_rom(tmp_path, store):
    database = Database(tmp_path / "pbs.db")
    played = attempt(5, 1400, course_id=1, star_id=0)
    try:
        database.replace_attempts([played])
        # A played save from before 2026-10-16 stamped no ROM; its run knows it.
        save(database, played, game_version=None)
        key = (1, 0, "igt", "Standard")
        assert current_pbs_by_strat(database.pbs(), running="us", ranks=store)[key]["attempt_id"] == 5
        assert key not in current_pbs_by_strat(database.pbs(), running="jp", ranks=store)
        imported = database.insert_pb(1, 0, "Standard", "igt", 1500, None, "2026-10-16T00:00:00Z",
                                      imported_from="sheet", game_version=None)
        for rom in ("us", "jp"):
            assert current_pbs_by_strat(database.pbs(), running=rom, ranks=store)[key]["id"] == imported
    finally:
        database.close()


def test_a_run_from_the_other_rom_of_a_regional_strategy_is_foreign(store):
    store.grading_version = "us"
    assert foreign_rom(store, attempt(1, course_id=1, star_id=0, game_version="jp"))
    assert not foreign_rom(store, attempt(1, course_id=1, star_id=0, game_version="us"))
    assert not foreign_rom(store, attempt(1, course_id=1, star_id=0, game_version=None))
    assert not foreign_rom(store, attempt(1, course_id=14, star_id=3, game_version="jp")), "shared"


T0 = datetime(2026, 10, 16, tzinfo=timezone.utc)


def _ev(type_, frame, payload=None):
    return Event(type=type_, frame=frame, timestamp_utc=T0, payload=payload or {})


def test_a_played_pb_stores_the_rom_it_was_set_on(tmp_path, store):
    """Before 2026-10-16 only imports named their ROM."""
    db = Database(tmp_path / "t.db")
    svc = TrackerService(db, Broadcaster(), store)
    svc.observed_game_version = lambda: "jp"

    async def scenario():
        await svc.start()
        await svc.set_strat(2, 1, "Triple Jump")
        for step in (_ev("level_changed", 1000, {"from": 6, "to": 24}),
                     _ev("area_changed", 1000, {"level": 24, "from": 1, "to": 1}),
                     _ev("practice_reset", 1047, {"igt_frames_before": 300, "mario_acted": True, "area": 1,
                                                  "prev_area": 1, "area_load": False, "teleport": False,
                                                  "loads_journaled": True}),
                     _ev("star_collected", 1500, {"course_id": 2, "star_id": 1, "igt_frames": 440,
                                                  "igt_timed_at": "xcam"})):
            await svc.publish(step)
        while svc.auto_pb_tasks:
            await asyncio.gather(*list(svc.auto_pb_tasks))
    try:
        asyncio.run(scenario())
        rows = db.pbs()
        assert [(row["frames"], row["game_version"]) for row in rows] == [(440, "jp")]
    finally:
        db.close()


# ---- what grading follows ---------------------------------------------------

class Recorder(Broadcaster):
    def __init__(self):
        super().__init__()
        self.events = []

    async def publish(self, event):
        self.events.append(event)
        return await super().publish(event)


def test_auto_grading_follows_the_running_cartridge_live(store):
    broadcaster = Recorder()
    svc = TrackerService(None, broadcaster, store)
    regrades = []
    svc.on_regrade = lambda: regrades.append(store.grading_version)
    assert store.grading_version == "us"
    out = asyncio.run(svc.note_running_version("jp"))
    assert out == {"setting": "auto", "effective": "jp"} and store.grading_version == "jp"
    assert regrades == ["jp"] and [event.type for event in broadcaster.events] == ["game_version_changed"]
    # Detaching the emulator keeps the last ROM; re-noting it changes nothing.
    assert asyncio.run(svc.note_running_version(None)) is None
    assert asyncio.run(svc.note_running_version("jp")) is None
    assert store.grading_version == "jp" and regrades == ["jp"]


def test_an_explicit_setting_wins_over_the_cartridge(store):
    svc = TrackerService(None, Recorder(), store)
    asyncio.run(svc.set_game_version(ModeConfig(version=GameVersion.US)))
    assert asyncio.run(svc.note_running_version("jp")) is None
    assert store.grading_version == "us"
    # Back to Auto-detect: the cartridge already seen decides at once.
    asyncio.run(svc.set_game_version(ModeConfig(version=GameVersion.AUTO)))
    assert store.grading_version == "jp"


def test_the_mode_api_resolves_auto_on_the_loaded_rom(tmp_path, store):
    svc = TrackerService(None, Recorder(), store)
    svc.observed_game_version = lambda: "jp"
    app = FastAPI()
    app.include_router(create_mode_router(svc, mode_path=tmp_path / "tracker_mode.json"))
    assert svc.on_regrade is not None, "a cartridge change absorbs MARELO like a setting flip"
    with TestClient(app) as client:
        assert client.get("/api/mode").json()["effective"] == "jp"
        assert client.put("/api/mode", json={"version": "us"}).json()["effective"] == "us"
        assert store.grading_version == "us"
        assert client.put("/api/mode", json={"version": "auto"}).json()["effective"] == "jp"
        assert store.grading_version == "jp"
