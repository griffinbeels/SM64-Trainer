"""The plugin/gfxwrap natives this tool builds, and the compile path every native helper shares.

    uv run python tools/build_plugin.py                       # the encoder helper, into src/sm64_events/data/plugin/
    uv run python tools/build_plugin.py --test-host --out <dir>   # gfxwrap_host.exe + a test plugin into <dir>

* THE 64-BIT GPU ENCODER HELPER the server's isolated helper process loads
  (SM64GpuEncoderV1.dll), a shipped native.
* A TEST PLUGIN (`build_test_plugin`): sm64_trainer_gfx.dll for the host
  tests, the capture layer (gfxwrap.c and RUNTIME_SOURCES) linked with a
  stand-in renderer instead of LINK's GLideN64 (plugin/gfxwrap/renderer_entries.h).
  Same binding, exports and .def as the shipped plugin; it never enters the
  bundle.
* The test host (`build_test_host`) that drives a plugin without Project64.

THE GRAPHICS PLUGIN Project64 loads (sm64_trainer_gfx.dll: LINK's GLideN64
v4.2, the overlay and this capture layer, one file since 2026-11-07) is built
by tools/build_renderer.py, which compiles the capture layer from
RUNTIME_SOURCES and `gfxwrap.c` here. tools/build_natives.py builds both
shipped natives.

Project64 1.6 is a 32-bit process, so the plugin and its test builds use the
x86 MSVC toolchain (`vcvars32.bat`, found through vswhere or the known
BuildTools folder) with a STATIC CRT (/MT): a user's machine need not carry
a VC redistributable for the plugin to load. Every compiler is spawned with
no console window (spawned-processes rule). Prints the outputs it wrote;
exits non-zero with cl's own text on a failure.

Every cl call goes through `_cl`, which gives the file it links a version
resource and makes the build repeatable (tools/native_identity.py): the
encoder helper built twice from the same commit is the same bytes, and its
PE timestamp is the source date, not the clock.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "plugin" / "gfxwrap"
BUNDLE = REPO / "src" / "sm64_events" / "data" / "plugin"
SHIPPED_HELPER = BUNDLE / "SM64GpuEncoderV1.dll"
PLUGIN_NAME = "sm64_trainer_gfx.dll"   # the shipped plugin's file name, and every test plugin's
# The capture layer's sources beside RUNTIME_SOURCES: the plugin's exported
# surface (gfxwrap.c, compiled as C with GFXWRAP_GPU_RUNTIME) and its .def.
CAPTURE_LAYER = "gfxwrap.c"
EXPORTS_DEF = "gfxwrap.def"
KNOWN_VCVARS = [
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\18\BuildTools\VC\Auxiliary\Build\vcvars32.bat"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat"),
    Path(r"C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvars32.bat"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars32.bat"),
]
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
COMMON_FLAGS = ["/nologo", "/O2", "/W4", "/std:c17", "/MT", "/D_CRT_SECURE_NO_WARNINGS",
                "/DWIN32", "/D_WINDOWS"]
LIBS = ["opengl32.lib", "user32.lib", "gdi32.lib", "kernel32.lib"]
RUNTIME_SOURCES = (
    "wrapper_runtime", "stamp_adapter", "runtime_control", "runtime_delivery", "gpu_request",
    "gpu_delivery", "gpu_render", "source_snapshot", "gl_snapshot", "gpu_channel", "scene_tap",
    "scene_spool",
)
RUNTIME_LIBS = ["d3d11.lib", "dxgi.lib"]   # what the capture layer links beyond LIBS
# The encoder helper: a 64-bit DLL loaded by the isolated helper process,
# never by Project64. Its NVENC header is the vendored nvEncodeAPI.h.
ENCODER_SOURCES = ("gpu_encoder_dll", "gpu_platform_encoder", "gpu_bridge_encoder", "gpu_mf_encoder")
ENCODER_LIBS = ["d3d11.lib", "dxgi.lib", "mfplat.lib", "mfuuid.lib", "mf.lib", "ole32.lib", "oleaut32.lib",
                "delayimp.lib", "/DELAYLOAD:MFPlat.DLL"]


def find_vcvars32() -> Path | None:
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    if vswhere.exists():
        found = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
            capture_output=True, text=True, creationflags=_NO_WINDOW, check=False)
        for line in found.stdout.splitlines():
            candidate = Path(line.strip()) / "VC" / "Auxiliary" / "Build" / "vcvars32.bat"
            if candidate.exists():
                return candidate
    for candidate in KNOWN_VCVARS:
        if candidate.exists():
            return candidate
    return None


def find_vcvars64(vcvars32: Path | None = None) -> Path | None:
    """The x64 toolchain beside the x86 one; the helper is a 64-bit DLL."""
    vcvars32 = vcvars32 or find_vcvars32()
    if vcvars32 is None:
        return None
    candidate = vcvars32.with_name("vcvars64.bat")
    return candidate if candidate.is_file() else None


def toolchain_available() -> bool:
    return find_vcvars32() is not None


def _tool(name: str):
    """A sibling module of tools/ (this file is often loaded by path)."""
    if str(REPO / "tools") not in sys.path:  # one shared module: its toolchain memo too
        sys.path.insert(0, str(REPO / "tools"))
    return __import__(name)


def _cl(vcvars: Path, args: list, cwd: Path, *, version=None, identity=None) -> None:
    """Run one cl.exe invocation inside vcvars32's environment, in `cwd`.

    Every call is made repeatable (/experimental:deterministic with fixed
    path names, /Brepro at link), and one that links an .exe or .dll first
    gets that file's version resource (tools/native_identity.py:
    `version` and `identity` name a shipped native; other outputs are
    development builds named by their file name)."""
    native_identity = _tool("native_identity")
    scratch = Path(tempfile.mkdtemp(prefix="sm64_identity_"))
    try:
        args = native_identity.prepare(vcvars, args, Path(cwd), scratch,
                                       version=version or native_identity.DEVELOPMENT, identity=identity)
        # The test suite reuses identical earlier compiles (tools/native_build_cache.py).
        if os.environ.get("SM64_NATIVE_BUILD_CACHE", "0") != "0":
            native_build_cache = _tool("native_build_cache")
            native_build_cache.cl(vcvars, args, cwd, native_build_cache.enabled())
            return
        # A batch file rather than a `cmd /c "..."` string: subprocess re-quotes
        # an argument holding quotes, and cmd then sees `\"` literally.
        quoted = " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)
        script = cwd / "build_step.bat"
        # Without the telemetry opt-out vcvars may start vctip.exe, which then
        # outlives the build (one was still running 47 minutes on, 2026-11-06).
        script.write_text(f'@echo off\r\nset VSCMD_SKIP_SENDTELEMETRY=1\r\ncall "{vcvars}" >nul\r\ncl {quoted}\r\n',
                          encoding="utf-8")
        result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=str(cwd),
                                capture_output=True, text=True, creationflags=_NO_WINDOW,
                                check=False)
        if result.returncode != 0:
            raise RuntimeError(f"cl failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _source_files() -> list[Path]:
    """Everything of plugin/gfxwrap the wrapper and the helper compile from,
    the vendored NVENC header included."""
    files = [path for path in sorted(SOURCE.iterdir()) if path.suffix in {".c", ".cpp", ".h", ".def"}]
    return files + sorted(path for path in (SOURCE / "vendor").glob("*.h"))


def _build_term() -> tuple[bytes, int]:
    """This recipe, the identity recipe, the toolchain and the source date
    (tools/native_identity.py), and that date."""
    inputs = [*_source_files(), Path(__file__)]
    term, date = _tool("native_identity").build_term(inputs, find_vcvars32())
    return Path(__file__).read_bytes() + b"\0" + term, date


def sources_digest() -> str:
    """Identify everything of plugin/gfxwrap that changes a native's bytes: the
    sources (headers included), this recipe, the toolchain and the source
    date the build stamps. The plugin's id (tools/build_renderer.py), the
    encoder helper's and a test plugin's each extend it with their role."""
    digest = hashlib.sha256()
    for path in _source_files():
        digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    digest.update(_build_term()[0])
    return digest.hexdigest()


def _shipped_version():
    """The FileVersion (named by the source date) and the date a shipped native carries."""
    date = _build_term()[1]
    return _tool("native_identity").calendar_version(date), date


def _runtime_objects(vcvars, work, identity, names=None):
    """The capture layer's C++ objects: RUNTIME_SOURCES, or `names` in its place
    (a composition test swaps the delivery worker for a CPU fixture)."""
    names = RUNTIME_SOURCES if names is None else tuple(names)
    sources = [SOURCE / f"{name}.cpp" for name in names]
    missing = [path.name for path in sources if not path.is_file()]
    if missing:
        raise RuntimeError("GPU runtime implementation is incomplete: " + ", ".join(missing))
    flags = [flag for flag in COMMON_FLAGS if not flag.startswith("/std:")]
    _cl(vcvars, flags + ["/std:c++17", "/EHsc", "/c", f"/FI{identity}",
        *(str(path) for path in sources), f"/Fo{work}\\"], work)
    return [str(work / f"{name}.obj") for name in names]


TEST_PLUGIN_ROLE = "-test-plugin"


def test_plugin_build_id() -> str:
    return sources_digest() + TEST_PLUGIN_ROLE


def _renderer_objects(vcvars, work: Path, renderer, defines) -> list[str]:
    """The stand-in renderer's objects: C sources as C17, C++ as C++17."""
    paths = [Path(item) if Path(item).is_absolute() else SOURCE / item for item in renderer]
    objects = []
    for suffix, flags in ((".c", COMMON_FLAGS),
                          (".cpp", [*(f for f in COMMON_FLAGS if not f.startswith("/std:")), "/std:c++17", "/EHsc"])):
        chosen = [path for path in paths if path.suffix == suffix]
        if chosen:
            _cl(vcvars, [*flags, *defines, f"/I{SOURCE}", "/c", *(str(path) for path in chosen), f"/Fo{work}\\"], work)
            objects += [str(work / f"{path.stem}.obj") for path in chosen]
    if len(objects) != len(paths):
        raise RuntimeError(f"a stand-in renderer source is neither C nor C++: {renderer}")
    return objects


