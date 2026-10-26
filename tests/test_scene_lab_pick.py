"""Set focus's point (tools/scene_lab/pick.py): where a picture's Fast3D list drew one pixel.

Scenes are written as real display lists by fake_viewer_host.FakeList (viewport, matrices,
modes, triangles in a small big-endian RDRAM) and walked by the real pick.py; each expected
point comes from projecting the scene forward with the same projection, never from pick.py's
own inverse. The real package's faithfulness (Mario's pixel lands on Mario, the ground under
his feet at his height, a point re-picked from a camera turned around it) is measured by the
lab's CPU check, not here: it needs the ROM and the symbol map.
"""
import math
import struct
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import camera_state  # noqa: E402
import pick  # noqa: E402
from fake_viewer_host import IDENTITY, G_ZBUFFER, FakeList, perspective, wall  # noqa: E402

G_CULL_FRONT = 0x1000
ROUNDING = 0.01     # the list holds s15.16 matrices: a point is exact to their rounding
PERSPECTIVE = perspective()


def scene() -> FakeList:
    return FakeList().viewport().matrix(perspective(), projection=True).matrix(IDENTITY)


def square(z: float, half: float = 3000.0, facing: bool = True):
    """Two triangles at depth z filling the view, counter-clockwise from the camera unless `facing` is False."""
    a, b, c, d = (-half, -half, z), (half, -half, z), (half, half, z), (-half, half, z)
    return [(a, b, c), (a, c, d)] if facing else [(a, c, b), (a, d, c)]


def where(point, projection=PERSPECTIVE) -> tuple[float, float]:
    """Forward: a camera-space point's place in the picture (u, v from the top left)."""
    clip = np.array([*point, 1.0]) @ np.array(projection).reshape(4, 4)
    ndc = clip[:2] / clip[3]
    return (ndc[0] + 1) / 2, (1 - ndc[1]) / 2


def test_a_pixel_comes_back_on_the_surface_the_list_drew_there():
    ram, at = scene().modes().triangles(*square(-1500)).end()
    walked = pick.walk(ram, at)
    for target in ([0, 0, -1500], [400, -250, -1500], [-900, 500, -1500]):
        u, v = where(target)
        assert pick.nearest(walked, u, v) == pytest.approx(target, abs=ROUNDING)


def test_the_nearest_depth_writing_surface_wins_and_what_writes_no_depth_is_seen_through():
    nearer = [(-200, -200, -800), (200, -200, -800), (200, 200, -800)]
    ram, at = (scene().modes().triangles(*square(-2000))
               .modes(render=0).triangles(*square(-500))          # translucent: tested, never written
               .modes().triangles(nearer).end())
    walked = pick.walk(ram, at)
    assert pick.nearest(walked, *where([100, -100, -800]))[2] == pytest.approx(-800)
    assert pick.nearest(walked, *where([-1000, 900, -2000]))[2] == pytest.approx(-2000)
    ram, at = scene().modes(geometry=0).triangles(*square(-500)).modes().triangles(*square(-2000)).end()
    assert pick.nearest(pick.walk(ram, at), 0.5, 0.5)[2] == pytest.approx(-2000)   # z-buffer off: no depth


def test_culled_faces_are_not_there_and_unculled_backs_are():
    back = square(-500, facing=False)
    ram, at = scene().modes().triangles(*back, *square(-2000)).end()
    assert pick.nearest(pick.walk(ram, at), 0.5, 0.5)[2] == pytest.approx(-2000)     # back face culled
    ram, at = scene().modes(geometry=G_ZBUFFER).triangles(*back, *square(-2000)).end()
    assert pick.nearest(pick.walk(ram, at), 0.5, 0.5)[2] == pytest.approx(-500)      # no culling: drawn
    ram, at = scene().modes(geometry=G_ZBUFFER | G_CULL_FRONT).triangles(*square(-500), *square(-2000)).end()
    assert pick.nearest(pick.walk(ram, at), 0.5, 0.5) is None                         # front faces culled


def test_sky_the_hud_and_past_the_far_plane_are_nothing_to_focus_on():
    ram, at = scene().modes().triangles([(-100, -100, -1000), (100, -100, -1000), (100, 100, -1000)]).end()
    assert pick.nearest(pick.walk(ram, at), 0.05, 0.05) is None                      # sky beside it
    ram, at = scene().modes().triangles(*square(-20000, half=30000)).end()
    assert pick.nearest(pick.walk(ram, at), 0.5, 0.5) is None                         # beyond far (12800)
    ortho = [1 / 160, 0, 0, 0, 0, 1 / 120, 0, 0, 0, 0, -1 / 10, 0, 0, 0, 0, 1]
    ram, at = (FakeList().viewport().matrix(ortho, projection=True).matrix(IDENTITY).modes()
               .triangles(*square(-5, half=500)).end())
    walked = pick.walk(ram, at)
    assert walked.projection is None and pick.nearest(walked, 0.5, 0.5) is None       # a HUD is not the world


