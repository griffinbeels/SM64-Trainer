"""A simulated scene's writer (tools/scene_lab/sim_scene.py): the clock its records play by, and the packer's
kept knowledge of the ROM. The host feeds frames as fast as the simulation makes them and the viewer plays a scene
by its record times, so each task must sit one game frame after the one before and its picture half a frame after
it, however tap.bin's bytes arrive while the package is written beside it."""
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


def test_records_move_to_the_games_clock_however_the_bytes_arrive():
    kinds = [tap.REC_TASK, tap.REC_REGIONS, tap.REC_PRESENT, tap.REC_TASK, tap.REC_REGIONS, tap.REC_REGIONS,
             tap.REC_PRESENT, tap.REC_TASK, tap.REC_PRESENT]
    host_clock = [5, 9, 70, 400, 401, 405, 900, 2000, 2600]          # the host's own, uneven
    data = b"".join(record(kind, n, qpc, b"x" * n) for n, (kind, qpc) in enumerate(zip(kinds, host_clock, strict=True)))
    clock = sim_scene.Clock(10_000_000)
    out = b""
    for at in range(0, len(data), 45):                              # pieces that tear records apart
        out += clock.feed(data[at:at + 45])
    assert len(out) == len(data) and clock.pending == b""
    times = [(kind, qpc) for _at, kind, _size, _seq, qpc, _flags in tap._walk(out, torn_ok=False)]
    period = round(10_000_000 * 1001 / 30000)
    assert times == [(tap.REC_TASK, 0), (tap.REC_REGIONS, 0), (tap.REC_PRESENT, period // 2),
                     (tap.REC_TASK, period), (tap.REC_REGIONS, period), (tap.REC_REGIONS, period),
                     (tap.REC_PRESENT, period + period // 2), (tap.REC_TASK, 2 * period),
                     (tap.REC_PRESENT, 2 * period + period // 2)]
    assert [struct.unpack_from("<I", out, at + 12)[0] for at, *_rest in tap._walk(out, torn_ok=False)] == list(range(9))


def _rom() -> bytes:
    rom = bytearray(0x20000)
    rom[:4] = bytes.fromhex("80371240")
    struct.pack_into(">I", rom, 8, 0x80246000)
    return bytes(rom)


def test_the_packers_knowledge_of_a_rom_is_kept_between_builds(tmp_path, monkeypatch):
    """Decoding a ROM's loads cost 0.95 s of every build (2026-11-08); the second build reads them back, with the
    windows and misses the first one discovered, and a damaged file is decoded again rather than trusted."""
    import provenance
    decoded = []
    loads = [{"segment": 7, "start": 0x1000, "end": 0x1400, "kind": 0x17, "data": bytes(range(256)) * 4},
             {"segment": None, "start": 0x2000, "end": 0x2000, "kind": 0x18, "data": b"\x01\x02" * 64}]
    monkeypatch.setattr(provenance, "segment_loads", lambda rom: decoded.append(1) or loads)
    rom = _rom()
    first = sim_scene.Seed(tmp_path, rom)
    attributor = first.attributor(8 << 20)
    assert decoded == [1]
    attributor.windows.add((0x400000, 0x10000, 0x18000))
    attributor.misses.add(b"\xab" * 32)
    first.keep(attributor)
    again = sim_scene.Seed(tmp_path, rom).attributor(8 << 20)
    assert decoded == [1]                                            # read back, not decoded
    assert (0x400000, 0x10000, 0x18000) in again.windows and b"\xab" * 32 in again.misses
    assert [(c.rom_start, bytes(c.data)) for c in again.by_segment[7]] == [(0x1000, loads[0]["data"])]
    assert [(c.rom_start, bytes(c.data)) for c in again.anywhere] == [(0x2000, loads[1]["data"])]
    seed_file = next(tmp_path.glob("seed-*.bin"))
    seed_file.write_bytes(seed_file.read_bytes()[:40])               # torn
    sim_scene.Seed(tmp_path, rom).attributor(8 << 20)
    assert decoded == [1, 1]
    other = _rom()[:-1] + b"\x01"
    sim_scene.Seed(tmp_path, other).attributor(8 << 20)              # another ROM never reads this one's
    assert decoded == [1, 1, 1]
