"""THE ONE RECIPE for each native the Ghost Lab runs (core/update_plan.py LAB_NATIVES).

    uv run python tools/build_lab.py --out <dir>            # all five into <dir>
    uv run python tools/build_lab.py --out <dir> viewer_host.exe

viewer_host.exe, tap_offline_host.exe and ghost_sound_host.exe are x86
programs beside the x86 graphics plugin they load or share code with;
scene_cpu_hooks.dll and scene_cxd4.dll are x64 DLLs the server's own Python
children load. A source checkout compiles each into the scene lab's build
cache on first use (`lab_native_cached`, called by the build function of
tools/scene_lab/viewer.py, tap.py, ghost_sound.py, cpu_hooks.py and
stems_live.py); tools/build_natives.py builds the same bytes into
src/sm64_events/data/lab/, which the installed app carries and never compiles
(core/paths.py bundled_lab_file).

Each carries ONE embedded build id naming every input that changes its bytes
(`lab_build_id`: its sources and every header they include, this recipe,
tools/build_plugin.py's shared compile path, the toolchain and the source
date), a version resource, and the source date in its PE header, as the
graphics plugin does (tools/native_identity.py): two builds of one commit
are the same file. The id lives in a one-line unit of its own, kept in the
image by /INCLUDE, so no lab source changes to carry it; core/capturelayer.py
_build_identity reads it as it reads the plugin's. Nothing here packs,
obfuscates or otherwise changes how a scanner sees a file.

The recipes live here, not in tools/build_plugin.py, so that a lab change
never changes the graphics plugin's identity (that file's bytes are in the
plugin's build id).
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Callable, NamedTuple

REPO = Path(__file__).resolve().parents[1]
LAB = REPO / "tools" / "scene_lab"
NATIVE = REPO / "plugin" / "gfxwrap"
RECIPE_VERSION = "1"
ID_SYMBOL = "sm64_lab_build_id"


def _load(name: str):
    """A sibling tool, loaded once by path (this file is itself often loaded by path)."""
    module = sys.modules.get(f"lab_{name}")
    if module is None:
        spec = importlib.util.spec_from_file_location(f"lab_{name}", REPO / "tools" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


def _compiler():
    return _load("build_plugin")


class ToolchainMissing(RuntimeError):
    """No MSVC toolchain of the bitness a native needs: a checkout's lab tools fall back or say so."""


class LabNative(NamedTuple):
    name: str                                  # its file: a core/update_plan.py LAB_NATIVES name
    role: str                                  # the build id's suffix: letters and dashes only (core/capturelayer.py)
    x64: bool                                  # x64 toolchain (a DLL Python loads) or x86 (beside Project64's plugin)
    inputs: Callable[[], list[Path]]           # every file whose bytes reach the binary, beyond the recipe
    args: Callable[[Path, Path], tuple[list, list]]   # (vcvars, work) -> (cl sources and flags, link inputs)
    extra_term: Callable[[], bytes] = lambda: b""     # inputs that are not files of this checkout
    needs: str = ""                            # what building it takes beyond the toolchain


def _cxx_flags() -> list[str]:
    return [*(flag for flag in _compiler().COMMON_FLAGS if not flag.startswith("/std:")), "/std:c++17", "/EHsc"]


def _viewer_args(vcvars, work):
    return ([*_compiler().COMMON_FLAGS, f"/I{NATIVE}", str(LAB / "viewer_host.c")], ["user32.lib", "kernel32.lib"])


TAP_SOURCES = (LAB / "tap_offline_host.cpp", NATIVE / "scene_tap.cpp", NATIVE / "scene_spool.cpp")


def _tap_args(vcvars, work):
    # SCENE_TAP_ONE_SHOT: the armed.txt mode, which only this host has (scene_tap.cpp's header).
    return ([*_cxx_flags(), "/DSCENE_TAP_ONE_SHOT", f"/I{NATIVE}", *map(str, TAP_SOURCES)],
            ["kernel32.lib", "user32.lib"])


