"""The ghost ticks: his PB first, any saved recordings beside it, at every width."""
from copy import deepcopy
from pathlib import Path

from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT
from tests.test_ui_ghost_switch import (
    FIXTURE_SEGMENT, MEASURE, WIDTHS, get_driver, ghost_session,
    serve_ui_with_ghost, settled,
)

ROOT = Path(__file__).resolve().parents[1]
PICKS = ".ghost-recordings"
PB = {"attempt_id": 1497, "timer_mode": "igt", "native_available": True,
      "native_reason_text": None, "whole_available": False,
      "whole_reason_text": "This PB's video is still being saved."}


def tick(page, key):
    """Click the real checkbox for `key` ("pb" or a template id)."""
    page.evaluate("""(() => {
      const items = Array.from(document.querySelectorAll('.ghost-pick'));
      const name = %s;
      const item = items.find(el => name === 'pb'
        ? el.textContent.includes('Your PB') : el.textContent.includes('Attempt #' + (1200 + Number(name))));
      item.querySelector('input').click();
    })()""" % repr(str(key)))


def picks(page):
    return page.evaluate("""(() => {
      const box = document.querySelector('.ghost-recordings');
      const row = box.closest('.ghost-row');
      const rowBox = row.getBoundingClientRect();
      return {items: Array.from(box.querySelectorAll('.ghost-pick'), el => ({
          label: el.querySelector('.ghost-pick-name').textContent.trim(),
          checked: el.querySelector('input').checked,
          disabled: el.querySelector('input').disabled,
          hue: el.querySelector('.ghost-pick-hue') ? getComputedStyle(el.querySelector('.ghost-pick-hue')).backgroundColor : null,
          fits: el.getBoundingClientRect().right <= rowBox.right + 0.5})),
        legend: box.querySelector('legend').textContent.trim()};
    })()""")


def shoot(page, path):
    page.evaluate("document.querySelector('.ghost-row').scrollIntoView({block:'center'})")
    path.write_bytes(page.screenshot())


def selection_session(pb=PB):
    initial = ghost_session(enabled=False, available=False, view="mario")
    # The real, longest native refusal: an older pose file he must re-record.
    initial["source"].update(native_available=False, native_reason_text=OLDER_POSE_FORMAT_TEXT,
                              whole_available=False, whole_reason_text="No saved video",
                              reason_text=OLDER_POSE_FORMAT_TEXT)
    initial["selection"] = {"ghosts": {"pb": True, "templates": []}, "missing": [],
                            "pb": dict(pb) if pb else None, "playing": [], "candidates": [
        {"template_id": key, "attempt_id": 1200 + key,
         "name": "Fall onto the Caged Island", "strat": "TJ Owlless",
         "native_available": native, "whole_available": not native,
         "native_reason_text": None if native else OLDER_POSE_FORMAT_TEXT,
         "whole_reason_text": "No saved video" if native else None}
        for key, native in [(7, False), (8, True), (9, True)]
    ]}
    return initial


def test_ghost_ticks_race_pb_plus_saved_ghosts_and_fit_supported_widths():
    initial = selection_session()
    screenshots = ROOT / ".iteration" / "ghost-selector-ui"
    screenshots.mkdir(parents=True, exist_ok=True)
    target = initial["target"]
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(PICKS)
                shown = picks(page)
                assert shown["legend"] == "Ghosts"
                assert [(i["label"].split(" · ")[0], i["checked"], i["disabled"]) for i in shown["items"]] == [
                    ("Your PB", True, False), ("Attempt #1207", False, True),
                    ("Attempt #1208", False, False), ("Attempt #1209", False, False)]
                assert all(item["fits"] for item in shown["items"])

                tick(page, 8)
                assert settled(page, state, 1) == [
                    {"ghosts": {"pb": True, "templates": [8]}, "target": target}]
                tick(page, 9)
                assert settled(page, state, 2)[1] == {"ghosts": {"pb": True, "templates": [8, 9]},
                                                      "target": target}
                page.click(".ghost-row-head > .ghost-switch")
                assert settled(page, state, 3)[2] == {"enabled": True}
                page.wait_for(".ghost-opacity")
                shown = picks(page)
                assert [item["checked"] for item in shown["items"]] == [True, False, True, True]
                hues = [item["hue"] for item in shown["items"] if item["checked"]]
                assert None not in hues and len(set(hues)) == 3  # three ghosts, three colours
                row = page.evaluate(MEASURE)[0]
                assert row["rowScroll"] <= row["rowClient"] + 1
                assert row["cardScroll"] <= row["cardClient"] + 1
                shoot(page, screenshots / f"ticks-{width}.png")

                # Unticking his PB is remembered: the write says so explicitly.
                tick(page, "pb")
                assert settled(page, state, 4)[3] == {"ghosts": {"pb": False, "templates": [8, 9]},
                                                      "target": target}
                assert [item["checked"] for item in picks(page)["items"]] == [False, False, True, True]
                assert page.problems() == []


def test_no_pb_yet_keeps_the_box_ticked_for_the_next_one():
    initial = selection_session(pb=None)
    screenshots = ROOT / ".iteration" / "ghost-selector-ui"
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(PICKS)
                first = picks(page)["items"][0]
                assert first["label"] == "Your PB" and first["checked"] and not first["disabled"]
                assert page.evaluate(
                    "document.querySelector('.ghost-pick-why').textContent").endswith("next PB appears here")
                shoot(page, screenshots / f"no-pb-{width}.png")
                assert page.problems() == []
