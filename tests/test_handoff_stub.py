"""The keep-across-reset stub's decisions, run instruction by instruction with Usamune's routines as fakes.

The recorded proof lives in the simulator (tools/scene_lab/handoff_stub.py) and needs a ROM and a recording.
These tests need neither: a small MIPS loop runs the assembled words, so a wrong branch or offset turns red.
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "scene_lab"))
import handoff_slot as slots  # noqa: E402
import handoff_stub as stub  # noqa: E402

LEVEL, AREA = 9, 1
OLD_MARIO, SLOT_MARIO = 0x803473A8, 0x8034FFC8
INDEX = 3
SP_AT_SITE = 0x80206AF8
RETURN_HERE = 0x802CB250
POS_X, POS_Y, ACTION = 0x11111111, 0x22222222, 0x03000888
TIMER, COUNTER, SEED, CHILDREN, FOCUS = 1000, 2000, 0xBEEF, 0x8034FFC8, 0x8034E0E8
PADS = bytes(range(stub.CONTROLLERS_BYTES))


class Mips:
    """Just the instructions the stub uses, with delay slots. Fake routines stand in at their addresses."""

    def __init__(self):
        self.ram: dict[int, int] = {}
        self.regs = [0] * 32
        self.lo = 0
        self.fakes: dict = {}
        self.calls: list[int] = []

    def u32(self, address: int) -> int:
        return int.from_bytes(bytes(self.ram.get(address + i, 0) for i in range(4)), "big")

    def put(self, address: int, data: bytes) -> None:
        for i, value in enumerate(data):
            self.ram[address + i] = value

    def put_u32(self, address: int, value: int) -> None:
        self.put(address, struct.pack(">I", value & 0xFFFFFFFF))

    def _step(self, word: int, pc: int, next_pc: int) -> int | None:
        """Run one word; returns a branch target when it took one."""
        op, rs, rt = word >> 26, word >> 21 & 31, word >> 16 & 31
        rd, funct, imm = word >> 11 & 31, word & 63, word & 0xFFFF
        simm = imm - 0x10000 if imm & 0x8000 else imm
        r = self.regs
        at = (r[rs] + simm) & 0xFFFFFFFF
        target = None
        if op == 0 and funct == 0x21:
            r[rd] = (r[rs] + r[rt]) & 0xFFFFFFFF
        elif op == 0 and funct == 0x19:
            self.lo = (r[rs] * r[rt]) & 0xFFFFFFFF
        elif op == 0 and funct == 0x12:
            r[rd] = self.lo
        elif op == 0 and funct == 0x08:
            target = r[rs]
        elif op == 0x0F:
            r[rt] = imm << 16
        elif op == 0x0D:
            r[rt] = r[rs] | imm
        elif op == 0x09:
            r[rt] = (r[rs] + simm) & 0xFFFFFFFF
        elif op == 0x0B:
            r[rt] = int(r[rs] < (simm & 0xFFFFFFFF))
        elif op == 0x23:
            r[rt] = self.u32(at)
        elif op == 0x2B:
            self.put_u32(at, r[rt])
        elif op == 0x21:
            value = int.from_bytes(bytes(self.ram.get(at + i, 0) for i in range(2)), "big")
            r[rt] = (value - 0x10000 if value & 0x8000 else value) & 0xFFFFFFFF
        elif op == 0x25:
            r[rt] = int.from_bytes(bytes(self.ram.get(at + i, 0) for i in range(2)), "big")
        elif op == 0x29:
            self.put(at, struct.pack(">H", r[rt] & 0xFFFF))
        elif op == 0x24:
            r[rt] = self.ram.get(at, 0)
        elif op == 0x02:
            target = 0x80000000 | (word & 0x03FFFFFF) << 2
        elif op == 0x03:
            r[31] = next_pc + 4
            target = 0x80000000 | (word & 0x03FFFFFF) << 2
        elif op == 0x04 and r[rs] == r[rt] or op == 0x05 and r[rs] != r[rt]:
            target = (next_pc + (simm << 2)) & 0xFFFFFFFF
        elif op not in (0x04, 0x05) and word != 0:
            raise AssertionError(f"{pc:08x}: {word:08x} is not an instruction the stub should use")
        self.regs[0] = 0
        return target

    def run(self, pc: int, stop: int) -> None:
        for _ in range(5000):
            if pc == stop:
                return
            if pc in self.fakes:
                self.calls.append(pc)
                self.fakes[pc]()
                pc = self.regs[31]
                continue
            word = self.u32(pc)
            target = self._step(word, pc, pc + 4)
            if target is None:
                pc += 4
                continue
            if self._step(self.u32(pc + 4), pc + 4, pc + 8) is not None:
                raise AssertionError("a branch in a delay slot")
            pc = target
        raise AssertionError("the stub never returned")


def game(built: stub.Stub, *, live=OLD_MARIO, slot_mario=SLOT_MARIO, level=LEVEL, area=AREA, record=True) -> Mips:
    """The moment Usamune's dispatcher jumps to the load: a slot saved before a reset, Mario since moved."""
    cpu = Mips()
    for i, word in enumerate(built.words):
        cpu.put_u32(built.base + i * 4, word)
    slot = slots.slot_address(INDEX)
    cpu.put(slots.LOAD_INDEX, bytes([INDEX]))
    cpu.put_u32(slot + slots.TRAILER_MARIO, slot_mario)
    cpu.put(slot + slots.TRAILER_LEVEL, struct.pack(">hh", level, area))
    cpu.put_u32(slot + stub.MARIO_POS_IN_SLOT, POS_X)
    cpu.put_u32(slot + stub.MARIO_POS_IN_SLOT + 4, POS_Y)
    cpu.put_u32(slot + stub.MARIO_ACTION_IN_SLOT, ACTION)
    cpu.put_u32(slots.MARIO_OBJECT, live)
    cpu.put(slots.CURR_LEVEL, struct.pack(">h", LEVEL))
    cpu.put(slots.CURR_AREA, struct.pack(">h", AREA))
    for offset, allowed in stub.CHAIN:
        cpu.put_u32(SP_AT_SITE + offset, allowed[0])
    cpu.put_u32(SP_AT_SITE + stub.FIRST_OBJ, live)
    if record:
        cpu.put(stub.SIDECAR_BASE + INDEX * stub.RECORD_BYTES, stub.record_bytes(
            mario=SLOT_MARIO, pos_x=POS_X, pos_y=POS_Y, action=ACTION, timer=TIMER, area_counter=COUNTER,
            seed=SEED, children=CHILDREN, focus=FOCUS, pads=PADS))
    cpu.regs[29], cpu.regs[31] = SP_AT_SITE, RETURN_HERE

    def usamune_load() -> None:
        """Usamune's own gate, then its copy: only gMarioObject matters here."""
        at_slot = slots.slot_address(cpu.ram[slots.LOAD_INDEX])
        same = (cpu.u32(at_slot + slots.TRAILER_MARIO) == cpu.u32(slots.MARIO_OBJECT)
                and cpu.u32(slots.MARIO_OBJECT) != 0
                and bytes(cpu.ram.get(at_slot + slots.TRAILER_LEVEL + i, 0) for i in range(4))
                == bytes(cpu.ram.get(a + i, 0) for a in (slots.CURR_LEVEL, slots.CURR_AREA) for i in range(2)))
        cpu.loaded = same
        if same:
            cpu.put_u32(slots.MARIO_OBJECT, slot_mario)
            cpu.regs[8:16] = [0xDEAD0000 + i for i in range(8)]      # the routine owns the t registers

    cpu.loaded = False
    cpu.fakes[slots.LOAD] = usamune_load
    cpu.fakes[stub.LOAD_OBJ_WARP_NODES] = lambda: None
    return cpu


