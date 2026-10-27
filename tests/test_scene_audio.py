"""Source audio for scene packages (tools/scene_lab/audio_clock.py, audio_layer.py).

Synthetic streams only: a device clock running a known number of ppm off a
10 MHz QPC, chunk arrivals delayed by one-sided jitter, and the events the
mapping must survive (a hole, a delivery stall, lost chunks, a reopened tap).
The truth each test checks against is the simulated device clock itself, never
the module's own fit. No recorded audio is involved.
"""
import hashlib
import random
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/scene_lab"))
# Plain imports, never a fresh copy in sys.modules: a second audio_clock would make a second
# Picture class, and a package another test file already imported would then hold pictures
# that compare unequal to this run's (seen 2026-10-27 when the stems tests joined the run).
import audio_clock as ac  # noqa: E402
import audio_layer as al  # noqa: E402
import audio_session as asn  # noqa: E402

RATE, FREQ = 48000, 10_000_000
START = 7_000_000_000          # QPC ticks since boot when the tap opens (arbitrary)
CHUNK = 480                    # 10 ms, the WASAPI engine period


class Device:
    """A tap's sample clock: sample n of epoch e starts at `time(n)` true ticks."""

    def __init__(self, ppm: float, start: int = START, epoch: int = 0):
        self.period = FREQ / (RATE * (1 + ppm / 1e6))   # slow device (negative ppm): longer samples
        self.start, self.epoch = start, epoch
        self.holes: list[tuple[int, int]] = []          # (sample, ticks the device skipped before it)
        self.bends: list[tuple[int, float]] = []         # (sample, ppm from then on)

    def time(self, n: int) -> float:
        t, base, period = self.start, 0, self.period
        for at, ppm in self.bends:
            if n <= at:
                break
            t += (at - base) * period
            base, period = at, FREQ / (RATE * (1 + ppm / 1e6))
        t += (n - base) * period
        return t + sum(skip for at, skip in self.holes if n >= at)

    def sample_at(self, ticks: int) -> int:
        lo, hi = 0, 1 << 40
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.time(mid) <= ticks:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def chunks(self, seconds: float, *, seed: int = 1, stall: tuple[float, float] | None = None,
               lost: set[int] = frozenset(), first: int = 0) -> list:
        rng = random.Random(seed)
        out, arrival, n = [], 0, first
        total = int(seconds * RATE)
        while n < first + total:
            end = n + CHUNK
            ready = self.time(end - 1) + self.period                 # its last sample has ended
            delay = 3_000 + int(rng.expovariate(1 / 20_000))   # 0.3 ms floor, 2 ms mean jitter
            at = ready + delay
            if stall is not None:
                s0, s1 = START + stall[0] * FREQ, START + (stall[0] + stall[1]) * FREQ
                if s0 <= ready < s1:
                    at = s1 + rng.randint(0, 2_000)            # the backlog arrives together
            arrival = max(arrival, int(at))
            out.append(ac.Chunk(self.epoch, n, CHUNK, arrival, len(out) in lost))
            n = end
        return out


def _presents(begin: float, seconds: float, *, fps: float = 30.0, seq0: int = 10, seed: int = 2) -> list:
    rng = random.Random(seed)
    count = int(seconds * fps)
    return [ac.Present(seq0 + 3 * i, START + int((begin + i / fps) * FREQ) + rng.randint(-2_000, 2_000))
            for i in range(count)]


def _errors(clock, device: Device, epoch_first: int = 0):
    """Covered pictures' sample error against the device's true sample, in samples."""
    errors = []
    for picture in clock.pictures:
        if picture.status != ac.COVERED:
            continue
        segment = next(s for s in reversed(clock.segments) if s.stream_first <= picture.sample)
        mapped = segment.first + picture.sample - segment.stream_first
        errors.append(mapped - device.sample_at(clock.origin_qpc + picture.ticks))
    return np.array(errors)


