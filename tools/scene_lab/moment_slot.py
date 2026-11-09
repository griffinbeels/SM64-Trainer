"""One recorded frame as a Usamune slot, built from what a scene package keeps (Ghost Handoff, lab only).

    uv run python tools/scene_lab/moment_slot.py build --package P --rom R --map M --frame K --out SLOT.bin

Griffin's request: pick any moment in the Scene viewer and get a save state of it, exact. A package keeps
no boot record, so handoff_slot's replay from boot cannot run here, and replaying a whole attempt costs
15 ms a frame anyway. It does keep, frame by frame, every byte of game state the tap copies. So one frame
is enough: the state as recorded at the end of frame K-1, the pads recorded for frame K, and SM64's own
code running frame K in Unicorn up to Usamune's call site, where Usamune's own save routine makes the slot
(handoff_slot.SlotCpu). The rest of frame K then runs, and Mario and the objects must equal what the
recording holds at K, or the build is refused. That check is what makes the slot trustworthy: it compares
with what the real game did, not with another simulation.

What a recording may lack (each new since moment_slot; layout.py's handoff_* rows):
    the seed          a slot does not hold it, but the frame's objects before the call site may call the
                      random generator. Without it a build counts those calls and refuses if there were any.
    the tail of the slot's body (0x80361270 up to 0x80361490) and the power meter's HUD word
                      Usamune copies them and the tap's older spans stop short of them. Without them the
                      simulation runs WARM_FRAMES more frames first, which rebuilds the ones the game
                      recomputes each frame; the result says "worked_out".

Usamune's rule for when it can save is not restated here. Frames where its dispatcher never runs cannot
make a slot (handoff_slot says "this frame never reached Usamune's call site"); the dispatcher's one more
gate on a save is gCurrDemoInput. RAM bytes stay out of the printed lines and the repository.
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import handoff_slot as slots  # noqa: E402

sim = slots.sim
from sm64_events.memory.layout import layout_for  # noqa: E402
from sm64_events.slotlibrary.usamune import BODY_BYTES, SEGMENT_BYTES, US  # noqa: E402

# Every address below is the build's own (a package's ROM names it): the slot routines, body and segment table from
# usamune.build_for, gCurrDemoInput (the dispatcher saves only while it is 0; read off its code, US 0x8041266C) and
# gRandomSeed16 from the map (frame_sim.complete_symbols adds the seed JP's map lacks), the spans from layout.py.
# compare_command replays his US tap sessions: US layout's slot_tail_*, power_meter_hud_* and the seed's word.
TAIL = (0x361270, (US.body_start + BODY_BYTES) & 0x1FFFFFFF)
POWER_METER_HUD = (0x3325F0, 0x332600)
RANDOM_SEED_WORD = slots.RANDOM_SEED & ~3
THREAD5_FCSR = 0x01000800            # thread 5's saved FPCSR in every boot record so far (frame_sim.THREAD_FPCSR)
WARM_FRAMES = 30                     # enough for the bytes the game recomputes each frame; measured in the tests
FRAME_BUDGET_US = 2_000_000          # one frame far past this has left the rails: a refusal, not a hang

# Plain sentences a person reads. `code` is what the page and the tests key on.
WORDS = {
    "no-previous-frame": "This is the first frame. Pick a later frame.",
    "jump": "This frame comes right after a load or a reset. Pick a frame a little later.",
    "never-reached": "Usamune cannot save on this frame. The game is not running Mario here, such as during a "
                     "level start, a fade or a door.",
    "demo": "Usamune cannot save during the title demo.",
    "needs-seed": "This older recording cannot remake this frame exactly. Pick another frame or record it again.",
    "mismatch": "This frame could not be remade exactly, so no save state was made. Pick another frame.",
    "no-code": "This recording does not hold Usamune's code. Record it again.",
    "no-state": "This recording does not keep the game's state.",
    "crashed": "The game's code stopped on this frame, so no save state was made. Pick another frame.",
}


class Refused(Exception):
    """No slot. `code` names the case, `str()` is the sentence for a person."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(WORDS[code])
        self.code, self.detail = code, detail


@dataclass
class Moment:
    """One recorded frame's game state, in the N64's byte order."""
    index: int
    task_index: int
    timer: int | None
    level: int | None
    area: int | None
    spans: list                  # (physical start, bytes): the task regions, as frame_sim.Frame.spans
    pool: np.ndarray             # the level regions as they stood, the main pool's bytes


