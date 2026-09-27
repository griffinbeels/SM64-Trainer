"""Can the adapter under the capture layer record at all? Asked before a helper starts.

The GPU route encodes with NVENC (plugin/gfxwrap/gpu_bridge_encoder.cpp), so an
AMD or Intel adapter, a software adapter, or an NVIDIA driver older than the
encoder's API can never open it. The capture layer advertises GPU capture
whatever the vendor, and before 2026-09-27 each of those machines retried the
encoder every 10-60 s forever and recorded nothing. These are FACTS about the
machine, answered here once; gpusettings.py remembers the verdict and the
recorder keeps the desktop grab instead. Anything not certain is left to the
encoder's own Open, whose failures stay retryable.

Read-only: DXGI enumeration, and the driver's own NVENC version query, which
NVIDIA documents for exactly this check. The encoder DLL loads the same file.
"""
from __future__ import annotations

from dataclasses import dataclass
import ctypes
import ctypes.wintypes as wt
import logging

log = logging.getLogger("sm64.replay")

NVIDIA_VENDOR_ID = 0x10DE
#: The NVENC API the encoder is built against: NVENCAPI_MAJOR/MINOR_VERSION in
#: plugin/gfxwrap/vendor/nvEncodeAPI.h (tests/test_gpu_backend_route.py reads
#: the header). The encoder refuses a driver that supports less.
REQUIRED_NVENC_API = (13, 0)
_SOFTWARE_ADAPTER = 0x2  # DXGI_ADAPTER_FLAG_SOFTWARE
_LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x800

# IIDs packed little-endian (Data1/2/3) + big-endian (Data4), the COM layout.
_IID_IDXGIFactory1 = (ctypes.c_ubyte * 16).from_buffer_copy(
    b"\x78\xae\x0a\x77\x6f\xf2\xba\x4d\xa8\x29\x25\x3c\x83\xd1\xb3\x87")
_IID_IDXGIDevice = (ctypes.c_ubyte * 16).from_buffer_copy(
    b"\xfa\x77\xec\x54\x77\x13\xe6\x44\x8c\x32\x88\xfd\x5f\x44\xc8\x4c")


class _AdapterDesc1(ctypes.Structure):
    _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", wt.UINT),
                ("DeviceId", wt.UINT), ("SubSysId", wt.UINT), ("Revision", wt.UINT),
                ("DedicatedVideoMemory", ctypes.c_size_t),
                ("DedicatedSystemMemory", ctypes.c_size_t),
                ("SharedSystemMemory", ctypes.c_size_t),
                ("LuidLow", wt.DWORD), ("LuidHigh", wt.LONG), ("Flags", wt.UINT)]


@dataclass(frozen=True)
class AdapterFacts:
    name: str
    vendor_id: int
    software: bool
    driver: str | None  # user-mode driver version, "32.0.15.7283"


def _vtbl(obj, index, restype, *argtypes):
    """COM method #index on `obj`; core/procmem.py's idiom."""
    vptr = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vptr.contents[index])


def _driver_version(adapter) -> str | None:
    version = ctypes.c_int64()
    check = _vtbl(adapter, 9, ctypes.c_long, ctypes.POINTER(ctypes.c_ubyte),
                  ctypes.POINTER(ctypes.c_int64))               # CheckInterfaceSupport
    if check(adapter, _IID_IDXGIDevice, ctypes.byref(version)) != 0:
        return None
    v = version.value & 0xFFFFFFFFFFFFFFFF
    return ".".join(str((v >> shift) & 0xFFFF) for shift in (48, 32, 16, 0))


def adapter_facts(luid) -> AdapterFacts | None:
    """The DXGI adapter with this (high, low) LUID, or None when unknown."""
    high, low = luid
    try:
        create = ctypes.WinDLL("dxgi").CreateDXGIFactory1
        create.argtypes = [ctypes.POINTER(ctypes.c_ubyte), ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_long
        factory = ctypes.c_void_p()
        if create(_IID_IDXGIFactory1, ctypes.byref(factory)) != 0 or not factory.value:
            return None
    except (OSError, AttributeError):
        return None
    try:
        enum = _vtbl(factory, 12, ctypes.c_long, ctypes.c_uint,
                     ctypes.POINTER(ctypes.c_void_p))           # EnumAdapters1
        for index in range(64):
            adapter = ctypes.c_void_p()
            if enum(factory, index, ctypes.byref(adapter)) != 0 or not adapter.value:
                return None  # DXGI_ERROR_NOT_FOUND: no adapter has this LUID
            try:
                desc = _AdapterDesc1()
                _vtbl(adapter, 10, ctypes.c_long, ctypes.POINTER(_AdapterDesc1))(
                    adapter, ctypes.byref(desc))                # GetDesc1
                if (desc.LuidHigh, desc.LuidLow) == (high, low & 0xFFFFFFFF):
                    return AdapterFacts(desc.Description, desc.VendorId,
                                        bool(desc.Flags & _SOFTWARE_ADAPTER),
                                        _driver_version(adapter))
            finally:
                _vtbl(adapter, 2, ctypes.c_ulong)(adapter)      # Release
    except OSError:
        log.debug("graphics adapter enumeration failed", exc_info=True)
    finally:
        _vtbl(factory, 2, ctypes.c_ulong)(factory)              # Release
    return None


def nvenc_api_version() -> tuple[int, int] | None:
    """The newest NVENC API the installed driver offers; None without one."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LoadLibraryExW.argtypes = [wt.LPCWSTR, wt.HANDLE, wt.DWORD]
    kernel.LoadLibraryExW.restype = wt.HMODULE
    kernel.GetProcAddress.argtypes = [wt.HMODULE, wt.LPCSTR]
    kernel.GetProcAddress.restype = ctypes.c_void_p
    kernel.FreeLibrary.argtypes = [wt.HMODULE]
    module = kernel.LoadLibraryExW("nvEncodeAPI64.dll", None, _LOAD_LIBRARY_SEARCH_SYSTEM32)
    if not module:
        return None
    try:
        address = kernel.GetProcAddress(module, b"NvEncodeAPIGetMaxSupportedVersion")
        if not address:
            return None
        query = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.POINTER(ctypes.c_uint32))(address)
        version = ctypes.c_uint32()
        if query(ctypes.byref(version)) != 0:  # NV_ENC_SUCCESS
            return None
        return version.value >> 4, version.value & 0xF
    finally:
        kernel.FreeLibrary(module)


def unsupported_reason(facts: AdapterFacts | None) -> str | None:
    """Why this adapter can never open the encoder, or None when it may."""
    if facts is None:
        return None  # Unknown is not unsupported: the encoder's Open decides.
    if facts.software:
        return f"{facts.name} is a software adapter; the capture layer needs an NVIDIA GPU"
    if facts.vendor_id != NVIDIA_VENDOR_ID:
        return f"{facts.name} has no NVIDIA video encoder"
    api = nvenc_api_version()
    if api is None:
        return f"{facts.name} has no NVIDIA video encoder driver installed"
    if api < REQUIRED_NVENC_API:
        return (f"the NVIDIA driver for {facts.name} is too old (video encoder "
                f"{api[0]}.{api[1]}, needs {REQUIRED_NVENC_API[0]}.{REQUIRED_NVENC_API[1]})")
    return None
