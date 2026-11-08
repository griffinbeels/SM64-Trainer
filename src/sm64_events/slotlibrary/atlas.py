"""Atlases: for one level and area, where the two Usamune builds keep the same main-pool data.

A slot holds pointers into the main pool: the level's geometry, collision, models and the blocks objects
allocate. The pool is outside the slot and lands at other addresses in each build. Both builds lay a level out
the same way on every entry, so two cold boots of the ROMs, warped to the same place and run 10 frames, show the
pairs: the words both games hold in the same spot, in their slot bodies and in their same-size pool blocks, vote
for which address in one build is which in the other (`learn_pairs`, from the 2026-11-08 study: his own BoB
moment, translated with an atlas from cold boots, played on in a cold JP game for 300 frames, Mario and all 240
objects matching his recording).

An atlas is built on demand by tools/scene_lab/slot_atlas.py (both ROMs, about 20 s) and kept on disk beside the
Slot Library; `AtlasStore` is the one door. The file is data this Trainer wrote, but it is still checked as it is
read: a wrong field refuses rather than translating with it.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import threading
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from sm64_events.slotlibrary.store import Damaged, write_atomic
from sm64_events.slotlibrary.usamune import ROM_NAMES, Refusal

log = logging.getLogger("sm64.slotlibrary")

FORMAT = 1
SEGMENTS = 32
BLOCK_HEADER = 16                    # struct MainPoolBlock {prev, next} padded to 16 before each block's data
BLOCK_LIMIT = 4096
SIDES = ("L", "R")


@dataclass(frozen=True)
class World:
    """One build's cold-booted level at the atlas moment."""
    version: str
    pool: tuple[int, int]                          # sPoolStart, sPoolEnd
    blocks: tuple[tuple[str, int, int], ...]       # (side, data start, data size), in allocation order
    segments: tuple[int, ...]                      # sSegmentTable's 32 entries
    usamune: int                                   # Usamune's settings base: its data moves by the difference
    objects: tuple[tuple[int, str, float, float, float], ...]   # active objects: index, behaviour, spawn point

    @property
    def span(self) -> tuple[int, int]:
        """Every address the pool can hold, block headers included."""
        return self.pool[0] - BLOCK_HEADER, self.pool[1] + BLOCK_HEADER


@dataclass(frozen=True)
class Atlas:
    level: int
    area: int
    worlds: dict[str, World]
    pairs: dict[str, dict[int, int]]               # source version -> {its address: the other build's}

    def other(self, version: str) -> str:
        return next(name for name in self.worlds if name != version)


def pool_blocks(word: Callable[[int], int], start: int, end: int, head_l: int, head_r: int) -> tuple:
    """Every main-pool block in allocation order. The left side grows up from start, the right down from end."""
    blocks, block = [], start - BLOCK_HEADER
    while block != head_l:
        following = word(block + 4)
        if len(blocks) >= BLOCK_LIMIT or not block < following <= head_l:
            raise Refusal("the main pool's block list does not hold together")
        blocks.append(("L", block + BLOCK_HEADER, following - block - BLOCK_HEADER))
        block = following
    right, block = [], head_r
    while block != end:
        following = word(block + 4)
        if len(right) >= BLOCK_LIMIT or not block < following <= end:
            raise Refusal("the main pool's block list does not hold together")
        right.append(("R", block + BLOCK_HEADER, following - block - BLOCK_HEADER))
        block = following
    return tuple(blocks + right[::-1])


