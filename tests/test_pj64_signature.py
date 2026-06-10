# tests/test_pj64_signature.py
from sm64_events.memory import addresses as A
from sm64_events.memory.pj64 import looks_like_rdram


def fake_reader(values: dict):
    return lambda addr: values.get(addr, 0)


def test_accepts_n64_boot_config():
    reader = fake_reader({A.OS_ROM_BASE: 0xB0000000,
                          A.OS_MEM_SIZE: 0x400000,
                          A.OS_TV_TYPE: 1})
    assert looks_like_rdram(reader) is True


def test_accepts_expansion_pak_size():
    reader = fake_reader({A.OS_ROM_BASE: 0xB0000000,
                          A.OS_MEM_SIZE: 0x800000,
                          A.OS_TV_TYPE: 0})
    assert looks_like_rdram(reader) is True


def test_rejects_wrong_rom_base():
    reader = fake_reader({A.OS_ROM_BASE: 0x12345678,
                          A.OS_MEM_SIZE: 0x400000,
                          A.OS_TV_TYPE: 1})
    assert looks_like_rdram(reader) is False


def test_rejects_garbage_tv_type():
    reader = fake_reader({A.OS_ROM_BASE: 0xB0000000,
                          A.OS_MEM_SIZE: 0x400000,
                          A.OS_TV_TYPE: 7})
    assert looks_like_rdram(reader) is False
