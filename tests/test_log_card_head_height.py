"""Every `.log-card-head` renders at ONE height -- the same height the
unassigned card's head has always used.

One-line-heads round (2026-08-04): "we should remove the 'ACTIVE' indicator
... and we should move the selector to the right of the identity display. As
a result, this should also reduce the entire height of the card, because we
can fit it on one line -- they should all match the size of the unassigned
card." The unassigned card's own head has always been ONE row (art-less name
+ fold button, nothing else), so its rendered height is what "one line" means
operationally for this card.

Why this needs its OWN check rather than trusting the general responsive
sweep: `.log-card` is the page's one deliberately VARIABLE-height surface
(the log module's own comment says so), so a head that grew back to two or
three internal rows would clip nothing, overflow nothing and truncate
nothing -- every defect probe in tests/test_responsive.py would call the page
clean, exactly the "probe cannot express it" shape
`tests/test_log_card_name_fits.py` and `tests/test_step_track_fits.py`
already exist for on this same card.

Deliberately a PARITY check, not a pixel literal: `--log-icon-size` and
`--log-head-pad-y` are ordinary tunables a future inspector round is free to
change (CLAUDE.md: no test may assert a shipped tuning default's contents).
What must stay true regardless of what those numbers ARE is that every
ordinary card's head renders at the SAME height as the unassigned card's --
so this asserts the SET of measured heights has exactly one member, never a
literal "60".
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402
from uilab_project import PROJECT    # noqa: E402

# Wide and narrow -- the two ends of the supported range, same convention as
# test_log_card_name_fits.py's own width list.
WIDTHS = (1500, 850)

# Every `.log-card-head` on the page, unassigned card included: its own
# `getBoundingClientRect().height`, rounded to survive sub-pixel layout noise
# the same way test_log_card_name_fits.py's 1.5px epsilon does.
HEAD_HEIGHTS = """
(() => JSON.stringify(
  Array.from(document.querySelectorAll(".log-card-head"))
    .map((el) => Math.round(el.getBoundingClientRect().height))
))()
"""


@pytest.fixture(scope="module")
def measured():
    out = {}
    with PROJECT.open() as url, get_driver().launch() as page:
        page.goto(url)
        page.wait_for(PROJECT.ready_selector)
        for width in WIDTHS:
            page.set_viewport(width, 1200)
            page.wait_ms(320)
            raw = page.evaluate(HEAD_HEIGHTS)
            heights = json.loads(raw)
            assert len(heights) >= 2, (
                f"only {len(heights)} .log-card-head on the page at {width}px "
                "-- the fixture needs at least one entity card plus the "
                "unassigned card for this to measure anything")
            out[width] = heights
    return out


@pytest.mark.parametrize("width", WIDTHS)
def test_every_card_head_matches_the_unassigned_cards(measured, width):
    heights = measured[width]
    distinct = sorted(set(heights))
    assert len(distinct) == 1, (
        f"at {width}px, .log-card-head renders at {len(distinct)} different "
        f"heights ({distinct}) instead of one -- some card's head grew a "
        "second internal row (the deleted Ready/Running word, or the "
        "strategy picker stacking under the identity display instead of "
        "beside it) while the unassigned card's own single-row head stayed "
        "put")


def test_the_guard_can_fail(measured):
    """Confirms the mechanism itself can detect a height mismatch, the same
    proof-of-teeth `test_log_card_name_fits.py`'s own `test_the_guard_can_fail`
    gives its overflow sweep."""
    for width, heights in measured.items():
        rigged = heights + [heights[0] + 40]
        assert len(set(rigged)) != 1, (
            f"a deliberately mismatched height at {width}px did not register "
            "as a mismatch -- the guard is not actually comparing anything")
