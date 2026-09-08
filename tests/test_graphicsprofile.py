"""The optional instrument refuses absent/torn/stale data and labels its bounds."""
import struct
import threading

from sm64_events.replay import graphicsprofile as G


def snapshot_bytes():
    raw = bytearray(G.SIZE)
    raw[:8] = G.MAGIC
    struct.pack_into("<4Iq", raw, 8, G.VERSION, 42, 2, 123, 1_000_000)
    # Three independently specified samples, 0us, 2us and 9us.
    buckets = [0] * 32
    buckets[0], buckets[2], buckets[4] = 1, 1, 1
    struct.pack_into("<35Q", raw, 64, 3, 11, 9, *buckets)
    return raw


def test_histogram_exports_bounds_and_empty_stages_are_unknown():
    result = G.decode_snapshot(snapshot_bytes(), 42, 123)
    metric = result["metrics"]["update_screen"]
    assert metric["count"] == 3
    assert metric["total_ms"] == .011 and metric["max_ms"] == .009
    assert metric["p50_upper_ms"] == .004 and metric["p99_upper_ms"] == .016
    empty = result["metrics"]["gl_read_pixels"]
    assert empty["count"] == 0 and empty["mean_ms"] is None
    assert empty["p99_upper_ms"] is None


def test_absent_torn_old_and_inconsistent_snapshots_are_unavailable():
    raw = snapshot_bytes()
    assert G.decode_snapshot(raw, 41, 123) is None
    assert G.decode_snapshot(raw, 42, 124) is None
    assert G.decode_snapshot(raw[:100], 42, 123) is None
    for offset, value in ((8, 2), (16, 3), (24, 0), (64, 4)):
        broken = raw.copy()
        struct.pack_into("<I", broken, offset, value)
        assert G.decode_snapshot(broken, 42, 123) is None


def test_native_bucket_overflow_has_no_fabricated_upper_bound():
    raw = snapshot_bytes()
    buckets = [0] * 32
    buckets[31] = 1
    struct.pack_into("<35Q", raw, 64, 1, 3_000_000_000, 3_000_000_000, *buckets)
    result = G.decode_snapshot(raw, 42, 123)["metrics"]["update_screen"]
    assert result["p99_upper_ms"] is None
    assert result["max_ms"] == 3_000_000


def test_sidecar_control_does_not_create_a_map_when_disabled_and_revokes_stopped_reader(monkeypatch):
    mappings = []

    class Memory(bytearray):
        def close(self):
            pass

    def mapping(*args, **kwargs):
        result = Memory(G.SIZE)
        mappings.append(result)
        return result

    monkeypatch.setattr(G.mmap, "mmap", mapping)
    reader = G.GraphicsProfile("isolated")
    reader.refresh(None)
    assert mappings == []
    reader.refresh("first")
    first_generation = struct.unpack_from("<I", mappings[0], 32)[0]
    assert first_generation > 0
    reader.refresh("second")
    assert struct.unpack_from("<I", mappings[0], 32)[0] != first_generation
    stopped = threading.Event()
    stopped.set()
    reader.refresh("second", stopped)
    assert struct.unpack_from("<I", mappings[0], 32)[0] == 0
    assert reader.snapshot(42) is None  # old plugin has written no profile ABI
    reader.close()
    reader.refresh("third")
    assert len(mappings) == 1 and struct.unpack_from("<I", mappings[0], 32)[0] == 0
