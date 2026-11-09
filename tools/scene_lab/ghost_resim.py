# SPDX-License-Identifier: GPL-2.0-or-later
# Runs in the simulation program (SM64TrainerSim.exe); licenses/README.md lists its GPL-2.0-or-later code.
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
    simulated_reset  stage_sim's practice reset of that level, area and act, made from the cartridge alone
                     (the cold boot; no recording of his is needed)
For the last two the ghost's own clocks are set; its seed is not known (a ghost carries none). Mario
stayed exact from both (0 units over 351-403 frames); objects whose phase comes from before a reset
(the sliding platforms, the Bomps) stood elsewhere, 100-340 units.

Either cartridge re-simulates (2026-11-09): the lab's own ROM runs every ghost, whichever cartridge recorded it.
A start is the ROM's own: a start file or a downloaded start made on the other cartridge (its rom_sha256 names
another ROM) is refused, so a JP lab rebuilds a US uploader's ghost from its own simulated reset, and the other
way round. For a player that means: Mario follows the ghost exactly, but moving platforms and enemies may be at a
different point in their cycle than in the recording.

Every re-simulated frame's Mario (his object's drawn position and his action) is compared with the
ghost's recorded pose; the first record where they part is reported (None: they never part). The
package holds the ghost's records 1.. (record 0 is the start itself, whose display list no start holds).

Another player's ghost (2026-11-08, two Whomp's Fortress ghosts copied from the installed Trainer's
community cache, neither his): both re-simulated from his act-1 reset take with Mario on every recorded
pose (318 and 324), 27-34 s each. One was recorded on the JP cartridge and still kept Mario exact on this
US one; the scene says both cartridges ("recorded_on", "simulated_on") instead of implying either.

