"""The free-camera evaluator: a recorded scene re-rendered by SM64's own code (M4 E3/E5 lab).

    evaluator = Evaluator.open(package.sm64scene, rom.z64, MappingUS.map)
    state = evaluator.state(frame)              # the scene state that frame's render consumed
    view = evaluator.display_list(frame, (focus, position))

A viewer asks for display lists; the proof drivers (e2_georender.py from-package and
from-tap) ask for the same states and add their comparisons. state() rebuilds the frame's
RDRAM from the package's scene-state layer and the local ROM (SceneImages), then resolves what
the original render consumed (e2_georender.resolve_task: one traced regeneration, more when
render-stepped state or a throwMatrix must be put back). display_list() re-runs
geo_process_root from that state with gLakituState moved, untraced, and splices its output
between the captured prologue and HUD (e2_georender.moved_view). Both run on one pair of
reused sandboxes (e2_georender.Cpu), so translated code and ROM checks carry from frame to
frame. Recent scenes and states stay cached (each holds several 8 MiB images), so a camera
change re-runs only geo_process_root, and the next frame reuses the previous one.

Imported packages stay untrusted: the package reader checks every record, and code runs only
from this ROM (the package's code loads are rebuilt from it and refused on mismatch).
"""
from __future__ import annotations

import struct
import sys
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import package as scene_package  # noqa: E402

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
                 cache: int = 4, source: Path | None = None):
        import e2_georender as e2
        self.e2, self.package, self.rom, self.symbols = e2, package, rom, symbols
        self.source = source or Path("package")
        if package.states is None:
            raise scene_package.PackageError("this package carries no scene state (version 0, or --no-state)")
        self.windows = package.states.code_windows()
        self.images = SceneImages(*e2.code_images(rom, self.windows))
        self.cpu = e2.Cpu(rom, fcr31)
        self.cache = cache
        self._scenes: OrderedDict[int, Scene] = OrderedDict()
        self._states: OrderedDict[int, object] = OrderedDict()

    @classmethod
    def open(cls, package_path: Path, rom_path: Path, map_path: Path, **options) -> Evaluator:
        import e2_georender as e2
        symbols = e2.load_map(map_path)
        rom = rom_path.read_bytes()
        package = scene_package.open_package(package_path, rom_path)
        return cls(package, rom, symbols, source=package_path, **options)

    def __len__(self) -> int:
        return len(self.package)

    @staticmethod
    def _remember(cache: OrderedDict, index: int, value, limit: int):
        cache[index] = value
        cache.move_to_end(index)
        while len(cache) > limit:
            cache.popitem(last=False)
        return value

    def scene(self, index: int) -> Scene:
        """The frame's recorded state, assembled (package.scene_state, then SceneImages)."""
        if index in self._scenes:
            self._scenes.move_to_end(index)
            return self._scenes[index]
        task, regions, ram, known = self.package.scene_state(index)
        return self._remember(self._scenes, index, Scene(task, regions, *self.images.assemble(ram, known)), self.cache)

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
        and throwMatrix pointers put back; status names why a task has none ("no scene", ...)."""
        if index in self._states:
            self._states.move_to_end(index)
            return self._states[index]
        if index:
            self.scene(index - 1)          # seek forward: the previous frame first
        scene = self.scene(index)
        frame = self.e2.TapFrame(self.source, scene.task, scene.ram)
        resolved = self.e2.resolve_task(frame, self.cpu, scene.known, self.symbols, self.previous(index))
        return self._remember(self._states, index, resolved, self.cache)

    def pose(self, index: int) -> dict:
        """Frame `index`'s recorded camera (gLakituState's focus and position, the camera the
        drawing used) and Mario's position, in the game's world coordinates: where a free camera
        starts, and what Focus Mario looks at."""
        e2, ram = self.e2, self.scene(index).ram

        def vector(address: int) -> list[float]:
            return [float(value) for value in struct.unpack_from(">3f", ram, e2.phys(address))]
        lakitu = e2.US["gLakituState"]
        return {"focus": vector(lakitu + e2.LAKITU_FOCUS), "position": vector(lakitu + e2.LAKITU_POS),
                "mario": vector(e2.US["gMarioStates"] + e2.MARIO_POS)}

    def display_list(self, index: int, camera: tuple) -> View | None:
        """Frame `index` from camera (focus xyz, position xyz), or None when it has no scene."""
        resolved = self.state(index)
        if resolved.status:
            return None
        e2, frame, layout = self.e2, resolved.frame, resolved.layout
        main, size = frame.meta["task"]["data_ptr"], frame.meta["task"]["data_size"]
        geo = (layout["geo_start"], resolved.result["run"]["head"])
        moved, run, ram, at, spliced = e2.moved_view(frame, self.cpu, camera, (main + size + 15) & ~7,
                                                     layout["low_water"], geo, trace=False)
        return View(frame.task_words, ram, at, len(spliced), run, list(moved.culling))
