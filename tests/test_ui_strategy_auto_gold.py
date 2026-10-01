"""Real classification gestures bank GOLD and play the destination's earned climb."""
import json
import sys
from pathlib import Path

import pytest
from sm64_events.core.timefmt import format_igt_short

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from find_uilab import find_uilab  # noqa: E402
from ui_fixture import _run_coro, serve_ui_live  # noqa: E402

if missing := find_uilab():
    pytest.skip(missing, allow_module_level=True)
from uilab import trace  # noqa: E402
from uilab.driver import get_driver  # noqa: E402
from test_tracker_service import ev  # noqa: E402

CARD = ".log-card.log-card-active"
BANNER = f"{CARD} .rank-banner"
HEADER_PICK = f"{CARD} .log-card-strat-picker select"


def attempt(svc, anchor, frames):
    _run_coro(svc.publish(ev("practice_reset", anchor, {"igt_frames_before": 0})))
    _run_coro(svc.publish(ev("star_collected", anchor + frames,
               {"course_id": 2, "star_id": 4, "igt_frames": frames, "igt_timed_at": "xcam"})))
    return max(svc.db.attempts(), key=lambda row: row.id).id


def pick(page, selector, strategy):
    page.evaluate(f"""(() => {{
      const picker = document.querySelector({json.dumps(selector)});
      if (!picker) throw new Error('classification picker is missing');
      picker.value = {json.dumps(strategy)};
      picker.dispatchEvent(new Event('change', {{ bubbles: true }}));
    }})()""")


def climbs(result):
    return [row for row in result.of("climb").frames if row.get("width") is not None]


def choose_strategy_card(page):
    page.evaluate("""(() => {
      const cards = [...document.querySelectorAll('.strat-card')].filter(card =>
        card.querySelector('.strat-name')?.textContent === 'TJ Owlless');
      if (cards.length !== 1) throw new Error('expected one TJ Owlless card');
      cards[0].click();
    })()""")


def reveal_banner(page):
    page.wait_for(CARD)
    # Body rank placement deliberately hides the banner in a closed card.
    # Reveal its first mount without changing the production fold behavior.
    if page.count(f'{CARD} .log-card-fold[aria-expanded="false"]'):
        page.click(f'{CARD} .log-card-fold')


def choose_strategy_and_reveal_banner(page):
    choose_strategy_card(page)
    reveal_banner(page)


def change_pending_context_and_back(page, context):
    if context == "strategy":
        for strategy in ("Sideflip", "TJ Owlless"):
            pick(page, HEADER_PICK, strategy)
            page.wait_for(f'{HEADER_PICK}:has(option[value="{strategy}"]:checked)')
        return
    page.click('.app-sidebar .settings-link')
    clock_picker = '.settings-drawer .settings-field select:has(option[value="rta"])'
    page.wait_for(clock_picker)
    for clock in ("rta", "igt"):
        pick(page, clock_picker, clock)
        page.wait_for(f'{clock_picker}:has(option[value="{clock}"]:checked)')
    page.click('button[aria-label="Close settings"]')


def test_target_picker_celebrates_adopted_history_from_an_earlier_session(tmp_path):
    with serve_ui_live(seed=False, bundled_library=False) as (base, svc):
        _run_coro(svc.set_target(2, 4))
        best = attempt(svc, 1000, 343)
        _run_coro(svc.new_session())
        _run_coro(svc.publish(ev("stage_changed", 2000,
                  {"course_id": 2, "level": 24, "area": 1, "mode": "stars"})))
        _run_coro(svc.publish(ev("area_changed", 2000, {"level": 24, "from": None, "to": 1})))
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            target_button = 'button[title="Pick a star or segment to practice manually"]'
            page.wait_for(target_button)
            assert not page.count(CARD), "prior-session history must require a newly mounted card"
            page.click(target_button)
            course = ".entity-grid button[title=\"Whomp's Fortress\"]"
            page.wait_for(course)
            page.click(course)
            star = '.entity-grid button[title="Fall onto the Caged Island"]'
            page.wait_for(star)
            page.click(star)
            page.wait_for('.strat-card .strat-name')
            result = trace.record(page, watch={"climb": f"{BANNER}.is-climbing",
                                  "name": f"{BANNER} .rank-banner-name"},
                                  trigger=choose_strategy_and_reveal_banner, ms=9000)
            (tmp_path / "gold-prior-session.png").write_bytes(page.screenshot())
            assert svc.db.current_pb(2, 4, "igt", strat_tag="TJ Owlless")["attempt_id"] == best
            assert page.count(BANNER), page.evaluate("document.querySelector('.practice-page').textContent")
            assert climbs(result), "newly mounted target lost its live GOLD celebration"
            assert len({name for name in result.of("name").values("text") if name}) > 1
            assert not page.problems()
            page.goto(base)
            page.wait_for(CARD)
            arrival = trace.record(page, watch={"climb": f"{BANNER}.is-climbing"},
                                   trigger=reveal_banner, ms=1500)
            assert not climbs(arrival) and not page.problems()


