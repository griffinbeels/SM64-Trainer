"""A simulated frame as a Usamune save-state slot: Usamune's own routines write and load it (Ghost Handoff).

    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_slot.py candidates <session>
    uv run --with unicorn==2.1.4 --with capstone python tools/scene_lab/handoff_slot.py roundtrip <session>
        --frame K [--gap N] [--follow 300] [--out NAME]

Usamune v1.93u calls its save and load from one place: try_print_debug_mario_level_info's patched jal
(0x802CB248) into 0x80412624, after the frame's objects have updated and before anything is drawn. Both
routines take no arguments and read their slot from Usamune's settings block: save 0x8040392C reads
+0x119 and is a leaf (it ends in jr ra); load 0x80411CF0 reads +0x118, returns without touching anything
when gMarioObject, the level or the area differ from the slot's trailer, and tail-calls
set_mario_animation, which reloads the current animation into the main pool.

This module stops the simulated frame at that call site and calls the routine there, on the same stack, so
a slot is what Usamune itself writes, never a hand-built image. `roundtrip` saves at frame K, runs on, loads,
names every byte a load leaves different, then plays on with the pads recorded from K+1 and compares each
frame with what really happened. RAM bytes stay out of the printed lines and the repository.
"""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import struct
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_sim as sim  # noqa: E402
from sm64_events.slotlibrary.usamune import (  # noqa: E402
    FINGERPRINT_BYTES,
    SLOT_BYTES,
    TRAILER_AREA,
    TRAILER_LEVEL,
    TRAILER_MARIO,
    US,
    slot_address,
)

# This simulator replays US recordings (frame_sim), so its game is Usamune US.
CURR_AREA, CURR_LEVEL, CURRENT_AREA_POINTER = US.curr_area, US.curr_level, US.current_area_pointer
LOAD, MARIO_OBJECT, SAVE = US.load, US.mario_object, US.save

SITE = 0x802CB248                    # the jal into Usamune's save and load dispatcher
SETTINGS = 0x80417B4C
SAVE_INDEX, LOAD_INDEX = SETTINGS + 0x119, SETTINGS + 0x118
GLOBAL_TIMER, AREA_UPDATE_COUNTER, RANDOM_SEED = 0x8032D5D4, 0x8032DF08, 0x8038EEE0
CAMERA_BYTES = 0x6C                  # what Usamune copies from the current area's camera pointer
GROUP_MASK, GROUP_AIRBORNE = 0x1C0, 0x080
ACTION_NAMES = {0x03000880: "single jump", 0x03000881: "double jump", 0x01000882: "triple jump",
                0x01000883: "backflip", 0x01000887: "water jump", 0x03000888: "long jump", 0x0188088A: "dive",
                0x0100088C: "freefall", 0x01000889: "long jump (backwards)", 0x03000886: "side flip",
                0x010208B7: "ground pound", 0x000008A9: "air hit wall", 0x0100088A: "dive"}
# RAM a level's play scribbles over without any game state in it: leave it out of "what a load misses".
PRESENTATION = (("frame buffers and z buffer", 0x000400, 0x05C010), ("gfx pools", 0x207D00, 0x246000),
                ("the slots themselves", 0x500000, 0x800000), ("thread 5's stack", 0x204E00, 0x206E00))


