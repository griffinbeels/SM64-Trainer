"""Re-simulated scenes through the real routes (server/scene_resim.py and its hook in scene_lab_api.py).

The simulation's child is replaced by a runner that answers as ghost_resim.py's CLI does and hands back
a synthetic package (scene_attempt_fixture); the renderer is fake_viewer_host.py. What the simulation
itself makes is ghost_resim's own docstring and the measured runs; this pins the doors: what the child
is asked, what the page is told, and that the Save states switch holds at the moment route."""
import asyncio
import json
import sys
import threading
import time
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
    return {"folder": folder, "rom": folder / "rom.z64", "package": out, "session": session}


class Runner:
    """ghost_resim.py's CLI as the server sees it: one answer per command, the arguments kept."""

    def __init__(self, package: Path, start: str = "own_scene", refuse: str | None = None, load_ok: bool = True):
        self.package, self.start, self.refuse, self.load_ok = package, start, refuse, load_ok
        self.calls: list[list[str]] = []
        self.clips: list[bytes] = []
        self.done = threading.Event()

    def __call__(self, args, on_progress=None, exclusive=True):
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


def _lab(recording, tmp_path, monkeypatch, runner, builder=None, target=None):
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
        app, path, watch_seconds=0.05, ghost_loader=lambda ref: {"clip": CLIP, "label": ref, "target": target},
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


@pytest.mark.parametrize("target,act", [
    ({"kind": "star", "entity_key": "2-2", "strat": "Standard", "scope": "strategy"}, "3"),   # star 3: act 3
    ({"kind": "star", "entity_key": "2-0", "strat": "Standard", "scope": "strategy"}, "1"),
    ({"kind": "star", "entity_key": "2-6", "strat": "100c", "scope": "strategy"}, None),       # 100 coins: any act
    ({"kind": "star", "entity_key": "16-0", "strat": "Standard", "scope": "strategy"}, None),  # a castle star
    ({"kind": "segment", "entity_key": "7", "strat": "Standard", "scope": "strategy"}, None),
    (None, None),
])
def test_a_ghost_is_simulated_in_its_stars_act(recording, tmp_path, monkeypatch, target, act):
    """Mario alone cannot pick the act (another player's Whomp's Fortress ghost kept every pose in acts 1
    and 2), so the act comes from the listed ghost's star; without one the build chooses and says so."""
    runner = Runner(recording["package"])
    app, _lab_ = _lab(recording, tmp_path, monkeypatch, runner, target=target)
    with TestClient(app) as client:
        assert client.post("/api/scene-lab/open", json={"ghost": "ghost-1"}).status_code == 200
        client.post("/api/scene-lab/close")
    (args,) = runner.calls
    assert (_value(args, "--act") if "--act" in args else None) == act


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


class GrowingRunner:
    """ghost_resim's build as the fast load makes it (sim_scene.py): the package written in place, its first frames
    published with a sidecar and announced on a progress line, then the rest once the test lets it go on."""

    def __init__(self, recording, tmp_path, publish: int = 3, parting: int | None = None, fail: bool = False,
                 name: str = "ghost-growing"):
        self.recording, self.out = recording, tmp_path / "lab" / "resim" / f"{name}.sm64scene"
        self.publish, self.parting, self.fail = publish, parting, fail
        self.go = threading.Event()
        self.calls: list[list[str]] = []
        self.exclusive: list[bool] = []
        self.resim = None                                     # set by the test: a cancel ends this build

    def __call__(self, args, on_progress=None, exclusive=True):
        self.calls.append(args)
        self.exclusive.append(exclusive)
        if args[0] == "check-slot":
            return {"ok": True, "frames": 60, "compared": 60, "reason": None}
        pk = fixture.pk
        source = pk.tap_source(self.recording["session"], "next", states=True)
        self.out.parent.mkdir(parents=True, exist_ok=True)
        sidecar = self.out.with_suffix(".grow")
        writer = pk.PackageWriter(source, Path(self.recording["rom"]).read_bytes(), self.out, pk.ZLIB, in_place=True)
        captures = iter(source.captures)
        for _ in range(self.publish + 1):                     # the scene state runs one frame behind
            writer.add(next(captures))
        published = writer.publish(sidecar)
        if on_progress is not None:
            on_progress({"stage": "re-simulating", "done": published + 1, "total": 5, "package": str(self.out),
                         "sidecar": str(sidecar), "published": published, "start": "simulated_reset",
                         "built_to": published, "parting": self.parting,
                         "save_states": {"allowed": True, "reason": None, "until": self.parting, "check": "load"}})
        while not self.go.wait(0.05):
            if self.resim is not None and self.resim.building is not None and self.resim.building.cancelled:
                writer.abandon()
                raise ResimRefused("cancelled")
        if self.fail:
            writer.abandon()
            sidecar.unlink(missing_ok=True)
            raise ResimRefused("The simulated game stopped.")
        for capture in captures:
            writer.add(capture)
        writer.finish()
        sidecar.unlink()
        return {"path": str(self.out), "info": {"start": "simulated_reset", "parting_frame": self.parting,
                                                "first_record": 1, "exact": "mario", "records": 6,
                                                "save_states": {"allowed": True, "reason": None, "until": self.parting,
                                                                "check": "load"}}}


