"""Build every static library the renderer links, from pinned sources, into the build cache by hash.

    uv run python tools/build_renderer_libs.py              # Qt (once), then the libraries
    uv run python tools/build_renderer_libs.py --jobs 8     # fewer parallel compilers for Qt

SPDX-License-Identifier: GPL-2.0-or-later (licenses/README.md: the plugin's build tools)

The graphics plugin links four static libraries (renderer/README.md, "The
prebuilt libraries"). This is the recipe for all of them, built from one
staged copy of the pinned tree:

- libGLideNHQ.lib, the texture enhancement library, without xBRZ, with the
  libpng 1.6.32 and zlib 1.2.8 built here merged in;
- GLideNUI.lib, the settings window, with a static Qt merged in;
- osal.lib, upstream's small file-system layer;
- freetype253MT.lib, FreeType 2.5.3, which the plugin links itself.

Upstream's tree carries FreeType, libpng and zlib only as prebuilt .lib files
without their build files; these are built from each project's own release
archive with its own MSVC build files, release settings and the static CRT.

Their inputs are pinned in renderer/build-inputs.json (`qt`, `jom`,
`libraries`): qtbase 5.12.12 from download.qt.io, checked against the md5
download.qt.io publishes and a pinned SHA-256, built static with the
recorded configure line, and each library's archive by SHA-256. Each
result goes to `<cache>/prebuilt/<sha256>/<name>`, where tools/build_renderer.py
finds it once its hash is pinned. Two runs give the same bytes.

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
LIBRARIES = {"libGLideNHQ.lib": "libGLideNHQ.vcxproj", "GLideNUI.lib": "GLideNUI.vcxproj", "osal.lib": "osal.vcxproj"}
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


apply_patch = _load("source_patch").apply_patch   # a tracked patch file's edits, each anchored once


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


def fetch_source(cache: Path, spec: dict) -> Path:
    """A pinned library source, cached and checked (a download tool's user
    agent: SourceForge serves a web page to anything else)."""
    target = cache / "sources" / Path(spec["url"]).name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not (target.is_file() and _digest(target) == spec["sha256"]):
        request = urllib.request.Request(spec["url"], headers={"User-Agent": "Wget/1.21.4"})
        with urllib.request.urlopen(request, timeout=600) as response, target.open("wb") as out:
            shutil.copyfileobj(response, out)
        if _digest(target) != spec["sha256"]:
            target.unlink()
            raise ValueError(f"{spec['url']} is not the pinned file")
    return target


def platform_toolset(vcvars32: Path) -> str:
    """The newest MSVC platform toolset installed beside this vcvars (v145 for
    MSVC 14.5x): FreeType's own project names v100."""
    found = sorted({path.name for path in (vcvars32.parents[3] / "MSBuild" / "Microsoft" / "VC").glob(
        "v*/Platforms/Win32/PlatformToolsets/v*") if path.is_dir()}, key=lambda name: int(name[1:]))
    if not found:
        raise RuntimeError("no MSVC platform toolset found")
    return found[-1]


REPRO_PROPS = (b'<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003"><ItemDefinitionGroup>'
               b"<Lib><AdditionalOptions>/Brepro %(AdditionalOptions)</AdditionalOptions></Lib>"
               b"</ItemDefinitionGroup></Project>\r\n")
OSAL_PROPS = REPRO_PROPS.replace(b"<Lib>", b"<ClCompile><DebugInformationFormat>None</DebugInformationFormat>"
                                 b"</ClCompile><Lib>")


