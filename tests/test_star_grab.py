# tests/test_star_grab.py
from dataclasses import replace
from datetime import datetime, timezone

from sm64_events.core.snapshot import GameSnapshot
from sm64_events.detectors.star_grab import StarGrabDetector
from sm64_events.memory import addresses as A

ACT_IDLE = 0x0C400201  # any non-star-dance action works for tests


def snap(**overrides) -> GameSnapshot:
    defaults = dict(
        wall_time_utc=datetime(2026, 6, 10, tzinfo=timezone.utc),
        global_timer=1000,
        mario_action=ACT_IDLE,
        mario_action_timer=0,
        num_stars=5,
        last_completed_course=1,
        last_completed_star=3,
    )
    defaults.update(overrides)
    return GameSnapshot(**defaults)


def drained(snaps):
    """Usamune's own result write is allowed to settle after the x-cam, so a
    fixture that stops at the grab emits nothing at all. The tail is a CLONE of
    the last sample — same action, same result store, counter advanced with the
    clock — so it can only end that wait, never change the answer."""
    last = snaps[-1]
    ahead = StarGrabDetector.RESULT_SETTLE_FRAMES + 1
    return [*snaps, replace(last, global_timer=last.global_timer + ahead,
                            igt_overall=last.igt_overall + ahead)]


def run_pairs(detector, snaps, settle=True):
    """Feed consecutive snapshot pairs; return all emitted events."""
    stream = drained(snaps) if settle else snaps
    events = []
    for prev, curr in zip(stream, stream[1:]):
        events.extend(detector.process(prev, curr))
    return events


def test_edge_into_star_dance_emits_identified_event():
    # A GROUND grab: Mario enters the dance on the grab frame, so the x-cam
    # moment IS the grab and Usamune's freshly written result is the answer.
    prev = snap(num_stars=5, global_timer=1001, igt_overall=230, igt_result=0)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=2,
                global_timer=1002, num_stars=6,
                last_completed_course=1, last_completed_star=3,
                igt_overall=232, igt_result=231)
    events = run_pairs(StarGrabDetector(), [prev, curr])
    assert len(events) == 1
    ev = events[0]
    assert ev.type == "star_collected"
    assert ev.frame == 1000  # back-computed: 1002 - 2
    assert ev.payload == {
        "course_id": 1,
        "course_name": "Bob-omb Battlefield",
        "star_id": 2,  # game stores 1-based (3); API is 0-based
        "star_name": "Shoot to the Island in the Sky",
        "already_collected": False,
        "igt_frames": 231,  # taken verbatim from Usamune's result store
        "igt": "0'07\"70",  # the number Usamune shows
        "igt_source": "result",
        "igt_reconstructed": False,
        "igt_timed_at": "xcam",
        "grab_frame": 1000,
        "num_stars": 6,  # curr.num_stars at grab time
    }


def test_multi_area_star_uses_usamune_result():
    # SSL "Inside the Ancient Pyramid" regression: the section counter
    # resets at the area warp, but Usamune writes the exact overall star
    # time (595 = 0'19"83) into the result store at the grab.
    #
    # The source is "counter" rather than "result" only because this fixture
    # polls once every five frames, so the write cannot be PROVEN to have
    # landed on or after the x-cam frame (igt_clock._result_written_at_or_after
    # takes the conservative half of that bracket). The number is identical
    # either way, which is the point: the counter path is the same answer.
    snaps = [
        snap(global_timer=2000, igt_overall=590, igt_result=0),
        snap(global_timer=2005, igt_overall=595, igt_result=595,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=1),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].payload["igt_frames"] == 595
    assert events[0].payload["igt"] == "0'19\"83"


def test_stale_result_falls_back_to_overall_counter():
    # The result store still holds a PREVIOUS star's time (never observed
    # changing) -> use the running overall counter with the display tick.
    snaps = [
        snap(global_timer=1000, igt_overall=215, igt_result=999),
        snap(global_timer=1004, igt_overall=219, igt_result=999),
        snap(global_timer=1010, igt_overall=225, igt_result=999),
        snap(global_timer=1017, igt_overall=232, igt_result=999,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=2),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].payload["igt_frames"] == 231  # (232 - 2) + display tick
    assert events[0].payload["igt_source"] == "counter"


def test_counter_path_includes_display_tick_even_from_zero():
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT,
                igt_overall=0, igt_result=0)
    ev = run_pairs(StarGrabDetector(), [snap(), curr])[0]
    assert ev.payload["igt_frames"] == 1
    assert ev.payload["igt"] == "0'00\"03"
    assert ev.payload["igt_source"] == "counter"