def _status(client, until, seconds: float = 20.0):
    for _ in range(int(seconds / 0.05)):
        status = client.get("/api/scene-lab/status").json()
        if until(status):
            return status
        threading.Event().wait(0.05)
    raise AssertionError(f"status never reached the wanted state: {status}")


def test_a_ghost_opens_at_its_first_frames_and_grows_while_it_builds(recording, tmp_path, monkeypatch):
    """The fast load (his words: "There's gotta be a fast load method."): the open answers at the first
    publication, a growing scene with a building table; the rest arrives as a grown scene; the last table
    replaces the building one. Meanwhile (the lead's ruling, 2026-11-08) a built frame before any parting is a
    save state, built from the part written so far and load-checked beside the build; a frame past the build
    head is refused with the sentence."""
    from sm64_events.server.scene_resim import BUILDING
    built = []

    def builder(config, path, frame, register=lambda child: None):
        built.append((Path(path).name, frame, Path(path).stat().st_size))
        return {"level": 24, "area": 1}, b"slot"

    runner = GrowingRunner(recording, tmp_path)
    app, lab = _lab(recording, tmp_path, monkeypatch, runner, builder=builder)
    with TestClient(app) as client:
        opened = client.post("/api/scene-lab/open", json={"ghost": "ghost-1"})
        assert opened.status_code == 200, opened.text
        scene = opened.json()
        assert scene["growing"] is True and len(scene["frames"]) == 3
        assert scene["resim"] == {"start": "simulated_reset", "building": True, "records": 6, "first_record": 1,
                                  "built_to": 3, "parting_frame": None,
                                  "save_states": {"allowed": True, "reason": None, "until": None, "check": "load"}}
        saved = client.post("/api/scene-lab/moment", json={"ghost": "ghost-1", "frame": 2})   # record 3: built
        assert saved.status_code == 200, saved.text
        assert saved.json()["checked"] == {"frames": 60, "compared": 60} and "id" in saved.json()
        assert built and built[0][:2] == ("ghost-growing.sm64scene", 2)          # the part written so far
        later = client.post("/api/scene-lab/moment", json={"ghost": "ghost-1", "frame": 3})   # record 4: not yet
        assert later.status_code == 200 and later.json() == {"refused": "building", "reason": BUILDING}
        runner.go.set()
        status = _status(client, lambda s: s["session"] and not s["session"]["resim"].get("building"))
        assert status["session"]["growing"] is False and len(status["session"]["frames"]) == 6
        assert status["session"]["resim"]["parting_frame"] is None and status["session"]["resim"]["exact"] == "mario"
        client.post("/api/scene-lab/close")
    builds = [args for args in runner.calls if args[0] == "build"]
    checks = [exclusive for args, exclusive in zip(runner.calls, runner.exclusive, strict=True) if args[0] == "check-slot"]
    assert len(builds) == 1 and checks == [False]            # one build; the load check ran beside it


