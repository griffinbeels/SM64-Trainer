"""The media worker's tick wait: wake on demand, not on Windows' 15.6 ms timer.

`threading.Event.wait(0.004)` sleeps through `WaitForSingleObjectEx`, which
Windows quantizes to the default 15.6 ms timer tick: measured 15.45 ms median
for a 4 ms request on this machine (round 48). Every picture crosses that wait
three times (decision, submit, receipt), so the eight native source slots
(266 ms at 30 fps) drained in ~50 ms of Python latency per picture and any
short stall overflowed them. The same trap is documented for the legacy
capture loop in `video.py`, which already uses the high-resolution waitable
timer; this is that primitive for the GPU path, plus the native producer's
own event so a published offer or packet wakes the worker at once.
"""

from __future__ import annotations

import ctypes
import sys
import threading

_CREATE_WAITABLE_TIMER_HIGH_RESOLUTION = 0x00000002
_TIMER_ALL_ACCESS = 0x1F0003
_WAIT_FAILED = 0xFFFFFFFF
_MAX_WATCHED = 4


class TickWaiter:
    """set()/clear()/wait() like a `threading.Event`, with a high-resolution
    timeout and optional native event handles that also end the wait."""

    def __init__(self):
        self._event = threading.Event()  # portable fallback and the "set" flag
        self._k = None
        self._wake = self._timer = None
        self._watched: list[int] = []
        self._lock = threading.Lock()
        if sys.platform != "win32":
            return
        k = ctypes.windll.kernel32
        k.CreateEventW.restype = ctypes.c_void_p
        k.CreateWaitableTimerExW.restype = ctypes.c_void_p
        k.CreateWaitableTimerExW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                            ctypes.c_uint32, ctypes.c_uint32]
        k.SetWaitableTimer.restype = ctypes.c_int
        k.SetWaitableTimer.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_longlong),
                                       ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p,
                                       ctypes.c_int]
        k.WaitForMultipleObjects.restype = ctypes.c_uint32
        k.WaitForMultipleObjects.argtypes = [ctypes.c_uint32, ctypes.c_void_p,
                                             ctypes.c_int, ctypes.c_uint32]
        k.SetEvent.argtypes = [ctypes.c_void_p]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        wake = k.CreateEventW(None, False, False, None)  # auto-reset
        timer = k.CreateWaitableTimerExW(None, None, _CREATE_WAITABLE_TIMER_HIGH_RESOLUTION,
                                         _TIMER_ALL_ACCESS)
        if not timer:
            k.CreateWaitableTimerW.restype = ctypes.c_void_p
            timer = k.CreateWaitableTimerW(None, False, None)
        if wake and timer:
            self._k, self._wake, self._timer = k, wake, timer
        else:
            for handle in (wake, timer):
                if handle:
                    k.CloseHandle(handle)

    @property
    def high_resolution(self) -> bool:
        return self._k is not None

    def watch(self, handle) -> None:
        """Also wake when `handle` (a native auto-reset event) is signalled."""
        if not handle:
            return
        with self._lock:
            if handle not in self._watched and len(self._watched) < _MAX_WATCHED:
                self._watched.append(int(handle))

    def unwatch(self, handle) -> None:
        with self._lock:
            if handle in self._watched:
                self._watched.remove(handle)

    def set(self) -> None:
        self._event.set()
        if self._k is not None:
            self._k.SetEvent(self._wake)

    def clear(self) -> None:
        self._event.clear()

    def is_set(self) -> bool:
        return self._event.is_set()

    def wait(self, period: float) -> None:
        """Block until set(), a watched native event, or `period` seconds."""
        if self._k is None or self._event.is_set():
            self._event.wait(period)
            self._event.clear()
            return
        period = max(0.0, float(period))
        due = ctypes.c_longlong(-int(period * 10_000_000))
        self._k.SetWaitableTimer(self._timer, ctypes.byref(due), 0, None, None, False)
        with self._lock:
            handles = [self._wake, self._timer, *self._watched]
        array = (ctypes.c_void_p * len(handles))(*handles)
        result = self._k.WaitForMultipleObjects(len(handles), array, False,
                                                int(period * 1000) + 50)
        if result == _WAIT_FAILED:
            # A watched handle went away (its channel closed): stop watching it
            # and take the portable wait so a closed handle cannot spin us.
            with self._lock:
                self._watched.clear()
            self._event.wait(period)
        self._event.clear()

    def close(self) -> None:
        k, self._k = self._k, None
        if k is None:
            return
        for handle in (self._wake, self._timer):
            if handle:
                k.CloseHandle(handle)
        self._wake = self._timer = None
