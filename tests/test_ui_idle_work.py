"""Work the page does while nothing he can see changes (perf audit 2026-09-25).

Four witnesses, each driven through the real app on the UI fixture:

  * an unchanged pause poll (every 5 s) renders nothing -- it used to hand
    the store a fresh object and re-render the whole app, hidden panes too;
  * an event that cannot move the Daily Star or a goal refetches only the
    view and MARELO, and the hidden Library refetches nothing until it is
    shown -- one refresh event used to cost session + marelo + daily-star +
    entry_goals + one strategies fetch per Library entity;
  * a paused Compare clip asks for no animation frames -- its playhead loop
    ran at display rate from the first clip loaded until the page closed;
  * leaving the Compare tab pauses its clips -- they kept playing, sound on,
    behind whatever tab he moved to.

MARELO's own timing is deliberately NOT changed: GET /api/marelo drives the
rank-up celebration, so every refresh event still fetches it.
"""
import base64
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import FIXTURE_SEGMENT, _run_coro, serve_ui_live  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from sm64_events.core.events import Event  # noqa: E402

# Every fetch the page makes, by path, in order.
LOG_FETCHES = """(() => {
  const real = window.fetch;
  window.__fetched = [];
  window.fetch = (url, opts) => {
    if (typeof url === 'string') window.__fetched.push(url.split('?')[0]);
    return real(url, opts);
  };
  return true;
})()"""

# App renders, counted through Preact's own render hook.
COUNT_APP_RENDERS = """(async () => {
  const { options } = await import('/ui/vendor/preact.module.js');
  window.__appRenders = 0;
  const previous = options.__r;
  options.__r = (vnode) => {
    if (vnode && vnode.type && vnode.type.name === 'App') window.__appRenders += 1;
    if (previous) previous(vnode);
  };
  return true;
})()"""

# Animation frames asked for by the Compare playhead (videosync.js) only.
COUNT_PLAYHEAD_FRAMES = """(() => {
  const real = window.requestAnimationFrame.bind(window);
  window.__playheadFrames = 0;
  window.requestAnimationFrame = (callback) => {
    if ((new Error().stack || '').includes('videosync.js')) window.__playheadFrames += 1;
    return real(callback);
  };
  return true;
})()"""

# Paths a refresh event can cause. Timed polls (/api/pause, replay status)
# land whenever they like and are not part of the question.
EVENT_FETCHES = ("/api/session", "/api/marelo", "/api/daily-star",
                 "/api/scorecard/entry_goals", "/api/target/strategies")

COMPARE_VIDEOS = "[...document.querySelectorAll('.compare-page video')]"


