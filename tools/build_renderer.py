"""Build THE GRAPHICS PLUGIN Project64 loads: LINK's GLideN64 v4.2, the capture
overlay and the capture layer, linked into ONE 32-bit DLL, sm64_trainer_gfx.dll.

    uv run python tools/build_renderer.py            # into src/sm64_events/data/plugin/
    uv run python tools/build_renderer.py --out DIR  # a candidate folder: the DLL and nothing else

Until 2026-11-07 this built the renderer alone (GLideN64_SM64Trainer.dll),
which a separate capture wrapper loaded by a path its ini named. One file
now: the capture layer (plugin/gfxwrap/gfxwrap.c and tools/build_plugin.py's
RUNTIME_SOURCES) is compiled into the same MSBuild project, exports the
Zilmar API Project64 calls, and forwards to the renderer's own functions,
which the staging renames GLideN64_<name> (`stage_renderer_entries`;
plugin/gfxwrap/renderer_entries.h is the seam). Nothing in the plugin loads
another plugin or reads an ini of its own: why, in gfxwrap.c.

Inputs, all pinned in renderer/build-inputs.json:
  * the upstream archive at commit d0d1010 (downloaded into the build cache
    and verified by hash; --source names an already-verified tree instead);
  * the overlay: plugin/gfxwrap files staged by tools/stage_link_witness.py;
  * the capture layer: plugin/gfxwrap files staged here, each compiled as
    tools/build_plugin.py compiled the old wrapper (`CAPTURE_SETTINGS`);
  * three static libraries the upstream project links (GLideNUI needs a
    static Qt 5.12.12 and tools/build_renderer_ui.py builds it;
    renderer/README.md records how all three were produced). They live in
    the build cache by hash, never in git.

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
SHIPPED_PLUGIN = REPO / "src" / "sm64_events" / "data" / "plugin" / "sm64_trainer_gfx.dll"
# The renderer's own name in GetDllInfo (the plugin answers Project64 with
# "SM64 Trainer v1.0"; a scene-lab host in renderer-only mode sees this one).
LABEL = "LINK v4.2 [SM64 Trainer]"
OVERLAY_SOURCES = (
    "link_dispatch.h", "link_dispatch.cpp", "renderer_gl_state.h", "gl_snapshot.h",
    "source_format.h", "source_refusal.h", "practice_rom.h", "renderer_context_profile.h", "renderer_pixel_format.h", "context_lifetime.h", "context_lifetime.cpp",
    "renderer_boundary.h", "renderer_boundary.cpp", "link_source_api.h", "link_source_api.cpp", "source_descriptor3.h",
    "ghost_ram.h", "ghost_capture.h", "ghost_capture.cpp", "ghost_root.h", "ghost_root.cpp", "ghost_pass.h", "ghost_pass.cpp",
    "ghost_stream.h", "ghost_stream.cpp", "ghost_link.h", "ghost_link.cpp",
    "ghost_particle_capture.h", "ghost_particle_capture.cpp", "ghost_particle_gl.h",
    "ghost_particle_pose.h", "ghost_particle_pose.cpp",
    "ghost_clock.h", "ghost_clock.cpp", "ghost_origin.h", "ghost_origin.cpp",
    "ghost_pose.h", "ghost_pose.cpp", "ghost_scene.h", "ghost_compositor.h", "ghost_compositor.cpp", "ghost_compositor_gl.h",
    "ghost_clip.h", "ghost_assets.h", "ghost_assets.cpp", "ghost_rom.h", "ghost_rom.cpp", "ghost_ipc.h", "ghost_ipc.cpp",
    "ghost_draw.h", "ghost_draw.cpp", "ghost_playback.h", "ghost_playback.cpp",
    "ghost_dispatch.h", "ghost_dispatch.cpp", "ghost_presentation.h", "ghost_presentation.cpp",
    "ghost_audio.h", "ghost_audio.cpp",
    "renderer_settings_folder.h", "renderer_settings_folder.cpp",
)
OVERLAY_MODULES = ("link_dispatch.cpp", "renderer_boundary.cpp", "link_source_api.cpp", "context_lifetime.cpp",
                   "ghost_capture.cpp", "ghost_root.cpp", "ghost_clock.cpp", "ghost_origin.cpp",
                   "ghost_pass.cpp", "ghost_stream.cpp", "ghost_link.cpp", "ghost_particle_capture.cpp", "ghost_particle_pose.cpp",
                   "ghost_pose.cpp", "ghost_compositor.cpp", "ghost_assets.cpp", "ghost_rom.cpp", "ghost_ipc.cpp",
                   "ghost_draw.cpp", "ghost_playback.cpp", "ghost_dispatch.cpp", "ghost_presentation.cpp",
                   "ghost_audio.cpp", "ghost_annotation_assets.cpp", "ghost_font_cache.cpp",
                   "ghost_live_codec.cpp", "ghost_live_input.cpp", "ghost_live_render.cpp",
                   "ghost_live_session.cpp", "ghost_live_ipc.cpp", "ghost_viewer.cpp",
                   "renderer_settings_folder.cpp")
DEFINES = ("SM64_REPLAY_GL_WITNESS;SM64_REPLAY_SOURCE;SM64_REPLAY_CONTEXT_LIFETIME;SM64_GHOST_RENDERER;"
           "SM64_RENDERER_SETTINGS_FOLDER;")
RECIPE_VERSION = "4"   # 4: the capture layer is linked in (2026-11-07)
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


witness = _load("stage_link_witness", REPO / "tools" / "stage_link_witness.py")
build_plugin = _load("build_plugin", REPO / "tools" / "build_plugin.py")

# The capture layer's translation units, compiled into the plugin beside the
# renderer: its exported surface, RUNTIME_SOURCES, and the shipped renderer
# table (plugin/gfxwrap/renderer_entries.c).
RENDERER_TABLE = "renderer_entries.c"
CAPTURE_MODULES = (build_plugin.CAPTURE_LAYER, *(f"{name}.cpp" for name in build_plugin.RUNTIME_SOURCES),
                   RENDERER_TABLE)
# The renderer's Zilmar functions, renamed GLideN64_<name> so that only the
# capture layer exports these names. Each file must hold exactly this set.
RENDERER_ENTRY_FILES = {
    "src/ZilmarPluginAPI.cpp": ("RomOpen", "CaptureScreen", "CloseDLL", "DllAbout", "DllConfig", "DllTest",
                                "DrawScreen", "GetDllInfo", "ReadScreen"),
    "src/CommonPluginAPI.cpp": ("InitiateGFX", "MoveScreen", "ProcessDList", "ProcessRDPList", "RomClosed", "ShowCFB",
                                "UpdateScreen", "ViStatusChanged", "ViWidthChanged", "ChangeWindow", "FBWrite",
                                "FBRead", "FBGetFrameBufferInfo", "FBWList"),
}
RENDERER_DLLMAIN = ("src/windows/GLideN64_windows.cpp",
                    "BOOL WINAPI DllMain(HINSTANCE hinstDLL, DWORD dwReason, LPVOID /*lpvReserved*/)",
                    'extern "C" BOOL WINAPI GLideN64_DllMain(HINSTANCE hinstDLL, DWORD dwReason, LPVOID /*lpvReserved*/)')
IDENTITY_HEADER = "renderer_build_identity.h"

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


def overlay_sources() -> tuple[str, ...]:
    """OVERLAY_SOURCES plus every ghost source the staging tool copies by glob.
    A header missing from the hand list (the shadow shader, the proximity and
    occlusion rules) used to change the DLL without changing its id."""
    extra = sorted(path.name for path in (REPO / "plugin" / "gfxwrap").glob("ghost_*")
                   if path.suffix in (".h", ".cpp") and path.name not in OVERLAY_SOURCES)
    return (*OVERLAY_SOURCES, *extra)


def _date_inputs() -> list[Path]:
    """The files whose newest commit dates the plugin (tools/native_identity.py)."""
    tools = REPO / "tools"
    return [Path(__file__), INPUTS, tools / "stage_link_witness.py", tools / "stage_ghost_renderer.py",
            tools / "stage_ghost_presentation.py", tools / "build_model.py", REPO / "renderer" / "model",
            tools / "build_plugin.py", *(REPO / "plugin" / "gfxwrap" / name for name in overlay_sources()),
            *build_plugin._source_files()]


def _build_term() -> tuple[bytes, int]:
    return build_plugin._tool("native_identity").build_term(_date_inputs(), build_plugin.find_vcvars32())


def plugin_build_id() -> str:
    """THE plugin's one build id. Names everything the DLL is made of: the
    pinned upstream tree and the prebuilt libraries (build-inputs.json), the
    overlay sources, the staging tools, the label, this recipe, the capture
    layer (every plugin/gfxwrap source and tools/build_plugin.py, through its
    digest) and the identity term (the toolchain and the source date the
    build stamps; tools/native_identity.py). The role suffix is the capture
    layer's, which the control page, the plugin log and Python's readers
    already know (core/capturelayer.py)."""
    digest = hashlib.sha256()
    digest.update(f"{witness.TREE_HASH}\0{LABEL}\0{RECIPE_VERSION}\0".encode())
    digest.update(Path(__file__).read_bytes() + b"\0")
    digest.update(INPUTS.read_bytes() + b"\0")
    digest.update((REPO / "tools" / "stage_link_witness.py").read_bytes() + b"\0")
    digest.update((REPO / "tools" / "stage_ghost_renderer.py").read_bytes() + b"\0")
    digest.update((REPO / "tools" / "stage_ghost_presentation.py").read_bytes() + b"\0")
    model = _load("renderer_model_build", REPO / "tools/build_model.py")
    digest.update(model.component_hash().encode() + b"\0")
    for name in overlay_sources():
        digest.update(name.encode() + b"\0" + (REPO / "plugin" / "gfxwrap" / name).read_bytes() + b"\0")
    digest.update(build_plugin.sources_digest().encode() + b"\0")
    digest.update(_build_term()[0])
    return digest.hexdigest() + "-gpu-runtime"


def embedded_identity(path: Path) -> str | None:
    ids = set(re.findall(rb"[0-9a-f]{64}-[a-z][a-z-]*", path.read_bytes()))
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


def prebuilt_path(cache: Path, name: str, digest: str) -> Path:
    """Where a prebuilt library lives in the shared build cache: by its hash, so
    checkouts pinned to different builds of it never overwrite each other's."""
    return cache / "prebuilt" / digest / name


