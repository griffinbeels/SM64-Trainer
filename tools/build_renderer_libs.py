"""Build the renderer's two prebuilt libraries that differ from upstream's,
libGLideNHQ.lib and GLideNUI.lib, from pinned sources, into the build cache by hash.

    uv run python tools/build_renderer_libs.py              # Qt (once), then both libraries
    uv run python tools/build_renderer_libs.py --jobs 8     # fewer parallel compilers for Qt

SPDX-License-Identifier: GPL-2.0-or-later (licenses/README.md: the plugin's build tools)

The graphics plugin links three static libraries the upstream project builds
itself (renderer/README.md, "The three prebuilt libraries"). This is the recipe
for two of them, built from one staged copy of the pinned tree:

- libGLideNHQ.lib, the texture enhancement library, without xBRZ;
- GLideNUI.lib, the settings window, with a static Qt merged in.

Their inputs are pinned in renderer/build-inputs.json (`qt`, `jom`): qtbase
5.12.12 from download.qt.io, checked against the md5 download.qt.io publishes
and a pinned SHA-256, built static with the recorded configure line. Each
result goes to `<cache>/prebuilt/<sha256>/<name>`, where tools/build_renderer.py
finds it once its hash is pinned. osal.lib is still the 2026-09 build.

Two licenses decide what is in them (2026-11-07). GLideN64 is GPL v2 only, so:
- Qt is 5.12.12, not 5.15.2: from Qt 5.14 on, QtCore's qcalendar.cpp is GPL v3
  or later only. Every qtbase 5.12.12 file the settings window links offers the
  GPL v2.
- xBRZ (GLideNHQ's TextureFilters_xbrz.*, GPL v3 only) is left out by
  renderer/patches/no-xbrz.json: the xBRZ modes enhance nothing, an ini that
  names one loads as None, and the settings window no longer lists them.

It takes a long while on first run (Qt itself); every compiler runs hidden and
at below-normal priority, since he may be playing. The installed Qt is reused
from `<cache>/../qt512/` after that. Prints each library's SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PATCHES = (REPO / "renderer" / "patches" / "no-xbrz.json",)
LIBRARIES = {"libGLideNHQ.lib": "libGLideNHQ.vcxproj", "GLideNUI.lib": "GLideNUI.vcxproj"}
BELOW_NORMAL = 0x00004000
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# Upstream lists 5.15's support modules; 5.12's Windows platform plugin also
# needs the accessibility support module (src/plugins/platforms/windows/windows.pro).
EXTRA_QT_LIBS = ("Qt5AccessibilitySupport.lib",)
LIB_ANCHOR = b"<AdditionalDependencies>Dwmapi.lib;Version.lib;Wtsapi32.lib;Userenv.lib;Netapi32.lib;"
HQ_LIB_ANCHOR = b"      <AdditionalDependencies>libpng.lib;zlib.lib;</AdditionalDependencies>\n    </Lib>"
BREPRO = b"      <AdditionalOptions>/Brepro %(AdditionalOptions)</AdditionalOptions>\n"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"libs_{name}", REPO / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


renderer = _load("build_renderer")


def _digest(path: Path, algorithm: str = "sha256") -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def qt_root(cache: Path, version: str) -> Path:
    """The Qt build folder beside the renderer cache: `qt512` for 5.12.x."""
    return cache.parent / ("qt" + "".join(version.split(".")[:2]))


def fetch_qt(root: Path, spec: dict) -> Path:
    """The qtbase source tree, from the pinned archive (md5 and SHA-256 both checked)."""
    archive = root / Path(spec["url"]).name
    root.mkdir(parents=True, exist_ok=True)
    if not archive.is_file():
        with urllib.request.urlopen(spec["url"], timeout=900) as response, archive.open("wb") as out:
            shutil.copyfileobj(response, out)
    if _digest(archive, "md5") != spec["md5"] or _digest(archive) != spec["sha256"]:
        archive.unlink()
        raise ValueError(f"{archive.name} is not the pinned qtbase archive")
    source = root / "src"
    if not (source / "configure.bat").is_file():
        staging = root / "extract"
        shutil.rmtree(staging, ignore_errors=True)
        with tarfile.open(archive, "r:xz") as bundle:
            bundle.extractall(staging, filter="data")
        (top,) = list(staging.iterdir())
        shutil.rmtree(source, ignore_errors=True)
        top.rename(source)
        staging.rmdir()
    return source


def fetch_jom(root: Path, spec: dict) -> Path:
    folder = root / "jom"
    if not (folder / "jom.exe").is_file():
        with urllib.request.urlopen(spec["url"], timeout=120) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != spec["sha256"]:
            raise ValueError("jom download is not the pinned archive")
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            bundle.extractall(folder)
    return folder / "jom.exe"


def _run_hidden_low(script: Path, log: Path) -> None:
    """One batch file, hidden, at below-normal priority (its compilers inherit it)."""
    with log.open("wb") as out:
        result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=str(script.parent), stdout=out,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, check=False,
                                creationflags=_NO_WINDOW | BELOW_NORMAL)
    if result.returncode != 0:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-25:])
        raise RuntimeError(f"{script.name} failed ({result.returncode}); log at {log}\n{tail}")


def _preamble(vcvars32: Path) -> str:
    # The harness may set NoDefaultCurrentDirectoryInExePath, which stops cmd
    # finding configure.bat in its own folder; vcvars wants vswhere on PATH.
    installer = vcvars32.parents[5] / "Installer"   # ...\Microsoft Visual Studio\Installer
    return ("@echo off\r\nset VSCMD_SKIP_SENDTELEMETRY=1\r\nset NoDefaultCurrentDirectoryInExePath=\r\n"
            f'set "PATH={installer};%PATH%"\r\ncall "{vcvars32}" >nul\r\nif errorlevel 1 exit /b 10\r\n')


def configure_args(spec: dict, root: Path) -> list[str]:
    """The recorded configure line plus where it installs. The prefix compiled
    into QtCore is Qt's neutral default, not this machine's cache path."""
    return ["-prefix", f"C:/Qt/Qt-{spec['version']}", "-extprefix", (root / "prefix").as_posix(),
            *spec["configure"]]


def build_qt(root: Path, source: Path, jom: Path, spec: dict, vcvars32: Path, jobs: int) -> Path:
    """Configure, build and install qtbase into `root/prefix`; its build tree
    (about 1 GB) is removed afterwards, the installed Qt is what is reused."""
    prefix = root / "prefix"
    script = root / "build_qt.bat"
    script.write_bytes((
        _preamble(vcvars32) + f'set "PATH={jom.parent};%PATH%"\r\n'
        # Object files name their sources by a fixed path, not this user's folder.
        f'set "CL=/experimental:deterministic /pathmap:{source}=\\qtbase"\r\n'
        f'cd /d "{source}"\r\ncall .\\configure.bat {" ".join(configure_args(spec, root))}\r\n'
        f"if errorlevel 1 exit /b 11\r\njom -j {jobs}\r\nif errorlevel 1 exit /b 12\r\n"
        f"jom -j {jobs} install\r\nif errorlevel 1 exit /b 13\r\nexit /b 0\r\n").encode())
    _run_hidden_low(script, root / "build_qt.log")
    shutil.rmtree(source, ignore_errors=True)
    return prefix


def apply_patch(tree: Path, patch: Path) -> None:
    """A tracked patch file's edits, each to text that occurs exactly once."""
    for edit in json.loads(patch.read_text(encoding="utf-8"))["edits"]:
        path = tree / edit["file"]
        data = path.read_bytes()
        before, after = edit["before"].encode("utf-8"), edit["after"].encode("utf-8")
        if data.count(before) != 1:
            raise ValueError(f"{patch.name}: {edit['file']} no longer holds its anchor exactly once "
                             f"({data.count(before)} times)")
        path.write_bytes(data.replace(before, after))


