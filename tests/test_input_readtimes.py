"""Exact read history remains bounded while pending and closes its spill files."""
import random
import struct
import zlib

import pytest

from sm64_events.inputs.observation import InputObservation, decode_observations, encode_observations
from sm64_events.inputs.readtimes import ReadTimes, contains_time, micros, stamp_at
from sm64_events.inputs.sampler import InputSampler
from sm64_events.memory.layout import US
from test_inputs_sampler import ScriptedMemory


AT = "2026-08-20T21:00:00Z"


def decoded(blob):
    """Reference decoder: the whole zlib stream as little-endian int64 instants."""
    return [value for value, in struct.iter_unpack("<q", zlib.decompress(blob))]


def test_long_hold_spills_preserves_every_instant_and_closes_its_file(monkeypatch):
    # Incompressible low bits exercise spill instead of only zlib's tiny
    # representation of a repeated time. The threshold is a test config.
    monkeypatch.setattr(ReadTimes, "RAM_BYTES", 1024)
    rng = random.Random(52)
    expected = [micros(AT) + rng.randrange(3_600_000_000) for _ in range(20_000)]
    history = ReadTimes()
    for instant in expected:
        history.add(stamp_at(instant))
    file = history._file
    assert file._rolled  # actual tempfile switched from RAM to a disk file
    blob, lower, upper = history.finish()
    assert file.closed
    assert decoded(blob) == expected
    assert (micros(lower), micros(upper)) == (min(expected), max(expected))
    # Variable history is separately framed from the bounded identity JSON.
    observation = InputObservation("poll:long", 0, stamp_at(expected[-1]),
                                   stamp_at(expected[0]), lower, upper, blob)
    loaded, = decode_observations(encode_observations([observation], 1), 1)
    assert loaded == observation
    assert loaded.within(stamp_at(expected[500]), stamp_at(expected[500]))


def test_many_identical_instants_stream_across_decompression_blocks():
    # Expansion exceeds both compressed input and the fixed output block; only
    # the final instant differs, so a match proves the last block was read.
    blob = zlib.compress(struct.pack("<q", micros(AT)) * 49_999
                         + struct.pack("<q", micros(AT) + 2))
    assert contains_time(blob, micros(AT) + 2, micros(AT) + 2)
    assert not contains_time(blob, micros(AT) + 1, micros(AT) + 1)


@pytest.mark.parametrize("bad", [b"garbage", zlib.compress(b"x"),
                                 zlib.compress(b"12345678")[:-1],
                                 zlib.compress(b"12345678") + b"trailing"])
def test_malformed_histories_do_not_supply_membership(bad):
    with pytest.raises(ValueError, match="observation"):
        contains_time(bad, 0, 2**62)


def test_a_rewrite_closes_and_discards_its_stale_history():
    times = iter(["2026-08-20T21:00:00Z", "2026-08-20T21:00:01Z"])
    got = []
    sampler = InputSampler(ScriptedMemory([(100, 0x8000, 0, 80, 0),
                                           (100, 0x4000, 0, -80, 0)]), US,
                           lambda _number, _frame, **metadata: got.append(metadata["observation"]),
                           clock=lambda: next(times))
    sampler.sample()
    stale = sampler._read_times._file
    sampler.sample()
    assert stale.closed
    final = sampler._read_times._file
    sampler.flush()
    assert final.closed
    observation, = got
    assert not observation.within(AT, AT)
    assert observation.within("2026-08-20T21:00:01Z", "2026-08-20T21:00:01Z")
