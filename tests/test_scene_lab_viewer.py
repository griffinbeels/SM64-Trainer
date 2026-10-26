"""The Trainer's scene viewer (M4 E3, lab only) through its real routes, with protocol doubles.

The server (src/sm64_events/server/scene_lab_api.py) and the session
(tools/scene_lab/scene_session.py) run as shipped; the renderer host is
fake_viewer_host.py, a real child process speaking viewer_host.c's pipe protocol whose
picture is the sha256 of everything a request carried, and the evaluator is its
FakeEvaluator. Packages are test_scene_package.py's synthetic recording: six tasks, the
fifth never shown. No ROM, RAM image, GPU, emulator or browser is involved.
"""
import hashlib
import importlib.util
import json
import struct
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import scene_session  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

spec = importlib.util.spec_from_file_location("scene_package_synthetic", ROOT / "tests/test_scene_package.py")
synthetic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(synthetic)

SHOWN = [0, 1, 2, 3, 5]                  # package indices a present showed: the viewer's frames


def _write_package(session: Path, rom: bytes, out: Path) -> None:
    pk = synthetic.pk
    pk.write_package(pk.load_source(session), rom, out, pk.CODECS["zlib"], group=2)


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """The synthetic tap session, its package, and a package of the same tasks from another ROM."""
    base = tmp_path_factory.mktemp("scene-lab")
    rom, segment7 = synthetic._rom()
    session = base / "tap-synthetic"
    session.mkdir()
    records, sequence = [], 0
    for frame in range(6):
        records.append(synthetic._tap_record(synthetic.pk.tap.REC_TASK, sequence,
                                             synthetic._task_body(synthetic._rdram(rom, segment7, frame), frame)))
        sequence += 1
        if frame != 4:
            records.append(synthetic._tap_record(synthetic.pk.tap.REC_PRESENT, sequence, synthetic._present_body(frame)))
            sequence += 1
    (session / "tap.bin").write_bytes(b"".join(records))
    (session / "session.txt").write_text(
        f"schema=scene-lab-tap/1\nwrapper_build=test\nqpc_frequency=10000000\nrdram_size={synthetic.RDRAM}\n"
        f"rom_header={synthetic.pk.rom_header_hex(rom)}\n", encoding="ascii")
    (session / "armed.txt").write_text(f"extra={synthetic.TIMER:x}:4\n# identity=global_timer:{synthetic.TIMER:x}:4\n",
                                       encoding="ascii")
    package = base / "synthetic.sm64scene"
    _write_package(session, rom, package)
    other = bytearray(rom)
    other[0x1F0000:0x1F0010] = b"ANOTHER ROM BYTE"      # same tasks, a ROM with a different hash
    foreign = base / "foreign.sm64scene"
    _write_package(session, bytes(other), foreign)
    return {"rom": rom, "package": package.read_bytes(), "foreign": foreign.read_bytes()}


@pytest.fixture
def lab(tmp_path, recording, monkeypatch):
    """A lab folder: the ROM, a plugin folder, five packages (two good, one with a damaged index,
    one truncated, one from another ROM), a stray file, and the lab config."""
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    packages = tmp_path / "packages"
    packages.mkdir()
    (packages / "scene-a.sm64scene").write_bytes(recording["package"])
    (packages / "scene-b.sm64scene").write_bytes(recording["package"])
    corrupt = bytearray(recording["package"])
    corrupt[-1] ^= 0xFF                                  # the index sits last
    (packages / "corrupt.sm64scene").write_bytes(bytes(corrupt))
    (packages / "truncated.sm64scene").write_bytes(recording["package"][:-100])
    (packages / "foreign.sm64scene").write_bytes(recording["foreign"])
    (packages / "notes.txt").write_text("not a package", encoding="utf-8")
    (tmp_path / "rom.z64").write_bytes(recording["rom"])
    (tmp_path / "Plugin").mkdir()
    config = {"rom": str(tmp_path / "rom.z64"), "packages": str(packages), "pj64_plugin": str(tmp_path / "Plugin"),
              "map": str(tmp_path / "MappingUS.map")}
    path = tmp_path / "lab.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    return {"config": path, "dir": tmp_path, "values": config}