def test_a_frame_from_a_parting_found_while_building_makes_no_save_state(recording, tmp_path, monkeypatch):
    runner = GrowingRunner(recording, tmp_path, parting=2)
    app, lab = _lab(recording, tmp_path, monkeypatch, runner, builder=lambda *args, **kwargs: ({}, b"slot"))
    with TestClient(app) as client:
        scene = client.post("/api/scene-lab/open", json={"ghost": "ghost-1"}).json()
        assert scene["resim"]["parting_frame"] == 2 and scene["resim"]["built_to"] == 3
        assert client.post("/api/scene-lab/moment", json={"ghost": "ghost-1", "frame": 0}).status_code == 200
        parted = client.post("/api/scene-lab/moment", json={"ghost": "ghost-1", "frame": 1})   # record 2: parted
        assert parted.status_code == 409 and "no longer matches the ghost" in parted.json()["detail"]
        runner.go.set()


def test_a_newer_open_or_the_cancel_route_ends_an_unfinished_build(recording, tmp_path, monkeypatch):
    """One build at a time: the cancel route ends the running one (its partial package goes; the open scene keeps
    what it has); a second open of the same ghost joins its build."""
    runner = GrowingRunner(recording, tmp_path)
    app, lab = _lab(recording, tmp_path, monkeypatch, runner)
    runner.resim = lab.resim
    with TestClient(app) as client:
        assert client.post("/api/scene-lab/open", json={"ghost": "ghost-1"}).status_code == 200
        again = client.post("/api/scene-lab/open", json={"ghost": "ghost-1"})
        assert again.status_code == 200 and again.json()["resim"]["building"] is True
        assert len([args for args in runner.calls if args[0] == "build"]) == 1      # joined, not built twice
        assert client.post("/api/scene-lab/open/cancel").json() == {"cancelled": True}
        assert not runner.out.exists() and not runner.out.with_suffix(".grow").exists()
        status = _status(client, lambda s: s["session"] and s["session"]["resim"].get("cancelled"))
        assert status["session"]["growing"] is False and status["session"]["resim"]["building"] is False
        assert len(status["session"]["frames"]) == 3                                  # it keeps what it had
        assert client.post("/api/scene-lab/open/cancel").json() == {"cancelled": False}
        client.post("/api/scene-lab/close")


def test_a_build_that_fails_after_it_began_playing_closes_the_scene_with_the_sentence(recording, tmp_path, monkeypatch):
    runner = GrowingRunner(recording, tmp_path, fail=True)
    app, lab = _lab(recording, tmp_path, monkeypatch, runner)
    with TestClient(app) as client:
        assert client.post("/api/scene-lab/open", json={"ghost": "ghost-1"}).status_code == 200
        runner.go.set()
        status = _status(client, lambda s: s["session"] is None and s["ended"])
        assert status["ended"]["message"] == "The simulated game stopped."


class WarmChild:
    """A background warm child as the warmer sees it: it ends when the test lets it."""

    def __init__(self, gate: threading.Event):
        self.gate, self.returncode = gate, None

    def communicate(self, timeout=None):
        self.gate.wait(10)
        self.returncode = 0
        return '{"warm": "stage", "seconds": 5.6}\n', ""

    def poll(self):
        return self.returncode

    def kill(self):
        self.gate.set()


