"""The sheet-colour controls on the Rank tab's exports row (round 29 item 2,
rebuilt and moved in round 30), RENDERED.

Round 30, his words: "This font choice should definitely be a DROPDOWN, not
this weird text entry"; "we should see its name *in its font*, as well as in
the dropdown you've selected, AND in the preview"; "if I click in the middle
of the empty space here, it also triggers the color picker... It should ONLY
be when I click on the actual color picker button"; "they should be circles
where the whole circle color is the color we've selected, with an outline";
"never allow an empty font choice"; "The color pickers / text pickers should
live here on the scorecard itself to the right of the Rebuild button. Laid
out horizontally on the same row."

A UI change is not verified until the page draws it, so this drives the real
Rank tab: the controls sit in the exports row and nowhere in Settings, the
two legend cells wear the stored colours and font, the font is the app's own
dropdown with every option in its face and no text input to empty, a colour
swatch has no label that could open it from the row, it is a circle, a pick
through the real menu lands on the server and repaints, and Reset walks back.
"""
import json
import sys
import time
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

OPEN_RANK_TAB = "document.querySelector('button.nav-item[title=\"Rank\"]').click()"

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
    value = hex_colour.lstrip("#")
    return "rgb({}, {}, {})".format(*(int(value[i:i + 2], 16) for i in (0, 2, 4)))


def _wait_until(page, expression, timeout_ms=10000):
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_ms(100)
    raise AssertionError(f"timed out waiting for {expression!r}")


def _style(base: str) -> dict:
    with urllib.request.urlopen(f"{base}/api/scorecard/sheet_style", timeout=10) as response:
        return json.loads(response.read())


def _open_controls(page, base):
    page.goto(f"{base}/ui/index.html")
    page.wait_for(".log-list-card")
    page.evaluate(OPEN_RANK_TAB)
    page.wait_for(".rank-page .scorecard-exports .sheetstyle")
    page.wait_ms(200)


def test_the_controls_sit_on_the_exports_row_and_nowhere_in_settings(tmp_path):
    with serve_ui(tmp_path / "sheetstyle.db") as base:
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            _open_controls(page, base)
            geometry = page.evaluate("""
              (() => {
                const row = document.querySelector('.rank-page .scorecard-exports');
                const copy = row.querySelector('.scorecard-copy-column').getBoundingClientRect();
                const controls = row.querySelector('.sheetstyle').getBoundingClientRect();
                const parts = [...row.querySelectorAll('.sheetstyle > *')]
                  .map((el) => el.getBoundingClientRect());
                const preview = row.querySelector('.sheetstyle-preview');
                const font = row.querySelector('.sheetstyle-font').getBoundingClientRect();
                return {
                  rightOfCopy: controls.left >= copy.right,
                  sameRow: parts.every((box) => Math.abs((box.top + box.bottom) / 2
                                                  - (copy.top + copy.bottom) / 2) < 14),
                  parts: parts.length,
                  // Round 31: the preview comes AFTER every option, boxed and
                  // captioned "Preview" above its two cells.
                  previewAfterFont: preview.getBoundingClientRect().left >= font.right,
                  previewLabel: preview.querySelector('.sheetstyle-preview-label').textContent.trim(),
                  previewBoxed: parseFloat(getComputedStyle(preview).borderTopWidth) >= 1,
                  labelAboveCells: preview.querySelector('.sheetstyle-preview-label').getBoundingClientRect().bottom
                    <= preview.querySelector('.sheetstyle-preview-cells').getBoundingClientRect().top,
                };
              })()
            """)
            assert page.evaluate(OPEN_SETTINGS)
            page.wait_for(".settings-section")
            in_settings = page.count(".settings-drawer .sheetstyle, .settings-section.sheetstyle")
    assert geometry["rightOfCopy"], geometry
    assert geometry["sameRow"], geometry
    assert geometry["parts"] >= 5, geometry          # 3 swatches, font, preview
    assert geometry["previewAfterFont"], geometry
    assert geometry["previewLabel"] == "Preview" and geometry["previewBoxed"], geometry
    assert geometry["labelAboveCells"], geometry
    assert in_settings == 0, "the controls still live in Settings"


