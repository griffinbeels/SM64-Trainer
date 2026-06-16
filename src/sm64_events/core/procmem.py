"""Process + OS resource observability — the evidence layer for leak hunting.

WHY THIS EXISTS: a long session grew RAM/handles unboundedly and every fix
attempt missed, because the monitor sampled only THIS process's working set
and a total object count — blind to the places a leak actually hides. The
2026-06-14 widening adds, deliberately, one probe per previously-invisible
suspect (each maps to a hypothesis the old surface could not test):

- CHILD-PROCESS memory: encoding runs in an ffmpeg.exe subprocess. If ffmpeg
  grows, our RSS stays flat while the MACHINE runs out of RAM — the exact
  "we keep instrumenting and never catch it" failure. child_memory() sums it.
- GDI / USER / kernel HANDLES: the capture path touches user32/gdi32 (DWM
  handle query every 1 s; GDI fallback DCs/bitmaps). A handle leak craters the
  whole desktop with our RSS unmoved. handle_counts() exposes them.
- SYSTEM-WIDE pressure: a near-full pagefile thrashes everything and reads as
  "out of RAM" even if no single process is huge. system_memory() shows the
  commit charge and memory-load %.
- PRIVATE/COMMIT bytes vs working set: the OS trims working set under pressure,
  hiding a leak; committed private bytes is the honest signal. private_bytes().
- WHICH Python type is growing: a total count says "the heap grew" but not
  what. type_histogram() + top_type_growth() name the accumulating type.

All probes are pure-ctypes (no third-party deps, the codebase's Windows idiom)
and degrade to 0 / {} off-Windows or on probe failure — the monitor never
crashes the server. The periodic sampler that consumes these lives in
core/perfmon.py; the pure decision helpers here are unit-tested."""
import ctypes
import gc
import logging
import os
from collections import Counter
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger("sm64.procmem")

_GiB = 1024 ** 3


