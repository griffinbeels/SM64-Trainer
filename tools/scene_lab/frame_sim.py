# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
"""Run SM64's own game loop in Unicorn and compare each frame to the scene tap (Ghost Handoff probe).

    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/frame_sim.py scan
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/frame_sim.py run <session>
        --mode closed|carry|stale|stale-seed [--start K] [--frames N] [--out NAME] [--count]
        [--perturb FRAME seed|stick|history VALUE] [--inject-vblanks] [--time-step N]

The question: can game code outside Project64 reproduce the next frame's real memory byte for byte?

The tap copies at each graphics task. On this ROM that is thread 3 starting the task thread 5 just
sent, so a recorded state is thread 5 stopped inside display_and_vsync, at the return of
send_display_list, with gGlobalTimer not yet incremented. (The boot record shows it: thread 5's saved
PC lies in the osStartThread that send_display_list's osSendMesg called to wake thread 3. A session's
first task is rendering_init's own, ahead of the loop.) One step here resumes there, finishes
display_and_vsync, runs Usamune's end-of-loop hook, then the loop body (osContStartReadData,
audio_game_loop_tick, config_gfx_pool, read_controller_inputs, level_script_execute,
display_and_vsync) and stops at the same return. Thread 5's only live value across that point is
the level script address it keeps at 0x24(sp), which equals the interpreter's sCurrentCmd.

The modes differ in what they correct after each frame:
    closed      nothing: only each frame's captured controller pads go in, from the boot record on
    carry       the captured spans are written back, RAM outside them stays what the simulation made
                (so gRandomSeed16 stays the simulation's: the sharpest one-step test)
    stale       every frame starts from the boot record plus the captured spans alone, the state a
                reader of the tap can rebuild by itself
    stale-seed  stale from --from-frame on, keeping only the simulation's gRandomSeed16

Only thread 5 runs: threads 3 and 4 and the RSP do not. The libultra calls it makes are answered
here; time stands still unless --time-step moves it; anything else touching hardware stops the run
and names its address. --perturb and the diagnosis flags are the controls: a change that must show
up in the comparison, or a hidden input fed back in to see a difference go away.

Reports go to %LOCALAPPDATA%/SM64Trainer/scene-lab/handoff-sim-*, never the repository: the printed
lines hold symbol names and counts, not RAM bytes. Sessions and the ROM are private inputs.
"""
from __future__ import annotations

import argparse
import bisect
import collections
import json
import mmap
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tap  # noqa: E402

RDRAM_BYTES = 8 << 20
STATUS_CU1 = 0x20000000
BUDGET_US = 60_000_000
THREAD_PC, THREAD_FPCSR = 0x11C, 0x12C          # OSThread fields (libultra's THREAD_PC, THREAD_FPCSR)
OBJECT_BYTES, OBJECT_COUNT = 0x260, 240
MARIO_BYTES = 0xC8

# Where thread 5 stops and resumes differs per cartridge build: SimBuild, read off each build's own code (below).
NEVER = 0x80000800                             # an emu_start stop address nothing else reaches: routine calls return here
STACK_PAD = 16                                 # libultra's osCreateThread starts a thread's sp this far below its stack's top
USAMUNE_VI_OFFSET = 0x90                       # Usamune's own VI counter, in its state block
USAMUNE_IMAGE, USAMUNE_IMAGE_END = 0x80400000, 0x80424000   # Usamune's code, as its entry copies it (both builds)

STUB_NAMES = ("osRecvMesg", "osSendMesg", "osContStartReadData", "osContGetReadData", "osGetTime",
              "osWritebackDCache", "osWritebackDCacheAll", "osInvalDCache", "osInvalICache",
              "osPiStartDma", "osVirtualToPhysical", "osViSwapBuffer", "osViBlack", "osSetIntMask",
              "osEepromLongRead", "osEepromLongWrite", "osEepromRead", "osEepromWrite", "osEepromProbe",
              "osAiGetLength", "osAiSetNextBuffer", "osAiSetFrequency", "osViSetSpecialFeatures",
              "osSetTimer", "osStopTimer", "osGetCount", "wait_for_audio_frames")
MARIO_FIELDS = (
    (0x00, "playerID"), (0x02, "input"), (0x04, "flags"), (0x08, "particleFlags"), (0x0C, "action"),
    (0x10, "prevAction"), (0x14, "terrainSoundAddend"), (0x18, "actionState"), (0x1A, "actionTimer"),
    (0x1C, "actionArg"), (0x20, "intendedMag"), (0x24, "intendedYaw"), (0x26, "invincTimer"),
    (0x28, "framesSinceA"), (0x29, "framesSinceB"), (0x2A, "wallKickTimer"), (0x2B, "doubleJumpTimer"),
    (0x2C, "faceAngle"), (0x32, "angleVel"), (0x38, "slideYaw"), (0x3A, "twirlYaw"), (0x3C, "pos"),
    (0x48, "vel"), (0x54, "forwardVel"), (0x58, "slideVelX"), (0x5C, "slideVelZ"), (0x60, "wall"),
    (0x64, "ceil"), (0x68, "floor"), (0x6C, "ceilHeight"), (0x70, "floorHeight"), (0x74, "floorAngle"),
    (0x76, "waterLevel"), (0x78, "interactObj"), (0x7C, "heldObj"), (0x80, "usedObj"), (0x84, "riddenObj"),
    (0x88, "marioObj"), (0x8C, "spawnInfo"), (0x90, "area"), (0x94, "statusForCamera"), (0x98, "marioBodyState"),
    (0x9C, "controller"), (0xA0, "animation"), (0xA4, "collidedObjInteractTypes"), (0xA8, "numCoins"),
    (0xAA, "numStars"), (0xAC, "numKeys"), (0xAE, "numLives"), (0xB0, "health"), (0xB2, "unkB0"),
    (0xB4, "hurtCounter"), (0xB5, "healCounter"), (0xB6, "squishTimer"), (0xB7, "fadeWarpOpacity"),
    (0xB8, "capTimer"), (0xBA, "prevNumStarsForDialog"), (0xBC, "peakHeight"), (0xC0, "quicksandDepth"),
    (0xC4, "unkC4"))
