"""The local player workflow, through the shipped drawer and a scratch DB."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from find_uilab import find_uilab

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver
from uilab_project import PROJECT, STORIES

STORY = next(story for story in STORIES if story.name == "input-timeline")


def fill(page, selector, value):
    page.evaluate(f"""(() => {{
      const el = document.querySelector({json.dumps(selector)});
      el.value = {json.dumps(value)};
      el.dispatchEvent(new Event('input', {{bubbles:true}}));
    }})()""")


def button(page, text, scope=".input-template-tools"):
    page.evaluate(f"""(() => {{
      const buttons = [...document.querySelectorAll({json.dumps(scope + ' button')})];
      const found = buttons.find(el => el.textContent.trim() === {json.dumps(text)});
      if (!found || found.disabled) throw new Error('Button unavailable: ' + {json.dumps(text)});
      found.click();
    }})()""")
    page.wait_ms(150)


@pytest.fixture(params=[(1500, 1100), (850, 1180)], ids=["desktop", "narrow"])
def page(request):
    with PROJECT.open() as url, get_driver().launch(viewport=request.param) as opened:
        opened.goto(url)
        opened.wait_for(PROJECT.ready_selector)
        opened.evaluate(STORY.setup)
        opened.wait_for(".input-lanes")
        yield opened
        # The overlay test's reload can abort the UI log's POST in flight
        # (full run 35686423614). That log drops an observation rather than
        # ever block its page (ui/uilog.js), so the abort is this test's own
        # doing; every other failed request still counts.
        problems = [problem for problem in opened.problems()
                    if not (problem.startswith("requestfailed: ")
                            and problem.endswith("/api/uilog (net::ERR_ABORTED)"))]
        assert problems == [], "\n".join(problems)


def check_overlay_preference_survives_reload(page):
    page.click('.input-overlay-controls summary')
    page.evaluate("""(() => {
      const label = [...document.querySelectorAll('.input-overlay-switches label')]
        .find(el => el.textContent.trim() === 'Stick');
      label.querySelector('input').click();
    })()""")
    page.wait_ms(100)
    assert page.count('.stick-line.is-template') == 0
    assert page.count('.stick-line:not(.is-template)') == 2
    assert page.count('.input-bar.is-template') > 0

    # Saving refreshes the parent log and rank in parallel. The template can
    # paint before the rank response; reload only after that real response,
    # otherwise this test itself aborts it and records ERR_ABORTED as a fault.
    assert page.evaluate("!!window.__templateRankRefresh")
    page.evaluate("window.__templateRankRefresh")
    # A new mount must retain the preference; fresh drawers still carry data.
    page.evaluate("location.reload()")
    page.wait_for(PROJECT.ready_selector)
    page.evaluate(STORY.setup)
    page.wait_for('.input-lanes')
    assert page.count('.stick-line.is-template') == 0
    assert page.count('.stick-line:not(.is-template)') == 2


def test_save_import_switch_export_remove_and_persist_overlay(page, tmp_path):
    page.evaluate("""(() => {
      const original = window.fetch;
      window.__templateRankRefresh = null;
      window.fetch = (url, options) => {
        const response = original(url, options);
        if (url === '/api/marelo') {
          window.__templateRankRefresh = response.then(r => r.clone().json());
        }
        return response;
      };
    })()""")
    button(page, "Save as template")
    fill(page, '.input-template-manager input', 'My clean run')
    button(page, "Save template", ".input-template-manager")
    page.wait_for('.input-template-note strong:text-is("My clean run")')
    assert page.evaluate("document.querySelector('.input-template-note strong').textContent") == "My clean run"
    assert page.count('.modal-backdrop') == 0

    check_overlay_preference_survives_reload(page)

    original = page.evaluate("[...document.querySelectorAll('.input-template-note strong')].map(el=>el.textContent)")
    assert original == ["My clean run"]
    # Hand-authored example deliberately differs in target/author/strategy.
    text = "\n".join(["# sm64-inputs v2", "target: star 24 1", "strategy: visitor strategy",
        "version: us", "fps: 30", "origin: authored", "author: visiting player",
        "name: Visiting player setup", "--",
        "0-3 A +84,+0 0x04000440 0 12", "4 A +127,+0 0x04000440 0 12",
        "5-6 - gap", "7-9 B +84,+0 0x03000880 0 20", ""])
    path = tmp_path / "visitor.inputs.txt"
    path.write_text(text, encoding="utf-8")
    button(page, "Import inputs")
    page.set_input_files('.input-template-manager input[type=file]', str(path))
    page.wait_ms(150)
    page.wait_for('.input-import-preview')
    assert "visiting player" in page.evaluate("document.querySelector('.input-import-preview').textContent")
    assert "10 frames" in page.evaluate("document.querySelector('.input-import-preview').textContent")
    button(page, "Import and use template", ".input-template-manager")
    page.wait_for('.input-template-note strong:text-is("Visiting player setup")')
    assert page.evaluate("document.querySelector('.input-template-note strong').textContent") == "Visiting player setup"
    assert page.count('.stick-line.is-template') == 0
    assert page.evaluate("document.querySelector('.speed-line.is-template').getAttribute('d').match(/M /g).length") == 2
    page.click('.input-overlay-controls summary')
    page.evaluate("document.querySelector('.input-overlay-switches input').click()")
    page.wait_ms(80)
    heights = page.evaluate("[...document.querySelector('.stick-line.is-x.is-template').getAttribute('d').matchAll(/M [^,]+,([^ ]+)/g)].map(m => Number(m[1]))")
    assert len(heights) == 2 and heights[0] == heights[1]  # same raw 84 across a gap
    page.evaluate("document.querySelector('.input-overlay-switches input').click()")

    button(page, "Templates")
    page.wait_for('.input-template-list li')
    assert "visiting player" in page.evaluate("document.querySelector('.input-template-list').textContent")
    fill(page, '.input-template-manager input[type=search]', "My clean run")
    page.wait_ms(80)
    button(page, "Use for this strategy", ".input-template-list")
    page.wait_for('.input-template-note strong:text-is("My clean run")')
    assert page.evaluate("document.querySelector('.input-template-note strong').textContent") == "My clean run"
    assert page.count('.input-template-manager') == 1  # refresh must not unmount the modal
    button(page, "Export", ".input-template-list")
    button(page, "Remove", ".input-template-list")
    button(page, "Remove template", ".input-template-list")
    page.wait_for('body:not(:has(.input-template-note)):not(:has(.input-template-list li))')
    assert page.count('.input-template-list li') == 0
    assert page.count('.input-template-note') == 0
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")


def test_overlay_preferences_update_other_mounted_timelines(page):
    # Render another real timeline as another consumer of the same preference.
    # Its data comes from the same fixture API, not a mock of its DOM.
    page.evaluate("""(async () => {
      const {h, render} = await import('/ui/vendor/preact.module.js');
      const {InputTimeline} = await import('/ui/components/inputtimeline.js');
      const response = await fetch('/api/inputs/templates');
      const rows = (await response.json()).templates;
      const attempt = rows.map(r => /^attempt:(\\d+)$/.exec(r.origin)).find(Boolean);
      if (!attempt) throw new Error('fixture has no captured template');
      const host = document.createElement('div'); host.id = 'second-input-timeline';
      document.body.appendChild(host);
      render(h(InputTimeline, {attemptId: Number(attempt[1])}), host);
    })()""")
    page.wait_for('#second-input-timeline .input-lanes')
    assert page.count('.stick-line.is-template') == 4
    page.click('.attempt-drawer .input-overlay-controls summary')
    page.evaluate("document.querySelector('.attempt-drawer .input-overlay-switches input').click()")
    page.wait_ms(100)
    assert page.count('.stick-line.is-template') == 0
    assert page.count('.stick-line:not(.is-template)') == 4


def test_template_refresh_failure_keeps_library_and_can_retry(page):
    button(page, "Templates")
    page.wait_for('.input-template-list')
    page.evaluate("""(async () => {
      const original = window.fetch;
      window.fetch = (url, ...args) => String(url).match(/\\/attempts\\/\\d+\\/inputs(?:\\?|$)/)
        ? Promise.reject(new Error('offline test')) : original(url, ...args);
      window.restoreInputFetch = () => { window.fetch = original; };
      const {templatesChanged} = await import('/ui/inputpreferences.js');
      templatesChanged();
    })()""")
    page.wait_for('.input-timeline [role=alert]')
    assert page.count('.input-template-manager') == 1
    assert page.count('.input-lanes') == 1
    page.evaluate('window.restoreInputFetch()')
    button(page, "Retry", ".input-timeline")
    page.wait_for('.input-timeline:has(.input-lanes):not(:has([role=alert]))')
    assert page.count('.input-template-manager') == 1


def test_comparison_uses_shared_rows_and_two_indicators_per_dial(page):
    assert page.count('.input-lane.is-actions') == 1
    assert page.count('.input-lane.is-actions .action-span:not(.is-template)') > 0
    assert page.count('.input-lane.is-actions .action-span.is-template') > 0
    # Start at a real template frame, beyond the clip's lead-in.
    page.evaluate("document.querySelector('.action-span.is-template').click()")
    page.wait_ms(100)
    assert page.count('.input-inspector .controller-panel') == 2
    assert page.count('.input-inspector .stick-box:not(.facing-dial)') == 1
    assert page.count('.stick-box-stem:not(.is-template)') == 1
    assert page.count('.stick-box-stem.is-template') == 1
    assert page.count('.facing-dial') == 1
    assert page.count('.facing-needle:not(.is-template)') == 1
    assert page.count('.facing-needle.is-template') == 1


def test_exported_name_autofills_paste_and_copy_keeps_the_document(page):
    page.evaluate("""Object.defineProperty(navigator, 'clipboard', {configurable:true,
      value: {writeText: async text => { window.copiedInputs = text; }}})""")
    button(page, "Templates")
    page.wait_for('.input-template-list li')
    expected = page.evaluate("document.querySelector('.input-template-list li strong').textContent")
    button(page, "Copy inputs", ".input-template-list li:first-child")
    page.wait_for('.input-template-manager [role=status]')
    copied = page.evaluate("window.copiedInputs")
    assert f"name: {expected}" in copied
    button(page, "Import inputs", ".input-template-manager")
    fill(page, '.input-template-manager textarea', copied)
    button(page, "Preview import", ".input-template-manager")
    page.wait_for('.input-import-preview')
    assert page.evaluate("document.querySelector('.input-template-manager input[maxlength]').value") == expected
    fill(page, '.input-template-manager input[maxlength]', "My edited name")
    fill(page, '.input-template-manager textarea', copied + "\n# a note")
    button(page, "Preview import", ".input-template-manager")
    page.wait_for('.input-import-preview')
    assert page.evaluate("document.querySelector('.input-template-manager input[maxlength]').value") == "My edited name"


def test_choosing_a_file_keeps_a_name_the_player_already_typed(page, tmp_path):
    text = "\n".join(["# sm64-inputs v2", "target: star 2 2", "version: us",
                       "fps: 30", "origin: authored", "name: File's name", "--", "0 A neutral"])
    path = tmp_path / "filename.inputs.txt"
    path.write_text(text, encoding="utf-8")
    button(page, "Import inputs")
    fill(page, '.input-template-manager input[maxlength]', "My chosen name")
    page.set_input_files('.input-template-manager input[type=file]', str(path))
    page.wait_for('.input-import-preview')
    assert page.evaluate("document.querySelector('.input-template-manager input[maxlength]').value") == "My chosen name"
