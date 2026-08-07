"""The Compare fold-in (library.js's `studyInCompare` + app.js's nav change).

Task 6, spec 2026-08-07-library-page. A render test, per this project's own
rule: unit tests plus `node --check` once shipped an invisible feature.

task-6-caveats.md point 3 is WHY these assertions look the way they do: the
brief's own words describe a "Study" stage rendering `Compare` INSIDE the
Library, which the caveats file overrules -- Compare's pane never moves (it
stays mounted in app.js exactly where it always was, so loaded media and sync
survive leaving and returning). Task 6 is a pure ROUTING change: the Compare
nav entry is deleted, and both the Library's own "Study in Compare" button and
the practice log's existing per-attempt Compare button now arrive at the same
pane via `enterCompare`. So this file checks `.compare-page` becoming the
VISIBLE pane, never a `.library-study` wrapper -- that selector never exists
by design.

Fixture: same as test_ui_library_tray.py -- `ui_fixture.py`'s default seeding
(star:2:4, "Fall onto the Caged Island") auto-opens a section with several
approaches, each carrying example cards with an enabled "+"."""
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'

# Same idiom as test_ui_library_tray.py's own helper: adds up to `n` examples
# via the open section's own enabled "+" buttons.
_ADD_N_EXAMPLES = """
(() => {{
  const cards = Array.from(document.querySelectorAll(
    '.library-section.open .library-example'));
  let added = 0;
  for (const card of cards) {{
    if (added >= {n}) break;
    const btn = card.querySelector('.library-example-plus');
    if (btn && !btn.disabled) {{ btn.click(); added++; }}
  }}
  return added;
}})()
"""

# Stubs `window.fetch` for exactly the two import routes -- everything else
# (including the real `/api/session`/`/api/replay/available` Compare fetches
# once we route there) passes through to the real fixture server, per
# task-6-brief.md step 3's own suggested fallback ("mock by pre-loading a stub
# window.fetch wrapper before click"; uilab's Page has no `route` method to
# intercept at the network layer). The SECOND `/api/compare/import` call fails
# -- deterministic because studyInCompare awaits each item before starting the
# next, so call order is tray order.
_STUB_FETCH = """
(() => {
  const real = window.fetch.bind(window);
  let calls = 0;
  window.__studyJobs = {};
  window.fetch = (url, opts) => {
    if (url === '/api/compare/import' && opts && opts.method === 'POST') {
      calls += 1;
      const jobId = 'study-test-job-' + calls;
      window.__studyJobs[jobId] = (calls === 2)
        ? { state: 'error', message: 'video unavailable' }
        : { state: 'done', comparison: { id: 9000 + calls } };
      return Promise.resolve(new Response(JSON.stringify({ job_id: jobId }),
        { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    if (typeof url === 'string' && url.startsWith('/api/compare/import/')) {
      const jobId = url.split('/').pop();
      const status = window.__studyJobs[jobId]
        || { state: 'error', message: 'unknown job' };
      return Promise.resolve(new Response(JSON.stringify(status),
        { status: 200, headers: { 'Content-Type': 'application/json' } }));
    }
    return real(url, opts);
  };
})()
"""


@pytest.fixture(scope="module")
def library_server():
    with serve_ui(arm_segment=FIXTURE_SEGMENT, seed_editor_fixtures=True) as base:
        yield base


def _text_of(page, selector, timeout_s=8, interval=0.05):
    """Poll for `selector`'s textContent rather than `wait_for` (which
    requires VISIBLE). A success routes away in the SAME Preact commit that
    renders the failure summary (studyInCompare calls setStudying/
    setStudyResult/enterCompare synchronously, batched into one render), so
    `.library-study-msg` exists on the Library pane the instant it is hidden
    behind Compare's -- it is never visible, by design, only present for
    when the user navigates back. Same poll idiom test_ui_library_tray.py's
    own removal test uses for the opposite case (waiting for absence)."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        text = page.evaluate(
            f"(() => {{ const el = document.querySelector('{selector}'); "
            "return el ? el.textContent : null; })()")
        if text:
            return text
        time.sleep(interval)
    raise AssertionError(f"{selector} never appeared with text")


@pytest.fixture
def library_page(library_server):
    """A FRESH page per test -- same reason test_ui_library_target.py gives:
    tray state must not leak between tests."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        yield page


def test_the_compare_nav_entry_is_gone(library_page):
    """Step 2: the sidebar no longer offers a way to open Compare directly --
    every route into it now goes through the Library."""
    result = library_page.evaluate("""
      (() => ({
        compare: document.querySelector('.nav-item[title="Compare"]'),
        library: document.querySelector('.nav-item[title="Library"]'),
      }))()
    """)
    assert result["compare"] is None, "the Compare nav entry should be gone"
    assert result["library"] is not None, "the Library nav entry should exist"


def test_a_partial_import_failure_renders_the_line_and_still_studies_the_rest(library_page):
    """Step 1 + Step 3's own contract: a failing item must not sink the
    batch. Two items go into the tray; the mock fails the SECOND import.
    Expect: (a) a visible '1 of 2 imported; ... failed: ...' line render in
    the Library (readable even after navigating away, since the pane stays
    mounted with display:none rather than unmounting), and (b) the page
    still routes to the Compare pane for the item that DID import."""
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=2))
    assert added == 2, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    library_page.evaluate(_STUB_FETCH)
    clicked = library_page.evaluate("""
      (() => {
        const btn = document.querySelector('.library-tray-study');
        if (!btn || btn.disabled) return 'button missing or disabled';
        btn.click();
        return 'clicked';
      })()
    """)
    assert clicked == "clicked", clicked

    message = _text_of(library_page, ".library-study-msg")
    assert "1 of 2 imported" in message, message
    assert "failed: video unavailable" in message, message

    library_page.wait_for(".compare-page", timeout_ms=8000)
    pane_display = library_page.evaluate("""
      (() => {
        const page = document.querySelector('.compare-page');
        const pane = page && page.closest('.view-pane');
        return pane ? getComputedStyle(pane).display : 'no .compare-page';
      })()
    """)
    assert pane_display != "none", pane_display


def test_a_fully_failed_batch_shows_the_line_and_does_not_navigate(library_page):
    """The other half of "must not sink the batch": when NOTHING imported,
    there is nothing to study, so the page stays on the Library rather than
    routing to an empty Compare pane."""
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added == 1, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    library_page.evaluate("""
      (() => {
        const real = window.fetch.bind(window);
        window.fetch = (url, opts) => {
          if (url === '/api/compare/import' && opts && opts.method === 'POST') {
            return Promise.resolve(new Response(
              JSON.stringify({ job_id: 'always-fails' }),
              { status: 200, headers: { 'Content-Type': 'application/json' } }));
          }
          if (typeof url === 'string' && url.startsWith('/api/compare/import/')) {
            return Promise.resolve(new Response(
              JSON.stringify({ state: 'error', message: 'private video' }),
              { status: 200, headers: { 'Content-Type': 'application/json' } }));
          }
          return real(url, opts);
        };
      })()
    """)
    library_page.evaluate("document.querySelector('.library-tray-study').click()")
    message = _text_of(library_page, ".library-study-msg")
    assert "0 of 1 imported" in message, message
    assert "failed: private video" in message, message

    still_on_library = library_page.evaluate(
        "document.querySelector('.library-target-page') != null")
    assert still_on_library, "a fully-failed batch should not navigate away"