class Evaluators:
    """A FakeEvaluator per session, kept so a test can read what it was asked."""

    def __init__(self, **options):
        self.options, self.made = options, []

    def __call__(self, package, rom):
        self.made.append(fake_viewer_host.FakeEvaluator(**self.options))
        return self.made[-1]


def _app(lab, *, delay_ms=0, exit_after=0, evaluators=None, **options):
    app = FastAPI()
    host = scene_session.command_host(fake_viewer_host.command(delay_ms=delay_ms, exit_after=exit_after))
    server = scene_lab_api.mount_scene_lab(
        app, lab["config"], session_options={"host_factory": host, "evaluator_factory": evaluators or Evaluators()},
        **{"watch_seconds": 0.05, **options})
    return app, server


def _reply(socket):
    message = socket.receive()
    if message.get("bytes") is not None:
        data = message["bytes"]
        length = struct.unpack_from("<I", data)[0]
        header = json.loads(data[4:4 + length])
        header["payload"] = data[4 + length:]
        return header
    return json.loads(message["text"])


def _expected_pixels(task, vi, runs: bytes, data: bytes, size: int = 64 * 48 * 3) -> bytes:
    """fake_viewer_host.py's picture for a recorded-camera request."""
    digest = hashlib.sha256(struct.pack("<16I14I", *task, *vi) + runs + data).digest()
    return (digest * (size // 32 + 1))[:size]


def _recorded(recording, index: int) -> bytes:
    capture = scene_session.scene_package.Package(recording["package"], recording["rom"]).frame(index)
    runs = struct.pack(f"<{2 * len(capture.runs)}I", *(edge for run in capture.runs for edge in run))
    return _expected_pixels(capture.task, capture.vi, runs, capture.run_bytes)


def _host_process(server):
    return server.session.host.host.process


def _wait_exit(process, seconds=10.0) -> int:
    deadline = time.monotonic() + seconds
    while process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    return process.poll()


def _wait_closed(client, seconds=10.0) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = client.get("/api/scene-lab/status").json()
        if status["session"] is None and status["ended"]:
            return status
        time.sleep(0.05)
    raise AssertionError("the scene never closed")


# ---------------------------------------------------------------- the switch


def test_without_the_switch_nothing_is_imported_or_routed(monkeypatch, lab):
    import sm64_events.main as main
    monkeypatch.delenv("SM64_SCENE_LAB", raising=False)
    monkeypatch.delitem(sys.modules, "sm64_events.server.scene_lab_api")
    app = FastAPI()
    routes = {route.path for route in app.routes}
    assert main._mount_scene_lab(app) is app
    assert {route.path for route in app.routes} == routes
    assert "sm64_events.server.scene_lab_api" not in sys.modules
    monkeypatch.setenv("SM64_SCENE_LAB", str(lab["config"]))
    main._mount_scene_lab(app)
    assert {"/api/scene-lab", "/api/scene-lab/open", "/api/scene-lab/ws"} <= {route.path for route in app.routes}


def test_status_lists_only_packages_and_names_what_is_missing(lab):
    app, _server = _app(lab)
    with TestClient(app) as client:
        assert client.get("/api/scene-lab").status_code == 200
        status = client.get("/api/scene-lab/status").json()
        assert status["ready"] and status["hold"] is None and status["session"] is None
        assert [row["name"] for row in status["packages"]] == [
            "corrupt.sm64scene", "foreign.sm64scene", "scene-a.sm64scene", "scene-b.sm64scene",
            "truncated.sm64scene"]
        lab["config"].write_text(json.dumps({**lab["values"], "rom": "relative.z64"}), encoding="utf-8")
        status = client.get("/api/scene-lab/status").json()
        assert not status["ready"] and "absolute path" in status["problems"][0]
        assert client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).status_code == 409


# ---------------------------------------------------------------- pictures


def test_the_recorded_camera_shows_each_shown_task_exactly(lab, recording):
    app, server = _app(lab)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        assert len(opened["frames"]) == len(SHOWN)                    # the task no present showed is skipped
        assert opened["frames"][4]["game_frame"] == 1005 and opened["free_camera"] == {"available": True,
                                                                                         "reason": None}
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            for position in (4, 0, 2, 4):                               # any order, repeats included
                socket.send_json({"id": position, "session": opened["session"], "frame": position,
                                  "camera": None, "encoding": "raw"})
                header = _reply(socket)
                assert header["id"] == position and header["camera"] == "recorded" and header["reason"] is None
                assert header["payload"] == _recorded(recording, SHOWN[position])
                assert header["sha256"] == hashlib.sha256(header["payload"]).hexdigest()
            socket.send_json({"id": 9, "session": opened["session"], "frame": 0, "camera": None, "encoding": "jpeg"})
            header = _reply(socket)
            assert header["format"] == "image/jpeg" and header["payload"][:2] == b"\xff\xd8"
            for bad in ({"frame": 5}, {"frame": True}, {"encoding": "png"}):
                socket.send_json({"id": 10, "session": opened["session"], "frame": 0, "camera": None,
                                  "encoding": "raw", **bad})
                assert "error" in _reply(socket)
        assert server.session is not None and server.session.served == 5


def test_the_free_camera_redraws_from_the_evaluator_and_says_when_it_cannot(lab, recording):
    evaluators = Evaluators(no_scene=(SHOWN[1],))
    app, _server = _app(lab, evaluators=evaluators)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        camera = {"focus": [1.0, 2.0, 3.0], "position": [100.0, 200.0, 300.0]}
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": opened["session"], "kind": "pose", "frame": 2})
            assert _reply(socket) == {"id": 1, "kind": "pose", "pose": fake_viewer_host.FakeEvaluator.POSE,
                                      "reason": None}
            socket.send_json({"id": 2, "session": opened["session"], "frame": 2, "camera": camera,
                              "encoding": "raw"})
            header = _reply(socket)
            view = evaluators.made[0].display_list(SHOWN[2], ([1.0, 2.0, 3.0], [100.0, 200.0, 300.0]))
            task = list(view.task)
            task[12], task[13] = view.data_ptr & 0x1FFFFFFF, view.data_size
            data = scene_session.scene_package.swap_words(bytes(view.ram))
            vi = scene_session.scene_package.Package(recording["package"], recording["rom"]).frame(SHOWN[2]).vi
            assert header["camera"] == "free" and header["reason"] is None
            assert header["payload"] == _expected_pixels(task, vi, struct.pack("<II", 0, len(data)), data)
            socket.send_json({"id": 3, "session": opened["session"], "frame": 1, "camera": camera, "encoding": "raw"})
            header = _reply(socket)
            assert header["camera"] == "recorded" and header["reason"] == scene_session.NO_SCENE
            assert header["payload"] == _recorded(recording, SHOWN[1])
            for bad in ({"focus": [0, 0, float("nan")], "position": [1, 1, 1]}, {"focus": [1, 2, 3]},
                        {"focus": [1, 2, 3], "position": [1, 2, 3]}, [1, 2, 3]):
                socket.send_json({"id": 4, "session": opened["session"], "frame": 0, "camera": bad, "encoding": "raw"})
                reply = _reply(socket)
                assert reply["id"] == 4 and "error" in reply and not reply.get("closed")
        assert len(evaluators.made) == 1                                # one evaluator, built on first use


def test_a_free_camera_carries_its_own_roll_and_field_of_view_to_the_game_code(lab):
    evaluators = Evaluators()
    app, _server = _app(lab, evaluators=evaluators)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        base = {"focus": [1.0, 2.0, 3.0], "position": [100.0, 200.0, 300.0]}
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            for ident, extra in enumerate(({}, {"roll": -224, "fov": 52.5}, {"roll": 0}, {"fov": 80})):
                socket.send_json({"id": ident, "session": opened["session"], "frame": 2, "camera": {**base, **extra},
                                  "encoding": "raw"})
                assert _reply(socket)["camera"] == "free"
            for bad in ({"roll": 40000}, {"roll": 1.5}, {"roll": True}, {"fov": 0}, {"fov": 200},
                        {"fov": float("nan")}):
                socket.send_json({"id": 9, "session": opened["session"], "frame": 2, "camera": {**base, **bad},
                                  "encoding": "raw"})
                reply = _reply(socket)
                assert "error" in reply and not reply.get("closed"), bad
        cameras = [camera for _index, camera in evaluators.made[0].calls]
        focus, position = base["focus"], base["position"]
        assert cameras == [(focus, position), (focus, position, -224, 52.5), (focus, position, 0, None),
                           (focus, position, None, 80.0)]


def test_set_focus_answers_where_the_list_drew_the_clicked_point(lab):
    evaluators = Evaluators(wall=1000.0, no_scene=(SHOWN[1],))
    app, _server = _app(lab, evaluators=evaluators)
    camera = {"focus": [0.0, 0.0, 0.0], "position": [0.0, 0.0, 1000.0], "roll": 0, "fov": 45.0}
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        session = opened["session"]
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": session, "kind": "pick", "frame": 2, "camera": camera,
                              "u": 0.5, "v": 0.5})
            reply = _reply(socket)
            assert reply["kind"] == "pick" and reply["reason"] is None
            assert reply["point"] == pytest.approx([0, 0, -1000], abs=0.01)
            assert reply["distance"] == pytest.approx(1000, abs=0.01)
            socket.send_json({"id": 2, "session": session, "kind": "pick", "frame": 2, "camera": camera,
                              "u": 0.75, "v": 0.25})
            right, up, _ = _reply(socket)["point"]
            assert right > 0 and up > 0                                    # up and to the right of the middle
            socket.send_json({"id": 3, "session": session, "kind": "pick", "frame": 1, "camera": camera,
                              "u": 0.5, "v": 0.5})
            assert _reply(socket) == {"id": 3, "kind": "pick", "point": None, "distance": None,
                                      "reason": scene_session.NO_SCENE}
            socket.send_json({"id": 4, "session": session, "kind": "pick", "frame": 0, "camera": None,
                              "u": 0.5, "v": 0.5})
            recorded = _reply(socket)                                    # the synthetic capture draws no world
            assert recorded["kind"] == "pick" and recorded["point"] is None and recorded["reason"]
            for bad in ({"u": 1.5, "v": 0.5}, {"u": "0.5", "v": 0.5}, {"v": 0.5}):
                socket.send_json({"id": 5, "session": session, "kind": "pick", "frame": 2, "camera": camera, **bad})
                assert "error" in _reply(socket)


