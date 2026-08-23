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


# His ROM's real header, read live out of PJ64 1.6 on 2026-08-23 (word-
# swapped, as PJ64 stores it): name "SM64 USAMUNE v1.93u", country 'E'.
# The vanilla-only name check returned None on exactly this ROM, which
# skipped every sync gate that needs version.rom.
LIVE_USAMUNE_BE = bytes.fromhex(
    "80 37 12 40 00 00 00 0f 80 24 60 00 00 00 14 44"
    "af d1 3a 9d 86 22 56 8e 00 00 00 00 00 00 00 00"
    "53 4d 36 34 20 55 53 41 4d 55 4e 45 20 76 31 2e"
    "39 33 75 20 00 00 00 00 00 00 00 4e 53 4d 45 00".replace(" ", ""))


def test_the_usamune_header_names_its_version():
    assert version_from_header(_word_swapped(LIVE_USAMUNE_BE)) == "us"
    assert version_from_header(LIVE_USAMUNE_BE) == "us"


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
