"""The lab audio capture (tools/scene_lab/audio_capture.py), with no audio device.

A fake source stands in for ProcessAudioSource and a scripted clock for
time, so each rule runs instantly: the machine-wide recorder lock refuses a
second recorder, the device-wide fallback is refused, a chunk the writer
cannot keep up with is kept as a LOST record that still counts its samples,
and the capture ends when Project64 exits. What lands on disk is read back
with audio_session.py, the reader the offline join uses.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/scene_lab"))
import audio_capture as cap  # noqa: E402
import audio_clock as ac  # noqa: E402
import audio_session as asn  # noqa: E402

from sm64_events.core import recorder_lock  # noqa: E402

FREQ, CHUNK = 10_000_000, 480


def _pcm(i: int) -> np.ndarray:
    return np.full((CHUNK, 2), i, np.int16)


class Ticks:
    """QPC stamps 10 ms apart, as a steady 48 kHz tap would arrive."""

    def __init__(self):
        self.now = 7_000_000_000

    def __call__(self):
        self.now += CHUNK * FREQ // cap.RATE
        return self.now


class FakeSource:
    def __init__(self, pid, *, mode="process", chunks=4, fail=None, health=None):
        self.pid, self.mode, self.fail, self.health = pid, mode, fail, health
        self.to_send = [_pcm(i) for i in range(chunks)]
        self.on_pcm, self.started, self.stopped = None, False, False

    def start(self, on_pcm):
        if self.fail:
            raise self.fail
        self.on_pcm, self.started = on_pcm, True
        self.deliver()

    def deliver(self):
        if self.on_pcm is not None and not self.stopped and self.to_send:
            self.on_pcm(self.to_send.pop(0))

    def check_health(self):
        if self.health:
            raise RuntimeError(self.health)

    def stop(self):
        self.stopped = True


class Clock:
    def __init__(self, source_box):
        self.t, self.box = 0.0, source_box

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds
        if self.box:
            self.box[0].deliver()        # the source keeps delivering while the capture polls


class Watch:
    """Project64 stays open for `polls` checks, then exits."""

    def __init__(self, polls):
        self.polls, self.closed = polls, False

    def __call__(self, pid):
        return self

    def alive(self):
        self.polls -= 1
        return self.polls >= 0

    def close(self):
        self.closed = True


def _run(tmp_path, *, source=None, polls=8, find_pid=lambda: (4242, ""), seconds=600.0, **kwargs):
    box = [] if source is None else [source]
    clock = Clock(box)
    out = tmp_path / "audio.cap"
    result = cap.capture(out, seconds=seconds, wait_seconds=30.0, budget_bytes=1 << 20, find_pid=find_pid,
                         watch=Watch(polls), make_source=kwargs.pop("make_source", lambda pid: source),
                         frequency=FREQ, stamp=Ticks(), now=clock.now, sleep=clock.sleep, **kwargs)
    return out, result


def _status(out: Path) -> dict:
    return json.loads(out.with_name(out.name + ".json").read_text(encoding="utf-8"))


def _lock_is_free() -> bool:
    handle = recorder_lock.acquire_recorder_lock()
    if handle is None:
        return False
    handle.close()
    return True


def test_capture_records_until_project64_exits_and_reads_back(tmp_path):
    source = FakeSource(4242, chunks=6)
    out, result = _run(tmp_path, source=source, polls=4)
    assert result["state"] == "stopped" and result["reason"] == "Project64 exited"
    assert source.started and source.stopped and _lock_is_free()
    rate, frequency, chunks, payloads = asn.read_capture(out)
    assert (rate, frequency) == (cap.RATE, FREQ)
    assert [c.first for c in chunks] == [i * CHUNK for i in range(len(chunks))] and len(chunks) >= 4
    assert not any(c.lost for c in chunks)
    assert all(b.arrival > a.arrival for a, b in zip(chunks, chunks[1:], strict=False))
    assert payloads[2] == _pcm(2).tobytes()
    assert _status(out)["kept_chunks"] == len(chunks) and _status(out)["lost_samples"] == 0


def test_the_capture_ends_after_its_seconds_or_when_the_source_fails(tmp_path):
    _, result = _run(tmp_path, source=FakeSource(4242), polls=10_000, seconds=1.0)
    assert result["reason"] == "1 s passed"
    _, result = _run(tmp_path, source=FakeSource(4242, health="reader died"), polls=10_000)
    assert result["reason"] == "source failed: reader died"


def test_a_held_recorder_lock_refuses_and_writes_no_audio(tmp_path):
    held = recorder_lock.acquire_recorder_lock()
    assert held is not None
    source = FakeSource(4242)
    try:
        with pytest.raises(cap.CaptureRefused, match="recorder lock"):
            _run(tmp_path, source=source)
    finally:
        held.close()
    assert not source.started and not (tmp_path / "audio.cap").exists()
    assert _status(tmp_path / "audio.cap")["state"] == "refused"


def test_the_command_runs_without_any_console_as_pythonw_starts_it(tmp_path, monkeypatch):
    """pythonw gives the process no stdout or stderr; the refusal still lands in the status file."""
    held = recorder_lock.acquire_recorder_lock()
    out = tmp_path / "hidden" / "audio.cap"
    monkeypatch.setattr(sys, "argv", ["audio_capture.py", str(out)])
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    try:
        assert cap.main() == 2
    finally:
        held.close()
    assert _status(out)["state"] == "refused" and not out.exists()


def test_the_device_wide_fallback_is_refused_with_a_reason(tmp_path):
    system = FakeSource(4242, mode="system")
    with pytest.raises(cap.CaptureRefused, match="device\\s+loopback would record everything"):
        _run(tmp_path, source=system)
    assert not system.started and _lock_is_free()
    broken = FakeSource(4242, fail=OSError("process loopback unavailable"))
    with pytest.raises(cap.CaptureRefused, match="could not be isolated .*fallback was refused"):
        _run(tmp_path, source=broken)
    assert not (tmp_path / "audio.cap").exists() and _lock_is_free()


def test_no_project64_is_a_refusal_after_the_wait(tmp_path):
    with pytest.raises(cap.CaptureRefused, match="no single Project64 within 30 s"):
        _run(tmp_path, source=FakeSource(1), find_pid=lambda: (None, "Open Project64 v1.6."))
    assert _lock_is_free()


def test_the_default_source_is_the_recorders_own_process_tap():
    from sm64_events.replay.audio import ProcessAudioSource

    source = cap.recorder_source(4242)
    assert isinstance(source, ProcessAudioSource) and source.mode == "process"


def test_a_writer_that_falls_behind_marks_losses_with_their_samples_and_never_waits():
    ticks = Ticks()
    fanout = cap.Fanout(ticks, budget_bytes=3 * CHUNK * 4)
    for i in range(6):                   # the writer is stalled: three fit, three are lost
        fanout.on_pcm(_pcm(i))
    done = cap.threading.Event()
    drained = list(_take(fanout.records(done), 3))
    for i in range(6, 8):                # it caught up: the next chunks are kept again
        fanout.on_pcm(_pcm(i))
    fanout.close()
    done.set()
    records = drained + list(fanout.records(done))
    chunks = [c for c, _ in records]
    assert [(c.first, c.samples, c.lost) for c in chunks] == [
        (0, CHUNK, False), (CHUNK, CHUNK, False), (2 * CHUNK, CHUNK, False),
        (3 * CHUNK, 3 * CHUNK, True), (6 * CHUNK, CHUNK, False), (7 * CHUNK, CHUNK, False)]
    assert records[3][1] is None and records[4][1] == _pcm(6).tobytes()
    # The merged loss carries the arrival of its last chunk: never earlier than its samples existed.
    assert chunks[2].arrival < chunks[3].arrival < chunks[4].arrival
    assert (fanout.lost_records, fanout.lost_samples, fanout.kept_chunks) == (1, 3 * CHUNK, 5)
    clock = ac.build_clock(chunks, [], rate=cap.RATE, frequency=FREQ)
    assert clock.losses == [(3 * CHUNK, 3 * CHUNK)] and clock.total_samples == 8 * CHUNK
    assert fanout.cost_us()["max"] >= 0


def test_a_long_loss_splits_into_records_the_reader_accepts(monkeypatch):
    monkeypatch.setattr(cap.ac, "MAX_CHUNK_SAMPLES", 2 * CHUNK)
    fanout = cap.Fanout(Ticks(), budget_bytes=0)
    for i in range(5):
        fanout.on_pcm(_pcm(i))
    fanout.close()
    done = cap.threading.Event()
    done.set()
    chunks = [c for c, _ in fanout.records(done)]
    assert [(c.first, c.samples, c.lost) for c in chunks] == [
        (0, 2 * CHUNK, True), (2 * CHUNK, 2 * CHUNK, True), (4 * CHUNK, CHUNK, True)]


def _take(iterator, count):
    for _ in range(count):
        yield next(iterator)