OBJECT_FIELDS = (  # struct Object rows the report names; rawData starts at 0x88
    (0x00, "header.gfx"), (0x60, "header.next"), (0x68, "parentObj"), (0x6C, "prevObj"),
    (0x70, "collidedObjInteractTypes"), (0x74, "activeFlags"), (0x76, "numCollidedObjs"), (0x78, "collidedObjs"),
    (0x88, "rawData"), (0x1C8, "unused1"), (0x1CC, "bhvStackIndex"), (0x1D0, "bhvStack"), (0x1F0, "bhvDelayTimer"),
    (0x1F2, "respawnInfoType"), (0x1F4, "hitboxRadius"), (0x1F8, "hitboxHeight"), (0x1FC, "hurtboxRadius"),
    (0x200, "hurtboxHeight"), (0x204, "hitboxDownOffset"), (0x208, "curBhvCommand"), (0x20C, "behavior"),
    (0x210, "unused2"), (0x214, "platform"), (0x218, "collisionData"), (0x21C, "transform"), (0x25C, "respawnInfo"))


def lab_root() -> Path:
    return tap.lab_root()


def be32(data) -> np.ndarray:
    """Project64's word order <-> N64 big-endian bytes (its own inverse)."""
    return np.frombuffer(data, "<u4").astype(">u4").view(np.uint8)


def phys(address: int) -> int:
    return address & 0x1FFFFFFF


def sext(address: int) -> int:
    address &= 0xFFFFFFFF
    return address | 0xFFFFFFFF00000000 if address & 0x80000000 else address


class Symbols:
    def __init__(self, map_path: Path):
        pattern = re.compile(r"^\s+0x00000000([0-9a-f]{8})\s+([A-Za-z_]\w*)\s*$")
        self.by_name: dict[str, int] = {}
        pairs = {}
        for line in map_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if match := pattern.match(line):
                address = int(match[1], 16)
                self.by_name.setdefault(match[2], address)
                if address >= 0x80000000:
                    pairs.setdefault(address, match[2])
        self.addresses = sorted(pairs)
        self.names = [pairs[a] for a in self.addresses]

    def __getitem__(self, name: str) -> int:
        return self.by_name[name]

    def name_of(self, address: int) -> str:
        at = bisect.bisect_right(self.addresses, address) - 1
        return self.names[at] if at >= 0 else "?"

    def next_after(self, address: int) -> int | None:
        """The address of the first symbol past `address`: where a symbol's own bytes end."""
        at = bisect.bisect_right(self.addresses, address)
        return self.addresses[at] if at < len(self.addresses) else None


# --- one cartridge build's own facts ---------------------------------------------------------------
# US (v1.93u) and JP (v1.93j) lay their code out differently, so where thread 5 stops and resumes is read off the
# build's own code each time, never fixed: a module-wide value would serve one build and silently misplace the
# other (the atlas and the stage warm hold both in one process). Measured 2026-11-09 on both cold boots: these
# readings give US 0x802480F4 / 0x80248B70 / 0x80248BE0 / sp 0x80206DC8, JP 0x802480C4 / 0x80248B40 / 0x80248BB0 /
# sp 0x802071C8, and over 120 frames the synthetic resume equals the cold boot's own thread 5 byte for byte.
J_OP, BEQ_ZERO, ADDIU_SP, SW_RA, LW_A0_SP = 0x03, 0x1000, 0x27BD, 0xAFBF, 0x8FA4   # MIPS words: high halves


@dataclass(frozen=True)
class SimBuild:
    """Where one build's thread 5 stops and resumes (scene tap moment, loop frames), and the RAM it keeps there."""
    version: str            # "us" | "jp"
    resume: int             # display_and_vsync, after jal send_display_list: the scene tap's moment
    loop_head: int          # thread5_game_loop's loop head (rendering_init returns here)
    loop_return: int        # thread5_game_loop, after jal display_and_vsync
    thread5_sp: int         # thread5_game_loop's frame
    vsync_sp: int           # display_and_vsync's frame, inside it
    script_slot: int        # where thread 5 keeps the level script address, from thread5_sp
    return_slot: int        # where display_and_vsync keeps its return address, from vsync_sp
    usamune_vi_count: int   # Usamune's own VI counter (its state block + USAMUNE_VI_OFFSET)
    scene_bss: int          # the captured span that holds gControllers and gMarioStates (layout scene_bss_start)
    site: int | None        # the jal into Usamune's save and load dispatcher (None: no Usamune code there)


def version_of(rom: bytes) -> str | None:
    """The cartridge's build from its header: "us", "jp" or None."""
    from sm64_events.memory.version_probe import version_from_header  # noqa: PLC0415
    return version_from_header(bytes(rom[:0x40])) if len(rom) >= 0x40 else None


def _word(ram: np.ndarray, address: int) -> int:
    return struct.unpack_from(">I", ram, phys(address))[0]


def jal_site_after(ram: np.ndarray, function: int, callee: int, limit: int = 0x400) -> int:
    """The address after the delay slot of the first `jal callee` inside `function`."""
    jal = 0x0C000000 | (phys(callee) >> 2)
    words = np.frombuffer(ram[phys(function):phys(function) + limit].tobytes(), ">u4")
    hits = np.flatnonzero(words == jal)
    if not hits.size:
        raise SystemExit(f"no jal {callee:08x} in {function:08x}")
    return function + int(hits[0]) * 4 + 8


def _frame_bytes(ram: np.ndarray, function: int) -> int:
    """A function's frame size, from its first word (addiu sp, sp, -N)."""
    word = _word(ram, function)
    if word >> 16 != ADDIU_SP or not word & 0x8000:
        raise SystemExit(f"{function:08x} does not open with a stack frame")
    return 0x10000 - (word & 0xFFFF)


def _usamune_site(ram: np.ndarray, symbols) -> int | None:
    start = symbols["try_print_debug_mario_level_info"]
    words = np.frombuffer(ram[phys(start):phys(start) + 0x200].tobytes(), ">u4")
    for i, word in enumerate(words.tolist()):
        if word >> 26 == J_OP and USAMUNE_IMAGE <= (0x80000000 | (word & 0x3FFFFFF) << 2) < USAMUNE_IMAGE_END:
            return start + i * 4
    return None


