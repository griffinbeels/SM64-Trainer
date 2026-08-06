# tests/test_tunefeed_page_plays.py
"""The feed/disclosure inspector actually moves things.

A tuning page that renders perfectly and animates nothing is the failure this
guards, and it is invisible to every other check: the controls draw from the
registry, the stage draws from the stylesheet, and a rig whose animations never
start looks exactly like one whose values are all zero. `.claude/skills/
tuning-demo` names it directly -- verify by driving the page's OWN controls and
reading geometry back, never by asking whether text is present.

Two claims, and both are about the SHIPPED code rather than the page: pushing a
card in plays a real Web Animation on the cards it displaces (`useFeedMotion`),
and opening one plays a real height animation (`Disclose`). The page is only
how they are reached without a live emulator.
"""
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

UI = REPO / "src" / "sm64_events" / "ui"

# `getAnimations()` is the honest probe: it reports what the browser is ACTUALLY
# playing, so a plan that computed correctly and never started still fails. A
# geometry sample could not tell a 0ms animation from a missing one.
COUNT_RUNNING = """
  (() => {
    const list = document.querySelector('.log-list');
    if (!list) return {error: 'no list'};
    const running = [];
    list.querySelectorAll('.log-card').forEach((card) => {
      card.getAnimations({subtree: true}).forEach((a) => {
        running.push(Math.round(a.effect.getTiming().duration));
      });
    });
    return {cards: list.querySelectorAll('.log-card').length, running};
  })()
"""


@pytest.fixture(scope="module")
def page_server(tmp_path_factory):
    """A plain static server over `ui/`. The inspector talks to no API except
    SAVE, which this deliberately does not provide -- a test that could write
    the registry would rewrite the shipped defaults as a side effect."""
    port = 8471
    proc = subprocess.Popen(
        [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1"],
        cwd=str(UI.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.2)
        yield f"http://127.0.0.1:{port}/ui/tunefeed.html"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_pushing_a_card_in_animates_the_cards_below_it(page_server):
    with driver.get_driver().launch(headless=True, viewport=(1600, 1000)) as page:
        page.goto(page_server)
        page.evaluate("new Promise(r => setTimeout(r, 2000))")
        before = page.evaluate(COUNT_RUNNING)
        assert before.get("cards", 0) >= 3, (
            f"the stage never rendered its cards: {before}")
        assert before["running"] == [], (
            f"something is animating before anything happened: {before}")

        page.evaluate("""
          Array.from(document.querySelectorAll('.tune-actions button'))
            .find((b) => b.textContent.includes('Push a new card in')).click()
        """)
        page.evaluate("new Promise(r => setTimeout(r, 40))")
        during = page.evaluate(COUNT_RUNNING)

    assert during["cards"] > before["cards"], (
        f"the arrival never landed in the list: {during}")
    assert during["running"], (
        "a card arrived and nothing in the list is animating -- the feed "
        f"motion computed a plan and never played it: {during}")


def test_opening_a_card_animates_its_height(page_server):
    with driver.get_driver().launch(headless=True, viewport=(1600, 1000)) as page:
        page.goto(page_server)
        page.evaluate("new Promise(r => setTimeout(r, 2000))")
        page.evaluate("""
          Array.from(document.querySelectorAll('.tune-actions button'))
            .find((b) => b.textContent.includes('Open / close')).click()
        """)
        # SAMPLED MID-RUN, not at 40ms. The shipped curve starts at rest
        # (`c1y: 0`, which is his own "it should just start moving towards its
        # new destination" expressed as a number), so 40ms into a ~260ms open
        # the box has genuinely travelled almost nothing and rounds to zero --
        # the first version of this probe read that as "the box never grew"
        # and was measuring the curve rather than the bug.
        page.evaluate("new Promise(r => setTimeout(r, 140))")
        mid = page.evaluate("""
          (() => {
            const box = document.querySelector('.log-card .disclose');
            if (!box) return {error: 'no disclosure box'};
            return {
              animating: box.getAnimations().length,
              height: Math.round(box.getBoundingClientRect().height),
            };
          })()
        """)
    assert mid.get("animating", 0) > 0, (
        f"the card opened without a height animation: {mid}")
    assert mid["height"] > 0, f"the box never grew: {mid}"


def test_the_flip_measures_inside_the_list_not_the_viewport():
    """The "shuffle" he reported, as a structural claim.

    A viewport-relative FIRST/LAST pair measures the CONTAINER's own movement
    too, so a card opening above the log -- or the page scrolling -- gives
    EVERY card a non-zero displacement and animates all of them at once, which
    is what read as the list scrambling rather than one card bumping the rest
    down. His own guess named it: "I think maybe it's because of the way the
    overall containing box expands and contracts too?"

    A source check rather than a driven one, and the limit is worth stating:
    reaching the bug in a browser needs the container to move BETWEEN two key
    changes, which the tuning page (whose list is the only thing on it) cannot
    produce. Mutation-prove by deleting the subtraction.
    """
    from source_scan import strip_comments
    source = strip_comments(
        (REPO / "src" / "sm64_events" / "ui" / "components" / "feedmotion.js")
        .read_text(encoding="utf-8"))
    assert "root.getBoundingClientRect().top" in source, (
        "the hook no longer measures the list's own position, so it cannot "
        "subtract it")
    assert "- rootTop" in source, (
        "card positions are viewport-relative again -- every card will animate "
        "whenever anything moves the list")
