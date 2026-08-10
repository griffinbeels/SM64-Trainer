"""Round 31 deleted the enable/disable badge -- a piece is always tracked and
always shows in the practice log, so the switch has nothing left to say.
Griffin: "we don't need a button to enable / disable them now."

Asserted as a SOURCE scan over comment-stripped text, because a class nobody
renders leaves no DOM trace to assert on.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from source_scan import code_only  # noqa: E402

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"


@pytest.mark.xfail(strict=True, reason=(
    "The Bowser row's RedsCell (stagebanner.js) still renders CellToggles "
    "directly for its star/pipe pair -- a SEPARATE consumer from the "
    "subsection badge this round retires, and redesigning it into a plain "
    "StandardSegmentCell is plan Task 3 (design spec "
    "2026-08-10-reds-as-subsection-design.md item 3), not this one. "
    "xfail(strict=True) so this flips red the moment it starts passing "
    "unexpectedly, and Task 3 is what turns it into a real assertion by "
    "deleting celltoggles.js and every remaining reference."))
def test_no_surface_renders_a_cell_toggle():
    offenders = {}
    for path in (*UI.glob("*.js"), *(UI / "components").glob("*.js")):
        body = code_only(path)
        named = [c for c in ("cell-toggle", "CellToggles") if c in body]
        if named:
            offenders[path.name] = named
    assert not offenders, offenders
