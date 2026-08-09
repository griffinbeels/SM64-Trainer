"""The family dance (round 20 items 2+4, 2026-08-08).

Built ON round 19's target queue, his own naming of the mechanism: "using the
queue system, the first subsection should be active, then the second
subsection that gets detected, etc, until we finally END on the actual star
being grabbed."

The rules, each pinned below:

- a def whose `parents` name the HELD target is a piece of the thing being
  practiced; its arming takes the hand (even presence-typed, which
  `hooks_on_arm` rightly refuses for everything else), and the displaced
  target waits as the FAMILY ANCHOR;
- a sibling arming while a piece holds the hand queues FIFO (round 19);
- a piece completing hands onward — a queued sibling first, else back to
  the anchor, so the whole never goes missing between pieces;
- a grab of the held piece's PARENT star always takes the hand — item 2,
  the whole subsumes the piece: "when we complete the star that's
  associated with a subsection... it should automatically re-select the
  STAR". His screenshot is the armed case exactly (Volcano Entry, a plain
  strict def with no star-grab cancel branch, stayed selected through the
  Hot-Foot-It grab);
- "When we detect the subsection again, then we can re-select the
  subsection" — the family take fires again on the next arm;
- an explicit pick, a foreign-course forfeit and a session boundary end
  the dance.

WF (level 24, course 2) hosts the fixtures: the star target is star:2:1 and
the pieces start on `area_enter`, the presence-typed shape a real recorded
subsection carries (Volcano Entry starts by entering the volcano).
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
    """A recorded subsection of the WF star: strict, presence-armed by
    entering one subarea, ended by reaching the next."""
    return SegmentDef(id=id, name=f"piece {id}", enabled=True,
                      start_triggers=[{"type": "area_enter", "level": 24,
                                       "area": into_area}],
                      end_triggers=[{"type": "area_enter", "level": 24,
                                     "area": out_area}],
                      guards=[], match_mode="strict",
                      parents=list(parents))


def start(p):
    """Select the WF star and stand in WF area 1."""
    p.feed(jev(1, "target_set", 0, {"course_id": 2, "star_id": 1}))
    p.feed(jev(2, "level_changed", 900, {"from": 16, "to": 24}))
    p.feed(jev(3, "area_changed", 900, {"level": 24, "from": 1, "to": 1}))


def enter_area(p, id, frame, from_area, to_area):
    p.feed(jev(id, "area_changed", frame,
               {"level": 24, "from": from_area, "to": to_area}))


def test_the_key_helper_speaks_the_parents_format():
    assert target_entity_key(("star", 2, 1)) == "star:2:1"
    assert target_entity_key(("segment", 7)) == "segment:7"
    assert target_entity_key(None) is None


def test_a_piece_arming_takes_the_hand_from_its_parent_star():
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    assert p.target == STAR
    enter_area(p, 4, 1000, 1, 2)          # the piece's start fires
    assert 41 in p.armed_segment_ids()
    assert p.target == ("segment", 41), (
        "item 4: the first subsection detected should be active")


def test_an_unrelated_presence_arm_still_takes_nothing():
    """The family bypass is scoped to the family: a presence-armed def with
    no parents keeps round 19's rule and never hooks."""
    loner = SegmentDef(id=9, name="loner", enabled=True,
                       start_triggers=[{"type": "area_enter", "level": 24,
                                        "area": 2}],
                       end_triggers=[{"type": "area_enter", "level": 24,
                                      "area": 3}],
                       guards=[], match_mode="strict")
    p = Projector(segments=[loner])
    start(p)
    enter_area(p, 4, 1000, 1, 2)
    assert 9 in p.armed_segment_ids()
    assert p.target == STAR


def test_the_parent_stars_grab_takes_the_hand_back():
    """Item 2, and the armed case is the one his screenshot shows: a plain
    strict piece has no star-grab cancel branch, so it is STILL ARMED when
    the grab lands — the pre-round-20 rule would bounce off it."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    enter_area(p, 4, 1000, 1, 2)
    assert p.target == ("segment", 41)
    p.feed(jev(5, "star_collected", 2000,
               {"course_id": 2, "star_id": 1, "igt_frames": 900}))
    assert p.target == STAR, "the whole subsumes the piece"


def test_a_grab_never_takes_the_hand_from_an_unrelated_picked_piece():
    """The round 19 protection stands outside the family: a PICKED segment
    that is still armed keeps the slot through a grab that is not its
    parent's."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3,
                                  parents=("star:2:0",))])
    start(p)                                # target star:2:1
    enter_area(p, 4, 1000, 1, 2)            # 41 arms; NOT a child of 2:1
    assert p.target == STAR                 # no take
    p.feed(jev(5, "target_set", 0, {"kind": "segment", "segment_id": 41}))
    p.feed(jev(6, "star_collected", 2000,
               {"course_id": 2, "star_id": 1, "igt_frames": 900}))
    assert p.target == ("segment", 41)


