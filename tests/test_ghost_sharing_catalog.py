"""Opening a bulk collection binds one definition snapshot, never one per row."""
from sm64_events.ghost.portable import make_entry
from sm64_events.ghost.sharing_targets import describe
from tests.test_ghost_sharing import Store, package, provider, recording, segment
from types import SimpleNamespace

import pytest


def test_star_collection_never_reads_unrelated_segment_definitions(tmp_path):
    path, entry = recording(tmp_path)
    rows = [(make_entry(path, author=f"Player {number}", target=entry["target"], frames=number,
                        clock="igt", recorded_utc=entry["recorded_utc"], practice=False,
                        outcome="success"), path) for number in range(32)]
    source, _, _ = package(tmp_path, rows=rows)
    store = Store()
    sharing = provider(tmp_path, store)
    store.segment_defs = lambda: (_ for _ in ()).throw(AssertionError("Unrelated definition read"))
    assert sharing.import_file(source)["imported"] == 32
    assert len(sharing.imports_for("star", "2-1")) == 32
    assert sharing.imports_for("star", "2-2") == []
    assert len(sharing.imported_rows()) == 32


def test_segment_collection_binds_a_single_current_snapshot_per_operation(tmp_path):
    path, entry = recording(tmp_path, kind="segment")
    target = describe("segment", "3", "Standard", [segment(3)])
    rows = [(make_entry(path, author=f"Player {number}", target=target, frames=number,
                        clock="rta", recorded_utc=entry["recorded_utc"], practice=False,
                        outcome="success"), path) for number in range(32)]
    source, _, _ = package(tmp_path, rows=rows)
    store = Store(definitions=[segment(97)])
    sharing = provider(tmp_path, store)
    calls = []

    def definitions():
        calls.append(True)
        return store.definitions

    store.segment_defs = definitions
    for operation in (lambda: sharing.import_file(source),
                      lambda: sharing.imports_for("segment", "97"), sharing.imported_rows):
        calls.clear()
        operation()
        assert len(calls) == 1
    # A definition edit must invalidate matching immediately, without reload.
    store.definitions = [segment(97, end_level=9)]
    assert sharing.imports_for("segment", "97") == []
    assert all(not row["available"] for row in sharing.imported_rows())


def test_recent_local_race_becomes_selectable_without_creating_a_pb(tmp_path):
    from tests.test_ghost_session import a_session
    from sm64_events.ghost.saved import SAVED_KEY
    path, _ = recording(tmp_path)
    run = SimpleNamespace(id=77, segment_id=None, course_id=2, star_id=1, strat_tag="Standard", igt_frames=1)
    sharing = provider(tmp_path)
    sharing.replay.attempt = lambda _number: run
    sharing.replay.preserve_native = lambda _number: {"path": str(path)}
    sharing.replay.find_native = lambda _number: (path, {})
    session, *_ = a_session(tmp_path, target=("star", 2, 1), db=sharing.database, replay=sharing.replay)
    choice = {"pb": False, "attempts": [77]}
    target = session._read_target()
    with pytest.raises(ValueError, match="not saved"):
        session._queue_update({"ghosts": choice, "target": target}, target)
    assert sharing.prepare_race(choice, target) == choice
    session._queue_update({"ghosts": choice, "target": target}, target)
    assert session._pending["ghosts"] == choice
    assert sharing.database.writes == [SAVED_KEY]


def test_failed_or_foreign_recent_race_does_not_change_the_saved_list(tmp_path):
    sharing = provider(tmp_path)
    run = SimpleNamespace(id=77, segment_id=None, course_id=2, star_id=1)
    sharing.replay.attempt = lambda _number: run
    target = {"kind": "star", "entity_key": "2-1", "strat": "Standard"}
    sharing.replay.preserve_native = lambda _number: {"native_ghost_reason": "No exact endpoint"}
    with pytest.raises(ValueError, match="endpoint"):
        sharing.prepare_race({"pb": False, "attempts": [77]}, target)
    with pytest.raises(ValueError, match="current practice"):
        sharing.prepare_race({"pb": False, "attempts": [77]}, {**target, "entity_key": "2-2"})
    assert sharing.database.writes == []
