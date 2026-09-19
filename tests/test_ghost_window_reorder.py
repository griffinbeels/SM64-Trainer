"""Hearing that the stacking order moved, rather than waiting for a tick.

The ghost is not topmost: it is inserted directly above Project64, which is
what lets it stay up while he works in another app. The price is that clicking
Project64 raises the emulator over it, and the follower's 100 ms tick is long
enough for him to see the ghost vanish and come back. `ReorderWatch` is how the
repair gets to be prompt without polling Project64's window sixty times a
second.

The ctypes half -- that Windows actually delivers these events to a thread that
pumps -- is not testable here and is measured by `tools/ghost_window_rig.py`'s
`zorder` experiment against a stand-in. What IS testable is the decision, and
the decision is the part with a way to go wrong: waking the follower on every
event of a burst would drive a tick, and every tick reads Project64.
"""
import threading

import pytest

from sm64_events.ghost.window import reorder
from sm64_events.ghost.window.reorder import ReorderWatch


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def watch():
    """The watch as the overlay ships it: its OWN coalescing gap, not one a
    test chose. A test that passes its own number leaves the shipped constant
    free to be anything, including zero."""
    clock = Clock()
    wake = threading.Event()
    return ReorderWatch(wake, clock=clock), wake, clock


def test_one_event_wakes_the_follower_immediately(watch):
    """The whole point: his click on Project64 is repaired on the next
    follower pass, not up to a tick later."""
    watcher, wake, _clock = watch
    assert watcher.notice() is True
    assert wake.is_set()
    assert watcher.woken == 1


def test_a_burst_wakes_the_follower_once(watch):
    """A list view shuffling its own children emits reorder after reorder.
    Every wake costs a full tick, and a full tick reads Project64."""
    watcher, wake, clock = watch
    watcher.notice()
    wake.clear()
    for _ in range(50):
        clock.advance(reorder.MIN_WAKE_GAP_S / 100)
        assert watcher.notice() is False
    assert not wake.is_set()
    assert watcher.woken == 1
    assert watcher.coalesced == 50


def test_the_next_event_after_the_gap_wakes_it_again(watch):
    """Coalescing may never turn into ignoring: a second click has to land."""
    watcher, wake, clock = watch
    watcher.notice()
    wake.clear()
    clock.advance(reorder.MIN_WAKE_GAP_S * 1.5)
    assert watcher.notice() is True
    assert wake.is_set()
    assert watcher.woken == 2


def test_the_gap_is_shorter_than_a_frame_and_far_shorter_than_a_tick():
    """The number has to be small enough that the repair is invisible and big
    enough to bound a storm. One display frame is both."""
    from sm64_events.ghost.window.child import DEFAULT_FOLLOW_PERIOD_S

    assert 0 < reorder.MIN_WAKE_GAP_S <= 1 / 60 + 0.001
    assert reorder.MIN_WAKE_GAP_S < DEFAULT_FOLLOW_PERIOD_S / 4


def test_the_hook_is_asked_for_the_two_events_that_matter_and_nothing_else():
    """Foreground is his gesture; reorder is the wider class and the only one
    a rig can provoke without stealing focus. Both are out-of-context, so no
    DLL of ours is ever loaded into another application."""
    calls = []

    class FakeUser32:
        def SetWinEventHook(self, first, last, module, callback, pid, thread,
                            flags):
            calls.append((first, last, module, pid, thread, flags))
            return 0x1000 + len(calls)

        def MsgWaitForMultipleObjects(self, *_args):
            stop.set()
            return 0

        def PeekMessageW(self, *_args):
            return 0

        def DispatchMessageW(self, *_args):
            return 0

        def UnhookWinEvent(self, hook):
            calls.append(("unhook", hook))
            return True

    stop = threading.Event()
    watcher = ReorderWatch(threading.Event(), ops=FakeUser32())
    watcher.run(stop)

    hooked = [call for call in calls if not isinstance(call[0], str)]
    assert [call[0] for call in hooked] == [reorder.EVENT_SYSTEM_FOREGROUND,
                                            reorder.EVENT_OBJECT_REORDER]
    for first, last, module, pid, thread, flags in hooked:
        assert first == last, "one event per hook, never a range"
        assert (module, pid, thread) == (None, 0, 0), (
            "no DLL, and every process -- his click lands in Project64's")
        assert flags & reorder.WINEVENT_SKIPOWNPROCESS, (
            "our own placements must not wake the follower that made them")
        assert flags & ~(reorder.WINEVENT_SKIPOWNPROCESS
                         | reorder.WINEVENT_OUTOFCONTEXT) == 0
    assert watcher.installed is True
    assert [call for call in calls if call[0] == "unhook"], (
        "a hook left installed outlives the overlay")


def test_a_machine_that_refuses_the_hook_still_gets_the_follow_tick():
    """The hook is an improvement on the tick, never a dependency of it."""
    class Refusing:
        def SetWinEventHook(self, *_args):
            raise OSError("no hooks here")

    watcher = ReorderWatch(threading.Event(), ops=Refusing())
    watcher.run(threading.Event())
    assert watcher.installed is False
