"""WHICH ROM is loaded — read off the cartridge header PJ64 holds in memory.

An N64 ROM begins with a 0x40-byte header: the PI-domain magic `80 37 12 40`,
the internal name at +0x20 ("SUPER MARIO 64"), and the country code at
+0x3E — `E` for the US release, `J` for Japan. PJ64 keeps the whole ROM image
in its own process (it is not in RDRAM), so `Pj64Memory.rom_header()` finds
it by that magic and this module reads the byte.

TWO BYTE ORDERS ARE ACCEPTED, and PJ64 1.6 on this machine is now OBSERVED
(2026-08-23, read live at the start of an 8 MB committed region): it stores
the ROM word-swapped — the magic reads `40 12 37 80` and every 32-bit word
is reversed, matching how it stores RDRAM (`memory/base.py`). The big-endian
order stays accepted for an emulator that stores the file as shipped.

THE NAME FIELD IS THE PRACTICE ROM'S OWN, not vanilla's. The Usamune ROM
writes `SM64 USAMUNE v1.93u` at +0x20 where vanilla writes `SUPER MARIO
64` — read live off his ROM 2026-08-23, after the vanilla-only check made
`detect_version` return None on the exact ROM this project reads, which
skipped every sync gate needing `version.rom`. Both names are accepted (the
Usamune one by prefix, so a version bump keeps matching) and the country
byte — `E` on that same live header — still names the version either way.

This is the emulator-side `detected` argument that
`core/modes.py::effective_version` (feature/game-version) left open. The
sync runner also uses it to REFUSE running JP gates against a US ROM — a
wrong-version run would write garbage into the sync report.
"""
ROM_MAGIC_BE = b"\x80\x37\x12\x40"
ROM_MAGIC_WORD_SWAPPED = ROM_MAGIC_BE[::-1]      # 40 12 37 80
HEADER_SIZE = 0x40
INTERNAL_NAME = b"SUPER MARIO 64"
USAMUNE_NAME_PREFIX = b"SM64 USAMUNE"
NAME_FIELD = slice(0x20, 0x34)                   # 20 bytes, space-padded
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
    if header is None:
        return None
    name = header[NAME_FIELD]
    if not (name.startswith(INTERNAL_NAME)
            or name.startswith(USAMUNE_NAME_PREFIX)):
        return None
    return _COUNTRY.get(header[0x3E])


def detect_version(mem) -> str | None:
    """`"us"` / `"jp"` for the ROM the attached emulator has loaded, None when
    no SM64 header is found (not attached, no ROM, or a different game)."""
    header = mem.rom_header()
    if header is None:
        return None
    return version_from_header(header)
