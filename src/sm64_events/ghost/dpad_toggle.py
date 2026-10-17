# src/sm64_events/ghost/dpad_toggle.py
"""The ghost hotkey turns the Ghost Overlay on and off from the controller.

The hotkey is D-pad Up, unless Usamune's save state is on D-pad Up: then it
is all four C buttons held together ("Cx4"), the other choice on Usamune's
RESET page, so one press never both loads a state and flips the ghosts. His
ask, 2026-10-17: "if the user selected Cx4, then the ghost toggle is D-UP; if
the user selected D-UP, then the toggle is Cx4". `hotkey_for` reads the save
state button byte (layout row `usamune_savestate_button`, VERIFY), and
`SAVESTATE_ON_DPAD_UP` is the value meaning D-pad Up. An unknown byte keeps
D-pad Up. The key that is not the hotkey does nothing. The toggle tells the
ghost session which key it is whenever that changes
(`GhostSession.note_hotkey`), so the card's tooltip names it.

His words, 2026-10-16: "Gameplay active or normal pause --> toggle ghost
overlay; USAMUNE MENU --> don't do anything", because D-pad Up is how
Usamune's menu navigates. A press counts when, between two consecutive
snapshots:

- the hotkey goes from not held to held, whatever else is held (his ask,
  2026-10-17: "It should detect if dpad up is ever selected, even if it's
  combined with other buttons"; the first version ignored a press with L, R
  or Z held). For Cx4 that is the reading where the last of the four joins:
  they need not go down on one frame;
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
C_X4 = _BIT["Cup"] | _BIT["Cdown"] | _BIT["Cleft"] | _BIT["Cright"]
_NOT_BUTTONS = 0xFFFF & ~A.BUTTON_VALID_MASK

#: Each hotkey by the name `GET /api/ghost` gives it, and the buttons it holds.
HOTKEYS = {"dpad_up": DPAD_UP, "c_x4": C_X4}
#: The save state button byte's value for Usamune's DUP choice (0 is Cx4).
#: Read off his menu flips, 2026-10-17 (`memory/layout.py`'s US row).
SAVESTATE_ON_DPAD_UP = 1


def hotkey_for(savestate_button: int | None) -> str:
    """The hotkey's name: Cx4 while Usamune's save state is on D-pad Up, else
    D-pad Up, which an unknown byte also answers."""
    if savestate_button == SAVESTATE_ON_DPAD_UP:
        return "c_x4"
    return "dpad_up"


def _known(snap) -> bool:
    buttons = snap.buttons_held
    return (buttons is not None and not buttons & _NOT_BUTTONS
            and snap.usamune_menu_open is not None)


class HotkeyPress:
    """The press rule over consecutive (prev, curr) snapshots, for the keys
    the caller names. Its only state is the frame the menu was last seen
    open, cleared when `global_timer` goes backward (a reset or a state load)."""

    def __init__(self):
        self._menu_open_at: int | None = None

    def pressed(self, prev, curr, keys: int) -> bool:
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
        return ((curr.buttons_held & keys) == keys
                and (prev.buttons_held & keys) != keys)


class DpadGhostToggle:
    """The poller's chain member: publishes no event (a press is a command,
    not something that happened in the game) and calls `on_press` once per
    press of the current hotkey. `on_hotkey` hears the hotkey's name on the
    first reading and whenever it changes, never per frame. Rebuilt with the
    chain after a recovery, like every detector, so a rebuilt one tells the
    name again and the session ignores the repeat."""

    def __init__(self, on_press, on_hotkey=None):
        self._press = HotkeyPress()
        self._on_press = on_press
        self._on_hotkey = on_hotkey
        self._hotkey: str | None = None

    def process(self, prev, curr) -> list:
        hotkey = hotkey_for(curr.usamune_savestate_button)
        if hotkey != self._hotkey:
            self._hotkey = hotkey
            if self._on_hotkey is not None:
                self._on_hotkey(hotkey)
        if self._press.pressed(prev, curr, HOTKEYS[hotkey]):
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
                    log.info("ghost hotkey: no practice target to race ghosts on")
                    return None
                body = await session.update(enabled=not body["enabled"])
            except Exception:  # noqa: BLE001 - a press must never break the poll loop
                log.exception("the ghost hotkey could not flip the Ghost Overlay")
                return None
            log.info("ghost hotkey: Ghost Overlay %s", "on" if body["enabled"] else "off")
            return body
