"""A saved JP performance keeps both rank choices while the app grades on US."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from find_uilab import find_uilab  # noqa: E402

if missing := find_uilab():
    pytest.skip(missing, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from ui_fixture import _run_coro, serve_ui_live  # noqa: E402
from standards_panel import api  # noqa: E402
from test_ui_standards_sync import _clear_fixture_startup_errors  # noqa: E402

ENTITY = "star:2:4"
STRATEGY = "Regional selector regression"
CARD = f'.log-card[data-feed-key="{ENTITY}"]'
# Explicit reference cutoffs beat the other fixture strategies in both ROMs.
LADDER = {"Mario": 2., "Grandmaster": 3., "Master": 4., "Diamond": 5.,
          "Platinum": 6., "Gold": 7., "Silver": 8., "Bronze": 9.}


def _until(page, condition):
    assert page.evaluate(f"""(async () => {{
      const end = performance.now() + 10000;
      while (performance.now() < end) {{
        if ({condition}) return true;
        await new Promise(resolve => setTimeout(resolve, 40));
      }}
      return false;
    }})()"""), condition


def _capture(page, name):
    if directory := os.environ.get("OVERALL_BASIS_UI_EVIDENCE"):
        folder = Path(directory)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{name}.png").write_bytes(page.screenshot())


def test_saved_jp_pb_offers_its_distinct_overall_rank_while_running_us():
    with serve_ui_live(bundled_library=False) as (base, service):
        api(base, "/api/mode", {"version": "us"}, method="PUT")
        for rank, seconds in LADDER.items():
            api(base, f"/api/ranks/standards/{quote(ENTITY)}/{quote(STRATEGY)}/{rank}",
                {"seconds": seconds}, method="PUT")
        _run_coro(service.set_strat(2, 4, STRATEGY))
        imported = api(base, "/api/import/manual", {"entity_key": ENTITY,
            "strat_tag": STRATEGY, "time_cs": 750, "game_version": "jp"})
        assert imported["imported"] == 1, imported
        api(base, f"/api/ranks/overall/{quote(ENTITY)}/Gold?version=jp",
            {"seconds": 7.5}, method="PUT")
        standards = api(base, f"/api/ranks/standards?entity={quote(ENTITY)}&version=us")
        assert standards["overall_curve"]["interpolation"] == "legacy"
        assert standards["overall_overrides"] == {}
        assert standards["overall_curve"]["ladder_cs"] == {
            rank: int(seconds * 100) for rank, seconds in LADDER.items()}
        session = api(base, "/api/session?clock=igt&scope=lifetime")
        assert session["game_version"]["effective"] == "us"
        section = next(row for row in session["stars"]
                       if (row["course_id"], row["star_id"]) == (2, 4))
        assert section["rank"]["rank"] != section["entity_rank"]["rank"]
        assert section["entity_rank"]["rank"] == "Gold"
        saved = [row for row in service.db.pbs() if row["strat_tag"] == STRATEGY]
        assert len(saved) == 1 and saved[0]["game_version"] == "jp"

        with get_driver().launch(headless=True, viewport=(1500, 1100)) as page:
            page.goto(base)
            page.wait_for(CARD)
            # Independent control interaction proves the real app responds.
            page.click('.sidebar-nav .nav-item[title="Library"]')
            page.wait_for(".library-page")
            page.click('.sidebar-nav .nav-item[title="Practice"]')
            page.wait_for(CARD)
            page.evaluate(f"""(() => {{
              const card = document.querySelector({json.dumps(CARD)});
              if (card.classList.contains('is-closed')) card.querySelector('.log-card-fold').click();
            }})()""")
            page.wait_for(CARD + " .rank-mode-button")
            _clear_fixture_startup_errors(page, base)
            controls = page.evaluate(f"""[...document.querySelectorAll({json.dumps(CARD + ' .rank-mode-button')})]
              .map(button => ({{label: button.textContent.trim(),
                visible: button.checkVisibility(), disabled: button.disabled}}))""")
            _capture(page, "rank-choices")
            assert controls == [
                {"label": "Strategy", "visible": True, "disabled": False},
                {"label": "Overall", "visible": True, "disabled": False}], controls
            for mode, grade in [("strategy", section["rank"]), ("overall", section["entity_rank"])]:
                button = CARD + f' .rank-mode-button[title^="Show the {"rank for" if mode == "strategy" else "best overall"}"]'
                page.click(button)
                expected = page.evaluate(f"""(async () => {{
                  const caps = await import('/ui/components/caps.js');
                  return caps.capName({json.dumps(grade['rank'])}).toUpperCase()
                    + ' ' + caps.divisionDigit({json.dumps(grade['division'])});
                }})()""")
                _until(page, f"document.querySelector({json.dumps(button)}).getAttribute('aria-pressed') === 'true'")
                _until(page, f"document.querySelector({json.dumps(CARD + ' .rank-banner-name')}).textContent.trim() === {json.dumps(expected)}")
                _capture(page, mode)
            assert page.problems() == []
