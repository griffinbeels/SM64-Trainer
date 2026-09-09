"""Deferring compression preserves the exact existing history representation."""
import random
import struct
import zlib

import pytest

from sm64_events.inputs.readtimes import ReadTimes, iter_times, micros, stamp_at
from sm64_events.inputs.sampler import InputSampler
from sm64_events.memory.layout import US
from test_inputs_sampler import ScriptedMemory


AT = "2026-09-08T12:34:56Z"


@pytest.mark.parametrize("budget,count", [(1024, 1), (1024, 8), (1024, 127),
    (1024, 128), (1024, 129), (65536, 8191), (65536, 8192), (65536, 8193),
    (1024, 20000)])
def test_buffer_and_spill_keep_the_original_streamed_bytes(monkeypatch, budget, count):
    monkeypatch.setattr(ReadTimes, "RAM_BYTES", budget)
    rng = random.Random(684)
    values = [micros(AT) + rng.randrange(-1_000_000, 3_600_000_000) for _ in range(count)]
    # The former writer fed one packed value per compress call. This witness
    # pins its actual byte representation independently of the new buffer.
    encoder = zlib.compressobj()
    expected = b"".join(encoder.compress(struct.pack("<q", value)) for value in values)
    expected += encoder.flush()
    history = ReadTimes()
    for value in values:
        history.add(stamp_at(value))
        assert len(history._pending) < budget
    blob, lower, upper = history.finish()
    assert blob == expected
    assert list(iter_times(blob)) == values
    assert (micros(lower), micros(upper)) == (min(values), max(values))
    assert history._file.closed


def test_discarded_short_state_does_no_compression_and_closes_its_spool(monkeypatch):
    def unexpected(*args):
        pytest.fail("discarded pending reads must not allocate or feed zlib")

    monkeypatch.setattr(zlib, "compressobj", unexpected)
    monkeypatch.setattr(zlib, "compress", unexpected)
    history = ReadTimes()
    for offset in range(8):
        history.add(stamp_at(micros(AT) + offset * 4000))
    history.close()
    assert history._file.closed
    assert not history._pending
    with pytest.raises(ValueError, match="closed"):
        history.add(AT)
    with pytest.raises(ValueError, match="closed"):
        history.finish()


@pytest.mark.parametrize("failure", ["seal", "stream_create", "stream_write",
                                     "stream_flush", "stream_read"])
def test_real_compression_failure_loses_only_its_state_and_closes(monkeypatch, failure):
    made, observations = [], []
    factory = ReadTimes
    original_compress, original_object = zlib.compress, zlib.compressobj

    def unavailable(*args):
        raise OSError("compressed history storage unavailable")

    def history_factory():
        history = factory()
        if not made:
            if failure != "seal":
                history.RAM_BYTES = 8  # force actual streaming on the first read
            if failure == "stream_write":
                monkeypatch.setattr(history._file, "write", unavailable)
            if failure == "stream_read":
                monkeypatch.setattr(history._file, "read", unavailable)
        made.append(history)
        return history

    def fail_first_seal(*args):
        if len(made) == 1:
            unavailable()
        return original_compress(*args)

    class FlushFailure:
        def __init__(self):
            self.inner = original_object()

        def compress(self, data):
            return self.inner.compress(data)

        def flush(self):
            unavailable()

    if failure == "seal":
        monkeypatch.setattr(zlib, "compress", fail_first_seal)
    elif failure == "stream_create":
        monkeypatch.setattr(zlib, "compressobj", unavailable)
    elif failure == "stream_flush":
        monkeypatch.setattr(zlib, "compressobj", FlushFailure)
    monkeypatch.setattr("sm64_events.inputs.sampler.ReadTimes", history_factory)
    sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                           (101, 0x4000, 0, -80, 0)]), US,
        lambda number, frame, **meta: observations.append((number, meta["observation"])),
        clock=lambda: AT)
    assert sampler.sample() == 100
    assert sampler.sample() == 101
    sampler.flush()
    assert [number for number, _ in observations] == [101]
    assert list(iter_times(observations[0][1].history)) == [micros(AT)]
    assert all(history._file.closed for history in made)
    assert sampler.health()["history_failures"] == 1
