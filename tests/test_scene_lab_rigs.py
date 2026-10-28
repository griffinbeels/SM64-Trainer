"""The scene viewer's cinematic cameras (round 6, item 1): Front, Side and Orbit following a runner.

His words: "given the path that mario took for the ghost, what if the camera was on a dolly track
such that we're [1] right in front of mario, aimed at him, moving along the track as mario is moving
with close ups / wideshots / medium as options (...) [2] off to the side for a side profile shot, same
tracking idea (need to make sure that if we're off to the side, we choose a side that doesn't have a
wall lol), [3] a rotating microwave effect, where the camera is circling around mario (with
adjustable speed / radius)".

The rig math runs on synthetic runners and walls (camera_rig.py); the session and the lab's routes
run over fake_viewer_host.py's host and evaluator, on a synthetic recording whose scene state holds
Mario's place and a surface pool. Whether the pictures look good is the GPU check's (the round's
report).
"""
import json
import math
import re
import struct
import sys
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import camera_rig  # noqa: E402
import fake_viewer_host  # noqa: E402
import rig_path  # noqa: E402
import gpu_gate  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
import stage_ghosts  # noqa: E402
from ghost_native_fixture import frame, producer  # noqa: E402
from sm64_events.ghost.clip import STAR_CLOCK, encode_clip  # noqa: E402
from sm64_events.ghost.native_layout import POSE_AT  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402
from sm64_events.server.tuning_api import shipped_numbers  # noqa: E402
from source_scan import strip_comments  # noqa: E402
from test_scene_lab_stage_ghosts import Loader, _picture_header  # noqa: E402

# The reference tuning these laws are checked against (never the shipped values: SAVE rewrites them).
SHIPPED = shipped_numbers("scenecamera", camera_rig.TUNING_KEYS)
REFERENCE = {"rigCloseDistance": 200.0, "rigMediumDistance": 500.0, "rigWideDistance": 1000.0, "marioHeight": 100.0,
             "rigSmoothFrames": 6.0, "rigAimFrames": 0.0, "rigStillSpeed": 200.0, "rigFov": 50.0, "rigSideLead": 0.0,
             "rigSideHoldFrames": 10.0, "rigSideStayFrames": 0.0, "rigWallStandoff": 50.0, "rigPullFrames": 3.0, "rigCraneDistance": 0.0,
             "rigOrbitRadius": 600.0, "rigOrbitSpeed": 45.0, "rigLensKeepsSize": 1.0, "rigOverheadHeight": 900.0,
             "rigOverheadAngle": 60.0}


def rig(**fields) -> camera_rig.Rig:
    return camera_rig.Rig.of({"tuning": {**REFERENCE, **fields.pop("tuning", {})}, **fields}, SHIPPED)


def runner(positions, yaws=None, seconds=None, places=None) -> camera_rig.Runner:
    n = len(positions)
    return camera_rig.Runner(positions, [0] * n if yaws is None else yaws, places or [(24, 1)] * n,
                             [k / 30 for k in range(n)] if seconds is None else seconds)


def run_along_z(n=120, step=30.0, wobble=0.0):
    """A runner going +z `step` units a frame, his x wobbling ±`wobble` frame to frame."""
    return runner([[wobble * (-1) ** k, 0.0, step * k] for k in range(n)], [0] * n)


def wall_x(x, z_from, z_to, low=-2000.0, high=2000.0) -> np.ndarray:
    """Two triangles making a wall in the plane x = `x` from z_from to z_to."""
    a, b, c, d = [x, low, z_from], [x, low, z_to], [x, high, z_to], [x, high, z_from]
    return np.array([[a, b, c], [a, c, d]], np.float64)


def shots(r, which, frames, walls=None, planner=None):
    planner = planner or camera_rig.Planner()
    return {k: planner.shot(which, r, k, lambda place: walls) for k in frames}


# ------------------------------------------------------------------ the request

def test_a_request_takes_its_defaults_from_the_registry_and_names_a_wrong_field():
    plain = camera_rig.Rig.of({"kind": "front"}, SHIPPED)
    assert (plain.target, plain.shot, plain.side, plain.phase) == ("stage", "medium", "auto", 0.0)
    assert plain.height == SHIPPED["marioHeight"][0] and plain.radius == SHIPPED["rigOrbitRadius"][0]
    assert dict(plain.tuning) == {key: value for key, (value, _low, _high) in SHIPPED.items()}
    # Round 7: level, at the registry's lens, unless the request says; Overhead starts at its own height.
    assert (plain.roll, plain.fov, plain.position) == (0, SHIPPED["rigFov"][0], None)
    assert camera_rig.Rig.of({"kind": "overhead"}, SHIPPED).height == SHIPPED["rigOverheadHeight"][0]
    tripod = camera_rig.Rig.of({"kind": "tripod", "position": [1, 2, 3], "roll": -2731, "fov": 14.8}, SHIPPED)
    assert (tripod.position, tripod.roll, tripod.fov) == ((1.0, 2.0, 3.0), -2731, 14.8)
    for wrong, word in (({"kind": "dolly"}, "kind"), ({"kind": "front", "shot": "huge"}, "shot"),
                        ({"kind": "side", "side": "up"}, "side"), ({"kind": "orbit", "speed": 1e4}, "speed"),
                        ({"kind": "orbit", "radius": 1}, "radius"), ({"kind": "front", "height": -5}, "height"),
                        ({"kind": "front", "target": ""}, "follows"), ({"kind": "front", "zoom": 2}, "zoom"),
                        ({"kind": "front", "tuning": {"rigFov": 500}}, "rigFov"), ("front", "kind"),
                        ({"kind": "front", "roll": 1.5}, "roll"), ({"kind": "front", "roll": 40000}, "roll"),
                        ({"kind": "front", "roll": True}, "roll"), ({"kind": "front", "fov": 0.5}, "fov"),
                        ({"kind": "front", "fov": 171}, "fov"), ({"kind": "tripod"}, "position"),
                        ({"kind": "tripod", "position": [0, 0]}, "position"),
                        ({"kind": "tripod", "position": [0, 0, 2e6]}, "position"),
                        ({"kind": "front", "position": [0, 0, 0]}, "position")):
        with pytest.raises(ValueError, match=word):
            camera_rig.Rig.of(wrong, SHIPPED)


