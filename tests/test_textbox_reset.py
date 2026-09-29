"""A reset taken during any textbox splits the run once Mario respawns.

His rule, 2026-09-28: every reset splits the run, and "When we reset, ghost
mario resets." A reset during Bowser's speech already shows it on its own poll
(Mario is back in a spawn action; `test_bowser_intro.py`). One taken during
a course's NPC textbox can land a poll early, the action byte still reading
the textbox; the respawn reaches it on the same frame or up to two later.
The engine holds such a reset and lets `spawned` confirm it. The fresh-file
intro's own counter zero never respawns Mario, so it stays invisible.

Replayed from his journal: session 29's Bob-omb Battlefield, ids 5596-5623,
a reset during the pink Bob-omb's textbox 40 s into "Shoot to the Island in
the Sky" while the 100-coin engine was running.
"""
import dataclasses
import json

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.defaults import reconcile_defaults
from sm64_events.tracking.projection import Projector
from sm64_events.tracking.segments import MatchContext, SegmentDef, SegmentEngine

W = "2026-09-28T23:40:00Z"
NPC_DIALOG, WALKING, IDLE = 536875782, 67109952, 0x0C400201


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("textbox") / "t.db")
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    rows = db.segment_defs()
    return ([SegmentDef(**{k: row[k] for k in keys}) for row in rows],
            {row["seed_key"]: row["id"] for row in rows})


def reset(frame, before, action, prev, dialog):
    return ("practice_reset", frame, {
        "igt_frames_before": before, "mario_acted": True,
        "paused_frames_before": 0, "acted_tracking": True, "action": action,
        "prev_action": prev, "save_pending": False, "frames_since_door": None,
        "frames_since_dialog": dialog, "area": 1, "area_load": False,
        "teleport": False, "loads_journaled": True})


def spawned(frame):
    return ("spawned", frame, {"level": 9, "kind": "spawn", "area": 1,
                               "igt_frames": 0, "igt_source": "spawn"})


BOB = [("level_changed", 21814, {"from": 22, "to": 9, "from_area": 1,
                                 "igt_frames": 57}),
       ("area_changed", 21814, {"level": 9, "from": 1, "to": 1, "igt_frames": 57}),
       reset(31834, 749, 4866, 4866, None), spawned(31835),
       ("mario_acted", 31874, {"action": WALKING}),
       reset(33041, 1207, NPC_DIALOG, NPC_DIALOG, 0),
       spawned(33043), ("mario_acted", 33081, {"action": WALKING})]


def feed(p, rows, first_id=5596):
    closed = []
    for offset, (type_, frame, payload) in enumerate(rows):
        jid = first_id + offset
        closed += p.feed(EventRow(id=jid, session_id=29, seq=jid, type=type_,
                                  frame=frame, wall_time_utc=W, payload=payload))
    return closed


def test_the_respawn_confirms_a_reset_taken_mid_textbox(corpus):
    defs, ids = corpus
    engine_id = next(i for key, i in ids.items()
                     if key and key.startswith("seg:100c->exit:") and key.endswith("bob"))
    p = Projector(segments=defs)
    feed(p, BOB[:6])                               # up to the reset itself
    assert p.armed_arms()[engine_id]["start_frame"] == 31834, "held, not yet decided"
    feed(p, BOB[6:], first_id=5596 + 6)
    assert p.armed_arms()[engine_id]["start_frame"] == 33041


def test_a_confirmed_reset_row_is_stamped_by_the_reset_not_the_spawn(corpus):
    """The row it closes names the reset's own payload: a 100-coin run's
    reset row has no in-game time, where the spawn that confirmed it reads 0."""
    defs, _ = corpus
    p = Projector(segments=defs)
    p.feed(EventRow(id=5595, session_id=29, seq=5595, type="target_set",
                    frame=0, wall_time_utc=W,
                    payload={"course_id": 1, "star_id": 6}))
    closed = feed(p, BOB)
    rows = [(a.course_id, a.star_id, a.outcome, a.igt_frames)
            for a in closed if a.anchor_frame == 31834]
    assert rows == [(1, 6, "reset", None)]


def test_the_intro_zero_is_held_and_then_dropped():
    """No respawn follows the fresh-file intro's zero: Mario walks on, and the
    Lakitu Skip run it would have split stays whole (primary journal ids
    16723-16726)."""
    lakitu = SegmentDef(id=1, name="Lakitu Skip", enabled=True,
                        start_triggers=[{"type": "spawned", "level": 16,
                                         "kind": "intro"}],
                        end_triggers=[{"type": "level_enter", "to": 6}],
                        guards=[])
    engine = SegmentEngine([lakitu])
    ctx = MatchContext(level=16, prev_level=16, num_stars=0, area=1)
    rows = [("spawned", 1861, {"level": 16, "kind": "intro"}),
            ("practice_reset", 1862, {"igt_frames_before": 1484,
                                      "mario_acted": True,
                                      "paused_frames_before": 0,
                                      "acted_tracking": True,
                                      "action": 0x04000440,
                                      "prev_action": IDLE,
                                      "frames_since_dialog": 1}),
            ("mario_acted", 1862, {"action": 0x04000440}),
            ("rollout", 1945, {"level": 16})]
    for jid, (type_, frame, payload) in enumerate(rows, start=16723):
        closed, _ = engine.feed(EventRow(id=jid, session_id=1, seq=jid, type=type_,
                                         frame=frame, wall_time_utc=W,
                                         payload=payload), ctx)
        assert closed == []
    assert engine.armed_items()[1].start_frame == 1861
