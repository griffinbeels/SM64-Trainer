"""A ghost's view in the scene viewer (round 5, item 5): the stage's world from the camera a scene
ghost's own recording drew with, at the row its drawing shows in that picture.

His words: "we need one button per active ghost in the scene to follow their recorded POV." The
clips here are real portable clips (tests/ghost_native_fixture.py records, clip.encode_clip) whose
rows carry distinct cameras; the session runs over fake_viewer_host.py's host and evaluator, which
record the camera each picture was drawn from. Whether the renderer draws that camera's picture as
the ghost's own game did is the GPU check's (the round's report).
"""
import json
import struct
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import scene_session  # noqa: E402
import stage_ghosts  # noqa: E402
from ghost_native_fixture import frame, producer  # noqa: E402
from sm64_events.ghost.clip import STAR_CLOCK, decode_clip, encode_clip  # noqa: E402
from sm64_events.ghost.native_layout import CAMERA_AT  # noqa: E402
from test_scene_lab_stage_ghosts import TIMERS, Loader, _lab, _picture_header, config, recording  # noqa: E402,F401

LEVEL = 24                                   # the fixture recording's level and area (scene_attempt_fixture)


def row(sequence: int, x: float, *, elapsed: int | None = None, area: int = 1, camera: bool = True,
        roll: int = 0, screen_roll: int = 0, fov: float = 45.0) -> bytes:
    """One portable record whose camera stands at x (its focus below it), or none."""
    raw = bytearray(frame(sequence, elapsed=elapsed, igt=elapsed, level=LEVEL, area=area, valid=31 if camera else 27))
    if camera:
        struct.pack_into("<6f2hf", raw, CAMERA_AT, x, 300, 900, x, 100, 0, roll, screen_roll, fov)
    return bytes(raw)


def clip(rows) -> bytes:
    return encode_clip(rows, producer(), clock_kind=STAR_CLOCK)


def camera_x(camera) -> float | None:
    return None if camera is None else camera["position"][0]


def test_the_clip_decoder_reads_each_rows_camera():
    raw = clip([row(1, 10, roll=7, screen_roll=-1200, fov=52.5), row(2, 0, camera=False)])
    first, second = decode_clip(raw).frames
    assert first.camera == {"position": [10.0, 300.0, 900.0], "focus": [10.0, 100.0, 0.0], "roll": 7,
                            "screen_roll": -1200, "fov": 52.5}
    assert second.camera is None
    # The free camera takes the screen roll (gLakituState.roll, which it replaces), never the look-at roll.
    view, reason = stage_ghosts.GhostCameras(raw).at(0, (LEVEL, 1))
    assert view == {"focus": [10.0, 100.0, 0.0], "position": [10.0, 300.0, 900.0], "roll": -1200, "fov": 52.5}
    assert reason is None


def test_each_picture_takes_the_row_its_drawing_shows_and_falls_back_with_a_reason():
    """Row coordinates 1, 2, 3 (no camera), 5 (game frame 4 never recorded), 6 (another area), 7:
    frame k is the row whose coordinate is the first's plus k, as ghost_viewer.cpp choose finds it,
    not the k-th row; a frame whose row has no camera that applies shows the last one that does."""
    cameras = stage_ghosts.GhostCameras(clip([
        row(1, 0), row(2, 10), row(3, 20, camera=False), row(4, 40, elapsed=5), row(5, 50, elapsed=6, area=2),
        row(6, 60, elapsed=7)]))
    seen = {k: cameras.at(k, (LEVEL, 1)) for k in range(-2, 9)}
    assert {k: camera_x(view) for k, (view, _reason) in seen.items()} == {
        -2: 0, -1: 0, 0: 0, 1: 10, 2: 10, 3: 10, 4: 40, 5: 40, 6: 60, 7: 60, 8: 60}
    last, gap = stage_ghosts.VIEW_LAST, stage_ghosts.VIEW_GAP
    assert {k: reason for k, (_view, reason) in seen.items()} == {
        -2: stage_ghosts.VIEW_BEFORE + stage_ghosts.VIEW_FIRST, -1: stage_ghosts.VIEW_BEFORE + stage_ghosts.VIEW_FIRST,
        0: None, 1: None, 2: gap + last, 3: gap + last, 4: None, 5: stage_ghosts.VIEW_ELSEWHERE + last, 6: None,
        7: stage_ghosts.VIEW_ENDED + last, 8: stage_ghosts.VIEW_ENDED + last}
    # In the other area its own camera applies, and before it only the area's later one does.
    assert camera_x(cameras.at(5, (LEVEL, 2))[0]) == 50
    assert cameras.at(1, (LEVEL, 2)) == (cameras.at(5, (LEVEL, 2))[0], stage_ghosts.VIEW_ELSEWHERE + stage_ghosts.VIEW_NEXT)
    # An unknown stage place takes any row's camera.
    assert camera_x(cameras.at(5, None)[0]) == 50


def test_a_recording_without_a_camera_in_this_area_shows_the_recorded_camera():
    assert stage_ghosts.GhostCameras(clip([row(1, 0, camera=False), row(2, 0, camera=False)])).at(0, (LEVEL, 1)) \
        == (None, stage_ghosts.VIEW_NONE)
    assert stage_ghosts.GhostCameras(clip([row(1, 0, area=2)])).at(0, (LEVEL, 1)) == (None, stage_ghosts.VIEW_NONE)
    assert stage_ghosts.GhostCameras(b"not a clip").at(0, (LEVEL, 1)) == (None, stage_ghosts.VIEW_NONE)


