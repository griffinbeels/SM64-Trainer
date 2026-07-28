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
from responsive_sweep import (derived_matrix, measure_panes,  # noqa: E402
                              run_sweep)

SKIPPING = os.environ.get("SM64_SKIP_SWEEP") == "1"

# Reviewed, dated exemptions: "<w>x<h> [tab] <class> :: <selector>" -> why.
#
# Everything below is Wave 3 work, recorded here so the suite is green while it
# is outstanding and so the list of what is broken is the list of what to fix.
# Each row must name a REASON, and deleting the row is how a fix is signed off.
#
# EMPTY as of 2026-07-28: the sweep reports zero defects across all 36
# viewports.  Both reported bugs are fixed (the Active Target card's clipping
# and the Rank dead end), along with two the sweep found on its own -- the
# context bar overflowing `main.app-main` through the 761..775 band, and a
# hint tooltip wider than the block that clips it.  Every row that used to be
# here was removed by fixing the thing, not by exempting it.
KNOWN_DEFECTS: dict[str, str] = {}


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


@pytest.mark.skipif(SKIPPING, reason="SM64_SKIP_SWEEP=1")
def test_pane_width_is_not_monotonic_in_viewport_width():
    """The evidence the @container law rests on, kept executable.

    Dropping the sidebar to a rail at 1180px and removing it at 760px each make
    the pane WIDER as the window gets narrower.  Measured 2026-07-28: viewport
    1181 -> pane 932, viewport 1180 -> pane 1061 (+129); viewport 761 -> pane
    642, viewport 760 -> pane 725 (+83).  So `@media (max-width: 760px)` styles
    a 725px pane while the rules above it style a 642px one -- the narrowest
    layout applied to the WIDER container.

    If this ever stops being true the law's justification has changed and
    someone should read it again rather than discover it by surprise.  No
    tolerance on the exact pixels: the direction of the jump is the claim.
    """
    rows = dict(measure_panes([1181, 1180, 761, 760]))
    assert rows[1180]["pane"] > rows[1181]["pane"], (
        "the sidebar->rail step no longer widens the pane: "
        f"1181 -> {rows[1181]['pane']}, 1180 -> {rows[1180]['pane']}")
    assert rows[760]["pane"] > rows[761]["pane"], (
        "dropping the sidebar no longer widens the pane: "
        f"761 -> {rows[761]['pane']}, 760 -> {rows[760]['pane']}")


def test_the_known_defect_list_does_not_outlive_its_defects(sweep):
    """A stale exemption is a lie about what is broken.

    Without this, Wave 3 could fix the card and leave 13 rows claiming it is
    still cut off -- and the next person would trust the list.
    """
    stale = [key for key in KNOWN_DEFECTS if key not in sweep["defects"]]
    assert not stale, (
        f"Fixed, but still exempted — delete {len(stale)} row(s) from "
        f"KNOWN_DEFECTS: {stale[:8]}")