class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
    _fields_ = [("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t)]


class _MEMORYSTATUSEX(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


class _PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),  # ULONG_PTR
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", ctypes.c_wchar * 260)]


def _bind():
    """Bind every Win32 entry point ONCE with explicit argtypes/restypes.
    WITHOUT argtypes, ctypes defaults pointer args to c_int and truncates
    them to 32 bits on 64-bit Python, so byref() writes nowhere and the call
    silently no-ops (the bug that made an earlier RSS probe always read 0).
    Returns a flat namespace, or an all-None one off-Windows."""
    ns = type("WinAPI", (), {})()
    names = ("gpmi", "getcur", "getpid_handle", "handlecount", "guiresources",
             "memstatus", "snapshot", "proc_first", "proc_next", "openproc",
             "closehandle")
    try:
        k32 = ctypes.windll.kernel32
        psapi = ctypes.windll.psapi
        user32 = ctypes.windll.user32

        ns.gpmi = psapi.GetProcessMemoryInfo
        ns.gpmi.argtypes = [wintypes.HANDLE,
                            ctypes.POINTER(_PROCESS_MEMORY_COUNTERS),
                            wintypes.DWORD]
        ns.gpmi.restype = wintypes.BOOL

        ns.getcur = k32.GetCurrentProcess
        ns.getcur.restype = wintypes.HANDLE

        ns.handlecount = k32.GetProcessHandleCount
        ns.handlecount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        ns.handlecount.restype = wintypes.BOOL

        ns.guiresources = user32.GetGuiResources
        ns.guiresources.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        ns.guiresources.restype = wintypes.DWORD

        ns.memstatus = k32.GlobalMemoryStatusEx
        ns.memstatus.argtypes = [ctypes.POINTER(_MEMORYSTATUSEX)]
        ns.memstatus.restype = wintypes.BOOL

        ns.snapshot = k32.CreateToolhelp32Snapshot
        ns.snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        ns.snapshot.restype = wintypes.HANDLE

        ns.proc_first = k32.Process32FirstW
        ns.proc_first.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        ns.proc_first.restype = wintypes.BOOL

        ns.proc_next = k32.Process32NextW
        ns.proc_next.argtypes = [wintypes.HANDLE, ctypes.POINTER(_PROCESSENTRY32W)]
        ns.proc_next.restype = wintypes.BOOL

        ns.openproc = k32.OpenProcess
        ns.openproc.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        ns.openproc.restype = wintypes.HANDLE

        ns.closehandle = k32.CloseHandle
        ns.closehandle.argtypes = [wintypes.HANDLE]
        ns.closehandle.restype = wintypes.BOOL
        return ns
    except Exception:  # non-Windows or missing module
        for n in names:
            setattr(ns, n, None)
        return ns


_API = _bind()

_GR_GDIOBJECTS = 0
_GR_USEROBJECTS = 1
_TH32CS_SNAPPROCESS = 0x2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_INVALID_HANDLE = ctypes.c_void_p(-1).value


def _mem_counters(handle) -> _PROCESS_MEMORY_COUNTERS | None:
    """GetProcessMemoryInfo for an already-open handle, or None on failure."""
    if _API.gpmi is None or not handle:
        return None
    c = _PROCESS_MEMORY_COUNTERS()
    c.cb = ctypes.sizeof(c)
    if _API.gpmi(handle, ctypes.byref(c), c.cb):
        return c
    return None


def rss_bytes() -> int:
    """Current process working set (resident set) in bytes, or 0 if the
    platform can't report it."""
    if _API.getcur is None:
        return 0
    c = _mem_counters(_API.getcur())
    return int(c.WorkingSetSize) if c else 0


def private_bytes() -> int:
    """Current process COMMITTED private bytes (PagefileUsage) — the honest
    leak signal: unlike the working set, the OS does not trim it under memory
    pressure, so a steady climb here is a true leak. 0 if unavailable."""
    if _API.getcur is None:
        return 0
    c = _mem_counters(_API.getcur())
    return int(c.PagefileUsage) if c else 0


def handle_counts() -> dict:
    """Kernel handle count + GDI + USER object counts for THIS process. A
    climb in any is a system-wide-resource leak that leaves RSS flat (the
    'whole desktop lags but our process looks fine' signature). Per-process
    GDI/USER objects are capped at 10000 by Windows — alarms fire well below.
    Missing keys (off-Windows) read as 0 downstream."""
    out: dict = {}
    if _API.getcur is None:
        return out
    h = _API.getcur()
    if _API.handlecount is not None:
        n = wintypes.DWORD(0)
        if _API.handlecount(h, ctypes.byref(n)):
            out["handles"] = int(n.value)
    if _API.guiresources is not None:
        out["gdi_objects"] = int(_API.guiresources(h, _GR_GDIOBJECTS))
        out["user_objects"] = int(_API.guiresources(h, _GR_USEROBJECTS))
    return out


def system_memory() -> dict:
    """System-wide memory via GlobalMemoryStatusEx: load_pct (0-100), available
    physical, total physical, and the COMMIT charge (total page file - avail)
    against its limit. Distinguishes 'one process is huge' from 'the whole
    machine is out of commit' — both lag everything, the fixes differ. {} off
    Windows."""
    if _API.memstatus is None:
        return {}
    m = _MEMORYSTATUSEX()
    m.dwLength = ctypes.sizeof(m)
    if not _API.memstatus(ctypes.byref(m)):
        return {}
    return {"load_pct": int(m.dwMemoryLoad),
            "avail_phys_bytes": int(m.ullAvailPhys),
            "total_phys_bytes": int(m.ullTotalPhys),
            "commit_bytes": int(m.ullTotalPageFile - m.ullAvailPageFile),
            "commit_limit_bytes": int(m.ullTotalPageFile)}


def child_memory(parent_pid: int) -> dict:
    """Summed working-set + private bytes of the DIRECT child processes of
    parent_pid (ffmpeg.exe is spawned directly by the recorder). THE probe for
    the encoder-leak hypothesis: ffmpeg memory is invisible to a self-only RSS
    sample. Returns {count, rss_bytes, private_bytes, by_name:{exe: rss}}; {}
    off Windows. Best-effort — a child we can't open is skipped, never raises."""
    if _API.snapshot is None:
        return {}
    snap = _API.snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return {}
    count = 0
    rss = priv = 0
    by_name: Counter = Counter()
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _API.proc_first(snap, ctypes.byref(entry))
        while ok:
            if entry.th32ParentProcessID == parent_pid:
                h = _API.openproc(_PROCESS_QUERY_LIMITED_INFORMATION, False,
                                  entry.th32ProcessID)
                if h:
                    try:
                        c = _mem_counters(h)
                        if c:
                            count += 1
                            rss += int(c.WorkingSetSize)
                            priv += int(c.PagefileUsage)
                            by_name[entry.szExeFile] += int(c.WorkingSetSize)
                    finally:
                        _API.closehandle(h)
            ok = _API.proc_next(snap, ctypes.byref(entry))
    finally:
        _API.closehandle(snap)
    return {"count": count, "rss_bytes": rss, "private_bytes": priv,
            "by_name": dict(by_name)}


def named_process_memory(names) -> dict:
    """Working-set + private bytes of ALL processes whose exe matches `names`
    (case-insensitive), keyed by exe name. THE probe for 'is the system-commit
    growth PJ64's OWN leak, not ours?' — PJ64 is not our child, so child_memory
    misses it. `names`: iterable of exe filenames (e.g. {'project64.exe'}).
    Returns {exe: {count, rss_bytes, private_bytes}}; {} off Windows. Best-
    effort — a process we can't open is skipped, never raises."""
    if _API.snapshot is None:
        return {}
    wanted = {n.lower() for n in names}
    snap = _API.snapshot(_TH32CS_SNAPPROCESS, 0)
    if not snap or snap == _INVALID_HANDLE:
        return {}
    out: dict = {}
    try:
        entry = _PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        ok = _API.proc_first(snap, ctypes.byref(entry))
        while ok:
            exe = entry.szExeFile
            if exe.lower() in wanted:
                h = _API.openproc(_PROCESS_QUERY_LIMITED_INFORMATION, False,
                                  entry.th32ProcessID)
                if h:
                    try:
                        c = _mem_counters(h)
                        if c:
                            slot = out.setdefault(
                                exe, {"count": 0, "rss_bytes": 0,
                                      "private_bytes": 0})
                            slot["count"] += 1
                            slot["rss_bytes"] += int(c.WorkingSetSize)
                            slot["private_bytes"] += int(c.PagefileUsage)
                    finally:
                        _API.closehandle(h)
            ok = _API.proc_next(snap, ctypes.byref(entry))
    finally:
        _API.closehandle(snap)
    return out


# -- GPU VRAM (DXGI) ---------------------------------------------------------
# The DWM capture path is pure D3D11 (replay/_dwm.py): a leak of staging /
# shared-surface textures fills VRAM, which neither process RSS nor (until it
# spills to shared system memory) system commit reveals — the one dimension a
# CPU-memory sample is structurally blind to. QueryVideoMemoryInfo gives the
# adapter-wide usage; if OUR capture leaks, local usage climbs across a session.
class _DXGI_QVMI(ctypes.Structure):
    _fields_ = [("Budget", ctypes.c_uint64), ("CurrentUsage", ctypes.c_uint64),
                ("AvailableForReservation", ctypes.c_uint64),
                ("CurrentReservation", ctypes.c_uint64)]


# IIDs packed little-endian (Data1/2/3) + big-endian (Data4), the COM layout.
_IID_IDXGIFactory1 = (ctypes.c_ubyte * 16).from_buffer_copy(
    b"\x78\xae\x0a\x77\x6f\xf2\xba\x4d\xa8\x29\x25\x3c\x83\xd1\xb3\x87")
_IID_IDXGIAdapter3 = (ctypes.c_ubyte * 16).from_buffer_copy(
    b"\xa4\x67\x59\x64\x92\x13\x10\x43\xa7\x98\x80\x53\xce\x3e\x93\xfd")


def _vtbl(obj, index, restype, *argtypes):
    """Call COM method #index on `obj` (ctypes c_void_p to the interface).
    Mirrors replay/_dwm.py's helper — the codebase's COM-via-ctypes idiom."""
    vptr = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(
        vptr.contents[index])


def gpu_memory() -> dict:
    """Adapter VRAM usage via DXGI QueryVideoMemoryInfo, summed across adapters:
    {local_usage_bytes (VRAM in use), local_budget_bytes (what the OS grants),
    nonlocal_usage_bytes (shared-system spill)}. Adapter-wide, not per-process —
    but a climbing local_usage over a session is the GPU-resource-leak signal.
    {} off Windows / on any probe failure (it never raises into the monitor)."""
    try:
        dxgi = ctypes.windll.dxgi
        create = dxgi.CreateDXGIFactory1
        create.argtypes = [ctypes.POINTER(ctypes.c_ubyte),
                           ctypes.POINTER(ctypes.c_void_p)]
        create.restype = ctypes.c_long
    except Exception:
        return {}
    factory = ctypes.c_void_p()
    try:
        if create(_IID_IDXGIFactory1, ctypes.byref(factory)) != 0 \
                or not factory.value:
            return {}
    except Exception:
        return {}
    local_usage = local_budget = nonlocal_usage = 0
    try:
        # Adapter 0 = the primary/discrete GPU (the 5090 here). Summing all
        # adapters folded in the software render driver's system-RAM budget
        # (~192 GB of noise); the primary adapter is where our D3D capture and
        # PJ64 render, so its CurrentUsage is the signal. QueryVideoMemoryInfo
        # reports the CALLING process's usage — run inside the server it tracks
        # OUR VRAM, climbing if _dwm leaks staging/shared-surface textures.
        enum = _vtbl(factory, 12, ctypes.c_long, ctypes.c_uint,
                     ctypes.POINTER(ctypes.c_void_p))           # EnumAdapters1
        adapter = ctypes.c_void_p()
        if enum(factory, 0, ctypes.byref(adapter)) != 0 or not adapter.value:
            return {}
        try:
            a3 = ctypes.c_void_p()
            qi = _vtbl(adapter, 0, ctypes.c_long,
                       ctypes.POINTER(ctypes.c_ubyte),
                       ctypes.POINTER(ctypes.c_void_p))         # QueryInterface
            if qi(adapter, _IID_IDXGIAdapter3, ctypes.byref(a3)) == 0 \
                    and a3.value:
                try:
                    qvmi = _vtbl(a3, 14, ctypes.c_long, ctypes.c_uint,
                                 ctypes.c_int, ctypes.POINTER(_DXGI_QVMI))
                    info = _DXGI_QVMI()
                    if qvmi(a3, 0, 0, ctypes.byref(info)) == 0:      # 0 = LOCAL
                        local_usage = int(info.CurrentUsage)
                        local_budget = int(info.Budget)
                    info2 = _DXGI_QVMI()
                    if qvmi(a3, 0, 1, ctypes.byref(info2)) == 0:     # 1 = NONLOCAL
                        nonlocal_usage = int(info2.CurrentUsage)
                finally:
                    _vtbl(a3, 2, ctypes.c_ulong)(a3)                 # Release
        finally:
            _vtbl(adapter, 2, ctypes.c_ulong)(adapter)              # Release
    except Exception:
        return {}
    finally:
        _vtbl(factory, 2, ctypes.c_ulong)(factory)                  # Release
    return {"local_usage_bytes": local_usage,
            "local_budget_bytes": local_budget,
            "nonlocal_usage_bytes": nonlocal_usage}


def thread_count() -> int:
    """Python-level live thread count (threading.active_count). Captures the
    threads WE create — the capture/feeder/reader/gc-gen2 daemons — so a
    thread leak (e.g. ffmpeg reader threads never reaped across restarts)
    shows up. Cheap; no syscall."""
    import threading
    return threading.active_count()


def dir_size_bytes(path: Path) -> int:
    """Sum of regular-file sizes directly under `path` (one level — the
    scratch buffer is flat). Tolerant of files vanishing mid-scan (the
    recorder deletes discarded segments concurrently)."""
    total = 0
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_file():
                        total += entry.stat().st_size
                except OSError:
                    continue  # raced with an eviction unlink
    except (FileNotFoundError, NotADirectoryError):
        return 0
    return total


def gc_summary() -> dict:
    """Cheap GC state (no full heap walk). `counts` are the per-generation
    allocation counters; a gen-2 count pinned near 0 with a huge threshold is
    the _gcwatch fingerprint (gen-2 effectively never auto-collects)."""
    counts = gc.get_count()
    threshold = gc.get_threshold()
    return {"counts": list(counts), "threshold": list(threshold),
            "frozen": gc.get_freeze_count()}


def type_histogram(objs: list | None = None) -> dict[str, int]:
    """Map of qualified-type-name -> live instance count over the GC-tracked
    heap (pass an already-fetched gc.get_objects() to share the one walk).
    The FULL histogram (hundreds of types); callers persist only the top-N.
    Qualified names (module.qualname) disambiguate same-named classes."""
    if objs is None:
        objs = gc.get_objects()
    c: Counter = Counter()
    for o in objs:
        t = type(o)
        c[f"{t.__module__}.{t.__qualname__}"] += 1
    return dict(c)


def sample(scratch_dir: Path | None = None, *, count_objects: bool = False,
           resources: bool = False, children_of: int | None = None,
           histogram: bool = False, gpu: bool = False,
           processes=None) -> dict:
    """One observability snapshot. Cheap by default (RSS + GC). Opt-in adds:
    `resources` (private bytes, handle/GDI/USER counts, threads, system memory
    — all O(1) syscalls); `count_objects`/`histogram` (a gc.get_objects() heap
    walk — the only O(heap) cost, so the periodic monitor sets it, on-demand
    /health does not); `children_of` (child-process memory snapshot); `gpu`
    (DXGI VRAM usage of THIS process); `processes` (iterable of exe names to
    measure, e.g. PJ64); `scratch_dir` (flat dir size)."""
    snap: dict = {"rss_bytes": rss_bytes(), "gc": gc_summary()}
    if resources:
        snap["private_bytes"] = private_bytes()
        snap.update(handle_counts())
        snap["threads"] = thread_count()
        snap["system"] = system_memory()
    if count_objects or histogram:
        objs = gc.get_objects()
        snap["objects"] = len(objs)
        if histogram:
            snap["types"] = type_histogram(objs)
    if children_of is not None:
        snap["children"] = child_memory(children_of)
    if gpu:
        snap["gpu"] = gpu_memory()
    if processes:
        snap["processes"] = named_process_memory(processes)
    if scratch_dir is not None:
        snap["scratch_bytes"] = dir_size_bytes(scratch_dir)
    return snap


# -- pure decision helpers (unit-tested) -------------------------------------

def assess_growth(baseline_rss: int, current_rss: int, *,
                  warn_ratio: float = 2.0,
                  warn_floor_bytes: int = 2 * _GiB) -> str | None:
    """Pure leak-alarm decision. Warn only when BOTH the process has at least
    doubled vs its post-startup baseline AND it now exceeds an absolute floor —
    so a tiny baseline doubling to still-tiny doesn't cry wolf, and a genuinely
    large working set does. Returns the warning text or None."""
    if baseline_rss <= 0 or current_rss <= 0:
        return None
    if current_rss >= warn_floor_bytes and current_rss >= baseline_rss * warn_ratio:
        return (f"RSS {current_rss / _GiB:.2f} GiB is "
                f"{current_rss / baseline_rss:.1f}x the startup baseline "
                f"{baseline_rss / _GiB:.2f} GiB — possible leak; check the "
                f"gc/objects trend in this log")
    return None


def top_type_growth(baseline: dict[str, int], current: dict[str, int],
                    n: int = 10) -> list[dict]:
    """The n type names that grew most since baseline, as
    [{type, baseline, current, delta}], delta-descending. Pure — THE Python-
    heap attribution signal ('numpy.ndarray +12000' names a frame-array leak).
    Types absent from baseline count as 0; shrinkage is filtered out."""
    deltas = []
    for name, cur in current.items():
        base = baseline.get(name, 0)
        d = cur - base
        if d > 0:
            deltas.append({"type": name, "baseline": base, "current": cur,
                           "delta": d})
    deltas.sort(key=lambda r: r["delta"], reverse=True)
    return deltas[:n]


# Each row: (key, getter(snap)->int|None, ratio, floor, unit). ratio=None means
# ABSOLUTE: alarm when current >= floor (child ffmpeg has a known healthy ceiling
# and starts AFTER baseline, so a baseline ratio would be skipped). Otherwise
# RELATIVE: alarm when current >= floor AND current >= ratio x baseline (the
# assess_growth shape, generalised). Floors are chosen so normal operation never
# trips: GDI/USER well under the 10000 cap, handles/threads above steady-state,
# child/private/rss above a healthy session.
_ALARM_SPECS = [
    ("rss_bytes", lambda s: s.get("rss_bytes"), 2.0, 2 * _GiB, "GiB"),
    ("private_bytes", lambda s: s.get("private_bytes"), 2.0, 2 * _GiB, "GiB"),
    ("handles", lambda s: s.get("handles"), 3.0, 5000, ""),
    ("gdi_objects", lambda s: s.get("gdi_objects"), 3.0, 3000, ""),
    ("user_objects", lambda s: s.get("user_objects"), 3.0, 3000, ""),
    ("threads", lambda s: s.get("threads"), 3.0, 60, ""),
    ("child_rss", lambda s: (s.get("children") or {}).get("rss_bytes"),
     None, 2 * _GiB, "GiB"),  # absolute: ffmpeg over 2 GiB is pathological
]


def resource_alarms(baseline: dict, current: dict) -> list[str]:
    """Every resource class currently breaching its leak threshold, as warning
    strings. Pure — the monitor fires each message at most once. Covers RSS,
    private bytes, kernel handles, GDI/USER objects, thread count, and child
    (ffmpeg) RSS, plus an absolute system-memory-pressure check (>=92% load).
    A flat-RSS run with climbing handles or child RSS still alarms — the whole
    point of the widening."""
    out: list[str] = []
    for key, get, ratio, floor, unit in _ALARM_SPECS:
        cur = get(current)
        if cur is None or cur <= 0 or cur < floor:
            continue
        if ratio is None:
            shown = f"{cur / _GiB:.2f} GiB" if unit == "GiB" else str(cur)
            out.append(f"{key} {shown} exceeds the {floor / _GiB:.0f} GiB "
                       f"ceiling — possible leak" if unit == "GiB"
                       else f"{key} {cur} exceeds the {floor} ceiling — "
                       f"possible leak")
            continue
        base = get(baseline)
        if base is None or base <= 0 or cur < base * ratio:
            continue
        if unit == "GiB":
            out.append(f"{key} {cur / _GiB:.2f} GiB is {cur / base:.1f}x "
                       f"the baseline {base / _GiB:.2f} GiB — possible leak")
        else:
            out.append(f"{key} {cur} is {cur / base:.1f}x the baseline "
                       f"{base} — possible leak")
    load = (current.get("system") or {}).get("load_pct")
    if load is not None and load >= 92:
        out.append(f"system memory load {load}% — the machine is near "
                   f"out-of-RAM; correlate which row above is climbing")
    return out
