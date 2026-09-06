"""Preserve databases recorded before input-timeline merged main's v27-v30."""
import sqlite3

import pytest

from sm64_events.storage.db import Database, MIGRATIONS

# Historical branch schema, independent of the newly numbered migrations.
INPUT_SCHEMA = [
    "CREATE TABLE input_chunks (id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, "
    "start_frame INTEGER NOT NULL, end_frame INTEGER NOT NULL, started_utc TEXT NOT NULL, "
    "ended_utc TEXT NOT NULL, runs BLOB NOT NULL); "
    "CREATE INDEX idx_input_chunks_utc ON input_chunks(started_utc, ended_utc);",
    "CREATE TABLE input_templates (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
    "entity_key TEXT NOT NULL, strat_tag TEXT, name TEXT NOT NULL, origin TEXT NOT NULL, "
    "document TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, created_utc TEXT NOT NULL); "
    "CREATE UNIQUE INDEX idx_input_templates_active ON input_templates "
    "(kind, entity_key, IFNULL(strat_tag, '')) WHERE active = 1;",
    "ALTER TABLE input_chunks ADD COLUMN format INTEGER NOT NULL DEFAULT 1;",
    "CREATE INDEX idx_events_wall ON events(wall_time_utc);",
]


def legacy_database(path, version):
    with sqlite3.connect(path) as conn:
        for sql in [*MIGRATIONS[:26], *INPUT_SCHEMA[:version - 26]]:
            conn.executescript(sql)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("INSERT INTO input_chunks "
                     "(id,session_id,start_frame,end_frame,started_utc,ended_utc,runs) "
                     "VALUES (1,1,100,101,'start','end',x'010203')")
        if version >= 28:
            conn.execute("INSERT INTO input_templates VALUES "
                         "(7,'star','8-2','Pillarless','My run','attempt','original text',1,'today')")


@pytest.mark.parametrize("version", [27, 28, 29, 30])
def test_input_branch_upgrade_preserves_captures_and_templates(tmp_path, version):
    path = tmp_path / "old.db"
    legacy_database(path, version)
    for _ in range(2):
        db = Database(path)
        conn = db._conn
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
        row = conn.execute("SELECT runs, format FROM input_chunks WHERE id=1").fetchone()
        assert tuple(row) == (bytes([1, 2, 3]), 1)
        if version >= 28:
            row = conn.execute("SELECT name, document, active FROM input_templates WHERE id=7").fetchone()
            assert tuple(row) == ("My run", "original text", 1)
        assert "platform" in {r[1] for r in conn.execute("PRAGMA table_info(attempts)")}
        assert {"imported_from", "game_version"} <= {r[1] for r in conn.execute("PRAGMA table_info(pbs)")}
        assert conn.execute("SELECT count(*) FROM held_times").fetchone()[0] == 0
        db.close()


def test_main_v30_upgrade_preserves_held_imports(tmp_path):
    path = tmp_path / "main.db"
    with sqlite3.connect(path) as conn:
        for sql in MIGRATIONS[:30]:
            conn.executescript(sql)
        conn.execute("PRAGMA user_version=30")
        conn.execute("INSERT INTO held_times (source,row_key,time_cs,reason,saved_utc) "
                     "VALUES ('sheet:friend','castle',1234,'unmapped','today')")
    db = Database(path)
    assert db._conn.execute("SELECT time_cs FROM held_times").fetchone()[0] == 1234
    assert db._conn.execute("SELECT count(*) FROM input_chunks").fetchone()[0] == 0
    db.close()


def test_input_upgrade_failure_rolls_back_main_block_and_can_retry(tmp_path, monkeypatch):
    import sm64_events.storage.db as storage

    path = tmp_path / "old.db"
    legacy_database(path, 30)
    broken = [*MIGRATIONS]
    broken[29] += "CREATE TABLE invalid ("
    with monkeypatch.context() as patch:
        patch.setattr(storage, "MIGRATIONS", broken)
        with pytest.raises(sqlite3.OperationalError):
            Database(path)
    with sqlite3.connect(path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 30
        assert "platform" not in {r[1] for r in conn.execute("PRAGMA table_info(attempts)")}
        assert conn.execute("SELECT document FROM input_templates").fetchone()[0] == "original text"
    db = Database(path)
    assert db._conn.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    db.close()