def test_igt_reset_racing_grab_reports_prior_attempt_time():
    # Regression from a live trace (2026-06-10): the player's reset landed
    # ~3 game frames BEFORE the star touch, so the counters AND Usamune's
    # own result write hold the post-reset near-zero time. The event must
    # report the attempt that earned the star — extrapolated to the X-CAM
    # frame, since that is the moment the reported number describes:
    # 185 frames at g=429726, x-cam at g=429740 -> 199, +1 display tick.
    snaps = [
        snap(global_timer=429722, igt_overall=181),
        snap(global_timer=429726, igt_overall=185),
        snap(global_timer=429729, igt_overall=3),   # Usamune reset hit here
        snap(global_timer=429730, igt_overall=4),
        snap(global_timer=429731, igt_overall=5, igt_result=5,  # fresh but tainted
             mario_action=A.ACT_FALL_AFTER_STAR_GRAB, mario_action_timer=0),
        snap(global_timer=429740, igt_overall=14, igt_result=5,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=0),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert len(events) == 1
    assert events[0].payload["igt_frames"] == 200
    assert events[0].payload["igt_source"] == "reconstructed"
    assert events[0].payload["igt_reconstructed"] is True
    assert events[0].frame == 429740
    assert events[0].payload["grab_frame"] == 429731


def test_igt_reset_between_touch_and_sample_uses_exact_prior_value():
    # Touch frame back-computes to BEFORE the reset gap: the prior attempt's
    # clock extrapolates exactly to the touch.
    snaps = [
        snap(global_timer=1000, igt_overall=500),
        snap(global_timer=1003, igt_overall=1,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=3),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].payload["igt_frames"] == 501  # 500 + (1000 - 1000) + tick
    assert events[0].payload["igt_reconstructed"] is True


def test_grab_well_after_reset_is_a_genuine_new_attempt():
    # A reset in recent history must NOT hijack a grab that happened a full
    # attempt later (post-reset IGT >= RESET_GRACE_FRAMES).
    snaps = [
        snap(global_timer=1000, igt_overall=400),
        snap(global_timer=1010, igt_overall=5),    # reset
        snap(global_timer=1050, igt_overall=45),
        snap(global_timer=1100, igt_overall=95,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=0),
    ]
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].payload["igt_frames"] == 96  # 95 + display tick
    assert events[0].payload["igt_reconstructed"] is False


def test_history_cleared_when_time_jumps_backward():
    # A savestate load rewinds global_timer; pre-jump IGT samples must not
    # be used for reconstruction afterwards.
    d = StarGrabDetector()
    snaps_before = [
        snap(global_timer=5000, igt_overall=900),
        snap(global_timer=5001, igt_overall=901),
    ]
    run_pairs(d, snaps_before)
    snaps_after = [
        snap(global_timer=5001, igt_overall=901),
        snap(global_timer=100, igt_overall=50),  # backward jump (savestate)
        snap(global_timer=110, igt_overall=60,
             mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=0),
    ]
    events = run_pairs(d, snaps_after)
    assert len(events) == 1
    assert events[0].payload["igt_frames"] == 61  # 60 + display tick
    assert events[0].payload["igt_reconstructed"] is False


def test_already_collected_star_still_fires_with_flag_true():
    prev = snap(num_stars=6)
    curr = snap(mario_action=A.ACT_STAR_DANCE_NO_EXIT, num_stars=6)
    events = run_pairs(StarGrabDetector(), [prev, curr])
    assert len(events) == 1
    assert events[0].payload["already_collected"] is True


def test_every_dance_variant_fires_on_the_grab_frame():
    for action in (A.ACT_STAR_DANCE_EXIT, A.ACT_STAR_DANCE_WATER,
                   A.ACT_STAR_DANCE_NO_EXIT):
        events = run_pairs(StarGrabDetector(),
                           [snap(), snap(mario_action=action)])
        assert len(events) == 1, hex(action)
        assert events[0].payload["igt_timed_at"] == "xcam", hex(action)


def test_midair_grab_waits_for_the_landing_before_emitting():
    # ACT_FALL_AFTER_STAR_GRAB is the grab, not the x-cam: Usamune stops on
    # "Mario touches the ground after star-grab", so nothing is known yet.
    events = StarGrabDetector().process(
        snap(), snap(mario_action=A.ACT_FALL_AFTER_STAR_GRAB))
    assert events == []


def test_no_event_while_dance_continues():
    prev = snap(mario_action=A.ACT_STAR_DANCE_EXIT)
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=10)
    assert StarGrabDetector().process(prev, curr) == []


