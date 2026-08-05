"""Resetting inside a subarea and warping back to the course start is ONE reset.

Live report, task 0084: *"I might reset inside a subarea (like inside the
volcano in Lethal Lava Land). Then, I'll realize 'oh whoops I'm resetting inside
the subarea, I don't want to be here…' then I'll go ahead and open the Usamune
menu, and warp to the beginning of the course. This results in yet another
reset… My expectation would be that I realize I'm resetting in the subarea,
teleport to the beginning of the course, and only see one reset."*

The rule that should already have covered it is the no-op discard: the ~2 s
between those two resets contains nothing the player did, so the attempt they
bracket is not an attempt. It could not fire because the reset's own
INTERRUPTED action was re-read on the next poll and reported as activity —
`detectors/anchors.py`'s `_anchor_action`, whose docstring carries the journal
measurement.

Everything here plays real GameSnapshots through the REAL detector chain
(`main.build_detectors`) and projects what comes out, so a fix that only holds
in the unit test cannot pass. The frames mirror journal ids 24892-24896 (LLL,
2026-08-04): the mistaken reset, a spawn, ~0.7 s of counter, a 52-frame menu
pause, then the warp back to area 1.
"""
from datetime import datetime, timedelta, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.main import build_detectors
from sm64_events.memory.addresses import PASSIVE_ACTIONS
from sm64_events.storage.db import EventRow
from sm64_events.tracking.projection import project

ACT_WALKING = 0x04000440                    # not passive: real play
ACT_IDLE = 0x0C400201
ACT_SPAWN_NO_SPIN_AIRBORNE = 0x00001932     # what the reload puts Mario in
LLL = 22
VOLCANO = 2                                 # LLL's subarea; a course starts in 1
T0 = datetime(2026, 8, 4, 10, 4, tzinfo=timezone.utc)

FIRST_RESET = 613000        # opens the attempt he actually ran
SUBAREA_RESET = 613101      # the mistaken one, taken inside the volcano
MENU_WARP = 613171          # "get me back to the start of the course"


def snap(frame, counter, action=ACT_WALKING, area=VOLCANO):
    return GameSnapshot(
        wall_time_utc=T0 + timedelta(seconds=frame / 30),
        global_timer=frame, mario_action=action, mario_action_timer=0,
        num_stars=8, last_completed_course=7, last_completed_star=3,
        igt_overall=counter, curr_level=LLL, curr_area=area)


def the_reported_session():
    """Two resets with nothing between them but the reload and a menu pause."""
    snaps = [snap(612990, 200, action=ACT_IDLE)]
    snaps += [snap(f, 200 + (f - 612990)) for f in range(612991, FIRST_RESET)]
    snaps.append(snap(FIRST_RESET, 0))                      # reset #1
    snaps.append(snap(FIRST_RESET + 1, 1, action=ACT_SPAWN_NO_SPIN_AIRBORNE))
    snaps += [snap(f, f - FIRST_RESET) for f in range(FIRST_RESET + 2,
                                                      SUBAREA_RESET)]
    snaps.append(snap(SUBAREA_RESET, 0))                    # reset #2, in the volcano
    snaps.append(snap(SUBAREA_RESET, 0))                    # SAME frame, polled again
    snaps.append(snap(SUBAREA_RESET + 1, 1,
                      action=ACT_SPAWN_NO_SPIN_AIRBORNE))
    snaps += [snap(f, f - SUBAREA_RESET, action=ACT_IDLE)
              for f in range(SUBAREA_RESET + 2, SUBAREA_RESET + 21)]
    # the Usamune menu: global_timer runs, the counter does not
    snaps += [snap(f, 20, action=ACT_IDLE)
              for f in range(SUBAREA_RESET + 21, MENU_WARP)]
    snaps.append(snap(MENU_WARP, 0, action=ACT_IDLE, area=1))   # the warp
    snaps.append(snap(MENU_WARP + 1, 1, action=ACT_IDLE, area=1))
    return snaps


def journal(snapshots):
    """Snapshots -> journal rows, through the chain the shipped app runs."""
    detectors, rows = build_detectors(), []
    for prev, curr in zip(snapshots, snapshots[1:]):
        for detector in detectors:
            for event in detector.process(prev, curr):
                if event.type == "stage_changed":
                    continue
                rows.append(EventRow(
                    id=len(rows) + 1, session_id=1, seq=len(rows) + 1,
                    type=event.type, frame=event.frame,
                    wall_time_utc=event.timestamp_utc.isoformat(),
                    payload=event.payload))
    return rows


def test_the_spawn_action_this_test_leans_on_is_still_passive():
    """If the reload's spawn stopped being passive, every case below would
    pass for the wrong reason."""
    assert ACT_SPAWN_NO_SPIN_AIRBORNE in PASSIVE_ACTIONS
    assert ACT_WALKING not in PASSIVE_ACTIONS


def test_the_menu_warp_out_of_a_subarea_records_no_second_reset():
    rows = journal(the_reported_session())
    resets = [a for a in project(rows) if a.outcome == "reset"]
    assert len(resets) == 1, [(a.anchor_frame, a.igt_frames) for a in resets]
    assert resets[0].anchor_frame == FIRST_RESET      # the attempt he really ran
    assert resets[0].igt_frames == SUBAREA_RESET - FIRST_RESET - 1


def test_the_stub_attempt_is_journaled_as_unacted():
    """The projector's discard is the mechanism; this is what feeds it."""
    anchors = [r for r in journal(the_reported_session())
               if r.type == "practice_reset"]
    assert [a.frame for a in anchors] == [FIRST_RESET, SUBAREA_RESET, MENU_WARP]
    assert [a.payload["mario_acted"] for a in anchors] == [True, True, False]
    # and not because the AFK rule happened to catch it instead
    assert anchors[-1].payload["paused_frames_before"] < 150


def test_playing_on_after_the_subarea_reset_still_records_two():
    """The discard is about doing nothing, not about subareas. Walk for a
    second before warping out and both retries are real."""
    snaps = the_reported_session()
    played = [s for s in snaps if s.global_timer <= SUBAREA_RESET + 1]
    played += [snap(f, f - SUBAREA_RESET)          # ACT_WALKING: real play
               for f in range(SUBAREA_RESET + 2, MENU_WARP)]
    played.append(snap(MENU_WARP, 0, area=1))
    resets = [a for a in project(journal(played)) if a.outcome == "reset"]
    assert [a.anchor_frame for a in resets] == [FIRST_RESET, SUBAREA_RESET]
