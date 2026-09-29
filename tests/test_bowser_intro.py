"""A Bowser fight's run starts at his last reset, intro included.

Every Bowser arena opens with Mario dropping in and Bowser speaking, about
six or seven seconds before he can play. His report, 2026-09-28: "we're not
actually detecting resets during the initial cutscene... this SHOULD count as
a reset for Ghost Mario", and a Bowser 3 row read 3'22"36 for a run Usamune
timed at 0'50"53 because "it included a ton of resets". The rule he stated:

- every reset SPLITS the run, whether or not he had acted;
- a split before he could act is HIDDEN from the practice log, but it still
  starts the next run's clock and the ghost fresh;
- opening a stage is not a reset ("I simply just opened the stage").

Each test replays his own journal rows (session 29, verbatim payloads trimmed
to the keys the engine reads) through the real projector and the real corpus.
"""
import dataclasses
import json

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.defaults import reconcile_defaults
from sm64_events.tracking.projection import Projector
from sm64_events.tracking.segments import SegmentDef

W = "2026-09-29T00:01:16Z"
NPC_DIALOG, SPAWN_SPIN = 536875782, 6436


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("bowser") / "t.db")
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    rows = db.segment_defs()
    return ([SegmentDef(**{k: row[k] for k in keys}) for row in rows],
            {row["seed_key"]: row["id"] for row in rows})


def reset(frame, before, acted, prev, paused=0, dialog=1):
    return ("practice_reset", frame, {
        "igt_frames_before": before, "mario_acted": acted,
        "paused_frames_before": paused, "acted_tracking": True,
        "action": SPAWN_SPIN, "prev_action": prev, "save_pending": False,
        "frames_since_door": 40000, "frames_since_dialog": dialog, "area": 1,
        "area_load": False, "teleport": False, "loads_journaled": True})


def spawned(frame, level):
    return ("spawned", frame, {"level": level, "kind": "spawn", "area": 1,
                               "igt_frames": 0, "igt_source": "spawn"})


def speech(frame):
    return ("mario_acted", frame, {"action": NPC_DIALOG})


def enter(frame, came_from, arena, igt):
    return [("level_changed", frame, {"from": came_from, "to": arena,
                                      "from_area": 1, "igt_frames": igt}),
            ("area_changed", frame, {"level": arena, "from": 1, "to": 1,
                                     "igt_frames": igt})]


def fill(fight):
    return ("target_set", 0, {"kind": "segment", "segment_id": fight,
                              "auto": True})


def key(frame, arena, igt, which):
    return ("key_grabbed", frame, {"level": arena, "which": which,
                                   "igt_frames": igt, "igt_source": "result"})


def replay(defs, rows, first_id):
    """Feed the rows, returning every closed attempt and, per row id, the
    frame the fight's ghost clock was running from after it."""
    p = Projector(segments=defs)
    p.feed(EventRow(id=first_id - 1, session_id=29, seq=0,
                    type="session_started", frame=0, wall_time_utc=W,
                    payload={"attempt_rules": 2}))
    closed, origins = [], {}
    for offset, (type_, frame, payload) in enumerate(rows):
        jid = first_id + offset
        closed += p.feed(EventRow(id=jid, session_id=29, seq=jid, type=type_,
                                  frame=frame, wall_time_utc=W,
                                  payload=payload))
        origins[jid] = _origin_frame(p)
    return closed, origins


def _origin_frame(p):
    if not p.target or p.target[0] != "segment":
        return None
    origin = p.native_ghost_origin("segment", str(p.target[1]))
    return origin and origin["frame"]


def rows_for(closed, fight):
    return [(a.outcome, a.rta_frames) for a in closed if a.segment_id == fight]


def test_bowser_3_times_the_run_after_his_last_intro_reset(corpus):
    """Journal ids 6497-6529. Opening Bowser 3 from Bowser 2, Usamune's own
    arrival zero two frames later, four resets during Bowser's speech and a
    fifth after a long pause, then the 0'50"53 run. Before: one row, 6071
    frames (3'22"36), plus a "reset 00"06" for the arrival."""
    defs, ids = corpus
    fight = ids["seg:bowser-3"]
    rows = [*enter(61751, 33, 34, 1086), fill(fight),
            reset(61753, 1085, True, 4866, paused=50, dialog=400),
            spawned(61753, 34), speech(61790),
            reset(63029, 67, True, NPC_DIALOG), spawned(63029, 34), speech(63068),
            reset(63141, 112, True, NPC_DIALOG), spawned(63141, 34), speech(63180),
            reset(63226, 85, True, NPC_DIALOG), spawned(63226, 34), speech(63265),
            reset(63382, 156, True, NPC_DIALOG), spawned(63382, 34), speech(63421),
            reset(66310, 130, True, NPC_DIALOG, paused=2798),
            spawned(66310, 34), speech(66348),
            key(67824, 34, 1516, "grand")]
    closed, origins = replay(defs, rows, 6497)
    assert rows_for(closed, fight) == [("success", 1516)]
    # The star side reads the same rule: no reset rows for the speech either
    # (journal rows 6500-6512 each closed one before).
    assert [a.outcome for a in closed if a.segment_id is None] == []
    # Every reset restarted the ghost's clock where Mario respawned.
    reset_ids = [6497 + i for i, row in enumerate(rows) if row[0] == "practice_reset"]
    assert [origins[j] for j in reset_ids] == [61753, 63029, 63141, 63226, 63382, 66310]


