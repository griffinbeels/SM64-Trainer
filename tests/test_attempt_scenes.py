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
    journal = FakeJournal(tmp_path / "data" / "tracker.db")
    return {"config": path, "root": root, "journal": journal,
            "folder": attempt_scenes.journal_folder(root / "attempts", journal)}


class FakeJournal:
    """storage/db.py Database as the attempt scenes read it: its file and one attempt by id."""

    def __init__(self, path: Path):
        self.path, self.rows = path, {}

    def attempt(self, attempt_id: int):
        return self.rows.get(attempt_id)


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


def _tracker(lab=None, journal=None):
    calls = []
    return SimpleNamespace(on_replay_history=lambda *args, **options: calls.append((args, options)),
                           broadcaster=Broadcaster(), calls=calls,
                           db=journal or (lab["journal"] if lab is not None else None))


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
    server.follow_attempts(_tracker(lab))
    with TestClient(app) as client:
        assert client.get("/api/scene-lab").json() == {
            "lab": "scene-viewer", "live": {"recording": False, "reason": attempt_scenes.STARTING, "on": True,
                                            "switch": True}}
        cutters[0].on_event({"live": {"recording": True, "reason": None}})
        live = client.get("/api/scene-lab").json()["live"]
        assert (live["recording"], live["on"], live["switch"], live["reason"]) == (True, True, True, None)
        assert FakeLease.made[-1].running and FakeLease.made[-1].lab_root == lab["root"]
    assert not FakeLease.made[-1].running and cutters[0].closed
    off, _server, none = _app(lab, live=False)
    with TestClient(off) as client:
        live = client.get("/api/scene-lab").json()["live"]
        assert (live["recording"], live["switch"], live["reason"]) == (
            False, False, "This server does not run the live scene recording.")
        assert client.post("/api/scene-lab/recording", json={"on": True}).status_code == 409
    assert none == []


def test_the_switch_stops_and_starts_the_recording_and_the_next_server_keeps_it_off(lab):
    """The Scene viewer's switch: off deletes the lease (the wrapper stops within a second), on
    renews it again; the cutter keeps running for the scenes already cut; off survives a restart."""
    app, server, cutters = _app(lab)
    server.follow_attempts(_tracker(lab))
    with TestClient(app) as client:
        lease = FakeLease.made[-1]
        cutters[0].on_event({"live": {"recording": True, "reason": None}})
        answer = client.post("/api/scene-lab/recording", json={"on": False}).json()["live"]
        assert (answer["recording"], answer["on"], answer["switch"]) == (False, False, True)
        assert answer["reason"] == scene_lab_api.RECORDING_OFF and not lease.running and not cutters[0].closed
        assert json.loads((lab["root"] / scene_lab_api.RECORDING_SWITCH).read_text()) == {"on": False}
    later, _server, cutters = _app(lab)
    with TestClient(later) as client:
        lease = FakeLease.made[-1]
        assert not lease.running and client.get("/api/scene-lab").json()["live"]["on"] is False
        answer = client.post("/api/scene-lab/recording", json={"on": True}).json()["live"]
        assert answer["on"] is True and lease.running
        assert client.post("/api/scene-lab/recording", json={"on": "maybe"}).status_code == 422
    assert json.loads((lab["root"] / scene_lab_api.RECORDING_SWITCH).read_text()) == {"on": True}


def test_each_completed_attempt_is_cutting_at_once_and_the_replay_still_gets_its_call(lab, recording):
    app, server, cutters = _app(lab)
    tracker = _tracker(lab)
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
    tracker = _tracker(lab)
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
    tracker = _tracker(lab)
    server.follow_attempts(tracker)
    with TestClient(app):
        # Shaped as tracking/service.py drains the tracker's notices: the arm's frame is the
        # event's own and the payload is {segment_id, name}; a re-projection refreshes with frame 0.
        for kind, frame, payload in (("practice_reset", 900, {}), ("state_loaded", 900, {}), ("star_collected", 900, {}),
                                     ("segment_armed", 777, {"segment_id": 4, "name": "BitDW Pipe Entry"}),
                                     ("segment_armed", 0, {"segment_id": 5, "name": "Bowser 1"}),
                                     ("segment_disarmed", 900, {"segment_id": 4, "name": "BitDW Pipe Entry"})):
            asyncio.run(tracker.broadcaster.publish(Event(kind, frame, T0, payload)))
    assert cutters[0].notes == [
        {"op": "start", "kind": "anchor", "frame": 900, "utc": T0.isoformat()},
        {"op": "start", "kind": "anchor", "frame": 900, "utc": T0.isoformat()},
        {"op": "start", "kind": "segment", "frame": 777, "segment_id": 4, "utc": T0.isoformat()},
        {"op": "stop", "segment_id": 4}]


