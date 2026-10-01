"""Name/input switches and their dependent stick control on the actual practice card."""
from pathlib import Path
from copy import deepcopy

from test_ui_ghost_switch import (
    FIXTURE_SEGMENT, WIDTHS, get_driver, ghost_session, serve_ui_with_ghost, settled,
)
from test_ui_ghost_selector import PB, picks, problems_but_own_navigation, tick

ROOT = Path(__file__).resolve().parents[1]


def test_billboard_controls_write_immediately_keep_the_mode_and_fit_the_card():
    initial = ghost_session(enabled=True, view="mario", mario_boxes=True)
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(".ghost-inputs .ghost-switch")
                # A known-good sibling switch proves this fixture answers writes.
                page.click(".ghost-names .ghost-switch")
                assert settled(page, state, 1)[0] == {"names_enabled": False}
                page.click(".ghost-inputs .ghost-switch")
                assert settled(page, state, 2)[1] == {"inputs_enabled": True}
                page.wait_for(".ghost-input-mode select")
                page.evaluate("""(() => {
                  const select=document.querySelector('.ghost-input-mode select');
                  select.value='values';select.dispatchEvent(new Event('change',{bubbles:true}));
                })()""")
                assert settled(page, state, 3)[2] == {"input_mode": "values"}
                measured = page.evaluate("""(() => {
                  const input=document.querySelector('.ghost-inputs');
                  const child=document.querySelector('.ghost-input-mode');
                  const row=input.closest('.ghost-row');
                  const card=row.closest('.log-card');
                  return {parent:input.getBoundingClientRect().left, child:child.getBoundingClientRect().left,
                    selected:child.querySelector('select').value,
                    row:row.scrollWidth-row.clientWidth, card:card.scrollWidth-card.clientWidth};
                })()""")
                assert measured["selected"] == "values" and measured["child"] > measured["parent"]
                assert measured["row"] <= 1 and measured["card"] <= 1
                shots = ROOT / ".iteration" / "ghost148-controls"
                shots.mkdir(parents=True, exist_ok=True)
                (shots / f"inputs-values-{width}.png").write_bytes(page.screenshot())
                page.click(".ghost-inputs .ghost-switch")
                assert settled(page, state, 4)[3] == {"inputs_enabled": False}
                assert page.evaluate("document.querySelector('.ghost-input-mode') === null")
                page.click(".ghost-inputs .ghost-switch")
                assert settled(page, state, 5)[4] == {"inputs_enabled": True}
                page.wait_for(".ghost-input-mode select")
                assert page.evaluate("document.querySelector('.ghost-input-mode select').value") == "values"
                assert problems_but_own_navigation(page) == []


def test_imported_ghost_credit_and_ticks_survive_local_and_pb_changes():
    identity = "ab" * 32
    initial = ghost_session(enabled=True, view="mario", mario_boxes=True)
    initial["selection"] = {
        "ghosts": {"pb": True, "attempts": [1908], "imports": [identity]}, "missing": [],
        "pb": dict(PB), "playing": [{"key": "pb", "hue_degrees": 120},
                                     {"key": 1908, "hue_degrees": 257},
                                     {"key": identity, "hue_degrees": 34}],
        "candidates": [
            {"attempt_id": 1908, "frames": 540, "strat": initial["target"]["strat"],
             "native_available": True, "native_reason_text": None,
             "whole_available": False, "whole_reason_text": "No saved video"},
            {"attempt_id": None, "import_id": identity, "frames": 538,
             "author": "René the Very Patient Speedrunner", "strat": "Sideflip",
             "native_available": True, "native_reason_text": None,
             "whole_available": False, "whole_reason_text": "Imported ghosts are shown with Only Mario."},
        ],
    }
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(".ghost-names .ghost-switch")
                page.click(".ghost-names .ghost-switch")
                assert settled(page, state, 1)[0] == {"names_enabled": False}
                shown = picks(page)
                assert len(shown["items"]) == 3
                assert shown["items"][2]["label"] == '17"93 · René the Very Patient Speedrunner · Sideflip'
                assert all(row["fits"] for row in shown["items"])
                tick(page, 1)
                assert settled(page, state, 2)[1] == {
                    "ghosts": {"pb": True, "attempts": [], "imports": [identity]}, "target": initial["target"]}
                tick(page, 0)
                assert settled(page, state, 3)[2] == {
                    "ghosts": {"pb": False, "attempts": [], "imports": [identity]}, "target": initial["target"]}
                shown = picks(page)
                assert [row["checked"] for row in shown["items"]] == [False, False, True]
                assert shown["items"][2]["hue"] is not None
                assert state["session"]["selection"]["playing"][0]["key"] == identity
                assert state["session"]["source"]["attempt_id"] is None
                assert problems_but_own_navigation(page) == []