class SlotCpu(sim.GameCpu):
    """GameCpu that can stop at Usamune's call site once and run one thing there."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pending = None
        self.uc.hook_add(self.unicorn.UC_HOOK_CODE, self._on_site, begin=sim.sext(SITE), end=sim.sext(SITE))

    def _on_site(self, uc, _address, _size, _user):
        if self.pending is not None:
            uc.emu_stop()

    def _pc(self) -> int:
        return self.uc.reg_read(self.mips.UC_MIPS_REG_PC) & 0xFFFFFFFF

    def run_frame(self, begin: int = sim.RESUME) -> bool:
        self.armed = begin != sim.RESUME
        had_action = self.pending is not None
        try:
            self.uc.emu_start(sim.sext(begin), sim.sext(sim.NEVER), timeout=sim.BUDGET_US)
            if self.pending is not None and self._pc() == SITE:
                action, self.pending = self.pending, None
                action()
                self.uc.emu_start(sim.sext(SITE), sim.sext(sim.NEVER), timeout=sim.BUDGET_US)
        except self.unicorn.UcError as error:
            pc = self._pc()
            self.violations.append(f"unicorn {error} at {pc:08x} in {self.symbols.name_of(pc)}")
            return False
        if had_action and self.pending is not None:
            self.pending = None
            self.violations.append("this frame never reached Usamune's call site")
        if self._pc() != sim.RESUME:
            self.violations.append(f"stopped at {self._pc():08x} in {self.symbols.name_of(self._pc())}")
            return False
        if self.flush_due:
            self.uc.ctl_flush_tb()
            self.flush_due = False
        return not self.violations

    def call(self, routine: int) -> None:
        """Run one no-argument routine on the current stack; every register comes back as it was."""
        context = self.uc.context_save()
        self.uc.reg_write(self.mips.UC_MIPS_REG_RA, sim.sext(sim.NEVER))
        self.uc.emu_start(sim.sext(routine), sim.sext(sim.NEVER), timeout=sim.BUDGET_US)
        if self._pc() != sim.NEVER:
            self.violations.append(f"routine {routine:08x} stopped at {self._pc():08x}")
        self.uc.context_restore(context)

    def u16(self, address: int) -> int:
        return struct.unpack_from(">H", self.ram, sim.phys(address))[0]

    def put_u16(self, address: int, value: int) -> None:
        struct.pack_into(">H", self.ram, sim.phys(address), value & 0xFFFF)

    def u8(self, address: int) -> int:
        return int(self.ram[sim.phys(address)])

    def put_u8(self, address: int, value: int) -> None:
        self.ram[sim.phys(address)] = value & 0xFF

    def save_slot(self, index: int) -> bytes:
        """Usamune writes slot `index`; its settings come back as they were. Anything the routine
        writes outside the slot's range stops the run: that would make the slot a lie."""
        before = self.ram.copy()
        kept = self.u8(SAVE_INDEX)
        self.put_u8(SAVE_INDEX, index)
        before[sim.phys(SAVE_INDEX)] = index
        self.call(SAVE)
        self.put_u8(SAVE_INDEX, kept)
        before[sim.phys(SAVE_INDEX)] = kept
        low, high = sim.phys(slot_address(index)), sim.phys(slot_address(index)) + SLOT_BYTES
        changed = np.flatnonzero(self.ram != before)
        strays = changed[(changed < low) | (changed >= high)]
        if strays.size:
            raise SystemExit(f"Usamune's save wrote {strays.size} bytes outside slot {index}")
        return self.ram[low:high].tobytes()

    def load_slot(self, index: int) -> None:
        kept = self.u8(LOAD_INDEX)
        self.put_u8(LOAD_INDEX, index)
        self.call(LOAD)
        self.put_u8(LOAD_INDEX, kept)


@dataclass
class Replay:
    """One closed run from a session's boot record, frame by frame; `keep` holds frames for later."""
    cpu: SlotCpu
    stream: object
    pool: np.ndarray
    begin: int
    last_vi: int
    pad_at: int
    kept: list = field(default_factory=list)

    def absorb(self, frame) -> None:
        for begin, data in frame.level_blocks:
            self.pool[begin - sim.POOL_BEGIN:begin - sim.POOL_BEGIN + data.size] = data

    def feed(self, frame) -> None:
        """This frame's pads, and the captured VI count: a slot's trailer carries Usamune's lag fields."""
        pads = next(data for start, data in frame.spans if start == sim.phys(sim.SCENE_BSS))
        self.cpu.pads = pads[self.pad_at:self.pad_at + 24].tobytes()
        self.last_vi = sim.inject_vi(self.cpu, frame, self.last_vi)

    def step(self, frame) -> None:
        self.feed(frame)
        ok = self.cpu.run_frame(self.begin)
        self.begin = sim.RESUME
        if not ok:
            raise SystemExit(f"frame {frame.index}: {self.cpu.violations[:3]}")
        self.cpu.scheduler_model()


