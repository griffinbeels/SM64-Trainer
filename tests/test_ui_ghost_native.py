"""Native ghost playback stays reachable on the actual practice page."""
import os
from pathlib import Path

from tests.test_ui_ghost_switch import (FIXTURE_SEGMENT, MEASURE, WIDTHS,
                                        get_driver, ghost_session, serve_ui_with_ghost, settled)

ROOT = Path(__file__).resolve().parents[1]


def test_pose_only_ghost_can_be_enabled_at_supported_widths():
    initial = ghost_session(enabled=False, available=True)
    screenshots = ROOT / ".iteration" / "native-ui"
    screenshots.mkdir(parents=True, exist_ok=True)
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = initial
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                # The row mounts before GET /api/ghost supplies availability.
                page.wait_for(".ghost-row-head > .ghost-switch:enabled")
                row = page.evaluate(MEASURE)[0]
                assert not row["disabled"] and row["checked"] == "false"
                page.click(".ghost-row-head > .ghost-switch")
                assert settled(page, state, 1) == [{"enabled": True}]
                page.wait_for(".ghost-opacity")
                page.wait_ms(250)
                row = page.evaluate(MEASURE)[0]
                assert row["checked"] == "true"
                assert row["rowScroll"] <= row["rowClient"] + 1
                assert row["cardScroll"] <= row["cardClient"] + 1
                (screenshots / f"mario-only-{width}.png").write_bytes(page.screenshot())
                assert page.problems() == []


def test_ghost_in_another_area_has_a_visible_reason_and_enabled_controls():
    initial = ghost_session(enabled=True)
    initial["window"] = {"running": True, "visible": False, "backend": "native",
                         "reason": "Ghost is in another area."}
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(".ghost-opacity")
                page.wait_ms(250)
                row = page.evaluate(MEASURE)[0]
                assert row["whyText"] == "Ghost is in another area."
                assert row["whySeen"] and not row["disabled"]
                assert row["checked"] == "true"
                assert row["rowScroll"] <= row["rowClient"] + 1
                assert not state["writes"]
                assert page.problems() == []
                if shots := os.environ.get("SM64_SHOT_DIR"):
                    Path(shots, f"another_area_{width}.png").write_bytes(page.screenshot())