def prebuilt_libraries(cache: Path) -> dict[str, Path]:
    """The pinned prebuilt libraries: `prebuilt/<sha256>/<name>`, or `<name>` at
    the cache root where the first builds were kept, whichever holds the pinned
    bytes."""
    found = {}
    for name, expected in inputs()["prebuilt_libraries"].items():
        for path in (prebuilt_path(cache, name, expected), cache / name):
            if path.is_file() and _sha256(path) == expected:
                found[name] = path
                break
        else:
            raise FileNotFoundError(
                f"{name} {expected} is not in the build cache {cache}; renderer/README.md says how it is "
                "produced (GLideNUI.lib: tools/build_renderer_ui.py)")
    return found


# -- staging ----------------------------------------------------------------

SETTINGS_GUARD = "#if defined(OS_WINDOWS) && defined(SM64_RENDERER_SETTINGS_FOLDER)\n"


def stage_settings_folder(staged: Path) -> None:
    """The renderer's settings folder (renderer_settings_folder.h): upstream's one
    folder lookup asks it first, so a scene-lab host loads the renderer where the
    Trainer keeps it. Inside Project64 nothing names one: upstream's own folder."""
    gl = staged / witness.GL
    for name in ("renderer_settings_folder.h", "renderer_settings_folder.cpp"):
        shutil.copyfile(REPO / "plugin" / "gfxwrap" / name, gl / name)
    path = staged / "src" / "windows" / "CommonAPIImpl_windows.cpp"
    data = witness.replace_once(path.read_bytes(), '#include "../RSP.h"\n',
        '#include "../RSP.h"\n' + SETTINGS_GUARD
        + '#include "../Graphics/OpenGLContext/renderer_settings_folder.h"\n#endif\n')
    data = witness.replace_once(data, "\tif (_strPath == NULL)\n\t\treturn;\n\t::GetModuleFileName(",
        "\tif (_strPath == NULL)\n\t\treturn;\n" + SETTINGS_GUARD
        + "\tif (renderer_settings_folder::named(_strPath, PLUGIN_PATH_SIZE))\n\t\treturn;\n#endif\n"
        + "\t::GetModuleFileName(")
    path.write_bytes(data)


