"""Re-simulated scenes through the real routes (server/scene_resim.py and its hook in scene_lab_api.py).

The simulation's child is replaced by a runner that answers as ghost_resim.py's CLI does and hands back
a synthetic package (scene_attempt_fixture); the renderer is fake_viewer_host.py. What the simulation
itself makes is ghost_resim's own docstring and the measured runs; this pins the doors: what the child
is asked, what the page is told, and that the Save states switch holds at the moment route."""
import json
import sys
import threading
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import scene_attempt_fixture as fixture  # noqa: E402
import scene_session  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402
from sm64_events.server.scene_resim import ResimRefused  # noqa: E402

CLIP = b"SM64GHO7" + bytes(200)


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    folder = tmp_path_factory.mktemp("scene-resim")
    rom = bytearray(fixture.rom())
    rom[0x3E] = ord("E")
    rom = bytes(rom)
    session = fixture.write_session(folder, rom, [(timer, 24, 1) for timer in range(1000, 1006)])
    out = folder / "scene.sm64scene"
    fixture.pk.write_package(fixture.pk.tap_source(session, "next", states=True), rom, out, fixture.pk.ZLIB)
    (folder / "rom.z64").write_bytes(rom)
    return {"folder": folder, "rom": folder / "rom.z64", "package": out}


class Runner:
    """ghost_resim.py's CLI as the server sees it: one answer per command, the arguments kept."""

    def __init__(self, package: Path, start: str = "own_scene", refuse: str | None = None, load_ok: bool = True):
        self.package, self.start, self.refuse, self.load_ok = package, start, refuse, load_ok
        self.calls: list[list[str]] = []
        self.clips: list[bytes] = []
        self.done = threading.Event()

    def __call__(self, args):
        self.calls.append(args)
        if "--clip" in args:
            self.clips.append(Path(args[args.index("--clip") + 1]).read_bytes())
        if self.refuse:
            raise ResimRefused(self.refuse)
        if args[0] == "capture":
            self.done.set()
            return {"path": "start"}
        if args[0] == "check-slot":
            return {"ok": self.load_ok, "frames": 60, "compared": 60,
                    "reason": None if self.load_ok else "After this save state loads, Mario does not go on the way the ghost did."}
        allowed = self.start in ("own_scene", "ghost_start")
        return {"path": str(self.package), "info": {
            "start": self.start, "parting_frame": 120 if not allowed else None, "first_record": 1, "exact": "bytes",
            "save_states": {"allowed": allowed, "reason": None if allowed else "a reset"}}}


def _lab(recording, tmp_path, monkeypatch, runner, builder=None):
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    (tmp_path / "Plugin").mkdir(exist_ok=True)
    (tmp_path / "packages").mkdir(exist_ok=True)
    (tmp_path / "MappingUS.map").write_text("", encoding="utf-8")
    path = tmp_path / "lab.json"
    path.write_text(json.dumps({"rom": str(recording["rom"]), "packages": str(tmp_path / "packages"),
                                "pj64_plugin": str(tmp_path / "Plugin"), "map": str(tmp_path / "MappingUS.map"),
                                "lab_root": str(tmp_path / "lab")}), encoding="utf-8")
    app = FastAPI()
    options = {"builder": builder} if builder else {}
    lab = scene_lab_api.mount_scene_lab(
        app, path, watch_seconds=0.05, ghost_loader=lambda ref: {"clip": CLIP, "label": ref},
        session_options={"host_factory": scene_session.command_host(fake_viewer_host.command())},
        resim_options={"runner": runner, "starts_root": tmp_path / "population"}, moment_options=options)
    return app, lab


def _value(args, flag):
    return args[args.index(flag) + 1]


def test_a_ghost_opens_as_its_re_simulated_scene(recording, tmp_path, monkeypatch):
    runner = Runner(recording["package"])
    app, _lab_ = _lab(recording, tmp_path, monkeypatch, runner)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"ghost": "ghost-1"})
        assert opened.status_code == 200, opened.text
        scene = opened.json()
        assert scene["resim"]["start"] == "own_scene" and scene["resim"]["parting_frame"] is None
        assert scene["frames"]
        status = client.get("/api/scene-lab/status").json()
        assert status["session"]["resim"]["start"] == "own_scene"
        client.post("/api/scene-lab/close")
    (args,) = runner.calls
    assert args[0] == "build" and runner.clips == [CLIP]                     # the catalog's own clip bytes
    scenes = [Path(args[at + 1]) for at, part in enumerate(args) if part == "--scenes"]
    assert scenes == [tmp_path / "lab" / "attempts"]          # no journal open here: the legacy folder only
    assert Path(_value(args, "--starts")) == tmp_path / "population"
    assert Path(_value(args, "--cache")) == tmp_path / "lab" / "resim"


def test_a_stage_opens_by_course_and_act(recording, tmp_path, monkeypatch):
    runner = Runner(recording["package"], start="simulated_reset")
    app, _lab_ = _lab(recording, tmp_path, monkeypatch, runner)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"stage": {"course": 2, "act": 1}})
        assert opened.status_code == 200, opened.text
        assert opened.json()["resim"]["start"] == "simulated_reset"
    (args,) = runner.calls
    assert args[0] == "stage" and (_value(args, "--course"), _value(args, "--act"), _value(args, "--area")) == ("2", "1", "1")


@pytest.mark.parametrize("body", [{"ghost": "a", "package": "scene.sm64scene"}, {"ghost": "a", "attempt": 3},
                                  {"stage": {"course": 2, "act": 1}, "ghost": "a"}, {"stage": {"course": 0, "act": 1}}, {}])
def test_an_open_names_exactly_one_source(recording, tmp_path, monkeypatch, body):
    app, _lab_ = _lab(recording, tmp_path, monkeypatch, Runner(recording["package"]))
    with TestClient(app) as client:
        assert client.post("/api/scene-lab/open", json=body).status_code == 422


