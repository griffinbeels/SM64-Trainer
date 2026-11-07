# tests/test_process_access.py
"""The Trainer opens Project64 with the least access it needs and never asks
for the debug privilege (clean-behavior, 2026-11-06).

pymem's default open enabled SeDebugPrivilege and asked for PROCESS_ALL_ACCESS
on every attach, as password stealers and injectors do. Two guards keep it
gone: a source scan for the ingredients, and a fake kernel32 that records the
access mask of every handle the reader and the slot write open. The rights
below are Microsoft's literal values ("Process Security and Access Rights"),
not the module's constants, so a constant that grows a right turns this red.
tests/test_pj64_rom_header.py proves the same masks are enough against a
real process."""
import ctypes
import re
import subprocess
from pathlib import Path

import pytest
from source_scan import code_only

from sm64_events.memory import addresses as A
from sm64_events.memory import pj64, winprocess
from sm64_events.slotlibrary import usamune

ROOT = Path(__file__).resolve().parents[1]

PROCESS_VM_OPERATION, PROCESS_VM_READ, PROCESS_VM_WRITE = 0x0008, 0x0010, 0x0020
PROCESS_QUERY_INFORMATION, SYNCHRONIZE = 0x0400, 0x00100000
#: Everything a reader may hold: read memory, list its regions, wait for exit.
READER_MAY_HOLD = PROCESS_VM_READ | PROCESS_QUERY_INFORMATION | SYNCHRONIZE


# -- the source scan ----------------------------------------------------------

#: The ingredients of the old attach. A hex all-access mask is the same right
#: under another spelling (0x1F0FFF before Vista, 0x1FFFFF after).
FORBIDDEN = re.compile(r"PROCESS_ALL_ACCESS|SeDebugPrivilege|\b0x1F0FFF\b|\b0x1FFFFF\b", re.I)
#: pymem itself: its Pymem(...) and process.open() default to both.
PYMEM = re.compile(r"\bpymem\b")
#: The one module that reads or writes another process's memory.
MEMORY_OWNER = ROOT / "src/sm64_events/memory/winprocess.py"


def python_files() -> list[Path]:
    return sorted([*(ROOT / "src").rglob("*.py"), *(ROOT / "tools").rglob("*.py")])


def native_files() -> list[Path]:
    tracked = subprocess.run(
        ["git", "ls-files", "--", "*.c", "*.cpp", "*.h", "*.hpp"], cwd=ROOT,
        capture_output=True, text=True, check=True).stdout.split()
    return [ROOT / name for name in tracked if not name.startswith("tests/")]


def violations(path: Path, source: str | None = None) -> list[str]:
    code = code_only(path, source)
    found = [m.group(0) for m in FORBIDDEN.finditer(code)]
    if path.suffix == ".py":
        found += [m.group(0) for m in PYMEM.finditer(code)]
        if path != MEMORY_OWNER and path.is_relative_to(ROOT / "src"):
            found += re.findall(r"\b(?:Read|Write)ProcessMemory\b", code)
    return found


def test_the_scan_reads_code_and_not_the_comment_that_explains_it():
    sample = ROOT / "src/sm64_events/sample.py"
    assert violations(sample, '"""pymem asked for PROCESS_ALL_ACCESS."""\n# SeDebugPrivilege\nx = 1\n') == []
    assert violations(sample, "import pymem\n")
    assert violations(sample, "ACCESS = 0x1f0fff\n")
    assert violations(sample, "k.ReadProcessMemory(h, a, b, n, None)\n")
    native = ROOT / "plugin/sample.cpp"
    assert violations(native, "// SeDebugPrivilege is never enabled\nint x;\n") == []
    assert violations(native, "OpenProcess(PROCESS_ALL_ACCESS, FALSE, pid);\n")


def test_nothing_shipped_or_run_asks_for_all_access_or_the_debug_privilege():
    files = python_files() + native_files()
    assert len(files) > 100, "the scan found almost nothing to read"
    offenders = {str(p.relative_to(ROOT)): found for p in files if (found := violations(p))}
    assert offenders == {}, (
        "Open another process with only the rights each call uses "
        "(memory/winprocess.py): no PROCESS_ALL_ACCESS, no SeDebugPrivilege, no "
        "pymem, and only winprocess.py reads or writes another process's memory. "
        f"Found: {offenders}")


def test_pymem_is_no_longer_a_dependency():
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert not PYMEM.search(project), "pymem ships its all-access, debug-privilege open"