def test_stages_are_warmed_in_his_order_once_each_in_the_background(recording, tmp_path, monkeypatch):
    """The lead's order (2026-11-08): the star he is looking at first, then the targets on a list he is looking at,
    then his practice route's stars, then every other stage, the ones without an act last (courses 16-24, the
    arenas); each once; one child at a time, started at idle priority on one core (background=True); a segment
    warms nothing."""
    from sm64_events.memory import version_probe
    from sm64_events.server import scene_warm
    from sm64_events.server.scene_resim import SceneResim
    spawned, gate = [], threading.Event()

    def spawn(args, *, background=False):
        spawned.append(((int(_value(args, "--level")), int(_value(args, "--area")), int(_value(args, "--act"))),
                        background))
        if len(spawned) > 1:
            gate.set()                                       # the rest run without waiting
        return WarmChild(gate)
    monkeypatch.setattr(SceneResim, "_spawn", staticmethod(spawn))
    monkeypatch.setattr(version_probe, "version_from_header", lambda header: "us")   # the fixture ROM is no Usamune
    app, lab = _lab(recording, tmp_path, monkeypatch, Runner(recording["package"]))
    lab.tracker = type("Tracker", (), {"active_route": lambda self: {"star_keys": ["9:5", "4:0", "2:2"]}})()
    monkeypatch.setattr(lab.ghosts, "stage", lambda target, strat, every=False: {
        "target": {}, "attempt": None, "time": None, "reason": "none", "scenes": []})
    monkeypatch.setattr(lab.ghosts, "ghosts", lambda *args, **kwargs: {"items": []})
    monkeypatch.setattr(lab.ghosts, "fill", lambda *args, **kwargs: asyncio.sleep(0))
    with TestClient(app) as client:
        assert client.get("/api/scene-lab/stage", params={"course": 2, "star": 2}).status_code == 200   # current
        for _ in range(200):                                 # the first warm is running and holds the queue
            if spawned:
                break
            threading.Event().wait(0.01)
        client.get("/api/scene-lab/ghosts", params={"section": "top", "course": 5, "star": 0})        # a list
        client.get("/api/scene-lab/stage", params={"course": 2, "star": 2})                           # again
        client.get("/api/scene-lab/stage", params={"segment": 7})
        client.get("/api/scene-lab/stage", params={"course": 17, "star": 0})                           # act-less
        gate.set()
        for _ in range(500):
            if len(spawned) >= len(scene_warm.rest()):
                break
            threading.Event().wait(0.01)
        lab.resim.warmer.stop()
    keys = [key for key, _background in spawned]
    wf, bitfs, bbh, ddd, ccm, bob = 24, 19, 4, 23, 5, 9     # levels of courses 2, 17, 5, 9, 4, 1 (memory/addresses.py)
    assert keys[:6] == [(wf, 1, 3),                  # the star he opened first, already running
                        (bitfs, 1, 1),               # the second star he opened (current beats the list)
                        (bbh, 1, 1),                 # a target on the list he looked at
                        (ddd, 1, 6), (ccm, 1, 1),    # his route: 9:5 and 4:0 (2:2 was made already)
                        (bob, 1, 1)]                 # then the rest, course 1 act 1 first
    assert keys[-3:] == [(30, 1, 1), (33, 1, 1), (34, 1, 1)]                        # the arenas end the rest
    assert len(scene_warm.rest()) == 102 and len(keys) == len(set(keys)) == 102     # 90 acts, 9 courses, 3 arenas
    assert all(background for _key, background in spawned)


def test_a_click_joins_the_warm_of_its_own_stage_and_a_slow_game_holds_warming_back():
    """A click on a ghost of a stage being warmed waits for that warm (raised to his priority) instead of making
    the stage again; while his game runs below its pace no warm starts."""
    from sm64_events.server import scene_warm
    gate, spawned = threading.Event(), []
    warmer = scene_warm.StageWarmer(lambda *stage: spawned.append(stage) or WarmChild(gate))
    warmer.ask((24, 1, 3))
    for _ in range(200):
        if warmer.running == (24, 1, 3):
            break
        threading.Event().wait(0.01)
    threading.Timer(0.3, gate.set).start()
    began = time.monotonic()
    assert warmer.join((24, 1, 3)) is True and time.monotonic() - began >= 0.25   # waited for the warm, then went on
    warmer.stop()
    assert warmer.join((22, 1, 1)) is False and (22, 1, 1) in warmer.done          # nothing to join: the build makes it
    clock, timer = [0.0], [1000]
    pace = scene_warm.GamePace(lambda: timer[0], clock=lambda: clock[0])
    for step in range(12):                                   # 2 s at 20 game frames a second: slow
        clock[0], timer[0] = step * 0.2, 1000 + step * 4
        slow = pace.hurts()
    assert slow is True
    for step in range(12, 24):                               # 30 a second again
        clock[0], timer[0] = step * 0.2, timer[0] + 6
        slow = pace.hurts()
    assert slow is False
    assert scene_warm.GamePace(lambda: None).hurts() is False   # no game: never held back


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
