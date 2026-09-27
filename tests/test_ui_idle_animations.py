"""Endless animations stop costing frames where nobody can see them.

Perf audit 2026-09-25, UI-4, measured 2026-09-27 on the UI fixture: a CSS
animation of anything but transform or opacity recalculates style and
repaints on the main thread every display frame. Every practice-log row
carried a drifting GOLD!! word at opacity 0, and every red "pick a strategy"
nudge pulsed whether or not it was on screen: 25% of a renderer core idle on
the fixture's four rows, 53% on a 44-row log, at 60 Hz (his display: 240 Hz).

What must NOT change is how they look while visible -- he tuned them by feel.
So a loop only ever freezes where it cannot be seen, and carries on from the
same frame when it can. Pixel identity at 850 and 1500 was checked by pinned
first/middle/last frames when this landed; these tests pin the mechanism.
"""
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402

DAY_ONE = datetime.fromtimestamp(time.mktime((2026, 9, 23, 12, 0, 0, 0, 0, -1)), tz=timezone.utc)

# Every endless animation that runs on the main thread (anything beyond a
# transform or an opacity; a custom property reads as no property at all).
MAIN_THREAD_LOOPS = """(async () => {
  const { LOOPS } = await import('/ui/offscreenloops.js');
  const cheap = new Set(['transform', 'opacity', 'translate', 'rotate', 'scale']);
  return document.getAnimations()
    .filter((a) => a.effect && a.effect.getComputedTiming().iterations === Infinity)
    .map((a) => {
      const props = [...new Set(a.effect.getKeyframes().flatMap((k) => Object.keys(k)))]
        .filter((p) => !['offset', 'computedOffset', 'easing', 'composite'].includes(p));
      const target = a.effect.target;
      return { name: a.animationName, onThread: props.length === 0 || props.some((p) => !cheap.has(p)),
               covered: target.matches(LOOPS), where: target.className };
    })
    .filter((loop) => loop.onThread);
})()"""


@pytest.fixture(scope="module")
def base():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as url:
        yield url


@pytest.fixture
def browser():
    with sync_playwright() as play:
        launched = play.chromium.launch(headless=True)
        try:
            yield launched
        finally:
            launched.close()


def _open(browser, base, width=1500, height=1000, reduced="no-preference"):
    # reduced_motion is named, never left to the default: headless defaulting
    # to "reduce" would turn every loop off and pass every test here.
    context = browser.new_context(viewport={"width": width, "height": height},
                                  reduced_motion=reduced)
    page = context.new_page()
    # The Daily Star's foil is one of the loops: pin a day whose star is on
    # the fixture's own stage, announced already so no modal covers it.
    page.request.post(base + "/api/daily-star/seen", data={"date": "2026-09-23"})
    page.clock.install(time=DAY_ONE)
    page.goto(base + "/ui/index.html")
    page.wait_for_selector(".attempt-table tr.is-pb .pb-gold", timeout=30000)
    page.wait_for_timeout(1500)
    return page


def _states(page, selector, name):
    return page.evaluate(f"""[...document.querySelectorAll({selector!r})].map((el) =>
      (el.getAnimations().find((a) => a.animationName === {name!r}) || {{}}).playState || null)""")


def test_only_a_lit_gold_word_drifts(browser, base):
    page = _open(browser, base)
    lit = _states(page, ".attempt-table tr.is-pb .pb-gold", "pbdrift")
    unlit = _states(page, ".attempt-table tr:not(.is-pb):not(.replay-row) .pb-gold", "pbdrift")
    assert lit and set(lit) == {"running"}, f"the PB row's GOLD!! must drift: {lit}"
    assert unlit and set(unlit) == {"paused"}, f"an invisible GOLD!! drifted: {unlit}"
    # Leaving PB: the word fades out still drifting, then stops.
    page.evaluate("document.querySelector('.attempt-table tr.is-pb').classList.add('probe-row')")
    page.evaluate("document.querySelector('tr.probe-row').classList.remove('is-pb')")
    assert _states(page, "tr.probe-row .pb-gold", "pbdrift") == ["running"], "froze mid-fade"
    page.wait_for_timeout(1200)
    assert _states(page, "tr.probe-row .pb-gold", "pbdrift") == ["paused"]
    page.evaluate("document.querySelector('tr.probe-row').classList.add('is-pb')")
    page.wait_for_timeout(100)
    assert _states(page, "tr.probe-row .pb-gold", "pbdrift") == ["running"], "a crowned word stayed still"


