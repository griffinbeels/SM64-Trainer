"""The scene package's scene-state layer (format v1, tools/scene_lab/package.py) on synthetic data.

A synthetic scene tap records fourteen tasks with what SM64's renderer reads:
task regions (unsorted spans, one moving with the task's data pointer, one task
without), a level region keyed twice and patched block by block (one patch
ahead of the first task, one on a refused task), and the tap's full RDRAM copy
that locates a practice ROM's boot-copied code. tap.scene_states, the owner of
the rebuild, is the expected value. No private RAM image or real ROM is involved.

The scene-resolution layer (what the evaluator's search found per frame) is written over that
package by package.with_resolution; here its format and refusals. Whether a recorded resolution
reproduces the capture needs Unicorn and a real recording (e2_georender.py from-package).
"""
import importlib.util
import json
import random
import struct
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_package_v1", ROOT / "tools/scene_lab/package.py")
pk = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pk
spec.loader.exec_module(pk)

RDRAM = 8 << 20
TASKS = 14
EVERY = 4
LEVEL = (0x100000, 0x10000)        # the level region: [begin, bytes)
ROM_LEVEL = (0x160000, 0x8000)     # a LOAD_RAW the level copies to segment 0x0E's base
CODE_ROM, CODE_BYTES = 0x1D0000, 0x3000
REFUSED, NO_REGIONS, SECOND_KEY = 10, 5, 7
MARKER = b"FULL-COPY-ONLY!!"   # in the full RDRAM copy, outside every captured span


def _rom() -> bytes:
    rng = random.Random(81)
    rom = bytearray(2 << 20)
    rom[0:4] = bytes.fromhex("80371240")
    rom[8:12] = struct.pack(">I", 0x80246000)
    rom[0x20:0x34] = b"SYNTHETIC STATE TEST"
    rom[0x1000:0x9000] = rng.randbytes(0x8000)
    rom[ROM_LEVEL[0]:sum(ROM_LEVEL)] = rng.randbytes(ROM_LEVEL[1])
    rom[CODE_ROM:CODE_ROM + CODE_BYTES + 64] = rng.randbytes(CODE_BYTES + 64)
    script = struct.pack(">BBHII", 0x17, 0x0C, 0x0E, ROM_LEVEL[0], sum(ROM_LEVEL))
    rom[0x150000:0x150000 + len(script)] = script
    return bytes(rom)


def _record(kind: int, sequence: int, body: bytes) -> bytes:
    size = (32 + len(body) + 31) & ~31
    return struct.pack("<IIIIq8x", pk.tap.MAGIC, kind, size, sequence, 1000 * sequence) + body + bytes(
        size - 32 - len(body))


def _regions(task_index: int, kind: int, segment_base: int, spans: list, image: bytes) -> bytes:
    """A region record body: fields, the task's 16 segment bases, spans, their bytes (RDRAM order)."""
    segments = [0] * 16
    segments[0x0E] = segment_base
    data = b"".join(image[begin:begin + size] for begin, size in spans)
    return (struct.pack("<8I", task_index, kind, len(spans), len(data), 0, 0, 256, 0) + struct.pack("<16I", *segments)
            + b"".join(struct.pack("<II", *span) for span in spans) + data)


def _task(task_index: int, image: bytes, refused: bool) -> bytes:
    data_ptr = 0x208000 if task_index % 2 == 0 else 0x214000
    runs = [(data_ptr, data_ptr + 0x20)]
    fields = (int(refused), 1, 1000, 2000, 0, 1, 0, 0x20, 0x100000, 1, task_index, 0)
    task = (1, 0, 0x80246000, 208, 0x80247000, 4096, 0x80248000, 2048, 0x200000, 1024,
            0x210000, 0x220000, data_ptr, 8, 0, 0)
    return (struct.pack("<12I", *fields) + struct.pack("<16I", *task)
            + b"".join(struct.pack("<II", *run) for run in runs) + b"".join(image[b:e] for b, e in runs))