# ------------------------------------------------------------------ Front: a smoothed dolly, aimed at him

def test_front_rides_ahead_of_him_on_a_smoothed_path_aimed_at_his_eyes():
    """The camera stands the shot's distance along his travel, at the height asked, aimed at his eye
    level; his frame-to-frame wobble moves the aim, never the dolly."""
    r = run_along_z(wobble=8.0)
    front = rig(kind="front", shot="medium", height=100.0, tuning={"rigAimFrames": 0})
    got = shots(r, front, range(20, 100))
    for k, shot in got.items():
        position, focus = np.array(shot.camera["position"]), np.array(shot.camera["focus"])
        assert np.allclose(focus, r.positions[k] + [0, 100, 0])                   # aimed at him, unsmoothed
        assert position[2] - 30.0 * k == pytest.approx(500.0, abs=1.0)            # medium, ahead along +z
        assert position[1] == pytest.approx(100.0)
        assert abs(position[0]) < 0.6                                               # the wobble is gone
        assert shot.camera["roll"] == 0 and shot.camera["fov"] == 50.0
    # Each shot size is its own distance.
    for size, distance in (("close", 200.0), ("wide", 1000.0)):
        camera = shots(r, rig(kind="front", shot=size), [60])[60].camera
        assert camera["position"][2] - 30.0 * 60 == pytest.approx(distance, abs=1.0)


def test_standing_still_front_is_where_he_faces():
    yaw_x = 0x4000                                              # the game's quarter turn: facing +x
    r = runner([[0.0, 0.0, 0.0]] * 40, [yaw_x] * 40)
    camera = shots(r, rig(kind="front", shot="close"), [20])[20].camera
    assert camera["position"][0] == pytest.approx(200.0, abs=1e-6) and abs(camera["position"][2]) < 1e-6


def test_the_path_is_never_smoothed_across_a_warp_or_another_area():
    """A pipe moves him 5000 units in a frame: the pictures after it follow the new place at once."""
    path = [[0.0, 0.0, 30.0 * k] for k in range(30)] + [[5000.0, 0.0, 30.0 * k] for k in range(30, 60)]
    r = runner(path)
    assert list(r.cuts[[0, 29, 30, 59]]) == [0, 0, 1, 1]
    after = shots(r, rig(kind="front"), [30])[30].camera
    assert after["focus"][0] == pytest.approx(5000.0) and after["position"][0] == pytest.approx(5000.0, abs=1.0)
    elsewhere = runner(path[:30], places=[(24, 1)] * 15 + [(24, 2)] * 15)
    assert list(elsewhere.cuts[[14, 15]]) == [0, 1]


def test_a_picture_without_his_place_shows_the_recorded_camera_and_says_why():
    r = camera_rig.Runner([[0.0, 0.0, 0.0], [math.nan] * 3], [0, 0], [(24, 1), None], [0.0, 1 / 30])
    shot = shots(r, rig(kind="front"), [1])[1]
    assert shot.camera is None and shot.reason == camera_rig.NO_RUNNER


# ------------------------------------------------------------------ walls

def test_a_wall_between_him_and_the_camera_pulls_it_in_front_of_the_wall_and_eases():
    """A wall at z = 1700 to 2000 across his path: the Front camera (500 ahead) stops 50 short of it,
    and the pull starts a few pictures before the wall reaches the line and eases out after."""
    r = run_along_z(n=100)
    walls = camera_rig.StaticWalls(np.array([[[-2000, -2000, 2000], [2000, -2000, 2000], [2000, 2000, 2000]],
                                             [[-2000, -2000, 2000], [2000, 2000, 2000], [-2000, 2000, 2000]]], float))
    got = shots(r, rig(kind="front", tuning={"rigPullFrames": 0}), range(100), walls)
    # Frame 55: Mario at z 1650, the wall 350 ahead: the camera at 300 (the wall less the standoff).
    assert got[55].camera["position"][2] == pytest.approx(1650 + 300, abs=0.5) and got[55].pulled < 1
    assert got[40].pulled == 1.0                                                  # 800 short of the wall: clear
    eased = shots(r, rig(kind="front", tuning={"rigPullFrames": 3}), range(100), walls)
    steps = [abs(eased[k + 1].pulled - eased[k].pulled) for k in range(99)]
    sharp = [abs(got[k + 1].pulled - got[k].pulled) for k in range(99)]
    assert max(steps) < max(sharp)
    assert eased[49].pulled < 1.0 == got[49].pulled                              # it starts before the wall
    # While he is short of the wall the camera never ends up beyond it, eased or not: the standoff
    # short of it while that leaves 40 units, then halfway between his eyes and the wall.
    assert all(eased[k].camera["position"][2] <= 2000 - 50 + 1e-6 for k in range(64))
    assert all(eased[k].camera["position"][2] < 2000 for k in range(67))


def test_a_wall_too_close_makes_the_camera_rise_over_it_before_it_moves_in():
    """He stands facing +z with a wall 100 units in front of his eyes, 200 above them: a camera pulled
    in there would be nearer him than the game draws anything (100 units), so it rises in 15-degree
    steps until its line clears the wall's top (75 degrees), keeping its whole distance. Where a
    ceiling closes that way too, it takes the line that leaves the most room."""
    def wall_z(z, top, low=-2000.0):
        a, b, c, d = [-2000, low, z], [2000, low, z], [2000, top, z], [-2000, top, z]
        return np.array([[a, b, c], [a, c, d]], np.float64)
    r = runner([[0.0, 0.0, 0.0]] * 30, [0] * 30)
    front = rig(kind="front", shot="medium", tuning={"rigCraneDistance": 250.0})
    shot = shots(r, front, [15], camera_rig.StaticWalls(wall_z(100, 300)))[15]
    assert shot.raised == pytest.approx(75.0) and shot.pulled == 1.0
    assert shot.camera["position"] == pytest.approx([0.0, 100 + 500 * math.sin(math.radians(75)),
                                                     500 * math.cos(math.radians(75))], abs=1e-6)
    level = shots(r, rig(kind="front", shot="medium", tuning={"rigCraneDistance": 0.0}), [15],
                  camera_rig.StaticWalls(wall_z(100, 300)))[15]
    assert level.raised == 0.0 and level.camera["position"][2] == pytest.approx(50.0)     # 0: it only moves in
    ceiling = np.concatenate([wall_z(100, 300), [[[-2000, 160, -2000], [2000, 160, -2000], [2000, 160, 2000]],
                                                 [[-2000, 160, -2000], [2000, 160, 2000], [-2000, 160, 2000]]]])
    boxed = shots(r, front, [15], camera_rig.StaticWalls(ceiling))[15]
    assert boxed.pulled < 1 and boxed.camera["position"][1] < 160 and boxed.camera["position"][2] < 100