def run_load(cpu: Mips, built: stub.Stub) -> None:
    before = [cpu.regs[i] for i in range(16, 24)]
    cpu.run(built.load_entry, RETURN_HERE)
    assert cpu.regs[29] == SP_AT_SITE, "the stack came back"
    assert [cpu.regs[i] for i in range(16, 24)] == before, "the caller's saved registers came back"


def restored(cpu: Mips) -> bool:
    return cpu.u32(slots.GLOBAL_TIMER) == TIMER


def test_a_moved_mario_loads_and_the_object_loop_follows_it():
    built = stub.build()
    cpu = game(built)
    run_load(cpu, built)
    assert cpu.loaded
    assert cpu.u32(SP_AT_SITE + stub.FIRST_OBJ) == SLOT_MARIO
    assert cpu.u32(slots.slot_address(INDEX) + slots.TRAILER_MARIO) == SLOT_MARIO, "the slot's bytes came back"
    assert stub.LOAD_OBJ_WARP_NODES in cpu.calls


def test_a_moved_mario_gets_the_slots_seed_clocks_and_controllers_back():
    built = stub.build()
    cpu = game(built)
    run_load(cpu, built)
    assert restored(cpu)
    assert cpu.u32(slots.AREA_UPDATE_COUNTER) == COUNTER
    assert bytes(cpu.ram[slots.RANDOM_SEED + i] for i in range(2)) == struct.pack(">H", SEED)
    assert cpu.u32(stub.OBJECT_CIRCLE_START) == CHILDREN
    assert cpu.u32(stub.SECOND_CAMERA_FOCUS) == FOCUS
    assert bytes(cpu.ram[stub.CONTROLLERS + i] for i in range(stub.CONTROLLERS_BYTES)) == PADS


