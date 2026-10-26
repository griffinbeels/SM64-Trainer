"""Attempt scenes through the lab's routes (server/scene_lab_api.py, tools/scene_lab/attempt_scenes.py).

The lab-only API: GET /api/scene-lab carries "live", GET /api/scene-lab/attempt-scenes?ids= says
which attempts have a scene, POST /api/scene-lab/open {"attempt": id} opens one. The cutter is a
double that records the notes it is sent, except in the end-to-end test, where the real child
(attempt_cutter.py) cuts scene_attempt_fixture.py's synthetic session through the spool double
and the viewer opens the result with fake_viewer_host.py's renderer. No ROM, RAM image, GPU,
emulator or live recording is involved.
"""
import asyncio
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import attempt_scenes  # noqa: E402
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import package  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
from scene_spool_double import CONFIG as DOUBLE_CONFIG  # noqa: E402
from scene_spool_double import RecordedSpool  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402
from sm64_events.server.broadcaster import Broadcaster  # noqa: E402

T0 = datetime(2026, 10, 26, 20, 16, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    folder = tmp_path_factory.mktemp("attempt-scenes")
    rom = fixture.rom()
    places = [(5000 + index, 9 if index < 300 else 6, 1) for index in range(400)]
    session = fixture.write_session(folder, rom, places)
    scene = folder / "scene.sm64scene"
    package.write_package(package.tap_source(session, "next", states=True), rom, scene, package.ZLIB)
    return {"rom": rom, "session": session, "scene": scene.read_bytes()}


@pytest.fixture
def lab(tmp_path, recording, monkeypatch):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    (tmp_path / "packages").mkdir()
    (tmp_path / "Plugin").mkdir()
    (tmp_path / "rom.z64").write_bytes(recording["rom"])
    root = tmp_path / "lab"
    root.mkdir()
    config = {"rom": str(tmp_path / "rom.z64"), "packages": str(tmp_path / "packages"),
              "pj64_plugin": str(tmp_path / "Plugin"), "map": str(tmp_path / "MappingUS.map"), "lab_root": str(root)}
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return {"config": path, "root": root, "folder": root / "attempts"}


class FakeLease:
    made: list = []

    def __init__(self, lab_root):
        self.lab_root, self.running = lab_root, False
        FakeLease.made.append(self)

    def start(self):
        self.running = True

    def stop(self):
        self.running = False


class FakeCutter:
    def __init__(self, folder, on_event):
        self.folder, self.on_event, self.notes, self.closed = folder, on_event, [], False

    def send(self, note):
        self.notes.append(note)

    def close(self):
        self.closed = True

    def ready(self, attempt_id: int, data: bytes, seconds: float = 9.7) -> Path:
        path = self.folder / f"{attempt_id}.sm64scene"
        path.write_bytes(data)
        self.on_event({"id": attempt_id, "state": "ready", "seconds": seconds, "path": str(path)})
        return path


def _attempt(attempt_id: int, ended_seconds: float, *, anchor=5020, closed=5150, outcome="success"):
    return SimpleNamespace(id=attempt_id, anchor_frame=anchor, closed_frame=closed, outcome=outcome,
                           started_utc=(T0 + timedelta(seconds=ended_seconds - 5)).isoformat(),
                           ended_utc=(T0 + timedelta(seconds=ended_seconds)).isoformat())


def _tracker():
    calls = []
    return SimpleNamespace(on_replay_history=lambda *args, **options: calls.append((args, options)),
                           broadcaster=Broadcaster(), calls=calls)


def _app(lab, **options):
    app = FastAPI()
    cutters = []

    def cutter(folder, on_event):
        cutters.append(FakeCutter(folder, on_event))
        return cutters[-1]

    host = scene_session.command_host(fake_viewer_host.command())
    defaults = {"live": True, "lease_factory": FakeLease, "cutter_factory": cutter,
                "session_options": {"host_factory": host,
                                    "evaluator_factory": lambda package, rom: fake_viewer_host.FakeEvaluator()}}
    server = scene_lab_api.mount_scene_lab(app, lab["config"], **(defaults | options))
    return app, server, cutters


def _scenes(client, *ids) -> dict:
    return client.get("/api/scene-lab/attempt-scenes", params={"ids": ",".join(map(str, ids))}).json()["scenes"]


def test_the_probe_says_whether_the_live_recording_runs(lab):
    app, server, cutters = _app(lab)
    with TestClient(app) as client:
        assert client.get("/api/scene-lab").json() == {
            "lab": "scene-viewer", "live": {"recording": False, "reason": attempt_scenes.STARTING}}
        cutters[0].on_event({"live": {"recording": True, "reason": None}})
        assert client.get("/api/scene-lab").json()["live"] == {"recording": True, "reason": None}
        assert FakeLease.made[-1].running and FakeLease.made[-1].lab_root == lab["root"]
    assert not FakeLease.made[-1].running and cutters[0].closed
    off, _server, none = _app(lab, live=False)
    with TestClient(off) as client:
        assert client.get("/api/scene-lab").json()["live"] == {
            "recording": False, "reason": "This server does not run the live scene recording."}
    assert none == []


def test_each_completed_attempt_is_cutting_at_once_and_the_replay_still_gets_its_call(lab, recording):
    app, server, cutters = _app(lab)
    tracker = _tracker()
    replay_hook = tracker.on_replay_history
    server.follow_attempts(tracker)
    with TestClient(app) as client:
        first, second = _attempt(12, 10), _attempt(13, 20, outcome="reset")
        tracker.on_replay_history([first, second], "2026-10-26T20:16:30Z")
        tracker.on_replay_history([], None, replace=True)
        assert tracker.calls == [(([first, second], "2026-10-26T20:16:30Z"), {}), (([], None), {"replace": True})]
        assert replay_hook is not tracker.on_replay_history
        assert [note["attempt"]["id"] for note in cutters[0].notes] == [12, 13]
        assert cutters[0].notes[0]["attempt"]["anchor_frame"] == 5020
        assert _scenes(client, 12, 13, 99) == {"12": {"state": "cutting"}, "13": {"state": "cutting"}}
        cutters[0].ready(12, recording["scene"])
        cutters[0].on_event({"id": 13, "state": "unavailable",
                             "reason": "The scene recording lost 2 pictures during this attempt."})
        assert _scenes(client, 12, 13) == {
            "12": {"state": "ready", "seconds": 9.7, "growing": False},
            "13": {"state": "unavailable", "reason": "The scene recording lost 2 pictures during this attempt."}}
        tracker.on_replay_history([first], None)                 # a repeat is not a new attempt
        assert len(cutters[0].notes) == 2
        assert client.get("/api/scene-lab/attempt-scenes", params={"ids": "12,x"}).status_code == 400


def test_an_attempt_opens_by_its_id_and_every_refusal_says_why(lab, recording):
    app, server, cutters = _app(lab)
    tracker = _tracker()
    server.follow_attempts(tracker)
    with TestClient(app) as client:
        tracker.on_replay_history([_attempt(12, 10), _attempt(13, 20), _attempt(14, 30)], None)
        cutters[0].ready(12, recording["scene"])
        cutters[0].on_event({"id": 14, "state": "unavailable", "reason": "This attempt has no recorded start frame, "
                             "so it has no scene."})
        refused = client.post("/api/scene-lab/open", json={"attempt": 13})
        assert refused.status_code == 409 and refused.json()["detail"] == "This attempt's scene is still being cut."
        refused = client.post("/api/scene-lab/open", json={"attempt": 14})
        assert refused.status_code == 409 and refused.json()["detail"].startswith("This attempt has no recorded start")
        assert client.post("/api/scene-lab/open", json={"attempt": 99}).status_code == 404
        assert client.post("/api/scene-lab/open", json={}).status_code == 422
        assert client.post("/api/scene-lab/open", json={"attempt": 12, "package": "a.sm64scene"}).status_code == 422
        opened = client.post("/api/scene-lab/open", json={"attempt": 12})
        assert opened.status_code == 200, opened.text
        scene = opened.json()
        assert scene["attempt"] == 12 and scene["name"] == "12.sm64scene"
        assert (scene["frames"][0]["game_frame"], scene["frames"][-1]["game_frame"]) == (5000, 5399)
        assert client.get("/api/scene-lab/status").json()["session"]["attempt"] == 12
        client.post("/api/scene-lab/close")
        assert "attempt" not in (client.get("/api/scene-lab/status").json()["session"] or {})


def test_the_event_stream_announces_where_attempts_may_start(lab):
    app, server, cutters = _app(lab)
    tracker = _tracker()
    server.follow_attempts(tracker)
    with TestClient(app):
        for kind, payload in (("practice_reset", {}), ("state_loaded", {}), ("star_collected", {}),
                              ("segment_armed", {"segment_id": 4, "frame": 777}), ("segment_disarmed", {"segment_id": 4})):
            asyncio.run(tracker.broadcaster.publish(Event(kind, 900, T0, payload)))
    assert cutters[0].notes == [
        {"op": "start", "kind": "anchor", "frame": 900, "utc": T0.isoformat()},
        {"op": "start", "kind": "anchor", "frame": 900, "utc": T0.isoformat()},
        {"op": "start", "kind": "segment", "frame": 777, "segment_id": 4, "utc": T0.isoformat()},
        {"op": "stop", "segment_id": 4}]


def test_the_newest_twenty_attempts_keep_their_scenes_and_the_open_one_is_spared(lab, recording):
    app, server, cutters = _app(lab)
    tracker = _tracker()
    server.follow_attempts(tracker)
    with TestClient(app) as client:
        tracker.on_replay_history([_attempt(1, 1)], None)
        oldest = cutters[0].ready(1, recording["scene"])
        assert client.post("/api/scene-lab/open", json={"attempt": 1}).status_code == 200
        for attempt_id in range(2, 22):                                  # 20 newer attempts
            tracker.on_replay_history([_attempt(attempt_id, attempt_id)], None)
            cutters[0].ready(attempt_id, recording["scene"])
        assert _scenes(client, 1) == {} and oldest.is_file()             # dropped, but the viewer reads it
        assert {"op": "forget", "id": 1} in cutters[0].notes
        assert len(_scenes(client, *range(1, 23))) == attempt_scenes.RETAIN_ATTEMPTS
        client.post("/api/scene-lab/close")
        tracker.on_replay_history([_attempt(22, 22)], None)
        assert not oldest.exists() and not (lab["folder"] / "2.sm64scene").exists()
        assert len(list(lab["folder"].glob("*.sm64scene"))) == attempt_scenes.RETAIN_ATTEMPTS - 1


def test_one_server_owns_the_folder_and_clears_what_a_previous_one_left(lab):
    lab["folder"].mkdir(parents=True)
    (lab["folder"] / "7.sm64scene").write_bytes(b"from a previous server")
    (lab["folder"] / "take-3-1.sm64scene.partial").write_bytes(b"half")
    first = attempt_scenes.AttemptScenes(lambda: lab["folder"], lambda folder, on_event: FakeCutter(folder, on_event))
    first.start()
    assert first.problem is None and sorted(path.name for path in lab["folder"].iterdir()) == [".owner.lock"]
    second = attempt_scenes.AttemptScenes(lambda: lab["folder"], lambda folder, on_event: FakeCutter(folder, on_event))
    second.start()
    assert second.cutter is None and second.live() == {
        "recording": False, "reason": "Another Trainer server is cutting attempt scenes into this lab folder."}
    first.stop()
    second.stop()


def test_a_cutter_that_exits_marks_its_attempts_and_starts_again(lab):
    cutters = []
    scenes = attempt_scenes.AttemptScenes(lambda: lab["folder"], lambda folder, on_event: cutters.append(
        FakeCutter(folder, on_event)) or cutters[-1], restart_seconds=0.05)
    scenes.start()
    scenes.observe_completed([_attempt(5, 5)])
    cutters[0].on_event({"exited": 1})
    assert scenes.states([5]) == {"5": {"state": "unavailable", "reason": attempt_scenes.STOPPED}}
    deadline = time.monotonic() + 5
    while (len(cutters) < 2 or scenes.cutter is None) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(cutters) == 2 and scenes.cutter is cutters[1]
    for _ in range(attempt_scenes.MAX_RESTARTS):                     # one that keeps dying is left stopped
        scenes.cutter.on_event({"exited": 1})
        while scenes.cutter is None and scenes.problem is None and time.monotonic() < deadline + 10:
            time.sleep(0.01)
    assert scenes.cutter is None and scenes.live() == {"recording": False, "reason": attempt_scenes.GAVE_UP}
    assert len(cutters) == 1 + attempt_scenes.MAX_RESTARTS
    scenes.stop()


def test_main_feeds_the_lab_from_its_own_tracker_only(monkeypatch, lab):
    import sm64_events.main as main
    monkeypatch.setenv("SM64_SCENE_LAB", str(lab["config"]))
    tracker = _tracker()
    replay_hook = tracker.on_replay_history
    app = main._mount_scene_lab(FastAPI(), tracker=tracker)
    assert app.state.scene_lab.live_on and tracker.on_replay_history is not replay_hook
    assert len(tracker.broadcaster._observers) == 1
    fixture_app = main._mount_scene_lab(FastAPI())                   # the UI fixture: the viewer alone
    assert not fixture_app.state.scene_lab.live_on


def test_end_to_end_the_real_cutter_child_cuts_a_completed_attempt_that_then_opens(lab, recording):
    """The tracker completes a star attempt (anchor 5020, the grab at 5150); the child cuts it from
    the recording through the spool double and the viewer opens it by id: its first picture is the
    anchor's frame and its last the fade-out's, the castle's first frame (5300) left out."""
    (lab["root"] / DOUBLE_CONFIG).write_text(json.dumps({"session": str(recording["session"])}), encoding="utf-8")
    refs = RecordedSpool(recording["session"]).refs
    app, server, _cutters = _app(lab, cutter_factory=None, cutter_options={
        "spool_factory": "scene_spool_double:open_recorded", "extra_path": (ROOT / "tests",)})
    tracker = _tracker()
    server.follow_attempts(tracker)
    with TestClient(app) as client:
        attempt = SimpleNamespace(id=31, anchor_frame=5020, closed_frame=5150, outcome="success",
                                  started_utc=refs[20].utc.isoformat(), ended_utc=refs[150].utc.isoformat())
        tracker.on_replay_history([attempt], None)
        deadline = time.monotonic() + 90
        while _scenes(client, 31).get("31", {}).get("state") == "cutting" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _scenes(client, 31) == {"31": {"state": "ready", "seconds": 9.3, "growing": False}}
        child = server.scenes.cutter.process
        scene = client.post("/api/scene-lab/open", json={"attempt": 31}).json()
        assert scene["attempt"] == 31 and len(scene["frames"]) == 280
        assert (scene["frames"][0]["game_frame"], scene["frames"][-1]["game_frame"]) == (5020, 5299)
        assert {frame["level"] for frame in scene["frames"]} == {9}
    assert child.poll() is not None                                      # shutdown ended the child
    assert not list(lab["folder"].glob("*.grow"))


def _take_event(folder: Path, refs, first: int, name: str = "take-20-1") -> dict:
    return {"take": {"path": str(folder / f"{name}.sm64scene"), "grow": str(folder / f"{name}.grow"),
                     "frame": refs[first].global_timer, "utc": refs[first].utc.isoformat(), "first": first,
                     "kind": "anchor"}}


def test_a_completed_attempt_with_a_take_answers_ready_in_the_same_call(lab, recording):
    """The Practice log asks for a row's scene once, when the row appears (the attempt_completed
    broadcast that follows the hook): the answer must already be ready, growing, never omitted."""
    refs = RecordedSpool(recording["session"]).refs
    app, server, cutters = _app(lab)
    tracker = _tracker()
    server.follow_attempts(tracker)
    with TestClient(app) as client:
        cutters[0].on_event(_take_event(lab["folder"], refs, 20))
        attempt = SimpleNamespace(id=40, anchor_frame=5020, closed_frame=5150, outcome="success",
                                  started_utc=refs[20].utc.isoformat(), ended_utc=refs[150].utc.isoformat())
        tracker.on_replay_history([attempt], None)
        assert _scenes(client, 40) == {"40": {"state": "ready", "seconds": 4.3, "growing": True}}
        late = SimpleNamespace(id=41, anchor_frame=5200, closed_frame=5250, outcome="reset",
                               started_utc=refs[200].utc.isoformat(), ended_utc=refs[250].utc.isoformat())
        tracker.on_replay_history([late], None)                       # no take began there: the slow way
        assert _scenes(client, 41) == {"41": {"state": "cutting"}}
        cutters[0].on_event({"id": 40, "state": "ready", "growing": False, "seconds": 9.3,
                             "path": str(lab["folder"] / "take-20-1.sm64scene")})
        assert _scenes(client, 40) == {"40": {"state": "ready", "seconds": 9.3, "growing": False}}


def test_a_growing_scene_opens_at_once_and_its_page_gets_each_new_stretch(lab, recording):
    """A take's package published while written: the viewer opens it by the attempt's id, its
    pages hear {"kind": "grown"} as it gains frames, and the last word is the finished file."""
    refs = RecordedSpool(recording["session"]).refs
    lab["folder"].mkdir(parents=True, exist_ok=True)
    app, server, cutters = _app(lab, grow_seconds=0.05)
    tracker = _tracker()
    server.follow_attempts(tracker)
    with TestClient(app) as client:
        path, grow = lab["folder"] / "take-20-1.sm64scene", lab["folder"] / "take-20-1.grow"
        source = RecordedSpool(recording["session"]).follow(20)
        source.stop_at(299)
        captures = iter(source.captures)
        writer = package.PackageWriter(source, recording["rom"], path, package.ZLIB, level=6, in_place=True)
        for _ in range(61):
            writer.add(next(captures))
        assert writer.publish(grow) == 60
        cutters[0].on_event(_take_event(lab["folder"], refs, 20))
        tracker.on_replay_history([SimpleNamespace(
            id=50, anchor_frame=5020, closed_frame=5150, outcome="success",
            started_utc=refs[20].utc.isoformat(), ended_utc=refs[150].utc.isoformat())], None)
        scene = client.post("/api/scene-lab/open", json={"attempt": 50}).json()
        assert scene["attempt"] == 50 and scene["growing"] is True and len(scene["frames"]) == 60
        with client.websocket_connect("/api/scene-lab/ws") as page:
            for _ in range(100):
                writer.add(next(captures))
            covered = writer.publish(grow)
            grown = page.receive_json()
            assert grown["kind"] == "grown" and grown["session"] == scene["session"] and grown["growing"] is True
            assert grown["from"] == 60 and len(grown["frames"]) == covered - 60
            assert grown["frames"][0]["game_frame"] == 5080
            for capture in captures:
                writer.add(capture)
            writer.finish()
            grow.unlink()
            last = page.receive_json()
            assert last["kind"] == "grown" and last["growing"] is False and last["from"] + len(last["frames"]) == 280
            assert last["frames"][-1]["game_frame"] == 5299
        status = client.get("/api/scene-lab/status").json()["session"]
        assert status["growing"] is False and len(status["frames"]) == 280
