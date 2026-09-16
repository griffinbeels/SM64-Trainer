"""Bound automatic-idle history without restarting its capture/encoder run.

The archive calls this on its media thread. Polling only swaps the immutable
idle descriptor; it never waits for file deletion. Each decision freezes the
published A/V frontier before reading that descriptor, so resume cannot move
an already-made deletion decision forward into the preserved lead-in.
"""
from collections import deque
from math import ceil


class IdleTail:
    def __init__(self, run, ring, window):
        self.run, self.ring, self.window = run, ring, window
        self._epoch = None
        self._candidates = deque()

    def maintain(self, frontier, *, closed=None):
        if self.window is None:
            return
        window = self.window()  # frontier was frozen by the caller first
        epoch = window[0] if window is not None else None
        if epoch != self._epoch:
            # The tail at resume becomes ordinary attempt history. Never
            # revisit its eligibility during a later idle period.
            self._candidates.clear()
            self._epoch = epoch
        if window is None:
            return
        if closed is not None and closed.units and closed.start >= self.run.ticks_at(epoch.timestamp()):
            self._candidates.append(closed)
        if frontier is None:
            return
        cutoff = frontier - ceil(max(0.0, window[1]) * 90000)
        # Leave a full predecessor extent for a GOP and crossing AAC packet,
        # plus every extent touching the lead-in and the current writer extent.
        while len(self._candidates) >= 2 and self._candidates[1].units[-1].end <= cutoff:
            current = self.window()
            if current != window:
                return  # resume, another idle epoch, or live padding change
            expired = self._candidates.popleft()
            self.ring.forget_temp(expired.group, delete=True)

    def prune(self, groups):
        self._candidates = deque(e for e in self._candidates if e.group in groups)