def read_build(ram: np.ndarray, symbols, version: str) -> SimBuild:
    """This build's SimBuild from the game code in `ram` (IPL3's copy of the main segment is enough) and its map."""
    from sm64_events.memory.layout import layout_for  # noqa: PLC0415
    layout = layout_for(version)
    resume = jal_site_after(ram, symbols["display_and_vsync"], symbols["send_display_list"])
    loop = symbols["thread5_game_loop"]
    loop_return = jal_site_after(ram, loop, symbols["display_and_vsync"])
    loop_head = None
    for at in range(loop_return, loop_return + 0x100, 4):              # the loop's closing `b head`
        word = _word(ram, at)
        target = at + 4 + ((word & 0xFFFF) - (0x10000 if word & 0x8000 else 0)) * 4
        if word >> 16 == BEQ_ZERO and target < loop_return:
            loop_head = target
            break
    script_call = jal_site_after(ram, loop, symbols["level_script_execute"]) - 4   # its delay slot loads the script
    script = _word(ram, script_call)
    stack = symbols.next_after(symbols["gThread5Stack"]) if hasattr(symbols, "next_after") else None
    if loop_head is None or script >> 16 != LW_A0_SP or stack is None:
        raise SystemExit(f"thread5_game_loop at {loop:08x} is not the loop this simulator resumes")
    vsync = symbols["display_and_vsync"]
    saves_ra = [_word(ram, vsync + 4 * i) for i in range(1, 4) if _word(ram, vsync + 4 * i) >> 16 == SW_RA]
    if not saves_ra:
        raise SystemExit(f"display_and_vsync at {vsync:08x} does not save its return address")
    thread5_sp = stack - STACK_PAD - _frame_bytes(ram, loop)
    return SimBuild(version, resume, loop_head, loop_return, thread5_sp, thread5_sp - _frame_bytes(ram, vsync),
                    script & 0xFFFF, saves_ra[0] & 0xFFFF, layout.usamune_state_start + USAMUNE_VI_OFFSET,
                    layout.scene_bss_start, _usamune_site(ram, symbols))


# Names the simulator reads that STROOP's JP map does not name, each read off the build's own code (measured
# 2026-11-09: on US both readings equal the map's sCurrentCmd 0x8038BE28 and gRandomSeed16 0x8038EEE0, and on JP
# they give the same two addresses; JP's engine code differs from US's, so this is read, never assumed).
LUI, ADDIU, ORI, SW, LHU = 0x0F, 0x09, 0x0D, 0x2B, 0x25


def _stores_of(ram: np.ndarray, function: int, opcode: int, rt: int | None, words: int = 64) -> list[int]:
    """Absolute addresses `function` reaches with `opcode` (and register rt) through lui-based pointers."""
    high: dict[int, int] = {}
    found = []
    for i in range(words):
        word = _word(ram, function + 4 * i)
        op, rs, target, imm = word >> 26, (word >> 21) & 31, (word >> 16) & 31, word & 0xFFFF
        signed = imm - 0x10000 if imm & 0x8000 else imm
        if op == LUI:
            high[target] = imm << 16
        elif op == ADDIU and rs in high:
            high[target] = (high[rs] + signed) & 0xFFFFFFFF
        elif op == ORI and rs in high:
            high[target] = high[rs] | imm
        elif op == opcode and rs in high and (rt is None or target == rt):
            found.append((high[rs] + signed) & 0xFFFFFFFF)
    return found


def complete_symbols(symbols, ram: np.ndarray) -> None:
    """Add what the map lacks of sCurrentCmd (level_script_execute's first word store through a lui pointer: it
    stores its argument there, after the halfword sScriptStatus) and gRandomSeed16 (random_u16's first `lhu`), once
    the engine segment holding both functions is in `ram`. A name the map has stays the map's."""
    by_name = getattr(symbols, "by_name", None)
    if by_name is None:
        return
    readers = {"sCurrentCmd": ("level_script_execute", SW, None), "gRandomSeed16": ("random_u16", LHU, None)}
    for name, (function, opcode, rt) in readers.items():
        if name in by_name or function not in by_name or not _word(ram, by_name[function]):
            continue
        hits = _stores_of(ram, by_name[function], opcode, rt)
        if hits:
            by_name[name] = hits[0] | 0x80000000


def field_of(table, offset: int) -> str:
    name = table[0][1]
    for start, row in table:
        if offset >= start:
            name = row
    return name


def label(symbols: Symbols, address: int) -> str:
    """Where a RAM difference lies: symbol, object slot and field, or Mario's field."""
    virtual = address | 0x80000000
    mario, pool = symbols["gMarioStates"], symbols["gObjectPool"]
    if 0x8005C010 <= virtual < 0x801C0FF0:
        return f"main pool {(virtual - 0x8005C010) >> 16:02x}xxxx"
    if mario <= virtual < mario + MARIO_BYTES:
        return f"gMarioStates.{field_of(MARIO_FIELDS, virtual - mario)}"
    if pool <= virtual < pool + OBJECT_COUNT * OBJECT_BYTES:
        slot, offset = divmod(virtual - pool, OBJECT_BYTES)
        return f"object[{slot}].{field_of(OBJECT_FIELDS, offset)}"
    return symbols.name_of(virtual)


def category(symbols: Symbols, address: int) -> str:
    virtual = address | 0x80000000
    mario, pool = symbols["gMarioStates"], symbols["gObjectPool"]
    if mario <= virtual < mario + MARIO_BYTES:
        return "mario"
    if pool <= virtual < pool + OBJECT_COUNT * OBJECT_BYTES:
        return "objects"
    if 0x8005C010 <= virtual < 0x801C0FF0:
        return "main_pool"
    return "other_span"


# --- the session -------------------------------------------------------------------------------
# The identity extras of a session armed before its arming named them (tap.identity_spec): Usamune US's.
US_IDENTITY = {"global_timer": (0x32D5D4, 4), "curr_level": (0x32DDF8, 2), "curr_area": (0x33BACA, 2)}


class Frame:
    __slots__ = ("index", "timer", "level", "area", "qpc", "spans", "level_blocks", "level_key", "segments")