def test_no_event_when_the_grab_itself_was_never_seen():
    # A detector that starts up mid-fall never saw the grab edge, so it has no
    # identity to emit — the dance alone is not a grab.
    prev = snap(mario_action=A.ACT_FALL_AFTER_STAR_GRAB)
    curr = snap(mario_action=A.ACT_STAR_DANCE_NO_EXIT)
    assert StarGrabDetector().process(prev, curr) == []


def test_no_event_without_edge():
    assert StarGrabDetector().process(snap(), snap(global_timer=1001)) == []


def test_same_star_twice_produces_two_events():
    detector = StarGrabDetector()
    first = run_pairs(detector, [snap(), snap(mario_action=A.ACT_STAR_DANCE_EXIT)])
    second = run_pairs(detector, [snap(global_timer=2000),
                                  snap(global_timer=2001,
                                       mario_action=A.ACT_STAR_DANCE_EXIT)])
    assert len(first) == 1 and len(second) == 1


def test_never_collected_sentinel_is_dropped():
    # last_completed_star == 0 means "never set" — cannot identify a star
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT,
                last_completed_course=0, last_completed_star=0)
    assert StarGrabDetector().process(snap(), curr) == []


def test_frame_never_negative():
    curr = snap(mario_action=A.ACT_STAR_DANCE_EXIT,
                global_timer=1, mario_action_timer=5)
    events = run_pairs(StarGrabDetector(), [snap(), curr])
    assert events[0].frame == 0


def test_key_grab_in_bowser_arena_does_not_emit_star_collected():
    d = StarGrabDetector()
    events = d.process(snap(curr_level=A.BOWSER_1_ARENA),
                       snap(curr_level=A.BOWSER_1_ARENA,
                            mario_action=A.ACT_STAR_DANCE_EXIT))
    assert events == []


# --- the x-cam moment: Usamune stops when Mario LANDS, not when he touches ---
#
# Measured 2026-08-01 (tools/derive_xcam.py, scored against Usamune's own
# settled result store): the x-cam moment is the star-dance entry and Usamune's
# number is that frame's overall counter + DISPLAY_TICK. Four midair grabs came
# back within a frame there; the grab frame came back -4, -11, -23 and -39.

GRAB_FRAME = 1000
GRAB_COUNTER = 300


def midair_snaps(fall_frames: int, *, result: int = 0, prior_result: int = 0,
                 dance_late: int = 0):
    """A dense (one sample per game frame) midair grab: Mario touches the star
    at GRAB_FRAME and lands `fall_frames` later.

    `result` is what Usamune's result store holds from the grab onward — 301
    models STOP=GrabX writing the GRAB time there (the write we used to take),
    a value equal to `prior_result` models STOP=Xcam, where nothing has been
    written by the time Mario lands. `dance_late` skips that many polls, so the
    dance is first SEEN with its action timer already running."""
    snaps = [snap(global_timer=GRAB_FRAME - 2, igt_overall=GRAB_COUNTER - 2,
                  igt_result=prior_result),
             snap(global_timer=GRAB_FRAME - 1, igt_overall=GRAB_COUNTER - 1,
                  igt_result=prior_result)]
    for offset in range(fall_frames):
        snaps.append(snap(global_timer=GRAB_FRAME + offset,
                          igt_overall=GRAB_COUNTER + offset,
                          igt_result=result,
                          mario_action=A.ACT_FALL_AFTER_STAR_GRAB,
                          mario_action_timer=offset))
    seen = fall_frames + dance_late
    snaps.append(snap(global_timer=GRAB_FRAME + seen,
                      igt_overall=GRAB_COUNTER + seen, igt_result=result,
                      mario_action=A.ACT_STAR_DANCE_EXIT,
                      mario_action_timer=dance_late))
    return snaps


def test_midair_grab_is_timed_at_the_landing_not_the_touch():
    events = run_pairs(StarGrabDetector(), midair_snaps(10))
    assert len(events) == 1
    ev = events[0]
    assert ev.frame == GRAB_FRAME + 10          # the star-dance entry
    assert ev.payload["grab_frame"] == GRAB_FRAME
    assert ev.payload["igt_frames"] == 311      # counter 310 + display tick
    assert ev.payload["igt_source"] == "counter"
    assert ev.payload["igt_timed_at"] == "xcam"


def test_usamunes_grab_time_write_is_not_taken_for_a_midair_grab():
    # STOP=GrabX writes the result store TWICE — once at the grab, again at the
    # x-cam (manual). The first write is what we used to read, and it sits well
    # inside IgtClock.RESULT_FRESH_FRAMES of the landing, so nothing but the
    # at-or-after rule keeps it out. Live gaps under GrabX: 9, 11 and 28 frames.
    events = run_pairs(StarGrabDetector(), midair_snaps(10, result=301))
    assert events[0].payload["igt_frames"] == 311
    assert events[0].payload["igt_frames"] != 301  # the grab-time write


