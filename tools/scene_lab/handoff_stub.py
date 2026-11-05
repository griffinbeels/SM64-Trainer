"""A small piece of N64 code that keeps a Usamune slot loadable after a level reset (Ghost Handoff, simulation only).

    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_stub.py listing
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_stub.py prove <session>
        --pair K J [--pair K J ...] [--follow 300] [--slot 3] [--out NAME]
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_stub.py controls <session>
        --same K J --wrong-level K J [--moved K J] [--out NAME]
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_stub.py again <session>
        --pair K J [--rounds 3] [--follow 300] [--out NAME]

Why it exists: a practice reset unloads the area without clear_objects, so the freed objects go back on the
free list and Mario's object lands in a different pool slot. Usamune's load refuses a slot whose stored
gMarioObject differs from the live one. Forcing that word alone crashes, because the dispatcher runs inside
Mario's own behavior script: update_objects_starting_at still holds the old Mario in its firstObj argument
and walks on from whatever now lives there.

What it does: Usamune's dispatcher tail-jumps to its load routine at LOAD_JUMP. That one word is pointed at
STUB_LOAD. The stub repeats Usamune's own three checks, and when only Mario's slot differs it proves the call
chain from the stack, writes the live pointer into the slot's occupied word, calls the real load, puts the
word back, and points firstObj at the slot's Mario. firstObj is not found by scanning. The chain is compiled
without optimization and every frame size is fixed: firstObj sits FIRST_OBJ bytes above the stack pointer at
try_print_debug_mario_level_info's call to the dispatcher, on both object loops. If a return address in
that chain is not the expected one, the stub falls back to the plain load, which refuses.

SAVE_CALL, the dispatcher's call to the save routine, goes to STUB_SAVE, which also stores the seed, clocks
and controller state for that slot in a sidecar. A load restores them only when the record's tag still matches
the slot, so a slot written by anything else simply loads without them.

Nothing here touches a running game. It writes only a simulated RDRAM. See the design record for what a
live install requires. Reports go to scene-lab/handoff-sim-*; RAM bytes stay out of the printed lines.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import types
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_slot as slots  # noqa: E402

# Unused RAM: above the nine slots, inside what Usamune's boot zeroes (0x80500000 + 0x300000). Nothing in
# Usamune's code or the game's own refers to it. Every recording and PJ64 savestate shows it zero.
STUB_BASE = 0x80700000
SIDECAR_BASE = 0x80701000
RECORD_BYTES = 0xA8
TAG_SLOT_MARIO, TAG_POS_X, TAG_POS_Y, TAG_ACTION = 0x00, 0x04, 0x08, 0x0C
RECORD_TIMER, RECORD_AREA_COUNTER, RECORD_SEED, RECORD_CHILDREN, RECORD_FOCUS = 0x10, 0x14, 0x18, 0x1C, 0x20
RECORD_PADS = 0x28
MARIO_ACTION_IN_SLOT, MARIO_POS_IN_SLOT = 0x0C, 0x3C      # gMarioStates is the slot body's first bytes

LOAD_JUMP, SAVE_CALL = 0x804126B0, 0x804126B8             # in Usamune's dispatcher 0x80412624
CONTROLLERS, CONTROLLERS_BYTES = 0x8033AF90, 0x80         # gControllers and what follows it, up to 0x8033B010
OBJECT_CIRCLE_START = 0x8038BD98                          # gObjParentGraphNode.children: where drawing starts
SECOND_CAMERA_FOCUS = 0x8032DF30                          # gSecondCameraFocus: an object pointer outside the slot
LOAD_OBJ_WARP_NODES = 0x8027AA74                          # load_obj_warp_nodes: rebuilds warp nodes from the objects

# The call chain at the dispatcher's call, as offsets from the stack pointer there. Frame sizes are from the
# code: try_print 0x28, bhv_cmd_call_native 0x20, cur_obj_update 0x30, either object loop 0x20.
CHAIN = ((0x1C, (0x8038469C,)),                           # try_print returns into bhv_cmd_call_native
         (0x3C, (0x80385D1C,)),                           # bhv_cmd_call_native returns into cur_obj_update
         (0x5C, (0x8029CB80, 0x8029CCC0)),                # cur_obj_update returns into one of the two loops
         (0x8C, (0x8029CD60, 0x8029CD74)))                # that loop returns into update_objects_in_list
FIRST_OBJ = 0x9C                                          # the loop's firstObj (its 0x24(sp)): the only live copy


# --- a small assembler: only the instructions the stub uses ---------------------------------------
REG = {name: i for i, name in enumerate(
    "zero at v0 v1 a0 a1 a2 a3 t0 t1 t2 t3 t4 t5 t6 t7 s0 s1 s2 s3 s4 s5 s6 s7 t8 t9 k0 k1 gp sp fp ra".split())}
OPS = {"lw": 0x23, "sw": 0x2B, "lh": 0x21, "lhu": 0x25, "sh": 0x29, "lbu": 0x24}


class Asm:
    """Two-pass by construction: branches record a fixup and resolve when the labels are known."""

    def __init__(self, base: int):
        self.base = base
        self.words: list[int] = []
        self.labels: dict[str, int] = {}
        self.fixups: list[tuple[int, str]] = []

    @property
    def here(self) -> int:
        return self.base + 4 * len(self.words)

    def label(self, name: str) -> None:
        if name in self.labels:
            raise ValueError(f"label {name} defined twice")
        self.labels[name] = self.here

    def raw(self, word: int) -> None:
        self.words.append(word & 0xFFFFFFFF)

    def nop(self) -> None:
        self.raw(0)

    def imm(self, op: int, rs: str, rt: str, value: int) -> None:
        self.raw(op << 26 | REG[rs] << 21 | REG[rt] << 16 | value & 0xFFFF)

    def lui(self, rt: str, value: int) -> None:
        self.imm(0x0F, "zero", rt, value)

    def ori(self, rt: str, rs: str, value: int) -> None:
        self.imm(0x0D, rs, rt, value)

    def addiu(self, rt: str, rs: str, value: int) -> None:
        self.imm(0x09, rs, rt, value)

    def sltiu(self, rt: str, rs: str, value: int) -> None:
        self.imm(0x0B, rs, rt, value)

    def li(self, rt: str, value: int) -> None:
        self.lui(rt, value >> 16)
        self.ori(rt, rt, value & 0xFFFF)

    def mem(self, op: str, rt: str, offset: int, rs: str) -> None:
        self.imm(OPS[op], rs, rt, offset)

    def absolute(self, op: str, rt: str, address: int, via: str = "at") -> None:
        """A load or store at a fixed address, with the usual sign-carrying hi/lo split."""
        low = address & 0xFFFF
        self.lui(via, ((address >> 16) + (low >> 15)) & 0xFFFF)
        self.mem(op, rt, low, via)

    def addu(self, rd: str, rs: str, rt: str) -> None:
        self.raw(REG[rs] << 21 | REG[rt] << 16 | REG[rd] << 11 | 0x21)

    def multu(self, rs: str, rt: str) -> None:
        self.raw(REG[rs] << 21 | REG[rt] << 16 | 0x19)

    def mflo(self, rd: str) -> None:
        self.raw(REG[rd] << 11 | 0x12)

    def jr(self, rs: str) -> None:
        self.raw(REG[rs] << 21 | 0x08)

    def j(self, target: int) -> None:
        self.raw(j_word(target))

    def jal(self, target: int) -> None:
        self.raw(jal_word(target))

    def branch(self, op: int, rs: str, rt: str, label: str) -> None:
        self.fixups.append((len(self.words), label))
        self.imm(op, rs, rt, 0)

    def beq(self, rs: str, rt: str, label: str) -> None:
        self.branch(0x04, rs, rt, label)

    def bne(self, rs: str, rt: str, label: str) -> None:
        self.branch(0x05, rs, rt, label)

    def finish(self) -> list[int]:
        for at, name in self.fixups:
            offset = (self.labels[name] - (self.base + 4 * at + 4)) >> 2
            if not -0x8000 <= offset < 0x8000:
                raise ValueError(f"branch to {name} is out of range")
            self.words[at] |= offset & 0xFFFF
        return self.words


def j_word(target: int) -> int:
    return 0x08000000 | (target & 0x0FFFFFFF) >> 2


def jal_word(target: int) -> int:
    return 0x0C000000 | (target & 0x0FFFFFFF) >> 2


@dataclass(frozen=True)
class Options:
    """repoint=False and chain_check=False exist only as controls: each must be shown to matter."""
    repoint: bool = True
    chain_check: bool = True
    restore: bool = True
    restore_pads: bool = True
    warp_nodes: bool = True


@dataclass(frozen=True)
class Stub:
    words: tuple[int, ...]
    base: int
    load_entry: int
    save_entry: int

    def patches(self) -> dict[int, int]:
        """The only two words of Usamune that change, and their new values."""
        return {LOAD_JUMP: j_word(self.load_entry), SAVE_CALL: jal_word(self.save_entry)}


def _slot_and_record(asm: Asm, index_offset: int, slot: str, record: str, bad: str) -> None:
    asm.absolute("lbu", "t0", slots.SETTINGS + index_offset)
    asm.sltiu("t1", "t0", slots.SLOT_COUNT)
    asm.beq("t1", "zero", bad)
    asm.nop()
    asm.li("t1", slots.SLOT_BYTES)
    asm.multu("t0", "t1")
    asm.mflo("t2")
    asm.li("t3", slots.SLOT_BASE)
    asm.addu(slot, "t3", "t2")
    asm.li("t1", RECORD_BYTES)
    asm.multu("t0", "t1")
    asm.mflo("t2")
    asm.li("t3", SIDECAR_BASE)
    asm.addu(record, "t3", "t2")


def _copy_words(asm: Asm, name: str, source: str, target: str, count_bytes: int) -> None:
    """Word copy loop: clobbers t0, t1, and advances both pointers."""
    asm.addiu("t1", source, count_bytes)
    asm.label(name)
    asm.mem("lw", "t0", 0, source)
    asm.addiu(source, source, 4)
    asm.mem("sw", "t0", 0, target)
    asm.bne(source, "t1", name)
    asm.addiu(target, target, 4)


def build(options: Options = Options(), base: int = STUB_BASE) -> Stub:
    frame = 0x28
    asm = Asm(base)

    # STUB_LOAD: entered in place of LOAD with the dispatcher's frame gone, so sp + frame is the site's sp.
    load_entry = asm.here
    asm.addiu("sp", "sp", -frame)
    asm.mem("sw", "ra", 0x24, "sp")
    for at, register in ((0x10, "s0"), (0x14, "s1"), (0x18, "s2"), (0x1C, "s3")):
        asm.mem("sw", register, at, "sp")
    _slot_and_record(asm, 0x118, "s0", "s3", "plain")
    asm.li("t1", slots.BODY_BYTES)
    asm.addu("s1", "s0", "t1")                    # s1: the trailer
    asm.mem("lw", "s2", slots.TRAILER_MARIO - slots.BODY_BYTES, "s1")   # s2: the slot's Mario
    asm.absolute("lw", "t5", slots.MARIO_OBJECT)  # t5: the live Mario
    asm.beq("s2", "zero", "plain")
    asm.nop()
    asm.beq("t5", "zero", "plain")
    asm.nop()
    asm.mem("lh", "t6", slots.TRAILER_LEVEL - slots.BODY_BYTES, "s1")
    asm.absolute("lh", "t7", slots.CURR_LEVEL)
    asm.bne("t6", "t7", "plain")
    asm.nop()
    asm.mem("lh", "t6", slots.TRAILER_AREA - slots.BODY_BYTES, "s1")
    asm.absolute("lh", "t7", slots.CURR_AREA)
    asm.bne("t6", "t7", "plain")
    asm.nop()
    asm.beq("s2", "t5", "same_mario")
    asm.nop()

    # Only Mario's slot differs. Refuse unless the stack is the chain we proved.
    if options.chain_check:
        for offset, allowed in CHAIN:
            asm.mem("lw", "t6", frame + offset, "sp")
            for value in allowed:
                asm.li("t7", value)
                asm.beq("t6", "t7", f"chain_{offset:x}")
                asm.nop()
            asm.beq("zero", "zero", "plain")
            asm.nop()
            asm.label(f"chain_{offset:x}")
        asm.mem("lw", "t6", frame + FIRST_OBJ, "sp")
        asm.bne("t6", "t5", "plain")
        asm.nop()
    asm.mem("sw", "t5", slots.TRAILER_MARIO - slots.BODY_BYTES, "s1")
    asm.jal(slots.LOAD)
    asm.nop()
    asm.mem("sw", "s2", slots.TRAILER_MARIO - slots.BODY_BYTES, "s1")   # the slot's bytes come back as they were
    asm.absolute("lw", "t0", slots.MARIO_OBJECT)
    asm.bne("t0", "s2", "done")                   # the load did not run: nothing to repoint or restore
    asm.nop()
    if options.repoint:
        asm.mem("sw", "s2", frame + FIRST_OBJ, "sp")
    asm.addiu("s1", "zero", 1)                    # s1 = 1: Mario moved, so the warp nodes name the wrong objects
    asm.beq("zero", "zero", "restore")
    asm.nop()

    asm.label("same_mario")
    asm.jal(slots.LOAD)
    asm.addu("s1", "zero", "zero")

    # Seed, clocks and controllers, only from a record whose tag still matches this slot.
    asm.label("restore")
    if options.restore:
        asm.mem("lw", "t0", TAG_SLOT_MARIO, "s3")
        asm.bne("t0", "s2", "rebuild")
        asm.nop()
        for tag, in_slot in ((TAG_POS_X, MARIO_POS_IN_SLOT), (TAG_POS_Y, MARIO_POS_IN_SLOT + 4), (TAG_ACTION, MARIO_ACTION_IN_SLOT)):
            asm.mem("lw", "t0", tag, "s3")
            asm.mem("lw", "t1", in_slot, "s0")
            asm.bne("t0", "t1", "rebuild")
            asm.nop()
        asm.mem("lw", "t0", RECORD_SEED, "s3")
        asm.absolute("sh", "t0", slots.RANDOM_SEED)
        asm.mem("lw", "t0", RECORD_CHILDREN, "s3")
        asm.absolute("sw", "t0", OBJECT_CIRCLE_START)
        asm.mem("lw", "t0", RECORD_FOCUS, "s3")
        asm.absolute("sw", "t0", SECOND_CAMERA_FOCUS)
        asm.mem("lw", "t0", RECORD_TIMER, "s3")
        asm.absolute("sw", "t0", slots.GLOBAL_TIMER)
        asm.mem("lw", "t0", RECORD_AREA_COUNTER, "s3")
        asm.absolute("sw", "t0", slots.AREA_UPDATE_COUNTER)
        if options.restore_pads:
            asm.addiu("t2", "s3", RECORD_PADS)
            asm.li("t3", CONTROLLERS)
            _copy_words(asm, "pads_back", "t2", "t3", CONTROLLERS_BYTES)

    asm.label("rebuild")
    if options.warp_nodes:
        asm.beq("s1", "zero", "done")
        asm.nop()
        asm.jal(LOAD_OBJ_WARP_NODES)
        asm.nop()

    asm.label("done")
    asm.mem("lw", "ra", 0x24, "sp")
    for at, register in ((0x10, "s0"), (0x14, "s1"), (0x18, "s2"), (0x1C, "s3")):
        asm.mem("lw", register, at, "sp")
    asm.jr("ra")
    asm.addiu("sp", "sp", frame)

    # Anything else is Usamune's call, unchanged: its own gate refuses.
    asm.label("plain")
    asm.mem("lw", "ra", 0x24, "sp")
    for at, register in ((0x10, "s0"), (0x14, "s1"), (0x18, "s2"), (0x1C, "s3")):
        asm.mem("lw", register, at, "sp")
    asm.j(slots.LOAD)
    asm.addiu("sp", "sp", frame)

    # STUB_SAVE: Usamune's save, then the record that lets this slot's load be exact.
    save_entry = asm.here
    asm.addiu("sp", "sp", -0x20)
    asm.mem("sw", "ra", 0x1C, "sp")
    asm.mem("sw", "s0", 0x10, "sp")
    asm.mem("sw", "s1", 0x14, "sp")
    asm.jal(slots.SAVE)
    asm.nop()
    _slot_and_record(asm, 0x119, "s0", "s1", "saved")
    asm.li("t1", slots.BODY_BYTES)
    asm.addu("t1", "s0", "t1")
    asm.mem("lw", "t0", slots.TRAILER_MARIO - slots.BODY_BYTES, "t1")
    asm.mem("sw", "t0", TAG_SLOT_MARIO, "s1")
    for tag, in_slot in ((TAG_POS_X, MARIO_POS_IN_SLOT), (TAG_POS_Y, MARIO_POS_IN_SLOT + 4), (TAG_ACTION, MARIO_ACTION_IN_SLOT)):
        asm.mem("lw", "t0", in_slot, "s0")
        asm.mem("sw", "t0", tag, "s1")
    asm.absolute("lw", "t0", slots.GLOBAL_TIMER)
    asm.mem("sw", "t0", RECORD_TIMER, "s1")
    asm.absolute("lw", "t0", slots.AREA_UPDATE_COUNTER)
    asm.mem("sw", "t0", RECORD_AREA_COUNTER, "s1")
    asm.absolute("lhu", "t0", slots.RANDOM_SEED)
    asm.mem("sw", "t0", RECORD_SEED, "s1")
    asm.absolute("lw", "t0", OBJECT_CIRCLE_START)
    asm.mem("sw", "t0", RECORD_CHILDREN, "s1")
    asm.absolute("lw", "t0", SECOND_CAMERA_FOCUS)
    asm.mem("sw", "t0", RECORD_FOCUS, "s1")
    asm.li("t2", CONTROLLERS)
    asm.addiu("t3", "s1", RECORD_PADS)
    _copy_words(asm, "pads_out", "t2", "t3", CONTROLLERS_BYTES)
    asm.label("saved")
    asm.mem("lw", "ra", 0x1C, "sp")
    asm.mem("lw", "s0", 0x10, "sp")
    asm.mem("lw", "s1", 0x14, "sp")
    asm.jr("ra")
    asm.addiu("sp", "sp", 0x20)

    words = asm.finish()
    if STUB_BASE == base and asm.here > SIDECAR_BASE:
        raise ValueError("the stub overruns its sidecar")
    return Stub(tuple(words), base, load_entry, save_entry)


def record_bytes(*, mario: int, pos_x: int, pos_y: int, action: int, timer: int, area_counter: int, seed: int,
                 children: int, focus: int, pads: bytes) -> bytes:
    """One sidecar record, in the N64's byte order: what a writer of slots stores next to a slot it writes."""
    if len(pads) != CONTROLLERS_BYTES:
        raise ValueError("pads must be the controller block")
    head = struct.pack(">9I4x", mario, pos_x, pos_y, action, timer, area_counter, seed, children, focus)
    return head + pads


