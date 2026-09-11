"""Fresh practice successes return the actual viewport to the top, for both kinds."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from frontend_runner import run_frontend
from sm64_events.core.events import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from find_uilab import find_uilab
from ui_fixture import _place_time, _run_coro, serve_ui_live


def test_practice_scroll_hook():
    run_frontend("practicescroll.test.js")


def _emit(service, kind, frame, payload):
    _run_coro(service.publish(Event(type=kind, frame=frame,
        timestamp_utc=datetime.now(timezone.utc), payload=payload)))


def _scroll_down(page):
    page.evaluate("window.scrollTo({top: 550, behavior: 'instant'})")
    page.wait_ms(150)
    assert page.evaluate("window.scrollY") > 150, "Fixture must actually scroll"


def _star_success(page, service, star_id, frame):
    _emit(service, "practice_reset", frame, {"igt_frames_before": 0})
    _emit(service, "star_collected", frame + 350, {
        "course_id": 2, "star_id": star_id,
        "igt_frames": 350, "igt_timed_at": "xcam"})
    page.wait_ms(1800)


def _segment_success(page, service, frame, level=19, course=17):
    # Actual segment matcher: entry starts the clock; the warp completes it.
    _emit(service, "stage_changed", frame,
          {"course_id": course, "level": level, "area": 1, "mode": "bowser_course"})
    _emit(service, "level_changed", frame, _place_time({"from": 24, "to": level}, 600))
    _emit(service, "area_changed", frame,
          _place_time({"level": level, "from": None, "to": 1}, 600))
    _emit(service, "warp_entered", frame + 90, _place_time({"level": level}, 690))
    page.wait_ms(2300)


@pytest.mark.parametrize("width", [1400, 850])
def test_success_on_different_entity_scrolls_viewport(width):
    missing = find_uilab()
    assert missing is None, missing
    from uilab.driver import get_driver

    with serve_ui_live(arm_segment=6) as (base, service), get_driver().launch(
            viewport=(width, 800)) as page:
        page.goto(base + "/ui/index.html")
        page.wait_for(".log-card")
        page.wait_ms(700)
        assert page.evaluate("document.querySelectorAll('.log-card').length") >= 2

        _scroll_down(page)
        _star_success(page, service, 4, 9000)
        assert page.evaluate("window.scrollY") > 150

        _run_coro(service.request_target("star", course_id=2, star_id=3))
        page.wait_ms(800)
        _scroll_down(page)
        _emit(service, "practice_reset", 10000, {"igt_frames_before": 0})
        page.wait_ms(1000)
        assert page.evaluate("window.scrollY") > 150
        _star_success(page, service, 3, 11000)
        assert page.evaluate("window.scrollY") == 0

        _scroll_down(page)
        _star_success(page, service, 3, 12000)
        assert page.evaluate("window.scrollY") > 150
        _segment_success(page, service, 16000)
        assert page.evaluate("window.scrollY") == 0

        _scroll_down(page)
        _segment_success(page, service, 17000)
        assert page.evaluate("window.scrollY") > 150
        page.emulate_motion(True)
        _segment_success(page, service, 18000, level=17, course=16)
        assert page.evaluate("window.scrollY") == 0
        assert not page.problems(), page.problems()