GHOST = clip([row(index + 1, 100.0 * index) for index in range(10)])


def _session(config, recording):
    evaluators = []

    def made(package, rom):
        evaluators.append(fake_viewer_host.FakeEvaluator())
        return evaluators[-1]
    host = scene_session.command_host(fake_viewer_host.command())
    session = scene_session.SceneSession.open(config, recording["package"], host_factory=host, evaluator_factory=made)
    return session, evaluators


def test_a_ghosts_view_draws_its_camera_at_the_ghost_frame_of_each_picture(config, recording):
    """Lag frames and held pictures: picture p shows the ghost's row TIMERS[p] - TIMERS[0], the
    same count its drawing uses (_ghost_block), drawn through the free camera's path."""
    session, evaluators = _session(config, recording)
    try:
        session.set_ghosts([{"ref": "a", "label": "Sully"}], Loader({"a": (GHOST, "Sully")}))
        for position in range(len(session.frames)):
            header, _ = session.picture(position, {"ghost": "a"}, "raw")
            expected = 100.0 * (TIMERS[position] - TIMERS[0])
            assert header["camera"] == "free" and header["reason"] is None
            assert header["view"]["ghost"] == "a" and header["view"]["camera"]["position"][0] == expected
            index, drawn = evaluators[0].calls[-1]
            assert index == session.frames[position]
            assert drawn == ([expected, 100.0, 0.0], [expected, 300.0, 900.0], 0, 45.0)
        # The header's camera, sent back as a free camera, is the very camera the game's code just
        # ran for (the session reuses that view, as it does for an unchanged camera): Free camera
        # starts from a ghost's view without a jump.
        ghost_view, _ = session.picture(3, {"ghost": "a"}, "raw")
        asked = len(evaluators[0].calls)
        free_view, _ = session.picture(3, ghost_view["view"]["camera"], "raw")
        assert len(evaluators[0].calls) == asked and "view" not in free_view
        assert session._last_view[:2] == (session.frames[3], evaluators[0].calls[-1][1])
    finally:
        session.close()


def test_a_view_that_cannot_be_drawn_shows_the_recorded_camera_and_says_why(config, recording):
    session, _evaluators = _session(config, recording)
    try:
        session.set_ghosts([{"ref": "a", "label": "Sully"}], Loader({"a": (GHOST, "Sully")}))
        recorded, _ = session.picture(1, None, "raw")
        gone, _ = session.picture(1, {"ghost": "nobody"}, "raw")
        assert gone["camera"] == "recorded" and gone["reason"] == stage_ghosts.VIEW_GONE
        assert gone["view"] == {"ghost": "nobody", "camera": None} and gone["sha256"] == recorded["sha256"]
        session.ghost_origin = session.frames[2]                 # every picture before it is before the start
        early, _ = session.picture(0, {"ghost": "a"}, "raw")
        assert early["view"]["camera"]["position"][0] == 0.0
        assert early["reason"] == stage_ghosts.VIEW_BEFORE + stage_ghosts.VIEW_FIRST
        for wrong in ({"ghost": 7}, {"ghost": ""}, {"ghost": "a", "focus": [0, 0, 0]}):
            with pytest.raises(ValueError):
                session.picture(1, wrong, "raw")
    finally:
        session.close()


def test_without_the_free_camera_a_ghosts_view_is_the_recorded_picture_with_its_reason(config, recording):
    host = scene_session.command_host(fake_viewer_host.command())
    session = scene_session.SceneSession.open(config, recording["package"], host_factory=host)   # map None
    try:
        session.set_ghosts([{"ref": "a", "label": "Sully"}], Loader({"a": (GHOST, "Sully")}))
        header, _ = session.picture(1, {"ghost": "a"}, "raw")
        assert header["camera"] == "recorded" and header["view"] == {"ghost": "a", "camera": None}
        assert header["reason"] == session.free_reason and "address map" in header["reason"]
    finally:
        session.close()


def test_the_socket_asks_for_a_ghosts_view_in_one_picture_request(config, recording, tmp_path):
    """Interface: {"camera": {"ghost": ref}} through the lab's real routes, one round trip per picture."""
    app = _lab(config, recording, tmp_path, Loader({"a": (GHOST, "Sully")}))
    app.state.scene_lab.session_options["evaluator_factory"] = lambda package, rom: fake_viewer_host.FakeEvaluator()
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": opened["session"], "kind": "ghosts", "set": [{"ref": "a"}]})
            assert json.loads(socket.receive()["text"])["shown"] == ["a"]
            socket.send_json({"id": 2, "session": opened["session"], "frame": 2, "camera": {"ghost": "a"},
                              "encoding": "raw"})
            header = _picture_header(socket)
            assert header["id"] == 2 and header["camera"] == "free" and header["reason"] is None
            assert header["view"]["ghost"] == "a"
            assert header["view"]["camera"]["position"][0] == 100.0 * (TIMERS[2] - TIMERS[0])
            socket.send_json({"id": 3, "session": opened["session"], "frame": 2, "camera": {"ghost": 3},
                              "encoding": "raw"})
            assert "error" in json.loads(socket.receive()["text"])
