"""Where the ghost goes, when it must not be on screen, and how we know.

The rectangle is the game PICTURE -- Project64's client area in screen
coordinates -- never its window frame: a ghost aligned to the outside of the
window is a ghost offset by the title bar.

**There is no DPI guess in here any more, and that is the fix.** Until
2026-09-19 the follower read PJ64's picture, then multiplied it by
`GetDpiForWindow(overlay) / GetDpiForWindow(pj64)`. PJ64 1.6 is DPI-unaware so
that first number is always 96 while ours is 144 at 150 % scaling -- so the
factor was 1.5 even when the read and the placement already agreed, and his
first try got an overlay half again as wide and tall as the game
(`round-1-first-try.md`). The rectangle read in one DPI context and applied in
that SAME context needs no correction at all, so the follower now places what
it read and then READS THE WINDOW BACK: `rects_agree` is the assertion, and
`correction_for` is the one bounded retry before the overlay is hidden rather
than left covering other apps.
"""
from dataclasses import dataclass

#: Below this the "picture" is a window mid-restore, not something to cover.
MIN_PICTURE_SIDE = 16

#: How far a read-back window rectangle may sit from the one we asked for
#: before it counts as a different rectangle. Windows returns exactly what was
#: asked for when both sides agree about DPI; this allows only for a rounding
#: edge, never for a scale factor.
PLACE_TOLERANCE_PX = 2

#: A correction is only attempted when both sides of the rectangle are wrong by
#: the same factor (a scale), and only inside this band. Anything else is not a
#: coordinate-space disagreement and must not be "fixed" by guessing.
CORRECTION_AGREEMENT = 0.02
CORRECTION_MIN = 0.25
CORRECTION_MAX = 4.0


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
    #: Whether the emulator is the app the player is actually using. A ghost
    #: over a window nobody is looking at is a ghost covering whatever they
    #: ARE looking at -- which is what he reported.
    foreground: bool = True


@dataclass(frozen=True)
class Placement:
    """What the overlay should be doing right now, and why."""

    show: bool
    rect: Rect | None
    reason: str


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


def rects_agree(asked: Rect, got: Rect | None,
                tolerance: int = PLACE_TOLERANCE_PX) -> bool:
    """Did the window actually land where it was told to?

    Compared EDGE BY EDGE rather than as sizes: a window of the right size in
    the wrong place covers the same amount of somebody else's screen.
    """
    if got is None:
        return False
    return (abs(got.x - asked.x) <= tolerance
            and abs(got.y - asked.y) <= tolerance
            and abs(got.width - asked.width) <= tolerance
            and abs(got.height - asked.height) <= tolerance)


def correction_for(asked: Rect, got: Rect) -> Rect | None:
    """What to ask for instead, when Windows scaled what we asked for.

    Only a UNIFORM scale is corrected, and only once: if both sides came back
    multiplied by the same factor, the placement crossed a coordinate-space
    boundary and asking for `asked / factor` lands it right. Anything else --
    a window clamped by a monitor edge, a size that changed but a position
    that did not -- returns None, and the caller hides the overlay instead of
    guessing at the player's screen.
    """
    if got.width <= 0 or got.height <= 0 or asked.width <= 0 or asked.height <= 0:
        return None
    by_width = asked.width / got.width
    by_height = asked.height / got.height
    if abs(by_width - by_height) > CORRECTION_AGREEMENT:
        return None
    if not CORRECTION_MIN <= by_width <= CORRECTION_MAX:
        return None
    corrected = scale_rect(asked, by_width)
    return None if corrected == asked else corrected


def follow_decision(game: GameWindow | None, *, wanted_visible: bool = True,
                    presentable: bool = True) -> Placement:
    """The whole rule the overlay follows, as one pure function.

    `presentable` is the page's own answer to "have I got a picture worth
    showing". It has to be part of this decision rather than a separate check,
    because whole-window alpha dims everything under the window: live
    2026-09-19, the ghost at 80 % read as a translucent dark sheet over his
    game and his browser with no footage on it. A window with nothing to show
    is a window making his game harder to see.
    """
    if not wanted_visible:
        return Placement(show=False, rect=None, reason="hidden_by_request")
    if game is None or not game.found:
        return Placement(show=False, rect=None, reason="gone")
    if game.minimized:
        return Placement(show=False, rect=None, reason="minimized")
    if not game.foreground:
        return Placement(show=False, rect=None, reason="not_foreground")
    if not presentable:
        return Placement(show=False, rect=None, reason="no_footage")
    client = game.client
    if (client is None or client.width < MIN_PICTURE_SIDE
            or client.height < MIN_PICTURE_SIDE):
        return Placement(show=False, rect=None, reason="too_small")
    return Placement(show=True, rect=client, reason="follow")