def test_a_loop_out_of_sight_freezes_and_comes_back(browser, base):
    page = _open(browser, base, width=850, height=500)
    page.evaluate("window.scrollTo(0, 0); document.querySelectorAll('*').forEach((el) => "
                  "{ if (el.scrollTop) el.scrollTop = 0; })")
    page.wait_for_timeout(400)
    far = page.evaluate("""(() => {
      const all = [...document.querySelectorAll('.needs-strat')];
      const el = all.find((e) => e.getBoundingClientRect().top > innerHeight + 200);
      if (el) el.classList.add('probe-far');
      return Boolean(el); })()""")
    assert far, "fixture drift: no strategy nudge sits below the fold at 850x500"
    assert page.evaluate("document.querySelector('.probe-far').hasAttribute('data-offscreen')")
    assert _states(page, ".probe-far", "needstrat") == ["paused"], "an unseen nudge kept pulsing"
    page.evaluate("document.querySelector('.probe-far').scrollIntoView({block: 'center'})")
    page.wait_for_timeout(400)
    assert not page.evaluate("document.querySelector('.probe-far').hasAttribute('data-offscreen')")
    assert _states(page, ".probe-far", "needstrat") == ["running"], "a nudge back in view stayed still"


@pytest.mark.parametrize("width", [850, 1500])
def test_every_endless_main_thread_loop_can_freeze(browser, base, width):
    """The guard for the next loop someone adds: it must be in LOOPS, or it
    burns a main-thread frame for as long as it exists, seen or not."""
    page = _open(browser, base, width=width)
    loops = page.evaluate(MAIN_THREAD_LOOPS)
    names = {loop["name"] for loop in loops}
    assert {"pbdrift", "needstrat", "daily-foil-drift"} <= names, (
        f"fixture drift: the loops this guard exists for are not on the page ({names})")
    uncovered = [loop for loop in loops if not loop["covered"]]
    assert not uncovered, f"endless main-thread loops outside offscreenloops.js LOOPS: {uncovered}"


def test_reduced_motion_runs_no_endless_loop(browser, base):
    """Reduced motion already ends every animation at once (index.html's
    global reduced-motion rule); this pins that the idle loops stay covered."""
    page = _open(browser, base, reduced="reduce")
    running = page.evaluate("document.getAnimations().filter((a) => a.playState === 'running'"
                            " && a.effect.getComputedTiming().iterations === Infinity).length")
    assert running == 0, f"{running} endless animations run under reduced motion"


PAINT = "((el) => { const s = getComputedStyle(el); return [s.boxShadow, s.backgroundColor, s.borderTopColor]; })"


def test_reduced_motion_keeps_the_nudge_red(browser, base):
    """With the pulse gone, the strategy nudge still wears its resting frame.
    The picker sets `border: 0`, so the red a person sees is the pulse's glow
    and tint: all three painted values must match, not the border colour alone."""
    moving = _open(browser, base)
    rest = moving.evaluate(f"""(() => {{
      const el = document.querySelector('.needs-strat');
      const pulse = el.getAnimations().find((a) => a.animationName === 'needstrat');
      pulse.pause(); pulse.currentTime = 0;
      return {PAINT}(el); }})()""")
    assert rest[0] != "none", f"fixture drift: the pulse rests with no glow ({rest})"
    still = _open(browser, base, reduced="reduce")
    paints = still.evaluate(f"[...document.querySelectorAll('.needs-strat')].map({PAINT})")
    assert paints, "fixture drift: no strategy nudge on the page"
    assert all(paint == rest for paint in paints), (
        f"the nudge under reduced motion paints {paints}; its resting frame paints {rest}")