_RENDERER_ENTRY = re.compile(rb"\bEXPORT (void|BOOL) CALL (\w+)")


def stage_renderer_entries(staged: Path) -> None:
    """Upstream's Zilmar functions become the renderer's entries,
    GLideN64_<name>, unexported (their bodies are untouched), and its DllMain
    becomes GLideN64_DllMain, which the plugin's DllMain calls first. Only
    the capture layer then exports the Zilmar names, so Project64's every call
    passes through it (plugin/gfxwrap/renderer_entries.h)."""
    for relative, names in RENDERER_ENTRY_FILES.items():
        path = staged / relative
        data = path.read_bytes()
        found = sorted(match[2].decode() for match in _RENDERER_ENTRY.finditer(data))
        if found != sorted(names):
            raise ValueError(f"{relative}: the exported Zilmar functions changed ({found})")
        path.write_bytes(_RENDERER_ENTRY.sub(rb"\1 CALL GLideN64_\2", data))
    relative, before, after = RENDERER_DLLMAIN
    path = staged / relative
    path.write_bytes(witness.replace_once(path.read_bytes(), before, after))


# How each capture-layer unit compiles: as tools/build_plugin.py compiled the
# capture wrapper before 2026-11-07 (/O2 /W4 /MT /EHsc, its own defines, no
# include path of the renderer's), not with the renderer's settings. Without
# whole-program optimization, so the renderer's own link-time code generation
# sees the same units it saw when it was a DLL of its own.
CAPTURE_DEFINES = "WIN32;_WINDOWS;_CRT_SECURE_NO_WARNINGS"


