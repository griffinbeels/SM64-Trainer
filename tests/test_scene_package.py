"""The experimental scene package (tools/scene_lab/package.py) on synthetic data.

A synthetic ROM holds a main executable, a level-script LOAD_RAW, a MIO0 block
and a payload only content discovery finds; a synthetic scene tap records six
tasks over it. No private RAM image or real ROM is involved.
"""
import importlib.util
import json
import random
import struct
import sys
import zlib
from argparse import Namespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_package", ROOT / "tools/scene_lab/package.py")
pk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pk
spec.loader.exec_module(pk)

RDRAM = 4 << 20
RUNS = [(0x208000, 0x208020), (0x209000, 0x209400), (0x246000, 0x248000),
        (0x350000, 0x352000), (0x360000, 0x362000), (0x3C0000, 0x3C0400)]
CHANGED = 0x361000            # a segment-7 granule the game rewrites each frame
TIMER = 0x32D5D4


def _mio0(data: bytes) -> bytes:
    """A literal-only MIO0 block."""
    words = (len(data) + 31) // 32
    offset = 16 + 4 * words
    return b"MIO0" + struct.pack(">III", len(data), offset, offset) + b"\xff" * 4 * words + data


def _rom() -> tuple[bytes, bytes]:
    rng = random.Random(64)
    rom = bytearray(2 << 20)
    rom[0:4] = bytes.fromhex("80371240")
    rom[8:12] = struct.pack(">I", 0x80246000)
    rom[0x20:0x34] = b"SYNTHETIC SCENE TEST"
    rom[0x1000:0x9000] = rng.randbytes(0x8000)          # main executable, IPL3 copies it to 0x80246000
    rom[0x160000:0x168000] = rng.randbytes(0x8000)      # LOAD_RAW to segment 0x0E
    segment7 = rng.randbytes(0x4000)
    block = _mio0(segment7)
    rom[0x170000:0x170000 + len(block)] = block          # LOAD_MIO0 to segment 7
    rom[0x1C0000:0x1C2000] = rng.randbytes(0x2000)      # a payload the game copies by itself
    script = (struct.pack(">BBHII", 0x17, 0x0C, 0x0E, 0x160000, 0x168000)
              + struct.pack(">BBHII", 0x18, 0x0C, 0x07, 0x170000, 0x170000 + len(block)))
    rom[0x150000:0x150000 + len(script)] = script
    return bytes(rom), segment7


def _rdram(rom: bytes, segment7: bytes, frame: int) -> bytes:
    ram = bytearray(RDRAM)
    ram[0x246000:0x24E000] = rom[0x1000:0x9000]
    ram[0x350000:0x358000] = rom[0x160000:0x168000]
    ram[0x360000:0x364000] = segment7
    ram[CHANGED:CHANGED + 32] = random.Random(100 + frame).randbytes(32)
    ram[0x3C0000:0x3C2000] = rom[0x1C0000:0x1C2000]
    dl = struct.pack(">6I", 0xBC000006 | 0x0E << 10, 0x350000, 0xBC000006 | 7 << 10, 0x360000, 0xB8000000, 0)
    ram[0x208000:0x208000 + len(dl)] = dl
    ram[0x209000:0x209400] = random.Random(frame % 3).randbytes(0x400)   # frames 0 and 3 repeat
    ram[TIMER:TIMER + 4] = struct.pack(">I", 1000 + frame)
    return pk.swap_words(bytes(ram))


def _tap_record(kind: int, sequence: int, body: bytes) -> bytes:
    size = (32 + len(body) + 31) & ~31
    return struct.pack("<IIIIq8x", pk.tap.MAGIC, kind, size, sequence, 1000 * sequence) + body + bytes(
        size - 32 - len(body))


def _task_body(image: bytes, frame: int) -> bytes:
    cimg = 0x100000 + (frame % 2) * 0x80000
    closure = sum(end - begin for begin, end in RUNS)
    fields = (0, 3, 1000, 2000, 0, len(RUNS), 1, closure, cimg, 1, frame, 0)
    task = (1, 0, 0x80246000, 208, 0x80247000, 4096, 0x80248000, 2048, 0x200000, 1024,
            0x210000, 0x220000, 0x208000, 24 + frame, 0, 0)
    return (struct.pack("<12I", *fields) + struct.pack("<16I", *task)
            + b"".join(struct.pack("<II", *run) for run in RUNS) + struct.pack("<II", TIMER, 4)
            + b"".join(image[begin:end] for begin, end in RUNS) + image[TIMER:TIMER + 4])


