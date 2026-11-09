"""The Ghost Lab in the installed app: no lab.json, its config from the app's own inputs (core/lab_inputs.py).

Griffin, 2026-11-09: "I want it all built into a single, actual beta candidate (ghost lab + save states and all
that included)." The installed app (frozen) mounts the Ghost Lab by itself (D2, on by default) from the ROM store,
the bundled maps and the Plugin folder setup found; without a ROM or a set-up Project64 it still mounts and every
door says why in a plain sentence (D6). SM64_SCENE_LAB stays the developer's override (test_scene_lab_viewer.py
holds that path), and a source checkout without it still has no lab.

"Frozen" here is core/lab_inputs.py's own seam, never sys.frozen: core/paths.py reads that one and would aim the
test at his real user data. The ROM store (interface I3) and the bundled maps (I2) are doubles shaped like their
frozen interfaces; the renderer is fake_viewer_host.py (a child, no window, no GPU)."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import cold_boot  # noqa: E402
import fake_viewer_host  # noqa: E402
import gpu_gate  # noqa: E402
import scene_session  # noqa: E402
import stage_sim  # noqa: E402
import test_cold_boot  # noqa: E402
import test_scene_lab_viewer as viewer_tests  # noqa: E402
from sm64_events.core import lab_inputs  # noqa: E402
from sm64_events.server import scene_lab_api  # noqa: E402

NOT_FOUND = "The Ghost Lab needs the Super Mario 64 Usamune v1.93u ROM. Open it in Project64 once (a test's words)."
PACKAGE = "synthetic.sm64scene"


class Store:
    """sm64_events/scenelab/roms.py's interface (I3): rom_for(region) -> Path | None, missing_reason(region)."""

    def __init__(self, roms: dict | None = None):
        self.roms = dict(roms or {})

    def rom_for(self, region):
        return self.roms.get(region)

    def missing_reason(self, region):
        return None if self.roms.get(region) else NOT_FOUND.replace("v1.93u", "v1.93j" if region == "jp" else "v1.93u")


@pytest.fixture
def app_lab(tmp_path, monkeypatch):
    """What the installed app has instead of a lab.json: a ROM in its store, its bundled maps, a set-up Project64,
    and a lab folder (scratch) holding the synthetic recording as a package."""
    monkeypatch.setattr(gpu_gate, "HOLD", tmp_path / "no-hold.txt")
    rom, segment7 = viewer_tests.synthetic._rom()
    (tmp_path / "store").mkdir()
    (tmp_path / "store" / "us.z64").write_bytes(rom)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    for name in lab_inputs.MAP_NAMES.values():
        (bundle / name).write_text("", encoding="utf-8")
    (tmp_path / "Plugin").mkdir()
    root = tmp_path / "lab-root"
    (root / "packages").mkdir(parents=True)
    session = tmp_path / "tap-synthetic"
    session.mkdir()
    synthetic = viewer_tests.synthetic
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
    viewer_tests._write_package(session, rom, root / "packages" / PACKAGE)
    store = Store({"us": tmp_path / "store" / "us.z64"})

    def inputs(**overrides):
        return lab_inputs.app_inputs(store=overrides.get("store", store),
                                     lab_file=lambda name: bundle / name if (bundle / name).is_file() else None,
                                     plugin=overrides.get("plugin", lambda: tmp_path / "Plugin"), lab_root=root)
    return {"dir": tmp_path, "store": store, "bundle": bundle, "root": root, "inputs": inputs, "rom": rom}


def _mount(app_inputs, **options):
    app = FastAPI()
    lab = scene_lab_api.mount_scene_lab(
        app, None, app_inputs=app_inputs, watch_seconds=0.05,
        session_options={"host_factory": scene_session.command_host(fake_viewer_host.command()),
                         "evaluator_factory": lambda package, rom: fake_viewer_host.FakeEvaluator()}, **options)
    return app, lab


# ---------------------------------------------------------------- the switch, through main.py


