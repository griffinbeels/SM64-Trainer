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


def test_toggling_fast_never_strands_the_box_shorter_than_its_contents(page_server):
    """The bleed: rows painting straight through the card and over its
    neighbours (2026-08-05, two screenshots).

    A run that gets replaced mid-flight used to skip its cleanup, leaving the
    inline `height` frozen at a stale measurement while the replacement had
    already cleared `overflow` -- a box shorter than what it holds, and no
    longer clipping it. The symptom is entirely geometric and has no DOM
    signature: the markup is identical, and every probe that walks the tree
    reads the card as healthy.

    Toggling several times inside one animation is how it was reached, so that
    is what this does.
    """
    with driver.get_driver().launch(headless=True, viewport=(1600, 1000)) as page:
        page.goto(page_server)
        page.evaluate("new Promise(r => setTimeout(r, 2000))")
        for _ in range(4):
            page.evaluate("""
              Array.from(document.querySelectorAll('.tune-actions button'))
                .find((b) => b.textContent.includes('Open / close')).click()
            """)
            page.evaluate("new Promise(r => setTimeout(r, 60))")
        # Open, then let everything settle.
        page.evaluate("""
          Array.from(document.querySelectorAll('.tune-actions button'))
            .find((b) => b.textContent.includes('Open / close')).click()
        """)
        page.evaluate("new Promise(r => setTimeout(r, 1200))")
        state = page.evaluate("""
          (() => {
            const box = document.querySelector('.log-card .disclose');
            const inner = box && box.querySelector('.disclose-inner');
            if (!box || !inner) return {error: 'no disclosure box'};
            return {
              inlineHeight: box.style.height,
              inlineOverflow: box.style.overflow,
              boxHeight: Math.round(box.getBoundingClientRect().height),
              contentHeight: Math.round(inner.getBoundingClientRect().height),
              stillAnimating: box.getAnimations().length,
            };
          })()
        """)

    assert state.get("stillAnimating") == 0, (
        f"an animation is still running long after it should have settled: {state}")
    assert state["inlineHeight"] == "" and state["inlineOverflow"] == "", (
        f"the box is stranded holding inline state from a replaced run: {state}")
    assert state["boxHeight"] >= state["contentHeight"] - 1, (
        "the box is SHORTER than what it holds and is no longer clipping -- "
        f"this is the bleed: {state}")


def test_the_contents_keep_pace_with_the_opening_edge(page_server):
    """Griffin, 2026-08-06, choosing this over clipping a static body: "they
    should animate from top to bottom, as if they're in-sync, falling as the
    dropdown itself falls downwards... keeping pace with the dropdown."

    "Keeping pace" is a measurable claim, and this is what measures it: at any
    instant during the open, the contents' BOTTOM edge should sit on the box's
    bottom edge, because the inner is offset by exactly the height still to
    come. A body that was merely being uncovered would have its bottom edge
    far below the box, hidden by the clip -- which is the animation this
    replaced.
    """
    with driver.get_driver().launch(headless=True, viewport=(1600, 1000)) as page:
        page.goto(page_server)
        page.evaluate("new Promise(r => setTimeout(r, 2000))")
        page.evaluate("""
          Array.from(document.querySelectorAll('.tune-actions button'))
            .find((b) => b.textContent.includes('Open / close')).click()
        """)
        page.evaluate("new Promise(r => setTimeout(r, 120))")
        mid = page.evaluate("""
          (() => {
            const box = document.querySelector('.log-card .disclose');
            const inner = box && box.querySelector('.disclose-inner');
            if (!box || !inner) return {error: 'no disclosure box'};
            const b = box.getBoundingClientRect();
            const i = inner.getBoundingClientRect();
            return {boxBottom: Math.round(b.bottom), innerBottom: Math.round(i.bottom),
                    boxHeight: Math.round(b.height), innerHeight: Math.round(i.height)};
          })()
        """)

    assert mid.get("boxHeight", 0) > 0, f"the box never grew: {mid}"
    assert mid["boxHeight"] < mid["innerHeight"], (
        f"sampled after the animation finished, so this proves nothing: {mid}")
    assert abs(mid["innerBottom"] - mid["boxBottom"]) <= 2, (
        "the contents are not keeping pace with the edge -- they are being "
        f"uncovered by it instead: {mid}")


def test_the_contents_keep_pace_while_CLOSING_too(page_server):
    """The direction bug, and the one his frame-by-frame capture caught
    (2026-08-06): "EVERYTHING INCORRECTLY DISAPPEARS!!!! ... it's as if
    everything got offset by the entire height of the open panel area by
    accident?"

    It was. The offset must be minus the height still to come in BOTH
    directions -- open runs -full -> 0, close runs 0 -> -full. The previous
    form was `from - to`, which is right on the way open by luck and pushes
    the contents DOWN by a whole panel on the way closed, out of the clip.

    Same property as the opening test, sampled during the close: a body being
    shoved the wrong way has its bottom edge a full panel BELOW the box's.
    """
    with driver.get_driver().launch(headless=True, viewport=(1600, 1000)) as page:
        page.goto(page_server)
        page.evaluate("new Promise(r => setTimeout(r, 2000))")
        page.evaluate("""
          Array.from(document.querySelectorAll('.tune-actions button'))
            .find((b) => b.textContent.includes('Open / close')).click()
        """)
        page.evaluate("new Promise(r => setTimeout(r, 900))")   # fully open
        page.evaluate("""
          Array.from(document.querySelectorAll('.tune-actions button'))
            .find((b) => b.textContent.includes('Open / close')).click()
        """)
        page.evaluate("new Promise(r => setTimeout(r, 90))")
        mid = page.evaluate("""
          (() => {
            const box = document.querySelector('.log-card .disclose');
            const inner = box && box.querySelector('.disclose-inner');
            if (!box || !inner) return {error: 'no disclosure box'};
            const b = box.getBoundingClientRect();
            const i = inner.getBoundingClientRect();
            return {boxBottom: Math.round(b.bottom), innerBottom: Math.round(i.bottom),
                    boxHeight: Math.round(b.height), innerHeight: Math.round(i.height)};
          })()
        """)

    assert mid.get("innerHeight", 0) > 0, (
        f"the contents were gone before the close could be measured: {mid}")
    assert mid["boxHeight"] < mid["innerHeight"], (
        f"sampled outside the close, so this proves nothing: {mid}")
    assert abs(mid["innerBottom"] - mid["boxBottom"]) <= 2, (
        "the contents are not keeping pace on the way closed -- a positive "
        f"offset pushes them out of the clip entirely: {mid}")
