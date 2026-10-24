"""Stage the capture overlay on the pinned LINK source; never install/build.

The output is confined to this checkout's .iteration directory or a scratch
folder under the system temp directory (tools/build_renderer.py stages
there, builds, and removes it). Source remains unchanged. The shipped
renderer is the --boundary --context-lifetime staging, compiled with the
overlay defines and modules named in tools/build_renderer.py; the opt-in
source witnesses read a staging named by SM64_LINK_WITNESS_SOURCE.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import shutil
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIN = "d0d101054421dd74c6c398919112c7adfd8265e3"
TREE_HASH = "d566bb90ac34a8017d9a2ea745ea667060f6d75d4d801a0e0cda215da6c16e20"
GL = "src/Graphics/OpenGLContext/"
HASHES = {
    GL + "GLFunctions.h": "08d4501d54162aae9f3c0c01024ac82719c0931837c6855014af9a98af2863e7",
    GL + "GLFunctions.cpp": "149e36b5d207a62d91e43934553b4999ea923a119b50728839ae26b1569879b6",
    GL + "windows/windows_DisplayWindow.cpp": "d0980900a00753871aceb7064ba1faf63fab693542bc3ca615cf75bdb297ee10",
}
GUARD = "#if defined(OS_WINDOWS) && defined(SM64_REPLAY_GL_WITNESS)\n"


def replace_once(data: bytes, before: str, after: str) -> bytes:
    # Upstream zip source is LF. Refuse drift instead of normalizing a whole file.
    needle = before.encode()
    if data.count(needle) != 1:
        raise ValueError(f"source anchor is not unique: {before[:70]}")
    return data.replace(needle, after.encode(), 1)


def context_profile_overlay(data: bytes) -> bytes:
    """Preserve LINK's context/version; expose the opt-in practice-ROM A/B variant."""
    data = replace_once(data, "#include <DisplayWindow.h>", "#include <DisplayWindow.h>\n" + GUARD +
        '#include "../renderer_context_profile.h"\n#endif')
    return replace_once(data,
        "\t\t\t\tWGL_CONTEXT_PROFILE_MASK_ARB, WGL_CONTEXT_CORE_PROFILE_BIT_ARB,",
        "\t\t\t\tWGL_CONTEXT_PROFILE_MASK_ARB,\n" + GUARD +
        "\t\t\t\trenderer_context_profile::requested(HEADER, WGL_CONTEXT_CORE_PROFILE_BIT_ARB, "
        "WGL_CONTEXT_COMPATIBILITY_PROFILE_BIT_ARB),\n#else\n"
        "\t\t\t\tWGL_CONTEXT_CORE_PROFILE_BIT_ARB,\n#endif")


def buffered_drawer_overlay(data: bytes) -> bytes:
    """Use each buffer's actual mapping; failed immutable maps become mutable.

    Buffer.data is owned by the renderer's context/thread and exists only for a
    successful persistent map. Capability alone cannot authorize a CPU copy.
    Streaming SubData is a void GL call: its admission is not upload proof.
    """
    data = replace_once(data,
        "\t\t_buffer.data = (GLubyte*)glMapBufferRange(_buffer.type, 0, _bufSize, m_bufMapBits);\n"
        "\t} else {\n\t\tglBufferData(_buffer.type, _bufSize, nullptr, GL_DYNAMIC_DRAW);\n\t}",
        "\t\t_buffer.data = (GLubyte*)glMapBufferRange(_buffer.type, 0, _bufSize, m_bufMapBits);\n"
        "\t\tif (_buffer.data != nullptr)\n\t\t\treturn;\n"
        "\t\t// Immutable storage cannot accept SubData. Recreate before VAO attributes.\n"
        "\t\tconst GLuint retired = _buffer.handle;\n"
        "\t\tglDeleteBuffers(1, &_buffer.handle);\n"
        "\t\tglGenBuffers(1, &_buffer.handle);\n"
        "\t\t// Deletion unbinds the old object, even when its name is recycled.\n"
        "\t\tm_bindBuffer->reset();\n"
        "\t\tm_bindBuffer->bind(Parameter(_buffer.type), ObjectHandle(_buffer.handle));\n"
        "\t\tglBufferData(_buffer.type, _bufSize, nullptr, GL_DYNAMIC_DRAW);\n"
        "\t\tLOG(LOG_ERROR, \"buffer_fallback target=0x%x retired=%u replacement=%u bytes=%u "
        "map=null mutable_request=issued error_observed=unavailable error_origin=unassigned\\n\",\n"
        "\t\t\t_buffer.type, retired, _buffer.handle, _bufSize);\n"
        "\t\treturn;\n\t}\n\tglBufferData(_buffer.type, _bufSize, nullptr, GL_DYNAMIC_DRAW);")
    data = replace_once(data,
        "\tif (m_glInfo.bufferStorage) {\n\t\tmemcpy(&_buffer.data[_buffer.offset], _data, _dataSize);",
        "\tif (_buffer.data != nullptr) {\n\t\tmemcpy(&_buffer.data[_buffer.offset], _data, _dataSize);")
    data = replace_once(data,
        "\t\tvoid* buffer_pointer = glMapBufferRange(_buffer.type, _buffer.offset, _dataSize, GL_MAP_WRITE_BIT | GL_MAP_UNSYNCHRONIZED_BIT);\n"
        "\t\tmemcpy(buffer_pointer, _data, _dataSize);\n\t\tglUnmapBuffer(_buffer.type);",
        "\t\t// Mutable upload needs no CPU mapping and cannot memcpy a failed map.\n"
        "\t\tglBufferSubData(_buffer.type, _buffer.offset, _dataSize, _data);")
    return replace_once(data,
        "\tif (m_glInfo.bufferStorage) {\n\t\t_updateBuffer(buffer, _params.verticesCount, dataSize, _params.vertices);",
        "\tif (buffer.data != nullptr) {\n\t\t_updateBuffer(buffer, _params.verticesCount, dataSize, _params.vertices);")


