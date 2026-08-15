"""Pointer keys -> symbol keys, once, harmlessly.

The proof that matters is the last test: a db written by the OLD key shape
projects to the SAME attempts after the rekey as before it, and a second
rekey touches nothing.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sm64_events.core.events import Event
from sm64_events.storage.db import Database
from sm64_events.storage.rekey import rekey_text, us_symbol_of_pointer
from sm64_events.tracking.projection import replay

DOOR = "6:1:800ebc8c:-1775,0,-824"
REPO = Path(__file__).resolve().parents[1]


def test_instance_and_kind_keys_rewrite_to_symbols():
    text = ('{"key": "%s", "kind_key": "kind:800ebc8c", "behaviour": 2148449420}'
            % DOOR)
    out = rekey_text(text, us_symbol_of_pointer)
    assert '"key": "6:1:bhvDoor:-1775,0,-824"' in out
    assert '"kind_key": "kind:bhvDoor"' in out
    assert '"behaviour": 2148449420' in out          # the pointer stays as evidence


def test_rekey_is_idempotent_and_leaves_symbol_and_ptr_keys_alone():
    once = rekey_text(DOOR, us_symbol_of_pointer)
    assert once == "6:1:bhvDoor:-1775,0,-824"
    assert rekey_text(once, us_symbol_of_pointer) == once
    assert rekey_text("6:1:ptr_12345678:0,0,0", us_symbol_of_pointer) \
        == "6:1:ptr_12345678:0,0,0"
    assert rekey_text("kind:bhvDoor", us_symbol_of_pointer) == "kind:bhvDoor"


def test_unknown_pointer_becomes_ptr_key():
    assert rekey_text("6:1:12345678:0,0,0", us_symbol_of_pointer) == "6:1:ptr_12345678:0,0,0"
    assert rekey_text("kind:12345678", us_symbol_of_pointer) == "kind:ptr_12345678"


def _old_shape_db(path: Path) -> None:
    """A db as a pre-2026-08-15 build would have left it: pointer keys in the
    journal, the catalogue and one segment definition. Written with raw SQL so
    Database.__init__'s own repair cannot pre-empt what the test measures."""
    conn = sqlite3.connect(path)
    Database(path).close()          # schema, migrations, empty repair
    conn.execute("INSERT INTO sessions (started_utc) VALUES ('2026-08-01T00:00:00Z')")
    payload = json.dumps({
        "kind": "door_open", "ordinal": 1, "level": 6, "area": 1,
        "action": 0x1320, "igt_frames": 64, "igt_source": "counter",
        "igt": "0'02\"13", "action_timer": 0, "counter": 62,
        "landmark": {"key": DOOR, "kind_key": "kind:800ebc8c",
                     "behaviour": 0x800EBC8C, "home": [-1775, 0, -824],
                     "pos": [-1775, 0, -824], "placed": True, "nameable": True}})
    conn.execute("INSERT INTO events (session_id, seq, type, frame, wall_time_utc, payload)"
                 " VALUES (1, 1, 'moment_reached', 100, '2026-08-01T00:00:01Z', ?)",
                 (payload,))
    conn.execute("INSERT INTO landmark_names (key, name, seed_key, seed_dirty, updated_utc)"
                 " VALUES (?, 'Left Basement Door', 'landmark:' || ?, 1, '2026-08-01T00:00:00Z')",
                 (DOOR, DOOR))
    conn.execute("INSERT INTO landmark_names (key, name, seed_key, seed_dirty, updated_utc)"
                 " VALUES ('kind:800ebc8c', 'door', 'landmark:kind:800ebc8c', 0, '2026-08-01T00:00:00Z')")
    conn.execute("INSERT INTO segment_defs (name, enabled, start_triggers, end_triggers,"
                 " guards, created_utc, waypoints, category, seed_key, seed_dirty)"
                 " VALUES ('LBLJ', 1, ?, ?, '[]', '2026-06-11T00:00:00Z', '[]', 'Tricks',"
                 " 'seg:test-rekey', 1)",
                 (json.dumps([{"type": "moment_reached", "kind": "door_open",
                               "level": 6, "landmark": DOOR}]),
                  json.dumps([{"type": "warp_entered", "level": 6}])))
    conn.commit()
    conn.close()


