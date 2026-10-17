# src/sm64_events/ghost/dpad_toggle.py
"""D-pad Up turns the Ghost Overlay on and off from the controller.

His words, 2026-10-16: "Gameplay active or normal pause --> toggle ghost
overlay; USAMUNE MENU --> don't do anything", because D-pad Up is how
Usamune's menu navigates. A press counts when, between two consecutive
snapshots:

- D-pad Up goes from up to down;
- L, R and Z are all up, so none of Usamune's button combos ever flips it;
- Usamune's menu reads closed on this reading and the one before, and was
  not seen open within one game frame, so a press that opens or closes the
  menu never counts (two readings can share a frame without a pad sampler);
- both readings carry a known, valid button word and a known menu byte.
  A layout without either address (`player1_controller`, `usamune_menu`,
  both VERIFY) never presses.

The game's own pause menu is not special: it toggles like gameplay.

## Why the poller and not the native plugin

The plugin reads the pad and the menu byte each frame too
(`plugin/gfxwrap/ghost_capture.cpp`), but exporting a press from it costs an
IPC field, a version bump and a native rebuild for a human-paced gesture.
The poller already observes every game frame. With a pad sampler it
snapshots once per frame after the controller settles (`server/poller.py`).
No frame went unobserved at 60, 120, 250 or 500 Hz (measured 2026-08-20).
A press lasts several frames and the edge is read off the HELD word, never
the one-frame newly-pressed word, so a late tick still sees it.

Known limits: a press that starts and ends inside one poller stall (a
`"kind": "stall"` line in `data/perf_log.jsonl`) is missed. Nothing toggles
while the tracker's session is paused or the poller is detached, since no
snapshot is read then.

## The flip

One press is one edge, so it is at most one flip. `OverlayFlipper` runs the
card's own doors in order: the state `GET /api/ghost` answers
(`GhostSession.refresh(resolve=False)`), then `PUT /api/ghost {enabled}`
(`GhostSession.update`). The setting is remembered and `ghost_changed` reaches
every client. Like the card's ghost toggle it is a preference, not a
capability (round 5): it flips whenever there is a practice target, even with
nobody to race yet, and the card's Lineup says why. Flips are serialized so a
quick second press reads the first one's result.
"""
import asyncio
import logging

from sm64_events.memory import addresses as A

log = logging.getLogger("sm64.ghost")

_BIT = {name: bit for bit, name in A.BUTTON_BITS}
DPAD_UP = _BIT["Dup"]
#: Held with D-pad Up, these are Usamune's combos rather than a toggle.
COMBO_BUTTONS = _BIT["L"] | _BIT["R"] | _BIT["Z"]
_NOT_BUTTONS = 0xFFFF & ~A.BUTTON_VALID_MASK


def _known(snap) -> bool:
    buttons = snap.buttons_held
    return (buttons is not None and not buttons & _NOT_BUTTONS
            and snap.usamune_menu_open is not None)


class DpadUpPress:
    """The press rule over consecutive (prev, curr) snapshots. Its only state
    is the frame the menu was last seen open, cleared when `global_timer`
    goes backward (a reset or a state load)."""

    def __init__(self):
        self._menu_open_at: int | None = None

    def pressed(self, prev, curr) -> bool:
        if not (_known(prev) and _known(curr)):
            return False
        if self._menu_open_at is not None and curr.global_timer < self._menu_open_at:
            self._menu_open_at = None
        if curr.usamune_menu_open or prev.usamune_menu_open:
            self._menu_open_at = (curr if curr.usamune_menu_open else prev).global_timer
            return False
        if (self._menu_open_at is not None
                and curr.global_timer - self._menu_open_at <= 1):
            return False
        return bool(curr.buttons_held & DPAD_UP
                    and not prev.buttons_held & DPAD_UP
                    and not curr.buttons_held & COMBO_BUTTONS)


class DpadGhostToggle:
    """The poller's chain member: publishes no event (a press is a command,
    not something that happened in the game) and calls `on_press` once per
    press. Rebuilt with the chain after a recovery, like every detector."""

    def __init__(self, on_press):
        self._press = DpadUpPress()
        self._on_press = on_press

    def process(self, prev, curr) -> list:
        if self._press.pressed(prev, curr):
            self._on_press()
        return []


class OverlayFlipper:
    """Flips the Ghost Overlay through the card's doors, one press at a time.
    Built once and bound to the session after it exists (`main.build`), so
    serialization survives detector rebuilds."""

    def __init__(self, session=None):
        self.session = session
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()

    def request(self) -> None:
        """Called inside a poll tick: schedule the flip, never wait for it."""
        if self.session is None:
            return
        try:
            task = asyncio.get_running_loop().create_task(self.flip())
        except RuntimeError:  # no loop: a tool replaying snapshots offline
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def flip(self) -> dict | None:
        """The new state, or None with no practice target to flip it for."""
        async with self._lock:
            session = self.session
            if session is None:
                return None
            try:
                body = await session.refresh(resolve=False)
                if not body.get("target"):
                    log.info("D-pad Up: no practice target to race ghosts on")
                    return None
                body = await session.update(enabled=not body["enabled"])
            except Exception:  # noqa: BLE001 - a press must never break the poll loop
                log.exception("D-pad Up could not flip the Ghost Overlay")
                return None
            log.info("D-pad Up: Ghost Overlay %s", "on" if body["enabled"] else "off")
            return body
