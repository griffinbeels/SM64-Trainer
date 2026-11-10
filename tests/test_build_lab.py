"""The Ghost Lab's natives: one recipe each (tools/build_lab.py), one embedded id, the same bytes twice.

The installed app carries five natives it never compiles (core/update_plan.py
LAB_NATIVES). Each id must name every input that changes its bytes, so a
header edit reaches the build cache and the bundle; and two clean builds of
one commit must be the same file, as the graphics plugin is
(tools/native_identity.py). The recipes live apart from tools/build_plugin.py
so a lab change never changes the graphics plugin's identity.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import re
import struct
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import native_identity  # noqa: E402


def _tool(name):
    spec = importlib.util.spec_from_file_location(f"lab_test_{name}", ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lab = _tool("build_lab")
BUILD_ID = re.compile(rb"[0-9a-f]{64}-[a-z][a-z-]*")   # core/capturelayer.py::_BUILD_ID


def test_one_recipe_per_registered_native_and_the_self_check_agrees_on_each_machine():
    from sm64_events.core.update_plan import LAB_NATIVES
    from sm64_events.desktop import beta_probe
    assert list(lab.RECIPES) == list(LAB_NATIVES)
    for name, (machine, dll) in beta_probe._LAB_NATIVES:
        recipe = lab.RECIPES[name]
        assert machine == (0x8664 if recipe.x64 else 0x14C) and dll == name.endswith(".dll"), name
        assert re.fullmatch(r"-[a-z][a-z-]*", recipe.role), f"{name}: the id reader stops at a digit"
    assert len({recipe.role for recipe in lab.RECIPES.values()}) == len(LAB_NATIVES)


def test_each_id_hashes_every_header_its_sources_include():
    """The scene lab's old cache keys missed headers (ghost_sound_host.exe never hashed
    ghost_clip_upgrade.h or ghost_behaviours.h, which ghost_assets.cpp includes)."""
    names = {name: {path.name for path in recipe.inputs()} for name, recipe in lab.RECIPES.items()}
    assert {"ghost_clip_upgrade.h", "ghost_behaviours.h", "audio.h", "build_model.py"} <= names["ghost_sound_host.exe"]
    assert {"scene_closure.h", "scene_spool.h", "zilmar.h"} <= names["tap_offline_host.exe"]
    assert {"renderer_load.h", "zilmar.h"} <= names["viewer_host.exe"]
    assert {"vu.h", "su.h", "module.h", "stems_lle.c"} <= names["scene_cxd4.dll"]


def test_an_included_header_edit_changes_the_id(tmp_path, monkeypatch):
    (tmp_path / "host.c").write_text('#include "a.h"\nint main(void){return 0;}\n')
    (tmp_path / "a.h").write_text('#include "deep/b.h"\n')
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "b.h").write_text("#define B 1\n")
    recipe = lab.LabNative("probe_host.exe", "-lab-probe", False, lab._closure([tmp_path / "host.c"]),
                           lambda vcvars, work: ([], []))
    monkeypatch.setitem(lab.RECIPES, "probe_host.exe", recipe)
    assert {path.name for path in recipe.inputs()} == {"host.c", "a.h", "b.h"}
    before = lab.build_id("probe_host.exe")
    (tmp_path / "deep" / "b.h").write_text("#define B 2\n")
    assert lab.build_id("probe_host.exe") != before
    assert before.endswith("-lab-probe")


def test_a_lab_recipe_is_no_input_of_the_graphics_plugin():
    """Editing tools/build_lab.py must not change sm64_trainer_gfx.dll's id: an installed
    plugin would then read as out of date for a lab-only change."""
    renderer = _tool("build_renderer")
    names = {Path(path).name for path in renderer._date_inputs()}
    assert "build_lab.py" not in names and "build_plugin.py" in names


def _version(path: Path) -> dict:
    from ctypes import wintypes
    version = ctypes.WinDLL("version")
    version.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, ctypes.c_void_p]
    version.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    version.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p),
                                       ctypes.POINTER(wintypes.UINT)]
    size = version.GetFileVersionInfoSizeW(str(path), None)
    buffer = ctypes.create_string_buffer(size)
    assert version.GetFileVersionInfoW(str(path), 0, size, buffer)
    found = {}
    for key in ("CompanyName", "FileVersion", "OriginalFilename"):
        pointer, length = ctypes.c_void_p(), wintypes.UINT()
        if version.VerQueryValueW(buffer, f"\\StringFileInfo\\040904B0\\{key}", ctypes.byref(pointer),
                                  ctypes.byref(length)) and length.value:
            found[key] = ctypes.wstring_at(pointer, length.value - 1)
    return found


@pytest.mark.skipif(sys.platform != "win32" or _tool("build_plugin").find_vcvars64() is None,
                    reason="needs Windows and both MSVC toolchains")
@pytest.mark.parametrize("name, machine, dll", [("viewer_host.exe", 0x14C, False),
                                                ("scene_cpu_hooks.dll", 0x8664, True)])
def test_built_twice_is_the_same_file_with_one_id_a_version_and_the_source_date(tmp_path, monkeypatch, name,
                                                                                    machine, dll):
    """Two clean builds, without the test compile cache. viewer_host.exe stands for the x86
    programs, scene_cpu_hooks.dll for the x64 DLLs."""
    monkeypatch.setenv("SM64_NATIVE_BUILD_CACHE", "0")
    first, second = lab.build(name, tmp_path / "first"), lab.build(name, tmp_path / "second")
    data = first.read_bytes()
    assert hashlib.sha256(data).hexdigest() == hashlib.sha256(second.read_bytes()).hexdigest()
    assert set(BUILD_ID.findall(data)) == {lab.build_id(name).encode()}
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    assert struct.unpack_from("<H", data, pe + 4)[0] == machine
    assert bool(struct.unpack_from("<H", data, pe + 22)[0] & 0x2000) == dll
    date = lab._term(lab.RECIPES[name])[1]
    assert struct.unpack_from("<I", data, pe + 8)[0] == date, "the PE header carries the source date"
    found = _version(first)
    assert found["CompanyName"] == "SM64 Trainer" and found["OriginalFilename"] == name
    assert found["FileVersion"] == native_identity.calendar_version(date).text
