"""A recording's AAC filter graph runs on the media worker, not a thread pool.

libav's default gives a filter graph one worker per core. The mux only
resamples one stereo stream, so those workers idled for the whole run while
the server carried 15 extra OS threads per recording (measured 2026-09-25 on a
32-thread CPU). Counted through Toolhelp, the OS's own list, because libav's
threads are invisible to `threading`.
"""
import ctypes as C
import io
import os
import sys
from ctypes import wintypes as W

import pytest

from sm64_events.replay.gpuprocess.process_resume import ThreadEntry
from sm64_events.replay.media import MediaRun
from sm64_events.replay.packetmux import NativeFormat, PacketFragmentMux

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Toolhelp is Windows")


def os_threads() -> int:
    kernel = C.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.restype = W.HANDLE
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)  # TH32CS_SNAPTHREAD
    assert snapshot not in (None, 0, C.c_void_p(-1).value), C.get_last_error()
    try:
        entry = ThreadEntry()
        entry.size = C.sizeof(entry)
        count = 0
        more = kernel.Thread32First(snapshot, C.byref(entry))
        while more:
            count += entry.owner_pid == os.getpid()
            entry.size = C.sizeof(entry)
            more = kernel.Thread32Next(snapshot, C.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return count


def test_building_a_recordings_mux_starts_no_filter_thread_pool():
    before = os_threads()
    mux = PacketFragmentMux(io.BytesIO(), NativeFormat("h264", 320, 240, 30),
                            MediaRun("thread-count", 1000.0), audio_rate=48000,
                            audio_bitrate=160000, packet_limit=1 << 20,
                            pcm_limit=384000)
    try:
        grown = os_threads() - before
    finally:
        mux.abort()
    # 0 is the expectation; 1 tolerates an unrelated thread starting meanwhile.
    assert grown <= 1, f"{grown} OS threads started by one mux's filter graph"
