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

FIX ROUND 1 (controller + reviewer, live-verified): the FIRST version of this
file only ever asserted the pane was VISIBLE, never that anything was OPEN
inside it -- Study imported clips and showed the user an empty pane and a
"load existing" dropdown to hunt through. Every test that drives a successful
import now asserts `.compare-cmp` COUNT, never just pane visibility, per this
project's own rule that a guard asserting existence rather than content is
how an invisible feature ships.

FIX ROUND 2 (controller, root cause named three times in ui-core.md): fix
round 1's own content assertions still ran against a HAND-BUILT fake
`/api/compare/view` in this file, because `tools/ui_fixture.py::serve_ui()`
had NO Compare backend at all -- `/api/compare/import` and `/api/compare/view`
both 404d under it regardless of what the app code did. That fake could drift
from the real `views.py::build_compare_view` (it did: it filtered `saved` by
entity only, ignoring `strat`, so it would have stayed green over the exact
`item.strat`-vs-`body.strat` drift fix round 1 fixed). `serve_ui` now wires a
REAL `CompareService` (real sqlite `comparisons` table, real `/api/compare/
view`) -- only the network download and ffmpeg re-encode are stubbed, the
same idiom `tests/test_compare_service.py::_svc` already uses server-side.
Every test below drives the REAL import/view/PUT routes; `_STUB_FETCH` here
is now a thin WRAPPER, not a fake -- it forces at most ONE targeted `/api/
compare/import` call to fail without ever reaching the real backend for that
call (so a "failure" scenario needs no cooperation from the download stub),
and every other call passes straight through to `real()`.

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

# FIX ROUND 2: no longer a fake backend -- everything but ONE optional call
# passes straight through to the REAL, now-real `/api/compare/*` routes
# (real import, real db, real `/api/compare/view`). The only override: the
# Nth `/api/compare/import` POST (1-based, `FAIL_ON_CALL`) is answered with a
# synthetic job id that reports "error" on poll, WITHOUT ever reaching the
# real backend for that call -- so a failure test needs no cooperation from
# the fixture's download stub and creates no real db row for the item that
# "failed". `null` means nothing fails; every import in the batch is real.
#
# Every call (including passthroughs) is logged to `window.__fetchCalls` so a
# test can assert the ABSENCE of a call, not just the presence of one (FIX
# ROUND 1, item 4 -- "no PUT was issued" needs a record of what WAS issued).
#
# `studyInCompare` awaits each item before starting the next, so call order
# is deterministically tray order.
_STUB_FETCH = """
(() => {{
  const real = window.fetch.bind(window);
  let calls = 0;
  const fakeJobs = new Set();
  window.__fetchCalls = [];
  const FAIL_ON_CALL = {fail_on_call};
  window.fetch = (url, opts) => {{
    const method = (opts && opts.method) || 'GET';
    window.__fetchCalls.push(method + ' ' + url);
    if (url === '/api/compare/import' && method === 'POST') {{
      calls += 1;
      if (calls === FAIL_ON_CALL) {{
        const jobId = 'fixture-fail-' + calls;
        fakeJobs.add(jobId);
        return Promise.resolve(new Response(JSON.stringify({{ job_id: jobId }}),
          {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}));
      }}
      return real(url, opts);
    }}
    if (typeof url === 'string' && url.startsWith('/api/compare/import/')) {{
      const jobId = url.split('/').pop();
      if (fakeJobs.has(jobId)) {{
        return Promise.resolve(new Response(JSON.stringify(
          {{ state: 'error', message: 'video unavailable' }}),
          {{ status: 200, headers: {{ 'Content-Type': 'application/json' }} }}));
      }}
      return real(url, opts);
    }}
    return real(url, opts);
  }};
}})()
"""


def _stub_fetch(page, fail_on_call="null"):
    page.evaluate(_STUB_FETCH.format(fail_on_call=fail_on_call))


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


