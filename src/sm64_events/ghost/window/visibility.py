"""Whether the ghost window is on screen at all, decided in ONE place.

There are two ways to keep a ghost with nothing to show from dimming his game,
and which one is right is an open measurement, not a preference:

`HIDDEN_UNTIL_PRESENTABLE` (shipping) keeps the window hidden until the page
says it has a picture. Simple, and it cannot leak a dark sheet. The open
question against it: pywebview never touches `CoreWebView2Controller.IsVisible`
(absent from both its WinForms and its EdgeChromium backends), so WebView2 may
believe it is visible while the host window has no `WS_VISIBLE` and DWM never
composites it -- and a page that is never composited may never fire
`requestVideoFrameCallback`, which is the only thing that sets the page's
`presentable` flag. If that is true this strategy is a deadlock by
construction: hidden until presentable, never presentable because hidden.

`SHOWN_AT_ZERO_ALPHA` keeps the window on screen and fully transparent
instead, so WebView2 composites and the page can reach a first frame, and the
opacity he chose is only applied once there is something to see.

**The two differ in exactly one function, and nothing else may branch on the
strategy.** A parallel track is measuring which is true in a rig; flipping
`STRATEGY` is the whole change when the answer arrives.
"""
from dataclasses import dataclass

from sm64_events.ghost.window.geometry import Placement, Rect

#: Hidden while there is nothing to show.
HIDDEN_UNTIL_PRESENTABLE = "hidden_until_presentable"
#: On screen, fully transparent, while there is nothing to show.
SHOWN_AT_ZERO_ALPHA = "shown_at_zero_alpha"

STRATEGIES = (HIDDEN_UNTIL_PRESENTABLE, SHOWN_AT_ZERO_ALPHA)

#: What ships today. See the module docstring for what would change it.
STRATEGY = HIDDEN_UNTIL_PRESENTABLE

#: Fully transparent: on screen for the compositor, invisible to him.
CLEAR = 0


@dataclass(frozen=True)
class Visibility:
    """What the window should be doing, once it is ready to be told."""

    shown: bool
    alpha: int
    rect: Rect | None
    reason: str


def visibility_for(placement: Placement, *, wanted_alpha: int,
                   strategy: str | None = None) -> Visibility:
    """The one decision. `wanted_alpha` is the opacity he chose, as a byte."""
    chosen = STRATEGY if strategy is None else strategy
    if chosen not in STRATEGIES:
        raise ValueError(f"unknown ghost visibility strategy {chosen!r}")
    if chosen == SHOWN_AT_ZERO_ALPHA:
        # On screen wherever there is a picture to sit on, even with nothing
        # to show -- transparent until there is.
        return Visibility(shown=placement.rect is not None,
                          alpha=wanted_alpha if placement.show else CLEAR,
                          rect=placement.rect, reason=placement.reason)
    return Visibility(shown=placement.show and placement.rect is not None,
                      alpha=wanted_alpha,
                      rect=placement.rect if placement.show else None,
                      reason=placement.reason)