def learn_pairs(source: np.ndarray, target: np.ndarray, source_world: World, target_world: World,
                source_body: tuple[int, int], target_body: tuple[int, int]) -> dict[int, int]:
    """source/target: each build's RAM from 0x80000000, big-endian bytes. The bodies word for word, then each pair
    of same-side same-size pool blocks word for word; where both words point into their pools, they vote."""
    low, high = source_world.span
    other_low, other_high = target_world.span
    votes: dict[int, Counter] = defaultdict(Counter)

    def words(ram: np.ndarray, address: int, size: int) -> np.ndarray:
        at = address - 0x80000000
        return np.frombuffer(ram[at:at + size // 4 * 4].tobytes(), ">u4")

    def pair(ours: np.ndarray, theirs: np.ndarray) -> None:
        hit = (ours >= low) & (ours < high) & (theirs >= other_low) & (theirs < other_high)
        for a, b in zip(ours[hit].tolist(), theirs[hit].tolist(), strict=True):
            votes[a][b] += 1

    pair(words(source, *source_body), words(target, *target_body))
    paired = zip(source_world.blocks, target_world.blocks, strict=False)   # a longer list's extra blocks pair nothing
    for (side, start, size), (other_side, other_start, other_size) in paired:
        if (side, size) == (other_side, other_size):
            pair(words(source, start, size), words(target, other_start, size))
    return {a: count.most_common(1)[0][0] for a, count in votes.items()}


def _pack(pairs: dict[int, int]) -> str:
    flat = np.array([value for key in sorted(pairs) for value in (key, pairs[key])], ">u4")
    return base64.b64encode(flat.tobytes()).decode("ascii")


def _unpack(text: str) -> dict[int, int]:
    flat = np.frombuffer(base64.b64decode(text, validate=True), ">u4")
    if flat.size % 2:
        raise ValueError("an odd number of words")
    return dict(zip(flat[0::2].tolist(), flat[1::2].tolist(), strict=True))


def to_json(atlas: Atlas) -> bytes:
    worlds = {version: {"pool": list(world.pool), "blocks": [list(block) for block in world.blocks],
                        "segments": list(world.segments), "usamune": world.usamune,
                        "objects": [list(entry) for entry in world.objects]}
              for version, world in atlas.worlds.items()}
    return json.dumps({"format": FORMAT, "level": atlas.level, "area": atlas.area, "roms": ROM_NAMES,
                       "worlds": worlds, "pairs": {v: _pack(p) for v, p in atlas.pairs.items()}}).encode("utf-8")


def _world(version: str, raw: dict) -> World:
    pool, blocks, segments = raw["pool"], raw["blocks"], raw["segments"]
    if len(pool) != 2 or len(segments) != SEGMENTS or not all(
            len(b) == 3 and b[0] in SIDES and type(b[1]) is int and type(b[2]) is int for b in blocks):
        raise ValueError(f"the {version} world is malformed")
    objects = tuple((int(i), str(name), float(x), float(y), float(z)) for i, name, x, y, z in raw["objects"])
    return World(version, (int(pool[0]), int(pool[1])), tuple((s, a, n) for s, a, n in blocks),
                 tuple(int(s) for s in segments), int(raw["usamune"]), objects)


def from_json(blob: bytes) -> Atlas:
    """Raises Damaged for anything but a whole atlas of this format, for the two builds this Trainer knows."""
    try:
        raw = json.loads(blob)
        if raw.get("format") != FORMAT or raw.get("roms") != ROM_NAMES or set(raw["worlds"]) != set(ROM_NAMES):
            raise ValueError(f"not a format {FORMAT} atlas of {', '.join(ROM_NAMES.values())}")
        worlds = {version: _world(version, entry) for version, entry in raw["worlds"].items()}
        pairs = {version: _unpack(raw["pairs"][version]) for version in ROM_NAMES}
        return Atlas(int(raw["level"]), int(raw["area"]), worlds, pairs)
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise Damaged(f"the atlas cannot be read: {error}") from error


Builder = Callable[[int, int, Path], None]       # (level, area, where to write the atlas) -> written, or Refusal


class AtlasStore:
    """Atlases on disk, one file per level and area, built on first use. One build at a time."""

    def __init__(self, root: Path, builder: Builder | None):
        self.root, self.builder = Path(root), builder
        self._lock = threading.Lock()

    def path(self, level: int, area: int) -> Path:
        return self.root / f"level{level:02d}-area{area}.json"

    def has(self, level: int, area: int) -> bool:
        return self.path(level, area).exists()

    def get(self, level: int, area: int) -> Atlas:
        """The atlas for this place, built now when there is none. Refusal when it cannot be had."""
        with self._lock:
            path = self.path(level, area)
            if path.exists():
                try:
                    return self._checked(path.read_bytes(), level, area)
                except Damaged:
                    path.unlink(missing_ok=True)
            if self.builder is None:
                raise Refusal("there is no map between the two game versions for this place yet")
            self.root.mkdir(parents=True, exist_ok=True)
            self.builder(level, area, path)
            try:
                return self._checked(path.read_bytes(), level, area)
            except (OSError, Damaged) as error:
                raise Refusal(f"the map between the two game versions could not be made: {error}") from error

    @staticmethod
    def _checked(blob: bytes, level: int, area: int) -> Atlas:
        atlas = from_json(blob)
        if (atlas.level, atlas.area) != (level, area):
            raise Damaged("the atlas is for another place")
        return atlas

    def put(self, atlas: Atlas) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.path(atlas.level, atlas.area)
        write_atomic(path, to_json(atlas))
        return path


CHILD_SECONDS = 240                  # two cold boots and a warp each: about 20 s on his machine (2026-11-08)


def child_builder(register: Callable = lambda child: None) -> Builder:
    """Builds an atlas in a hidden child (tools/scene_lab/slot_atlas.py), the way the moment builder runs.
    register(child) is told the child, then None, so its owner can end it on shutdown."""
    def build(level: int, area: int, path: Path) -> None:
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        tools = Path(__file__).resolve().parents[3] / "tools" / "scene_lab"
        argv, env = python_child("slot_atlas", "--scene-lab-atlas", ["build", "--level", str(level), "--area",
                                 str(area), "--out", str(path)], site_packages=True)
        env = dict(os.environ) if env is None else env
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(tools), env.get("PYTHONPATH"))))
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 encoding="utf-8", errors="replace", env=env, **quiet_spawn_kwargs())
        register(child)
        try:
            stdout, stderr = child.communicate(timeout=CHILD_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            raise Refusal("making the map between the two game versions took too long") from None
        finally:
            register(None)
        lines = stdout.strip().splitlines()
        answer = json.loads(lines[-1]) if lines and lines[-1].startswith("{") else {}
        if child.returncode != 0 or "atlas" not in answer:
            log.warning("An atlas build ended with %s: %s", child.returncode, stderr[-800:])
            raise Refusal(answer.get("refused") or "the map between the two game versions could not be made")
    return build


def default_store(register: Callable = lambda child: None) -> AtlasStore:
    """The atlases beside the Slot Library, built on demand by the child."""
    from sm64_events.core.paths import slot_library_dir
    return AtlasStore(slot_library_dir() / "atlases", child_builder(register))
