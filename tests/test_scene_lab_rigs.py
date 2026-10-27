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
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import camera_rig  # noqa: E402
import fake_viewer_host  # noqa: E402
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
             "rigOrbitRadius": 600.0, "rigOrbitSpeed": 45.0}


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
    for wrong, word in (({"kind": "dolly"}, "kind"), ({"kind": "front", "shot": "huge"}, "shot"),
                        ({"kind": "side", "side": "up"}, "side"), ({"kind": "orbit", "speed": 1e4}, "speed"),
                        ({"kind": "orbit", "radius": 1}, "radius"), ({"kind": "front", "height": -5}, "height"),
                        ({"kind": "front", "target": ""}, "follows"), ({"kind": "front", "zoom": 2}, "zoom"),
                        ({"kind": "front", "tuning": {"rigFov": 500}}, "rigFov"), ("front", "kind")):
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


def test_every_rig_is_the_same_camera_whatever_was_asked_before():
    """Forward, backward, shuffled, one planner or a fresh one per picture: the same cameras."""
    r = run_along_z(n=90, wobble=5.0)
    walls = camera_rig.StaticWalls(np.concatenate([wall_x(-300, 900, 2000), wall_x(300, 1500, 1590),
                                                   wall_x(-2000, 600, 700)]))
    order = list(range(90))
    shuffled = order[::7] + order[3::7] + order[1::7] + order[5::7] + order[2::7] + order[6::7] + order[4::7]
    for which in (rig(kind="front"), rig(kind="side"), rig(kind="orbit", speed=60.0)):
        forward = shots(r, which, order, walls)
        backward = shots(r, which, order[::-1], walls)
        mixed = shots(r, which, shuffled, walls)
        alone = {k: camera_rig.Planner().shot(which, r, k, lambda place: walls) for k in order}
        for k in order:
            assert forward[k].camera == backward[k].camera == mixed[k].camera == alone[k].camera, (which.kind, k)
    # One planner switching between rigs (the page switching cameras) keeps nothing of the last one.
    shared = camera_rig.Planner()
    for which in (rig(kind="side"), rig(kind="front", shot="close"), rig(kind="orbit"), rig(kind="front", shot="wide")):
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


def _session(config, recording):
    made = Evaluators(recording["map"])
    host = scene_session.command_host(fake_viewer_host.command())
    return scene_session.SceneSession.open(config, recording["package"], host_factory=host, evaluator_factory=made), made


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
    session = scene_session.SceneSession.open(config, package, host_factory=host, evaluator_factory=Evaluators(None))
    try:
        header, _ = session.picture(1, {"rig": {"kind": "side"}}, "raw")
        assert header["camera"] == "recorded" and header["reason"] == camera_rig.NO_RUNNER
    finally:
        session.close()


def test_the_socket_asks_for_a_rig_in_one_picture_request(config, recording, tmp_path):
    """Interface D through the lab's real routes: {"camera": {"rig": {...}}}, one round trip per picture."""
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "scene.sm64scene").write_bytes(recording["package"].read_bytes())
    path = tmp_path / "lab.json"
    path.write_text(json.dumps({"rom": str(recording["rom"]), "packages": str(tmp_path / "packages"),
                                "pj64_plugin": str(config.pj64_plugin), "map": str(recording["map"])}), encoding="utf-8")
    from fastapi import FastAPI
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, path, watch_seconds=0.05, ghost_loader=Loader({}), session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command()),
        "evaluator_factory": Evaluators(recording["map"])})
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
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
