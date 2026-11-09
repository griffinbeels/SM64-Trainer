"""The simulation processes (tools/scene_lab/sim_boundary.py): Unicorn never shares a process with GPL-3.0 code.

Unicorn 2.1.4 is GPL-2.0-only; the server loads PyAV's FFmpeg (GPL-3.0) and the window pystray (LGPL-3.0). So the
free camera's evaluator runs in a process of its own (evaluator_child.py), and every process that loads Unicorn
loads no (L)GPL-3.0 library and only the Trainer modules sim_boundary.MODULES lists (those are licensed
GPL-2.0-or-later). Real processes run here: the server's composition root with the Ghost Lab's routes, the
evaluator's process and a state resolver, the scene export's child; each writes the modules it imported
(tests/sim_modules/sitecustomize.py). Over a synthetic scene the game's code finds no scene to draw, but each
of them loads and runs it as it would on his recording; the same processes on his real recording (the moment
builder, the re-simulation, the atlas too, which need the Usamune ROMs) were measured outside the suite.
"""
import ast
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAB, SRC, HELPER = ROOT / "tools" / "scene_lab", ROOT / "src", ROOT / "tests" / "sim_modules"
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(LAB))
import scene_attempt_fixture as fixture  # noqa: E402
import sim_boundary  # noqa: E402

from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.memory.layout import layout_for  # noqa: E402

GPL3 = re.compile(r"\b(L|A)?GPL-?v?3|GNU (Lesser |Affero )?General Public License v3", re.IGNORECASE)
# A distribution's own license: its SPDX expression, its classifiers, and a short License field. A long field is
# the whole text with every bundled notice (scipy's carries GCC's runtime library exception, whose text names
# GPLv3 though it keeps the program out of the GPL), not the distribution's license.
SHORT_LICENSE = 200
POSES = [(100.0 * k, 50.0, -200.0, 0) for k in range(8)]


def _gpl3_libraries() -> set[str]:
    """Top-level names of the installed distributions whose license is (L/A)GPL-3.0, the Trainer's own aside."""
    names = set()
    for top, distributions in importlib.metadata.packages_distributions().items():
        for name in distributions:
            if name.lower().replace("_", "-") == "sm64-events":
                continue
            meta = importlib.metadata.metadata(name)
            field = meta.get("License") or ""
            text = " ".join(filter(None, [meta.get("License-Expression"), field if len(field) <= SHORT_LICENSE else "",
                                          *(meta.get_all("Classifier") or [])]))
            if GPL3.search(text):
                names.add(top)
    return names


def _trainer(modules) -> set[str]:
    """The Trainer's own modules among `modules`: the package's and tools/scene_lab's (by bare name)."""
    lab = {path.stem for path in LAB.glob("*.py")}
    return {name for name in modules if name == "sm64_events" or name.startswith("sm64_events.") or name in lab}


def _offenders(modules) -> dict:
    """What a simulation process must never hold: a FORBIDDEN or (L)GPL-3.0 library, or a Trainer module the
    GPL-2.0-or-later list lacks."""
    tops = {name.partition(".")[0] for name in modules}
    return {"gpl3": sorted(tops & (set(sim_boundary.FORBIDDEN) | _gpl3_libraries())),
            "unlisted": sorted(_trainer(modules) - set(sim_boundary.MODULES))}


def _env(out: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(HELPER), str(SRC), env.get("PYTHONPATH"))))
    env["SIM_MODULES_OUT"] = str(out)
    return env


