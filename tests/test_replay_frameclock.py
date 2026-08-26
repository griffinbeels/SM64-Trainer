"""The frame clock: wall time -> game frame, per video frame of a clip.

The shapes that matter are his own sightings: a game frame lasting two
video frames (the duplicate), a game frame the footage never shows (the
skip), and a shift that appears mid-clip and heals -- all of which a
constant offset cannot express and a map states directly.

The v4 tests build a PHYSICAL WORLD first -- true present times, the frame
each present put on screen, the logic clock running beside them -- and
assert the map against that truth, never against the derivation's own
arithmetic. The one constant the world shares with the code is
PRESENT_LAG_FRAMES: at the earliest-phase tick of a run the screen trails
logic by exactly that many frames, which is the constant's definition.
"""
from datetime import datetime, timezone

from sm64_events.replay.frameclock import (FrameClock, PRESENT_LAG_FRAMES,
                                           derive_present_frames)


def clock_at(pairs):
    clock = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    for wall, frame in pairs:
        clock._now = lambda wall=wall: wall
        clock.mark(frame)
    return clock


def utc(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc)


def test_a_steady_30fps_game_maps_two_video_frames_per_game_frame():
    clock = clock_at([(100.0 + n / 30, 1000 + n) for n in range(30)])
    got, source = clock.frame_map(utc(100.0), 0.5, 60, lag_s=0.0)
    expected = []
    for game_frame in range(1000, 1015):
        expected += [game_frame, game_frame]
    assert got == expected
    assert source == "edges"


def test_a_long_game_frame_is_a_duplicate_and_a_catchup_is_a_skip():
    # Frame 1001 arrives LATE (the emulator stalled half a game frame), so
    # its predecessor holds the screen an extra video frame; 1002 lands on
    # schedule, so 1001 gets only one slot -- his counter's 27, 27, 29 shape.
    clock = clock_at([(100.0, 1000), (100.0 + 1.5 / 30, 1001),
                      (100.0 + 2 / 30, 1002), (100.0 + 3 / 30, 1003)])
    got, _source = clock.frame_map(utc(100.0), 4 / 30, 60, lag_s=0.0)
    assert got == [1000, 1000, 1000, 1001, 1002, 1002, 1003, 1003]


def test_the_lag_shifts_which_game_frame_a_picture_shows():
    clock = clock_at([(100.0 + n / 30, 1000 + n) for n in range(10)])
    plain, _source = clock.frame_map(utc(100.0), 0.2, 60, lag_s=0.0)
    lagged, _source = clock.frame_map(utc(100.0), 0.2, 60, lag_s=1 / 30)
    # One game frame of display lag: every slot answers one frame older.
    assert lagged[2:] == [frame - 1 for frame in plain[2:] if frame]


def test_a_clip_outside_the_marked_span_has_no_map():
    clock = clock_at([(100.0, 1000)])
    assert clock.frame_map(utc(500.0), 1.0, 60, lag_s=0.0) is None
    assert clock.frame_map(utc(10.0), 1.0, 60, lag_s=0.0) is None
    assert FrameClock().frame_map(utc(100.0), 1.0, 60, lag_s=0.0) is None


def test_slots_before_coverage_read_none_not_a_guess():
    clock = clock_at([(100.0, 1000), (100.0 + 1 / 30, 1001)])
    got, _source = clock.frame_map(utc(99.9), 0.2, 30, lag_s=0.0)
    assert got[0] is None and got[-1] == 1001

# --- v2: the fed-tag series (scored on his clip 741, 2026-08-23) -------------