def test_stale_result_from_an_earlier_star_is_not_taken_either():
    # STOP=Xcam: nothing has been written when Mario lands, and the store still
    # holds a previous star's number.
    events = run_pairs(StarGrabDetector(),
                       midair_snaps(10, result=999, prior_result=999))
    assert events[0].payload["igt_frames"] == 311
    assert events[0].payload["igt_source"] == "counter"


def test_dance_seen_late_is_back_computed_to_its_entry_frame():
    # The poll missed the first two frames of the dance; the action timer says
    # so, and both the frame and the number must come out unchanged.
    events = run_pairs(StarGrabDetector(), midair_snaps(10, dance_late=2))
    assert events[0].frame == GRAB_FRAME + 10
    assert events[0].payload["igt_frames"] == 311


def test_grab_that_never_lands_is_journaled_at_the_grab():
    # Backstop: a grab must never be lost, so after XCAM_TIMEOUT_FRAMES the
    # grab-moment number is emitted, LABELLED as the grab moment.
    snaps = midair_snaps(3)[:-1]  # never reaches a dance
    snaps.append(snap(global_timer=GRAB_FRAME + StarGrabDetector.XCAM_TIMEOUT_FRAMES,
                      igt_overall=GRAB_COUNTER + 300,
                      mario_action=A.ACT_FALL_AFTER_STAR_GRAB))
    events = run_pairs(StarGrabDetector(), snaps)
    assert len(events) == 1
    assert events[0].frame == GRAB_FRAME
    assert events[0].payload["igt_timed_at"] == "grab"
    assert events[0].payload["igt_frames"] == GRAB_COUNTER + 1


def test_savestate_load_mid_fall_still_journals_the_grab():
    snaps = midair_snaps(3)[:-1]
    snaps.append(snap(global_timer=50, igt_overall=10))  # backward jump
    events = run_pairs(StarGrabDetector(), snaps)
    assert len(events) == 1
    assert events[0].payload["igt_timed_at"] == "grab"
    assert events[0].payload["igt_frames"] == GRAB_COUNTER + 1


def test_leaving_the_level_mid_fall_still_journals_the_grab():
    # Keeps event ORDER honest: a deferred star_collected must not land after
    # the level_changed that ended the level it happened in.
    snaps = midair_snaps(3)[:-1]
    snaps.append(snap(global_timer=GRAB_FRAME + 4, igt_overall=GRAB_COUNTER + 4,
                      curr_level=A.LEVEL_CASTLE_INSIDE))
    events = run_pairs(StarGrabDetector(), snaps)
    assert len(events) == 1
    assert events[0].payload["igt_timed_at"] == "grab"


def test_a_deferred_grab_emits_exactly_once():
    snaps = midair_snaps(4)
    snaps.append(snap(global_timer=GRAB_FRAME + 5, igt_overall=GRAB_COUNTER + 5,
                      mario_action=A.ACT_STAR_DANCE_EXIT, mario_action_timer=1))
    assert len(run_pairs(StarGrabDetector(), snaps)) == 1


def test_usamune_reset_mid_fall_journals_the_grab_immediately():
    # A reset destroys the attempt the pending number would have described,
    # and waiting out the backstop would land star_collected ten seconds after
    # the practice_reset that ended its run.
    snaps = midair_snaps(3)[:-1]
    snaps.append(snap(global_timer=GRAB_FRAME + 4, igt_overall=2))
    events = run_pairs(StarGrabDetector(), snaps)
    assert len(events) == 1
    assert events[0].payload["igt_timed_at"] == "grab"


def test_a_one_frame_stale_counter_read_does_not_abort_the_wait():
    # The abort is on SIZE, not direction: a torn snapshot reads the counter a
    # frame behind, and treating that as a reset would re-introduce the very
    # grab-time number this file exists to stop emitting.
    snaps = midair_snaps(10)
    torn = snaps[5]
    snaps[5] = snap(global_timer=torn.global_timer,
                    igt_overall=snaps[4].igt_overall - 1,  # a frame BEHIND prev
                    mario_action=A.ACT_FALL_AFTER_STAR_GRAB,
                    mario_action_timer=torn.mario_action_timer)
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].payload["igt_timed_at"] == "xcam"
    assert events[0].payload["igt_frames"] == 311


