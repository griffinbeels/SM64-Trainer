"""Hearing that the stacking order moved, instead of waiting for a tick.

**Why this exists.** The ghost is not topmost -- it is inserted directly above
Project64, which is what makes "keep it up while he clicks another app" safe:
whatever he raises over the emulator is over the ghost too. The cost of that
choice is that ACTIVATING Project64 puts the emulator back on top of the ghost,
so the ghost is behind the game until something notices and re-inserts it. The
follower notices on its next tick, and at a 100 ms tick that is a blink he can
see -- the exact complaint ("it blinks in / out") this round is fixing.

**Why a hook and not a faster tick.** Every z-order check is a user32 query
against Project64, and a user32 query against Project64 can block on its UI
thread's window lock for 110-170 ms (`replay/video.py`). Polling at frame rate
would multiply that traffic sixfold for an event that happens when he clicks,
which is to say almost never. A `SetWinEventHook` costs nothing while nothing
happens and fires when something does.

**Which events, and why both.** `EVENT_SYSTEM_FOREGROUND` is his actual
gesture -- clicking Project64. `EVENT_OBJECT_REORDER` is the wider class: any
window moved in the stacking order without the foreground changing, which is
also the only half a rig can generate without stealing focus, so it is the
half that can be measured.

**What this thread may do, and what it may not.** It pumps messages and sets an
event. It never touches a window: DPI awareness is per thread and every user32
write in this package belongs to the follower, so the repair happens there, on
the one thread that is pinned. Hook callbacks arrive on whichever thread
installed the hook, and the follower cannot pump a message queue while it is
blocked inside a query against Project64 -- so the hook gets its own thread and
hands over through the follower's existing wake event.
"""
import ctypes
import ctypes.wintypes as wt
import logging
import threading
import time

log = logging.getLogger("sm64.ghost")

#: The foreground window changed -- his click on Project64.
EVENT_SYSTEM_FOREGROUND = 0x0003
#: Something was moved in the z-order. Broader, and the one a rig can provoke.
EVENT_OBJECT_REORDER = 0x8004

#: The callback runs in OUR process, on the thread that installed the hook --
#: no DLL is injected into anybody else's. SKIPOWNPROCESS keeps the overlay's
#: own placements from waking the follower that just made them.
WINEVENT_OUTOFCONTEXT = 0x0000
WINEVENT_SKIPOWNPROCESS = 0x0002

PM_REMOVE = 0x0001
QS_ALLINPUT = 0x04FF

#: How long the pump waits before looking at `stop` again. Only teardown
#: promptness depends on it: an event arrives during the wait, not after it.
PUMP_TIMEOUT_MS = 100

#: The closest two wakes may be. A burst of reorders (a list view shuffling its
#: own children) would otherwise drive the follower's full tick -- which reads
#: Project64 -- as fast as the events arrive. One display frame is under what
#: he can see and bounds the worst case to about 60 ticks a second.
MIN_WAKE_GAP_S = 0.016

_WINEVENTPROC = ctypes.WINFUNCTYPE(None, ctypes.c_void_p, wt.DWORD, wt.HWND,
                                   wt.LONG, wt.LONG, wt.DWORD, wt.DWORD)


class ReorderWatch:
    """Wakes the follower when the stacking order may have moved under it.

    Pure enough to test without Windows: `notice()` is the whole decision, and
    `run()` is the ctypes boundary that calls it.
    """

    def __init__(self, wake: threading.Event, *, clock=time.monotonic,
                 min_gap_s: float = MIN_WAKE_GAP_S, ops=None):
        self._wake = wake
        self._clock = clock
        self._min_gap = min_gap_s
        self._ops = ops
        self._last: float | None = None
        self.woken = 0
        self.coalesced = 0
        self.installed: bool | None = None

    def notice(self) -> bool:
        """One event heard. True when the follower was actually woken."""
        now = self._clock()
        if self._last is not None and now - self._last < self._min_gap:
            self.coalesced += 1
            return False
        self._last = now
        self.woken += 1
        self._wake.set()
        return True

    def run(self, stop: threading.Event) -> None:
        """Install the hook, pump until asked to stop, then remove it.

        Never raises: a machine that refuses the hook must still get the
        follower's 100 ms tick, which is the behaviour this replaces rather
        than depends on.
        """
        user32 = ctypes.windll.user32 if self._ops is None else self._ops

        def heard(*_args) -> None:
            self.notice()

        callback = _WINEVENTPROC(heard)
        hooks: list[int] = []
        try:
            for event in (EVENT_SYSTEM_FOREGROUND, EVENT_OBJECT_REORDER):
                hooks.append(int(user32.SetWinEventHook(
                    event, event, None, callback, 0, 0,
                    WINEVENT_OUTOFCONTEXT | WINEVENT_SKIPOWNPROCESS) or 0))
        except Exception as error:  # noqa: BLE001 - the tick is the fallback
            log.warning("ghost overlay reorder hook refused: %s", error)
            self.installed = False
            return
        self.installed = bool(hooks) and all(hooks)
        message = wt.MSG()
        try:
            while not stop.is_set():
                # Waits in a state that lets Windows deliver the callback, and
                # returns the moment it does rather than at the timeout.
                user32.MsgWaitForMultipleObjects(0, None, False,
                                                 PUMP_TIMEOUT_MS, QS_ALLINPUT)
                while user32.PeekMessageW(ctypes.byref(message), None, 0, 0,
                                          PM_REMOVE):
                    user32.DispatchMessageW(ctypes.byref(message))
        finally:
            for hook in hooks:
                if hook:
                    user32.UnhookWinEvent(hook)