def _records(out: Path) -> list[dict]:
    """Every process's record: its module ("-m" name, or "-c" for a script) and what it imported."""
    found = []
    for path in sorted(out.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        argv = record["argv"]
        record["entry"] = argv[argv.index("-m") + 1] if "-m" in argv else "-c" if "-c" in argv else Path(argv[-1]).stem
        found.append(record)
    return found


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    """A synthetic scene with scene state (eight frames of Mario running), its ROM, a symbol map that names
    every symbol the evaluator reads, and a lab config over them."""
    import e2_georender
    folder = tmp_path_factory.mktemp("sim-boundary")
    rom = bytearray(fixture.rom())
    rom[0x3E] = ord("E")
    rom = bytes(rom)
    session = fixture.write_session(folder, rom, [(1000 + k, 24, 1) for k in range(8)], poses=POSES)
    (folder / "packages").mkdir()
    out = folder / "packages" / "scene.sm64scene"
    fixture.pk.write_package(fixture.pk.tap_source(session, "next", states=True), rom, out, fixture.pk.ZLIB)
    (folder / "rom.z64").write_bytes(rom)
    layout, lines = layout_for("us"), []
    for number, name in enumerate(e2_georender.SYMBOLS):
        field = e2_georender.LAYOUT_ROWS_FOR.get(name)
        address = layout.value(field) if field and layout.value(field) is not None else 0x80300000 + 0x40 * number
        lines.append(f"                0x00000000{address & 0xFFFFFFFF:08x}                {name}")
    lines += [".main           0x0000000080246000    0x00090000 load address 0x0000000000001000",
              ".main.noload    0x00000000802d6000    0x00010000 load address 0x0000000000091000",
              ".engine         0x0000000080378800    0x00020000 load address 0x00000000000a0000",
              ".engine.noload  0x0000000080398800    0x00010000 load address 0x00000000000c0000"]
    (folder / "MappingUS.map").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (folder / "Plugin").mkdir()
    (folder / "Plugin" / "GLideN64.ini").write_text("[User]\n", encoding="utf-8")
    config = {"rom": str(folder / "rom.z64"), "packages": str(folder / "packages"), "pj64_plugin": str(folder / "Plugin"),
              "map": str(folder / "MappingUS.map"), "lab_root": str(folder / "lab-root")}
    (folder / "lab.json").write_text(json.dumps(config), encoding="utf-8")
    return {"folder": folder, "rom": folder / "rom.z64", "map": folder / "MappingUS.map", "package": out}


# ---------------------------------------------------------------- the lists

def test_a_simulation_process_refuses_the_gpl3_libraries_and_will_not_start_beside_one():
    script = ("import sys; sys.path.insert(0, sys.argv[1]); import sim_boundary; sim_boundary.enter()\n"
              "try:\n    import av\nexcept sim_boundary.Refused as refused:\n    print('refused', refused)\n"
              "try:\n    import pystray\nexcept ImportError:\n    print('refused pystray')\n")
    done = subprocess.run([sys.executable, "-c", script, str(LAB)], capture_output=True, text=True, timeout=60,
                          **quiet_spawn_kwargs())
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("refused av cannot load in a simulation process") and "refused pystray" in done.stdout
    late = subprocess.run([sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); import av, sim_boundary; "
                           "sim_boundary.enter()", str(LAB)], capture_output=True, text=True, timeout=60,
                          **quiet_spawn_kwargs())
    assert late.returncode != 0 and "started with av loaded" in late.stderr


def test_the_forbidden_list_names_every_gpl3_library_installed():
    """A new (L)GPL-3.0 dependency must be named in FORBIDDEN, which the sim program is built without. PyAV's own
    license is BSD, but the FFmpeg it carries is a GPL-3.0 build, so FORBIDDEN names it by hand."""
    assert _gpl3_libraries() <= set(sim_boundary.FORBIDDEN)
    assert "av" in sim_boundary.FORBIDDEN and "unicorn" not in sim_boundary.FORBIDDEN


def test_every_simulation_module_imports_only_listed_trainer_modules_and_no_gpl3_library():
    """Each module in the GPL-2.0-or-later list, imported after enter() (as its process imports it), together
    with Unicorn: what they load at import holds no (L)GPL-3.0 library and no Trainer module outside the list."""
    lab = [name for name in sim_boundary.MODULES if not name.startswith("sm64_events")]
    script = ("import json, sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; import sim_boundary; sim_boundary.enter()\n"
              "import unicorn\nfor name in sys.argv[3:]:\n    __import__(name)\nprint(json.dumps(sorted(sys.modules)))")
    done = subprocess.run([sys.executable, "-c", script, str(LAB), str(SRC), *lab], capture_output=True, text=True,
                          timeout=120, **quiet_spawn_kwargs())
    assert done.returncode == 0, done.stderr[-2000:]
    modules = json.loads(done.stdout)
    assert {"unicorn", "e2_georender", "frame_sim", "evaluator"} <= set(modules)
    assert _offenders(modules) == {"gpl3": [], "unlisted": []}


