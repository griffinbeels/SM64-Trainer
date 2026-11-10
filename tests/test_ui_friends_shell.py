"""Reach Friends through the shipping app shell, without a live recorder/SDK."""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from find_uilab import find_uilab  # noqa: E402
if find_uilab():
    pytest.skip("uilab is required for the app shell browser gate", allow_module_level=True)
from uilab.driver import get_driver  # noqa: E402
from ui_fixture import serve_ui  # noqa: E402


@pytest.mark.parametrize("width", [640, 850, 1400])
def test_persistent_friends_entry_is_reachable_at_supported_shell_sizes(tmp_path, width):
    with serve_ui(tmp_path / "fixture.db", from_dev_db=False, seed=False) as base:
        with get_driver().launch(headless=True, viewport=(width, 1000)) as page:
            page.goto(base + "/")
            page.wait_for(".friends-control")
            assert page.count(".friends-control") == 1
            page.click(".friends-control")
            page.wait_for(".friends-unavailable")
            assert "this build" in page.evaluate("return document.querySelector('.friends-unavailable').textContent")
            bounds = page.evaluate("return document.querySelector('.modal').getBoundingClientRect().toJSON()")
            assert bounds["left"] >= 0 and bounds["right"] <= width
            page.click(".modal-close")
            assert page.count(".friends-control") == 1 and page.count(".friends-surface") == 0
            (tmp_path / f"friends-shell-{width}.png").write_bytes(page.screenshot())
            assert page.problems() == []
