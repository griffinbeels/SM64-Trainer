"""Round 21 item 5, rendered: "when we move inside the subarea, I would
expect only the stars relevant to that subarea to be displayed. I'm inside
the volcano, so I can only do stars inside there."

The table itself (`addresses.COURSE_SUBAREA_STARS`) was measured off his own
journals before it was authored — the settled area at every star grab — and
its coherence is pinned here beside the render, because a wrong row HIDES a
valid star, which is his dead-control class.
"""
import asyncio
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from sm64_events.core.events import Event  # noqa: E402
from sm64_events.memory.addresses import (COURSE_BY_LEVEL,  # noqa: E402
                                          COURSE_SUBAREA_STARS, star_count)

from ui_fixture import serve_ui_live  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

LLL_COURSE, LLL_LEVEL, VOLCANO = 7, 22, 2


# -- the table's own coherence (no browser needed) ---------------------------

def test_every_row_names_a_real_course_and_real_stars():
    for (level, area), stars in COURSE_SUBAREA_STARS.items():
        course = COURSE_BY_LEVEL.get(level)
        assert course is not None, f"level {level} maps to no course"
        assert area > 1, "area 1 is the course start, never a subarea row"
        for star in stars:
            assert 0 <= star < star_count(course), (
                f"({level},{area}) lists star {star}, outside course "
                f"{course}'s range")


def test_the_hundred_coin_star_rides_every_row():
    """Measured, not courtesy: his six CCM 100-coin grabs landed INSIDE the
    slide — the 100-coin star completes wherever you cross 100, so hiding
    its cell inside a subarea would hide a star you can genuinely grab
    there."""
    for (level, area), stars in COURSE_SUBAREA_STARS.items():
        assert 6 in stars, f"({level},{area}) hides the 100-coin star"


def test_the_volcano_row_is_his_measured_pair():
    """41 volcano grabs in his journals, all stars 4 and 5 — the row must
    agree with the measurement it came from."""
    assert COURSE_SUBAREA_STARS[(LLL_LEVEL, VOLCANO)] == (4, 5, 6)


# -- the render --------------------------------------------------------------

_MISSING = find_uilab()
if not _MISSING:
    from uilab import driver  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, 2000))"
COUNT_CELLS = "document.querySelectorAll('.stagebanner .starcell').length"


@pytest.mark.skipif(bool(_MISSING), reason=_MISSING or "")
def test_inside_the_volcano_the_row_narrows_to_the_volcano_stars(tmp_path):
    with serve_ui_live(tmp_path / "volcano.db",
                       stage=(LLL_COURSE, LLL_LEVEL),
                       target=(LLL_COURSE, 2)) as (base, service), \
            driver.get_driver().launch(headless=True) as page:
        page.goto(base)
        page.evaluate(SETTLE)
        outside = page.evaluate(COUNT_CELLS)

        def publish(area):
            async def go():
                await service.publish(Event(
                    type="stage_changed", frame=95000,
                    timestamp_utc=datetime(2026, 6, 10, 12, 5,
                                           tzinfo=timezone.utc),
                    payload={"course_id": LLL_COURSE, "level": LLL_LEVEL,
                             "area": area, "mode": "stars"}))
            worker = threading.Thread(target=lambda: asyncio.run(go()))
            worker.start()
            worker.join(timeout=10)

        publish(VOLCANO)
        page.evaluate(SETTLE)
        inside = page.evaluate(COUNT_CELLS)
        publish(1)
        page.evaluate(SETTLE)
        back_out = page.evaluate(COUNT_CELLS)

    assert outside == 7, (
        f"outside the volcano LLL shows all 7 stars, drew {outside}")
    assert inside == 3, (
        f"inside the volcano only its stars (4, 5, 100c) belong, drew {inside}")
    assert back_out == 7, "walking back out must restore the full row"