class PackageMoments:
    """A package's frames as Moments (package.Package.scene_regions)."""

    def __init__(self, package):
        self.package = package
        if package.states is None:
            raise Refused("no-state")
        # Where its arming put the clocks and the place (either build's addresses); a package without them is US.
        self.identity = dict(sim.US_IDENTITY)
        self.identity.update({name: (address, size) for name, address, size in package.identity})

    def __len__(self) -> int:
        return len(self.package)

    def moment(self, index: int) -> Moment:
        fields, extras, spans, level, _known = self.package.scene_regions(index)
        pool = sim.be32(level[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES]).copy()
        pieces = [(begin, sim.be32(data)) for begin, data in spans]
        return Moment(index, fields["task_index"], _extra(extras, *self.identity["global_timer"]),
                      _extra(extras, *self.identity["curr_level"]), _extra(extras, *self.identity["curr_area"]), pieces, pool)

    def code_windows(self) -> list:
        return self.package.states.code_windows()


def _extra(extras, address: int, size: int) -> int | None:
    return sim.tap.word_value(extras, address, size)


def base_image(rom: bytes, map_path: Path, windows: list) -> np.ndarray:
    """RDRAM before any game state: the ROM's main and engine segments and Usamune's boot-copied code.
    Everything the game changes lies in the spans, or the build's check says so. The segments come from the
    map's section rows (`.main 0x80246000 0xf4580 load address 0x1000`): STROOP's JP map puts both segments'
    `__romPos` symbols at 0x761BE0, Usamune's image, so those symbols cannot place them."""
    ram = np.zeros(sim.RDRAM_BYTES, np.uint8)
    text = map_path.read_text(encoding="utf-8", errors="replace")
    for segment in ("main", "engine"):
        row = re.search(rf"^\.{segment}\s+0x0*([0-9a-f]+)\s+0x([0-9a-f]+)\s+load address 0x0*([0-9a-f]+)", text, re.M)
        if row is None:
            raise Refused("no-code", f"the map has no .{segment} section row")
        start, size, rom_start = int(row[1], 16), int(row[2], 16), int(row[3], 16)
        at = sim.phys(start)
        ram[at:at + size] = np.frombuffer(rom, np.uint8, size, rom_start)
    for address, length, offset in windows:
        ram[sim.phys(address):sim.phys(address) + length] = np.frombuffer(rom, np.uint8, length, offset)
    return ram


def overlay(cpu: slots.SlotCpu, moment: Moment) -> None:
    for begin, data in moment.spans:
        if data.size != sim.TAIL_BYTES:      # the gfx pool's tail is presentation, in another buffer each frame
            cpu.ram[begin:begin + data.size] = data
    cpu.ram[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES] = moment.pool


def covers(moment: Moment, low: int, high: int) -> bool:
    return any(begin <= low and high <= begin + data.size for begin, data in moment.spans)


def feed(cpu: slots.SlotCpu, moment: Moment) -> None:
    span = next(data for begin, data in moment.spans if begin == sim.phys(cpu.build.scene_bss))
    at = sim.phys(cpu.symbols["gControllerPads"]) - sim.phys(cpu.build.scene_bss)
    cpu.pads = span[at:at + 24].tobytes()
    sim.inject_vi(cpu, moment, cpu.u32(cpu.symbols["sNumVblanks"]))


def run_from(cpu: slots.SlotCpu, recorded: Moment, following: Moment) -> bool:
    overlay(cpu, recorded)
    cpu.enter_resume(cpu.u32(cpu.symbols["sCurrentCmd"]))
    cpu.scheduler_model()
    cpu.violations.clear()
    feed(cpu, following)
    return cpu.run_frame()


def warm_up(cpu: slots.SlotCpu, source, frame: int, frames: int) -> int:
    """Run the frames before K-1, each from its recorded state, so RAM the recording lacks is what the game
    would have made. Stops at a load, a level change or a frame that leaves the rails. Returns frames run."""
    first = max(0, frame - 1 - frames)
    run = 0
    chain = [source.moment(index) for index in range(first, frame)]
    start = 0
    for at in range(1, len(chain)):
        if not next_frame(chain[at - 1], chain[at]):
            start = at
    for recorded, following in zip(chain[start:], chain[start + 1:], strict=False):
        if not run_from(cpu, recorded, following):
            break
        run += 1
    cpu.violations.clear()
    return run


