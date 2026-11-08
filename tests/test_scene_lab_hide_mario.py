"""The Scene viewer's Mario switch (2026-11-08, from Xander's SM64Scripts): the stage's Mario left out of
every picture the lab redraws, the ghosts kept, live and in an export.

The write itself (camera_state.hide_mario: his object's graph node loses GRAPH_RENDER_ACTIVE, in the
sandbox's own RAM) is checked on bytes; the session over fake_viewer_host.py's evaluator, which names
the camera it was handed, so a test sees the switch reach the drawing; the export over its fake host.
Whether the game's code then skips him on the real ROM is the GPU check's (the round's report).
"""
import json
import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import camera_state  # noqa: E402
import fake_viewer_host  # noqa: E402
import scene_export  # noqa: E402
import scene_session  # noqa: E402
from test_scene_lab_stage_ghosts import Loader, config, recording  # noqa: E402,F401

MARIO_STATES, MARIO_OBJECT = 0x33B170, 0x340000
LAKITU, FOV = 0x33C000, 0x33D000


def _memory(obj=0x80000000 | MARIO_OBJECT, flags=0x0021):
    memory = bytearray(0x400000)
    struct.pack_into(">I", memory, MARIO_STATES + camera_state.MARIO_OBJ, obj)
    struct.pack_into(">H", memory, MARIO_OBJECT + camera_state.NODE_FLAGS, flags)
    return memory


def _flags(memory) -> int:
    return struct.unpack_from(">H", memory, MARIO_OBJECT + camera_state.NODE_FLAGS)[0]


def test_hiding_him_clears_his_nodes_render_flag_and_nothing_else():
    camera = ([0.0, 0.0, 0.0], [0.0, 100.0, 900.0], None, None)
    shown = _memory()
    camera_state.place(shown, camera, LAKITU, FOV, MARIO_STATES)
    assert _flags(shown) == 0x0021                                  # four items: he stays
    hidden = _memory()
    camera_state.place(hidden, (*camera, True), LAKITU, FOV, MARIO_STATES)
    assert _flags(hidden) == 0x0020                                 # only GRAPH_RENDER_ACTIVE goes
    changed = [at for at in range(0x330000, 0x350000) if shown[at] != hidden[at]]
    assert changed == [MARIO_OBJECT + camera_state.NODE_FLAGS + 1]
    nobody = _memory(obj=0)
    assert camera_state.hide_mario(nobody, MARIO_STATES) is False and _flags(nobody) == 0x0021


def _model(memory, ranges, extra_type=0x01B):
    """Mario's object's model: a root whose children ring holds a display list node and one level-of-detail
    node per range (the game's GEO_RENDER_RANGE), as his geo layout makes them."""
    root, first = 0x341000, 0x341100
    struct.pack_into(">I", memory, MARIO_OBJECT + camera_state.SHARED_CHILD, 0x80000000 | root)
    struct.pack_into(">I", memory, root + camera_state.NODE_CHILDREN, 0x80000000 | first)
    nodes = [first + 0x40 * k for k in range(len(ranges) + 1)]
    for k, node in enumerate(nodes):
        struct.pack_into(">h", memory, node, extra_type if k == 0 else camera_state.GRAPH_NODE_TYPE_LEVEL_OF_DETAIL)
        struct.pack_into(">I", memory, node + camera_state.NODE_NEXT, 0x80000000 | nodes[(k + 1) % len(nodes)])
        if k:
            struct.pack_into(">hh", memory, node + camera_state.LOD_MIN, *ranges[k - 1])
    return nodes[1:]


def _ranges(memory, nodes):
    return [struct.unpack_from(">hh", memory, node + camera_state.LOD_MIN) for node in nodes]


def test_every_moved_camera_draws_marios_most_detailed_model_at_any_distance():
    """Measured on the BoB package (2026-11-08, scene-cameras' CPU check): his 752-triangle model nearer than
    600 units, 514 to about 1600, 212 beyond. Now the nearest range covers every distance and the others
    none; whatever is not his model is untouched."""
    memory = _memory()
    nodes = _model(memory, [(600, 1600), (-2048, 600), (1600, 32767)])
    camera = ([0.0, 0.0, 0.0], [0.0, 100.0, 9000.0])
    before = bytearray(memory)
    camera_state.place(before, camera, LAKITU, FOV)                 # the camera alone
    camera_state.place(memory, camera, LAKITU, FOV, MARIO_STATES)
    assert _ranges(memory, nodes) == [(32767, 32767), (-32768, 32767), (32767, 32767)]
    changed = [at for at in range(0x330000, 0x350000) if memory[at] != before[at]]
    assert changed
    assert all(any(node + camera_state.LOD_MIN <= at < node + camera_state.LOD_BYTES for node in nodes) for at in changed)
    assert _flags(memory) == 0x0021                                 # still drawn
    # No Mario, or a model whose graph points nowhere: nothing changes, and a ring that never closes ends.
    assert camera_state.full_detail(_memory(obj=0), MARIO_STATES) == 0
    broken = _memory()
    nodes = _model(broken, [(-2048, 600), (600, 32767)])
    struct.pack_into(">I", broken, nodes[-1] + camera_state.NODE_NEXT, 0x80000000 | nodes[-1])   # a loop onto itself
    assert camera_state.full_detail(broken, MARIO_STATES) == 2


