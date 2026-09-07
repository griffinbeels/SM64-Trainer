"""The read-only diagnostic must resolve the same stored occurrence as the app."""
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.observation import InputObservation
from sm64_events.storage.db import Database

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from dump_inputs import attempts, frames_for, main, open_readonly


@pytest.mark.parametrize("observed", [False, True])
def test_dump_uses_owner_time_and_attempt_end_without_writing_the_journal(tmp_path, monkeypatch, capsys, observed):
    path = tmp_path / "capture.db"
    at = "2026-08-20T21:00:00.500000+00:00"
    with closing(Database(path)) as db:
        session = db.insert_session(at)
        foreign = db.insert_session(at)
        for owner, buttons in ((foreign, 0x4000), (session, 0x8000)):
            frames = [(raw, InputFrame(buttons, 0, 80, 0)) for raw in range(100, 106)]
            metadata = [InputObservation(f"poll:{owner}", n, at) for n in range(6)] if observed else None
            db.inputs.append(owner, frames, at, at, observations=metadata)
        db._conn.execute(
            "INSERT INTO attempts(id,session_id,started_utc,ended_utc,outcome,"
            " anchor_frame,rta_frames,igt_frames,course_id,star_id,anchor_type)"
            " VALUES(7,?,?,?,?,?,?,?,?,?,'savestate')",
            (session, "2026-08-20T21:00:00Z", "2026-08-20T21:00:01Z", "success",
             100, 2, 3, 24, 1))
        db._conn.commit()
    original = path.read_bytes()
    with closing(open_readonly(path)) as conn:
        conn.row_factory = sqlite3.Row
        row, = attempts(conn, 1)
        assert [(raw, pad.buttons) for raw, pad in frames_for(conn, row)] == [
            (100, 0x8000), (101, 0x8000), (102, 0x8000)]
    monkeypatch.setattr("sys.argv", ["dump_inputs", "--journal", str(path), "--attempt", "7"])
    assert main() == 0
    assert "0-2 A +80,+0" in capsys.readouterr().out
    assert path.read_bytes() == original
