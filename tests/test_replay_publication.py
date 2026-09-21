import json
from pathlib import Path

import pytest

from sm64_events.replay import publication


def inputs(tmp_path):
    source = tmp_path / "scratch.mp4"
    source.write_bytes(b"immutable video payload")
    root = tmp_path / "saved"
    destination = root / "date" / "attempt_0042_test.mp4"
    return source, root, destination


def native_inputs(tmp_path, destination):
    from ghost_native_fixture import attempt, frame, producer
    from sm64_events.ghost.clip import STAR_CLOCK, attempt_origin, write_clip

    row = attempt(end=101)
    row.id = 42
    target = {"kind": "star", "entity_key": "24-1"}
    ghost = tmp_path / "scratch.ghost"
    identity = write_clip(ghost, (frame(),), producer(), clock_kind=STAR_CLOCK)
    identity.update(attempt_id=row.id, session_id=row.session_id, target=target,
                    file=destination.with_suffix(".ghost").name, **attempt_origin(row))
    return ghost, identity, row, target


def test_publication_keeps_media_and_metadata_after_scratch_removed(tmp_path):
    source, root, destination = inputs(tmp_path)
    publication.publish(root, 42, source, destination, {"clock": [0, 0.0333]}, {"loop": None})
    source.unlink()
    assert destination.read_bytes() == b"immutable video payload"
    assert json.loads(destination.with_suffix(".json").read_text())["clock"] == [0, 0.0333]
    assert json.loads(destination.with_suffix(".review.json").read_text())["state"] == {"loop": None}
    assert not (root / ".pending" / "42").exists()


def test_same_volume_save_does_not_copy_video(tmp_path, monkeypatch):
    source, root, destination = inputs(tmp_path)
    def forbidden(*args):
        pytest.fail("same-volume immutable video should be linked, not copied")
    monkeypatch.setattr(publication.shutil, "copyfile", forbidden)
    publication.publish(root, 42, source, destination, {}, {})
    assert source.stat().st_ino == destination.stat().st_ino


def test_recover_after_metadata_published_before_video(tmp_path, monkeypatch):
    source, root, destination = inputs(tmp_path)
    replace = publication.os.replace
    def interrupt(src, dst):
        if Path(dst) == destination.resolve():
            raise OSError("injected interruption before video commit")
        return replace(src, dst)
    monkeypatch.setattr(publication.os, "replace", interrupt)
    with pytest.raises(OSError, match="injected"):
        publication.publish(root, 42, source, destination, {"clock": 7}, {})
    assert not destination.exists()
    source.unlink()  # recovery is independent of deleted scratch and recorder
    monkeypatch.setattr(publication.os, "replace", replace)
    assert publication.recover(root) == []
    assert destination.read_bytes() == b"immutable video payload"
    assert json.loads(destination.with_suffix(".json").read_text()) == {"clock": 7}


def test_cross_volume_copy_failure_never_publishes_partial(tmp_path, monkeypatch):
    source, root, destination = inputs(tmp_path)
    def no_link(*args):
        raise OSError("different volume")
    def partial(src, dst):
        Path(dst).write_bytes(b"partial")
        raise OSError("disk full")
    monkeypatch.setattr(publication.os, "link", no_link)
    monkeypatch.setattr(publication.shutil, "copyfile", partial)
    with pytest.raises(OSError, match="disk full"):
        publication.publish(root, 42, source, destination, {}, {}, reserve_bytes=0)
    assert not destination.exists()
    assert not list(root.rglob("*.copying"))
    assert source.read_bytes() == b"immutable video payload"


def test_recovery_refuses_outside_root_without_touching_target(tmp_path):
    root = tmp_path / "saved"
    pending = root / ".pending" / "42"
    pending.mkdir(parents=True)
    outside = tmp_path / "attempt_42.mp4"
    outside.write_bytes(b"keep")
    (pending / "intent.json").write_text(json.dumps({"version": 1,
        "destination": "../attempt_42.mp4", "media_bytes": 4}))
    assert "invalid replay publication destination" in publication.recover(root)[0]
    assert outside.read_bytes() == b"keep"


def test_native_pose_sidecar_commits_with_video_and_recovers_without_scratch(tmp_path, monkeypatch):
    from sm64_events.ghost.clip import check_manifest
    source, root, destination = inputs(tmp_path)
    ghost, identity, row, target = native_inputs(tmp_path, destination)
    replace = publication.os.replace

    def interrupt(src, dst):
        if Path(dst) == destination.resolve():
            assert destination.with_suffix(".ghost").is_file()
            raise OSError("native publication interrupted")
        return replace(src, dst)

    monkeypatch.setattr(publication.os, "replace", interrupt)
    with pytest.raises(OSError, match="interrupted"):
        publication.publish(root, 42, source, destination, {"native_ghost": identity}, {}, ghost=ghost)
    assert not destination.exists()
    source.unlink()
    ghost.unlink()
    monkeypatch.setattr(publication.os, "replace", replace)
    assert publication.recover(root) == []
    saved_identity = json.loads(destination.with_suffix(".json").read_text())["native_ghost"]
    assert saved_identity == identity
    recovered = check_manifest(destination.with_suffix(".ghost"), saved_identity,
                               attempt=row, target=target)
    assert recovered.clip_id == identity["clip_id"]


def test_native_pose_tampering_or_missing_bytes_never_publishes_video(tmp_path):
    from sm64_events.ghost.clip import check_manifest
    source, root, destination = inputs(tmp_path)
    ghost, identity, row, target = native_inputs(tmp_path, destination)
    check_manifest(ghost, {**identity, "file": ghost.name}, attempt=row, target=target)
    metadata = {"native_ghost": identity}
    with pytest.raises(ValueError, match="missing native ghost bytes"):
        publication.publish(root, 42, source, destination, metadata, {})
    changed = bytearray(ghost.read_bytes())
    changed[-1] ^= 1
    ghost.write_bytes(changed)
    with pytest.raises(ValueError, match="hash mismatch"):
        publication.publish(root, 42, source, destination, metadata, {}, ghost=ghost)
    assert not destination.exists()