def _session(folder: Path, rom: bytes) -> Path:
    """Fourteen tasks over an evolving RDRAM; each record carries what the live tap would."""
    rng = random.Random(7)
    ram = bytearray(RDRAM)
    ram[0x330000:0x332000] = rng.randbytes(0x2000)          # a data-like span
    ram[0x320000:0x321000] = bytes(0x1000)                    # a BSS-like span, mostly zero
    ram[LEVEL[0] + 0x8000:sum(LEVEL)] = rng.randbytes(0x8000)
    base = LEVEL[0]
    ram[base:base + ROM_LEVEL[1]] = rom[ROM_LEVEL[0]:sum(ROM_LEVEL)]
    for data_ptr in (0x208000, 0x214000):
        ram[data_ptr:data_ptr + 8] = struct.pack(">II", 0xB8000000, 0)   # G_ENDDL: the whole display list
    full = bytearray(ram)
    full[0x400000:0x400000 + CODE_BYTES + 64] = rom[CODE_ROM:CODE_ROM + CODE_BYTES + 64]
    full[0x400000 + CODE_BYTES] ^= 0xFF                       # a setting the code changed since boot
    full[0x500000:0x500000 + len(MARKER)] = MARKER
    records, sequence = [], 0

    def add(kind: int, body: bytes) -> None:
        nonlocal sequence
        records.append(_record(kind, sequence, body))
        sequence += 1

    add(pk.tap.REC_FULL, struct.pack("<I12x", RDRAM) + pk.swap_words(bytes(full)))
    swapped = pk.swap_words(bytes(ram))
    add(pk.tap.REC_REGIONS, _regions(0, 1, base, [LEVEL], swapped))       # a key ahead of the first task
    for index in range(TASKS):
        ram[0x330000 + 64 * index:0x330000 + 64 * index + 8] = rng.randbytes(8)
        ram[0x320000 + 4 * index] = index + 1
        tail = (0x208000 if index % 2 == 0 else 0x214000) + 0x800
        ram[tail:tail + 0x100] = rng.randbytes(0x100)
        changed = sorted(rng.sample(range(0x80, 0x100), 2))  # level blocks in the dynamic half
        for block in changed:
            at = LEVEL[0] + 256 * block + rng.randrange(0, 256, 4)
            ram[at:at + 4] = rng.randbytes(4)
        if index == SECOND_KEY:
            base = LEVEL[0] + 0x4000                         # segment 0x0E moved: a level load
            ram[base:base + ROM_LEVEL[1]] = rom[ROM_LEVEL[0]:sum(ROM_LEVEL)]
        swapped = pk.swap_words(bytes(ram))
        add(pk.tap.REC_TASK, _task(index, swapped, index == REFUSED))
        if index != NO_REGIONS:
            spans = [(0x330000, 0x2000), (0x320000, 0x1000), (tail, 0x100)]
            add(pk.tap.REC_REGIONS, _regions(index, 0, base, spans, swapped))
        if index == SECOND_KEY:
            add(pk.tap.REC_REGIONS, _regions(index, 1, base, [LEVEL], swapped))
        elif index:
            blocks = [(LEVEL[0] + 256 * block, 256) for block in changed]
            add(pk.tap.REC_REGIONS, _regions(index, 2, base, blocks if index != 3 else [], swapped))
        if index != 12:                                       # task 12 is overtaken by task 13
            add(pk.tap.REC_PRESENT, struct.pack("<14I", *[index] * 14) + struct.pack("<4I", 0, 0, 0, 1))
    session = folder / "tap-synthetic-state"
    session.mkdir()
    (session / "tap.bin").write_bytes(b"".join(records))
    (session / "session.txt").write_text(
        f"schema=scene-lab-tap/1\nwrapper_build=test\nqpc_frequency=10000000\nrdram_size={RDRAM}\n"
        f"rom_header={pk.rom_header_hex(rom)}\nregion=330000:8192\nregion=320000:4096\ntask_region=800:256\n"
        f"level_region={LEVEL[0]:x}:{LEVEL[1]}\n", encoding="ascii")
    (session / "armed.txt").write_text("# identity=\n", encoding="ascii")
    return session