def start_replay(session: Path) -> Replay:
    config = json.loads((sim.lab_root() / "lab.json").read_text(encoding="utf-8"))
    rom = Path(config["rom"]).read_bytes()
    symbols = sim.Symbols(Path(config["map"]))
    ram, stream = sim.boot_state(session)
    frame0 = next(stream)
    sim.check_frame(ram, symbols, frame0)
    fcsr = struct.unpack_from(">I", ram, sim.phys(symbols["gGameLoopThread"]) + sim.THREAD_FPCSR)[0]
    cpu = SlotCpu(ram, rom, symbols, fcsr)
    pool = np.zeros(sim.POOL_BYTES, np.uint8)
    begin = sim.first_entry(cpu, symbols, frame0)
    cpu.scheduler_model()
    replay = Replay(cpu, stream, pool, begin, cpu.u32(symbols["sNumVblanks"]),
                    sim.phys(symbols["gControllerPads"]) - sim.phys(sim.SCENE_BSS))
    replay.absorb(frame0)
    return replay


@dataclass
class BuiltSlot:
    data: bytes                 # big-endian, as the N64 sees it
    frame: int
    level: int
    area: int
    mario_object: int
    seed: int
    segments: bytes             # sSegmentTable when Usamune saved: a level's pointers are valid only under it
    current_area: int
    fingerprint: str            # Usamune's save and load code, hashed


def routine_fingerprint(cpu: SlotCpu) -> str:
    """The same hash the writer takes of the running game (handoff_try.routine_fingerprint)."""
    digest = hashlib.sha256()
    for start in (SAVE, LOAD):
        digest.update(cpu.ram[sim.phys(start):sim.phys(start) + FINGERPRINT_BYTES].tobytes())
    return digest.hexdigest()


def build_slot(session: Path, frame_index: int, index: int) -> BuiltSlot:
    """Run a session to `frame_index` and let Usamune save it at its own call site."""
    replay = start_replay(session)
    cpu, found = replay.cpu, {}

    def save() -> None:
        table = cpu.symbols["sSegmentTable"]
        found.update(data=cpu.save_slot(index), seed=cpu.u16(RANDOM_SEED), current_area=cpu.u32(CURRENT_AREA_POINTER),
                     segments=cpu.ram[sim.phys(table):sim.phys(table) + 64].tobytes(),
                     fingerprint=routine_fingerprint(cpu))

    for frame in replay.stream:
        replay.absorb(frame)
        if frame.index == frame_index:
            cpu.pending = save
        replay.step(frame)
        if frame.index == frame_index:
            data = found["data"]
            mario = struct.unpack_from(">I", data, TRAILER_MARIO)[0]
            level, area = struct.unpack_from(">hh", data, TRAILER_LEVEL)
            return BuiltSlot(data, frame_index, level, area, mario, found["seed"], found["segments"],
                             found["current_area"], found["fingerprint"])
    raise SystemExit(f"{session.name} has no frame {frame_index}")


# --- what differs -------------------------------------------------------------------------------
def group_diff(a: np.ndarray, b: np.ndarray, symbols: sim.Symbols, skip=PRESENTATION) -> collections.Counter:
    """Bytes differing between two RDRAM images, by label, without the presentation regions."""
    mask = a != b
    for _name, low, high in skip:
        mask[low:high] = False
    groups: collections.Counter = collections.Counter()
    for address in np.flatnonzero(mask).tolist():
        groups[sim.label(symbols, address)] += 1
    return groups


def summarize(groups: collections.Counter) -> dict:
    kinds: collections.Counter = collections.Counter()
    for name, count in groups.items():
        if name.startswith("gMarioStates"):
            kinds["Mario"] += count
        elif name.startswith("object["):
            kinds["objects"] += count
        elif name.startswith("gLakitu") or "amera" in name:
            kinds["camera"] += count
        elif name.startswith("main pool"):
            kinds["main pool"] += count
        else:
            kinds["elsewhere"] += count
    return dict(kinds)