def next_frame(earlier: Moment, later: Moment) -> bool:
    """Whether `later` is what the game made from `earlier`, one game frame on, in the same place."""
    return (later.task_index == earlier.task_index + 1 and earlier.timer is not None and later.timer == earlier.timer + 1
            and (earlier.level, earlier.area) == (later.level, later.area))


@dataclass
class BuiltMoment:
    data: bytes                  # big-endian, as the N64 sees it: Usamune's own save wrote it
    frame: int
    level: int
    area: int
    mario_object: int
    action: int
    seed: int | None             # None: the recording does not hold it
    segments: bytes
    current_area: int
    fingerprint: str
    worked_out: bool             # the recording lacks the body's tail, which the warm-up rebuilt
    random_before_site: int
    random_in_frame: int
    checked: dict                # bytes differing from the recording at frame K's end, by kind
    warm_frames: int
    seconds: float


class CountingCpu(slots.SlotCpu):
    """SlotCpu that counts calls of the random generator, before Usamune's call site and in all."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.random_calls = self.random_at_site = 0
        self.reached = False
        random_u16 = sim.sext(self.symbols["random_u16"])
        site = sim.sext(self.build.site)
        self.uc.hook_add(self.unicorn.UC_HOOK_CODE, self._on_random, begin=random_u16, end=random_u16)
        self.uc.hook_add(self.unicorn.UC_HOOK_CODE, self._on_count_site, begin=site, end=site)
        # The audio thread's half of the sound never runs here and a package keeps none of its heap, so the
        # game thread's half would walk empty lists. Nothing in a slot comes from it.
        tick = sim.sext(self.symbols["audio_game_loop_tick"])
        self.uc.hook_add(self.unicorn.UC_HOOK_CODE, self._stub("audio_game_loop_tick", lambda: 0), begin=tick, end=tick)

    def _on_random(self, *_):
        self.random_calls += 1

    def _on_count_site(self, *_):
        if not self.reached:
            self.reached, self.random_at_site = True, self.random_calls

    def begin_counting(self) -> None:
        self.random_calls = self.random_at_site = 0
        self.reached = False


def differences(cpu: slots.SlotCpu, moment: Moment) -> dict:
    """What frame K's end holds that the recording does not, by kind, minus what never came from game state."""
    where = sim.compare(cpu, moment.spans, moment.pool, pool_known=True)
    groups: collections.Counter = collections.Counter()
    for address in where.tolist():
        name = sim.label(cpu.symbols, address)
        if name not in PRESENTATION and not POOL_HEAD[0] <= address < POOL_HEAD[1]:
            groups[name] += 1
    return slots.summarize(groups)


# Written by threads and clocks the simulation does not run, or by the display list's own allocation.
PRESENTATION = frozenset({"gProfilerFrameData", "gActiveSPTask", "sNumVblanks", "gCurrentFrameIndex2", "gMatStack",
                          "gDisplayListHead", "gGfxPoolEnd", "gGameVblankQueue", "D_80339CD0", "D_80339CD4"})


# The main pool's first allocation changes between tasks on threads the simulation does not run, and no slot copies it.
POOL_HEAD = (sim.POOL_BEGIN, sim.POOL_BEGIN + 0x400)


def recorded_build(before: Moment, target: Moment, rom: bytes, source):
    """The ROM's build's layout and the recording's code windows, once both frames hold that build's game state and
    the recording holds its Usamune code."""
    version = sim.version_of(rom)
    scene_bss = sim.phys(layout_for(version).scene_bss_start) if version else None
    if scene_bss is None or not (covers(before, scene_bss, scene_bss + 4) and covers(target, scene_bss, scene_bss + 4)):
        raise Refused("no-state")
    windows = source.code_windows()
    save_routine = layout_for(version).usamune_slot_save
    if not any(sim.phys(address) <= sim.phys(save_routine) < sim.phys(address) + length for address, length, _ in windows):
        raise Refused("no-code")
    return layout_for(version), windows


