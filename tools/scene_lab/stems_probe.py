"""Music and effects stems from SM64's own audio command lists (M4 probe; CPU only, private data).

    uv run --with unicorn==2.1.4 python tools/scene_lab/stems_probe.py run <savestate.pj.zip>
        --rom <Usamune US.z64> --map <MappingUS.map> --hle-src <mupen64plus-rsp-hle/src>
        [--lle-src <mupen64plus-rsp-cxd4>] --out <scratch dir> [--seconds 6] [--sfx-demo]

The question: can the Scene viewer mute the music and the sound effects separately, and stay the
game's own sound? This probe answers the offline half from one Project64 savestate.

A savestate is taken at a VI with the next audio task dispatched but not yet run (the task sits in
gSPTaskMesgQueue), so RDRAM holds a complete RSP input: the command list in gAudioCmdBuffers and
every buffer it reads (note state, sample DMA buffers, codebooks, the reverb ring), and SP DMEM
holds what the last audio task left (some lists read DMEM they never wrote). The probe

1. runs that pending list, then SM64's own create_next_audio_frame_task in Unicorn for each later
   audio frame (one per VI) from the same RAM, the libultra calls it makes answered here: PI DMA
   copies ROM bytes, the AI length follows a modelled AI queue, cache calls do nothing;
2. renders each list with the ROM's own audio microcode (rspboot + aspMain) in cxd4's RSP
   interpreter (stems_lle.c), the reference, and with mupen64plus-rsp-hle's ABI1 commands
   (stems_hle.c), a comparison: each carries its own chain of RSP-owned bytes and DMEM;
3. renders two stems per list: every note of the other side keeps decoding, resampling and saving
   its state, but its mixing goes to scratch DMEM (edited_list: any RSP) or is rolled back (the
   HLE). A note belongs to the sequence player owning it (players 0 and 1 are music, 2 is the
   sound effect player; attribute names the note of every mix-writing command);
4. writes mix / music / effects WAVs at the game's 32006 Hz and a JSON report: list bytes, cost
   per task, how far music+effects is from the mix, microcode vs HLE, capture sizes.

--sfx-demo asks the game's own play_sound for a few effects (coins, Mario's voice) on game-loop
ticks, because a savestate's game thread is frozen: without it the effects stem only carries
what was already sounding. Those requests are synthetic; the synthesis of them is the game's.

Offline this proves the mechanism, not fidelity to a recording. The live proof gate (each task's
captured inputs re-run here, against the RSP output Project64 wrote, captured at the next VI)
needs the tap. RAM images and WAVs stay in scratch.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import lzma
import re
import struct
import sys
import tempfile
import time
import wave
from collections import deque
from pathlib import Path
from typing import NamedTuple

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from savestate_frames import TASK_NAMES, dmem_for, read_savestate, write_frames  # noqa: E402

HLE_REVISION = "8a7a472a7172eb2c8725b305eae26818ed7b51a2"   # mupen64plus-rsp-hle, GPL-2.0-or-later
HLE_FILES = ("alist.c", "audio.c", "memory.c")
LLE_REVISION = "00906a92641c540a64e4e7505012157315314810"   # mupen64plus-rsp-cxd4, CC0-1.0
LLE_FILES = ("su.c", "vu/vu.c", "vu/multiply.c", "vu/add.c", "vu/select.c", "vu/logical.c", "vu/divide.c")
RDRAM_BYTES = 8 << 20
SCRATCH_PHYS, SCRATCH_BYTES = 0x01000000, 1 << 20
SENTINEL, STACK_TOP = 0x81000000, 0x810FFF00
STATUS_CU1 = 0x20000000
AI_RATE = 32006                 # gAiFrequency (osAiSetFrequency(32000) on NTSC)
VI_RATE = 60000 / 1001          # one audio frame per VI (NTSC); only paces the modelled AI here
MUSIC_PLAYERS, EFFECT_PLAYERS = (0, 1), (2,)

SYMBOLS = ("create_next_audio_frame_task", "play_sound", "osAiGetLength", "osAiSetNextBuffer",
           "osPiStartDma", "osRecvMesg", "osSendMesg", "osInvalDCache", "osWritebackDCache",
           "osWritebackDCacheAll", "osVirtualToPhysical", "gAudioHeap", "gAudioHeapSize", "gNotes",
           "gMaxSimultaneousNotes", "gSequencePlayers", "gAudioCmdBuffers", "gAudioTasks",
           "gAudioTaskIndex", "gCurrAiBufferIndex", "gAiBuffers", "gAiBufferLengths", "gCurrAiBuffer",
           "gSynthesisReverb", "sGameLoopTicked", "gAudioFrameCount", "gSPTaskMesgQueue",
           "gAudioGlobalsStartMarker", "gAudioGlobalsEndMarker", "synthesis_execute", "sSampleDmas",
           "prepare_reverb_ring_buffer", "osCreateMesgQueue", "__osAiDeviceBusy", "gCurrLevelNum",
           "dispatch_audio_sptask", "gActiveSPTask", "sCurrentAudioSPTask")
# The STROOP map keeps an older name for gGlobalSoundSource (external.c: f32[3] at the camera).
SYMBOL_ALIASES = {"gGlobalSoundSource": "gDefaultSoundArgs"}
# SM64's audio code and the libultra calls it makes, as map symbol bounds: run only where RAM equals the ROM.
CODE_CHECK = (("audio code", "prepare_reverb_ring_buffer", "osCreateMesgQueue"),
              ("libultra os", "osCreateMesgQueue", "__osAiDeviceBusy"))
MAIN_SECTION = re.compile(r"^\.main\s+0x0*([0-9a-f]{8})\s+0x([0-9a-f]+)\s+load address 0x([0-9a-f]+)")

NOTE_BYTES, LAYER_SEQ_CHANNEL, CHANNEL_SEQ_PLAYER, PLAYER_BYTES, PLAYER_CHANNELS = 0xC0, 0x50, 0x40, 0x140, 0x2C
NOTE = {"flags": 0x00, "bankId": 0x07, "prevParentLayer": 0x28, "parentLayer": 0x2C,
        "wantedParentLayer": 0x30, "synthesisBuffers": 0x34, "targetVolLeft": 0x3C,
        "targetVolRight": 0x3E, "curVolLeft": 0x9C, "reverbVolShifted": 0xA0}
SYNTH = {"adpcmdecState": 0x00, "finalResampleState": 0x20, "mixEnvelopeState": 0x40,
         "panResampleState": 0x90, "panSamplesBuffer": 0xB0, "dummyResampleState": 0xF0,
         "samples": 0x110, "end": 0x190}
A_ADPCM, A_CLEARBUFF, A_ENVMIXER, A_LOADBUFF, A_RESAMPLE, A_SAVEBUFF = 1, 2, 3, 4, 5, 6
A_SETBUFF, A_SETVOL, A_DMEMMOVE, A_LOADADPCM, A_MIXER, A_INTERLEAVE, A_SETLOOP = 8, 9, 10, 11, 12, 13, 15
MIX_WRITERS = (A_ENVMIXER, A_MIXER)

SFX_DEMO = (  # (seconds, sound bits, label): sounds.h SOUND_ARG_LOAD values (status WAITING), at gGlobalSoundSource
    (0.40, 0x38118081, "coin"), (1.10, 0x38118081, "coin"), (1.80, 0x24008081, "Mario yah"),
    (2.50, 0x38118081, "coin"), (3.20, 0x30689081, "red coin"), (4.00, 0x04008081, "jump"),
    (4.10, 0x24008081, "Mario yah"))


def phys(address: int) -> int:
    return address & 0x1FFFFFFF


def sext(address: int) -> int:
    address &= 0xFFFFFFFF
    return address | 0xFFFFFFFF00000000 if address & 0x80000000 else address


def swap32(data: np.ndarray) -> np.ndarray:
    """Project64/mupen64plus word order <-> N64 big-endian bytes (its own inverse)."""
    return data.view("<u4").byteswap().view(np.uint8)


def load_symbols(path: Path) -> dict[str, int]:
    pattern = re.compile(r"^\s+0x00000000([0-9a-f]{8})\s+([A-Za-z_]\w*)\s*$")
    found: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if match := pattern.match(line):
            found.setdefault(match[2], int(match[1], 16))
    wanted = {name: name for name in SYMBOLS} | SYMBOL_ALIASES
    missing = [name for name, mapped in wanted.items() if mapped not in found]
    if missing:
        raise SystemExit(f"{path}: the map names none of {missing}")
    return {name: found[mapped] for name, mapped in wanted.items()}


def main_section(path: Path) -> tuple[int, int, int]:
    """The main segment as the map lays it out: (RAM address, bytes, ROM offset)."""
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if match := MAIN_SECTION.match(line):
            return int(match[1], 16), int(match[2], 16), int(match[3], 16)
    raise SystemExit(f"{path}: the map has no .main section")


# --- the two RSPs: the ROM's microcode in cxd4 (stems_lle.c), the HLE (stems_hle.c) ---
def build_native(name: str, source: Path, files: tuple[str, ...], headers: tuple[str, ...], out: Path,
                 defines: tuple[str, ...] = ()) -> Path:
    """Compile tools/scene_lab/<name>.c with these files of a third-party checkout into a DLL,
    cached by the hash of every input."""
    spec = importlib.util.spec_from_file_location("scene_lab_build", ROOT / "tools/build_plugin.py")
    compiler = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(compiler)
    harness = Path(__file__).resolve().parent / f"{name}.c"
    digest = hashlib.sha256(harness.read_bytes() + b"".join(
        (source / file).read_bytes() for file in (*files, *headers)) + " ".join(defines).encode()).hexdigest()[:16]
    work = out / f"stm-{name}-{digest}"
    target = work / f"{name}.dll"
    if target.is_file():
        return target
    vcvars = compiler.find_vcvars64()
    if vcvars is None:
        raise SystemExit("needs the x64 MSVC toolchain (vcvars64.bat)")
    work.mkdir(parents=True, exist_ok=True)
    compiler._cl(vcvars, ["/nologo", "/O2", "/LD", "/MT", "/W3", *[f"/D{d}" for d in defines], f"/I{source}", str(harness),
                          *[str(source / file) for file in files], f"/Fe:{target}", f"/Fo{work}\\"], work)
    return target


def build_hle(source: Path, out: Path) -> Path:
    return build_native("stems_hle", source, HLE_FILES, ("alist_audio.c",), out)


def build_lle(source: Path, out: Path) -> Path:
    return build_native("stems_lle", source, LLE_FILES, ("su.h", "module.h", "rsp.h", "my_types.h"), out,
                        ("SP_EXECUTE_LOG",))


class Hle:
    """mupen64plus-rsp-hle's ABI1 commands: fast, but neither bit-exact with the microcode nor able to
    read the note state the microcode saves (its envelope state has another layout)."""

    def __init__(self, dll: Path):
        self.lib = ctypes.CDLL(str(dll))
        self.lib.stm_run.argtypes = (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p)
        self.lib.stm_run.restype = ctypes.c_int
        self.lib.stm_dmem.restype = ctypes.POINTER(ctypes.c_uint8 * 0x1000)
        self.dmem = np.ctypeslib.as_array(self.lib.stm_dmem().contents)   # the DLL's own DMEM, in place
        self.dram = np.zeros(RDRAM_BYTES, np.uint8)      # Project64 word order

    def run(self, alist: int, size: int, rollback: np.ndarray | None) -> int:
        mask = rollback.ctypes.data if rollback is not None else None
        return self.lib.stm_run(self.dram.ctypes.data, RDRAM_BYTES, phys(alist), size, mask)


class Lle:
    """The ROM's own audio microcode (rspboot + aspMain) in cxd4's RSP interpreter. DMEM and IMEM
    persist from task to task, as on the RSP; the task is loaded the way osSpTaskLoad loads it."""

    def __init__(self, dll: Path, dmem: bytes, imem: bytes):
        self.lib = ctypes.CDLL(str(dll))
        self.lib.stm_lle_run.argtypes = (ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
        self.lib.stm_lle_run.restype = ctypes.c_int
        self.lib.stm_lle_steps.argtypes = (ctypes.c_void_p,)
        self.lib.stm_lle_steps.restype = ctypes.c_ulonglong
        self.recent = np.zeros(64, np.uint32)
        # One block, IMEM right after DMEM: cxd4's DMA addresses IMEM as DMEM + 0x1000.
        self.sp = np.concatenate([np.frombuffer(dmem, np.uint8), np.frombuffer(imem, np.uint8)])
        self.dmem, self.imem = self.sp[:0x1000], self.sp[0x1000:]

    def steps(self) -> int:
        """Instructions the last task ran (its last 64 words land in self.recent)."""
        return self.lib.stm_lle_steps(self.recent.ctypes.data)

    def run(self, dram: np.ndarray, task_words: tuple[int, ...]) -> int:
        task = dict(zip(TASK_NAMES, task_words, strict=True))
        self.dmem[:] = np.frombuffer(dmem_for(task, self.dmem.tobytes()), np.uint8)
        boot, size = phys(task["ucode_boot"]), task["ucode_boot_size"]
        self.imem[:size] = dram[boot:boot + size]
        return self.lib.stm_lle_run(dram.ctypes.data, self.dmem.ctypes.data, self.imem.ctypes.data)


# --- the CPU (SM64's audio thread code in Unicorn) ---
class AudioCpu:
    def __init__(self, ram_be: bytes, rom: bytes, symbols: dict[str, int], fcr31: int):
        self.rom, self.sym = rom, symbols
        import unicorn   # the CPU half only (uv run --with unicorn); the partition helpers need none
        from unicorn import mips_const as mips
        self.unicorn, self.mips = unicorn, mips
        self.uc = uc = unicorn.Uc(unicorn.UC_ARCH_MIPS, unicorn.UC_MODE_MIPS64 | unicorn.UC_MODE_BIG_ENDIAN)
        uc.ctl_set_cpu_model(mips.UC_CPU_MIPS64_R4000)
        self.ram = np.zeros(RDRAM_BYTES + 4096, np.uint8)
        skip = -self.ram.ctypes.data % 4096
        self.ram = self.ram[skip:skip + RDRAM_BYTES]
        self.ram[:] = np.frombuffer(ram_be, np.uint8)
        self.stack = np.zeros(SCRATCH_BYTES + 4096, np.uint8)
        skip = -self.stack.ctypes.data % 4096
        self.stack = self.stack[skip:skip + SCRATCH_BYTES]
        uc.mem_map_ptr(0, RDRAM_BYTES, unicorn.UC_PROT_ALL, self.ram.ctypes.data)
        uc.mem_map_ptr(SCRATCH_PHYS, SCRATCH_BYTES, unicorn.UC_PROT_ALL, self.stack.ctypes.data)
        uc.reg_write(mips.UC_MIPS_REG_CP0_STATUS, STATUS_CU1)
        uc.reg_write(mips.UC_MIPS_REG_FCSR, fcr31)
        self.violations: list[str] = []
        self.dma_bytes = self.dma_count = 0
        self.ai_queue: deque[float] = deque()     # samples left per queued AI buffer, head playing
        self.ai_submitted: list[tuple[int, int]] = []
        stubs = {"osAiGetLength": self._ai_get_length, "osAiSetNextBuffer": self._ai_set_next_buffer,
                 "osPiStartDma": self._pi_start_dma, "osRecvMesg": self._recv_mesg, "osSendMesg": self._send_mesg,
                 "osInvalDCache": self._nothing, "osWritebackDCache": self._nothing,
                 "osWritebackDCacheAll": self._nothing, "osVirtualToPhysical": self._virtual_to_physical}
        for name, stub in stubs.items():
            uc.hook_add(unicorn.UC_HOOK_CODE, self._stub(stub), begin=sext(symbols[name]), end=sext(symbols[name]))
        uc.hook_add(unicorn.UC_HOOK_INTR, self._on_exception)

    # memory helpers (virtual KSEG0 addresses, big-endian)
    def u32(self, address: int) -> int:
        return struct.unpack_from(">I", self.ram, phys(address))[0]

    def s16(self, address: int) -> int:
        return struct.unpack_from(">h", self.ram, phys(address))[0]

    def u8(self, address: int) -> int:
        return int(self.ram[phys(address)])

    def put_u32(self, address: int, value: int) -> None:
        struct.pack_into(">I", self.ram, phys(address), value & 0xFFFFFFFF)

    def arg(self, index: int) -> int:
        if index < 4:
            return self.uc.reg_read(self.mips.UC_MIPS_REG_A0 + index) & 0xFFFFFFFF
        stack = phys(self.uc.reg_read(self.mips.UC_MIPS_REG_SP) + 4 * index)   # the stack page is outside RDRAM
        return struct.unpack(">I", self.uc.mem_read(stack, 4))[0]

    def _stub(self, handler):
        def hook(uc, address, size, _):
            value = handler()
            uc.reg_write(self.mips.UC_MIPS_REG_V0, sext(value or 0))
            uc.reg_write(self.mips.UC_MIPS_REG_PC, uc.reg_read(self.mips.UC_MIPS_REG_RA))
        return hook

    def _nothing(self) -> int:
        return 0

    def _virtual_to_physical(self) -> int:
        return phys(self.arg(0))

    def _ai_get_length(self) -> int:
        return int(self.ai_queue[0]) * 4 if self.ai_queue else 0

    def _ai_set_next_buffer(self) -> int:
        buffer, size = self.arg(0), self.arg(1)
        self.ai_queue.append(size / 4)
        self.ai_submitted.append((buffer, size))
        return 0

    def ai_play(self, samples: float) -> None:
        while samples > 0 and self.ai_queue:
            used = min(samples, self.ai_queue[0])
            self.ai_queue[0] -= used
            samples -= used
            if self.ai_queue[0] <= 0:
                self.ai_queue.popleft()

    def _queue(self, queue: int) -> tuple[int, int, int, int]:
        valid, first, count, messages = struct.unpack_from(">iiiI", self.ram, phys(queue) + 8)
        return valid, first, count, messages

    def _send(self, queue: int, message: int) -> int:
        valid, first, count, messages = self._queue(queue)
        if valid >= count:
            return 0xFFFFFFFF                     # full, as libultra's OS_MESG_NOBLOCK reports
        self.put_u32(messages + 4 * ((first + valid) % count), message)
        struct.pack_into(">i", self.ram, phys(queue) + 8, valid + 1)
        return 0

    def _send_mesg(self) -> int:
        return self._send(self.arg(0), self.arg(1))

    def take(self, queue: int) -> int | None:
        """Receive the queue's next message, if any (what a thread's osRecvMesg gets)."""
        valid, first, count, messages = self._queue(queue)
        if valid == 0:
            return None
        message = self.u32(messages + 4 * first)
        struct.pack_into(">ii", self.ram, phys(queue) + 8, valid - 1, (first + 1) % count)
        return message

    def _recv_mesg(self) -> int:
        queue, out, flags = self.arg(0), self.arg(1), self.arg(2)
        message = self.take(queue)
        if message is None:
            if flags:                              # OS_MESG_BLOCK: nothing will ever arrive here
                self.violations.append(f"blocking receive on empty queue {queue:08x}")
            return 0xFFFFFFFF
        if out:
            self.put_u32(out, message)
        return 0

    def _pi_start_dma(self) -> int:
        message, direction, device, target, size, queue = (self.arg(0), self.arg(2), self.arg(3),
                                                           self.arg(4), self.arg(5), self.arg(6))
        if direction != 0 or device + size > len(self.rom):
            self.violations.append(f"PI DMA dir={direction} dev={device:08x} size={size}")
            return 0xFFFFFFFF
        self.ram[phys(target):phys(target) + size] = np.frombuffer(self.rom, np.uint8, size, device)
        self.dma_bytes += size
        self.dma_count += 1
        if queue:
            self._send(queue, message)
        return 0

    def _on_exception(self, uc, number, _):
        pc = uc.reg_read(self.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
        self.violations.append(f"exception {number} at {pc:08x}")
        uc.emu_stop()

    def call(self, function: int, *args: int, budget: int = 50_000_000) -> int:
        uc = self.uc
        for register, value in zip((self.mips.UC_MIPS_REG_A0, self.mips.UC_MIPS_REG_A1, self.mips.UC_MIPS_REG_A2,
                                    self.mips.UC_MIPS_REG_A3), args, strict=False):
            uc.reg_write(register, sext(value))
        uc.reg_write(self.mips.UC_MIPS_REG_SP, sext(STACK_TOP))
        uc.reg_write(self.mips.UC_MIPS_REG_RA, sext(SENTINEL))
        try:
            uc.emu_start(sext(function), sext(SENTINEL), count=budget)
        except self.unicorn.UcError as error:
            self.violations.append(f"unicorn: {error}")
        pc = uc.reg_read(self.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF
        if pc != SENTINEL:
            self.violations.append(f"stopped at {pc:08x}")
        return uc.reg_read(self.mips.UC_MIPS_REG_V0) & 0xFFFFFFFF


def check_code(ram_be: np.ndarray, rom: bytes, symbols: dict[str, int], main: tuple[int, int, int]) -> list[str]:
    """SM64's audio code and the libultra it calls must be the ROM's own bytes."""
    bad = []
    for name, first, last in CODE_CHECK:
        lo, hi = symbols[first], symbols[last]
        image = rom[lo - main[0] + main[2]:hi - main[0] + main[2]]
        if ram_be[phys(lo):phys(hi)].tobytes() != image:
            bad.append(name)
    return bad


# --- note ownership and the command list ---
class Owners:
    """Who owns each note voice: the sequence player (and its channel) of its layer. A releasing
    note keeps the owner it had; a note seen first while releasing falls back to prevParentLayer."""

    def __init__(self, cpu: AudioCpu):
        self.cpu, self.sym = cpu, cpu.sym
        self.owner: dict[int, tuple[int, int]] = {}
        self.unknown = 0

    def player_of_layer(self, layer: int) -> tuple[int, int] | None:
        if not layer or layer >> 24 != 0x80:
            return None
        channel = self.cpu.u32(layer + LAYER_SEQ_CHANNEL)
        if not channel or channel >> 24 != 0x80:
            return None
        player = self.cpu.u32(channel + CHANNEL_SEQ_PLAYER)
        offset = player - self.sym["gSequencePlayers"]
        if not 0 <= offset < 3 * PLAYER_BYTES or offset % PLAYER_BYTES:
            return None
        index = offset // PLAYER_BYTES
        channels = [self.cpu.u32(player + PLAYER_CHANNELS + 4 * i) for i in range(16)]
        return index, channels.index(channel) if channel in channels else -1

    def update(self) -> dict[int, dict]:
        """Each note's owner after this frame's list was built, and the owner it had before."""
        cpu, notes = self.cpu, self.cpu.u32(self.sym["gNotes"])
        count = cpu.u8(self.sym["gMaxSimultaneousNotes"] + 3)
        table = {}
        for index in range(count):
            base = notes + index * NOTE_BYTES
            before = self.owner.get(index)
            parent = self.player_of_layer(cpu.u32(base + NOTE["parentLayer"]))
            if parent is not None:
                self.owner[index] = parent
            elif index not in self.owner:
                previous = self.player_of_layer(cpu.u32(base + NOTE["prevParentLayer"]))
                if previous is not None:
                    self.owner[index] = previous
            table[index] = {"buffers": cpu.u32(base + NOTE["synthesisBuffers"]),
                            "enabled": bool(cpu.u8(base) & 0x80), "owner": self.owner.get(index),
                            "previous": before}
        return table


def commands(cpu: AudioCpu, alist: int, size: int) -> list[tuple[int, int]]:
    words = struct.unpack_from(f">{size // 4}I", cpu.ram, phys(alist))
    return list(zip(words[0::2], words[1::2], strict=True))


def attribute(cmds: list[tuple[int, int]], notes: dict[int, dict], ring: tuple[int, int]) -> tuple[list, dict]:
    """The owner (player, channel) of each command that writes the mix, None for the rest.

    A command carrying a note's state address names that note; the mix-writing commands after it
    (envelope mixer, stereo-strong and headset-pan mixes) stay with it until another note or a
    non-note buffer appears. A note that starts in this list (its final resample runs with
    A_INIT: synthesis.c sets that only when note->needsInit) belonged to its previous owner in
    the updates before the start, which matters when the sequencer steals a voice mid-frame."""
    by_buffer = {index: (phys(note["buffers"]), phys(note["buffers"]) + SYNTH["end"])
                 for index, note in notes.items()}

    def note_at(address: int) -> int | None:
        for index, (lo, hi) in by_buffer.items():
            if lo <= address < hi:
                return index
        return None
    current, started, owners = None, set(), []
    stats = {"starts": 0, "handovers": [], "unattributed_mix": 0}
    for w1, w2 in cmds:
        op = (w1 >> 24) & 0x7F
        if op in (A_ADPCM, A_ENVMIXER, A_RESAMPLE, A_LOADBUFF, A_SAVEBUFF):
            target = note_at(phys(w2))
            if target is not None:
                current = target
                if op == A_RESAMPLE and (w1 >> 16) & 1 and \
                        phys(w2) == by_buffer[target][0] + SYNTH["finalResampleState"]:
                    started.add(target)
                    stats["starts"] += 1
            elif op in (A_LOADBUFF, A_SAVEBUFF) and ring[0] <= phys(w2) < ring[1]:
                current = None               # reverb ring: between updates, not a note
        elif op == A_CLEARBUFF and (w1 & 0xFFFF) == 0x4C0:
            current = None                   # update start without reverb
        elif op == A_INTERLEAVE:
            current = None
        if op not in MIX_WRITERS:
            owners.append(None)
            continue
        if current is None:                  # the reverb return's gain at an update's start
            stats["unattributed_mix"] += op == A_ENVMIXER
            owners.append(None)
            continue
        note = notes[current]
        before_start = current not in started and any(
            phys(cmds[k][1]) == by_buffer[current][0] + SYNTH["finalResampleState"] and (cmds[k][0] >> 16) & 1
            and (cmds[k][0] >> 24) & 0x7F == A_RESAMPLE for k in range(len(owners) + 1, len(cmds)))
        owner = note["previous"] if before_start and note["previous"] is not None else note["owner"]
        if before_start and note["previous"] != note["owner"]:
            stats["handovers"].append((current, note["previous"], note["owner"]))
        owners.append(owner if owner is not None else "unknown")
    return owners, stats


def heap_span(symbols: dict[str, int], cpu: AudioCpu) -> tuple[int, int]:
    lo = phys(symbols["gAudioHeap"])
    return lo, lo + cpu.u32(symbols["gAudioHeapSize"])


def envelope_state_check(cpu: AudioCpu, notes: dict[int, dict]) -> list[dict]:
    """Read each sounding note's envelope state (written by Project64's RSP, LLE) with the HLE's
    layout: if the layouts agree, dry is gVolume, wet the note's reverb send and targets its
    target volumes."""
    rows = []
    base = cpu.u32(cpu.sym["gNotes"])
    for index, note in notes.items():
        if not note["enabled"] or not note["buffers"]:
            continue
        state = phys(note["buffers"] + SYNTH["mixEnvelopeState"])
        le = swap32(cpu.ram[state:state + 80].copy())
        shorts, ints = le.view("<i2"), le.view("<i4")
        address = base + index * NOTE_BYTES
        rows.append({"note": index, "hle_wet": int(shorts[0]), "hle_dry": int(shorts[2]),
                     "hle_target_left": int(ints[2]) >> 16, "hle_target_right": int(ints[3]) >> 16,
                     "hle_value_left": int(ints[8]) >> 16, "hle_value_right": int(ints[9]) >> 16,
                     "note_reverbVolShifted": cpu.s16(address + NOTE["reverbVolShifted"]),
                     "note_targetVolLeft": cpu.s16(address + NOTE["targetVolLeft"]),
                     "note_targetVolRight": cpu.s16(address + NOTE["targetVolRight"]),
                     "note_curVolLeft": cpu.s16(address + NOTE["curVolLeft"])})
    return rows


def write_wav(path: Path, stereo: np.ndarray, rate: int = AI_RATE) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(stereo.astype("<i2").tobytes())


AIBUFFER_BYTES = 0xA00          # data.h AIBUFFER_LEN
A_AUX_FLAG = 0x08
SCRATCH_DMEM = 0x200            # stereo-strong / headset-pan temp: every note that uses it clears it first
BLOCK = 256


def ai_samples(dram: np.ndarray, buffer: int, samples: int) -> np.ndarray:
    lo = phys(buffer)
    return swap32(dram[lo:lo + samples * 4].copy()).view(">i2").astype(np.int16).reshape(-1, 2)


def muted_mask(mix_owner: list, keep: tuple[int, ...]) -> np.ndarray:
    """1 for each mix-writing command of a note whose player is not kept (an unknown owner: muted)."""
    return np.array([1 if owner is not None and (owner == "unknown" or owner[0] not in keep) else 0
                     for owner in mix_owner], np.uint8)


def edited_list(cmds: list[tuple[int, int]], muted: np.ndarray) -> list[tuple[int, int]]:
    """The same list with each muted note's mixing sent to scratch DMEM: its envelope mixer still
    runs and saves its state, but writes none of the four output buffers, and its own mixes
    (stereo-strong, headset pan) become no-ops. Any RSP runs it, the microcode included."""
    out = list(cmds)
    for index in np.flatnonzero(muted):
        w1 = cmds[index][0]
        if (w1 >> 24) & 0x7F == A_MIXER:
            out[index] = (0, 0)                                  # A_SPNOOP
            continue
        j = index - 1
        while j > 0 and (cmds[j][0] >> 24) & 0x7F == A_SETVOL:
            j -= 1
        aux, main = cmds[j], cmds[j - 1]
        if (aux[0] >> 24) & 0x7F != A_SETBUFF or not (aux[0] >> 16) & A_AUX_FLAG or \
                (main[0] >> 24) & 0x7F != A_SETBUFF or (main[0] >> 16) & A_AUX_FLAG:
            raise ValueError(f"envelope mixer at command {index} without its two buffer settings")
        out[j] = ((aux[0] & 0xFFFF0000) | SCRATCH_DMEM, (SCRATCH_DMEM << 16) | SCRATCH_DMEM)
        out[j - 1] = (main[0], (SCRATCH_DMEM << 16) | (main[1] & 0xFFFF))
    return out


def put_list(dram: np.ndarray, alist: int, cmds: list[tuple[int, int]]) -> None:
    lo = phys(alist)
    dram[lo:lo + 8 * len(cmds)] = np.array([word for pair in cmds for word in pair], "<u4").view(np.uint8)


class Task(NamedTuple):
    words: tuple[int, ...]          # the OSTask as the game filled it
    cmds: list[tuple[int, int]]     # its command list
    ai_buffer: int                  # where the list's final save puts this frame's samples
    ai_length: int                  # how many stereo samples


ADDRESSED = (A_LOADBUFF, A_SAVEBUFF, A_ADPCM, A_RESAMPLE, A_ENVMIXER, A_LOADADPCM, A_SETLOOP)


class Chain:
    """One RSP implementation carrying the game's audio on from the savestate: the bytes the RSP
    owns in RDRAM (note state, reverb ring, AI buffers) and its DMEM, from one task to the next."""

    def __init__(self, name: str, regions: list[tuple[int, int]], dram: np.ndarray, sp: np.ndarray,
                 ring: tuple[int, int], stems: dict):
        self.name, self.regions, self.ring = name, regions, ring
        self.state = [dram[lo:hi].copy() for lo, hi in regions]
        self.sp = self.last_sp = sp.copy()
        self.mix: list[np.ndarray] = []
        self.stems = {stem: [] for stem in stems}
        self.rings = {stem: np.zeros(ring[1] - ring[0], np.uint8) for stem in stems}  # stems start silent
        self.ms: list[float] = []
        self.statuses: list[int] = []
        self.outside: list[str] = []

    def overlay(self, dram: np.ndarray) -> None:
        for (lo, hi), data in zip(self.regions, self.state, strict=True):
            dram[lo:hi] = data

    def keep(self, dram: np.ndarray) -> None:
        self.state = [dram[lo:hi].copy() for lo, hi in self.regions]


class Probe:
    """One savestate carried forward: SM64's audio thread in Unicorn, each list rendered by every
    chain (the microcode first when it is built: it carries the game on), with both stems."""

    def __init__(self, args):
        self.args = args
        self.rom = args.rom.read_bytes()
        self.sym = load_symbols(args.map)
        state = read_savestate(args.savestate)
        self.ram_be = swap32(np.frombuffer(state["rdram"], np.uint8).copy())
        self.rom_header, self.dumped = state["rom_header"], []
        bad = check_code(self.ram_be, self.rom, self.sym, main_section(args.map))
        if bad:
            raise SystemExit(f"RAM code differs from the ROM in {bad}: refusing to run it")
        self.cpu = cpu = AudioCpu(self.ram_be.tobytes(), self.rom, self.sym, args.fcr31)
        self.hle = Hle(build_hle(args.hle_src, args.out))
        self.hle.dram[:] = swap32(cpu.ram.copy())  # the boot and audio microcode live outside the heap
        self.dram = self.hle.dram                  # one RDRAM image (Project64 word order) for both RSPs
        self.lle = Lle(build_lle(args.lle_src, args.out), state["dmem"], bytes(0x1000)) if args.lle_src else None
        self.heap = heap_span(self.sym, cpu)
        queue = struct.unpack_from(">6I", cpu.ram, phys(self.sym["gSPTaskMesgQueue"]))
        if not (queue[2] == 1 and cpu.u32(queue[5] + 4 * queue[3]) - self.sym["gAudioTasks"] in (0, 0x50)):
            raise SystemExit("the savestate holds no dispatched-but-unrun audio task")
        reverb = self.sym["gSynthesisReverb"]
        ring_samples = cpu.u32(reverb + 0x10)
        self.ring = (phys(cpu.u32(reverb + 0x14)), phys(cpu.u32(reverb + 0x18)) + ring_samples * 2)
        self.owners = Owners(cpu)
        notes = self.owners.update()
        # What the RSP writes in RDRAM: each note's state (all but the CPU's wave samples), the
        # reverb ring and the three AI buffers. Every run checks it writes nothing else.
        buffers = [phys(cpu.u32(self.sym["gAiBuffers"] + 4 * i)) for i in range(3)]
        self.regions = sorted({(phys(n["buffers"]), phys(n["buffers"]) + SYNTH["samples"]) for n in notes.values()}
                              | {self.ring} | {(lo, lo + AIBUFFER_BYTES) for lo in buffers})
        self.stems = {"music": MUSIC_PLAYERS, "effects": EFFECT_PLAYERS}
        hle_dmem = np.frombuffer(state["dmem"], np.uint8) if args.dmem_start == "savestate" else np.zeros(0x1000, np.uint8)
        self.chains = ([Chain("lle", self.regions, self.dram, self.lle.sp, self.ring, self.stems)] if self.lle else []) \
            + [Chain("hle", self.regions, self.dram, hle_dmem, self.ring, self.stems)]
        self.report = {"savestate": args.savestate.name, "rom_sha1": hashlib.sha1(self.rom).hexdigest(),
                       "hle_revision": HLE_REVISION, "lle_revision": LLE_REVISION if self.lle else None,
                       "level": cpu.s16(self.sym["gCurrLevelNum"]), "heap": [hex(0x80000000 | self.heap[0]), self.heap[1] - self.heap[0]],
                       "reverb": {"ring_samples_per_channel": ring_samples, "gain": hex(cpu.u32(reverb + 4) >> 16),
                                  "useReverb": cpu.u8(reverb + 1)},
                       "rsp_regions": [[hex(0x80000000 | lo), hi - lo] for lo, hi in self.regions],
                       "envelope_state_check": envelope_state_check(cpu, notes),
                       "sfx_distance": args.sfx_distance, "frames": [], "violations": cpu.violations}
        self.demo = sorted(SFX_DEMO) if args.sfx_demo else []
        if args.sfx_distance:       # move the demo's source away from the camera (sandbox RAM only)
            struct.pack_into(">3f", cpu.ram, phys(self.sym["gGlobalSoundSource"]), 0.0, 0.0, -args.sfx_distance)
        # Approach A's capture: the audio globals, heap and sound-bank state, 256-byte blocks that changed.
        self.spans = [(phys(self.sym["gAudioGlobalsStartMarker"]), phys(self.sym["gAudioGlobalsEndMarker"])),
                      self.heap, (phys(self.sym["gCurrAiBuffer"]), phys(self.sym["gCurrAiBuffer"]) + 0x3710)]
        self.previous_spans = None
        self.streams = {"state": bytearray(), "lists": bytearray(), "owners": bytearray()}
        self.cpu_ms: list[float] = []
        self.stale: list[bool] = []

    def real_buffers(self) -> list[np.ndarray]:
        """The last two buffers Project64's RSP wrote before the pending task's (savestate bytes)."""
        cpu, sym = self.cpu, self.sym
        index = cpu.u32(sym["gCurrAiBufferIndex"])
        lengths = [cpu.s16(sym["gAiBufferLengths"] + 2 * i) for i in range(3)]
        return [ai_samples(swap32(self.ram_be.copy()), cpu.u32(sym["gAiBuffers"] + 4 * ((index - back) % 3)),
                           lengths[(index - back) % 3]) for back in (2, 1)]

    def note_state_blocks(self) -> None:
        snapshot = [self.cpu.ram[lo:hi].copy() for lo, hi in self.spans]
        if self.previous_spans is not None:
            for now, before in zip(snapshot, self.previous_spans, strict=True):
                changed = np.pad(now != before, (0, -len(now) % BLOCK)).reshape(-1, BLOCK).any(axis=1)
                for at in np.flatnonzero(changed):
                    self.streams["state"] += struct.pack("<I", int(at)) + now[at * BLOCK:(at + 1) * BLOCK].tobytes()
        self.previous_spans = snapshot

    def cpu_frame(self, frame: int, requested: list[str]) -> bool:
        """Build this frame's list with the game's own code; False when the game made no task."""
        cpu, sym = self.cpu, self.sym
        if frame == 0:          # the savestate's dispatched list: the game already did the CPU half
            return True
        cpu.ai_play(AI_RATE / VI_RATE)
        if frame % 2 == 1:      # the game loop ticks at 30 Hz
            cpu.put_u32(sym["sGameLoopTicked"], 1)
            while self.demo and self.demo[0][0] * VI_RATE <= frame:
                _, bits, label = self.demo.pop(0)
                cpu.call(sym["play_sound"], bits, sym["gGlobalSoundSource"])
                requested.append(label)
        begin = time.perf_counter()
        task = cpu.call(sym["create_next_audio_frame_task"])
        self.cpu_ms.append((time.perf_counter() - begin) * 1000)
        if task:                # thread4_sound posts it for the scheduler
            cpu.call(sym["dispatch_audio_sptask"], task)
        return bool(task)

    def dump_frame(self, frame: int) -> None:
        """RDRAM and SP DMEM as Project64 would hand the wrapper at this VI's UpdateScreen: the
        task dispatched and not yet run (a tap_offline_host vi: frame)."""
        folder = self.args.frames_out / f"vi-{frame:05d}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "rdram.bin").write_bytes(swap32(self.cpu.ram.copy()).tobytes())
        (folder / "dmem.bin").write_bytes(self.chains[0].sp[:0x1000].tobytes())
        (folder / "frame.json").write_text(json.dumps({
            "schema": "scene-lab-frame/1", "vi": {"origin": 0}, "rom_header": self.rom_header.hex(),
            "source": {"kind": "stems-probe", "savestate": self.args.savestate.name, "audio_frame": frame}},
            indent=2) + "\n", encoding="utf-8")
        self.dumped.append(folder)

    def step(self, frame: int) -> None:
        cpu, sym, heap = self.cpu, self.sym, self.heap
        self.note_state_blocks()
        requested: list[str] = []
        if not self.cpu_frame(frame, requested):
            self.report["frames"].append({"frame": frame, "task": None})
            return
        task_words = struct.unpack_from(">16I", cpu.ram, phys(sym["gAudioTasks"] + 0x50 * cpu.u32(sym["gAudioTaskIndex"])))
        alist, size = task_words[12], task_words[13]
        ai_index = cpu.u32(sym["gCurrAiBufferIndex"])
        task = Task(task_words, commands(cpu, alist, size), cpu.u32(sym["gAiBuffers"] + 4 * ai_index),
                    cpu.s16(sym["gAiBufferLengths"] + 2 * ai_index))
        self.streams["lists"] += cpu.ram[phys(alist):phys(alist) + size].tobytes()
        notes = self.owners.update()
        self.streams["owners"] += bytes((n["owner"] or (255,))[0] for n in notes.values())
        mix_owner, stats = attribute(task.cmds, notes, self.ring)
        masks = {stem: muted_mask(mix_owner, keep) for stem, keep in self.stems.items()}
        outside = [hex(w2) for w1, w2 in task.cmds if (w1 >> 24) & 0x7F in ADDRESSED and not heap[0] <= phys(w2) < heap[1]]
        row = {"frame": frame, "commands": len(task.cmds), "bytes": size, "samples": task.ai_length,
               "outside_heap": outside[:4], "requested": requested,
               "sounding": {str(i): n["owner"] for i, n in notes.items() if n["enabled"]},
               "unowned_mix_commands": mix_owner.count("unknown") + stats["unattributed_mix"],
               "note_starts": stats["starts"], "handovers": stats["handovers"]}
        if self.args.frames_out:
            self.dump_frame(frame)
        heap_le = swap32(cpu.ram[heap[0]:heap[1]].copy())
        for chain in self.chains:
            self.render(chain, task, heap_le, masks, frame)
        if len(self.chains) == 2:
            row["lle_vs_hle_max_abs"] = int(np.abs(self.chains[0].mix[-1].astype(np.int32) - self.chains[1].mix[-1]).max())
        self.report["frames"].append(row)
        self.dram[heap[0]:heap[1]] = heap_le
        self.chains[0].overlay(self.dram)          # the first chain carries the game on
        cpu.ram[heap[0]:heap[1]] = swap32(self.dram[heap[0]:heap[1]].copy())
        cpu.take(sym["gSPTaskMesgQueue"])          # the scheduler takes the task at the next VI

    def execute(self, chain: Chain, task: "Task", sp: np.ndarray, rollback: np.ndarray | None = None) -> int:
        """One task on this chain's RSP from this DMEM (the microcode, or the HLE)."""
        if chain.name == "lle":
            self.lle.sp[:] = sp
            status = self.lle.run(self.dram, task.words)
            chain.last_sp = self.lle.sp.copy()
        else:
            self.hle.dmem[:] = sp
            status = self.hle.run(task.words[12], task.words[13], rollback)
            chain.last_sp = self.hle.dmem.copy()
        return status

    def render(self, chain: Chain, task: "Task", heap_le: np.ndarray, masks: dict, frame: int) -> None:
        dram, heap = self.dram, self.heap
        dram[heap[0]:heap[1]] = heap_le
        chain.overlay(dram)
        pre, sp_pre = dram[heap[0]:heap[1]].copy(), chain.sp.copy()
        if self.args.dmem_probe and chain is self.chains[0]:
            # Does this list read DMEM it never wrote? The same inputs on patterned list buffers.
            patterned_sp = sp_pre.copy()
            patterned_sp[0x5C0:0xF80] = 0xA5
            self.execute(chain, task, patterned_sp)
            patterned = ai_samples(dram, task.ai_buffer, task.ai_length)
            dram[heap[0]:heap[1]] = pre
        begin = time.perf_counter()
        chain.statuses.append(self.execute(chain, task, sp_pre))
        chain.ms.append((time.perf_counter() - begin) * 1000)
        chain.sp = chain.last_sp
        chain.mix.append(ai_samples(dram, task.ai_buffer, task.ai_length))
        if self.args.dmem_probe and chain is self.chains[0]:
            self.stale.append(not np.array_equal(patterned, chain.mix[-1]))
        wrote = np.flatnonzero(dram[heap[0]:heap[1]] != pre)
        known = np.zeros(heap[1] - heap[0], bool)
        for lo, hi in self.regions:
            known[lo - heap[0]:hi - heap[0]] = True
        stray = wrote[~known[wrote]]
        if stray.size:
            chain.outside.append(f"frame {frame}: {stray.size} bytes from {0x80000000 | heap[0] + int(stray[0]):08x}")
        chain.keep(dram)
        for stem in self.stems:
            dram[heap[0]:heap[1]] = pre
            dram[self.ring[0]:self.ring[1]] = chain.rings[stem]
            if chain.name == "lle":     # the microcode itself: the muted notes' mixing goes to scratch
                put_list(dram, task.words[12], edited_list(task.cmds, masks[stem]))
                self.execute(chain, task, sp_pre)
            else:                       # the HLE: the muted notes' mixing is rolled back
                self.execute(chain, task, sp_pre, masks[stem])
            chain.rings[stem] = dram[self.ring[0]:self.ring[1]].copy()
            chain.stems[stem].append(ai_samples(dram, task.ai_buffer, task.ai_length))

    def capture_sizes(self, per_minute: float) -> dict:
        streams = self.streams
        return {"command_lists_raw": int(len(streams["lists"]) * per_minute),
                "command_lists_lzma": int(len(lzma.compress(bytes(streams["lists"]), preset=9)) * per_minute),
                "note_owners_lzma": int(len(lzma.compress(bytes(streams["owners"]), preset=9)) * per_minute),
                "sample_dmas": {"count": int(self.cpu.dma_count * per_minute),
                                "rom_bytes": int(self.cpu.dma_bytes * per_minute)},
                "approach_a_state_blocks_raw": int(len(streams["state"]) * per_minute),
                "approach_a_state_blocks_lzma": int(len(lzma.compress(bytes(streams["state"]), preset=9)) * per_minute),
                "rsp_output_pcm_raw": int(AI_RATE * 4 * 60)}

    def chain_summary(self, chain: Chain, name: str, wavs: dict) -> dict:
        mix = np.concatenate(chain.mix)
        music, effects = np.concatenate(chain.stems["music"]), np.concatenate(chain.stems["effects"])
        residual = np.abs(music.astype(np.int32) + effects - mix.astype(np.int32))
        renders = (("mix", mix), ("music", music), ("effects", effects))
        for label, data in renders:
            path = self.args.out / f"stm-{name}-{chain.name}-{label}.wav"
            write_wav(path, data)
            wavs[f"{chain.name}-{label}"] = str(path)
        return {"ms_per_task_p50": round(float(np.median(chain.ms)), 4),
                "ms_per_audio_second_mix_and_two_stems": round(float(np.median(chain.ms)) * 3 * VI_RATE, 2),
                "nonzero_status_tasks": sum(1 for s in chain.statuses if s),
                "writes_outside_rsp_regions": chain.outside[:5],
                "stems_sum_minus_mix_max_abs_per_second": [int(residual[i:i + AI_RATE].max())
                                                           for i in range(0, len(residual), AI_RATE)],
                "peak": {label: int(np.abs(data.astype(np.int32)).max()) for label, data in renders},
                "rms": {label: round(float(np.sqrt(np.mean(data.astype(np.float64) ** 2))), 1)
                        for label, data in renders}}

    def lle_vs_hle(self) -> dict:
        diffs = [row["lle_vs_hle_max_abs"] for row in self.report["frames"] if "lle_vs_hle_max_abs" in row]
        last_bad = max((i for i, d in enumerate(diffs) if d), default=-1)
        lle, hle = self.chains
        return {"identical_frames": sum(1 for d in diffs if d == 0), "frames": len(diffs),
                "identical_from_frame": last_bad + 1, "max_abs": max(diffs),
                "instructions_per_task_last": int(self.lle.steps()),
                "stems_max_abs_after_that": {
                    stem: max((int(np.abs(a.astype(np.int32) - b).max()) for a, b in
                               zip(lle.stems[stem][last_bad + 1:], hle.stems[stem][last_bad + 1:], strict=True)),
                              default=None) for stem in self.stems}}

    def finish(self) -> dict:
        args = self.args
        args.out.mkdir(parents=True, exist_ok=True)
        name = args.savestate.name.split(".")[0] + args.tag
        seconds = sum(len(m) for m in self.chains[0].mix) / AI_RATE
        wavs: dict[str, str] = {}
        summary = {"audio_seconds": round(seconds, 3), "frames": len(self.chains[0].mix),
                   "violations": len(self.cpu.violations), "capture_bytes_per_minute": self.capture_sizes(60 / seconds),
                   "cpu_ms_per_audio_frame_p50": round(float(np.median(self.cpu_ms)), 3) if self.cpu_ms else None,
                   "stale_dmem_frames": sum(self.stale) if self.stale else None,
                   "chains": {chain.name: self.chain_summary(chain, name, wavs) for chain in self.chains}}
        if self.lle is not None:
            summary["lle_vs_hle"] = self.lle_vs_hle()
        path = args.out / f"stm-{name}-real-then-{self.chains[0].name}-mix.wav"
        write_wav(path, np.concatenate([*self.real_buffers(), *self.chains[0].mix]))
        summary["wavs"] = wavs | {"real-then-mix": str(path)}
        self.report["summary"] = summary
        (args.out / f"stm-{name}-report.json").write_text(json.dumps(self.report, indent=1) + "\n", encoding="utf-8")
        return summary


