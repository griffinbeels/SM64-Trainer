"""Clean/display presentation seams on the pinned renderer, with no installation."""
from pathlib import Path

GUARD = "#if defined(OS_WINDOWS) && defined(SM64_GHOST_RENDERER)\n"
INCLUDE = GUARD + '#include "Graphics/OpenGLContext/ghost_presentation.h"\n#endif\n'


def edits(source: Path, replace_once) -> dict[str, bytes]:
    """Compose after capture/ghost staging; refuse every ambiguous source anchor."""
    result = {}
    path = "src/FrameBuffer.cpp"
    data = INCLUDE.encode() + (source / path).read_bytes()
    anchor = "\tdrawer.copyTexturedRect(blitParams);\n\n\tif (pNextBuffer != nullptr)"
    data = replace_once(data, anchor, "\tdrawer.copyTexturedRect(blitParams);\n" + GUARD +
        "\tghost_presentation::draw_vi(*pBuffer, blitParams, rdpRes.vi_origin, pNextBuffer != nullptr);\n#endif\n\n"
        "\tif (pNextBuffer != nullptr)")
    anchor = "\tdrawer.blitOrCopyTexturedRect(blitParams);\n\n\tgfxContext.bindFramebuffer(bufferTarget::READ_FRAMEBUFFER, ObjectHandle::defaultFramebuffer);\n\n\twnd.swapBuffers();"
    data = replace_once(data, anchor, "\tdrawer.blitOrCopyTexturedRect(blitParams);\n" + GUARD +
        "\tghost_presentation::draw_screen_vi(*pBuffer, blitParams, *REG.VI_ORIGIN);\n#endif\n\n"
        "\tgfxContext.bindFramebuffer(bufferTarget::READ_FRAMEBUFFER, ObjectHandle::defaultFramebuffer);\n\n\twnd.swapBuffers();")
    result[path] = data

    path = "src/FrameBuffer.h"
    data = (source / path).read_bytes()
    anchor = "\tFrameBuffer * getCurrent() const {return m_pCurrent;}"
    data = replace_once(data, anchor, anchor + "\n" + GUARD +
        "\tFrameBuffer * getScreenSizeBuffer() { return m_list.empty() ? nullptr : &m_list.back(); }\n#endif")
    result[path] = data

    path = "src/DisplayWindow.cpp"
    data = INCLUDE.encode() + (source / path).read_bytes()
    # The original drawer owns its cached shader/texture state. Setup follows
    # its initialization; destruction precedes it while the same GL context lives.
    data = replace_once(data, "\tm_drawer._initData();\n\tm_buffersSwapCount = 0;",
        "\tm_drawer._initData();\n" + GUARD + "\tghost_presentation::context_ready();\n#endif\n\tm_buffersSwapCount = 0;")
    data = replace_once(data, "void DisplayWindow::stop()\n{", "void DisplayWindow::stop()\n{\n" + GUARD +
        "\tghost_presentation::context_stopping();\n#endif")
    for guard in ["m_bToggleFullscreen", "m_bResizeWindow"]:
        anchor = f"\tif (!{guard})\n\t\treturn false;\n\tm_drawer._destroyData();"
        data = replace_once(data, anchor, f"\tif (!{guard})\n\t\treturn false;\n" + GUARD +
            "\tghost_presentation::drawable_changing();\n#endif\n\tm_drawer._destroyData();")
        anchor = f"\tm_drawer._initData();\n\t{guard} = false;"
        data = replace_once(data, anchor, "\tm_drawer._initData();\n" + GUARD +
            f"\tghost_presentation::context_ready();\n#endif\n\t{guard} = false;")
    data = replace_once(data, "\tif (m_drawer.getDrawingState() != DrawingState::Non)\n\t\tm_drawer._destroyData();",
        GUARD + "\tghost_presentation::context_stopping();\n#endif\n"
        "\tif (m_drawer.getDrawingState() != DrawingState::Non)\n\t\tm_drawer._destroyData();")
    data = replace_once(data, "\tm_drawer.drawOSD();\n\t_swapBuffers();", GUARD +
        "\tghost_presentation::before_swap();\n#endif\n\tm_drawer.drawOSD();\n" + GUARD +
        "\tghost_presentation::after_osd();\n#endif\n\t_swapBuffers();")
    result[path] = data

    path = "src/TextDrawer.cpp"
    data = INCLUDE.encode() + (source / path).read_bytes()
    anchor = "\tgfxContext.drawRects(rectParams);"
    data = replace_once(data, anchor, anchor + "\n" + GUARD +
        "\tif (ghost_presentation::begin_osd_raster()) {\n"
        "\t\tstruct RestoreGhostOSD { ~RestoreGhostOSD() { ghost_presentation::end_osd_raster(); } } restoreGhostOSD;\n"
        "\t\tgfxContext.drawRects(rectParams);\n\t}\n#endif")
    result[path] = data

    path = "src/Graphics/OpenGLContext/windows/windows_DisplayWindow.cpp"
    data = (GUARD + '#include "../ghost_presentation.h"\n#endif\n').encode() + (source / path).read_bytes()
    anchor = "\tif (hDC == NULL)\n\t\tSwapBuffers( wglGetCurrentDC() );\n\telse\n\t\tSwapBuffers( hDC );"
    data = replace_once(data, anchor,
        "\tconst BOOL swapped = SwapBuffers(hDC == NULL ? wglGetCurrentDC() : hDC);\n" + GUARD +
        "\tghost_presentation::swap_completed(swapped != FALSE);\n#endif")
    result[path] = data
    return result
