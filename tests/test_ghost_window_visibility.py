"""Whether the ghost is on screen at all — one decision, two candidates.

Which candidate is right is an open measurement, not a preference. Today the
window is HIDDEN until the page says it has a picture. The doubt against that:
pywebview never touches `CoreWebView2Controller.IsVisible`, so a hidden window
may never be composited, and a page that is never composited may never fire
the callback that sets `presentable` -- hidden until presentable, never
presentable because hidden.

The other candidate keeps the window on screen and fully transparent instead.
Both are written and tested HERE, so flipping `STRATEGY` when the rig reports
is one line and nothing else has to be found and changed.
"""
import ast
from pathlib import Path

import pytest

from sm64_events.ghost.window.geometry import GameWindow, Rect, follow_decision
from sm64_events.ghost.window.visibility import (
    CLEAR,
    HIDDEN_UNTIL_PRESENTABLE,
    SHOWN_AT_ZERO_ALPHA,
    STRATEGIES,
    STRATEGY,
    visibility_for,
)

PICTURE = Rect(x=120, y=80, width=1600, height=1224)
PLAYING = GameWindow(found=True, minimized=False, client=PICTURE)
CHOSEN = 128

PLAYABLE = follow_decision(PLAYING)
NO_FOOTAGE = follow_decision(PLAYING, presentable=False)
GONE = follow_decision(None)


def test_the_shipping_strategy_is_the_hidden_one():
    """Stated as a test so a flip is a deliberate, reviewable change rather
    than something that arrives with an unrelated edit."""
    assert STRATEGY == HIDDEN_UNTIL_PRESENTABLE
    assert set(STRATEGIES) == {HIDDEN_UNTIL_PRESENTABLE, SHOWN_AT_ZERO_ALPHA}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_footage_on_the_game_picture_is_shown_at_his_own_opacity(strategy):
    want = visibility_for(PLAYABLE, wanted_alpha=CHOSEN, strategy=strategy)
    assert want.shown is True
    assert want.alpha == CHOSEN
    assert want.rect == PICTURE


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_no_emulator_means_no_window_under_either_strategy(strategy):
    want = visibility_for(GONE, wanted_alpha=CHOSEN, strategy=strategy)
    assert want.shown is False
    assert want.rect is None


def test_hidden_until_presentable_keeps_an_empty_window_off_screen():
    want = visibility_for(NO_FOOTAGE, wanted_alpha=CHOSEN,
                          strategy=HIDDEN_UNTIL_PRESENTABLE)
    assert want.shown is False
    assert want.rect is None, "nothing is placed, because nothing is shown"
    assert want.reason == "no_footage"


def test_shown_at_zero_alpha_keeps_an_empty_window_on_screen_but_invisible():
    """The candidate: on screen so WebView2 composites and the page can reach
    a first frame, fully transparent so he sees nothing until it has."""
    want = visibility_for(NO_FOOTAGE, wanted_alpha=CHOSEN,
                          strategy=SHOWN_AT_ZERO_ALPHA)
    assert want.shown is True
    assert want.alpha == CLEAR
    assert want.rect == PICTURE, "it still has to be over the game picture"
    assert want.reason == "no_footage"


def test_an_unknown_strategy_is_refused_rather_than_guessed():
    with pytest.raises(ValueError):
        visibility_for(PLAYABLE, wanted_alpha=CHOSEN, strategy="whatever")


def test_nothing_outside_this_module_branches_on_the_strategy():
    """The flip must be ONE line. A second place that asks which strategy is
    in force is a second place to forget."""
    package = (Path(__file__).resolve().parents[1] / "src" / "sm64_events"
               / "ghost" / "window")
    names = {"STRATEGY", "HIDDEN_UNTIL_PRESENTABLE", "SHOWN_AT_ZERO_ALPHA",
             "STRATEGIES"}
    guilty = {}
    for path in sorted(package.glob("*.py")):
        if path.name == "visibility.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used |= {node.attr for node in ast.walk(tree)
                 if isinstance(node, ast.Attribute)}
        if used & names:
            guilty[path.name] = sorted(used & names)
    assert not guilty, f"the strategy is decided outside visibility.py: {guilty}"