def _fingerprints(session: Path) -> dict:
    """tap.scene_states' rebuild of every task, by task sequence."""
    states = pk.tap.scene_states(pk.tap.read_records(session / "tap.bin"), RDRAM)
    return {task.sequence: pk.state_fingerprint(regions, ram, known) for task, regions, ram, known in states}


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    folder = tmp_path_factory.mktemp("state")
    rom = _rom()
    session = _session(folder, rom)
    out = folder / "synthetic-state.sm64scene"
    stats = pk.write_package(pk.load_source(session, states=True), rom, out, pk.ZLIB, group=3,
                             checkpoint_every=EVERY)
    v0 = folder / "synthetic-v0.sm64scene"
    pk.write_package(pk.load_source(session), rom, v0, pk.ZLIB, group=3)
    return {"rom": rom, "session": session, "data": out.read_bytes(), "stats": stats, "v0": v0.read_bytes(),
            "expected": _fingerprints(session), "captures": list(pk.load_source(session).captures)}


def _state(package, index: int) -> bytes:
    return pk.state_fingerprint(*package.scene_state(index)[1:])


def test_every_state_rebuilds_from_the_package_alone_in_any_order(scene):
    data, rom, captures = scene["data"], scene["rom"], scene["captures"]
    assert pk.read_header(data).version == 1 and len(captures) == TASKS - 1      # the refused task is not packaged
    expected = [scene["expected"][capture.task_sequence] for capture in captures]
    order = list(range(len(captures)))
    shuffled = order[:]
    random.Random(3).shuffle(shuffled)
    for sequence in (order, order[::-1], shuffled):
        package = pk.Package(data, rom)
        assert [_state(package, index) for index in sequence] == [expected[index] for index in sequence]
    package = pk.Package(data, rom)
    assert [package.frame(index) for index in order] == captures      # the closures are untouched
    task, regions, _ram, known = package.scene_state(4)
    assert task.fields["task_index"] == 4 and regions.spans[0][0] < regions.spans[1][0]   # canonical order
    assert package.scene_state(NO_REGIONS)[1] is None and known.sum() > 0


def test_checkpoints_fall_on_the_spacing_level_keys_and_shape_changes(scene):
    package = pk.Package(scene["data"], scene["rom"])
    checkpoints = [index for index, row in enumerate(package.states.rows) if row[3] & pk.CHECKPOINT]
    frame_of = {package.fields(index)["task_index"]: index for index in range(len(package))}
    assert checkpoints == sorted({0, 4, 8, 12, frame_of[SECOND_KEY], frame_of[NO_REGIONS + 1]})
    assert scene["stats"]["state_checkpoints"] == len(checkpoints)


def test_a_few_bytes_of_a_state_are_the_whole_states_bytes_there(scene):
    """package.scene_bytes (the ghost sound's camera and Mario reads) against scene_state's RDRAM:
    spans across every region's edges, where nothing was captured and at random, in every frame."""
    whole, few = pk.Package(scene["data"], scene["rom"]), pk.Package(scene["data"], scene["rom"])
    rng = random.Random(11)
    edges = [0x330000, 0x332000, 0x320000, 0x321000, 0x208000, 0x208020, 0x208800, 0x214000, 0x214820, LEVEL[0],
             sum(LEVEL), LEVEL[0] + 0x4000, 0x500000]
    for index in range(len(whole)):
        ram = whole.scene_state(index)[2]
        spans = [(edge - 6, 12) for edge in edges] + [(rng.randrange(0, RDRAM - 64), rng.randrange(1, 64))
                                                       for _ in range(20)]
        assert few.scene_bytes(index, spans) == [ram[at:at + size].tobytes() for at, size in spans]