def build_test_plugin(out_dir: Path, renderer=("fake_gfx.c",), vcvars: Path | None = None, *,
                      defines=(), runtime=None) -> Path:
    """sm64_trainer_gfx.dll for the host tests, into out_dir and nothing else:
    the capture layer (gfxwrap.c, RUNTIME_SOURCES or `runtime`) linked with a
    stand-in `renderer` (plugin/gfxwrap file names or absolute paths, each
    defining renderer_entries.h) instead of LINK's GLideN64. The binding,
    the exports and the .def are the shipped plugin's; GFXWRAP_TEST_PLUGIN
    adds only SM64_TEST_CONTROL_NAME (gfxwrap.c control_name). A development
    build that never enters the bundle: tools/build_natives.py builds the
    plugin from tools/build_renderer.py alone."""
    vcvars = vcvars or find_vcvars32()
    if vcvars is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="sm64_test_plugin_build_"))
    target = work / PLUGIN_NAME
    identity = work / "build_identity.h"
    identity.write_text(f'#define GFXWRAP_BUILD_ID "{test_plugin_build_id()}"\n', encoding="ascii")
    native_identity = _tool("native_identity")
    described = native_identity.Identity(
        "SM64 Trainer test graphics plugin: the capture layer with a stand-in renderer (developer tool)",
        native_identity.OURS_GPL3)
    try:
        objects = _runtime_objects(vcvars, work, identity, runtime)
        objects += _renderer_objects(vcvars, work, renderer, list(defines))
        _cl(vcvars, COMMON_FLAGS + ["/DGFXWRAP_GPU_RUNTIME", "/DGFXWRAP_TEST_PLUGIN", *defines, f"/FI{identity}",
                                    "/LD", str(SOURCE / CAPTURE_LAYER), f"/Fe:{target}", *objects, f"/Fo{work}\\",
                                    "/link", f"/DEF:{SOURCE / EXPORTS_DEF}", *LIBS, *RUNTIME_LIBS],
            work, identity=described)
        delivered = out_dir / target.name
        shutil.copy2(target, delivered)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return delivered