def test_static_walls_are_the_pools_first_surfaces_less_the_ones_a_camera_sees_through():
    """sSurfacePool's first gNumStaticSurfaces surfaces, read in Project64's word order; a surface the
    game's camera passes (SURFACE_FLAG_NO_CAM_COLLISION) or an invisible one is left out."""
    ram = bytearray(0x2000)

    def surface(kind, flags, vertices):
        return struct.pack(">hhbbhh9h3ffI", kind, 0, flags, 0, 0, 0, *vertices, 0, 0, 1, 0, 0)
    pool = b"".join([surface(0, 0, (0, 0, 0, 100, 0, 0, 0, 100, 0)), surface(0, 0x02, (1,) * 9),
                     surface(0x0072, 0, (2,) * 9), surface(0x0001, 0, (300, 0, 0, 300, 100, 0, 300, 0, 100)),
                     surface(0, 0, (9,) * 9)])
    ram[0x1000:0x1000 + len(pool)] = pool
    struct.pack_into(">Ii", ram, 0x100, 0x80001000, 4)                           # the pointer, then the count
    swapped = np.frombuffer(bytes(ram), ">u4").astype("<u4").tobytes()            # Project64's word order
    walls = camera_rig.static_walls(lambda offset, size: swapped[offset:offset + size], 0x80000100, 0x80000104)
    assert walls.triangles.tolist() == [[[0, 0, 0], [100, 0, 0], [0, 100, 0]], [[300, 0, 0], [300, 100, 0], [300, 0, 100]]]
    struct.pack_into(">I", ram, 0x100, 0x12345678)                                # not a pointer into RDRAM
    swapped = np.frombuffer(bytes(ram), ">u4").astype("<u4").tobytes()
    assert camera_rig.static_walls(lambda offset, size: swapped[offset:offset + size], 0x80000100, 0x80000104) is None


# ------------------------------------------------------------------ Side: the clear side, held

def sides_of(got) -> str:
    return "".join({"left": "L", "right": "R"}.get(shot.side, ".") for _k, shot in sorted(got.items()))


def test_side_takes_the_clear_side_and_keeps_it_through_a_short_block():
    """Running +z, his left is +x. A long wall on his right (x = -300, z 885 to 2715: frames 30 to 90)
    moves the shot to his left rigPullFrames (3) before it begins, so the camera never moves in just
    before the cut; a pillar on his left for four frames (50 to 53) only pulls the camera in, easing;
    after the wall ends the shot stays left (no reason to cut back)."""
    r = run_along_z(n=120)
    walls = camera_rig.StaticWalls(np.concatenate([wall_x(-300, 885, 2715), wall_x(300, 1485, 1605)]))
    side = rig(kind="side", shot="medium")
    got = shots(r, side, range(120), walls)
    assert sides_of(got) == "R" * 27 + "L" * 93
    assert all(got[k].pulled == 1.0 for k in range(27))                          # nothing moved in before the cut
    assert all(got[k].pulled < 1 for k in range(47, 57)) and got[40].pulled == got[60].pulled == 1.0
    left = np.array(got[70].camera["position"]) - np.array(r.positions[70])
    assert left[0] == pytest.approx(500.0, abs=1.0)


def test_a_wall_shorter_than_the_hold_never_flips_the_side():
    """A wall on his right that comes and goes every few frames (shorter than the hold each time):
    the shot never cuts back and forth."""
    r = run_along_z(n=120)
    flicker = np.concatenate([wall_x(-300, 30.0 * start, 30.0 * (start + 4)) for start in range(20, 100, 8)])
    got = shots(r, rig(kind="side"), range(120), camera_rig.StaticWalls(flicker))
    assert set(sides_of(got)) == {"R"}
    # Proven by the opposite: a hold of 1 frame does cut on those same walls.
    eager = shots(r, rig(kind="side", tuning={"rigSideHoldFrames": 1}), range(120), camera_rig.StaticWalls(flicker))
    assert "L" in sides_of(eager)


def test_after_a_cut_the_side_stays_a_while_before_it_cuts_back():
    """A wall on his right (frames 30 to 45), then one on his left (50 to 90): with no stay the shot cuts
    left at 27 and back right 20 frames later; staying 30 frames, it cuts back only at 57 and rides
    the left wall's first frames pulled in."""
    r = run_along_z(n=120)
    walls = camera_rig.StaticWalls(np.concatenate([wall_x(-300, 885, 1365), wall_x(300, 1485, 2715)]))
    eager = shots(r, rig(kind="side", tuning={"rigSideStayFrames": 0}), range(120), walls)
    assert sides_of(eager) == "R" * 27 + "L" * 20 + "R" * 73
    steady = shots(r, rig(kind="side", tuning={"rigSideStayFrames": 30}), range(120), walls)
    assert sides_of(steady) == "R" * 27 + "L" * 30 + "R" * 63
    assert steady[52].pulled < 1 or steady[52].raised > 0