def test_a_few_bytes_keep_the_closure_over_the_task_regions_over_the_level():
    state = pk.tap.SceneState(256)
    state.apply_level(SimpleNamespace(kind="level_key", spans=[(0, 128)], data=bytes(range(128))))
    regions = SimpleNamespace(spans=[(96, 64)], data=bytes([0xA0]) * 64)
    task = SimpleNamespace(runs=[(120, 136)], run_bytes=bytes([0xC0]) * 16)
    ram, _known = state.task_ram(task, regions)
    spans = [(0, 256), (90, 40), (118, 4), (134, 6), (200, 8)]
    assert state.task_bytes(task, regions, spans) == [ram[at:at + size].tobytes() for at, size in spans]
    with pytest.raises(ValueError):
        state.task_bytes(task, regions, [(250, 8)])


def test_rom_loads_travel_as_references_and_no_full_copy_travels(scene):
    data, rom, stats = scene["data"], scene["rom"], scene["stats"]
    assert stats["state_ref_bytes"] >= ROM_LEVEL[1] * scene["stats"]["state_checkpoints"]
    package = pk.Package(data, rom)
    decoded = b"".join(package._chunk(index) for index in range(len(package.index.chunks)))
    for start in (ROM_LEVEL[0], ROM_LEVEL[0] + 0x4000, CODE_ROM, CODE_ROM + 0x1000):
        sample = rom[start:start + 16]
        assert sample not in decoded and pk.swap_words(sample) not in decoded
    assert MARKER not in decoded and pk.swap_words(MARKER) not in decoded and b"tap-synthetic" not in data
    assert package.states.code_windows() == [(0x80400000, CODE_BYTES, CODE_ROM)]


def test_a_closures_only_package_is_version_0_and_still_reads(scene):
    package = pk.Package(scene["v0"], scene["rom"])
    assert package.header.version == 0 and package.states is None
    assert [package.frame(index) for index in range(len(package))] == scene["captures"]
    with pytest.raises(pk.PackageError, match="no scene state"):
        package.scene_state(0)


def _rebuild(data: bytes, change=None, meta=None, version=None) -> bytes:
    """Re-assemble a v1 package whose index (or body) was edited, every hash recomputed."""
    header = pk.read_header(data)
    index = pk.parse_index(pk.decompress(pk.ZLIB, data[header.index_offset:], header.index_raw), header.version)
    states, codes = index.layers["scene-state"]
    tables = {"loads": list(index.loads), "chunks": list(index.chunks), "blocks": list(index.blocks),
              "frames": list(index.frames), "states": list(states), "codes": list(codes)}
    body = data[pk.HEADER.size:header.index_offset]
    edited_body = change(tables, body) if change else None
    body = edited_body if isinstance(edited_body, bytes) else body
    edited = json.loads(json.dumps(index.meta))
    if meta:
        meta(edited)
    raw = pk.encode_index(edited, tables["loads"], tables["chunks"], tables["blocks"], tables["frames"],
                          (pk.STATE, tables["states"]), (pk.CODE, tables["codes"]))
    return pk.assemble(header.rom_sha, header.rdram_size, len(tables["frames"]), body, raw,
                       header.version if version is None else version)


def _replace(table: list, row: int, column: int, value):
    table[row] = (*table[row][:column], value, *table[row][column + 1:])


def _with_record(record: bytes):
    """Point state row 0 at a new stored chunk holding `record`: only the record parser can refuse it."""
    def change(tables, body):
        tables["chunks"].append((pk.HEADER.size + len(body), len(record), len(record), pk.STORED,
                                 pk.hashlib.sha256(record).digest()))
        _replace(tables["states"], 0, 0, len(tables["chunks"]) - 1)
        _replace(tables["states"], 0, 1, 0)
        _replace(tables["states"], 0, 2, len(record))
        return body + record
    return change


def _open_all(data: bytes, rom: bytes) -> str:
    try:
        package = pk.Package(data, rom)
        for index in range(len(package)):
            package.frame(index)
            package.scene_state(index)
        package.states.code_windows()
    except pk.PackageError as error:
        return f"refused: {error}"
    return "opened"