def build(source, rom: bytes, map_path: Path, frame: int, index: int, *, symbols: sim.Symbols | None = None,
          warm: int = WARM_FRAMES) -> BuiltMoment:
    """Usamune's slot for `frame`, or Refused. Nothing here touches a running game."""
    begun = time.perf_counter()
    slots.slot_address(index)
    if frame < 1:
        raise Refused("no-previous-frame")
    if frame >= len(source):
        raise ValueError(f"no frame {frame}")
    target, before = source.moment(frame), source.moment(frame - 1)
    if not next_frame(before, target):
        raise Refused("jump")
    layout, windows = recorded_build(before, target, rom, source)
    symbols = symbols or sim.Symbols(map_path)
    sim.BUDGET_US = FRAME_BUDGET_US
    cpu = CountingCpu(base_image(rom, map_path, windows), rom, symbols, THREAD5_FCSR)
    cpu.uc.reg_write(cpu.mips.UC_MIPS_REG_SP, sim.sext(cpu.build.vsync_sp))
    cpu.call(symbols["sound_init"])      # a stop_sound walks gSoundBanks' lists, which a package does not keep
    seed_word = sim.phys(symbols["gRandomSeed16"]) & ~3
    has_tail = covers(before, sim.phys(layout.slot_tail_start), sim.phys(layout.slot_tail_end)) and \
        covers(before, sim.phys(layout.power_meter_hud_start), sim.phys(layout.power_meter_hud_end))
    has_seed = covers(before, seed_word, seed_word + 4)
    warmed = 0 if has_tail else warm_up(cpu, source, frame, warm)
    found: dict = {}

    def save() -> None:
        if cpu.u32(symbols["gCurrDemoInput"]) != 0:
            raise Refused("demo")
        table = sim.phys(cpu.usamune.segment_table)
        found.update(data=cpu.save_slot(index), current_area=cpu.u32(cpu.usamune.current_area_pointer),
                     segments=cpu.ram[table:table + SEGMENT_BYTES].tobytes(), fingerprint=slots.routine_fingerprint(cpu),
                     seed=cpu.u16(symbols["gRandomSeed16"]) if has_seed else None,
                     action=cpu.u32(cpu.usamune.body_start + 0x0C))
    cpu.begin_counting()
    cpu.pending = save
    ran = run_from(cpu, before, target)
    if "data" not in found:
        if any("never reached" in line for line in cpu.violations):
            raise Refused("never-reached")
        raise Refused("crashed", "; ".join(cpu.violations[:2]))
    if not ran:
        raise Refused("crashed", "; ".join(cpu.violations[:2]))
    checked = differences(cpu, target)
    if has_seed or cpu.random_calls == 0:
        if checked.get("Mario") or checked.get("objects"):
            raise Refused("mismatch", json.dumps(checked))
    elif cpu.random_at_site:
        raise Refused("needs-seed", f"{cpu.random_at_site} random calls before the call site")
    data = found["data"]
    mario = struct.unpack_from(">I", data, slots.TRAILER_MARIO)[0]
    level, area = struct.unpack_from(">hh", data, slots.TRAILER_LEVEL)
    return BuiltMoment(data, frame, level, area, mario, found["action"], found["seed"], found["segments"],
                       found["current_area"], found["fingerprint"], not has_tail, cpu.random_at_site, cpu.random_calls,
                       checked, warmed, time.perf_counter() - begun)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    make = commands.add_parser("build")
    make.add_argument("--package", type=Path, required=True)
    make.add_argument("--rom", type=Path, required=True)
    make.add_argument("--map", type=Path, required=True)
    make.add_argument("--frame", type=int, required=True)
    make.add_argument("--slot", type=int, default=0)
    make.add_argument("--out", type=Path, required=True)
    make.set_defaults(handler=build_command)
    compare = commands.add_parser("compare", help="this build against handoff_slot's replay from a tap session's boot record")
    compare.add_argument("session")
    compare.add_argument("--every", type=int, default=61)
    compare.set_defaults(handler=compare_command)
    args = parser.parse_args()
    return args.handler(args)