# --- running it in the simulator ------------------------------------------------------------------
sim = slots.sim
AFTER_SITE = slots.SITE + 8
SETTINGS_ON, SETTINGS_COMBO = slots.SETTINGS + 0x10, slots.SETTINGS + 0x1A
PRESS_LOAD, PRESS_SAVE = 0x080F, 0x1000             # any of Usamune's load combos; START for its save
BUTTON_DOWN, BUTTON_PRESSED = CONTROLLERS + 0x10, CONTROLLERS + 0x12
ORIGINAL = {LOAD_JUMP: j_word(slots.LOAD), SAVE_CALL: jal_word(slots.SAVE)}


def install(cpu: slots.SlotCpu, stub: Stub) -> None:
    for at, expected in ORIGINAL.items():
        now = cpu.u32(at)
        ours = now >> 26 == expected >> 26 and sim.phys(STUB_BASE) <= (now & 0x03FFFFFF) << 2 < sim.phys(SIDECAR_BASE)
        if now != expected and not ours:
            raise SystemExit(f"{at:08x} holds {now:08x}: not the dispatcher this stub knows")
    for offset, word in enumerate(stub.words):
        cpu.put_u32(stub.base + 4 * offset, word)
    for at, word in stub.patches().items():
        cpu.put_u32(at, word)
    cpu.uc.ctl_flush_tb()


