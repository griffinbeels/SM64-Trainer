"""The ghost recording picker stays usable when its current source is not."""
from copy import deepcopy
from pathlib import Path

from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT
from tests.test_ui_ghost_switch import (
    FIXTURE_SEGMENT, MEASURE, WIDTHS, get_driver, ghost_session,
    serve_ui_with_ghost, settled,
)

ROOT = Path(__file__).resolve().parents[1]
SELECT = ".ghost-recording-select"


def choose(page, selector, value):
    import json

    page.evaluate("""(() => {
      const pick = document.querySelector(%s);
      pick.value = %s;
      pick.dispatchEvent(new Event('change', {bubbles: true}));
    })()""" % (json.dumps(selector), json.dumps(value)))


def picker(page):
    return page.evaluate("""(() => {
      const pick = document.querySelector('.ghost-recording-select');
      const row = pick.closest('.ghost-row');
      return {value: pick.value, disabled: pick.disabled,
        label: pick.getAttribute('aria-label'),
        options: Array.from(pick.options, option => ({
          value: option.value, disabled: option.disabled, label: option.textContent.trim()})),
        fits: pick.getBoundingClientRect().right <= row.getBoundingClientRect().right};
    })()""")


def shoot(page, path):
    page.evaluate("document.querySelector('.ghost-row').scrollIntoView({block:'center'})")
    path.write_bytes(page.screenshot())


def selection_session():
    initial = ghost_session(enabled=False, available=False, view="mario")
    # The real, longest native refusal: an older pose file he must re-record.
    initial["source"].update(native_available=False, native_reason_text=OLDER_POSE_FORMAT_TEXT,
                              whole_available=False, whole_reason_text="No saved video",
                              reason_text=OLDER_POSE_FORMAT_TEXT)
    initial["selection"] = {"template_id": None, "candidates": [
        {"template_id": key, "attempt_id": 1200 + key,
         "name": "Fall onto the Caged Island", "strat": "TJ Owlless",
         "native_available": native, "whole_available": not native,
         "native_reason_text": None if native else OLDER_POSE_FORMAT_TEXT,
         "whole_reason_text": "No saved video" if native else None}
        for key, native in [(7, False), (8, True)]
    ]}
    return initial


def test_recording_picker_is_independent_reachable_and_fits_supported_widths():
    initial = selection_session()
    screenshots = ROOT / ".iteration" / "ghost-selector-ui"
    screenshots.mkdir(parents=True, exist_ok=True)
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(SELECT)
                # First prove an unrelated normal interaction updates this real app.
                page.click('.app-sidebar .nav-item[title="Run"]')
                page.wait_for('.app-sidebar .nav-item[title="Run"][aria-current="page"]')
                page.click('.app-sidebar .nav-item[title="Practice"]')
                page.wait_for(SELECT)
                before = picker(page)
                assert not before["disabled"] and before["value"] == ""
                assert before["label"] == "Ghost recording" and before["fits"]
                assert [option["disabled"] for option in before["options"]] == [False, True, False]
                assert "older pose format" in before["options"][1]["label"]
                shoot(page, screenshots / f"older-{width}.png")
                assert page.evaluate(MEASURE)[0]["disabled"]

                choose(page, SELECT, "8")
                expected = {"template_id": 8, "target": initial["target"]}
                assert settled(page, state, 1) == [expected]
                assert picker(page)["value"] == "8"
                assert state["session"]["enabled"] is False
                assert not page.evaluate(MEASURE)[0]["disabled"]
                page.click(".ghost-row-head > .ghost-switch")
                assert settled(page, state, 2)[1] == {"enabled": True}
                page.wait_for(".ghost-opacity")
                row = page.evaluate(MEASURE)[0]
                assert row["rowScroll"] <= row["rowClient"] + 1
                assert row["cardScroll"] <= row["cardClient"] + 1
                shoot(page, screenshots / f"selected-{width}.png")

                # A second recording supports Whole picture even though this one does not.
                choose(page, ".ghost-view > select", "whole")
                assert settled(page, state, 3)[2] == {"view": "whole"}
                assert [option["disabled"] for option in picker(page)["options"]] == [False, False, True]
                choose(page, SELECT, "7")
                assert settled(page, state, 4)[3] == {"template_id": 7, "target": initial["target"]}
                choose(page, SELECT, "")
                assert settled(page, state, 5)[4] == {"template_id": None, "target": initial["target"]}
                assert picker(page)["value"] == ""

                # Deleting the saved choice cannot silently choose a different recording.
                state["session"]["selection"]["template_id"] = 99
                page.goto(base)
                page.wait_for(SELECT)
                missing = picker(page)
                assert missing["value"] == "99" and not missing["disabled"]
                assert "unavailable" in missing["options"][1]["label"]
                assert page.problems() == []


def test_a_saved_pb_is_the_default_ghost_at_supported_widths():
    initial = selection_session()
    initial["source"].update(native_available=True, native_reason_text=None, reason_text=None,
                             available=True, attempt_id=1497)
    initial["selection"]["pb"] = {"attempt_id": 1497, "timer_mode": "igt",
                                  "native_available": True, "native_reason_text": None,
                                  "whole_available": False,
                                  "whole_reason_text": "This PB's video is still being saved."}
    screenshots = ROOT / ".iteration" / "ghost-selector-ui"
    screenshots.mkdir(parents=True, exist_ok=True)
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(SELECT)
                shown = picker(page)
                assert shown["value"] == "" and shown["fits"]
                assert shown["options"][0]["label"] == "Your PB · Attempt #1497"
                assert not page.evaluate(MEASURE)[0]["disabled"]
                shoot(page, screenshots / f"pb-{width}.png")
                assert page.problems() == []