def test_drift_is_fitted_and_every_picture_maps_to_its_true_sample():
    device = Device(ppm=-120.0)
    chunks = device.chunks(120)
    presents = _presents(0.5, 119)
    clock = ac.build_clock(chunks, presents, rate=RATE, frequency=FREQ)
    assert len(clock.segments) == 1 and clock.segments[0].flags & ac.MEASURED
    assert abs(clock.stats["drift_ppm"] - -120.0) < 2.0
    assert clock.stats["pictures"]["covered"] == len(presents)
    errors = _errors(clock, device)
    assert np.abs(errors).max() <= 48, errors          # within 1 ms everywhere
    # The discriminator: even a perfectly anchored fixed offset at the nominal rate
    # (no drift fit) is off by 120 ppm x 2 min ~ 14 ms (~690 samples) at the end.
    last = presents[-1].qpc
    nominal = (last - START) * RATE // FREQ
    assert abs(nominal - device.sample_at(last)) > 500


def test_a_hole_in_the_device_stream_becomes_a_gap_and_the_rest_stays_exact():
    device = Device(ppm=80.0)
    device.holes.append((40 * RATE // CHUNK * CHUNK, 500_000))     # 50 ms with no samples at ~40 s
    clock = ac.build_clock(device.chunks(90), _presents(0.5, 89), rate=RATE, frequency=FREQ)
    assert clock.stats["holes"] == 1 and clock.stats["overruns"] == 0
    assert abs(clock.stats["steps_ms"][0] - 50.0) < 1.5
    assert len(clock.segments) == 2 and not clock.segments[1].flags & ac.JOINED
    gap = [p for p in clock.pictures if p.status == ac.GAP]
    assert 1 <= len(gap) <= 2                                       # 50 ms at 30 fps
    assert np.abs(_errors(clock, device)).max() <= 48


def test_a_delivery_stall_whose_backlog_arrives_moves_nothing():
    device = Device(ppm=-60.0)
    clock = ac.build_clock(device.chunks(60, stall=(25.0, 0.4)), _presents(0.5, 59), rate=RATE, frequency=FREQ)
    assert clock.stats["holes"] == 0 and len(clock.segments) == 1
    assert clock.stats["segments"][0]["jitter_us"]["max"] > 300_000  # the stall is visible as jitter only
    assert np.abs(_errors(clock, device)).max() <= 48


def test_lost_chunks_are_marked_with_their_time_and_never_hidden():
    device = Device(ppm=0.0)
    lost = set(range(1000, 1010))                                   # 100 ms the fanout could not keep
    chunks = device.chunks(30, lost=lost)
    clock = ac.build_clock(chunks, _presents(0.5, 29), rate=RATE, frequency=FREQ)
    assert clock.losses == [(1000 * CHUNK, 10 * CHUNK)]
    assert clock.stats["holes"] == 0 and clock.stats["lost_samples"] == 10 * CHUNK
    marked = [p for p in clock.pictures if p.status == ac.LOSS]
    assert 2 <= len(marked) <= 4 and all(1000 * CHUNK <= p.sample < 1010 * CHUNK for p in marked)
    payloads = [None if c.lost else bytes([i % 251]) * (CHUNK * 4) for i, c in enumerate(chunks)]
    pcm = ac.stream_pcm(chunks, payloads)
    assert not pcm[1000 * CHUNK:1010 * CHUNK].any() and pcm[999 * CHUNK].any()
    with pytest.raises(ac.ClockError):
        ac.stream_pcm(chunks, [b"\0" * (CHUNK * 4)] * len(chunks))   # a lost chunk cannot carry bytes


def test_a_reopened_tap_starts_a_new_epoch_with_a_gap_between():
    first = Device(ppm=-100.0)
    second = Device(ppm=-100.0, start=START + 32 * FREQ, epoch=1)   # reopened 2 s after the first stopped
    chunks = first.chunks(30) + second.chunks(30)
    clock = ac.build_clock(chunks, _presents(0.5, 61), rate=RATE, frequency=FREQ)
    assert clock.stats["epochs"] == 2 and [s.epoch for s in clock.segments] == [0, 1]
    assert clock.segments[1].first == 0 and clock.segments[1].stream_first == clock.segments[0].samples
    assert 55 <= clock.stats["pictures"]["gap"] <= 62                # ~2 s at 30 fps
    for picture in clock.pictures:
        if picture.status == ac.COVERED:
            device = first if picture.sample < clock.segments[1].stream_first else second
            segment = clock.segments[picture.sample >= clock.segments[1].stream_first]
            mapped = segment.first + picture.sample - segment.stream_first
            assert abs(mapped - device.sample_at(clock.origin_qpc + picture.ticks)) <= 48


def test_a_changing_device_rate_fits_piecewise():
    device = Device(ppm=-100.0)
    device.bends.append((100 * RATE, 60.0))                          # the crystal warms up at 100 s
    clock = ac.build_clock(device.chunks(200), _presents(0.5, 199), rate=RATE, frequency=FREQ)
    assert len(clock.segments) >= 2
    assert all(s.flags & ac.JOINED for s in clock.segments[1:])
    assert clock.stats["pictures"]["gap"] == 0
    assert np.abs(_errors(clock, device)).max() <= 48


def test_pictures_before_and_after_the_audio_are_outside_it():
    device = Device(ppm=0.0, start=START + 5 * FREQ)
    clock = ac.build_clock(device.chunks(10), _presents(0.0, 20), rate=RATE, frequency=FREQ)
    counts = clock.stats["pictures"]
    assert counts["before"] == pytest.approx(150, abs=2) and counts["after"] == pytest.approx(150, abs=2)
    assert all(p.sample == -1 for p in clock.pictures if p.status in (ac.BEFORE, ac.AFTER))


def test_one_picture_hold_plays_the_audio_heard_during_it():
    device = Device(ppm=0.0)
    clock = ac.build_clock(device.chunks(5), _presents(0.5, 4, seed=3), rate=RATE, frequency=FREQ)
    spans = ac.picture_spans(clock)
    for (first, count), now, nxt in zip(spans, clock.pictures, clock.pictures[1:], strict=False):
        assert first == now.sample and first + count == nxt.sample
        assert abs(count - (nxt.ticks - now.ticks) * RATE / FREQ) <= 2


@pytest.mark.parametrize("mutate, message", [
    (lambda c, p: c.__setitem__(5, ac.Chunk(0, c[5].first + 1, CHUNK, c[5].arrival)), "contiguous"),
    (lambda c, p: c.__setitem__(5, ac.Chunk(0, c[5].first, CHUNK, c[4].arrival - 1)), "arrival moved backward"),
    (lambda c, p: c.__setitem__(5, ac.Chunk(0, c[5].first, 0, c[5].arrival)), "out of range"),
    (lambda c, p: c.append(ac.Chunk(-1, 0, CHUNK, c[-1].arrival)), "epoch moved backward"),
    (lambda c, p: c.__setitem__(5, ac.Chunk(0, c[5].first, CHUNK, float(c[5].arrival))), "integer"),
    (lambda c, p: p.__setitem__(3, ac.Present(p[2].sequence, p[3].qpc)), "out of order"),
    (lambda c, p: p.__setitem__(3, ac.Present(p[3].sequence, p[2].qpc - 1)), "out of order"),
])
def test_malformed_stamps_are_refused(mutate, message):
    chunks, presents = Device(ppm=0.0).chunks(2), _presents(0.5, 1)
    mutate(chunks, presents)
    with pytest.raises(ac.ClockError, match=message):
        ac.build_clock(chunks, presents, rate=RATE, frequency=FREQ)


# -- the layer payload -------------------------------------------------------------------------------


def _recording(seconds=12.0, lost=frozenset({300, 301})):
    device = Device(ppm=-90.0)
    chunks = device.chunks(seconds, lost=lost)
    clock = ac.build_clock(chunks, _presents(0.5, seconds - 1), rate=RATE, frequency=FREQ)
    rng = np.random.default_rng(5)
    t = np.arange(clock.total_samples) / RATE
    tone = np.stack([np.sin(2 * np.pi * 523 * t), np.sin(2 * np.pi * 784 * t)], axis=1) * 6000
    pcm = (tone + rng.normal(0, 40, tone.shape)).astype(np.int16)
    pcm[3 * RATE:5 * RATE] = 0                                     # a silent stretch
    payloads = [None if c.lost else pcm[c.first:c.first + c.samples].tobytes() for c in chunks]
    return clock, ac.stream_pcm(chunks, payloads)


@pytest.mark.parametrize("codec", ["flac", "stored"])
def test_layer_round_trip_is_exact_and_seekable(codec):
    clock, pcm = _recording()
    payload = al.encode_layer(clock, pcm, codec=codec, block_frames=4)
    layer = al.read_layer(payload)
    layer.verify()
    assert layer.pictures == clock.pictures and layer.segments == clock.segments
    assert layer.losses == clock.losses
    first, count = layer.block_samples - 100, 2 * layer.block_samples     # spans three blocks
    assert np.array_equal(layer.pcm(first, count), pcm[first:first + count])
    picture = next(p for p in layer.pictures if p.status == ac.COVERED and p.sample > 5 * RATE)
    assert np.array_equal(layer.pcm(picture.sample, 10), pcm[picture.sample:picture.sample + 10])
    assert struct.pack("<q", clock.origin_qpc) not in payload        # no absolute host clock value
    if codec == "flac":
        assert len(payload) < pcm.nbytes * 0.8


def test_a_flac_block_is_a_complete_flac_file():
    import io

    import av

    clock, pcm = _recording(seconds=4.0, lost=frozenset())
    layer = al.read_layer(al.encode_layer(clock, pcm, block_frames=8))
    offset, stored, samples, _ = layer.blocks[0]
    with av.open(io.BytesIO(al.flac_file(layer.payload[offset:offset + stored], RATE)), format="flac") as box:
        decoded = np.concatenate([f.to_ndarray().reshape(-1, 2) for f in box.decode(audio=0)])
    assert np.array_equal(decoded, pcm[:samples])


def _rehash(payload: bytes) -> bytes:
    """Recompute the index hash so a test reaches the check behind it."""
    header = list(al.HEADER.unpack_from(payload))
    index = payload[al.HEADER.size:al.HEADER.size + header[14]]
    header[16] = hashlib.sha256(index).digest()
    return al.HEADER.pack(*header) + payload[al.HEADER.size:]


def _poke(payload: bytes, at: int, fmt: str, value) -> bytes:
    data = bytearray(payload)
    struct.pack_into(fmt, data, at, value)
    return bytes(data)


def _picture_at(layer, i: int) -> int:
    return (al.HEADER.size + len(layer.blocks) * al.BLOCK.size + len(layer.segments) * al.SEGMENT.size
            + len(layer.losses) * al.LOSS.size + i * al.PICTURE.size)


def test_malformed_layers_are_refused():
    clock, pcm = _recording()
    payload = al.encode_layer(clock, pcm, codec="flac", block_frames=4)
    layer = al.read_layer(payload)
    covered = next(i for i, p in enumerate(layer.pictures) if p.status == ac.COVERED)
    cases = {
        "not an audio layer": b"X" + payload[1:],
        "unknown audio layer version": _poke(payload, 8, "<H", 2),
        "unknown audio codec": _poke(payload, 10, "<H", 7),
        "truncated": payload[:100],
        "index fails its hash": _poke(payload, al.HEADER.size + 8, "<I", 1),
        "length differs": payload + b"\0",
        "block size out of range": _poke(payload, 36, "<I", 4000),
        "longer than allowed": _poke(payload, 28, "<Q", RATE * al.MAX_SECONDS + 1),
        # The index hash matches, but a picture claims a sample the clock does not give it.
        "does not follow from the stored clock": _rehash(_poke(payload, _picture_at(layer, covered) + 16, "<q",
                                                               layer.pictures[covered].sample + 1)),
        "rate": _rehash(_poke(payload, al.HEADER.size + len(layer.blocks) * al.BLOCK.size + 40, "<Q",
                              layer.segments[0].period_q32 * 2)),
    }
    for message, bad in cases.items():
        with pytest.raises(al.LayerError, match=message):
            al.read_layer(bad)
    # A flipped audio byte parses (the index is intact) and is refused when that block decodes.
    offset = layer.blocks[1][0]
    corrupt = al.read_layer(payload[:offset + 40] + bytes([payload[offset + 40] ^ 0xFF]) + payload[offset + 41:])
    corrupt.block(0)
    with pytest.raises(al.LayerError):
        corrupt.block(1)
    with pytest.raises(al.LayerError, match="out of bounds"):
        layer.pcm(layer.total_samples - 5, 10)


def _tap_session(folder: Path, presents: list) -> Path:
    """A scene-tap session holding only present records (tap.py's wire format)."""
    import tap

    folder.mkdir()
    records = []
    for present in presents:
        body = struct.pack("<14I", *([0] * 14)) + struct.pack("<4I", 0, 0, 0, 1)
        size = (32 + len(body) + 31) & ~31
        records.append(struct.pack("<IIIIq8x", tap.MAGIC, tap.REC_PRESENT, size, present.sequence, present.qpc)
                       + body + bytes(size - 32 - len(body)))
    (folder / "tap.bin").write_bytes(b"".join(records))
    (folder / "session.txt").write_text(f"schema=scene-lab-tap/1\nqpc_frequency={FREQ}\n", encoding="ascii")
    return folder


def test_a_capture_file_and_a_tap_session_build_the_layer(tmp_path):
    device = Device(ppm=110.0)
    chunks = device.chunks(20, lost=frozenset({700}))
    rng = np.random.default_rng(9)
    payloads = [None if c.lost else rng.integers(-3000, 3000, (CHUNK, 2), dtype=np.int16).tobytes() for c in chunks]
    capture = tmp_path / "audio.cap"
    asn.write_capture(capture, RATE, FREQ, zip(chunks, payloads, strict=True))
    presents = _presents(0.5, 19)
    session = _tap_session(tmp_path / "tap-synthetic", presents)
    clock, pcm = asn.build(session, capture)
    assert [p.sequence for p in clock.pictures] == [p.sequence for p in presents]
    assert clock.losses == [(700 * CHUNK, CHUNK)] and clock.stats["pictures"]["covered"] >= len(presents) - 1
    assert np.array_equal(pcm[:CHUNK], np.frombuffer(payloads[0], np.int16).reshape(-1, 2))
    layer = al.read_layer(al.encode_layer(clock, pcm))
    layer.verify()
    assert layer.pictures == clock.pictures
    other = asn.FILE_HEADER.pack(asn.MAGIC, asn.VERSION, 2, RATE, 3_579_545)   # another clock's ticks
    (tmp_path / "other.cap").write_bytes(other + capture.read_bytes()[asn.FILE_HEADER.size:])
    with pytest.raises(asn.CaptureError, match="differs from the session"):
        asn.build(session, tmp_path / "other.cap")


def test_malformed_capture_files_are_refused(tmp_path):
    chunks = Device(ppm=0.0).chunks(1)
    capture = tmp_path / "audio.cap"
    asn.write_capture(capture, RATE, FREQ, [(c, bytes(CHUNK * 4)) for c in chunks])
    data = capture.read_bytes()
    cases = {
        "not a version-1": b"X" + data[1:],
        "PCM truncated": data[:-10],
        "record truncated": data + b"\1\0",
        "unknown capture record": data[:asn.FILE_HEADER.size] + struct.pack("<H", 9) + data[asn.FILE_HEADER.size + 2:],
    }
    for message, bad in cases.items():
        (tmp_path / "bad.cap").write_bytes(bad)
        with pytest.raises(asn.CaptureError, match=message):
            asn.read_capture(tmp_path / "bad.cap")


def test_the_writer_refuses_a_stream_that_does_not_match_its_clock():
    clock, pcm = _recording(seconds=3.0, lost=frozenset())
    with pytest.raises(al.LayerError, match="int16"):
        al.encode_layer(clock, pcm[:-1])
    with pytest.raises(al.LayerError, match="unknown audio codec"):
        al.encode_layer(clock, pcm, codec="mp3")
