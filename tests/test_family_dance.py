"""The star owns the hand (round 21, 2026-08-08 — superseding round 20's
family-take the same day, off his first live session on it).

His corrections, verbatim: "The STAR's practice log should be prioritized /
it should always be prioritized over specific subsection... The subsections
are still ongoing and still get entries, but the main star that they're a
part of should be the priority. The EXCEPTION would be if the user manually
selects a subsection... Basically, explicit user choices take priority."
And on arrival: "if I load into LLL, I would expect nothing to be selected
by default (even though the Volcano Entry subsection triggers), because how
do you know for sure I'm going to practice anything? You don't."
And the pick edge, answered directly: "'picked piece should survive its
parent's grab' is probably the right approach."

The rules pinned here:

- a piece ARMING never takes the hand — the star stays lit and the piece
  records underneath (round 20's take is gone, its anchor machinery with
  it);
- a piece never hooks or queues, whatever its clause shape — a subsection
  is selected by CLICK only;
- a piece's SUCCESS follows onto its PARENT star (the one family-shaped
  move detection may make: it points at the whole, never the piece) —
  item 1's "swap to the correct list";
- a PICKED piece survives everything short of its own invalidation,
  including its parent star's grab;
- a HOOKED piece (an auto fill) still yields to its parent's grab.

WF (level 24, course 2) hosts the fixtures; the pieces start on
`area_enter`, the presence-typed shape a real recorded subsection carries.
"""

from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import Projector, target_entity_key
from sm64_events.tracking.segments import SegmentDef

W = "2026-08-08T12:00:00Z"
STAR = ("star", 2, 1)          # a WF star
STAR_KEY = "star:2:1"


def jev(id, type, frame, payload=None, session_id=1):
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def piece(id, into_area, out_area, parents=(STAR_KEY,)):
    """A recorded subsection: strict, presence-armed by entering one
    subarea, ended by reaching the next."""
    return SegmentDef(id=id, name=f"piece {id}", enabled=True,
                      start_triggers=[{"type": "area_enter", "level": 24,
                                       "area": into_area}],
                      end_triggers=[{"type": "area_enter", "level": 24,
                                     "area": out_area}],
                      guards=[], match_mode="strict",
                      parents=list(parents))


def deliberate_piece(id, parents=(STAR_KEY,)):
    """A subsection whose start is a DELIBERATE clause shape (level exit) —
    the shape round 19 lets hook. Being a piece must override that."""
    return SegmentDef(id=id, name=f"deliberate piece {id}", enabled=True,
                      start_triggers=[{"type": "level_exit", "from": 24}],
                      end_triggers=[{"type": "level_enter", "to": 24}],
                      guards=[], match_mode="strict", parents=list(parents))


def start(p, star=STAR):
    """Select a WF star and stand in WF area 1."""
    p.feed(jev(1, "target_set", 0, {"course_id": star[1], "star_id": star[2]}))
    p.feed(jev(2, "level_changed", 900, {"from": 16, "to": 24}))
    p.feed(jev(3, "area_changed", 900, {"level": 24, "from": 1, "to": 1}))


def enter_area(p, id, frame, from_area, to_area):
    p.feed(jev(id, "area_changed", frame,
               {"level": 24, "from": from_area, "to": to_area}))


def test_the_key_helper_speaks_the_parents_format():
    assert target_entity_key(("star", 2, 1)) == "star:2:1"
    assert target_entity_key(("segment", 7)) == "segment:7"
    assert target_entity_key(None) is None


# -- detection never selects a piece ----------------------------------------

def test_a_piece_arming_leaves_its_parent_star_lit():
    """Image 30's correction exactly: the STAR keeps the highlight while
    its piece runs underneath."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    enter_area(p, 4, 1000, 1, 2)          # the piece's start fires
    assert 41 in p.armed_segment_ids()    # still ongoing, still recording
    assert p.target == STAR               # and NOT selected


def test_a_piece_arming_takes_nothing_from_an_empty_hand():
    """"How do you know for sure I'm going to practice anything? You
    don't." — arrival detection selects nothing."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 24}))
    p.feed(jev(2, "area_changed", 900, {"level": 24, "from": 1, "to": 1}))
    enter_area(p, 3, 1000, 1, 2)
    assert 41 in p.armed_segment_ids()
    assert p.target is None


