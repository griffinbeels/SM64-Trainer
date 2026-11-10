"""The E2 evaluator's native Unicorn hooks (tools/scene_lab/cpu_hooks.c) held to their Python reference.

cpu_hooks.decode_access and mark_access are the semantics e2_georender's Python hooks run. The DLL
must decode every opcode and mark every byte the same, clear exactly what it marked, call back into
Python only for a block without a passed ROM check, and log obj_is_in_view decisions in order.
Unicorn is not needed: the hooks read registers through a function pointer, faked here.
"""
import ctypes
import importlib.util
import random
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scene_cpu_hooks", ROOT / "tools/scene_lab/cpu_hooks.py")
hooks = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = hooks
spec.loader.exec_module(hooks)

RDRAM = hooks.RDRAM_BYTES
REG_READ = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p)
SENTINEL = 0x81000000
A0, V0 = 104, 105   # register ids the culling hooks are told to read (any distinct numbers)


@pytest.fixture(scope="module")
def library(tmp_path_factory):
    if sys.platform != "win32":
        pytest.skip("Win32 native component")
    path = hooks.build(tmp_path_factory.mktemp("scene-cpu-hooks"))
    assert path, "x64 MSVC toolchain is required for the evaluator's native hooks"
    return hooks.load(path)


class Cpu:
    """A scene_cpu over fresh buffers, its registers in a dict, its misses in a list."""

    def __init__(self, library, verify=lambda address, size: True, culling=8):
        self.library, self.ram = library, bytearray(RDRAM)
        self.masks = {name: bytearray(RDRAM) for name in ("first", "read", "wrote", "code")}
        self.verified, self.dirty = bytearray(RDRAM // 4), bytearray(RDRAM // hooks.PAGE_BYTES)
        self.registers, self.misses = {}, []
        self.culling = (ctypes.c_uint32 * (2 * culling))()
        native = self.native = hooks.NativeCpu()
        native.ram = hooks.address(self.ram)
        for name, buffer in self.masks.items():
            setattr(native, name, hooks.address(buffer))
        native.verified, native.dirty = hooks.address(self.verified), hooks.address(self.dirty)
        self._reg_read = REG_READ(self._read)
        self._miss = hooks.MISS(lambda uc, address, size, user: self._on_miss(verify, address, size))
        native.reg_read = ctypes.cast(self._reg_read, ctypes.c_void_p).value
        native.miss = ctypes.cast(self._miss, ctypes.c_void_p).value
        native.gpr[:] = list(range(32))
        native.sentinel = SENTINEL
        native.culling, native.culling_capacity = ctypes.addressof(self.culling), culling
        native.reg_a0, native.reg_v0 = A0, V0

    def _read(self, _uc, register, value):
        ctypes.c_uint64.from_address(value).value = self.registers.get(register, 0)
        return 0

    def _on_miss(self, verify, address, size):
        self.misses.append(address)
        if verify(address, size):
            self.verified[(address & 0x1FFFFFFF) >> 2] = 1
        else:
            self.native.stop = 1

    def call(self, name, address, size=4):
        getattr(self.library, name)(None, address, size, ctypes.byref(self.native))

    def mask(self, name):
        return np.frombuffer(self.masks[name], np.uint8)


def _accesses(rng: random.Random, count: int):
    """(instruction word, base register value): every load and store, bases in RDRAM, at its very
    end, on the stack page outside it, and sign-extended as the 64-bit CPU holds them."""
    opcodes = sorted(hooks.ACCESS)
    for _ in range(count):
        regime = rng.randrange(4)
        if regime == 0:
            base = 0x80000000 | rng.randrange(RDRAM)
        elif regime == 1:
            base = 0x80000000 | (RDRAM - rng.randrange(1, 24))
        elif regime == 2:
            base = 0x810FF000 | rng.randrange(0x1000)
        else:
            base = 0xFFFFFFFF80000000 | rng.randrange(RDRAM)
        word = (rng.choice(opcodes) << 26) | (rng.randrange(32) << 21) | (rng.randrange(32) << 16) \
            | rng.randrange(0x10000)
        yield word, base


def test_every_opcode_decodes_like_the_reference(library):
    out = (ctypes.c_int32 * 5)()
    kinds = {value: name for name, value in hooks.KINDS.items()}
    for opcode in range(64):
        for low in (0x0000, 0x7FFC, 0x8000, 0xFFF8):
            word = (opcode << 26) | (29 << 21) | (8 << 16) | low
            native = tuple(out[:4]) + (kinds[out[4]],) if library.scene_decode(word, out) else None
            assert native == hooks.decode_access(word), hex(word)


def test_every_access_marks_the_same_bytes_as_the_reference(library):
    cpu = Cpu(library)
    first, read, wrote = bytearray(RDRAM), bytearray(RDRAM), bytearray(RDRAM)
    stack = 0
    for word, base in _accesses(random.Random(64), 4000):
        library.scene_mark(ctypes.byref(cpu.native), word, base)
        stack += hooks.mark_access(hooks.decode_access(word), base, first, read, wrote)
    assert stack and cpu.native.stack_accesses == stack
    for name, reference in (("first", first), ("read", read), ("wrote", wrote)):
        assert cpu.masks[name] == reference, name
    marked = (cpu.mask("first") | cpu.mask("read") | cpu.mask("wrote")).reshape(-1, hooks.PAGE_BYTES).any(axis=1)
    assert np.array_equal(marked, np.frombuffer(cpu.dirty, np.uint8).astype(bool))   # every mark, and only those


def test_a_clear_empties_what_the_hooks_marked(library):
    cpu = Cpu(library)
    for word, base in _accesses(random.Random(65), 200):
        library.scene_mark(ctypes.byref(cpu.native), word, base)
    cpu.call("scene_on_block", 0x80001000, 64)
    library.scene_clear(ctypes.byref(cpu.native))
    assert not any(cpu.mask(name).any() for name in cpu.masks)
    assert not any(cpu.dirty) and cpu.native.instructions == cpu.native.stack_accesses == 0


def test_an_access_hook_reads_its_instruction_and_base_register(library):
    cpu = Cpu(library)
    store = (0x2B << 26) | (29 << 21) | (8 << 16) | 0xFFF8          # sw $t0, -8($sp)
    struct.pack_into(">II", cpu.ram, 0x1000, store, 0x24080001)     # then addiu $t0, $zero, 1
    cpu.registers[29] = 0xFFFFFFFF80200010
    cpu.call("scene_on_access", 0x80001000)
    cpu.call("scene_on_access", 0x80001004)                         # not a load or store
    assert np.flatnonzero(cpu.mask("wrote")).tolist() == [0x200008, 0x200009, 0x20000A, 0x20000B]
    assert set(cpu.mask("first")[0x200008:0x20000C]) == {2} and not cpu.mask("read").any()


def test_a_block_calls_python_until_it_passes_and_a_refused_one_marks_nothing(library):
    cpu = Cpu(library, verify=lambda address, size: address != 0x80003000)
    for address in (0x80001000, 0x80001000, 0x80002000, 0x80003000, SENTINEL, 0x80001000):
        cpu.call("scene_on_block", address, 16)
    assert cpu.misses == [0x80001000, 0x80002000, 0x80003000]
    assert cpu.native.instructions == 5 * 4                          # every block but the sentinel
    code = cpu.mask("code")
    assert code[0x1000:0x1010].all() and code[0x2000:0x2010].all() and not code[0x3000:0x3010].any()
    assert int(code.sum()) == 32


def test_the_culling_log_keeps_each_decision_in_order_and_counts_the_overflow(library):
    cpu = Cpu(library, culling=2)
    for node, visible in ((0x8033D488, 1), (0x8033D6E8, 0), (0x8033D948, 1)):
        cpu.registers[A0] = 0xFFFFFFFF00000000 | node
        cpu.call("scene_on_view_entry", 0x8027B6C0)
        cpu.registers[V0] = visible
        cpu.call("scene_on_view_return", 0x8027D0A8)
    assert list(cpu.culling) == [0x8033D488, 1, 0x8033D6E8, 0]
    assert cpu.native.culling_count == 3
    library.scene_clear(ctypes.byref(cpu.native))
    assert cpu.native.culling_count == cpu.native.pending_view == 0