class CountingWalls(camera_rig.StaticWalls):
    """Walls that count the lines tested against them."""

    def __init__(self, triangles):
        super().__init__(triangles)
        self.lines = 0

    def clearance(self, origins, ends):
        self.lines += len(np.asarray(origins).reshape(-1, 3))
        return super().clearance(origins, ends)


def test_a_growing_scenes_side_tests_only_the_frames_it_gained():
    """A scene still being written gains frames: the frames further from its old end than the smoothing
    reaches (3 x 6 + 0 + 2 = 20 here) keep their wall tests, only the rest are tested again, and the
    sides are the ones a fresh look at the whole path chooses."""
    whole = run_along_z(n=120)
    walls = CountingWalls(np.concatenate([wall_x(-300, 885, 2715), wall_x(300, 1485, 1605)]))
    side = rig(kind="side")
    first = runner(whole.positions[:80], whole.yaws[:80], seconds=whole.seconds[:80])
    grown = runner(whole.positions, whole.yaws, seconds=whole.seconds)
    planner = camera_rig.Planner()
    planner.shot(side, first, 10, lambda place: walls)
    carried = planner._carried(side, grown)
    assert carried[1] == 60
    walls.lines = 0
    sides, _ = camera_rig.choose_sides(side, camera_rig.Track(grown, 6, 0, 200.0, 100.0), lambda place: walls, carried)
    assert walls.lines == 2 * (120 - 60)
    fresh, _ = camera_rig.choose_sides(side, camera_rig.Track(grown, 6, 0, 200.0, 100.0), lambda place: walls)
    assert list(sides) == list(fresh)
    got = {k: planner.shot(side, grown, k, lambda place: walls) for k in range(120)}
    assert {k: shot.camera for k, shot in got.items()} == {k: shot.camera for k, shot in shots(grown, side, range(120), walls).items()}
    # A path that changed before its end (not only grew) is tested again whole.
    moved = runner(np.concatenate([whole.positions[:40] + [5.0, 0, 0], whole.positions[40:]]), whole.yaws,
                   seconds=whole.seconds)
    assert planner._carried(side, moved) is None


def test_a_chosen_side_stays_put_and_only_pulls_in():
    r = run_along_z(n=120)
    walls = camera_rig.StaticWalls(wall_x(-300, 885, 2715))
    got = shots(r, rig(kind="side", side="right"), range(120), walls)
    assert set(sides_of(got)) == {"R"} and got[60].pulled < 1


# ------------------------------------------------------------------ Orbit: the scene's own clock

def test_orbit_turns_with_the_scenes_clock():
    """Angle = phase + speed x the picture's time: a held picture (a level load showing 817 ms) keeps
    its angle, and the next picture is where that much time put it."""
    seconds = [0.0, 1 / 30, 2 / 30, 0.95, 0.95 + 1 / 30]
    r = runner([[0.0, 0.0, 0.0]] * 5, seconds=seconds)
    orbit = rig(kind="orbit", radius=600.0, speed=90.0, phase=30.0, height=100.0)
    for k, shot in shots(r, orbit, range(5)).items():
        angle = math.radians(30.0 + 90.0 * seconds[k])
        assert shot.camera["position"] == pytest.approx([600 * math.sin(angle), 100.0, 600 * math.cos(angle)], abs=1e-6)
        assert shot.camera["focus"] == pytest.approx([0.0, 100.0, 0.0])


# ------------------------------------------------------------------ round 7: tilt, lens, Chase, Overhead, Tripod
# His words: "I want to be able to allow users to basically do whatever angle they want / tilt the camera /
# adjust focal length / etc".

def test_every_rig_draws_with_its_own_roll_and_lens_and_keeps_him_the_same_size():
    """The picture's roll and field of view are the request's. At another lens the camera's line to his
    eyes lengthens by tan(rigFov/2) / tan(fov/2), so distance x tan(fov/2), his size in the picture,
    holds; rigLensKeepsSize 0 leaves the camera where it was and only zooms."""
    r = run_along_z(n=120)
    eyes = r.positions[60] + [0, 100, 0]
    for kind, fields in (("front", {}), ("side", {"side": "left"}), ("orbit", {"speed": 0.0, "phase": 40.0}),
                         ("chase", {"shot": "wide"}), ("overhead", {}), ("front", {"height": 400.0})):
        base = shots(r, rig(kind=kind, **fields), [60])[60].camera
        assert (base["roll"], base["fov"]) == (0, 50.0)
        sizes = []
        for fov in (14.8, 50.0, 85.7):
            camera = shots(r, rig(kind=kind, roll=-2731, fov=fov, **fields), [60])[60].camera
            assert (camera["roll"], camera["fov"]) == (-2731, fov)
            line = np.array(camera["position"]) - eyes
            assert np.cross(line, np.array(base["position"]) - eyes) == pytest.approx([0, 0, 0], abs=1e-6)  # same bearing
            sizes.append(np.linalg.norm(line) * math.tan(math.radians(fov) / 2))
            still = shots(r, rig(kind=kind, fov=fov, tuning={"rigLensKeepsSize": 0.0}, **fields), [60])[60].camera
            assert still["position"] == pytest.approx(base["position"]) and still["fov"] == fov
        assert sizes == pytest.approx([sizes[1]] * 3), kind


def test_chase_rides_behind_him_and_overhead_looks_down_from_behind_at_its_angle():
    r = run_along_z(n=120)
    chase = shots(r, rig(kind="chase", height=100.0), [60])[60].camera
    assert chase["position"] == pytest.approx([0.0, 100.0, 30.0 * 60 - 500.0], abs=1.0)    # medium, behind along -z
    assert chase["focus"] == pytest.approx([0.0, 100.0, 30.0 * 60])
    over = shots(r, rig(kind="overhead"), [60])[60].camera                                   # height 900 above his feet
    line = np.array(over["position"]) - np.array(over["focus"])
    assert over["position"][1] == pytest.approx(900.0, abs=1e-6)
    assert math.degrees(camera_rig.elevation(line[None])[0]) == pytest.approx(60.0)          # rigOverheadAngle
    assert line[2] < 0 and abs(line[0]) < 1e-6                                               # behind him: he runs up the picture
    higher = shots(r, rig(kind="overhead", height=2000.0), [60])[60].camera
    assert higher["position"][1] == pytest.approx(2000.0, abs=1e-6)


