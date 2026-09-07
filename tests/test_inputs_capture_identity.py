"""Opposite inputs on repeated counters must retain their capture occurrence."""
import pytest

from sm64_events.inputs.document import decode
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.service import InputsService
from sm64_events.inputs.store import ChunkWriter
from sm64_events.inputs.track import document_for_attempt, track_for_attempt, track_with_lead
from sm64_events.storage.db import Database
from test_inputs_track import AT, LATER, FakeAttempt


A = InputFrame(0x8000, 0, 80, 0)
B = InputFrame(0x4000, 0, -80, 0)
EARLIER = "2026-08-20T20:59:55+00:00"


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "identity.db")
    yield database
    database.close()


@pytest.mark.parametrize("old_start", [99, 900])
def test_attempt_keeps_the_chunk_occurrence_when_an_older_epoch_covers_its_numbers(db, old_start):
    session = db.insert_session(EARLIER)
    db.inputs.append(session, [(n, A) for n in range(old_start, old_start + 6)], EARLIER, EARLIER)
    db.inputs.append(session, [(n, B) for n in range(99, 103)], AT, LATER)
    attempt = FakeAttempt(anchor_frame=100, rta_frames=1, igt_frames=2,
                          course_id=24, star_id=1)
    assert track_for_attempt(db.inputs, attempt) == [(100, B), (101, B)]
    document = decode(document_for_attempt(db.inputs, attempt))
    assert document.frames == [(0, B), (1, B)]


def test_two_unresolved_occurrences_do_not_silently_export_the_first(db):
    session = db.insert_session(AT)
    db.inputs.append(session, [(100, A), (101, A)], AT, LATER)
    db.inputs.append(session, [(100, B), (101, B)], AT, LATER)
    attempt = FakeAttempt(anchor_frame=100, rta_frames=1, igt_frames=2)
    with pytest.raises(ValueError, match="capture.*ambiguous"):
        document_for_attempt(db.inputs, attempt)


def test_partial_capture_before_the_rta_anchor_still_belongs_to_the_igt_span(db):
    session = db.insert_session(AT)
    db.inputs.append(session, [(900, A), (901, A)], AT, LATER)
    db.inputs.append(session, [(98, B), (99, B)], AT, LATER)
    attempt = FakeAttempt(anchor_frame=100, rta_frames=2, igt_frames=5)
    document = decode(document_for_attempt(db.inputs, attempt))
    assert document.frame_count == 5
    assert document.frames == [(0, B), (1, B)]


def test_candidate_selection_keeps_the_input_before_a_delayed_star_dance(db):
    from sm64_events.memory.addresses import ACT_STAR_DANCE_EXIT

    session = db.insert_session(AT)
    dance = InputFrame(0, 0, 0, 0, ACT_STAR_DANCE_EXIT)
    db.inputs.append(session, [(900, A), (901, A)], AT, LATER)
    db.inputs.append(session, [(103, B), (104, B), (105, dance), (106, dance)], AT, LATER)
    attempt = FakeAttempt(anchor_frame=100, rta_frames=2, igt_frames=2)
    document = decode(document_for_attempt(db.inputs, attempt))
    assert document.frame_count == 2
    assert document.frames == [(0, B), (1, B)]


def test_missing_lead_samples_do_not_shift_template_frame_zero(db):
    session = db.insert_session(AT)
    db.inputs.append(session, [(n, A) for n in [90, 92, 100, 101]], AT, LATER)
    attempt = FakeAttempt(anchor_frame=100, rta_frames=1, igt_frames=2,
                          course_id=24, star_id=1)
    _, lead = track_with_lead(db.inputs, attempt, (90, 101))
    assert lead == 10
    service = InputsService(db.inputs, db.input_templates, lambda: [attempt])
    service.mark_template(attempt.id, "Example")
    payload = service.timeline(attempt.id, span=(90, 101))
    assert payload["lead_frames"] == 10
    assert payload["template"]["runs"][0]["start"] == 10