GHOST_SOUND_SOURCES = (LAB / "ghost_sound_host.cpp", NATIVE / "ghost_audio.cpp", NATIVE / "ghost_assets.cpp")


def _ghost_sound_args(vcvars, work):
    """The model library's audio engine (tools/build_model.py), its library built into `work`
    from the model's own download cache."""
    cache = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SM64Trainer" / "build-cache" / "model"
    library = _load("build_model").build_model(work / "model", cache=cache, vcvars32=vcvars)
    source = work / "model" / "source" / "src"
    flags = ["/nologo", "/O2", "/W4", "/MT", "/EHsc", "/std:c++17", f"/I{NATIVE}", f"/I{REPO / 'renderer/model'}",
             f"/I{source}", f"/I{source / 'decomp/include'}", *map(str, GHOST_SOUND_SOURCES), str(library)]
    return flags, ["winmm.lib", "bcrypt.lib", *_compiler().LIBS]


def _cpu_hooks_args(vcvars, work):
    return (["/nologo", "/O2", "/W4", "/LD", "/MT", str(LAB / "cpu_hooks.c")], [])


CXD4 = LAB / "cxd4"
# stems_probe.py LLE_FILES: the ROM's own audio microcode in cxd4's RSP interpreter.
CXD4_SOURCES = (LAB / "stems_lle.c", *(CXD4 / name for name in (
    "su.c", "vu/vu.c", "vu/multiply.c", "vu/add.c", "vu/select.c", "vu/logical.c", "vu/divide.c")))


def _cxd4_args(vcvars, work):
    return (["/nologo", "/O2", "/LD", "/MT", "/W3", "/DSP_EXECUTE_LOG", f"/I{CXD4}", *map(str, CXD4_SOURCES)], [])


_QUOTED_INCLUDE = re.compile(r'^\s*#\s*include\s*"([^"]+)"', re.MULTILINE)


def _closure(sources, folders=(), extra=()) -> Callable[[], list[Path]]:
    """The sources, every file of this checkout they #include "..." (beside the
    including file first, then `folders`, as cl looks), and `extra`: what the id
    hashes, so a header nobody listed still changes it."""
    def inputs() -> list[Path]:
        found, pending = {}, [Path(path).resolve() for path in sources]
        while pending:
            path = pending.pop()
            if path in found:
                continue
            found[path] = None
            for name in _QUOTED_INCLUDE.findall(path.read_text(encoding="utf-8", errors="replace")):
                for folder in (path.parent, *map(Path, folders)):
                    if (folder / name).is_file():
                        pending.append((folder / name).resolve())
                        break
        return [*sorted(found, key=lambda path: path.as_posix()), *map(Path, extra)]
    return inputs


RECIPES = {recipe.name: recipe for recipe in (
    LabNative("viewer_host.exe", "-lab-viewer", False, _closure([LAB / "viewer_host.c"], [NATIVE]), _viewer_args),
    LabNative("tap_offline_host.exe", "-lab-tap", False, _closure(TAP_SOURCES, [NATIVE]), _tap_args),
    LabNative("ghost_sound_host.exe", "-lab-ghost-sound", False,
              _closure(GHOST_SOUND_SOURCES, [NATIVE, REPO / "renderer/model"], [REPO / "tools/build_model.py"]),
              _ghost_sound_args, lambda: _load("build_model").component_hash().encode(),
              "the pose model's pinned sources (tools/build_model.py)"),
    LabNative("scene_cpu_hooks.dll", "-lab-cpu-hooks", True, _closure([LAB / "cpu_hooks.c"]), _cpu_hooks_args),
    LabNative("scene_cxd4.dll", "-lab-rsp", True, _closure(CXD4_SOURCES, [CXD4]), _cxd4_args),
)}