def build_command(args) -> int:
    import package as packages
    try:
        rom = args.rom.read_bytes()
        built = build(PackageMoments(packages.open_package(args.package, args.rom)), rom, args.map, args.frame, args.slot)
    except Refused as refused:
        print(json.dumps({"refused": refused.code, "reason": str(refused), "detail": refused.detail}))
        return 0
    args.out.write_bytes(built.data)
    # "build": which Usamune made the slot (the ROM's own). The server's write translates a slot only when this
    # differs from the running game's, so a JP slot named "us" would be translated into a JP game as if it were US.
    print(json.dumps({"build": sim.version_of(rom), "frame": built.frame, "level": built.level, "area": built.area,
                      "mario_object": built.mario_object,
                      "action": built.action, "seed": built.seed, "segments": built.segments.hex(),
                      "current_area": built.current_area, "fingerprint": built.fingerprint, "worked_out": built.worked_out,
                      "random_before_site": built.random_before_site, "random_in_frame": built.random_in_frame,
                      "checked": built.checked, "warm_frames": built.warm_frames, "seconds": round(built.seconds, 3)}))
    return 0


class TapMoments:
    """A tap session's frames as Moments, as the closed replay passes them. Older sessions lack the settings and save
    buffer spans the live lease has, so the replay's own bytes stand in; `extra` adds a new recording's spans."""

    SETTINGS, SAVE_BUFFER = (0x417B4C, 308), (0x207700, 512)

    def __init__(self, windows: list):
        self.windows, self.frames, self.extra = windows, {}, {}

    def __len__(self) -> int:
        return 1 << 30

    def code_windows(self) -> list:
        return self.windows

    def note(self, frame, pool: np.ndarray, ram: np.ndarray) -> None:
        kept = [(low, ram[low:low + size].copy()) for low, size in (self.SETTINGS, self.SAVE_BUFFER)]
        self.frames[frame.index] = (frame, pool, kept)
        self.frames.pop(frame.index - WARM_FRAMES - 3, None)

    def moment(self, index: int) -> Moment:
        frame, pool, kept = self.frames[index]
        return Moment(index, frame.index, frame.timer, frame.level, frame.area,
                      [*frame.spans, *kept, *self.extra.get(index, ())], pool)


def compare_command(args) -> int:
    """Both ways of making frame K's slot, byte for byte; Usamune's profiler bytes are a clock the replay stops."""
    lab = sim.lab_root()
    config = json.loads((lab / "lab.json").read_text(encoding="utf-8"))
    rom, map_path = Path(config["rom"]).read_bytes(), Path(config["map"])
    replay = slots.start_replay(lab / args.session)
    truth = replay.cpu
    anchor = bytes(truth.ram[0x400000:0x400040])
    start = rom.find(anchor)
    code = int(np.flatnonzero(np.frombuffer(rom, np.uint8, 0x20000, start) != truth.ram[0x400000:0x420000])[0])
    tap = TapMoments([(0x80400000, code & ~3, start)])
    tallies: collections.Counter = collections.Counter()
    names: collections.Counter = collections.Counter()
    spans = (TAIL, POWER_METER_HUD, (sim.phys(RANDOM_SEED_WORD), sim.phys(RANDOM_SEED_WORD) + 4))
    before: list = []
    for frame in replay.stream:
        replay.absorb(frame)
        got: dict = {}
        due = frame.index % args.every == 0 and frame.index - 1 in tap.frames

        def at_site(got=got) -> None:
            low = sim.phys(slots.slot_address(3))
            truth.ram[low:low + slots.SLOT_BYTES] = 0      # a fresh slot: Usamune leaves some trailer bytes as it finds them
            got["slot"] = truth.save_slot(3)
        if due:
            truth.pending = at_site
        try:
            replay.step(frame)
        except SystemExit:
            truth.violations.clear()
            truth.scheduler_model()
            tallies["replay did not reach the site"] += 1
        tap.note(frame, replay.pool.copy(), truth.ram)
        if "slot" in got:
            for name, extra in (("older", {}), ("new", {frame.index - 1: before})):
                tap.extra = extra
                try:
                    built = build(tap, rom, map_path, frame.index, 3)
                except Refused as refused:
                    tallies[f"{name}: refused {refused.code}"] += 1
                    continue
                ours, theirs = (np.frombuffer(data, np.uint8) for data in (built.data, got["slot"]))
                differing = {sim.label(truth.symbols, 0x33B170 + int(at)) if at < BODY_BYTES else f"trailer+{int(at) - BODY_BYTES:#x}"
                             for at in np.flatnonzero(ours != theirs)} - {"gProfilerFrameData"}
                tallies[f"{name}: {'equal' if not differing else 'differs'}"] += 1
                names.update((name, label) for label in differing)
        before = [(low, truth.ram[low:high].copy()) for low, high in spans]
    print(dict(tallies))
    print(names.most_common(30))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
