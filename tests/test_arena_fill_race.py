"""The arena's fight keeps the hand whatever order its two writers land in.

Live, 2026-09-28 (journal ids 6417-6431): *"I just got a gold for Bowser 1,
but the ghost wasn't available to be used. Once I triggered another
successful attempt, THEN the ghost appeared."* The hand held the BitDW reds
movement, hooked by the Bowser course row's fill; he warped straight into
the Bowser 1 arena. Two writers then touched the hand in the same second:

- the arena row's fill for the armed fight (`target_set {auto}`), and
- the arena's own practice reset, which forfeits a hooked head carried in
  from a foreign course.

Fill first, and the projector refused it (a hooked head held its turn), then
the reset emptied the hand -- nothing selected for the whole fight, so the
ghost had no target, until the key grab followed onto the fight. Reset
first, and the fill found an empty hand and landed. The same run, two
outcomes.

Replayed against the REAL corpus (the fight is a presence arm and the reds
movement arrives by course entry, which is exactly what makes this reachable)
for all three arenas, in both orders.
"""
import dataclasses
import json

import pytest

from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.defaults import reconcile_defaults
from sm64_events.tracking.projection import Projector
from sm64_events.tracking.segments import SegmentDef

W = "2026-09-28T23:54:27Z"
LEVEL_CASTLE = 6

# (Bowser course level, its arena level, the course's abbreviation, which Bowser)
STAGES = [(17, 30, "bitdw", 1), (19, 33, "bitfs", 2), (21, 34, "bits", 3)]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory):
    db = Database(tmp_path_factory.mktemp("arena") / "t.db")
    seed = json.loads(bundled_defaults_seed().read_bytes().decode("utf-8"))
    assert reconcile_defaults(db, seed) == []
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    rows = db.segment_defs()
    # ids differ per install, so tests name rows by seed key
    return ([SegmentDef(**{k: row[k] for k in keys}) for row in rows],
            {row["seed_key"]: row["id"] for row in rows})


def reset(id, frame, acted):
    # The shape of journal id 6425: Usamune's own reset on the arrival.
    return EventRow(id=id, session_id=1, seq=id, type="practice_reset",
                    frame=frame, wall_time_utc=W,
                    payload={"igt_frames_before": 27, "mario_acted": acted,
                             "acted_tracking": True, "area": 1,
                             "prev_area": 1, "area_load": False,
                             "teleport": False})


def ev(id, type, frame, payload):
    return EventRow(id=id, session_id=1, seq=id, type=type, frame=frame,
                    wall_time_utc=W, payload=payload)


def into_the_arena(defs, ids, course_level, arena, abbrev):
    """Castle -> the Bowser course, whose row fills the reds movement (it
    hooks), then straight into the arena by the level select, which
    cancels the reds arm without completing it."""
    reds = ids[f"seg:reds->pipe:{abbrev}"]
    p = Projector(segments=defs)
    p.feed(ev(1, "level_changed", 49470, {"from": LEVEL_CASTLE,
                                          "to": course_level}))
    p.feed(ev(2, "area_changed", 49470, {"level": course_level, "from": 1,
                                         "to": 1}))
    p.feed(ev(3, "target_set", 0, {"kind": "segment", "segment_id": reds,
                                   "auto": True}))
    p.feed(reset(4, 49471, True))
    p.feed(ev(5, "level_changed", 49517, {"from": course_level,
                                          "to": arena}))
    p.feed(ev(6, "area_changed", 49517, {"level": arena, "from": 1,
                                         "to": 1}))
    assert p.target == ("segment", reds) and p._target_hooked, \
        "precondition: a disarmed reds head carried into the arena"
    assert reds not in p.armed_segment_ids()
    return p


@pytest.mark.parametrize("course_level,arena,abbrev,bowser", STAGES)
@pytest.mark.parametrize("fill_first", [True, False],
                         ids=["fill-then-reset", "reset-then-fill"])
def test_the_arena_fight_holds_the_hand_in_either_order(
        corpus, course_level, arena, abbrev, bowser, fill_first):
    defs, ids = corpus
    fight = ids[f"seg:bowser-{bowser}"]
    p = into_the_arena(defs, ids, course_level, arena, abbrev)
    assert fight in p.armed_segment_ids()
    fill = ev(7, "target_set", 0, {"kind": "segment", "segment_id": fight,
                                   "auto": True})
    writers = [fill, reset(8, 49519, False)]
    for writer in writers if fill_first else reversed(writers):
        p.feed(writer)
    assert p.target == ("segment", fight), \
        "the fight must be in hand for the attempt, or the ghost has no target"

    # The hand holding it is what makes the fight's own resets RECORD: an
    # untargeted segment failure is dropped, so every reset he made while
    # the hand sat empty would have gone unrecorded.
    p.feed(ev(9, "mario_acted", 49556, {"action": 536875782}))
    closed = p.feed(reset(10, 49600, True))
    assert any(a.segment_id == fight and a.outcome == "reset" for a in closed)
    assert p.target == ("segment", fight)

    p.feed(ev(11, "mario_acted", 49640, {"action": 536875782}))
    closed = p.feed(ev(12, "key_grabbed", 50343, {"level": arena,
                                                  "igt_frames": 828}))
    assert any(a.segment_id == fight and a.outcome == "success"
               for a in closed)
    assert p.target == ("segment", fight)


def test_a_live_hooked_head_still_refuses_the_fill(corpus):
    """The yield is for a STALE head only. One still armed is a run in
    progress, and round 19's hold applies unchanged -- even against a fill
    that is armed too (No Reds arms beside Reds on the same course entry)."""
    defs, ids = corpus
    reds, no_reds = ids["seg:reds->pipe:bitdw"], ids["seg:bitdw-pipe"]
    p = Projector(segments=defs)
    p.feed(ev(1, "level_changed", 100, {"from": LEVEL_CASTLE, "to": 17}))
    p.feed(ev(2, "area_changed", 100, {"level": 17, "from": 1, "to": 1}))
    p.feed(ev(3, "target_set", 0, {"kind": "segment", "segment_id": reds,
                                   "auto": True}))
    assert {reds, no_reds} <= p.armed_segment_ids()
    p.feed(ev(4, "target_set", 0, {"kind": "segment", "segment_id": no_reds,
                                   "auto": True}))
    assert p.target == ("segment", reds)