HEAD = pk.STATE_HEAD.pack
RUN = pk.RUN.pack


@pytest.mark.parametrize("record, message", [
    (HEAD(pk.KEY_RECORD, 0xFFFFFFFF, 0, 0, 0), "counts out of range"),
    (HEAD(pk.KEY_RECORD, 0, RDRAM, 0, 0), "counts out of range"),
    (HEAD(pk.KEY_RECORD, 2, 0, 0, 0) + RUN(0x1000, 0x1100) + RUN(0x10F0, 0x1200), "overlap"),
    (HEAD(pk.KEY_RECORD, 1, 0, 0, 0) + RUN(RDRAM - 16, RDRAM + 16), "leave RDRAM"),
    (HEAD(pk.KEY_RECORD, 1, 0, 0, 0) + RUN(0x1000, 0x1100), "do not tile"),
    (HEAD(pk.KEY_RECORD, 3, 0, 0, 0) + RUN(0x1000, 0x1100), "truncated scene-state record"),
    (HEAD(pk.KEY_RECORD, 0, 0, 0, 0) + bytes(4), "trailing bytes"),
    (HEAD(pk.DELTA_RECORD, 0, 0, 0, 0), "does not match its row"),
    (HEAD(pk.KEY_RECORD, 0, 0, 0, 0), "state 0 does not reconstruct"),
])
def test_hostile_state_records_are_refused(scene, record, message):
    outcome = _open_all(_rebuild(scene["data"], _with_record(record)), scene["rom"])
    assert outcome.startswith("refused") and message in outcome, outcome


@pytest.mark.parametrize("change, message", [
    (lambda t, b: _replace(t["states"], 1, 2, 1 << 30), "outside its chunk"),
    (lambda t, b: _replace(t["states"], 1, 0, len(t["chunks"])), "outside its chunk"),
    (lambda t, b: _replace(t["states"], 0, 3, 0), "malformed scene-state rows"),
    (lambda t, b: _replace(t["states"], 2, 4, bytes(32)), "state 2 does not reconstruct"),
    (lambda t, b: t["states"].pop(), "do not match the frames"),
    (lambda t, b: _replace(t["chunks"], t["states"][0][0], 4, bytes(32)), "is corrupt"),
    (lambda t, b: _replace(t["chunks"], t["states"][0][0], 2, t["chunks"][t["states"][0][0]][2] + 1),
     "decoded size"),
])
def test_corrupt_or_oversized_state_chunks_and_rows_are_refused(scene, change, message):
    outcome = _open_all(_rebuild(scene["data"], change), scene["rom"])
    assert outcome.startswith("refused") and message in outcome, outcome


def test_the_code_load_must_match_the_viewers_rom(scene):
    data, rom = scene["data"], scene["rom"]
    load = pk.Package(data, rom).states.codes[0][0]
    for change, message in [
        (lambda t, b: _replace(t["loads"], load, 4, bytes(32)), "does not match this ROM"),
        (lambda t, b: (_replace(t["loads"], load, 1, CODE_ROM + 4), _replace(t["loads"], load, 2, CODE_ROM + 4
                                                                              + CODE_BYTES)), "does not match"),
        (lambda t, b: _replace(t["codes"], 0, 1, 0x80400002), "malformed code load"),
        (lambda t, b: _replace(t["codes"], 0, 1, 0x807FF000), "malformed code load"),
        (lambda t, b: _replace(t["codes"], 0, 0, len(t["loads"])), "malformed code load"),
    ]:
        outcome = _open_all(_rebuild(data, change), rom)
        assert outcome.startswith("refused") and message in outcome, outcome


