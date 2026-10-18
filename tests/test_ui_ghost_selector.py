"""The ghost ticks: his PB first, any saved recordings beside it, at every width."""
from copy import deepcopy
from pathlib import Path

from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT
from sm64_events.ghost.settings import limits as published_limits
from tests.test_ui_ghost_switch import (
    FIXTURE_SEGMENT, MEASURE, TOGGLE, WIDTHS, get_driver, ghost_session,
    serve_ui_with_ghost, settled,
)

ROOT = Path(__file__).resolve().parents[1]
PICKS = ".ghost-recordings"
#: The rendered sliders and the published range each one must carry.
SLIDERS = {".ghost-opacity": "opacity", ".ghost-hue": "tint", ".ghost-volume-range": "volume"}
READ_SLIDERS = """(() => Object.fromEntries(%s.map((selector) => {
  const input = document.querySelector(selector);
  return [selector, input && [input.min, input.max, input.step].map(Number)];
})))()"""
PB = {"attempt_id": 1906, "frames": 534, "native_available": True,
      "native_reason_text": None}


def tick(page, index):
    """Click the real checkbox of the `index`th pill (0 is his PB)."""
    page.evaluate("document.querySelectorAll('.ghost-pick input')[%d].click()" % index)


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
    available = bool(pb and pb["native_available"])
    initial = ghost_session(enabled=False, available=available)
    # The real, longest native refusal: an older pose file he must re-record.
    initial["source"]["reason_text"] = None if available else "There is no personal best to race here yet."
    strat = initial["target"]["strat"]
    initial["selection"] = {"ghosts": {"pb": True, "attempts": []}, "missing": [],
                            "pb": dict(pb) if pb else None, "playing": [], "candidates": [
        {"attempt_id": attempt, "frames": frames, "strat": run_strat or strat,
         "native_available": native,
         "native_reason_text": None if native else OLDER_POSE_FORMAT_TEXT}
        for attempt, frames, run_strat, native in [
            (1907, 538, None, False), (1908, 540, None, True), (1909, 560, "Sideflip", True)]
    ]}
    return initial


# The page's timed GET polls (`pollJSON` in ui/store.js and ui/components/replay.js).
POLLS = ("/api/pause", "/api/replay/status")


def problems_but_own_navigation(page):
    """Page problems, less the polls this test's own `goto` cancels.

    Each width reloads the page, and a timed poll still in flight from the
    previous load comes back net::ERR_ABORTED -- under a loaded full run that
    is often (/api/pause in the merge checks of 2026-09-28 and 09-29,
    /api/replay/status twice on 2026-10-13). The poll simply runs again on
    the new page, so the abort is the test's doing; any other failed request
    still counts."""
    return [problem for problem in page.problems()
            if not (problem.startswith("requestfailed: ")
                    and any(problem.endswith(f"{poll} (net::ERR_ABORTED)") for poll in POLLS))]


def flip_off(page, state, owner, setting, writes):
    """A native ghost switch that starts on: one press turns it off."""
    checked = f"document.querySelector('.{owner} .ghost-switch').getAttribute('aria-checked')"
    assert page.evaluate(checked) == "true"
    page.click(f".{owner} .ghost-switch")
    assert settled(page, state, writes)[writes - 1] == {setting: False}
    assert page.evaluate(checked) == "false"


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
                # Named by the time each run achieved; a strategy only when it differs.
                assert [(i["label"], i["checked"], i["disabled"]) for i in shown["items"]] == [
                    ('Your PB · 17"80', True, False), ('17"93', False, True),
                    ('18"00', False, False), ('18"66 · Sideflip', False, False)]
                assert all(item["fits"] for item in shown["items"])

                tick(page, 2)
                assert settled(page, state, 1) == [
                    {"ghosts": {"pb": True, "attempts": [1908]}, "target": target}]
                tick(page, 3)
                assert settled(page, state, 2)[1] == {"ghosts": {"pb": True, "attempts": [1908, 1909]},
                                                      "target": target}
                page.click(TOGGLE)
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
                # Every slider's range is the one the server published.
                ranges = published_limits()
                shown = page.evaluate(READ_SLIDERS % list(SLIDERS))
                assert shown == {selector: [ranges[name][key] for key in ("min", "max", "step")]
                                 for selector, name in SLIDERS.items()}

                # The shadow A/B switch sits beside the colour.
                flip_off(page, state, "ghost-shadow", "shadow", 4)
                page.evaluate("""(() => { const range = document.querySelector('.ghost-volume-range');
                  range.value = '0.8'; range.dispatchEvent(new Event('input', {bubbles: true})); })()""")
                assert settled(page, state, 5)[4] == {"volume": 0.8}
                # So does the player-first fade's A/B switch, on by default.
                flip_off(page, state, "ghost-front-fade", "occlusion_fade", 6)
                # And the switch that fades its sound with its picture, on by
                # default, right before the volume.
                assert page.evaluate("""(() => { const s = document.querySelector('.ghost-sound-fade');
                  return s.nextElementSibling === document.querySelector('.ghost-volume'); })()""")
                flip_off(page, state, "ghost-sound-fade", "sound_fade", 7)

                # Unticking his PB is remembered: the write says so explicitly.
                tick(page, 0)
                assert settled(page, state, 8)[7] == {"ghosts": {"pb": False, "attempts": [1908, 1909]},
                                                      "target": target}
                assert [item["checked"] for item in picks(page)["items"]] == [False, False, True, True]
                assert problems_but_own_navigation(page) == []


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
                assert problems_but_own_navigation(page) == []
