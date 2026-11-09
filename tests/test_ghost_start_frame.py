"""A ghost's start frame (src/sm64_events/ghost/start_frame.py): what it keeps comes back byte for byte, and a
start from another machine is refused, with a plain sentence, for every way it can be wrong or hostile."""
import json
import lzma
import random
import struct
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.ghost import start_frame as sf
from sm64_events.ghost.native_layout import POSE_AT, VALID_POSE
from sm64_events.memory.layout import layout_for

SHA = "ab" * 32
US = layout_for("us")
# A stand-in ROM: seeded noise through the boot window, no game bytes. Its boot image of .data is what a start's
# .data travels against.
ROM = random.Random(64).randbytes(sf.BOOT_ROM + sf.BOOT_BYTES)
OTHER_ROM = random.Random(65).randbytes(sf.BOOT_ROM + sf.BOOT_BYTES)
FIXTURES = Path(__file__).resolve().parents[1] / "cloud" / "community" / "test" / "starts"


@pytest.fixture(autouse=True)
def no_rom_on_this_machine(monkeypatch):
    """Hermetic: the ROMs this install knows are only the ones a test names (never his real game file)."""
    known = []
    monkeypatch.setattr(sf, "_rom_candidates", lambda: list(known))
    monkeypatch.setattr(sf, "_ROM_CACHE", {})
    return known


def install_rom(tmp_path, known, data=ROM) -> Path:
    path = tmp_path / f"rom-{len(known)}.z64"
    path.write_bytes(data)
    known.append(path)
    return path


def regions(timer=3489, counter=2194, level=24, area=1, action=0x1924, position=(2602.0, 1256.0, 5120.0), rom=None):
    boot = dict(sf.boot_spans("us")) if rom is not None else {}
    spans = [(begin, bytearray(sf.boot_image(rom, begin, size) if begin in boot else bytes(size)))
             for begin, size in sf.allowed_spans("us")]

    def put(address, fmt, *values):
        at = address & 0x1FFFFFFF
        for begin, data in spans:
            if begin <= at < begin + len(data):
                struct.pack_into(fmt, data, at - begin, *values)
                return
        raise AssertionError(hex(address))

    put(US.global_timer, ">I", timer)
    put(US.area_update_counter, ">H", counter)
    put(US.curr_level, ">h", level)
    put(US.curr_area, ">h", area)
    put(US.mario_struct + 0x0C, ">I", action)
    put(US.mario_struct + 0x3C, ">3f", *position)
    return [(begin, bytes(data)) for begin, data in spans]


def sample(**changes) -> sf.StartFrame:
    start = sf.StartFrame(SHA, "us", 3489, 2194, 24, 1, 1, regions(), "cd" * 32, "ef" * 32,
                          "cold-reset-v1-24-1-1", "12" * 32, [(0x5C010, b"\x01\x02\x03"), (0x1C0000, b"\xff" * 9)])
    for name, value in changes.items():
        setattr(start, name, value)
    return start


def record(timer=3489, counter=2194, level=24, area=1, action=0x1924, position=(2602.0, 1256.0, 5120.0)):
    raw = bytearray(1400)
    struct.pack_into("<3f", raw, POSE_AT, *position)
    struct.pack_into("<I", raw, POSE_AT + 40, action)
    return SimpleNamespace(global_timer=timer, area_update_counter=counter, level=level, area=area,
                           valid_fields=VALID_POSE, raw=bytes(raw))


def test_a_start_round_trips(tmp_path):
    written = sf.write_start(tmp_path, sample())
    assert written == tmp_path / "starts" / f"{SHA}.sm64start"
    back, want = sf.read_start(tmp_path, SHA), sample()
    assert (back.global_timer, back.area_update_counter, back.level, back.area, back.act) == (3489, 2194, 24, 1, 1)
    assert back.regions == want.regions and back.pool_runs == want.pool_runs
    assert (back.pool_base, back.pool_base_sha256, back.rom_sha256, back.usamune_fingerprint) == (
        want.pool_base, want.pool_base_sha256, want.rom_sha256, want.usamune_fingerprint)
    assert len(written.read_bytes()) < sf.MAX_BLOB_BYTES