def test_unknown_versions_and_layers_are_refused_with_a_reason(scene):
    data, rom = scene["data"], scene["rom"]
    with pytest.raises(pk.PackageError, match="unsupported package version 2"):
        pk.Package(_rebuild(data, version=2), rom)
    renamed = _rebuild(data, meta=lambda meta: meta["layers"][0].update(name="scene-motion"))
    with pytest.raises(pk.PackageError, match="unknown package layer 'scene-motion' version 1"):
        pk.Package(renamed, rom)
    with pytest.raises(pk.PackageError, match="unknown package layer 'scene-state' version 2"):
        pk.Package(_rebuild(data, meta=lambda meta: meta["layers"][0].update(version=2)), rom)
    with pytest.raises(pk.PackageError, match="malformed package layers"):
        pk.Package(_rebuild(data, version=0, meta=lambda meta: meta.update(schema="sm64-scene/0")), rom)


def test_truncated_or_flipped_v1_packages_are_refused(scene):
    data, rom = scene["data"], scene["rom"]
    for cut in (pk.HEADER.size, len(data) // 3, len(data) // 2, len(data) - 1):
        assert _open_all(data[:cut], rom).startswith("refused"), cut
    outcomes = set()
    for position in range(pk.HEADER.size, len(data), max(1, len(data) // 120)):
        flipped = bytearray(data)
        flipped[position] ^= 0x41
        outcomes.add(_open_all(bytes(flipped), rom).split(":")[0])
    assert outcomes == {"refused"}


# ---------------------------------------------------------------- the scene-resolution layer

OBJECT, MATRIX = 0x8033D488, 0x80214480


def _answers(count: int) -> list:
    """Frame 1 restores two ranges, frame 2 puts back one throwMatrix pointer, frame 3 needed
    neither, and the search reproduced none of the others."""
    answers = [None] * count
    answers[1] = ([(0x1000, 0x1010), (0x2000, 0x2004)], [])
    answers[2] = ([], [(OBJECT, MATRIX)])
    answers[3] = ([], [])
    return answers


def _layers(data: bytes, layers: list, version: int | None = None) -> bytes:
    """The package re-assembled with exactly these (metadata, tables) layers, hashes recomputed."""
    formats = {"scene-state": (pk.STATE, pk.CODE), "scene-resolution": (pk.RESOLVE, pk.RANGE, pk.POINTER)}
    header = pk.read_header(data)
    index = pk.parse_index(pk.decompress(pk.ZLIB, data[header.index_offset:], header.index_raw), header.version)
    meta = json.loads(json.dumps(index.meta))
    meta["layers"] = [layer_meta for layer_meta, _tables in layers]
    encoded = [(fmt, list(rows)) for layer_meta, tables in layers
               for fmt, rows in zip(formats[layer_meta["name"]], tables, strict=True)]
    raw = pk.encode_index(meta, index.loads, index.chunks, index.blocks, index.frames, *encoded)
    body = data[pk.HEADER.size:header.index_offset]
    return pk.assemble(header.rom_sha, header.rdram_size, len(index.frames), body, raw,
                       header.version if version is None else version)


def test_a_resolution_layer_records_each_frames_answer_and_keeps_the_body(scene):
    data, rom = scene["data"], scene["rom"]
    plain = pk.Package(data, rom)
    assert plain.resolutions is None and plain.resolution(1) is None
    answers = _answers(len(plain))
    resolved = pk.with_resolution(data, rom, answers)
    package = pk.Package(resolved, rom)
    assert [package.resolution(index) for index in range(len(package))] == [
        None if answer is None else (list(answer[0]), list(answer[1])) for answer in answers]
    before, after = pk.read_header(data), pk.read_header(resolved)
    assert resolved[pk.HEADER.size:after.index_offset] == data[pk.HEADER.size:before.index_offset]
    assert [package.frame(index) for index in range(len(package))] == scene["captures"]
    assert _state(package, 4) == _state(plain, 4)
    again = pk.Package(pk.with_resolution(resolved, rom, [None] * len(package)), rom)    # replaced, not added
    assert [layer["name"] for layer in again.index.meta["layers"]] == ["scene-state", "scene-resolution"]
    assert all(again.resolution(index) is None for index in range(len(again)))
    with pytest.raises(pk.PackageError, match="needs the scene-state layer"):
        pk.with_resolution(scene["v0"], rom, [None] * len(package))
    with pytest.raises(pk.PackageError, match="one resolution per frame"):
        pk.with_resolution(data, rom, answers[:-1])


def _tables(scene) -> tuple:
    """(state layer tables, resolution rows, ranges, pointers) of the resolved synthetic package."""
    package = pk.Package(pk.with_resolution(scene["data"], scene["rom"], _answers(TASKS - 1)), scene["rom"])
    rows, ranges, pointers = package.index.layers["scene-resolution"]
    return package.index.layers["scene-state"], list(rows), list(ranges), list(pointers)


STATE_META = {"name": "scene-state", "version": 1, "checkpoint_every": EVERY, "group": 3}
RESOLUTION_META = {"name": "scene-resolution", "version": 1}


@pytest.mark.parametrize("edit, message", [
    (lambda rows, ranges, pointers: rows.pop(), "rows do not match the frames"),
    (lambda rows, ranges, pointers: _replace(rows, 1, 0, 2), "malformed scene-resolution rows"),
    (lambda rows, ranges, pointers: _replace(rows, 1, 2, 3), "malformed scene-resolution rows"),
    (lambda rows, ranges, pointers: _replace(rows, 1, 2, pk.MAX_FRAME_RANGES + 1), "malformed scene-resolution"),
    (lambda rows, ranges, pointers: _replace(rows, 2, 4, 2), "malformed scene-resolution rows"),
    (lambda rows, ranges, pointers: _replace(rows, 0, 2, 1), "malformed scene-resolution rows"),   # unresolved
    (lambda rows, ranges, pointers: ranges.reverse(), "restored ranges overlap, are unordered"),
    (lambda rows, ranges, pointers: _replace(ranges, 1, 0, 0x1008), "restored ranges overlap"),
    (lambda rows, ranges, pointers: _replace(ranges, 1, 1, RDRAM + 4), "leave RDRAM"),
    (lambda rows, ranges, pointers: _replace(pointers, 0, 0, 0x80000000 + RDRAM), "pointer leaves RDRAM"),
    (lambda rows, ranges, pointers: _replace(pointers, 0, 1, 0x80000000 + RDRAM - 32), "pointer leaves RDRAM"),
    (lambda rows, ranges, pointers: _replace(pointers, 0, 1, MATRIX + 2), "pointer leaves RDRAM"),
    (lambda rows, ranges, pointers: _replace(pointers, 0, 0, 0x00214480), "pointer leaves RDRAM"),
])
def test_hostile_resolution_rows_are_refused(scene, edit, message):
    state, rows, ranges, pointers = _tables(scene)
    edit(rows, ranges, pointers)
    data = _layers(scene["data"], [(STATE_META, state), (RESOLUTION_META, (rows, ranges, pointers))])
    with pytest.raises(pk.PackageError, match=message):
        pk.Package(data, scene["rom"])


def test_a_resolution_layer_needs_the_state_layer_and_a_known_version(scene):
    state, rows, ranges, pointers = _tables(scene)
    data, rom = scene["data"], scene["rom"]
    resolution = (rows, ranges, pointers)
    assert pk.Package(_layers(data, [(STATE_META, state), (RESOLUTION_META, resolution)]), rom).resolution(2)
    with pytest.raises(pk.PackageError, match="unknown package layer 'scene-resolution' version 2"):
        pk.Package(_layers(data, [(STATE_META, state), ({**RESOLUTION_META, "version": 2}, resolution)]), rom)
    with pytest.raises(pk.PackageError, match="needs the scene-state layer"):
        pk.Package(_layers(data, [(RESOLUTION_META, resolution)]), rom)
    with pytest.raises(pk.PackageError, match="malformed package layers"):
        pk.Package(_layers(data, [(STATE_META, state), (RESOLUTION_META, resolution)], version=0), rom)
