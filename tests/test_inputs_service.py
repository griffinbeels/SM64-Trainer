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
    rta_frames: int | None = None
    segment_id: int | None = None
    course_id: int | None = 24
    star_id: int | None = 1
    strat_tag: str | None = "10 coin"


def frames(spec):
    return [(number, InputFrame(buttons, 0, stick_x, stick_y))
            for number, buttons, stick_x, stick_y in spec]


def run(start, length, buttons, stick_x, stick_y, yaw=0, speed=0.0):
    return {"start": start, "length": length, "buttons": buttons,
            "stick_x": stick_x, "stick_y": stick_y, "yaw": yaw,
            "speed": speed}


@pytest.fixture
def rig(tmp_path):
    db = Database(tmp_path / "t.db")
    session = db.insert_session(AT)
    db.inputs.append(session, frames(
        [(100 + n, 0, -45, -45) for n in range(13)]
        + [(113, 0x8000, -45, -45)]
        + [(114 + n, 0xC000, -45, -45) for n in range(14)]), AT, LATER)
    templates = db.input_templates
    attempt = FakeAttempt()
    service = InputsService(db.inputs, templates, lambda: [attempt])
    return service, templates, attempt


def test_runs_collapse_and_are_zero_based():
    got = runs_of(frames([(500, 0x8000, 1, 2), (501, 0x8000, 1, 2),
                          (502, 0, 0, 0)]))
    # A run carries the pad AND Mario's facing and speed (round 32), so his
    # facing turning under a held stick breaks a run.
    assert got == [run(0, 2, 0x8000, 1, 2), run(2, 1, 0, 0, 0)]


def test_a_gap_starts_a_new_run_at_its_own_offset():
    got = runs_of(frames([(0, 0x8000, 0, 0), (5, 0x8000, 0, 0)]))
    assert got == [run(0, 1, 0x8000, 0, 0), run(5, 1, 0x8000, 0, 0)]


def test_both_kinds_of_attempt_answer_an_entity_key():
    """Star-segment parity: an input timeline belongs to a practiced thing."""
    assert entity_key_of(FakeAttempt()) == ("star", "24-1")
    assert entity_key_of(FakeAttempt(segment_id=12,
                                     course_id=None, star_id=None)) == \
        ("segment", "12")


def test_the_timeline_carries_the_journals_moments_when_wired(rig):
    """Round 32 item 3: the moments ride the payload as `markers`, through
    the recorder's own sentence. A service wired without a journal reader
    (older callers, tests) simply carries none."""
    from sm64_events.storage.db import EventRow
    service, _templates, attempt = rig
    assert service.timeline(7)["markers"] == []
    attempt.anchor_frame = 100

    def events(started_utc, ended_utc):
        if ended_utc == attempt.started_utc:
            return []                     # the lead-in's level-entry search
        assert (started_utc, ended_utc) == (attempt.started_utc,
                                            attempt.ended_utc)
        return [EventRow(id=1, session_id=1, seq=1, type="moment_reached",
                         frame=105, wall_time_utc=started_utc,
                         payload={"kind": "pole_grab", "level": 9,
                                  "ordinal": 1})]

    service._events = events
    service._landmark_names = lambda: {}
    [marker] = service.timeline(7)["markers"]
    assert marker == {"frame": 5, "type": "moment_reached",
                      "label": "Grab a pole in Bob-omb Battlefield"}


def test_pad_lookup_answers_the_span_by_raw_frame(rig):
    """The pixel refiner's door: raw frame -> the pad Usamune's display
    draws (buttons, stick), over a wall-clock span."""
    service, _templates, _attempt = rig
    pads = service.pad_lookup(AT, 30.0)
    assert pads[100] == (0, -45, -45)
    assert pads[113] == (0x8000, -45, -45)
    assert len(pads) == 28


def test_the_timeline_ships_the_axis_seams(rig):
    """`stretches` is how a clip's frame_map (raw counter values) lands on
    the zero-based axis in the browser: one (axis_start, raw_start, length)
    per ascending stretch, split at a counter restart."""
    service, _templates, _attempt = rig
    assert service.timeline(7)["stretches"] == [[0, 100, 28]]

    from sm64_events.inputs.runs import stretches
    from sm64_events.inputs.frame import InputFrame
    reset = [(number, InputFrame(0, 0, 0, 0)) for number in
             [1000, 1001, 1002, 50, 51]]
    assert stretches(reset) == [(0, 1000, 3), (3, 50, 2)]


def test_the_timeline_carries_the_runs_and_the_span(rig):
    service, _templates, _attempt = rig
    payload = service.timeline(7)
    assert payload["frames"] == 28
    assert payload["runs"][0] == run(0, 13, 0, -45, -45)
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
    assert payload["template"]["runs"] == [run(0, 1, 0x8000, 10, 10)]


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
    assert got == [run(0, 2, 0x8000, 0, 0), run(2, 2, 0x4000, 0, 0)]


