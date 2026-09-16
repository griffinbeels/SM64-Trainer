"""Real Overall cutoff controls, compiled goals and region isolation at both widths."""
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import quote

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402
from standards_panel import api  # noqa: E402
from test_ui_library_target import (OPEN_OVERALL,  # noqa: E402
                                    assert_compiled_overall, set_native_value)
from sm64_events.core.timefmt import cs_of_frame, frame_at_or_after  # noqa: E402
from sm64_events.ranks.curves import with_anchors  # noqa: E402

ENTITY = "star:2:4"
OVERALL = ".library-target .library-overall"


@pytest.fixture(scope="module")
def overall_server():
    # Deterministic seeded journal and bundled real Sheet, isolated from the
    # live tracker. This context manager closes the fixture and its listeners.
    with serve_ui() as base:
        yield base


@pytest.fixture
def overall_fixture(overall_server):
    try:
        yield overall_server
    finally:
        # A failed UI assertion must not leave a pin for the next viewport.
        for version in ("us", "jp"):
            api(overall_server, f"/api/ranks/overall/{quote(ENTITY)}?version={version}", method="DELETE")


def _payload(base, version):
    return api(base, f"/api/ranks/standards?entity={quote(ENTITY)}&version={version}")


def _wait(page, expression):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if page.evaluate(expression):
            return
        page.wait_ms(50)
    assert page.evaluate(expression), expression


def _editable_pin(curve):
    # Select a real one-frame change that respects the generated neighbors.
    # Validate before driving the form so a deliberate 409 does not dilute
    # the zero-API-failure smoke gate.
    for rank in ("Silver", "Gold", "Bronze", "Diamond", "Mario"):
        frame = frame_at_or_after(curve["ladder_cs"][rank])
        for delta in (-1, 1):
            candidate = cs_of_frame(frame + delta)
            try:
                with_anchors(curve, {rank: candidate})
            except ValueError:
                continue
            return rank, candidate
    raise AssertionError("The real fixture offers no independently editable one-frame cutoff")


def _write_cutoff(page, rank, centiseconds):
    scope = f'{OVERALL} [data-tier="{rank}"] form'
    parts = {"minutes": centiseconds // 6000, "seconds": centiseconds // 100 % 60,
             "centis": centiseconds % 100}
    for name, value in parts.items():
        selector = scope + f' input[aria-label$=" {name}"]'
        page.evaluate(f"document.querySelector({json.dumps(selector)}).focus()")
        set_native_value(page, selector, str(value))
    page.click(scope + ' button[type="submit"]')


def _layout(page):
    return page.evaluate("""(() => {
      const root = document.querySelector('.library-target .library-overall');
      const rect = root.getBoundingClientRect();
      const bands = [...root.querySelectorAll('.library-overall-band')];
      const rows = [...root.querySelectorAll('.library-overall-division')];
      return {width: innerWidth, left: rect.left, right: rect.right,
        clientWidth: root.clientWidth, scrollWidth: root.scrollWidth,
        tiers: bands.length, divisions: rows.length,
        minimumIndent: Math.min(...bands.map(band =>
          band.querySelector('.library-overall-division').getBoundingClientRect().left -
          band.querySelector('.library-overall-band-head').getBoundingClientRect().left)),
        overflowingRows: rows.filter(row => row.scrollWidth > row.clientWidth + 1).length,
        editor: [...root.querySelectorAll('form input, form button')].map(el => {
          const box = el.getBoundingClientRect(); const style = getComputedStyle(el);
          return {label: el.getAttribute('aria-label') || el.textContent.trim(),
            left: box.left, right: box.right, width: box.width, height: box.height,
            fontSize: parseFloat(style.fontSize), disabled: el.disabled};
        })};
    })()""")


def _assert_layout(measurements):
    assert measurements["tiers"] == 9 and measurements["divisions"] == 45, measurements
    assert measurements["minimumIndent"] >= 8, measurements
    assert measurements["overflowingRows"] == 0, measurements
    assert measurements["scrollWidth"] <= measurements["clientWidth"] + 1, measurements
    assert 0 <= measurements["left"] < measurements["right"] <= measurements["width"], measurements
    for control in measurements["editor"]:
        assert control["height"] >= 20 and control["width"] >= 20, control
        assert control["fontSize"] >= 11, control
        assert measurements["left"] <= control["left"] < control["right"] <= measurements["right"], control


