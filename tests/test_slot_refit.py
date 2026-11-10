"""Slot refit after a spawn: which slots move to Mario's new place, which stay untouched, and when it runs.

Fake memory only; no process. The relocation itself is pinned in test_slot_relocate.py; here it is the step that
picks slots, refuses, keeps his saves, and the trigger that runs it once per spawn.
"""
from __future__ import annotations

import json
import re
import struct
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools"))
import test_slot_library as libfakes  # noqa: E402
import test_slot_relocate as world  # noqa: E402
import what_happened  # noqa: E402
from sm64_events.server.broadcaster import Broadcaster  # noqa: E402
from sm64_events.slotlibrary import refit, usamune  # noqa: E402
from sm64_events.slotlibrary import relocate as R  # noqa: E402
from sm64_events.slotlibrary.store import SlotLibrary, state_id  # noqa: E402

HERE, ELSEWHERE = (libfakes.LEVEL, libfakes.AREA), (24, 1)
FITTED = R.relocate(world.make_slot(), [(world.SLOT_MARIO, world.LIVE)], usamune.US).data


def game_with(slots: dict[int, bytes], *, live: int = world.LIVE, cls=libfakes.FakeGame):
    game = cls()
    world.live_world(game, live=live)
    for index, data in slots.items():
        game.save(index, data)
    return game


def writes_into(game, index: int) -> list[int]:
    low, high = usamune.slot_range(index)
    return [address for address, _data in game.writes if low <= address < high]


def outcomes(report) -> dict[int, tuple[str, str]]:
    return {slot.slot: (slot.outcome, slot.why) for slot in report.slots}


# ---- one pass ---------------------------------------------------------------------------------------------

def test_a_moved_mario_refits_this_places_slots_and_leaves_other_places_alone():
    other = world.make_slot(level=ELSEWHERE[0], area=ELSEWHERE[1])
    game = game_with({0: world.make_slot(), 2: world.make_slot(), 5: other})
    report = refit.refit_slots(game)
    assert outcomes(report) == {0: (refit.REFIT, ""), 2: (refit.REFIT, "")}
    assert game.slot(0) == FITTED and game.slot(2) == FITTED
    facts, live = usamune.slot_facts(game.slot(0)), usamune.live_facts(game, usamune.US)
    assert facts == live, "Usamune's load gate now sees a match"
    assert game.slot(5) == other and writes_into(game, 5) == []
    assert all(writes_into(game, index) == [] for index in (1, 3, 4, 6, 7, 8))


def test_a_slot_already_in_place_is_not_written():
    game = game_with({1: world.make_slot()}, live=world.SLOT_MARIO)
    report = refit.refit_slots(game)
    assert outcomes(report) == {1: (refit.ALREADY_FIT, "")} and game.writes == []


class SavesDuringTheRefit(libfakes.FakeGame):
    """Usamune saves into slot 0 right after the refit's first read of it, before the write."""
    new_save = world.make_slot()[:-8] + b"\x01" * 8   # another save of the same place

    def __init__(self):
        super().__init__()
        self.slot_reads = 0

    def read(self, address: int, size: int) -> bytes:
        data = super().read(address, size)
        if address == usamune.slot_address(0) and size == usamune.SLOT_BYTES:
            self.slot_reads += 1
            if self.slot_reads == 1:
                self.save(0, self.new_save)
        return data


def test_a_slot_he_saves_over_between_the_read_and_the_write_is_skipped():
    game = game_with({0: world.make_slot(), 3: world.make_slot()}, cls=SavesDuringTheRefit)
    report = refit.refit_slots(game)
    assert outcomes(report)[0] == (refit.CHANGED, "")
    assert game.slot(0) == SavesDuringTheRefit.new_save and writes_into(game, 0) == []
    assert outcomes(report)[3] == (refit.REFIT, ""), "the other slots still refit"


def test_a_slot_with_a_word_nothing_explains_is_refused_without_a_write():
    unclear = bytearray(world.make_slot())
    world.put(unclear, world.at(world.OTHER_PLACE, 0x20), world.SLOT_MARIO)
    game = game_with({4: bytes(unclear)})
    report = refit.refit_slots(game)
    assert outcomes(report) == {4: (refit.REFUSED, refit.UNCLEAR)}
    assert game.writes == [] and game.slot(4) == bytes(unclear)


def test_no_mario_in_a_level_writes_nothing():
    game = game_with({0: world.make_slot()}, live=0)
    report = refit.refit_slots(game)
    assert report.not_ready and report.slots == () and game.writes == []