def frames_of(session: Path):
    """The tap's tasks in order, each with its spans in big-endian bytes, without its picture. The identity
    extras are read where the session's arming put them (its `# identity=` line: either build's addresses);
    a session armed before that line existed is a US one."""
    identity = dict(US_IDENTITY)
    if (session / "armed.txt").is_file():
        identity.update({name: (address, size) for name, address, size in tap.identity_spec(session)})
    path = session / "tap.bin"
    with path.open("rb") as handle, mmap.mmap(handle.fileno(), 0, access=mmap.ACCESS_READ) as data:
        frame = None
        for at, kind, size, sequence, qpc, _flags in tap._walk(data, torn_ok=False):
            if kind == tap.REC_PRESENT or kind == tap.REC_PAD:
                continue
            with memoryview(data)[at + 32:at + size] as body:
                record = tap._parse_record(kind, sequence, qpc, body)
            if isinstance(record, tuple):
                yield ("full", be32(record[2][:RDRAM_BYTES]))
            elif isinstance(record, tap.Task):
                if frame is not None:
                    yield frame
                frame = Frame()
                frame.index = record.fields["task_index"]
                frame.timer = tap.word_value(record.extras, *identity["global_timer"])
                frame.level = tap.word_value(record.extras, *identity["curr_level"])
                frame.area = tap.word_value(record.extras, *identity["curr_area"])
                frame.qpc = qpc
                frame.spans, frame.level_blocks, frame.level_key = [], [], False
                frame.segments = ()
            elif isinstance(record, tap.Regions):
                pieces = frame.spans if record.kind == "task" else frame.level_blocks
                frame.level_key = frame.level_key or record.kind == "level_key"
                frame.segments = record.segments
                offset = 0
                for begin, length in record.spans:
                    pieces.append((begin, be32(record.data[offset:offset + length])))
                    offset += length
        if frame is not None:
            yield frame


