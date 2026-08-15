"""Which ROM is loaded: the cartridge header's country byte names it."""
from sm64_events.memory.version_probe import (detect_version, normalise_header,
                                              version_from_header)


def _header(country: bytes) -> bytes:
    raw = bytearray(0x40)
    raw[0:4] = b"\x80\x37\x12\x40"
    raw[0x20:0x2E] = b"SUPER MARIO 64".ljust(14)
    raw[0x3E:0x3F] = country
    return bytes(raw)


def _word_swapped(header: bytes) -> bytes:
    return b"".join(header[at:at + 4][::-1] for at in range(0, len(header), 4))


def test_country_byte_names_the_version():
    assert version_from_header(_header(b"E")) == "us"
    assert version_from_header(_header(b"J")) == "jp"
    assert version_from_header(_header(b"P")) is None      # PAL: not ours
    assert version_from_header(b"\0" * 0x40) is None
    assert version_from_header(_header(b"E")[:0x30]) is None


def test_a_word_swapped_image_normalises_back():
    header = _header(b"J")
    assert normalise_header(_word_swapped(header)) == header
    assert normalise_header(header) == header
    assert normalise_header(b"junk" * 16) is None


def test_detect_reads_the_header_off_the_memory():
    class Mem:
        def rom_header(self):
            return _header(b"J")
    assert detect_version(Mem()) == "jp"

    class NoRom:
        def rom_header(self):
            return None
    assert detect_version(NoRom()) is None
