"""Build THE CAPTURE LAYER (plugin/gfxwrap -> sm64_trainer_gfx.dll, 32-bit),
the 64-bit NVENC encoder helper the server spawns (SM64GpuEncoderV1.dll)
and, with --test-host, the host that drives the wrapper without Project64.
The renderer the wrapper wraps is built by tools/build_renderer.py.

    uv run python tools/build_plugin.py                 # wrapper + encoder helper, into src/sm64_events/data/plugin/
    uv run python tools/build_plugin.py --out <dir>          # a candidate folder: the wrapper alone
    uv run python tools/build_plugin.py --test-host --out <dir>   # gfxwrap_host.exe + fake_gfx.dll (+ the wrapper) into <dir>

The wrapper has ONE mode since 2026-09-16: the GPU runtime (the stamp adapter,
the control worker and the delivery worker); the raw ReadScreen/frame-stream
capture and the control-only diagnostic build were deleted with the CPU path.

Project64 1.6 is a 32-bit process, so the plugin is compiled with the x86
MSVC toolchain (`vcvars32.bat`, found through vswhere or the known
BuildTools folder) with a STATIC CRT (/MT): a user's machine need not carry
a VC redistributable for the layer to load. Every compiler is spawned with
no console window (spawned-processes rule). Prints the outputs it wrote;
exits non-zero with cl's own text on a failure.
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
SHIPPED = REPO / "src" / "sm64_events" / "data" / "plugin" / "sm64_trainer_gfx.dll"
SHIPPED_HELPER = SHIPPED.parent / "SM64GpuEncoderV1.dll"
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
    "gpu_delivery", "gpu_delivery_context", "source_snapshot", "gl_snapshot", "gpu_selection",
    "gpu_bridge", "gpu_channel",
)
# The encoder helper: a 64-bit DLL loaded by the isolated helper process,
# never by Project64. Its NVENC header is the vendored nvEncodeAPI.h.
ENCODER_SOURCES = ("gpu_encoder_dll", "gpu_bridge_encoder")
ENCODER_LIBS = ["d3d11.lib", "dxgi.lib"]


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


def _cl(vcvars: Path, args: list, cwd: Path) -> None:
    """Run one cl.exe invocation inside vcvars32's environment, in `cwd`."""
    # A batch file rather than a `cmd /c "..."` string: subprocess re-quotes
    # an argument holding quotes, and cmd then sees `\"` literally.
    quoted = " ".join(f'"{arg}"' if " " in str(arg) else str(arg) for arg in args)
    script = cwd / "build_step.bat"
    script.write_text(f'@echo off\r\ncall "{vcvars}" >nul\r\ncl {quoted}\r\n',
                      encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=str(cwd),
                            capture_output=True, text=True, creationflags=_NO_WINDOW,
                            check=False)
    if result.returncode != 0:
        raise RuntimeError(f"cl failed ({result.returncode}):\n{result.stdout}\n{result.stderr}")