def test_the_font_is_the_apps_dropdown_with_every_name_in_its_own_face(tmp_path):
    with serve_ui(tmp_path / "sheetstyle.db") as base:
        defaults = _style(base)["defaults"]
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            _open_controls(page, base)
            structure = page.evaluate("""
              (() => {
                const box = document.querySelector('.sheetstyle');
                const value = box.querySelector('.search-select-value');
                return {
                  textInputs: box.querySelectorAll('input[type="text"], input[list], datalist').length,
                  trigger: !!box.querySelector('.search-select-trigger'),
                  valueText: value.textContent.trim(),
                  valueFont: getComputedStyle(value).fontFamily,
                  previewFonts: [...box.querySelectorAll('.sheetstyle-cell')]
                    .map((cell) => getComputedStyle(cell).fontFamily),
                  fontsLink: !!document.querySelector('link[href^="https://fonts.googleapis.com/"]'),
                };
              })()
            """)
            page.evaluate("document.querySelector('.sheetstyle .search-select-trigger').click()")
            page.wait_for(".sheetstyle .search-menu")
            page.wait_ms(150)
            menu = page.evaluate("""
              (() => {
                const menu = document.querySelector('.sheetstyle .search-menu');
                const trigger = document.querySelector('.sheetstyle .search-select-trigger')
                  .getBoundingClientRect();
                const box = menu.getBoundingClientRect();
                const options = [...menu.querySelectorAll('.search-menu-option')];
                return {
                  count: options.length,
                  inOwnFace: options.every((o) => getComputedStyle(o).fontFamily
                    .toLowerCase().includes(o.textContent.trim().toLowerCase())),
                  belowTrigger: box.top >= trigger.bottom,
                  rightAligned: Math.abs(box.right - trigger.right) < 2,
                  scrolls: getComputedStyle(menu.querySelector('.search-menu-options')).overflowY === 'auto',
                  hasLobster: options.some((o) => o.textContent.trim() === 'Lobster'),
                };
              })()
            """)
            page.evaluate("""
              [...document.querySelectorAll('.sheetstyle .search-menu-option')]
                .find((o) => o.textContent.trim() === 'Lobster').click()
            """)
            _wait_until(page, "document.querySelector('.sheetstyle .search-select-value')"
                              ".textContent.trim() === 'Lobster'")
            page.wait_ms(300)
            after = page.evaluate("""
              (() => {
                const box = document.querySelector('.sheetstyle');
                return {
                  valueFont: getComputedStyle(box.querySelector('.search-select-value')).fontFamily,
                  previewFont: getComputedStyle(box.querySelector('.sheetstyle-cell')).fontFamily,
                  menuOpen: !!box.querySelector('.search-menu'),
                };
              })()
            """)
            stored = _style(base)["style"]

    assert structure["textInputs"] == 0, "the font is still a text field"
    assert structure["trigger"] is True
    assert structure["valueText"] == defaults["font_family"]
    assert defaults["font_family"].lower() in structure["valueFont"].lower()
    assert all(defaults["font_family"].lower() in font.lower()
               for font in structure["previewFonts"]), structure
    assert structure["fontsLink"], "the Google fonts on the list are never loaded"
    assert menu["count"] >= 20 and menu["hasLobster"], menu
    assert menu["inOwnFace"], "an option is not drawn in its own font"
    assert menu["belowTrigger"] and menu["rightAligned"], menu
    assert menu["scrolls"], "the list does not scroll"
    assert "lobster" in after["valueFont"].lower() and "lobster" in after["previewFont"].lower(), after
    assert after["menuOpen"] is False
    assert stored["font_family"] == "Lobster"


def test_a_swatch_is_a_circle_that_only_its_own_click_opens(tmp_path):
    """A label click ACTIVATES its control, and activating a colour input
    opens the picker -- which is how the empty space beside "N64 cell" opened
    one. So no swatch may have a label at all, and the swatch is drawn as a
    circle filled with its colour inside an outline."""
    with serve_ui(tmp_path / "sheetstyle.db") as base:
        defaults = _style(base)["defaults"]
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            _open_controls(page, base)
            swatches = page.evaluate("""
              [...document.querySelectorAll('.sheetstyle-swatch')].map((input) => {
                const css = getComputedStyle(input);
                return {
                  labels: input.labels ? input.labels.length : -1,
                  insideLabel: !!input.closest('label'),
                  radius: css.borderRadius,
                  square: input.getBoundingClientRect().width === input.getBoundingClientRect().height,
                  outline: parseFloat(css.borderTopWidth) >= 1,
                  appearance: css.appearance,
                  value: input.value.toUpperCase(),
                };
              })
            """)
    assert len(swatches) == 3
    for swatch in swatches:
        assert swatch["labels"] == 0 and swatch["insideLabel"] is False, swatch
        assert swatch["radius"] == "50%" and swatch["square"], swatch
        assert swatch["outline"], swatch
        assert swatch["appearance"] == "none", swatch
    assert [s["value"] for s in swatches] == [
        defaults["emu_fill"], defaults["n64_fill"], defaults["font_color"]]


def test_the_legend_cells_preview_the_stored_colours_and_a_change_lands(tmp_path):
    with serve_ui(tmp_path / "sheetstyle.db") as base:
        defaults = _style(base)["defaults"]
        with get_driver().launch(headless=True, viewport=(1500, 1000)) as page:
            _open_controls(page, base)
            previews = page.evaluate(
                "Array.from(document.querySelectorAll('.sheetstyle-cell')).map((cell) => "
                "[cell.dataset.platform, cell.textContent.trim(), "
                "getComputedStyle(cell).backgroundColor, getComputedStyle(cell).color])")
            assert previews == [
                ["emu", "EMU", _rgb(defaults["emu_fill"]), _rgb(defaults["font_color"])],
                ["n64", "N64", _rgb(defaults["n64_fill"]), _rgb(defaults["font_color"])]]
            reset_at_rest = page.count(".sheetstyle-reset")

            assert page.evaluate(SET_COLOUR % ("n64_fill", "#ab3f14"))
            _wait_until(page, "getComputedStyle(document.querySelector("
                              "'.sheetstyle-cell[data-platform=\"n64\"]')).backgroundColor"
                              " === 'rgb(171, 63, 20)'")
            page.wait_ms(300)
            stored_after_change = _style(base)["style"]
            page.wait_for(".sheetstyle-reset")
            page.evaluate("document.querySelector('.sheetstyle-reset').click()")
            _wait_until(page, "getComputedStyle(document.querySelector("
                              f"'.sheetstyle-cell[data-platform=\"n64\"]')).backgroundColor"
                              f" === '{_rgb(defaults['n64_fill'])}'")
            page.wait_ms(300)
            stored_after_reset = _style(base)["style"]
            reset_after = page.count(".sheetstyle-reset")

    assert reset_at_rest == 0, "Reset must be absent while the defaults apply"
    assert stored_after_change["n64_fill"] == "#AB3F14", stored_after_change
    assert stored_after_reset == defaults
    assert reset_after == 0