def run(args) -> int:
    probe = Probe(args)
    for frame in range(int(args.seconds * VI_RATE) + 1):
        probe.step(frame)
    print(json.dumps(probe.finish(), indent=1))
    if args.frames_out:
        # tap.py offline's input: the savestate's own graphics task first (the tap's clock starts
        # at a task), then one UpdateScreen per audio frame.
        first = write_frames(args.savestate, args.frames_out / "task", None)[0]
        (args.frames_out / "frames.txt").write_text(
            "\n".join([str(first), *(f"vi:{folder}" for folder in probe.dumped)]) + "\n", encoding="utf-8", newline="\n")
    return 0


def from_tap(args) -> int:
    import stems_check
    return stems_check.run(args)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands_ = parser.add_subparsers(dest="command", required=True)
    probe = commands_.add_parser("run")
    probe.add_argument("savestate", type=Path)
    probe.add_argument("--rom", type=Path, required=True)
    probe.add_argument("--map", type=Path, required=True)
    probe.add_argument("--hle-src", type=Path, required=True,
                       help=f"mupen64plus-rsp-hle src/ at {HLE_REVISION}")
    probe.add_argument("--lle-src", type=Path, default=None,
                       help=f"mupen64plus-rsp-cxd4 checkout at {LLE_REVISION}: also run the ROM's microcode")
    probe.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()))
    probe.add_argument("--seconds", type=float, default=6.0)
    probe.add_argument("--sfx-demo", action="store_true")
    probe.add_argument("--sfx-distance", type=float, default=0.0,
                       help="put the demo's sound source this far in front of the camera")
    probe.add_argument("--tag", default="", help="suffix for this run's output names")
    probe.add_argument("--dmem-start", choices=("savestate", "zero"), default="savestate")
    probe.add_argument("--dmem-probe", action="store_true",
                       help="also run each list on patterned DMEM: does it read bytes it never wrote?")
    probe.add_argument("--fcr31", type=lambda value: int(value, 0), default=0x01000800)
    probe.add_argument("--frames-out", type=Path, default=None,
                       help="also write each VI's RDRAM and DMEM as tap_offline_host frames (a dry run)")
    probe.set_defaults(handler=run)
    check = commands_.add_parser("from-tap", help="G0-G3 on a recorded session (stems_check.py)")
    check.add_argument("session", type=Path)
    check.add_argument("--rom", type=Path, required=True)
    check.add_argument("--map", type=Path, required=True)
    check.add_argument("--lle-src", type=Path, default=None,
                       help=f"mupen64plus-rsp-cxd4 checkout at {LLE_REVISION}; without it G1 and G3 do not run")
    check.add_argument("--capture", type=Path, default=None, help="audio_capture.py's file, for G2")
    check.add_argument("--report", type=Path, default=None)
    check.add_argument("--wavs", type=Path, default=None, help="write mix, music and effects WAVs here")
    check.add_argument("--build", type=Path, default=Path(tempfile.gettempdir()) / "stm-build",
                       help="where the cxd4 harness DLL is built")
    check.set_defaults(handler=from_tap)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
