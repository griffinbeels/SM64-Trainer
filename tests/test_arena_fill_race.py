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


# -- the arrival area settling (2026-09-29) --------------------------------
#
# Live, 2026-09-29 (journal ids 7416-7434): *"the bowser stage doesn't
# immediately detect the Ghost? ... I clearly have a PB here, but I'm being
# told that the ghost overlay can't be found yet?"* The fill landed this time
# (the stale head yielded, above), and the hand emptied anyway, one event
# BEFORE the arrival reset: an arrival from outside an area 1 lands in that
# area and settles to area 1 two frames later, which disarms the fight until
# the reset re-arms it on the same frame. The fill had counted its own
# journaled frame 0 as a sighting, so that disarm measured 24,547 frames of
# staleness and expired it. The page re-offers only when it sees the hand
# holding the fight and then not, and 107 ms was too short to paint it.
# Every arrival above is from an area 1, which is why they never saw it.


def arena_reset(id, frame, *, acted, paused, prev_area):
    # The shape of journal ids 7425 and 7428, verbatim where it matters.
    return EventRow(id=id, session_id=1, seq=id, type="practice_reset",
                    frame=frame, wall_time_utc=W,
                    payload={"igt_frames_before": 123, "mario_acted": acted,
                             "paused_frames_before": paused,
                             "acted_tracking": True, "action": 6436,
                             "save_pending": False, "warp_op": 18,
                             "frames_since_warp_op": 1146, "area": 1,
                             "prev_area": prev_area, "area_load": False,
                             "teleport": False, "loads_journaled": True})


def test_the_bowser_2_fill_outlives_the_arrival_area_settling(corpus):
    """His run, event for event: a BitFS pipe entry hooked by the course
    row's fill, a lava death warp to the castle, a level select from
    upstairs (area 3) into the Bowser 2 arena, the fill, the settle, the
    reset, and a 33"13 key. A live clock tick between the settle and the
    reset must not expire the fill either."""
    defs, ids = corpus
    pipe, fight = ids["seg:bitfs-pipe"], ids["seg:bowser-2"]
    p = Projector(segments=defs)
    p.feed(ev(7351, "level_changed", 18798, {"from": 6, "to": 19,
                                             "from_area": 3}))
    p.feed(ev(7352, "area_changed", 18798, {"level": 19, "from": 3, "to": 1,
                                            "from_transient": True}))
    p.feed(arena_reset(7353, 18798, acted=True, paused=99, prev_area=3))
    p.feed(ev(7355, "target_set", 0, {"kind": "segment", "segment_id": pipe,
                                      "auto": True}))
    p.feed(ev(7413, "jump", 22704, {"level": 19, "kind": "triple"}))
    assert p.target == ("segment", pipe) and p._target_hooked
    p.feed(ev(7414, "death", 23351, {"cause": "lava", "level": 19}))
    p.feed(ev(7415, "warp_entered", 23396, {"level": 19, "area": 1, "to": 6,
                                            "mario_alive": False}))
    p.feed(ev(7416, "level_changed", 23401, {"from": 19, "to": 6,
                                             "from_area": 1}))
    p.feed(ev(7417, "area_changed", 23401, {"level": 6, "from": 1, "to": 1,
                                            "from_transient": True}))
    p.feed(ev(7418, "area_changed", 23402, {"level": 6, "from": 1, "to": 3}))
    p.feed(ev(7420, "mario_acted", 23444, {"action": 132192}))
    assert p.target == ("segment", pipe) and pipe not in p.armed_segment_ids(), \
        "precondition: a stale hooked head carried to the level select"

    p.feed(ev(7421, "level_changed", 24545, {"from": 6, "to": 33,
                                             "from_area": 3}))
    p.feed(ev(7422, "area_changed", 24545, {"level": 33, "from": 3, "to": 3,
                                            "from_transient": True}))
    assert fight in p.armed_segment_ids()
    p.feed(ev(7423, "target_set", 0, {"kind": "segment", "segment_id": fight,
                                      "auto": True}))
    assert p.target == ("segment", fight)
    p.feed(ev(7424, "area_changed", 24547, {"level": 33, "from": 3, "to": 1}))
    assert fight not in p.armed_segment_ids(), \
        "precondition: the settle disarms the fight until the reset"
    p.settle(24547)
    assert p.target == ("segment", fight), \
        "the settle emptied the hand: the ghost has no target for the fight"
    p.feed(arena_reset(7425, 24547, acted=True, paused=1022, prev_area=3))
    p.feed(ev(7426, "spawned", 24547, {"level": 33, "kind": "spawn",
                                       "area": 1}))
    assert fight in p.armed_segment_ids()
    assert p.target == ("segment", fight)

    p.feed(ev(7427, "moment_reached", 24592, {"kind": "textbox", "level": 33,
                                              "area": 1}))
    p.feed(arena_reset(7428, 25006, acted=False, paused=0, prev_area=1))
    p.feed(ev(7431, "mario_acted", 26981, {"action": 8651858}))
    assert p.target == ("segment", fight)
    closed = p.feed(ev(7434, "key_grabbed", 27677, {"level": 33,
                                                    "which": "bitfs",
                                                    "igt_frames": 994}))
    assert any(a.segment_id == fight and a.outcome == "success"
               for a in closed)
    assert p.target == ("segment", fight)


@pytest.mark.parametrize("arena,bowser", [(30, 1), (33, 2), (34, 3)])
@pytest.mark.parametrize("fill_first", [True, False],
                         ids=["fill-then-settle", "settle-then-fill"])
def test_every_arena_keeps_its_fill_through_a_settling_arrival(
        corpus, arena, bowser, fill_first):
    """The same arrival, empty-handed, into each arena. The fill lands
    before the settle and the reset or after both, never between (one poll
    carries the pair), and the fight holds the hand either way."""
    defs, ids = corpus
    fight = ids[f"seg:bowser-{bowser}"]
    p = Projector(segments=defs)
    p.feed(ev(1, "level_changed", 23401, {"from": 19, "to": LEVEL_CASTLE,
                                          "from_area": 1}))
    p.feed(ev(2, "area_changed", 23402, {"level": LEVEL_CASTLE, "from": 1,
                                         "to": 3}))
    p.feed(ev(3, "level_changed", 24545, {"from": LEVEL_CASTLE, "to": arena,
                                          "from_area": 3}))
    p.feed(ev(4, "area_changed", 24545, {"level": arena, "from": 3, "to": 3,
                                         "from_transient": True}))
    fill = [ev(5, "target_set", 0, {"kind": "segment", "segment_id": fight,
                                    "auto": True})]
    arrival = [ev(6, "area_changed", 24547, {"level": arena, "from": 3,
                                             "to": 1}),
               arena_reset(7, 24547, acted=True, paused=1022, prev_area=3)]
    for writer in fill + arrival if fill_first else arrival + fill:
        p.feed(writer)
    assert fight in p.armed_segment_ids()
    assert p.target == ("segment", fight)
