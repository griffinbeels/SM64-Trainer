"""Nanosecond clock shared with native Windows QueryPerformanceCounter.

CPython's Windows perf_counter_ns scales raw QPC without rebasing its epoch.
Python 3.12's monotonic_ns uses GetTickCount64 instead; its values cannot be
compared with native QPC timestamps. Keep the live capture and room on this
binding; archive and wall-clock timestamps have their own contracts.
"""
from time import perf_counter_ns as qpc_ns

__all__ = ["qpc_ns"]
