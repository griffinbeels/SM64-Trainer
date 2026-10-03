"""Name/input switches and their dependent stick control on the actual practice card."""
from pathlib import Path
from copy import deepcopy

from test_ui_ghost_switch import (
    FIXTURE_SEGMENT, WIDTHS, get_driver, ghost_session, serve_ui_with_ghost, settled,
)
from test_ui_ghost_selector import PB, picks, problems_but_own_navigation, tick

ROOT = Path(__file__).resolve().parents[1]


def test_billboard_switches_write_immediately_and_offer_no_mode_of_their_own():
    """Names and Inputs write on the click; there is no Stick/Values picker,
    because every ghost display takes the mode he chose for his own HUD in
    Usamune's menu (his ask, 2026-10-02: "keep these in sync")."""
    initial = ghost_session(enabled=True, view="mario", mario_boxes=True)
    initial["hud_inputs_enabled"] = False  # each switch is probed from off
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
                page.click(".ghost-hud-inputs .ghost-switch")
                assert settled(page, state, 3)[2] == {"hud_inputs_enabled": True}
                # Ghosts in recording: off until he turns it on (his choice, 2026-10-02).
                page.click(".ghost-record .ghost-switch")
                assert settled(page, state, 4)[3] == {"record_ghosts": True}
                measured = page.evaluate("""(() => {
                  const input=document.querySelector('.ghost-inputs');
                  const row=input.closest('.ghost-row');
                  const card=row.closest('.log-card');
                  return {picker:document.querySelector('.ghost-input-mode')!==null,
                    row:row.scrollWidth-row.clientWidth, card:card.scrollWidth-card.clientWidth};
                })()""")
                assert not measured["picker"], "no separate Stick/Values choice to drift from his HUD"
                assert measured["row"] <= 1 and measured["card"] <= 1
                shots = ROOT / ".iteration" / "ghost148-controls"
                shots.mkdir(parents=True, exist_ok=True)
                (shots / f"inputs-{width}.png").write_bytes(page.screenshot())
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
                assert problems_but_own_navigation(page) == []


PATH_MEASURE = """(() => {
  const row=document.querySelector('.ghost-row');
  const card=row.closest('.log-card');
  const path=document.querySelector('.ghost-path');
  const options=document.querySelector('.ghost-path-options');
  const tune=document.querySelector('.ghost-path-tune');
  const box=(el)=>el?el.getBoundingClientRect():null;
  return {
    path: path ? path.querySelector('.ghost-switch').getAttribute('aria-checked') : null,
    options: Boolean(options), tune: Boolean(tune), open: Boolean(tune && tune.open),
    buttons: (document.querySelector('.ghost-path-buttons .ghost-switch')||{}).ariaChecked ?? null,
    source: (document.querySelector('.ghost-path-source select')||{}).value ?? null,
    sourceOptions: Array.from(document.querySelectorAll('.ghost-path-source option')).map((o)=>o.textContent.trim()),
    sliders: Array.from(document.querySelectorAll('.ghost-path-range')).map((input)=>({
      name: input.closest('.ghost-field').querySelector('.ghost-field-name').textContent.trim(),
      min: Number(input.min), max: Number(input.max), step: Number(input.step), value: Number(input.value),
      readout: input.closest('.ghost-field').querySelector('.ghost-field-value').textContent.trim(),
      right: input.closest('.ghost-field').getBoundingClientRect().right})),
    parent: path ? box(path).left : null, child: options ? box(options).left : null,
    cardRight: box(card).right,
    row: row.scrollWidth-row.clientWidth, card: card.scrollWidth-card.clientWidth,
    page: document.documentElement.scrollWidth-document.documentElement.clientWidth,
  };
})()"""


def test_the_path_nests_its_choices_and_tunes_from_the_published_ranges():
    """Path (on at first) carries Path buttons and Path from beneath it, and a
    Tune path disclosure whose four sliders are the server's ranges; each
    writes the one thing it changed, and switching the path off takes all of
    them away (his ruling: omit what does not apply)."""
    from sm64_events.ghost import settings
    ranges = settings.limits()["path_tuning"]
    initial = ghost_session(enabled=True, view="mario", mario_boxes=True)
    with serve_ui_with_ghost(initial, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for width in WIDTHS:
                state["session"] = deepcopy(initial)
                state["writes"].clear()
                page.set_viewport(width, 1200)
                page.goto(base)
                page.wait_for(".ghost-path-tune summary")
                # A known-good sibling switch proves this fixture answers writes.
                page.click(".ghost-names .ghost-switch")
                assert settled(page, state, 1)[0] == {"names_enabled": False}
                page.click(".ghost-path-tune summary")
                page.wait_ms(100)
                shown = page.evaluate(PATH_MEASURE)
                assert shown["path"] == "true" and shown["buttons"] == "false" and shown["open"]
                assert shown["source"] == "fastest"
                assert shown["sourceOptions"] == ["Fastest ghost", "Average of ghosts"]
                assert [slider["name"] for slider in shown["sliders"]] == [
                    "How far ahead", "Width", "Strength", "Arrow spacing"]
                assert [(slider["min"], slider["max"], slider["step"]) for slider in shown["sliders"]] == [
                    (ranges[name]["min"], ranges[name]["max"], ranges[name]["step"])
                    for name in ("ahead_s", "width", "opacity", "spacing_s")]
                # The shipped defaults, read from the registry rather than restated.
                tuning = settings.DEFAULT_PATH_TUNING
                assert [slider["readout"] for slider in shown["sliders"]] == [
                    f"{tuning['ahead_s']:.1f} s", f"{tuning['width']:.0f} units", f"{tuning['opacity']:.0%}",
                    f"{tuning['spacing_s']:.2f} s"]
                assert shown["child"] > shown["parent"], "the path's choices hang beneath it"
                assert shown["row"] <= 1 and shown["card"] <= 1 and shown["page"] <= 0
                assert all(slider["right"] <= shown["cardRight"] + 1 for slider in shown["sliders"])
                page.click(".ghost-path-buttons .ghost-switch")
                assert settled(page, state, 2)[1] == {"path_buttons": True}
                page.evaluate("""(() => {
                  const select=document.querySelector('.ghost-path-source select');
                  select.value='average';select.dispatchEvent(new Event('change',{bubbles:true}));
                })()""")
                assert settled(page, state, 3)[2] == {"path_source": "average"}
                # Two sliders dragged inside one settle window: one write carrying both.
                page.evaluate("""(() => {
                  const [ahead, wide]=document.querySelectorAll('.ghost-path-range');
                  for (const value of ['50','60','75']) {
                    wide.value=value;wide.dispatchEvent(new Event('input',{bubbles:true}));
                  }
                  ahead.value='5';ahead.dispatchEvent(new Event('input',{bubbles:true}));
                })()""")
                assert settled(page, state, 4)[3:] == [{"path_tuning": {"width": 75, "ahead_s": 5}}]
                shown = page.evaluate(PATH_MEASURE)
                assert [slider["readout"] for slider in shown["sliders"]][:2] == ["5.0 s", "75 units"]
                assert state["session"]["path_tuning"] == {**settings.DEFAULT_PATH_TUNING, "width": 75, "ahead_s": 5}
                page.click(".ghost-path .ghost-switch")
                assert settled(page, state, 5)[4] == {"path_enabled": False}
                shown = page.evaluate(PATH_MEASURE)
                assert shown["path"] == "false" and not shown["options"] and not shown["tune"]
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
