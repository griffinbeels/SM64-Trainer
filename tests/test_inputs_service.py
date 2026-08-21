from dataclasses import dataclass

import pytest

from sm64_events.inputs.document import encode
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.service import (InputsService, actions_of,
                                        entity_key_of, runs_of)
from sm64_events.inputs.templates import TemplateStore
from sm64_events.memory import addresses as A
from sm64_events.storage.db import Database

AT = "2026-08-20T21:00:00+00:00"
LATER = "2026-08-20T21:00:30+00:00"


@dataclass
class FakeAttempt:
    id: int = 7
    started_utc: str = AT
    ended_utc: str = LATER
    anchor_frame: int | None = None
    segment_id: int | None = None
    course_id: int | None = 24
    star_id: int | None = 1
    strat_tag: str | None = "10 coin"


def frames(spec):
    return [(number, InputFrame(buttons, 0, stick_x, stick_y))
            for number, buttons, stick_x, stick_y in spec]


@pytest.fixture
def rig(tmp_path):
    db = Database(tmp_path / "t.db")
    session = db.insert_session(AT)
    db.inputs.append(session, frames(
        [(100 + n, 0, -45, -45) for n in range(13)]
        + [(113, 0x8000, -45, -45)]
        + [(114 + n, 0xC000, -45, -45) for n in range(14)]), AT, LATER)
    templates = TemplateStore(db._conn, db._lock)
    attempt = FakeAttempt()
    service = InputsService(db.inputs, templates, lambda: [attempt])
    return service, templates, attempt


def test_runs_collapse_and_are_zero_based():
    got = runs_of(frames([(500, 0x8000, 1, 2), (501, 0x8000, 1, 2),
                          (502, 0, 0, 0)]))
    # A run is [start, length, buttons, stick_x, stick_y, yaw] -- yaw joined in
    # round 32, so Mario's facing turning under a held stick breaks a run.
    assert got == [[0, 2, 0x8000, 1, 2, 0, 0.0], [2, 1, 0, 0, 0, 0, 0.0]]


def test_a_gap_starts_a_new_run_at_its_own_offset():
    got = runs_of(frames([(0, 0x8000, 0, 0), (5, 0x8000, 0, 0)]))
    assert got == [[0, 1, 0x8000, 0, 0, 0, 0.0], [5, 1, 0x8000, 0, 0, 0, 0.0]]


def test_both_kinds_of_attempt_answer_an_entity_key():
    """Star-segment parity: an input timeline belongs to a practiced thing."""
    assert entity_key_of(FakeAttempt()) == ("star", "24-1")
    assert entity_key_of(FakeAttempt(segment_id=12,
                                     course_id=None, star_id=None)) == \
        ("segment", "12")


def test_the_timeline_carries_the_runs_and_the_span(rig):
    service, _templates, _attempt = rig
    payload = service.timeline(7)
    assert payload["frames"] == 28
    assert payload["runs"][0] == [0, 13, 0, -45, -45, 0, 0.0]
    assert payload["target"] == "star 24 1"
    assert payload["strategy"] == "10 coin"


def test_the_timeline_SENDS_the_button_table(rig):
    """The browser must never name a button bit. Sending the table means the
    duplicate cannot be written, which is stronger than a test that compares
    two copies of it."""
    service, _templates, _attempt = rig
    payload = service.timeline(7)
    assert payload["buttons"] == [[bit, name] for bit, name in A.BUTTON_BITS]
    assert payload["stick_max"] == A.STICK_MAX


def test_no_template_reads_as_none_rather_than_an_empty_one(rig):
    service, _templates, _attempt = rig
    assert service.timeline(7)["template"] is None


def test_an_active_template_rides_along_with_its_own_runs(rig):
    service, templates, _attempt = rig
    templates.save(kind="star", entity_key="24-1", strat_tag="10 coin",
                   name="the good one", origin="attempt:3",
                   document=encode(frames([(0, 0x8000, 10, 10)]),
                                   target="star 24 1", strategy="10 coin",
                                   version="us", origin="attempt 3"))
    payload = service.timeline(7)
    assert payload["template"]["name"] == "the good one"
    assert payload["template"]["runs"] == [[0, 1, 0x8000, 10, 10, 0, 0.0]]


def test_a_template_that_stopped_loading_says_so_instead_of_drawing_nothing(rig):
    """A document can be hand-edited after it is stored. Silently drawing one
    track would leave him wondering which of the two is missing."""
    service, templates, _attempt = rig
    templates.save(kind="star", entity_key="24-1", strat_tag="10 coin",
                   name="edited by hand", origin="authored",
                   document=encode(frames([(0, 0x8000, 10, 10)]),
                                   target="star 24 1", strategy="10 coin",
                                   version="us", origin="authored"))
    row = templates.active_for("star", "24-1", "10 coin")
    with templates._lock:
        templates._conn.execute(
            "UPDATE input_templates SET document=? WHERE id=?",
            ("# sm64-inputs v1\ntarget: x\nversion: us\nfps: 30\n"
             "origin: authored\n--\nnonsense\n", row.id))
        templates._conn.commit()
    payload = service.timeline(7)
    assert payload["template"]["runs"] == []
    assert "nonsense" in payload["template"]["error"]


