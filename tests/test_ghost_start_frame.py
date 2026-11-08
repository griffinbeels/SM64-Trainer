"""A ghost's start frame file: what it keeps comes back byte for byte, and what it cannot use it refuses."""
import struct

import pytest

from sm64_events.ghost import start_frame as sf

SHA = "ab" * 32


def sample(**changes) -> sf.StartFrame:
    start = sf.StartFrame(SHA, 3489, 24, 1, 1, [(0x33AF90, bytes(range(256)) * 4), (0x38EEE0, b"\x12\x34\x00\x00")],
                          "v1-tap-x-2419-24-1-1", "cd" * 32, [(0x5C010, b"\x01\x02\x03"), (0x1C0000, b"\xff" * 9)])
    for name, value in changes.items():
        setattr(start, name, value)
    return start


def test_a_start_round_trips(tmp_path):
    written = sf.write_start(tmp_path, sample())
    assert written == tmp_path / "starts" / f"{SHA}.sm64start"
    back = sf.read_start(tmp_path, SHA)
    want = sample()
    assert (back.global_timer, back.level, back.area, back.act) == (3489, 24, 1, 1)
    assert back.regions == want.regions and back.pool_runs == want.pool_runs
    assert (back.pool_base, back.pool_base_sha256) == (want.pool_base, want.pool_base_sha256)


def test_regions_alone_round_trip(tmp_path):
    sf.write_start(tmp_path, sample(pool_base=None, pool_base_sha256=None, pool_runs=[]))
    back = sf.read_start(tmp_path, SHA)
    assert back.pool_base is None and back.pool_runs == [] and len(back.regions) == 2


def test_a_ghost_without_a_start_reads_none(tmp_path):
    assert sf.read_start(tmp_path, SHA) is None


def test_another_version_is_refused(tmp_path):
    data = bytearray(sf.encode(sample()))
    struct.pack_into("<I", data, 8, sf.VERSION + 1)
    with pytest.raises(sf.StartError, match="version"):
        sf.decode(bytes(data))


def test_a_start_named_for_another_ghost_is_refused():
    with pytest.raises(sf.StartError, match="another ghost"):
        sf.decode(sf.encode(sample()), "ef" * 32)


@pytest.mark.parametrize("damage", ["magic", "truncated", "extra"])
def test_damage_is_refused(damage):
    data = sf.encode(sample())
    if damage == "magic":
        data = b"X" + data[1:]
    elif damage == "truncated":
        data = data[:-20]
    else:
        data = data + b"\0"
    with pytest.raises(sf.StartError):
        sf.decode(data)


def test_a_region_outside_rdram_is_refused():
    with pytest.raises(sf.StartError, match="outside"):
        sf.encode(sample(regions=[(8 << 20, b"\0")]))


def test_a_name_is_a_sha256():
    with pytest.raises(sf.StartError):
        sf.start_path(".", "../escape")


def test_removing_a_start(tmp_path):
    sf.write_start(tmp_path, sample())
    sf.remove_start(tmp_path, SHA)
    assert sf.read_start(tmp_path, SHA) is None
