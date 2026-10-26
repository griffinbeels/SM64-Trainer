"""The sound of live play for cut scenes (tools/scene_lab/live_audio.py), with no audio device.

A fake source stands in for the recorder's ProcessAudioSource and delivers what its pump
would (read-only int16 stereo arrays) while a scripted QPC clock says when each chunk arrived.
The device clock runs a known ppm off a 10 MHz QPC, as in scene_sound_fixture.py, so every
expected sample is the simulation's truth. The last test cuts a scene from a synthetic tap
session, adds the sound the ring kept and opens it through the lab's real routes.
"""
import io
import json
import random

import av
import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import scene_sound_fixture as fx
from scene_sound_fixture import ac, pk

import audio_capture as cap  # noqa: E402  (tools/scene_lab, on the path the fixture set)
import audio_session as asn  # noqa: E402
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import live_audio as la  # noqa: E402
import scene_session  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

RATE, FREQ, CHUNK = fx.RATE, fx.FREQ, fx.CHUNK
CHUNK_TICKS = CHUNK * FREQ // RATE


class Clock:
    """The QPC the fanout reads: the test sets it to each chunk's arrival before delivering."""

    def __init__(self, now: int = fx.START):
        self.now = now

    def __call__(self) -> int:
        return self.now


class FakeSource:
    """What the recorder holds: start(on_pcm), stop(), check_health(), mode, ghost_timeline."""

    def __init__(self, pid, mode="process"):
        self.pid, self.mode = pid, mode
        self.on_pcm, self.stopped, self.health_checks = None, False, 0
        self.ghost_timeline = object()

    def start(self, on_pcm):
        self.on_pcm = on_pcm

    def check_health(self):
        self.health_checks += 1

    def stop(self):
        self.stopped = True


def _pcm(value: int, samples: int = CHUNK) -> np.ndarray:
    """A chunk as the pump hands it over: read-only, (samples, 2) int16."""
    return np.frombuffer(np.full((samples, 2), value, np.int16).tobytes(), np.int16).reshape(-1, 2)


def _records(live) -> list:
    """What the ring holds now, as (Chunk, bytes | None)."""
    return [(cap.chunk_of(record), record[5]) for record in live._fanout.snapshot()]


def _live(seconds=60.0, **kwargs):
    clock = Clock()
    return la.LiveAudio(seconds=seconds, frequency=FREQ, stamp=clock, **kwargs), clock


def _play(clock: Clock, source: FakeSource, chunks: int, *, start: int | None = None, value=None):
    """Deliver `chunks` 10 ms chunks arriving 10 ms apart; returns what was sent."""
    sent = []
    clock.now = clock.now if start is None else start
    for index in range(chunks):
        clock.now += CHUNK_TICKS
        sent.append(_pcm(index if value is None else value))
        source.on_pcm(sent[-1])
    return sent


def test_the_recorder_gets_the_very_same_pcm_and_the_same_source(tmp_path):
    live, clock = _live()
    made = []
    wrapped = live.wrap(lambda pid: made.append(FakeSource(pid)) or made[-1])(4242)
    source = made[0]
    received = []
    wrapped.start(received.append)
    sent = _play(clock, source, 5)
    assert all(got is chunk for got, chunk in zip(received, sent, strict=True))   # the same arrays, in order
    assert wrapped.mode == "process" and wrapped.ghost_timeline is source.ghost_timeline
    wrapped.check_health()
    assert source.health_checks == 1
    records = _records(live)
    assert [data for _chunk, data in records] == [chunk.tobytes() for chunk in sent]
    assert [chunk.first for chunk, _data in records] == [i * CHUNK for i in range(5)]
    wrapped.stop()
    assert source.stopped and live.wrap(None) is None


def test_the_real_process_source_keeps_everything_the_recorder_reads():
    from sm64_events.replay.audio import GhostTimeline, ProcessAudioSource

    live, _clock = _live()
    wrapped = live.wrap(lambda pid: ProcessAudioSource(pid))(4242)       # constructing starts nothing
    assert wrapped.mode == "process" and isinstance(wrapped.ghost_timeline, GhostTimeline)


