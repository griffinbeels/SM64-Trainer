"""Render one surface at every supported width, into a single image to LOOK at.

The layout gate answers "is something broken". It cannot answer "does this read
as the thing you meant" — and the two failures that cost the most days were
both obvious on sight and invisible to every assertion:

  * the fixture drew ONE rank banner where the real card draws two, so an
    entire class of defect was unmeasurable. One glance says "there's only one
    rank standard".
  * two banner washes overlapped by 15px at every stacked width while four DOM
    probes reported the page clean, because a pseudo-element is not in the DOM.

So take a sheet while you are still implementing, not after.

    uv run python tools/contact_sheet.py                       # the whole page
    uv run python tools/contact_sheet.py .objective-card       # one card
    uv run python tools/contact_sheet.py .objective-card --collapsed

Writes a PNG and prints its path. Widths come from the project's own supported
range, so this and the gate can never be looking at different sizes.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from find_uilab import find_uilab                     # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    raise SystemExit(_MISSING)

from uilab import sheet                               # noqa: E402
from uilab.sweep import derived_matrix                # noqa: E402
from uilab_project import PROJECT, STORIES            # noqa: E402

# Wide, the two stacked layouts, and the supported floor. Not the full matrix:
# a sheet of 30 tiles is one nobody reads, and these are the four places this
# app's layout actually changes shape.
WIDTHS = (1500, 1200, 900, 850)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    selector = args[0] if args else None
    story = None
    if "--collapsed" in argv:
        story = next(s for s in STORIES if s.name == "page-collapsed")

    floor = PROJECT.min_viewport_width
    assert min(WIDTHS) >= floor, (
        f"this sheet shoots {min(WIDTHS)}px, below the supported floor {floor}")

    out = Path(tempfile.gettempdir()) / "uilab" / (
        f"sheet-{(selector or 'page').strip('.#') }"
        f"{'-collapsed' if story else ''}.png")
    path = sheet.write(out, PROJECT, WIDTHS, selector=selector, story=story)
    print(f"{path}")
    print(f"  widths {', '.join(f'{w}px' for w in WIDTHS)}"
          f"   (supported floor {floor}px)")
    # Say what the matrix is NOT looking at, every time. A narrowed range that
    # reports "0 defects" reads exactly like a complete one.
    from uilab.sweep import dropped_viewports
    dropped = sorted({view.width for view in dropped_viewports(PROJECT)})
    if dropped:
        print(f"  below the floor and no longer measured at all: "
              f"{', '.join(str(w) for w in dropped)}")
    print(f"  matrix now spans {len(derived_matrix(PROJECT))} viewports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
