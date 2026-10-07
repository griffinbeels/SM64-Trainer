"""Review leases publish only selected, revalidated assets, never PBs."""
from datetime import datetime, timedelta, timezone
import json
import zipfile

import pytest

from sm64_events.ghost.portable import canonical, identity, make_entry, validate_package, write_package
from tests.test_ghost_sharing import Store, package, provider, recording, segment
from sm64_events.ghost.sharing_targets import describe


def two_rows(tmp_path):
    original_path, first = recording(tmp_path)
    first_path = tmp_path / "first.ghost"
    first_path.write_bytes(original_path.read_bytes())
    second_path, second = recording(tmp_path, pad=(1, 1, 1))
    source = tmp_path / "two.sm64ghosts"
    write_package(source, [(first, first_path), (second, second_path)])
    return source, first, second


def test_preview_and_discard_leave_collection_and_pb_state_unchanged(tmp_path):
    source, _, row = package(tmp_path)
    sharing = provider(tmp_path)
    preview = sharing.preview_import(source)
    ghost, = preview["ghosts"]
    assert ghost["id"] == row["id"] and ghost["importable"] and not ghost["duplicate"]
    assert ghost["comparison"] == "unknown" and ghost["game_version"] is None
    assert sharing.imported_rows() == [] and not sharing._index.exists() and sharing.database.writes == []
    folder = sharing.previews.rows[preview["preview_id"]]["folder"]
    assert folder.is_dir()
    assert sharing.discard_preview(preview["preview_id"]) == {"discarded": True}
    assert not folder.exists() and sharing.imported_rows() == []
    with pytest.raises(ValueError, match="expired"):
        sharing.commit_import(preview["preview_id"], [row["id"]])


def test_selective_commit_publishes_only_selected_id_and_clip(tmp_path):
    source, first, second = two_rows(tmp_path)
    sharing = provider(tmp_path)
    preview = sharing.preview_import(source)
    result = sharing.commit_import(preview["preview_id"], [second["id"]])
    assert result["import_ids"] == [second["id"]] and result["imported"] == 1
    assert [row["id"] for row in sharing.imported_rows()] == [second["id"]]
    assert not (sharing.root / f"{first['sha256']}.ghost").exists()
    assert (sharing.root / f"{second['sha256']}.ghost").exists()
    assert sharing.database.writes == []


def test_preview_duplicates_disabled_and_commit_rechecks_concurrent_import(tmp_path):
    source, _, row = package(tmp_path)
    sharing = provider(tmp_path)
    preview = sharing.preview_import(source)
    sharing.import_file(source)
    result = sharing.commit_import(preview["preview_id"], [row["id"]])
    assert result["imported"] == 0 and result["duplicates"] == 1
    again = sharing.preview_import(source)
    assert again["ghosts"][0]["duplicate"] and not again["ghosts"][0]["importable"]


def test_commit_rechecks_bytes_before_any_publication_and_keeps_preview_for_retry(tmp_path):
    source, _, row = package(tmp_path)
    sharing = provider(tmp_path)
    preview = sharing.preview_import(source)
    lease = sharing.previews.rows[preview["preview_id"]]
    (lease["folder"] / f"{row['sha256']}.ghost").write_bytes(b"changed after review")
    with pytest.raises(ValueError):
        sharing.commit_import(preview["preview_id"], [row["id"]])
    assert sharing.imported_rows() == [] and not sharing._index.exists()
    assert preview["preview_id"] in sharing.previews.rows


def test_commit_rechecks_definition_and_imports_unmatched_segment_without_creating_it(tmp_path):
    target = describe("segment", "3", "Standard", [segment(3)])
    path, row = recording(tmp_path, kind="segment", target=target)
    source, _, _ = package(tmp_path, rows=[(row, path)])
    store = Store(definitions=[segment(97)])
    sharing = provider(tmp_path, store)
    preview = sharing.preview_import(source)
    assert preview["ghosts"][0]["available"]
    store.definitions = []
    result = sharing.commit_import(preview["preview_id"], [row["id"]])
    assert result["unmatched"] == 1 and store.definitions == []
    assert not sharing.imported_rows()[0]["available"]