def test_the_xcam_frame_can_never_precede_the_grab_it_belongs_to():
    # A stale action timer on the first dance sample would otherwise claim a
    # moment before Mario touched the star, and the counter would be
    # back-computed past it.
    snaps = midair_snaps(3)[:-1]
    snaps.append(snap(global_timer=GRAB_FRAME + 3, igt_overall=GRAB_COUNTER + 3,
                      mario_action=A.ACT_STAR_DANCE_EXIT,
                      mario_action_timer=99))
    events = run_pairs(StarGrabDetector(), snaps)
    assert events[0].frame == GRAB_FRAME
    assert events[0].payload["igt_frames"] >= GRAB_COUNTER


# --- Usamune's own write wins, because our counter is SUBAREA-LOCAL ---------
#
# Live 2026-08-01, his gate run: nine single-area stars matched Usamune exactly
# and the only two failures were the two he took inside a subarea. LLL
# "Hot-Foot-It into the Volcano" read 0'40"63 against Usamune's 0'52"46 and SSL
# "Inside the Ancient Pyramid" 0'02"43 against 0'19"13 — our number was the time
# since he entered the volcano/pyramid, his was the whole star. `USAMUNE_OVERALL`
# restarts at an area warp, so deriving the x-cam correctly is not enough: the
# moment is ours, the number has to be Usamune's whenever it writes one.


def subarea_snaps(*, counter_at_grab=68, fall_frames=3, usamune=574,
                  write_at=30, stale=452):
    """A multi-area star: the counter is subarea-local, so Usamune's late write
    is the ONLY source that knows the whole star's time."""
    snaps = [snap(global_timer=GRAB_FRAME - 1,
                  igt_overall=counter_at_grab - 1, igt_result=stale)]
    for offset in range(fall_frames):
        snaps.append(snap(global_timer=GRAB_FRAME + offset,
                          igt_overall=counter_at_grab + offset,
                          igt_result=stale,
                          mario_action=A.ACT_FALL_AFTER_STAR_GRAB,
                          mario_action_timer=offset))
    for offset in range(fall_frames, fall_frames
                        + StarGrabDetector.RESULT_SETTLE_FRAMES + 2):
        snaps.append(snap(global_timer=GRAB_FRAME + offset,
                          igt_overall=counter_at_grab + offset,
                          igt_result=usamune if offset >= write_at else stale,
                          mario_action=A.ACT_STAR_DANCE_EXIT,
                          mario_action_timer=offset - fall_frames))
    return snaps


def test_a_multi_area_star_takes_usamunes_number_not_our_counter():
    events = run_pairs(StarGrabDetector(), subarea_snaps(), settle=False)
    assert len(events) == 1
    ev = events[0]
    assert ev.payload["igt_frames"] == 574        # the whole star
    assert ev.payload["igt_frames"] != 72         # time inside the pyramid
    assert ev.payload["igt_source"] == "result"
    assert ev.payload["igt_timed_at"] == "xcam"
    assert ev.frame == GRAB_FRAME + 3             # still OUR x-cam moment


def test_the_write_is_waited_for_rather_than_missed_by_a_frame():
    """It lands one frame after the dance on an ordinary star and 27-28 after
    it on a multi-area one. Emitting at the x-cam saw neither."""
    barely = subarea_snaps()[:6]  # a few frames past the landing, no write yet
    assert run_pairs(StarGrabDetector(), barely, settle=False) == []


def test_a_write_that_lands_before_the_xcam_is_still_refused():
    # STOP=Grab writes once, at the touch. Waiting must not turn that into a
    # believed answer just because the wait gave it time to be noticed.
    events = run_pairs(StarGrabDetector(), midair_snaps(10, result=301))
    assert events[0].payload["igt_source"] == "counter"
    assert events[0].payload["igt_frames"] == 311


def test_the_settle_window_sits_inside_the_bracket_measured_live():
    """Both ends of `RESULT_SETTLE_FRAMES` are live measurements, and the
    CEILING is the one nobody expects — a wait made "generous" walks into a
    cleared store and journals 0'00"00.

    His subarea run, 2026-08-01: the corrected whole-star write landed 26-28
    frames after the dance entry (so anything under 28 emits the subarea-local
    number), and the store was zeroed 90 frames after it on a course exit (so
    anything from 90 up can read that zero). This is the only guard that can
    fail when someone tunes the wait by reasoning; the evidence for each end
    is on RESULT_SETTLE_BRACKET."""
    floor, ceiling = StarGrabDetector.RESULT_SETTLE_BRACKET
    assert floor <= StarGrabDetector.RESULT_SETTLE_FRAMES < ceiling
