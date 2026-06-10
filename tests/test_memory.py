# tests/test_memory.py
import pytest

from sm64_events.memory.buffer import BufferMemory


@pytest.fixture
def mem():
    return BufferMemory()


def test_u32_roundtrip(mem):
    mem.write_u32(0x80001000, 0x11223344)
    assert mem.read_u32(0x80001000) == 0x11223344


def test_bytes_within_word_are_big_endian_as_n64_sees_them(mem):
    mem.write_u32(0x80001000, 0x11223344)
    assert mem.read_u8(0x80001000) == 0x11
    assert mem.read_u8(0x80001001) == 0x22
    assert mem.read_u8(0x80001002) == 0x33
    assert mem.read_u8(0x80001003) == 0x44


def test_u16_halves_of_word(mem):
    mem.write_u32(0x80001000, 0x11223344)
    assert mem.read_u16(0x80001000) == 0x1122
    assert mem.read_u16(0x80001002) == 0x3344


def test_u8_roundtrip_at_odd_address(mem):
    mem.write_u8(0x80002001, 0xAB)
    assert mem.read_u8(0x80002001) == 0xAB


def test_signed_reads(mem):
    mem.write_u8(0x80003000, 0xFF)
    assert mem.read_s8(0x80003000) == -1
    mem.write_u16(0x80003002, 0x8000)
    assert mem.read_s16(0x80003002) == -32768
    mem.write_u16(0x80003004, 0x0042)
    assert mem.read_s16(0x80003004) == 0x42
