"""THE PRACTICE ROM RULE, once in Python and once in native code, must agree.

The trainer server decides with core/onboarding.py (identify_rom +
is_practice_rom); the capture wrapper and the renderer overlay decide with
plugin/gfxwrap/practice_rom.h, because they must decide with no server running.
Any disagreement is a real run that records, or practice that silently runs as
plain GLideN64. His ruling, 2026-09-16: vanilla SM64, another SM64 ROM and every
other game run "identically to the baseline graphics plugin we forked from".
"""
import importlib.util
import subprocess
from pathlib import Path

import pytest

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.core.onboarding import identify_rom, is_practice_rom

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "plugin" / "gfxwrap"


def big_endian(name: bytes, country: int, magic: bytes = b"\x80\x37\x12\x40") -> bytes:
    header = bytearray(0x40)
    header[0:4] = magic
    header[0x20:0x34] = name.ljust(20, b" ")[:20]
    header[0x3E] = country
    return bytes(header)


def word_swapped(header: bytes) -> bytes:
    return b"".join(header[at:at + 4][::-1] for at in range(0, 0x40, 4))


def byte_swapped(header: bytes) -> bytes:  # .v64 order: supported by neither side
    return b"".join(header[at:at + 2][::-1] for at in range(0, 0x40, 2))


NAMES = [
    b"SM64 USAMUNE v1.93u", b"SM64 USAMUNE v1.92u", b"SM64 USAMUNE v1.93j", b"SM64 USAMUNE",
    b"SM64 USAMUNE v1.93u\0", b"SM64 USAMUNE v1.93\0u", b"SM64 USAMUNEv1.93u", b"sm64 usamune v1.93u",
    b"SUPER MARIO 64", b"THE LEGEND OF ZELDA", b"", b"\xffSM64 USAMUNE v1.93",
]
COUNTRIES = [ord("E"), ord("J"), ord("P"), 0]


def headers():
    cases = []
    for name in NAMES:
        for country in COUNTRIES:
            header = big_endian(name, country)
            cases += [header, word_swapped(header), byte_swapped(header)]
    cases += [bytes(0x40), b"\xff" * 0x40, big_endian(b"SM64 USAMUNE v1.93u", ord("E"), b"\x00\x00\x00\x00")]
    return cases


@pytest.fixture(scope="module")
def host(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("practice_build", ROOT / "tools/build_plugin.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    vcvars = build.find_vcvars32()
    if vcvars is None:
        pytest.skip("MSVC unavailable")
    out = tmp_path_factory.mktemp("practice_rom")
    target = out / "practice_rom_host.exe"
    build._cl(vcvars, build.COMMON_FLAGS + [str(NATIVE / "practice_rom_host.c"), f"/Fe:{target}",
                                            f"/Fo{out}\\"], out)
    return target


def test_native_and_python_agree_on_every_header(host):
    cases = headers()
    result = subprocess.run([str(host)], input="".join(h.hex() + "\n" for h in cases),
                            capture_output=True, text=True, timeout=15, **quiet_spawn_kwargs())
    assert result.returncode == 0, result.stderr
    native = [int(line) for line in result.stdout.split()]
    python = [int(is_practice_rom(identify_rom(h))) for h in cases]
    disagreements = [(h.hex(), n, p) for h, n, p in zip(cases, native, python, strict=True) if n != p]
    assert not disagreements
    # The corpus exercises both answers, in both stored byte orders.
    assert python.count(1) >= 4 and python.count(0) > python.count(1)


def test_the_rule_names_usamune_only():
    usamune_us = big_endian(b"SM64 USAMUNE v1.93u", ord("E"))
    usamune_jp = big_endian(b"SM64 USAMUNE v1.93j", ord("J"))
    vanilla_us = big_endian(b"SUPER MARIO 64", ord("E"))
    other_game = big_endian(b"THE LEGEND OF ZELDA", ord("E"))
    assert is_practice_rom(identify_rom(word_swapped(usamune_us)))
    assert is_practice_rom(identify_rom(usamune_jp))
    assert not is_practice_rom(identify_rom(vanilla_us))
    assert not is_practice_rom(identify_rom(big_endian(b"SM64 USAMUNE v1.92u", ord("E"))))
    assert not is_practice_rom(identify_rom(other_game))
    assert not is_practice_rom(identify_rom(None))
