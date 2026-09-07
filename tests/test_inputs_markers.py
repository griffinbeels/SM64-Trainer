"""The JOIN between the journal and an input track: a moment he caused lands
on the frame of the capture axis where it happened, through the same sentence
the segment recorder gives it."""
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.markers import markers_of
from sm64_events.storage.db import EventRow


def jev(id, type, frame, payload):
    return EventRow(id=id, session_id=1, seq=id, type=type, frame=frame,
                    wall_time_utc="2026-08-20T21:00:00+00:00", payload=payload)


def frames(numbers):
    return [(number, InputFrame(0, 0, 0, 0)) for number in numbers]


def pole(id, frame, ordinal=1):
    return jev(id, "moment_reached", frame,
               {"kind": "pole_grab", "level": 9, "ordinal": ordinal})


def test_a_moment_lands_on_the_capture_axis_not_the_raw_counter():
    track = frames(range(1000, 1010))
    [marker] = markers_of([pole(1, 1004)], track, {})
    assert marker == {"frame": 4, "type": "moment_reached",
                      "label": "Grab a pole in Bob-omb Battlefield"}


def test_a_moment_in_a_capture_hole_sits_where_it_happened():
    # Frames 1003-1005 were never captured; the axis keeps that hole (the
    # track reads 0,1,2,6,7), and the pole grab at 1004 draws inside it.
    track = frames([1000, 1001, 1002, 1006, 1007])
    [marker] = markers_of([pole(1, 1004)], track, {})
    assert marker["frame"] == 4


def test_a_moment_after_a_console_reset_finds_the_second_stretch():
    # The counter restarted at 50 mid-track: raw 52 appears nowhere in the
    # first stretch, and the axis continues rather than restarting.
    track = frames(list(range(1000, 1005)) + list(range(50, 55)))
    [marker] = markers_of([pole(1, 52)], track, {})
    assert marker["frame"] == 7


def test_a_moment_outside_the_track_is_not_a_marker():
    track = frames(range(1000, 1010))
    assert markers_of([pole(1, 999), pole(2, 1010)], track, {}) == []


def test_only_steps_he_did_become_markers():
    """The anchor itself (a practice reset at frame 0) is the track's origin,
    not a thing to mark; a journal row with no sentence never draws."""
    track = frames(range(1000, 1010))
    rows = [jev(1, "practice_reset", 1000, {}),
            jev(2, "rollout", 1002, {"action": 1}),
            jev(3, "star_collected", 1008,
                {"course_id": 9, "star_id": 1, "igt_frames": 100})]
    got = markers_of(rows, track, {})
    assert [m["type"] for m in got] == ["star_collected"]
    assert got[0]["frame"] == 8


def test_repeated_sentences_count_like_the_recorder_does():
    track = frames(range(1000, 1010))
    got = markers_of([pole(1, 1001), pole(2, 1005, ordinal=2)], track, {})
    assert [m["label"] for m in got] == [
        "Grab a pole in Bob-omb Battlefield",
        "Grab a pole (#2) in Bob-omb Battlefield"]


def test_a_named_landmark_reads_by_its_name():
    track = frames(range(1000, 1010))
    row = jev(1, "moment_reached", 1003,
              {"kind": "pole_grab", "level": 9, "ordinal": 1,
               "landmark": {"key": "9:1:bb:1,2,3"}})
    [marker] = markers_of([row], track, {"9:1:bb:1,2,3": "BoB Tree"})
    assert marker["label"] == "Grab the BoB Tree in Bob-omb Battlefield"
