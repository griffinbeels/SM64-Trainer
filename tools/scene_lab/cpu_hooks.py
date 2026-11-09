"""The E2 evaluator's Unicorn hooks: reference semantics in Python, the same in native code.

e2_georender.Sandbox decodes every load and store before it executes (Unicorn 2.1.4's MIPS
memory hooks raise a spurious reserved-instruction exception on a delay-slot access) and checks
every executed block against the ROM. As Python callbacks that costs ~0.7 us per instruction,
~190 ms per geo_process_root run; cpu_hooks.c does the per-instruction and per-block work in an
x64 DLL and calls back into Python only for a block that has not passed the ROM check.

ACCESS, decode_access and mark_access are the reference: the Python hooks (no toolchain) use
them, and tests/test_scene_cpu_hooks.py holds the DLL to them opcode by opcode. build() compiles
the DLL with the x64 MSVC toolchain, cached by source hash like closure.py's probe. Nothing here
imports Unicorn: attach() takes the Sandbox's Uc and its binding's library.
"""
from __future__ import annotations

import ctypes
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().with_name("cpu_hooks.c")
RDRAM_BYTES = 8 << 20
PAGE_BYTES = 1 << 12

# MIPS III loads and stores: opcode -> (write, bytes, kind); "left"/"right" are the unaligned halves.
ACCESS = {0x20: (0, 1, ""), 0x21: (0, 2, ""), 0x22: (0, 4, "left"), 0x23: (0, 4, ""), 0x24: (0, 1, ""),
          0x25: (0, 2, ""), 0x26: (0, 4, "right"), 0x27: (0, 4, ""), 0x30: (0, 4, ""), 0x31: (0, 4, ""),
          0x34: (0, 8, ""), 0x35: (0, 8, ""), 0x37: (0, 8, ""), 0x1A: (0, 8, "left"), 0x1B: (0, 8, "right"),
          0x28: (1, 1, ""), 0x29: (1, 2, ""), 0x2A: (1, 4, "left"), 0x2B: (1, 4, ""), 0x2C: (1, 8, "left"),
          0x2D: (1, 8, "right"), 0x2E: (1, 4, "right"), 0x38: (1, 4, ""), 0x39: (1, 4, ""), 0x3C: (1, 8, ""),
          0x3D: (1, 8, ""), 0x3F: (1, 8, "")}
KINDS = {"": 0, "left": 1, "right": 2}


def decode_access(word: int) -> tuple | None:
    """(write, base register, signed offset, bytes, kind) of a load or store, else None."""
    entry = ACCESS.get(word >> 26)
    if entry is None:
        return None
    offset = word & 0xFFFF
    return entry[0], (word >> 21) & 31, offset - 0x10000 if offset & 0x8000 else offset, entry[1], entry[2]


def mark_access(access: tuple, base_value: int, first, read, wrote) -> bool:
    """Mark the RDRAM bytes one decoded access touches at this base register value: first touch
    (1 read, 2 written), and the read or written set. True when it lands outside RDRAM (the stack)."""
    write, _base, offset, size, kind = access
    target = (base_value + offset) & 0x1FFFFFFF
    if kind == "left":
        size -= target & (size - 1)
    elif kind == "right":
        size, target = (target & (size - 1)) + 1, target & ~(size - 1)
    if target >= RDRAM_BYTES:
        return True
    end = min(target + size, RDRAM_BYTES)
    mark = 2 if write else 1
    for at in range(target, end):
        if not first[at]:
            first[at] = mark
    (wrote if write else read)[target:end] = b"\x01" * (end - target)
    return False


class NativeCpu(ctypes.Structure):
    """cpu_hooks.c's scene_cpu: the buffers the hooks write and the counters they keep."""
    _fields_ = [("ram", ctypes.c_void_p), ("first", ctypes.c_void_p), ("read", ctypes.c_void_p),
                ("wrote", ctypes.c_void_p), ("code", ctypes.c_void_p), ("verified", ctypes.c_void_p),
                ("dirty", ctypes.c_void_p), ("reg_read", ctypes.c_void_p), ("miss", ctypes.c_void_p),
                ("gpr", ctypes.c_int32 * 32), ("sentinel", ctypes.c_uint32), ("stop", ctypes.c_int32),
                ("instructions", ctypes.c_uint64), ("stack_accesses", ctypes.c_uint64),
                ("culling", ctypes.c_void_p), ("culling_capacity", ctypes.c_uint32),
                ("culling_count", ctypes.c_uint32), ("pending_view", ctypes.c_uint32),
                ("reg_a0", ctypes.c_int32), ("reg_v0", ctypes.c_int32)]


CULLING_CAPACITY = 1 << 16   # obj_is_in_view decisions one run may log (a frame makes a few hundred)


MISS = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.c_void_p)


def address(buffer) -> int:
    """The address of a bytearray's (or writable buffer's) first byte."""
    return ctypes.addressof((ctypes.c_char * len(buffer)).from_buffer(buffer))


def build(work: Path | None = None) -> Path | None:
    """The hooks DLL; None without it, so the Python hooks run. The installed app runs its own copy
    (core/paths.py bundled_lab_file) and never compiles; a checkout compiles the one recipe
    (tools/build_lab.py) once per build id into the scene lab's build cache, or into
    `work` when given, and has none without an x64 MSVC toolchain."""
    import sys
    if getattr(sys, "frozen", False):
        from sm64_events.core.paths import bundled_lab_file
        return bundled_lab_file("scene_cpu_hooks.dll")
    spec = importlib.util.spec_from_file_location("scene_lab_build", ROOT / "tools/build_lab.py")
    recipes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(recipes)
    try:
        return recipes.cached("scene_cpu_hooks.dll", work)
    except recipes.ToolchainMissing:
        return None


_LIBRARY: list = []


def load(path: Path) -> ctypes.CDLL:
    """The hooks DLL with its exports' prototypes (the hooks themselves are called by Unicorn)."""
    dll = ctypes.CDLL(str(path))
    hook = (ctypes.c_void_p, ctypes.c_uint64, ctypes.c_uint32, ctypes.POINTER(NativeCpu))
    for name in ("scene_on_block", "scene_on_access", "scene_on_view_entry", "scene_on_view_return"):
        getattr(dll, name).argtypes, getattr(dll, name).restype = hook, None
    dll.scene_decode.argtypes = (ctypes.c_uint32, ctypes.POINTER(ctypes.c_int32))
    dll.scene_mark.argtypes, dll.scene_mark.restype = (ctypes.POINTER(NativeCpu), ctypes.c_uint32, ctypes.c_uint64), None
    dll.scene_clear.argtypes, dll.scene_clear.restype = (ctypes.POINTER(NativeCpu),), None
    return dll


def library() -> ctypes.CDLL | None:
    """The loaded hooks DLL (built on first use), or None without the toolchain."""
    if not _LIBRARY:
        path = build()
        _LIBRARY.append(load(path) if path else None)
    return _LIBRARY[0]


def attach(uc, uclib, hook_type: int, function: str, cpu: NativeCpu, begin: int = 1, end: int = 0) -> int:
    """Register one native hook with `cpu` as its user data: over every address (begin 1 > end 0)
    or over [begin, end]."""
    handle = ctypes.c_size_t()
    target = ctypes.cast(getattr(library(), function), ctypes.c_void_p)
    status = uclib.uc_hook_add(uc._uch, ctypes.byref(handle), hook_type, target, ctypes.c_void_p(ctypes.addressof(cpu)),
                               begin, end)
    if status:
        raise RuntimeError(f"uc_hook_add({function}) failed: {status}")
    return handle.value