def _free_session_pictures(lab, keeps_rdram: bool, requests: list) -> list[dict]:
    """Every reply to `requests` ((frame, camera or None, encoding)) from a session whose fake host
    keeps an RDRAM and names it in the picture, its evaluator's views whole 4 MiB images."""
    app = FastAPI()
    host = scene_session.command_host(fake_viewer_host.command(ram=True), keeps_rdram=keeps_rdram)
    scene_lab_api.mount_scene_lab(app, lab["config"], session_options={
        "host_factory": host, "evaluator_factory": Evaluators(ram_bytes=synthetic.RDRAM)}, watch_seconds=0.05)
    replies = []
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            for ident, (frame, camera, encoding) in enumerate(requests):
                socket.send_json({"id": ident, "session": opened["session"], "frame": frame, "camera": camera,
                                  "encoding": encoding})
                replies.append(_reply(socket))
    return replies


def test_a_free_picture_sends_only_what_changed_and_leaves_the_host_as_a_full_send_would(lab):
    one = {"focus": [1.0, 2.0, 3.0], "position": [100.0, 200.0, 300.0]}
    two = {"focus": [1.0, 2.0, 3.0], "position": [120.0, 200.0, 300.0]}
    requests = [(2, one, "raw"), (2, two, "raw"), (3, two, "raw"), (0, None, "raw"), (3, one, "raw"),
                (0, one, "raw"), (4, None, "raw"), (4, two, "raw"), (4, two, "raw")]
    whole = _free_session_pictures(lab, False, requests)
    changed = _free_session_pictures(lab, True, requests)
    assert [reply["camera"] for reply in changed] == ["free"] * 3 + ["recorded"] + ["free"] * 2 + [
        "recorded"] + ["free"] * 2
    assert [reply["payload"] for reply in changed] == [reply["payload"] for reply in whole]
    assert len({reply["payload"] for reply in changed}) == 8                  # the last repeats the one before
    sent = [reply["timings"].get("send_bytes") for reply in changed]
    assert all(reply["timings"].get("send_bytes") in (None, synthetic.RDRAM) for reply in whole)
    assert sent[0] == synthetic.RDRAM                                            # the first sends everything
    assert all(0 < sent[at] < 4096 for at in (1, 2, 5)), sent                   # a new camera, the next frame
    assert all(0 < sent[at] < synthetic.RDRAM for at in (4, 7)), sent            # also undoes a recorded closure
    assert sent[3] is None and sent[6] is None and sent[8] == 0, sent           # the same view again: nothing


