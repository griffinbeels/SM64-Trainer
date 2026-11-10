"""The Scene viewer's step to the next action change (2026-11-08, from Xander's FrameAdvance): the lab's
half. Every frame's action of the stage's Mario comes from his path's reads (rig_path.read_poses, the
u32 gMarioStates.action beside his place), a stage ghost's from its clip's live_action in the row its
drawing shows, named in plain words by memory/addresses.py action_label; the socket's "actions"
question answers them once his path is read.

The page's half (which frame is a change, the row, [ and ]) is tests/frontend/scenepath.test.js. No
ROM, GPU or emulator: a synthetic recording whose scene state holds Mario's action and place, over
fake_viewer_host.py's host and evaluator.
"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import rig_path  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
import stage_ghosts  # noqa: E402
from ghost_native_fixture import frame, producer  # noqa: E402
from sm64_events.ghost.clip import STAR_CLOCK, encode_clip  # noqa: E402
from sm64_events.memory.addresses import ACT_DIVE, ACT_JUMP, ACT_JUMP_LAND, action_label  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402
from test_scene_lab_stage_ghosts import Loader  # noqa: E402

MOVES = [ACT_JUMP_LAND] * 3 + [ACT_JUMP] * 2 + [ACT_DIVE] * 3
POSES = [(100.0 * k, 50.0, -200.0, 0) for k in range(len(MOVES))]


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scene-actions")
    rom = bytearray(fixture.rom())
    rom[0x3E] = ord("E")
    rom = bytes(rom)
    session = fixture.write_session(folder, rom, [(1000 + k, 24, 1) for k in range(len(MOVES))],
                                    marios=[(action, 0) for action in MOVES], poses=POSES)
    out = folder / "scene.sm64scene"
    fixture.pk.write_package(fixture.pk.tap_source(session, "next", states=True), rom, out, fixture.pk.ZLIB)
    (folder / "rom.z64").write_bytes(rom)
    (folder / "MappingUS.map").write_text("", encoding="utf-8")
    return {"folder": folder, "rom": folder / "rom.z64", "package": out, "map": folder / "MappingUS.map"}


@pytest.fixture
def config(recording, tmp_path, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    (tmp_path / "Plugin").mkdir()
    return type("Config", (), {"rom": recording["rom"], "pj64_plugin": tmp_path / "Plugin", "renderer": "trainer",
                               "resolution": None, "map": recording["map"]})()


class Evaluators:
    def __init__(self, map_path):
        self.map_path = map_path

    def __call__(self, package, rom):
        return fake_viewer_host.FakeEvaluator()


def _session(config, recording):
    return scene_session.SceneSession.open(config, recording["package"],
                                           host_factory=scene_session.command_host(fake_viewer_host.command()),
                                           evaluator_factory=Evaluators(recording["map"]),
                                           path_reader_factory=rig_path.InProcessReader)


def ghost_clip(actions) -> bytes:
    """A ghost with one row a game frame, each with its own action."""
    return encode_clip([frame(index + 1, elapsed=index + 1, igt=index + 1, level=24, area=1, action=action)
                        for index, action in enumerate(actions)], producer(), clock_kind=STAR_CLOCK)


def test_the_path_reader_reads_marios_action_beside_his_place(recording):
    package = fixture.pk.Package(recording["package"].read_bytes(), recording["rom"].read_bytes())
    rows = rig_path.read_poses(package, list(range(len(MOVES))), layout_for("us").mario_struct & 0x1FFFFFFF)
    assert rows.shape == (len(MOVES), rig_path.ROW)
    assert rows[:, 4].astype(np.int64).tolist() == MOVES
    assert rows[:, 0].tolist() == [pose[0] for pose in POSES]


def test_the_stage_and_a_ghost_answer_every_frames_action_in_plain_words(config, recording):
    session = _session(config, recording)
    try:
        session.prepare_path().result()
        answer = session.actions("stage")
        assert answer["reason"] is None and answer["actions"] == MOVES
        assert answer["names"] == {str(code): action_label(code) for code in sorted(set(MOVES))}
        assert answer["names"][str(ACT_DIVE)] == "dive" and answer["names"][str(ACT_JUMP_LAND)] == "jump land"
        # A ghost: its own clip's action in the row its drawing shows (its first frame on the scene's first).
        ghost = [ACT_JUMP, ACT_JUMP, ACT_DIVE, ACT_DIVE, ACT_JUMP_LAND]
        session.set_ghosts([{"ref": "a", "label": "Sully"}], Loader({"a": (ghost_clip(ghost), "Sully")}))
        assert session.actions("a")["actions"] == ghost + [ACT_JUMP_LAND] * (len(MOVES) - len(ghost))   # held at its end
        assert session.actions("nobody") == {"actions": [], "names": {}, "reason": stage_ghosts.VIEW_GONE}
    finally:
        session.close()


def test_a_path_camera_aimed_at_mario_looks_at_his_eyes_on_his_path(config, recording):
    """A camera path's key can aim at a runner (the lead's correction, 2026-11-08): {..., "aim": {"stage": w}}
    has its focus moved that share of the way to his eyes on his lightly smoothed path, the point a
    cinematic camera aims at (here his straight run: exactly his place, eye height up). A ghost not in
    the scene keeps the camera's own focus and says why; weights that sum past 1 are refused."""
    session = _session(config, recording)
    try:
        session.prepare_path().result()
        eye = session._shipped_rig_tuning()["marioHeight"][0]
        own = {"focus": [0.0, 0.0, 0.0], "position": [0.0, 300.0, 1200.0], "roll": 0, "fov": 45.0}
        for position in (2, 4):
            header, _ = session.picture(position, {**own, "aim": {"stage": 1.0}}, "raw")
            x, y, z, _yaw = POSES[position]
            assert header["camera_used"]["focus"] == pytest.approx([x, y + eye, z]) and header["reason"] is None
            assert header["camera_used"]["position"] == own["position"]
        half, _ = session.picture(4, {**own, "aim": {"stage": 0.5}}, "raw")
        assert half["camera_used"]["focus"] == pytest.approx([POSES[4][0] / 2, (POSES[4][1] + eye) / 2, POSES[4][2] / 2])
        gone, _ = session.picture(4, {**own, "aim": {"nobody": 1.0}}, "raw")
        assert gone["camera_used"]["focus"] == own["focus"] and gone["reason"] == stage_ghosts.VIEW_GONE
        with pytest.raises(ValueError, match="aim"):
            session.picture(4, {**own, "aim": {"stage": 0.8, "nobody": 0.8}}, "raw")
    finally:
        session.close()


def test_the_route_answers_every_frames_action_once_his_path_is_read(config, recording, tmp_path):
    (tmp_path / "packages").mkdir()
    (tmp_path / "packages" / "scene.sm64scene").write_bytes(recording["package"].read_bytes())
    lab = tmp_path / "lab.json"
    lab.write_text(json.dumps({"rom": str(recording["rom"]), "packages": str(tmp_path / "packages"),
                               "pj64_plugin": str(config.pj64_plugin), "map": str(recording["map"])}), encoding="utf-8")
    from fastapi import FastAPI
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, lab, watch_seconds=0.05, ghost_loader=Loader({}), session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command()),
        "evaluator_factory": Evaluators(recording["map"]), "path_reader_factory": rig_path.InProcessReader})
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
        answer = client.get(f"/api/scene-lab/actions?session={opened['session']}&target=stage").json()
        assert answer["target"] == "stage" and answer["reason"] is None
        assert answer["actions"] == MOVES and answer["names"][str(ACT_JUMP)] == "jump"
        assert client.get(f"/api/scene-lab/actions?session={opened['session']}&target=").status_code == 422
        closed = client.get(f"/api/scene-lab/actions?session={opened['session'] + 1}&target=stage")
        assert closed.status_code == 409                                    # not the open scene