def test_the_same_mario_is_a_plain_load_with_nothing_repointed():
    built = stub.build()
    cpu = game(built, live=SLOT_MARIO)
    run_load(cpu, built)
    assert cpu.loaded
    assert cpu.u32(SP_AT_SITE + stub.FIRST_OBJ) == SLOT_MARIO
    assert stub.LOAD_OBJ_WARP_NODES not in cpu.calls
    assert restored(cpu)


@pytest.mark.parametrize("change", [{"level": LEVEL + 1}, {"area": AREA + 1}, {"slot_mario": 0}, {"live": 0}],
                         ids=["wrong level", "wrong area", "empty slot", "no Mario"])
def test_usamunes_refusals_all_stand(change):
    built = stub.build()
    cpu = game(built, **change)
    slot_word = cpu.u32(slots.slot_address(INDEX) + slots.TRAILER_MARIO)
    held = cpu.u32(SP_AT_SITE + stub.FIRST_OBJ)
    run_load(cpu, built)
    assert not cpu.loaded
    assert cpu.calls == [slots.LOAD], "Usamune's own call decides, and nothing runs after it"
    assert cpu.u32(slots.slot_address(INDEX) + slots.TRAILER_MARIO) == slot_word
    assert cpu.u32(SP_AT_SITE + stub.FIRST_OBJ) == held
    assert not restored(cpu)


@pytest.mark.parametrize("offset", [row[0] for row in stub.CHAIN] + [stub.FIRST_OBJ])
def test_any_stack_word_it_did_not_prove_refuses_the_load(offset):
    built = stub.build()
    cpu = game(built)
    cpu.put_u32(SP_AT_SITE + offset, 0x80000000 + offset)
    run_load(cpu, built)
    assert not cpu.loaded
    assert cpu.u32(slots.slot_address(INDEX) + slots.TRAILER_MARIO) == SLOT_MARIO
    assert not restored(cpu)


def test_both_object_loops_are_accepted():
    for loop_return, list_return in zip(stub.CHAIN[2][1], stub.CHAIN[3][1], strict=True):
        built = stub.build()
        cpu = game(built)
        cpu.put_u32(SP_AT_SITE + stub.CHAIN[2][0], loop_return)
        cpu.put_u32(SP_AT_SITE + stub.CHAIN[3][0], list_return)
        run_load(cpu, built)
        assert cpu.loaded


def test_a_record_for_another_save_is_not_applied():
    built = stub.build()
    cpu = game(built)
    cpu.put_u32(slots.slot_address(INDEX) + stub.MARIO_POS_IN_SLOT, POS_X + 1)   # the slot was saved over
    run_load(cpu, built)
    assert cpu.loaded
    assert not restored(cpu)
    assert stub.LOAD_OBJ_WARP_NODES in cpu.calls, "the warp nodes still follow the slot's objects"