def test_the_modelview_stack_places_each_object_and_pops_back():
    moved = [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 300, 0, -1000, 1]          # row vectors: translate (300, 0, -1000)
    little = [(-50, -50, 0), (50, -50, 0), (50, 50, 0)]
    ram, at = (scene().modes().matrix(moved, push=True).triangles(little).pop()
               .triangles([(-50, -50, -1000), (50, -50, -1000), (50, 50, -1000)]).end())
    walked = pick.walk(ram, at)
    assert pick.nearest(walked, *where([310, -10, -1000])) == pytest.approx([310, -10, -1000], abs=ROUNDING)
    assert pick.nearest(walked, *where([10, -10, -1000])) == pytest.approx([10, -10, -1000], abs=ROUNDING)


def test_a_rolled_and_narrowed_projection_is_undone_exactly():
    angle = math.radians(20)
    roll = [math.cos(angle), math.sin(angle), 0, 0, -math.sin(angle), math.cos(angle), 0, 0,
            0, 0, 1, 0, 0, 0, 0, 1]
    projection = perspective(30)
    combined = (np.array(roll).reshape(4, 4) @ np.array(projection).reshape(4, 4)).reshape(16).tolist()
    ram, at = (FakeList().viewport().matrix(projection, projection=True).matrix(roll, projection=True, load=False)
               .matrix(IDENTITY).modes().triangles(*square(-1200)).end())
    walked = pick.walk(ram, at)
    assert pick.fov_of(walked.projection) == pytest.approx(30, abs=1e-3)
    target = [150, 220, -1200]
    assert pick.nearest(walked, *where(target, combined)) == pytest.approx(target, abs=ROUNDING)


def test_a_recorded_closure_becomes_the_ram_the_walk_reads():
    ram, at = wall(900)
    words = np.frombuffer(bytes(ram[:at + 256]), ">u4")
    runs = [(0, at + 256)]
    rebuilt = pick.ram_from_runs(runs, words.astype("<u4").tobytes())       # Project64's word order
    assert pick.nearest(pick.walk(rebuilt, at), 0.5, 0.5) == pytest.approx([0, 0, -900], abs=ROUNDING)
    with pytest.raises(ValueError):
        pick.ram_from_runs([(8, 4)], b"")


def test_a_list_that_runs_off_or_never_ends_is_refused():
    with pytest.raises(ValueError):
        pick.walk(bytearray(64), 60)
    looping = bytearray(64)
    struct.pack_into(">II", looping, 0, 0x06010000, 0)                     # G_DL branch to itself
    with pytest.raises(ValueError):
        pick.walk(looping, 0)


def test_the_camera_state_writes_land_where_the_game_reads_them():
    lakitu, fov_state = 0x8033C698, 0x8033C5A0
    memory = bytearray(8 << 20)
    struct.pack_into(">B", memory, fov_state & 0xFFFFFF, 9)                 # an FOV function running
    struct.pack_into(">f", memory, (fov_state & 0xFFFFFF) + camera_state.FOV_SHAKE, 192.0)
    struct.pack_into(">h", memory, (lakitu & 0xFFFFFF) + camera_state.LAKITU_ROLL, 256)
    camera_state.place(memory, ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0]), lakitu, fov_state)
    assert struct.unpack_from(">3f", memory, (lakitu & 0xFFFFFF) + 0x80) == (1.0, 2.0, 3.0)
    assert struct.unpack_from(">3f", memory, (lakitu & 0xFFFFFF) + 0x8C) == (4.0, 5.0, 6.0)
    assert struct.unpack_from(">h", memory, (lakitu & 0xFFFFFF) + 0x7A) == (256,)             # the frame's own
    assert memory[fov_state & 0xFFFFFF] == 9
    camera_state.place(memory, ([1.0, 2.0, 3.0], [4.0, 5.0, 6.0], -224, 52.5), lakitu, fov_state)
    assert struct.unpack_from(">h", memory, (lakitu & 0xFFFFFF) + 0x7A) == (-224,)
    assert memory[fov_state & 0xFFFFFF] == 0                                 # no FOV function
    assert struct.unpack_from(">f", memory, (fov_state & 0xFFFFFF) + 4) == (52.5,)
    assert struct.unpack_from(">f", memory, (fov_state & 0xFFFFFF) + 0x10) == (0.0,)       # no shake