# -- the fake kernel32 ----------------------------------------------------------

PID, RDRAM_BASE = 4242, 0x4E7E0000


class FakeKernel32:
    """Project64 as Windows shows it: every OpenProcess records its mask, and
    a handle may only do what its mask allows (Microsoft's table)."""

    NEEDS = {"read": PROCESS_VM_READ, "write": PROCESS_VM_WRITE | PROCESS_VM_OPERATION,
             "wait": SYNCHRONIZE}

    def __init__(self):
        self.memory = bytearray(A.RDRAM_FULL_SIZE)
        for at, value in ((A.OS_TV_TYPE, 1), (A.OS_ROM_BASE, 0xB0000000), (A.OS_MEM_SIZE, 0x800000)):
            offset = at - A.KSEG0_BASE
            self.memory[offset:offset + 4] = value.to_bytes(4, "little")
        self.opened: list[tuple[int, int, int]] = []  # (handle, pid, access)
        self.open_handles: dict[int, int] = {}
        self.writes: list[int] = []  # the handle each write used

    def _check(self, handle: int, use: str) -> None:
        access = self.open_handles[handle]  # KeyError: a closed or unknown handle
        assert access & self.NEEDS[use] == self.NEEDS[use], f"{use} on a handle opened 0x{access:X}"

    def OpenProcess(self, access, _inherit, pid):
        handle = 0x100 + 4 * len(self.opened)
        self.opened.append((handle, pid, access))
        self.open_handles[handle] = access
        return handle

    def CloseHandle(self, handle):
        del self.open_handles[handle]
        return 1

    def ReadProcessMemory(self, handle, address, buffer, size, _count):
        self._check(handle, "read")
        at = address - RDRAM_BASE
        ctypes.memmove(buffer, bytes(self.memory[at:at + size]), size)
        return 1

    def WriteProcessMemory(self, handle, address, data, size, _count):
        self._check(handle, "write")
        at = address - RDRAM_BASE
        self.memory[at:at + size] = data[:size]
        self.writes.append(handle)
        return 1

    def WaitForSingleObject(self, handle, _ms):
        self._check(handle, "wait")
        return 0x102  # still running


@pytest.fixture
def kernel(monkeypatch):
    fake = FakeKernel32()
    monkeypatch.setattr(winprocess, "_kernel32", fake)
    walked = []

    def regions(handle):
        walked.append(handle)
        return iter([(RDRAM_BASE, A.RDRAM_FULL_SIZE)])

    monkeypatch.setattr(pj64, "iter_committed_regions", regions)
    fake.walked = walked
    return fake


def test_the_reader_opens_project64_read_only(kernel):
    reader = pj64.Pj64Memory()
    assert reader.attach(pid=PID) and reader.alive()
    assert reader.read_u32(A.OS_MEM_SIZE) == 0x800000
    [(handle, pid, access)] = kernel.opened
    assert pid == PID and kernel.walked == [handle]
    assert access & PROCESS_VM_READ, "the reader must be able to read"
    assert access & ~READER_MAY_HOLD == 0, f"the reader asked for more than it uses: 0x{access:X}"
    reader.detach()
    assert kernel.open_handles == {}


def test_a_slot_write_opens_only_write_rights_for_that_write(kernel):
    reader = pj64.Pj64Memory()
    assert reader.attach(pid=PID)
    game = usamune.Project64Game(reader)
    game.write(0x80000100, b"\x01\x02\x03\x04")
    assert game.read(0x80000100, 4) == b"\x01\x02\x03\x04"
    (reader_handle, _, _), (writer, pid, access) = kernel.opened
    assert pid == PID and access == PROCESS_VM_WRITE | PROCESS_VM_OPERATION
    assert kernel.writes == [writer], "the write must not use the reader's handle"
    assert list(kernel.open_handles) == [reader_handle], "the write handle closes after the write"
    game.write(0x80000104, b"\x05\x06\x07\x08")
    assert len(kernel.opened) == 3 and list(kernel.open_handles) == [reader_handle]
    reader.detach()
    assert kernel.open_handles == {}


def test_a_refused_open_leaves_the_reader_detached(kernel, monkeypatch):
    monkeypatch.setattr(kernel, "OpenProcess", lambda *_: None)
    reader = pj64.Pj64Memory()
    assert not reader.attach(pid=PID)
    assert not reader.attached and reader.pid is None
