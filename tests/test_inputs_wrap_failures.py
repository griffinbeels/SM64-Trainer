"""Storage failures do not stop capture or partially replace a template."""
from contextlib import closing
import sqlite3

import pytest

from sm64_events.inputs.readtimes import ReadTimes
from sm64_events.inputs.sampler import InputSampler
from sm64_events.inputs.store import ChunkWriter
from sm64_events.inputs.track import track_for_attempt
from sm64_events.memory.base import MemoryReadError
from sm64_events.memory.layout import US
from sm64_events.storage.db import Database
from test_inputs_sampler import ScriptedMemory
from test_inputs_templates import save
from test_inputs_track import AT, LATER, FakeAttempt


@pytest.mark.parametrize("operation", ["create", "add", "finish"])
def test_spool_failure_drops_only_unproven_state_and_later_capture_continues(
        tmp_path, monkeypatch, operation):
    made = []

    def factory():
        if not made:
            made.append(None)
            if operation == "create":
                raise OSError("temporary storage unavailable")
            history = ReadTimes()
            made[0] = history

            def fail(*args):
                raise OSError("temporary storage unavailable")

            monkeypatch.setattr(history, operation, fail)
            return history
        return ReadTimes()

    monkeypatch.setattr("sm64_events.inputs.sampler.ReadTimes", factory)
    with closing(Database(tmp_path / "capture.db")) as db:
        writer = ChunkWriter(db.inputs, db.insert_session(AT))
        script = [(100, 0x8000, 0, 80, 0), (100, 0x8000, 0, 80, 0),
                  (101, 0x8000, 0, 80, 0), (101, 0x4000, 0, -80, 0),
                  (102, 0, 0, 0, 0)]
        sampler = InputSampler(ScriptedMemory(script), US, writer.add, clock=lambda: AT)
        assert [sampler.sample() for _ in script] == [100, 100, 101, 101, 102]
        sampler.flush()
        writer.close()
        chunks = db.inputs.chunks_between(AT, AT)
        assert [(n, f.buttons) for c in chunks for n, f in c.frames] == [(101, 0x4000), (102, 0)]
        assert all(c.observations is not None for c in chunks)
        assert sampler.health()["history_failures"] == 1
        if made[0] is not None:
            assert made[0]._file.closed


def test_spool_cleanup_failure_does_not_emit_the_pre_rewrite_state(monkeypatch):
    got = []
    sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                           (100, 0x4000, 0, -80, 0)]), US,
        lambda n, f, **meta: got.append((n, f, meta)), clock=lambda: AT)
    sampler.sample()
    history = sampler._read_times
    close = history.close

    def fail_close():
        close()
        raise OSError("close failed")

    monkeypatch.setattr(history, "close", fail_close)
    sampler.sample()
    sampler.flush()
    assert [(n, f.buttons) for n, f, _ in got] == [(100, 0x4000)]
    assert got[0][2]["observation"].within(AT, AT)
    assert history._file.closed


@pytest.mark.parametrize("operation", ["save", "activate"])
def test_failed_replacement_rolls_back_before_the_next_journal_commit(tmp_path, operation):
    from sm64_events.inputs.templates import TemplateStore
    with closing(Database(tmp_path / "templates.db")) as db:
        templates = TemplateStore(db._conn, db._lock)
        first = save(templates)
        other = save(templates, active=False, name="other")
        clause = "INSERT" if operation == "save" else "UPDATE OF active"
        condition = "" if operation == "save" else f"WHEN NEW.id={other.id} AND NEW.active=1"
        db._conn.execute(f"CREATE TEMP TRIGGER fail_replace BEFORE {clause} ON input_templates "
                         f"{condition} BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="test failure"):
            if operation == "save":
                save(templates, name="replacement")
            else:
                templates.activate(other.id)
        db.insert_session(AT)  # an unrelated shared-connection commit
        assert templates.active_for("star", "24-1", "10 coin").id == first.id
        assert len(templates.all()) == 2


def test_monotonic_counters_do_not_prove_continuity_across_a_read_outage(tmp_path):
    # A reset and return to a higher counter can occur while reads fail.
    # Preserve both stretches, but refuse to join them as one occurrence.
    with closing(Database(tmp_path / "boundary.db")) as db:
        writer = ChunkWriter(db.inputs, db.insert_session(AT))
        memory = ScriptedMemory([(100, 0x8000, 0, 80, 0), (110, 0x8000, 0, 80, 0),
                                 (112, 0x4000, 0, -80, 0), (120, 0x4000, 0, -80, 0)])
        sampler = InputSampler(memory, US, writer.add, clock=lambda: AT)
        sampler.sample()
        sampler.sample()

        class Unreadable:
            def read_u32(self, address):
                raise MemoryReadError("temporary outage")

        sampler._memory = Unreadable()
        assert sampler.sample() is None
        sampler._memory = memory
        sampler.sample()
        sampler.sample()
        sampler.flush()
        writer.close()
        assert [n for c in db.inputs.chunks_between(AT, LATER) for n, _ in c.frames] == [100, 110, 112, 120]
        with pytest.raises(ValueError, match="ambiguous"):
            track_for_attempt(db.inputs, FakeAttempt(anchor_frame=100, rta_frames=20))
