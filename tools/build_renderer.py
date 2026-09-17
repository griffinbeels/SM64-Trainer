"""Build THE RENDERER: LINK's GLideN64 v4.2 plus the capture overlay ->
GLideN64_SM64Trainer.dll (32-bit), the graphics plugin the capture wrapper
wraps. Without it the wrapper captures nothing (no SourceV2 export), so the
setup screen installs both.

    uv run python tools/build_renderer.py            # into src/sm64_events/data/plugin/
    uv run python tools/build_renderer.py --out DIR  # a candidate folder: the DLL and nothing else

Inputs, all pinned in renderer/build-inputs.json:
  * the upstream archive at commit d0d1010 (downloaded into the build cache
    and verified by hash; --source names an already-verified tree instead);
  * the overlay: plugin/gfxwrap files staged by tools/stage_link_witness.py;
  * three static libraries the upstream project links (GLideNUI needs a
    static Qt 5.15.2; renderer/README.md records how they were produced).
    They live in the build cache, verified by hash, never in git.

The build cache is %LOCALAPPDATA%/SM64Trainer/build-cache/renderer so it
survives worktrees. Intermediates stay in a scratch folder that is removed;
the output folder receives the DLL alone. MSBuild runs hidden.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
INPUTS = REPO / "renderer" / "build-inputs.json"
SHIPPED_RENDERER = REPO / "src" / "sm64_events" / "data" / "plugin" / "GLideN64_SM64Trainer.dll"
LABEL = "LINK v4.2 [SM64 Trainer]"
OVERLAY_SOURCES = (
    "link_dispatch.h", "link_dispatch.cpp", "renderer_gl_state.h", "gl_snapshot.h",
    "source_format.h", "practice_rom.h", "context_lifetime.h", "context_lifetime.cpp",
    "renderer_boundary.h", "renderer_boundary.cpp", "link_source_api.h", "link_source_api.cpp",
)
OVERLAY_MODULES = ("link_dispatch.cpp", "renderer_boundary.cpp", "link_source_api.cpp", "context_lifetime.cpp")
DEFINES = "SM64_REPLAY_GL_WITNESS;SM64_REPLAY_SOURCE;SM64_REPLAY_CONTEXT_LIFETIME;"
RECIPE_VERSION = "2"
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


witness = _load("stage_link_witness", REPO / "tools" / "stage_link_witness.py")
build_plugin = _load("build_plugin", REPO / "tools" / "build_plugin.py")


def inputs() -> dict:
    return json.loads(INPUTS.read_text(encoding="utf-8"))


def default_cache() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "SM64Trainer" / "build-cache" / "renderer"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def renderer_build_id() -> str:
    """Names everything the DLL is made of: the pinned upstream tree, the
    overlay sources, the staging tool, the label and this recipe. The static
    libraries are pinned inputs of the same tree, so they need no separate
    term. A wrapper-only edit does not change it."""
    digest = hashlib.sha256()
    digest.update(f"{witness.TREE_HASH}\0{LABEL}\0{RECIPE_VERSION}\0".encode())
    digest.update((REPO / "tools" / "stage_link_witness.py").read_bytes() + b"\0")
    for name in OVERLAY_SOURCES:
        digest.update(name.encode() + b"\0" + (REPO / "plugin" / "gfxwrap" / name).read_bytes() + b"\0")
    return digest.hexdigest() + "-renderer"


def embedded_identity(path: Path) -> str | None:
    ids = set(re.findall(rb"[0-9a-f]{64}-renderer", path.read_bytes()))
    return ids.pop().decode() if len(ids) == 1 else None


# -- inputs -----------------------------------------------------------------

def fetch_archive(cache: Path, spec: dict) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / f"GLideN64-{spec['commit']}.zip"
    if not archive.is_file():
        with urllib.request.urlopen(spec["url"], timeout=120) as response:
            data = response.read()
        archive.write_bytes(data)
    actual = _sha256(archive)
    if actual != spec["archive_sha256"]:
        archive.unlink()
        raise ValueError(f"upstream archive hash {actual} differs from the pinned {spec['archive_sha256']}")
    return archive


def pristine_source(cache: Path, scratch: Path, source: Path | None) -> Path:
    """A verified copy of the pinned upstream tree (tools/stage_link_witness.py
    checks every file against the pinned tree hash)."""
    if source is not None:
        witness.verify_source(source)
        return source
    archive = fetch_archive(cache, inputs()["upstream"])
    extracted = scratch / "upstream"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    roots = [entry for entry in extracted.iterdir() if entry.is_dir()]
    if len(roots) != 1:
        raise ValueError("the upstream archive must hold exactly one top-level folder")
    witness.verify_source(roots[0])
    return roots[0]


def prebuilt_libraries(cache: Path) -> dict[str, Path]:
    found = {}
    for name, expected in inputs()["prebuilt_libraries"].items():
        path = cache / name
        if not path.is_file():
            raise FileNotFoundError(
                f"{name} is not in the build cache {cache}; renderer/README.md says how it is produced")
        actual = _sha256(path)
        if actual != expected:
            raise ValueError(f"{name} in the build cache ({actual}) is not the pinned library ({expected})")
        found[name] = path
    return found


# -- staging ----------------------------------------------------------------

def stage(pristine: Path, staged: Path, libraries: dict[str, Path], build_id: str) -> None:
    witness.overlay(pristine, staged, boundary=True, label=LABEL, context_lifetime=True)
    gl = staged / witness.GL
    (gl / "renderer_build_identity.h").write_text(
        f'#define SM64_TRAINER_RENDERER_BUILD_ID "{build_id}"\n', encoding="ascii")
    commit = inputs()["upstream"]["commit"][:7]
    (staged / "src" / "Revision.h").write_text(
        f'#define PLUGIN_REVISION "{commit}"\n#define PLUGIN_REVISION_W L"{commit}"\n', encoding="ascii")
    msvc = staged / "projects" / "msvc"
    project = msvc / "GLideN64.vcxproj"
    data = project.read_bytes()
    anchor = b'    <ClCompile Include="..\\..\\src\\3DMath.cpp" />'
    if data.count(anchor) != 1:
        raise ValueError("GLideN64.vcxproj anchor is not unique")
    extra = b"".join(b'    <ClCompile Include="..\\..\\src\\Graphics\\OpenGLContext\\' + name.encode() + b'" />\r\n'
                     for name in OVERLAY_MODULES)
    data = data.replace(anchor, extra + anchor)
    old = b"<PreprocessorDefinitions>UNICODE;_USRDLL;"
    if data.count(old) != 1:
        raise ValueError(f"GLideN64.vcxproj anchor count changed: {old[:40]!r}")
    data = data.replace(old, b"<LanguageStandard>stdcpp17</LanguageStandard>\r\n      <PreprocessorDefinitions>"
                        + DEFINES.encode() + b"UNICODE;_USRDLL;")
    project.write_bytes(data)
    # No pre/post-build hooks: the upstream project rewrites Revision.h from
    # git and copies the DLL into emulator folders. Neither belongs here.
    for path in msvc.glob("*.vcxproj"):
        stripped = re.sub(rb"<(PreBuildEvent|PostBuildEvent)>.*?</\1>", b"", path.read_bytes(), flags=re.S)
        if b"copy /Y" in stripped or b"getRevision.bat" in stripped:
            raise ValueError(f"{path.name} still carries a build hook")
        path.write_bytes(stripped)
    lib_dir = msvc / "bin" / "Win32" / "Release" / "lib"
    lib_dir.mkdir(parents=True)
    for name, path in libraries.items():
        shutil.copyfile(path, lib_dir / name)


# -- building ---------------------------------------------------------------

def find_msbuild(vcvars32: Path) -> Path:
    candidate = vcvars32.parents[3] / "MSBuild" / "Current" / "Bin" / "MSBuild.exe"
    if candidate.is_file():
        return candidate
    raise RuntimeError(f"MSBuild.exe not found beside {vcvars32}")


def msbuild(staged: Path, vcvars32: Path, log: Path) -> Path:
    msvc = staged / "projects" / "msvc"
    user_props = staged / "empty-user-props"
    user_props.mkdir()
    args = ["/nologo", "/nr:false", "/m:2", "/v:minimal", "/p:Configuration=Release", "/p:Platform=Win32",
            f"/p:SolutionDir={msvc}\\", f"/p:UserRootDir={user_props}\\",
            "/p:N64PluginsDir=", "/p:Mupen64PluginsDir=", "/p:Mupen64PluginsDir_x64=",
            "/p:UseMultiToolTask=false", "/p:PreBuildEventUseInBuild=false",
            "/p:PostBuildEventUseInBuild=false", "/p:BuildProjectReferences=false"]
    script = staged / "build_renderer.bat"
    script.write_text(
        f'@echo off\r\ncall "{vcvars32}" >nul\r\nif errorlevel 1 exit /b %errorlevel%\r\n'
        "set N64PluginsDir=\r\nset Mupen64PluginsDir=\r\nset Mupen64PluginsDir_x64=\r\n"
        f'"{find_msbuild(vcvars32)}" GLideN64.vcxproj {" ".join(args)}\r\nexit /b %errorlevel%\r\n',
        encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=str(msvc), capture_output=True,
                            text=True, creationflags=_NO_WINDOW, check=False, stdin=subprocess.DEVNULL)
    log.write_text(result.stdout + result.stderr, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])
        raise RuntimeError(f"MSBuild failed ({result.returncode}); log at {log}\n{tail}")
    built = msvc / "bin" / "Win32" / "Release" / "GLideN64.dll"
    if not built.is_file():
        raise RuntimeError(f"MSBuild reported success but {built} is missing")
    return built


def build_renderer(out_dir: Path, *, cache: Path | None = None, source: Path | None = None,
                   vcvars32: Path | None = None, keep_scratch: bool = False) -> Path:
    vcvars32 = vcvars32 or build_plugin.find_vcvars32()
    if vcvars32 is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
    cache = cache or default_cache()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    build_id = renderer_build_id()
    libraries = prebuilt_libraries(cache)
    scratch = Path(tempfile.mkdtemp(prefix="sm64_rdr_"))
    try:
        pristine = pristine_source(cache, scratch, source)
        staged = scratch / "staged"
        stage(pristine, staged, libraries, build_id)
        built = msbuild(staged, vcvars32, scratch / "msbuild.log")
        if embedded_identity(built) != build_id:
            raise RuntimeError("the built renderer does not carry its own build id")
        delivered = out_dir / SHIPPED_RENDERER.name
        shutil.copyfile(built, delivered)
    finally:
        if keep_scratch:
            print(f"scratch kept at {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    return delivered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=SHIPPED_RENDERER.parent,
                        help="output folder (default: the shipped location)")
    parser.add_argument("--cache", type=Path, default=None, help="build cache folder")
    parser.add_argument("--source", type=Path, default=None,
                        help="an already extracted pinned upstream tree instead of the archive")
    parser.add_argument("--keep-scratch", action="store_true", help="keep the staged tree and log")
    args = parser.parse_args()
    try:
        print(f"wrote {build_renderer(args.out, cache=args.cache, source=args.source, keep_scratch=args.keep_scratch)}")
    except (RuntimeError, ValueError, FileNotFoundError) as failure:
        print(failure)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
