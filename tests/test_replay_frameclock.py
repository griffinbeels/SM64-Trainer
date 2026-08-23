"""The frame clock: wall time -> game frame, per video frame of a clip.

The shapes that matter are his own two sightings: a game frame lasting two
video frames (the duplicate), a game frame the footage never shows (the
skip), and a shift that appears mid-clip -- all of which a constant offset
cannot express and a map states directly.
"""
from datetime import datetime, timezone

from sm64_events.replay.frameclock import FrameClock


def clock_at(pairs):
    clock = FrameClock(now=lambda: 0.0)
    for wall, frame in pairs:
        clock._now = lambda wall=wall: wall
        clock.mark(frame)
    return clock


def utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_a_steady_30fps_game_maps_two_video_frames_per_game_frame():
    clock = clock_at([(100.0 + n / 30, 1000 + n) for n in range(30)])
    got = clock.frame_map(utc(100.0), 0.5, 60, lag_s=0.0)
    expected = []
    for game_frame in range(1000, 1015):
        expected += [game_frame, game_frame]
    assert got == expected


def test_a_long_game_frame_is_a_duplicate_and_a_catchup_is_a_skip():
    # Frame 1001 arrives LATE (the emulator stalled half a game frame), so
    # its predecessor holds the screen an extra video frame; 1002 lands on
    # schedule, so 1001 gets only one slot -- his counter's 27, 27, 29 shape.
    clock = clock_at([(100.0, 1000), (100.0 + 1.5 / 30, 1001),
                      (100.0 + 2 / 30, 1002), (100.0 + 3 / 30, 1003)])
    got = clock.frame_map(utc(100.0), 4 / 30, 60, lag_s=0.0)
    assert got == [1000, 1000, 1000, 1001, 1002, 1002, 1003, 1003]


def test_the_lag_shifts_which_game_frame_a_picture_shows():
    clock = clock_at([(100.0 + n / 30, 1000 + n) for n in range(10)])
    plain = clock.frame_map(utc(100.0), 0.2, 60, lag_s=0.0)
    lagged = clock.frame_map(utc(100.0), 0.2, 60, lag_s=1 / 30)
    # One game frame of display lag: every slot answers one frame older.
    assert lagged[2:] == [frame - 1 for frame in plain[2:] if frame]


def test_a_clip_outside_the_marked_span_has_no_map():
    clock = clock_at([(100.0, 1000)])
    assert clock.frame_map(utc(500.0), 1.0, 60, lag_s=0.0) is None
    assert clock.frame_map(utc(10.0), 1.0, 60, lag_s=0.0) is None
    assert FrameClock().frame_map(utc(100.0), 1.0, 60, lag_s=0.0) is None


def test_slots_before_coverage_read_none_not_a_guess():
    clock = clock_at([(100.0, 1000), (100.0 + 1 / 30, 1001)])
    got = clock.frame_map(utc(99.9), 0.2, 30, lag_s=0.0)
    assert got[0] is None and got[-1] == 1001
