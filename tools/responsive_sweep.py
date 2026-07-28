"""Render the real app across a viewport matrix and report layout defects.

    uv run python tools/responsive_sweep.py            # human table
    uv run python tools/responsive_sweep.py --json     # machine readable
    uv run python tools/responsive_sweep.py --shots    # + contact sheet

The matrix is DERIVED, never hand-listed: every `(min|max)-(width|height)`
threshold declared in index.html gets a probe point on BOTH sides (N and N+1),
because a threshold is exactly the place where a layout changes and therefore
the only place a layout can newly break.  Hand-picking three sample widths is
how the rank banners passed at 1400/900/700 while every window from ~1101px to
~1500px was broken.

Nothing here trusts a screenshot.  See tools/responsive_probe.js for why.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import namedtuple
from pathlib import Path

from cdp import CHROME, chrome_session
from css_blocks import UI_HTML, parse_blocks, size_blocks, style_block, thresholds
from ui_fixture import serve_ui

HERE = Path(__file__).resolve().parent
PROBE_JS = (HERE / "responsive_probe.js").read_text(encoding="utf-8")
APP_JS = UI_HTML.parent / "app.js"

Viewport = namedtuple("Viewport", "width height label")

# Sizes that earn a place regardless of what the stylesheet declares.
FIXED_POINTS = (
    Viewport(900, 1180, "reported-vertical"),      # the user's report
    Viewport(760, 1180, "reported-narrow"),        # the user's report
    Viewport(1500, 900, "max-workspace"),          # .workspace max-width
    Viewport(1920, 1080, "desktop"),
    Viewport(1280, 720, "short-window"),
    Viewport(320, 800, "wcag-reflow"),             # WCAG 1.4.10 Reflow floor
)

# The tab swept at EVERY viewport: the densest page, and the one both reported
# bugs live on.  The rest are swept at SECONDARY_WIDTHS -- and the sweep prints
# what that skipped, because a silent cap reads as full coverage.
PRIMARY_TAB = "Practice"

# Widths where a secondary page is actually worth re-measuring: both sides of
# each sidebar step (where the pane jumps WIDER as the window narrows), both
# sides of the 700px container threshold the narrow layouts now use, a mid
# desktop width, and the WCAG reflow floor.
#
# Three labels used to be enough because the other pages carried no converted
# rules. They do now, and a conversion checked at three viewports is a
# conversion checked nowhere near its own thresholds.
SECONDARY_WIDTHS = (1500, 1181, 1180, 901, 900, 761, 760, 701, 700, 431, 430, 320)

# Interactions the sweep drives, as one injected helper.  Kept beside the probe
# rather than inline in Python so both halves of "what runs in the page" are
# readable as JavaScript.
CONTROL_JS = """
(() => {
  const clickable = () => Array.from(
    document.querySelectorAll("button, a, [role=button], [role=tab]"))
    .filter((el) => {
      const s = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      return s.display !== "none" && s.visibility !== "hidden"
             && r.width > 0 && r.height > 0;
    });
  window.__goto = (name) => {
    const hit = clickable().find((el) => (el.textContent || "").trim() === name);
    if (!hit) return false;
    hit.click();
    return true;
  };
  window.__activeTab = () => {
    const on = document.querySelector(".nav-item.on");
    return on ? (on.textContent || "").trim() : null;
  };
})();
"""


def nav_tabs() -> tuple[str, ...]:
    """The tabs the shell CLAIMS to have, read out of app.js's NAV_GROUPS.

    Parsed, never retyped.  A second hand-maintained list is precisely the bug
    this probe exists to catch -- Rank sits in NAV_GROUPS and in neither of the
    two hardcoded mobile lists, which is why it was unreachable below 760px.
    """
    source = APP_JS.read_text(encoding="utf-8")
    match = re.search(r"const NAV_GROUPS = \[(.*?)\n\];", source, re.S)
    if not match:
        raise RuntimeError("NAV_GROUPS not found in app.js — did it get renamed?")
    return tuple(re.findall(r'\["([^"]+)",\s*"[^"]+"\]', match.group(1)))


def derived_matrix() -> list[Viewport]:
    """Both sides of every declared threshold, plus the fixed points."""
    css = style_block(UI_HTML.read_text(encoding="utf-8"))
    points: dict[tuple[int, int], Viewport] = {
        (v.width, v.height): v for v in FIXED_POINTS}
    for block in size_blocks(parse_blocks(css)):
        for name, value in thresholds(block):
            for edge in (value, value + 1):
                if name.endswith("width"):
                    key, view = (edge, 1000), Viewport(edge, 1000, f"w{value}")
                else:
                    key, view = (1400, edge), Viewport(1400, edge, f"h{value}")
                points.setdefault(key, view)
    return sorted(points.values(), key=lambda v: (-v.width, -v.height))


def _probe(session, tabs: tuple[str, ...]) -> dict:
    return json.loads(session.evaluate(
        f"JSON.stringify(__sweep({json.dumps(list(tabs))}))"))


def _reachability(session, tabs: tuple[str, ...]) -> list[dict]:
    """Unreachable tabs, checked with the More sheet CLOSED and then OPEN.

    A tab behind one tap is reachable; the probe can only see what is on
    screen, so the sheet has to be opened for it to have an opinion.
    """
    unreachable = {u["selector"] for u in _probe(session, tabs)["unreachable"]}
    if unreachable and session.evaluate('__goto("More")'):
        time.sleep(0.25)
        unreachable &= {u["selector"] for u in _probe(session, tabs)["unreachable"]}
        session.evaluate('__goto("Close menu") || __goto("×") || true')
    return [{"selector": t, "detail": "no visible control opens it at this size"}
            for t in sorted(unreachable)]


def run_sweep(viewports: list[Viewport] | None = None, shots: bool = False,
              verbose: bool = False) -> dict:
    """Sweep the matrix.  Returns {defects, skipped, viewports, errors}."""
    if CHROME is None:
        raise RuntimeError("Chrome not found — see tools/cdp.py")
    viewports = viewports or derived_matrix()
    tabs = nav_tabs()
    secondary = [t for t in tabs if t not in (PRIMARY_TAB, "Sessions")]

    defects: dict[str, str] = {}
    skipped: list[str] = []
    errors: list[str] = []
    images: list[tuple[str, bytes]] = []

    def record(view: Viewport, tab: str, kind: str, items: list[dict]) -> None:
        for item in items:
            defects[f"{view.width}x{view.height} [{tab}] {kind} :: "
                    f"{item['selector']}"] = item["detail"]

    with serve_ui() as base, chrome_session(f"{base}/ui/index.html") as session:
        session.evaluate(PROBE_JS)
        session.evaluate(CONTROL_JS)

        # CONTROL INTERACTION, once, before any measurement.  Without it a
        # harness fault is indistinguishable from the bug being hunted: a whole
        # session's evidence once pointed at "the picker never opens" when the
        # app was frozen for an unrelated reason and nothing at all responded.
        session.set_viewport(1600, 1000)
        time.sleep(0.3)
        before = session.evaluate("__activeTab()")
        assert session.evaluate('__goto("Run")'), "control: no visible Run tab"
        time.sleep(0.3)
        after = session.evaluate("__activeTab()")
        if before == after:
            raise RuntimeError(
                f"CONTROL FAILED: clicking Run left the active tab at {after!r}. "
                "The app is not responding — every measurement below would be "
                "about the harness, not the layout.")
        session.evaluate(f'__goto("{PRIMARY_TAB}")')

        for view in viewports:
            session.set_viewport(view.width, view.height)
            time.sleep(0.35)                 # ResizeObserver + container reflow
            session.evaluate(PROBE_JS)       # survives any re-navigation
            session.evaluate(CONTROL_JS)

            sweep_tabs = [PRIMARY_TAB]
            if view.width in SECONDARY_WIDTHS:
                sweep_tabs += secondary
            else:
                skipped.append(f"{view.width}x{view.height}: "
                               f"{', '.join(secondary)} (primary tab only)")

            for tab in sweep_tabs:
                if not session.evaluate(f"__goto({json.dumps(tab)})"):
                    if tab != PRIMARY_TAB:
                        skipped.append(f"{view.width}x{view.height}: {tab} "
                                       "(no control to reach it)")
                    continue
                time.sleep(0.3)
                result = _probe(session, ())
                for kind in ("overflow", "clipped", "truncated", "overlap"):
                    record(view, tab, kind, result[kind])
                if shots:
                    images.append((f"{view.width}x{view.height}-{tab}",
                                   session.screenshot()))
                session.evaluate(f'__goto("{PRIMARY_TAB}")')
                time.sleep(0.2)

            record(view, "shell", "unreachable", _reachability(session, tabs))
            if verbose:
                print(f"  swept {view.width}x{view.height} ({view.label})",
                      file=sys.stderr)

        errors = list(session.errors)

    if shots and images:
        _contact_sheet(images)
    return {"defects": defects, "skipped": skipped, "errors": errors,
            "viewports": len(viewports)}


PANE_JS = """
(() => {
  const inner = (sel) => {
    const el = document.querySelector(sel);
    if (!el) return null;
    const cs = getComputedStyle(el);
    return Math.round(el.clientWidth
      - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight));
  };
  return JSON.stringify({sidebar: inner(".app-sidebar"), pane: inner(".practice-page"),
                         objective: inner(".objective-card"),
                         metrics: inner(".objective-metrics"),
                         rankslot: inner(".rank-slot")});
})()
"""


def measure_panes(widths: list[int] | None = None) -> list[tuple[int, dict]]:
    """Viewport width -> the width each container ACTUALLY gets.

    The translation table for turning a viewport threshold into a container
    one, and the reason that translation can never be a rename.  Measured on
    the shipping shell 2026-07-28:

        viewport 1181 -> pane  932        viewport 761 -> pane 642
        viewport 1180 -> pane 1061        viewport 760 -> pane 725

    Pane width is not monotonic in viewport width, and the discontinuities are
    large: dropping the sidebar to a rail at 1180px makes the pane 129px WIDER,
    and dropping it entirely at 760px makes it 83px wider again.  So the mobile
    block styles a 725px pane while the block above it styles a 642px pane --
    the narrowest layout applied to the WIDER container.  No viewport threshold
    can express "this card is too narrow", which is the whole reason for the
    @container law in .claude/rules/ui-core.md.
    """
    widths = widths or sorted({v.width for v in derived_matrix()}, reverse=True)
    rows = []
    with serve_ui() as base, chrome_session(f"{base}/ui/index.html") as page:
        for width in widths:
            page.set_viewport(width, 1000)
            time.sleep(0.35)
            page.evaluate(CONTROL_JS)
            page.evaluate(f'__goto("{PRIMARY_TAB}")')
            time.sleep(0.25)
            rows.append((width, json.loads(page.evaluate(PANE_JS))))
    return rows


def _print_panes(rows: list[tuple[int, dict]]) -> None:
    keys = ("sidebar", "pane", "objective", "metrics", "rankslot")
    print(f"{'viewport':>9}" + "".join(f"{k:>10}" for k in keys))
    print("-" * (9 + 10 * len(keys)))
    previous = None
    for width, row in rows:
        cells = "".join(f"{'-' if row[k] is None else row[k]:>10}" for k in keys)
        jump = ""
        if previous is not None and row["pane"] and previous > row["pane"] * 0:
            delta = (row["pane"] or 0) - previous
            if delta > 0:
                jump = f"   <- pane grew {delta}px as the viewport SHRANK"
        previous = row["pane"] or previous
        print(f"{width:>9}{cells}{jump}")


def _contact_sheet(images: list[tuple[str, bytes]]) -> Path:
    """A grid of every rendered size, for a HUMAN eye.  Never a gate."""
    import io

    from PIL import Image

    thumbs = []
    for label, data in images:
        image = Image.open(io.BytesIO(data)).convert("RGB")
        image.thumbnail((420, 420))
        thumbs.append((label, image))
    columns = 4
    cell_w = max(t.width for _, t in thumbs) + 12
    cell_h = max(t.height for _, t in thumbs) + 28
    rows = (len(thumbs) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * cell_w, rows * cell_h), (10, 20, 34))
    for index, (label, thumb) in enumerate(thumbs):
        x = (index % columns) * cell_w + 6
        y = (index // columns) * cell_h + 22
        sheet.paste(thumb, (x, y))
    out = Path.cwd() / "responsive-contact-sheet.png"
    sheet.save(out)
    print(f"contact sheet: {out}  ({len(thumbs)} renders)", file=sys.stderr)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="machine-readable")
    parser.add_argument("--shots", action="store_true", help="contact sheet")
    parser.add_argument("--width", type=int, help="sweep one width only")
    parser.add_argument("--panes", action="store_true",
                        help="viewport -> container width table (no defect scan)")
    args = parser.parse_args()

    if args.panes:
        _print_panes(measure_panes())
        return 0

    matrix = derived_matrix()
    if args.width:
        matrix = [v for v in matrix if v.width == args.width] or [
            Viewport(args.width, 1000, "ad-hoc")]

    result = run_sweep(matrix, shots=args.shots, verbose=not args.json)
    if args.json:
        print(json.dumps(result, indent=2))
        return 1 if result["defects"] else 0

    print(f"\nswept {result['viewports']} viewports, "
          f"{len(result['defects'])} defects\n")
    for key, detail in sorted(result["defects"].items()):
        print(f"  {key}\n      {detail}")
    if result["errors"]:
        print(f"\npage exceptions ({len(result['errors'])}):")
        for error in result["errors"][:10]:
            print(f"  {error}")
    # No silent caps: say out loud what was not covered.
    if result["skipped"]:
        print(f"\nNOT SWEPT ({len(result['skipped'])} combinations):")
        for line in result["skipped"][:20]:
            print(f"  {line}")
        if len(result["skipped"]) > 20:
            print(f"  … and {len(result['skipped']) - 20} more")
    return 1 if result["defects"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