The Save states row's one switch is SAVE_STATES (below); his decision, pending since 2026-11-08.
"""
from __future__ import annotations

import hashlib
import json
import os
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
import start_check  # noqa: E402

from sm64_events.ghost import start_frame  # noqa: E402
from sm64_events.ghost.clip import decode_clip  # noqa: E402
from sm64_events.ghost.native_layout import POSE_AT, VALID_POSE  # noqa: E402
from sm64_events.memory.ghost_layout import ROM_LAYOUTS  # noqa: E402
from sm64_events.memory.version_probe import version_from_header  # noqa: E402

REGION_OF_LAYOUT = {layout: region for region, layout in ROM_LAYOUTS.items()}   # a clip's rom_layout: "us" | "jp"

RESIM_VERSION = 3                    # 2: every frame shown (VI registers) at the game's 30 fps; 3: a scene's start
                                     # lies over its level's reset, so the level draws (it was black over the boot)
GHOST_START, OWN_SCENE, ANOTHER_RESET, SIMULATED_RESET = "ghost_start", "own_scene", "another_reset", "simulated_reset"
DOWNLOADED_START = "downloaded_start"     # a start that came with someone else's ghost: checked, never trusted
STARTS = (GHOST_START, DOWNLOADED_START, OWN_SCENE, ANOTHER_RESET, SIMULATED_RESET)
# THE switch for the Save states row of a re-simulated scene. Griffin decides (2026-11-08: Mario is exact
# from a reset, moving platforms are at another phase). True: every frame; "before parting": frames
# before the first parting frame only; False: none.
# "checked" (his decision 2026-11-08, "As long as it works with the system without breaking anything, then it's
# fine"): frames before the first parting frame only, and each slot first passes LOAD_CHECK_FRAMES of a load
# check in the simulation (check_slot_load).
SAVE_STATES = {GHOST_START: True, OWN_SCENE: True, DOWNLOADED_START: "checked", ANOTHER_RESET: "checked",
               SIMULATED_RESET: "checked"}
EXACT = {GHOST_START: "bytes", OWN_SCENE: "bytes", DOWNLOADED_START: "objects", ANOTHER_RESET: "mario",
         SIMULATED_RESET: "mario"}
PROBES_FILE, PROBE_VERSION = "scene-probes.json", 1   # his scenes' first-frame words, kept between builds
WARM_FRAMES = 10                     # a warmed stage's frames packed ahead, for the packer's seed
LOAD_CHECK_FRAMES = 60
LOAD_INDEX = 3                       # the simulated slot a load check uses (the running game is never touched)
GAME_TIMER, AREA_COUNTER, MARIO_OBJECT = "gGlobalTimer", "gAreaUpdateCounter", stage_sim.MARIO_OBJECT   # map names
POSE_ACTION = POSE_AT + 40           # gc_pose: position[3], scale[3], angles[3], 3 animation s16, 2 u16, action
GFX_POS = 0x20                       # an object's header.gfx.pos: where the game draws it


def save_states(start: str, frame: int | None = None, parting: int | None = None) -> dict:
    """Whether the Save states row may offer this re-simulated frame (or the scene, frame None)."""
    rule = SAVE_STATES.get(start, False)
    if rule is True:
        return {"allowed": True, "reason": None}
    if rule in ("before parting", "checked"):
        if frame is None or parting is None or frame < parting:
            return {"allowed": True, "reason": None, "until": parting, "check": "load" if rule == "checked" else None}
        return {"allowed": False, "reason": "From this frame on the rebuilt run no longer matches the ghost, "
                "so it makes no save state."}
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
    held and Mario in a spawn action, his level's own (stage_sim.SPAWN_ACTIONS; a take that begins any other way
    is mid-play or mid-warp)."""
    return (words["level"], words["area"]) == (first.level, first.area) and words["action"] in stage_sim.SPAWN_ACTIONS \
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
        self.refusals: list = []
        self.guard = threading.Lock()

    # ---------------------------------------------------------------- scenes he recorded
    def _scenes(self):
        """(path, stamp, the first frame's identity words) of every readable scene in the attempt scenes
        folder; each scene file version is read once and its words kept on disk (PROBES_FILE), so a build
        reads only the scene it starts from. Reading all of his 16 scenes cost 1.5 s on every ask, a cached
        ghost's included (2026-11-08)."""
        folders = self.scenes_dir if isinstance(self.scenes_dir, (list, tuple)) else [self.scenes_dir]
        paths = sorted({path for folder in folders if folder is not None and Path(folder).is_dir()
                        for path in Path(folder).glob("*.sm64scene")})
        known = self._probe_file()
        seen, found = {}, []
        for path in paths:
            try:
                stat = path.stat()
                stamp = f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}"
                where = f"{path}|{stat.st_size}|{stat.st_mtime_ns}"
                words = self.probes.get(stamp) or known.get(where)
                if words is None:
                    regions, _pool = first_state(package.open_package(path, self.rom_path))
                    words = probe(regions, self.symbols)
                self.probes[stamp] = seen[where] = words
            except (OSError, package.PackageError):     # a scene still being written, or not one
                continue
            found.append((path, stamp, words))
        if seen != known:
            self._probe_file(seen)
        yield from found

    def _probe_file(self, words: dict | None = None) -> dict:
        """The kept probe words ({path|size|mtime: words}) for this map; with `words`, writes them instead."""
        path = self.cache / PROBES_FILE
        mark = f"{PROBE_VERSION}|{getattr(self.stages, 'map_path', None)}|{self.rom_path}"
        if words is not None:
            try:
                self.cache.mkdir(parents=True, exist_ok=True)
                partial = path.with_name(path.name + ".partial")
                partial.write_text(json.dumps({"mark": mark, "words": words}), encoding="utf-8")
                partial.replace(path)
            except OSError:
                pass                                   # a next build reads the scenes again
            return words
        try:
            kept = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return kept.get("words", {}) if isinstance(kept, dict) and kept.get("mark") == mark else {}

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

        def base_of(words: dict, detail: dict):
            return lambda: self._scene_base(words, detail, act)

        if self.starts_dir is not None:
            try:
                saved = start_frame.read_start(self.starts_dir, clip_sha, rom=self.stages.rom)
            except start_frame.StartError:
                saved = None
            if saved is not None and saved.rom_sha256 != hashlib.sha256(self.stages.rom).hexdigest():
                self.refusals.append({"start": GHOST_START, "reason": "This ghost's start was made on another ROM."})
            elif saved is not None:
                yield self._from_start_file(saved, clip_sha)
            downloaded = self._downloaded(clip, clip_sha)
            if downloaded is not None:
                yield downloaded
        resets = []
        for path, stamp, words in self._scenes():
            if words_match(words, clip):
                detail = {"scene": path.name}
                yield Start(OWN_SCENE, lambda path=path, words=words, detail=detail: self._read_start(
                    path, base_of(words, detail)), f"{OWN_SCENE}:{stamp}", detail)
            elif words_reset(words, first):
                resets.append((path, stamp, words))
        wanted = act if act is not None else next((words["act"] for _p, _s, words in resets), None)
        for path, stamp, words in resets:
            if wanted is None or words["act"] == wanted:
                detail = {"scene": path.name, "act": words["act"], "seed": "the reset's own"}
                yield Start(ANOTHER_RESET, lambda path=path, words=words, detail=detail: self._with_clocks(
                    self._read_start(path, base_of(words, detail)), first), f"{ANOTHER_RESET}:{stamp}", detail)
        chosen = wanted or 1
        detail = {"level": first.level, "area": first.area, "act": chosen, "seed": "the simulated reset's own"}
        if wanted is None:
            detail["act_assumed"] = True
        yield Start(SIMULATED_RESET, lambda: self._with_clocks(self.stages.reset(first.level, first.area, chosen).copy(), first),
                    f"{SIMULATED_RESET}:{self.stages.key(first.level, first.area, chosen)}", detail)

    def _scene_base(self, words: dict, detail: dict, ghost_act: int | None) -> np.ndarray:
        """What a scene's first frame lies over: the reset of its own level, area and act, which holds the level's
        data the game loaded outside the scene's regions and main pool (segment 0x09, WF's textures, begins at
        0x801C1000, just past the main pool's end; over the castle's boot RAM the level drew black, the first real
        pictures showed, 2026-11-09). The act is the scene's own first-frame gCurrActNum. Where that says nothing in
        a course with an act selector, the ghost's star act is taken when known (`detail["act_from"]` says so),
        else the start is refused (StageError: the next start is tried); a course without one uses act 1, the key
        its reset has. Where no reset can be made (the castle's own levels, scene_warm.py), the boot, and `detail`
        says why. `detail["base"]` names what it lay over."""
        level, area, scene_act = words["level"], words["area"], words["act"]
        if 1 <= self.stages.course_of_level(level) <= stage_sim.LAST_ACT_COURSE:
            chosen = scene_act or ghost_act
            if not chosen:
                raise stage_sim.StageError("This scene names no act, and its course has six, so the level it lies "
                                           "over cannot be chosen.")
            if not scene_act:
                detail["act_from"] = "the ghost's star (the scene names none)"
        else:
            chosen = 1
        try:
            ram = self.stages.reset(level, area, chosen)
        except stage_sim.StageError as error:
            detail.update(base="boot", base_reason=str(error))
            return self.stages.boot()[0]
        detail["base"] = f"reset {level}-{area}-{chosen}"
        return ram

    def _downloaded(self, clip, clip_sha: str) -> Start | None:
        """A start that came with someone else's ghost (start_frame.accept_start_blob kept it apart), once it
        passes every check: its format and binding again, this ROM and Usamune build, then start_check over
        the state it would make. Its main-pool changes are never applied (start_check.py says why). A start
        that fails is skipped; its sentence goes to the scene's refused starts."""
        try:
            saved = start_frame.read_start(self.starts_dir, clip_sha, downloaded=True, rom=self.stages.rom)
            if saved is None:
                return None
            start_frame.bind(saved, clip.frames[0])
        except start_frame.StartError as error:
            self.refusals.append({"start": DOWNLOADED_START, "reason": str(error)})
            return None
        if saved.rom_sha256 != hashlib.sha256(self.stages.rom).hexdigest():
            self.refusals.append({"start": DOWNLOADED_START, "reason": "This ghost's start was made on another ROM."})
            return None
        if saved.usamune_fingerprint != self.stages.fingerprint():
            self.refusals.append({"start": DOWNLOADED_START, "reason": "This ghost's start was made on another Usamune build."})
            return None
        try:
            reference = self.stages.reset(saved.level, saved.area, saved.act)
        except stage_sim.StageError as error:
            self.refusals.append({"start": DOWNLOADED_START, "reason": str(error)})
            return None
        ram = reference.copy()
        for begin, data in saved.regions:
            ram[begin:begin + len(data)] = np.frombuffer(data, np.uint8)
        reasons = start_check.check_state(ram, self.stages.reference(saved.level, saved.area, saved.act),
                                          start_frame.allowed_spans(saved.game_version))
        if reasons:
            self.refusals.append({"start": DOWNLOADED_START, "reason": reasons[0], "more": len(reasons) - 1})
            return None
        return Start(DOWNLOADED_START, ram, f"{DOWNLOADED_START}:{clip_sha}:{saved.made_utc}", {"exact": "objects"})

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
        self.stages.simulates()                     # one sentence for the whole cartridge, not one per start
        refused = self.refusals = []
        if progress is not None:
            progress("choosing a start", 0, 0)
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
        log_refusals = "; ".join(f"{row['start']}: {row['reason']}" for row in refused)
        print(f"no start could run {clip_sha[:16]}: {log_refusals}", file=sys.stderr)
        raise stage_sim.StageError("No start could play this ghost in the simulation, so it cannot become a scene.")

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
        exactly without the scene, with the main pool's delta against the cold reset (a new one costs ~6 s)."""
        scene = next((path for path, _stamp, _words in self._scenes() if path.name == started.detail["scene"]), None)
        if scene is None:
            return False
        try:
            return capture_start(scene, clip_bytes, self.starts_dir, self.stages, self.rom_path, self.symbols) is not None
        except (OSError, ValueError, package.PackageError, stage_sim.StageError):
            return False

    def _simulate(self, clip, clip_sha: str, started: Start, out: Path, progress) -> dict:
        template = started.ram[sim.phys(self.symbols["gControllerPads"]):sim.phys(self.symbols["gControllerPads"]) + 24].tobytes()
        machine = self.stages.machine(started.ram.copy())
        total = len(clip.frames) - 1
        sidecar = out.with_suffix(".grow")
        state = {"simulated": 0, "parting": None}       # the main thread's, read by the packer's publications

        def published(frames: int) -> None:
            """The scene so far can be opened (scene_session.Growth): the server opens it at the first one. Its
            frames 0..frames-1 are records 1..frames, each compared before it was packed, so "built_to" (the
            newest record shown) lies before any parting found so far, and the save state rule already holds
            for it."""
            parting = state["parting"]
            if progress is not None:
                progress("re-simulating", state["simulated"], total, package=str(out), sidecar=str(sidecar),
                         published=frames, start=started.kind, built_to=frames, parting=parting,
                         save_states=save_states(started.kind, parting=parting))
        writer = sim_scene.SimScene(out, self.cache / f"work-{time.monotonic_ns()}", self.rom_path, self.symbols,
                                    self.stages.vi(), sidecar=sidecar, on_publish=published,
                                    seeds=getattr(self.stages, "cache", None))
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
                        parting = state["parting"] = index
                writer.add(machine.ram)
                state["simulated"] = index
                if progress is not None:
                    progress("re-simulating", index, total)
            if progress is not None:
                progress("writing the scene", total, total)
            writer.finish()
        except BaseException:
            writer.abandon()
            raise
        return {"version": RESIM_VERSION, "ghost_sha256": clip_sha, "start": started.kind, "start_detail": started.detail,
                "exact": started.detail.get("exact") or EXACT[started.kind],
                "recorded_on": REGION_OF_LAYOUT.get(clip.rom_layout), "simulated_on": self.stages.region(),
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
    rom = Path(rom_path).read_bytes()
    saved = start_frame.StartFrame(clip_sha, version_from_header(rom[:0x40]) or "us", first.global_timer,
                                   first.area_update_counter, first.level, first.area, act,
                                   [(begin, data.tobytes()) for begin, data in regions], hashlib.sha256(rom).hexdigest(),
                                   stages.fingerprint() if stages is not None else "0" * 64)
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
    return start_frame.write_start(starts_dir, saved, rom)


# ---------------------------------------------------------------- the load check
def check_slot_load(package_path: Path, frame: int, slot: bytes, clip, stages: stage_sim.Stages,
                    first_record: int = 1) -> dict:
    """Does a save state built at this package frame load and play on? In the simulation only: the scene's
    state at that frame, the slot written into a simulated slot, Usamune's own load at its own call site on
    the next frame, then the ghost's pads for LOAD_CHECK_FRAMES frames. It passes when nothing stops the
    game and Mario stands where the ghost's recorded pose has him on every frame the ghost covers.
    {"ok": bool, "reason": sentence or None, "frames": played, "compared": poses compared}."""
    import handoff_slot  # noqa: PLC0415 - Usamune's own load at its own site, the build's own (frame_sim.SimBuild)
    from sm64_events.slotlibrary.usamune import BODY_BYTES, slot_address  # noqa: PLC0415
    reasons = start_check.check_slot(slot, stages.reference_for_slot(slot), BODY_BYTES)
    if reasons:
        return {"ok": False, "reason": reasons[0], "frames": 0, "compared": 0}
    pkg = package.open_package(package_path, stages.rom_path)
    _fields, _extras, spans, level, _known = pkg.scene_regions(frame)
    ram = stages.boot()[0].copy()
    for begin, data in spans:
        if len(data) != sim.TAIL_BYTES:
            ram[begin:begin + len(data)] = sim.be32(bytes(data))
    ram[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES] = sim.be32(bytes(level[sim.POOL_BEGIN:sim.POOL_BEGIN + sim.POOL_BYTES]))
    low = sim.phys(slot_address(LOAD_INDEX))
    ram[low:low + len(slot)] = np.frombuffer(slot, np.uint8)
    _base, fcsr = stages.boot()
    machine = stage_sim.Machine(ram, stages.rom, stages.symbols, fcsr, cpu_class=handoff_slot.SlotCpu)
    cpu = machine.cpu
    template = ram[sim.phys(stages.symbols["gControllerPads"]):sim.phys(stages.symbols["gControllerPads"]) + 24].tobytes()
    record = frame + first_record
    records = clip.frames
    cpu.pending = lambda: cpu.load_slot(LOAD_INDEX)
    played = compared = 0
    try:
        machine.step(pads_of(records[record], template))       # the frame Usamune's load runs in
        for index in range(record + 1, record + 1 + LOAD_CHECK_FRAMES):
            covered = index < len(records)
            machine.step(pads_of(records[index], template) if covered else stage_sim.pads(0, 0, 0, template))
            played += 1
            pose = pose_of(records[index]) if covered else None
            if pose is not None:
                compared += 1
                position, action = sim_mario(machine.ram, stages.symbols)
                if action != pose[1] or not np.array_equal(position, pose[0]):
                    return {"ok": False, "frames": played, "compared": compared,
                            "reason": "After this save state loads, Mario does not go on the way the ghost did."}
    except stage_sim.StageError:
        return {"ok": False, "frames": played, "compared": compared,
                "reason": "The game stopped after this save state loaded, so it is not offered."}
    return {"ok": True, "reason": None, "frames": played, "compared": compared}


def warm(stages: stage_sim.Stages, level: int, area: int, act: int, scenes: GhostScenes | None = None) -> dict:
    """What a first build of that stage would make before its first frame, made ahead (the server's
    background child, scene_resim.warm): the practice reset, the boot RAM, the packer's seed of the ROM and that
    level's ROM windows, and his scenes' first frames read (scene-probes.json)."""
    began = time.perf_counter()
    kept = sum(1 for _scene in scenes._scenes()) if scenes is not None else 0
    stages.reset(level, area, act)
    # A few frames of that stage packed and thrown away: the packer's seed then knows that level's ROM windows
    # (its first frames otherwise search the ROM: ~0.8 s of a first build, 2026-11-08).
    out = stages.cache / f"warm-{os.getpid()}.sm64scene"
    try:
        stages.package(level, area, act, frames=WARM_FRAMES, out=out)
    finally:
        out.unlink(missing_ok=True)
    return {"warm": stages.key(level, area, act), "scenes": kept, "seconds": round(time.perf_counter() - began, 2)}


# ---------------------------------------------------------------- the server's child (scene_resim.py)
def _progress_lines():
    """progress(stage, done, total, **extra): one JSON line the server reads as it comes (scene_resim.py), at most
    ~5 a second; every publication of the growing package (extra: package, sidecar, published, start) is printed,
    from the packer's thread beside the main one."""
    shown, printing = {"at": 0.0}, threading.Lock()

    def progress(stage: str, done: int, total: int, **extra) -> None:
        now = time.monotonic()
        with printing:
            if extra or done in (0, total) or now - shown["at"] > 0.2:
                shown["at"] = now
                sys.stdout.write(json.dumps({"progress": {"stage": stage, "done": done, "total": total, **extra}}) + "\n")
                sys.stdout.flush()
    return progress


def main(argv=None) -> int:
    """One build in a child of its own, so the server's interpreter never runs the simulation: prints one
    JSON line ({"path", "info"} | {"path"} | {"error"})."""
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(description="Ghost Handoff re-simulation: one build, one JSON line.")
    parser.add_argument("command", choices=("build", "stage", "capture", "check-slot", "warm"))
    parser.add_argument("--lab-root", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--rom", required=True)
    parser.add_argument("--map", required=True)
    parser.add_argument("--clip")
    parser.add_argument("--scenes", action="append", default=[], help="a folder of his attempt scenes (repeatable)")
    parser.add_argument("--starts")
    parser.add_argument("--scene")
    parser.add_argument("--act", type=int)
    parser.add_argument("--course", type=int)
    parser.add_argument("--level", type=int)
    parser.add_argument("--area", type=int, default=1)
    parser.add_argument("--frames", type=int, default=150)
    parser.add_argument("--package")
    parser.add_argument("--frame", type=int)
    parser.add_argument("--slot")
    parser.add_argument("--first-record", type=int, default=1)
    args = parser.parse_args(argv)
    symbols = sim.Symbols(Path(args.map))
    progress = _progress_lines()
    stages = stage_sim.Stages(Path(args.lab_root), Path(args.cache) / "stages", rom_path=Path(args.rom), symbols=symbols,
                              map_path=Path(args.map))
    try:
        if args.command == "build":
            scenes = GhostScenes(Path(args.lab_root), Path(args.cache), scenes_dir=[Path(folder) for folder in args.scenes],
                                 starts_dir=Path(args.starts) if args.starts else None, stages=stages)
            path, info = scenes.build(Path(args.clip).read_bytes(), act=args.act, progress=progress)
            answer = {"path": str(path), "info": info}
        elif args.command == "stage":
            act, level = args.act or 1, stages.level_of_course(args.course)
            progress("making the stage", 0, 0)
            answer = {"path": str(stages.package(level, args.area, act, frames=args.frames)),
                      "info": {"course": args.course, "level": level, "area": args.area, "act": act, "frames": args.frames,
                               "start": SIMULATED_RESET, "first_record": 0, "parting_frame": None,
                               "exact": EXACT[SIMULATED_RESET], "save_states": save_states(SIMULATED_RESET)}}
        elif args.command == "warm":
            level = args.level if args.level is not None else stages.level_of_course(args.course)
            answer = warm(stages, level, args.area, args.act or 1,
                          GhostScenes(Path(args.lab_root), Path(args.cache), scenes_dir=[Path(folder) for folder in args.scenes],
                                      starts_dir=None, stages=stages))
        elif args.command == "check-slot":
            answer = check_slot_load(Path(args.package), args.frame, Path(args.slot).read_bytes(),
                                     decode_clip(Path(args.clip).read_bytes()), stages, args.first_record)
        else:
            written = capture_start(Path(args.scene), Path(args.clip).read_bytes(), Path(args.starts), stages,
                                    Path(args.rom), symbols)
            answer = {"path": str(written) if written else None}
    except (stage_sim.StageError, sim_scene.SimSceneError, ValueError, OSError) as error:
        answer = {"error": str(error)}
    print(json.dumps(answer))
    return 0


if __name__ == "__main__":
    import sim_boundary  # a simulation process: no (L)GPL-3.0 library from here on
    sim_boundary.enter()
    raise SystemExit(main())
