"""A simulated session's clock (tools/scene_lab/sim_scene.py retime): the host feeds frames as fast as the
simulation makes them, the viewer plays a scene by its record times, so each task must sit one game frame
after the one before and its picture half a frame after it."""
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import sim_scene  # noqa: E402
import tap  # noqa: E402


def record(kind: int, sequence: int, qpc: int, body: bytes = b"") -> bytes:
    size = 32 + (len(body) + 31) // 32 * 32
    return tap.HEADER.pack(tap.MAGIC, kind, size, sequence, qpc, 0, 0) + body.ljust(size - 32, b"\0")


def test_records_move_to_the_games_clock(tmp_path):
    (tmp_path / "session.txt").write_text("qpc_frequency=10000000\n", encoding="utf-8")
    kinds = [tap.REC_TASK, tap.REC_REGIONS, tap.REC_PRESENT, tap.REC_TASK, tap.REC_REGIONS, tap.REC_REGIONS,
             tap.REC_PRESENT, tap.REC_TASK, tap.REC_PRESENT]
    host_clock = [5, 9, 70, 400, 401, 405, 900, 2000, 2600]          # the host's own, uneven
    (tmp_path / "tap.bin").write_bytes(b"".join(record(kind, n, qpc, b"x" * n)
                                                for n, (kind, qpc) in enumerate(zip(kinds, host_clock, strict=True))))
    sim_scene.retime(tmp_path)
    data = (tmp_path / "tap.bin").read_bytes()
    times = [(kind, qpc) for _at, kind, _size, _seq, qpc, _flags in tap._walk(data, torn_ok=False)]
    period = round(10_000_000 * 1001 / 30000)
    assert times == [(tap.REC_TASK, 0), (tap.REC_REGIONS, 0), (tap.REC_PRESENT, period // 2),
                     (tap.REC_TASK, period), (tap.REC_REGIONS, period), (tap.REC_REGIONS, period),
                     (tap.REC_PRESENT, period + period // 2), (tap.REC_TASK, 2 * period),
                     (tap.REC_PRESENT, 2 * period + period // 2)]
    assert [struct.unpack_from("<I", data, at + 12)[0] for at, *_rest in tap._walk(data, torn_ok=False)] == list(range(9))
