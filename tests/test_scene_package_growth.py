"""A scene package read while it is written (tools/scene_lab/package.py: PackageWriter.publish,
grown_package, Package.carry and the reader's check of what a publication adds).

scene_attempt_fixture.py's synthetic session (with scene state) is written in place and
published as it grows; each publication must read like the finished package for the frames it
holds, and the reader's shortcut, checking only the rows an earlier publication did not hold,
must still refuse what a whole check refuses.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import package  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402

TASKS = 120


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    folder = tmp_path_factory.mktemp("growth")
    rom = fixture.rom()
    session = fixture.write_session(folder, rom, [(5000 + index, 9, 1) for index in range(TASKS)])
    source = package.tap_source(session, "next", states=True)
    path, sidecar = folder / "growing.sm64scene", folder / "growing.grow"
    writer = package.PackageWriter(source, rom, path, package.ZLIB, level=6, in_place=True, group=4)
    publications = []
    for count, capture in enumerate(source.captures, 1):
        writer.add(capture)
        if count in (30, 31, 77):
            frames = writer.publish(sidecar)
            publications.append((frames, sidecar.read_bytes(), path.read_bytes()))
    writer.finish()
    return {"rom": rom, "publications": publications, "final": package.Package(path.read_bytes(), rom)}


def _grown(published, number, previous=None):
    frames, sidecar, file = published["publications"][number]
    header = package.Header(*package.HEADER.unpack_from(sidecar))
    return package.grown_package(sidecar, [file[package.HEADER.size:header.index_offset]], published["rom"], previous)


def _same(a: package.Package, b: package.Package, count: int) -> None:
    for index in range(count):
        assert a.frame(index) == b.frame(index)
        assert package.state_fingerprint(*a.scene_state(index)[1:]) == package.state_fingerprint(*b.scene_state(index)[1:])


def test_every_publication_reads_as_the_finished_package_for_the_frames_it_holds(published):
    final = published["final"]
    previous = None
    for number, (frames, _sidecar, _file) in enumerate(published["publications"]):
        assert frames == [29, 30, 76][number]                      # the scene state runs one frame behind
        grown = _grown(published, number, previous)
        assert len(grown) == frames
        _same(grown, final, frames)
        assert package._checked_counts(grown.index, previous.index)["frames"] == len(previous) \
            if previous is not None else True
        previous = grown
    final.carry(previous)
    assert final.lock is previous.lock                             # one cursor, one lock, across the swap
    _same(final, published["final"], len(final))


def test_a_publication_that_does_not_extend_the_one_before_is_checked_whole_and_refused(published):
    earlier = _grown(published, 0)
    later = _grown(published, 2, earlier)
    index = later.index
    moved = list(index.frames)
    moved[3] = (moved[3][0], moved[3][1] + 4, *moved[3][2:])      # an old frame record moved: no longer a prefix
    tampered = package.Index(index.meta, index.loads, index.chunks, index.blocks, moved, index.layers)
    assert package._checked_counts(tampered, earlier.index)["frames"] == 0
    with pytest.raises(package.PackageError, match="overlapping entries in a chunk|outside its chunk"):
        package.check_index(tampered, later.header, len(published["rom"]), earlier.index)
    overlapping = list(index.frames)                              # a new frame over an old one in a shared chunk
    first_new = len(earlier)
    overlapping[first_new] = (overlapping[first_new - 1][0], overlapping[first_new - 1][1], *overlapping[first_new][2:])
    tampered = package.Index(index.meta, index.loads, index.chunks, index.blocks, overlapping, index.layers)
    assert package._checked_counts(tampered, earlier.index)["frames"] == len(earlier)
    with pytest.raises(package.PackageError, match="overlapping entries in a chunk|outside its chunk"):
        package.check_index(tampered, later.header, len(published["rom"]), earlier.index)