def test_a_tripod_stays_where_it_was_put_and_turns_to_follow_him():
    """Its place is the request's in every picture, walls or not; it aims at his eyes as he runs past;
    standing on his eyes it has no direction and shows the recorded camera."""
    r = run_along_z(n=120)
    walls = camera_rig.StaticWalls(wall_x(150, 0, 4000))                     # between him and the tripod
    tripod = rig(kind="tripod", position=[300.0, 400.0, 1200.0], roll=1000, fov=30.0)
    got = shots(r, tripod, range(0, 120, 7), walls)
    for k, shot in got.items():
        assert shot.camera["position"] == [300.0, 400.0, 1200.0] and shot.pulled == 1.0 and shot.raised == 0.0
        assert shot.camera["focus"] == pytest.approx(list(r.positions[k] + [0, 100, 0]))
        assert (shot.camera["roll"], shot.camera["fov"]) == (1000, 30.0)
    on_him = shots(r, rig(kind="tripod", position=[0.0, 100.0, 30.0 * 20]), [20])[20]
    assert on_him.camera is None and on_him.reason == camera_rig.TRIPOD_ON_HIM


def test_every_rig_is_the_same_camera_whatever_was_asked_before():
    """Forward, backward, shuffled, one planner or a fresh one per picture: the same cameras."""
    r = run_along_z(n=90, wobble=5.0)
    walls = camera_rig.StaticWalls(np.concatenate([wall_x(-300, 900, 2000), wall_x(300, 1500, 1590),
                                                   wall_x(-2000, 600, 700)]))
    order = list(range(90))
    shuffled = order[::7] + order[3::7] + order[1::7] + order[5::7] + order[2::7] + order[6::7] + order[4::7]
    for which in (rig(kind="front"), rig(kind="side"), rig(kind="orbit", speed=60.0), rig(kind="chase", roll=900),
                  rig(kind="overhead", fov=20.0), rig(kind="tripod", position=[300.0, 400.0, 1200.0], roll=-5461),
                  rig(kind="side", fov=90.0, roll=300)):
        forward = shots(r, which, order, walls)
        backward = shots(r, which, order[::-1], walls)
        mixed = shots(r, which, shuffled, walls)
        alone = {k: camera_rig.Planner().shot(which, r, k, lambda place: walls) for k in order}
        for k in order:
            assert forward[k].camera == backward[k].camera == mixed[k].camera == alone[k].camera, (which.kind, k)
    # One planner switching between rigs (the page switching cameras) keeps nothing of the last one.
    shared = camera_rig.Planner()
    for which in (rig(kind="side"), rig(kind="front", shot="close"), rig(kind="orbit"), rig(kind="front", shot="wide"),
                  rig(kind="tripod", position=[0.0, 300.0, 0.0]), rig(kind="chase", fov=30.0), rig(kind="overhead")):
        for k in shuffled[:30]:
            assert shared.shot(which, r, k, lambda place: walls).camera \
                == camera_rig.Planner().shot(which, r, k, lambda place: walls).camera, (which.kind, k)


# ------------------------------------------------------------------ the session and the lab's routes

POSES = [(100.0 * k, 50.0, -200.0, 0) for k in range(8)]          # running +x, facing +z (yaw 0)
POOL, POOL_POINTER = 0x350100, 0x350000


def _pool() -> dict:
    """A surface pool holding one wall across x = 500 (his path runs into it), and the two words
    sSurfacePool and gNumStaticSurfaces name."""
    surface = struct.pack(">hhbbhh9h3ffI", 0, 0, 0, 0, 0, 0, 500, -1000, -1000, 500, 1000, -1000, 500, 0, 1000, 1, 0, 0, 0, 0)
    return {POOL_POINTER: struct.pack(">Ii", 0x80000000 | POOL, 1), POOL: surface}


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scene-rigs")
    rom = bytearray(fixture.rom())
    rom[0x3E] = ord("E")
    rom = bytes(rom)
    session = fixture.write_session(folder, rom, [(1000 + k, 24, 1) for k in range(8)], poses=POSES, extra=_pool())
    out = folder / "scene.sm64scene"
    fixture.pk.write_package(fixture.pk.tap_source(session, "next", states=True), rom, out, fixture.pk.ZLIB)
    (folder / "rom.z64").write_bytes(rom)
    (folder / "MappingUS.map").write_text(
        f"                0x00000000{0x80000000 | POOL_POINTER:08x}                sSurfacePool\n"
        f"                0x00000000{0x80000000 | POOL_POINTER + 4:08x}                gNumStaticSurfaces\n",
        encoding="utf-8")
    return {"folder": folder, "rom": folder / "rom.z64", "package": out, "map": folder / "MappingUS.map"}


