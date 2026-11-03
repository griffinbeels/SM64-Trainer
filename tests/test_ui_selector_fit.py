"""The selector at the smallest window: its row scrolls inside its card, and its
caps follow the practice log's Strategy / Overall choice.

His reports, 2026-11-03, on beta.36 at the minimum window:
  * "when there are too many options for the selector, it bleeds horizontally,
    adds a scrollbar, and then I can't see the right part of the screen ...
    there should just be a slider for the segment selector, WITHOUT changing
    the overall width of the page."
  * "change the icon that's displayed in the star selector to match the option
    you have selected for the practice log ... Need to stay in sync wherever
    this is displayed."
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_fixture import FIXTURE_COURSE, FIXTURE_STAR, serve_ui_live  # noqa: E402

CARD = ".log-card.log-card-active"
ROW = ".selector-card .starrow.segcells"
# 834 is the page inside his smallest window: 850 outside, less the frame.
WIDTHS = (834, 850, 1200)
FIT = """(() => {
  const pane = document.querySelector('.practice-page').getBoundingClientRect();
  const row = document.querySelector('%s');
  const cards = [...document.querySelectorAll('.practice-page > *, .practice-card, .log-card, .attempts-tools')];
  return {page: [document.documentElement.scrollWidth, innerWidth],
    pane: pane.right, widest: Math.max(...cards.map(el => el.getBoundingClientRect().right)),
    row: [row.scrollWidth, row.clientWidth], rowRight: row.getBoundingClientRect().right,
    cells: row.children.length, cellWidth: row.children[0].getBoundingClientRect().width};
})()""" % ROW


def _plant_segments(page):
    """More castle segments than fit, the shape his lobby has."""
    def more(route):
        answer = route.fetch()
        body = answer.json()
        targets = body.get("segment_targets") or []
        model = next((row for row in targets if 6 in (row.get("start_levels") or [])), targets[0])
        body["segment_targets"] = [*targets, *({**model, "segment_id": 90000 + number,
                                                "name": f"WF to a long destination {number}"}
                                               for number in range(14))]
        route.fulfill(response=answer, json=body)
    page.route("**/api/session*", more)


@pytest.mark.parametrize("width", WIDTHS)
def test_a_long_selector_scrolls_inside_its_card_and_the_page_keeps_its_width(width):
    with serve_ui_live(castle_stage=1) as (base, _service), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": width, "height": 1000})
            _plant_segments(page)
            page.goto(base)
            page.wait_for_function(f"document.querySelectorAll('{ROW} > .starcell').length >= 14", timeout=20000)
            page.wait_for_timeout(600)
            fit = page.evaluate(FIT)
            assert fit["page"][0] <= fit["page"][1], fit
            assert fit["widest"] <= fit["pane"] + .5, fit
            assert fit["rowRight"] <= fit["pane"] + .5, fit
            assert fit["row"][0] > fit["row"][1], fit  # it really is long
            # No cell shrank to fit: each keeps the width of a seven-cell row.
            assert fit["cellWidth"] >= 60, fit
            # The slider reaches the last cell.
            reached = page.evaluate(f"""(() => {{
              const row = document.querySelector('{ROW}');
              row.scrollLeft = row.scrollWidth;
              const last = row.lastElementChild.getBoundingClientRect();
              return last.right <= row.getBoundingClientRect().right + .5;
            }})()""")
            assert reached, fit
            # The name and strategy still fit above the slider.
            clipped = page.evaluate(f"""(() => {{
              const row = document.querySelector('{ROW}');
              const bottom = row.getBoundingClientRect().top + row.clientHeight;
              const name = row.querySelector('.starname').getBoundingClientRect();
              return name.bottom - bottom;
            }})()""")
            assert clipped <= .5, clipped
        finally:
            browser.close()


def test_a_star_row_of_seven_still_fills_its_card_without_a_slider():
    with serve_ui_live() as (base, _service), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 834, "height": 1000})
            page.goto(base)
            page.wait_for_selector(".selector-card .starrow .starcell", timeout=20000)
            page.wait_for_timeout(400)
            row = page.evaluate("""(() => { const row = document.querySelector('.selector-card .starrow');
              return [row.scrollWidth, row.clientWidth]; })()""")
            assert row[0] <= row[1], row
        finally:
            browser.close()




def test_the_selector_cap_follows_the_cards_strategy_or_overall_choice():
    with serve_ui_live() as (base, _service), sync_playwright() as play:
        browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
        try:
            page = browser.new_page(viewport={"width": 1500, "height": 1000})
            page.goto(base)
            page.locator(f"{CARD} .rank-mode-button.is-selected").wait_for(timeout=20000)
            view = page.evaluate("fetch('/api/session').then(r => r.json())")
            key = f"{FIXTURE_COURSE}:{FIXTURE_STAR}"
            strategy, overall = view["rank_by_star"][key], view["overall_rank_by_star"][key]
            # The fixture's star must grade differently on the two, or this proves nothing.
            assert strategy != overall, (strategy, overall)
            # The cell's Overall answer is the card's own banner, not a second grading.
            card = next(sec for sec in view["stars"] if sec.get("course_id") == FIXTURE_COURSE
                        and sec.get("star_id") == FIXTURE_STAR)
            assert (overall["rank"], overall["division"]) == (card["entity_rank"]["rank"], card["entity_rank"]["division"])
            cap = ".selector-card .starcell.active-star .starrank"

            def shown():
                return page.evaluate("""selector => {
                  const glyph = document.querySelector(selector + ' [data-tier], ' + selector + ' .hat');
                  return glyph ? glyph.outerHTML : null; }""", cap)

            page.wait_for_selector(cap)
            on_overall = shown()
            assert on_overall, "no cap drawn in the selector"
            page.locator(f"{CARD} .rank-mode-button", has_text="Strategy").click()
            page.wait_for_function("""([selector, before]) => {
              const glyph = document.querySelector(selector + ' [data-tier], ' + selector + ' .hat');
              return glyph && glyph.outerHTML !== before; }""", arg=[cap, on_overall], timeout=5000)
            page.locator(f"{CARD} .rank-mode-button", has_text="Overall").click()
            page.wait_for_function("""([selector, before]) => {
              const glyph = document.querySelector(selector + ' [data-tier], ' + selector + ' .hat');
              return glyph && glyph.outerHTML === before; }""", arg=[cap, on_overall], timeout=5000)
            # The target picker reads the same choice for its badges.
            picked = page.evaluate("""import('/ui/rankdisplaymode.js').then(mode => {
              const entity = 'star:%d:%d';
              const entry = {rank: 'S', division: 'I', strat: 'x', overall: {rank: 'O', division: 'V'}};
              mode.writeRankDisplayMode(entity, 'strategy');
              const asStrategy = mode.pickerRanks({[entity]: entry})[entity].rank;
              mode.writeRankDisplayMode(entity, 'overall');
              return [asStrategy, mode.pickerRanks({[entity]: entry})[entity].rank];
            })""" % (FIXTURE_COURSE, FIXTURE_STAR))
            assert picked == ["S", "O"]
            stored = page.evaluate("localStorage.getItem('sm64.practiceRankModes')")
            assert json.loads(stored)[f"star:{FIXTURE_COURSE}:{FIXTURE_STAR}"] == "overall"
        finally:
            browser.close()