def _present_body(frame: int) -> bytes:
    vi = [0x3016, 0x100000 + (frame % 2) * 0x80000, 320] + [frame] * 11
    return struct.pack("<14I", *vi) + struct.pack("<4I", 0, 0, 0, 1)


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    rom, segment7 = _rom()
    session = tmp_path_factory.mktemp("scene") / "tap-synthetic"
    session.mkdir()
    records, sequence = [], 0
    for frame in range(6):
        records.append(_tap_record(pk.tap.REC_TASK, sequence, _task_body(_rdram(rom, segment7, frame), frame)))
        sequence += 1
        if frame != 4:                    # task 4 is overtaken by task 5: never shown
            records.append(_tap_record(pk.tap.REC_PRESENT, sequence, _present_body(frame)))
            sequence += 1
    (session / "tap.bin").write_bytes(b"".join(records))
    (session / "session.txt").write_text(
        f"schema=scene-lab-tap/1\nwrapper_build=test\nqpc_frequency=10000000\nrdram_size={RDRAM}\n"
        f"rom_header={pk.rom_header_hex(rom)}\n", encoding="ascii")
    (session / "armed.txt").write_text(f"extra={TIMER:x}:4\n# identity=global_timer:{TIMER:x}:4\n", encoding="ascii")
    packages = {}
    for codec in ("lzma", "zlib"):
        out = session.parent / f"synthetic-{codec}.sm64scene"
        packages[codec] = (pk.write_package(pk.load_source(session), rom, out, pk.CODECS[codec], group=2),
                           out.read_bytes())
    expected = list(pk.load_source(session).captures)
    return {"rom": rom, "session": session, "packages": packages, "expected": expected}


@pytest.mark.parametrize("codec", ["lzma", "zlib"])
def test_every_task_reconstructs_in_any_order(scene, codec):
    stats, data = scene["packages"][codec]
    expected = scene["expected"]
    assert len(expected) == 6 and [capture.shown for capture in expected] == [True] * 4 + [False, True]
    assert expected[4].vi is None and expected[5].vi[1] == 0x180000
    order = list(range(6))
    shuffled = order[:]
    random.Random(7).shuffle(shuffled)
    for sequence in (order, order[::-1], shuffled):
        package = pk.Package(data, scene["rom"])
        assert [package.frame(index) for index in sequence] == [expected[index] for index in sequence]
    # Boot copy, LOAD_RAW and discovered payload as ROM slices, segment 7 as MIO0;
    # the DL, the dynamic block and the rewritten granule travel raw.
    assert stats["ref_bytes"] == 6 * (8192 * 3 - 32 + 1024)
    assert stats["raw_bytes"] == 6 * (32 + 1024 + 32)
    assert stats["raw_unique_bytes"] < stats["raw_bytes"]       # frame 3 repeats frame 0's block
    assert stats["discovered_windows"] == 1
    assert sorted(load[0] for load in package.index.loads) == [pk.LOAD_ROM] * 3 + [pk.LOAD_MIO0]


def test_one_frame_decodes_without_visiting_the_others(scene):
    _stats, data = scene["packages"]["lzma"]
    package = pk.Package(data, scene["rom"])
    assert package.frame(5) == scene["expected"][5]
    first_record_chunk = package.index.frames[0][0]
    assert first_record_chunk != package.index.frames[5][0]
    assert first_record_chunk not in package._chunks


def test_package_carries_no_rom_bytes(scene):
    _stats, data = scene["packages"]["lzma"]
    package = pk.Package(data, scene["rom"])
    decoded = b"".join(package._chunk(index) for index in range(len(package.index.chunks)))
    rom = scene["rom"]
    for start in (0x1000, 0x2F00, 0x160000, 0x161F00, 0x171000, 0x172000, 0x1C0000, 0x1C0300):
        sample = rom[start:start + 16]
        assert sample not in decoded and pk.swap_words(sample) not in decoded
    assert b"SYNTHETIC" not in data and b"tap-synthetic" not in data


