"""Stage the capture overlay on the pinned LINK source; never install/build.

The output is confined to this checkout's .iteration directory or a scratch
folder under the system temp directory (tools/build_renderer.py stages
there, builds, and removes it). Source remains unchanged. Compile with
SM64_REPLAY_GL_WITNESS and add link_dispatch.cpp; the shipped renderer is the
--boundary --context-lifetime staging built by tools/build_renderer.py.
"""
from __future__ import annotations

import argparse
import hashlib
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


def overlay(source: Path, output: Path, *, boundary: bool = False, label: str | None = None, context_lifetime: bool = False) -> None:
    source, output = source.resolve(), output.resolve()
    scratch = Path(tempfile.gettempdir()).resolve()
    allowed = [ROOT / ".iteration", scratch]
    if not any(output.is_relative_to(base) and output != base for base in allowed):
        raise ValueError("staged output must be a new directory under .iteration or the temp folder")
    if output.exists() or output.is_relative_to(source) or source.is_relative_to(output):
        raise ValueError("source/output must be separate; output must not exist")
    if context_lifetime and not boundary:
        raise ValueError('context lifetime requires the explicit source boundary')
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
    data = (source / key).read_bytes()
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
    if context_lifetime:
        lifetime_guard = "#if defined(OS_WINDOWS) && defined(SM64_REPLAY_CONTEXT_LIFETIME)\n"
        data = replace_once(data, "void DisplayWindowWindows::_stop()\n{",
            "void DisplayWindowWindows::_stop()\n{\n" + lifetime_guard +
            "\tconst auto retirement = source_context::Revoke(hRC);\n#endif")
        data = replace_once(data, "\tif (hDC != NULL) {", lifetime_guard +
            "\tif (retirement.value) source_context::Retire(retirement);\n#endif\n\tif (hDC != NULL) {")
    edited[key] = data
    if boundary:
        edited.update(boundary_overlay(source))
    if label:
        edited["src/windows/ZilmarAPIImpl_windows.cpp"] = labeled_info(source, label)
    shutil.copytree(source, output)
    for relative, data in edited.items():
        (output / relative).write_bytes(data)
    for name in ["link_dispatch.h", "link_dispatch.cpp", "renderer_gl_state.h", "gl_snapshot.h", "source_format.h",
                 "practice_rom.h"]:
        shutil.copyfile(ROOT / "plugin/gfxwrap" / name, output / GL / name)
    if context_lifetime:
        for name in ["context_lifetime.h", "context_lifetime.cpp"]:
            shutil.copyfile(ROOT / "plugin/gfxwrap" / name, output / GL / name)
    if boundary:
        for name in ["renderer_boundary.h", "renderer_boundary.cpp", "link_source_api.h", "link_source_api.cpp"]:
            shutil.copyfile(ROOT / "plugin/gfxwrap" / name, output / GL / name)
    print(f"Staged experimental state witness: {output}; pinned LINK {PIN}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--context-lifetime", action="store_true", help="also stage protected context retirement; compile with SM64_REPLAY_CONTEXT_LIFETIME")
    parser.add_argument("--boundary", action="store_true", help="also stage the explicit command boundary API")
    parser.add_argument("--label", help="distinct test-only selector name; settings identity stays unchanged")
    args = parser.parse_args()
    overlay(args.source, args.out, boundary=args.boundary, label=args.label, context_lifetime=args.context_lifetime)


if __name__ == "__main__":
    main()