def wrapper_build_id() -> str:
    """Identify the native source bundle, including headers used by the wrapper."""
    digest = hashlib.sha256()
    for path in sorted(SOURCE.iterdir()):
        if path.suffix in {".c", ".cpp", ".h", ".def"}:
            digest.update(path.name.encode() + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _runtime_objects(vcvars, work, identity):
    sources = [SOURCE / f"{name}.cpp" for name in RUNTIME_SOURCES]
    missing = [path.name for path in sources if not path.is_file()]
    if missing:
        raise RuntimeError("GPU runtime implementation is incomplete: " + ", ".join(missing))
    flags = [flag for flag in COMMON_FLAGS if not flag.startswith("/std:")]
    _cl(vcvars, flags + ["/std:c++17", "/EHsc", "/c", f"/FI{identity}",
        *(str(path) for path in sources), f"/Fo{work}\\"], work)
    return [str(work / f"{name}.obj") for name in RUNTIME_SOURCES]


def build_wrapper(out_dir: Path, vcvars: Path | None = None) -> Path:
    """sm64_trainer_gfx.dll (x86, static CRT, GPU runtime) into out_dir and nothing else."""
    vcvars = vcvars or find_vcvars32()
    if vcvars is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # The output folder receives the DLL and nothing else. Objects, the
    # export library, the batch step and the identity header live in a
    # fresh scratch folder that is removed after the copy: a build aimed at
    # an install candidate must not leave a `_build_wrapper` folder and an
    # .exp/.lib pair beside the file he installs (his ask, 2026-09-15).
    work = Path(tempfile.mkdtemp(prefix="sm64_gfxwrap_build_"))
    target = work / "sm64_trainer_gfx.dll"
    identity = work / "build_identity.h"
    # The suffix names the one shipped mode; the setup screen and
    # tests/test_bundled_natives.py compare bundles by this id.
    build_id = wrapper_build_id() + "-gpu-runtime"
    identity.write_text(f'#define GFXWRAP_BUILD_ID "{build_id}"\n', encoding="ascii")
    defines = ["/DGFXWRAP_GPU_RUNTIME"]
    objects = _runtime_objects(vcvars, work, identity)
    extra_libs = ["d3d11.lib", "dxgi.lib"]
    try:
        _cl(vcvars, COMMON_FLAGS + defines + [f"/FI{identity}", "/LD", str(SOURCE / "gfxwrap.c"), f"/Fe:{target}",
                                    *objects, f"/Fo{work}\\", "/link", f"/DEF:{SOURCE / 'gfxwrap.def'}",
                                    *LIBS, *extra_libs], work)
        delivered = out_dir / target.name
        shutil.copy2(target, delivered)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return delivered


def helper_build_id() -> str:
    """One id for the whole native bundle: the helper shares the wrapper's
    digest with its own suffix, so "rebuild the natives" is one command and
    one comparison."""
    return wrapper_build_id() + "-gpu-encoder"


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
    try:
        _cl(vcvars64, flags + ["/std:c++17", "/EHsc", "/WX", f"/I{SOURCE}", f"/I{SOURCE / 'vendor'}",
                               f"/FI{identity}", "/LD", *(str(path) for path in sources),
                               f"/Fe:{target}", f"/Fo{work}\\", "/link", *ENCODER_LIBS], work)
        delivered = out_dir / target.name
        shutil.copy2(target, delivered)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return delivered


def build_test_host(out_dir: Path, vcvars: Path | None = None) -> tuple:
    vcvars = vcvars or find_vcvars32()
    if vcvars is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_build_host"
    work.mkdir(exist_ok=True)
    host = out_dir / "gfxwrap_host.exe"
    fake = out_dir / "fake_gfx.dll"
    _cl(vcvars, COMMON_FLAGS + [str(SOURCE / "host.c"), f"/Fe:{host}", f"/Fo{work}\\",
                                "/link", *LIBS], work)
    _cl(vcvars, COMMON_FLAGS + ["/LD", str(SOURCE / "fake_gfx.c"), f"/Fe:{fake}",
                                f"/Fo{work}\\", "/link", *LIBS], work)
    return host, fake


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--test-host", action="store_true",
                        help="also build gfxwrap_host.exe and fake_gfx.dll")
    parser.add_argument("--out", type=Path, default=None,
                        help="output folder (default: the shipped location for the DLL, "
                             "the scratch folder for the host)")
    args = parser.parse_args()
    vcvars = find_vcvars32()
    if vcvars is None:
        print("no x86 MSVC toolchain found (vcvars32.bat); install the VS Build Tools")
        return 2
    if args.test_host:
        out = args.out or Path(os.environ.get("TEMP", ".")) / "sm64_gfxwrap_host"
        host, fake = build_test_host(out, vcvars)
        wrapper = build_wrapper(out, vcvars)
        print(f"wrote {host}\nwrote {fake}\nwrote {wrapper}")
        return 0
    if args.out is not None:
        print(f"wrote {build_wrapper(args.out, vcvars)}")
        return 0
    # The shipped location gets the DLL alone: intermediates (.obj, .exp,
    # .lib, the batch step) stay in a scratch folder.
    # The bundled wrapper is the GPU capture candidate built from THESE
    # sources: the setup screen compares the installed file with it, so a
    # stale bundle reads as "differs from this build" and asks him to
    # reinstall an older DLL (round 48). tests/test_bundled_natives.py pins that
    # every bundled build id matches the current sources.
    work = Path(os.environ.get("TEMP", ".")) / "sm64_gfxwrap_build"
    built = build_wrapper(work, vcvars)
    SHIPPED.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, SHIPPED)
    print(f"wrote {SHIPPED}")
    helper = build_encoder_helper(work, find_vcvars64(vcvars))
    shutil.copy2(helper, SHIPPED_HELPER)
    print(f"wrote {SHIPPED_HELPER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
