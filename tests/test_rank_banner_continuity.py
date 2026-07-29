"""A rank band must look the same in every layout.

The two banners sit side by side in a wide pane and stack in a narrow one, and
three separate bugs have come out of that one switch — each reported by the
user, none caught by a test:

  1. stacked, the two washes OVERLAPPED by 15.2px, so the second covered the
     first one's lower edge and the colours met with no seam;
  2. the wash bled 16px past the rank slot's right edge onto the live-state
     column, at every width;
  3. the fix for (1) killed the inner bleed, which took the band's own internal
     padding with it — the top band then ended flush against its own progress
     bar while the bottom one kept its room. "Breaks continuity with how the
     actual rank standard display is designed" (2026-07-29).

(1) and (2) are geometry between different elements, and uilab's decoration
probe covers them generically. (3) is not: every box was the right size, in the
right place, overlapping nothing. What was wrong is that the SAME component
drew itself differently in two layouts — which no defect probe can express,
because neither rendering is defective on its own.

So this compares them. The wide layout is the reference, because that is the one
the user pointed at when he said the narrow one had diverged from it.
"""
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

REFERENCE_WIDTH = 1400          # the two banners sit side by side here
STACKED_WIDTHS = (1100, 900, 500)
TOLERANCE = 1.0                 # sub-pixel layout noise

# The painted band is the banner's ::before, which is not in the DOM — its rect
# comes from the host's padding box plus the pseudo's own resolved offsets.
BANDS = r"""
(() => {
  const card = document.querySelector(
    ".practice-detail-grid.is-primary .objective-card");
  if (!card) return [];
  const num = (value) => parseFloat(value) || 0;
  return [...card.querySelectorAll(".rank-banner")].map((banner) => {
    const host = banner.getBoundingClientRect();
    const own = getComputedStyle(banner);
    const wash = getComputedStyle(banner, "::before");
    const top = host.top + num(own.borderTopWidth) + num(wash.top);
    const bottom = top + num(wash.height);
    const row = banner.querySelector(".rank-banner-row");
    const bar = banner.querySelector(".rank-progress-track");
    return {
      height: bottom - top,
      aboveMedalRow: row ? row.getBoundingClientRect().top - top : null,
      belowProgressBar: bar ? bottom - bar.getBoundingClientRect().bottom : null,
      top, bottom,
      stacked: getComputedStyle(banner.closest(".rank-slot")).flexDirection
               === "column",
    };
  });
})()
"""


@pytest.fixture(scope="module")
def page():
    with PROJECT.open() as url, get_driver().launch() as opened:
        opened.goto(url)
        opened.wait_for(PROJECT.ready_selector)
        yield opened


def bands_at(page, width):
    page.set_viewport(width, 1100)
    page.wait_ms(450)
    return page.evaluate(BANDS)


@pytest.fixture(scope="module")
def reference(page):
    bands = bands_at(page, REFERENCE_WIDTH)
    assert len(bands) >= 2, (
        f"the fixture rendered {len(bands)} rank bands at {REFERENCE_WIDTH}px — "
        "this whole file measures nothing without two. The card needs a stage, "
        "a target, a strategy and a PB before either banner mounts.")
    assert not bands[0]["stacked"], (
        f"{REFERENCE_WIDTH}px was chosen because the banners sit SIDE BY SIDE "
        "there; they are stacked, so there is no reference layout to compare "
        "against and every assertion below is vacuous.")
    return bands[0]


@pytest.mark.parametrize("width", STACKED_WIDTHS)
def test_a_stacked_band_is_shaped_like_a_side_by_side_band(page, reference, width):
    """The regression the user reported: same component, two different shapes."""
    for index, band in enumerate(bands_at(page, width)):
        for field in ("height", "aboveMedalRow", "belowProgressBar"):
            assert band[field] == pytest.approx(reference[field], abs=TOLERANCE), (
                f"band {index} at {width}px has {field}={band[field]:.1f}, but "
                f"the side-by-side reference has {reference[field]:.1f}. The "
                "same component must draw itself the same way in both layouts.")


@pytest.mark.parametrize("width", STACKED_WIDTHS)
def test_stacked_bands_never_touch(page, width):
    """15.2px of overlap is what "the rank standards are overlapping" meant,
    three reports running. Zero is not enough either — abutting colours read as
    one smeared block, which is what the user was actually looking at."""
    bands = bands_at(page, width)
    assert bands and bands[0]["stacked"], (
        f"{width}px was chosen as a STACKED width and the banners are side by "
        "side there, so this test is not measuring what it claims to")
    for upper, lower in zip(bands, bands[1:]):
        gap = lower["top"] - upper["bottom"]
        assert gap >= 4, (
            f"only {gap:.1f}px between two rank bands at {width}px — they need "
            "visible separation, not merely non-overlap")
