"""Whether the ghost window is on screen at all, decided in ONE place.

**`SHOWN_AT_ZERO_ALPHA` ships, and the rig is why.** Measured on this machine
over three runs (2026-09-19): a HIDDEN pywebview window runs its page --
`setInterval` ticks, `evaluate_js` answers in 0.18 s -- and presents NOTHING.
`requestAnimationFrame` 0/s, `requestVideoFrameCallback` 0/s,
`visibilityState` "hidden", a `<video>` stuck at `readyState` 0, and its own
pixels all black under `PrintWindow`. The SAME window shown at alpha 0:
rAF 237/s, rVFC 29.6/s, "visible", `readyState` 4, real picture pixels.

So the old rule was a deadlock by construction -- hidden until the page has a
picture, and the page can never get one because it is hidden. That is what he
saw on his second try: nothing at all.

The window's resting state is therefore ON SCREEN and fully transparent, over
the game picture, click-through and non-activating. "Visible to him" is an
ALPHA, never a `ShowWindow`: the opacity he chose is reached only once the
page says it has a picture, the session wants the ghost, Project64 is the
foreground app, and the placement has been proved by readback. Any of those
going false takes the alpha back to 0 -- not the window off screen, because
taking it off screen is what stops the page presenting at all.

`HIDDEN_UNTIL_PRESENTABLE` is kept, tested and unused: it is the thing the rig
ruled out, and a named alternative is cheaper to re-read than a deleted one.

**Nothing outside this module may branch on the strategy**, which
`tests/test_ghost_window_visibility.py` enforces by reading the package.
"""
from dataclasses import dataclass

from sm64_events.ghost.window.geometry import Placement, Rect

#: Hidden while there is nothing to show.
HIDDEN_UNTIL_PRESENTABLE = "hidden_until_presentable"
#: On screen, fully transparent, while there is nothing to show.
SHOWN_AT_ZERO_ALPHA = "shown_at_zero_alpha"

STRATEGIES = (HIDDEN_UNTIL_PRESENTABLE, SHOWN_AT_ZERO_ALPHA)

#: What ships. Flipped from HIDDEN_UNTIL_PRESENTABLE on 2026-09-19 when the
#: rig measured a hidden WebView2 presenting nothing at all.
STRATEGY = SHOWN_AT_ZERO_ALPHA

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