def _capture_item(name: str) -> bytes:
    defines = CAPTURE_DEFINES + (";GFXWRAP_GPU_RUNTIME" if name == build_plugin.CAPTURE_LAYER else "")
    lines = [f'    <ClCompile Include="..\\..\\src\\Graphics\\OpenGLContext\\{name}">',
             f"      <PreprocessorDefinitions>{defines}</PreprocessorDefinitions>",
             "      <AdditionalIncludeDirectories>.</AdditionalIncludeDirectories>",
             "      <Optimization>MaxSpeed</Optimization>",
             "      <WarningLevel>Level4</WarningLevel>",
             "      <ExceptionHandling>Sync</ExceptionHandling>",
             "      <WholeProgramOptimization>false</WholeProgramOptimization>"]
    if name.endswith(".c"):
        lines.append("      <LanguageStandard_C>stdc17</LanguageStandard_C>")
    if name != RENDERER_TABLE:
        lines.append(f"      <ForcedIncludeFiles>{IDENTITY_HEADER}</ForcedIncludeFiles>")
    return ("\r\n".join([*lines, "    </ClCompile>"]) + "\r\n").encode()


def stage_capture_layer(staged: Path) -> None:
    """The capture layer beside the overlay: its units, every plugin/gfxwrap
    header (the ones the overlay staged already are the same files), the
    .def that names the plugin's exports and its file, and the libraries it
    links. MSBuild then writes sm64_trainer_gfx.dll (`msbuild`, TargetName)."""
    gl = staged / witness.GL
    for path in build_plugin._source_files():
        if path.parent == build_plugin.SOURCE and (path.suffix == ".h" or path.name in CAPTURE_MODULES
                                                   or path.name == build_plugin.EXPORTS_DEF):
            shutil.copyfile(path, gl / path.name)
    stage_renderer_entries(staged)
    project = staged / "projects" / "msvc" / "GLideN64.vcxproj"
    data = project.read_bytes()
    anchor = b'    <ClCompile Include="..\\..\\src\\3DMath.cpp" />'
    if data.count(anchor) != 1:
        raise ValueError("GLideN64.vcxproj anchor is not unique")
    data = data.replace(anchor, b"".join(_capture_item(name) for name in CAPTURE_MODULES) + anchor)
    anchor = b'  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />'
    if data.count(anchor) != 1:
        raise ValueError("GLideN64.vcxproj targets anchor is not unique")
    libraries = ";".join(build_plugin.RUNTIME_LIBS)
    link = ("  <ItemDefinitionGroup>\r\n    <Link>\r\n"
            f"      <ModuleDefinitionFile>..\\..\\src\\Graphics\\OpenGLContext\\{build_plugin.EXPORTS_DEF}"
            "</ModuleDefinitionFile>\r\n"
            f"      <AdditionalDependencies>{libraries};%(AdditionalDependencies)</AdditionalDependencies>\r\n"
            "    </Link>\r\n  </ItemDefinitionGroup>\r\n").encode()
    data = data.replace(anchor, link + anchor)
    # The file's own name, set in this project only: a global /p:TargetName
    # would rename the osal and GLideNHQ libraries it references as well.
    anchor = b"    <TargetName>mupen64plus-video-GLideN64</TargetName>\n  </PropertyGroup>\n"
    if data.count(anchor) != 1:
        raise ValueError("GLideN64.vcxproj TargetName anchor is not unique")
    project.write_bytes(data.replace(anchor, anchor + b"  <PropertyGroup Condition=\"'$(Configuration)'=='Release'\">\r\n"
                                     b"    <TargetName>" + SHIPPED_PLUGIN.stem.encode() + b"</TargetName>\r\n"
                                     b"  </PropertyGroup>\r\n"))


