"""The Scene viewer's camera paths (2026-11-08): the store (tools/scene_lab/camera_paths.py), its two
routes (GET and PUT /api/scene-lab/camera-paths?level=&area=), and the lab's half of a path that eases
into or out of the recorded camera (scene_session.recorded_blend): a free camera with "recorded":
weight is drawn that share of the way to the frame's recorded camera, live and in an export alike.

The page's half (what a key means, the curve between keys, the path's clock) is
tests/frontend/scenepath.test.js; test_the_path_in_jsdom runs it. No ROM, GPU or emulator: the lab
config names a lab folder under tmp_path, and the export test draws through fake_viewer_host.py.
"""
import json
import math
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import camera_paths  # noqa: E402
import fake_viewer_host  # noqa: E402
import scene_session  # noqa: E402
from frontend_runner import run_frontend  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402
from test_scene_lab_stage_ghosts import config, recording  # noqa: E402,F401

WIDE = {"focus": [100.0, 200.0, -300.0], "position": [100.0, 900.0, 1700.0], "roll": 0, "fov": 45.0}
LOW = {"focus": [-50.0, 0.0, 10.0], "position": [400.0, 20.0, 10.0], "roll": -1200, "fov": 30.5}


def key(frame, camera=WIDE, ease=True, hold=0.0, speed=1, aim=None):
    return {"frame": frame, "camera": camera, "ease": ease, "hold": hold, "speed": speed, "aim": aim}


def test_the_path_in_jsdom():
    run_frontend("scenepath.test.js")


@pytest.fixture
def store(tmp_path):
    return camera_paths.CameraPaths(tmp_path / camera_paths.FILE_NAME)


def test_each_level_and_area_keeps_its_own_path_across_a_restart(store, tmp_path):
    first = [key(3, WIDE, aim="stage"), key(40, None, ease=False), key(40, LOW, hold=2000.0, aim="ghost:4"),
             key(90, LOW, speed=0.25)]
    store.replace(9, 1, first)
    store.replace(9, 2, [key(1, LOW)])
    again = camera_paths.CameraPaths(tmp_path / camera_paths.FILE_NAME)     # the next server
    assert again.keys(9, 1) == {"keys": first, "problem": None}            # in the page's order, holds kept
    assert again.keys(9, 2)["keys"] == [key(1, LOW)]
    assert again.keys(24, 1) == {"keys": [], "problem": None}
    again.replace(9, 2, [])
    document = json.loads((tmp_path / camera_paths.FILE_NAME).read_text(encoding="utf-8"))
    assert document == {"version": 1, "areas": {"9:1": first}}
    # A key saved before keys could aim reads back looking at its own point.
    older = {name: value for name, value in key(5).items() if name != "aim"}
    assert camera_paths.checked_keys([older]) == [key(5)]


@pytest.mark.parametrize(("keys", "words"), [
    ([key(1, {"focus": [0, 0, 0], "position": [0, 0, 0]})], "sits on its own focus point"),
    ([{**key(1), "extra": 1}], "Each key is"),
    ([key(-1)], "frame"),
    ([key(1, speed=0.3)], "speed"),
    ([key(1, speed=True)], "speed"),
    ([key(1, hold=-5)], "holds"),
    ([key(1, hold=600000)], "holds"),
    ([key(1, ease="yes")], "ease"),
    ([key(1, aim="")], "aims"),
    ([key(1, aim=7)], "aims"),
    ([key(n) for n in range(65)], "at most 64"),
])
def test_a_path_it_refuses_names_why_and_changes_nothing(store, tmp_path, keys, words):
    with pytest.raises(camera_paths.PathError, match=words):
        store.replace(9, 1, keys)
    assert not (tmp_path / camera_paths.FILE_NAME).exists()


def test_a_file_it_cannot_read_answers_empty_with_a_sentence_and_is_kept_aside(store, tmp_path):
    (tmp_path / camera_paths.FILE_NAME).write_text("{oops", encoding="utf-8")
    assert store.keys(9, 1) == {"keys": [], "problem": camera_paths.UNREADABLE}
    store.replace(9, 1, [key(2)])
    assert (tmp_path / ("camera-paths" + camera_paths.BAD_SUFFIX)).read_text(encoding="utf-8") == "{oops"
    assert store.keys(9, 1) == {"keys": [key(2)], "problem": None}


@pytest.fixture
def client(tmp_path):
    lab_root = tmp_path / "lab-root"
    config_file = tmp_path / "lab.json"
    config_file.write_text(json.dumps({"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
                                       "lab_root": str(lab_root)}), encoding="utf-8")
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, config_file)
    with TestClient(app) as test_client:
        yield test_client, lab_root


