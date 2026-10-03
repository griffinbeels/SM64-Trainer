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
    # The corner stack off, so the Stick display answers to Inputs alone here.
    initial["hud_inputs_enabled"] = False
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


def test_name_box_and_corner_inputs_write_and_fit_the_card():
    """The box behind the names nests under Names (colour on the pick, solidity
    on the slider's trailing edge) and the corner inputs are their own switch."""
    initial = ghost_session(enabled=True, view="mario", mario_boxes=True)
    initial["hud_inputs_enabled"] = False  # the switch is probed from off
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(".ghost-name-box select")
                # A known-good sibling switch proves this fixture answers writes.
                page.click(".ghost-inputs .ghost-switch")
                assert settled(page, state, 1)[0] == {"inputs_enabled": True}
                page.evaluate("""(() => {
                  const select=document.querySelector('.ghost-name-box select');
                  select.value='white';select.dispatchEvent(new Event('change',{bubbles:true}));
                })()""")
                assert settled(page, state, 2)[1] == {"name_background": "white"}
                page.evaluate("""(() => {
                  const range=document.querySelector('.ghost-name-box-range');
                  for (const value of ['0.5','0.4','0.3']) {
                    range.value=value;range.dispatchEvent(new Event('input',{bubbles:true}));
                  }
                })()""")
                assert settled(page, state, 3)[2:] == [{"name_background_opacity": 0.3}]
                page.click(".ghost-hud-inputs .ghost-switch")
                assert settled(page, state, 4)[3] == {"hud_inputs_enabled": True}
                page.wait_for(".ghost-input-mode select")
                measured = page.evaluate("""(() => {
                  const names=document.querySelector('.ghost-names');
                  const box=document.querySelector('.ghost-name-box');
                  const row=names.closest('.ghost-row');
                  const card=row.closest('.log-card');
                  return {parent:names.getBoundingClientRect().left, child:box.getBoundingClientRect().left,
                    colour:box.querySelector('select').value, readout:box.querySelector('.ghost-field-value').textContent,
                    corner:document.querySelector('.ghost-hud-inputs .ghost-switch').getAttribute('aria-checked'),
                    row:row.scrollWidth-row.clientWidth, card:card.scrollWidth-card.clientWidth};
                })()""")
                assert measured["colour"] == "white" and measured["readout"] == "30%"
                assert measured["corner"] == "true" and measured["child"] > measured["parent"]
                assert measured["row"] <= 1 and measured["card"] <= 1
                shots = ROOT / ".iteration" / "ghost-name-box"
                shots.mkdir(parents=True, exist_ok=True)
                (shots / f"name-box-{width}.png").write_bytes(page.screenshot())
                page.click(".ghost-names .ghost-switch")
                assert settled(page, state, 5)[4] == {"names_enabled": False}
                assert page.evaluate("document.querySelector('.ghost-name-box') === null")
                page.click(".ghost-names .ghost-switch")
                assert settled(page, state, 6)[5] == {"names_enabled": True}
                page.wait_for(".ghost-name-box select")
                assert page.evaluate("document.querySelector('.ghost-name-box select').value") == "white"
                # One stick display serves both: with only the corner stack on it stays offered.
                page.click(".ghost-inputs .ghost-switch")
                assert settled(page, state, 7)[6] == {"inputs_enabled": False}
                page.wait_for(".ghost-input-mode select")
                page.click(".ghost-hud-inputs .ghost-switch")
                assert settled(page, state, 8)[7] == {"hud_inputs_enabled": False}
                assert page.evaluate("document.querySelector('.ghost-input-mode') === null")
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