def uninstall(cpu: slots.SlotCpu) -> None:
    for at, word in ORIGINAL.items():
        cpu.put_u32(at, word)
    cpu.uc.ctl_flush_tb()


class PressedCpu:
    """Drive Usamune's real dispatcher: press the buttons in RAM at the call site, take them away right after.

    The recordings hold no press at these frames, so the press exists only for the one call."""

    def __init__(self, cpu: slots.SlotCpu):
        self.cpu = cpu
        self.after = None
        cpu.uc.hook_add(cpu.unicorn.UC_HOOK_CODE, self._after, begin=sim.sext(AFTER_SITE), end=sim.sext(AFTER_SITE))

    def _after(self, _uc, _address, _size, _user):
        if self.after is not None:
            action, self.after = self.after, None
            action()

    def press(self, down: int, pressed: int, then=None) -> None:
        cpu = self.cpu
        kept = cpu.u16(BUTTON_DOWN), cpu.u16(BUTTON_PRESSED)
        enable = cpu.u8(SETTINGS_ON)
        cpu.put_u8(SETTINGS_ON, enable or 1)
        cpu.put_u16(BUTTON_DOWN, kept[0] | down)
        cpu.put_u16(BUTTON_PRESSED, pressed)

        def undo() -> None:
            # Unless the stub already put the slot's controllers back, the press comes out again.
            if cpu.u16(BUTTON_DOWN) == kept[0] | down and cpu.u16(BUTTON_PRESSED) == pressed:
                cpu.put_u16(BUTTON_DOWN, kept[0])
                cpu.put_u16(BUTTON_PRESSED, kept[1])
            cpu.put_u8(SETTINGS_ON, enable)
            if then is not None:
                then(kept)
        self.after = undo


