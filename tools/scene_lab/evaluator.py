"""The free-camera evaluator: a recorded scene re-rendered by SM64's own code (M4 E3/E5 lab).

    evaluator = Evaluator.open(package.sm64scene, rom.z64, MappingUS.map)
    state = evaluator.state(frame)              # the scene state that frame's render consumed
    view = evaluator.display_list(frame, (focus, position))

    uv run --with unicorn python tools/scene_lab/evaluator.py resolve <in.sm64scene> <out.sm64scene>
        --rom <z64> --map <MappingUS.map>

A viewer asks for display lists; the proof drivers (e2_georender.py from-package and
from-tap) ask for the same states and add their comparisons. state() rebuilds the frame's
RDRAM from the package's scene-state layer and the local ROM (SceneImages), then resolves what
the original render consumed (e2_georender.resolve_task). Searching costs one traced
regeneration, and several more where render-stepped state or a throwMatrix must be put back
(~32 ms a frame on Bob-omb Battlefield's walkers). A package whose scene-resolution layer
recorded that search's answer costs one traced regeneration, which must reproduce the captured
list byte for byte or the search runs as before (e2_georender.recorded_state). `resolve` writes
that layer: it searches every frame of a package and records the answers.

display_list() re-runs geo_process_root from that state with gLakituState moved, untraced, and
splices its output between the captured prologue and HUD (e2_georender.moved_view). Each kind
of run keeps its own reused sandboxes (e2_georender.Cpu): resolution on one pair, views on
another, so translated code and ROM checks carry from frame to frame, and a viewer can resolve
the next frames on a second thread (prepare) while it draws a view of this one. Two locks:
`lock` covers only the caches (held for a lookup or an insert), `resolving` serializes the
work that fills them (package reads, assembly, the resolution sandboxes), so a view of a cached
state never waits behind a resolution of another frame. Recent scenes and states stay cached
(each holds several 8 MiB images), so a camera change re-runs only geo_process_root, and the
next frame reuses the previous one. A viewer may attach state resolvers (state_resolver.py):
child processes with their own Evaluator over the same package, which resolve the frames it
hands them (ahead) and deliver each state into this cache; state() then waits for a frame a
child has rather than resolving it again, and resolves here what no child can.

The Trainer runs this evaluator in a simulation process of its own (evaluator_child.py: Unicorn is
GPL-2.0-only and never loads in the server, sim_boundary.py); the proof commands (e2_georender.py,
this file's resolve) run it in the process they are started in.

Imported packages stay untrusted: the package reader checks every record, and code runs only
from this ROM (the package's code loads are rebuilt from it and refused on mismatch). A recorded
resolution names addresses only and is taken only when the game's own code, run from the state
it describes, redraws exactly what was captured.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package as scene_package  # noqa: E402
import pick  # noqa: E402

FCR31 = 0x01000800      # libultra's FS|EV, as the savestates hold


def _spans(mask: np.ndarray) -> list[tuple[int, int]]:
    edges = np.flatnonzero(np.diff(np.concatenate(([0], mask.astype(np.int8), [0]))))
    return [(int(edges[i]), int(edges[i + 1])) for i in range(0, len(edges), 2)]


class SceneImages:
    """A recorded scene state as the evaluator's RDRAM: the captured bytes in the CPU's
    big-endian order, the ROM's code images where nothing was captured (image, fill), and
    the known mask widened by the code images (image_known), as tap.SceneState's arrays
    come in (Project64's word-swapped order, the pattern where nothing was captured)."""

    def __init__(self, image: np.ndarray, image_known: np.ndarray, fill: np.ndarray):
        self.image = image
        self.fill_spans = _spans(fill)
        self.known_spans = _spans(image_known)

    def assemble(self, ram: np.ndarray, known: np.ndarray) -> tuple[bytes, np.ndarray]:
        """(RDRAM big-endian, known | the code images) for one state; the inputs are not changed."""
        ram_be = ram.view(np.uint32).byteswap().view(np.uint8)   # captured spans are whole words
        for lo, hi in self.fill_spans:
            np.copyto(ram_be[lo:hi], self.image[lo:hi], where=~known[lo:hi])
        known_all = known.copy()
        for lo, hi in self.known_spans:
            known_all[lo:hi] = True
        return ram_be.tobytes(), known_all


@dataclass
class Scene:
    """One frame's recorded state, assembled: its task, its task regions, RDRAM, known mask."""
    task: object
    regions: object
    ram: bytes
    known: np.ndarray


@dataclass
class View:
    """A task re-rendered from a moved camera: RDRAM holding the spliced list at data_ptr
    (None after a violation or without pool room), the run and its object culling."""
    task: tuple
    ram: bytearray | None
    data_ptr: int
    data_size: int
    run: dict
    culling: list


class Evaluator:
    """A scene package bound to the local ROM, evaluated frame by frame."""

    def __init__(self, package: scene_package.Package, rom: bytes, symbols, fcr31: int = FCR31,
                 cache: int = 4, source: Path | None = None, use_recorded: bool = True):
        import e2_georender as e2
        self.e2, self.package, self.rom, self.symbols = e2, package, rom, symbols
        self.source = source or Path("package")
        if package.states is None:
            raise scene_package.PackageError("this package carries no scene state (version 0, or --no-state)")
        self.windows = package.states.code_windows()
        self.images = SceneImages(*e2.code_images(rom, self.windows))
        self.cpu = e2.Cpu(rom, fcr31)              # resolution: traced runs and untraced trials
        self.view_cpu = e2.Cpu(rom, fcr31)         # display_list's moved views
        self.cache = cache
        self.use_recorded = use_recorded
        self.recorded = Counter()                  # taken, refused (searched instead), absent
        self.lock = threading.Lock()               # the caches
        self.resolving = threading.RLock()         # filling them: package, assembly, self.cpu
        self._pending: set[int] = set()            # states a thread is resolving now
        self._settled = threading.Condition(self.lock)
        self._scenes: OrderedDict[int, Scene] = OrderedDict()
        self._states: OrderedDict[int, object] = OrderedDict()
        self.resolvers = None                      # state_resolver.Resolvers: states resolved in child processes
        self.state_cache = cache                   # states kept (attach() makes room for the frames ahead)

    @classmethod
    def open(cls, package_path: Path, rom_path: Path, map_path: Path, **options) -> Evaluator:
        import e2_georender as e2
        rom = rom_path.read_bytes()
        symbols = e2.load_map(map_path, rom=rom)
        package = scene_package.open_package(package_path, rom_path)
        return cls(package, rom, symbols, source=package_path, **options)

    def __len__(self) -> int:
        return len(self.package)

    def _cached(self, cache: OrderedDict, index: int):
        with self.lock:
            value = cache.get(index)
            if value is not None:
                cache.move_to_end(index)
            return value

    def _remember(self, cache: OrderedDict, index: int, value):
        with self.lock:
            cache[index] = value
            cache.move_to_end(index)
            while len(cache) > (self.state_cache if cache is self._states else self.cache):
                cache.popitem(last=False)
        return value

    def scene(self, index: int) -> Scene:
        """The frame's recorded state, assembled (package.scene_state, then SceneImages)."""
        found = self._cached(self._scenes, index)
        if found is not None:
            return found
        with self.resolving:
            found = self._cached(self._scenes, index)          # assembled while this waited
            if found is not None:
                return found
            task, regions, ram, known = self.package.scene_state(index)
            return self._remember(self._scenes, index, Scene(task, regions, *self.images.assemble(ram, known)))

    def previous(self, index: int) -> tuple[bytes, np.ndarray] | None:
        """The previous frame's (RDRAM, known mask) when it is the previous game frame."""
        if not index:
            return None
        before = self.scene(index - 1)
        timer = self.e2.US["gGlobalTimer"]
        now = struct.unpack_from(">I", self.scene(index).ram, self.e2.phys(timer))[0]
        then = struct.unpack_from(">I", before.ram, self.e2.phys(timer))[0]
        return (before.ram, before.known) if then + 1 == now else None

    def state(self, index: int):
        """The scene state frame `index`'s render consumed (e2_georender.Resolved): the task's
        geo output found in its pool, regenerated from captured state with render-stepped state
        and throwMatrix pointers put back (as the package recorded them when that checks out,
        else searched); status names why a task has none ("no scene", ...)."""
        with self.lock:
            while True:
                found = self._states.get(index)
                if found is not None:
                    self._states.move_to_end(index)
                    return found
                if index in self._pending:             # another thread is resolving this one: wait
                    self._settled.wait()               # for it, not for whatever that thread does next
                elif self.resolvers is not None and self.resolvers.take(index):
                    self._settled.wait(1.0)            # a child resolves it (delivered); take() again
                else:
                    break
            self._pending.add(index)
        try:
            with self.resolving:
                if index:
                    self.scene(index - 1)          # seek forward: the previous frame first
                scene = self.scene(index)
                frame = self.e2.TapFrame(self.source, scene.task, scene.ram)
                recorded = self.package.resolution(index) if self.use_recorded else None
                resolved = self.e2.resolve_task(frame, self.cpu, scene.known, self.symbols, self.previous(index),
                                                recorded)
                self.recorded["absent" if recorded is None else "taken" if resolved.recorded else "refused"] += 1
            return self._remember(self._states, index, resolved)
        finally:
            with self.lock:
                self._pending.discard(index)
                self._settled.notify_all()

    def prepare(self, index: int) -> None:
        """Resolve frame `index` now so a later state() or display_list() finds it cached (a viewer
        calls this on a second thread for the frames it expects next)."""
        if 0 <= index < len(self):
            self.state(index)

    def attach(self, resolvers, ahead: int) -> None:
        """Resolve states in child processes from now on (state_resolver.Resolvers, made with
        delivered as its deliver). The cache keeps room for the `ahead` frames they resolve ahead
        and as many just drawn: a state read moves to the end of the cache, so with less room a
        frame resolved ahead and not yet drawn was the oldest and went first."""
        self.state_cache = self.cache + 2 * ahead
        self.resolvers = resolvers

    def ahead(self, indices: list[int]) -> bool:
        """Hands the frames a viewer expects next to the child processes, without waiting; a frame
        already resolved or being resolved here is left out. False without children ready: the
        caller resolves ahead itself (prepare)."""
        resolvers = self.resolvers
        if resolvers is None:
            return False
        with self.lock:              # held while queueing: a state delivered meanwhile is not queued again
            return resolvers.ahead([index for index in indices if 0 <= index < len(self)
                                    and index not in self._states and index not in self._pending])

    def delivered(self, index: int, answer: dict | None, ram: bytes | None) -> None:
        """A state a child resolved (state_resolver: the state's small parts and its resolved
        RDRAM), cached as if state() had resolved it here, when the child's package recorded the
        same frame and scene state as this one's. None, or another frame: the waiting state()
        resolves it here."""
        resolved = None
        if answer is not None and answer.get("digests") != [self.package.index.frames[index][4].hex(),
                                                           self.package.states.rows[index][4].hex()]:
            resolvers, self.resolvers = self.resolvers, None     # another package: resolve here from now on
            if resolvers is not None:
                resolvers.close()
        elif answer is not None:
            frame = None
            if answer["status"] is None:
                frame = self.e2.TapFrame(self.source, SimpleNamespace(task=tuple(answer["words"])), ram)
            resolved = self.e2.Resolved(answer["entry"], None, answer["status"], frame, answer.get("layout"),
                                        answer.get("result"), answer.get("notes") or {},
                                        [tuple(span) for span in answer.get("restored", ())],
                                        [tuple(pointer) for pointer in answer.get("pointers", ())],
                                        bool(answer.get("recorded")))
            self._remember(self._states, index, resolved)
        with self.lock:
            if resolved is not None:
                self.recorded[answer["recorded_as"]] += 1
            self._settled.notify_all()

    def pose(self, index: int) -> dict:
        """Frame `index`'s recorded camera (gLakituState's focus, position and screen roll, the
        camera the drawing used, and the field of view the picture shows) and Mario's position,
        in the game's world coordinates: where a free camera starts, and what Focus Mario looks
        at. A free camera of all four draws the picture's own camera (run_geo).

        The FOV is the captured list's own (pick.fov_of its world projection), shake included,
        so a free camera starts at the FOV on screen even where this evaluator's state for the
        frame draws another (the first frame of a FOV ramp or a shake, measured 2026-10-25:
        48.5 against 52 degrees). Where a run from this camera drew the same FOV to 0.001
        degrees it is the float the game drew with (sFOVState's fov, plus the shake offset when
        a shake added one: shake_camera_fov leaves a stale offset when none runs), so the free
        picture is the recorded one byte for byte. None when the picture has no perspective
        camera."""
        e2, ram = self.e2, self.scene(index).ram

        def vector(address: int) -> list[float]:
            return [float(value) for value in struct.unpack_from(">3f", ram, e2.phys(address))]
        lakitu = e2.US["gLakituState"]
        focus, position = vector(lakitu + e2.LAKITU_FOCUS), vector(lakitu + e2.LAKITU_POS)
        capture = self.package.frame(index)
        try:
            drawn = pick.walk(pick.ram_from_runs(capture.runs, capture.run_bytes), capture.task[12]).projection
        except ValueError:
            drawn = None
        fov = None if drawn is None else pick.fov_of(drawn)
        view = self.display_list(index, (focus, position)) if fov is not None and focus != position else None
        if view is not None and view.ram is not None:
            base, shake = np.frombuffer(view.ram, ">f4", 2, e2.phys(e2.US["sFOVState"] + e2.FOV_VALUE))
            for game in (float(base), float(np.float32(base) + np.float32(shake))):
                if abs(game - fov) < 1e-3:
                    fov = game
                    break
        return {"focus": focus, "position": position,
                "roll": struct.unpack_from(">h", ram, e2.phys(lakitu + e2.LAKITU_ROLL))[0], "fov": fov,
                "mario": vector(e2.US["gMarioStates"] + e2.MARIO_POS)}

    def mario(self, index: int) -> list[float]:
        """Mario's position at frame `index` (pose's "mario", without pose's camera work)."""
        return [float(value) for value in struct.unpack_from(
            ">3f", self.scene(index).ram, self.e2.phys(self.e2.US["gMarioStates"] + self.e2.MARIO_POS))]

    def display_list(self, index: int, camera: tuple) -> View | None:
        """Frame `index` from camera (focus xyz, position xyz[, roll, fov]: e2_georender.run_geo),
        or None when it has no scene. One caller thread at a time: views share one sandbox
        (resolution may run beside them)."""
        resolved = self.state(index)
        if resolved.status:
            return None
        e2, frame, layout = self.e2, resolved.frame, resolved.layout
        main, size = frame.meta["task"]["data_ptr"], frame.meta["task"]["data_size"]
        geo = (layout["geo_start"], resolved.result["run"]["head"])
        moved, run, ram, at, spliced = e2.moved_view(frame, self.view_cpu, camera, (main + size + 15) & ~7,
                                                     layout["low_water"], geo, trace=False)
        return View(frame.task_words, ram, at, len(spliced), run, list(moved.culling))


def resolve_package(package_path: Path, rom_path: Path, map_path: Path, out: Path, progress=None) -> dict:
    """Search every frame of a package (resolve_task, the package's own layer ignored) and write
    the package again with each answer that reproduced the captured list as its scene-resolution
    layer. Returns counts: frames, resolved, with restored ranges, with throwMatrix pointers."""
    started = time.perf_counter()
    evaluator = Evaluator.open(package_path, rom_path, map_path, use_recorded=False)
    answers, counts = [], Counter()
    for index in range(len(evaluator)):
        resolved = evaluator.state(index)
        result = resolved.result
        reproduced = (resolved.status is None and not result["run"]["violations"]
                      and not any(result["differing"].values()))
        answers.append((resolved.restored, resolved.pointers) if reproduced else None)
        counts.update(frames=1, resolved=reproduced, restored=reproduced and bool(resolved.restored),
                      pointers=reproduced and bool(resolved.pointers))
        if progress and index % 200 == 0:
            progress(index, len(evaluator))
    data = scene_package.with_resolution(evaluator.package.data, evaluator.rom, answers)
    partial = out.with_name(out.name + ".partial")
    partial.write_bytes(data)
    partial.replace(out)
    return dict(counts) | {"package_bytes": len(data), "seconds": round(time.perf_counter() - started, 1)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    resolve = commands.add_parser("resolve", help="write a package's scene-resolution layer")
    resolve.add_argument("package", type=Path, help="a scene package with the scene-state layer")
    resolve.add_argument("out", type=Path, help="the package written again with the layer (may be the same path)")
    resolve.add_argument("--rom", type=Path, required=True)
    resolve.add_argument("--map", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        counts = resolve_package(args.package, args.rom, args.map, args.out,
                                 lambda done, total: print(f"{done}/{total}", file=sys.stderr, flush=True))
    except scene_package.PackageError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
