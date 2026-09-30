"""Pose-only template selection stays reachable on the actual practice page."""
import os
from pathlib import Path

from tests.test_ui_ghost_switch import (FIXTURE_SEGMENT, MEASURE, WIDTHS,
                                        get_driver, ghost_session, serve_ui_with_ghost, settled)

ROOT = Path(__file__).resolve().parents[1]


def test_pose_only_view_can_be_chosen_before_enabling_at_supported_widths():
    initial = ghost_session(enabled=False, available=False)
    initial["source"].update(native_available=True, native_reason_text=None,
                              whole_available=False, whole_reason_text="Save this run's footage to show the whole picture.",
                              reason_text="Save this run's footage to show the whole picture.")
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
                page.wait_for(".ghost-view-select")
                row = page.evaluate(MEASURE)[0]
                assert not row["viewDisabled"] and row["disabled"] and row["views"] == 1
                page.evaluate("""(() => {
                  const select=document.querySelector('.ghost-view-select');
                  select.value='mario';select.dispatchEvent(new Event('change',{bubbles:true}));
                })()""")
                assert settled(page, state, 1)[0] == {"view": "mario"}
                assert not page.evaluate(MEASURE)[0]["disabled"]
                page.click(".ghost-row-head > .ghost-switch")
                assert settled(page, state, 2)[1] == {"enabled": True}
                page.wait_for(".ghost-opacity")
                page.wait_ms(250)
                row = page.evaluate(MEASURE)[0]
                assert row["viewValue"] == "mario" and row["checked"] == "true" and row["views"] == 1
                assert row["rowScroll"] <= row["rowClient"] + 1
                assert row["cardScroll"] <= row["cardClient"] + 1
                (screenshots / f"mario-only-{width}.png").write_bytes(page.screenshot())
                assert page.problems() == []


def test_ghost_in_another_area_has_a_visible_reason_and_enabled_controls():
    initial = ghost_session(enabled=True, view="mario", mario_boxes=True)
    initial["source"].update(native_available=True, native_reason_text=None)
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
                assert row["checked"] == "true" and row["viewValue"] == "mario"
                assert row["rowScroll"] <= row["rowClient"] + 1
                assert not state["writes"]
                assert page.problems() == []
                if shots := os.environ.get("SM64_SHOT_DIR"):
                    Path(shots, f"another_area_{width}.png").write_bytes(page.screenshot())