def pixel_format_overlay(data: bytes) -> bytes:
    """Prefer supported RGB8 storage for practice ghosts; retain baseline ROMs."""
    data = replace_once(data, '#include "../renderer_context_profile.h"',
                        '#include "../renderer_context_profile.h"\n#include "../renderer_pixel_format.h"')
    return replace_once(data, "\tif ((pixelFormat = ChoosePixelFormat(hDC, &pfd )) == 0) {",
        GUARD + "\tpixelFormat = renderer_pixel_format::choose(HEADER, hDC, &pfd);\n#else\n"
        "\tpixelFormat = ChoosePixelFormat(hDC, &pfd);\n#endif\n\tif (pixelFormat == 0) {")


def boundary_overlay(source: Path) -> dict[str, bytes]:
    edited = {}
    api_header = "src/PluginAPI.h"
    data = (source / api_header).read_bytes()
    source_guard = "#if defined(OS_WINDOWS) && defined(SM64_REPLAY_SOURCE)\n"
    data = replace_once(data, "class APICommand;", source_guard +
        '#include "Graphics/OpenGLContext/link_source_api.h"\n#endif\n\nclass APICommand;')
    data = replace_once(data, "\tvoid UpdateScreen();", "\tvoid UpdateScreen();\n" + source_guard +
        "\tvoid UpdateScreenCaptured(rb_ticket request);\n#endif")
    data = replace_once(data, "\tvoid CloseDLL(void) {}", source_guard +
        "\tvoid CloseDLL(void) { rb_close(); }\n#else\n\tvoid CloseDLL(void) {}\n#endif")
    edited[api_header] = data
    common = "src/common/CommonAPIImpl_common.cpp"
    data = (source / common).read_bytes()
    old = "class ProcessUpdateScreenCommand : public APICommand {\npublic:\n\tbool run() {\n\t\tVI_UpdateScreen();\n\t\treturn true;\n\t}\n};"
    new = "class ProcessUpdateScreenCommand : public APICommand {\npublic:\n" + source_guard + \
        "\tProcessUpdateScreenCommand(rb_ticket request = {0,0}) : m_capture(request) {}\n#endif\n" + \
        "\tbool run() {\n" + source_guard + "\t\tconst auto capture = rb_source_begin(m_capture);\n#endif\n" + \
        "\t\tVI_UpdateScreen();\n" + source_guard + "\t\trb_source_end(capture);\n#endif\n\t\treturn true;\n\t}\n" + \
        source_guard + "private:\n\trb_ticket m_capture;\n#endif\n};"
    data = replace_once(data, old, new)
    anchor = "void PluginAPI::_initiateGFX(const GFX_INFO & _gfxInfo) const {"
    extra = source_guard + "void PluginAPI::UpdateScreenCaptured(rb_ticket request)\n{\n" + \
        '\tLOG(LOG_APIFUNC, "UpdateScreen\\n");\n#ifdef RSPTHREAD\n' + \
        "\t_callAPICommand(ProcessUpdateScreenCommand(request));\n#else\n" + \
        "\tconst auto capture = rb_source_begin(request);\n\tVI_UpdateScreen();\n\trb_source_end(capture);\n#endif\n}\n#endif\n\n"
    data = replace_once(data, anchor, extra + anchor)
    data = replace_once(data, "\tm_bRomOpen = true;", "\tm_bRomOpen = true;\n" + source_guard + "\trb_rom_open();\n#endif")
    data = replace_once(data, "\tm_bRomOpen = false;", "\tm_bRomOpen = false;\n" + source_guard + "\trb_rom_closed();\n#endif")
    update = b"VI_UpdateScreen();"
    if data.count(update) != 3:
        raise ValueError("expected three actual renderer VI update call sites")
    data = data.replace(update, ("\n" + source_guard + "rs_before_update();\n#endif\nVI_UpdateScreen();").encode())
    edited[common] = data
    return edited


