"""Storage maintenance reuses admitted paths, but never stale file sizes."""
import os
from dataclasses import replace
from pathlib import Path

import pytest

from sm64_events.replay.ring import SegmentRing
from test_replay_ring import T0, seg


def test_maintenance_path_io_scales_with_directories_not_retained_segments(tmp_path, monkeypatch):
    ring = SegmentRing(None, 10**9, scratch_root=tmp_path)
    for index in range(80):
        ring.add(seg(tmp_path, index))
    nested = tmp_path / "active"
    nested.mkdir()
    growing = nested / "encoder.ts"
    growing.write_bytes(b"x" * 30)
    resolved = []
    original = Path.resolve

    def observe(path, *args, **kwargs):
        resolved.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", observe)
    ring.maintain()
    assert ring.total_bytes == 8030
    assert len(resolved) <= 2, "maintenance reopens retained paths instead of reusing their identities"
    resolved.clear()
    with ring.pin("video", T0, T0):
        pass
    assert not resolved, "releasing a lease must not reopen every retained segment"
    growing.write_bytes(b"x" * 70)
    ring.maintain()
    assert ring.total_bytes == 8070
    assert len(resolved) <= 2


def test_inventory_refreshes_nested_partials_publication_and_removal(tmp_path):
    ring = SegmentRing(None, 10**9, scratch_root=tmp_path)
    nested = tmp_path / "clips"
    nested.mkdir()
    partial, output = nested / "cut.part", nested / "cut.mp4"
    partial.write_bytes(b"x" * 21)
    ring.maintain()
    assert ring.total_bytes == 21
    with partial.open("ab") as stream:
        stream.write(b"more")
    partial.replace(output)
    ring.maintain()
    assert ring.total_bytes == 25
    output.unlink()
    ring.maintain()
    assert ring.total_bytes == 0


def test_unavailable_scan_keeps_previous_inventory_until_volume_returns(tmp_path, monkeypatch):
    ring = SegmentRing(None, 1000, scratch_root=tmp_path)
    path = tmp_path / "active.ts"
    path.write_bytes(b"x" * 120)
    ring.maintain()
    original = os.scandir

    def unavailable(_):
        raise PermissionError("volume temporarily unavailable")

    monkeypatch.setattr(os, "scandir", unavailable)
    ring.maintain()
    assert ring.total_bytes == 120
    monkeypatch.setattr(os, "scandir", original)
    path.write_bytes(b"x" * 180)
    ring.maintain()
    assert ring.total_bytes == 180


def test_relative_segment_identity_survives_recount_prune_and_deletion(tmp_path, monkeypatch):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(tmp_path)
    source = seg(scratch, 0)
    ring = SegmentRing(None, 1000, scratch_root=scratch)
    ring.add(replace(source, path=Path("scratch") / source.path.name))
    monkeypatch.chdir(scratch)
    ring.prune_missing()
    with ring.pin("video", source.utc_start, source.utc_end):
        assert ring.protected_paths() == {source.path}
        ring.maintain()
        assert ring.total_bytes == 100
        ring.set_limits(None, 0)
        assert source.path.exists()
    assert not source.path.exists()
    assert ring.total_bytes == 0
    assert not ring._segment_paths
    replacement = seg(scratch, 0, size=80)
    ring.set_limits(None, 1000)
    ring.add(replacement)
    ring.reset()
    assert ring.total_bytes == 0 and not ring._segment_paths


@pytest.mark.skipif(os.name != "nt", reason="Windows directory junction boundary")
def test_inventory_never_traverses_external_junction(tmp_path):
    import _winapi

    scratch, outside = tmp_path / "scratch", tmp_path / "saved"
    scratch.mkdir()
    outside.mkdir()
    saved = outside / "keep.mp4"
    saved.write_bytes(b"saved")
    junction = scratch / "linked"
    _winapi.CreateJunction(str(outside), str(junction))
    ring = SegmentRing(None, 0, scratch_root=scratch)
    try:
        ring.maintain()
        assert ring.total_bytes == 0
        ring.clear()
        assert saved.read_bytes() == b"saved"
    finally:
        junction.rmdir()  # remove only the junction, never its target


def test_inventory_never_counts_symlink_targets(tmp_path):
    scratch, outside = tmp_path / "scratch", tmp_path / "saved.mp4"
    scratch.mkdir()
    outside.write_bytes(b"saved")
    try:
        (scratch / "link.mp4").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    ring = SegmentRing(None, 0, scratch_root=scratch)
    ring.maintain()
    assert ring.total_bytes == 0
    ring.clear()
    assert outside.read_bytes() == b"saved"
