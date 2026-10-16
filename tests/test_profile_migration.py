"""Migration preserves PBs/WAL/recordings and never overwrites normal data."""
from contextlib import closing
import os
import shutil
import sqlite3

import pytest

from sm64_events.core import profile_migration as migration
from sm64_events.storage.instance_lock import acquire_instance_lock, release_instance_lock


def profile(tmp_path):
    old = tmp_path / "SM64TrainerBeta"
    (old / "data").mkdir(parents=True)
    (old / "replays").mkdir()
    (old / "replays" / "saved.ghost").write_bytes(b"saved-recording")
    (old / "data" / "settings.json").write_text('{"setting":true}')
    return old, tmp_path / "SM64Trainer"


def test_opt_in_copies_committed_wal_rows_and_keeps_original_and_recordings(tmp_path):
    old, new = profile(tmp_path)
    original = sqlite3.connect(old / "data" / "tracker.db")
    try:
        original.execute("PRAGMA journal_mode=WAL")
        original.execute("CREATE TABLE best(time INTEGER)")
        original.execute("INSERT INTO best VALUES(321)")
        original.commit()
        (old / "server.pid").write_text("fictional-pid")
        (old / "data" / "replay_buffer").mkdir()
        (old / "data" / "replay_buffer" / "scratch.ts").write_bytes(b"transient")
        assert migration.beta_profile_migration_available(tmp_path)
        result = migration.migrate_private_beta_profile(tmp_path, confirmed=True)
        assert result["state"] == "copied" and result["source"] == str(old)
        assert not list(new.rglob("*-wal"))
        with closing(sqlite3.connect(new / "data" / "tracker.db")) as copied:
            assert copied.execute("SELECT time FROM best").fetchone() == (321,)
        assert original.execute("SELECT time FROM best").fetchone() == (321,)
        for root in (old, new):
            assert (root / "replays" / "saved.ghost").read_bytes() == b"saved-recording"
            assert (root / "data" / "settings.json").read_text() == '{"setting":true}'
        assert not (new / "server.pid").exists()
        assert not (new / "data" / "tracker.lock").exists()
        assert not (new / "data" / "replay_buffer").exists()
        assert not migration.beta_profile_migration_available(tmp_path)
    finally:
        original.close()


def test_unconfirmed_copy_does_no_io_and_existing_normal_profile_is_preserved(tmp_path):
    old, new = profile(tmp_path)
    assert migration.migrate_private_beta_profile(tmp_path)["state"] == "not_requested"
    assert not new.exists()
    new.mkdir()
    (new / "my-pb.dat").write_bytes(b"keep-existing-pb")
    assert not migration.beta_profile_migration_available(tmp_path)
    with pytest.raises(ValueError, match="empty normal profile"):
        migration.migrate_private_beta_profile(tmp_path, confirmed=True)
    assert (new / "my-pb.dat").read_bytes() == b"keep-existing-pb"
    assert (old / "replays" / "saved.ghost").exists()


def test_active_legacy_tracker_prevents_copy(tmp_path):
    old, new = profile(tmp_path)
    handle = acquire_instance_lock(old / "data" / "tracker.lock")
    try:
        with pytest.raises(ValueError, match="Close SM64 Trainer Beta"):
            migration.migrate_private_beta_profile(tmp_path, confirmed=True)
        assert not new.exists()
    finally:
        release_instance_lock(handle)


@pytest.mark.parametrize("empty_target", [False, True])
def test_copy_or_publish_failure_restores_empty_destination_and_reaps_staging(tmp_path, monkeypatch, empty_target):
    old, new = profile(tmp_path)
    if empty_target:
        new.mkdir()
    def failed(*_args, **_kwargs):
        raise OSError("injected publication failure")
    monkeypatch.setattr(migration.os, "rename", failed)
    with pytest.raises(OSError, match="injected"):
        migration.migrate_private_beta_profile(tmp_path, confirmed=True)
    assert (old / "replays" / "saved.ghost").read_bytes() == b"saved-recording"
    assert new.exists() == empty_target
    assert not list(tmp_path.glob(".sm64-profile-copy-*"))


def test_target_becoming_populated_during_copy_is_not_overwritten(tmp_path, monkeypatch):
    old, new = profile(tmp_path)
    copy = migration.shutil.copytree
    def raced(*args, **kwargs):
        result = copy(*args, **kwargs)
        if str(args[0]) == str(old):
            new.mkdir()
            (new / "new-pb.dat").write_bytes(b"concurrent-pb")
        return result
    monkeypatch.setattr(migration.shutil, "copytree", raced)
    with pytest.raises(ValueError, match="now contains data"):
        migration.migrate_private_beta_profile(tmp_path, confirmed=True)
    assert (new / "new-pb.dat").read_bytes() == b"concurrent-pb"
    assert not list(tmp_path.glob(".sm64-profile-copy-*"))


def test_linked_source_entry_is_refused_and_preserves_original(tmp_path):
    old, new = profile(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    linked = old / "data" / "linked"
    try:
        os.symlink(outside, linked, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Symlink permission unavailable.")
        import _winapi
        _winapi.CreateJunction(str(outside), str(linked))
    with pytest.raises((ValueError, shutil.Error), match="linked"):
        migration.migrate_private_beta_profile(tmp_path, confirmed=True)
    assert not new.exists()
