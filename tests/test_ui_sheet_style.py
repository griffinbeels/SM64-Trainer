"""The sheet-colour inspector in Settings (round 29 item 2), RENDERED.

"The user should be able to select a color for N64 times, and select a color
for EMU times... you should also be able to choose the font color" -- three
colours and a font as one stored preference, with a live preview of the two
cells a paste will produce. A UI change is not verified until the page draws
it, so this drives the real drawer: the section is there, the preview wears
the stored colours, a change through the real control lands on the server and
repaints the preview, and Reset walks it back.
"""
import json
import sys
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402

OPEN_SETTINGS = """
(() => {
  const button = [...document.querySelectorAll('button')].find(
    (candidate) => /settings/i.test(
      (candidate.getAttribute('aria-label') || '') + ' '
      + (candidate.title || '') + ' ' + candidate.textContent));
  if (button) button.click();
  return !!button;
})()
"""

SET_COLOUR = """
(() => {
  const box = document.querySelector('.sheetstyle-%s');
  if (!box) return false;
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
  setter.call(box, '%s');
  box.dispatchEvent(new Event('input', {bubbles: true}));
  box.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})()
"""


def _rgb(hex_colour: str) -> str:
    """`#RRGGBB` as the `rgb(r, g, b)` a computed style reports."""
    value = hex_colour.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


def _wait_until(page, expression, timeout_ms=10000):
    import time
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_ms(100)
    raise AssertionError(f"timed out waiting for {expression!r}")


def _style(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/scorecard/sheet_style", timeout=10) as response:
        return json.loads(response.read())


def test_the_inspector_draws_the_stored_style_and_a_change_lands(tmp_path):
    with serve_ui(tmp_path / "sheetstyle.db") as base:
        defaults = _style(base)["defaults"]
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            page.goto(f"{base}/ui/index.html")
            page.wait_for(".log-list-card")
            assert page.evaluate(OPEN_SETTINGS)
            page.wait_for(".sheetstyle .sheetstyle-preview")
            page.wait_ms(200)
            previews = page.evaluate(
                "Array.from(document.querySelectorAll('.sheetstyle-cell')).map((cell) => "
                "[cell.dataset.platform, getComputedStyle(cell).backgroundColor, "
                "getComputedStyle(cell).color])")
            assert previews == [["emu", _rgb(defaults["emu_fill"]), _rgb(defaults["font_color"])],
                                ["n64", _rgb(defaults["n64_fill"]), _rgb(defaults["font_color"])]]
            fonts_offered = page.evaluate(
                "document.querySelectorAll('#sheetstyle-fonts option').length")
            assert fonts_offered >= 20, "the Sheets font suggestions are not offered"
            reset_disabled_at_rest = page.evaluate(
                "document.querySelector('.sheetstyle-reset').disabled")

            assert page.evaluate(SET_COLOUR % ("n64_fill", "#ab3f14"))
            _wait_until(page, "getComputedStyle(document.querySelector("
                              "'.sheetstyle-cell[data-platform=\"n64\"]')).backgroundColor"
                              " === 'rgb(171, 63, 20)'")
            page.wait_ms(300)
            stored_after_change = _style(base)["style"]
            reset_enabled = page.evaluate(
                "!document.querySelector('.sheetstyle-reset').disabled")

            page.evaluate("document.querySelector('.sheetstyle-reset').click()")
            _wait_until(page, "getComputedStyle(document.querySelector("
                              f"'.sheetstyle-cell[data-platform=\"n64\"]')).backgroundColor"
                              f" === '{_rgb(defaults['n64_fill'])}'")
            page.wait_ms(300)
            stored_after_reset = _style(base)["style"]

    assert reset_disabled_at_rest is True, "Reset must be inert while the defaults apply"
    assert stored_after_change["n64_fill"] == "#AB3F14", stored_after_change
    assert reset_enabled is True
    assert stored_after_reset == defaults
