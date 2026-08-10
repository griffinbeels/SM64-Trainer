"""The Library's search box, in a real browser.

Round 12. `tests/test_library_search.py` proves what a search MEANS under
node; this proves the box is there, that typing swaps the course grid for the
results, and that a result actually opens its target -- the half a rule test
structurally cannot reach, and the half this project has shipped invisible
before.
"""
import shutil
import sys
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

from ui_fixture import serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'

# React/Preact reads `value` off the element's own property descriptor, so
# assigning `.value` directly and firing `input` is the only way a driven test
# reaches a controlled input -- `.claude/rules/ui-core.md`'s own note about
# reading in a SEPARATE tick applies to the assertion, not to this.
TYPE = """
(() => {
  const box = document.querySelector('.library-page .library-find-input');
  if (!box) return 'no search box';
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(box, %s);
  box.dispatchEvent(new Event('input', {bubbles: true}));
  return 'typed';
})()
"""


@pytest.fixture(scope="module")
def page():
    """No seeding, so auto-open does not race the course grid out of the way
    -- the same reason test_ui_library_nav.py keeps an `empty_page`."""
    with serve_ui(seed=False) as base, \
            driver.get_driver().launch(headless=True) as page:
        page.goto(f"{base}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.evaluate(CLICK_LIBRARY_TAB)
        page.wait_for(".library-page .library-courses", timeout_ms=15000)
        yield page


def clear(page):
    assert page.evaluate(TYPE % "''") == "typed"
    page.wait_ms(150)


def test_the_box_sits_above_the_course_grid(page):
    """His mock: a full-width input between the header and the first row."""
    verdict = page.evaluate("""
      (() => {
        const box = document.querySelector('.library-page .library-find-input');
        if (!box) return 'no search box on the landing grid';
        const grid = document.querySelector('.library-page .entity-grid');
        if (!grid) return 'no course grid';
        const order = box.compareDocumentPosition(grid);
        if (!(order & Node.DOCUMENT_POSITION_FOLLOWING))
          return 'the box is drawn below the grid';
        if (box.getBoundingClientRect().width < 200)
          return 'the box is ' + Math.round(box.getBoundingClientRect().width) + 'px wide';
        return 'ok';
      })()
    """)
    assert verdict == "ok", verdict


def test_typing_replaces_the_grid_with_result_rows(page):
    """His call over a panel floating on a dimmed grid: the results REPLACE
    the grid, so the page never holds two scrolling regions at once."""
    assert page.evaluate(TYPE % "'tick tock'") == "typed"
    page.wait_ms(200)
    verdict = page.evaluate("""
      (() => {
        const rows = document.querySelectorAll('.library-page .library-result');
        if (!rows.length) return 'no result rows';
        if (document.querySelector('.library-page .entity-grid'))
          return 'the course grid is still drawn beside the results';
        const names = Array.from(rows).map(
          (row) => row.querySelector('.library-result-name').textContent);
        if (!names.every((name) => name.includes('Tick Tock Clock')))
          return 'a row is not from the searched course: ' + JSON.stringify(names);
        return 'ok';
      })()
    """)
    assert verdict == "ok", verdict
    clear(page)


def test_clearing_the_box_puts_the_grid_back(page):
    assert page.evaluate(TYPE % "'tick'") == "typed"
    page.wait_ms(200)
    assert page.count(".library-page .entity-grid") == 0
    clear(page)
    assert page.count(".library-page .entity-grid") == 1, \
        "clearing the search left the results up"


def test_an_approach_name_finds_its_target_and_the_row_says_so(page):
    """A way of DOING a target, typed instead of the target's own name, finds
    it -- his call at capture ("typing LBLJ finds the target that documents
    it") and the reason the index ships approach names at all.

    The query is DERIVED from the real snapshot rather than hardcoded: a word
    some approach carries that no target label or group name does. A literal
    ("lblj") happened to appear in labels too, so the test passed on the wrong
    mechanism -- and the next sheet revision could do that to any word I
    picked by hand.
    """
    picked = page.evaluate("""
      (async () => {
        const fold = (text) => String(text).toLowerCase()
          .replace(/[^a-z0-9]+/g, ' ').trim();
        const index = await (await fetch('/api/library')).json();
        const labelWords = new Set();
        for (const group of index.groups)
          for (const target of group.targets)
            fold(group.group + ' ' + target.label).split(' ')
              .forEach((word) => labelWords.add(word));
        for (const group of index.groups)
          for (const target of group.targets)
            for (const name of (target.approach_names || []))
              for (const word of fold(name).split(' '))
                if (word.length > 4 && !labelWords.has(word))
                  return JSON.stringify({word, label: target.label, name});
        return JSON.stringify(null);
      })()
    """)
    import json as _json
    picked = _json.loads(picked)
    assert picked, ("no approach name carries a word its target's own label "
                    "does not -- the index is not shipping approach_names")

    assert page.evaluate(TYPE % _json.dumps(picked["word"])) == "typed"
    page.wait_ms(200)
    verdict = page.evaluate("""
      (() => {
        const rows = Array.from(document.querySelectorAll(
          '.library-page .library-result'));
        if (!rows.length) return 'the derived approach word found nothing';
        const subs = rows.map((row) => row.querySelector(
          '.library-result-sub').textContent);
        if (!subs.some((sub) => sub.startsWith('matched:')))
          return 'no row credits an approach: ' + JSON.stringify(subs);
        return 'ok';
      })()
    """)
    assert verdict == "ok", (
        f"{verdict} (query was {picked['word']!r} from approach "
        f"{picked['name']!r})")
    clear(page)


def test_a_result_opens_its_target_page(page):
    """The whole point of the round: one click lands on the page you would
    have walked two layers of grid to reach."""
    assert page.evaluate(TYPE % "'tick tock'") == "typed"
    page.wait_ms(200)
    page.evaluate(
        "document.querySelector('.library-page .library-result').click()")
    page.wait_for(".library-target", timeout_ms=15000)
    assert page.count(".library-target") == 1
    page.evaluate("""
      (() => { const back = document.querySelector('.library-page .entity-back');
               if (back) back.click(); })()
    """)
    page.wait_ms(200)


def test_a_query_matching_nothing_says_so(page):
    """An empty result list with no words is the state a search box is most
    often left in by a typo -- it must read as an answer, not as a blank
    page."""
    page.wait_for(".library-page .library-find-input", timeout_ms=15000)
    assert page.evaluate(TYPE % "'zzzznotathing'") == "typed"
    page.wait_ms(200)
    verdict = page.evaluate("""
      (() => {
        const note = document.querySelector('.library-page .library-find-empty');
        if (!note) return 'nothing said anything';
        if (!note.textContent.includes('zzzznotathing'))
          return 'the note does not quote the query: ' + note.textContent;
        return 'ok';
      })()
    """)
    assert verdict == "ok", verdict
    clear(page)


def test_a_runner_name_finds_the_targets_they_have_run(page):
    """2026-08-10, his ask straight after the round landed. The runner is
    DERIVED from the live roster rather than hardcoded, for the same reason
    the approach word is: a name picked by hand can match a label and pass on
    the wrong mechanism."""
    picked = page.evaluate("""
      (async () => {
        const fold = (text) => String(text).toLowerCase()
          .replace(/[^a-z0-9]+/g, ' ').trim();
        const index = await (await fetch('/api/library')).json();
        if (!index.runners) return JSON.stringify(null);
        const labelWords = new Set();
        for (const group of index.groups)
          for (const target of group.targets) {
            fold(group.group + ' ' + target.label).split(' ')
              .forEach((word) => labelWords.add(word));
            for (const name of (target.approach_names || []))
              fold(name).split(' ').forEach((word) => labelWords.add(word));
          }
        for (const [name, positions] of Object.entries(index.runners)) {
          const words = fold(name).split(' ').filter(Boolean);
          if (words.length === 1 && words[0].length > 4
              && !labelWords.has(words[0]) && positions.length > 1)
            return JSON.stringify({name, word: words[0], count: positions.length});
        }
        return JSON.stringify(null);
      })()
    """)
    import json as _json
    picked = _json.loads(picked)
    assert picked, "the index is not shipping a `runners` roster"

    assert page.evaluate(TYPE % _json.dumps(picked["word"])) == "typed"
    page.wait_ms(250)
    verdict = page.evaluate("""
      (() => {
        const rows = Array.from(document.querySelectorAll(
          '.library-page .library-result'));
        if (!rows.length) return 'the derived runner found nothing';
        const subs = rows.map((row) => row.querySelector(
          '.library-result-sub').textContent);
        if (!subs.some((sub) => sub.includes('has a time here')))
          return 'no row credits a runner: ' + JSON.stringify(subs.slice(0, 4));
        return 'ok';
      })()
    """)
    assert verdict == "ok", f"{verdict} (runner was {picked['name']!r})"
    clear(page)
