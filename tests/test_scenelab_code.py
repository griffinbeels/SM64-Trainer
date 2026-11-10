"""The installed app carries exactly the Ghost Lab modules it reaches, under
bare names nothing else uses (sm64_events/scenelab_code.py, the one door).

tools/bundle_policy.py's LAB_MODULES is the list tools/build_exe.py collects.
The closure below is computed the way the app reaches the lab: every name src/
asks lab_module (or the server's _lab_module) for, every module a lab child
process runs, then every bare-name import between lab modules. A module the
app reaches but the list lacks would work from a checkout and fail installed.
"""
import ast
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LAB = ROOT / "tools" / "scene_lab"
sys.path.insert(0, str(ROOT / "tools"))
import bundle_policy  # noqa: E402

from sm64_events import scenelab_code  # noqa: E402
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402

LAB_NAMES = frozenset(path.stem for path in LAB.glob("*.py"))


def _string(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _roots(path: Path) -> set[str]:
    """Lab modules one file reaches: lab_module("x") and _lab_module("x"), the
    module a python_child(...) runs under a --scene-lab- flag, and a direct import."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and node.args:
            called = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            first = _string(node.args[0])
            if called in ("lab_module", "_lab_module") and first:
                found.add(first)
            flag = _string(node.args[1]) if len(node.args) > 1 else None
            if called == "python_child" and first and (flag or "").startswith("--scene-lab-"):
                found.add(first)
        elif isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            found.add(node.module.split(".")[0])
    return found & LAB_NAMES


def _closure() -> set[str]:
    reached = set()
    for path in (ROOT / "src").rglob("*.py"):
        if path.name != "scenelab_code.py":
            reached |= _roots(path)
    edges = {name: _roots(LAB / f"{name}.py") - {name} for name in LAB_NAMES}
    seen, todo = set(), sorted(reached)
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo.extend(edges[name])
    return seen


def test_lab_modules_are_exactly_what_the_app_reaches():
    reached = _closure()
    assert reached, "the closure found no lab module at all: the scan is broken"
    listed = set(bundle_policy.LAB_MODULES)
    assert len(listed) == len(bundle_policy.LAB_MODULES), "LAB_MODULES names a module twice"
    assert sorted(reached - listed) == [], "reached by the app but not collected into it"
    assert sorted(listed - reached) == [], "collected but nothing in the app reaches it"
    assert listed <= LAB_NAMES


def test_no_lab_module_name_shadows_another_module():
    """The installed app holds every module under its bare name. A lab module
    called like a standard library module, an installed package or one of the
    app's own top-level names would hide that module, or be hidden by it."""
    clash = set(bundle_policy.LAB_MODULES) & sys.stdlib_module_names
    assert sorted(clash) == []
    clash = set(bundle_policy.LAB_MODULES) & {"gui_entry", "bootstrap_entry", "beta_installer_entry", "sm64_events"}
    assert sorted(clash) == []
    # Every top-level name the build's own interpreter can import, without
    # tools/scene_lab: the venv's packages and modules and the editable src/.
    probe = ("import json, pkgutil, sys; "
             "print(json.dumps(sorted({m.name for m in pkgutil.iter_modules()} | set(sys.builtin_module_names))))")
    out = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, encoding="utf-8",
                         cwd=ROOT / "tests", check=True, timeout=120, **quiet_spawn_kwargs())
    importable = set(__import__("json").loads(out.stdout))
    assert "numpy" in importable and "unicorn" in importable, "the probe did not see the build's packages"
    assert sorted(set(bundle_policy.LAB_MODULES) & importable) == []


def test_lab_module_imports_from_this_checkout(monkeypatch):
    monkeypatch.setattr(sys, "path", [p for p in sys.path if p != str(LAB)])
    module = scenelab_code.lab_module("gpu_gate")
    assert Path(module.__file__).resolve() == (LAB / "gpu_gate.py").resolve()
    assert sys.path[0] == str(LAB)


