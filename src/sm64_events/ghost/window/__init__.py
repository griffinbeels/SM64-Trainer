"""The ghost overlay window: one page shown over the Project64 picture.

The trainer imports `GhostWindowController`; everything else here belongs to
the child process it starts. `child.py` explains the thread layout, `style.py`
the window bits, `geometry.py` the one rule about where the overlay goes.
"""
from sm64_events.ghost.window.controller import (
    GHOST_WINDOW_FLAG,
    GhostWindowController,
    ghost_window_command,
)

__all__ = ["GHOST_WINDOW_FLAG", "GhostWindowController", "ghost_window_command"]