def test_a_ghost_the_simulation_refuses_says_why(recording, tmp_path, monkeypatch):
    app, _lab_ = _lab(recording, tmp_path, monkeypatch, Runner(recording["package"], refuse="No start could run this ghost."))
    with TestClient(app) as client:
        refused = client.post("/api/scene-lab/open", json={"ghost": "ghost-1"})
        assert refused.status_code == 409 and "No start could run" in refused.json()["detail"]


@pytest.mark.parametrize("start, frame, load_ok, outcome", [
    ("own_scene", 3, True, "written"),                    # his own scene: no check
    ("simulated_reset", 3, True, "checked"),              # before the parting frame (120), and the load check passes
    ("another_reset", 3, False, "check refused"),         # the load check fails: the row says why
    ("simulated_reset", 119, True, "parted"),             # package frame 119 is record 120: the run parted there
])
def test_the_save_states_switch_holds_at_the_moment_route(recording, tmp_path, monkeypatch, start, frame, load_ok, outcome):
    """His decision (2026-11-08): a rebuilt start makes save states before its parting frame, each after a load
    check in the simulation (ghost_resim.SAVE_STATES); his own scene's need none."""
    built = []

    def builder(config, path, frame, register=lambda child: None):
        built.append((path, frame))
        return {"level": 24, "area": 1}, b"slot"

    runner = Runner(recording["package"], start=start, load_ok=load_ok)
    app, _lab_ = _lab(recording, tmp_path, monkeypatch, runner, builder=builder)
    with TestClient(app) as client:
        answer = client.post("/api/scene-lab/moment", json={"ghost": "ghost-1", "frame": frame})
    checks = [args for args in runner.calls if args[0] == "check-slot"]
    if outcome == "parted":
        assert answer.status_code == 409 and "no longer matches the ghost" in answer.json()["detail"]
        assert built == [] and checks == []
        return
    assert answer.status_code == 200, answer.text
    assert built == [(recording["package"], frame)]
    if outcome == "written":
        assert checks == [] and "refused" not in answer.json()
    elif outcome == "checked":
        (args,) = checks
        assert args[args.index("--frame") + 1] == str(frame) and args[args.index("--first-record") + 1] == "1"
        assert answer.json()["checked"] == {"frames": 60, "compared": 60}
    else:
        assert answer.json() == {"refused": "check", "reason": "After this save state loads, Mario does not go on the way the ghost did."}


def test_a_ready_scene_keeps_its_runs_start(recording, tmp_path, monkeypatch):
    """The cutter's ready event reaches the start capture with the scene and his run's clip."""
    runner = Runner(recording["package"])
    _app, lab = _lab(recording, tmp_path, monkeypatch, runner)
    monkeypatch.setattr(lab.resim, "_run_clip", lambda attempt_id: CLIP if attempt_id == 41 else None)
    lab.resim.observe_cutter({"id": 41, "state": "cutting"})
    lab.resim.observe_cutter({"id": 41, "state": "ready", "growing": True, "path": "x.sm64scene"})
    lab.resim.observe_cutter({"id": 41, "state": "ready", "path": str(tmp_path / "take-1.sm64scene")})
    assert runner.done.wait(10)
    (args,) = runner.calls
    assert args[0] == "capture" and Path(_value(args, "--scene")) == tmp_path / "take-1.sm64scene"
    assert runner.clips == [CLIP] and Path(_value(args, "--starts")) == tmp_path / "population"


def test_the_stage_falls_through_to_the_ghost_when_he_has_no_scene_of_it(recording, tmp_path, monkeypatch):
    """The Library's Ghost button: /stage names what POST /open takes, his scene first, else that ghost rebuilt."""
    from sm64_events.server import scene_lab_api as api
    found = {"target": {}, "attempt": None, "time": None, "reason": "He has no scene of this star yet.", "scenes": []}
    assert api._stage_open(found, "ghost-7") == {**found, "open": {"ghost": "ghost-7"}, "resim": True, "reason": None}
    assert api._stage_open(found, None)["open"] is None
    his = {**found, "attempt": 12, "reason": None}
    assert api._stage_open(his, "ghost-7")["open"] == {"attempt": 12}


def test_a_first_build_shows_its_progress_on_status(recording, tmp_path, monkeypatch):
    """The child's progress lines reach /status while it runs; its last line is the answer; then nothing shows."""
    import sys as system
    from sm64_events.core import childproc
    from sm64_events.server.scene_resim import SceneResim
    flag = tmp_path / "go"
    script = ("import json, pathlib, time\n"
              "print(json.dumps({'progress': {'stage': 're-simulating', 'done': 40, 'total': 396}}), flush=True)\n"
              f"while not pathlib.Path({str(flag)!r}).exists(): time.sleep(0.02)\n"
              "print(json.dumps({'path': 'scene.sm64scene', 'info': {'start': 'own_scene'}}), flush=True)\n")
    monkeypatch.setattr(childproc, "python_child", lambda *args, **kwargs: ([system.executable, "-c", script], {}))
    lab = type("Lab", (), {})()
    resim = SceneResim(lab)
    answer = {}
    worker = threading.Thread(target=lambda: answer.update(resim._run_child(["build"])))
    worker.start()
    try:
        for _ in range(500):
            if resim.progress and resim.progress.get("done") == 40:
                break
            threading.Event().wait(0.02)
        assert resim.progress == {"what": "build", "stage": "re-simulating", "done": 40, "total": 396}
    finally:
        flag.write_text("go", encoding="utf-8")
        worker.join(30)
    assert answer == {"path": "scene.sm64scene", "info": {"start": "own_scene"}} and resim.progress is None