def test_the_installed_app_mounts_the_ghost_lab_with_no_lab_config(app_lab, monkeypatch):
    """main.py's one switch: frozen, with no SM64_SCENE_LAB, the lab mounts from the app's own inputs (the ROM
    store, the bundled maps, setup's Plugin folder) and is ready; a source checkout without the variable, or the
    app with IN_APP off, has no lab at all."""
    import sm64_events.main as main
    monkeypatch.delenv("SM64_SCENE_LAB", raising=False)
    monkeypatch.setattr(lab_inputs, "frozen", lambda: True)
    monkeypatch.setattr(lab_inputs, "_store", lambda: app_lab["store"])
    bundle = app_lab["bundle"]
    monkeypatch.setattr(lab_inputs, "_lab_file", lambda: lambda name: bundle / name)
    app = main._mount_scene_lab(FastAPI(), plugin_dir=lambda: app_lab["dir"] / "Plugin")
    lab = app.state.scene_lab
    assert lab.config_path is None
    config = lab.config()
    assert config.in_app and config.rom == app_lab["dir"] / "store" / "us.z64"
    assert config.map == bundle / "MappingUS.map" and config.pj64_plugin == app_lab["dir"] / "Plugin"
    with TestClient(app) as client:
        assert client.get("/api/scene-lab").json()["lab"] == "scene-viewer"
        status = client.get("/api/scene-lab/status").json()
        assert status["ready"] and status["problems"] == [] and status["hold"] is None
    monkeypatch.setattr(lab_inputs, "IN_APP", False)                 # D2's one switch, off
    off = main._mount_scene_lab(FastAPI())
    with TestClient(off) as client:
        assert client.get("/api/scene-lab").json() == {"lab": None}
    monkeypatch.setattr(lab_inputs, "IN_APP", True)
    monkeypatch.setattr(lab_inputs, "frozen", lambda: False)         # a source checkout: as before, no lab
    source = main._mount_scene_lab(FastAPI())
    with TestClient(source) as client:
        assert client.get("/api/scene-lab").json() == {"lab": None}


def test_a_developer_lab_config_still_wins_in_the_installed_app(app_lab, monkeypatch, tmp_path):
    import sm64_events.main as main
    config = tmp_path / "dev-lab.json"
    config.write_text(json.dumps({"rom": str(app_lab["dir"] / "store" / "us.z64"),
                                  "packages": str(app_lab["root"] / "packages"),
                                  "pj64_plugin": str(app_lab["dir"] / "Plugin"), "map": str(tmp_path / "x.map")}),
                      encoding="utf-8")
    monkeypatch.setenv("SM64_SCENE_LAB", str(config))
    monkeypatch.setattr(lab_inputs, "frozen", lambda: True)
    lab = main._mount_scene_lab(FastAPI()).state.scene_lab
    assert lab.config_path == config and not lab.config().in_app and lab.config().map == tmp_path / "x.map"


# ---------------------------------------------------------------- D6: the lab mounts and says why


def test_with_no_rom_or_no_project64_the_lab_mounts_and_says_why(app_lab):
    """No ROM in the store yet: the status names the store's own sentence, and opening anything answers it with a
    409 instead of failing; with Project64 not set up, the setup sentence joins it. A ROM the store finds later
    counts at the next ask, with no restart."""
    empty = Store()
    app, lab = _mount(lambda: app_lab["inputs"](store=empty, plugin=lambda: None))
    with TestClient(app) as client:
        assert client.get("/api/scene-lab").json()["lab"] == "scene-viewer"
        status = client.get("/api/scene-lab/status").json()
        assert not status["ready"]
        assert status["problems"] == [NOT_FOUND, lab_inputs.NO_PLUGIN]
        refused = client.post("/api/scene-lab/open", json={"package": PACKAGE})
        assert refused.status_code == 409 and refused.json()["detail"] == NOT_FOUND
        refused = client.post("/api/scene-lab/open", json={"stage": {"course": 1, "act": 1}})
        assert refused.status_code == 409 and refused.json()["detail"] == NOT_FOUND
        stage = client.get("/api/scene-lab/stage", params={"course": 1, "star": 0}).json()
        assert stage["reason"] == NOT_FOUND and stage["open"] is None      # the line the page shows by the stage
        empty.roms["us"] = app_lab["store"].roms["us"]               # the store found it while the app ran
        status = client.get("/api/scene-lab/status").json()
        assert status["problems"] == [lab_inputs.NO_PLUGIN]
        stage = client.get("/api/scene-lab/stage", params={"course": 1, "star": 0}).json()
        assert stage["reason"] == lab_inputs.NO_PLUGIN


