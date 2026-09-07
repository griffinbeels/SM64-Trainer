"""Build THE CAPTURE LAYER (plugin/gfxwrap -> sm64_trainer_gfx.dll, 32-bit)
and, with --test-host, the host that drives it without Project64.

    uv run python tools/build_plugin.py                 # the shipped DLL, into src/sm64_events/data/plugin/
    uv run python tools/build_plugin.py --test-host --out <dir>   # gfxwrap_host.exe + fake_gfx.dll (+ the wrapper) into <dir>

Project64 1.6 is a 32-bit process, so the plugin is compiled with the x86
MSVC toolchain (`vcvars32.bat`, found through vswhere or the known
BuildTools folder) with a STATIC CRT (/MT): a user's machine need not carry
a VC redistributable for the layer to load. Every compiler is spawned with
no console window (spawned-processes rule). Prints the outputs it wrote;
exits non-zero with cl's own text on a failure.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "plugin" / "gfxwrap"
SHIPPED = REPO / "src" / "sm64_events" / "data" / "plugin" / "sm64_trainer_gfx.dll"
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


def build_wrapper(out_dir: Path, vcvars: Path | None = None) -> Path:
    vcvars = vcvars or find_vcvars32()
    if vcvars is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "_build_wrapper"
    work.mkdir(exist_ok=True)
    target = out_dir / "sm64_trainer_gfx.dll"
    _cl(vcvars, COMMON_FLAGS + ["/LD", str(SOURCE / "gfxwrap.c"), f"/Fe:{target}",
                                f"/Fo{work}\\", "/link", f"/DEF:{SOURCE / 'gfxwrap.def'}",
                                *LIBS], work)
    return target


def build_test_host(out_dir: Path, vcvars: Path | None = None) -> tuple:
    vcvars = vcvars or find_vcvars32()
    if vcvars is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
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
    work = Path(os.environ.get("TEMP", ".")) / "sm64_gfxwrap_build"
    built = build_wrapper(work, vcvars)
    SHIPPED.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(built, SHIPPED)
    print(f"wrote {SHIPPED}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