def labeled_info(source: Path, label: str) -> bytes:
    """Only change the selector label; preserve pluginName/settings identity."""
    if not label or len(label) > 80 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 .[]_-" for c in label):
        raise ValueError("label must be 1-80 ASCII letters, numbers, spaces or .[]_-")
    path = source / "src/windows/ZilmarAPIImpl_windows.cpp"
    return replace_once(path.read_bytes(),
        '\tsprintf(PluginInfo->Name, "LINK\'s %s v4.2", pluginName, PLUGIN_REVISION);',
        f'\tsprintf(PluginInfo->Name, "%s", "{label}");')


def verify_source(source: Path) -> None:
    for relative, expected in HASHES.items():
        if hashlib.sha256((source / relative).read_bytes()).hexdigest() != expected:
            raise ValueError(f"pinned source mismatch: {relative}")
    # No links/junctions copied from a source tree into our output.
    for path in source.rglob("*"):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f"linked source entry refused: {path}")
    digest = hashlib.sha256()
    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        digest.update(path.relative_to(source).as_posix().encode() + b"\0" + path.read_bytes() + b"\0")
    if digest.hexdigest() != TREE_HASH:
        raise ValueError("source tree differs from the complete pinned archive")


def _validate_modes(boundary: bool, context_lifetime: bool, ghost: bool) -> None:
    if context_lifetime and not boundary:
        raise ValueError('context lifetime requires the explicit source boundary')
    if ghost and not context_lifetime:
        raise ValueError('ghost capture requires the explicit source/context lifetime')