def test_a_recorder_failure_is_its_own_and_a_ring_failure_never_reaches_it():
    live, clock = _live()
    source = FakeSource(1)
    wrapped = live.wrap(lambda pid: source)(1)

    def recorder(pcm):
        if int(pcm[0, 0]) == 1:
            raise RuntimeError("sink closed")

    wrapped.start(recorder)
    clock.now += CHUNK_TICKS
    source.on_pcm(_pcm(0))
    clock.now += CHUNK_TICKS
    with pytest.raises(RuntimeError, match="sink closed"):
        source.on_pcm(_pcm(1))                         # the recorder's own error, unchanged
    clock.now += CHUNK_TICKS
    source.on_pcm(_pcm(2))
    assert [c.first for c, _d in _records(live)] == [0, CHUNK, 2 * CHUNK]   # still counted

    def broken(pcm, arrival):
        raise MemoryError("no room")

    received = []
    other = FakeSource(2)
    wrapped = live.wrap(lambda pid: other)(2)
    wrapped.start(received.append)
    live._fanout.on_pcm, kept = broken, live._fanout.on_pcm
    clock.now += CHUNK_TICKS
    other.on_pcm(_pcm(7))                              # no exception reaches the recorder's thread
    live._fanout.on_pcm = kept
    clock.now += CHUNK_TICKS
    other.on_pcm(_pcm(8))
    assert len(received) == 2 and live.errors == 1
    last = _records(live)[-1][0]
    assert last.first == 0 and last.epoch == live._fanout.epoch     # a fresh epoch after the failure


def test_device_loopback_reaches_the_recorder_but_never_the_ring(tmp_path):
    live, clock = _live()
    assert live.health()["reason"] == la.NOT_STARTED
    source = FakeSource(1, mode="system")
    wrapped = live.wrap(lambda pid: source)(1)
    received = []
    wrapped.start(received.append)
    begun = clock.now
    sent = _play(clock, source, 20)
    assert len(received) == 20 and received[3] is sent[3]
    health = live.health()
    assert health["reason"] == la.NOT_ISOLATED and health["mode"] == "system" and health["chunks"] == 0
    refused = live.write_span(begun, clock.now, tmp_path / "a.cap")
    assert refused == {"written": False, "reason": la.NOT_ISOLATED} and not (tmp_path / "a.cap").exists()


def test_the_ring_keeps_its_seconds_and_its_bytes_and_counts_what_it_drops():
    live, clock = _live(seconds=1.0)
    source = FakeSource(1)
    live.wrap(lambda pid: source)(1).start(lambda pcm: None)
    _play(clock, source, 300)                          # 3 s through a 1 s ring
    health = live.health()
    assert health["recording"] and health["reason"] is None
    assert 1.0 - 0.011 <= health["kept_seconds"] <= 1.0 + 0.011
    assert health["evicted_chunks"] == 300 - health["chunks"] and health["lost_samples"] == 0
    firsts = [chunk.first for chunk, _data in _records(live)]
    assert firsts == list(range(firsts[0], 300 * CHUNK, CHUNK))      # contiguous after eviction

    small, clock = _live(seconds=60.0, budget_bytes=20 * CHUNK * 4)
    source = FakeSource(1)
    small.wrap(lambda pid: source)(1).start(lambda pcm: None)
    _play(clock, source, 50)
    assert small.health()["chunks"] == 20 and small.health()["kept_bytes"] == 20 * CHUNK * 4
    clock.now += CHUNK_TICKS
    source.on_pcm(_pcm(0, samples=21 * CHUNK))         # larger than the whole budget: lost, counted
    clock.now += CHUNK_TICKS
    source.on_pcm(_pcm(0))
    health = small.health()
    assert health["lost_samples"] == 21 * CHUNK and health["lost_records"] == 1
    lost = [chunk for chunk, data in _records(small) if data is None]
    assert [(c.first, c.samples) for c in lost] == [(50 * CHUNK, 21 * CHUNK)]


def test_a_stopped_recorder_says_so():
    live, clock = _live()
    source = FakeSource(1)
    wrapped = live.wrap(lambda pid: source)(1)
    wrapped.start(lambda pcm: None)
    _play(clock, source, 3)
    clock.now += 3 * FREQ
    assert live.health()["reason"] == "Project64's sound stopped reaching the Trainer."
    wrapped.stop()
    assert "stopped recording sound" in live.health()["reason"]