def test_preview_expiry_count_and_disk_budget_are_bounded(tmp_path, monkeypatch):
    source, _, row = package(tmp_path)
    sharing = provider(tmp_path)
    monkeypatch.setattr("sm64_events.ghost.library_imports.MAX_PREVIEWS", 1)
    first = sharing.preview_import(source)
    with pytest.raises(ValueError, match="Close"):
        sharing.preview_import(source)
    lease = sharing.previews.rows[first["preview_id"]]
    folder = lease["folder"]
    lease["expires"] = datetime.now(timezone.utc) - timedelta(seconds=1)
    second = sharing.preview_import(source)
    assert not folder.exists() and second["preview_id"] != first["preview_id"]
    sharing.discard_preview(second["preview_id"])
    monkeypatch.setattr("sm64_events.ghost.library_imports.MAX_PREVIEW_BYTES", 1)
    with pytest.raises(ValueError, match="size limit"):
        sharing.preview_import(source)
    assert not list(sharing.previews.root.glob("preview-*"))


def test_provider_recreation_cleans_owned_staging_only(tmp_path):
    source, _, _ = package(tmp_path)
    sharing = provider(tmp_path)
    preview = sharing.preview_import(source)
    folder = sharing.previews.rows[preview["preview_id"]]["folder"]
    unrelated = sharing.previews.root / "preview-unowned"
    unrelated.mkdir(); (unrelated / "keep.txt").write_text("unrelated")
    fresh = provider(tmp_path)
    assert not folder.exists() and unrelated.is_dir() and fresh.imported_rows() == []


def test_preview_known_version_compares_against_real_history_not_recording_catalog(tmp_path):
    from dataclasses import replace
    from tests.test_ghost_library_bests import run
    path, unknown = recording(tmp_path)
    row = make_entry(path, author=unknown["author"], target=unknown["target"], frames=190,
                     clock="igt", recorded_utc=unknown["recorded_utc"], practice=False,
                     outcome="success", game_version="us")
    source, _, _ = package(tmp_path, rows=[(row, path)])
    sharing = provider(tmp_path, Store(attempts=[replace(run(1, 200, game_version="us"), strat_tag="10 coin")]))
    assert sharing.catalog()["ghosts"] == []
    ghost, = sharing.preview_import(source)["ghosts"]
    assert ghost["comparison"] == "faster" and ghost["your_best_frames"] == 200


def test_expensive_clip_verification_does_not_block_native_provider_reads(tmp_path, monkeypatch):
    import threading
    from sm64_events.ghost import sharing as module
    source, _, row = package(tmp_path)
    sharing = provider(tmp_path)
    preview = sharing.preview_import(source)
    real = module.read_clip
    checks = []

    def read(*args):
        answer = []
        reader = threading.Thread(target=lambda: answer.append(sharing.generation))
        reader.start(); reader.join(timeout=1)
        assert not reader.is_alive(), "Clip validation blocked the native session's generation read"
        checks.append(True)
        return real(*args)

    monkeypatch.setattr(module, "read_clip", read)
    sharing.commit_import(preview["preview_id"], [row["id"]])
    assert checks == [True]


def test_selection_and_current_capacity_refused_before_publication(tmp_path, monkeypatch):
    source, _, row = package(tmp_path)
    sharing = provider(tmp_path)
    preview = sharing.preview_import(source)
    for ids in ([], [row["id"], row["id"]], ["0" * 64], [1]):
        with pytest.raises(ValueError):
            sharing.commit_import(preview["preview_id"], ids)
    monkeypatch.setattr("sm64_events.ghost.sharing.MAX_IMPORTED", 0)
    with pytest.raises(ValueError, match="full"):
        sharing.commit_import(preview["preview_id"], [row["id"]])
    assert not sharing._index.exists()


def test_version_two_carries_version_and_legacy_reexport_retains_identity_and_credit(tmp_path):
    path, row = recording(tmp_path)
    versioned = make_entry(path, author=row["author"], target=row["target"], frames=row["frames"],
                           clock=row["clock"], recorded_utc=row["recorded_utc"], practice=False,
                           outcome="success", game_version="jp")
    assert versioned["id"] != row["id"]
    legacy = {key: value for key, value in row.items() if key != "game_version"}
    legacy["id"] = identity(legacy)
    source = tmp_path / "legacy.sm64ghosts"
    with zipfile.ZipFile(source, "w") as bundle:
        bundle.writestr("collection.json", canonical({"format": "sm64-ghost-collection", "version": 1, "ghosts": [legacy]}))
        bundle.write(path, legacy["file"])
    sharing = provider(tmp_path)
    sharing.import_file(source)
    assert sharing.imported_rows()[0]["game_version"] is None
    exported = sharing.export({"imports": [legacy["id"]]}, "Someone else")
    staging = tmp_path / "roundtrip"; staging.mkdir()
    rows, _ = validate_package(exported["path"], staging)
    assert rows == [legacy] and rows[0]["author"] == "Runner One"
    with zipfile.ZipFile(exported["path"]) as bundle:
        assert json.loads(bundle.read("collection.json"))["version"] == 2