def _count(page, selector, timeout_s=8, interval=0.05, at_least=1):
    """Poll for `document.querySelectorAll(selector).length >= at_least`,
    then return the count. Same visibility caveat as `_text_of` -- `.compare-
    cmp` renders on a pane that may already be `display:none` again by the
    time a later assertion in the SAME test looks at it (e.g. after
    navigating away once already), so this polls presence in the DOM, not
    visibility."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        count = page.evaluate(
            f"document.querySelectorAll('{selector}').length")
        if count >= at_least:
            return count
        time.sleep(interval)
    return page.evaluate(f"document.querySelectorAll('{selector}').length")


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


def test_a_successful_study_opens_every_imported_comparison(library_page):
    """FIX ROUND 1, CRITICAL. The bug the reviewer found live: Study imported
    the clips and left the Compare pane showing NONE of them (`.compare-cmp`
    count 0, both stuck in "load existing"). Two items, both succeed -- assert
    `.compare-cmp` count equals what was actually imported, not merely that
    the pane became visible."""
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=2))
    assert added == 2, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    _stub_fetch(library_page)  # fail_on_call=null -- nothing fails
    clicked = library_page.evaluate("""
      (() => {
        const btn = document.querySelector('.library-tray-study');
        if (!btn || btn.disabled) return 'button missing or disabled';
        btn.click();
        return 'clicked';
      })()
    """)
    assert clicked == "clicked", clicked

    library_page.wait_for(".compare-page", timeout_ms=8000)
    open_count = _count(library_page, ".compare-cmp", at_least=2)
    assert open_count == 2, (
        f"expected 2 open comparisons ({open_count} rendered) -- Study must "
        "OPEN what it imported, not just navigate to an empty pane")


def test_a_partial_import_failure_renders_the_line_and_still_studies_the_rest(library_page):
    """Step 1 + Step 3's own contract: a failing item must not sink the
    batch. Two items go into the tray; the mock fails the SECOND import.
    Expect: (a) a visible '1 of 2 imported; ... failed: ...' line render in
    the Library (readable even after navigating away, since the pane stays
    mounted with display:none rather than unmounting), (b) the page routes
    to the Compare pane for the item that DID import, and (c) that ONE
    comparison is actually OPEN there (FIX ROUND 1 -- content, not just a
    visible pane)."""
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=2))
    assert added == 2, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    _stub_fetch(library_page, fail_on_call="2")
    clicked = library_page.evaluate("""
      (() => {
        const btn = document.querySelector('.library-tray-study');
        if (!btn || btn.disabled) return 'button missing or disabled';
        btn.click();
        return 'clicked';
      })()
    """)
    assert clicked == "clicked", clicked

    message = _text_of(library_page, ".library-study-msg-line.is-error")
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

    open_count = _count(library_page, ".compare-cmp", at_least=1)
    assert open_count == 1, (
        f"expected the ONE successfully-imported comparison open "
        f"({open_count} rendered)")


def test_a_fully_failed_batch_shows_the_line_and_does_not_navigate(library_page):
    """The other half of "must not sink the batch": when NOTHING imported,
    there is nothing to study, so the page stays on the Library rather than
    routing to an empty Compare pane."""
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added == 1, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    _stub_fetch(library_page, fail_on_call="1")
    library_page.evaluate("document.querySelector('.library-tray-study').click()")
    message = _text_of(library_page, ".library-study-msg-line.is-error")
    assert "0 of 1 imported" in message, message
    assert "failed: video unavailable" in message, message

    still_on_library = library_page.evaluate(
        "document.querySelector('.library-target-page') != null")
    assert still_on_library, "a fully-failed batch should not navigate away"


def test_an_untrimmed_item_is_never_put_to_compare_videos(library_page):
    """FIX ROUND 1, item 4. `trayToImport`'s `edit` is null for an untrimmed
    tray item (every example card adds with `trim: null`, librarytarget.js),
    and the server-side dedupe on `source_ref` within (entity_key, strat)
    keeps whatever trim a re-imported comparison already has saved -- an
    unconditional PUT would silently overwrite a user's own alignment on
    re-add. Asserts the ABSENCE of the PUT directly, from the fetch call
    log, rather than relying on the incidental 404 a real unmocked route
    would produce (the reviewer's own reproduction: mutating the `if (edit)`
    guard away hits the real, unmocked PUT route and 404s, which reports the
    item as FAILED for the wrong reason and would silently stop proving
    anything the moment a future round also mocks that route)."""
    added = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added == 1, added
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    _stub_fetch(library_page)
    library_page.evaluate("document.querySelector('.library-tray-study').click()")
    # Wait for the BATCH to finish, not for navigation -- against the real
    # backend (fix round 2), an unconditional PUT sending `edit: null` as its
    # body errors, which fails the item entirely and means Study never
    # routes anywhere at all. `.library-tray-study` re-enabling (studying ->
    # false) is true whether the batch succeeded or not, so it is the right
    # thing to wait for here regardless of which way this test is run.
    for _ in range(100):
        if not library_page.evaluate(
                "document.querySelector('.library-tray-study').disabled"):
            break
        time.sleep(0.05)

    calls = library_page.evaluate("window.__fetchCalls")
    put_calls = [c for c in calls if c.startswith("PUT /api/compare/videos/")]
    assert put_calls == [], f"expected NO PUT for an untrimmed item, got: {put_calls}"


def test_a_mixed_entity_study_names_what_it_is_showing_and_what_else_imported(library_page):
    """FIX ROUND 1, item 2 (controller ruling, confirmed by the reviewer).
    The tray is ordinary cross-entity state (librarytray.js's own header),
    but Compare can only ever show ONE (entity, strat) pane -- before this
    fix a mixed-entity Study said NOTHING about the ones it couldn't show
    (StudyMessage only rendered on failure, and Compare's own "load
    existing" is entity-scoped so it can't even hint others exist). Adds one
    item on star:2:4 (already open), navigates to its sibling star:2:5 (same
    course group, same navigation the tray's own cross-entity test in
    test_ui_library_tray.py uses) and adds one there too, then asserts the
    note names what is shown, says more was imported elsewhere, and that
    `.compare-cmp` only ever shows the ONE entity Compare can actually
    display."""
    added_first = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added_first == 1, added_first
    library_page.wait_for(".library-tray-chip", timeout_ms=5000)

    library_page.evaluate("document.querySelector('.entity-back').click()")
    library_page.wait_for(".library-courses", timeout_ms=15000)
    opened_group = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-courses .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === "2. Whomp's Fortress");
        if (!cell) return "no Whomp's Fortress group cell";
        cell.click();
        return 'clicked';
      })()
    """)
    assert opened_group == "clicked", opened_group
    library_page.wait_for(".library-group", timeout_ms=15000)
    picked_target = library_page.evaluate("""
      (() => {
        const cell = Array.from(document.querySelectorAll('.library-group .starcell'))
          .find((c) => c.querySelector('.starname')?.textContent === 'Blast Away the Wall');
        if (!cell) return 'no Blast Away the Wall cell';
        cell.click();
        return 'clicked';
      })()
    """)
    assert picked_target == "clicked", picked_target
    library_page.wait_for(".library-section.open .library-example", timeout_ms=10000)

    added_second = library_page.evaluate(_ADD_N_EXAMPLES.format(n=1))
    assert added_second == 1, added_second
    library_page.wait_for(".library-tray-chip:nth-child(2)", timeout_ms=5000)

    _stub_fetch(library_page)
    library_page.evaluate("document.querySelector('.library-tray-study').click()")
    library_page.wait_for(".compare-page", timeout_ms=8000)

    note = _text_of(library_page, ".library-study-msg-line:not(.is-error)")
    assert "Showing" in note, note
    assert "also imported" in note, note
    # star:2:4 is the FIRST tray item (added before navigating away), so it
    # is the one Compare lands on and opens -- star:2:5's import is real
    # (server-side, its own bucket) but not shown in THIS pane.
    assert "2:5" in note or "Blast Away the Wall" in note, note

    open_count = _count(library_page, ".compare-cmp", at_least=1)
    assert open_count == 1, (
        f"only the primary entity's comparison should be open here "
        f"({open_count} rendered)")
