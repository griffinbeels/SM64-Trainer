"""The target queue (round 19, 2026-08-08).

His ruling, verbatim: "the held target should prioritize whatever is hooked
into first... It should be held in queue order: first in, first out... once
the first held target is completed, the one we detected second should be the
next held target... If there's nothing left in the queue, it's neutral and
nothing is selected. So, if I exited Bowser 1, and did Bowser 1 -> WF, I need
to go back into Bitdw. But, since Bowser 1 -> WF is at the front of the
queue, it stays selected until either completed or canceled."

Two flavors of held target, split by WHO chose it:

- a PICKED head (his click, `target_set` without `auto`) keeps every
  pre-queue rule — origin retirement, the auto-follow retry loop, "nothing
  overwrites a segment pick" — and supersedes the detected backlog;
- a HOOKED head (a deliberate arm taking an empty hand, a promotion, or an
  `auto` convenience fill) is governed by his bounds instead: held until it
  completes, forfeits (a real non-echo reset in a foreign course), or
  expires (the def's own hold budget).

Deliberate vs presence arming is `segments.hooks_on_arm`: a def that fires
by the player merely BEING somewhere (LBLJ's castle entry, the pipe/100-coin
families' course entry) never hooks and never queues.
"""

from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import Projector, replay
from sm64_events.tracking.segments import (
    MIN_BUDGET_FRAMES, SegmentDef, hooks_on_arm)

W = "2026-08-08T12:00:00Z"


def jev(id, type, frame, payload=None, session_id=1):
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def exit_def(id=1, name="B1 -> WF", start_from=30, end_to=24):
    """A deliberate, strict castle movement: armed by a level EXIT (his
    Bowser 1 -> WF shape), ended by a level entry."""
    return SegmentDef(id=id, name=name, enabled=True,
                      start_triggers=[{"type": "level_exit",
                                       "from": start_from}],
                      end_triggers=[{"type": "level_enter", "to": end_to}],
                      guards=[])


def presence_def(id=9, name="LBLJ-ish"):
    """Arms by mere presence (level entry) — must never hook or queue."""
    return SegmentDef(id=id, name=name, enabled=True,
                      start_triggers=[{"type": "level_enter", "to": 6}],
                      end_triggers=[{"type": "level_enter", "to": 17}],
                      guards=[])


# -- the predicate ----------------------------------------------------------

def test_hooks_on_arm_splits_deliberate_from_presence():
    assert hooks_on_arm([{"type": "level_exit", "from": 30}])
    assert hooks_on_arm([{"type": "star_grabbed", "course_id": 6}])
    assert hooks_on_arm([{"type": "moment_reached", "moment": "door_opened"}])
    assert not hooks_on_arm([{"type": "level_enter", "to": 6}])
    assert not hooks_on_arm([{"type": "attempt_anchor", "level": 6}])
    assert not hooks_on_arm([{"type": "spawned"}])
    # one presence clause disqualifies the whole any-of set
    assert not hooks_on_arm([{"type": "level_exit", "from": 30},
                             {"type": "level_enter", "to": 6}])


# -- hooking ---------------------------------------------------------------

def test_a_deliberate_arm_takes_an_empty_hand():
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    assert p.target == ("segment", 1)


def test_a_presence_arm_leaves_an_empty_hand_empty():
    p = Projector(segments=[presence_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 16, "to": 6}))
    assert 9 in p.armed_segment_ids()      # it armed...
    assert p.target is None                # ...and hooked nothing


def test_a_second_detection_queues_behind_the_held_target():
    a, b = exit_def(id=1), exit_def(id=2, name="second", start_from=16,
                                    end_to=8)
    p = Projector(segments=[a, b])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))   # A hooks
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 16}))
    p.feed(jev(3, "level_changed", 2000, {"from": 16, "to": 6}))   # B arms
    assert p.target == ("segment", 1)
    assert p.target_queue() == [2]


# -- FIFO: completion hands the turn onward --------------------------------

