"""Where the ghost goes, and when it must not be on screen at all.

The rectangle is the game PICTURE -- Project64's client area in screen
coordinates -- never its window frame: a ghost aligned to the outside of the
window is a ghost offset by the title bar.

`scale` exists for one reason. PJ64 1.6 is DPI-unaware, so a DPI-unaware
reader sees its client as 1600x1224 while an aware one sees the same window
as 2400x1836 at 150 % scaling (live 2026-07-23). The follower reads and
places from the same thread, in the same awareness context, so the scale is
normally exactly 1.0; it is not when pinning the process's awareness was
refused, and that is the case this factor corrects.
"""
from dataclasses import dataclass

#: Below this the "picture" is a window mid-restore, not something to cover.
MIN_PICTURE_SIDE = 16


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.width, self.height]


@dataclass(frozen=True)
class GameWindow:
    """One observation of the Project64 window, all in ONE coordinate space."""

    found: bool
    minimized: bool
    client: Rect | None


@dataclass(frozen=True)
class Placement:
    """What the overlay should be doing right now, and why."""

    show: bool
    rect: Rect | None
    reason: str


def dpi_scale(read_dpi: int, place_dpi: int) -> float:
    """How much to grow a rectangle read at one DPI to place it at another.

    A failed query reports 0; that must leave the rectangle alone rather than
    collapse it to a point.
    """
    if read_dpi <= 0 or place_dpi <= 0:
        return 1.0
    return place_dpi / read_dpi


def scale_rect(rect: Rect, scale: float) -> Rect:
    """Scale a rectangle by its EDGES, so the far edge lands on one pixel.

    Rounding a width independently of its origin walks the right edge off the
    picture by a pixel at some positions and not others.
    """
    if scale == 1.0:
        return rect
    left, top = round(rect.x * scale), round(rect.y * scale)
    right = round((rect.x + rect.width) * scale)
    bottom = round((rect.y + rect.height) * scale)
    return Rect(x=left, y=top, width=right - left, height=bottom - top)


def follow_decision(game: GameWindow | None, *, scale: float = 1.0,
                    wanted_visible: bool = True) -> Placement:
    """The whole rule the overlay follows, as one pure function."""
    if not wanted_visible:
        return Placement(show=False, rect=None, reason="hidden_by_request")
    if game is None or not game.found:
        return Placement(show=False, rect=None, reason="gone")
    if game.minimized:
        return Placement(show=False, rect=None, reason="minimized")
    client = game.client
    if (client is None or client.width < MIN_PICTURE_SIDE
            or client.height < MIN_PICTURE_SIDE):
        return Placement(show=False, rect=None, reason="too_small")
    return Placement(show=True, rect=scale_rect(client, scale), reason="follow")