def test_free_pictures_moving_frame_by_frame_resolve_the_next_one_ahead(lab):
    evaluators = Evaluators()
    app, server = _app(lab, evaluators=evaluators)
    camera = {"focus": [1.0, 2.0, 3.0], "position": [100.0, 200.0, 300.0]}

    def free(socket, session, frame, expected):
        socket.send_json({"id": frame, "session": session, "frame": frame, "camera": camera, "encoding": "raw"})
        assert _reply(socket)["camera"] == "free"
        deadline = time.monotonic() + 10
        while len(evaluators.made[0].prepared) < expected and time.monotonic() < deadline:
            time.sleep(0.01)
        return [index for index, _thread in evaluators.made[0].prepared]

    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            session = opened["session"]
            first = [SHOWN[2], SHOWN[3], SHOWN[4]]
            assert free(socket, session, 1, 3) == first                         # the first: the next three
            assert free(socket, session, 2, 5) == first + [SHOWN[3], SHOWN[4]]  # stepping on (cached: no cost)
            assert free(socket, session, 2, 5) == first + [SHOWN[3], SHOWN[4]]  # a drag predicts nothing new
            assert free(socket, session, 4, 5) == first + [SHOWN[3], SHOWN[4]]  # the last frame: nothing after
            back = first + [SHOWN[3], SHOWN[4], SHOWN[2], SHOWN[1], SHOWN[0]]
            assert free(socket, session, 3, 8) == back                          # stepping back: the ones before
            assert free(socket, session, 1, 9) == back + [SHOWN[0]]             # back two: still backward
            assert free(socket, session, 4, 9) == back + [SHOWN[0]]             # a seek forward to the end
        threads = {thread for _index, thread in evaluators.made[0].prepared}
        assert len(threads) == 1 and threads.pop().startswith("scene-lab-ahead")
        client.post("/api/scene-lab/close")
        assert server.session is None
    assert not [thread for thread in threading.enumerate() if thread.name.startswith("scene-lab-ahead")]