def test_a_completed_piece_hands_back_to_the_star():
    """No queued successor: the hand returns to the displaced parent rather
    than going neutral — the whole never goes missing between pieces."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    enter_area(p, 4, 1000, 1, 2)            # piece takes the hand
    enter_area(p, 5, 1500, 2, 3)            # piece completes
    assert p.target == STAR


def test_pieces_chain_in_detection_order_and_end_on_the_grab():
    """His item 4 walkthrough end to end: piece one active, piece two next
    as detected, the star closes the dance."""
    first = piece(41, into_area=2, out_area=3)
    second = piece(42, into_area=3, out_area=4)
    p = Projector(segments=[first, second])
    start(p)
    enter_area(p, 4, 1000, 1, 2)            # first detected
    assert p.target == ("segment", 41)
    enter_area(p, 5, 1500, 2, 3)
    # ONE event: first completed AND second armed — the promotion takes the
    # newly-queued sibling, not the anchor.
    assert p.target == ("segment", 42)
    enter_area(p, 6, 2000, 3, 4)            # second completes
    assert p.target == STAR
    p.feed(jev(7, "star_collected", 2500,
               {"course_id": 2, "star_id": 1, "igt_frames": 900}))
    assert p.target == STAR


def test_detecting_the_piece_again_reselects_it_after_the_grab():
    """Item 2's closing clause: "When we detect the subsection again, then
    we can re-select the subsection." """
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    enter_area(p, 4, 1000, 1, 2)
    p.feed(jev(5, "star_collected", 2000,
               {"course_id": 2, "star_id": 1, "igt_frames": 900}))
    assert p.target == STAR
    # walk back out and in: the piece's start fires again
    enter_area(p, 6, 2500, 2, 1)
    enter_area(p, 7, 3000, 1, 2)
    assert p.target == ("segment", 41)


def test_an_explicit_pick_ends_the_dance():
    """A pick supersedes the dance, so the abandoned anchor may never
    restore. Probed at the one moment an anchor CAN act — the hand
    emptying: the picked star retires on entering a foreign course, and a
    surviving anchor would hand the old star straight back."""
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    enter_area(p, 4, 1000, 1, 2)            # dance on; anchor = the star
    p.feed(jev(5, "target_set", 0, {"course_id": 2, "star_id": 0}))
    assert p.target == ("star", 2, 0)
    p.feed(jev(6, "level_changed", 1500, {"from": 24, "to": 5}))  # into CCM
    assert p.target is None


def test_a_session_boundary_ends_the_dance():
    p = Projector(segments=[piece(41, into_area=2, out_area=3)])
    start(p)
    enter_area(p, 4, 1000, 1, 2)
    p.feed(jev(5, "session_started", 0, {}, session_id=2))
    assert p.target is None


def test_a_piece_of_a_picked_segment_dances_the_same_way():
    """Rule 11's shape: `parents` carries "segment:<id>" through the same
    field, so a castle movement's pieces need no mechanism of their own."""
    parent = SegmentDef(id=7, name="movement", enabled=True,
                        start_triggers=[{"type": "level_exit", "from": 30}],
                        end_triggers=[{"type": "level_enter", "to": 24}],
                        guards=[])
    child = SegmentDef(id=8, name="its piece", enabled=True,
                       start_triggers=[{"type": "area_enter", "level": 6,
                                        "area": 2}],
                       end_triggers=[{"type": "area_enter", "level": 6,
                                      "area": 3}],
                       guards=[], match_mode="strict",
                       parents=["segment:7"])
    p = Projector(segments=[parent, child])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 7}))
    p.feed(jev(2, "level_changed", 900, {"from": 30, "to": 6}))
    p.feed(jev(3, "area_changed", 900, {"level": 6, "from": 3, "to": 1}))
    assert p.target == ("segment", 7)
    p.feed(jev(4, "area_changed", 1200, {"level": 6, "from": 1, "to": 2}))
    assert p.target == ("segment", 8), "the piece takes its parent's hand"
    p.feed(jev(5, "area_changed", 1600, {"level": 6, "from": 2, "to": 3}))
    assert p.target == ("segment", 7), "and its completion hands back"
