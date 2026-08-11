"""Round 31 deleted the enable/disable badge -- a piece is always tracked and
always shows in the practice log, so the switch has nothing left to say.
Griffin: "we don't need a button to enable / disable them now." Task 3
(2026-08-10) finished the deletion: the Bowser row's RedsCell was a SEPARATE
consumer of `celltoggles.js` (its star/pipe pair, a different toggle from the
subsection badge this round retires) and is now a plain StandardSegmentCell --
`components/celltoggles.js` itself is deleted, along with every reference.

Asserted as a SOURCE scan over comment-stripped text, because a class nobody
renders leaves no DOM trace to assert on.
"""
from pathlib import Path

from source_scan import code_only

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"


def test_no_surface_renders_a_cell_toggle():
    offenders = {}
    for path in (*UI.glob("*.js"), *(UI / "components").glob("*.js")):
        body = code_only(path)
        named = [c for c in ("cell-toggle", "CellToggles") if c in body]
        if named:
            offenders[path.name] = named
    assert not offenders, offenders
