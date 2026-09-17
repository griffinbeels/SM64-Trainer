"""CPU-only eviction acknowledgements over scratch SQLite and inert media."""
from datetime import datetime, timezone
import threading
from types import SimpleNamespace

import pytest

from sm64_events.replay.fragmentmedia import FragmentMedia
from sm64_events.replay.media import MediaRun
from sm64_events.replay.picturearchive import PictureArchive
from sm64_events.replay.ring import SegmentRing


def utc(stamp):
    return datetime.fromtimestamp(stamp, timezone.utc)


@pytest.fixture
def retained(tmp_path):
    ledger = PictureArchive(tmp_path / 'identity.sqlite3')
    ring = SegmentRing(None, 10**8, scratch_root=tmp_path)
    media = FragmentMedia(tmp_path, ring, ledger)
    ring._on_temp_evict = media.evicted
    run = MediaRun('retired-run', 1000)
    archive = SimpleNamespace(run=run, closed=True, sample_count=0, coverage=lambda: None)
    media._runs[run.id] = archive
    for number in range(3):
        stamp = 1000 + number
        ledger.add_row({'ts': stamp, 'frame': number, 'source_id': f'picture-{number}'})
        ledger.add_feed({'at': stamp, 'ts': stamp, 'run_id': run.id,
                         'pts': number * 90000, 'source_id': f'picture-{number}'})
    try:
        yield media, ledger, archive
    finally:
        ledger.close()


def test_failed_eviction_retries_exact_interval_before_forgetting_archive(retained, monkeypatch):
    media, ledger, archive = retained
    media.evicted(f'fragments:{archive.run.id}:1', utc(1000), utc(1002))
    calls = []
    discard = ledger.discard_segment

    def fail_once(segment):
        calls.append((segment.media_run.id, segment.utc_start, segment.utc_end))
        if len(calls) == 1:
            raise OSError('injected ledger failure')
        discard(segment)

    monkeypatch.setattr(ledger, 'discard_segment', fail_once)
    with pytest.raises(OSError, match='injected'):
        media.maintain()
    assert media._runs[archive.run.id] is archive
    assert len(media._evicted) == 1
    assert len(ledger.rows_between(1000, 1001)) == 2
    media.maintain()
    assert calls == [(archive.run.id, utc(1000), utc(1002))] * 2
    assert not media._evicted and archive.run.id not in media._runs
    assert ledger.rows_between(1000, 1001) == ledger.feeds_between(1000, 1001) == []
    assert ledger.rows_between(1002, 1002)[0]['source_id'] == 'picture-2'


def test_competing_maintenance_does_not_acknowledge_or_wait_for_pending_callback(retained, monkeypatch):
    media, ledger, archive = retained
    media.evicted(f'fragments:{archive.run.id}:1', utc(1000), utc(1002))
    entered, release, competitor_done = threading.Event(), threading.Event(), threading.Event()
    calls, errors = [], []
    discard = ledger.discard_segment

    def blocked_once(segment):
        calls.append(threading.get_ident())
        if len(calls) == 1:
            entered.set()
            assert release.wait(3), 'test must release the original maintenance caller'
            raise OSError('first callback failed after competing maintenance')
        discard(segment)

    def first():
        try:
            media.maintain()
        except OSError as error:
            errors.append(str(error))

    def second():
        media.maintain()
        competitor_done.set()

    monkeypatch.setattr(ledger, 'discard_segment', blocked_once)
    worker = threading.Thread(target=first)
    competitor = threading.Thread(target=second)
    worker.start()
    try:
        assert entered.wait(1)
        competitor.start()
        assert competitor_done.wait(1), 'competing maintenance must skip without blocking'
        assert len(calls) == 1 and len(media._evicted) == 1
        assert media._runs[archive.run.id] is archive
    finally:
        release.set()
        worker.join(3)
        if competitor.ident is not None:
            competitor.join(3)
    assert not worker.is_alive() and not competitor.is_alive() and len(errors) == 1
    media.maintain()
    assert len(calls) == 2 and not media._evicted
    assert ledger.rows_between(1000, 1001) == ledger.feeds_between(1000, 1001) == []


def test_reader_delays_eviction_without_losing_the_empty_archives_run_clock(retained, tmp_path):
    media, ledger, archive = retained
    path = tmp_path / 'leased-fragment.bin'
    path.write_bytes(b'original fragment bytes')
    group = f'fragments:{archive.run.id}:1'
    media.ring.register_temp(group, [path], utc(1000), utc(1002))
    with media.ring.pin_temp(group):
        media.ring.forget_temp(group, delete=True)
        media.maintain()
        assert path.exists() and not media._evicted
        assert media._runs[archive.run.id] is archive
        assert len(ledger.rows_between(1000, 1001)) == 2
    assert not path.exists() and len(media._evicted) == 1
    media.maintain()
    assert not media._evicted and archive.run.id not in media._runs
    assert ledger.rows_between(1000, 1001) == ledger.feeds_between(1000, 1001) == []