@pytest.fixture(scope="module")
def live():
    with serve_ui_live(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as served:
        yield served


def _open(base):
    page_cm = get_driver().launch(headless=True, viewport=(1500, 1000))
    page = page_cm.__enter__()
    page.goto(f"{base}/ui/index.html")
    page.wait_for(".log-list-card")
    return page_cm, page


def _tab(page, name):
    page.evaluate(f"document.querySelector('.app-sidebar .nav-item[title=\"{name}\"]').click()")


def _wait_until(page, expression, ms=10000):
    page.evaluate(f"""new Promise((done, fail) => {{
      const until = performance.now() + {ms};
      (function poll() {{
        if ({expression}) return done(true);
        if (performance.now() > until) return fail(new Error('timed out: ' + {expression!r}));
        setTimeout(poll, 50);
      }})();
    }})""")


def _publish(service, kind, payload=None):
    event = Event(type=kind, frame=0, timestamp_utc=datetime.now(timezone.utc),
                  payload=payload or {})
    _run_coro(service.broadcaster.publish(event))


def _event_fetches(page):
    return [path for path in page.evaluate("window.__fetched") if path in EVENT_FETCHES]


def test_an_unchanged_pause_poll_renders_nothing(live):
    base, _service = live
    page_cm, page = _open(base)
    try:
        page.wait_ms(1500)
        page.evaluate(LOG_FETCHES)
        page.evaluate(COUNT_APP_RENDERS)
        _wait_until(page, "window.__fetched.filter((p) => p === '/api/pause').length >= 1", 8000)
        page.wait_ms(500)
        assert page.evaluate("window.__appRenders") == 0, (
            "a pause poll that answered the same {paused, reason} re-rendered the app")
        # Control: a real change still renders.
        page.evaluate("fetch('/api/pause', {method: 'POST', headers: {'Content-Type': "
                      "'application/json'}, body: JSON.stringify({paused: true})})")
        _wait_until(page, "window.__fetched.filter((p) => p === '/api/pause').length >= 3", 8000)
        page.wait_ms(500)
        assert page.evaluate("window.__appRenders") >= 1, "the paused state never reached the app"
    finally:
        page.evaluate("fetch('/api/pause', {method: 'POST', headers: {'Content-Type': "
                      "'application/json'}, body: JSON.stringify({paused: false})})")
        page_cm.__exit__(None, None, None)


def test_refresh_events_fetch_only_what_they_can_move(live):
    base, service = live
    page_cm, page = _open(base)
    try:
        page.evaluate(LOG_FETCHES)
        # Control: the Library opens the last practised entry on its first
        # visit and fetches its strategies -- the fetch that must then stop.
        _tab(page, "Library")
        _wait_until(page, "window.__fetched.includes('/api/target/strategies')")
        _tab(page, "Practice")
        page.wait_ms(1500)

        page.evaluate("window.__fetched = []")
        _publish(service, "mario_acted", {"action": 0x0C400201})
        page.wait_ms(1200)
        assert _event_fetches(page) == [], "an event outside REFRESH_ON fetched something"

        _publish(service, "segment_progress", {"segment_id": FIXTURE_SEGMENT})
        page.wait_ms(1500)
        fetched = _event_fetches(page)
        assert sorted(fetched) == ["/api/marelo", "/api/session"], (
            f"segment_progress cannot move a PB, rank or goal, yet fetched {fetched}")

        page.evaluate("window.__fetched = []")
        _publish(service, "goal_changed", {"key": "star:2:4"})
        page.wait_ms(1500)
        fetched = _event_fetches(page)
        for path in ("/api/session", "/api/marelo", "/api/daily-star",
                     "/api/scorecard/entry_goals"):
            assert path in fetched, f"goal_changed did not refetch {path}: {fetched}"
        assert "/api/target/strategies" not in fetched, (
            "the hidden Library refetched its strategies on a refresh event")

        page.evaluate("window.__fetched = []")
        _tab(page, "Library")
        _wait_until(page, "window.__fetched.includes('/api/target/strategies')", 5000)
    finally:
        page_cm.__exit__(None, None, None)


def _study_a_real_clip(page):
    """Study one Library example in Compare, then play a real clip under it."""
    _tab(page, "Library")
    page.wait_for(".library-section.open .library-division-head")
    page.evaluate("document.querySelectorAll('.library-section.open .library-division-head')"
                  ".forEach((head) => head.getAttribute('aria-expanded') === 'true' || head.click())")
    page.wait_for(".library-section.open .library-example")
    added = page.evaluate("""(() => {
      for (const card of document.querySelectorAll('.library-section.open .library-example')) {
        const plus = card.querySelector('.library-example-plus');
        if (plus && !plus.disabled) { plus.click(); return 1; }
      }
      return 0; })()""")
    assert added == 1, "fixture drift: no example could be added to the tray"
    page.wait_ms(300)
    page.evaluate("document.querySelector('.library-tray-study').click()")
    _wait_until(page, f"{COMPARE_VIDEOS}.length >= 1", 15000)
    clip = base64.b64encode((REPO / "tests/fixtures/recording-controls.mp4").read_bytes()).decode()
    page.evaluate(f"""(() => {{
      const bytes = Uint8Array.from(atob('{clip}'), (c) => c.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], {{type: 'video/mp4'}}));
      for (const video of {COMPARE_VIDEOS}) {{ video.muted = true; video.loop = true; video.src = url; }}
      return true; }})()""")
    _wait_until(page, f"{COMPARE_VIDEOS}.every((video) => video.readyState >= 2)")


def _transport(page, word):
    page.evaluate(f"""[...document.querySelectorAll('.compare-transport button')]
      .find((button) => button.textContent.trim().endsWith('{word}')).click()""")


def _playhead_frames_over(page, ms):
    page.evaluate("window.__playheadFrames = 0")
    page.wait_ms(ms)
    return page.evaluate("window.__playheadFrames")


def test_a_paused_compare_clip_asks_for_no_frames(live):
    base, _service = live
    page_cm, page = _open(base)
    try:
        _study_a_real_clip(page)
        page.evaluate(COUNT_PLAYHEAD_FRAMES)
        _transport(page, "Play")
        _wait_until(page, f"{COMPARE_VIDEOS}.every((video) => !video.paused)")
        # Control: a playing clip's playhead follows it every frame.
        assert _playhead_frames_over(page, 500) > 10, "the playhead stopped following a playing clip"
        _transport(page, "Pause")
        page.wait_ms(100)
        assert _playhead_frames_over(page, 1000) == 0, (
            "a paused clip's playhead kept asking for animation frames")
        # A paused seek still moves the playhead, once.
        page.evaluate(f"{COMPARE_VIDEOS}[0].currentTime = 0.5")
        page.wait_ms(400)
        left = page.evaluate("parseFloat(document.querySelector('.compare-page .wa-playhead').style.left)")
        duration = page.evaluate(f"{COMPARE_VIDEOS}[0].duration")
        assert abs(left - 100 * 0.5 / duration) < 1, f"the playhead sat at {left}% after a paused seek"
        assert _playhead_frames_over(page, 500) == 0
    finally:
        page_cm.__exit__(None, None, None)


def test_leaving_compare_pauses_its_clips(live):
    base, _service = live
    page_cm, page = _open(base)
    try:
        _study_a_real_clip(page)
        _transport(page, "Play")
        _wait_until(page, f"{COMPARE_VIDEOS}.every((video) => !video.paused)")
        _tab(page, "Practice")
        page.wait_ms(500)
        assert page.evaluate(f"{COMPARE_VIDEOS}.every((video) => video.paused)"), (
            "Compare kept playing behind the Practice tab")
    finally:
        page_cm.__exit__(None, None, None)
