"""Whether the ghosts the player asked for have gone unseen too long.

The practice card's setup notice reads this (`display_stalled` in the ghost
payload). It uses the report watch's own predicate,
`silent_failures.ghost_display_missing`, and its grace: the expectation has
held with no progress for `GRACE_S`. It also counts a renderer that is not
reachable at all while ghosts are expected, which is what a Project64 using
another graphics plugin looks like. The notice only shows when the setup check
also finds a difference, so a matching setup that still fails stays our bug,
carried by the report, and the player sees nothing new.

It does not know whether the game is paused; a long pause before the first
ghost appears can hold it true. That is accepted: the notice names a setup
difference that is real either way.
"""
from __future__ import annotations

from time import monotonic

from sm64_events.core.silent_failures import GRACE_S, ghost_display_missing


def unseen(status: dict) -> bool:
    return ghost_display_missing(status) or (
        status.get("expected_display") is True and status.get("available") is False)


class DisplayStall:
    def __init__(self, clock=monotonic, hold_s: float = GRACE_S):
        self._clock, self.hold_s = clock, hold_s
        self._since: tuple | None = None   # (scope, progress, when)
        self.stalled = False

    def observe(self, status: dict | None) -> bool:
        """One look at the native expectation; an empty (busy) read changes nothing."""
        if not status:
            return self.stalled
        if not unseen(status):
            self._since, self.stalled = None, False
            return False
        key = (status.get("scope"), status.get("progress"))
        now = self._clock()
        if self._since is None or self._since[:2] != key:
            self._since = (*key, now)
        self.stalled = now - self._since[2] >= self.hold_s
        return self.stalled
