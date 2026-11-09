"""The Ghost Lab's child processes start in the installed app.

A lab child is one of the app's two programs started with a --scene-lab-*
flag (core/childproc.py::python_child; sm64_events/scenelab_code.py's
LAB_CHILDREN says which program). A child that runs Unicorn (GPL-2.0-only)
runs in SM64TrainerSim.exe, never in the Trainer, which loads PyAV's GPL-3.0
FFmpeg (the license ruling, 2026-11-09); the rest re-enter SM64Trainer.exe.
Each program's entry must hand its flags to the lab module before any desktop
import: without that a child fell through to the desktop app, its window and
its instance takeover. And a frozen python_child used to answer env None,
which seven spawn sites then extended: a TypeError before any child started.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events import scenelab_code
from sm64_events.core import childproc
from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
from sm64_events.scenelab_code import LAB_CHILDREN, SIM_PROGRAM, TRAINER_PROGRAM

ROOT = Path(__file__).resolve().parents[1]
TRAINER_FLAGS = sorted(flag for flag, (_module, program) in LAB_CHILDREN.items() if program == TRAINER_PROGRAM)
SIM_FLAGS = sorted(flag for flag, (_module, program) in LAB_CHILDREN.items() if program == SIM_PROGRAM)
#: the children that run Unicorn today (the license ruling's list)
UNICORN_CHILDREN = {"--scene-lab-resolver", "--scene-lab-resim", "--scene-lab-moment", "--scene-lab-atlas",
                    "--scene-lab-stems"}


def _spawn_sites() -> set[tuple[str, str]]:
    """(module, flag) of every python_child call that starts a lab child."""
    found = set()
    for path in (*(ROOT / "src").rglob("*.py"), *(ROOT / "tools" / "scene_lab").glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "python_child"
                    and len(node.args) > 1 and isinstance(node.args[1], ast.Constant)
                    and str(node.args[1].value).startswith("--scene-lab-")):
                found.add((node.args[0].value, node.args[1].value))
    return found


def test_every_lab_spawn_site_has_a_dispatch_and_every_dispatch_a_site():
    sites = _spawn_sites()
    assert len(sites) >= 10, sites
    assert sites == {(module, flag) for flag, (module, _program) in LAB_CHILDREN.items()}


def test_every_unicorn_child_runs_in_the_simulation_program():
    assert UNICORN_CHILDREN <= set(SIM_FLAGS)
    assert not UNICORN_CHILDREN & set(TRAINER_FLAGS)


def _entry(monkeypatch, name: str):
    monkeypatch.syspath_prepend(str(ROOT))
    import importlib
    module = importlib.import_module(name)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    seen = []
    monkeypatch.setattr(scenelab_code, "run_child", lambda given, args: seen.append((given, args)) or 0)
    monkeypatch.setitem(sys.modules, "sm64_events.desktop.app",
                        SimpleNamespace(main=lambda: pytest.fail("a lab child started the desktop app")))
    monkeypatch.setitem(sys.modules, "sm64_events.core.reporting_setup",
                        SimpleNamespace(initialize_reporting=lambda: pytest.fail("a lab child set up reporting")))
    return module, seen


@pytest.mark.parametrize("flag", TRAINER_FLAGS)
def test_the_trainer_hands_its_lab_flags_to_the_child_before_any_desktop_import(monkeypatch, flag):
    gui_entry, seen = _entry(monkeypatch, "gui_entry")
    monkeypatch.setattr(sys, "argv", ["SM64Trainer.exe", flag, "--rom", "C:/rom.z64"])
    assert gui_entry.main() == 0
    assert seen == [(flag, ["--rom", "C:/rom.z64"])]


@pytest.mark.parametrize("flag", SIM_FLAGS)
def test_the_trainer_never_runs_a_unicorn_child(monkeypatch, flag):
    gui_entry, seen = _entry(monkeypatch, "gui_entry")
    monkeypatch.setattr(sys, "argv", ["SM64Trainer.exe", flag, "--rom", "C:/rom.z64"])
    assert gui_entry.main() == 2
    assert seen == []


@pytest.mark.parametrize("flag", SIM_FLAGS)
def test_the_simulation_program_runs_its_own_children(monkeypatch, flag):
    sim_entry, seen = _entry(monkeypatch, "sim_entry")
    monkeypatch.setattr(sys, "argv", ["SM64TrainerSim.exe", flag, "--map", "M.map"])
    assert sim_entry.main() == 0
    assert seen == [(flag, ["--map", "M.map"])]


@pytest.mark.parametrize("argv", [[*TRAINER_FLAGS[:1], "--rom", "r"], ["--beta-self-check", "x.json"], []])
def test_the_simulation_program_runs_nothing_else(monkeypatch, argv):
    sim_entry, seen = _entry(monkeypatch, "sim_entry")
    monkeypatch.setattr(sys, "argv", ["SM64TrainerSim.exe", *argv])
    assert sim_entry.main() == 2
    assert seen == []


def test_a_child_runs_its_module_as_the_main_program(monkeypatch, tmp_path):
    """As `python -m module args` does: the module's own main block runs with
    those arguments, and its exit code is the process's."""
    record = tmp_path / "argv.txt"
    (tmp_path / "lab_child_fixture.py").write_text(
        "import sys\n"
        "if __name__ == '__main__':\n"
        f"    open({str(record)!r}, 'w').write(' '.join(sys.argv[1:]))\n"
        "    raise SystemExit(5)\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setitem(LAB_CHILDREN, "--scene-lab-fixture", ("lab_child_fixture", TRAINER_PROGRAM))
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    with pytest.raises(SystemExit) as stopped:
        scenelab_code.run_child("--scene-lab-fixture", ["--serve", "x"])
    assert stopped.value.code == 5
    assert record.read_text() == "--serve x"


def test_a_frozen_child_gets_an_environment_its_spawner_can_extend(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("__PYVENV_LAUNCHER__", "C:/venv/Scripts/python.exe")
    argv, env = python_child("attempt_cutter", "--scene-lab-cutter", ["--rom", "r"], site_packages=True)
    assert argv == [sys.executable, "--scene-lab-cutter", "--rom", "r"]
    assert isinstance(env, dict) and "__PYVENV_LAUNCHER__" not in env
    assert env == {k: v for k, v in os.environ.items() if k != "__PYVENV_LAUNCHER__"}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, ("C:/lab", env.get("PYTHONPATH"))))   # the spawn sites' line
    assert os.environ.get("__PYVENV_LAUNCHER__") == "C:/venv/Scripts/python.exe"          # a copy, not the parent's