def test_the_routes_put_and_get_one_areas_path_in_the_lab_folder(client):
    http, lab_root = client
    assert http.get("/api/scene-lab/camera-paths?level=9&area=1").json() == {"keys": [], "problem": None}
    put = http.put("/api/scene-lab/camera-paths?level=9&area=1", json={"keys": [key(2), key(9, None)]})
    assert put.status_code == 200 and put.json() == {"keys": [key(2), key(9, None)], "problem": None}
    assert http.get("/api/scene-lab/camera-paths?level=9&area=1").json()["keys"] == [key(2), key(9, None)]
    assert http.get("/api/scene-lab/camera-paths?level=9&area=2").json()["keys"] == []
    assert json.loads((lab_root / camera_paths.FILE_NAME).read_text(encoding="utf-8"))["areas"] == {
        "9:1": [key(2), key(9, None)]}
    refused = http.put("/api/scene-lab/camera-paths?level=9&area=1", json={"keys": [key(2, speed=3)]})
    assert refused.status_code == 422 and "speed" in refused.text
    assert http.get("/api/scene-lab/camera-paths?level=9&area=1").json()["keys"] == [key(2), key(9, None)]


# ------------------------------------------------------------------ the recorded camera's blend

POSE = fake_viewer_host.FakeEvaluator.POSE       # every frame's recorded camera in the fake evaluator


def test_a_blend_moves_place_aim_roll_and_lens_that_share_of_the_way():
    toward = {"focus": [100.0, 0.0, 0.0], "position": [100.0, 0.0, 1000.0], "roll": -32000, "fov": 10.0}
    start = {"focus": [0.0, 0.0, 0.0], "position": [0.0, 0.0, 1000.0], "roll": 32000, "fov": 90.0}
    half = scene_session.blend_cameras(start, toward, 0.5)
    assert half["focus"] == [50.0, 0.0, 0.0] and half["position"] == [50.0, 0.0, 1000.0]
    assert abs(half["roll"]) > 32000                                       # the short way round, through 32768
    tan = lambda fov: math.tan(math.radians(fov) / 2)  # noqa: E731
    assert math.isclose(tan(half["fov"]) ** 2, tan(90) * tan(10))          # evenly in log(tan)
    assert scene_session.blend_cameras(start, toward, 0.0) == {**start}
    assert scene_session.blend_cameras(start, toward, 1.0)["position"] == toward["position"]
    own_lens = scene_session.blend_cameras({**start, "fov": None, "roll": None}, toward, 0.25)
    assert math.isclose(own_lens["fov"], 10.0) and own_lens["roll"] == -32000   # a missing one takes the other's


def _session(config, recording, evaluator):  # noqa: F811
    return scene_session.SceneSession.open(
        config, recording["package"], host_factory=scene_session.command_host(fake_viewer_host.command()),
        evaluator_factory=lambda package, rom: evaluator, encoder_factory=lambda: None)


def test_the_lab_draws_a_path_camera_blended_with_the_recorded_one(config, recording):  # noqa: F811
    """The page sends {..., "recorded": weight}; the lab draws that camera moved toward the frame's own
    pose, says so in camera_used, and draws weight 0 as the camera itself. A weight outside 0 to 1 is
    refused."""
    evaluator = fake_viewer_host.FakeEvaluator()
    session = _session(config, recording, evaluator)
    try:
        header, _ = session.picture(2, {**LOW, "recorded": 0.25})
        expected = scene_session.blend_cameras(LOW, POSE, 0.25)
        assert header["camera"] == "free" and header["camera_used"] == expected and header["reason"] is None
        assert evaluator.calls[-1][1] == (expected["focus"], expected["position"], expected["roll"], expected["fov"])
        header, _ = session.picture(2, {**LOW, "recorded": 0})
        assert header["camera_used"] == {key: LOW[key] for key in ("focus", "position", "roll", "fov")}
        with pytest.raises(ValueError, match="recorded weight"):
            session.picture(2, {**LOW, "recorded": 1.5})
    finally:
        session.close()


def test_a_recording_of_a_path_exports_the_same_cameras(config, recording, tmp_path):  # noqa: F811
    """A scene recording made while the camera path played carries each display frame's camera (the page's
    half: scenepath.test.js), blends with the recorded camera included; the export draws every entry
    from that camera, exactly as the viewer's own picture request of it."""
    from test_scene_export import _app, _body, _direct, _finished
    from test_scene_lab_stage_ghosts import Loader
    moments = [(1, LOW), (1, {**LOW, "position": [410.0, 25.0, 12.0]}), (2, {**WIDE, "recorded": 0.4}),
               (3, {**WIDE, "recorded": 0.9}), (3, {**WIDE, "aim": {"stage": 0.6}, "recorded": 0.2}), (4, None)]
    entries = [{"t_ms": k * 1000 / 60, "scene_frame": frame, "camera": camera, "camera_used": None, "hud": True,
                "ghosts": [], "play_ms": None, "sound": {"music": True, "effects": True, "volume": 1.0, "muted": False,
                                                        "ghost_mutes": []}}
               for k, (frame, camera) in enumerate(moments)]
    with TestClient(_app(tmp_path, {"scene.sm64scene": recording["package"].read_bytes()}, recording["rom"],
                         Loader({}))) as http:
        opened = http.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        job = http.post("/api/scene-lab/export", json=_body(opened, entries, duration_ms=len(entries) * 1000 / 60)).json()["job"]
        status = _finished(http, job)
    assert status["state"] == "done", status
    made = json.loads(Path(status["path"]).read_text(encoding="utf-8"))
    expected = _direct(config, recording, entries, Loader({}))
    assert made["pictures"] == expected and len(set(expected)) == len(entries)   # each camera its own picture
