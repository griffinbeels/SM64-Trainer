# tests/test_ui_strat_pick_settles.py
"""A strategy pick settles on the picked value the instant it is made.

His report (task 0113, 2026-08-31): *"it sometimes immediately glitches
between no strategy && the new strategy that I chose. It goes back and forth
a few times, and then usually settles on the correct strategy... When I
select a new strategy, it should settle on that strategy immediately."*

Reproduced on a copy of his own journal before the fix, sampling the
<select> every frame after the pick: `TJ Owlless -> Sideflip (6 ms) -> TJ
Owlless (22 ms) -> Sideflip (1055 ms)`. Two causes, both pinned here:

  * the picker drew the SERVER's value and nothing else, so the first store
    re-render after the pick -- the WebSocket event the write itself
    broadcasts, arriving before the write's own response -- reset the native
    <select> to the old value (Preact compares `value` against the DOM, not
    against the previous prop), and the pick only came back with the next
    view fetch;
  * a row retag then replayed the ENTIRE journal on the server (0.7-1.0 s at
    18k events, event loop blocked), which is how long that old value stayed.

The second half is the server's contract (tests/test_tracker_service.py::
test_set_attempt_strat_does_not_replay_the_journal). This file pins the
browser's: the picker holds the pick until the server agrees, and every view
refresh a component asks for goes through the store's one coalescer, so two
view fetches can never be in flight together and land out of order.

Both tests hold a response back INSIDE the page rather than slowing the
server: the window the bug lived in is opened deliberately, wide enough to
sample, and independent of how fast this machine happens to answer today.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import FIXTURE_FOREIGN_STRAT, FIXTURE_STRAT, serve_ui  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402

# The practice-log card that carries attempt rows -- found by shape, not index.
CARD = ("Array.from(document.querySelectorAll('.log-card'))"
        ".find((c) => c.querySelector('td.attempt-strategy select'))")
ROW_PICKER = f"{CARD}.querySelectorAll('td.attempt-strategy select')[0]"
HEAD_PICKER = f"{CARD}.querySelector('.log-card-strat-picker select')"

# Hold every /api RESPONSE for `ms` before the page sees it -- the write's
# own, AND the view fetches that follow it. The server still commits and
# broadcasts at once, so the store re-renders from the WebSocket events while
# the picker's write is, from its point of view, still in flight (the window
# the snap-back lived in); and with the view held too, the only thing that can
# put the pick on screen inside that window is the picker holding it itself.
# A picker that merely waits for the server would pass a "never reverts"
# check while showing the old value for the whole hold -- proved by mutation
# on 2026-09-01, which is why the first transition's TIME is asserted too.
HOLD_API = """
((ms) => {
  const real = window.fetch;
  window.fetch = async (url, opts) => {
    const response = await real(url, opts);
    if (typeof url === 'string' && url.includes('/api/'))
      await new Promise((done) => setTimeout(done, ms));
    return response;
  };
})(%d)
"""
HOLD_MS = 400
# A pick has to be on screen by the next frame or two; anything approaching
# the hold means it waited for the server.
SETTLE_BUDGET_MS = 100

# Count view fetches in flight together, as the page itself sees them. Each
# one's settle is held `ms`, so two fired inside that window overlap for
# certain instead of by luck of the round trip.
COUNT_VIEW_FETCHES = """
((ms) => {
  const real = window.fetch;
  window.__viewInFlight = 0;
  window.__viewMostAtOnce = 0;
  window.__viewFetches = 0;
  window.fetch = async (url, opts) => {
    const isView = typeof url === 'string' && url.includes('/api/session?');
    if (isView) {
      window.__viewFetches += 1;
      window.__viewInFlight += 1;
      window.__viewMostAtOnce = Math.max(window.__viewMostAtOnce, window.__viewInFlight);
    }
    try {
      const response = await real(url, opts);
      if (isView) await new Promise((done) => setTimeout(done, ms));
      return response;
    } finally {
      if (isView) window.__viewInFlight -= 1;
    }
  };
})(%d)
"""


def _sample_every_frame(page, getter, ms):
    """Record every DISTINCT value the <select> shows over the next `ms`,
    read off the DOM on the page's own frame clock, with the ms since the
    sampler was armed (the pick follows on the very next evaluate)."""
    page.evaluate(f"""
      (() => {{
        window.__seen = [];
        window.__armed = performance.now();
        const until = window.__armed + {ms};
        (function loop() {{
          const el = {getter};
          const value = el ? el.value : null;
          const seen = window.__seen;
          if (!seen.length || seen[seen.length - 1].value !== value)
            seen.push({{value, at: Math.round(performance.now() - window.__armed)}});
          if (performance.now() < until) requestAnimationFrame(loop);
        }})();
      }})()
    """)


def _pick(page, getter, value):
    page.evaluate(f"""
      (() => {{
        const el = {getter};
        el.value = {value!r};
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
        return el.value;
      }})()
    """)


def _assert_settled_at_once(page, was, pick, which):
    seen = page.evaluate("window.__seen")
    values = [entry["value"] for entry in seen]
    assert values == [was, pick], (
        f"{which} showed {values} after the pick -- anything but "
        f"[{was!r}, {pick!r}] is a value he did not choose")
    assert seen[1]["at"] < SETTLE_BUDGET_MS, (
        f"{which} took {seen[1]['at']} ms to show the pick: it waited for "
        "the server instead of holding the pick itself")


def _open_practice_log(page, base):
    page.goto(f"{base}/ui/index.html")
    page.wait_for(".log-list-card")
    page.wait_for("td.attempt-strategy select")


def test_a_pick_never_shows_the_old_value_again():
    with serve_ui() as base, get_driver().launch() as page:
        _open_practice_log(page, base)
        page.evaluate(HOLD_API % HOLD_MS)

        # An attempt row: the retag path, and the picker that flickered longest.
        was = page.evaluate(f"({ROW_PICKER}).value")
        assert was == FIXTURE_STRAT, "fixture drift: the first row is not on the seeded strategy"
        _sample_every_frame(page, ROW_PICKER, 3 * HOLD_MS)
        _pick(page, ROW_PICKER, FIXTURE_FOREIGN_STRAT)
        page.wait_ms(3 * HOLD_MS + 100)
        _assert_settled_at_once(page, was, FIXTURE_FOREIGN_STRAT, "the row's picker")

        # The card's own picker: the active strategy, "overall".
        was = page.evaluate(f"({HEAD_PICKER}).value")
        pick = FIXTURE_STRAT if was != FIXTURE_STRAT else FIXTURE_FOREIGN_STRAT
        _sample_every_frame(page, HEAD_PICKER, 3 * HOLD_MS)
        _pick(page, HEAD_PICKER, pick)
        page.wait_ms(3 * HOLD_MS + 100)
        _assert_settled_at_once(page, was, pick, "the card's picker")


def test_a_pick_never_runs_two_view_fetches_at_once():
    with serve_ui() as base, get_driver().launch() as page:
        _open_practice_log(page, base)
        page.evaluate(COUNT_VIEW_FETCHES % 300)
        _pick(page, ROW_PICKER, FIXTURE_FOREIGN_STRAT)
        page.wait_ms(1500)
        fetches = page.evaluate("window.__viewFetches")
        assert fetches >= 1, "the pick never refreshed the view at all"
        assert page.evaluate("window.__viewMostAtOnce") == 1, (
            f"{fetches} view fetches for one pick and two were in flight together "
            "-- the later-issued one can land first and paint a stale view over a fresh one")
