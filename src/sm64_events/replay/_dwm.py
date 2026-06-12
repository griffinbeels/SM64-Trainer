"""DWM shared-surface reader — lock-free capture of a window's redirection
surface via the undocumented user32!DwmGetDxSharedSurface.

WHY THIS EXISTS (the end of a long road, all live-measured):
- WGC/DDA see only the DWM composition path, which refreshes at 1-6 fps for
  PJ64 1.6's bitblt-model presentation: frozen content.
- GDI BitBlt of the window DC sees fresh content but serializes against the
  target's UI thread, which PJ64 holds ~110-170 ms ONCE PER SECOND (some
  internal 1 Hz work; survives hiding the FPS display): a visible skip and
  matching audio hiccup every second of every replay.
- The redirection surface itself — the exact pixels Jabo blits every
  present — is shared with DWM as a D3D11 texture. Reading it GPU-side
  through DWM's handle involves no window lock at all. Probe: 600 grabs in
  10 s, 30.1 distinct/s (the game's full 30 fps content rate), ZERO stalls,
  max gap 18 ms, 2.1 ms per grab.

UNDOCUMENTED-API RISK, accepted deliberately: DwmGetDxSharedSurface has
shipped in user32 since Windows 8 and is widely relied upon by capture
tooling, but it could change in a future Windows release. The video source
falls back to GDI BitBlt automatically if it ever returns failure.

The surface covers the WHOLE window (logical/DPI-unaware coordinates,
e.g. 1606x1273 for a 1600x1224 client) — callers crop the client region.
The handle changes on window resize: acquire() re-queries it per call
(measured lock-free) and rebuilds the staging texture when the surface
descriptor changes."""
import ctypes
import ctypes.wintypes as wt
import logging

import numpy as np

log = logging.getLogger("sm64.replay")

_user32 = ctypes.windll.user32
_d3d11 = ctypes.windll.d3d11

_DwmGetDxSharedSurface = _user32.DwmGetDxSharedSurface
_DwmGetDxSharedSurface.restype = wt.BOOL

# {6F15AAF2-D208-4E89-9AB4-489535D34F9C} packed little-endian
_IID_ID3D11Texture2D = (ctypes.c_ubyte * 16).from_buffer_copy(
    b"\xf2\xaa\x15\x6f\x08\xd2\x89\x4e\x9a\xb4\x48\x95\x35\xd3\x4f\x9c")

_DXGI_FORMAT_B8G8R8A8_UNORM = 87


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", wt.DWORD), ("HighPart", wt.LONG)]


class _TEX2D_DESC(ctypes.Structure):
    _fields_ = [("Width", wt.UINT), ("Height", wt.UINT), ("MipLevels", wt.UINT),
                ("ArraySize", wt.UINT), ("Format", wt.UINT),
                ("SampleCount", wt.UINT), ("SampleQuality", wt.UINT),
                ("Usage", wt.UINT), ("BindFlags", wt.UINT),
                ("CPUAccessFlags", wt.UINT), ("MiscFlags", wt.UINT)]


class _MAPPED(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", wt.UINT),
                ("DepthPitch", wt.UINT)]


def _vtbl(obj, index, restype, *argtypes):
    vptr = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(
        vptr.contents[index])


def _release(obj) -> None:
    if obj:
        _vtbl(obj, 2, ctypes.c_ulong)(obj)


