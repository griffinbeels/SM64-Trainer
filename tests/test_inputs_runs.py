"""The one run derivation every reader of a track shares.

Four readers used to fold frames into runs with four loops; the document's
copy wrote a backwards gap row across a counter restart. Now each reader
says only what makes two frames the same, and the seam handling is here once.
"""
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.runs import Run, capture_axis, collapse


def pad(buttons=0, stick_x=0, stick_y=0, yaw=0):
    return InputFrame(buttons, 0, stick_x, stick_y, 0, yaw)


def test_consecutive_equal_frames_fold_into_one_run():
    runs = collapse([(10, pad(1)), (11, pad(1)), (12, pad(1))])
    assert runs == [Run(10, 3, pad(1))]
    assert runs[0].end == 13


def test_a_changed_frame_starts_a_new_run():
    runs = collapse([(10, pad(1)), (11, pad(2))])
    assert [(run.start, run.length) for run in runs] == [(10, 1), (11, 1)]


def test_a_skipped_number_starts_a_new_run_even_when_the_state_is_equal():
    """A hole survives as a hole; nothing interpolates across it."""
    runs = collapse([(10, pad(1)), (12, pad(1))])
    assert [(run.start, run.length) for run in runs] == [(10, 1), (12, 1)]


def test_the_reader_says_what_counts_as_the_same():
    same_buttons = lambda frame, previous: frame.buttons == previous.buttons
    runs = collapse([(0, pad(1, yaw=5)), (1, pad(1, yaw=9))], same_buttons)
    assert len(runs) == 1


def test_a_run_is_capped_at_the_storage_format_s_length_field():
    runs = collapse([(number, pad(1)) for number in range(5)], max_length=2)
    assert [(run.start, run.length) for run in runs] == [(0, 2), (2, 2), (4, 1)]


def test_the_capture_axis_starts_at_zero():
    axis = capture_axis([(500, pad()), (501, pad())])
    assert [number for number, _ in axis] == [0, 1]


def test_a_counter_restart_lays_the_next_stretch_end_to_end():
    """A reset is a seam in the recording, not a jump backwards through it."""
    axis = capture_axis([(900, pad()), (901, pad()), (12, pad()), (13, pad())])
    assert [number for number, _ in axis] == [0, 1, 2, 3]


def test_a_hole_inside_a_stretch_stays_a_hole_on_the_axis():
    axis = capture_axis([(0, pad()), (9, pad())])
    assert [number for number, _ in axis] == [0, 9]


def test_an_empty_track_has_an_empty_axis():
    assert capture_axis([]) == []
    assert collapse([]) == []