def test_a_hole_INSIDE_a_stretch_still_reads_as_a_hole():
    """A reset is a seam in the recording; a hole is a hole. Laying stretches
    end to end must not also close the gaps within one."""
    got = runs_of(frames([(0, 0x8000, 0, 0), (9, 0x8000, 0, 0)]))
    assert got == [run(0, 1, 0x8000, 0, 0), run(9, 1, 0x8000, 0, 0)]


def test_the_span_is_never_negative_however_the_counter_moves(rig):
    service, _templates, _attempt = rig
    for spec in ([(900, 0, 0, 0), (5, 0, 0, 0)],
                 [(5, 0, 0, 0), (900, 0, 0, 0)],
                 [(7, 0, 0, 0)]):
        runs = runs_of(frames(spec))
        assert runs[0]["start"] == 0
        assert runs[-1]["start"] + runs[-1]["length"] > 0


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
    assert [each["yaw"] for each in runs_of(rows)] == [100, 200]


def test_speed_rides_on_each_run_too():
    rows = [(0, InputFrame(0, 0, 0, 0, 0, 0, 12.5)),
            (1, InputFrame(0, 0, 0, 0, 0, 0, 31.25))]
    assert [each["speed"] for each in runs_of(rows)] == [12.5, 31.25]


def test_the_timeline_sends_the_angle_units_so_the_browser_does_no_maths(rig):
    service, _templates, _attempt = rig
    payload = service.timeline(7)
    assert payload["angle_units"] == A.ANGLE_UNITS
    assert isinstance(payload["actions"], list)


def test_the_template_carries_marios_rows_like_the_attempt_does(rig):
    """His ruling 2026-08-22: "compare my gameplay against the exact example,
    including all mario data". A template made from an attempt keeps the
    action, facing and speed, and the payload sends them in the run's shape."""
    service, templates, _attempt = rig
    rows = [(0, InputFrame(0x8000, 0, 10, 10, A.ACT_DIVE, -1234, 31.25)),
            (1, InputFrame(0x8000, 0, 10, 10, A.ACT_DIVE, -1200, 30.0))]
    templates.save(kind="star", entity_key="24-1", strat_tag="10 coin",
                   name="exact example", origin="import:exact",
                   document=encode(rows, target="star 24 1",
                                   strategy="10 coin", version="us",
                                   origin="x"))
    template = service.timeline(7)["template"]
    assert [each["yaw"] for each in template["runs"]] == [-1234, -1200]
    assert [each["speed"] for each in template["runs"]] == [31.25, 30.0]
    assert [each["label"] for each in template["actions"]] == ["dive"]


def test_the_timeline_spans_the_CLIP_when_the_caller_names_it(rig):
    """Round 32 item 53, correcting item 51. His rule: "the expectation for
    the input timeline is that it visibly matches the actual contents of the
    video shown... I would expect the duration of the timeline to match the
    exact duration of the video, including the before and after buffer." So
    the caller hands over the clip's own frame range and the track spans
    exactly that -- while FRAME 0 stays the attempt's start (`lead_frames`
    counts what precedes it) and the attempt's own length is untouched."""
    service, _templates, attempt = rig
    # Clear of the rig's own 100..127 chunk: two chunks holding the same
    # frame numbers would both answer and every count would double.
    attempt.anchor_frame = 1000
    attempt.igt_frames = 20
    attempt.rta_frames = 20
    service.store.append(1, frames([(980 + n, 0, 0, 0) for n in range(60)]),
                         AT, LATER)
    # The attempt itself: close = 1020, first = 1020 - 19 = 1001.
    plain = service.timeline(7)
    assert plain["lead_frames"] == 0 and plain["frames"] == 20
    # The clip carries 20 frames of run-up and 10 past the end.
    spanned = service.timeline(7, span=(981, 1030))
    assert spanned["lead_frames"] == 20        # 981..1000
    assert spanned["frames"] == 50             # 981..1030, buffers included


def test_a_span_can_only_widen_the_attempt_never_cut_it(rig):
    """A clip that starts after the attempt did (the ring evicted its
    run-up) must not shorten the timeline -- the attempt's own frames are
    the one thing the span may never take away."""
    service, _templates, attempt = rig
    attempt.anchor_frame = 1000
    attempt.igt_frames = 20
    attempt.rta_frames = 20
    service.store.append(1, frames([(980 + n, 0, 0, 0) for n in range(60)]),
                         AT, LATER)
    inside = service.timeline(7, span=(1010, 1015))
    assert inside["frames"] == 20 and inside["lead_frames"] == 0


def test_a_shifted_template_is_clipped_to_the_track(rig):
    """The template shifts right by the lead so frame 0 meets frame 0 --
    and is then CLIPPED. A template as long as the attempt otherwise runs
    off the right edge and the lanes overflow their own box (66 layout
    defects, measured 2026-08-31). What has nothing to compare against is
    not drawn."""
    from sm64_events.inputs.service import _shifted_spans
    spans = [{"start": 0, "length": 10}, {"start": 10, "length": 10}]
    assert _shifted_spans(spans, 0, 0) == spans
    moved = _shifted_spans(spans, 5, 18)
    assert moved == [{"start": 5, "length": 10}, {"start": 15, "length": 3}]
    assert _shifted_spans(spans, 20, 18) == []