def test_write_span_writes_the_chunks_around_a_span_across_a_restart(tmp_path):
    live, clock = _live(seconds=30.0)
    first = FakeSource(1)
    wrapped = live.wrap(lambda pid: first)(1)
    wrapped.start(lambda pcm: None)
    origin = clock.now
    _play(clock, first, 500)                           # 5 s
    wrapped.stop()
    clock.now += FREQ // 2                             # half a second with no source
    second = FakeSource(1)
    live.wrap(lambda pid: second)(1).start(lambda pcm: None)
    _play(clock, second, 500)
    span = (origin + 4 * FREQ, origin + 6 * FREQ)
    result = live.write_span(*span, tmp_path / "span.cap", margin_s=0.5)
    assert result["written"] and result["reason"] is None and result["epochs"] == 2
    assert result["covers_start"] and result["covers_end"]
    rate, frequency, chunks, payloads = asn.read_capture(tmp_path / "span.cap")
    assert (rate, frequency) == (RATE, FREQ) and len(chunks) == result["chunks"]
    assert chunks[0].arrival >= span[0] - FREQ // 2 > chunks[0].arrival - 2 * CHUNK_TICKS
    assert chunks[-1].arrival <= span[1] + FREQ // 2 + la.LATE_S * FREQ
    assert payloads[0] == _pcm(int(chunks[0].first // CHUNK)).tobytes()
    clock_fit = ac.build_clock(chunks, [], rate=RATE, frequency=FREQ)
    assert clock_fit.stats["epochs"] == 2 and not list(tmp_path.glob("*.partial"))


def test_write_span_refuses_with_a_sentence_for_each_silent_case(tmp_path):
    live, clock = _live(seconds=2.0)
    source = FakeSource(1)
    wrapped = live.wrap(lambda pid: source)(1)
    out = tmp_path / "x.cap"
    assert live.write_span(5, 9, out)["reason"] == la.NOT_STARTED
    wrapped.start(lambda pcm: None)
    origin = clock.now
    _play(clock, source, 500)                          # 5 s through a 2 s ring
    early = (origin, origin + FREQ * 4 // 10)          # its margins end before the ring's oldest chunk
    assert live.write_span(*early, out)["reason"] == \
        "This attempt's sound has already left the live sound buffer, which keeps the last 2 seconds."
    later = clock.now + 10 * FREQ
    assert live.write_span(later, later + FREQ, out)["reason"] == "This attempt's sound has not reached the Trainer yet."
    wrapped.stop()
    assert live.write_span(later, later + FREQ, out)["reason"] == \
        "The Trainer was not recording Project64's sound during this attempt."
    assert live.write_span(9, 5, out)["written"] is False and not out.exists()


# ---------------------------------------------------------------- a cut scene with its sound

def _device_stream(presents: list, *, before_s=3.0, after_s=3.0, seed=5):
    """(chunks of (arrival, pcm), the true device sample of each present): a tap opened
    `before_s` ahead of the first picture, its clicks on every CLICK_EVERY-th picture."""
    rng = random.Random(seed)
    start = presents[0].qpc - round(before_s * FREQ)
    period = FREQ / (RATE * (1 + fx.PPM / 1e6))
    total = int((presents[-1].qpc - start + after_s * FREQ) / period) // CHUNK * CHUNK
    truth = [int((present.qpc - start) // period) for present in presents]
    t = np.arange(total) / RATE
    pcm = np.stack([np.sin(2 * np.pi * 330 * t), np.sin(2 * np.pi * 495 * t)], axis=1) * 1500
    for frame in range(0, len(presents), fx.CLICK_EVERY):
        pcm[truth[frame]:truth[frame] + 48] = 28000
    pcm = pcm.astype(np.int16)
    chunks, arrival = [], 0
    for first in range(0, total, CHUNK):
        ready = start + (first + CHUNK) * period
        arrival = max(arrival, int(ready + 3_000 + rng.expovariate(1 / 20_000)))
        chunks.append((arrival, np.frombuffer(pcm[first:first + CHUNK].tobytes(), np.int16).reshape(-1, 2)))
    return chunks, truth


@pytest.fixture(scope="module")
def cut(tmp_path_factory):
    """A closures-only package cut from a synthetic tap session, the live ring's capture of its
    span, and the truth: what the lab's cut and attach steps would leave in its folder."""
    base = tmp_path_factory.mktemp("live-sound")
    rom, segment7 = fx.synthetic._rom()
    session, presents = fx.tap_session(base / "tap-live", rom, segment7)
    packages = base / "packages"
    packages.mkdir()
    out = packages / "attempt-12.sm64scene"
    pk.write_package(pk.load_source(session), rom, out, pk.ZLIB, group=4)
    plain = out.read_bytes()
    clock = Clock(0)
    live = la.LiveAudio(seconds=60.0, frequency=FREQ, stamp=clock)
    source = FakeSource(4242)
    recorder = []
    live.wrap(lambda pid: source)(4242).start(recorder.append)
    chunks, truth = _device_stream(presents)
    for arrival, pcm in chunks:
        clock.now = arrival
        source.on_pcm(pcm)
    capture = base / "attempt-12.cap"
    written = live.write_span(presents[0].qpc, presents[-1].qpc, capture)
    return {"rom": rom, "presents": presents, "package": out, "plain": plain, "capture": capture,
            "written": written, "truth": truth, "chunks": chunks, "base": base, "recorder": recorder}


def test_the_span_keeps_its_margins_and_the_recorder_got_every_chunk(cut):
    written = cut["written"]
    assert written["written"] and written["covers_start"] and written["covers_end"]
    assert written["lost_samples"] == 0 and written["epochs"] == 1
    assert 40 / 30 + 2 * la.MARGIN_S - 0.1 <= written["seconds"] <= 40 / 30 + 2 * la.MARGIN_S + la.LATE_S + 0.1
    assert len(cut["recorder"]) == len(cut["chunks"])
    assert all(got is pcm for got, (_arrival, pcm) in zip(cut["recorder"], cut["chunks"], strict=True))


@pytest.fixture
def lab(tmp_path, cut, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    packages = cut["package"].parent
    assert la.attach_span(cut["package"], cut["capture"], [(p.sequence, p.qpc) for p in cut["presents"]],
                          FREQ, cut["rom"]) is None
    (tmp_path / "rom.z64").write_bytes(cut["rom"])
    (tmp_path / "Plugin").mkdir()
    config = tmp_path / "lab.json"
    config.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(packages),
                                  "pj64_plugin": str(tmp_path / "Plugin"), "map": str(tmp_path / "Map.map")}),
                      encoding="utf-8")
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, config, session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command())}, watch_seconds=0.05)
    return app