def helper_build_id() -> str:
    """The helper shares the plugin/gfxwrap digest with its own role suffix,
    so "rebuild the natives" is one command and one comparison."""
    return sources_digest() + "-gpu-encoder"


def build_encoder_helper(out_dir: Path, vcvars64: Path | None = None) -> Path:
    """SM64GpuEncoderV1.dll (x64, static CRT) into out_dir and nothing else."""
    vcvars64 = vcvars64 or find_vcvars64()
    if vcvars64 is None:
        raise RuntimeError("no x64 MSVC toolchain (vcvars64.bat) found")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    sources = [SOURCE / f"{name}.cpp" for name in ENCODER_SOURCES]
    missing = [path.name for path in sources if not path.is_file()]
    if missing:
        raise RuntimeError("encoder helper implementation is incomplete: " + ", ".join(missing))
    work = Path(tempfile.mkdtemp(prefix="sm64_gpu_encoder_build_"))
    target = work / SHIPPED_HELPER.name
    identity = work / "build_identity.h"
    identity.write_text(f'#define GBENC_BUILD_ID "{helper_build_id()}"\n', encoding="ascii")
    flags = [flag for flag in COMMON_FLAGS if not flag.startswith("/std:")]
    version, date = _shipped_version()
    try:
        _cl(vcvars64, flags + ["/std:c++17", "/EHsc", "/WX", f"/I{SOURCE}", f"/I{SOURCE / 'vendor'}",
                               f"/FI{identity}", "/LD", *(str(path) for path in sources),
                               f"/Fe:{target}", f"/Fo{work}\\", "/link", *ENCODER_LIBS], work, version=version)
        _tool("native_identity").stamp(target, date)
        delivered = out_dir / target.name
        shutil.copy2(target, delivered)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return delivered