def test_mario_moving_during_a_write_stops_the_pass_and_his_save_is_still_kept(tmp_path):
    game = game_with({0: world.make_slot(), 1: world.make_slot()})

    def moves(address, _data):
        if address == usamune.slot_address(0) + usamune.TRAILER_MARIO + 4:
            game.poke(usamune.US.mario_object, struct.pack(">I", world.OBJ(77)))
    game.on_write = moves
    library = SlotLibrary(tmp_path / "library")
    report = refit.SlotRefitter(lambda: game, library, steady_seconds=0).run_once()
    assert outcomes(report) == {0: (refit.REFUSED, refit.MOVED)}, "slot 1 is not tried for the old place"
    assert game.read(usamune.slot_address(0) + usamune.TRAILER_MARIO, 4) == bytes(4)
    assert [state.id for state in library.scan().states] == [state_id(world.make_slot())]


# ---- what it keeps and records ----------------------------------------------------------------------------

def test_his_save_is_kept_once_and_resets_add_no_files(tmp_path):
    game = game_with({0: world.make_slot(), 1: world.make_slot()})
    library = SlotLibrary(tmp_path / "library")
    refitter = refit.SlotRefitter(lambda: game, library, steady_seconds=0)
    refitter.run_once()
    assert len(library.scan().states) == 1, "two slots of one save are one kept state"
    for place in (90, 50, 140):                       # three more resets, each moving Mario
        world.live_world(game, live=world.OBJ(place))
        report = refitter.run_once()
        assert report.count(refit.REFIT) == 2
    assert len(library.scan().states) == 1
    game.save(1, SavesDuringTheRefit.new_save)        # a new save of his
    world.live_world(game, live=world.OBJ(60))
    refitter.run_once()
    assert len(library.scan().states) == 2


def test_each_pass_records_one_line_with_counts_and_no_addresses(tmp_path):
    other = world.make_slot(level=ELSEWHERE[0], area=ELSEWHERE[1])
    game = game_with({0: world.make_slot(), 5: other})
    log_file = tmp_path / "data" / refit.LOG_NAME
    refitter = refit.SlotRefitter(lambda: game, None, log_file=log_file, steady_seconds=0)
    refitter.run_once(("practice_reset", "spawned"), 4321)
    lines = log_file.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[0])
    assert len(lines) == 1 and entry["refit"] == 1 and entry["already_fit"] == 0 and entry["frame"] == 4321
    assert entry["triggers"] == ["practice_reset", "spawned"] and (entry["level"], entry["area"]) == HERE
    assert not re.search(r"80[0-9a-f]{6}", lines[0], re.I), "no addresses"
    rows = what_happened.refit_rows(tmp_path / "data" / "tracker.db", None)
    assert [row[2] for row in rows] == ["TRAINER slot refit"]
    assert "1 refit" in rows[0][3] and "level 9" not in rows[0][3]


def test_the_info_line_names_the_place_in_words():
    report = refit.RefitReport(9, 1, (refit.SlotOutcome(0, refit.REFIT),))
    line = refit.describe(report)
    assert "Bob-omb Battlefield" in line and "level 9" not in line and "1 refit" in line


# ---- the trigger ------------------------------------------------------------------------------------------

def event(kind: str, frame: int = 100):
    return SimpleNamespace(type=kind, frame=frame, timestamp_utc=datetime.now(UTC), payload={})


def test_one_resets_burst_of_events_runs_one_pass(tmp_path):
    game = game_with({0: world.make_slot()})
    done, reports = threading.Event(), []

    def reported(report):
        reports.append(report)
        done.set()
    refitter = refit.SlotRefitter(lambda: game, None, settle_seconds=0.1, steady_seconds=0, on_report=reported,
                                  log_file=tmp_path / refit.LOG_NAME)
    refitter.start()
    try:
        for kind in ("practice_reset", "star_collected", "area_changed", "spawned"):
            refitter.observe(event(kind))
            time.sleep(0.04)                          # a reset's events arrive a frame or two apart
        assert done.wait(5)
        time.sleep(0.4)
        assert len(reports) == 1 and reports[0].count(refit.REFIT) == 1
        assert json.loads((tmp_path / refit.LOG_NAME).read_text(encoding="utf-8"))["triggers"] == [
            "practice_reset", "area_changed", "spawned"]
        done.clear()
        world.live_world(game, live=world.OBJ(90))
        refitter.observe(event("spawned", 200))
        assert done.wait(5) and len(reports) == 2 and reports[1].count(refit.REFIT) == 1
        refitter.observe(event("star_collected"))
        time.sleep(0.3)
        assert len(reports) == 2, "an event that is no spawn runs nothing"
    finally:
        refitter.stop()
    assert not refitter._thread.is_alive()


def test_the_lab_off_never_follows_spawns(monkeypatch):
    import sm64_events.main as main
    monkeypatch.delenv("SM64_SCENE_LAB", raising=False)
    tracker = SimpleNamespace(on_replay_history=None, broadcaster=Broadcaster())
    app = main._mount_scene_lab(FastAPI(), tracker=tracker)
    assert tracker.broadcaster._observers == set() and getattr(app.state, "scene_lab", None) is None