@pytest.fixture
def config(recording, tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    (tmp_path / "Plugin").mkdir()
    return type("Config", (), {"rom": recording["rom"], "pj64_plugin": tmp_path / "Plugin", "renderer": "trainer",
                               "resolution": None, "map": recording["map"]})()


class Evaluators:
    """The evaluator factory the lab is given, over fake_viewer_host's: it names the lab's symbol map."""

    def __init__(self, map_path):
        self.map_path, self.made = map_path, []

    def __call__(self, package, rom):
        self.made.append(fake_viewer_host.FakeEvaluator())
        return self.made[-1]


def _session(config, recording, reader=rig_path.InProcessReader):
    """A session over the fake host and evaluator; Mario's path read by `reader` (in this process
    unless a test asks for the child)."""
    made = Evaluators(recording["map"])
    host = scene_session.command_host(fake_viewer_host.command())
    return scene_session.SceneSession.open(config, recording["package"], host_factory=host, evaluator_factory=made,
                                           path_reader_factory=reader), made


class HeldReader:
    """A path reader whose reads finish when the test says (finish), in this process."""

    def __init__(self):
        self.asked = []

    def read(self, data, rom, indices, mario):
        from concurrent.futures import Future
        self.asked.append((data, rom, list(indices), mario))
        self.future = Future()
        return self.future

    def finish(self):
        data, rom, indices, mario = self.asked[-1]
        self.future.set_result(rig_path.InProcessReader().read(data, rom, indices, mario).result())

    def close(self):
        pass


def test_a_rig_asked_before_marios_path_is_read_shows_the_recorded_camera_and_never_waits(config, recording):
    """The lead's ask (round 6): the path read must never hold a picture. Until the reader answers,
    a rig picture is the recorded one with one plain sentence, and the next one after it follows him."""
    held = HeldReader()
    session, _made = _session(config, recording, reader=lambda: held)
    try:
        front = {"rig": {"kind": "front", "tuning": {"marioHeight": 100, "rigAimFrames": 0}}}
        waiting, _ = session.picture(2, front, "raw")
        assert waiting["camera"] == "recorded" and waiting["reason"] == camera_rig.PATH_PENDING
        assert waiting["camera_used"] is None and waiting["rig"]["kind"] == "front"
        assert held.asked[0][2] == session.frames and len(held.asked) == 1
        assert session.picture(3, front, "raw")[0]["reason"] == camera_rig.PATH_PENDING
        assert len(held.asked) == 1                                       # one read, not one a picture
        held.finish()
        assert session.prepare_path().done()
        drawn, _ = session.picture(2, front, "raw")
        assert drawn["camera"] == "free" and drawn["camera_used"]["focus"] == pytest.approx([200.0, 150.0, -200.0])
        # A growing scene's newest frame not read yet: the frames before it follow him meanwhile, that
        # one says it is getting ready, and only it is read again.
        newest = session.frames[-1]
        del session._stage_reads[newest]
        assert session.picture(len(session.frames) - 1, front, "raw")[0]["reason"] == camera_rig.PATH_PENDING
        assert held.asked[-1][2] == [newest]
        assert session.picture(2, front, "raw")[0]["camera"] == "free"
        held.finish()
        assert session.picture(len(session.frames) - 1, front, "raw")[0]["camera"] == "free"
    finally:
        session.close()


def test_the_child_reads_the_same_path_as_this_process(recording):
    """rig_path.PathReader's child (below normal priority, hidden) answers what read_poses gives here."""
    import package as scene_package
    data, rom = recording["package"].read_bytes(), recording["rom"].read_bytes()
    package = scene_package.Package(data, rom)
    indices = list(range(len(package)))
    from sm64_events.memory.layout import layout_for
    mario = layout_for("us").mario_struct & 0x1FFFFFFF
    reader = rig_path.PathReader()
    try:
        poses = reader.read(data, rom, indices, mario).result(timeout=60)
        again = reader.read(data, rom, indices[2:5], mario).result(timeout=60)          # the ROM went once
    finally:
        assert reader.close() == 0
    here = rig_path.read_poses(package, indices, mario)
    assert np.array_equal(poses, here, equal_nan=True) and np.array_equal(again, here[2:5], equal_nan=True)
    assert poses[:, 0].tolist() == [x for x, _y, _z, _yaw in POSES]


def test_a_rig_follows_the_stages_mario_from_the_scene_state_through_the_free_cameras_path(config, recording):
    session, made = _session(config, recording)
    try:
        for position in range(len(session.frames)):
            header, _ = session.picture(position, {"rig": {"kind": "orbit", "radius": 300, "speed": 0, "phase": 90,
                                                           "height": 120, "tuning": {"marioHeight": 120, "rigAimFrames": 0}}},
                                        "raw")
            x = POSES[position][0]
            assert header["camera"] == "free" and header["reason"] is None
            assert {key: header["rig"][key] for key in ("kind", "target", "shot", "side")} == {
                "kind": "orbit", "target": "stage", "shot": "medium", "side": None}
            used = header["camera_used"]
            assert used["focus"] == pytest.approx([x, 170.0, -200.0])               # his eyes: feet 50 + 120
            index, drawn = made.made[0].calls[-1]
            assert index == session.frames[position]
            assert drawn == (used["focus"], used["position"], 0, used["fov"])        # what it says it drew
        # The wall at x = 500 (the scene state's surface pool): a camera 300 units on his +x side pulls
        # in as he nears it, and is clear where the wall is far.
        clear, _ = session.picture(0, {"rig": {"kind": "orbit", "radius": 300, "speed": 0, "phase": 90,
                                               "tuning": {"rigPullFrames": 0}}}, "raw")
        assert clear["rig"]["pulled"] == 1.0
        near, _ = session.picture(4, {"rig": {"kind": "orbit", "radius": 300, "speed": 0, "phase": 90,
                                              "tuning": {"rigPullFrames": 0, "rigCraneDistance": 0}}}, "raw")
        assert near["rig"]["pulled"] < 1 and near["camera_used"]["position"][0] < 500
        # Allowed to rise, it goes over the wall's top instead and keeps its distance.
        over, _ = session.picture(4, {"rig": {"kind": "orbit", "radius": 300, "speed": 0, "phase": 90,
                                              "tuning": {"rigPullFrames": 0, "rigCraneDistance": 250}}}, "raw")
        assert over["rig"]["raised"] > 0 and over["rig"]["pulled"] == 1.0
    finally:
        session.close()


def test_every_header_says_which_camera_it_drew(config, recording):
    """Interface E: camera_used is the pose drawn for any camera the server computed, null for the recorded one."""
    session, _made = _session(config, recording)
    try:
        assert session.picture(1, None, "raw")[0]["camera_used"] is None
        free = {"focus": [0.0, 100.0, 0.0], "position": [0.0, 400.0, 900.0], "roll": 200, "fov": 40.0}
        assert session.picture(1, free, "raw")[0]["camera_used"] == free
        plain = {"focus": [0.0, 100.0, 0.0], "position": [0.0, 400.0, 900.0]}
        assert session.picture(1, plain, "raw")[0]["camera_used"] == {**plain, "roll": None, "fov": None}
    finally:
        session.close()


def test_a_rigs_tilt_and_lens_reach_the_games_drawing_and_its_header(config, recording):
    """Interface I (round 7): a rig's roll and fov are what the game's drawing code is given and what
    camera_used reports, so a free camera of camera_used (Free camera, a preset, an export) draws the
    same picture; a tripod stands where its request puts it."""
    session, made = _session(config, recording)
    try:
        request = {"rig": {"kind": "chase", "roll": -2731, "fov": 20.0, "tuning": {"marioHeight": 120}}}
        header, _ = session.picture(3, request, "raw")
        used = header["camera_used"]
        assert header["camera"] == "free" and header["reason"] is None and (used["roll"], used["fov"]) == (-2731, 20.0)
        drawn = made.made[0].calls[-1]
        assert drawn[1] == (used["focus"], used["position"], -2731, 20.0)
        again, _ = session.picture(3, used, "raw")                    # the same camera, as a free camera
        assert again["camera_used"] == used and made.made[0].calls[-1] == drawn
        session.picture(3, {"rig": {**request["rig"], "roll": 0}}, "raw")
        assert made.made[0].calls[-1][1][2:] == (0, 20.0)
        tripod, _ = session.picture(3, {"rig": {"kind": "tripod", "position": [0.0, 900.0, 900.0], "fov": 70.0}}, "raw")
        assert tripod["camera_used"]["position"] == [0.0, 900.0, 900.0]
        assert (tripod["camera_used"]["roll"], tripod["camera_used"]["fov"]) == (0, 70.0)
    finally:
        session.close()


def ghost_clip(coordinates) -> bytes:
    """A ghost whose figure stands at x = -40 x (its row's coordinate - 1), z 700, one row per coordinate."""
    rows = []
    for index, coordinate in enumerate(coordinates):
        raw = bytearray(frame(index + 1, elapsed=coordinate, igt=coordinate, level=24, area=1))
        struct.pack_into("<3f", raw, POSE_AT, -40.0 * (coordinate - 1), 0.0, 700.0)
        rows.append(bytes(raw))
    return encode_clip(rows, producer(), clock_kind=STAR_CLOCK)


def test_a_rig_follows_a_ghost_through_its_recording_and_says_when_it_cannot(config, recording):
    session, _made = _session(config, recording)
    try:
        # Game frame 3 of its run was never recorded: picture 2 shows the row before it, as its drawing does.
        session.set_ghosts([{"ref": "a", "label": "Sully"}], Loader({"a": (ghost_clip([1, 2, 4, 5, 6]), "Sully")}))
        follow = {"kind": "front", "target": "a", "tuning": {"marioHeight": 100, "rigAimFrames": 0}}
        for position in range(len(session.frames)):
            header, _ = session.picture(position, {"rig": follow}, "raw")
            x = [0, -40, -40, -120, -160, -200, -200, -200][position]      # it holds its last pose past its end
            assert header["camera_used"]["focus"] == pytest.approx([x, 100.0, 700.0])
            assert header["rig"]["target"] == "a"
        gone, _ = session.picture(1, {"rig": {"kind": "front", "target": "nobody"}}, "raw")
        assert gone["camera"] == "recorded" and gone["reason"] == stage_ghosts.VIEW_GONE and gone["camera_used"] is None
        for wrong in ({"rig": {"kind": "spin"}}, {"rig": {"kind": "front"}, "focus": [0, 0, 0]}, {"rig": "front"}):
            with pytest.raises(ValueError):
                session.picture(1, wrong, "raw")
    finally:
        session.close()


def test_without_mario_in_the_scene_state_a_rig_shows_the_recorded_camera(config, recording, tmp_path):
    """A recording whose state never captured Mario (the pattern reads as no place) says why."""
    rom = recording["rom"].read_bytes()
    session_dir = fixture.write_session(tmp_path, rom, [(1000 + k, 24, 1) for k in range(4)])
    package = tmp_path / "plain.sm64scene"
    fixture.pk.write_package(fixture.pk.tap_source(session_dir, "next", states=True), rom, package, fixture.pk.ZLIB)
    host = scene_session.command_host(fake_viewer_host.command())
    session = scene_session.SceneSession.open(config, package, host_factory=host, evaluator_factory=Evaluators(None),
                                              path_reader_factory=rig_path.InProcessReader)
    try:
        header, _ = session.picture(1, {"rig": {"kind": "side"}}, "raw")
        assert header["camera"] == "recorded" and header["reason"] == camera_rig.NO_RUNNER
    finally:
        session.close()


def _routes(config, recording, tmp_path, **options):
    """The lab's real routes over the fake host, Mario's path read by the real child unless `options` say."""
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "scene.sm64scene").write_bytes(recording["package"].read_bytes())
    path = tmp_path / "lab.json"
    path.write_text(json.dumps({"rom": str(recording["rom"]), "packages": str(tmp_path / "packages"),
                                "pj64_plugin": str(config.pj64_plugin), "map": str(recording["map"])}), encoding="utf-8")
    from fastapi import FastAPI
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, path, watch_seconds=0.05, ghost_loader=Loader({}), session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command(**options.pop("fake_host", {}))),
        "evaluator_factory": Evaluators(recording["map"]), **options})
    return app