def build_test_host(out_dir: Path, vcvars: Path | None = None) -> Path:
    """gfxwrap_host.exe (plugin/gfxwrap/host.c), which drives any plugin DLL."""
    vcvars = vcvars or find_vcvars32()
    if vcvars is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_build_host"
    work.mkdir(exist_ok=True)
    host = out_dir / "gfxwrap_host.exe"
    _cl(vcvars, COMMON_FLAGS + [str(SOURCE / "host.c"), f"/Fe:{host}", f"/Fo{work}\\",
                                "/link", *LIBS], work)
    return host


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--test-host", action="store_true",
                        help="build gfxwrap_host.exe and a test plugin (the capture layer with fake_gfx.c)")
    parser.add_argument("--out", type=Path, default=None,
                        help="output folder (default: the bundle for the helper, the scratch folder for the host)")
    args = parser.parse_args()
    vcvars = find_vcvars32()
    if vcvars is None:
        print("no x86 MSVC toolchain found (vcvars32.bat); install the VS Build Tools")
        return 2
    if args.test_host:
        out = args.out or Path(os.environ.get("TEMP", ".")) / "sm64_gfxwrap_host"
        host = build_test_host(out, vcvars)
        plugin = build_test_plugin(out, vcvars=vcvars)
        print(f"wrote {host}\nwrote {plugin}")
        return 0
    # The bundle gets the DLL alone: intermediates (.obj, .exp, .lib, the
    # batch step) stay in a scratch folder. tests/test_bundled_natives.py pins
    # that the bundled id matches the current sources; tools/build_natives.py
    # builds the plugin and this helper together.
    out = args.out or BUNDLE
    print(f"wrote {build_encoder_helper(out, find_vcvars64(vcvars))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())