def test_even_a_deliberately_armed_piece_never_hooks_or_queues():
    """Round 19 lets a level-exit arm hook an empty hand; being a PIECE
    overrides that — a subsection is selected by click only."""
    p = Projector(segments=[deliberate_piece(43)])
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 24}))
    p.feed(jev(2, "level_changed", 1000, {"from": 24, "to": 6}))  # start fires
    assert 43 in p.armed_segment_ids()
    assert p.target is None
    assert p.target_queue() == []


# -- a piece's success speaks through its parent ----------------------------

def test_a_piece_completing_keeps_its_parent_star_as_the_target():
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    enter_area(p, 4, 1000, 1, 2)
    enter_area(p, 5, 1500, 2, 3)          # the piece completes
    assert p.target == STAR


def test_a_piece_completing_on_an_empty_hand_selects_its_parent_star():
    """Item 1's swap: the completed piece's family takes the row — with the
    STAR active, never the piece itself."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    p.feed(jev(1, "level_changed", 900, {"from": 16, "to": 24}))
    p.feed(jev(2, "area_changed", 900, {"level": 24, "from": 1, "to": 1}))
    enter_area(p, 3, 1000, 1, 2)
    enter_area(p, 4, 1500, 2, 3)
    assert p.target == STAR


def test_a_piece_completing_swaps_the_hand_off_a_different_star():
    """His 8-Coin case in reverse: practicing star A, completing a piece of
    star B moves the hand to STAR B (the correct list), not to the piece."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3,
                                  parents=("star:2:0",))])
    start(p)                               # target star:2:1
    enter_area(p, 4, 1000, 1, 2)
    enter_area(p, 5, 1500, 2, 3)
    assert p.target == ("star", 2, 0)


def test_a_shared_piece_keeps_the_parent_he_is_practicing():
    """Two parents, one of them already the target: the current one wins
    over the primary."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3,
                                  parents=("star:2:0", STAR_KEY))])
    start(p)                               # target star:2:1 (the SECOND parent)
    enter_area(p, 4, 1000, 1, 2)
    enter_area(p, 5, 1500, 2, 3)
    assert p.target == STAR


def test_a_top_level_segments_success_still_follows_onto_itself():
    """The pre-existing auto-follow is untouched for everything that is not
    a piece."""
    loner = SegmentDef(id=9, name="loner", enabled=True,
                       start_triggers=[{"type": "area_enter", "level": 24,
                                        "area": 2}],
                       end_triggers=[{"type": "area_enter", "level": 24,
                                      "area": 3}],
                       guards=[], match_mode="strict")
    p = Projector(segments=[loner])
    start(p)
    enter_area(p, 4, 1000, 1, 2)
    enter_area(p, 5, 1500, 2, 3)
    assert p.target == ("segment", 9)


# -- explicit user choices take priority ------------------------------------

def test_a_picked_piece_survives_its_parent_stars_grab():
    """His call, verbatim: "'picked piece should survive its parent's grab'
    is probably the right approach." """
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    p.feed(jev(4, "target_set", 0, {"kind": "segment", "segment_id": 41}))
    enter_area(p, 5, 1000, 1, 2)           # the pick's own def arms
    p.feed(jev(6, "star_collected", 2000,
               {"course_id": 2, "star_id": 1, "igt_frames": 900}))
    assert p.target == ("segment", 41)


def test_a_picked_piece_survives_a_sibling_completing():
    first = piece(41, into_area=2, out_area=3)
    second = piece(42, into_area=3, out_area=4)
    p = Projector(segments=[first, second])
    start(p)
    p.feed(jev(4, "target_set", 0, {"kind": "segment", "segment_id": 41}))
    enter_area(p, 5, 1000, 1, 3)           # sibling's start
    enter_area(p, 6, 1500, 3, 4)           # sibling completes
    assert p.target == ("segment", 41)


def test_a_hooked_piece_still_yields_to_its_parents_grab():
    """An AUTO fill is detection-flavored (round 19), and a detection-held
    piece yields to the whole — the surviving half of round 20's rule."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    p.feed(jev(4, "target_set", 0, {"kind": "segment", "segment_id": 41,
                                    "auto": True}))
    assert p.target == ("segment", 41)
    p.feed(jev(5, "star_collected", 2000,
               {"course_id": 2, "star_id": 1, "igt_frames": 900}))
    assert p.target == STAR
