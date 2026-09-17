"""The actual capture sink publishes readable indexed media before it stops."""
import io
import json
import os
import threading
import time
from types import SimpleNamespace

import av
import pytest

from test_replay_picture_identity import encoder as encoder, read_pictures, record_picture_schedule
from sm64_events.replay.config import ReplayConfig
from sm64_events.replay.ffmpeg_sink import FfmpegAvSink
from sm64_events.replay.fragmentstore import FragmentArchive
from sm64_events.replay.fragments import FragmentReader
from sm64_events.replay.ledger import PictureLedger
from sm64_events.replay.ring import SegmentRing


def connected_capture(tmp_path, encoder):
    ffmpeg, codec = encoder
    ring = SegmentRing(None, 10**8, scratch_root=tmp_path)
    archives, errors = [], []
    opened = threading.Event()

    def create(run, dims):
        assert dims == (640, 480)
        archive = FragmentArchive(tmp_path, ring, run, extent_bytes=4096)
        archives.append(archive)
        opened.set()
        return archive

    ledger = PictureLedger()
    config = ReplayConfig(scratch_dir=tmp_path, segment_s=.5)
    sink = FfmpegAvSink(config, ring.add, ffmpeg=ffmpeg, codec=codec, fragment_factory=create,
                       on_fed=lambda tag, at, **clock: ledger.mark_fed(tag[1] if tag else None, at, **clock))

    def capture():
        try:
            record_picture_schedule(sink, ledger, "regular")
        except (AssertionError, OSError, ValueError, RuntimeError) as error:
            errors.append(repr(error))

    worker = threading.Thread(target=capture, daemon=True)
    worker.start()
    prefix = None
    try:
        assert opened.wait(5), errors
        archive = archives[0]
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            try:
                with archive.read(0, 45000) as (_, chunks):
                    prefix = b"".join(chunks)
                break
            except LookupError:
                time.sleep(.02)
        assert prefix and worker.is_alive() and not archive.closed, (errors, archive.error)
        with av.open(io.BytesIO(prefix)) as source:
            assert next(source.decode(video=0)).pts == 0
    finally:
        worker.join(15)
        if worker.is_alive():
            sink.stop()
            worker.join(5)
    assert not worker.is_alive() and not errors, errors
    assert len(archives) == 1 and archive.closed and archive.error is None
    return archive, ledger, prefix


def test_actual_sink_to_indexed_leased_prefix(tmp_path, encoder):
    archive, ledger, prefix = connected_capture(tmp_path, encoder)
    feeds = ledger.feeds_between(0, 1e12)
    # End before the final heartbeat; its last nominal duration may outlast
    # the stopped audio source. No tail synthesis belongs to this read path.
    end = feeds[-2]["pts"]
    with archive.read(0, end) as (samples, chunks):
        output = tmp_path / "selected.mp4"
        full_bytes = b"".join(chunks)
        output.write_bytes(full_bytes)
    decoded = read_pictures(output)
    actual_ticks = [round(stamp * 90000) for stamp, _ in decoded]
    rows = {row["ts"]: row["frame"] for row in ledger.rows_between(0, 1e12)}
    expected = [rows[entry["ts"]] for entry in feeds if entry["pts"] < end]
    actual = [number for (stamp, number) in decoded if round(stamp * 90000) < end]
    assert len(expected) >= 118 and actual == expected
    assert actual_ticks[:len(samples)] == [sample.pts for sample in samples]
    assert actual_ticks == [entry["pts"] for entry in feeds[:len(actual_ticks)]]
    assert all(entry["run_id"] == archive.run.id for entry in feeds)
    assert len(list(tmp_path.glob("fragments_*.bin"))) > 1, "exercise real extent rotation"
    assert not list(tmp_path.glob("*.ts")), "experimental publication must not duplicate the recording"
    # Random access spans actual rotated extent files, retaining preceding GOP
    # bytes. Logical visible bounds select source ticks without re-timing them.
    with archive.read(81000, 166500) as (middle, chunks):
        output.write_bytes(b"".join(chunks))
    middle_decoded = read_pictures(output)
    assert [round(stamp * 90000) for stamp, _ in middle_decoded if 81000 <= round(stamp * 90000) < 166500] == [
        sample.pts for sample in middle if sample.pts >= 81000]
    # Independently demux the actual output to find exact keyframe boundaries.
    # AAC packets can straddle these; selecting video alone loses needed audio.
    with av.open(io.BytesIO(full_bytes)) as source:
        keys = [p.pts for p in source.demux(video=0) if p.size and p.is_keyframe and 0 < p.pts < end - 18000]
    assert keys
    for key in keys:
        with archive.read(key, key + 18000) as (boundary, chunks):
            output.write_bytes(b"".join(chunks))
        assert boundary[0].pts == key
        assert [round(stamp * 90000) for stamp, _ in read_pictures(output)
                if key <= round(stamp * 90000) < key + 18000] == [s.pts for s in boundary]
    bounded_index_keeps_reader(tmp_path / "bounded", archive.run, full_bytes)
    (tmp_path / "connection.json").write_text(json.dumps({"codec": encoder[1], "run": archive.run.id,
        "prefix_bytes": len(prefix), "source_pictures": len(feeds), "decoded_pictures": len(decoded),
        "extent_files": len(list(tmp_path.glob("fragments_*.bin")))}), encoding="utf-8")


def bounded_index_keeps_reader(root, run, encoded):
    ring = SegmentRing(None, 10**8, scratch_root=root)
    archive = FragmentArchive(root, ring, run, extent_samples=1, index_samples=100)
    parser = FragmentReader()
    units = iter(parser.feed(encoded))
    archive.feed(next(units).data)
    # Publish enough for the early reader, then exceed the metadata limit while
    # its immutable ranges remain leased. There are no per-attempt media copies.
    for unit in units:
        archive.feed(unit.data)
        try:
            with archive.read(0, 18000):
                break
        except LookupError:
            pass
    with archive.read(0, 18000) as (_, old_reader):
        for unit in units:
            archive.feed(unit.data)
        archive.finish()
        assert archive.error is None and archive._count <= 100
        with pytest.raises(LookupError, match="evicted"):
            with archive.read(0, 18000):
                pass
        with av.open(io.BytesIO(b"".join(old_reader))) as source:
            assert next(source.decode(video=0)).pts == 0
        leased_bytes = ring.total_bytes
    assert ring.total_bytes < leased_bytes
    ring.set_limits(None, 0)
    assert ring.total_bytes == 0 and not ring.protected_paths()


def test_failed_archive_drains_encoded_pipe_without_blocking_producer(tmp_path):
    read_fd, write_fd = os.pipe()
    wrote, failures, finished = [], [], []

    class Rejected:
        def feed(self, data):
            raise ValueError("index rejected")

        def finish(self, error):
            finished.append(error)

    def producer():
        try:
            for _ in range(32):
                wrote.append(os.write(write_fd, b"x" * 16384))
        except OSError as error:
            failures.append(error)
        finally:
            os.close(write_fd)

    sink = FfmpegAvSink(ReplayConfig(scratch_dir=tmp_path), lambda _: None,
                       fragment_factory=lambda *_: Rejected())
    worker = threading.Thread(target=producer, daemon=True)
    worker.start()
    try:
        sink._fragment_loop(SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: read_fd)), (640, 480), None)
        worker.join(2)
    finally:
        os.close(read_fd)
        worker.join(2)
    assert not worker.is_alive() and not failures
    assert sum(wrote) == 32 * 16384 and finished == ["index rejected"]
