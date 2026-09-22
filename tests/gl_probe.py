"""Does this machine have an OpenGL driver the native GL witnesses can use?

The GL snapshot, context-lifetime, capture-chain and source-snapshot witnesses
create a hidden WGL context and load the GL 3.x entry points the renderer
overlay uses. A GitHub runner has no GPU driver: Windows falls back to its GDI
software OpenGL 1.1, those entry points load as NULL, and every witness fails
inside the native host on its loader assertion -- a machine gap, not a defect.

This asks the same question from Python, once, with a hidden window that never
takes focus: create a context, and look up `glFenceSync` (GL 3.2, the newest
entry point the witnesses need). None means the driver is there.
"""
from __future__ import annotations

import ctypes
import functools
from ctypes import wintypes


class _PixelFormat(ctypes.Structure):
    _fields_ = [("nSize", wintypes.WORD), ("nVersion", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("iPixelType", ctypes.c_ubyte), ("cColorBits", ctypes.c_ubyte),
                ("rest", ctypes.c_ubyte * 13), ("cDepthBits", ctypes.c_ubyte),
                ("tail", ctypes.c_ubyte * 16)]   # PIXELFORMATDESCRIPTOR: 40 bytes


@functools.lru_cache(maxsize=1)
def missing_modern_gl() -> str | None:
    """None when a GL 3.2+ context is available, else why not."""
    try:
        user32, gdi32 = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
        gl = ctypes.WinDLL("opengl32")
    except OSError as error:
        return f"no OpenGL 3.3+ driver on this machine ({error})"
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
    user32.GetDC.restype = wintypes.HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    gdi32.ChoosePixelFormat.argtypes = [wintypes.HDC, ctypes.POINTER(_PixelFormat)]
    gdi32.SetPixelFormat.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.POINTER(_PixelFormat)]
    gl.wglCreateContext.restype = ctypes.c_void_p
    gl.wglCreateContext.argtypes = [wintypes.HDC]
    gl.wglMakeCurrent.argtypes = [wintypes.HDC, ctypes.c_void_p]
    gl.wglDeleteContext.argtypes = [ctypes.c_void_p]
    gl.wglGetProcAddress.restype = ctypes.c_void_p
    gl.wglGetProcAddress.argtypes = [ctypes.c_char_p]
    gl.glGetString.restype = ctypes.c_char_p
    gl.glGetString.argtypes = [ctypes.c_uint]
    window = user32.CreateWindowExW(0, "STATIC", "sm64-gl-probe", 0, 0, 0, 1, 1, None, None, None, None)
    if not window:
        return "no OpenGL 3.3+ driver on this machine (could not create a probe window)"
    dc = user32.GetDC(window)
    context = None
    try:
        pfd = _PixelFormat(nSize=ctypes.sizeof(_PixelFormat), nVersion=1,
                           dwFlags=0x4 | 0x20 | 0x1, cColorBits=32, cDepthBits=24)
        pixel_format = gdi32.ChoosePixelFormat(dc, ctypes.byref(pfd))
        if not pixel_format or not gdi32.SetPixelFormat(dc, pixel_format, ctypes.byref(pfd)):
            return "no OpenGL 3.3+ driver on this machine (no pixel format)"
        context = gl.wglCreateContext(dc)
        if not context or not gl.wglMakeCurrent(dc, context):
            return "no OpenGL 3.3+ driver on this machine (no context)"
        renderer = (gl.glGetString(0x1F01) or b"?").decode(errors="replace")   # GL_RENDERER
        if not gl.wglGetProcAddress(b"glFenceSync"):
            return f"no OpenGL 3.3+ driver on this machine (renderer {renderer!r})"
        return None
    finally:
        if context:
            gl.wglMakeCurrent(None, None)
            gl.wglDeleteContext(context)
        user32.ReleaseDC(window, dc)
        user32.DestroyWindow(window)