def stage_ui_project(project: Path, prefix: Path) -> None:
    """Upstream's GLideNUI.vcxproj, linked against this Qt: the support modules
    5.12's platform plugin also needs, and no build hooks."""
    data = project.read_bytes()
    if data.count(LIB_ANCHOR) != 1:
        raise ValueError("GLideNUI.vcxproj Release library anchor is not unique")
    extra = [name for name in EXTRA_QT_LIBS if (prefix / "lib" / name).is_file()]
    data = data.replace(LIB_ANCHOR, LIB_ANCHOR + "".join(f"{name};" for name in extra).encode())
    for event in (b"PreBuildEvent", b"PostBuildEvent"):
        if b"<" + event + b">" in data:
            raise ValueError(f"GLideNUI.vcxproj carries a {event.decode()}")
    # A repeatable library: no timestamps in its members' headers.
    anchor = b"      <LinkTimeCodeGeneration>true</LinkTimeCodeGeneration>\n    </Lib>"
    if data.count(anchor) != 1:
        raise ValueError("GLideNUI.vcxproj Lib anchor is not unique")
    project.write_bytes(data.replace(anchor, anchor[:-len(b"    </Lib>")] + BREPRO + b"    </Lib>"))


def stage_hq_project(project: Path) -> None:
    """Upstream's libGLideNHQ.vcxproj, built repeatable (no member timestamps)."""
    data = project.read_bytes()
    if data.count(HQ_LIB_ANCHOR) != 1:
        raise ValueError("libGLideNHQ.vcxproj Release Lib anchor is not unique")
    project.write_bytes(data.replace(HQ_LIB_ANCHOR, HQ_LIB_ANCHOR[:-len(b"    </Lib>")] + BREPRO + b"    </Lib>"))