class TimedEvaluators(Evaluators):
    """Evaluators that note when each was made and each pose asked of it."""

    def __call__(self, package, rom):
        evaluator = super().__call__(package, rom)
        self.at, original = time.perf_counter(), evaluator.pose

        def pose(index):
            self.posed = getattr(self, "posed", 0) + 1
            return original(index)
        evaluator.pose = pose
        return evaluator


def test_the_free_camera_warms_once_after_the_first_picture_while_the_page_is_idle(config, recording, tmp_path):
    """The lead's ask (round 6): neither the first Free camera nor the first rig pays the free camera's
    start (~200 ms). Once a scene's first picture is delivered and nothing is being drawn, the worker
    starts the evaluator and draws one view from the recorded camera; never while pictures are being
    drawn (here four pictures from a slow renderer: it waits for the last), and once a scene."""
    made = TimedEvaluators(recording["map"])
    app = _routes(config, recording, tmp_path, evaluator_factory=made, path_reader_factory=rig_path.InProcessReader,
                  fake_host={"delay_ms": 120})
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            for ident in range(1, 5):
                socket.send_json({"id": ident, "session": opened["session"], "frame": ident, "encoding": "raw",
                                  "camera": None})
            replies = []                                    # each answered id: a picture, or superseded
            while len(replies) < 4:
                data = socket.receive()
                raw = data.get("bytes")
                answer = json.loads(raw[4:4 + struct.unpack_from("<I", raw)[0]]) if raw else json.loads(data["text"])
                replies.append((answer["id"], time.perf_counter()))
            replies = [at for _ident, at in replies]
            deadline = time.monotonic() + 10
            while not made.made and time.monotonic() < deadline:
                time.sleep(0.02)
            assert len(made.made) == 1 and made.posed == 1                    # warmed, from the recorded camera
            assert made.at >= replies[-1] - 0.05                              # after the last picture went out
            # Then, Mario's path read, the cinematic cameras' runner and Front's first windows.
            session = app.state.scene_lab.session
            while not session.rigs.tracks and time.monotonic() < deadline:
                time.sleep(0.02)
            assert session.rigs.tracks and session.rigs.rig.kind == "front" and session.rigs.pulls
            socket.send_json({"id": 9, "session": opened["session"], "frame": 2, "encoding": "raw",
                              "camera": {"focus": [0.0, 100.0, 0.0], "position": [0.0, 400.0, 900.0]}})
            assert _picture_header(socket)["camera"] == "free"
            time.sleep(0.3)
            assert len(made.made) == 1 and made.posed == 1                    # once a scene


