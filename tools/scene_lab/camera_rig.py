"""Cinematic cameras (M4 round 6, item 1): cameras that ride along a runner's path in the scene viewer.

    runner = Runner(positions, yaws, places, seconds)       # the stage's Mario or a scene ghost, per picture
    rig = Rig.of(request["rig"], defaults)                  # ValueError names what is wrong
    shot = Planner().shot(rig, runner, frame, walls_at)     # Shot(camera, side, reason, pulled)

His words (round 6, item 1): "given the path that mario took for the ghost, what if the camera was on
a dolly track such that we're [1] right in front of mario, aimed at him, moving along the track as
mario is moving with close ups / wideshots / medium as options (...) adjust the height of the dolly,
default being at eye level of mario (...) aimed at him), [2] off to the side for a side profile shot,
same tracking idea (need to make sure that if we're off to the side, we choose a side that doesn't
have a wall lol), [3] a rotating microwave effect, where the camera is circling around mario (with
adjustable speed / radius)".

A runner is one figure's place in every picture of the scene (a frame position, as the page counts
them): where its feet are, which way it faces, the level and area it is in, and when the recording
showed the picture (the scene's own clock, seconds after its first picture). None where it is not in
the stage's world (a level load, a menu, a ghost in another area, or before the stage's Mario exists).

A rig is a pure function of (runner, frame, request): the camera rides a SMOOTHED copy of the
runner's path, every picture's place the weighted mean of the pictures around it (Gaussian weights,
`rigSmoothFrames` each side, never across a cut: another area, a warp, an unknown place), so the
dolly glides through Mario's per-frame jitter without lagging him (it looks ahead as far as behind).
The track is indexed by the picture (the scene's time), never by the nearest point of the path: on
a path that loops or doubles back, a nearest point jumps a long way for a small step.
Its aim stays on him: the lightly smoothed path (`rigAimFrames`) at his eye level (`marioHeight`,
the same point Look at Mario turns to). "Front" is the smoothed path's direction of travel; below
`rigStillSpeed` it turns smoothly toward the way he faces, so standing still keeps the camera in
front of his face. Steps, seeks, Play backward and an export of the same request draw the same
camera, because nothing here remembers the last picture.

    front   the camera `distance` ahead of him along that direction
    side    `distance` to his left or right ("auto": the side a wall does not block), `rigSideLead` ahead
    orbit   `radius` from him at angle phase + speed x (the picture's time on the scene's clock), so Play,
            a pause and an export of the same pictures agree

Each stands `height` above the smoothed path (default his eye level), always aimed at him.

Walls are the stage's own collision (StaticWalls: the level's static surfaces the scene state holds,
the ones the game's camera bumps into, less the invisible ones). When a wall lies between his eyes
and the camera, the camera first rises over it, its line turned up about his eyes in 15-degree steps
(at most 80 degrees above level) until it keeps `rigCraneDistance` from him, and then moves in along
that line to `rigWallStandoff` in front of whatever still blocks it. Rising comes first because the
game draws nothing nearer its camera than 100 units: a camera pulled in that close cuts Mario away
(measured 2026-10-27 in HMC's shaft, where Front showed only the shaft's wall). Rise and pull each
ease in and out over `rigPullFrames` (the most in a window, averaged over the window), so a pillar
passing does not make the camera jump. "auto" side tests both sides for every picture of the scene
and keeps a side until it would stay blocked for `rigSideHoldFrames` pictures in a row while the
other is clear, judged `rigPullFrames` ahead so the cut lands before any rise (the decision looks
ahead along the same path, a pure function of it, so a short pillar never flips the shot); a
blocked side that is kept rises and pulls in like front. A side change is a cut.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

KINDS = ("front", "side", "orbit")
SHOTS = ("close", "medium", "wide")
SIDES = ("auto", "left", "right")
SHOT_KEYS = {"close": "rigCloseDistance", "medium": "rigMediumDistance", "wide": "rigWideDistance"}
# The tuning rows a rig reads (ui/scenecameratuning.js, which owns their defaults and ranges).
TUNING_KEYS = ("rigCloseDistance", "rigMediumDistance", "rigWideDistance", "marioHeight", "rigSmoothFrames",
               "rigAimFrames", "rigStillSpeed", "rigFov", "rigSideLead", "rigSideHoldFrames", "rigSideStayFrames", "rigWallStandoff",
               "rigPullFrames", "rigCraneDistance", "rigOrbitRadius", "rigOrbitSpeed")
HEIGHT_RANGE = (0.0, 3000.0)       # units above his feet
RADIUS_RANGE = (50.0, 20000.0)
SPEED_LIMIT = 720.0                # degrees a second either way
WARP_UNITS = 600.0                 # a step this long between two pictures is a warp: the path is cut there
BLOCKED = 0.8                      # a side whose camera a wall pulls in to less than this is blocked
NEAREST = 40.0                     # a pulled-in camera stays this far from his eyes when the wall allows (units)
MIN_KEPT = 2.0                     # and never nearer than this (a camera on its focus point has no direction)
RISE_STEP = math.radians(15.0)     # a wall makes the camera rise in these steps...
RISE_LIMIT = math.radians(80.0)    # ...to at most this far above level (straight down rolls the picture)
MAX_TARGET = 512

NO_RUNNER = "Mario's place is unknown in this picture (a level load or a menu), so it shows the recorded camera."
GHOST_ELSEWHERE = "This ghost is not in this area of the level here, so this shows the recorded camera."
NO_STATE = "This recording carries no scene state, so a cinematic camera has no path to follow."


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


@dataclass(frozen=True)
class Rig:
    """One cinematic camera request (interface D's {"rig": ...}), checked and with every default filled."""
    kind: str
    target: str                # "stage" (the stage's Mario) or a scene ghost's ref
    shot: str
    height: float              # above his feet
    radius: float              # orbit
    speed: float               # orbit, degrees a second
    phase: float               # orbit, degrees at the scene's first picture
    side: str                  # "auto", "left", "right"
    tuning: tuple              # (key, value) for every TUNING_KEYS row

    @classmethod
    def of(cls, request, defaults: dict) -> Rig:
        """`request` as the page sends it; `defaults` {key: (value, low, high)} for every TUNING_KEYS
        row (the registry's). A field left out takes its default; ValueError names a wrong one."""
        if not isinstance(request, dict):
            raise ValueError('a cinematic camera is {"rig": {"kind": "front"|"side"|"orbit", ...}}')
        unknown = set(request) - {"kind", "target", "shot", "height", "radius", "speed", "phase", "side", "tuning"}
        if unknown:
            raise ValueError(f"a cinematic camera has no {sorted(unknown)[0]!r}")
        kind = request.get("kind")
        if kind not in KINDS:
            raise ValueError('a cinematic camera\'s kind is "front", "side" or "orbit"')
        target = request.get("target", "stage")
        if not isinstance(target, str) or not target or len(target) > MAX_TARGET:
            raise ValueError('a cinematic camera follows "stage" (the stage\'s Mario) or a ghost in the scene')
        shot, side = request.get("shot", "medium"), request.get("side", "auto")
        if shot not in SHOTS:
            raise ValueError('a shot is "close", "medium" or "wide"')
        if side not in SIDES:
            raise ValueError('a side is "auto", "left" or "right"')
        tuning = request.get("tuning", {})
        if not isinstance(tuning, dict):
            raise ValueError("a cinematic camera's tuning is {name: number}")
        values = {}
        for key in TUNING_KEYS:
            value, low, high = defaults[key]
            given = tuning.get(key, value)
            if not (_number(given) and low <= given <= high):
                raise ValueError(f"{key} must be a number from {low:g} to {high:g}")
            values[key] = float(given)
        height = request.get("height", values["marioHeight"])
        radius = request.get("radius", values["rigOrbitRadius"])
        speed = request.get("speed", values["rigOrbitSpeed"])
        phase = request.get("phase", 0.0)
        if not (_number(height) and HEIGHT_RANGE[0] <= height <= HEIGHT_RANGE[1]):
            raise ValueError(f"a cinematic camera's height is {HEIGHT_RANGE[0]:g} to {HEIGHT_RANGE[1]:g} units above his feet")
        if not (_number(radius) and RADIUS_RANGE[0] <= radius <= RADIUS_RANGE[1]):
            raise ValueError(f"an orbit's radius is {RADIUS_RANGE[0]:g} to {RADIUS_RANGE[1]:g} units")
        if not (_number(speed) and abs(speed) <= SPEED_LIMIT):
            raise ValueError(f"an orbit's speed is -{SPEED_LIMIT:g} to {SPEED_LIMIT:g} degrees a second")
        if not _number(phase):
            raise ValueError("an orbit's phase is a number of degrees")
        return cls(kind, target, shot, float(height), float(radius), float(speed), float(phase) % 360.0, side,
                   tuple((key, values[key]) for key in TUNING_KEYS))

    def value(self, key: str) -> float:
        return dict(self.tuning)[key]

    @property
    def distance(self) -> float:
        return self.radius if self.kind == "orbit" else self.value(SHOT_KEYS[self.shot])

    def track_key(self) -> tuple:
        """What the smoothed track depends on (Track.of): the same for every rig sharing it."""
        return tuple(self.value(key) for key in ("rigSmoothFrames", "rigAimFrames", "rigStillSpeed", "marioHeight"))


@dataclass
class Shot:
    """A rig's camera for one picture ({"focus", "position", "roll", "fov"}, or None and the reason
    the picture shows the recorded camera) and, for a side shot, the side it took."""
    camera: dict | None
    side: str | None = None
    reason: str | None = None
    pulled: float = 1.0        # the share of its distance the camera kept (below 1: a wall pulled it in)
    raised: float = 0.0        # degrees a wall made it rise above its line


class Runner:
    """One figure's place in each picture of a scene (frame positions 0..n-1): `positions` (n, 3) of
    its feet, NaN where it is not in the stage's world; `yaws` (n,) the game's s16 facing, NaN where
    unknown; `places` [(level, area) or None]; `seconds` (n,) when the recording showed each picture."""

    def __init__(self, positions, yaws, places, seconds, reason: str = NO_RUNNER):
        self.positions = np.asarray(positions, np.float64).reshape(-1, 3)
        self.yaws = np.asarray(yaws, np.float64).reshape(-1)
        self.places = list(places)
        self.seconds = np.asarray(seconds, np.float64).reshape(-1)
        self.reason = reason
        if not (len(self.positions) == len(self.yaws) == len(self.places) == len(self.seconds)):
            raise ValueError("a runner has one position, facing, place and time per picture")
        self.cuts = self._cuts()

    def __len__(self) -> int:
        return len(self.positions)

    def _cuts(self) -> np.ndarray:
        """A run number per picture: pictures in one run are a continuous stretch of the path (known,
        one place, no warp between neighbours); -1 where the place is unknown."""
        known = np.isfinite(self.positions).all(axis=1) & np.array([place is not None for place in self.places], bool)
        runs = np.full(len(self), -1, np.int64)
        run, last = -1, None
        for k in range(len(self)):
            if not known[k]:
                last = None
                continue
            if last is None or self.places[k] != self.places[last] \
                    or np.linalg.norm(self.positions[k] - self.positions[last]) > WARP_UNITS:
                run += 1
            runs[k], last = run, k
        return runs


def _weights(half: int) -> np.ndarray:
    """Gaussian weights for offsets -half..half (sigma half / 2); [1] for 0."""
    if half <= 0:
        return np.ones(1)
    offsets = np.arange(-half, half + 1, dtype=np.float64)
    return np.exp(-0.5 * (offsets / (half / 2.0)) ** 2)


def smoothed(values: np.ndarray, runs: np.ndarray, half: int) -> np.ndarray:
    """Each row of `values` (n, d) as the weighted mean of the rows within `half` pictures in its own
    stretch of `runs` (consecutive equal run numbers; -1: NaN). Near a stretch's end the window
    narrows to as many pictures on each side as the end leaves, so it stays centred: a straight run
    is its own smoothed path right up to a cut, never pulled toward its middle."""
    n = len(values)
    index = np.arange(n)
    starts = np.flatnonzero(np.concatenate(([True], runs[1:] != runs[:-1])))
    lengths = np.diff(np.concatenate((starts, [n])))
    start = np.repeat(starts, lengths)
    end = np.repeat(starts + lengths - 1, lengths)
    reach = np.minimum(np.minimum(index - start, end - index), half)
    total = np.zeros_like(values, dtype=np.float64)
    weight = np.zeros(n)
    for offset, w in zip(range(-half, half + 1), _weights(half), strict=True):
        lo, hi = max(0, -offset), min(n, n - offset)
        if lo >= hi:
            continue
        same = (runs[lo:hi] >= 0) & (reach[lo:hi] >= abs(offset))
        total[lo:hi][same] += w * values[lo + offset:hi + offset][same]
        weight[lo:hi][same] += w
    out = np.full_like(values, np.nan, dtype=np.float64)
    good = weight > 0
    out[good] = total[good] / weight[good][:, None]
    return out


class Track:
    """A runner's smoothed path for one smoothing setting: `feet` (n, 3), `aim` (n, 3) (his eye level
    on the lightly smoothed path) and `ahead` (n, 3) the unit direction that is his front, level."""

    def __init__(self, runner: Runner, smooth: int, aim: int, still_speed: float, eye: float):
        runs = runner.cuts
        self.runner = runner
        self.feet = smoothed(runner.positions, runs, smooth)
        self.aim = smoothed(runner.positions, runs, aim) + np.array([0.0, eye, 0.0])
        n = len(runner)
        # Travel: the smoothed path's own direction over a few pictures each side, clipped to its run.
        step = max(1, smooth // 2)
        index = np.arange(n)
        before, after = index.copy(), index.copy()
        for k in range(n):
            if runs[k] < 0:
                continue
            lo, hi = k, k
            while lo > max(0, k - step) and runs[lo - 1] == runs[k]:
                lo -= 1
            while hi < min(n - 1, k + step) and runs[hi + 1] == runs[k]:
                hi += 1
            before[k], after[k] = lo, hi
        moved = self.feet[after] - self.feet[before]
        moved[:, 1] = 0.0
        elapsed = runner.seconds[after] - runner.seconds[before]
        elapsed = np.where(elapsed > 0, elapsed, (after - before) / 30.0)
        length = np.linalg.norm(moved, axis=1)
        with np.errstate(invalid="ignore", divide="ignore"):
            speed = np.where(after > before, length / np.where(elapsed > 0, elapsed, 1.0), 0.0)
            travel = moved / np.where(length > 1e-6, length, 1.0)[:, None]
        # Facing: the game's yaw as a level unit vector (sin, 0, cos), smoothed the same way.
        yaw = runner.yaws * (2 * math.pi / 65536.0)
        facing_raw = np.stack([np.sin(yaw), np.zeros(n), np.cos(yaw)], axis=1)
        facing_runs = np.where(np.isfinite(yaw), runs, -1)
        facing = smoothed(np.nan_to_num(facing_raw), facing_runs, smooth)
        # Below half the still speed his facing decides, above it his travel; between, a smooth blend.
        blend = np.clip((speed - 0.5 * still_speed) / max(0.5 * still_speed, 1e-6), 0.0, 1.0)
        blend = blend * blend * (3 - 2 * blend)
        blend = np.where(length > 1e-6, blend, 0.0)
        mixed = blend[:, None] * travel + (1 - blend[:, None]) * np.nan_to_num(facing)
        # The direction is smoothed once more over the same window: the camera stands `distance`
        # along it, so a wobble of a degree swings a wide shot by tens of units.
        mixed = np.nan_to_num(smoothed(mixed, runs, smooth))
        norm = np.linalg.norm(mixed, axis=1)
        fallback = np.where(np.linalg.norm(np.nan_to_num(facing), axis=1) > 1e-6, 0, 1)
        self.ahead = np.where((norm > 1e-6)[:, None], mixed / np.where(norm > 1e-6, norm, 1.0)[:, None],
                              np.array([0.0, 0.0, 1.0]) * fallback[:, None])
        self.speed = speed


def side_vectors(ahead: np.ndarray) -> dict:
    """The level unit vectors to his left and right of `ahead` (the game's y is up; facing +z, +x is
    his left)."""
    left = np.stack([ahead[:, 2], np.zeros(len(ahead)), -ahead[:, 0]], axis=1)
    return {"left": left, "right": -left}


def raw_positions(rig: Rig, track: Track, frames: np.ndarray, side: np.ndarray | None = None) -> np.ndarray:
    """Where the rig's camera stands at each of `frames` before any wall pulls it in. `side` (one
    "left"/"right" per frame) for a side shot."""
    feet, ahead = track.feet[frames], track.ahead[frames]
    up = np.array([0.0, rig.height, 0.0])
    if rig.kind == "front":
        return feet + ahead * rig.distance + up
    if rig.kind == "side":
        vectors = side_vectors(ahead)
        sideways = np.where((np.asarray(side) == "left")[:, None], vectors["left"], vectors["right"])
        return feet + sideways * rig.distance + ahead * rig.value("rigSideLead") + up
    angle = np.radians(rig.phase + rig.speed * track.runner.seconds[frames])
    return feet + np.stack([np.sin(angle), np.zeros(len(frames)), np.cos(angle)], axis=1) * rig.radius + up


class StaticWalls:
    """The level's static collision triangles (n, 3, 3) in world units, which a camera's line of sight
    is tested against. clearance() casts many segments at once."""

    CHUNK = 512

    def __init__(self, triangles):
        self.triangles = np.asarray(triangles, np.float64).reshape(-1, 3, 3)
        self.low = self.triangles.min(axis=1)
        self.high = self.triangles.max(axis=1)
        self.edge1 = self.triangles[:, 1] - self.triangles[:, 0]
        self.edge2 = self.triangles[:, 2] - self.triangles[:, 0]

    def __len__(self) -> int:
        return len(self.triangles)

    def clearance(self, origins, ends) -> np.ndarray:
        """For each segment origin -> end, the share of it (0..1] before the first triangle it meets,
        1 when it meets none. Both faces block."""
        origins = np.asarray(origins, np.float64).reshape(-1, 3)
        ends = np.asarray(ends, np.float64).reshape(-1, 3)
        out = np.ones(len(origins))
        if not len(self.triangles):
            return out
        for start in range(0, len(origins), self.CHUNK):
            o, e = origins[start:start + self.CHUNK], ends[start:start + self.CHUNK]
            lo, hi = np.minimum(o, e), np.maximum(o, e)
            near = ((self.low[None, :, :] <= hi[:, None, :]) & (self.high[None, :, :] >= lo[:, None, :])).all(axis=2)
            rays, tris = np.nonzero(near)
            if not len(rays):
                continue
            direction = e[rays] - o[rays]
            e1, e2 = self.edge1[tris], self.edge2[tris]
            p = np.cross(direction, e2)
            det = np.einsum("ij,ij->i", e1, p)
            usable = np.abs(det) > 1e-9
            inverse = np.where(usable, 1.0 / np.where(usable, det, 1.0), 0.0)
            offset = o[rays] - self.triangles[tris, 0]
            u = np.einsum("ij,ij->i", offset, p) * inverse
            q = np.cross(offset, e1)
            v = np.einsum("ij,ij->i", direction, q) * inverse
            t = np.einsum("ij,ij->i", e2, q) * inverse
            hit = usable & (u >= 0) & (v >= 0) & (u + v <= 1) & (t > 1e-6) & (t <= 1)
            chunk = out[start:start + self.CHUNK]
            np.minimum.at(chunk, rays[hit], t[hit])
        return out


# The game's surface types a camera sees through: nothing is drawn there (decomp surface_terrains.h).
INVISIBLE_SURFACES = {0x000A,      # SURFACE_DEATH_PLANE, the plane under the level
                      0x0012,      # SURFACE_INTANGIBLE, room separators
                      0x0072}      # SURFACE_CAMERA_BOUNDARY, walls only the game's camera bumps into
NO_CAM_COLLISION = 0x02            # SURFACE_FLAG_NO_CAM_COLLISION: the game's own camera passes it
SURFACE_BYTES = 0x30               # struct Surface (US)
SURFACE = np.dtype([("type", ">i2"), ("force", ">i2"), ("flags", "i1"), ("room", "i1"), ("lower", ">i2"),
                    ("upper", ">i2"), ("vertices", ">i2", (3, 3)), ("normal", ">f4", (3,)), ("origin", ">f4"),
                    ("object", ">u4")])
MAX_SURFACES = 30000
WALL_SYMBOLS = ("sSurfacePool", "gNumStaticSurfaces")
_MAP_SYMBOL = re.compile(r"^\s+0x00000000([0-9a-f]{8})\s+(\w+)\s*$")


def map_symbols(map_path: Path | None, names) -> dict[str, int]:
    """The addresses of `names` in the lab's symbol map (MappingUS.map); the ones it lacks are missing,
    and so is everything without a map."""
    wanted, found = set(names), {}
    try:
        for line in Path(map_path).read_text(encoding="utf-8", errors="replace").splitlines():
            if (match := _MAP_SYMBOL.match(line)) and match[2] in wanted:
                found.setdefault(match[2], int(match[1], 16))
    except (OSError, TypeError):
        return {}
    return found


def static_walls(read, pool_address: int, count_address: int) -> StaticWalls | None:
    """The level's static surfaces from one picture's scene state: `read(offset, size)` gives RDRAM
    bytes in Project64's word order (package.scene_bytes); sSurfacePool's pointer and
    gNumStaticSurfaces at those addresses (the symbol map's). The static surfaces are the pool's
    first gNumStaticSurfaces (surface_load.c loads the level's before any object's). None when the
    state does not hold a usable pool."""
    pointer = int.from_bytes(read(pool_address & 0x1FFFFFFF, 4), "little")
    count = int.from_bytes(read(count_address & 0x1FFFFFFF, 4), "little", signed=True)
    start = pointer & 0x1FFFFFFF
    if not (0 < count <= MAX_SURFACES and 0x80000000 <= pointer < 0x80800000 and not start & 3):
        return None
    data = read(start, count * SURFACE_BYTES)
    big = np.frombuffer(data, "<u4").astype(">u4").tobytes()            # the CPU's own byte order
    surfaces = np.frombuffer(big, SURFACE, count)
    seen = ((surfaces["flags"] & NO_CAM_COLLISION) == 0) & ~np.isin(surfaces["type"], list(INVISIBLE_SURFACES))
    return StaticWalls(surfaces["vertices"][seen].astype(np.float64))


class Planner:
    """The rig's work kept between pictures: each runner's smoothed track, an auto side's choice for
    the whole path, and each picture's rise and pull, for the one rig most recently asked (a change
    of rig or runner starts over). Everything it keeps is a function of (runner, rig), so what it
    returns never depends on which pictures were asked before."""

    def __init__(self):
        self.tracks: list[tuple[Runner, tuple, Track]] = []   # the newest few (runner, track_key, Track)
        self.runner: Runner | None = None                     # whose sides, rises and pulls are kept
        self.rig: Rig | None = None
        self.sides: np.ndarray | None = None
        self.rises: dict[int, float] = {}                     # frame -> its own rise (radians)
        self.pulls: dict[int, float] = {}                     # frame -> its own pull along its eased line (units)

    def track(self, rig: Rig, runner: Runner) -> Track:
        key = rig.track_key()
        for seen, seen_key, found in self.tracks:
            if seen is runner and seen_key == key:
                return found
        found = Track(runner, int(rig.value("rigSmoothFrames")), int(rig.value("rigAimFrames")),
                      rig.value("rigStillSpeed"), rig.value("marioHeight"))
        self.tracks = [(runner, key, found), *self.tracks[:3]]
        return found

    def shot(self, rig: Rig, runner: Runner, frame: int, walls_at) -> Shot:
        if not 0 <= frame < len(runner):
            raise ValueError("no such frame")
        track = self.track(rig, runner)
        if runner.cuts[frame] < 0 or not np.isfinite(track.feet[frame]).all():
            return Shot(None, reason=runner.reason)
        if self.runner is not runner or self.rig != rig:
            self.runner, self.rig, self.sides, self.rises, self.pulls = runner, rig, None, {}, {}
        if rig.kind == "side" and self.sides is None:
            self.sides = choose_sides(rig, track, walls_at)
        aim = track.aim[frame]
        line = self._lines(rig, track, np.array([frame]), walls_at)[0]
        length = float(np.linalg.norm(line))
        kept = max(MIN_KEPT, length - self._eased(self.pulls, self._own_pulls, rig, track, frame, walls_at))
        share = min(1.0, kept / length) if length > 0 else 1.0
        position = aim + line * share
        return Shot({"focus": [float(v) for v in aim], "position": [float(v) for v in position], "roll": 0,
                     "fov": rig.value("rigFov")}, None if self.sides is None else str(self.sides[frame]), None,
                    share, math.degrees(self._eased(self.rises, self._own_rises, rig, track, frame, walls_at)))

    def _block(self, track: Track, frame: int, reach: int) -> tuple[int, int]:
        """The pictures within `reach` of `frame` in one stretch with it: its run, and a side shot's side."""
        runs, sides = track.runner.cuts, self.sides

        def same(j: int) -> bool:
            return runs[j] == runs[frame] and (sides is None or sides[j] == sides[frame])
        lo = frame
        while lo > max(0, frame - reach) and same(lo - 1):
            lo -= 1
        hi = frame
        while hi < min(len(runs) - 1, frame + reach) and same(hi + 1):
            hi += 1
        return lo, hi

    def _eased(self, kept: dict, own, rig: Rig, track: Track, frame: int, walls_at) -> float:
        """A per-picture amount (a rise, a pull) eased over rigPullFrames: the most any picture within
        that many is moved, averaged over the same window, each picture's own window within its
        stretch. It begins before a wall reaches the camera and ends after, and every window holds
        `frame` itself, so a picture is moved at least as far as its own wall asks."""
        half = int(rig.value("rigPullFrames"))
        lo, hi = self._block(track, frame, 2 * half)
        needed = [k for k in range(lo, hi + 1) if k not in kept]
        if needed:
            kept.update(zip(needed, own(rig, track, np.array(needed), walls_at), strict=True))

        def most(k: int) -> float:
            return max(kept[j] for j in range(max(lo, k - half), min(hi, k + half) + 1))
        window = range(max(lo, frame - half), min(hi, frame + half) + 1)
        return float(sum(most(k) for k in window) / len(window))

    def _raw_lines(self, rig: Rig, track: Track, frames: np.ndarray) -> np.ndarray:
        """Each picture's aim -> camera line before any wall moved it."""
        side = None if self.sides is None else [str(value) for value in self.sides[frames]]
        return raw_positions(rig, track, frames, side) - track.aim[frames]

    def _lines(self, rig: Rig, track: Track, frames: np.ndarray, walls_at) -> np.ndarray:
        """Each picture's line raised by its eased rise (same length and bearing)."""
        rises = np.array([self._eased(self.rises, self._own_rises, rig, track, int(k), walls_at) for k in frames])
        return raised(self._raw_lines(rig, track, frames), rises)

    def _own_rises(self, rig: Rig, track: Track, frames: np.ndarray, walls_at) -> list[float]:
        """Each picture's own rise: none while its line leaves the camera rigCraneDistance or more from
        his eyes; else the least of RISE_STEP, 2 x RISE_STEP, ... (up to RISE_LIMIT above level) whose
        line does, or the one that leaves the most."""
        lines = self._raw_lines(rig, track, frames)
        steps = np.arange(0.0, RISE_LIMIT + 1e-9, RISE_STEP)
        base = elevation(lines)
        options = np.clip(base[:, None] + steps[None, :], None, np.maximum(base, RISE_LIMIT)[:, None])
        rises = options - base[:, None]                                       # (frames, steps)
        candidates = raised(np.repeat(lines, len(steps), axis=0), rises.reshape(-1))
        aims = np.repeat(track.aim[frames], len(steps), axis=0)
        room = _kept(aims, aims + candidates, track.runner, np.repeat(frames, len(steps)), walls_at,
                     rig.value("rigWallStandoff")).reshape(len(frames), len(steps))
        whole = np.linalg.norm(lines, axis=1)
        enough = room >= np.minimum(whole, rig.value("rigCraneDistance"))[:, None] - 1e-6
        best = np.where(enough.any(axis=1), enough.argmax(axis=1), room.argmax(axis=1))
        return [float(rises[row, best[row]]) for row in range(len(frames))]

    def _own_pulls(self, rig: Rig, track: Track, frames: np.ndarray, walls_at) -> list[float]:
        """Each picture's own pull toward his eyes along its raised line, in units (_kept)."""
        lines = self._lines(rig, track, frames, walls_at)
        aims = track.aim[frames]
        room = _kept(aims, aims + lines, track.runner, frames, walls_at, rig.value("rigWallStandoff"))
        return [float(value) for value in np.linalg.norm(lines, axis=1) - room]


def elevation(lines: np.ndarray) -> np.ndarray:
    """Each line's angle above level, radians."""
    return np.arctan2(lines[:, 1], np.hypot(lines[:, 0], lines[:, 2]))


def raised(lines: np.ndarray, rises) -> np.ndarray:
    """`lines` turned up by `rises` (radians) about his eyes, keeping their length and bearing, never
    past RISE_LIMIT above level unless a line was already steeper."""
    rises = np.asarray(rises, np.float64).reshape(-1)
    length = np.linalg.norm(lines, axis=1)
    base = elevation(lines)
    angle = np.minimum(base + rises, np.maximum(base, RISE_LIMIT))
    bearing = np.arctan2(lines[:, 0], lines[:, 2])
    out = np.stack([np.cos(angle) * np.sin(bearing), np.sin(angle), np.cos(angle) * np.cos(bearing)], axis=1)
    return np.where((rises > 0)[:, None], out * length[:, None], lines)


def _kept(aims, cameras, runner: Runner, frames, walls_at, standoff: float) -> np.ndarray:
    """How far along each aim -> camera line its camera may stand, in units: `standoff` in front of
    the first wall the line meets, or, where that leaves less than NEAREST, NEAREST or halfway to the
    wall, whichever is nearer (never past it); the whole line where it meets none or the place has no
    walls known."""
    length = np.linalg.norm(cameras - aims, axis=1)
    kept = length.copy()
    places = [runner.places[k] for k in frames]
    for place in set(places):
        walls = None if place is None else walls_at(place)
        if walls is None:
            continue
        rows = np.array([at for at, seen in enumerate(places) if seen == place])
        clear = walls.clearance(aims[rows], cameras[rows])
        wall = clear * length[rows]
        kept[rows] = np.where(clear < 1.0, np.maximum(wall - standoff, np.minimum(NEAREST, wall / 2)), length[rows])
    return kept


def choose_sides(rig: Rig, track: Track, walls_at) -> np.ndarray:
    """The side a side shot takes at every picture ("left"/"right"; "" where the runner is unknown).
    A fixed side is that side throughout. "auto" starts each run on the side clear for more of its
    first rigSideHoldFrames pictures (his right on a tie), and changes side only where the side it
    is on is blocked for the next rigSideHoldFrames pictures in a row and the other side is clear for
    more of them: a short pillar is ridden through (pulled in), a long wall moves the shot across.
    After a change it stays at least rigSideStayFrames pictures, so two walls close together never
    make it flicker from side to side."""
    runner, n = track.runner, len(track.runner)
    out = np.full(n, "", dtype=object)
    known = np.flatnonzero(runner.cuts >= 0)
    if rig.side != "auto":
        out[known] = rig.side
        return out
    if not len(known):
        return out
    hold = max(1, int(rig.value("rigSideHoldFrames")))
    lead = int(rig.value("rigPullFrames"))
    standoff = rig.value("rigWallStandoff")
    blocked = {}
    for name in ("left", "right"):
        cameras = raw_positions(rig, track, known, [name] * len(known))
        kept = _kept(track.aim[known], cameras, runner, known, walls_at, standoff)
        flags = np.zeros(n, bool)
        flags[known] = kept < BLOCKED * np.linalg.norm(cameras - track.aim[known], axis=1)
        blocked[name] = flags
    other = {"left": "right", "right": "left"}
    runs = runner.cuts
    stay = int(rig.value("rigSideStayFrames"))
    current, run, since = None, None, 0
    for k in known.tolist():
        # A wall is judged rigPullFrames ahead: the cut lands as the pull would have begun, so the
        # camera never visibly moves in just before it cuts across.
        soon = max(j for j in range(k, min(n, k + lead + 1)) if runs[j] == runs[k])
        ahead = [j for j in range(soon, min(n, soon + hold)) if runs[j] == runs[k]]
        if runs[k] != run:
            run, since = runs[k], k - stay                # a run's first side is no cut: it may change at once
            clear_left = sum(not blocked["left"][j] for j in ahead)
            clear_right = sum(not blocked["right"][j] for j in ahead)
            current = "left" if clear_left > clear_right else "right"
        elif blocked[current][soon] and k - since >= stay:
            stuck = all(blocked[current][j] for j in ahead) and len(ahead) == hold
            if stuck and sum(not blocked[other[current]][j] for j in ahead) > sum(not blocked[current][j] for j in ahead):
                current, since = other[current], k
        out[k] = current
    return out
