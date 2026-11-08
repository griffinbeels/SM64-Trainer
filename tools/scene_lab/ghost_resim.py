"""A ghost becomes a scene: its recorded pads re-run the game from a start, in the simulation, and the
frames become a scene package the Scene viewer opens as its stage (Ghost Handoff re-simulation).
Simulation only: nothing here touches Project64 or the running game.

    scenes = GhostScenes(lab_root, cache, scenes_dir=..., starts_dir=..., stages=stage_sim.Stages(...))
    path, info = scenes.build(clip_bytes)   # cached: a second build of the same ghost and start is a read
    info["start"], info["parting_frame"], info["save_states"]

The start, best first (measured on his Whomp's Fortress takes, 2026-11-08):
    ghost_start      the start frame saved beside the ghost (start_frame.py): byte-exact with its pool
                     delta, Mario and objects exact on its regions alone
    own_scene        his attempt scene whose first frame is this ghost's first record (same clock,
                     level, area and Mario): 0 bytes off over 351-403 frames with only the ghost's pads
    another_reset    another of his scenes' practice-reset frame in that level and area
    simulated_reset  stage_sim's practice reset of that level, area and act, made from his boot session
For the last two the ghost's own clocks are set; its seed is not known (a ghost carries none). Mario
stayed exact from both (0 units over 351-403 frames); objects whose phase comes from before a reset
(the sliding platforms, the Bomps) stood elsewhere, 100-340 units.

Every re-simulated frame's Mario (his object's drawn position and his action) is compared with the
ghost's recorded pose; the first record where they part is reported (None: they never part). The
package holds the ghost's records 1.. (record 0 is the start itself, whose display list no start holds).

The Save states row's one switch is SAVE_STATES (below); his decision, pending since 2026-11-08.
"""
from __future__ import annotations

import hashlib
import json
import struct
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_sim as sim  # noqa: E402
import package  # noqa: E402
import sim_scene  # noqa: E402
import stage_sim  # noqa: E402

from sm64_events.ghost import start_frame  # noqa: E402
from sm64_events.ghost.clip import decode_clip  # noqa: E402
from sm64_events.ghost.native_layout import POSE_AT, VALID_POSE  # noqa: E402

RESIM_VERSION = 1
GHOST_START, OWN_SCENE, ANOTHER_RESET, SIMULATED_RESET = "ghost_start", "own_scene", "another_reset", "simulated_reset"
STARTS = (GHOST_START, OWN_SCENE, ANOTHER_RESET, SIMULATED_RESET)
# THE switch for the Save states row of a re-simulated scene. Griffin decides (2026-11-08: Mario is exact
# from a reset, moving platforms are at another phase). True: every frame; "before parting": frames
# before the first parting frame only; False: none.
SAVE_STATES = {GHOST_START: True, OWN_SCENE: True, ANOTHER_RESET: False, SIMULATED_RESET: False}
EXACT = {GHOST_START: "bytes", OWN_SCENE: "bytes", ANOTHER_RESET: "mario", SIMULATED_RESET: "mario"}
GAME_TIMER, AREA_COUNTER, MARIO_OBJECT = "gGlobalTimer", "gAreaUpdateCounter", stage_sim.MARIO_OBJECT   # map names
POSE_ACTION = POSE_AT + 40           # gc_pose: position[3], scale[3], angles[3], 3 animation s16, 2 u16, action
GFX_POS = 0x20                       # an object's header.gfx.pos: where the game draws it


def save_states(start: str, frame: int | None = None, parting: int | None = None) -> dict:
    """Whether the Save states row may offer this re-simulated frame (or the scene, frame None)."""
    rule = SAVE_STATES.get(start, False)
    if rule is True:
        return {"allowed": True, "reason": None}
    if rule == "before parting":
        if frame is None or parting is None or frame < parting:
            return {"allowed": True, "reason": None, "until": parting}
        return {"allowed": False, "reason": "The simulated run parts from the ghost before this frame."}
    return {"allowed": False, "reason": "This stage was rebuilt from a practice reset: Mario is exact, but moving "
            "platforms and enemies may be at another point in their cycle, so it makes no save states."}


@dataclass
class Start:
    kind: str
    make: object                      # () -> the start's RAM; called only when its scene is not cached
    identity: str                     # what the cache key names: which file, scene or stage
    detail: dict = field(default_factory=dict)

    @property
    def ram(self) -> np.ndarray:
        if not isinstance(self.make, np.ndarray):
            self.make = self.make()
        return self.make