def test_quick_steps_replace_the_request_waiting_behind_a_slow_render(lab):
    app, server = _app(lab, delay_ms=300)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            for position in range(5):
                socket.send_json({"id": position, "session": opened["session"], "frame": position,
                                  "camera": None, "encoding": "raw"})
            replies = [_reply(socket) for _ in range(5)]
        drawn = [reply["frame"] for reply in replies if "payload" in reply]
        superseded = sorted(reply["id"] for reply in replies if reply.get("superseded"))
        assert drawn == [0, 4] and superseded == [1, 2, 3]
        assert server.session.served == 2                               # the host drew nothing stale


class _FrameFlags:
    """A client's negotiated permessage-deflate, wrapped to note each data message's RSV1 bit
    (set: the server compressed it)."""

    def __init__(self, extension):
        self.extension, self.name, self.compressed = extension, extension.name, []

    def decode(self, frame, *, max_size=None):
        if frame.opcode.name in ("TEXT", "BINARY"):
            self.compressed.append(frame.rsv1)
        return self.extension.decode(frame, max_size=max_size)

    def encode(self, frame):
        return self.extension.encode(frame)


def _compressed_replies(lab) -> list[bool]:
    """Over a real uvicorn server (permessage-deflate on, as the app runs it, behind the Trainer's
    local access middleware) and a client that offers it, as browsers do: whether each reply to
    a pose, a picture and a bad request came compressed."""
    import socket as sockets

    import httpx
    import uvicorn
    from websockets.sync.client import connect

    from sm64_events.server.local_access import mount_local_access
    app, _server = _app(lab)
    listener = sockets.socket(sockets.AF_INET, sockets.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    mount_local_access(app, port=port, protected=True)
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on", ws="websockets"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        http = httpx.Client(base_url=origin, timeout=60)
        token = {"X-SM64-Request-Token": http.get("/api/local-access").json()["request_token"]}
        opened = http.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}, headers=token).json()
        with connect(f"ws://127.0.0.1:{port}/api/scene-lab/ws", origin=origin, compression="deflate") as socket:
            extensions = socket.protocol.extensions
            assert [extension.name for extension in extensions] == ["permessage-deflate"]
            extensions[0] = flags = _FrameFlags(extensions[0])
            socket.send(json.dumps({"id": 1, "session": opened["session"], "kind": "pose", "frame": 0}))
            assert json.loads(socket.recv(timeout=30))["kind"] == "pose"
            socket.send(json.dumps({"id": 2, "session": opened["session"], "frame": 0, "camera": None,
                                    "encoding": "jpeg"}))
            assert socket.recv(timeout=30)[4:8] != b""                         # a picture, decoded whole
            socket.send("not json")
            assert "error" in json.loads(socket.recv(timeout=30))
        assert http.post("/api/scene-lab/close", headers=token).json()["host_exit"] == 0
        http.close()
        return flags.compressed
    finally:
        server.should_exit = True
        thread.join(timeout=30)


