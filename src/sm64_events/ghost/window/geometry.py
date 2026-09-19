"""Where the ghost goes, when it must not be on screen, and how we know.

The rectangle is the game PICTURE -- the part of Project64's client area the
RENDERER draws into -- never its window frame and, since 2026-09-19, never the
whole client area either.

**Why the client area is too big, measured on his machine.** Project64 1.6
keeps a status bar inside its own client area: a `msctls_statusbar32` child
docked along the bottom. Live read of his running emulator, per-monitor-aware:
client `[18, 81, 2400, 1836]`, status bar window rect `[18, 1883, 2400, 35]`,
and the (hidden) ROM-browser list sized to `[18, 81, 2400, 1802]` -- PJ64's own
arithmetic for "the client, minus the strip". The renderer gets that remainder,
which is why every clip the trainer records is 1600x1200: his client is 1600
logical wide by 1224 tall, the strip takes 23, and the capture layer's
floor-even crop turns the remaining 1601 into 1200. A ghost placed on the whole
client is therefore a ghost stretched by 1836/1802 -- about 1.9 % -- which is
the drift he could see at the bottom edge and on the Mario icon.

So the picture is derived here, from read-only geometry: the client rectangle
with any visible full-width strip docked to its bottom edge subtracted. Keyed
on the SHAPE rather than on the class name, because a build with a different
status-bar control is still a strip; the class is logged either way.

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

#: What makes a child window a STRIP of chrome rather than a panel of content.
#: A status bar spans the client and is a small fraction of it; the ROM browser
#: spans the client and is nearly all of it, and must never be subtracted.
STRIP_MAX_HEIGHT_FRACTION = 0.5
STRIP_MIN_WIDTH_FRACTION = 0.9
#: Windows rounds a DPI-unaware child's rectangle into the aware space, so the
#: strip's bottom lands within a pixel or two of the client's, sometimes past
#: it (measured: client bottom 1917, status bar bottom 1918).
STRIP_EDGE_TOLERANCE_PX = 4


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int

    def as_list(self) -> list[int]:
        return [self.x, self.y, self.width, self.height]


@dataclass(frozen=True)
class ChildWindow:
    """One window living inside Project64's client area, as read.

    `visible` is the child's OWN `WS_VISIBLE` bit, not `IsWindowVisible`: a
    child of a window that is itself hidden reports invisible whatever it
    chose, and the question here is which children Project64 is currently
    laying out -- the ROM browser is style-hidden while a game runs, the
    status bar is not.
    """

    hwnd: int
    cls: str
    rect: Rect
    visible: bool


@dataclass(frozen=True)
class GameWindow:
    """One observation of the Project64 window, all in ONE coordinate space."""

    found: bool
    minimized: bool
    client: Rect | None
    #: Whether the emulator is the app the player is actually using. READ AND
    #: LOGGED, never branched on: the ghost sits directly above Project64 in
    #: the stacking order rather than above everything, so an app he clicks
    #: covers it exactly as it covers the game. Taking the ghost down for this
    #: is what made it "blink in / out" (his round-3 words), and
    #: `tests/test_ghost_window_geometry.py` holds the reversal.
    foreground: bool = True
    #: Everything inside the client area, so the picture can be derived from
    #: what is actually there rather than from an assumed layout.
    children: tuple[ChildWindow, ...] = ()

    @property
    def picture(self) -> Rect | None:
        """The rectangle the renderer draws into. The ONE derivation."""
        return picture_rect(self.client, self.children)


@dataclass(frozen=True)
class Placement:
    """What the overlay should be doing right now, and why."""

    show: bool
    rect: Rect | None
    reason: str


def _strip_at(client: Rect, bottom: int,
              children: tuple[ChildWindow, ...] | list[ChildWindow],
              taken: set[int]) -> ChildWindow | None:
    """A child docked along `bottom`, or None. One step of the walk below."""
    for child in children:
        rect = child.rect
        if child.hwnd in taken or not child.visible:
            continue
        if rect.height <= 0 or rect.width <= 0:
            continue
        if rect.height > client.height * STRIP_MAX_HEIGHT_FRACTION:
            continue
        if rect.width < client.width * STRIP_MIN_WIDTH_FRACTION:
            continue
        if abs(rect.x - client.x) > STRIP_EDGE_TOLERANCE_PX:
            continue
        if rect.y + rect.height < bottom - STRIP_EDGE_TOLERANCE_PX:
            continue
        return child
    return None


def bottom_strip(client: Rect | None,
                 children: tuple[ChildWindow, ...] | list[ChildWindow]
                 ) -> ChildWindow | None:
    """The topmost edge of the chrome docked along the bottom of the client.

    A strip is a VISIBLE child that starts at the client's left edge, spans
    almost its whole width, reaches its bottom edge, and is a small fraction of
    its height. That last clause is what keeps Project64's ROM browser -- same
    width, same left edge, but nearly the full height -- from being mistaken
    for chrome, and it is also why there is no "must start below the client's
    top" clause: a child short enough to be a strip and low enough to reach the
    bottom edge cannot also begin at the top. A clause added on 2026-09-19 to
    say so survived every mutation, which is how it was found to be dead.

    Stacked strips are walked: once the lowest is found the bottom moves up to
    its top and the search runs again, so the answer is the HIGHEST child of a
    contiguous run and the picture stops above all of them. Project64 1.6 has
    exactly one (its `msctls_statusbar32`); the walk costs three lines and
    removes a limit that would otherwise have to be discovered live.
    """
    if client is None or client.width <= 0 or client.height <= 0:
        return None
    bottom = client.y + client.height
    taken: set[int] = set()
    found: ChildWindow | None = None
    for _pass in range(len(children)):
        strip = _strip_at(client, bottom, children, taken)
        if strip is None:
            break
        found, bottom = strip, strip.rect.y
        taken.add(strip.hwnd)
    return found


def picture_rect(client: Rect | None,
                 children: tuple[ChildWindow, ...] | list[ChildWindow] = ()
                 ) -> Rect | None:
    """The client area, minus whatever chrome is docked inside its bottom.

    With no children -- a test's stand-in, or an emulator with its status bar
    turned off -- this is the client rectangle unchanged, which is what the
    overlay did before this function existed.
    """
    if client is None:
        return None
    strip = bottom_strip(client, children)
    if strip is None:
        return client
    height = strip.rect.y - client.y
    if height <= 0:
        return client
    return Rect(x=client.x, y=client.y, width=client.width, height=height)


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

    **Which app he is CLICKING is deliberately not one of these clauses**, as
    of round 3. "It should just stay always on, unless we turn the ghost
    overlay off (or move out of the area and disable the ghost naturally)."
    Hiding it whenever Project64 lost the foreground made it blink every time
    he touched another window, and the ghost never needed that rule: it is
    inserted directly above Project64 in the stacking order, so anything he
    raises over the emulator is over the ghost too.
    """
    if not wanted_visible:
        return Placement(show=False, rect=None, reason="hidden_by_request")
    if game is None or not game.found:
        return Placement(show=False, rect=None, reason="gone")
    if game.minimized:
        return Placement(show=False, rect=None, reason="minimized")
    picture = game.picture
    if (picture is None or picture.width < MIN_PICTURE_SIDE
            or picture.height < MIN_PICTURE_SIDE):
        return Placement(show=False, rect=None, reason="too_small")
    # From here the game picture EXISTS, so the rectangle is carried even
    # though the ghost is not raised. That is what lets the window park over
    # the picture, fully transparent, instead of being hidden -- and a hidden
    # WebView2 never presents a frame at all (rig, 2026-09-19: 0 rAF/s hidden
    # against 237/s shown at alpha 0), which made the old rule a deadlock.
    if not presentable:
        return Placement(show=False, rect=picture, reason="no_footage")
    return Placement(show=True, rect=picture, reason="follow")
