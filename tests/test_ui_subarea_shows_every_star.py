"""Task 0122, rendered: a course shows EVERY one of its stars, whatever
subarea you are standing in.

Replaces `test_ui_subarea_stars.py`, which pinned the opposite. The selector
used to narrow its row to the stars a subarea hosts (round 21 item 5), and
three rounds of fixes went into the moments where the area byte is not his --
a course load's transient (round 23), the star-select screen after a grab
(round 26). It still flashed the subarea's stars on a reset inside SSL or
LLL, and his ruling retired the feature rather than the flash: "I would
expect to load into a course and see all the stars for that course at all
times, regardless of which subarea im in. That's the new functionality."

Driven through the REAL service and the REAL page, because the removal is
about what the browser DRAWS -- a unit test on the vocabulary would stay green
while a second narrowing path was written beside it.
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

from ui_fixture import serve_ui_live  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

LLL_COURSE, LLL_LEVEL, VOLCANO = 7, 22, 2
LLL_STARS = 7          # six course stars plus the 100-coin

_MISSING = find_uilab()
if not _MISSING:
    from uilab import driver  # noqa: E402

COUNT_CELLS = "document.querySelectorAll('.stagebanner .starcell').length"


@pytest.mark.skipif(bool(_MISSING), reason=_MISSING or "")
def test_the_volcano_still_offers_every_star_in_the_course(tmp_path):
    with serve_ui_live(tmp_path / "volcano.db",
                       stage=(LLL_COURSE, LLL_LEVEL),
                       target=(LLL_COURSE, 2)) as (base, service), \
            driver.get_driver().launch(headless=True) as page:

        def publish(event_type, payload, frame):
            async def go():
                await service.publish(Event(
                    type=event_type, frame=frame,
                    timestamp_utc=datetime(2026, 6, 10, 12, 5,
                                           tzinfo=timezone.utc),
                    payload=payload))
            worker = threading.Thread(target=lambda: asyncio.run(go()))
            worker.start()
            worker.join(timeout=10)

        def cells_settle():
            """Poll rather than sleep a fixed wait: every published event costs
            a websocket hop plus a view fetch, and a fixed wait is a race that
            passes alone and fails under load."""
            last = -1
            for _ in range(40):
                page.evaluate("new Promise(r => setTimeout(r, 150))")
                now = page.evaluate(COUNT_CELLS)
                if now == last and now:
                    return now
                last = now
            return last

        page.goto(base)
        outside = cells_settle()

        # Walk into the volcano. Nothing about the row may change.
        publish("stage_changed", {"course_id": LLL_COURSE, "level": LLL_LEVEL,
                                  "area": VOLCANO, "mode": "stars"}, 95000)
        inside = cells_settle()

        # Grab a star in there: the star-select screen comes up and the area
        # byte does not move. This was the window round 26 was about.
        publish("star_collected", {"course_id": LLL_COURSE, "star_id": 5,
                                   "igt_frames": 400}, 95100)
        on_star_select = cells_settle()

        # And a reset back into the volcano -- his own report's moment.
        publish("spawned", {"level": LLL_LEVEL, "kind": "spawn",
                            "area": VOLCANO}, 95200)
        after_reset = cells_settle()

    assert outside == LLL_STARS, (
        f"LLL's main area draws all {LLL_STARS} stars, drew {outside}")
    assert inside == LLL_STARS, (
        f"standing in the volcano must not hide a star -- drew {inside}")
    assert on_star_select == LLL_STARS, (
        f"the star select shows the whole course -- drew {on_star_select}")
    assert after_reset == LLL_STARS, (
        "a reset inside the subarea is the moment he reported: the row must "
        f"not flash the volcano's stars -- drew {after_reset}")