def _project(db: Database):
    """The real projection: events + segment definitions + his names, the way
    tracking/service.py::start feeds replay()."""
    import dataclasses
    from sm64_events.tracking.segments import SegmentDef
    keys = [f.name for f in dataclasses.fields(SegmentDef)]
    segments = [SegmentDef(**{k: row[k] for k in keys}) for row in db.segment_defs()]
    names = db.landmark_names()
    attempts, _ = replay(db.events(), segments=segments,
                         landmark_names=lambda: names)
    return [a.__dict__ for a in attempts]


def test_the_db_rekey_touches_all_three_tables_and_reprojects_identically(
        tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    _old_shape_db(path)
    # Open WITHOUT the boot repair so the OLD shape can be projected first.
    monkeypatch.setattr(Database, "_repair_landmark_keys", lambda self: None)
    db = Database(path)
    assert DOOR in db.landmark_names()
    before = _project(db)

    counts = db.rekey_landmark_keys(us_symbol_of_pointer)
    assert counts == {"events": 1, "landmark_names": 2, "segment_defs": 1}
    names = db.landmark_names()
    assert "6:1:bhvDoor:-1775,0,-824" in names and DOOR not in names
    assert names["kind:bhvDoor"] == "door"
    payload = db.events()[0].payload
    payload = json.loads(payload) if isinstance(payload, str) else payload
    assert payload["landmark"]["key"] == "6:1:bhvDoor:-1775,0,-824"
    assert payload["landmark"]["behaviour"] == 0x800EBC8C
    [lblj] = [d for d in db.segment_defs() if d["seed_key"] == "seg:test-rekey"]
    trigger = lblj["start_triggers"][0]
    assert trigger["landmark"] == "6:1:bhvDoor:-1775,0,-824"
    assert _project(db) == before                       # same attempts
    assert db.rekey_landmark_keys(us_symbol_of_pointer) == {
        "events": 0, "landmark_names": 0, "segment_defs": 0}   # idempotent
    db.close()

    monkeypatch.undo()
    repaired = Database(path)                            # boot repair: no-op now
    assert repaired.landmark_names() == names
    repaired.close()


def test_the_boot_repair_runs_at_open(tmp_path):
    path = tmp_path / "old.db"
    _old_shape_db(path)
    db = Database(path)
    assert DOOR not in db.landmark_names()
    assert "6:1:bhvDoor:-1775,0,-824" in db.landmark_names()
    db.close()


def _live_journal() -> Path | None:
    """The dev journal: this checkout's, else the primary checkout's when this
    is a worktree under .claude/worktrees (worktree data is disposable; the
    primary's is the one that matters)."""
    for root in (REPO, REPO.parents[2] if REPO.parent.name == "worktrees" else None):
        if root is not None and (root / "data" / "tracker.db").exists():
            return root / "data" / "tracker.db"
    return None


@pytest.mark.skipif(_live_journal() is None, reason="no live journal reachable")
def test_the_live_journal_projects_identically_after_the_rekey(tmp_path, monkeypatch):
    """The real proof, on a `Connection.backup` copy of the dev journal.
    Read-only on the source; the copy is projected, repaired, projected."""
    src = sqlite3.connect(f"file:{_live_journal()}?mode=ro", uri=True)
    copy_path = tmp_path / "live.db"
    dst = sqlite3.connect(copy_path)
    src.backup(dst)
    src.close()
    dst.close()
    monkeypatch.setattr(Database, "_repair_landmark_keys", lambda self: None)
    db = Database(copy_path)
    before = _project(db)
    counts = db.rekey_landmark_keys(us_symbol_of_pointer)
    still = db._conn.execute(
        "SELECT count(*) FROM events WHERE payload LIKE '%:800e%:%'").fetchone()[0]
    assert still == 0, f"{still} journal payloads still carry a pointer key"
    assert _project(db) == before
    assert db.rekey_landmark_keys(us_symbol_of_pointer) == {
        "events": 0, "landmark_names": 0, "segment_defs": 0}
    db.close()
    print(f"live journal: rekeyed {counts}; {len(before)} attempts identical")