class DwmSurfaceReader:
    """One D3D11 device + staging pipeline reading a window's redirection
    surface. acquire() returns a fresh BGRA ndarray of the full surface
    (or None when the surface is unavailable, e.g. window gone)."""

    def __init__(self) -> None:
        self._device = ctypes.c_void_p()
        self._context = ctypes.c_void_p()
        hr = _d3d11.D3D11CreateDevice(None, 1, None, 0, None, 0, 7,
                                      ctypes.byref(self._device), None,
                                      ctypes.byref(self._context))
        if hr != 0:
            raise RuntimeError(f"D3D11CreateDevice failed: 0x{hr & 0xFFFFFFFF:08X}")
        self._staging = ctypes.c_void_p()
        self._sdesc = _TEX2D_DESC()
        self._have_staging = False
        # Bind COM methods ONCE: constructing WINFUNCTYPE wrappers per call
        # is pure GIL-held Python overhead in the 60 Hz hot path (measured
        # as extra missed slots vs the probe).
        self._open_shared = _vtbl(self._device, 28, ctypes.c_long, wt.HANDLE,
                                  ctypes.POINTER(ctypes.c_ubyte),
                                  ctypes.POINTER(ctypes.c_void_p))
        self._create_tex = _vtbl(self._device, 5, ctypes.c_long,
                                 ctypes.POINTER(_TEX2D_DESC), ctypes.c_void_p,
                                 ctypes.POINTER(ctypes.c_void_p))
        self._copy_resource = _vtbl(self._context, 47, None, ctypes.c_void_p,
                                    ctypes.c_void_p)
        self._map = _vtbl(self._context, 14, ctypes.c_long, ctypes.c_void_p,
                          wt.UINT, wt.UINT, wt.UINT, ctypes.POINTER(_MAPPED))
        self._unmap = _vtbl(self._context, 15, None, ctypes.c_void_p, wt.UINT)

    def acquire(self, hwnd: int) -> np.ndarray | None:
        hsurf = wt.HANDLE()
        luid = _LUID()
        fmt = wt.ULONG()
        flags = wt.ULONG()
        upd = ctypes.c_ulonglong()
        if not _DwmGetDxSharedSurface(wt.HWND(hwnd), ctypes.byref(hsurf),
                                      ctypes.byref(luid), ctypes.byref(fmt),
                                      ctypes.byref(flags), ctypes.byref(upd)) \
                or not hsurf.value:
            return None
        tex = ctypes.c_void_p()
        hr = self._open_shared(self._device, hsurf, _IID_ID3D11Texture2D,
                               ctypes.byref(tex))
        if hr != 0 or not tex.value:
            return None
        try:
            desc = _TEX2D_DESC()
            _vtbl(tex, 10, None, ctypes.POINTER(_TEX2D_DESC))(
                tex, ctypes.byref(desc))
            if desc.Format != _DXGI_FORMAT_B8G8R8A8_UNORM:
                log.warning("dwm surface format %d unsupported", desc.Format)
                return None
            if (not self._have_staging
                    or desc.Width != self._sdesc.Width
                    or desc.Height != self._sdesc.Height):
                if self._have_staging:
                    _release(self._staging)
                    self._staging = ctypes.c_void_p()
                ctypes.memmove(ctypes.byref(self._sdesc), ctypes.byref(desc),
                               ctypes.sizeof(desc))
                self._sdesc.Usage = 3              # STAGING
                self._sdesc.BindFlags = 0
                self._sdesc.CPUAccessFlags = 0x20000  # CPU_ACCESS_READ
                self._sdesc.MiscFlags = 0
                self._sdesc.MipLevels = 1
                hr = self._create_tex(self._device, ctypes.byref(self._sdesc),
                                      None, ctypes.byref(self._staging))
                if hr != 0:
                    return None
                self._have_staging = True
            self._copy_resource(self._context, self._staging, tex)
            m = _MAPPED()
            hr = self._map(self._context, self._staging, 0, 1, 0,
                           ctypes.byref(m))
            if hr != 0:
                return None
            try:
                h, w = self._sdesc.Height, self._sdesc.Width
                buf = ctypes.string_at(m.pData, m.RowPitch * h)
                arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, m.RowPitch)
                return arr[:, :w * 4].reshape(h, w, 4).copy()
            finally:
                self._unmap(self._context, self._staging, 0)
        finally:
            _release(tex)

    def close(self) -> None:
        if self._have_staging:
            _release(self._staging)
            self._have_staging = False
        _release(self._context)
        _release(self._device)
        self._context = ctypes.c_void_p()
        self._device = ctypes.c_void_p()
