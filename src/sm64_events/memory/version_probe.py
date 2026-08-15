"""WHICH ROM is loaded — read off the cartridge header PJ64 holds in memory.

An N64 ROM begins with a 0x40-byte header: the PI-domain magic `80 37 12 40`,
the internal name at +0x20 ("SUPER MARIO 64"), and the country code at
+0x3E — `E` for the US release, `J` for Japan. PJ64 keeps the whole ROM image
in its own process (it is not in RDRAM), so `Pj64Memory.rom_header()` finds
it by that magic and this module reads the byte.

TWO BYTE ORDERS ARE ACCEPTED, and which one PJ64 1.6 uses is a LIVE-GATE
ITEM (`sync/address_gates.py::version.rom`, part of the US baseline run):
the ROM as shipped is big-endian (`80 37 12 40`), and PJ64 stores RDRAM as
little-endian 32-bit words (`memory/base.py`) — if it stores the ROM the same
way, the magic reads `40 12 37 80` and every word is reversed. Neither has
been observed on this machine yet, so both are handled and the first run
settles it; nothing here is asserted from reasoning alone.

This is the emulator-side `detected` argument that
`core/modes.py::effective_version` (feature/game-version) left open. The
sync runner also uses it to REFUSE running JP gates against a US ROM — a
wrong-version run would write garbage into the sync report.
"""
ROM_MAGIC_BE = b"\x80\x37\x12\x40"
ROM_MAGIC_WORD_SWAPPED = ROM_MAGIC_BE[::-1]      # 40 12 37 80
HEADER_SIZE = 0x40
INTERNAL_NAME = b"SUPER MARIO 64"
_COUNTRY = {0x45: "us", 0x4A: "jp"}              # 'E', 'J'


def normalise_header(raw: bytes) -> bytes | None:
    """The first 0x40 bytes of a ROM image in N64 (big-endian) byte order,
    whichever order the emulator stored it in; None if it is not a header."""
    if len(raw) < HEADER_SIZE:
        return None
    if raw[:4] == ROM_MAGIC_BE:
        return bytes(raw[:HEADER_SIZE])
    if raw[:4] == ROM_MAGIC_WORD_SWAPPED:
        return b"".join(raw[at:at + 4][::-1] for at in range(0, HEADER_SIZE, 4))
    return None


def version_from_header(raw: bytes) -> str | None:
    header = normalise_header(raw)
    if header is None or header[0x20:0x20 + len(INTERNAL_NAME)] != INTERNAL_NAME:
        return None
    return _COUNTRY.get(header[0x3E])


def detect_version(mem) -> str | None:
    """`"us"` / `"jp"` for the ROM the attached emulator has loaded, None when
    no SM64 header is found (not attached, no ROM, or a different game)."""
    header = mem.rom_header()
    if header is None:
        return None
    return version_from_header(header)