def test_the_server_sends_uncompressed_where_the_page_offered_deflate(lab, monkeypatch):
    assert _compressed_replies(lab) == [False, False, False]
    monkeypatch.setattr(scene_lab_api, "_send_uncompressed", lambda websocket: False)
    assert _compressed_replies(lab) == [True, True, True]                     # what websockets does by itself


def test_the_event_loop_answers_while_the_host_draws(lab):
    app, _server = _app(lab, delay_ms=600, measure_loop=True)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": opened["session"], "frame": 0, "camera": None, "encoding": "raw"})
            time.sleep(0.1)                                             # the render is under way
            started = time.perf_counter()
            status = client.get("/api/scene-lab/status")
            answered = time.perf_counter() - started
            assert status.status_code == 200 and answered < 0.3, answered
            assert "payload" in _reply(socket)
            loop = client.get("/api/scene-lab/status").json()["loop"]
        assert loop["samples"] > 20 and loop["late_ms"]["max"] < 300, loop


# ---------------------------------------------------------------- lifetime


def test_close_ends_the_host_and_a_second_open_replaces_the_first(lab):
    app, server = _app(lab)
    with TestClient(app) as client:
        first = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        first_host = _host_process(server)
        assert first_host.poll() is None
        second = client.post("/api/scene-lab/open", json={"package": "scene-b.sm64scene"}).json()
        assert _wait_exit(first_host) == 0 and second["session"] == first["session"] + 1
        assert client.get("/api/scene-lab/status").json()["session"]["name"] == "scene-b.sm64scene"
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": first["session"], "frame": 0, "camera": None, "encoding": "raw"})
            assert _reply(socket)["closed"]                             # a request for the replaced scene
        second_host = _host_process(server)
        closed = client.post("/api/scene-lab/close").json()
        assert closed == {"closed": True, "host_exit": 0} and second_host.poll() == 0
        assert client.get("/api/scene-lab/status").json()["ended"]["code"] == "closed"


