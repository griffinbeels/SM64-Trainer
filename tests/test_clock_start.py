"""When the clock starts (round 15 item 3) — "trigger" vs "move".

His ruling, verbatim: *"it's DETECTED when the specific event occurs (e.g.,
touching the CCM door), but the timer doesn't actually START until mario is
able to finally move, aka when Usamune's timer actually resets to 0 when we
go to the new section."*

The fixture is HIS journal, not an invention: ids 3930-3933 (2026-08-08) —
the CCM door moment at frame 1815640 carrying igt 42, the door's own echo
anchor at +51 (the counter zero the door caused), and the CCM entrance touch
at +127 carrying igt 77. The live engine recorded that attempt as 127 frames
(0'04"23, the number he reported) under "trigger"; Usamune showed ~0'02"57,
which is the 77 the closing event already carried. "move" takes the 77.
"""
from sm64_events.storage.db import EventRow
from sm64_events.tracking.segments import (CLOCK_START_WINDOW_FRAMES,
                                           SegmentDef, SegmentEngine,
                                           MatchContext)

W = "2026-08-08T02:38:58Z"


# Id 3931's payload VERBATIM — `frames_since_door: 2` is what makes the
# anchor a door ECHO (invisible to the matcher) rather than a player retry,
# and inventing a slimmer payload turned it into a retry and split the span.
DOOR_ECHO = {"igt_frames_before": 90, "mario_acted": True,
             "paused_frames_before": 0, "acted_tracking": True,
             "action": 67109952, "prev_action": 205521409,
             "save_pending": False, "frames_since_door": 2,
             "frames_since_dialog": None, "warp_op": 4,
             "frames_since_warp_op": 2148, "area": 1, "prev_area": 1,
             "area_load": False, "teleport": False}


def jev(id, type, frame, payload=None, session_id=1):
    return EventRow(id=id, session_id=session_id, seq=id, type=type,
                    frame=frame, wall_time_utc=W, payload=payload or {})


def ctx(level=6, area=1):
    return MatchContext(level=level, prev_level=level, num_stars=0, area=area)


def ccm_piece(clock_start: str) -> SegmentDef:
    return SegmentDef(
        id=1, name="CCM Door → CCM", enabled=True,
        start_triggers=[{"type": "moment_reached", "kind": "door_open",
                         "level": 6}],
        end_triggers=[{"type": "entrance_touched", "to": 5}],
        guards=[], match_mode="strict", clock_start=clock_start)


def run_his_span(engine: SegmentEngine, close_igt=77):
    """Ids 3930-3933, frames and payloads as journaled."""
    rows = []
    rows += engine.feed(jev(3930, "moment_reached", 1815640,
                            {"kind": "door_open", "level": 6, "area": 1,
                             "igt_frames": 42}), ctx())[0]
    rows += engine.feed(jev(3931, "practice_reset", 1815691,
                            dict(DOOR_ECHO)), ctx())[0]
    rows += engine.feed(jev(3933, "warp_entered", 1815767,
                            {"level": 6, "area": 1, "to": 5,
                             "igt_frames": close_igt}), ctx())[0]
    return rows


def test_a_move_clock_records_what_usamune_shows():
    [attempt] = run_his_span(SegmentEngine([ccm_piece("move")]))
    assert attempt.outcome == "success"
    assert attempt.rta_frames == 77, (
        "the clock must start at the door's own counter zero — the moment "
        "Mario can move — which is the closing event's igt verbatim")
    assert attempt.timed_by == "igt"


def test_a_trigger_clock_still_records_the_full_span():
    # The control, and every pre-round-15 definition: door animation and
    # fade included, exactly what he reported as 0'04"23.
    [attempt] = run_his_span(SegmentEngine([ccm_piece("trigger")]))
    assert attempt.outcome == "success"
    assert attempt.rta_frames == 1815767 - 1815640 == 127 or \
        attempt.rta_frames == 127
    assert attempt.timed_by == "delta"


def test_a_mid_piece_zero_falls_back_to_a_delta_from_the_move_origin():
    """A SECOND section change long after the start is a different leg — the
    closing igt then measures only that leg, so it must not be taken; the
    delta from the move origin at least spans the right two moments."""
    engine = SegmentEngine([ccm_piece("move")])
    engine.feed(jev(3930, "moment_reached", 1815640,
                    {"kind": "door_open", "level": 6, "area": 1,
                     "igt_frames": 42}), ctx())
    engine.feed(jev(3931, "practice_reset", 1815691,
                    dict(DOOR_ECHO)), ctx())
    # A second door echo 300 frames on — outside the window, zeroes the
    # counter without being a player retry.
    engine.feed(jev(3932, "practice_reset", 1815991,
                    dict(DOOR_ECHO)), ctx())
    [attempt] = engine.feed(jev(3933, "warp_entered", 1816091,
                                {"level": 6, "area": 1, "to": 5,
                                 "igt_frames": 100}), ctx())[0]
    assert attempt.timed_by == "delta"
    assert attempt.rta_frames == 1816091 - 1815691, (
        "the delta must start at the move origin (the first section entry "
        "the start caused), never at the arm and never at the later zero")


def test_a_move_def_with_no_section_change_times_like_trigger():
    # Mario never stopped being able to move, so there is nothing to rebase
    # to — the arm is the origin, exactly as before this field existed.
    engine = SegmentEngine([ccm_piece("move")])
    engine.feed(jev(3930, "moment_reached", 1815640,
                    {"kind": "door_open", "level": 6, "area": 1,
                     "igt_frames": 42}), ctx())
    [attempt] = engine.feed(jev(3933, "warp_entered", 1815767,
                                {"level": 6, "area": 1, "to": 5,
                                 "igt_frames": 77}), ctx())[0]
    assert attempt.timed_by == "delta"
    assert attempt.rta_frames == 127


def test_the_window_is_what_separates_the_two_modes():
    # Mutation guard by construction: the door's zero sits at +51, so any
    # window below that must behave exactly like "trigger". Proves the
    # rebase rides CLOCK_START_WINDOW_FRAMES and nothing else.
    assert CLOCK_START_WINDOW_FRAMES > 51


def test_vocab_ships_the_clock_starts_move_first():
    """The builder renders this registry the way it renders match_modes, and
    ORDER is load-bearing: the blank definition reads position 0 as its
    default, and he ruled "move" the default for new definitions."""
    from sm64_events.tracking.segments import vocab
    served = vocab()["clock_starts"]
    assert [entry["key"] for entry in served] == ["move", "trigger"]
    assert all(entry["label"] and entry["description"] for entry in served)


def test_validation_rejects_an_unknown_clock_start():
    import pytest
    from sm64_events.tracking.segments import validate_definition
    base = {"name": "probe", "start_triggers": [{"type": "reset_game"}],
            "end_triggers": [{"type": "level_enter", "to": 5}], "guards": []}
    validate_definition({**base, "clock_start": "move"})
    validate_definition({**base, "clock_start": "trigger"})
    validate_definition(base)   # absent = trigger, every pre-round-15 row
    with pytest.raises(ValueError, match="clock_start"):
        validate_definition({**base, "clock_start": "usamune"})