def camera_block(cpu: SlotCpu) -> bytes:
    pointer = cpu.u32(cpu.u32(CURRENT_AREA_POINTER) + 0x24)
    return cpu.ram[sim.phys(pointer):sim.phys(pointer) + CAMERA_BYTES].tobytes()


@dataclass
class Snapshot:
    """A replay paused between frames, to run several endings from one point."""
    ram: np.ndarray
    context: object
    pool: np.ndarray
    last_vi: int

    @classmethod
    def of(cls, replay: Replay) -> Snapshot:
        return cls(replay.cpu.ram.copy(), replay.cpu.uc.context_save(), replay.pool.copy(), replay.last_vi)

    def back_to(self, replay: Replay) -> None:
        replay.cpu.ram[:] = self.ram
        replay.cpu.uc.context_restore(self.context)
        replay.cpu.uc.ctl_flush_tb()
        replay.pool[:] = self.pool
        replay.last_vi = self.last_vi
        replay.cpu.violations.clear()


def mario_gap(cpu: SlotCpu, frame) -> tuple[float, bool]:
    """How far the simulated Mario stands from the true one, and whether they share an action."""
    span = next(data for start, data in frame.spans if start == sim.phys(sim.SCENE_BSS))
    at = 0x8033B170 - sim.SCENE_BSS
    real = np.array(struct.unpack(">3f", span[at + 0x3C:at + 0x48].tobytes()))
    ours = np.array(struct.unpack_from(">3f", cpu.ram, sim.phys(0x8033B170) + 0x3C))
    same = span[at + 0x0C:at + 0x10].tobytes() == cpu.ram[sim.phys(0x8033B170) + 0x0C:sim.phys(0x8033B170) + 0x10].tobytes()
    return float(np.linalg.norm(real - ours)), same


def follow(replay: Replay, frames: list, pool_k: np.ndarray, stick: bool = False) -> dict:
    """Play the recorded pads from K+1 on and compare each frame with what really happened."""
    cpu = replay.cpu
    pool = pool_k.copy()
    first: dict[str, int] = {}
    per_frame = []
    labels: collections.Counter = collections.Counter()
    farthest, same_action = 0.0, 0
    for frame in frames:
        for begin, data in frame.level_blocks:
            pool[begin - sim.POOL_BEGIN:begin - sim.POOL_BEGIN + data.size] = data
        replay.feed(frame)
        if stick and frame is frames[0]:
            cpu.perturb("stick", 0x7F)
        if not cpu.run_frame(sim.RESUME):
            return {"crashed_at": frame.index, "violations": cpu.violations[:3], "first": first, "frames_ok": len(per_frame)}
        cpu.scheduler_model()
        away, action_same = mario_gap(cpu, frame)
        farthest, same_action = max(farthest, away), same_action + action_same
        where = sim.compare(cpu, frame.spans, pool, pool_known=True)
        groups: collections.Counter = collections.Counter()
        for address in where.tolist():
            groups[sim.label(cpu.symbols, address)] += 1
        labels.update(groups)
        kinds = summarize(groups)
        for kind in kinds:
            first.setdefault(kind, frame.index)
        for name in groups:
            first.setdefault(name, frame.index)
        per_frame.append(kinds)
    return {"farthest_units": farthest, "same_action_frames": same_action, "frames_ok": len(per_frame), "first": {k: first[k] for k in first if k in ("Mario", "objects", "camera", "main pool", "elsewhere")},
            "first_named": dict(sorted(first.items(), key=lambda item: item[1])[:30]),
            "last_frame": per_frame[-1] if per_frame else {}, "totals": dict(labels.most_common(40))}


@dataclass
class Trip:
    """A replay saved at K and paused just before J, ready to end several ways."""
    replay: Replay
    index: int
    k: int
    j: int
    saved: dict
    pool_k: np.ndarray
    frame_j: object
    after: list
    pause: Snapshot
    report: dict


