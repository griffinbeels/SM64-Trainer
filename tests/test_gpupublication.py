"""Slow disks cannot retain capture credits; accepted bytes stay exact and bounded."""

from types import SimpleNamespace as NS
import threading

import pytest

from sm64_events.replay.gpupublication import ArchiveOutput, PublicationBusyError, PublicationError


class BlockedArchive:
    def __init__(self):
        self.entered, self.release = threading.Event(), threading.Event()
        self.chunks = []
        self.finished = False
        self.error = None

    def feed(self, data):
        self.entered.set()
        assert self.release.wait(5), "test must release the owned archive worker"
        self.chunks.append(data)

    def finish(self, error):
        self.error, self.finished = error, True


def test_blocked_disk_preserves_byte_order_without_blocking_capture():
    archive = BlockedArchive()
    output = ArchiveOutput(archive, max_bytes=16)
    try:
        assert output.write(b"first") == 5
        assert archive.entered.wait(2)
        # Consumer is provably blocked; this second admission must still return.
        assert output.write(b"second") == 6
        assert output.status()["pending_bytes"] == 11
        assert not archive.chunks and not archive.finished
        with pytest.raises(PublicationBusyError):
            output.finish(timeout=.01)
        assert not archive.finished  # caller cannot dispose a writer-owned archive
    finally:
        archive.release.set()
        output.finish(timeout=2)
    assert archive.chunks == [b"first", b"second"]
    assert archive.finished and archive.error is None
    assert output.status()["pending_bytes"] == 0
    with pytest.raises(RuntimeError, match="closed"):
        output.write(b"late")


@pytest.mark.parametrize("max_bytes,max_blocks", [(7, 20), (100, 1)])
def test_capacity_counts_inflight_block(max_bytes, max_blocks):
    archive = BlockedArchive()
    output = ArchiveOutput(archive, max_bytes=max_bytes, max_blocks=max_blocks)
    try:
        output.write(b"1234")
        assert archive.entered.wait(2)
        with pytest.raises(RuntimeError, match="capacity"):
            output.write(b"5678")
        assert output.status()["pending_bytes"] == 4
    finally:
        archive.release.set()
        with pytest.raises(RuntimeError, match="capacity"):
            output.finish(timeout=2)
    assert archive.chunks == [b"1234"] and "capacity" in archive.error


def test_age_limit_includes_block_inside_disk_call():
    now = [10.0]
    archive = BlockedArchive()
    output = ArchiveOutput(archive, max_bytes=16, max_age=1, clock=lambda: now[0])
    try:
        output.write(b"held")
        assert archive.entered.wait(2)
        now[0] = 11.01
        with pytest.raises(RuntimeError, match="age budget"):
            output.check()
    finally:
        archive.release.set()
        with pytest.raises(RuntimeError, match="age budget"):
            output.finish(timeout=2)
    assert "age budget" in archive.error


def test_expired_feed_remains_failed_after_it_finishes_without_owner_polling():
    now = [10.0]
    archive = BlockedArchive()
    output = ArchiveOutput(archive, max_bytes=16, max_age=1, clock=lambda: now[0])
    try:
        output.write(b"held")
        assert archive.entered.wait(2)
        now[0] = 12.0
    finally:
        archive.release.set()
        with pytest.raises(PublicationError, match="age budget"):
            output.finish(timeout=2)
    assert output.status()["pending_bytes"] == 0
    with pytest.raises(RuntimeError, match="age budget"):
        output.check()


def test_nonthrowing_archive_close_failure_is_reported():
    archive = NS(feed=lambda data: None, error=None)
    archive.finish = lambda error: setattr(archive, "error", "incomplete MP4 box")
    output = ArchiveOutput(archive, max_bytes=16)
    output.write(b"bytes")
    with pytest.raises(PublicationError, match="incomplete MP4 box"):
        output.finish(timeout=2)


@pytest.mark.parametrize("where", ["feed", "finish"])
def test_writer_failure_reaches_owner_and_rejects_new_admission(where):
    def fail(*args):
        raise OSError("disk gone")

    archive = NS(feed=fail if where == "feed" else lambda data: None,
                 finish=fail if where == "finish" else lambda error: None)
    output = ArchiveOutput(archive, max_bytes=16)
    output.write(b"bytes")
    with pytest.raises(RuntimeError, match="disk gone"):
        output.finish(timeout=2)
    with pytest.raises(RuntimeError, match="disk gone"):
        output.check()
    with pytest.raises(RuntimeError, match="disk gone"):
        output.write(b"late")