def _clip_sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pose_of(record) -> tuple[np.ndarray, int] | None:
    """The ghost's recorded Mario on this record (drawn position, action), None without a pose."""
    if not record.valid_fields & VALID_POSE:
        return None
    position = np.array(struct.unpack_from("<3f", record.raw, POSE_AT), np.float32)
    return position, struct.unpack_from("<I", record.raw, POSE_ACTION)[0]


def sim_mario(ram: np.ndarray, symbols: sim.Symbols) -> tuple[np.ndarray, int]:
    mario = struct.unpack_from(">I", ram, sim.phys(symbols[MARIO_OBJECT]))[0]
    position = np.array(struct.unpack_from(">3f", ram, sim.phys(mario) + GFX_POS), np.float32) if mario else np.full(3, np.nan, np.float32)
    action = struct.unpack_from(">I", ram, sim.phys(symbols["gMarioStates"]) + 0x0C)[0]
    return position, action


def pads_of(record, template: bytes) -> bytes:
    x, y, buttons = record.pad
    return stage_sim.pads(buttons, x, y, template)


def first_state(pkg) -> tuple[list, np.ndarray]:
    """A scene's first frame: its task regions and main pool, big-endian as the game holds them."""
    _fields, _extras, spans, level, _known = pkg.scene_regions(0)
    regions = [(begin, sim.be32(bytes(data))) for begin, data in spans if len(data) != sim.TAIL_BYTES]
    pool = sim.be32(bytes(level[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES]))
    return regions, pool


def region_word(regions: list, address: int, size: int) -> bytes | None:
    at = sim.phys(address)
    for begin, data in regions:
        if begin <= at and at + size <= begin + data.size:
            return data[at - begin:at - begin + size].tobytes()
    return None


def probe(regions: list, symbols: sim.Symbols) -> dict:
    """The first frame's words a start is chosen by (None where the regions lack one)."""
    fields = {"timer": (symbols[GAME_TIMER], 4), "counter": (symbols[AREA_COUNTER], 2),
              "level": (symbols[stage_sim.CURR_LEVEL], 2), "area": (symbols[stage_sim.CURR_AREA], 2),
              "action": (symbols["gMarioStates"] + 0x0C, 4),
              "buttons": (symbols["gControllerPads"], 2), "act": (symbols["gCurrActNum"], 2)}
    words = {}
    for name, (address, size) in fields.items():
        word = region_word(regions, address, size)
        words[name] = None if word is None else int.from_bytes(word, "big")
    return words


def words_match(words: dict, clip) -> bool:
    """This scene's first frame is this ghost's first record: same clocks, level, area and Mario's action."""
    first = clip.frames[0]
    if [words["timer"], words["counter"], words["level"], words["area"]] != [
            first.global_timer, first.area_update_counter, first.level, first.area]:
        return False
    pose = pose_of(first)
    return pose is None or words["action"] == pose[1]


def words_reset(words: dict, first) -> bool:
    """A scene that begins with Usamune's practice reset in this ghost's level and area: the reset button
    held and Mario in the spawn action (a take that begins any other way is mid-play or mid-warp)."""
    return (words["level"], words["area"]) == (first.level, first.area) and words["action"] == stage_sim.SPAWN_ACTION \
        and bool((words["buttons"] or 0) & stage_sim.RESET_BUTTON)


def matches(regions: list, clip, symbols: sim.Symbols) -> bool:
    return words_match(probe(regions, symbols), clip)


def act_of(regions: list, symbols: sim.Symbols) -> int | None:
    word = region_word(regions, symbols["gCurrActNum"], 2)
    return None if word is None else int.from_bytes(word, "big")