def ride_to_pause(args) -> Trip:
    """Run a session to K, let Usamune save there, run on to just before J in the same level and area."""
    replay = start_replay(sim.lab_root() / args.session)
    cpu = replay.cpu
    k, j = args.frame, args.frame + args.gap
    saved: dict = {}

    def save_here() -> None:
        saved.update(slot=cpu.save_slot(args.slot), natural=cpu.ram.copy(), camera=camera_block(cpu))

    pool_k, frames_after, identity = None, [], None
    for frame in replay.stream:
        replay.absorb(frame)
        if frame.index == k:
            cpu.pending = save_here
            identity = (frame.level, frame.area)
        if frame.index > k:
            frames_after.append(frame)
        if frame.index < j:
            replay.step(frame)
        if frame.index == k:
            pool_k = replay.pool.copy()
            saved["tap"] = cpu.ram.copy()
        if frame.index >= k + args.follow + 1 and frame.index >= j:
            break
    if pool_k is None:
        raise SystemExit("frame not in session")
    frame_j = next(frame for frame in frames_after if frame.index == j)
    if (frame_j.level, frame_j.area) != identity:
        raise SystemExit(f"frame {j} left level {identity}")
    report = {"session": args.session, "k": k, "j": j, "slot_index": args.slot, "identity": identity}
    following = [frame for frame in frames_after if frame.index <= k + args.follow]
    return Trip(replay, args.slot, k, j, saved, pool_k, frame_j, following, Snapshot.of(replay), report)


def run_ending(trip: Trip, name: str, spans=(), stick: bool = False) -> dict:
    """Load at J, optionally put more of frame K's RAM back, then follow the recorded pads."""
    replay, cpu, saved = trip.replay, trip.replay.cpu, trip.saved
    trip.pause.back_to(replay)
    checks: dict = {}

    def at_site() -> None:
        cpu.load_slot(trip.index)
        checks["after_load"] = group_diff(cpu.ram, saved["natural"], cpu.symbols)
        checks["camera_equal"] = camera_block(cpu) == saved["camera"]
        for low, high in spans:
            cpu.ram[low:high] = saved["natural"][low:high]
    cpu.pending = at_site
    replay.feed(trip.frame_j)
    if not cpu.run_frame(sim.RESUME):
        raise SystemExit(f"{name}: frame {trip.j}: {cpu.violations[:3]}")
    cpu.scheduler_model()
    checks["after_draw"] = group_diff(cpu.ram, saved["tap"], cpu.symbols)
    result = follow(replay, trip.after, trip.pool_k, stick=stick)
    result.update(load_summary=summarize(checks["after_load"]), draw_summary=summarize(checks["after_draw"]),
                  camera_equal=checks["camera_equal"], after_load=dict(checks["after_load"].most_common(60)),
                  after_draw=dict(checks["after_draw"].most_common(60)))
    trip.report[name] = result
    print(f"{name}: right after the load {result['load_summary']}; after its draw {result['draw_summary']}; "
          f"camera block equal {result['camera_equal']}")
    print(f"    {result['frames_ok']} frames played; first difference from the true frames {result.get('first')}; "
          f"last frame {result.get('last_frame')}; Mario at most {result.get('farthest_units', 0):.3f} units away, "
          f"same action on {result.get('same_action_frames')} frames")
    if "crashed_at" in result:
        print(f"    CRASHED at {result['crashed_at']}: {result['violations']}")
    return result