def test_completed_head_promotes_the_second_detection():
    # B is LOOSE so it is still armed when its turn comes — a strict
    # bystander disarms on the same foreign level edges the head shrugs
    # off, and a dead detection is deliberately not promoted (see the
    # skipped-at-promotion test below).
    a = exit_def(id=1)
    b = SegmentDef(id=2, name="second", enabled=True, match_mode="loose",
                   start_triggers=[{"type": "level_exit", "from": 16}],
                   end_triggers=[{"type": "level_enter", "to": 8}],
                   guards=[])
    p = Projector(segments=[a, b])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))   # A hooks
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 16}))   # A disarms; held
    p.feed(jev(3, "level_changed", 2000, {"from": 16, "to": 6}))   # B arms+queues
    p.feed(jev(4, "level_changed", 2500, {"from": 6, "to": 30}))
    p.feed(jev(5, "level_changed", 3000, {"from": 30, "to": 6}))   # A re-arms
    assert p.target == ("segment", 1) and p.target_queue() == [2]
    p.feed(jev(6, "level_changed", 3500, {"from": 6, "to": 24}))   # A ends
    assert p.target == ("segment", 2)
    assert p.target_queue() == []


def test_completed_head_with_empty_queue_is_neutral():
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    p.feed(jev(2, "level_changed", 2000, {"from": 6, "to": 24}))   # completed
    assert p.target is None


def test_a_dead_detection_is_skipped_at_promotion():
    a, b = exit_def(id=1), exit_def(id=2, name="second", start_from=16,
                                    end_to=8)
    p = Projector(segments=[a, b])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))   # A hooks
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 16}))
    p.feed(jev(3, "level_changed", 2000, {"from": 16, "to": 6}))   # B queues
    # B dies (game_reset disarms everything; it also drops the hooked head,
    # which doubles as that assertion)
    p.feed(jev(4, "game_reset", 100, {}))
    assert p.target is None and p.target_queue() == []


# -- his worked example: the trip back does not cost the selection ----------

def test_hooked_head_survives_the_loop_back_through_a_foreign_course():
    """Exit Bowser 1 -> hooked. Going back through a course to redo the
    fight disarms the def (relocation) — the selection must hold anyway,
    re-arm on the next exit, and clear only on completion."""
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))    # hooked
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 5}))     # into CCM
    p.feed(jev(3, "area_changed", 1500, {"level": 5, "to": 1}))     # disarms
    assert 1 not in p.armed_segment_ids()
    assert p.target == ("segment", 1), "the trip back kept the selection"
    p.feed(jev(4, "level_changed", 4000, {"from": 5, "to": 6}))
    p.feed(jev(5, "level_changed", 4500, {"from": 6, "to": 30}))    # refight
    p.feed(jev(6, "level_changed", 5000, {"from": 30, "to": 6}))    # re-arm
    assert p.target == ("segment", 1)
    p.feed(jev(7, "level_changed", 5500, {"from": 6, "to": 24}))    # done
    assert p.target is None


def test_hooked_head_forfeits_on_a_real_reset_in_a_foreign_course():
    """His 2026-08-01 bound, applied to the held target: "if... I decided to
    reset to bitdw, I think that's a genuine kill"."""
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))    # hooked
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 5}))     # into CCM
    p.feed(jev(3, "area_changed", 1500, {"level": 5, "to": 1}))     # disarms
    p.feed(jev(4, "practice_reset", 2500,
               {"igt_frames_before": 100, "mario_acted": True}))    # reset THERE
    assert p.target is None


def test_hooked_head_survives_a_reset_in_castle_transit():
    """Same anchor, but in the castle — course None on both sides, so it is
    the retry loop (or transit), never a forfeit."""
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))    # hooked
    p.feed(jev(2, "area_changed", 1000, {"level": 6, "to": 1}))     # pin area
    p.feed(jev(3, "area_changed", 1600, {"level": 6, "to": 2}))     # relocate: disarm
    assert 1 not in p.armed_segment_ids()
    p.feed(jev(4, "practice_reset", 2500,
               {"igt_frames_before": 100, "mario_acted": True}))
    assert p.target == ("segment", 1)