@dataclass
class Ride:
    """A replay whose slot was saved at K through the real dispatcher, paused just before J."""
    replay: slots.Replay
    press: PressedCpu
    slot: int
    k: int
    j: int
    pool_k: object
    after: list
    frame_j: object
    pause: slots.Snapshot
    saved: bytes
    natural: object
    tap: object
    identity: tuple


def ride(session: Path, k: int, j: int, follow_frames: int, slot: int, stub: Stub | None) -> Ride:
    replay = slots.start_replay(session)
    cpu = replay.cpu
    press = PressedCpu(cpu)
    if stub is not None:
        install(cpu, stub)
    kept: dict = {}

    def save_at_k() -> None:
        cpu.put_u8(slots.SAVE_INDEX, slot)
        block = cpu.ram[sim.phys(CONTROLLERS):sim.phys(CONTROLLERS) + CONTROLLERS_BYTES].copy()

        def done(_buttons) -> None:
            kept["natural"] = cpu.ram.copy()
            low = sim.phys(slots.slot_address(slot))
            kept["slot"] = cpu.ram[low:low + slots.SLOT_BYTES].tobytes()
            if stub is not None:
                # The press is ours, not the recording's: the record keeps the frame's real controllers.
                at = sim.phys(SIDECAR_BASE + slot * RECORD_BYTES + RECORD_PADS)
                cpu.ram[at:at + CONTROLLERS_BYTES] = block
        press.press(PRESS_SAVE, PRESS_SAVE, done)

    pool_k, frames_after, identity = None, [], None
    for frame in replay.stream:
        replay.absorb(frame)
        if frame.index == k:
            cpu.pending = save_at_k
            identity = (frame.level, frame.area)
        if frame.index > k:
            frames_after.append(frame)
        if frame.index < j:
            replay.step(frame)
        if frame.index == k:
            pool_k = replay.pool.copy()
            kept["tap"] = cpu.ram.copy()
        if frame.index >= k + follow_frames + 1 and frame.index >= j:
            break
    if "slot" not in kept or not any(kept["slot"][slots.TRAILER_MARIO:slots.TRAILER_MARIO + 4]):
        raise SystemExit(f"Usamune's dispatcher did not save at frame {k}")
    frame_j = next(frame for frame in frames_after if frame.index == j)
    following = [frame for frame in frames_after if frame.index <= k + follow_frames]
    return Ride(replay, press, slot, k, j, pool_k, following, frame_j, slots.Snapshot.of(replay), kept["slot"],
                kept["natural"], kept["tap"], identity)