def _server(lab, journal=None):
    app, server, cutters = _app(lab)
    tracker = _tracker(lab, journal)
    server.follow_attempts(tracker)
    return app, server, cutters, tracker


def test_finished_scenes_survive_a_restart_and_only_for_the_attempt_the_journal_still_holds(lab, recording, tmp_path):
    """His ask, 2026-11-08: "we should be able to keep ghosts after restarts." The next server
    serves each finished scene again when the journal still holds that attempt id with the same
    start and end; a take cut short by the stop is deleted, and so is a scene whose id now names
    another attempt (a journal restored from a backup at the same path)."""
    app, server, cutters, tracker = _server(lab)
    kept, renamed, cut_short = _attempt(12, 10), _attempt(13, 20), _attempt(14, 30)
    lab["journal"].rows.update({12: kept, 13: renamed, 14: cut_short})
    with TestClient(app) as client:
        tracker.on_replay_history([kept, renamed, cut_short], None)
        cutters[0].ready(12, recording["scene"])
        cutters[0].ready(13, recording["scene"])
        (lab["folder"] / "take-0a1b2c3d-20-1.sm64scene").write_bytes(b"14's take, still writing")
        (lab["folder"] / "take-0a1b2c3d-20-1.grow").write_bytes(b"its publication")
        assert _scenes(client, 12, 13, 14)["14"] == {"state": "cutting"}
    lab["journal"].rows[13] = _attempt(13, 99)
    again, _later, cutters, _tracked = _server(lab)
    with TestClient(again) as client:
        assert _scenes(client, 12, 13, 14) == {"12": {"state": "ready", "seconds": 9.7, "growing": False}}
        opened = client.post("/api/scene-lab/open", json={"attempt": 12})
        assert opened.status_code == 200 and opened.json()["attempt"] == 12
        client.post("/api/scene-lab/close")
    assert sorted(path.name for path in lab["folder"].iterdir()) == [".owner.lock", "12.sm64scene",
                                                                    attempt_scenes.INDEX_NAME]
    other = FakeJournal(tmp_path / "other checkout" / "data" / "tracker.db")
    other.rows.update(lab["journal"].rows)                      # the same ids, in another journal
    elsewhere, _server_3, _cutters_3, _tracker_3 = _server(lab, other)
    with TestClient(elsewhere) as client:
        assert _scenes(client, 12) == {}
    assert (lab["folder"] / "12.sm64scene").is_file()           # and that server never touched them


def test_a_purge_deletes_finished_scenes_skips_the_open_one_and_never_cuts_them_again(lab, recording):
    app, server, cutters, tracker = _server(lab)
    first, second = _attempt(21, 10), _attempt(22, 20)
    lab["journal"].rows.update({21: first, 22: second})
    with TestClient(app) as client:
        tracker.on_replay_history([first, second], None)
        open_one, other = cutters[0].ready(21, recording["scene"]), cutters[0].ready(22, recording["scene"])
        assert client.post("/api/scene-lab/open", json={"attempt": 21}).status_code == 200
        size = len(recording["scene"])
        assert sorted((row["attempt"], row["bytes"], row["in_use"]) for row in server.scenes.holdings()) == [
            (21, size, True), (22, size, False)]
        assert server.scenes.purge([21, 22]) == {"deleted": [22], "bytes": size, "skipped": [21]}
        assert open_one.is_file() and not other.exists()
        assert _scenes(client, 21, 22) == {"21": {"state": "ready", "seconds": 9.7, "growing": False}}
        assert {"op": "forget", "id": 22} in cutters[0].notes
        notes = len(cutters[0].notes)
        tracker.on_replay_history([first, second], None, replace=True)     # a re-projection hands both over again
        assert len(cutters[0].notes) == notes and _scenes(client, 22) == {}
        client.post("/api/scene-lab/close")
    index = json.loads((lab["folder"] / attempt_scenes.INDEX_NAME).read_text(encoding="utf-8"))
    assert [row["attempt"] for row in index["scenes"]] == [21]


