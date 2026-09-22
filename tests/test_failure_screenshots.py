"""The full run's pictures of a failing page (tests/conftest.py).

A wait that fails inside a fixture has its page closed before the report
exists, so the wait photographs its own page before raising; without that the
full run keeps nothing to look at for a page that never rendered.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

import conftest  # noqa: E402
from uilab import driver  # noqa: E402


def test_a_wait_that_times_out_leaves_a_picture_of_its_page(tmp_path, monkeypatch, request):
    from playwright.sync_api import Browser, BrowserContext, Locator
    from playwright.sync_api import TimeoutError as PlaywrightTimeout
    for owner, name in ((Locator, "wait_for"), (Browser, "new_page"), (BrowserContext, "new_page")):
        monkeypatch.setattr(owner, name, getattr(owner, name))   # put back after this test
    monkeypatch.setenv("SM64_REPORT_DIR", str(tmp_path))
    conftest._keep_failure_screenshots()
    with driver.get_driver().launch(headless=True) as page:
        page.goto("data:text/html,<p>the card never comes</p>")
        with pytest.raises(PlaywrightTimeout):
            page.wait_for(".a-card-that-never-comes", timeout_ms=300)
    shots = sorted(path.name for path in (tmp_path / "screenshots").glob("*.png"))
    assert shots == [f"{conftest._stem(request.node.nodeid)}-wait-timeout.png"]
