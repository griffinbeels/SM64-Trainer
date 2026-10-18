"""The row that just landed flashes white across the whole row, once.

His live report, 2026-10-16: the old new-entry blink was painted per cell,
"split up with these ugly lines / separate boxes", and read as red on a
successful run -- "it should just be a white background flash to indicate that
it just appeared". The flash now animates one property on the ROW, the way the
GOLD!! wash does, so it is one continuous colour.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from playwright.sync_api import sync_playwright  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402

ROW = ".log-card.log-card-active .attempt-table tr:not(.replay-row)"
SAMPLE = """(() => {
  const row = document.querySelectorAll(%s)[1];
  row.classList.add('row-new');
  return new Promise(done => setTimeout(() => {
    const cells = [...row.children].map(td => getComputedStyle(td));
    done({image: getComputedStyle(row).backgroundImage,
          cellAnimations: cells.map(c => c.animationName),
          cellColours: cells.map(c => c.backgroundColor)});
  }, 170));
})()"""


def _white_alphas(image):
    return [float(alpha) for alpha in re.findall(r"rgba\(255, 255, 255, ([0-9.]+)\)", image)]


def test_a_new_row_flashes_white_across_the_row_never_per_cell():
    with serve_ui() as base:
        with sync_playwright() as play:
            browser = play.chromium.launch(headless=True, args=["--disable-gpu"])
            try:
                page = browser.new_page(viewport={"width": 850, "height": 1000})
                page.goto(base)
                page.locator(ROW).nth(1).wait_for(timeout=20000)
                seen = page.evaluate(SAMPLE % repr(ROW))
            finally:
                browser.close()
    # The row itself carries a white layer, part-way through its flash ...
    assert any(alpha > 0.05 for alpha in _white_alphas(seen["image"])), seen["image"]
    # ... and no cell paints a box of its own.
    assert all(name == "none" for name in seen["cellAnimations"]), seen["cellAnimations"]
    assert all(colour in ("rgba(0, 0, 0, 0)", "transparent") for colour in seen["cellColours"]), seen["cellColours"]