def test_hooked_head_expires_after_its_hold_budget():
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))    # hooked
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 5}))     # into CCM
    p.feed(jev(3, "area_changed", 1500, {"level": 5, "to": 1}))     # disarms
    p.feed(jev(4, "mario_acted", 1500 + MIN_BUDGET_FRAMES + 1, {}))
    assert p.target is None


def test_settle_delivers_the_expiry_on_the_clock():
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))    # hooked
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 5}))     # into CCM
    p.feed(jev(3, "area_changed", 1500, {"level": 5, "to": 1}))     # disarms
    p.settle(1500 + MIN_BUDGET_FRAMES + 1)
    assert p.target is None


# -- the click stays sovereign ---------------------------------------------

def test_a_click_supersedes_and_clears_the_detected_backlog():
    a, b = exit_def(id=1), exit_def(id=2, name="second", start_from=16,
                                    end_to=8)
    p = Projector(segments=[a, b])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))   # A hooks
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 16}))
    p.feed(jev(3, "level_changed", 2000, {"from": 16, "to": 6}))   # B queues
    p.feed(jev(4, "target_set", 0, {"kind": "segment", "segment_id": 2}))
    assert p.target == ("segment", 2)
    assert p.target_queue() == []


def test_a_picked_head_keeps_the_retry_loop_on_completion():
    # end into the GROUNDS (level 16, no course) so the entered-stage clear
    # stays out of it: a click completing keeps itself (pre-queue rule)...
    d = exit_def(id=1, end_to=16)
    p = Projector(segments=[d])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 1}))
    p.feed(jev(2, "level_changed", 1000, {"from": 30, "to": 6}))
    closed = p.feed(jev(3, "level_changed", 2000, {"from": 6, "to": 16}))
    assert any(a.segment_id == 1 and a.outcome == "success" for a in closed)
    assert p.target == ("segment", 1)


def test_a_hooked_head_pops_on_the_same_completion():
    # ...while the SAME def, hooked by detection instead of clicked, hands
    # the turn onward (his FIFO) — the flavor is the whole difference.
    d = exit_def(id=1, end_to=16)
    p = Projector(segments=[d])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    closed = p.feed(jev(2, "level_changed", 2000, {"from": 6, "to": 16}))
    assert any(a.segment_id == 1 and a.outcome == "success" for a in closed)
    assert p.target is None


def test_an_auto_fill_may_replace_a_picked_head_but_never_a_hooked_one():
    """The fill sites carry their own decline rules (the arena row keeps a
    held target that starts in its own arena and overrides one that does
    not, 2026-08-05) — the projector only guards the case no client can
    see: a detection promoted between the client's read and its write."""
    p = Projector(segments=[exit_def(id=1), exit_def(id=2, name="other",
                                                     start_from=16)])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 1}))
    p.feed(jev(2, "target_set", 0, {"kind": "segment", "segment_id": 2,
                                    "auto": True}))
    assert p.target == ("segment", 2)      # picked head: the fill's own call
    p2 = Projector(segments=[exit_def(id=1), exit_def(id=2, name="other",
                                                      start_from=16)])
    p2.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))  # 1 hooks
    p2.feed(jev(2, "target_set", 0, {"kind": "segment", "segment_id": 2,
                                     "auto": True}))
    assert p2.target == ("segment", 1)     # hooked head: the fill is dropped


def test_an_auto_fill_is_hooked_and_a_late_one_is_dropped():
    d = exit_def(id=1, end_to=16)
    p = Projector(segments=[d])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 1,
                                    "auto": True}))
    assert p.target == ("segment", 1)
    # a fill arriving on a hand that has since filled must not steal
    p.feed(jev(2, "target_set", 0, {"kind": "segment", "segment_id": 7,
                                    "auto": True}))
    assert p.target == ("segment", 1)
    # hooked flavor: its completion pops instead of holding
    p.feed(jev(3, "level_changed", 1000, {"from": 30, "to": 6}))
    p.feed(jev(4, "level_changed", 2000, {"from": 6, "to": 16}))
    assert p.target is None


