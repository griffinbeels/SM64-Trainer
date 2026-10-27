"""A stand-in for the state resolvers' evaluator (state_resolver.serve --evaluator scene_resolver_double:...).

FakeStates(package bytes, ROM path, map path) reads the package with the real reader, so the
digests a child reports are that package's own. Its state of frame i is ram_of(i) (4 KiB naming
the frame), its entry names the child's process id; frame NO_SCENE has no scene, frame FAILS raises
and frame DIES ends the child. Each state takes DELAY_S (SlowStates: SLOW_S), so the order in which
the children take frames shows. WrongPackage reports digests no package has: a child that read
something other than the session's package.
"""
import hashlib
import os
import sys
import time
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "scene_lab"))
import package as scene_package  # noqa: E402

NO_SCENE, FAILS, DIES = 2, 3, 12
DELAY_S, SLOW_S = 0.02, 0.4


def ram_of(index: int) -> bytes:
    return hashlib.sha256(f"state {index}".encode("ascii")).digest() * 128


class FakeStates:
    delay_s = DELAY_S

    def __init__(self, data: bytes, rom: Path, symbol_map: Path):
        self.package = scene_package.Package(data, Path(rom).read_bytes())
        self.recorded = Counter()

    def __len__(self) -> int:
        return len(self.package)

    def state(self, index: int):
        time.sleep(self.delay_s)
        if index == DIES:
            os._exit(3)
        if index == FAILS:
            raise ValueError("this frame cannot be resolved")
        self.recorded["absent"] += 1
        entry = {"global_timer": index, "pool": f"{os.getpid()}", "data_ptr": "80000000"}
        if index == NO_SCENE:
            return SimpleNamespace(status="no scene", entry=entry)
        return SimpleNamespace(status=None, entry=entry, frame=SimpleNamespace(task_words=tuple(range(16)),
                                                                               ram=ram_of(index)),
                               layout={"geo_start": 0x80300000, "top": 0x80380000, "pre": 0x40, "low_water": 0x80310000},
                               result={"run": {"head": 0x80300100 + index, "violations": []}, "differing": {}},
                               notes={}, restored=[(0x1000, 0x1010)], pointers=[], recorded=False)


class SlowStates(FakeStates):
    delay_s = SLOW_S


class WrongPackage(FakeStates):
    def __init__(self, data: bytes, rom: Path, symbol_map: Path):
        super().__init__(data, rom, symbol_map)
        frames = len(self.package)
        self.package = SimpleNamespace(index=SimpleNamespace(frames=[(0, 0, 0, 0, b"\1" * 32)] * frames),
                                       states=SimpleNamespace(rows=[(0, 0, 0, 0, b"\2" * 32)] * frames))
        self.frames = frames

    def __len__(self) -> int:
        return self.frames