def _decode(file: bytes) -> np.ndarray:
    with av.open(io.BytesIO(file)) as box:
        return np.concatenate([frame.to_ndarray().reshape(-1, 2) for frame in box.decode(audio=0)])


def test_a_cut_scene_plays_the_attempts_own_sound_and_each_click_lands_on_its_picture(lab, cut):
    with TestClient(lab) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "attempt-12.sm64scene"}).json()
        audio = opened["audio"]
        assert audio["available"] and audio["rate"] == RATE and audio["lost"] == []
        assert len(audio["samples"]) == len(opened["frames"]) == fx.FRAMES
        stream_first = asn.read_capture(cut["capture"])[2][0].first   # the stored stream starts at the span's first chunk
        truth = np.array(cut["truth"]) - stream_first
        errors = np.array(audio["samples"]) - truth
        assert np.abs(errors).max() <= RATE // 1000                  # every frame within 1 ms of its true sample
        step = audio["block_samples"]
        stream = np.concatenate([
            _decode(client.get(f"/api/scene-lab/audio/{block}", params={"session": opened["session"]}).content)
            for block in range(audio["blocks"])])
        for frame in range(0, fx.FRAMES, fx.CLICK_EVERY):
            at = audio["samples"][frame]
            window = stream[max(0, at - 240):at + 240, 0]
            onset = int(np.argmax(window >= 28000)) + max(0, at - 240)
            assert abs(onset - at) <= RATE // 1000, f"frame {frame}: click {onset - at} samples off its picture"
        assert len(stream) >= step and stream.shape[1] == 2
        client.post("/api/scene-lab/close")


def test_attach_span_refuses_with_a_sentence_and_leaves_the_package_alone(cut, tmp_path):
    package = tmp_path / "scene.sm64scene"
    package.write_bytes(cut["plain"])
    presents = [(p.sequence, p.qpc) for p in cut["presents"]]
    rom = cut["rom"]
    assert la.attach_span(package, cut["capture"], presents, FREQ + 1, rom) == \
        "This attempt's sound was timed on a different clock than its pictures."
    assert la.attach_span(package, tmp_path / "missing.cap", presents, FREQ, rom).startswith(
        "This attempt's sound could not be read")
    foreign = [(sequence + 1000, qpc) for sequence, qpc in presents]
    assert "belongs to another recording" in la.attach_span(package, cut["capture"], foreign, FREQ, rom)
    long_after = [(sequence, qpc + 600 * FREQ) for sequence, qpc in presents]
    assert la.attach_span(package, cut["capture"], long_after, FREQ, rom) == \
        "No game sound was recorded while this attempt's pictures were on screen."
    assert package.read_bytes() == cut["plain"] and not list(tmp_path.glob("*.partial"))
    assert la.attach_span(package, cut["capture"], presents, FREQ, rom) is None
    assert pk.Package(package.read_bytes(), rom).audio is not None