def test_an_idle_scene_releases_its_host(lab):
    app, server = _app(lab, idle_seconds=0.4)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        host = _host_process(server)
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": opened["session"], "frame": 0, "camera": None, "encoding": "raw"})
            assert "payload" in _reply(socket)
            status = _wait_closed(client)                               # the page stays open, unused
            assert status["ended"]["code"] == "idle" and _wait_exit(host) == 0
            socket.send_json({"id": 2, "session": opened["session"], "frame": 0, "camera": None, "encoding": "raw"})
            reply = _reply(socket)
            assert reply["closed"] and "without use" in reply["error"]


def test_a_scene_no_page_shows_is_released(lab):
    app, server = _app(lab, page_gone_seconds=0.3)
    with TestClient(app) as client:
        client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"})
        host = _host_process(server)
        with client.websocket_connect("/api/scene-lab/ws"):
            time.sleep(0.6)                                             # a connected page keeps it
            assert client.get("/api/scene-lab/status").json()["session"] is not None
        assert _wait_closed(client)["ended"]["code"] == "page" and _wait_exit(host) == 0
        client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"})
        never_shown = _host_process(server)                             # opened, and no page ever connects
        assert _wait_closed(client)["ended"]["code"] == "page" and _wait_exit(never_shown) == 0


def test_stopping_the_server_ends_the_host(lab):
    app, server = _app(lab)
    with TestClient(app) as client:
        client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"})
        host = _host_process(server)
    assert host.poll() == 0 and server.ended["code"] == "shutdown"


def test_a_host_that_dies_ends_the_scene_with_the_reason(lab):
    app, server = _app(lab, exit_after=2)                               # the warm-up and one picture
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        host = _host_process(server)
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            for ident in (1, 2):
                socket.send_json({"id": ident, "session": opened["session"], "frame": 0, "camera": None,
                                  "encoding": "raw"})
            assert "payload" in _reply(socket)
            reply = _reply(socket)
            assert reply["closed"] and "renderer stopped" in reply["error"]
        status = client.get("/api/scene-lab/status").json()
        assert status["session"] is None and status["ended"]["code"] == "renderer"
        assert host.poll() == 9


# ---------------------------------------------------------------- refusals


@pytest.mark.parametrize("name, code, words", [
    ("corrupt.sm64scene", 409, "cannot be opened"),
    ("truncated.sm64scene", 409, "truncated"),
    ("foreign.sm64scene", 409, "different ROM"),
    ("notes.txt", 404, "No scene package"),
    ("../lab.json", 404, "No scene package"),
    ("scene-a.sm64scene/../../lab.json", 404, "No scene package"),
    ("C:\\Windows\\win.ini", 404, "No scene package"),
])
def test_a_package_the_lab_cannot_trust_never_starts_a_host(lab, name, code, words):
    started = []
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, lab["config"], session_options={
        "host_factory": lambda header: started.append(header) or pytest.fail("a host started"),
        "evaluator_factory": Evaluators()})
    with TestClient(app) as client:
        response = client.post("/api/scene-lab/open", json={"package": name})
        assert response.status_code == code and words in response.json()["detail"], response.json()
        assert client.get("/api/scene-lab/status").json()["session"] is None and not started


def test_a_missing_rom_or_a_held_gpu_refuses_with_the_reason(lab, monkeypatch):
    app, _server = _app(lab)
    hold = lab["dir"] / "gpu-hold.txt"
    monkeypatch.setattr(gpu_gate, "HOLD", hold)
    with TestClient(app) as client:
        hold.write_text("Griffin is playing: pause anything heavy on the GPU", encoding="utf-8")
        assert "playing" in client.get("/api/scene-lab/status").json()["hold"]
        response = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"})
        assert response.status_code == 409 and "on hold" in response.json()["detail"]
        hold.write_text("Lifted by Griffin, 2026-10-24", encoding="utf-8")
        assert client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).status_code == 200
        client.post("/api/scene-lab/close")
        (lab["dir"] / "rom.z64").unlink()
        response = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"})
        assert response.status_code == 409 and "ROM" in response.json()["detail"]