@pytest.mark.parametrize("context", ["strategy", "clock"])
def test_an_unrevealed_gold_cannot_replay_after_a_context_round_trip(tmp_path, context):
    with serve_ui_live(seed=False, bundled_library=False) as (base, svc):
        _run_coro(svc.set_target(2, 4))
        best = attempt(svc, 1000, 343)
        _run_coro(svc.new_session())
        _run_coro(svc.publish(ev("stage_changed", 2000,
                  {"course_id": 2, "level": 24, "area": 1, "mode": "stars"})))
        _run_coro(svc.publish(ev("area_changed", 2000, {"level": 24, "from": None, "to": 1})))
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(base)
            page.wait_for('button[title="Pick a star or segment to practice manually"]')
            page.click('button[title="Pick a star or segment to practice manually"]')
            course = ".entity-grid button[title=\"Whomp's Fortress\"]"
            page.wait_for(course)
            page.click(course)
            star = '.entity-grid button[title="Fall onto the Caged Island"]'
            page.wait_for(star)
            page.click(star)
            page.wait_for('.strat-card .strat-name')
            choose_strategy_card(page)
            page.wait_for(f'{HEADER_PICK}:has(option[value="TJ Owlless"]:checked)')
            assert svc.db.current_pb(2, 4, "igt", strat_tag="TJ Owlless")["attempt_id"] == best
            assert not page.count(BANNER), "the earned receipt must still be waiting for its first reveal"
            change_pending_context_and_back(page, context)
            assert not page.count(BANNER), "context changes must not reveal the body banner"
            result = trace.record(page, watch={"climb": f"{BANNER}.is-climbing"},
                                  trigger=reveal_banner, ms=3000)
            (tmp_path / f"gold-pending-{context}-round-trip.png").write_bytes(page.screenshot())
            assert page.count(BANNER), "the banner must actually mount before checking for a stale climb"
            assert not climbs(result), f"an ordinary {context} round trip replayed an earlier GOLD"
            assert not page.problems()


@pytest.mark.parametrize("width", [1500, 850])
def test_classification_gold_and_earned_feedback(tmp_path, width):
    with serve_ui_live(seed=False, bundled_library=False) as (base, svc):
        _run_coro(svc.publish(ev("stage_changed", 900,
                   {"course_id": 2, "level": 24, "area": 1, "mode": "stars"})))
        _run_coro(svc.publish(ev("area_changed", 900, {"level": 24, "from": None, "to": 1})))
        _run_coro(svc.set_target(2, 4))
        attempt(svc, 1000, 600)
        # Bundled TJ Owlless grades 343 frames as Mario; a floor-only
        # result cannot prove that a multi-tier celebration actually climbs.
        best = attempt(svc, 2000, 343)
        with get_driver().launch(headless=True, viewport=(width, 1000)) as page:
            page.goto(base)
            page.wait_for(HEADER_PICK)
            assert page.evaluate(f"document.querySelector({json.dumps(HEADER_PICK)}).value") == ""
            first = trace.record(page, watch={"climb": f"{BANNER}.is-climbing", "name": f"{BANNER} .rank-banner-name"},
                                 trigger=lambda pg: pick(pg, HEADER_PICK, "TJ Owlless"), ms=9000)
            assert svc.db.current_pb(2, 4, "igt", strat_tag="TJ Owlless")["attempt_id"] == best
            assert climbs(first), "first classification did not play its earned climb"
            assert len({name for name in first.of("name").values("text") if name}) > 1
            assert not page.problems()
            (tmp_path / f"gold-first-{width}.png").write_bytes(page.screenshot())

            # Known-good ordinary strategy selection: there is no newly
            # classified history, so this must exchange without celebrating.
            plain = trace.record(page, watch={"climb": f"{BANNER}.is-climbing"},
                                 trigger=lambda pg: pick(pg, HEADER_PICK, "Sideflip"), ms=1800)
            assert not climbs(plain)
            destination = attempt(svc, 3000, 361)
            _run_coro(svc.save_pb(destination, "igt"))
            page.wait_ms(9000)
            pick(page, HEADER_PICK, "TJ Owlless")
            page.wait_ms(1800)
            latest = attempt(svc, 4000, 330)
            if page.count(f'{CARD} .log-card-fold[aria-expanded="false"]'):
                page.click(f'{CARD} .log-card-fold')
            row_picker = f'{CARD} .attempt-table tr:first-child .attempt-strategy select'
            # Before this new row arrives the prior newest row is Sideflip.
            # Wait for its actual strategy/time, rather than editing that row.
            page.wait_for(f'{row_picker}:has(option[value="TJ Owlless"]:checked)')
            newest_time = CARD + " .attempt-table tr:first-child .attempt-result b"
            assert page.evaluate(f'document.querySelector({json.dumps(newest_time)}).textContent') == format_igt_short(330)
            earned = trace.record(page, watch={"climb": f"{BANNER}.is-climbing", "swap": f"{BANNER}.is-swapping",
                                                "name": f"{BANNER} .rank-banner-name"},
                                  trigger=lambda pg: pick(pg, row_picker, "Sideflip"), ms=9000)
            assert svc.db.current_pb(2, 4, "igt", strat_tag="Sideflip")["attempt_id"] == latest
            assert climbs(earned), "latest correction earned GOLD but only exchanged strategies"
            names = [name.upper() for name in earned.of("name").values("text") if name]
            assert any(name.startswith("WALUIGI") for name in names), "correction skipped the destination PB's rank"
            assert names[-1].startswith("METAL")
            assert not any(name.startswith("CAPLESS") for name in names), "correction incorrectly climbed from the floor"
            assert not any(row.get("width") is not None for row in earned.of("swap").frames)
            assert not page.problems()
            (tmp_path / f"gold-corrected-{width}.png").write_bytes(page.screenshot())

            page.goto(base)
            page.wait_for(BANNER)
            reload = trace.record(page, watch={"climb": f"{BANNER}.is-climbing"}, ms=1500)
            assert not climbs(reload), "reload replayed an obsolete celebration"
            assert not page.problems()