class GhostScenes:
    def __init__(self, lab_root: Path, cache: Path, *, scenes_dir: Path | None, starts_dir: Path | None,
                 stages: stage_sim.Stages):
        self.lab_root, self.cache = Path(lab_root), Path(cache)
        self.scenes_dir, self.starts_dir, self.stages = scenes_dir, starts_dir, stages
        self.symbols, self.rom_path = stages.symbols, stages.rom_path
        self.locks: dict[str, threading.Lock] = {}
        self.probes: dict[str, dict] = {}
        self.guard = threading.Lock()

    # ---------------------------------------------------------------- scenes he recorded
    def _scenes(self):
        """(path, stamp, the first frame's identity words) of every readable scene in the attempt scenes
        folder; each scene is read once per file version (PROBE_WORDS), so a second build reads only the
        scene it starts from."""
        if self.scenes_dir is None or not Path(self.scenes_dir).is_dir():
            return
        for path in sorted(Path(self.scenes_dir).glob("*.sm64scene")):
            try:
                stat = path.stat()
                stamp = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"
                words = self.probes.get(stamp)
                if words is None:
                    regions, _pool = first_state(package.open_package(path, self.rom_path))
                    words = self.probes[stamp] = probe(regions, self.symbols)
            except (OSError, package.PackageError):
                continue
            yield path, stamp, words

    def _read_start(self, path: Path, base) -> np.ndarray:
        regions, pool = first_state(package.open_package(path, self.rom_path))
        return self._start_ram(regions, pool, base())

    def _start_ram(self, regions: list, pool: np.ndarray | None, base: np.ndarray) -> np.ndarray:
        ram = base.copy()
        for begin, data in regions:
            ram[begin:begin + data.size] = data
        if pool is not None:
            ram[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES] = pool
        return ram

    # ---------------------------------------------------------------- the start
    def starts(self, clip, clip_sha: str, act: int | None = None):
        """The starts this ghost could run from, best first (module docstring); lazily, so a found start
        costs no more scene reads than it took."""
        first = clip.frames[0]

        def base() -> np.ndarray:
            return self.stages.boot()[0]

        if self.starts_dir is not None:
            try:
                saved = start_frame.read_start(self.starts_dir, clip_sha)
            except start_frame.StartError:
                saved = None
            if saved is not None:
                yield self._from_start_file(saved, clip_sha)
        resets = []
        for path, stamp, words in self._scenes():
            if words_match(words, clip):
                yield Start(OWN_SCENE, lambda path=path: self._read_start(path, base), f"{OWN_SCENE}:{stamp}",
                            {"scene": path.name})
            elif words_reset(words, first):
                resets.append((path, stamp, words))
        wanted = act if act is not None else next((words["act"] for _p, _s, words in resets), None)
        for path, stamp, words in resets:
            if wanted is None or words["act"] == wanted:
                yield Start(ANOTHER_RESET, lambda path=path: self._with_clocks(self._read_start(path, base), first),
                            f"{ANOTHER_RESET}:{stamp}",
                            {"scene": path.name, "act": words["act"], "seed": "the reset's own"})
        chosen = wanted or 1
        detail = {"level": first.level, "area": first.area, "act": chosen, "seed": "the simulated reset's own"}
        if wanted is None:
            detail["act_assumed"] = True
        yield Start(SIMULATED_RESET, lambda: self._with_clocks(self.stages.reset(first.level, first.area, chosen).copy(), first),
                    f"{SIMULATED_RESET}:{self.stages.key(first.level, first.area, chosen)}", detail)

    def _from_start_file(self, saved: start_frame.StartFrame, clip_sha: str) -> Start:
        """Regions over the simulated reset of its level, area and act; its pool delta too when that reset
        is the one the delta was made against (else Mario and objects exact, the pool the reset's)."""
        whole = saved.pool_base is not None and saved.pool_base_sha256 == self.stages.reset_digest(
            saved.level, saved.area, saved.act)
        exact = "bytes" if whole else "objects"

        def make() -> np.ndarray:
            ram = self.stages.reset(saved.level, saved.area, saved.act).copy()
            for begin, data in saved.regions + (saved.pool_runs if whole else []):
                ram[begin:begin + len(data)] = np.frombuffer(data, np.uint8)
            return ram
        return Start(GHOST_START, make, f"{GHOST_START}:{clip_sha}:{exact}", {"exact": exact})

    def _with_clocks(self, ram: np.ndarray, first) -> np.ndarray:
        """The ghost carries both clocks on every record; a reset of another run gets this run's."""
        struct.pack_into(">I", ram, sim.phys(self.symbols[GAME_TIMER]), first.global_timer)
        struct.pack_into(">H", ram, sim.phys(self.symbols[AREA_COUNTER]), first.area_update_counter)
        return ram

    # ---------------------------------------------------------------- the scene
    def _lock(self, key: str) -> threading.Lock:
        with self.guard:
            return self.locks.setdefault(key, threading.Lock())

    def build(self, clip_bytes: bytes, *, act: int | None = None, progress=None) -> tuple[Path, dict]:
        clip = decode_clip(clip_bytes)
        clip_sha = _clip_sha(clip_bytes)
        if any(record.pad is None for record in clip.frames):
            raise ValueError("This ghost's inputs are not known on every frame, so it cannot be re-simulated.")
        exact = self._cached_exact(clip_sha)
        if exact is not None:
            return exact
        refused = []
        for started in self.starts(clip, clip_sha, act):
            key = hashlib.sha256(f"{RESIM_VERSION}|{clip_sha}|{started.identity}".encode()).hexdigest()[:20]
            out = self.cache / f"ghost-{clip_sha[:16]}-{started.kind}-{key}.sm64scene"
            note = out.with_suffix(".json")
            with self._lock(key):
                if out.is_file() and note.is_file():
                    return out, json.loads(note.read_text(encoding="utf-8"))
                try:
                    info = self._simulate(clip, clip_sha, started, out, progress)
                except stage_sim.StageError as error:     # this start cannot run the ghost: the next one
                    refused.append({"start": started.kind, **started.detail, "reason": str(error)})
                    continue
                info["refused_starts"] = refused
                if started.kind == OWN_SCENE and self.starts_dir is not None:
                    info["start_saved"] = self._keep_start(clip, clip_bytes, started)
                self.cache.mkdir(parents=True, exist_ok=True)
                note.write_text(json.dumps(info, indent=1), encoding="utf-8")
                return out, info
        raise stage_sim.StageError(f"No start could run this ghost: {refused}")

    def _cached_exact(self, clip_sha: str) -> tuple[Path, dict] | None:
        """A byte-exact scene of this ghost made before (its own scene or its start file): no start can do better."""
        for note in sorted(self.cache.glob(f"ghost-{clip_sha[:16]}-*.json")):
            try:
                info = json.loads(note.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out = note.with_suffix(".sm64scene")
            if info.get("version") == RESIM_VERSION and info.get("ghost_sha256") == clip_sha \
                    and info.get("exact") == "bytes" and out.is_file():
                return out, info
        return None

    def _keep_start(self, clip, clip_bytes: bytes, started: Start) -> bool:
        """His own scene will not stay (retention): save this ghost's start beside it, so it re-simulates
        exactly without the scene. With the pool delta only when the simulated reset is made already (a
        new one costs ~40 s); the regions alone keep Mario and every object exact."""
        first = clip.frames[0]
        scene = Path(self.scenes_dir) / started.detail["scene"]
        act = None
        for _path, stamp, words in self._scenes():
            if stamp.startswith(scene.name + ":"):
                act = words["act"] or 1
        stages = self.stages if act is not None and self.stages.has_reset(first.level, first.area, act) else None
        try:
            return capture_start(scene, clip_bytes, self.starts_dir, stages, self.rom_path, self.symbols) is not None
        except (OSError, ValueError, package.PackageError):
            return False

    def _simulate(self, clip, clip_sha: str, started: Start, out: Path, progress) -> dict:
        _base, fcsr = self.stages.boot()
        template = started.ram[sim.phys(self.symbols["gControllerPads"]):sim.phys(self.symbols["gControllerPads"]) + 24].tobytes()
        machine = stage_sim.Machine(started.ram.copy(), self.stages.rom, self.symbols, fcsr)
        writer = sim_scene.SimScene(out, self.cache / f"work-{time.monotonic_ns()}", self.rom_path, self.symbols)
        parting = farthest = None
        compared, began = 0, time.perf_counter()
        try:
            for index, record in enumerate(clip.frames[1:], start=1):
                machine.step(pads_of(record, template))
                pose = pose_of(record)
                if pose is not None:
                    compared += 1
                    position, action = sim_mario(machine.ram, self.symbols)
                    apart = float(np.linalg.norm(position - pose[0]))
                    farthest = apart if farthest is None or apart > farthest else farthest
                    if parting is None and (action != pose[1] or not np.array_equal(position, pose[0])):
                        parting = index
                writer.add(machine.ram)
                if progress is not None:
                    progress(index, len(clip.frames) - 1)
            writer.finish()
        except BaseException:
            writer.abandon()
            raise
        return {"version": RESIM_VERSION, "ghost_sha256": clip_sha, "start": started.kind, "start_detail": started.detail,
                "exact": EXACT[started.kind] if started.kind != GHOST_START else started.detail["exact"],
                "records": len(clip.frames), "first_record": 1, "parting_frame": parting,
                "parting_global_timer": clip.frames[parting].global_timer if parting is not None else None,
                "poses_compared": compared, "mario_farthest_units": farthest,
                "save_states": save_states(started.kind, parting=parting), "seconds": round(time.perf_counter() - began, 1)}


def capture_start(scene_path: Path, clip_bytes: bytes, starts_dir: Path, stages: stage_sim.Stages | None,
                  rom_path: Path, symbols: sim.Symbols) -> Path | None:
    """A ghost's start frame from his attempt scene (its first frame is the ghost's first record),
    saved beside the ghost: the regions, and the main pool's delta against the simulated reset when one
    can be made. None when the scene is not this ghost's."""
    clip = decode_clip(clip_bytes)
    clip_sha = _clip_sha(clip_bytes)
    regions, pool = first_state(package.open_package(scene_path, rom_path))
    if not matches(regions, clip, symbols):
        return None
    act = act_of(regions, symbols) or 1
    first = clip.frames[0]
    saved = start_frame.StartFrame(clip_sha, first.global_timer, first.level, first.area, act,
                                   [(begin, data.tobytes()) for begin, data in regions])
    if stages is not None:
        try:
            base = stages.reset(first.level, first.area, act)
            reference = base[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES]
            where = np.flatnonzero(pool != reference)
            runs = []
            if where.size:
                for run in np.split(where, np.flatnonzero(np.diff(where) > 8) + 1):
                    low, high = int(run[0]), int(run[-1]) + 1
                    runs.append((sim.POOL_BEGIN + low, pool[low:high].tobytes()))
            saved.pool_base = stages.key(first.level, first.area, act)
            saved.pool_base_sha256 = stages.reset_digest(first.level, first.area, act)
            saved.pool_runs = runs
        except stage_sim.StageError:
            pass                                  # regions alone: Mario and objects exact
    return start_frame.write_start(starts_dir, saved)


# ---------------------------------------------------------------- the server's child (scene_resim.py)
def main(argv=None) -> int:
    """One build in a child of its own, so the server's interpreter never runs the simulation: prints one
    JSON line ({"path", "info"} | {"path"} | {"error"})."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Ghost Handoff re-simulation: one build, one JSON line.")
    parser.add_argument("command", choices=("build", "stage", "capture"))
    parser.add_argument("--lab-root", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--rom", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--clip")
    parser.add_argument("--scenes")
    parser.add_argument("--starts")
    parser.add_argument("--scene")
    parser.add_argument("--act", type=int)
    parser.add_argument("--course", type=int)
    parser.add_argument("--area", type=int, default=1)
    parser.add_argument("--frames", type=int, default=150)
    args = parser.parse_args(argv)
    symbols = sim.Symbols(Path(args.map))
    stages = stage_sim.Stages(Path(args.lab_root), Path(args.cache) / "stages", rom_path=Path(args.rom), symbols=symbols)
    try:
        if args.command == "build":
            scenes = GhostScenes(Path(args.lab_root), Path(args.cache), scenes_dir=Path(args.scenes) if args.scenes else None,
                                 starts_dir=Path(args.starts) if args.starts else None, stages=stages)
            path, info = scenes.build(Path(args.clip).read_bytes(), act=args.act)
            answer = {"path": str(path), "info": info}
        elif args.command == "stage":
            act, level = args.act or 1, stages.level_of_course(args.course)
            answer = {"path": str(stages.package(level, args.area, act, frames=args.frames)),
                      "info": {"course": args.course, "level": level, "area": args.area, "act": act, "frames": args.frames,
                               "start": SIMULATED_RESET, "first_record": 0, "parting_frame": None,
                               "exact": EXACT[SIMULATED_RESET], "save_states": save_states(SIMULATED_RESET)}}
        else:
            written = capture_start(Path(args.scene), Path(args.clip).read_bytes(), Path(args.starts), stages,
                                    Path(args.rom), symbols)
            answer = {"path": str(written) if written else None}
    except (stage_sim.StageError, sim_scene.SimSceneError, ValueError, OSError) as error:
        answer = {"error": str(error)}
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