def test_the_upload_door_gives_his_own_start_and_nothing_else(tmp_path):
    assert sf.start_blob(tmp_path, SHA) is None
    sf.write_start(tmp_path, sample())
    travels = sf.decode(sf.start_blob(tmp_path, SHA), SHA)
    assert travels.regions == sample().regions                       # what keeps Mario and objects exact
    assert travels.pool_runs == [] and travels.pool_base is None     # never applied on another machine
    (tmp_path / "starts" / f"{'cd' * 32}{sf.SUFFIX}").write_bytes(b"not a start")
    assert sf.start_blob(tmp_path, "cd" * 32) is None                 # a damaged file never travels


def test_a_downloaded_start_bound_to_its_ghost_is_kept_apart(tmp_path, monkeypatch):
    clip_bytes = b"clip bytes"
    sha = sf.digest(clip_bytes)
    monkeypatch.setattr("sm64_events.ghost.clip.decode_clip", lambda data: SimpleNamespace(frames=[record()]))
    kept = sf.accept_start_blob(tmp_path, clip_bytes, sf.encode(sample(clip_sha256=sha)))
    assert kept == tmp_path / "starts" / "downloaded" / f"{sha}.sm64start"
    assert sf.read_start(tmp_path, sha) is None                      # never among his own starts
    assert sf.start_blob(tmp_path, sha) is None                      # and never travels on
    assert sf.read_start(tmp_path, sha, downloaded=True).level == 24


@pytest.mark.parametrize("change, words", [
    ({"timer": 3490}, "does not begin where"), ({"counter": 7}, "does not begin where"), ({"level": 9}, "does not begin where"),
    ({"area": 2}, "does not begin where"), ({"action": 0x0C400201}, "Mario is not where"),
    ({"position": (0.0, 0.0, 0.0)}, "Mario is not where")])
def test_a_start_not_bound_to_its_ghost_is_refused(tmp_path, monkeypatch, change, words):
    clip_bytes = b"clip bytes"
    monkeypatch.setattr("sm64_events.ghost.clip.decode_clip", lambda data: SimpleNamespace(frames=[record(**change)]))
    with pytest.raises(sf.StartError, match=words):
        sf.accept_start_blob(tmp_path, clip_bytes, sf.encode(sample(clip_sha256=sf.digest(clip_bytes))))
    assert not (tmp_path / "starts").exists()                        # nothing written


def test_a_header_that_disagrees_with_its_own_state_is_refused():
    start = sample(regions=regions(timer=1))
    with pytest.raises(sf.StartError, match="disagrees"):
        sf.bind(start, record())


def test_a_start_named_for_another_ghost_is_refused():
    with pytest.raises(sf.StartError, match="another ghost"):
        sf.decode(sf.encode(sample()), "ef" * 32)


def _blob_with(payload: bytes, meta_changes: dict | None = None, version: int = sf.VERSION) -> bytes:
    good = sf.encode(sample())
    _magic, _version, length = struct.unpack_from("<8sII", good)
    import json
    meta = json.loads(good[16:16 + length])
    meta.update(meta_changes or {})
    text = json.dumps(meta).encode()
    return struct.pack("<8sII", sf.MAGIC, version, len(text)) + text + lzma.compress(payload)


def test_a_decompression_bomb_is_refused_without_unpacking_it(monkeypatch):
    """The output is bounded while it unpacks (max_length), not measured after: 64 MiB of zeros packs to a few
    KB and would otherwise be held whole."""
    bomb = _blob_with(bytes(64 << 20))
    assert len(bomb) < sf.MAX_BLOB_BYTES
    produced = []
    real = lzma.LZMADecompressor

    class Watched:                                   # the C type takes no subclass: wrap it
        def __init__(self, *args, **kwargs):
            self.inner = real(*args, **kwargs)

        def decompress(self, data, max_length=-1):
            out = self.inner.decompress(data, max_length)
            produced.append((max_length, len(out)))
            return out

        def __getattr__(self, name):
            return getattr(self.inner, name)

    monkeypatch.setattr(sf.lzma, "LZMADecompressor", Watched)
    with pytest.raises(sf.StartError, match="more than any real start"):
        sf.decode(bomb)
    assert produced and all(0 < limit <= sf.MAX_PAYLOAD_BYTES + 1 and size <= limit for limit, size in produced)


@pytest.mark.parametrize("damage, words", [("magic", "not a ghost start"), ("truncated", "damaged|cut short|unpacks"),
                                           ("extra", "unpacks"), ("huge", "wrong size")])
def test_damage_is_refused(damage, words):
    data = sf.encode(sample())
    if damage == "magic":
        data = b"X" + data[1:]
    elif damage == "truncated":
        data = data[:-20]
    elif damage == "extra":
        data = data + b"\0"
    else:
        data = data + bytes(sf.MAX_BLOB_BYTES)
    with pytest.raises(sf.StartError, match=words):
        sf.decode(data)