def test_missing_first_and_last_inputs_remain_gaps_in_timeline_and_template(db):
    session = db.insert_session(AT)
    db.inputs.append(session, [(101, B)], AT, LATER)
    attempt = FakeAttempt(anchor_frame=100, rta_frames=2, igt_frames=3,
                          course_id=24, star_id=1)
    service = InputsService(db.inputs, db.input_templates, lambda: [attempt])
    timeline = service.timeline(attempt.id)
    assert timeline["frames"] == 3
    assert timeline["runs"][0]["start"] == 1
    assert timeline["stretches"] == [[0, 100, 3]]
    document = decode(service.document(attempt.id))
    assert document.frame_count == 3
    assert document.frames == [(1, B)]
    service.mark_template(attempt.id, "Missing boundaries")
    assert service.timeline(attempt.id)["template"]["runs"][0]["start"] == 1


def test_equal_raw_counters_from_two_sessions_remain_distinct_without_an_anchor(db):
    first = db.insert_session(AT)
    second = db.insert_session(LATER)
    db.inputs.append(first, [(100, A)], AT, AT)
    db.inputs.append(second, [(100, B)], LATER, LATER)
    attempt = FakeAttempt()
    service = InputsService(db.inputs, db.input_templates, lambda: [attempt])
    document = decode(service.document(attempt.id))
    assert document.frames == [(0, A), (1, B)]
    payload = service.timeline(attempt.id)
    assert payload["frames"] == 2
    assert payload["stretches"] == [[0, 100, 1], [1, 100, 1]]


def test_writer_keeps_the_session_that_captured_the_frame(db):
    first = db.insert_session(AT)
    second = db.insert_session(LATER)
    current = [first]
    writer = ChunkWriter(db.inputs, lambda: current[0], clock=lambda: AT)
    writer.add(100, A)
    current[0] = second
    writer.add(101, B)
    writer.close()
    rows = db._conn.execute("SELECT session_id,start_frame,end_frame FROM input_chunks ORDER BY id")
    assert [tuple(row) for row in rows] == [(first, 100, 100), (second, 101, 101)]


def test_a_frame_captured_before_any_session_cannot_be_assigned_at_flush(db):
    current = [None]
    writer = ChunkWriter(db.inputs, lambda: current[0], clock=lambda: AT)
    writer.add(100, A)
    current[0] = db.insert_session(LATER)
    writer.close()
    assert db.inputs.frames_between(AT, LATER) == []


@pytest.mark.parametrize("initial_owner", [None, "first"])
@pytest.mark.parametrize("next_counter", [100, 101])
def test_pending_sampler_frame_keeps_its_owner_across_session_change(db, initial_owner, next_counter):
    import ast
    from pathlib import Path
    from types import SimpleNamespace
    from sm64_events.inputs.sampler import InputSampler
    from sm64_events.memory.layout import US
    from test_inputs_sampler import ScriptedMemory

    first = db.insert_session(AT)
    second = db.insert_session(LATER)
    service = SimpleNamespace(session_id=first if initial_owner else None)
    writer = ChunkWriter(db.inputs, lambda: service.session_id, clock=lambda: AT)
    memory = ScriptedMemory([(100, A.buttons, 0, 80, 0),
                             (next_counter, B.buttons, 0, -80, 0)])
    # Evaluate the actual composition-root constructor, so omitting ownership
    # wiring in main fails the same pending-frame scenario as a broken sampler.
    source = Path(__file__).resolve().parents[1] / "src/sm64_events/main.py"
    calls = [node for node in ast.walk(ast.parse(source.read_text(encoding="utf-8")))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
             and node.func.id == "InputSampler"]
    [call] = calls
    sampler = eval(compile(ast.Expression(call), str(source), "eval"),
                   {"InputSampler": InputSampler, "memory": memory, "layout": US,
                    "input_writer": writer, "service": service, "replay": None})
    sampler.sample()
    service.session_id = second
    sampler.sample()
    sampler.flush()
    writer.close()
    rows = db._conn.execute("SELECT session_id,start_frame,end_frame FROM input_chunks ORDER BY id")
    assert [tuple(row) for row in rows] == (
        ([(first, 100, 100)] if initial_owner else [])
        + [(second, next_counter, next_counter)])