# -- nothing steals a held head --------------------------------------------

def test_a_foreign_success_never_steals_a_held_head():
    """Pre-queue, a neighbouring success took the slot whenever the held
    pick was not among the closures. FIFO forbids the steal outright."""
    a = exit_def(id=1)
    # B ends back in the Bowser 1 arena, which is A's OWN origin. Deliberate
    # since 2026-08-10: a picked head is now also retired by standing
    # somewhere its segment neither starts nor passes through, and this test
    # is about the FIFO steal, not about that rule -- ending B in the
    # courtyard retired A for an unrelated reason and B's arm then filled the
    # empty hand, which looks exactly like the steal this forbids.
    b = exit_def(id=2, name="other", start_from=16, end_to=30)
    p = Projector(segments=[a, b])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 1}))
    p.feed(jev(2, "level_changed", 1000, {"from": 16, "to": 6}))   # B arms
    closed = p.feed(jev(3, "level_changed", 2000, {"from": 6, "to": 30}))
    assert any(x.segment_id == 2 and x.outcome == "success" for x in closed)
    assert p.target == ("segment", 1)


def test_a_deliberate_arm_promotes_over_a_star_target():
    """A star target cleared by a foreign arm used to leave the hand empty
    for the client to fill; the detection that cleared it now takes it."""
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 100, {"from": 6, "to": 7}))     # HMC
    p.feed(jev(2, "star_collected", 900,
               {"course_id": 6, "star_id": 2, "igt_frames": 300}))
    assert p.target == ("star", 6, 2)
    p.feed(jev(3, "level_changed", 1500, {"from": 7, "to": 6}))
    p.feed(jev(4, "level_changed", 2000, {"from": 6, "to": 30}))
    p.feed(jev(5, "level_changed", 2500, {"from": 30, "to": 6}))   # arm+hook
    assert p.target == ("segment", 1)


def test_a_grab_takes_the_hand_from_a_stale_hook_but_not_a_live_or_picked_one():
    """Measured before shipping: with a re-entry movement hooked on every
    course exit of a route grind, a declining grab left the grind's failures
    unattributed (and the prune eats unlabelled rows). A stale hook yields
    to a grab; a RUNNING hook and a PICK keep the slot."""
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))    # hooked
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 7}))     # into HMC
    p.feed(jev(3, "area_changed", 1500, {"level": 7, "to": 1}))     # disarms
    p.feed(jev(4, "star_collected", 2000,
               {"course_id": 6, "star_id": 2, "igt_frames": 300}))
    assert p.target == ("star", 6, 2)      # the stale hook yielded
    armed = Projector(segments=[exit_def()])
    armed.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    armed.feed(jev(2, "star_collected", 2000,
                   {"course_id": 6, "star_id": 2, "igt_frames": 300}))
    assert armed.target == ("segment", 1)  # a live run keeps the slot
    # ...but a run the grab itself CANCELS (a waypoint arm's major-action
    # rule) yields on the same event — the claim is deferred past the
    # matcher, exactly like the origin retirement.
    wp = SegmentDef(id=3, name="re-entry", enabled=True,
                    start_triggers=[{"type": "level_exit", "from": 7}],
                    waypoints=[[{"type": "area_enter", "level": 6,
                                 "area": 3}]],
                    end_triggers=[{"type": "level_enter", "to": 7}],
                    guards=[])
    cancelled = Projector(segments=[wp])
    cancelled.feed(jev(1, "level_changed", 1000, {"from": 7, "to": 6}))
    assert cancelled.target == ("segment", 3)
    cancelled.feed(jev(2, "star_collected", 2000,
                       {"course_id": 6, "star_id": 2, "igt_frames": 300}))
    assert cancelled.target == ("star", 6, 2)
    picked = Projector(segments=[exit_def()])
    picked.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 1}))
    picked.feed(jev(2, "star_collected", 2000,
                    {"course_id": 6, "star_id": 2, "igt_frames": 300}))
    assert picked.target == ("segment", 1)  # a click is sovereign