def test_another_version_is_refused():
    data = bytearray(sf.encode(sample()))
    for version in (1, 4):
        struct.pack_into("<I", data, 8, version)
        with pytest.raises(sf.StartError, match=f"version {version}"):
            sf.decode(bytes(data))


# -- .data travels as its difference from the ROM's boot image (Griffin, 2026-11-09: "Sure") ----------------
def on_rom(**changes) -> sf.StartFrame:
    return sample(regions=regions(rom=ROM), **changes)


def boot_region(start) -> tuple[int, object]:
    begin = sf.boot_spans("us")[0][0]
    return begin, dict(start.regions)[begin]


def test_data_travels_as_its_difference_and_the_receivers_rom_rebuilds_it_byte_for_byte():
    whole, diffed = sf.encode(on_rom()), sf.encode(on_rom(), ROM)
    assert len(diffed) < len(whole) - 1000                            # 15,760 bytes of noise no longer travel
    travelled = sf.decode(diffed, SHA)
    _begin, diff = boot_region(travelled)
    assert isinstance(diff, sf.BootDiff) and 0 < len(diff.runs) <= 4  # the header words alone differ
    assert not sf.is_resolved(travelled)
    assert sf.resolve(travelled, ROM).regions == on_rom().regions
    assert sf.decode(whole, SHA).regions == on_rom().regions          # a start sent whole still reads


def test_a_receiver_with_another_rom_or_none_is_told_why_in_a_plain_sentence(tmp_path, no_rom_on_this_machine):
    sf.write_start(tmp_path, on_rom(), ROM)
    with pytest.raises(sf.StartError, match="different Super Mario 64 Usamune ROM"):
        sf.read_start(tmp_path, SHA, rom=OTHER_ROM)
    with pytest.raises(sf.StartError, match="needs your Super Mario 64 Usamune ROM"):
        sf.read_start(tmp_path, SHA)
    install_rom(tmp_path, no_rom_on_this_machine, OTHER_ROM)
    with pytest.raises(sf.StartError, match="different Super Mario 64 Usamune ROM"):
        sf.read_start(tmp_path, SHA)
    install_rom(tmp_path, no_rom_on_this_machine)                     # his own ROM found among the install's
    assert sf.read_start(tmp_path, SHA).regions == on_rom().regions


def test_a_downloaded_start_is_rebuilt_from_his_rom_or_kept_until_he_has_one(tmp_path, monkeypatch,
                                                                             no_rom_on_this_machine):
    clip_bytes = b"clip bytes"
    sha = sf.digest(clip_bytes)
    monkeypatch.setattr("sm64_events.ghost.clip.decode_clip", lambda data: SimpleNamespace(frames=[record()]))
    blob = sf.encode(on_rom(clip_sha256=sha), ROM)
    sf.accept_start_blob(tmp_path, clip_bytes, blob)                  # no ROM yet: kept as it came
    assert sf.start_path(tmp_path, sha, downloaded=True).read_bytes() == blob
    install_rom(tmp_path, no_rom_on_this_machine, OTHER_ROM)
    other = tmp_path / "other"
    with pytest.raises(sf.StartError, match="different Super Mario 64 Usamune ROM"):
        sf.accept_start_blob(other, clip_bytes, blob)
    assert not (other / "starts").exists()                            # nothing written
    assert sf.read_start(tmp_path, sha, downloaded=True, rom=ROM).regions == on_rom().regions


def test_without_his_rom_the_binding_still_checks_every_word_the_start_carries(tmp_path, monkeypatch):
    """The clock and Mario differ from the boot image, so they travel; a ghost whose first record disagrees
    with them is refused even before the .data is rebuilt."""
    clip_bytes = b"clip bytes"
    blob = sf.encode(on_rom(clip_sha256=sf.digest(clip_bytes)), ROM)
    timer = struct.unpack(">I", sf.boot_image(ROM, US.global_timer & 0x1FFFFFFF, 4))[0]
    assert timer != 3489                                              # the noise does not hold the start's clock
    for change, words in (({"timer": 3490}, "does not begin where"), ({"action": 7}, "Mario is not where")):
        monkeypatch.setattr("sm64_events.ghost.clip.decode_clip", lambda data, c=change: SimpleNamespace(frames=[record(**c)]))
        with pytest.raises(sf.StartError, match=words):
            sf.accept_start_blob(tmp_path, clip_bytes, blob)
    lying = sf.decode(sf.encode(sample(regions=regions(timer=1, rom=ROM)), ROM))
    assert not sf.is_resolved(lying)
    with pytest.raises(sf.StartError, match="disagrees"):
        sf.bind(lying, record())                                      # a carried word that contradicts its header