def _stage_ghost(output: Path, enabled: bool) -> None:
    if not enabled:
        return
    spec = importlib.util.spec_from_file_location("stage_ghost_renderer", ROOT / "tools/stage_ghost_renderer.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative, data in module.edits(output, replace_once).items():
        (output / relative).write_bytes(data)
    spec = importlib.util.spec_from_file_location("stage_ghost_presentation", ROOT / "tools/stage_ghost_presentation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative, data in module.edits(output, replace_once).items():
        (output / relative).write_bytes(data)
    # Every ghost source ships in the renderer. A hand list silently missed
    # ghost_root/ghost_proximity/ghost_shadow_gl and broke the bundle build.
    for path in sorted((ROOT / "plugin/gfxwrap").glob("ghost_*")):
        if path.suffix in (".h", ".cpp"):
            shutil.copyfile(path, output / GL / path.name)
    shutil.copyfile(ROOT / "renderer/model/model.h", output / GL / "model.h")


def overlay(source: Path, output: Path, *, boundary: bool = False, label: str | None = None,
            context_lifetime: bool = False, ghost: bool = False) -> None:
    source, output = source.resolve(), output.resolve()
    scratch = Path(tempfile.gettempdir()).resolve()
    allowed = [ROOT / ".iteration", scratch]
    if not any(output.is_relative_to(base) and output != base for base in allowed):
        raise ValueError("staged output must be a new directory under .iteration or the temp folder")
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("source/output must be separate; output must not exist")
    _validate_modes(boundary, context_lifetime, ghost)
    verify_source(source)
    edited = {}
    key = GL + "GLFunctions.h"
    data = (source / key).read_bytes()
    # Include before checked() templates so their existing glGetError consumers
    # also use the source gateway. Release keeps its original check frequency.
    edited[key] = replace_once(data, "template<typename F> void checked(F fn, const char* _functionName)",
        GUARD + '#include "link_dispatch.h"\n#endif\n\n' +
        "template<typename F> void checked(F fn, const char* _functionName)")
    key = GL + "GLFunctions.cpp"
    edited[key] = replace_once((source / key).read_bytes(),
        "\tGL_GET_PROC_ADR(PFNGLEGLIMAGETARGETTEXTURE2DOESPROC, glEGLImageTargetTexture2DOES);",
        "\tGL_GET_PROC_ADR(PFNGLEGLIMAGETARGETTEXTURE2DOESPROC, glEGLImageTargetTexture2DOES);\n"
        + GUARD + "\treplay_gl::InstallDispatch();\n#endif")
    key = GL + "windows/windows_DisplayWindow.cpp"
    data = pixel_format_overlay(context_profile_overlay((source / key).read_bytes()))
    for anchor in ["bool DisplayWindowWindows::_start()\n{", "void DisplayWindowWindows::_stop()\n{"]:
        data = replace_once(data, anchor, anchor + "\n" + GUARD + "\treplay_gl::ContextLost();\n#endif")
    data = replace_once(data, "\treturn _resizeWindow();", GUARD +
        "\tconst bool resized = _resizeWindow();\n"
        "\tPIXELFORMATDESCRIPTOR actual = {};\n"
        "\tif (resized && DescribePixelFormat(hDC, GetPixelFormat(hDC), sizeof(actual), &actual))\n"
        "\t\treplay_gl::ContextCreated(hRC, hDC, (actual.dwFlags & PFD_DOUBLEBUFFER) != 0);\n"
        "\treplay_gl::DrawableCommitted(hWnd, resized);\n"
        "\treturn resized;\n#else\n\treturn _resizeWindow();\n#endif")
    anchor = "bool DisplayWindowWindows::_resizeWindow()\n{"
    data = replace_once(data, anchor, anchor + "\n" + GUARD + "\treplay_gl::DrawableChanged();\n#endif")
    for original in [
        "\t\treturn (SetWindowPos(hWnd, NULL, 0, 0, m_screenWidth, m_screenHeight, SWP_NOACTIVATE | SWP_NOZORDER | SWP_SHOWWINDOW) == TRUE);",
        "\t\treturn (SetWindowPos( hWnd, NULL, 0, 0, windowRect.right - windowRect.left + 1,\n"
        "\t\t\twindowRect.bottom - windowRect.top + 1 + toolRect.bottom - toolRect.top + 1, SWP_NOACTIVATE | SWP_NOZORDER | SWP_NOMOVE ) == TRUE);",
    ]:
        # The original resize executes once, with identical arguments/result.
        observed = original.replace("return (", "return replay_gl::DrawableCommitted(hWnd, (")[:-1] + ");"
        data = replace_once(data, original, GUARD + observed + "\n#else\n" + original + "\n#endif")
    edited[key] = data
    key = GL + "opengl_BufferedDrawer.cpp"
    edited[key] = buffered_drawer_overlay((source / key).read_bytes())
    if boundary:
        edited.update(boundary_overlay(source))
    if label:
        edited["src/windows/ZilmarAPIImpl_windows.cpp"] = labeled_info(source, label)
    shutil.copytree(source, output)
    for relative, data in edited.items():
        (output / relative).write_bytes(data)
    for name in ["link_dispatch.h", "link_dispatch.cpp", "renderer_gl_state.h", "gl_snapshot.h", "source_format.h",
                 "practice_rom.h", "renderer_context_profile.h", "renderer_pixel_format.h"]:
        shutil.copyfile(ROOT / "plugin/gfxwrap" / name, output / GL / name)
    if context_lifetime:
        for name in ["context_lifetime.h", "context_lifetime.cpp"]:
            shutil.copyfile(ROOT / "plugin/gfxwrap" / name, output / GL / name)
    if boundary:
        for name in ["renderer_boundary.h", "renderer_boundary.cpp", "link_source_api.h", "link_source_api.cpp", "source_descriptor3.h", "source_refusal.h"]:
            shutil.copyfile(ROOT / "plugin/gfxwrap" / name, output / GL / name)
    _stage_ghost(output, ghost)
    print(f"Staged the capture overlay: {output}; pinned LINK {PIN}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--context-lifetime", action="store_true", help="also stage ContextV1 (exported for the wrapper; the overlay shares no context since 2026-09-28); compile with SM64_REPLAY_CONTEXT_LIFETIME")
    parser.add_argument("--boundary", action="store_true", help="also stage the explicit command boundary API")
    parser.add_argument("--ghost", action="store_true", help="stage GhostV2 per-DList pose/camera/world capture")
    parser.add_argument("--label", help="distinct test-only selector name; settings identity stays unchanged")
    args = parser.parse_args()
    overlay(args.source, args.out, boundary=args.boundary, label=args.label,
            context_lifetime=args.context_lifetime, ghost=args.ghost)


if __name__ == "__main__":
    main()