def stage(pristine: Path, staged: Path, libraries: dict[str, Path], build_id: str) -> None:
    witness.overlay(pristine, staged, boundary=True, label=LABEL, context_lifetime=True, ghost=True)
    stage_settings_folder(staged)
    gl = staged / witness.GL
    # ONE id: the overlay's identity export and the capture layer's (the
    # control page, the plugin log, the About box) name the same build.
    (gl / IDENTITY_HEADER).write_text(
        f'#define SM64_TRAINER_RENDERER_BUILD_ID "{build_id}"\n#define GFXWRAP_BUILD_ID "{build_id}"\n',
        encoding="ascii")
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
    stage_capture_layer(staged)
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
    stage_identity(staged)

VERSION_RC = "sm64_trainer_version.rc"


def stage_identity(staged: Path) -> None:
    """The plugin's version resource and the repeatable-build switches
    (tools/native_identity.py) for every compile and the link: the staged
    tree reads as \\sm64-renderer inside the objects, so two builds of these
    sources from different scratch folders are the same bytes."""
    native_identity = build_plugin._tool("native_identity")
    version = native_identity.calendar_version(_build_term()[1])
    (staged / "src" / VERSION_RC).write_text(native_identity.rc_text(SHIPPED_PLUGIN.name, version),
                                             encoding="ascii", newline="\r\n")
    project = staged / "projects" / "msvc" / "GLideN64.vcxproj"
    data = project.read_bytes()
    anchor = b'  <Import Project="$(VCTargetsPath)\\Microsoft.Cpp.targets" />'
    if data.count(anchor) != 1:
        raise ValueError("GLideN64.vcxproj targets anchor is not unique")
    compile_options = " ".join([*native_identity.REPRO_COMPILE,
                                *(f'"{flag}"' for flag in native_identity.path_maps((staged, r"\sm64-renderer")))])
    link_options = " ".join(native_identity.REPRO_LINK)
    identity = (
        f'  <ItemGroup>\r\n    <ResourceCompile Include="..\\..\\src\\{VERSION_RC}" />\r\n  </ItemGroup>\r\n'
        "  <ItemDefinitionGroup>\r\n"
        f"    <ClCompile>\r\n      <AdditionalOptions>{compile_options} %(AdditionalOptions)</AdditionalOptions>\r\n"
        "    </ClCompile>\r\n"
        f"    <Link>\r\n      <AdditionalOptions>{link_options} %(AdditionalOptions)</AdditionalOptions>\r\n"
        "    </Link>\r\n  </ItemDefinitionGroup>\r\n").encode()
    project.write_bytes(data.replace(anchor, identity + anchor))