def test_his_older_whole_start_travels_as_a_difference_once_this_install_has_its_rom(tmp_path, no_rom_on_this_machine):
    path = sf.start_path(tmp_path, SHA)
    path.parent.mkdir(parents=True)
    path.write_bytes(FIXTURES.joinpath("start-v2.bin").read_bytes())  # made by the version-2 encoder (zeros)
    assert struct.unpack_from("<I", path.read_bytes(), 8)[0] == 2
    v2 = sf.read_start(tmp_path, SHA)                                 # version 2 still reads
    assert (v2.level, v2.area, v2.global_timer) == (24, 1, 3489)
    whole = sf.decode(sf.start_blob(tmp_path, SHA), SHA)
    assert sf.is_resolved(whole)                                      # no ROM here: it travels whole
    rom = bytearray(ROM)                                              # a ROM named by the start's rom_sha256
    path.write_bytes(sf.encode(sample(regions=regions(rom=ROM), pool_base=None, pool_runs=[],
                                      rom_sha256=sf.digest(bytes(rom)))))
    install_rom(tmp_path, no_rom_on_this_machine, bytes(rom))
    travelled = sf.decode(sf.start_blob(tmp_path, SHA), SHA)
    assert not sf.is_resolved(travelled)
    assert sf.resolve(travelled, ROM).regions == regions(rom=ROM)


def _rewritten(blob: bytes, change) -> bytes:
    _magic, version, length = struct.unpack_from("<8sII", blob)
    meta = json.loads(blob[16:16 + length])
    change(meta)
    text = json.dumps(meta).encode()
    return struct.pack("<8sII", sf.MAGIC, version, len(text)) + text + blob[16 + length:]


@pytest.mark.parametrize("change, words", [
    (lambda m: m["boot"][0].update(begin=sf.allowed_spans("us")[3][0]), "outside the parts"),   # bss as a diff
    (lambda m: m["boot"].append(dict(m["boot"][0])), "outside the parts"),                      # twice
    (lambda m: m["boot"][0].update(runs=sf.MAX_BOOT_RUNS + 1), "more than any real start"),
    (lambda m: m["boot"][0].update(base_sha256="zz"), "not a sha256"),
    (lambda m: m["boot"][0].update(extra=1), "incomplete"),
    (lambda m: m.update(boot={}), "incomplete"),
    (lambda m: m.pop("boot"), "incomplete"),
    (lambda m: m["boot"][0].update(runs=m["boot"][0]["runs"] + 1), "outside its part|cut short|does not name")])
def test_a_hostile_difference_is_refused(change, words):
    with pytest.raises(sf.StartError, match=words):
        sf.decode(_rewritten(sf.encode(on_rom(), ROM), change))


@pytest.mark.parametrize("runs, words", [(((16, b"\1" * 4), (8, b"\1" * 4)), "out of order"),
                                         (((15_756, b"\1" * 8),), "outside its part"), (((0, b""),), "outside its part")])
def test_runs_outside_the_region_or_out_of_order_are_refused(runs, words):
    begin, size = sf.boot_spans("us")[0]
    start = on_rom()
    start.regions = [(b, sf.BootDiff(size, "12" * 32, runs) if b == begin else d) for b, d in start.regions]
    with pytest.raises(sf.StartError, match=words):
        sf.encode(start)


def test_a_version_2_header_naming_differences_is_refused():
    blob = bytearray(_rewritten(sf.encode(on_rom(), ROM), lambda m: m.update(version=2)))
    struct.pack_into("<I", blob, 8, 2)
    with pytest.raises(sf.StartError, match="incomplete"):
        sf.decode(bytes(blob))


def fixture_v3() -> bytes:
    """The start the Worker's tests send (cloud/community/test/starts/start-v3.bin): this encoder's output for
    the stand-in ROM. Regenerate it with this function when the format changes."""
    return sf.encode(sample(regions=regions(rom=ROM), pool_base=None, pool_runs=[], made_utc="2026-11-09T00:00:00+00:00"),
                     ROM)


