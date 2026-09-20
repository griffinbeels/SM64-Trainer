"""Contact sheets of the Ghost Overlay switch, one per state, to LOOK at.

`tools/contact_sheet.py` shoots the project's own fixture, which has no ghost
session door yet (Track E is building it in parallel), so the card there would
render the "still looking" state at every width -- a clean sheet of a surface
nobody is looking at, which is the exact failure `.claude/rules/ui-core.md`
warns about. This is that tool's machinery (uilab's `sheet.write`, the same
widths, the same real project: real stylesheet, real shell, real practice
page) over `tools/ghost_fixture.py`'s session instead.

    uv run python tools/ghost_switch_sheet.py            # all three states
    uv run python tools/ghost_switch_sheet.py on         # one of them

Writes one PNG per state and prints the paths. Assertions answer "is
something broken"; only a picture answers "is this the thing you meant".
"""
from __future__ import annotations

import contextlib
import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from find_uilab import find_uilab                       # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    raise SystemExit(_MISSING)

from uilab import sheet                                 # noqa: E402
from ghost_fixture import (NO_FRAME_MAP_TEXT,           # noqa: E402
                           ghost_session, serve_ui_with_ghost)
from ui_fixture import FIXTURE_SEGMENT                  # noqa: E402
from uilab_project import PROJECT                       # noqa: E402

# The four places this app's layout changes shape, same as contact_sheet.py.
WIDTHS = (1500, 1200, 900, 850)

STATES = {
    "on": ghost_session(enabled=True, opacity=0.6, nudge_frames=3),
    # The colour treatment switched off: the tuning line is at its WIDEST in
    # "on" (the wheel is out), and this is where a control has to disappear
    # cleanly rather than sit there greyed. Both are worth looking at.
    "plain": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           tint={"enabled": False, "hue_degrees": 120}),
    # WHAT THE GHOST SHOWS. Three states worth a look: the control usable
    # in either position, and the one every recording made before this round
    # lands in -- disabled, with the server's own sentence beside it.
    "boxed": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           mario_boxes=True),
    "mario": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           mario_boxes=True, view="mario"),
    # The session asks for Mario and THIS ghost has no boxes: the ordinary
    # state on every older recording once he has picked the Mario view.
    "asked": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           view="mario"),
    "off": ghost_session(enabled=False, opacity=0.6),
    # The longest refusal the server can produce, not the shortest: a sheet
    # of the easy end of a range is a picture of a card nobody has.
    "disabled": ghost_session(available=False,
                              reason="recording_has_no_frame_map",
                              reason_text=NO_FRAME_MAP_TEXT),
}


def _project_for(session):
    @contextlib.contextmanager
    def serve():
        with serve_ui_with_ghost(session, arm_segment=FIXTURE_SEGMENT,
                                 seed_editor_fixtures=True) as (base, _state):
            yield base
    # Everything else is PROJECT's -- the stylesheet, the shell list, the
    # supported floor. Only the server and what to wait for change.
    return dataclasses.replace(PROJECT, serve=serve,
                               ready_selector=".ghost-row", stories=[])


def main(argv: list[str]) -> int:
    wanted = [name for name in argv if name in STATES] or list(STATES)
    unknown = [name for name in argv if name not in STATES]
    if unknown:
        raise SystemExit(f"unknown state {unknown} -- have: {', '.join(STATES)}")
    out_dir = Path(tempfile.gettempdir()) / "uilab"
    for name in wanted:
        out = out_dir / f"sheet-ghost-{name}.png"
        path = sheet.write(out, _project_for(STATES[name]), WIDTHS,
                           selector=".log-card.log-card-active")
        print(f"{name}: {path}")
    print(f"  widths {', '.join(f'{w}px' for w in WIDTHS)}"
          f"   (supported floor {PROJECT.min_viewport_width}px)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
