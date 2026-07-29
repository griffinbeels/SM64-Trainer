"""Render the real app across the matrix; fail on any layout defect.

Chrome missing FAILS rather than skips.  A skipped test is green forever, and
this is the only render-based gate in the suite -- the only thing standing
between a stylesheet edit and the Active Target card silently guillotining its
own "Ready" row again.  SM64_SKIP_SWEEP=1 opts out explicitly, so opting out is
a visible decision someone made rather than an accident of a missing binary.

Runtime is ~40s for 30 viewports.  That is the price of the only test here that
can see the difference between a card that fits and a card that is cut off.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from cdp import CHROME  # noqa: E402
from css_blocks import (UI_HTML, parse_blocks, size_blocks,  # noqa: E402
                        style_block, thresholds)
from responsive_sweep import derived_matrix, run_sweep  # noqa: E402

SKIPPING = os.environ.get("SM64_SKIP_SWEEP") == "1"

# Reviewed, dated exemptions: "<w>x<h> [tab] <class> :: <selector>" -> why.
#
# Everything below is Wave 3 work, recorded here so the suite is green while it
# is outstanding and so the list of what is broken is the list of what to fix.
# Each row must name a REASON, and deleting the row is how a fix is signed off.
KNOWN_DEFECTS: dict[str, str] = {
    # The reported bug (task 0032, live 2026-07-28). `.objective-card` is
    # height:258px under @media (max-width:760px) and its content needs 277px,
    # so 21px -- the "Ready" row -- is guillotined by overflow:hidden. The
    # height was hand-set before the card grew a second rank banner, the
    # moved-in strategy picker and the target-pick eyebrow.
    **{f"{w}x{h} [Practice] clipped :: section.practice-card.objective-card":
       "Wave 3: fixed height 258px vs 277px of content"
       for w, h in [(320, 800), (330, 1000), (331, 1000), (430, 1000),
                    (431, 1000), (500, 1000), (501, 1000), (600, 1000),
                    (601, 1000), (640, 1000), (641, 1000), (760, 1000),
                    (760, 1180)]},
    # Same card, the other axis, at the one width where the rail is still
    # present but the mobile layout has not taken over.
    "761x1000 [Practice] clipped :: section.practice-card.objective-card":
        "Wave 3: 646px of content in a 617px card",
    "761x1000 [Practice] clipped :: main.app-main":
        "Wave 3: consequence of the objective card above",
    # The reported nav dead end (task 0032). Rank is in app.js's NAV_GROUPS and
    # in NEITHER hardcoded mobile list, so below 760px it cannot be reached.
    # 340/341/420/421 are the practice-log Stats-trigger breakpoints added for
    # the "move the Stats button into the practice log" report (2026-07-28) --
    # new PROBE POINTS in the same already-broken sub-760px range, not a new
    # defect; the cause and the fix are identical to every other row here.
    **{f"{w}x{h} [shell] unreachable :: Rank":
       "Wave 3: MobileNav/MobileMore hardcode their own lists"
       for w, h in [(320, 800), (330, 1000), (331, 1000), (340, 1000),
                    (341, 1000), (420, 1000), (421, 1000), (430, 1000),
                    (431, 1000), (500, 1000), (501, 1000), (600, 1000),
                    (601, 1000), (640, 1000), (641, 1000), (760, 1000),
                    (760, 1180)]},
    **{f"{w}x{h} [Practice] clipped :: div.analysis-block.trend-block":
       "Wave 3: 6px of chart past the block at the WCAG reflow floor"
       for w, h in [(320, 800), (330, 1000), (331, 1000)]},
}


def test_chrome_is_available_or_the_opt_out_is_explicit():
    if SKIPPING:
        pytest.skip("SM64_SKIP_SWEEP=1 — sweep deliberately disabled")
    assert CHROME is not None, (
        "Chrome not found. The responsive sweep is the only render-based gate "
        "in this suite; install Chrome, or set SM64_SKIP_SWEEP=1 to opt out "
        "deliberately.")


def test_every_size_block_declares_a_threshold_the_matrix_understands():
    """The mechanism that stops this suite quietly going out of date.

    NOT "is every threshold in the matrix" -- that version was TAUTOLOGICAL and
    shipped green: `derived_matrix()` builds its points BY READING the same
    stylesheet, so it can never disagree with it, and a deliberately injected
    `@media (max-width: 543px)` passed the check it was supposed to fail
    (measured 2026-07-28, which is the only reason it was caught).  A guard
    that cannot fail is a guard that is not there.

    What can actually go wrong is a condition `thresholds()` does not
    UNDERSTAND: `40em`, `30rem`, `(orientation: portrait)`, an aspect ratio.
    Those parse to nothing, generate no probe points, and the block is then
    swept at no relevant size at all -- silently, while everything looks
    covered.  So the assertion is that every size block yields at least one
    px threshold, and that each one lands in the matrix on both sides.

    Needs no browser, so it runs even when the sweep is skipped.
    """
    matrix = derived_matrix()
    widths = {v.width for v in matrix}
    heights = {v.height for v in matrix}
    css = style_block(UI_HTML.read_text(encoding="utf-8"))

    unparsed, unprobed = [], []
    for block in size_blocks(parse_blocks(css)):
        found = thresholds(block)
        if not found:
            unparsed.append(f"@{block.kind} {block.condition} "
                            f"(style line {block.line})")
            continue
        for name, value in found:
            probed = widths if name.endswith("width") else heights
            if value not in probed or value + 1 not in probed:
                unprobed.append(f"@{block.kind} {block.condition} "
                                f"(style line {block.line})")

    assert not unparsed, (
        "These size blocks declare no px threshold that derived_matrix() can "
        "read, so NOTHING probes either side of them. Express the condition in "
        "px, or teach tools/css_blocks.py::thresholds the unit:\n  "
        + "\n  ".join(unparsed))
    assert not unprobed, (
        "Declared but not probed on both sides — the matrix was hand-edited "
        "away from the stylesheet:\n  " + "\n  ".join(unprobed))


@pytest.fixture(scope="module")
def sweep():
    """One sweep for the whole module — it costs ~40s and both tests read it."""
    if SKIPPING:
        pytest.skip("SM64_SKIP_SWEEP=1")
    return run_sweep()


def test_no_layout_defects_across_the_matrix(sweep):
    result = sweep
    new = {key: detail for key, detail in result["defects"].items()
           if key not in KNOWN_DEFECTS}
    assert not new, (
        f"{len(new)} NEW layout defect(s). Run `uv run python "
        f"tools/responsive_sweep.py` for the full table:\n  "
        + "\n  ".join(f"{k}\n      {v}" for k, v in sorted(new.items())[:25]))
    assert not result["errors"], (
        f"{len(result['errors'])} page exception(s) during the sweep — the app "
        f"threw while rendering:\n  " + "\n  ".join(result["errors"][:5]))


def test_the_known_defect_list_does_not_outlive_its_defects(sweep):
    """A stale exemption is a lie about what is broken.

    Without this, Wave 3 could fix the card and leave 13 rows claiming it is
    still cut off -- and the next person would trust the list.
    """
    stale = [key for key in KNOWN_DEFECTS if key not in sweep["defects"]]
    assert not stale, (
        f"Fixed, but still exempted — delete {len(stale)} row(s) from "
        f"KNOWN_DEFECTS: {stale[:8]}")