def test_the_worker_test_starts_are_what_this_trainer_reads():
    """The Worker's tests (ghost-start.test.js) send these two files; both must be real starts of each version."""
    v3 = FIXTURES.joinpath("start-v3.bin").read_bytes()
    assert struct.unpack_from("<I", v3, 8)[0] == 3
    _m, _v, length = struct.unpack_from("<8sII", v3)
    _m, _v, fresh = struct.unpack_from("<8sII", fixture_v3())
    assert json.loads(v3[16:16 + length]) == json.loads(fixture_v3()[16:16 + fresh])   # the header this encoder writes
    assert sf.resolve(sf.decode(v3, SHA), ROM).regions == regions(rom=ROM)
    assert sf.decode(FIXTURES.joinpath("start-v2.bin").read_bytes(), SHA).regions == regions()


def test_the_workers_span_table_is_this_trainers():
    """The Worker refuses a start whose regions are not these (cloud/community/src/start-spans.json). Every game
    version this Trainer can make starts for is there and equal; one it cannot make yet may be carried ahead
    (JP, from claude/lab-p15, so one Worker deploy takes both) and is checked here once its layout rows land.
    Update that file from allowed_spans() and boot_spans()."""
    table = json.loads((FIXTURES.parents[1] / "src" / "start-spans.json").read_text(encoding="utf-8"))
    versions = {k: v for k, v in table.items() if not k.startswith("_")}
    assert set(versions) <= set(sf.GAME_VERSIONS)
    made = 0
    for version in sf.GAME_VERSIONS:
        try:
            expected = {"regions": [list(span) for span in sf.allowed_spans(version)],
                        "boot": [begin for begin, _size in sf.boot_spans(version)]}
        except sf.StartError:
            continue                                                  # not made here yet: may be carried ahead
        made += 1
        assert versions.get(version) == expected, version
    assert made >= 1
    for version, spans in versions.items():                           # every row, carried ahead or not, is sane
        ends = [begin + size for begin, size in spans["regions"]]
        assert all(size > 0 for _begin, size in spans["regions"]) and len(spans["regions"]) == len(sf.SPAN_ROWS)
        assert all(ends[i] <= spans["regions"][i + 1][0] for i in range(len(ends) - 1)), version
        boot = dict(map(tuple, spans["regions"]))
        assert len(spans["boot"]) == len(sf.BOOT_ROWS) and all(
            b in boot and sf.BOOT_RAM <= b and b + boot[b] <= sf.BOOT_RAM + sf.BOOT_BYTES for b in spans["boot"]), version
    assert table["_versions"] == list(sf.READS) and table["_max_boot_runs"] == sf.MAX_BOOT_RUNS
    assert table["_max_meta_bytes"] == sf.MAX_META_BYTES


@pytest.mark.parametrize("regions_meta", [
    [[0x246000, 0x100]],                                              # the game's code
    [[begin, size] for begin, size in sf.allowed_spans("us")][:-1],    # one span short
    [[begin, size + 4] for begin, size in sf.allowed_spans("us")]])    # spilling past a span
def test_regions_outside_the_spans_a_start_may_hold_are_refused(regions_meta):
    with pytest.raises(sf.StartError, match="outside the parts"):
        sf.decode(_blob_with(b"", {"regions": regions_meta}))
    with pytest.raises(sf.StartError, match="outside the parts"):
        sf.encode(sample(regions=[(begin, bytes(size)) for begin, size in regions_meta]))


@pytest.mark.parametrize("runs", [[(0x246000, b"\0" * 4)], [(0x1C0FF0, b"\0")], [(0x6C000, b"\1"), (0x5C010, b"\1")]])
def test_pool_changes_outside_the_main_pool_or_out_of_order_are_refused(runs):
    with pytest.raises(sf.StartError, match="outside the main pool"):
        sf.encode(sample(pool_runs=runs))


def test_an_unknown_game_version_is_refused():
    with pytest.raises(sf.StartError, match="game version"):
        sf.encode(sample(game_version="pal"))


def test_a_name_is_a_sha256():
    with pytest.raises(sf.StartError):
        sf.start_path(".", "../escape")


def test_removing_a_ghost_removes_both_its_starts(tmp_path):
    sf.write_start(tmp_path, sample())
    sf.start_path(tmp_path, SHA, downloaded=True).parent.mkdir(parents=True)
    sf.start_path(tmp_path, SHA, downloaded=True).write_bytes(sf.encode(sample()))
    sf.remove_start(tmp_path, SHA)
    assert sf.read_start(tmp_path, SHA) is None and sf.read_start(tmp_path, SHA, downloaded=True) is None