class WarmedEvaluators(Evaluators):
    """Evaluators that can take state resolvers (attach, ahead) and a factory that notes how it
    started them (the frame they warm with), as scene_session.GameCodeEvaluator does."""

    class Evaluator(fake_viewer_host.FakeEvaluator):
        def attach(self, resolvers, ahead):
            self.resolvers = resolvers

        def ahead(self, indices):
            self.handed = list(indices)
            return False                          # children not ready yet: the caller resolves ahead

        def delivered(self, index, answer, ram):
            pass

    def __call__(self, package, rom):
        self.made.append(self.Evaluator())
        return self.made[-1]

    def resolvers(self, package, deliver, warm=None):
        from types import SimpleNamespace
        self.warm = warm
        return SimpleNamespace(close=lambda: None)


def test_the_warm_up_resolves_the_next_frames_here_and_warms_the_resolvers_past_them(config, recording):
    """warm(): the evaluator, one view of the frame on screen, the next WARM_FRAMES states here (a
    Play from this frame asks them first), and the state resolvers started on the frames after,
    each child warming the first of them before it says ready (its first frame is otherwise cold)."""
    made = WarmedEvaluators(recording["map"])
    host = scene_session.command_host(fake_viewer_host.command())
    session = scene_session.SceneSession.open(config, recording["package"], host_factory=host, evaluator_factory=made,
                                              path_reader_factory=rig_path.InProcessReader)
    try:
        session.picture(1, None, "raw")
        session.warm(1)
        evaluator = made.made[0]
        after = session.frames[2:2 + scene_session.WARM_FRAMES]
        assert [index for index, _thread in evaluator.prepared] == after
        assert made.warm == session.frames[2 + scene_session.WARM_FRAMES]
        assert evaluator.handed[0] == made.warm
        session.warm(1)                                                     # once a scene
        assert len(made.made) == 1 and len(evaluator.prepared) == scene_session.WARM_FRAMES
    finally:
        session.close()


def test_the_socket_asks_for_a_rig_in_one_picture_request(config, recording, tmp_path):
    """Interface D through the lab's real routes: {"camera": {"rig": {...}}}, one round trip per picture."""
    app = _routes(config, recording, tmp_path)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            # The page asks for the path as the scene opens; the answer comes once the child read it.
            socket.send_json({"id": 9, "session": opened["session"], "kind": "rig-path"})
            assert json.loads(socket.receive()["text"]) == {"id": 9, "kind": "rig-path", "ready": True, "reason": None}
            socket.send_json({"id": 1, "session": opened["session"], "frame": 3, "encoding": "raw",
                              "camera": {"rig": {"kind": "front", "shot": "close", "tuning": {"marioHeight": 100}}}})
            header = _picture_header(socket)
            assert header["id"] == 1 and header["camera"] == "free" and header["rig"]["kind"] == "front"
            assert header["camera_used"]["focus"][0] == pytest.approx(300.0, abs=1.0)
            socket.send_json({"id": 2, "session": opened["session"], "frame": 3, "encoding": "raw", "camera": None})
            assert _picture_header(socket)["camera_used"] is None
            socket.send_json({"id": 3, "session": opened["session"], "frame": 3, "encoding": "raw",
                              "camera": {"rig": {"kind": "front", "shot": "huge"}}})
            assert "shot" in json.loads(socket.receive()["text"])["error"]


def _rig_tuning_sent(source: str) -> set[str]:
    """The rows scenerigs.js's rigTuning puts in a request: each `name: tuning.name` it lists."""
    body = source[source.index("export function rigTuning"):]
    body = body[:body.index("\n}\n")]
    return {key for key, read in re.findall(r"(\w+): tuning\.(\w+)", body) if key == read}


def test_the_page_sends_every_tuning_row_a_rig_reads():
    """The rows the lab reads (camera_rig.TUNING_KEYS) are rows of the registry (SHIPPED reads each
    one) and exactly what the page's request carries."""
    source = strip_comments((ROOT / "src" / "sm64_events" / "ui" / "components" / "scenerigs.js").read_text(encoding="utf-8"))
    assert _rig_tuning_sent(source) == set(camera_rig.TUNING_KEYS) == set(SHIPPED)
    dropped = source.replace("rigPullFrames: tuning.rigPullFrames,", "", 1)          # the guard can still fail
    assert _rig_tuning_sent(dropped) == set(camera_rig.TUNING_KEYS) - {"rigPullFrames"}