def test_an_unknown_attempt_is_a_lookup_error(rig):
    service, _templates, _attempt = rig
    with pytest.raises(LookupError):
        service.timeline(999)


def test_an_attempt_with_no_captured_input_reads_as_an_empty_track(rig):
    """A finding, not an error: it was played before capture existed."""
    service, templates, _attempt = rig
    empty = FakeAttempt(id=8, started_utc="2026-08-01T00:00:00+00:00",
                        ended_utc="2026-08-01T00:00:10+00:00")
    service._attempts = lambda: [empty]
    payload = service.timeline(8)
    assert payload["runs"] == []
    assert payload["frames"] == 0


def test_a_counter_that_restarts_lays_the_next_stretch_END_TO_END():
    """The x-axis is a position in the CAPTURE, not the raw counter.

    The game's frame counter restarts on a console reset, so a track spanning
    one contains a descending number. Zero-basing on the first frame alone
    gives NEGATIVE offsets and a timeline reading "-937 frames" -- which is
    what the fixture render showed before this (2026-08-21).
    """
    got = runs_of(frames([(900, 0x8000, 0, 0), (901, 0x8000, 0, 0),
                          (12, 0x4000, 0, 0), (13, 0x4000, 0, 0)]))
    assert got == [[0, 2, 0x8000, 0, 0, 0, 0.0], [2, 2, 0x4000, 0, 0, 0, 0.0]]


def test_a_hole_INSIDE_a_stretch_still_reads_as_a_hole():
    """A reset is a seam in the recording; a hole is a hole. Laying stretches
    end to end must not also close the gaps within one."""
    got = runs_of(frames([(0, 0x8000, 0, 0), (9, 0x8000, 0, 0)]))
    assert got == [[0, 1, 0x8000, 0, 0, 0, 0.0], [9, 1, 0x8000, 0, 0, 0, 0.0]]


def test_the_span_is_never_negative_however_the_counter_moves(rig):
    service, _templates, _attempt = rig
    for spec in ([(900, 0, 0, 0), (5, 0, 0, 0)],
                 [(5, 0, 0, 0), (900, 0, 0, 0)],
                 [(7, 0, 0, 0)]):
        runs = runs_of(frames(spec))
        assert runs[0][0] == 0
        assert runs[-1][0] + runs[-1][1] > 0


# --- round 32: Mario's own state alongside the pad --------------------------

def test_the_action_row_reads_spans_not_a_field_on_every_run():
    """A run breaks whenever the pad moves and an action lasts across dozens
    of those, so the action rides its OWN list -- otherwise one fact is
    repeated hundreds of times and the reader still has to stitch it back."""
    rows = [(n, InputFrame(0, 0, n, 0, 0x0C400201, 0)) for n in range(5)]
    rows += [(5 + n, InputFrame(0, 0, n, 0, 0x0188088A, 0)) for n in range(5)]
    spans = actions_of(rows)
    assert [(s["start"], s["length"]) for s in spans] == [(0, 5), (5, 5)]


def test_a_known_action_reads_by_NAME_and_an_unknown_one_by_its_GROUP():
    """Never a bare hex id: a number nobody can read is not diagnostic
    information, however honestly it was captured."""
    known = actions_of([(0, InputFrame(0, 0, 0, 0, A.ACT_DIVE, 0))])[0]
    assert known["label"] == "dive"
    unknown = actions_of([(0, InputFrame(0, 0, 0, 0, 0x0300088C, 0))])[0]
    assert unknown["label"] == "airborne" == unknown["group"]


def test_the_action_spans_survive_a_counter_restart_like_the_runs_do():
    rows = [(900, InputFrame(0, 0, 0, 0, 5, 0)),
            (901, InputFrame(0, 0, 0, 0, 5, 0)),
            (12, InputFrame(0, 0, 0, 0, 9, 0))]
    spans = actions_of(rows)
    assert [(s["start"], s["length"]) for s in spans] == [(0, 2), (2, 1)]


def test_the_yaw_rides_on_each_run_because_it_moves_every_frame():
    """Unlike the action, facing changes continuously -- a span list for it
    would be one span per frame, which is the shape it already had."""
    rows = [(0, InputFrame(0, 0, 0, 0, 0, 100)),
            (1, InputFrame(0, 0, 0, 0, 0, 200))]
    assert [run[5] for run in runs_of(rows)] == [100, 200]


def test_speed_rides_on_each_run_too():
    rows = [(0, InputFrame(0, 0, 0, 0, 0, 0, 12.5)),
            (1, InputFrame(0, 0, 0, 0, 0, 0, 31.25))]
    assert [run[6] for run in runs_of(rows)] == [12.5, 31.25]


def test_the_timeline_sends_the_angle_units_so_the_browser_does_no_maths(rig):
    service, _templates, _attempt = rig
    payload = service.timeline(7)
    assert payload["angle_units"] == A.ANGLE_UNITS
    assert isinstance(payload["actions"], list)
