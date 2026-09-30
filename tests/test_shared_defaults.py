"""A maintainer correction must survive source regeneration and distribution."""
import copy
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.storage.db import Database
from sm64_events.tracking import shared_defaults
from sm64_events.tracking.defaults import reconcile_defaults
from sm64_events.tracking.shared_defaults import (
    SharedDefaultsStore, assemble_catalogue, base_fingerprint, read_authored,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import build_defaults_seed as generator  # noqa: E402
import build_exe  # noqa: E402


def segment(name="Original", **changes):
    return {"name": name, "start_triggers": [{"type": "spawned", "level": 16}],
            "end_triggers": [{"type": "level_enter", "to": 6}],
            "guards": [], "waypoints": [], **changes}


def base_seed(**changes):
    return {"seed_version": 2, "segments": [segment(seed_key="seg:base")],
            "routes": [{"seed_key": "route:base", "name": "Route", "steps": [
                {"need": 1, "candidates": [{"type": "segment", "seed_key": "seg:base"}]}]}],
            "landmarks": [], **changes}


def store(tmp_path, base=None):
    corpus = base_seed() if base is None else base
    return SharedDefaultsStore(tmp_path, base_builder=lambda: copy.deepcopy(corpus))


def seed_file(shared):
    return json.loads(shared.seed_path.read_text(encoding="utf-8"))


def db_segment(db, key="seg:base"):
    return next((row for row in db.segment_defs() if row["seed_key"] == key), None)


def test_shared_fix_survives_regeneration_and_refreshes_only_clean_users(tmp_path):
    original = base_seed()
    shared = store(tmp_path / "source", original)
    clean = Database(tmp_path / "clean.db")
    personal = Database(tmp_path / "personal.db")
    deleted = Database(tmp_path / "deleted.db")
    for db in (clean, personal, deleted):
        assert reconcile_defaults(db, original) == []
    original_id = db_segment(clean)["id"]
    personal_row = db_segment(personal)
    personal.update_segment_def(personal_row["id"], name="My customization")
    personal.set_seed_dirty("segment_defs", personal_row["id"], True)
    deleted_row = db_segment(deleted)
    from sm64_events.tracking.defaults import remember_deletion
    remember_deletion(deleted, "segments", deleted_row["seed_key"])
    deleted.delete_segment_def(deleted_row["id"])
    correction = segment("Fixed for everyone", enabled=False, category="Tricks",
                         default_strat="Standard", match_mode="loose",
                         parents=["star:2:1", "area:6:1"], clock_start="move")
    with shared.publish_segment(correction, "seg:base") as key:
        assert key == "seg:base"
        assert seed_file(shared)["segments"][0]["name"] == "Fixed for everyone"
    reconstructed = assemble_catalogue(original, read_authored(shared.authored_path))
    assert reconstructed == seed_file(shared)
    fresh = Database(tmp_path / "fresh.db")
    for db in (clean, personal, deleted, fresh):
        assert reconcile_defaults(db, reconstructed) == []
    for db in (clean, fresh):
        actual = db_segment(db)
        for field, expected in correction.items():
            assert actual[field] == expected, field
    assert db_segment(clean)["id"] == original_id
    assert db_segment(personal)["name"] == "My customization"
    assert db_segment(deleted) is None
    assert b"\r\n" not in shared.authored_path.read_bytes()
    assert b"\r\n" not in shared.seed_path.read_bytes()


def test_new_default_keeps_its_identity_and_drops_local_database_fields(tmp_path):
    shared = store(tmp_path)
    with shared.publish_segment(segment("New", id=52, seed_dirty=1,
                                        created_utc="local")) as key:
        assert key.startswith("seg:authored:")
    with shared.publish_segment(segment("Renamed"), key) as same_key:
        assert same_key == key
    rows = seed_file(shared)["segments"]
    assert len(rows) == 2 and rows[-1]["seed_key"] == key
    assert rows[-1]["name"] == "Renamed"
    assert not {"id", "seed_dirty", "created_utc"} & rows[-1].keys()
    assert rows[-1]["parents"] == []
    entry = read_authored(shared.authored_path)["segments"][0]
    assert entry["base_fingerprint"] is None


def test_replacing_complete_definition_clears_old_fields_but_keeps_seed_bindings(tmp_path):
    original = base_seed(segments=[segment(
        seed_key="seg:base", default_strat="Old", parents=["star:2:1"],
        clock_start="move", match_mode="loose",
        sheet_target={"section": "Castle Movement", "label": "Toad"})])
    shared = store(tmp_path, original)
    with shared.publish_segment(segment("Replacement"), "seg:base"):
        pass
    row = seed_file(shared)["segments"][0]
    assert row["default_strat"] is None and row["parents"] == []
    assert row["clock_start"] == "trigger" and row["match_mode"] == "strict"
    assert row["sheet_target"] == original["segments"][0]["sheet_target"]


@pytest.mark.parametrize("existing", [False, True])
def test_failed_database_callback_restores_both_source_files_byte_for_byte(tmp_path, existing):
    shared = store(tmp_path)
    if existing:
        with shared.publish_segment(segment("First"), "seg:base"):
            pass
        # Restoration must preserve original bytes, including formatting.
        shared.authored_path.write_bytes(shared.authored_path.read_bytes().replace(b"\n", b"\r\n"))
    before = {path: path.read_bytes() if path.exists() else None
              for path in (shared.authored_path, shared.seed_path)}
    with pytest.raises(RuntimeError, match="database failure"):
        with shared.publish_segment(segment("Failed"), "seg:base"):
            assert seed_file(shared)["segments"][0]["name"] == "Failed"
            raise RuntimeError("database failure")
    after = {path: path.read_bytes() if path.exists() else None for path in before}
    assert after == before


def test_partial_source_write_failure_restores_both_files(tmp_path, monkeypatch):
    shared = store(tmp_path)
    with shared.publish_segment(segment("First"), "seg:base"):
        pass
    before = (shared.authored_path.read_bytes(), shared.seed_path.read_bytes())
    actual_write = shared_defaults._atomic_write
    calls = 0

    def fail_second_write(path, data):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk failure")
        actual_write(path, data)

    monkeypatch.setattr(shared_defaults, "_atomic_write", fail_second_write)
    with pytest.raises(OSError, match="disk failure"):
        with shared.publish_segment(segment("Failed"), "seg:base"):
            pytest.fail("yielded after a failed write")
    assert (shared.authored_path.read_bytes(), shared.seed_path.read_bytes()) == before


@pytest.mark.parametrize("invalid, message", [
    (segment(parents=["segment:7"]), "Save only for me"),
    (segment(end_triggers=[{"type": "level_enter", "to": 24}]), "entrance touch"),
    (segment(match_mode="broken"), "match_mode"),
    ({"name": "Partial"}, "full definition"),
])
def test_unshareable_or_invalid_definitions_never_write(tmp_path, invalid, message):
    shared = store(tmp_path)
    with pytest.raises(ValueError, match=message):
        with shared.publish_segment(invalid, "seg:base"):
            pytest.fail("invalid definition was written")
    assert not shared.authored_path.exists() and not shared.seed_path.exists()


def test_base_fingerprint_cannot_silently_hide_a_later_corpus_change(tmp_path):
    original = base_seed()
    shared = store(tmp_path, original)
    with shared.publish_segment(segment("Maintainer correction"), "seg:base"):
        pass
    before = (shared.authored_path.read_bytes(), shared.seed_path.read_bytes())
    original["segments"][0]["name"] = "Later corpus change"
    with pytest.raises(ValueError, match="conflicts with changed base corpus"):
        assemble_catalogue(original, read_authored(shared.authored_path))
    with pytest.raises(ValueError, match="conflicts with changed base corpus"):
        with shared.publish_segment(segment("Cannot erase conflict"), "seg:base"):
            pass
    assert (shared.authored_path.read_bytes(), shared.seed_path.read_bytes()) == before


def test_final_catalogue_checks_untouched_rows_routes_and_key_collisions(tmp_path):
    for invalid in (
        base_seed(segments=[segment(seed_key="seg:base", match_mode="broken")]),
        base_seed(routes=[{"seed_key": "route:bad", "name": "Broken", "steps": [
            {"need": 1, "candidates": [{"type": "segment", "seed_key": "seg:missing"}]}]}]),
        base_seed(segments=[segment(seed_key="seg:base"), segment(seed_key="seg:base")]),
    ):
        shared = store(tmp_path, invalid)
        with pytest.raises(ValueError):
            with shared.publish_segment(segment("New")):
                pass
        assert not shared.authored_path.exists() and not shared.seed_path.exists()


def test_new_identity_retries_a_collision(tmp_path, monkeypatch):
    original = base_seed(segments=[segment(seed_key="seg:authored:existing")])
    original["routes"] = []
    identities = iter([SimpleNamespace(hex="existing"), SimpleNamespace(hex="new")])
    monkeypatch.setattr(shared_defaults.uuid, "uuid4", lambda: next(identities))
    with store(tmp_path, original).publish_segment(segment("New")) as key:
        assert key == "seg:authored:new"


def test_concurrent_save_waits_through_callback_and_keeps_both_corrections(tmp_path):
    first = store(tmp_path)
    second = store(tmp_path)
    started = threading.Event()
    yielded = threading.Event()

    def save_second():
        started.set()
        with second.publish_segment(segment("Second")):
            yielded.set()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with first.publish_segment(segment("First")):
            future = executor.submit(save_second)
            assert started.wait(2)
            assert not yielded.wait(0.05), "writer entered during the first database callback"
        future.result(timeout=5)
    assert yielded.is_set()
    assert [row["name"] for row in seed_file(first)["segments"]] == ["Original", "First", "Second"]


def test_generator_check_includes_the_authored_layer_and_refuses_stale_output(tmp_path, monkeypatch):
    shared = store(tmp_path)
    with shared.publish_segment(segment("Durable correction"), "seg:base"):
        pass
    monkeypatch.setattr(generator, "base", base_seed)
    monkeypatch.setattr(generator, "AUTHORED", shared.authored_path)
    monkeypatch.setattr(generator, "OUT", shared.seed_path)
    assert generator.main(["--check"]) == 0
    shared.seed_path.write_text("{}\n", encoding="utf-8")
    assert generator.main(["--check"]) == 1
    assert generator.main([]) == 0
    assert seed_file(shared)["segments"][0]["name"] == "Durable correction"


def test_direct_app_build_refuses_stale_defaults_before_pyinstaller(tmp_path, monkeypatch):
    path = tmp_path / "defaults.seed.json"
    path.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(generator, "OUT", path)
    monkeypatch.setattr(sys, "argv", ["build_exe.py", "--mode", "app"])
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    called = []
    monkeypatch.setitem(sys.modules, "PyInstaller.__main__",
                        SimpleNamespace(run=lambda args: called.append(args)))
    assert build_exe.main() == 1
    assert called == []


def test_authored_fingerprint_is_independent_of_key_order():
    row = segment(seed_key="seg:base")
    assert base_fingerprint(row) == base_fingerprint(dict(reversed(list(row.items()))))


def test_default_builder_reads_the_actual_source_corpus_even_with_legacy_console_encoding(monkeypatch):
    monkeypatch.setenv("PYTHONUTF8", "0")
    root = Path(__file__).resolve().parents[1]
    shared = SharedDefaultsStore(root)
    assert shared._build_base() == generator.base()


@pytest.mark.parametrize("error", [
    subprocess.CalledProcessError(1, "build_defaults_seed.py"),
    subprocess.TimeoutExpired("build_defaults_seed.py", 30),
])
def test_source_build_failure_is_actionable_without_source_writes(tmp_path, monkeypatch, error):
    shared = SharedDefaultsStore(tmp_path)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(shared_defaults.subprocess, "run", fail)
    with pytest.raises(RuntimeError, match="fix the reported problem before saving"):
        with shared.publish_segment(segment("Failed")):
            pytest.fail("yielded after the source builder failed")
    assert not shared.authored_path.exists() and not shared.seed_path.exists()