def test_the_apps_config_opens_a_scene_through_the_real_session(app_lab):
    """The config from_app builds is the one SceneSession.open reads: a listed package opens and draws."""
    app, _lab = _mount(app_lab["inputs"])
    with TestClient(app) as client:
        status = client.get("/api/scene-lab/status").json()
        assert status["ready"] and [row["name"] for row in status["packages"]] == [PACKAGE]
        opened = client.post("/api/scene-lab/open", json={"package": PACKAGE})
        assert opened.status_code == 200, opened.text
        assert [frame["package_frame"] for frame in opened.json()["frames"]] == viewer_tests.SHOWN
        client.post("/api/scene-lab/close")


def test_app_inputs_prefer_the_us_rom_and_name_every_missing_piece(tmp_path, monkeypatch):
    rom = tmp_path / "jp.z64"
    rom.write_bytes(b"x")
    bundle = tmp_path / "MappingJP.map"
    bundle.write_text("", encoding="utf-8")
    plugin = tmp_path / "Plugin"
    plugin.mkdir()
    jp_only = lab_inputs.app_inputs(store=Store({"jp": rom}), lab_file=lambda name: tmp_path / name,
                                    plugin=lambda: plugin)
    assert jp_only["region"] == "jp" and jp_only["rom"] == rom and jp_only["map"] == bundle
    assert jp_only["roms"] == {"us": None, "jp": rom} and jp_only["problems"] == []
    assert jp_only["rom_problems"]["us"] == NOT_FOUND
    no_map = lab_inputs.app_inputs(store=Store({"jp": rom}), lab_file=lambda name: None, plugin=lambda: plugin)
    assert no_map["problems"] == [lab_inputs.NO_MAP]
    # A build without the ROM store (never the real store: it would read this computer's Project64 settings).
    monkeypatch.setattr(lab_inputs, "_store", lambda: None)
    broken = lab_inputs.app_inputs(store=None, lab_file=lambda name: None, plugin=lambda: None)
    assert broken["rom"] is None and broken["problems"][0] in (lab_inputs.NO_STORE, NOT_FOUND)


def test_a_bundled_lab_module_loads_by_its_bare_name_when_frozen(monkeypatch):
    """Frozen, _lab_module asks sm64_events/scenelab_code.py (interface I1) and never the checkout's tools folder;
    a bundle without it says so in a sentence instead of an import error."""
    import types
    asked = []
    fake = types.ModuleType("sm64_events.scenelab_code")
    fake.lab_module = lambda name: asked.append(name) or types.SimpleNamespace(name=name)
    monkeypatch.setitem(sys.modules, "sm64_events.scenelab_code", fake)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    try:
        assert scene_lab_api._lab_module("gpu_gate").name == "gpu_gate" and asked == ["gpu_gate"]
        fake.lab_module = lambda name: (_ for _ in ()).throw(ModuleNotFoundError(name))
        with pytest.raises(scene_lab_api.ConfigError, match="Reinstall the Trainer"):
            scene_lab_api._lab_module("gpu_gate")
    finally:
        monkeypatch.delattr(sys, "frozen")


# ---------------------------------------------------------------- attempt scenes wait for the ROM


class Cutter:
    def __init__(self):
        self.notes, self.closed = [], False

    def send(self, note):
        self.notes.append(note)

    def close(self, timeout=10.0):
        self.closed = True
        return 0


def test_attempt_scenes_wait_for_the_rom_then_start_with_every_note_kept(app_lab, monkeypatch):
    """A new player's first session: the scene tap records from the start, but the cutter needs the ROM. Until the
    store has one the recording says the store's sentence and notes wait in order; the cutter starts once the ROM
    turns up (no restart) and gets them, then every later note."""
    monkeypatch.setattr(scene_lab_api.RomWait, "POLL_SECONDS", 0.02)
    empty, made = Store(), []
    lab = scene_lab_api.SceneLab(None, app_inputs=lambda: app_lab["inputs"](store=empty),
                                 cutter_factory=lambda folder, on_event: made.append(Cutter()) or made[-1])
    told = []
    cutter = lab._start_cutter(app_lab["root"] / "attempts", told.append)
    assert isinstance(cutter, scene_lab_api.RomWait) and made == []
    assert told == [{"live": {"recording": False, "reason": NOT_FOUND}}]
    cutter.send({"op": "start", "frame": 1})
    cutter.send({"op": "complete", "attempt": 7})
    time.sleep(0.1)
    assert made == []                                          # still no ROM: nothing started
    empty.roms["us"] = app_lab["store"].roms["us"]
    deadline = time.monotonic() + 5
    while not made and time.monotonic() < deadline:
        time.sleep(0.01)
    assert len(made) == 1
    cutter.send({"op": "stop"})
    assert made[0].notes == [{"op": "start", "frame": 1}, {"op": "complete", "attempt": 7}, {"op": "stop"}]
    cutter.close()
    assert made[0].closed