def _imports(path: Path, name: str) -> list[tuple[str, bool]]:
    """(module named, inside a function) for every import statement in module `name`'s file."""
    tree, found = ast.parse(path.read_text(encoding="utf-8")), []
    package = name if path.name == "__init__.py" else name.rpartition(".")[0]

    def visit(node, nested: bool) -> None:
        for child in ast.iter_child_nodes(node):
            inner = nested or isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
            if isinstance(child, ast.Import):
                found.extend((alias.name, inner) for alias in child.names)
            elif isinstance(child, ast.ImportFrom):
                base = child.module or ""
                if child.level:                      # relative: from the module's own package
                    parts = package.split(".")[:len(package.split(".")) - child.level + 1]
                    base = ".".join([*parts, *([base] if base else [])])
                found.append((base, inner))
                found.extend((f"{base}.{alias.name}", inner) for alias in child.names)
            elif (isinstance(child, ast.Call) and getattr(child.func, "id", None) == "__import__" and child.args
                  and isinstance(child.args[0], ast.Constant)):
                found.append((child.args[0].value, inner))
            visit(child, inner)
    visit(tree, False)
    return found


def _file(name: str) -> Path | None:
    if (LAB / f"{name}.py").is_file():
        return LAB / f"{name}.py"
    if name == "sm64_events" or name.startswith("sm64_events."):
        base = SRC.joinpath(*name.split("."))
        for path in (base / "__init__.py", base.with_suffix(".py")):
            if path.is_file():
                return path
    return None


def test_no_simulation_entry_reaches_a_gpl3_library_but_through_a_listed_lazy_import():
    """Every import statement anywhere in the code a simulation entry can reach, at any depth, module level or in a
    function (what an installed program's packager follows). An edge into a FORBIDDEN library is allowed only
    inside a function sim_boundary.LAZY_FORBIDDEN names with why it never runs there."""
    seen, edges, todo = set(), set(), list(sim_boundary.ENTRIES.values())
    while todo:
        name = todo.pop()
        path = _file(name)
        if name in seen or path is None:
            continue
        seen.add(name)
        for target, nested in _imports(path, name):
            parts = target.split(".")
            for depth in range(1, len(parts) + 1):
                item = ".".join(parts[:depth])
                if _file(item) is not None:
                    todo.append(item)
            if parts[0] in sim_boundary.FORBIDDEN:
                edges.add((name, parts[0], nested))
    assert {(name, library) for name, library, _nested in edges} <= set(sim_boundary.LAZY_FORBIDDEN), edges
    assert not [edge for edge in edges if not edge[2]], "a module-level import of a forbidden library"
    assert set(sim_boundary.ENTRIES.values()) <= seen


# ---------------------------------------------------------------- the processes

SERVER = r"""
import json, struct, sys, time
from pathlib import Path
lab, tests, folder = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
sys.path[:0] = [str(lab), str(tests)]
import sm64_events.main  # the composition root: everything the server process imports
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sm64_events.server import scene_lab_api
import fake_viewer_host, gpu_gate, scene_session
gpu_gate.HOLD = folder / "no-hold.txt"
app = FastAPI()
scene_lab_api.mount_scene_lab(app, folder / "lab.json", session_options={
    "host_factory": scene_session.command_host(fake_viewer_host.command())})

def reply(socket):
    message = socket.receive()
    if message.get("bytes") is not None:
        length = struct.unpack_from("<I", message["bytes"])[0]
        return json.loads(message["bytes"][4:4 + length])
    return json.loads(message["text"])

answers = []
with TestClient(app) as client:
    opened = client.post("/api/scene-lab/open", json={"package": "scene.sm64scene"}).json()
    with client.websocket_connect("/api/scene-lab/ws") as socket:
        for ident, message in enumerate([{"kind": "pose"},
                                         {"camera": {"focus": [0, 0, 0], "position": [0, 300, 900]}, "encoding": "raw"},
                                         {"camera": {"focus": [0, 0, 0], "position": [50, 300, 900]}, "encoding": "raw"}], 1):
            socket.send_json({"id": ident, "session": opened["session"], "frame": 2, **message})
            while True:
                header = reply(socket)
                if header.get("id") == ident:
                    answers.append({key: header.get(key) for key in ("kind", "camera", "reason", "pose")})
                    break
    client.post("/api/scene-lab/close")
print(json.dumps({"free_camera": opened["free_camera"], "answers": answers, "modules": sorted(sys.modules)}))
"""


