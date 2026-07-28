"""The probe must FIND a defect that is really there, and stay quiet otherwise.

A probe verified only against the healthy app is a probe that might be
returning [] for the wrong reason -- an exception swallowed, a selector that
matches nothing, a units mistake.  So each class is proved by INJECTING the
defect into the live page and watching the count go up.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from cdp import CHROME, chrome_session  # noqa: E402
from responsive_sweep import PROBE_JS  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402

pytestmark = pytest.mark.skipif(CHROME is None, reason="Chrome not installed")


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    db = tmp_path_factory.mktemp("probe") / "probe.db"
    with serve_ui(db) as base, chrome_session(f"{base}/ui/index.html") as session:
        session.set_viewport(1600, 1000)
        session.evaluate(PROBE_JS)
        # Control assertion: everything below measures the real tree, so prove
        # there IS one and that the probe installed itself.
        assert session.evaluate("document.querySelectorAll('.app-shell').length") == 1
        assert session.evaluate("typeof window.__sweep") == "function"
        yield session


def _sweep(page, tabs="[]"):
    return json.loads(page.evaluate(f"JSON.stringify(__sweep({tabs}))"))


def test_a_healthy_element_is_not_reported(page):
    page.evaluate("""(() => {
      const ok = document.createElement('div');
      ok.id = 'probe-ok';
      ok.style.cssText = 'width:80px;height:20px;overflow:hidden';
      ok.textContent = 'hi';
      document.body.appendChild(ok);
    })()""")
    try:
        clipped = json.dumps(_sweep(page)["clipped"])
        assert "probe-ok" not in clipped
    finally:
        page.evaluate("document.getElementById('probe-ok').remove()")


def test_content_clipped_by_a_fixed_height_is_detected(page):
    """The reported bug's exact shape: a hard height + overflow:hidden with
    content taller than the box."""
    page.evaluate("""(() => {
      const bad = document.createElement('div');
      bad.id = 'probe-clip';
      bad.style.cssText = 'width:80px;height:20px;overflow:hidden';
      bad.innerHTML = '<p style="height:400px;margin:0">tall</p>';
      document.body.appendChild(bad);
    })()""")
    try:
        assert "probe-clip" in json.dumps(_sweep(page)["clipped"])
    finally:
        page.evaluate("document.getElementById('probe-clip').remove()")


def test_content_escaping_the_viewport_sideways_is_detected(page):
    """Must be found by GEOMETRY, because scrollWidth cannot see it here.

    index.html:531 sets `html, body, #app { overflow-x: hidden }`, so this app
    never grows a horizontal scrollbar and documentElement.scrollWidth never
    exceeds innerWidth however far content escapes -- measured 2026-07-28, a
    5000px-wide injected child moved it by exactly zero.  A scrollWidth-based
    probe would have reported clean forever.  This test pins the geometry
    check by asserting BOTH halves: scrollWidth stays put, the probe fires.
    """
    baseline = len(_sweep(page)["overflow"])
    page.evaluate("""(() => {
      const wide = document.createElement('div');
      wide.id = 'probe-wide';
      wide.style.cssText = 'width:5000px;height:4px';
      document.body.appendChild(wide);
    })()""")
    try:
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"), (
            "overflow-x:hidden was removed -- scrollWidth can see this now, "
            "and the comment above is stale")
        assert len(_sweep(page)["overflow"]) > baseline, (
            "5000px child did not trip the geometry probe")
    finally:
        page.evaluate("document.getElementById('probe-wide').remove()")


def test_an_element_inside_a_scrollable_ancestor_is_not_reported(page):
    """Hanging past the edge is fine when something can scroll to reveal it."""
    page.evaluate("""(() => {
      const reel = document.createElement('div');
      reel.id = 'probe-reel';
      reel.style.cssText = 'overflow-x:auto;width:200px';
      reel.innerHTML = '<div style="width:5000px;height:4px"></div>';
      document.body.appendChild(reel);
    })()""")
    try:
        assert "probe-reel" not in json.dumps(_sweep(page)["overflow"])
    finally:
        page.evaluate("document.getElementById('probe-reel').remove()")


def test_overlapping_flow_siblings_are_detected(page):
    page.evaluate("""(() => {
      const host = document.createElement('div');
      host.id = 'probe-lap';
      host.innerHTML =
        '<div style="height:40px">a</div>' +
        '<div style="height:40px;margin-top:-30px">b</div>';
      document.body.appendChild(host);
    })()""")
    try:
        assert "probe-lap" in json.dumps(_sweep(page)["overlap"])
    finally:
        page.evaluate("document.getElementById('probe-lap').remove()")


def test_absolutely_positioned_overlap_is_ignored(page):
    """Overlap is narrow on purpose -- a popover ON something is not a bug."""
    page.evaluate("""(() => {
      const host = document.createElement('div');
      host.id = 'probe-abs'; host.style.position = 'relative';
      host.innerHTML =
        '<div style="height:40px">a</div>' +
        '<div style="position:absolute;inset:0">b</div>';
      document.body.appendChild(host);
    })()""")
    try:
        assert "probe-abs" not in json.dumps(_sweep(page)["overlap"])
    finally:
        page.evaluate("document.getElementById('probe-abs').remove()")


def test_an_unreachable_tab_is_reported_and_a_reachable_one_is_not(page):
    result = json.dumps(_sweep(page, '["Practice","Nonexistent Tab"]')["unreachable"])
    assert "Nonexistent Tab" in result
    assert "Practice" not in result