GUARDED_HEADERS = {"AboutDialog.h": b"UI_ABOUTDIALOG_H", "ConfigDialog.h": b"UI_CONFIGDIALOG_H"}
FIXED_MTIME = 1_600_000_000   # 2020-09-13 UTC; any constant works


def stage_ui_sources(tree: Path) -> None:
    """Two upstream headers guard themselves with the very macro uic writes into
    the header it generates for them (ui_AboutDialog.h: UI_ABOUTDIALOG_H), so
    whichever is included second is skipped and nothing compiles. Each gets its
    own guard; no code changes."""
    for name, guard in GUARDED_HEADERS.items():
        path = tree / "src" / "GLideNUI" / name
        data = path.read_bytes()
        if data.count(guard) < 2 or not data.startswith(b"#ifndef " + guard):
            raise ValueError(f"{name}: its include guard changed")
        path.write_bytes(data.replace(guard, b"GLIDENUI_" + guard[3:]))
    # rcc writes each resource file's modification time into qrc_icon.cpp, and
    # an extracted tree's times are the extraction's: fix them, so two builds
    # of the library are the same bytes.
    for path in (tree / "src" / "GLideNUI").iterdir():
        os.utime(path, (FIXED_MTIME, FIXED_MTIME))


def stage(tree: Path, prefix: Path) -> None:
    """Every edit the two libraries are built with, on a fresh pinned tree."""
    for patch in PATCHES:
        apply_patch(tree, patch)
    msvc = tree / "projects" / "msvc"
    stage_ui_sources(tree)
    stage_ui_project(msvc / "GLideNUI.vcxproj", prefix)
    stage_hq_project(msvc / "libGLideNHQ.vcxproj")


def build_libraries(root: Path, prefix: Path, cache: Path, vcvars32: Path) -> dict[str, Path]:
    work = root / "libs"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    tree = renderer.pristine_source(cache, work, None)
    stage(tree, prefix)
    msvc = tree / "projects" / "msvc"
    user_props = work / "empty-user-props"
    user_props.mkdir()
    args = ["/nologo", "/nr:false", "/m:1", "/v:minimal", "/p:Configuration=Release", "/p:Platform=Win32",
            f"/p:QTDIR={prefix}", f"/p:SolutionDir={msvc}\\", f"/p:UserRootDir={user_props}\\",
            "/p:UseMultiToolTask=false", "/p:BuildProjectReferences=false"]
    msbuild = renderer.find_msbuild(vcvars32)
    lines = "".join(f'"{msbuild}" {project} {" ".join(args)}\r\nif errorlevel 1 exit /b 20\r\n'
                    for project in LIBRARIES.values())
    script = work / "build_libs.bat"
    script.write_bytes((
        _preamble(vcvars32) + f'set "CL=/experimental:deterministic /pathmap:{tree}=\\gliden64"\r\n'
        f'cd /d "{msvc}"\r\n{lines}exit /b 0\r\n').encode())
    _run_hidden_low(script, work / "build_libs.log")
    built = {}
    for name in LIBRARIES:
        path = msvc / "bin" / "Win32" / "Release" / "lib" / name
        if not path.is_file():
            raise RuntimeError(f"MSBuild reported success but {path} is missing")
        target = renderer.prebuilt_path(cache, name, _digest(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
        built[name] = target
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--cache", type=Path, default=None, help="renderer build cache folder")
    parser.add_argument("--jobs", type=int, default=12, help="parallel compilers for Qt (default 12)")
    args = parser.parse_args()
    vcvars32 = renderer.build_plugin.find_vcvars32()
    if vcvars32 is None:
        print("no x86 MSVC toolchain (vcvars32.bat) found")
        return 2
    cache = args.cache or renderer.default_cache()
    inputs = renderer.inputs()
    try:
        root = qt_root(cache, inputs["qt"]["version"])
        prefix = root / "prefix"
        if not (prefix / "lib" / "Qt5Core.lib").is_file():
            source = fetch_qt(root, inputs["qt"])
            build_qt(root, source, fetch_jom(root, inputs["jom"]), inputs["qt"], vcvars32, args.jobs)
        built = build_libraries(root, prefix, cache, vcvars32)
        shutil.rmtree(root / "libs", ignore_errors=True)
    except (RuntimeError, ValueError, FileNotFoundError) as failure:
        print(failure)
        return 2
    for name, path in built.items():
        print(f"wrote {path}\n{name} sha256 {_digest(path)}")
    print("pin them in renderer/build-inputs.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