def build_dependencies(work: Path, cache: Path, vcvars32: Path) -> dict[str, Path]:
    """zlib 1.2.8, libpng 1.6.32 and FreeType 2.5.3 from their pinned sources,
    as the static /MT libraries upstream's tree carries prebuilt without the
    scripts that built them (`projects/msvc/lib/rel`): zlib.lib, libpng.lib,
    freetype253MT.lib. Their own MSVC build files, release settings, /MT."""
    deps = work / "deps"
    deps.mkdir()
    folders = {}
    for name, spec in renderer.inputs()["libraries"].items():
        with tarfile.open(fetch_source(cache, spec)) as bundle:
            bundle.extractall(deps, filter="data")
        folders[name] = deps / spec["folder"]
    (deps / "repro.props").write_bytes(REPRO_PROPS)
    zlib, png, freetype = folders["zlib"], folders["libpng"], folders["freetype"]
    script = deps / "build_deps.bat"
    script.write_bytes((
        _preamble(vcvars32) + f'set "CL=/experimental:deterministic /pathmap:{deps}=\\deps"\r\n'
        f'cd /d "{zlib}"\r\n'
        'nmake /nologo -f win32\\Makefile.msc CFLAGS="-nologo -MT -W3 -O2 -Oy-" ARFLAGS="-nologo /Brepro" zlib.lib\r\n'
        "if errorlevel 1 exit /b 30\r\n"
        f'cd /d "{png}"\r\n'
        f'nmake /nologo -f scripts\\makefile.vcwin32 CPPFLAGS="-I{zlib}" '
        'CFLAGS="-nologo -D_CRT_SECURE_NO_DEPRECATE -D_CRT_SECURE_NO_WARNINGS -MT -O2 -W3" '
        'ARFLAGS="-nologo /Brepro" libpng.lib\r\n'
        "if errorlevel 1 exit /b 31\r\n"
        f'cd /d "{freetype}\\builds\\windows\\vc2010"\r\n'
        f'"{renderer.find_msbuild(vcvars32)}" freetype.vcxproj /nologo /nr:false /v:minimal '
        f'"/p:Configuration=Release Multithreaded" /p:Platform=Win32 /p:PlatformToolset={platform_toolset(vcvars32)} '
        f'/p:WindowsTargetPlatformVersion=10.0 "/p:ForceImportBeforeCppTargets={deps / "repro.props"}"\r\n'
        "if errorlevel 1 exit /b 32\r\nexit /b 0\r\n").encode())
    _run_hidden_low(script, deps / "build_deps.log")
    built = {"zlib.lib": zlib / "zlib.lib", "libpng.lib": png / "libpng.lib",
             "freetype253MT.lib": freetype / "objs" / "win32" / "vc2010" / "freetype253MT.lib"}
    for name, path in built.items():
        if not path.is_file():
            raise RuntimeError(f"the dependency build reported success but {path} is missing")
    return built


def build_libraries(root: Path, prefix: Path, cache: Path, vcvars32: Path) -> dict[str, Path]:
    work = root / "libs"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    tree = renderer.pristine_source(cache, work, None)
    stage(tree, prefix)
    msvc = tree / "projects" / "msvc"
    # libGLideNHQ merges libpng.lib and zlib.lib from lib/rel: ours, built from source.
    deps = build_dependencies(work, cache, vcvars32)
    for name in ("libpng.lib", "zlib.lib"):
        shutil.copyfile(deps[name], msvc / "lib" / "rel" / name)
    user_props = work / "empty-user-props"
    user_props.mkdir()
    args = ["/nologo", "/nr:false", "/m:1", "/v:minimal", "/p:Configuration=Release", "/p:Platform=Win32",
            f"/p:QTDIR={prefix}", f"/p:SolutionDir={msvc}\\", f"/p:UserRootDir={user_props}\\",
            "/p:UseMultiToolTask=false", "/p:BuildProjectReferences=false"]
    msbuild = renderer.find_msbuild(vcvars32)
    # osal.vcxproj sets no librarian options to anchor /Brepro on, and writes
    # a program database whose path /pathmap would rewrite: a property sheet
    # for that build alone gives it /Brepro and no debug information.
    (work / "osal.props").write_bytes(OSAL_PROPS)
    extra = {"osal.vcxproj": f' "/p:ForceImportBeforeCppTargets={work / "osal.props"}"'}
    lines = "".join(f'"{msbuild}" {project} {" ".join(args)}{extra.get(project, "")}\r\nif errorlevel 1 exit /b 20\r\n'
                    for project in LIBRARIES.values())
    script = work / "build_libs.bat"
    script.write_bytes((
        _preamble(vcvars32) + f'set "CL=/experimental:deterministic /pathmap:{tree}=\\gliden64"\r\n'
        f'cd /d "{msvc}"\r\n{lines}exit /b 0\r\n').encode())
    _run_hidden_low(script, work / "build_libs.log")
    built = {}
    outputs = {name: msvc / "bin" / "Win32" / "Release" / "lib" / name for name in LIBRARIES}
    outputs["freetype253MT.lib"] = deps["freetype253MT.lib"]   # the plugin links it itself
    for name, path in outputs.items():
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