def roundtrip(args) -> int:
    trip = ride_to_pause(args)
    slot = trip.saved["slot"]
    print(f"slot: {len(slot)} bytes from Usamune's own save; "
          f"level {struct.unpack_from('>h', slot, TRAILER_LEVEL)[0]}, area {struct.unpack_from('>h', slot, TRAILER_AREA)[0]}, "
          f"gMarioObject {struct.unpack_from('>I', slot, TRAILER_MARIO)[0]:08x}")
    seed = (sim.phys(RANDOM_SEED), sim.phys(RANDOM_SEED) + 2)
    clocks = ((sim.phys(GLOBAL_TIMER), sim.phys(GLOBAL_TIMER) + 4),
              (sim.phys(AREA_UPDATE_COUNTER), sim.phys(AREA_UPDATE_COUNTER) + 4))
    pads = (sim.phys(sim.SCENE_BSS), sim.phys(0x8033B010))
    main_pool = (sim.POOL_BEGIN, sim.POOL_BEGIN + sim.POOL_BYTES)
    site_stack(trip.replay, trip.pause, trip.frame_j)
    plain = run_ending(trip, "load")
    print("    not restored right after the load:", plain["after_load"])
    for name, spans in (("load+seed", (seed,)), ("load+seed+clocks", (seed, *clocks)), ("load+pads", (pads,)),
                        ("load+main pool", (main_pool,)), ("load+pads+main pool", (pads, main_pool)),
                        ("load+seed+pads", (seed, pads)), ("load+seed+pads+main pool", (seed, pads, main_pool)),
                        ("load+seed+clocks+pads", (seed, *clocks, pads)),
                        ("load+seed+clocks+pads+main pool", (seed, *clocks, pads, main_pool)),
                        ("control: all RAM back to frame K", ((0, sim.phys(slot_address(0))),))):
        run_ending(trip, name, spans)
    flipped = run_ending(trip, "flipped stick, seed+clocks+pads+main pool", (seed, *clocks, pads, main_pool), stick=True)
    print("    the flip diverges Mario at", flipped.get("first", {}).get("Mario"))
    trip.report["refusals"] = run_refusals(trip.replay, trip.pause, trip.frame_j, args.slot)
    print("refusals:", trip.report["refusals"])
    out = sim.lab_root() / f"handoff-sim-{args.out or f'roundtrip-{trip.k}'}" / "report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(trip.report, default=str), encoding="utf-8")
    print(f"report: {out}")
    return 0


def site_stack(replay: Replay, pause: Snapshot, frame_j) -> None:
    """Who calls Usamune's dispatcher, read from the saved return addresses on the stack at the site."""
    cpu = replay.cpu
    pause.back_to(replay)
    chain: list[str] = []

    def at_site() -> None:
        sp = cpu.uc.reg_read(cpu.mips.UC_MIPS_REG_SP) & 0xFFFFFFFF
        ra = cpu.uc.reg_read(cpu.mips.UC_MIPS_REG_RA) & 0xFFFFFFFF
        chain.append(cpu.symbols.name_of(ra))
        for offset in range(0, 0x400, 4):
            word = cpu.u32(sp + offset)
            if 0x80246000 <= word < 0x80331000 and cpu.u32(word - 8) >> 26 == 3:   # a value just after a jal
                chain.append(cpu.symbols.name_of(word))
        raise StopFrame
    cpu.pending = at_site
    replay.feed(frame_j)
    try:
        cpu.run_frame(sim.RESUME)
    except StopFrame:
        pass
    print("the site's callers, innermost first:", chain[:14])
    names = ("sSurfacePool", "gMarioAnimsMemAlloc", "D_80339CF0", "gObjectMemoryPool", "gEnvFxBuffer", "sSurfaceNodePool", "gDemoInputsMemAlloc")
    found = {name: f"{cpu.u32(cpu.symbols[name]):08x}" for name in names if name in cpu.symbols.by_name}
    print("main-pool pointers (their pages):", found)


def run_refusals(replay: Replay, pause: Snapshot, frame_j, index: int) -> dict:
    """A wrong level, area or Mario pointer, or an empty slot, must leave RAM alone."""
    cpu = replay.cpu
    outcome: dict = {}

    def wrong_level() -> None:
        cpu.put_u16(CURR_LEVEL, cpu.u16(CURR_LEVEL) + 1)

    def wrong_area() -> None:
        cpu.put_u16(CURR_AREA, cpu.u16(CURR_AREA) + 1)

    def wrong_mario() -> None:
        cpu.put_u32(MARIO_OBJECT, cpu.u32(MARIO_OBJECT) + sim.OBJECT_BYTES)

    def no_mario() -> None:
        cpu.put_u32(MARIO_OBJECT, 0)

    def empty_slot() -> None:
        cpu.put_u32(slot_address(index) + TRAILER_MARIO, 0)

    def nothing() -> None:
        return None

    cases = (("wrong level", wrong_level), ("wrong area", wrong_area), ("wrong Mario pointer", wrong_mario),
             ("no Mario", no_mario), ("empty slot", empty_slot), ("control: nothing wrong", nothing))
    for name, change in cases:
        pause.back_to(replay)

        def at_site(name=name, change=change) -> None:
            change()
            kept_flag = cpu.u8(LOAD_INDEX)
            before = cpu.ram.copy()
            cpu.load_slot(index)
            changed = np.flatnonzero(cpu.ram != before)
            outcome[name] = "refused" if changed.size == 0 else f"loaded, {changed.size} bytes changed"
            cpu.put_u8(LOAD_INDEX, kept_flag)
            raise StopFrame
        cpu.pending = at_site
        replay.feed(frame_j)
        try:
            cpu.run_frame(sim.RESUME)
        except StopFrame:
            pass
    return outcome