def test_feeds_beat_the_edge_series_and_carry_the_lag_in_frames():
    clock = clock_at([(100.0 + n / 30, 1000 + n) for n in range(30)])
    # The feeder fed a picture tagged 1005 at 100.5, then 1006 at 100.533...
    for n in range(15):
        wall = 100.0 + n / 60
        clock._now = lambda t=wall: t
        clock.mark_feed((1000 + n // 2, None))
    got, source = clock.frame_map(utc(100.0), 0.2, 60, lag_s=1 / 30)
    # Slot k takes the last feed at or before its midpoint, minus one frame
    # of pipeline depth -- per-slot, not per-clock.
    assert got == [999, 999, 1000, 1000, 1001, 1001, 1002, 1002, 1003, 1003,
                   1004, 1004]
    assert source == "feeds"


def test_a_feeder_stall_holds_the_last_fed_tag_like_ffmpegs_dup_does():
    clock = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    for wall, tag in [(100.0, 1000), (100.0 + 1 / 60, 1001),
                      (100.0 + 10 / 60, 1002)]:      # a 9-slot stall
        clock._now = lambda t=wall: t
        clock.mark_feed((tag, None))
    got, _source = clock.frame_map(utc(100.0), 12 / 60, 60, lag_s=0.0)
    assert got[0] == 1000 and got[1:10] == [1001] * 9 and got[10] == 1002


def test_untagged_feeds_record_nothing_and_the_edge_series_answers():
    clock = clock_at([(100.0 + n / 30, 1000 + n) for n in range(10)])
    clock.mark_feed(None)
    got, source = clock.frame_map(utc(100.0), 0.1, 60, lag_s=0.0)
    assert got is not None and got[2] == 1001  # the v1 path, unchanged
    assert source == "edges"


def test_latest_frame_is_the_last_marked_edge():
    clock = clock_at([(100.0, 7), (100.1, 8)])
    assert clock.latest_frame() == 8
    assert FrameClock().latest_frame() is None


def test_capture_tag_carries_the_frame_and_the_composition_time():
    clock = clock_at([(100.0, 7)])
    assert clock.capture_tag(100.01) == (7, 100.01)
    assert clock.capture_tag(None) == (7, None)
    assert FrameClock().capture_tag(100.01) == (None, 100.01)
    assert FrameClock().capture_tag(None) is None

# --- v4: the present series (item 30; probe_host_present.py's counter) -------
#
# The physical model: present tick n happens at true time
# 100 + n/30 + phase_n and puts frame (999 + n) on the screen, so at a
# minimum-phase tick the logic clock (frame 1000+n by then) is exactly
# PRESENT_LAG_FRAMES ahead of the screen. The logic clock itself reads
# 1000 + floor((t - 100) * 30). phase drifting past 1/30 is the wobble his
# footage shows: the SAME picture sequence, presented late enough that a
# constant-lag stamp files it under the wrong frame.

COUNT0 = 5000        # the counter's arbitrary heap value at world frame 0


def world_timer(wall: float) -> int:
    return 1000 + int((wall - 100.0) * 30 + 1e-9)


def build_world(clock, phases, first_frame=0):
    """Mark presents for the given per-tick phases; return the truth
    timeline [(true present time, frame on screen)]."""
    truth = []
    for offset, phase in enumerate(phases):
        tick_index = first_frame + offset
        wall = 100.0 + tick_index / 30 + phase
        clock._now = lambda t=wall: t
        clock.mark_present(COUNT0 + tick_index, world_timer(wall))
        truth.append((wall, 999 + tick_index))
    return truth


def truth_at(truth, shown_at: float):
    frame = None
    for wall, shown in truth:
        if wall <= shown_at:
            frame = shown
    return frame


def feed_clip(clock, start: float, slots: int):
    """A healthy 60 Hz capture+feed chain over the clip: each slot's feed
    carries the picture composed 3 ms before the feed, tagged with the v2
    RAM-frame stamp of that instant."""
    capture_times = []
    for index in range(slots + 1):
        feed_wall = start + index / 60
        capture_ts = feed_wall - 0.003
        clock._now = lambda t=feed_wall: t
        clock.mark_feed((world_timer(capture_ts), capture_ts))
        capture_times.append(capture_ts)
    return capture_times


def test_presents_place_the_wobble_where_the_screen_put_it():
    clock = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    # Ten steady ticks, ten drifted past the next logic edge, ten steady:
    # a shift that appears mid-clip and heals, his second sighting's shape.
    phases = [0.005] * 10 + [0.036] * 10 + [0.005] * 10
    truth = build_world(clock, phases)
    start = 100.2
    slots = 24            # 0.4 s at 60 fps, inside present coverage
    feed_clip(clock, start, slots)
    got, source = clock.frame_map(utc(start), slots / 60, 60, lag_s=1 / 30)
    assert source == "presents"
    # Slot k's feed is the one at start + k/60 <= midpoint; its capture
    # sat 3 ms earlier -- the truth is what the screen held at that instant.
    expected = []
    for index in range(slots):
        midpoint = start + (index + 0.5) / 60
        last_feed = start + (int((midpoint - start) * 60)) / 60
        expected.append(truth_at(truth, last_feed - 0.003))
    assert got == expected
    # And the truth is NOT what the v2 constant-lag stamp would say: the
    # drifted run files at least one picture under a different frame.
    v2_says = []
    for index in range(slots):
        midpoint = start + (index + 0.5) / 60
        last_feed = start + (int((midpoint - start) * 60)) / 60
        v2_says.append(world_timer(last_feed - 0.003) - PRESENT_LAG_FRAMES)
    assert got != v2_says


def test_a_represent_train_never_fabricates_frames_past_the_frozen_clock():
    """Logic freezes (a load) while the plugin keeps re-presenting the
    frozen picture: consuming-in-order does not hold there, so the present
    series must refuse those ticks rather than count new frames into
    existence. The last true picture holds for PRESENT_HOLD_S (the screen
    IS frozen), then the feed tags answer."""
    clock = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    truth = build_world(clock, [0.005] * 10)   # frames 999..1008, to 100.305
    # Forty-five more counter ticks (1.5 s) with the logic clock frozen at
    # 1009 -- the frame the game finished and never advanced past.
    for extra in range(45):
        wall = 100.0 + (10 + extra) / 30 + 0.005
        clock._now = lambda t=wall: t
        clock.mark_present(COUNT0 + 10 + extra, 1009)
    start, slots = 100.4, 84                   # 1.4 s of clip
    for index in range(slots + 1):
        feed_wall = start + index / 60
        capture_ts = feed_wall - 0.003
        clock._now = lambda t=feed_wall: t
        clock.mark_feed((min(world_timer(capture_ts), 1009), capture_ts))
    got, source = clock.frame_map(utc(start), slots / 60, 60, lag_s=0.0)
    assert all(frame is not None for frame in got)
    assert not any(frame > 1009 for frame in got), \
        "fabricated frames past the frozen logic clock"
    # Within the hold window the frozen picture answers exactly...
    assert got[0] == truth[-1][1] == 1008
    # ...and past it the feed tags take over, so both series spoke.
    assert got[-1] == 1009 and source == "mixed"


def test_straddled_ticks_still_carry_their_count():
    clock = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    phases = [0.005] * 12
    truth = []
    for tick_index, phase in enumerate(phases):
        wall = 100.0 + tick_index / 30 + phase
        clock._now = lambda t=wall: t
        timer = None if tick_index % 3 == 1 else world_timer(wall)
        clock.mark_present(COUNT0 + tick_index, timer)
        truth.append((wall, 999 + tick_index))
    start, slots = 100.1, 12
    feed_clip(clock, start, slots)
    got, source = clock.frame_map(utc(start), slots / 60, 60, lag_s=0.0)
    assert source == "presents"
    for index in range(slots):
        midpoint = start + (index + 0.5) / 60
        last_feed = start + (int((midpoint - start) * 60)) / 60
        assert got[index] == truth_at(truth, last_feed - 0.003)


def test_a_counter_reset_splits_the_run_and_recovers():
    clock = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    truth = build_world(clock, [0.005] * 10)
    # The plugin restarted: the counter starts over at 12, the world (and
    # the logic clock) march on.
    for offset in range(10):
        tick_index = 10 + offset
        wall = 100.0 + tick_index / 30 + 0.005
        clock._now = lambda t=wall: t
        clock.mark_present(12 + offset, world_timer(wall))
        truth.append((wall, 999 + tick_index))
    start, slots = 100.15, 24
    feed_clip(clock, start, slots)
    got, source = clock.frame_map(utc(start), slots / 60, 60, lag_s=0.0)
    assert source == "presents"
    for index in range(slots):
        midpoint = start + (index + 0.5) / 60
        last_feed = start + (int((midpoint - start) * 60)) / 60
        assert got[index] == truth_at(truth, last_feed - 0.003)


def test_without_feeds_presents_answer_by_slot_wall_time():
    """The in-process encoder path has no feed series; presents still beat
    the edge series there, keyed on the slot's own wall time."""
    clock = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    truth = build_world(clock, [0.005] * 20)
    start, slots = 100.1, 12
    got, source = clock.frame_map(utc(start), slots / 60, 60, lag_s=0.0)
    assert source == "presents"
    for index in range(slots):
        midpoint = start + (index + 0.5) / 60
        assert got[index] == truth_at(truth, midpoint)


def test_presents_that_cover_half_a_clip_yield_a_mixed_map():
    clock = clock_at([(100.0 + n / 30, 1000 + n) for n in range(30)])
    clock._present_trail_s = 0.002       # this world has no compose stage
    clock._map_wall_bias_s = 0.0         # ...and no CFR half-slot
    # The hunt landed mid-clip: presents exist only from 100.5 on.
    for offset in range(15):
        wall = 100.5 + offset / 30 + 0.005
        clock._now = lambda t=wall: t
        clock.mark_present(COUNT0 + offset, world_timer(wall))
    start, slots = 100.1, 48
    feed_clip(clock, start, slots)
    got, source = clock.frame_map(utc(start), slots / 60, 60, lag_s=1 / 30)
    assert source == "mixed"
    assert all(frame is not None for frame in got)


def test_derive_refuses_a_rate_that_is_not_a_present_counter():
    # A 300/s counter that survived the hunt by luck must not become a map.
    ticks = [(100.0 + offset / 300, COUNT0 + offset,
              world_timer(100.0 + offset / 300)) for offset in range(300)]
    assert derive_present_frames(ticks) == []


def test_the_shipped_trail_stays_inside_its_physical_bounds():
    """The law, not the value (a scored clip re-measures the value): the
    trail can never be negative -- a tick observed before it happened --
    and never longer than one poll interval plus one 60 Hz refresh plus
    slack, because that is the whole chain it models."""
    from sm64_events.replay.frameclock import PRESENT_TICK_TRAIL_S
    assert 0.0 <= PRESENT_TICK_TRAIL_S <= 0.030


def test_the_wall_bias_shifts_every_series_exactly_one_slot():
    """The measured half-slot (attempt 2147: feeds 10/10 at -1, presents
    25/27 at -1) is absorbed at the ONE place slot walls are computed, so
    a biased map answers slot k with what the unbiased map answered at
    k+1 -- for every series."""
    marks = [(100.0 + n / 30, 1000 + n) for n in range(30)]
    plain = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                       map_wall_bias_s=0.0)
    biased = FrameClock(now=lambda: 0.0, present_trail_s=0.002,
                        map_wall_bias_s=1 / 60)
    for clock in (plain, biased):
        for wall, frame in marks:
            clock._now = lambda t=wall: t
            clock.mark(frame)
    got_plain, _source = plain.frame_map(utc(100.0), 0.4, 60, lag_s=0.0)
    got_biased, _source = biased.frame_map(utc(100.0), 0.4, 60, lag_s=0.0)
    assert got_biased[:-1] == got_plain[1:]


def test_two_counters_with_different_phases_derive_the_same_map():
    """The phase normalization (measured 2026-08-25: two sessions, two
    family members, seventeen milliseconds of phase between them): with
    the edge series to anchor on, WHICH counter the hunt picked must not
    change a single derived time."""
    edges = [(100.0 + n / 30, 1000 + n) for n in range(30)]
    def ticks_at(phase, count0):
        return [(100.0 + n / 30 + phase, count0 + n,
                 world_timer(100.0 + n / 30 + phase)) for n in range(30)]
    early = derive_present_frames(ticks_at(0.004, 5000), edge_pairs=edges)
    late = derive_present_frames(ticks_at(0.021, 9000), edge_pairs=edges)
    assert len(early) == len(late) == 30
    for (wall_a, frame_a), (wall_b, frame_b) in zip(early, late):
        assert frame_a == frame_b
        assert abs(wall_a - wall_b) < 1e-9


def test_normalized_ticks_keep_their_wander():
    """Anchoring on the run MEDIAN removes the counter's phase, never the
    per-tick deviation -- the display information presents exist for."""
    edges = [(100.0 + n / 30, 1000 + n) for n in range(30)]
    wander = [0.004 + (0.006 if 10 <= n < 20 else 0.0) for n in range(30)]
    ticks = [(100.0 + n / 30 + wander[n], 5000 + n,
              world_timer(100.0 + n / 30 + wander[n])) for n in range(30)]
    derived = derive_present_frames(ticks, edge_pairs=edges)
    gaps = [b[0] - a[0] for a, b in zip(derived, derived[1:])]
    assert max(gaps) > 1 / 30 + 0.004      # the wander survived
    assert min(gaps) < 1 / 30 - 0.004


def test_the_shipped_bias_stays_inside_its_physical_bounds():
    from sm64_events.replay.frameclock import MAP_WALL_BIAS_S
    assert 0.0 <= MAP_WALL_BIAS_S <= 1 / 30