def test_without_unicorn_or_a_map_only_the_recorded_camera_is_offered(lab, recording):
    package = scene_session.scene_package.Package(recording["package"], recording["rom"])
    evaluator = scene_session.GameCodeEvaluator(lab["dir"] / "MappingUS.map", Path("scene-a.sm64scene"))
    assert "no scene state" in evaluator.unavailable(package)      # this synthetic package is closures only
    app = FastAPI()
    scene_lab_api.mount_scene_lab(app, lab["config"], session_options={
        "host_factory": scene_session.command_host(fake_viewer_host.command())})
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"package": "scene-a.sm64scene"}).json()
        assert opened["free_camera"]["available"] is False and "no scene state" in opened["free_camera"]["reason"]
        with client.websocket_connect("/api/scene-lab/ws") as socket:
            socket.send_json({"id": 1, "session": opened["session"], "frame": 0,
                              "camera": {"focus": [0, 0, 0], "position": [0, 0, 500]}, "encoding": "raw"})
            header = _reply(socket)
            assert header["camera"] == "recorded" and "no scene state" in header["reason"]
            assert header["payload"] == _recorded(recording, 0)


def test_the_measurement_refuses_before_starting_anything_while_the_gpu_is_needed(lab, monkeypatch):
    import e3_measure
    monkeypatch.setattr(e3_measure, "Server", lambda *args: pytest.fail("a server started"))
    monkeypatch.setattr(e3_measure, "machine", lambda: pytest.fail("the run began"))
    hold = lab["dir"] / "gpu-hold.txt"
    monkeypatch.setattr(gpu_gate, "HOLD", hold)
    hold.write_text("pause anything heavy on the GPU", encoding="utf-8")
    arguments = ["--lab-config", str(lab["config"]), "--package", "scene-a.sm64scene"]
    with pytest.raises(SystemExit, match="GPU held"):
        e3_measure.main(arguments)
    hold.write_text("Lifted by Griffin", encoding="utf-8")
    monkeypatch.setattr(gpu_gate, "game_reasons", lambda ignore=(): ["Project64 is running"])
    with pytest.raises(SystemExit, match="Project64 is running"):
        e3_measure.main(arguments)


def test_the_measurement_dry_run_reports_every_number_through_the_real_routes(lab, tmp_path):
    import e3_measure
    out = tmp_path / "report.json"
    assert e3_measure.main(["--lab-config", str(lab["config"]), "--package", "scene-a.sm64scene", "--fake",
                            "--steps", "3", "--seeks", "4", "--drag-moves", "24", "--play-seconds", "0.3",
                            "--out", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["fake"] and report["frames"] == len(SHOWN) and report["open_to_first_picture_ms"] > 0
    assert report["recorded"]["step_forward_ms"]["count"] == 3 and report["recorded"]["seek_ms"]["count"] == 4
    free = report["free_camera"]
    assert free["first_picture_camera"] == "free" and free["recorded_fallbacks"] == 0
    assert free["step_ms"]["count"] == 3 and free["seek_ms"]["count"] == 4
    assert report["recorded"]["playback"]["pictures"] > 3 and free["playback"]["pictures"] > 3
    assert free["drag"]["inputs"] == 24 and 0 < free["drag"]["pictures"] <= 24
    assert free["drag"]["input_to_decoded_ms"]["count"] == free["drag"]["pictures"]
    assert report["loop"]["samples"] > 0 and report["host"]["running"]
    assert report["host_exit"] == 0 and report["host_after_close"]["running"] is False
    assert set(report["targets"]) == set(e3_measure.TARGETS)
    assert all(row["measured"] is not None for row in report["targets"].values())


def test_the_gpu_hold_reads_as_his_file_says(tmp_path, monkeypatch):
    hold = tmp_path / "gpu-hold.txt"
    monkeypatch.setattr(gpu_gate, "HOLD", hold)
    assert gpu_gate.hold_reason() is None
    hold.write_text("pause anything heavy on the GPU", encoding="utf-8")
    assert "pause anything heavy" in gpu_gate.hold_reason()
    with pytest.raises(SystemExit):
        gpu_gate.require_gpu()
    hold.write_text("Lifted by Griffin: GPU hold is lifted, continue", encoding="utf-8")
    assert gpu_gate.hold_reason() is None