def test_the_free_camera_runs_the_game_code_in_its_own_process_and_never_in_the_server(recording, tmp_path):
    """The server's composition root and the Ghost Lab's routes open a scene with the real evaluator factory and
    ask for a pose and two free-camera pictures: the server never imports Unicorn (nor the evaluator's code),
    while the evaluator's process, which ran the game's code for them, holds Unicorn and no GPL-3.0 library."""
    out = tmp_path / "modules"
    out.mkdir()
    done = subprocess.run([sys.executable, "-c", SERVER, str(LAB), str(ROOT / "tests"), str(recording["folder"])],
                          capture_output=True, text=True, timeout=300, env=_env(out), **quiet_spawn_kwargs())
    assert done.returncode == 0, done.stderr[-3000:]
    server = json.loads(done.stdout.strip().splitlines()[-1])
    assert server["free_camera"]["available"], server["free_camera"]
    assert "av" in {name.partition(".")[0] for name in server["modules"]}       # the server does load GPL-3.0 code
    assert not {"unicorn", "e2_georender", "evaluator", "cpu_hooks"} & set(server["modules"])
    pose, *pictures = server["answers"]
    assert pose["pose"]["mario"] == [200.0, 50.0, -200.0], pose    # the evaluator's process read frame 2's state
    assert [picture["camera"] for picture in pictures] == ["recorded", "recorded"]   # its game code finds no scene
    assert all("no 3D scene" in picture["reason"] for picture in pictures), pictures
    children = {record["entry"]: record for record in _records(out)}
    child = children["evaluator_child"]["modules"]
    assert {"unicorn", "e2_georender", "evaluator", "package"} <= set(child)
    assert _offenders(child) == {"gpl3": [], "unlisted": []}


def test_a_state_resolver_with_the_game_code_loads_no_gpl3_library(recording, tmp_path, monkeypatch):
    import state_resolver
    out = tmp_path / "modules"
    out.mkdir()
    monkeypatch.setenv("SIM_MODULES_OUT", str(out))
    got, lock = {}, threading.Condition()

    def deliver(index, answer, ram):
        with lock:
            got[index] = answer
            lock.notify_all()
    resolvers = state_resolver.Resolvers(recording["package"].read_bytes(), recording["rom"], recording["map"], deliver,
                                         count=1, extra_path=(HELPER,))
    try:
        deadline = time.monotonic() + 60
        while resolvers.ready < 1 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert resolvers.ready == 1
        resolvers.ahead([2])
        with lock:
            assert lock.wait_for(lambda: 2 in got, timeout=60)
    finally:
        resolvers.close()
    record = next(record for record in _records(out) if record["entry"] == "state_resolver")
    assert {"unicorn", "e2_georender", "evaluator"} <= set(record["modules"])
    assert _offenders(record["modules"]) == {"gpl3": [], "unlisted": []}


def test_a_scene_export_draws_its_free_camera_through_the_evaluator_process(recording, tmp_path):
    """The export child (it mixes the scene's sound with PyAV) draws free-camera entries without loading Unicorn:
    its session's evaluator is a process of its own, which holds no GPL-3.0 library."""
    out, folder = tmp_path / "modules", tmp_path / "job"
    out.mkdir()
    folder.mkdir()
    camera = {"focus": [0.0, 0.0, 0.0], "position": [0.0, 300.0, 900.0]}
    sound = {"music": True, "effects": True, "volume": 1.0, "muted": False, "ghost_mutes": []}
    timeline = [{"t_ms": k * 1000 / 30, "scene_frame": k, "camera": camera if k % 2 else None, "camera_used": None,
                 "hud": True, "ghosts": [], "play_ms": k * 1000 / 30, "sound": sound} for k in range(4)]
    job = {"version": 2, "config": {"rom": str(recording["rom"]), "pj64_plugin": str(recording["folder"] / "Plugin"),
                                    "map": str(recording["map"])},
           "package": str(recording["package"]), "timeline": timeline, "fps": 30, "host": "fake", "evaluator": "game",
           "encoder": {"kind": "frames"}, "output": str(folder / "clip.json")}
    (folder / "job.json").write_text(json.dumps(job), encoding="utf-8")
    env = _env(out)
    env["PYTHONPATH"] = os.pathsep.join((str(LAB), env["PYTHONPATH"]))
    process = subprocess.Popen([sys.executable, "-m", "scene_export", "--job", str(folder)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, **quiet_spawn_kwargs())
    try:
        events = []
        for line in process.stdout:                  # stdin stays open: its end would cancel the export
            events.append(json.loads(line))
            if events[-1]["kind"] in ("done", "error", "cancelled"):
                break
    finally:
        process.stdin.close()
        stderr = process.stderr.read()
        process.wait(timeout=60)
    assert events[-1]["kind"] == "done", (events[-3:], stderr[-2000:])
    children = {record["entry"]: record["modules"] for record in _records(out)}
    assert "unicorn" not in children["scene_export"] and "e2_georender" not in children["scene_export"]
    assert {"unicorn", "e2_georender"} <= set(children["evaluator_child"])
    assert _offenders(children["evaluator_child"]) == {"gpl3": [], "unlisted": []}