def test_read_writes_frame_inputs_with_the_fill_pattern(scene, tmp_path):
    session = scene["session"]
    package_path = session.parent / "synthetic-lzma.sm64scene"
    rom_path = tmp_path / "rom.z64"
    rom_path.write_bytes(scene["rom"])
    out = tmp_path / "frames"
    assert pk.read(Namespace(package=str(package_path), rom=str(rom_path), out=str(out), frames="",
                             pattern=0x5A)) == 0
    frames = sorted(out.iterdir())
    assert [frame.name for frame in frames] == ["00000-task00000", "00001-task00001", "00002-task00002",
                                                 "00003-task00003", "00005-task00005"]
    rdram = (frames[-1] / "rdram.bin").read_bytes()
    capture = scene["expected"][5]
    assert len(rdram) == RDRAM and rdram[:0x208000] == b"\x5a" * 0x208000
    assert b"".join(rdram[begin:end] for begin, end in RUNS) == capture.run_bytes
    assert struct.unpack_from("<16I", (frames[-1] / "dmem.bin").read_bytes(), 0xFC0) == capture.task
    meta = json.loads((frames[-1] / "frame.json").read_text(encoding="utf-8"))
    assert meta["schema"] == "scene-lab-frame/1" and meta["identity"] == {"global_timer": 1005}
    assert meta["vi"]["origin"] == 0x180000 and meta["rom_header"] == pk.rom_header_hex(scene["rom"])
    assert meta["task"]["data_ptr"] == 0x208000 and meta["source"]["task_sequence"] == capture.task_sequence


def test_frame_inputs_package_like_a_tap(scene, tmp_path):
    rom, segment7 = _rom()
    for frame in range(2):
        folder = tmp_path / f"state-{frame}"
        folder.mkdir()
        (folder / "rdram.bin").write_bytes(_rdram(rom, segment7, frame))
        dmem = bytearray(0x1000)
        struct.pack_into("<16I", dmem, 0xFC0, *scene["expected"][frame].task)
        (folder / "dmem.bin").write_bytes(dmem)
        (folder / "frame.json").write_text(json.dumps({
            "rom_header": pk.rom_header_hex(rom), "vi": dict.fromkeys(pk.tap.VI_NAMES, frame)}), encoding="utf-8")
        (folder / "closure.json").write_text(json.dumps({
            "refusals": 0, "ranges": [{"begin": begin, "end": end} for begin, end in RUNS]}), encoding="utf-8")
    out = tmp_path / "states.sm64scene"
    pk.write_package(pk.load_source(tmp_path, frames_glob="state-*"), rom, out)
    package = pk.Package(out.read_bytes(), rom)
    assert [package.frame(index) for index in (1, 0)] == list(pk.load_source(tmp_path, frames_glob="state-*")
                                                              .captures)[::-1]


def test_a_different_rom_is_refused(scene, tmp_path):
    _stats, data = scene["packages"]["lzma"]
    other = bytearray(scene["rom"])
    other[0x1F0000] ^= 1
    with pytest.raises(pk.PackageError, match="different ROM"):
        pk.Package(data, bytes(other))
    other[0x20] ^= 1                       # a different header: the writer refuses the session
    with pytest.raises(SystemExit, match="different ROM"):
        pk.write_package(pk.load_source(scene["session"]), bytes(other), tmp_path / "x.sm64scene")


def _outcome(data: bytes, rom: bytes, expected: list) -> str:
    try:
        package = pk.Package(data, rom)
        frames = [package.frame(index) for index in range(len(package))]
    except pk.PackageError:
        return "refused"
    return "identical" if frames == expected else "WRONG"