@pytest.mark.parametrize("flag", SIM_FLAGS)
def test_a_frozen_unicorn_child_starts_the_simulation_program_beside_the_trainer(monkeypatch, flag):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Programs\SM64Trainer\SM64Trainer.exe")
    argv, env = python_child(LAB_CHILDREN[flag][0], flag, ["--map", "M.map"])
    assert argv == [r"C:\Programs\SM64Trainer\SM64TrainerSim.exe", flag, "--map", "M.map"]
    assert isinstance(env, dict)


def test_other_frozen_children_still_re_enter_the_running_exe(monkeypatch):
    """The GPU helper, the ghost sound player, the library worker and the
    Trainer's own lab children: the running exe, whatever it is named."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", r"C:\Programs\SM64TrainerBeta\SM64TrainerBeta.exe")
    assert python_child("x", "--gpu-helper")[0][0] == sys.executable
    assert python_child("rig_path", "--scene-lab-rig-path")[0][0] == sys.executable


def test_a_source_child_still_runs_this_checkout(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    argv, env = python_child("state_resolver", "--scene-lab-resolver", ["--rom", "r"])
    assert argv[1:] == ["-m", "state_resolver", "--rom", "r"]
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(childproc._SOURCE_ROOT)


@pytest.mark.parametrize("flag", sorted(LAB_CHILDREN))
def test_each_lab_child_starts_through_its_entry_and_exits_quietly(tmp_path, flag):
    """The real entry and the real module, from this checkout: `--help` (or an
    empty request stream) answers and exits 0 with no window. LOCALAPPDATA is
    a temporary folder so no child can touch the user's data."""
    entry = "sim_entry.py" if flag in SIM_FLAGS else "gui_entry.py"
    env = {**os.environ, "LOCALAPPDATA": str(tmp_path), "APPDATA": str(tmp_path)}
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT / "src"), env.get("PYTHONPATH"))))
    done = subprocess.run([sys.executable, str(ROOT / entry), flag, "--help"], cwd=tmp_path, env=env,
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=180, **quiet_spawn_kwargs())
    assert done.returncode == 0, done.stderr.decode("utf-8", "replace")[-2000:]