def slot_bytes(cpu: slots.SlotCpu, index: int) -> bytes:
    low = sim.phys(slots.slot_address(index))
    return cpu.ram[low:low + slots.SLOT_BYTES].tobytes()


def press_load(trip: Ride, stub: Stub | None, tamper=None, before_frame=None) -> dict:
    """Back to the pause, the stub (or none) in place, frame J with a real load press. Returns what the press did."""
    replay, cpu = trip.replay, trip.replay.cpu
    trip.pause.back_to(replay)
    if stub is None:
        uninstall(cpu)
    else:
        install(cpu, stub)
    seen: dict = {"live_before": cpu.u32(slots.MARIO_OBJECT)}
    if before_frame is not None:
        before_frame(cpu)

    def at_site() -> None:
        sp = cpu.uc.reg_read(cpu.mips.UC_MIPS_REG_SP) & 0xFFFFFFFF
        seen["loop_return"] = cpu.u32(sp + CHAIN[2][0])
        cpu.put_u8(slots.LOAD_INDEX, trip.slot)
        undo_tamper = tamper(cpu) if tamper is not None else None
        seen["ram_before"] = cpu.ram.copy()
        seen["slot_before"] = slot_bytes(cpu, trip.slot)

        def after_press(_buttons) -> None:
            seen.update(ram_after=cpu.ram.copy(), slot_after=slot_bytes(cpu, trip.slot))
            if undo_tamper is not None:
                undo_tamper()
        trip.press.press(PRESS_LOAD, 0, after_press)
    cpu.pending = at_site
    replay.feed(trip.frame_j)
    seen["ran"] = cpu.run_frame(sim.RESUME)
    seen["violations"] = list(cpu.violations)
    if seen["ran"]:
        cpu.scheduler_model()
    return seen