def test_one_server_owns_a_journal_folder_and_the_flat_layout_is_cleared_once(lab):
    flat = lab["root"] / "attempts"
    flat.mkdir(parents=True)
    (flat / "7.sm64scene").write_bytes(b"from the flat layout")
    (flat / "take-3-1.sm64scene.partial").write_bytes(b"half")

    def scenes(journal=lambda: lab["journal"]):
        return attempt_scenes.AttemptScenes(lambda: lab["folder"], lambda folder, on_event: FakeCutter(folder, on_event),
                                            journal=journal, legacy=lambda: flat)
    first = scenes()
    first.start()
    assert first.problem is None and first.cutter is not None
    assert sorted(path.name for path in flat.iterdir()) == [".owner.lock", lab["folder"].name]
    assert sorted(path.name for path in lab["folder"].iterdir()) == [".owner.lock", attempt_scenes.INDEX_NAME]
    second = scenes()
    second.start()
    assert second.cutter is None and second.live() == {
        "recording": False, "reason": "Another Trainer server is cutting attempt scenes into this lab folder."}
    first.stop()
    second.stop()
    older = attempt_scenes._lock(flat)                          # a server from before 2026-11-08 still cutting
    (flat / "8.sm64scene").write_bytes(b"an older server's")
    third = scenes()
    third.start()
    assert (flat / "8.sm64scene").is_file() and third.problem is None
    third.stop()
    older.close()
    closed = scenes(journal=lambda: None)                       # a broadcast-only server has no journal open
    closed.start()
    assert closed.cutter is None and closed.problem == attempt_scenes.NO_JOURNAL
    closed.stop()


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
    tracker = _tracker(lab)
    replay_hook = tracker.on_replay_history
    app = main._mount_scene_lab(FastAPI(), tracker=tracker)
    assert app.state.scene_lab.live_on and tracker.on_replay_history is not replay_hook
    refitter = app.state.scene_lab.refitter
    assert refitter is not None and tracker.broadcaster._observers == {app.state.scene_lab.scenes.observe_event,
                                                                       refitter.observe}
    fixture_app = main._mount_scene_lab(FastAPI())                   # the UI fixture: the viewer alone
    assert not fixture_app.state.scene_lab.live_on and fixture_app.state.scene_lab.refitter is None


def test_a_published_spawn_refits_the_slots_while_the_app_runs_and_the_thread_ends_with_it(lab, tmp_path):
    """The live server's path: the broadcaster publishes a spawn, the lab's refitter (started with the app) refits
    the slot on its own thread, and the app's stop ends that thread. Fake memory; slotlibrary/refit.py."""
    sys.path.insert(0, str(ROOT / "tests"))
    import test_slot_library as libfakes
    import test_slot_relocate as world
    from sm64_events.slotlibrary import refit, usamune
    game = libfakes.FakeGame()
    world.live_world(game)
    game.save(0, world.make_slot())
    app, server, _cutters = _app(lab)
    tracker = _tracker(lab)
    server.follow_spawns(tracker, game=lambda: game, library=None, log_file=tmp_path / refit.LOG_NAME)
    with TestClient(app):
        asyncio.run(tracker.broadcaster.publish(Event("spawned", 700, T0, {"level": 9, "area": 1})))
        deadline = time.monotonic() + 10
        while server.refitter.passes == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.refitter.passes == 1
        assert usamune.slot_facts(game.slot(0)) == usamune.live_facts(game)
    assert not server.refitter._thread.is_alive()


def test_end_to_end_the_real_cutter_child_cuts_a_completed_attempt_that_then_opens(lab, recording):
    """The tracker completes a star attempt (anchor 5020, the grab at 5150); the child cuts it from
    the recording through the spool double and the viewer opens it by id: its first picture is the
    anchor's frame and its last the fade-out's, the castle's first frame (5300) left out."""
    (lab["root"] / DOUBLE_CONFIG).write_text(json.dumps({"session": str(recording["session"])}), encoding="utf-8")
    refs = RecordedSpool(recording["session"]).refs
    app, server, _cutters = _app(lab, cutter_factory=None, cutter_options={
        "spool_factory": "scene_spool_double:open_recorded", "extra_path": (ROOT / "tests",)})
    tracker = _tracker(lab)
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
    tracker = _tracker(lab)
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