def test_with_a_rom_the_apps_cutter_starts_at_once(app_lab):
    made = []
    lab = scene_lab_api.SceneLab(None, app_inputs=app_lab["inputs"],
                                 cutter_factory=lambda folder, on_event: made.append(Cutter()) or made[-1])
    assert lab._start_cutter(app_lab["root"] / "attempts", lambda event: None) is made[0]


# ---------------------------------------------------------------- the stage warmer's cache check


def test_a_stage_already_in_the_cache_starts_no_warm_child(app_lab, monkeypatch):
    """Each stage is asked once per server run, and every one of the 102 used to start a child (an app start, in the
    installed app) only to find its files made. Now a stage whose boot and reset are in the cache starts none; the
    rest still do, at idle priority (background)."""
    from sm64_events.memory import version_probe
    from sm64_events.server.scene_resim import SceneResim
    monkeypatch.setattr(version_probe, "version_from_header", lambda header: "us")   # the synthetic ROM is no Usamune
    spawned = []
    monkeypatch.setattr(SceneResim, "_spawn", staticmethod(lambda args, *, background=False: spawned.append(
        (args[args.index("--level") + 1], background)) or "child"))
    _app, lab = _mount(app_lab["inputs"])
    stages = app_lab["root"] / "resim" / "stages"
    stages.mkdir(parents=True)
    recipe = stage_sim.recipe(app_lab["rom"])
    np.savez(stage_sim.boot_file(stages, recipe), ram=np.zeros(4, np.uint8))
    np.savez(stage_sim.reset_file(stages, recipe, 9, 1, 1), ram=np.zeros(4, np.uint8))
    assert lab.resim._spawn_warm(9, 1, 1) is None and spawned == []          # made: no child
    assert lab.resim._spawn_warm(9, 1, 2) == "child" and spawned == [("9", True)]
    stage_sim.reset_file(stages, recipe, 9, 1, 1).unlink()                   # gone again: warmed again
    assert lab.resim._spawn_warm(9, 1, 1) == "child"


def test_the_cache_check_names_exactly_the_files_the_simulation_reads(tmp_path, monkeypatch):
    """One source for the names: a boot and a reset saved under stage_sim's names are what Stages loads, without
    simulating anything (it would need Unicorn and a real cartridge)."""
    from sm64_events.memory import version_probe
    monkeypatch.setattr(version_probe, "version_from_header", lambda header: "us")
    rom = tmp_path / "rom.z64"
    rom.write_bytes(bytes(range(256)) * 16)
    stages = stage_sim.Stages(tmp_path, tmp_path / "stages", rom_path=rom, symbols=object(), map_path=tmp_path / "m")
    recipe = stage_sim.recipe(rom.read_bytes())
    assert stages.key(9, 1, 3) == f"{recipe}-9-1-3"
    (tmp_path / "stages").mkdir()
    boot, reset = np.arange(8, dtype=np.uint8), np.arange(8, 16, dtype=np.uint8)
    np.savez(stage_sim.boot_file(tmp_path / "stages", recipe), ram=boot)
    np.savez(stage_sim.reset_file(tmp_path / "stages", recipe, 9, 1, 3), ram=reset)
    assert stage_sim.made(tmp_path / "stages", recipe, 9, 1, 3)
    assert np.array_equal(stages.boot()[0], boot) and np.array_equal(stages.reset(9, 1, 3), reset)


# ---------------------------------------------------------------- the tools' config


