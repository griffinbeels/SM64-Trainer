"""Drive the native overlap controls on the actual practice card."""
from pathlib import Path

from tests.test_ui_ghost_switch import (FIXTURE_SEGMENT, WIDTHS, get_driver,
    ghost_session, serve_ui_with_ghost, settled)

ROOT = Path(__file__).resolve().parents[1]


def test_native_overlap_controls_preserve_distance_and_hide_for_video():
    initial = ghost_session(enabled=True, available=True, view="mario")
    initial["proximity"] = {"enabled": True, "extra_distance": 0}
    initial["source"].update(native_available=True, native_reason_text=None,
        whole_available=True, whole_reason_text=None)
    screenshots = ROOT / ".iteration/ghost-overlay/round-5-ui"
    screenshots.mkdir(parents=True, exist_ok=True)
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = initial
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(".ghost-proximity summary")
                page.click(".ghost-proximity summary")
                page.wait_for(".ghost-distance")
                page.evaluate("""(() => {
                  const distance = document.querySelector('.ghost-distance');
                  for (const value of [40, 80, 120]) {
                    distance.value=value;
                    distance.dispatchEvent(new Event('input',{bubbles:true}));
                  }
                })()""")
                assert settled(page, state, 1) == [{"proximity": {"extra_distance": 120}}]
                # The ghost's sound follows this fade: its switch sits here, on
                # by default and not in the tuning row, and one flip is one write.
                help_text = "document.querySelector('.ghost-proximity-help').textContent"
                assert page.evaluate("document.querySelector('.ghost-overlap-sound input').checked") is True
                assert not page.evaluate("!!document.querySelector('.ghost-tune .ghost-overlap-sound')")
                assert "Its sound fades the same way" in page.evaluate(help_text)
                (screenshots / f"overlap-sound-{width}.png").write_bytes(page.screenshot())
                page.click(".ghost-overlap-sound input")
                assert settled(page, state, 2)[1] == {"overlap_sound": False}
                assert page.evaluate("document.querySelector('.ghost-overlap-sound input').checked") is False
                assert "Its sound" not in page.evaluate(help_text)
                page.click(".ghost-overlap-fade input")
                assert settled(page, state, 3)[2] == {"proximity": {"enabled": False}}
                assert not page.evaluate("!!document.querySelector('.ghost-distance')")
                assert not page.evaluate("!!document.querySelector('.ghost-overlap-sound')")
                page.click(".ghost-overlap-fade input")
                assert settled(page, state, 4)[3] == {"proximity": {"enabled": True}}
                assert page.evaluate("document.querySelector('.ghost-distance').value") == "120"
                from sm64_events.ghost.settings import limits as published_limits
                reach = published_limits()["proximity"]
                assert page.evaluate("""(() => { const d=document.querySelector('.ghost-distance');
                  return [d.min, d.max, d.step].map(Number); })()""") == [reach["min"], reach["max"], reach["step"]]
                assert f"keeps {round(reach['floor'] * 100)}% of your Visibility" in page.evaluate(
                    "document.querySelector('.ghost-proximity-help').textContent")
                assert page.evaluate("""(() => {
                  const row=document.querySelector('.ghost-row');
                  return row.scrollWidth <= row.clientWidth + 1;
                })()""")
                (screenshots / f"overlap-{width}.png").write_bytes(page.screenshot())
                page.evaluate("""(() => {
                  const select=document.querySelector('.ghost-view-select');
                  select.value='whole';select.dispatchEvent(new Event('change',{bubbles:true}));
                })()""")
                assert settled(page, state, 5)[4] == {"view": "whole"}
                assert not page.evaluate("!!document.querySelector('.ghost-proximity')")
                assert page.problems() == []
