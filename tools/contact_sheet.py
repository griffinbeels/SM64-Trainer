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
    uv run python tools/contact_sheet.py .log-card              # one card
    uv run python tools/contact_sheet.py .log-card --collapsed
    uv run python tools/contact_sheet.py .library-target --story=library-target

`--story=<name>` navigates through a registered Story's own `setup` before
shooting -- required for anything that is not on the default page/practice
state (a selector with no matching story reports every width "NOT PRESENT",
which looks like a missing feature rather than a missing navigation step).
`--collapsed` is the pre-existing shorthand for `--story=page-collapsed`.

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
from uilab_project import (PROJECT, STORIES,          # noqa: E402
                           SUBSECTION_PROJECT, SUBSECTION_STORIES)

# Wide, the two stacked layouts, and the supported floor. Not the full matrix:
# a sheet of 30 tiles is one nobody reads, and these are the four places this
# app's layout actually changes shape.
WIDTHS = (1500, 1200, 900, 850)


def main(argv: list[str]) -> int:
    args = [a for a in argv if not a.startswith("--")]
    selector = args[0] if args else None
    story_name = next((a.split("=", 1)[1] for a in argv if a.startswith("--story=")), None)
    if "--collapsed" in argv:
        story_name = "page-collapsed"
    story = None
    if story_name:
        try:
            story = next(s for s in STORIES if s.name == story_name)
        except StopIteration:
            names = ", ".join(s.name for s in STORIES)
            raise SystemExit(f"no story named {story_name!r} -- have: {names}") from None

    # The RECORDER, which is a modal and so is unreachable from a plain page
    # load -- `--recorder` opens it empty (the arrival state), `--recording`
    # with two moments picked (the review), `--waypoints` with three (a stop
    # the person named rather than one the walk derived).
    for flag, name in (("--recorder", "recorder-open"),
                       ("--recording", "recorder-review"),
                       ("--waypoints", "recorder-waypoints")):
        if flag in argv:
            story = next(s for s in STORIES if s.name == name)
            selector = selector or ".modal"

    # A star wearing its pieces needs a fixture with a `parent` in it -- no
    # shipped definition has one, so the default project literally cannot
    # draw this surface. `--folded` (a badge switched OFF) is RETIRED with
    # the badge itself: round 31 (task 2/3, 2026-08-10) deleted the
    # enable/disable badge outright -- a piece is always tracked and always
    # shows, so there is no "off" state left to shoot. `--subsections` is
    # the one story worth a picture now: every piece drawn, unconditionally.
    project = PROJECT
    if "--nested" in argv:
        project = SUBSECTION_PROJECT
        story = next(s for s in SUBSECTION_STORIES if s.name == "page")
        selector = selector or ".log-card"
    if "--subsections" in argv:
        project = SUBSECTION_PROJECT
        story = next(s for s in SUBSECTION_STORIES if s.name == "selector-pieces-on")
        selector = selector or ".stagebanner"
    if "--folded" in argv:
        raise SystemExit(
            "--folded is retired: round 31 deleted the enable/disable badge "
            "it used to shoot switched off, so there is no folded state left "
            "to render. Use --subsections.")

    floor = project.min_viewport_width
    assert min(WIDTHS) >= floor, (
        f"this sheet shoots {min(WIDTHS)}px, below the supported floor {floor}")

    out = Path(tempfile.gettempdir()) / "uilab" / (
        f"sheet-{(selector or 'page').strip('.#') }"
        f"{'-' + story.name if story else ''}.png")
    path = sheet.write(out, project, WIDTHS, selector=selector, story=story)
    print(f"{path}")
    print(f"  widths {', '.join(f'{w}px' for w in WIDTHS)}"
          f"   (supported floor {floor}px)")
    # Say what the matrix is NOT looking at, every time. A narrowed range that
    # reports "0 defects" reads exactly like a complete one.
    from uilab.sweep import dropped_viewports
    dropped = sorted({view.width for view in dropped_viewports(project)})
    if dropped:
        print(f"  below the floor and no longer measured at all: "
              f"{', '.join(str(w) for w in dropped)}")
    print(f"  matrix now spans {len(derived_matrix(project))} viewports")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