def press_changed(seen: dict, cpu: slots.SlotCpu) -> dict:
    """What a press changed outside thread 5's stack (the calls themselves scribble below the stack pointer)."""
    return dict(slots.group_diff(seen["ram_after"], seen["ram_before"], cpu.symbols, skip=(("thread 5's stack", 0x204E00, 0x206E00),)))


def named(groups: dict, limit: int = 24) -> dict:
    """Differences, with objects folded by field so a long list stays readable."""
    folded: dict[str, int] = {}
    for name, count in groups.items():
        folded[name] = folded.get(name, 0) + count
    return dict(sorted(folded.items(), key=lambda row: -row[1])[:limit])


def play(trip: Ride, name: str, stub: Stub | None, report: dict, stick: bool = False, tamper=None, before_frame=None) -> dict:
    seen = press_load(trip, stub, tamper, before_frame)
    row: dict = {"loaded": False, "stub": stub is not None, "loop_return": f"{seen.get('loop_return', 0):08x}"}
    if not seen["ran"]:
        pc = trip.replay.cpu.uc.reg_read(trip.replay.cpu.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
        ra = trip.replay.cpu.uc.reg_read(trip.replay.cpu.mips.UC_MIPS_REG_RA) & 0xFFFFFFFF
        row.update(crashed_on_load_frame=True, violations=seen["violations"][:2], pc=f"{pc:08x}", ra=f"{ra:08x}",
                   ra_in=trip.replay.cpu.symbols.name_of(ra))
        report[name] = row
        print(f"{name}: CRASH on the load frame, {seen['violations'][:1]}, RA in {row['ra_in']}")
        return row
    cpu = trip.replay.cpu
    diff = slots.group_diff(seen["ram_after"], trip.natural, cpu.symbols,
                            skip=(*slots.PRESENTATION, ("the stub", sim.phys(STUB_BASE), sim.phys(STUB_BASE) + 0x10000)))
    row["loaded"] = "ram_after" in seen and cpu.u32(slots.MARIO_OBJECT) != 0 and bool(
        (seen["ram_after"] != seen["ram_before"]).any())
    row["slot_unchanged"] = seen.get("slot_before") == seen.get("slot_after")
    row["after_load"] = slots.summarize(diff)
    row["after_load_named"] = dict(diff.most_common(40))
    result = slots.follow(trip.replay, trip.after, trip.pool_k, stick=stick)
    row.update(result)
    row["last_frame_named"] = None
    report[name] = row
    print(f"{name}: slot bytes unchanged {row['slot_unchanged']}; right after the load {row['after_load']}")
    print(f"    {row['frames_ok']} frames; Mario at most {row.get('farthest_units', 0):.3f} units, same action "
          f"{row.get('same_action_frames')}; first difference {row.get('first')}; last frame {row.get('last_frame')}")
    if "crashed_at" in row:
        print(f"    CRASHED at frame {row['crashed_at']}: {row['violations']}")
    return row


def end_state(trip: Ride, frames: list) -> dict:
    """Named differences on the follow's last frame alone, against the recording."""
    cpu = trip.replay.cpu
    pool = trip.pool_k.copy()
    for frame in frames:
        for begin, data in frame.level_blocks:
            pool[begin - sim.POOL_BEGIN:begin - sim.POOL_BEGIN + data.size] = data
    where = sim.compare(cpu, frames[-1].spans, pool, pool_known=True)
    groups: dict[str, int] = {}
    for address in where.tolist():
        name = sim.label(cpu.symbols, address)
        groups[name] = groups.get(name, 0) + 1
    return named(groups, 40)


def prove(args) -> int:
    session = sim.lab_root() / args.session
    stub = build()
    report: dict = {"session": args.session, "follow": args.follow, "stub_words": len(stub.words)}
    for k, j in args.pair:
        trip = ride(session, k, j, args.follow, args.slot, stub)
        cpu = trip.replay.cpu
        slot_mario = struct.unpack_from(">I", trip.saved, slots.TRAILER_MARIO)[0]
        title = f"K={k} J={j}"
        print(f"\n{title}: slot Mario {slot_mario:08x}, live {cpu.u32(slots.MARIO_OBJECT):08x}, level/area {trip.identity}")
        rows: dict = {"slot_mario": f"{slot_mario:08x}", "live_mario": f"{cpu.u32(slots.MARIO_OBJECT):08x}"}
        report[title] = rows
        variants = (("stub, nothing restored", build(Options(restore=False))),
                    ("stub, no warp node rebuild", build(Options(warp_nodes=False))),
                    ("stub, seed+clocks", build(Options(restore_pads=False))),
                    ("stub, seed+clocks+pads", stub))
        for name, variant in variants:
            row = play(trip, name, variant, rows)
            if "crashed_at" not in row and not row.get("crashed_on_load_frame"):
                row["end_named"] = end_state(trip, trip.after)
                print("    on the last frame:", row["end_named"])
        play(trip, "control: no repoint", build(Options(repoint=False)), rows)
        flip = play(trip, "control: flipped stick", stub, rows, stick=True)
        print("    the flipped stick diverges Mario at", flip.get("first", {}).get("Mario"))
        play(trip, "control: Usamune's own load, no stub", None, rows)
    write_report(args, report)
    return 0


def controls(args) -> int:
    """The stub must change nothing when no reset happened, and must keep every refusal."""
    session = sim.lab_root() / args.session
    report: dict = {"session": args.session}
    stub = build()
    if args.same:
        k, j = args.same
        trip = ride(session, k, j, args.follow, args.slot, stub)
        rows: dict = {}
        report[f"no reset K={k} J={j}"] = rows
        plain = press_load(trip, None)
        ours = press_load(trip, build(Options(restore=False)))
        cpu = trip.replay.cpu
        patched = tuple((f"patched word {at:08x}", sim.phys(at), sim.phys(at) + 4) for at in ORIGINAL)
        difference = slots.group_diff(plain["ram_after"], ours["ram_after"], cpu.symbols,
                                      skip=(*slots.PRESENTATION, *patched, ("stub", sim.phys(STUB_BASE), sim.phys(STUB_BASE) + 0x10000)))
        rows["stub without restore vs Usamune's plain load, right after the load"] = dict(difference)
        print(f"no reset K={k} J={j}: stub without restore against the plain load, all RAM but the two patched words, "
              f"the stack and the stub: {sum(difference.values())} bytes {dict(difference)}")
        row = play(trip, "stub, seed+clocks+pads", stub, rows)
        if "crashed_at" not in row and not row.get("crashed_on_load_frame"):
            row["end_named"] = end_state(trip, trip.after)
            print("    on the last frame:", row["end_named"])
        play(trip, "control: plain load", None, rows)
    if args.wrong_level:
        k, j = args.wrong_level
        trip = ride(session, k, j, args.follow, args.slot, stub)
        rows = {}
        report[f"wrong level K={k} J={j}"] = rows
        cpu = trip.replay.cpu
        seen = press_load(trip, stub)
        changed = press_changed(seen, cpu)
        rows["what the press changed"] = changed
        rows["ran"] = seen["ran"]
        print(f"wrong level K={k} J={j} (slot level {trip.identity}, live {cpu.u16(slots.CURR_LEVEL)}): press changed {changed}; "
              f"frame ran {seen['ran']}")
    if args.moved:
        k, j = args.moved
        trip = ride(session, k, j, args.follow, args.slot, stub)
        rows = {}
        report[f"refusals K={k} J={j}"] = rows
        area_word = slots.slot_address(args.slot) + slots.TRAILER_AREA

        def wrong_area(cpu) -> None:
            cpu.put_u16(area_word, cpu.u16(area_word) + 1)

        def empty(cpu) -> None:
            cpu.put_u32(slots.slot_address(args.slot) + slots.TRAILER_MARIO, 0)

        def changed_for_the_press(cpu, at: int, value: int):
            kept = cpu.u32(at)
            cpu.put_u32(at, value)
            return lambda: cpu.put_u32(at, kept)

        def strange_chain(cpu):
            sp = cpu.uc.reg_read(cpu.mips.UC_MIPS_REG_SP) & 0xFFFFFFFF
            return changed_for_the_press(cpu, sp + 0x8C, cpu.u32(sp + 0x8C) + 8)     # a caller the stub never proved

        def strange_held(cpu):
            sp = cpu.uc.reg_read(cpu.mips.UC_MIPS_REG_SP) & 0xFFFFFFFF
            return changed_for_the_press(cpu, sp + FIRST_OBJ, cpu.u32(sp + FIRST_OBJ) + sim.OBJECT_BYTES)

        for name, tamper in (("wrong area", wrong_area), ("empty slot", empty), ("unknown caller on the stack", strange_chain),
                             ("firstObj is not live Mario", strange_held)):
            seen = press_load(trip, stub, tamper)
            changed = press_changed(seen, cpu)
            rows[name] = {"what the press changed": changed, "ran": seen["ran"]}
            print(f"{name}: press changed {changed}; frame ran {seen['ran']} {seen['violations'][:1]}")
    write_report(args, report)
    return 0


def again(args) -> int:
    """Save, reset, load, play on through the recording's own reset, load again, and so on.

    A load at J goes back to K's world. The pads then replay K's own reset at K+n, so Mario moves again, and each
    further load is the same trip. Also: the same load with the game forced into the time-stop object loop."""
    session = sim.lab_root() / args.session
    stub = build()
    k, j = args.pair
    trip = ride(session, k, j, args.follow, args.slot, stub)
    cpu, replay = trip.replay.cpu, trip.replay
    report: dict = {"session": args.session, "k": k, "j": j, "rounds": []}

    def forced_stop(game) -> None:
        game.put_u32(game.symbols["gTimeStopState"], game.u32(game.symbols["gTimeStopState"]) | 0x40)

    row = play(trip, "the time-stop object loop", stub, report, before_frame=forced_stop)
    print(f"    the load ran with {row['loop_return']} on the stack ({cpu.symbols.name_of(int(row['loop_return'], 16))})")
    press_load(trip, stub)
    slot_copy = slot_bytes(cpu, trip.slot)
    frames = trip.after
    for round_index in range(args.rounds):
        result = slots.follow(replay, frames, trip.pool_k)
        row = {"round": round_index, "frames_ok": result["frames_ok"], "farthest_units": result.get("farthest_units"),
               "same_action_frames": result.get("same_action_frames"), "last_frame": result.get("last_frame"),
               "crashed_at": result.get("crashed_at"), "mario_at_the_end": f"{cpu.u32(slots.MARIO_OBJECT):08x}"}
        if "crashed_at" not in result:
            row["end_named"] = end_state(trip, frames)
        report["rounds"].append(row)
        print(row)
        if "crashed_at" in result:
            break
        live = cpu.u32(slots.MARIO_OBJECT)

        def press_again() -> None:
            cpu.put_u8(slots.LOAD_INDEX, trip.slot)
            trip.press.press(PRESS_LOAD, 0)
        cpu.pending = press_again
        replay.feed(trip.frame_j)
        ok = cpu.run_frame(sim.RESUME)
        cpu.scheduler_model()
        print(f"  pressed again with live Mario {live:08x}: frame ran {ok} {cpu.violations[:2]}; slot unchanged {slot_bytes(cpu, trip.slot) == slot_copy}")
        report["rounds"][-1]["pressed_with_live_mario"] = f"{live:08x}"
        if not ok:
            break
    write_report(args, report)
    return 0


def write_report(args, report: dict) -> None:
    out = sim.lab_root() / f"handoff-sim-{args.out or 'stub'}" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, default=str), encoding="utf-8")
    print(f"report: {out}")