def test_a_tool_reads_the_lab_root_config_from_source_and_the_apps_inputs_frozen(monkeypatch, tmp_path):
    """tools/scene_lab commands with no ROM or map named: from a source checkout the lab root's lab.json, as always;
    frozen, a developer's SM64_SCENE_LAB file, else the app's own inputs."""
    from sm64_events.core.paths import scene_lab_root
    monkeypatch.delenv("SM64_SCENE_LAB", raising=False)
    scene_lab_root().mkdir(parents=True, exist_ok=True)
    (scene_lab_root() / "lab.json").write_text(json.dumps({"rom": "C:/lab/us.z64", "map": "C:/lab/m.map"}),
                                               encoding="utf-8")
    assert lab_inputs.tool_config() == {"rom": "C:/lab/us.z64", "map": "C:/lab/m.map"}
    monkeypatch.setattr(lab_inputs, "frozen", lambda: True)
    monkeypatch.setattr(lab_inputs, "_store", lambda: Store({"us": scene_lab_root() / "lab.json"}))
    monkeypatch.setattr(lab_inputs, "_lab_file", lambda: lambda name: None)
    frozen = lab_inputs.tool_config()
    assert frozen["rom"] == scene_lab_root() / "lab.json" and frozen["roms"]["jp"] is None
    named = tmp_path / "dev.json"
    named.write_text(json.dumps({"rom": "C:/dev/us.z64"}), encoding="utf-8")
    monkeypatch.setenv("SM64_SCENE_LAB", str(named))
    assert lab_inputs.tool_config() == {"rom": "C:/dev/us.z64"}


def test_the_cold_boot_takes_each_builds_rom_and_map_from_the_config(tmp_path):
    us_rom = tmp_path / "us.z64"
    data = bytearray(test_cold_boot.entry(0x10000000 + 0x7CC6C0, 0x21BDC))
    data[0x20:0x20 + len(cold_boot.USAMUNE_HEADER["us"])] = cold_boot.USAMUNE_HEADER["us"]
    us_rom.write_bytes(bytes(data))
    config = {"roms": {"us": us_rom, "jp": None}, "maps": {"us": tmp_path / "us.map", "jp": None},
              "rom_problems": {"jp": "The Ghost Lab needs the JP ROM (a test's words)."}}
    found = cold_boot.find_rom("us", config)
    assert found.path == us_rom and (found.image_rom, found.image_bytes) == (0x7CC6C0, 0x21BDC)
    with pytest.raises(SystemExit, match="needs the JP ROM"):
        cold_boot.find_rom("jp", config)
    assert cold_boot.map_for("us", config) == tmp_path / "us.map"
    assert cold_boot.map_for("jp", config) == cold_boot.map_path("jp")       # none named: the lab's refs/ copy
    assert cold_boot.map_for("us", {"rom": str(us_rom)}) == cold_boot.map_path("us")


def lab_json_names(source: str) -> list[int]:
    """The lines where code (not a docstring, not a sentence) names the file "lab.json" itself."""
    import ast
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Constant) and node.value == "lab.json"]


def test_no_tool_reads_a_lab_json_of_its_own():
    """Every tools/scene_lab read of the lab's ROM and map goes through core/lab_inputs.py::tool_config (or is named
    by its caller), so the installed app, which has no lab.json, never meets one; tool_config is the one place that
    knows the lab root's lab.json."""
    offenders = {path.name: lines for path in sorted((ROOT / "tools" / "scene_lab").glob("*.py"))
                 if (lines := lab_json_names(path.read_text(encoding="utf-8")))}
    assert offenders == {}
    assert lab_json_names((ROOT / "src/sm64_events/core/lab_inputs.py").read_text(encoding="utf-8"))
    assert lab_json_names('config = json.loads((sim.lab_root() / "lab.json").read_text())')   # the guard can fail


def test_rom_wait_never_starts_two_cutters(app_lab, monkeypatch):
    """Notes sent while the cutter starts keep their order, and a close that races the start closes what started."""
    monkeypatch.setattr(scene_lab_api.RomWait, "POLL_SECONDS", 0.01)
    gate, made = threading.Event(), []

    def start():
        if not gate.is_set():
            return None
        made.append(Cutter())
        return made[-1]
    wait = scene_lab_api.RomWait(start, lambda: NOT_FOUND, lambda event: None)
    for index in range(300):                                         # more than KEPT: the oldest go
        wait.send({"n": index})
    gate.set()
    deadline = time.monotonic() + 5
    while not made and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.05)
    assert len(made) == 1
    assert [note["n"] for note in made[0].notes] == list(range(300 - scene_lab_api.RomWait.KEPT, 300))
    wait.close()
    assert made[0].closed