def test_two_attempts_from_one_start_are_both_ready_in_the_call_that_completes_them(lab, recording):
    """His star and the segment anchored on the same reset complete in one call: the first takes
    the package the child announced, the second is ready too and opens once the child has made
    its own (attempt_cutter.Take.seat_for), never the first's frames. Both notes carry the
    tracker's oldest open start, so the child keeps a take going for an attempt still open."""
    refs = RecordedSpool(recording["session"]).refs
    app, server, cutters = _app(lab)
    tracker = _tracker(lab)
    server.follow_attempts(tracker)
    folder = lab["folder"]
    with TestClient(app) as client:
        cutters[0].on_event(_take_event(folder, refs, 20))
        star, segment = (SimpleNamespace(id=attempt_id, anchor_frame=5020, closed_frame=closed, outcome="success",
                                         started_utc=refs[20].utc.isoformat(), ended_utc=refs[closed].utc.isoformat())
                         for attempt_id, closed in ((40, 150), (670000000040, 155)))
        tracker.on_replay_history([star, segment], refs[20].utc.isoformat())
        assert _scenes(client, 40, 670000000040) == {"40": {"state": "ready", "seconds": 4.3, "growing": True},
                                                     "670000000040": {"state": "ready", "seconds": 4.5, "growing": True}}
        assert [note["open_since"] for note in cutters[0].notes] == [refs[20].utc.isoformat()] * 2
        assert server.scenes.entries[40]["path"] == str(folder / "take-20-1.sm64scene")
        assert server.scenes.entries[670000000040]["path"] is None       # never the star's package
        refused = client.post("/api/scene-lab/open", json={"attempt": 670000000040})
        assert refused.status_code == 409 and refused.json()["detail"] == attempt_scenes.SCENE_STARTING
        own, grow = folder / "take-20-2.sm64scene", folder / "take-20-2.grow"
        cutters[0].on_event({"id": 670000000040, "state": "ready", "growing": True, "path": str(own), "grow": str(grow)})
        with pytest.raises(attempt_scenes.SceneUnavailable, match="starting"):
            server.scenes.path(670000000040)                      # named, its copy not made yet
        folder.mkdir(parents=True, exist_ok=True)
        own.write_bytes(b"body")
        grow.write_bytes(b"publication")
        assert server.scenes.path(670000000040) == (own, grow)
        cutters[0].on_event({"take_ended": str(folder / "take-20-1.sm64scene")})
        cutters[0].on_event(_take_event(folder, refs, 20, "take-20-3"))   # a take going on for one still open
        later = SimpleNamespace(id=80000000040, anchor_frame=5020, closed_frame=5290, outcome="success",
                                started_utc=refs[20].utc.isoformat(), ended_utc=refs[290].utc.isoformat())
        tracker.on_replay_history([later], None)
        assert server.scenes.entries[80000000040]["path"] == str(folder / "take-20-3.sm64scene")
        assert cutters[0].notes[-1]["open_since"] is None


def test_a_growing_scene_opens_at_once_and_its_page_gets_each_new_stretch(lab, recording):
    """A take's package published while written: the viewer opens it by the attempt's id, its
    pages hear {"kind": "grown"} as it gains frames, and the last word is the finished file."""
    refs = RecordedSpool(recording["session"]).refs
    lab["folder"].mkdir(parents=True, exist_ok=True)
    app, server, cutters = _app(lab, grow_seconds=0.05)
    tracker = _tracker(lab)
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


class StubLive:
    """live_audio.LiveAudio as the lab uses it: a ring that writes a span, or says why not."""
    frequency = 10_000_000

    def __init__(self, written: dict):
        self.written, self.spans = written, []

    def write_span(self, first, last, out):
        self.spans.append((first, last))
        if self.written["written"]:
            Path(out).write_bytes(b"capture")
        return self.written

    def health(self):
        return {"recording": True, "reason": None, "kept_seconds": 12.0}


def test_a_finished_scene_takes_the_recorders_sound_after_ready_and_says_why_when_it_cannot(lab, recording):
    silent = "The Trainer was not recording Project64's sound during this attempt."
    for written, expect in (({"written": False, "reason": silent}, silent), ({"written": True, "reason": None}, None)):
        live = StubLive(written)
        app, server, cutters = _app(lab, live_audio=live)
        tracker = _tracker(lab)
        server.follow_attempts(tracker)
        with TestClient(app) as client:
            assert client.get("/api/scene-lab").json()["live"]["sound"] == {
                "recording": True, "reason": None, "kept_seconds": 12.0}
            tracker.on_replay_history([_attempt(60, 10)], None)
            path = cutters[0].folder / "take-20-1.sm64scene"
            path.write_bytes(recording["scene"])
            cutters[0].on_event({"id": 60, "state": "ready", "growing": False, "seconds": 9.3, "path": str(path),
                                 "presents_qpc": [100, 900], "qpc_frequency": live.frequency})
            assert _scenes(client, 60) == {"60": {"state": "ready", "seconds": 9.3, "growing": False}}
            assert server.scenes.sound_reason(60).startswith("This attempt's sound is still being added")
            deadline = time.monotonic() + 5
            while not live.spans and time.monotonic() < deadline:
                time.sleep(0.02)
            assert live.spans == [(100, 900)]
            if written["written"]:
                deadline = time.monotonic() + 5
                while not any(note.get("op") == "sound" for note in cutters[0].notes) and time.monotonic() < deadline:
                    time.sleep(0.02)
                note = next(note for note in cutters[0].notes if note.get("op") == "sound")
                assert note["id"] == 60 and Path(note["capture"]).is_file()
                cutters[0].on_event({"id": 60, "sound": None})
                assert not Path(note["capture"]).exists()
            assert server.scenes.sound_reason(60) == expect
            if expect:
                opened = client.post("/api/scene-lab/open", json={"attempt": 60}).json()
                assert opened["audio"] == {"available": False, "reason": silent}
                client.post("/api/scene-lab/close")