@pytest.mark.parametrize("codec", ["lzma", "zlib"])
def test_truncated_or_corrupted_packages_are_refused(scene, codec):
    _stats, data = scene["packages"][codec]
    rom, expected = scene["rom"], scene["expected"]
    for cut in (0, 8, pk.HEADER.size - 1, pk.HEADER.size, len(data) // 2, len(data) - 1):
        assert _outcome(data[:cut], rom, expected) == "refused", cut
    outcomes = []
    for position in sorted(set(range(0, pk.HEADER.size, 4)) | set(range(0, len(data), max(1, len(data) // 160)))):
        flipped = bytearray(data)
        flipped[position] ^= 0x41
        outcomes.append(_outcome(bytes(flipped), rom, expected))
    assert len(outcomes) > 150 and set(outcomes) == {"refused"}


def _tampered(data: bytes, change) -> bytes:
    """Re-assemble a package whose index was edited, with every hash recomputed."""
    header = pk.read_header(data)
    raw = pk.decompress(pk.ZLIB, data[header.index_offset:], header.index_raw)
    index = pk.parse_index(raw)
    tables = {"loads": index.loads, "chunks": index.chunks, "blocks": index.blocks, "frames": index.frames}
    body = change(tables, data[pk.HEADER.size:header.index_offset]) or data[pk.HEADER.size:header.index_offset]
    return pk.assemble(header.rom_sha, header.rdram_size, len(tables["frames"]), body,
                       pk.encode_index(index.meta, *tables.values()))


def _replace(table: list, row: int, column: int, value):
    table[row] = (*table[row][:column], value, *table[row][column + 1:])


@pytest.mark.parametrize("change, message", [
    (lambda t, b: _replace(t["frames"], 0, 2, 1 << 30), "outside its chunk"),
    (lambda t, b: _replace(t["blocks"], 1, 1, t["blocks"][0][1]), "overlapping entries"),
    (lambda t, b: _replace(t["chunks"], 1, 0, t["chunks"][0][0]), "overlapping another"),
    (lambda t, b: _replace(t["chunks"], 0, 2, pk.MAX_CHUNK_BYTES + 1), "malformed chunk"),
    (lambda t, b: _replace(t["loads"], 0, 2, 1 << 30), "malformed load"),
    (lambda t, b: _replace(t["loads"], 0, 4, bytes(32)), "does not match this ROM"),
    (lambda t, b: _replace(t["chunks"], 0, 2, t["chunks"][0][2] + 1), "decoded size"),
    (lambda t, b: _replace(t["chunks"], 0, 4, bytes(32)), "chunk 0 is corrupt"),
    (lambda t, b: _replace(t["frames"], 0, 4, t["frames"][1][4]), "frame 0 does not reconstruct"),
])
def test_consistent_but_hostile_indexes_are_refused(scene, change, message):
    _stats, data = scene["packages"]["zlib"]
    hostile = _tampered(data, change)
    with pytest.raises(pk.PackageError, match=message):
        package = pk.Package(hostile, scene["rom"])
        for index in range(len(package)):
            package.frame(index)


def test_hostile_frame_records_and_compression_bombs_are_refused(scene):
    capture = scene["expected"][0]
    good = [(pk.SEG_RAW, end - begin, 0, 0) for begin, end in capture.runs]
    closure = len(capture.run_bytes)
    pk.parse_record(pk.encode_record(capture, good), RDRAM, closure)
    pair = [(pk.SEG_RAW, 64, 0, 0)]
    hostile = {      # each tiles its claimed closure, so only the named check can refuse it
        "overlap": ([(0x1000, 0x1020), (0x1010, 0x1030)], pair, 64),
        "unordered": ([(0x2000, 0x2020), (0x1000, 0x1020)], pair, 64),
        "past RDRAM": ([(RDRAM - 32, RDRAM + 32)], pair, 64),
        "unaligned": ([(0x1002, 0x1042)], pair, 64),
        "not tiling": (capture.runs, good[:-1], closure),
        "short claim": (capture.runs, good, closure - 4),
    }
    for name, (runs, segments, claim) in hostile.items():
        record = pk.encode_record(pk.Capture(0, capture.task, runs, b""), segments)
        with pytest.raises(pk.PackageError):
            pk.parse_record(record, RDRAM, claim)
            pytest.fail(name)
    with pytest.raises(pk.PackageError, match="decoded size"):
        pk.decompress(pk.ZLIB, zlib.compress(bytes(16 << 20)), 100)