def test_lab_module_installed_imports_by_bare_name_and_touches_no_path(monkeypatch, tmp_path):
    """Frozen, the module is already inside the app under its bare name; the
    door imports it as it is and never adds a folder that does not exist there."""
    (tmp_path / "bundled_lab_fixture.py").write_text("WHERE = 'inside the app'\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "path", [str(tmp_path), *(p for p in sys.path if p != str(LAB))])
    before = list(sys.path)
    try:
        assert scenelab_code.lab_module("bundled_lab_fixture").WHERE == "inside the app"
    finally:
        sys.modules.pop("bundled_lab_fixture", None)
    assert sys.path == before


def test_the_trainer_collects_every_lab_module_by_name_and_never_unicorn():
    import build_exe
    args = build_exe.app_args(None)
    hidden = {args[i + 1] for i, arg in enumerate(args) if arg == "--hidden-import"}
    assert set(bundle_policy.LAB_MODULES) <= hidden
    assert str(LAB) in {args[i + 1] for i, arg in enumerate(args) if arg == "--paths"}
    assert "unicorn" not in {args[i + 1] for i, arg in enumerate(args) if arg == "--collect-all"}
    added = [args[i + 1] for i, arg in enumerate(args) if arg == "--add-data"]
    from sm64_events.core.update_plan import LAB_NATIVE_FOLDER, LAB_NATIVES
    for name in LAB_NATIVES:
        source = build_exe.DATA_DIR / "lab" / name
        assert f"{source}{build_exe.SEP}{LAB_NATIVE_FOLDER.removeprefix('_internal/')}" in added
    for name in bundle_policy.PINNED_MAPS:
        assert f"{build_exe.DATA_DIR / 'maps' / name}{build_exe.SEP}sm64_events/data/maps" in added


def test_the_simulation_program_carries_unicorn_and_the_children_that_run_it(tmp_path):
    import build_exe
    from sm64_events.scenelab_code import SIM_PROGRAM, child_modules
    args = build_exe.sim_args(distpath=tmp_path)
    assert args[0].endswith("sim_entry.py") and args[args.index("--name") + 1] == "SM64TrainerSim"
    assert args[args.index("--contents-directory") + 1] == bundle_policy.SIM_CONTENTS == "_sim"
    hidden = {args[i + 1] for i, arg in enumerate(args) if arg == "--hidden-import"}
    assert set(child_modules(SIM_PROGRAM)) == set(bundle_policy.SIM_MODULES)
    assert hidden == set(bundle_policy.SIM_MODULES) | set(bundle_policy.SIM_IMPORTS)     # its self-check imports each
    collected = {args[i + 1] for i, arg in enumerate(args) if arg == "--collect-all"}
    assert "unicorn" in collected and not collected & bundle_policy.SIM_FORBIDDEN
    excluded = {args[i + 1] for i, arg in enumerate(args) if arg == "--exclude-module"}
    # Every forbidden library a Python import could bring in is left out by name (check_programs is the backstop),
    # and nothing a simulation process imports is (tests/test_sim_boundary.py: the sim excludes stay unloaded).
    assert {"av", "pystray", "pyaudiowpatch", "capstone", "webview", "_ssl", "_hashlib"} <= excluded
    assert not excluded & {"unicorn", "numpy", *bundle_policy.SIM_MODULES}
    assert "--windowed" in args and "--onefile" not in args


def test_the_install_notices_cover_both_programs(tmp_path, monkeypatch):
    """One licenses folder serves the install: the build stages it from the
    Trainer's packages and, once merged, the simulation program's (_sim)."""
    import build_exe
    import third_party_notices
    app = tmp_path / "SM64Trainer"
    (app / "_internal").mkdir(parents=True)
    asked, staged = [], []
    monkeypatch.setattr(third_party_notices, "bundled_import_names",
                        lambda _app, exe="SM64Trainer", contents="_internal": asked.append((exe, contents)) or {exe})
    monkeypatch.setattr(third_party_notices, "stage", lambda _internal, names, _ffmpeg: staged.append(names))
    monkeypatch.setattr(build_exe, "write_retired_natives", lambda _app: None)
    assert build_exe.stage_notices(tmp_path, Path("ffmpeg.exe")) == app and staged == [{"SM64Trainer"}]
    (app / "SM64TrainerSim.exe").write_bytes(b"MZ")
    build_exe.stage_notices(tmp_path, Path("ffmpeg.exe"))
    assert staged[-1] == {"SM64Trainer", "SM64TrainerSim"} and asked[-1] == ("SM64TrainerSim", "_sim")


def test_the_lab_natives_are_a_list_of_their_own():
    """BUNDLED_NATIVES has readers that expect exactly its two names
    (core/paths.py unpacks it, the beta self-check zips it strictly), so the
    Ghost Lab's natives are a second list with their own folder."""
    from sm64_events.core import paths
    from sm64_events.core.update_plan import BUNDLED_NATIVES, LAB_NATIVE_FOLDER, LAB_NATIVES
    assert len(BUNDLED_NATIVES) == 2 and (paths.WRAPPER_DLL_NAME, paths.ENCODER_DLL_NAME) == BUNDLED_NATIVES
    assert len(set(LAB_NATIVES)) == len(LAB_NATIVES) == 5 and not set(LAB_NATIVES) & set(BUNDLED_NATIVES)
    assert LAB_NATIVE_FOLDER == "_internal/sm64_events/data/lab"


def test_bundled_lab_file_answers_only_inside_the_installed_app(monkeypatch, tmp_path):
    from sm64_events.core import paths
    from sm64_events.core.update_plan import LAB_NATIVES
    assert paths.bundled_lab_file("viewer_host.exe") is None            # a checkout builds its own
    assert paths.bundled_lab_file("MappingUS.map") is None
    with pytest.raises(ValueError, match="not a Ghost Lab file"):
        paths.bundled_lab_file("sm64_trainer_gfx.dll")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert paths.bundled_lab_file("scene_cxd4.dll") is None               # this build lacks it
    lab = tmp_path / "sm64_events" / "data" / "lab"
    lab.mkdir(parents=True)
    for name in LAB_NATIVES:
        (lab / name).write_bytes(b"native")
    maps = tmp_path / "sm64_events" / "data" / "maps"
    maps.mkdir()
    (maps / "MappingJP.map").write_bytes(b"map")
    assert paths.bundled_lab_file("tap_offline_host.exe") == lab / "tap_offline_host.exe"
    assert paths.bundled_lab_file("MappingJP.map") == maps / "MappingJP.map"
    assert paths.bundled_lab_file("MappingUS.map") is None


def test_unicorn_is_a_pinned_runtime_dependency():
    """The free camera's CPU hooks are built against Unicorn 2.1.4's internals,
    and the simulation program is built from this environment."""
    import tomllib
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "unicorn==2.1.4" in project["project"]["dependencies"]
    assert not any(dep.startswith("unicorn") for group in project["dependency-groups"].values() for dep in group)
    from importlib.metadata import version
    assert version("unicorn") == "2.1.4"



def test_the_simulation_program_finds_the_trainers_data_beside_it(tmp_path, monkeypatch):
    """The simulation program's own contents folder (_sim, its _MEIPASS) holds only its code; the natives, maps and
    symbol tables live in the Trainer's _internal beside it, and every lookup from either program finds them."""
    from sm64_events.core import paths
    app = tmp_path / "SM64Trainer"
    lab = app / "_internal" / "sm64_events" / "data" / "lab"
    lab.mkdir(parents=True)
    (lab / "tap_offline_host.exe").write_bytes(b"MZ")
    (app / "_internal" / "sm64_events" / "data" / "maps").mkdir(parents=True)
    (app / "_internal" / "sm64_events" / "data" / "maps" / "MappingUS.map").write_text("m", encoding="utf-8")
    (app / "_internal" / "sm64_events" / "data" / "plugin").mkdir(parents=True)
    (app / "_internal" / "sm64_events" / "data" / "plugin" / "sm64_trainer_gfx.dll").write_bytes(b"MZ")
    (app / "_internal" / "symbols_us.tsv").write_text("s", encoding="utf-8")
    (app / "_sim").mkdir()
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    for program, contents in (("SM64TrainerSim.exe", "_sim"), ("SM64Trainer.exe", "_internal")):
        monkeypatch.setattr(sys, "_MEIPASS", str(app / contents), raising=False)
        monkeypatch.setattr(sys, "executable", str(app / program))
        assert paths.bundled_lab_file("tap_offline_host.exe") == lab / "tap_offline_host.exe", program
        assert paths.bundled_lab_file("MappingUS.map").read_text(encoding="utf-8") == "m"
        assert paths.bundled_plugin_dll() == app / "_internal" / "sm64_events" / "data" / "plugin" / "sm64_trainer_gfx.dll"
        assert paths.bundled_symbol_map("symbols_us.tsv").read_text(encoding="utf-8") == "s"
        assert paths.bundled_lab_file("viewer_host.exe") is None