def test_bowser_2_resets_during_the_speech_start_the_run_over(corpus):
    """Journal ids 6463-6476: he warped from the Bowser 1 key straight into
    Bowser 2 and reset twice during the speech. Before: one 1713-frame row
    (57"10), saved as his PB, for a 1000-frame run."""
    defs, ids = corpus
    fight = ids["seg:bowser-2"]
    rows = [*enter(57472, 30, 33, 824), fill(fight),
            reset(57473, 823, True, NPC_DIALOG, paused=27),
            spawned(57473, 33), speech(57511),
            reset(58187, 715, True, NPC_DIALOG), spawned(58187, 33), speech(58226),
            key(59185, 33, 1000, "bitfs")]
    closed, origins = replay(defs, rows, 6463)
    assert rows_for(closed, fight) == [("success", 1000)]
    assert origins[6463 + 7] == 58187


def test_opening_bowser_1_is_not_a_reset(corpus):
    """Journal ids 6400-6411: in through the BitDW pipe, and Usamune zeroes
    its counter two frames after the arrival. The flag on that zero says he
    acted -- in BitDW. Before: a "reset 00"06" row nobody made."""
    defs, ids = corpus
    fight = ids["seg:bowser-1"]
    rows = [*enter(48448, 17, 30, 1779), fill(fight),
            reset(48450, 1778, True, 4864, paused=2, dialog=16086),
            spawned(48450, 30), speech(48487),
            key(49272, 30, 826, "bitdw")]
    closed, _ = replay(defs, rows, 6401)
    assert rows_for(closed, fight) == [("success", 826)]


def test_a_reset_after_he_could_play_still_records(corpus):
    """The hiding is for resets before control. Once the speech is over and
    he has moved, a reset is a failed fight and keeps its row."""
    defs, ids = corpus
    fight = ids["seg:bowser-3"]
    rows = [*enter(61751, 33, 34, 1086), fill(fight),
            reset(61753, 1085, True, 4866, paused=50, dialog=400),
            spawned(61753, 34), ("mario_acted", 61950, {"action": 0x04000440}),
            reset(62400, 647, True, 0x04000440, dialog=500), spawned(62400, 34)]
    closed, _ = replay(defs, rows, 6497)
    assert rows_for(closed, fight) == [("reset", 647)]


def test_the_intro_cutscene_zero_is_still_not_a_reset(corpus):
    """Shape 5's own case, which the respawn exception must not reach: the
    fresh-file intro ends (`spawned kind=intro`) and Usamune zeroes one frame
    later with Mario already walking -- no respawn, so still invisible
    (primary journal id 16724)."""
    from sm64_events.tracking.segments import SegmentEngine, MatchContext
    lakitu = SegmentDef(id=1, name="Lakitu Skip", enabled=True,
                        start_triggers=[{"type": "spawned", "level": 16,
                                         "kind": "intro"}],
                        end_triggers=[{"type": "level_enter", "to": 6}],
                        guards=[])
    engine = SegmentEngine([lakitu])
    ctx = MatchContext(level=16, prev_level=16, num_stars=0, area=1)
    engine.feed(EventRow(id=1, session_id=1, seq=1, type="spawned", frame=1861,
                         wall_time_utc=W, payload={"level": 16, "kind": "intro"}), ctx)
    armed = engine.armed_items()[1]
    closed, _ = engine.feed(EventRow(
        id=2, session_id=1, seq=2, type="practice_reset", frame=1862,
        wall_time_utc=W, payload={
            "igt_frames_before": 1484, "mario_acted": True,
            "paused_frames_before": 0, "acted_tracking": True,
            "action": 0x04000440, "prev_action": 0x0C400201,
            "frames_since_dialog": 1}), ctx)
    assert closed == [] and engine.armed_items()[1] == armed
