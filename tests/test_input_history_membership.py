"""Long paused frames keep exact read membership and reject damaged evidence."""
import random
import struct
import zlib

import pytest

from sm64_events.inputs.observation import InputObservation
from sm64_events.inputs.readtimes import contains_time, iter_times, micros, stamp_at


AT = "2026-09-14T00:00:00Z"


def packed(values):
    return zlib.compress(b"".join(struct.pack("<q", value) for value in values))


@pytest.mark.parametrize("size", [1, 8, 32, 33, 128, 129, 8191, 8192, 8193, 20000])
def test_membership_preserves_exact_instants_gaps_and_clock_reversals(size):
    rng = random.Random(size)
    base = micros(AT)
    values = [base + 2 * rng.randrange(-100000, 100000) for _ in range(size)]
    blob = packed(values)
    original = bytes(blob)
    for low, high in [(values[0], values[0]), (values[-1], values[-1]),
                      (base + 1, base + 1), (base - 2, base + 2),
                      (base - 10**9, base + 10**9)]:
        assert contains_time(blob, low, high) == any(low <= v <= high for v in values)
    assert list(iter_times(blob)) == values
    assert blob == original


@pytest.mark.parametrize("damage", ["truncated", "trailing", "partial_integer", "checksum", "second_stream"])
def test_a_match_in_the_first_block_does_not_hide_a_malformed_tail(damage, monkeypatch):
    import sm64_events.inputs.readtimes as module

    base = micros(AT)
    raw = struct.pack("<q", base) * 20000
    blob = zlib.compress(raw + (b"x" if damage == "partial_integer" else b""))
    if damage == "truncated":
        blob = blob[:-1]
    elif damage == "trailing":
        blob += b"trailing"
    elif damage == "checksum":
        blob = blob[:-1] + bytes([blob[-1] ^ 1])
    elif damage == "second_stream":
        blob += zlib.compress(raw)
    factory = module.zlib.decompressobj

    class BoundedReader:
        def __init__(self):
            self.inner = factory()
            self.calls = 0

        def decompress(self, *args):
            self.calls += 1
            assert self.calls <= 12, "decoder repeatedly fed an unchanged trailing tail"
            return self.inner.decompress(*args)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    # The old EOF placement spins forever on a long expansion with a tail.
    # Bound actual decoder calls so restoring that bug fails without hanging
    # the runner; this does not replace the zlib parser or its error behavior.
    monkeypatch.setattr(module.zlib, "decompressobj", BoundedReader)
    observation = InputObservation("poll:hold", 0, AT, history=blob)
    with pytest.raises(ValueError, match="observation"):
        observation.within(AT, AT)


def test_empty_history_and_signed_integer_boundaries():
    assert not contains_time(zlib.compress(b""), -(2**63), 2**63 - 1)
    values = [-(2**63), -1, 0, 1, 2**63 - 1] * 2000
    blob = packed(values)
    for value in values[:5]:
        assert contains_time(blob, value, value)
    assert not contains_time(blob, -(2**63) + 1, -2)
    assert not contains_time(blob, 2, 2**63 - 2)


def test_large_membership_uses_bounded_array_views(monkeypatch):
    import sm64_events.inputs.readtimes as module

    base = micros(AT)
    blob = packed(range(base, base + 100000, 2))
    original = module.np.frombuffer
    sizes = []

    def bounded(buffer, **kwargs):
        sizes.append(len(buffer))
        assert len(buffer) <= 65536
        return original(buffer, **kwargs)

    monkeypatch.setattr(module.np, "frombuffer", bounded)
    observation = InputObservation("poll:hold", 0, AT, history=blob)
    assert not observation.within(stamp_at(base + 1), stamp_at(base + 1))
    assert len(sizes) > 1