def stage_model_library(staged: Path, library: Path) -> None:
    """Link the C component without leaking decomp types into renderer units."""
    gl = staged / witness.GL
    for header in ("model.h", "audio.h"):
        shutil.copyfile(REPO / "renderer/model" / header, gl / header)
    msvc = staged / "projects/msvc"
    target = msvc / "bin/Win32/Release/lib/sm64_model.lib"
    shutil.copyfile(library, target)
    project = msvc / "GLideN64.vcxproj"
    data = project.read_bytes()
    anchor = b"<AdditionalDependencies>opengl32.lib;glu32.lib;winmm.lib;"
    if data.count(anchor) != 1:
        raise ValueError("model library link anchor is not unique")
    data = data.replace(anchor, anchor + b"bin\\Win32\\Release\\lib\\sm64_model.lib;")
    project.write_bytes(data)


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
        f'@echo off\r\nset VSCMD_SKIP_SENDTELEMETRY=1\r\ncall "{vcvars32}" >nul\r\nif errorlevel 1 exit /b %errorlevel%\r\n'
        "set N64PluginsDir=\r\nset Mupen64PluginsDir=\r\nset Mupen64PluginsDir_x64=\r\n"
        f'"{find_msbuild(vcvars32)}" GLideN64.vcxproj {" ".join(args)}\r\nexit /b %errorlevel%\r\n',
        encoding="utf-8")
    result = subprocess.run(["cmd.exe", "/d", "/c", str(script)], cwd=str(msvc), capture_output=True,
                            text=True, creationflags=_NO_WINDOW, check=False, stdin=subprocess.DEVNULL)
    log.write_text(result.stdout + result.stderr, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])
        raise RuntimeError(f"MSBuild failed ({result.returncode}); log at {log}\n{tail}")
    built = msvc / "bin" / "Win32" / "Release" / SHIPPED_PLUGIN.name
    if not built.is_file():
        raise RuntimeError(f"MSBuild reported success but {built} is missing")
    return built


def build_graphics_plugin(out_dir: Path, *, cache: Path | None = None, source: Path | None = None,
                          vcvars32: Path | None = None, keep_scratch: bool = False) -> Path:
    """sm64_trainer_gfx.dll, the one graphics plugin, into out_dir and nothing else."""
    vcvars32 = vcvars32 or build_plugin.find_vcvars32()
    if vcvars32 is None:
        raise RuntimeError("no x86 MSVC toolchain (vcvars32.bat) found")
    cache = cache or default_cache()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    build_id = plugin_build_id()
    libraries = prebuilt_libraries(cache)
    scratch = Path(tempfile.mkdtemp(prefix="sm64_rdr_"))
    try:
        pristine = pristine_source(cache, scratch, source)
        staged = scratch / "staged"
        stage(pristine, staged, libraries, build_id)
        model = _load("renderer_model_build", REPO / "tools/build_model.py")
        library = model.build_model(scratch / "model", cache=cache.parent / "model", vcvars32=vcvars32)
        stage_model_library(staged, library)
        built = msbuild(staged, vcvars32, scratch / "msbuild.log")
        if embedded_identity(built) != build_id:
            raise RuntimeError("the built plugin does not carry its one build id")
        build_plugin._tool("native_identity").stamp(built, _build_term()[1])
        delivered = out_dir / SHIPPED_PLUGIN.name
        shutil.copyfile(built, delivered)
    finally:
        if keep_scratch:
            print(f"scratch kept at {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)
    return delivered


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", type=Path, default=SHIPPED_PLUGIN.parent,
                        help="output folder (default: the shipped location)")
    parser.add_argument("--cache", type=Path, default=None, help="build cache folder")
    parser.add_argument("--source", type=Path, default=None,
                        help="an already extracted pinned upstream tree instead of the archive")
    parser.add_argument("--keep-scratch", action="store_true", help="keep the staged tree and log")
    args = parser.parse_args()
    try:
        print(f"wrote {build_graphics_plugin(args.out, cache=args.cache, source=args.source,
                                             keep_scratch=args.keep_scratch)}")
    except (RuntimeError, ValueError, FileNotFoundError) as failure:
        print(failure)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
