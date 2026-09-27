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

The 2026-09-27 widening answers who burns CPU, not only who holds memory: a
machine-wide stall on 09-25 (Project64 frames up to 3.5 s, a 4.8 s server GC,
three failed recordings) could not be pinned on any process, because nothing
here looked past this server, its direct children and Project64. One
machine_snapshot() now reads every process and this server's threads, so the
sampler can name the busiest thread here, the whole helper tree, the machine's
top CPU/memory/disk consumers, and how much address space the 32-bit
Project64 has left (address_space()).

All probes are pure-ctypes (no third-party deps, the codebase's Windows idiom)
and degrade to 0 / {} off-Windows or on probe failure — the monitor never
crashes the server. The periodic sampler that consumes these lives in
core/perfmon.py; the pure decision helpers here are unit-tested."""
import ctypes
import gc
import logging
import os
import re
import struct
import threading
import time
from collections import Counter
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, NamedTuple

from sm64_events.core.heap_observation import heap_snapshot

log = logging.getLogger("sm64.procmem")

_GiB = 1024 ** 3
_MiB = 1024 ** 2


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


def _bind():
    """Bind every Win32 entry point ONCE with explicit argtypes/restypes.
    WITHOUT argtypes, ctypes defaults pointer args to c_int and truncates
    them to 32 bits on 64-bit Python, so byref() writes nowhere and the call
    silently no-ops (the bug that made an earlier RSS probe always read 0).
    Returns a flat namespace, or an all-None one off-Windows."""
    ns = type("WinAPI", (), {})()
    names = ("gpmi", "getcur", "getpid_handle", "handlecount", "guiresources",
             "memstatus", "openproc", "closehandle")
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


# -- one machine-wide process snapshot ---------------------------------------
# NtQuerySystemInformation(SystemProcessInformation) returns every process's
# CPU time, commit, working set, thread/handle counts, hard faults and I/O, and
# every thread's CPU time, in ONE call that opens no process. Measured on his
# machine (2026-09-27, 426 processes, 12,247 threads): 8.5 ms, against 12-13 ms
# for the Toolhelp32 pass it replaced, which walks the same kernel table and
# then opens each matched process. The layouts below are the 64-bit ones.
_SPI_PROCESS = struct.Struct(  # SYSTEM_PROCESS_INFORMATION, 0x100 bytes
    "<IIqII Qqqq HH4xQ i4x QQ II Q QQ I4x QQ QQQQ QQQ QQQ QQQ")
_SPI_THREAD = struct.Struct("<qqqI4xQQQiiIII4x")  # SYSTEM_THREAD_INFORMATION
_SPPI = struct.Struct("<qqqqqI4x")  # SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION
_SYSTEM_PROCESS_INFORMATION = 5
_SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION = 8
_STATUS_INFO_LENGTH_MISMATCH = 0xC0000004
_TICKS_PER_S = 10_000_000  # the kernel's 100 ns time unit


def _bind_nt():
    """ntdll/kernel32 entry points for the snapshot, the thread labels and the
    address-space walk, with explicit argtypes for the reason _bind() gives."""
    ns = type("NtAPI", (), {})()
    try:
        nt = ctypes.WinDLL("ntdll")
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        for name, lib, fn, args, res in (
            ("query", nt, "NtQuerySystemInformation",
             [wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG,
              ctypes.POINTER(wintypes.ULONG)], ctypes.c_long),
            ("thread_info", nt, "NtQueryInformationThread",
             [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.ULONG,
              ctypes.c_void_p], ctypes.c_long),
            ("open_thread", k32, "OpenThread",
             [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            ("thread_desc", k32, "GetThreadDescription",
             [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)], ctypes.c_long),
            ("local_free", k32, "LocalFree", [ctypes.c_void_p], ctypes.c_void_p),
            ("module_from", k32, "GetModuleHandleExW",
             [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HMODULE)],
             wintypes.BOOL),
            ("module_name", k32, "GetModuleFileNameW",
             [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD], wintypes.DWORD),
            ("vquery", k32, "VirtualQueryEx",
             [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t],
             ctypes.c_size_t),
            ("is_wow64", k32, "IsWow64Process",
             [wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            ("image_name", k32, "QueryFullProcessImageNameW",
             [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
              ctypes.POINTER(wintypes.DWORD)], wintypes.BOOL),
        ):
            f = getattr(lib, fn)
            f.argtypes, f.restype = args, res
            setattr(ns, name, f)
        ns.ok = ctypes.sizeof(ctypes.c_void_p) == 8  # the parser reads x64 layouts
    except (AttributeError, OSError):  # not Windows
        ns.ok = False
    return ns


_NT = _bind_nt()


class ProcInfo(NamedTuple):
    """One process in a MachineSnapshot. Times are the kernel's 100 ns units."""
    pid: int
    ppid: int
    exe: str
    created: int          # distinguishes a reused pid from the original
    cpu: int              # user + kernel time since the process started
    private_bytes: int    # commit charge: the figure private_bytes() reports
    rss_bytes: int        # working set
    virtual_bytes: int
    threads: int
    handles: int
    hard_faults: int      # page faults that had to wait for the disk
    io_bytes: int         # read + write transfer bytes


@dataclass(frozen=True)
class MachineSnapshot:
    """Every process on the machine at one instant. `threads` holds per-thread
    {tid: (created, cpu)} only for the pids the caller asked for; `cpus` is
    (idle, total, dpc, interrupt) time summed over the logical CPUs."""
    t: float
    procs: dict
    threads: dict
    cpus: tuple


_snapshot_lock = threading.Lock()  # one reused buffer (~1.3 MB on his machine)
_snapshot_buf = None


def _parse_processes(view, base: int, thread_pids) -> tuple[dict, dict]:
    procs: dict = {}
    threads: dict = {}
    off = 0
    while True:
        f = _SPI_PROCESS.unpack_from(view, off)
        pid, name_len, name_ptr = f[13], f[9], f[11]
        start = name_ptr - base
        exe = (bytes(view[start:start + name_len]).decode("utf-16-le", "replace")
               if name_ptr and 0 <= start < len(view) else
               ("Idle" if pid == 0 else "?"))
        procs[pid] = ProcInfo(pid, f[14], exe, f[6], f[7] + f[8], f[27], f[22],
                              f[19], f[1], f[15], f[3], f[33] + f[34])
        if pid in thread_pids:
            first = off + _SPI_PROCESS.size
            rows = (_SPI_THREAD.unpack_from(view, first + i * _SPI_THREAD.size)
                    for i in range(f[1]))
            threads[pid] = {r[6]: (r[2], r[0] + r[1]) for r in rows}
        if not f[0]:
            return procs, threads
        off += f[0]


def _cpu_times() -> tuple:
    """(idle, total, dpc, interrupt) summed over this processor group's CPUs.
    DPC and interrupt time belong to no process: a driver storm shows here."""
    buf = ctypes.create_string_buffer(_SPPI.size * 64)
    got = wintypes.ULONG(0)
    if _NT.query(_SYSTEM_PROCESSOR_PERFORMANCE_INFORMATION, buf, len(buf),
                 ctypes.byref(got)) != 0:
        return (0, 0, 0, 0)
    idle = total = dpc = interrupt = 0
    for i in range(got.value // _SPPI.size):
        f = _SPPI.unpack_from(buf, i * _SPPI.size)
        idle, total = idle + f[0], total + f[1] + f[2]  # kernel time includes idle
        dpc, interrupt = dpc + f[3], interrupt + f[4]
    return (idle, total, dpc, interrupt)


def machine_snapshot(thread_pids=()) -> MachineSnapshot | None:
    """Every process on the machine, plus the per-thread CPU of `thread_pids`,
    from one query-only syscall. None off Windows or if the query fails."""
    global _snapshot_buf
    if not _NT.ok:
        return None
    with _snapshot_lock:
        got = wintypes.ULONG(0)
        for _ in range(4):  # the table can grow between the size probe and the read
            if _snapshot_buf is None:
                _snapshot_buf = ctypes.create_string_buffer(
                    max(got.value * 5 // 4, 1 << 20))
            status = _NT.query(_SYSTEM_PROCESS_INFORMATION, _snapshot_buf,
                               len(_snapshot_buf), ctypes.byref(got)) & 0xFFFFFFFF
            if status == 0:
                break
            if status != _STATUS_INFO_LENGTH_MISMATCH:
                log.warning("process snapshot failed: NTSTATUS 0x%08X", status)
                return None
            _snapshot_buf = None
        else:
            return None
        taken = time.perf_counter()
        try:
            procs, threads = _parse_processes(
                memoryview(_snapshot_buf)[:got.value],
                ctypes.addressof(_snapshot_buf), set(thread_pids))
        except struct.error:
            log.exception("process snapshot unreadable")
            return None
    return MachineSnapshot(taken, procs, threads, _cpu_times())


def _since(prev: MachineSnapshot | None, p: ProcInfo, field: str) -> int:
    """How much a cumulative counter of `p` grew since `prev`. A process born
    after `prev` (or a reused pid) counts from zero."""
    q = prev.procs.get(p.pid) if prev is not None else None
    now = getattr(p, field)
    return now - getattr(q, field) if q is not None and q.created == p.created else now


def _core_pct(ticks: int, window_s: float) -> float:
    """100 ns ticks over a window, as percent of ONE core (a process busy on
    four cores reads 400)."""
    return round(ticks * 100 / _TICKS_PER_S / window_s, 1)


def descendants(snap: MachineSnapshot, root_pid: int, sticky=()) -> list:
    """Every process below root_pid, however deep: the encoder helper is a
    grandchild (the venv launcher sits between), and a direct-children count
    reported that launcher's 0.8 MiB instead of the helper's ~189 MiB. A child
    created before its supposed parent holds a reused pid and is someone
    else's. `sticky` is (pid, created) pairs seen in the tree earlier: a helper
    whose launcher exited is still ours while it lives."""
    kids: dict = {}
    for p in snap.procs.values():
        kids.setdefault(p.ppid, []).append(p)
    root = snap.procs.get(root_pid)
    out: list = []
    seen = {root_pid}
    stack = [(root_pid, root.created if root else 0)]
    for pid, created in sticky:
        p = snap.procs.get(pid)
        if p is not None and p.created == created and pid not in seen:
            seen.add(pid)
            out.append(p)
            stack.append((pid, created))
    while stack:
        parent, born = stack.pop()
        for c in kids.get(parent, ()):
            if c.pid not in seen and c.created >= born:
                seen.add(c.pid)
                out.append(c)
                stack.append((c.pid, c.created))
    return out


def tree_summary(members: list, prev: MachineSnapshot | None = None,
                 curr: MachineSnapshot | None = None, *, rows: int = 12) -> dict:
    """Totals over a process tree plus one row per member naming its image
    (largest commit first). With two snapshots each row carries its CPU share
    of the window between them."""
    by_name: Counter = Counter()
    for p in members:
        by_name[p.exe] += p.rss_bytes
    window = curr.t - prev.t if prev is not None and curr is not None else 0
    listed = []
    for p in sorted(members, key=lambda m: m.private_bytes, reverse=True)[:rows]:
        row = {"exe": p.exe, "pid": p.pid, "ppid": p.ppid,
               "private_mib": round(p.private_bytes / _MiB, 1),
               "rss_mib": round(p.rss_bytes / _MiB, 1), "threads": p.threads}
        if window > 0:
            row["cpu_pct"] = _core_pct(_since(prev, p, "cpu"), window)
        listed.append(row)
    return {"count": len(members),
            "rss_bytes": sum(p.rss_bytes for p in members),
            "private_bytes": sum(p.private_bytes for p in members),
            "threads": sum(p.threads for p in members),
            "by_name": dict(by_name), "members": listed}


def named_processes(snap: MachineSnapshot, names,
                    prev: MachineSnapshot | None = None) -> dict:
    """{exe: {count, rss_bytes, private_bytes, threads[, cpu_pct]}} for the
    processes whose image matches `names` (case-insensitive)."""
    wanted = {n.lower() for n in names}
    window = snap.t - prev.t if prev is not None else 0
    out: dict = {}
    for p in snap.procs.values():
        if p.exe.lower() not in wanted:
            continue
        slot = out.setdefault(p.exe, {"count": 0, "rss_bytes": 0,
                                      "private_bytes": 0, "threads": 0})
        slot["count"] += 1
        slot["rss_bytes"] += p.rss_bytes
        slot["private_bytes"] += p.private_bytes
        slot["threads"] += p.threads
        if window > 0:
            slot["cpu_pct"] = round(slot.get("cpu_pct", 0)
                                    + _core_pct(_since(prev, p, "cpu"), window), 1)
    return out


def machine_summary(prev: MachineSnapshot, curr: MachineSnapshot,
                    n: int = 5) -> dict:
    """Who used the machine between two snapshots: CPU busy/DPC/interrupt share
    of all logical CPUs, and the top `n` processes by CPU (percent of one
    core), commit, hard faults and disk bytes. The attribution a stall report
    needs and the monitor itself could not give on 2026-09-25."""
    window = curr.t - prev.t
    if window <= 0:
        return {}
    out: dict = {"window_s": round(window, 1)}
    idle, total, dpc, interrupt = (c - b for c, b in zip(curr.cpus, prev.cpus,
                                                         strict=True))
    if total > 0:
        out.update(busy_pct=round(100 * (total - idle) / total, 1),
                   dpc_pct=round(100 * dpc / total, 1),
                   interrupt_pct=round(100 * interrupt / total, 1))
    procs = [p for p in curr.procs.values() if p.pid != 0]  # 0 = idle time

    def top(field: str, key: str, scale):
        grown = [(p, _since(prev, p, field)) for p in procs]
        grown.sort(key=lambda r: r[1], reverse=True)
        return [{"exe": p.exe, "pid": p.pid, key: scale(v)}
                for p, v in grown[:n] if v > 0]

    out["top_cpu"] = top("cpu", "cpu_pct", lambda v: _core_pct(v, window))
    out["top_private"] = [
        {"exe": p.exe, "pid": p.pid, "private_mib": round(p.private_bytes / _MiB)}
        for p in sorted(procs, key=lambda p: p.private_bytes, reverse=True)[:n]]
    out["top_hard_faults"] = top("hard_faults", "hard_faults", int)
    out["top_io"] = top("io_bytes", "io_mib", lambda v: round(v / _MiB, 1))
    return out


# Python pool/anonymous thread names carry an index that changes with every
# worker; grouping them keeps one row per role across samples.
_ANON_THREAD = re.compile(r"^(Thread|Dummy)-\d+")
_POOL_INDEX = re.compile(r"(?:_\d+)+$")


def thread_group(name: str) -> str:
    """'asyncio_3' -> 'asyncio', 'Thread-12 (run)' -> 'Thread-N (run)'."""
    return _POOL_INDEX.sub("", _ANON_THREAD.sub(r"\1-N", name))


def thread_cpu(prev: MachineSnapshot, curr: MachineSnapshot, pid: int,
               label: Callable[[int, int], str], *, top: int = 15) -> dict:
    """One process's CPU between two snapshots, per thread NAME, in percent of
    one core. label(tid, created) names each live thread; names are grouped by
    thread_group. A thread that exited inside the window took its CPU with it,
    so the gap to the process total is reported as unattributed_pct rather
    than spread over the survivors. {} when either snapshot lacks the
    process's threads."""
    window = curr.t - prev.t
    proc = curr.procs.get(pid)
    if window <= 0 or proc is None or pid not in prev.threads \
            or pid not in curr.threads:
        return {}
    before = prev.threads[pid]
    groups: Counter = Counter()
    for tid, (created, cpu) in curr.threads[pid].items():
        was = before.get(tid)
        grew = cpu - was[1] if was is not None and was[0] == created else cpu
        groups[thread_group(label(tid, created))] += grew
    ranked = groups.most_common()
    named = {name: _core_pct(v, window) for name, v in ranked[:top] if v > 0}
    rest = sum(v for _, v in ranked[top:])
    if rest > 0:
        named["(other)"] = _core_pct(rest, window)
    total = _since(prev, proc, "cpu")
    return {"window_s": round(window, 1), "process_pct": _core_pct(total, window),
            "threads": named,
            "unattributed_pct": _core_pct(max(total - sum(groups.values()), 0),
                                          window)}


def native_thread_label(tid: int) -> str:
    """Name a thread of THIS process that Python did not create: the
    description a library gave it, else the module its start address lies in
    ('native:nvcuda64.dll'). Only meaningful for this process's own threads —
    the module lookup resolves addresses in our address space."""
    if not _NT.ok:
        return "native"
    h = _NT.open_thread(0x0840, False, tid)  # QUERY_INFORMATION | QUERY_LIMITED
    if not h:
        return "native"
    try:
        text = ctypes.c_void_p()
        if _NT.thread_desc(h, ctypes.byref(text)) >= 0 and text.value:
            try:
                desc = ctypes.wstring_at(text.value)
            finally:
                _NT.local_free(text)
            if desc:
                return desc
        start = ctypes.c_void_p()
        module = wintypes.HMODULE()
        if _NT.thread_info(h, 9, ctypes.byref(start), ctypes.sizeof(start),
                           None) != 0 or not start.value \
                or not _NT.module_from(0x6, start.value, ctypes.byref(module)):
            return "native"   # 9 = ThreadQuerySetWin32StartAddress
        path = ctypes.create_unicode_buffer(260)
        if not _NT.module_name(module, path, 260):
            return "native"
        return "native:" + os.path.basename(path.value)
    finally:
        _API.closehandle(h)


# -- address space headroom (VirtualQueryEx, query-only) ---------------------
# Project64 1.6 is 32-bit and not large-address-aware: 2 GiB of address space
# however much RAM the machine has. On 09-25 with capture and one ghost it had
# 306 MiB free and a largest free block of 145.5 MiB while the ghost clip
# budget is 192 MiB; its working set (495 MiB) hid all of that.
class _MBI(ctypes.Structure):  # MEMORY_BASIC_INFORMATION (64-bit caller)
    _fields_ = [("BaseAddress", ctypes.c_uint64),
                ("AllocationBase", ctypes.c_uint64),
                ("AllocationProtect", wintypes.DWORD),
                ("PartitionId", wintypes.WORD),
                ("RegionSize", ctypes.c_uint64),
                ("State", wintypes.DWORD), ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD)]


_MEM_FREE = 0x10000
_PROCESS_QUERY_INFORMATION = 0x400
_laa_cache: dict = {}  # (image path, mtime) -> large-address-aware flag


def image_is_large_address_aware(path: str) -> bool:
    """The IMAGE_FILE_LARGE_ADDRESS_AWARE bit (0x20) of an exe's PE file
    header, read from disk and cached until the file changes. False when
    unreadable."""
    try:
        key = (path, os.stat(path).st_mtime_ns)
        if key not in _laa_cache:
            with open(path, "rb") as f:
                head = f.read(4096)
            pe = struct.unpack_from("<I", head, 0x3C)[0]  # e_lfanew
            if head[pe:pe + 4] != b"PE\0\0":
                return False
            # "PE\0\0", then Machine, NumberOfSections, TimeDateStamp,
            # PointerToSymbolTable, NumberOfSymbols, SizeOfOptionalHeader.
            flags = struct.unpack_from("<H", head, pe + 22)[0]
            _laa_cache.clear()
            _laa_cache[key] = bool(flags & 0x20)
        return _laa_cache[key]
    except (OSError, struct.error):
        return False


def _large_address_aware(handle) -> bool:
    path = ctypes.create_unicode_buffer(1024)
    size = wintypes.DWORD(1024)
    if not _NT.image_name(handle, 0, path, ctypes.byref(size)):
        return False
    return image_is_large_address_aware(path.value)


def _address_limit(handle) -> int | None:
    """Top of the user address space: 2 GiB for a 32-bit process (4 GiB if
    large-address-aware), the system maximum for a 64-bit one."""
    wow = wintypes.BOOL(False)
    if not _NT.is_wow64(handle, ctypes.byref(wow)):
        return None
    if wow.value:
        return 4 * _GiB if _large_address_aware(handle) else 2 * _GiB
    return 1 << 47  # x64 user space; the walk stops where VirtualQueryEx does


def address_space(pid: int, *, only_32bit: bool = False) -> dict:
    """How much address space a process has used and has left: {vm_limit_mib,
    vm_used_mib (reserved + committed), vm_free_mib, vm_largest_free_mib,
    vm_regions, vm_walk_ms}. The largest free block is what a new big mapping
    (a ghost clip, a capture slot) needs. Walks every region with
    VirtualQueryEx under PROCESS_QUERY_INFORMATION: nothing is read or
    written. Measured 1.9-2.5 us per region on busy 32-bit apps and 6-11 us
    on idle services (2026-09-27); Project64 has ~1,100 regions, and
    vm_walk_ms records what its walk really cost. {} off Windows, without
    access, or for a 64-bit process when only_32bit (its headroom is not a
    constraint)."""
    if not _NT.ok:
        return {}
    started = time.perf_counter()
    h = _API.openproc(_PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        return {}
    try:
        limit = _address_limit(h)
        if limit is None or (only_32bit and limit > 4 * _GiB):
            return {}
        mbi = _MBI()
        ref, size, query = ctypes.byref(mbi), ctypes.sizeof(mbi), _NT.vquery
        addr = free = largest = regions = 0
        while addr < limit and query(h, addr, ref, size):
            end = min(mbi.BaseAddress + mbi.RegionSize, limit)
            if mbi.State == _MEM_FREE:
                free += end - mbi.BaseAddress
                largest = max(largest, end - mbi.BaseAddress)
            regions += 1
            addr = end
    finally:
        _API.closehandle(h)
    if not regions:
        return {}
    return {"vm_limit_mib": round(limit / _MiB, 1),
            "vm_used_mib": round((addr - free) / _MiB, 1),
            "vm_free_mib": round(free / _MiB, 1),
            "vm_largest_free_mib": round(largest / _MiB, 1),
            "vm_regions": regions,
            "vm_walk_ms": round((time.perf_counter() - started) * 1000, 2)}


def process_table(parent_pid: int | None = None, names=None, *,
                  snapshot: MachineSnapshot | None = None) -> dict:
    """ONE machine snapshot answering BOTH the whole process tree below
    parent_pid AND the processes whose exe matches `names` (case-insensitive).
    Returns {'children': tree_summary(...), 'processes': named_processes(...)}
    with only the requested keys present; {} off Windows or when nothing was
    requested. Pass `snapshot` to reuse one already taken."""
    if parent_pid is None and not names:
        return {}
    snap = snapshot or machine_snapshot()
    if snap is None:
        return {}
    out: dict = {}
    if parent_pid is not None:
        out["children"] = tree_summary(descendants(snap, parent_pid))
    if names:
        out["processes"] = named_processes(snap, names)
    return out


def child_memory(parent_pid: int) -> dict:
    """Summed working-set + private bytes of every process below parent_pid
    (the encoder helper tree). {count, rss_bytes, private_bytes, threads,
    by_name, members}; {} off Windows. Thin wrapper over process_table."""
    return process_table(parent_pid=parent_pid).get("children", {})


def named_process_memory(names) -> dict:
    """Working-set + private bytes of processes whose exe matches `names`
    (case-insensitive), keyed by exe. THE probe for 'is the growth PJ64's OWN,
    not ours?' (PJ64 is not our child). {exe: {count, rss_bytes, private_bytes,
    threads}}; {} off Windows. Thin wrapper over process_table (one snapshot)."""
    return process_table(names=names).get("processes", {})


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
    shows up. Cheap; no syscall. Native threads (19 Python of 52 OS threads
    live on 09-25) are only in a MachineSnapshot's thread count."""
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
    heap (pass a borrowed snapshot to share the one walk; its owner is
    responsible for coordinating resource lifetime until that list is released).
    The FULL histogram (hundreds of types); callers persist only the top-N.
    Qualified names (module.qualname) disambiguate same-named classes."""
    if objs is None:
        with heap_snapshot() as objects:
            return type_histogram(objects)
    c: Counter = Counter()
    o = None
    try:
        for o in objs:
            t = type(o)
            c[f"{t.__module__}.{t.__qualname__}"] += 1
    finally:
        o = None  # A failed probe's traceback must not retain a resource.
    return dict(c)


def sample(scratch_dir: Path | None = None, *, count_objects: bool = False,
           resources: bool = False, children_of: int | None = None,
           histogram: bool = False, gpu: bool = False,
           processes=None) -> dict:
    """One observability snapshot. Cheap by default (RSS + GC). Opt-in adds:
    `resources` (private bytes, handle/GDI/USER counts, threads, system memory
    — all O(1) syscalls); `count_objects`/`histogram` (a gc.get_objects() heap
    walk — only deliberate deep diagnostics enable it); `children_of`
    (child-process memory snapshot); `gpu`
    (DXGI VRAM usage of THIS process); `processes` (iterable of exe names to
    measure, e.g. PJ64); `scratch_dir` (flat dir size)."""
    snap: dict = {"rss_bytes": rss_bytes(), "gc": gc_summary()}
    if resources:
        snap["private_bytes"] = private_bytes()
        snap.update(handle_counts())
        snap["threads"] = thread_count()
        snap["system"] = system_memory()
    if count_objects or histogram:
        with heap_snapshot() as objs:
            snap["objects"] = len(objs)
            if histogram:
                snap["types"] = type_histogram(objs)
    if children_of is not None or processes:
        pt = process_table(parent_pid=children_of, names=processes)  # ONE pass
        if children_of is not None:
            snap["children"] = pt.get("children", {})
        if processes:
            snap["processes"] = pt.get("processes", {})
    if gpu:
        snap["gpu"] = gpu_memory()
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