def test_a_slot_without_a_record_still_loads():
    built = stub.build()
    cpu = game(built, record=False)
    run_load(cpu, built)
    assert cpu.loaded and not restored(cpu)
    assert cpu.u32(SP_AT_SITE + stub.FIRST_OBJ) == SLOT_MARIO


def test_the_controls_really_remove_what_they_name():
    built = stub.build(stub.Options(repoint=False))
    cpu = game(built)
    run_load(cpu, built)
    assert cpu.u32(SP_AT_SITE + stub.FIRST_OBJ) == OLD_MARIO
    built = stub.build(stub.Options(restore=False))
    cpu = game(built)
    run_load(cpu, built)
    assert not restored(cpu)


def test_the_save_entry_stores_a_record_the_load_entry_accepts():
    built = stub.build()
    cpu = game(built, record=False)
    cpu.put(slots.SAVE_INDEX, bytes([INDEX]))
    cpu.put_u32(slots.GLOBAL_TIMER, TIMER)
    cpu.put_u32(slots.AREA_UPDATE_COUNTER, COUNTER)
    cpu.put(slots.RANDOM_SEED, struct.pack(">H", SEED))
    cpu.put_u32(stub.OBJECT_CIRCLE_START, CHILDREN)
    cpu.put_u32(stub.SECOND_CAMERA_FOCUS, FOCUS)
    cpu.put(stub.CONTROLLERS, PADS)
    cpu.fakes[slots.SAVE] = lambda: None
    return_to = 0x804126C0
    cpu.regs[31] = return_to
    cpu.run(built.save_entry, return_to)
    assert cpu.regs[29] == SP_AT_SITE and slots.SAVE in cpu.calls
    for address in (slots.GLOBAL_TIMER, slots.AREA_UPDATE_COUNTER, stub.OBJECT_CIRCLE_START, stub.SECOND_CAMERA_FOCUS):
        cpu.put_u32(address, 0)
    cpu.regs[31] = RETURN_HERE
    run_load(cpu, built)
    assert restored(cpu) and cpu.u32(stub.SECOND_CAMERA_FOCUS) == FOCUS


def test_it_fits_above_the_slots_and_below_the_top_of_ram():
    built = stub.build()
    end_of_slots = slots.SLOT_BASE + slots.SLOT_COUNT * slots.SLOT_BYTES
    assert end_of_slots <= stub.STUB_BASE
    assert built.base + 4 * len(built.words) <= stub.SIDECAR_BASE
    assert stub.SIDECAR_BASE + slots.SLOT_COUNT * stub.RECORD_BYTES <= 0x80800000


def test_it_changes_two_words_of_usamune_and_they_point_at_the_entries():
    built = stub.build()
    assert set(built.patches()) == {stub.LOAD_JUMP, stub.SAVE_CALL}
    assert built.patches()[stub.LOAD_JUMP] == stub.j_word(built.load_entry)
    assert built.patches()[stub.SAVE_CALL] == stub.jal_word(built.save_entry)


def test_the_assembler_writes_the_words_the_dispatcher_itself_holds():
    assert stub.j_word(slots.LOAD) == 0x0810473C
    assert stub.jal_word(slots.SAVE) == 0x0C100E4B
    asm = stub.Asm(0x80000000)
    asm.lui("t1", 0x8041)
    asm.addiu("sp", "sp", -0x18)
    asm.mem("sw", "ra", 0x14, "sp")
    asm.mem("lbu", "v1", 0x118, "t1")
    asm.jr("ra")
    assert asm.finish() == [0x3C098041, 0x27BDFFE8, 0xAFBF0014, 0x91230118, 0x03E00008]


def test_an_absolute_address_carries_into_the_high_half():
    asm = stub.Asm(0x80000000)
    asm.absolute("lw", "t0", 0x8038EEE0)
    assert asm.finish() == [0x3C018039, 0x8C28EEE0]


def test_a_branch_lands_on_its_label():
    asm = stub.Asm(0x80000000)
    asm.beq("zero", "zero", "there")
    asm.nop()
    asm.nop()
    asm.label("there")
    assert asm.finish()[0] == 0x10000002
