"""Source-only GhostV3 seams on the pinned renderer; never install or run it."""
from pathlib import Path

GUARD = "#if defined(OS_WINDOWS) && defined(SM64_GHOST_RENDERER)\n"
INCLUDE = GUARD + '#include "Graphics/OpenGLContext/ghost_link.h"\n#endif\n'


def display_list_edit(data: bytes, replace_once) -> bytes:
    """Observe only admitted pushes, before upstream changes PC stack identity."""
    return replace_once(data, '\t\tDebugMsg(DEBUG_NORMAL, "gSPDisplayList( 0x%08X ) push\\n", dl);',
        GUARD + '\t\tghost_link::display_list(dl, address);\n#endif\n'
        '\t\tDebugMsg(DEBUG_NORMAL, "gSPDisplayList( 0x%08X ) push\\n", dl);')


def edits(source: Path, replace_once) -> dict[str, bytes]:
    """Each anchor is checked exactly once by the pinned source staging tool."""
    result = {}
    path = "src/RSP.cpp"
    data = (source / path).read_bytes()
    data = replace_once(data, '#include "DisplayWindow.h"', '#include "DisplayWindow.h"\n' + INCLUDE)
    # The guard's destructor runs on every return, including skipped/interrupted
    # lists. It publishes after deferred raster work, before another occurrence.
    data = replace_once(data, "void RSP_ProcessDList()\n{", "void RSP_ProcessDList()\n{\n" + GUARD +
        "\tstruct GhostList {\n\t\tbool resumed = RSP.infloop;\n"
        "\t\tbool active = ghost_link::begin_list();\n"
        "\t\t~GhostList() { if (!active) return;\n"
        "\t\t\tconst bool complete = !resumed && !ConfigOpen && !dwnd().isResizeWindow() && RSP.halt && !RSP.infloop;\n"
        "\t\t\tif (complete) { dwnd().getDrawer().drawTriangles(); dwnd().getDrawer().flush(); }\n"
        "\t\t\tghost_link::end_list(complete); }\n"
        "\t} ghostList;\n#endif")
    result[path] = data
    path = "src/gSP.cpp"
    data = (source / path).read_bytes()
    data = replace_once(data, '#include "DisplayWindow.h"', '#include "DisplayWindow.h"\n' + INCLUDE)
    data = replace_once(data, '\n\tDebugMsg(DEBUG_NORMAL, "gSPMatrix( 0x%08X, %s | %s | %s );\\n",',
        "\n" + GUARD + "\tif (param & G_MTX_PROJECTION) ghost_link::projection();\n#endif\n" +
        '\tDebugMsg(DEBUG_NORMAL, "gSPMatrix( 0x%08X, %s | %s | %s );\\n",')
    for anchor, after in [
        ("\tgSP.geometryMode = (gSP.geometryMode & ~clear) | set;", "(gSP.geometryMode & ~clear) | set"),
        ("\tgSP.geometryMode |= mode;", "gSP.geometryMode | mode"),
        ("\tgSP.geometryMode &= ~mode;", "gSP.geometryMode & ~mode"),
    ]:
        data = replace_once(data, "\n" + anchor, "\n" + GUARD +
            f"\tghost_link::geometry(gSP.geometryMode, {after});\n#endif\n" + anchor)
    data = display_list_edit(data, replace_once)
    result[path] = data
    path = "src/gDP.cpp"
    data = (source / path).read_bytes()
    data = INCLUDE.encode() + data
    anchor = "\tgDP.colorImage.format = format;"
    data = replace_once(data, anchor, GUARD +
        "\tif (address != gDP.colorImage.address || width != gDP.colorImage.width\n"
        "\t\t\t|| format != gDP.colorImage.format || size != gDP.colorImage.size)\n"
        "\t\tghost_link::color_target_changed();\n#endif\n" + anchor)
    result[path] = data
    path = "src/Graphics/OpenGLContext/opengl_ContextImpl.cpp"
    data = (source / path).read_bytes()
    data = (GUARD + '#include "ghost_link.h"\n#endif\n').encode() + data
    anchor = "\tm_graphicsDrawer->drawTriangles(_params);"
    data = replace_once(data, anchor, anchor + "\n" + GUARD +
        "\tghost_link::triangles((_params.elementsCount ? _params.elementsCount : _params.verticesCount) / 3);\n#endif")
    # Mirror only the actual raster operation. A scope restores bindings even
    # if an upstream draw reports an exception; no N64 command runs twice.
    for call in ("m_graphicsDrawer->drawTriangles(_params);",
                 "m_graphicsDrawer->drawRects(_params);",
                 "m_graphicsDrawer->drawLine(_width, _vertices);"):
        anchor = "\t" + call
        data = replace_once(data, anchor, anchor + "\n" + GUARD +
            "\t{ struct GhostHud { bool active = ghost_link::begin_hud();\n"
            "\t\t~GhostHud() { if (active) ghost_link::end_hud(); } } hud;\n"
            f"\t\tif (hud.active) {call} }}\n#endif")
    for signature in (
        "void ContextImpl::clearColorBuffer(f32 _red, f32 _green, f32 _blue, f32 _alpha)",
        "void ContextImpl::clearDepthBuffer()",
        "bool ContextImpl::blitFramebuffers(const graphics::Context::BlitFramebuffersParams & _params)",
    ):
        anchor = signature + "\n{"
        data = replace_once(data, anchor, anchor + "\n" + GUARD +
                            "\tghost_link::clear_target();\n#endif")
    result[path] = data
    path = "src/common/CommonAPIImpl_common.cpp"
    data = (source / path).read_bytes()
    data = INCLUDE.encode() + data
    for opened, anchor in [(True, "\tm_bRomOpen = true;"), (False, "\tm_bRomOpen = false;")]:
        value = "true" if opened else "false"
        data = replace_once(data, anchor, anchor + "\n" + GUARD + f"\tghost_link::rom({value});\n#endif")
    result[path] = data
    path = "src/PluginAPI.h"
    data = (GUARD + '#include "Graphics/OpenGLContext/ghost_ipc.h"\n#endif\n').encode() + (source / path).read_bytes()
    data = replace_once(data, "\tvoid CloseDLL(void) { rb_close(); }",
        "\tvoid CloseDLL(void) { rb_close();\n" + GUARD +
        "\t\tghost_ipc::rom(false);\n#endif\n\t}")
    result[path] = data
    path = "src/Graphics/OpenGLContext/windows/windows_DisplayWindow.cpp"
    data = (source / path).read_bytes()
    data = (GUARD + '#include "../ghost_link.h"\n#endif\n').encode() + data
    # Apply after the existing capture overlay: these are actual current-context
    # lifecycle sites, before the original context is deleted or replaced.
    data = replace_once(data, "\treplay_gl::ContextCreated(hRC, hDC, (actual.dwFlags & PFD_DOUBLEBUFFER) != 0);",
        "\treplay_gl::ContextCreated(hRC, hDC, (actual.dwFlags & PFD_DOUBLEBUFFER) != 0);\n" +
        GUARD + "\tif (resized && hRC && wglGetCurrentContext() == hRC) ghost_link::context_created();\n#endif")
    for anchor in ["bool DisplayWindowWindows::_start()\n{", "void DisplayWindowWindows::_stop()\n{"]:
        data = replace_once(data, anchor, anchor + "\n" + GUARD + "\tghost_link::context_lost();\n#endif")
    result[path] = data
    return result