def _checked_layout(page):
    measurements = _layout(page)
    _assert_layout(measurements)
    return measurements


def _capture(page, width, name):
    directory = os.environ.get("OVERALL_UI_EVIDENCE")
    if not directory:
        return
    folder = Path(directory)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"overall-{width}-{name}.png").write_bytes(page.screenshot())


def _record(width, report):
    directory = os.environ.get("OVERALL_UI_EVIDENCE")
    if directory:
        path = Path(directory) / f"overall-{width}-report.json"
        path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def _check_other_region(page, base, original_jp, saved, fixed_selector, width):
    # Both regions start enabled. Turning US off selects the JP ladder.
    page.click('.version-switch[aria-label="Regions shown"] [aria-label="US"]')
    _wait(page, f"!document.querySelector({json.dumps(fixed_selector)})")
    page.wait_for(OVERALL + " .library-overall-band")
    japanese = _payload(base, "jp")
    assert japanese["overall_curve"] == original_jp["overall_curve"]
    assert japanese["overall_overrides"] == {}
    assert_compiled_overall(page, japanese)
    _capture(page, width, "jp")
    page.click('.version-switch[aria-label="Regions shown"] [aria-label="US"]')
    page.wait_for(fixed_selector)
    assert_compiled_overall(page, saved)


@pytest.mark.parametrize("width", [850, 1500])
def test_overall_edit_fixed_reset_and_region_isolation(overall_fixture, width):
    overall_server = overall_fixture
    before = {version: _payload(overall_server, version) for version in ("us", "jp")}
    assert all(not item["overall_overrides"] for item in before.values())
    rank, pin = _editable_pin(before["us"]["overall_curve"])
    report = {"width": width, "rank": rank, "generated_cs": before["us"]["overall_curve"]["ladder_cs"][rank],
              "fixed_cs": pin, "population": before["us"]["overall_curve"]["metadata"]["population_count"]}
    fixed_selector = f'{OVERALL} [data-tier="{rank}"] .library-overall-by .meta'
    with driver.get_driver().launch(headless=True, viewport=(width, 1100)) as page:
        page.goto(f"{overall_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        page.click('.sidebar-nav .nav-item[title="Library"]')
        page.wait_for(".library-target .library-section", timeout_ms=15000)
        page.evaluate(OPEN_OVERALL)
        page.wait_for(OVERALL + " .library-overall-band")
        assert_compiled_overall(page, before["us"])
        report["initial"] = _checked_layout(page)
        _capture(page, width, "initial")

        page.click(f'{OVERALL} [data-tier="{rank}"] .library-overall-by button')
        page.wait_for(OVERALL + " form input")
        report["editor"] = _checked_layout(page)
        assert len(report["editor"]["editor"]) == 5
        _capture(page, width, "editor")
        _write_cutoff(page, rank, pin)
        page.wait_for(fixed_selector)
        _wait(page, f"!document.querySelector({json.dumps(OVERALL + ' form')})")
        saved = _payload(overall_server, "us")
        assert saved["overall_overrides"] == {rank: pin / 100}
        assert saved["overall_curve"]["ladder_cs"][rank] == pin
        assert saved["strategies"] == before["us"]["strategies"]
        assert_compiled_overall(page, saved)
        report["fixed"] = _checked_layout(page)
        _capture(page, width, "fixed")

        _check_other_region(page, overall_server, before["jp"], saved, fixed_selector, width)

        page.click(OVERALL + " .library-overall-body > .library-overall-band-head > button")
        _wait(page, f"!document.querySelector({json.dumps(fixed_selector)})")
        restored = _payload(overall_server, "us")
        assert restored["overall_overrides"] == {}
        assert restored["overall_curve"] == before["us"]["overall_curve"]
        assert _payload(overall_server, "jp")["overall_curve"] == before["jp"]["overall_curve"]
        assert_compiled_overall(page, restored)
        report["reset"] = _checked_layout(page)
        page.set_viewport(width, 2300)
        page.evaluate("document.querySelector('.library-overall').scrollIntoView({block: 'start'})")
        page.wait_ms(250)
        _capture(page, width, "full-ladder")
        report["problems"] = page.problems()
        _record(width, report)
        assert report["problems"] == [], report["problems"]