# --- the CPU -----------------------------------------------------------------------------------
class GameCpu:
    """Thread 5 alone over a private RDRAM. Messages it waits for arrive at once; time stands still. `build` is the
    cartridge's own SimBuild, read off the code in `ram` unless given; `extra_stubs` names more libultra calls to
    answer (cold_boot's thread and device calls) for this CPU only."""

    def __init__(self, ram: np.ndarray, rom: bytes, symbols: Symbols, fcsr: int, count_blocks: bool = False, *,
                 build: SimBuild | None = None, extra_stubs: tuple[str, ...] = ()):
        import unicorn
        from unicorn import mips_const
        self.unicorn, self.mips = unicorn, mips_const
        self.symbols, self.rom = symbols, rom
        if build is None:
            version = version_of(rom)
            if version is None:
                raise SystemExit("the ROM is not a Usamune US or JP cartridge")
            build = read_build(ram, symbols, version)
        self.build = build
        complete_symbols(symbols, ram)
        self.uc = uc = unicorn.Uc(unicorn.UC_ARCH_MIPS, unicorn.UC_MODE_MIPS64 | unicorn.UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(mips_const.UC_CPU_MIPS64_R4000)
        backing = np.zeros(RDRAM_BYTES + 4096, np.uint8)
        skip = -backing.ctypes.data % 4096
        self.ram = backing[skip:skip + RDRAM_BYTES]
        self.ram[:] = ram
        uc.mem_map_ptr(0, RDRAM_BYTES, unicorn.UC_PROT_ALL, self.ram.ctypes.data)
        uc.reg_write(mips_const.UC_MIPS_REG_CP0_STATUS, STATUS_CU1)
        uc.reg_write(mips_const.UC_MIPS_REG_FCSR, fcsr)
        self.pads = bytes(24)
        self.calls: collections.Counter = collections.Counter()
        self.blocked: collections.Counter = collections.Counter()
        self.violations: list[str] = []
        self.dma_bytes = 0
        self.instructions = 0
        self.flush_due = False
        self.clock, self.time_step = 0, 0
        self.trace: collections.deque = collections.deque(maxlen=400)
        handlers = {"osRecvMesg": self._recv, "osSendMesg": self._send_mesg, "osContGetReadData": self._pads,
                    "osPiStartDma": self._dma, "osVirtualToPhysical": lambda: phys(self.arg(0)),
                    "osInvalICache": self._flush, "wait_for_audio_frames": self._audio_frames,
                    "osEepromLongRead": self._eeprom_read,
                    "osEepromRead": self._eeprom_read, "osGetTime": self._time}
        for name in dict.fromkeys(STUB_NAMES + tuple(extra_stubs)):
            if name not in symbols.by_name:
                continue
            handler = handlers.get(name, lambda: 0)
            at = sext(symbols[name])
            uc.hook_add(unicorn.UC_HOOK_CODE, self._stub(name, handler), begin=at, end=at)
        uc.hook_add(unicorn.UC_HOOK_CODE, self._on_resume_point, begin=sext(build.resume), end=sext(build.resume))
        uc.hook_add(unicorn.UC_HOOK_INTR, self._on_exception)
        if count_blocks:
            uc.hook_add(unicorn.UC_HOOK_BLOCK, self._on_block)

    def _on_block(self, _uc, address, size, _user):
        self.instructions += size // 4
        self.trace.append(address & 0xFFFFFFFF)

    def _time(self) -> int:
        """osGetTime: stands still by default (a u64 in v0:v1). time_step > 0 makes it run, for diagnosis."""
        self.clock += self.time_step
        self.uc.reg_write(self.mips.UC_MIPS_REG_V1, sext(self.clock & 0xFFFFFFFF))
        return (self.clock >> 32) & 0xFFFFFFFF

    def _audio_frames(self) -> int:
        """A level load spins on gAudioFrameCount, which only the audio thread counts: those frames pass."""
        self.put_u32(self.symbols["gAudioFrameCount"], self.arg(0))
        return 0

    def _flush(self) -> int:
        self.flush_due = True      # flushing inside a hook loses the run; see run_frame
        return 0

    def _eeprom_read(self) -> int:
        self.violations.append("EEPROM read: the probe holds no EEPROM")
        return 0

    def _stub(self, name, handler):
        v0, pc, ra = self.mips.UC_MIPS_REG_V0, self.mips.UC_MIPS_REG_PC, self.mips.UC_MIPS_REG_RA

        def hook(uc, _address, _size, _user):
            self.calls[name] += 1
            uc.reg_write(v0, sext(handler() or 0))
            uc.reg_write(pc, uc.reg_read(ra))
        return hook

    def arg(self, index: int) -> int:
        if index < 4:
            return self.uc.reg_read(self.mips.UC_MIPS_REG_A0 + index) & 0xFFFFFFFF
        sp = self.uc.reg_read(self.mips.UC_MIPS_REG_SP) & 0xFFFFFFFF
        return self.u32(sp + 4 * index)

    def u32(self, address: int) -> int:
        return struct.unpack_from(">I", self.ram, phys(address))[0]

    def put_u32(self, address: int, value: int) -> None:
        struct.pack_into(">I", self.ram, phys(address), value & 0xFFFFFFFF)

    def _send(self, queue: int, message: int) -> int:
        valid, first, count, messages = struct.unpack_from(">iiiI", self.ram, phys(queue) + 8)
        if valid >= count:
            return 0xFFFFFFFF
        self.put_u32(messages + 4 * ((first + valid) % count), message)
        struct.pack_into(">i", self.ram, phys(queue) + 8, valid + 1)
        return 0

    def _send_mesg(self) -> int:
        return self._send(self.arg(0), self.arg(1))

    def _recv(self) -> int:
        queue, out, block = self.arg(0), self.arg(1), self.arg(2)
        valid, first, count, messages = struct.unpack_from(">iiiI", self.ram, phys(queue) + 8)
        if valid == 0:
            if not block:
                return 0xFFFFFFFF
            self.blocked[self.symbols.name_of(queue)] += 1   # what would wake it arrives at once
            if out:
                self.put_u32(out, 0)
            return 0
        message = self.u32(messages + 4 * first)
        struct.pack_into(">ii", self.ram, phys(queue) + 8, valid - 1, (first + 1) % count)
        if out:
            self.put_u32(out, message)
        return 0

    def _pads(self) -> int:
        target = phys(self.arg(0))
        self.ram[target:target + 24] = np.frombuffer(self.pads, np.uint8)
        return 0

    def _dma(self) -> int:
        message, direction, device, target, size, queue = (self.arg(0), self.arg(2), self.arg(3),
                                                           self.arg(4), self.arg(5), self.arg(6))
        if direction != 0 or device + size > len(self.rom):
            self.violations.append(f"PI DMA dir={direction} dev={device:08x} size={size}")
            return 0xFFFFFFFF
        self.ram[phys(target):phys(target) + size] = np.frombuffer(self.rom, np.uint8, size, device)
        self.dma_bytes += size
        if queue:
            self._send(queue, message)
        return 0

    def _on_exception(self, uc, number, _user):
        pc = uc.reg_read(self.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
        self.violations.append(f"exception {number} at {pc:08x} in {self.symbols.name_of(pc)}")
        uc.emu_stop()

    def perturb(self, what: str, value: int) -> None:
        """A control run: this change must show up in the comparison, or the comparison is blind."""
        if what == "seed":
            struct.pack_into(">H", self.ram, phys(self.symbols["gRandomSeed16"]), value & 0xFFFF)
        elif what == "history":
            # What a level load must rebuild by itself: every object and Mario's struct are wiped.
            pool, mario = phys(self.symbols["gObjectPool"]), phys(self.symbols["gMarioStates"])
            self.ram[pool:pool + OBJECT_BYTES * OBJECT_COUNT] = value & 0xFF
            self.ram[mario:mario + MARIO_BYTES] = value & 0xFF
        elif what == "stick":
            self.pads = bytes([self.pads[0], self.pads[1], value & 0xFF]) + self.pads[3:]
        else:
            raise SystemExit(f"nothing named {what} to perturb")

    def enter_resume(self, script_address: int) -> None:
        """Thread 5 as the tap leaves it: only the stack frames matter; no saved register is live."""
        build = self.build
        self.put_u32(build.vsync_sp + build.return_slot, build.loop_return)
        self.put_u32(build.thread5_sp + build.script_slot, script_address)
        self.uc.reg_write(self.mips.UC_MIPS_REG_SP, sext(build.vsync_sp))

    @property
    def resume(self) -> int:
        return self.build.resume

    def script_held(self) -> bool:
        """Thread 5's own copy of the level script address equals the interpreter's."""
        return self.u32(self.build.thread5_sp + self.build.script_slot) == self.u32(self.symbols["sCurrentCmd"])

    def scheduler_model(self) -> None:
        """What thread 3 leaves by the time the graphics task has finished."""
        for name in ("gActiveSPTask", "sCurrentDisplaySPTask", "sNextDisplaySPTask", "sCurrentAudioSPTask"):
            self.put_u32(self.symbols[name], 0)

    def _on_resume_point(self, uc, _address, _size, _user):
        # emu_start's `until` is honored only when the block is translated, so a block translated
        # under another `until` runs straight through it: a hook is the one reliable stop.
        if self.armed:
            uc.emu_stop()
        self.armed = True

    def run_frame(self, begin: int | None = None) -> bool:
        begin = self.build.resume if begin is None else begin
        self.armed = begin != self.build.resume
        try:
            self.uc.emu_start(sext(begin), sext(NEVER), timeout=BUDGET_US)
        except self.unicorn.UcError as error:
            pc = self.uc.reg_read(self.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
            self.violations.append(f"unicorn {error} at {pc:08x} in {self.symbols.name_of(pc)}")
            return False
        pc = self.uc.reg_read(self.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
        if pc != self.build.resume:
            self.violations.append(f"stopped at {pc:08x} in {self.symbols.name_of(pc)}")
            return False
        if self.flush_due:
            self.uc.ctl_flush_tb()
            self.flush_due = False
        return not self.violations


# --- comparison --------------------------------------------------------------------------------
def diff_runs(sim: np.ndarray, real: np.ndarray, base: int) -> np.ndarray:
    return np.flatnonzero(sim[base:base + real.size] != real) + base


def compare(cpu: GameCpu, spans, pool: np.ndarray, pool_known: bool) -> np.ndarray:
    pieces = [diff_runs(cpu.ram, data, begin) for begin, data in spans if data.size != TAIL_BYTES]
    if pool_known:
        pieces.append(diff_runs(cpu.ram, pool, POOL_BEGIN))
    return np.concatenate(pieces) if pieces else np.zeros(0, np.int64)


POOL_BEGIN, POOL_BYTES = 0x5C010, 1462240
TAIL_BYTES = 4096                              # the task region: the gfx pool's tail


def relation(sim: int, real: int) -> str:
    """How a differing word relates, without its value."""
    if sim == 0:
        return "sim zero"
    if real == 0:
        return "real zero"
    if abs(sim - real) < 16:
        return "near (under 16 apart)"
    if 0x80000000 <= sim < 0x80800000 and 0x80000000 <= real < 0x80800000:
        return "both RDRAM pointers"
    return "unrelated"


def real_word(frame: Frame, pool: np.ndarray, word: int) -> int | None:
    if POOL_BEGIN <= word < POOL_BEGIN + POOL_BYTES - 3:
        return int.from_bytes(pool[word - POOL_BEGIN:word - POOL_BEGIN + 4].tobytes(), "big")
    for begin, data in frame.spans:
        if data.size != TAIL_BYTES and begin <= word and word + 4 <= begin + data.size:
            return int.from_bytes(data[word - begin:word - begin + 4].tobytes(), "big")
    return None


def boot_state(session: Path):
    """The session's boot record, and the stream of tasks after it."""
    stream = frames_of(session)
    first = next(stream)
    if not (isinstance(first, tuple) and first[0] == "full"):
        raise SystemExit(f"{session}: no boot record")
    return first[1].copy(), stream


class Tally:
    """What the comparison found: per frame for the report, per word and label in total."""

    def __init__(self, symbols: Symbols, report: dict):
        self.symbols, self.report = symbols, report
        self.totals: collections.Counter = collections.Counter()
        self.frames_with: collections.Counter = collections.Counter()
        self.first_seen: dict[str, int] = {}
        self.first_divergence: dict[str, int] = {}
        self.words: dict[int, dict] = {}
        self.last_pos: bytes | None = None
        self.mario_moved = 0
        self.actions: set[bytes] = set()

    def _add_labels(self, frame: Frame, where: np.ndarray) -> tuple[dict, dict]:
        labels: collections.Counter = collections.Counter()
        cats: collections.Counter = collections.Counter()
        for address in where.tolist():
            cats[category(self.symbols, address)] += 1
            labels[label(self.symbols, address)] += 1
        for name, count in labels.items():
            self.totals[name] += count
            self.frames_with[name] += 1
            self.first_seen.setdefault(name, frame.index)
        for name in cats:
            self.first_divergence.setdefault(name, frame.index)
        return dict(cats), dict(labels)

    def _add_words(self, cpu: GameCpu, frame: Frame, pool: np.ndarray, where: np.ndarray) -> None:
        for word in sorted(set((where & ~3).tolist())):
            row = self.words.setdefault(word, {"frames": 0, "first": frame.index, "last": frame.index,
                                               "relations": collections.Counter()})
            row["frames"] += 1
            row["last"] = frame.index
            real_value = real_word(frame, pool, word)
            if real_value is not None:
                row["relations"][relation(cpu.u32(word), real_value)] += 1

    def _mario_moved(self, frame: Frame, scene_bss: int) -> bool:
        span = next(data for begin, data in frame.spans if begin == phys(scene_bss))
        at = phys(self.symbols["gMarioStates"]) - phys(scene_bss)
        pos, action = span[at + 0x3C:at + 0x48].tobytes(), span[at + 0x0C:at + 0x10].tobytes()
        moved = self.last_pos is not None and pos != self.last_pos
        self.mario_moved += moved
        self.actions.add(action)
        self.last_pos = pos
        return moved

    def add(self, cpu: GameCpu, frame: Frame, pool: np.ndarray, seed_moved: bool, instructions: int) -> int:
        where = compare(cpu, frame.spans, pool, pool_known=True)
        tail = sum(int(diff_runs(cpu.ram, data, begin).size) for begin, data in frame.spans if data.size == TAIL_BYTES)
        cats, labels = self._add_labels(frame, where)
        self._add_words(cpu, frame, pool, where)
        held = cpu.script_held()
        self.report["frames"].append({
            "index": frame.index, "timer": frame.timer, "level": frame.level, "area": frame.area,
            "mario_moved": self._mario_moved(frame, cpu.build.scene_bss), "seed_moved": seed_moved, "bytes": int(where.size),
            "gfx_tail_bytes": tail, "script_held": held, "by_category": cats, "by_label": labels,
            "instructions": instructions})
        return int(where.size)


def first_entry(cpu: GameCpu, symbols: Symbols, current: Frame) -> int:
    """Where the run enters: a session's first task is rendering_init's own, ahead of the loop."""
    if current.index != 0:
        cpu.enter_resume(cpu.u32(symbols["sCurrentCmd"]))
        return cpu.build.resume
    jal = 0x0C000000 | (phys(symbols["send_display_list"]) >> 2)
    lo, hi = phys(symbols["rendering_init"]), phys(symbols["config_gfx_pool"])
    sites = np.flatnonzero(np.frombuffer(cpu.ram[lo:hi].tobytes(), ">u4") == jal)
    if sites.size != 1:
        raise SystemExit("rendering_init should send one display list")
    cpu.uc.reg_write(cpu.mips.UC_MIPS_REG_SP, sext(cpu.build.vsync_sp))
    return symbols["rendering_init"] + int(sites[0]) * 4 + 8


def captured_word(frame: Frame, address: int) -> int:
    at = phys(address)
    for begin, data in frame.spans:
        if data.size != TAIL_BYTES and begin <= at and at + 4 <= begin + data.size:
            return int.from_bytes(data[at - begin:at - begin + 4].tobytes(), "big")
    raise SystemExit(f"{address:08x} lies outside the spans")


def outside_spans_changed(cpu: GameCpu, boot: np.ndarray, frame: Frame, symbols: Symbols) -> collections.Counter:
    """What the tap's spans never show: bytes the simulation changed outside them since the boot record."""
    covered = np.zeros(RDRAM_BYTES, bool)
    covered[POOL_BEGIN:POOL_BEGIN + POOL_BYTES] = True
    for begin, data in frame.spans:
        if data.size != TAIL_BYTES:
            covered[begin:begin + data.size] = True
    outside = np.flatnonzero(cpu.ram != boot)
    groups: collections.Counter = collections.Counter()
    for address in outside[~covered[outside]].tolist():
        groups[symbols.name_of(address | 0x80000000)] += 1
    return groups


class Corrector:
    """What each mode writes back between frames (see the module docstring)."""

    def __init__(self, args, cpu: GameCpu, boot: np.ndarray, pool: np.ndarray):
        self.mode, self.from_frame = args.mode, args.from_frame
        self.cpu, self.boot, self.pool = cpu, boot, pool
        self.seed_at = phys(cpu.symbols["gRandomSeed16"])

    def overlay(self, frame: Frame) -> None:
        for begin, data in frame.spans:
            if data.size != TAIL_BYTES:    # the gfx-pool tail is presentation, in another buffer each frame
                self.cpu.ram[begin:begin + data.size] = data
        self.cpu.ram[POOL_BEGIN:POOL_BEGIN + POOL_BYTES] = self.pool

    def _rebuild(self, frame: Frame, keep_seed: bool) -> None:
        seed = self.cpu.ram[self.seed_at:self.seed_at + 2].copy()
        self.cpu.ram[:] = self.boot
        self.overlay(frame)
        if keep_seed:
            self.cpu.ram[self.seed_at:self.seed_at + 2] = seed
        self.cpu.uc.ctl_flush_tb()

    def after(self, frame: Frame) -> None:
        corrected = self.mode in ("carry", "stale") or (self.mode == "stale-seed" and frame.index >= self.from_frame)
        if self.mode == "carry":
            self.overlay(frame)
        elif corrected:
            self._rebuild(frame, keep_seed=self.mode == "stale-seed")
        sp = self.cpu.uc.reg_read(self.cpu.mips.UC_MIPS_REG_SP) & 0xFFFFFFFF
        if corrected and sp == self.cpu.build.vsync_sp:
            self.cpu.enter_resume(self.cpu.u32(self.cpu.symbols["sCurrentCmd"]))
        self.cpu.scheduler_model()


def print_summary(args, report: dict, tally: Tally, cpu: GameCpu, seconds: float, out: Path) -> None:
    done, rows = report["frames_run"], report["frames"]
    print(f"frames run {done}; {seconds / max(done, 1) * 1000:.1f} ms per frame; "
          f"first divergence by category: {tally.first_divergence}")
    print(f"frames with a differing gfx-pool tail: {sum(1 for row in rows if row['gfx_tail_bytes'])}; "
          f"script address held at 0x24(sp) on {sum(row['script_held'] for row in rows)} frames")
    print(f"frames Mario moved {tally.mario_moved}, distinct actions {len(tally.actions)}, "
          f"frames gRandomSeed16 moved {report['seed_moved']}")
    if args.count:
        print(f"instructions per frame: mean {cpu.instructions / max(done, 1):.0f}, "
              f"max {max(row['instructions'] for row in rows)}")
    print(f"blocking receives answered: {dict(cpu.blocked)}")
    print(f"calls: {dict(cpu.calls)}")
    print(f"violations: {cpu.violations[:5]}")
    for name, count in tally.totals.most_common(args.top):
        print(f"  {name}: {count} bytes over {tally.frames_with[name]} frames, first at {tally.first_seen[name]}")
    print(f"report: {out}")


def finish_report(args, report: dict, tally: Tally, cpu: GameCpu, groups: collections.Counter,
                  seconds: float, done: int) -> None:
    report["words"] = {f"{0x80000000 | word:08x}": {**row, "relations": dict(row["relations"]),
                                                    "name": label(cpu.symbols, word)}
                       for word, row in tally.words.items()}
    report.update(outside_spans_changed=dict(groups.most_common(60)), mario_moved=tally.mario_moved,
                  distinct_actions=len(tally.actions), seed_moved=sum(row["seed_moved"] for row in report["frames"]),
                  frames_run=done, seconds_per_frame=seconds / max(done, 1), calls=dict(cpu.calls),
                  blocked=dict(cpu.blocked), violations=cpu.violations, first_divergence=tally.first_divergence,
                  totals=dict(tally.totals), frames_with=dict(tally.frames_with), first_seen=tally.first_seen,
                  dma_bytes=cpu.dma_bytes)
    out = lab_root() / f"handoff-sim-{args.out or args.mode}" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report), encoding="utf-8")
    print_summary(args, report, tally, cpu, seconds, out)


def inject_vi(cpu: GameCpu, frame: Frame, last_vi: int) -> int:
    """Diagnosis only: thread 3's VI count, which no ghost holds, and Usamune's VI counter, which its
    end-of-loop hook differences into a lag count (state block +0x90, +0x94, +0x98)."""
    value = captured_word(frame, cpu.symbols["sNumVblanks"])
    cpu.put_u32(cpu.symbols["sNumVblanks"], value)
    counter = cpu.build.usamune_vi_count
    cpu.put_u32(counter, cpu.u32(counter) + value - last_vi)
    return value


def run(args) -> int:
    config = json.loads((lab_root() / "lab.json").read_text(encoding="utf-8"))
    rom = Path(config["rom"]).read_bytes()
    symbols = Symbols(Path(config["map"]))
    ram, stream = boot_state(lab_root() / args.session)
    frame0 = next(stream)
    fcsr = int(args.fcsr, 16) if args.fcsr is not None else struct.unpack_from(
        ">I", ram, phys(symbols["gGameLoopThread"]) + THREAD_FPCSR)[0]
    build = read_build(ram, symbols, version_of(rom) or "us")
    check_frame(ram, symbols, frame0, build)
    boot = ram.copy()
    pool = np.zeros(POOL_BYTES, np.uint8)

    def absorb(frame: Frame) -> None:
        for begin, data in frame.level_blocks:
            pool[begin - POOL_BEGIN:begin - POOL_BEGIN + data.size] = data

    absorb(frame0)
    current = frame0
    while current.index < args.start:
        current = next(stream)
        absorb(current)
    cpu = GameCpu(ram, rom, symbols, fcsr, count_blocks=args.count, build=build)
    cpu.time_step = args.time_step
    corrector = Corrector(args, cpu, boot, pool)
    corrector.overlay(current)          # for k > 0, the state is the boot record plus the spans alone
    begin = first_entry(cpu, symbols, current)
    cpu.scheduler_model()
    report = {"session": args.session, "mode": args.mode, "start": args.start, "fcsr_is_thread5": args.fcsr is None,
              "frames": []}
    tally = Tally(symbols, report)
    seconds, done, last_vi = 0.0, 0, cpu.u32(symbols["sNumVblanks"])
    pad_at, seed_at = phys(symbols["gControllerPads"]) - phys(build.scene_bss), corrector.seed_at
    for frame in stream:
        if done >= args.frames:
            break
        absorb(frame)
        pads = next(data for start, data in frame.spans if start == phys(build.scene_bss))
        cpu.pads = pads[pad_at:pad_at + 24].tobytes()
        if args.inject_vblanks:
            last_vi = inject_vi(cpu, frame, last_vi)
        if args.perturb and frame.index == args.perturb[0]:
            cpu.perturb(args.perturb[1], args.perturb[2])
        seed_before = cpu.ram[seed_at:seed_at + 2].tobytes()
        begun, before = time.perf_counter(), cpu.instructions
        ok = cpu.run_frame(begin)
        begin, seconds, done = build.resume, seconds + time.perf_counter() - begun, done + 1
        differing = tally.add(cpu, frame, pool, cpu.ram[seed_at:seed_at + 2].tobytes() != seed_before,
                              cpu.instructions - before)
        if args.verbose or not ok or frame.index % args.every == 0:
            print(f"frame {frame.index} level {frame.level}/{frame.area}: {differing} bytes differ "
                  f"{report['frames'][-1]['by_category']}", flush=True)
        if not ok:
            print("stopped:", cpu.violations[:5], flush=True)
            break
        corrector.after(frame)
    groups = outside_spans_changed(cpu, boot, current, symbols)
    print(f"bytes changed since the boot record outside the spans: {sum(groups.values())}; biggest {groups.most_common(14)}")
    finish_report(args, report, tally, cpu, groups, seconds, done)
    return 0


def check_frame(ram: np.ndarray, symbols: Symbols, frame0: Frame, build: SimBuild) -> None:
    """The boot record shows thread 5 where this probe says it is, or the premise is wrong."""
    return_to = struct.unpack_from(">I", ram, phys(build.vsync_sp + build.return_slot))[0]
    saved_pc = struct.unpack_from(">I", ram, phys(symbols["gGameLoopThread"]) + THREAD_PC)[0]
    print(f"thread 5 at the boot record: saved PC in {symbols.name_of(saved_pc)}; rendering_init returns "
          f"to {return_to:08x} (want {build.loop_head:08x})")
    if return_to != build.loop_head or symbols.name_of(saved_pc) != "osStartThread":
        raise SystemExit("the boot record does not show thread 5 where this probe resumes it")
    in_record = 0
    for begin, data in frame0.spans:
        in_record += int(np.count_nonzero(ram[begin:begin + data.size] != data))
    print(f"boot record vs first task's spans: {in_record} bytes differ")


def scan(_args) -> int:
    """What thread 5's code could read that this probe does not model: CP0 count, hardware registers."""
    from capstone import CS_ARCH_MIPS, CS_MODE_BIG_ENDIAN, CS_MODE_MIPS64, Cs
    lab = lab_root()
    config = json.loads((lab / "lab.json").read_text(encoding="utf-8"))
    symbols = Symbols(Path(config["map"]))
    session = lab / "tap-20261025-182310"
    ram, _ = boot_state(session)
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS64 | CS_MODE_BIG_ENDIAN)
    md.skipdata = True
    for name, lo, hi in (("usamune", 0x400000, 0x421C00), ("main text", 0x246000, 0x331000), ("engine text", 0x378800, 0x38B800)):
        count_reads, hw = [], []
        code = ram[lo:hi].tobytes()
        for insn in md.disasm(code, 0x80000000 | lo):
            if insn.mnemonic in ("mfc0", "dmfc0") and insn.op_str.split(", ")[1] == "$9":
                count_reads.append(insn.address)
            if insn.mnemonic == "lui":
                value = int(insn.op_str.split(",")[1], 0) & 0xFFFF
                if 0xA400 <= value <= 0xA4FF or 0xB000 <= value <= 0xBFFF:
                    hw.append(insn.address)
        print(name, "reads of CP0 count at", [f"{a:08x}:{symbols.name_of(a)}" for a in count_reads[:20]])
        print(name, "hardware-register lui in", sorted({symbols.name_of(a) if a < 0x80400000 else f"{a:08x}" for a in hw})[:60])
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("scan").set_defaults(handler=scan)
    runner = commands.add_parser("run")
    runner.add_argument("session")
    runner.add_argument("--mode", choices=("closed", "carry", "stale", "stale-seed"), default="closed")
    runner.add_argument("--from-frame", type=int, default=0, help="stale-seed: run closed up to this frame")
    runner.add_argument("--start", type=int, default=0)
    runner.add_argument("--frames", type=int, default=10)
    runner.add_argument("--every", type=int, default=50)
    runner.add_argument("--top", type=int, default=40)
    runner.add_argument("--out")
    runner.add_argument("--fcsr")
    runner.add_argument("--count", action="store_true", help="count instructions (slows the run)")
    runner.add_argument("--verbose", action="store_true")
    runner.add_argument("--time-step", type=int, default=0, help="diagnosis: osGetTime advances this much per call")
    runner.add_argument("--inject-vblanks", action="store_true", help="diagnosis: write the captured sNumVblanks each frame")
    runner.add_argument("--perturb", nargs=3, metavar=("FRAME", "WHAT", "VALUE"), help="control: at FRAME change seed|stick|history")
    runner.set_defaults(handler=run)
    args = parser.parse_args()
    if getattr(args, "perturb", None):
        args.perturb = (int(args.perturb[0]), args.perturb[1], int(args.perturb[2], 0))
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
