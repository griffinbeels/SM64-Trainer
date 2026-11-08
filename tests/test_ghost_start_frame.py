"""A ghost's start frame (src/sm64_events/ghost/start_frame.py): what it keeps comes back byte for byte, and a
start from another machine is refused, with a plain sentence, for every way it can be wrong or hostile."""
import lzma
import struct
from types import SimpleNamespace

import pytest

from sm64_events.ghost import start_frame as sf
from sm64_events.ghost.native_layout import POSE_AT, VALID_POSE
from sm64_events.memory.layout import layout_for

SHA = "ab" * 32
US = layout_for("us")


def regions(timer=3489, counter=2194, level=24, area=1, action=0x1924, position=(2602.0, 1256.0, 5120.0)):
    spans = [(begin, bytearray(size)) for begin, size in sf.allowed_spans("us")]

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
    struct.pack_into("<I", data, 8, 1)
    with pytest.raises(sf.StartError, match="version 1"):
        sf.decode(bytes(data))


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