def test_unshifted_template_clips_without_losing_its_full_length(rig):
    service, templates, _attempt = rig
    template = templates.save(kind="star", entity_key="24-1", strat_tag="10 coin",
                              name="longer", origin="authored",
                              document=encode(frames([(n, 0x8000, 0, 0) for n in range(40)]),
                                              target="star 24 1", strategy=None,
                                              version="us", origin="authored", author="friend"))
    payload = service.timeline(7)["template"]
    assert payload["frames"] == 40
    assert payload["author"] == "friend"
    assert payload["runs"] == [run(0, 28, 0x8000, 0, 0)]
    assert payload["actions"][0]["length"] == 28
    assert payload["source"]["frames"] == 40
    assert payload["source"]["runs"] == [run(0, 40, 0x8000, 0, 0)]
    assert payload["source"]["actions"][0]["length"] == 40
    assert len(payload["source"]["revision"]) == 64
    assert service._template_payload(template, limit=0)["runs"] == []
    shifted = service._template_payload(template, shift=20, limit=28)
    assert shifted["source"] == payload["source"]


def test_document_frame_numbers_are_already_an_axis(rig):
    service, templates, _attempt = rig
    text = encode([], target="star 24 1", strategy=None, version="us", origin="authored")
    template = templates.save(kind="star", entity_key="24-1", strat_tag="10 coin",
                              name="gaps", origin="authored",
                              document=text + "0-4 - gap\n5-8 A neutral\n9-11 - gap\n")
    payload = service._template_payload(template, shift=3, limit=28)
    assert payload["frames"] == 12
    assert payload["runs"] == [run(8, 4, 0x8000, 0, 0)]
    assert payload["actions"][0]["start"] == 8
    assert payload["source"]["runs"] == [run(5, 4, 0x8000, 0, 0)]
    assert payload["source"]["actions"][0]["start"] == 5
    assert payload["source"]["frames"] == 12


def test_source_revision_tracks_document_content_not_view_bounds(rig):
    from dataclasses import replace

    service, templates, _attempt = rig
    template = service.mark_template(7)
    source = service._template_payload(template)["source"]
    renamed = replace(template, name="renamed")
    assert service._template_payload(renamed)["source"]["revision"] == source["revision"]
    changed = replace(template, document=encode(frames([(0, 0x8000, 1, 0)]),
                       target="star 24 1", strategy=None, version="us", origin="authored"))
    assert service._template_payload(changed)["source"]["revision"] != source["revision"]


def test_preview_and_import_bind_to_current_local_segment(rig):
    service, _templates, attempt = rig
    attempt.segment_id = 12
    text = encode(frames([(0, 0x8000, 0, 0)]), target="segment 999",
                  strategy="their strategy", version="jp", origin="authored", author="friend")
    preview = service.preview_template(7, text)
    assert preview["document"]["target"] == "segment 999"
    assert preview["destination"] == {"kind": "segment", "entity_key": "12",
                                       "target": "segment 12", "strategy": "10 coin"}
    imported = service.import_template(7, text, "Their example")
    assert (imported.kind, imported.entity_key, imported.strat_tag) == ("segment", "12", "10 coin")
    assert imported.document == text


@pytest.mark.parametrize("source_strategy", ["other strategy", None])
def test_selecting_another_strategy_reuses_a_local_binding(rig, source_strategy):
    service, templates, _attempt = rig
    service.mark_template(7, "current")
    text = encode(frames([(0, 0x8000, 1, 2)]), target="star 24 1",
                  strategy=source_strategy, version="jp", origin="attempt 999", author="friend")
    source = templates.save(kind="star", entity_key="24-1", strat_tag=source_strategy,
                            name="their example", origin="import:friend", document=text)
    selected = service.select_template(7, source.id)
    assert selected.id != source.id
    assert selected.strat_tag == "10 coin"
    assert (selected.document, selected.name, selected.origin) == (text, source.name, source.origin)
    assert service.timeline(7)["template"]["id"] == selected.id
    assert templates.active_for("star", "24-1", source_strategy).id == source.id
    service.mark_template(7, "new current")
    assert service.select_template(7, source.id).id == selected.id
    assert service.select_template(7, selected.id).id == selected.id
    assert len(templates.all()) == 4


@pytest.mark.parametrize("kind,key", [("star", "9-3"), ("segment", "12")])
def test_selecting_another_target_is_refused(rig, kind, key):
    service, templates, _attempt = rig
    current = service.mark_template(7)
    other = templates.save(kind=kind, entity_key=key, strat_tag=None, name="elsewhere",
                           origin=current.origin, document=current.document)
    with pytest.raises(ValueError, match="target"):
        service.select_template(7, other.id)
    assert service.timeline(7)["template"]["id"] == current.id
