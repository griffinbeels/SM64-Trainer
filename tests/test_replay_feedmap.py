"""The frame map read off the picture feed's log (replay/feedmap.py).

Item 38: the ring holds one video frame per distinct captured picture and
the ledger's feed log says which row each write carried, so a clip's map
is bookkeeping -- frame k at start + frame_times[k] matches one feed entry
by wall time, and the entry's row names the game frame. Nothing here reads
pixels.
"""
from sm64_events.replay.feedmap import FEED_MATCH_TOLERANCE_S, feed_map

START = 1_000_000.0          # the clip's media origin, on the wall clock
PERIOD = 1 / 30


def rows_and_feeds(count: int, first_frame: int = 500, latency: float = 0.004,
                   bias: float = 0.0):
    """`count` pictures a game frame apart: each row composed at ts, fed
    `latency` later (the write completing), stamped with the RAM frame."""
    rows, feeds = [], []
    for index in range(count):
        ts = START + index * PERIOD
        rows.append({"ts": ts, "frame": first_frame + index, "phase": 0.010})
        feeds.append({"at": ts + latency + bias, "ts": ts})
    return rows, feeds


def clip_times(count: int, latency: float = 0.004, first: int = 0):
    """The clip's own frame times: ffmpeg stamped each frame at the read
    the write satisfied, so frame k sits `latency` after row k's ts."""
    return [(first + k) * PERIOD + latency for k in range(count)]


def test_every_frame_names_its_row_and_the_map_is_the_rows_stamps():
    rows, feeds = rows_and_feeds(12)
    built, repeats, stats = feed_map(clip_times(12), START, rows, feeds)
    # unwrapped_display: stamp - 1 - canonical; with one phase everywhere the
    # canonical split is 0, so consecutive rows are consecutive frames.
    assert built == [499 + k for k in range(12)]
    assert repeats == [False] * 12
    assert stats["matched"] == 12 and stats["unmatched"] == 0
    assert stats["repeats"] == 0


def test_a_cut_starting_mid_ring_matches_the_right_rows_not_the_first():
    rows, feeds = rows_and_feeds(40)
    # The clip starts at row 25: its media origin is 25 periods in.
    origin = START + 25 * PERIOD
    times = clip_times(10)
    built, _repeats, stats = feed_map(times, origin, rows, feeds)
    assert built == [499 + 25 + k for k in range(10)]
    assert stats["unmatched"] == 0


def test_a_constant_clock_bias_is_measured_and_removed_not_assumed():
    """The sink's segment anchor can hold a small constant offset against
    ffmpeg's stamps; the median delta absorbs it, like ledger_map's."""
    rows, feeds = rows_and_feeds(20, bias=0.009)
    built, _repeats, stats = feed_map(clip_times(20), START, rows, feeds)
    assert built == [499 + k for k in range(20)]
    assert abs(stats["bias_ms"] - 9.0) < 0.5
    assert stats["residual_ms"]["max"] < 0.5


def test_a_heartbeat_repeat_is_the_same_game_frame_and_is_flagged():
    rows, feeds = rows_and_feeds(6)
    # A 1 s hold after picture 5: the sink re-fed it, untagged.
    hold_at = feeds[-1]["at"] + 1.0
    feeds.append({"at": hold_at, "ts": None})
    times = clip_times(6) + [hold_at - START]
    built, repeats, stats = feed_map(times, START, rows, feeds)
    assert built[-1] == built[-2]
    assert repeats == [False] * 6 + [True]
    assert stats["repeats"] == 1


def test_the_same_feed_join_can_return_a_rows_coherent_clock_pair():
    rows, feeds = rows_and_feeds(6)
    for index, row in enumerate(rows):
        row["igt_overall"] = 40 + index
    pairs, repeats, stats = feed_map(
        clip_times(6), START, rows, feeds,
        row_value=lambda row: ((row["frame"], row["igt_overall"])
                               if row.get("igt_overall") is not None else None))
    assert pairs == [(500 + k, 40 + k) for k in range(6)]
    assert repeats == [False] * 6
    assert stats["matched"] == 6


def test_a_clip_from_before_the_igt_stamp_has_no_clock_pair_map():
    rows, feeds = rows_and_feeds(6)
    pairs, _repeats, stats = feed_map(
        clip_times(6), START, rows, feeds,
        row_value=lambda row: None)
    assert pairs is None
    assert stats["matched"] == 0


def test_a_frame_with_no_feed_entry_is_unknown_not_guessed():
    rows, feeds = rows_and_feeds(10)
    times = clip_times(10)
    # Frame 4's feed entry is missing (a dropped write): its slot is None,
    # every other frame still answers, and the count says so.
    del feeds[4]
    built, _repeats, stats = feed_map(times, START, rows, feeds)
    assert built[4] is None
    assert [v for k, v in enumerate(built) if k != 4] == [499 + k for k in range(10) if k != 4]
    assert stats["unmatched"] == 1


def test_one_feed_entry_answers_at_most_one_frame():
    rows, feeds = rows_and_feeds(5)
    # Two clip frames a millisecond apart cannot both be row 2.
    times = clip_times(5)
    times.insert(3, times[2] + 0.001)
    built, _repeats, stats = feed_map(times, START, rows, feeds)
    assert built[2] == 501 and built[3] is None
    assert stats["unmatched"] == 1


def test_jitter_beyond_the_tolerance_does_not_match_a_neighbour():
    rows, feeds = rows_and_feeds(8)
    times = clip_times(8)
    times[5] += FEED_MATCH_TOLERANCE_S * 1.5     # still nearer row 5 than row 6
    built, _repeats, _stats = feed_map(times, START, rows, feeds)
    assert built[5] is None


def test_too_few_matches_means_no_map():
    rows, feeds = rows_and_feeds(3)
    times = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0]        # nowhere near the feeds
    built, repeats, stats = feed_map(times, START, rows, feeds)
    # The measured bias can drag ONE frame onto a feed entry; the map is
    # still refused, which is the property that matters.
    assert built is None and stats["matched"] < 3
    assert repeats == [False] * 6


def test_a_capture_miss_shows_as_a_two_frame_step_never_a_filled_hole():
    """A picture the capture never grabbed has no row and no frame: the
    map steps by two there, and no frame is invented to hide it."""
    rows, feeds = rows_and_feeds(10)
    del rows[4]
    del feeds[4]
    times = clip_times(10)
    del times[4]
    built, _repeats, stats = feed_map(times, START, rows, feeds)
    assert built == [499, 500, 501, 502, 504, 505, 506, 507, 508]
    assert stats["unmatched"] == 0