def _vcvars(recipe: LabNative) -> Path | None:
    compiler = _compiler()
    return compiler.find_vcvars64() if recipe.x64 else compiler.find_vcvars32()


def _recipe_files() -> list[Path]:
    """The recipe itself: this file and the shared compile path it builds through."""
    return [Path(__file__), REPO / "tools" / "build_plugin.py"]


def _term(recipe: LabNative) -> tuple[bytes, int]:
    """The identity term (toolchain, source date: tools/native_identity.py) over the native's inputs and recipe."""
    return _compiler()._tool("native_identity").build_term([*recipe.inputs(), *_recipe_files()], _vcvars(recipe))


def build_id(name: str) -> str:
    """The one id a lab native embeds."""
    recipe = RECIPES[name]
    digest = hashlib.sha256(f"lab native {RECIPE_VERSION}\0{name}\0{'x64' if recipe.x64 else 'x86'}\0".encode())
    for path in [*recipe.inputs(), *_recipe_files()]:
        try:
            relative = path.resolve().relative_to(REPO).as_posix()
        except ValueError:
            relative = path.name
        digest.update(relative.encode() + b"\0" + path.read_bytes() + b"\0")
    digest.update(recipe.extra_term() + b"\0" + _term(recipe)[0])
    return digest.hexdigest() + recipe.role


def build(name: str, out_dir: Path, vcvars: Path | None = None) -> Path:
    """One lab native into out_dir and nothing else, carrying its build id."""
    recipe = RECIPES[name]
    vcvars = vcvars or _vcvars(recipe)
    if vcvars is None:
        raise ToolchainMissing(f"no {'x64' if recipe.x64 else 'x86'} MSVC toolchain found (VS Build Tools)")
    compiler = _compiler()
    native_identity = compiler._tool("native_identity")
    out_dir = Path(out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    identity = build_id(name)
    date = _term(recipe)[1]
    work = Path(tempfile.mkdtemp(prefix=f"sm64_lab_{Path(name).stem}_"))
    try:
        unit = work / "lab_build_id.c"
        unit.write_text(f"/* tools/build_lab.py: the build id of {name}. */\n"
                        f'const char {ID_SYMBOL}[] = "{identity}";\n', encoding="ascii")
        compiler._cl(vcvars, ["/nologo", "/c", str(unit), f"/Fo{work}\\"], work)
        flags, link = recipe.args(vcvars, work)
        target = work / name
        symbol = ID_SYMBOL if recipe.x64 else "_" + ID_SYMBOL   # x86 C names carry the underscore
        compiler._cl(vcvars, [*flags, str(work / "lab_build_id.obj"), f"/Fe:{target}", f"/Fo{work}\\", "/link",
                              *link, f"/INCLUDE:{symbol}"], work, version=native_identity.calendar_version(date))
        native_identity.stamp(target, date)
        delivered = out_dir / name
        shutil.copy2(target, delivered)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return delivered


def cached(name: str, folder: Path | None = None) -> Path:
    """A source checkout's lab native: the scene lab's build cache entry for its id
    (%LOCALAPPDATA%/SM64Trainer/build-cache/scene-lab/<stem>-<id>/<name>), compiled on a
    miss, or into `folder` when one is given. Raises ToolchainMissing without the toolchain."""
    if folder is None:
        cache = Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir())) / "SM64Trainer" / "build-cache" / "scene-lab"
        folder = cache / f"{Path(name).stem}-{hashlib.sha256(build_id(name).encode()).hexdigest()[:16]}"
    target = Path(folder) / name
    if target.is_file():
        return target
    return build(name, folder)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    parser.add_argument("names", nargs="*", help=f"natives to build (default: all of {', '.join(RECIPES)})")
    args = parser.parse_args()
    for name in args.names or RECIPES:
        if name not in RECIPES:
            print(f"not a lab native: {name}")
            return 2
        try:
            print(f"wrote {build(name, args.out)}")
        except RuntimeError as failure:
            print(f"{name}: {failure}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