# -- boundaries ------------------------------------------------------------

def test_session_start_clears_head_and_queue():
    a, b = exit_def(id=1), exit_def(id=2, name="second", start_from=16,
                                    end_to=8)
    p = Projector(segments=[a, b])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    p.feed(jev(2, "level_changed", 1500, {"from": 6, "to": 16}))
    p.feed(jev(3, "level_changed", 2000, {"from": 16, "to": 6}))
    p.feed(jev(4, "session_started", 0, {}, session_id=2))
    assert p.target is None and p.target_queue() == []


def test_game_reset_keeps_a_picked_head():
    p = Projector(segments=[exit_def()])
    p.feed(jev(1, "target_set", 0, {"kind": "segment", "segment_id": 1}))
    p.feed(jev(2, "game_reset", 100, {}))
    assert p.target == ("segment", 1)


# -- replay rebuilds it ----------------------------------------------------

def test_replay_rebuilds_head_flavor_and_queue():
    a, b = exit_def(id=1), exit_def(id=2, name="second", start_from=16,
                                    end_to=8)
    events = [
        jev(1, "level_changed", 1000, {"from": 30, "to": 6}),
        jev(2, "level_changed", 1500, {"from": 6, "to": 16}),
        jev(3, "level_changed", 2000, {"from": 16, "to": 6}),
    ]
    live = Projector(segments=[a, b])
    for ev in events:
        live.feed(ev)
    _, rebuilt = replay(events, segments=[a, b])
    assert rebuilt.target == live.target == ("segment", 1)
    assert rebuilt.target_queue() == live.target_queue() == [2]
    assert rebuilt._target_hooked == live._target_hooked is True


def test_a_burst_of_arms_detects_nothing():
    """Leaving the Bowser 1 arena is ONE `level_exit from=30` and all six
    `Bowser 1 -> X` movements arm on it together. His ruling, 2026-08-10:
    *"it pre-selected Bowser 1 -> BOB. This makes no sense, because there are
    too many options here to autoselect any of them."*

    They neither hook NOR queue -- queueing would only defer the same
    assertion to the next promotion."""
    six = [exit_def(id=n, name=f"B1 -> {n}", start_from=30, end_to=end)
           for n, end in enumerate([9, 24, 5, 8, 23, 19], start=40)]
    p = Projector(segments=six)
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    assert len(p.armed_segment_ids()) == 6, "all six really do arm together"
    assert p.target is None, "nobody picked between them"
    assert p.target_queue() == [], (
        "and none may sit in line either -- a promotion is the same "
        "assertion one pop later")


def test_a_lone_arm_still_takes_the_empty_hand():
    """The threshold is 'more than one', not 'any'. Round 19's whole point is
    that ONE detection into an empty hand is a reading of what he did, and
    dropping that would make a single practised movement unselectable."""
    p = Projector(segments=[exit_def(id=1)])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    assert p.target == ("segment", 1)


def test_the_burst_count_ignores_arms_that_could_never_hook():
    """Counted over the ELIGIBLE arms only, so a burst whose extras are
    subsections or presence arms still leaves one unambiguous detection."""
    piece = SegmentDef(id=2, name="a piece", enabled=True,
                       start_triggers=[{"type": "level_exit", "from": 30}],
                       end_triggers=[{"type": "level_enter", "to": 5}],
                       guards=[], parents=["star:1:1"])
    p = Projector(segments=[exit_def(id=1), piece, presence_def(id=9)])
    p.feed(jev(1, "level_changed", 1000, {"from": 30, "to": 6}))
    assert 2 in p.armed_segment_ids(), "the piece really did arm"
    assert p.target == ("segment", 1), (
        "a subsection never hooks, so it cannot make a lone arm ambiguous")
