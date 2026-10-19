"""Can the adapter under the capture layer record at all? Asked before a helper starts.

NVIDIA retains the proven NVENC backend. Other hardware uses the Windows
D3D11 encoder backend and answers its actual codec capabilities at Open.
Only positive hardware/driver incompatibility is an early refusal; a vendor
name alone cannot establish that hardware encoding is unavailable.

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
_DXGI_ERROR_NOT_FOUND = 0x887A0002
_MAX_ADAPTERS = 64
_KNOWN_GPU_VENDORS = {NVIDIA_VENDOR_ID, 0x1002, 0x8086}  # NVIDIA, AMD, Intel

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


def adapter_inventory() -> tuple[tuple[tuple[int, int], AdapterFacts], ...] | None:
    """All DXGI adapters and their LUIDs, or None if enumeration is incomplete.

    Only DXGI_ERROR_NOT_FOUND proves the end. An error, failed description or
    the enumeration bound cannot prove that a usable adapter is absent.
    """
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
        adapters = []
        for index in range(_MAX_ADAPTERS):
            adapter = ctypes.c_void_p()
            result = enum(factory, index, ctypes.byref(adapter))
            if result & 0xFFFFFFFF == _DXGI_ERROR_NOT_FOUND:
                return tuple(adapters)
            if result != 0 or not adapter.value:
                return None
            try:
                desc = _AdapterDesc1()
                if _vtbl(adapter, 10, ctypes.c_long, ctypes.POINTER(_AdapterDesc1))(
                        adapter, ctypes.byref(desc)) != 0:       # GetDesc1
                    return None
                adapters.append(((desc.LuidHigh, desc.LuidLow), AdapterFacts(
                    desc.Description, desc.VendorId, bool(desc.Flags & _SOFTWARE_ADAPTER),
                    _driver_version(adapter))))
            finally:
                _vtbl(adapter, 2, ctypes.c_ulong)(adapter)      # Release
    except (OSError, AttributeError):
        log.debug("graphics adapter enumeration failed", exc_info=True)
    finally:
        _vtbl(factory, 2, ctypes.c_ulong)(factory)              # Release
    return None


def adapter_facts(luid) -> AdapterFacts | None:
    """The DXGI adapter with this (high, low) LUID, or None when unknown."""
    inventory = adapter_inventory()
    key = (luid[0], luid[1] & 0xFFFFFFFF)
    return next((facts for found, facts in inventory if found == key), None) if inventory else None


def machine_unsupported_reason() -> str | None:
    """A verdict only if EVERY possible capture adapter is definitely unsupported.

    This precedes the channel's actual GL adapter identity. A hybrid machine,
    unknown vendor or incomplete inventory must retain that later check.
    """
    inventory = adapter_inventory()
    if not inventory:
        return None
    reasons = []
    for _luid, facts in inventory:
        if not facts.software and facts.vendor_id not in _KNOWN_GPU_VENDORS:
            return None
        try:
            if not facts.software and facts.vendor_id == NVIDIA_VENDOR_ID:
                api = nvenc_api_version()
                if api is None:
                    return None  # Absent DLL and failed query share this answer.
                reason = _driver_unsupported_reason(facts, api)
            else:
                reason = unsupported_reason(facts)
        except (OSError, AttributeError):
            return None  # A failed driver query cannot prove hardware absence.
        if reason is None:
            return None
        reasons.append(reason)
    return "; ".join(dict.fromkeys(reasons))


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
        return f"{facts.name} is a software adapter; the capture layer needs a hardware GPU"
    if facts.vendor_id != NVIDIA_VENDOR_ID:
        return None  # Its actual Windows hardware encoder decides by codec.
    return _driver_unsupported_reason(facts, nvenc_api_version())


def encoder_backend(facts: AdapterFacts | None) -> int:
    """Resolve the native backend for the renderer's adapter, not adapter zero."""
    from sm64_events.replay.gpuencoder_abi import BACKEND_NVENC, BACKEND_WINDOWS

    # Missing facts preserve legacy NVIDIA behavior. The helper still binds
    # the exact channel LUID; it must never choose the first installed GPU.
    return (BACKEND_WINDOWS if facts is not None
            and facts.vendor_id != NVIDIA_VENDOR_ID else BACKEND_NVENC)


def _driver_unsupported_reason(facts, api):
    if api is None:
        return f"{facts.name} has no NVIDIA video encoder driver installed"
    if api < REQUIRED_NVENC_API:
        return (f"the NVIDIA driver for {facts.name} is too old (video encoder "
                f"{api[0]}.{api[1]}, needs {REQUIRED_NVENC_API[0]}.{REQUIRED_NVENC_API[1]})")
    return None