def listing(_args) -> int:
    from capstone import CS_ARCH_MIPS, CS_MODE_BIG_ENDIAN, CS_MODE_MIPS64, Cs
    stub = build()
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_BIG_ENDIAN)
    code = b"".join(struct.pack(">I", word) for word in stub.words)
    for ins in md.disasm(code, stub.base):
        print(f"{ins.address:08x} {ins.mnemonic} {ins.op_str}")
    print(f"{len(stub.words)} words; load entry {stub.load_entry:08x}; save entry {stub.save_entry:08x}")
    print("patches:", {f"{at:08x}": f"{word:08x}" for at, word in stub.patches().items()})
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("listing").set_defaults(handler=listing)
    for name, handler in (("prove", prove), ("controls", controls), ("again", again)):
        command = commands.add_parser(name)
        command.add_argument("session")
        command.add_argument("--follow", type=int, default=300)
        command.add_argument("--slot", type=int, default=3)
        command.add_argument("--out")
        command.set_defaults(handler=handler)
        if name == "prove":
            command.add_argument("--pair", nargs=2, type=int, action="append", required=True, metavar=("K", "J"))
        elif name == "again":
            command.add_argument("--pair", nargs=2, type=int, required=True, metavar=("K", "J"))
            command.add_argument("--rounds", type=int, default=3)
        else:
            for flag in ("--same", "--wrong-level", "--moved"):
                command.add_argument(flag, nargs=2, type=int, metavar=("K", "J"))
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