class StopFrame(Exception):
    """A refusal case has its answer: the rest of that frame does not matter."""


# --- first-try candidates -----------------------------------------------------------------------
def candidates(args) -> int:
    """Frames whose Mario is high up and fast, far apart, easy to judge by eye: read from the capture."""
    session = sim.lab_root() / args.session
    _ram, stream = sim.boot_state(session)
    picked: list[dict] = []
    seen_actions: set[int] = set()
    fallback: list[dict] = []
    for frame in stream:
        if frame.level != args.level or frame.area != args.area:
            continue
        span = next(data for start, data in frame.spans if start == sim.phys(sim.SCENE_BSS))
        at = 0x8033B170 - sim.SCENE_BSS
        action = int.from_bytes(span[at + 0x0C:at + 0x10].tobytes(), "big")
        pos_y = struct.unpack(">f", span[at + 0x40:at + 0x44].tobytes())[0]
        floor_y = struct.unpack(">f", span[at + 0x70:at + 0x74].tobytes())[0]
        speed = struct.unpack(">f", span[at + 0x54:at + 0x58].tobytes())[0]
        mario_at = MARIO_OBJECT - sim.SCENE_BSS
        mario = int.from_bytes(span[mario_at:mario_at + 4].tobytes(), "big")
        if action & GROUP_MASK != GROUP_AIRBORNE or pos_y - floor_y < args.height or abs(speed) < args.speed:
            continue
        row = {"frame": frame.index, "level": frame.level, "area": frame.area, "gMarioObject": f"{mario:08x}",
               "action": ACTION_NAMES.get(action, f"airborne action {action:08x}")}
        far = all(abs(frame.index - other["frame"]) > args.apart for other in picked)
        if far and action not in seen_actions and len(picked) < args.count:
            picked.append(row)
            seen_actions.add(action)
        elif far:
            fallback.append(row)
    for row in fallback:
        if len(picked) >= args.count:
            break
        if all(abs(row["frame"] - other["frame"]) > args.apart for other in picked):
            picked.append(row)
    for row in sorted(picked, key=lambda item: item["frame"]):
        print(f"frame {row['frame']}: level {row['level']}, area {row['area']}, gMarioObject {row['gMarioObject']}, "
              f"{row['action']}, over {args.height:g} units up, over {args.speed:g} speed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    trip = commands.add_parser("roundtrip")
    trip.add_argument("session")
    trip.add_argument("--frame", type=int, required=True)
    trip.add_argument("--gap", type=int, default=60)
    trip.add_argument("--follow", type=int, default=300)
    trip.add_argument("--slot", type=int, default=3)
    trip.add_argument("--out")
    trip.set_defaults(handler=roundtrip)
    pick = commands.add_parser("candidates")
    pick.add_argument("session")
    pick.add_argument("--level", type=int, default=9)
    pick.add_argument("--area", type=int, default=1)
    pick.add_argument("--height", type=float, default=200.0)
    pick.add_argument("--speed", type=float, default=25.0)
    pick.add_argument("--apart", type=int, default=150)
    pick.add_argument("--count", type=int, default=3)
    pick.set_defaults(handler=candidates)
    args = parser.parse_args()
    return args.handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
