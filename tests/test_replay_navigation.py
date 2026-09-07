"""A held pre-buffer picture must not invent a long input timeline."""
from sm64_events.replay.navigation import captured_input_span, attempt_start_slot


def test_previous_attempt_heartbeat_does_not_extend_the_input_axis():
    # Pyramid #32: the first two encoded pictures retained the previous
    # attempt; capture resumed 1,398 counters later inside this clip.
    meta = {"frame_map": [140152, 140152, 141550, None, 141612, 142199],
            "repeats": [True, True, False, False, False, False]}
    assert captured_input_span(meta) == [141550, 142199]
    assert attempt_start_slot(meta, 141611) == 4
    assert meta["frame_map"][:2] == [140152, 140152]  # retained picture identity


def test_real_capture_gaps_and_buffers_remain_in_the_extent():
    meta = {"frame_map": [80, 90, 100, 120, 130, 130],
            "repeats": [False, False, False, False, False, True]}
    assert captured_input_span(meta) == [80, 130]
    assert attempt_start_slot(meta, 100) == 2


def test_a_held_only_clip_cannot_define_a_fresh_input_span():
    assert captured_input_span({"frame_map": [10, 10], "repeats": [True, True]}) is None
    assert captured_input_span({}) is None


def test_start_does_not_guess_an_occurrence_after_a_counter_reset():
    assert attempt_start_slot({"frame_map": [100, 101, 99, 100]}, 100) is None
    assert attempt_start_slot({"frame_map": [99, None, 100, 100, 101]}, 100) == 2
    assert attempt_start_slot({"frame_map": [105, 106]}, 100) == 0
    assert attempt_start_slot({"frame_map": [98, 99]}, 100) is None
    assert attempt_start_slot({}, None) is None