FREE = {"focus": [0.0, 160.0, 0.0], "position": [0.0, 560.0, 1500.0], "roll": 0, "fov": 45.0}


def _session(config, recording, evaluator):  # noqa: F811
    return scene_session.SceneSession.open(
        config, recording["package"], host_factory=scene_session.command_host(fake_viewer_host.command()),
        evaluator_factory=lambda package, rom: evaluator, encoder_factory=lambda: None)


def test_the_switch_reaches_the_drawing_of_every_camera(config, recording):  # noqa: F811
    """A free camera, the recorded camera (drawn again from its own pose) and a frame that cannot be
    redrawn, which keeps him and says so."""
    evaluator = fake_viewer_host.FakeEvaluator(no_scene=(0,))
    session = _session(config, recording, evaluator)
    try:
        header, _ = session.picture(2, FREE, "raw")
        assert "mario" not in header and len(evaluator.calls[-1][1]) == 4
        header = session.finish(session.begin(2, FREE, "raw", True, False))[0]
        assert header["mario"] is False and header["camera"] == "free"
        assert evaluator.calls[-1][1] == (FREE["focus"], FREE["position"], 0, 45.0, True)
        recorded = session.finish(session.begin(3, None, "raw", True, False))[0]
        pose = fake_viewer_host.FakeEvaluator.POSE
        assert recorded["camera"] == "free" and recorded["mario"] is False and recorded["reason"] is None
        assert recorded["camera_used"] == {key: pose[key] for key in ("focus", "position", "roll", "fov")}
        assert evaluator.calls[-1][1][-1] is True
        loading = session.finish(session.begin(0, None, "raw", True, False))[0]
        assert loading["camera"] == "recorded" and "mario" not in loading
        assert loading["reason"] == scene_session.MARIO_SHOWN
        with pytest.raises(ValueError, match="mario"):
            session.begin(2, FREE, "raw", True, "no")
    finally:
        session.close()


def test_a_recording_without_mario_exports_without_him_and_a_version_1_one_still_draws_him(config, recording, tmp_path):  # noqa: F811
    from test_scene_export import _app, _direct, _finished
    entries = [{"t_ms": k * 1000 / 60, "scene_frame": 1 + k, "camera": FREE, "camera_used": None, "hud": True,
                "ghosts": [], "mario": k % 2 == 0, "play_ms": None,
                "sound": {"music": True, "effects": True, "volume": 1.0, "muted": False, "ghost_mutes": []}}
               for k in range(4)]
    assert scene_export.picture_key(entries[0]) != scene_export.picture_key({**entries[0], "mario": False})
    with TestClient(_app(tmp_path, {"scene.sm64scene": recording["package"].read_bytes()}, recording["rom"],
                         Loader({}))) as http:
        opened = http.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        body = {"version": scene_export.TIMELINE_VERSION, "session": opened["session"], "timeline": entries,
                "fps": 60, "duration_ms": 4 * 1000 / 60}
        status = _finished(http, http.post("/api/scene-lab/export", json=body).json()["job"])
        old = {**body, "version": 1, "timeline": [{key: value for key, value in entry.items() if key != "mario"}
                                                  for entry in entries]}
        old_status = _finished(http, http.post("/api/scene-lab/export", json=old).json()["job"])
    assert status["state"] == "done" and old_status["state"] == "done", (status, old_status)
    made = json.loads(Path(status["path"]).read_text(encoding="utf-8"))["pictures"]
    drawn_old = json.loads(Path(old_status["path"]).read_text(encoding="utf-8"))["pictures"]
    expected = _direct_with_mario(config, recording, entries)
    assert made == expected
    assert drawn_old == _direct(config, recording, [{**entry, "mario": True} for entry in entries], Loader({}))
    assert made[0] == drawn_old[0] and made[1] != drawn_old[1]          # only the entries without him differ


def _direct_with_mario(config, recording, entries):  # noqa: F811
    session = _session(config, recording, fake_viewer_host.FakeEvaluator())
    try:
        return [session.finish(session.begin(entry["scene_frame"], entry["camera"], "raw", True, entry["mario"]))[0]["sha256"]
                for entry in entries]
    finally:
        session.close()
