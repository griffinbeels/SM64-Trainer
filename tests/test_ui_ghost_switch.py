"""The Ghost Overlay switch, on the real practice card.

Two halves, and neither substitutes for the other (`.claude/rules/ui-core.md`
states the layered-guard rule this follows): source scans that hold the rules
a render cannot see, then the rendered card in each of its three states at the
supported floor and wide.

What the render is here to catch is the class of failure this project keeps
paying for: a control whose markup is perfect and whose CSS never applies, a
refusal sentence that is in the DOM but not on the screen, and a row that
overflows the card it sits in at the one width nobody looks at.
"""
import json
import re
import sys
from pathlib import Path

import pytest

from source_scan import strip_comments

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"
PRACTICELOG_JS = UI / "components" / "practicelog.js"
GHOSTSWITCH_JS = UI / "components" / "ghostswitch.js"
INDEX_HTML = UI / "index.html"

WIDTHS = (1500, 850)


# --------------------------------------------------------------------------
# The rules a render cannot see
# --------------------------------------------------------------------------

def test_the_practice_log_mounts_the_switch_exactly_once_and_gates_it_on_active():
    """ONE element, on an existing line, gated by the `active` prop.

    `codex/first-strategy-session` is rebuilding these cards, so this file's
    whole footprint is one mount. The active-card rule travels as a PROP
    rather than as a conditional beside the mount -- asserted here so that
    moving it back into practicelog.js is a deliberate change rather than a
    drift, and so the gate cannot quietly disappear from both places.
    """
    source = strip_comments(PRACTICELOG_JS.read_text(encoding="utf-8"))
    mounts = re.findall(r"<\$\{GhostSwitch\}[^>]*>", source)
    assert len(mounts) == 1, f"expected one GhostSwitch mount, found {mounts}"
    assert "active=${active}" in mounts[0], (
        "the mount must pass the card's own active flag -- the switch belongs "
        f"to the entity being practised, not to every card: {mounts[0]}")
    assert "entityKey=${ek}" in mounts[0]
    # ...and it is not gated on a kind, which would ship it for stars alone.
    assert not re.search(r"isSegment\(sec\)[^\n]*GhostSwitch", source), (
        "the switch is gated on the entity kind -- stars and segments both "
        "get it (rule 11)")


def test_the_switch_never_reaches_a_card_that_is_not_active():
    """The prop is load-bearing: both the fetch and the render read it. With
    nine cards on the page, a switch that ignored it would be nine reads of
    one app-wide session and nine controls claiming to speak for it."""
    source = strip_comments(GHOSTSWITCH_JS.read_text(encoding="utf-8"))
    assert "if (!active) return undefined;" in source, (
        "the session read is not gated on `active` -- every card would "
        "request the session")
    assert "if (!active || !reachable) return null;" in source, (
        "GhostSwitch renders without checking `active`")


def test_only_the_ghost_switch_names_the_session_endpoint():
    """One door, the way `ui/target.js` owns /api/target. A second caller is
    how two surfaces come to disagree about one app-wide session."""
    named = [path.name for path in UI.rglob("*.js")
             if "/api/ghost" in strip_comments(path.read_text(encoding="utf-8"))]
    assert named == ["ghostswitch.js"], named


def test_every_class_the_switch_renders_is_styled():
    """A class rename empties the stylesheet silently: the markup stays
    correct, `node --check` is happy, and nothing paints."""
    source = GHOSTSWITCH_JS.read_text(encoding="utf-8")
    css = INDEX_HTML.read_text(encoding="utf-8")
    rendered = set()
    for match in re.finditer(r'class="([^"$]*)"', source):
        rendered.update(re.findall(r"[\w-]+", match.group(1)))
    ghost_classes = {name for name in rendered if name.startswith("ghost-")}
    assert ghost_classes, "no ghost classes found -- did the scan break?"
    missing = [name for name in sorted(ghost_classes) if f".{name}" not in css]
    assert not missing, f"unstyled classes: {missing}"


def test_the_stylesheet_carries_one_ghost_block():
    """One block, per the branch's own constraint. Counted on the section
    banner rather than on rule bodies, which a reformat would move."""
    css = INDEX_HTML.read_text(encoding="utf-8")
    assert css.count("The Ghost Overlay row (ui/components/ghostswitch.js)") == 1


def test_no_technique_names_reach_the_player():
    """His standing rule for anything a user reads: say what it does. The
    words the ghost machinery uses internally -- frame map, nudge, opacity --
    must not appear in a label, a readout or a hover on this control."""
    source = GHOSTSWITCH_JS.read_text(encoding="utf-8")
    visible = " ".join(re.findall(r'title=[^>]*?"([^"]+)"', source)
                       + re.findall(r">([A-Z][A-Za-z ]+)<", source))
    for banned in ("frame map", "nudge", "opacity", "template", "PJ64"):
        assert banned.lower() not in visible.lower(), (
            f"user-facing wording names the technique: {banned!r} in {visible!r}")


def test_the_fixtures_ghost_door_is_not_shadowed_by_the_real_one():
    """The app factory mounts `/api/ghost` itself now, and with no session
    behind it — an offline fixture — it answers 503. A duplicate FastAPI path
    shadows SILENTLY, first registration winning, so the fixture's door became
    unreachable the day tracks E and F met.

    What that looks like from outside is the reason this is its own test: the
    switch treats an unreachable session as "this build has no ghost" and
    renders nothing at all, so every rendered check below errors on a missing
    element and reads exactly like a broken component. Measured: 28 of them.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(REPO / "tools"))
    from ghost_fixture import attach_ghost_routes, ghost_session

    app = FastAPI()

    @app.get("/api/ghost")           # the real mount, with no session
    def _real():
        raise RuntimeError("the real route answered")

    state = {"session": ghost_session(enabled=True), "writes": []}
    attach_ghost_routes(app, state)
    with TestClient(app) as client:
        answer = client.get("/api/ghost")
    assert answer.status_code == 200, answer.text
    assert answer.json()["enabled"] is True


# --------------------------------------------------------------------------
# The rendered card
# --------------------------------------------------------------------------

sys.path.insert(0, str(REPO / "tools"))

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ghost_fixture import (NO_FRAME_MAP_TEXT, ghost_session,  # noqa: E402
                           serve_ui_with_ghost)
from ui_fixture import FIXTURE_SEGMENT  # noqa: E402
from uilab.driver import get_driver  # noqa: E402


STATES = {
    "off": ghost_session(enabled=False),
    "on": ghost_session(enabled=True, opacity=0.6, nudge_frames=3),
    "disabled": ghost_session(available=False, reason="recording_has_no_frame_map",
                              reason_text=NO_FRAME_MAP_TEXT),
}

MEASURE = """
(() => {
  const rows = Array.from(document.querySelectorAll('.ghost-row'));
  const box = (el) => { if (!el) return null;
    const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, top: r.top, bottom: r.bottom,
            width: r.width, height: r.height}; };
  const seen = (el) => Boolean(el) && el.getClientRects().length > 0
    && getComputedStyle(el).visibility !== 'hidden';
  return rows.map((row) => {
    const card = row.closest('.log-card');
    const sw = row.querySelector('.ghost-switch');
    const why = row.querySelector('.ghost-why');
    const slider = row.querySelector('.ghost-opacity');
    const track = row.querySelector('.ghost-switch-track');
    const knob = row.querySelector('.ghost-switch-knob');
    return {
      onActiveCard: card.classList.contains('log-card-active'),
      cardClasses: card.className,
      label: row.querySelector('.ghost-label').textContent.trim(),
      checked: sw.getAttribute('aria-checked'),
      role: sw.getAttribute('role'),
      disabled: sw.disabled,
      describedBy: sw.getAttribute('aria-describedby'),
      whyId: why ? why.id : null,
      whyText: why ? why.textContent.trim() : null,
      whySeen: seen(why),
      steps: row.querySelectorAll('.ghost-step').length,
      resets: row.querySelectorAll('.ghost-reset').length,
      sliders: row.querySelectorAll('.ghost-opacity').length,
      sliderValue: slider ? slider.value : null,
      timing: (row.querySelector('.ghost-nudge-value') || {}).textContent,
      readout: (row.querySelector('.ghost-field-value') || {}).textContent,
      trackPaint: getComputedStyle(track).backgroundColor,
      knobShift: getComputedStyle(knob).transform,
      tuneBoxes: Array.from(row.querySelectorAll('.ghost-tune > *'))
        .map((el) => box(el)),
      row: box(row), card: box(card),
      rowScroll: row.scrollWidth, rowClient: row.clientWidth,
      cardScroll: card.scrollWidth, cardClient: card.clientWidth,
      head: box(card.querySelector('.log-card-head')),
    };
  });
})()
"""


@pytest.fixture(scope="module")
def rendered():
    """Every state at every width, from one server and one browser.

    A reload per state rather than a server per state: the page reads the
    session on mount, so changing the fixture's payload and reloading is the
    whole of it.
    """
    out = {}
    with serve_ui_with_ghost(STATES["off"], arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            for name, session in STATES.items():
                state["session"] = session
                page.goto(base)
                page.wait_for(".ghost-row")
                for width in WIDTHS:
                    page.set_viewport(width, 1200)
                    page.wait_ms(320)
                    out[(name, width)] = page.evaluate(MEASURE)
    return out


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("width", WIDTHS)
def test_exactly_one_card_carries_the_switch(rendered, state, width):
    """The practice log draws up to nine cards. Counting them is the check --
    a second row would mean the active-card rule stopped holding, and it
    looks entirely correct in a screenshot of one card."""
    rows = rendered[(state, width)]
    assert len(rows) == 1, f"{len(rows)} ghost rows at {width}px in {state}"
    assert rows[0]["onActiveCard"], rows[0]["cardClasses"]
    assert rows[0]["label"] == "Ghost Overlay"
    assert rows[0]["role"] == "switch"


@pytest.mark.parametrize("width", WIDTHS)
def test_a_target_with_no_ghost_refuses_and_says_why_on_screen(rendered, width):
    """His words, and the acceptance rule in one: the switch is not
    selectable, and the reason is VISIBLE rather than behind a hover. Checked
    with the longest sentence the server can produce."""
    row = rendered[("disabled", width)][0]
    assert row["disabled"] is True
    assert row["checked"] == "false"
    assert row["whyText"] == NO_FRAME_MAP_TEXT
    assert row["whySeen"], "the reason is in the DOM but not on the screen"
    assert row["describedBy"] == row["whyId"], (
        "the switch does not point at its own reason")
    # Nothing to tune when there is nothing to show.
    assert (row["sliders"], row["steps"], row["resets"]) == (0, 0, 0)


@pytest.mark.parametrize("width", WIDTHS)
def test_off_offers_the_switch_and_nothing_else(rendered, width):
    row = rendered[("off", width)][0]
    assert row["disabled"] is False
    assert row["checked"] == "false"
    assert row["whyText"] is None
    assert (row["sliders"], row["steps"]) == (0, 0), (
        "the tuning controls are on screen with the overlay off")


@pytest.mark.parametrize("width", WIDTHS)
def test_on_offers_visibility_and_timing(rendered, width):
    row = rendered[("on", width)][0]
    assert row["checked"] == "true"
    assert row["sliders"] == 1
    assert row["steps"] == 2, "earlier and later"
    assert row["resets"] == 1, "a non-zero offset offers a way back"
    assert row["sliderValue"] == "0.6"
    assert "3 frames ahead" in row["timing"]
    assert "60%" in row["readout"]


@pytest.mark.parametrize("width", WIDTHS)
def test_the_tuning_line_stays_one_aligned_line_down_to_the_floor(rendered, width):
    """Measured rather than assumed, and the reason this carries no width
    rule: at the 850px floor the card's pane gives this row 695px and the
    controls need 461px. The assertion is that they share ONE baseline -- a
    wrap would put the timing group under the slider and leave the two step
    buttons a card apart."""
    boxes = rendered[("on", width)][0]["tuneBoxes"]
    assert len(boxes) == 2, f"expected the slider and the timing group: {boxes}"
    # They are centre-aligned, not top-aligned, so "one line" is an OVERLAP
    # in y -- a wrap separates them entirely.
    assert max(b["top"] for b in boxes) < min(b["bottom"] for b in boxes), (
        f"the tuning controls wrapped at {width}px: {boxes}")
    right = max(b["right"] for b in boxes)
    assert right <= rendered[("on", width)][0]["row"]["right"] + 0.5, (
        f"the tuning controls reach past the row at {width}px: {right}")


@pytest.mark.parametrize("width", WIDTHS)
def test_the_knob_actually_moves_when_it_is_on(rendered, width):
    """A style built from a custom property has no DOM signature when it
    fails -- the class is on the element either way. Read the PAINT."""
    on = rendered[("on", width)][0]
    off = rendered[("off", width)][0]
    assert on["knobShift"] != off["knobShift"], (
        f"the knob paints identically on and off: {on['knobShift']}")
    assert on["trackPaint"] != off["trackPaint"], (
        f"the track paints identically on and off: {on['trackPaint']}")
    for paint in (on["trackPaint"], off["trackPaint"]):
        assert paint != "rgba(0, 0, 0, 0)", (
            "the track's background computed to transparent -- a colour-mix "
            "that is invalid at computed-value time reverts silently")


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("width", WIDTHS)
def test_the_row_fits_the_card_it_sits_in(rendered, state, width):
    row = rendered[(state, width)][0]
    assert row["rowScroll"] <= row["rowClient"] + 1, (
        f"the ghost row overflows itself at {width}px in {state}: {row}")
    assert row["cardScroll"] <= row["cardClient"] + 1, (
        f"the card overflows with the ghost row in it at {width}px: {row}")
    assert row["row"]["left"] >= row["card"]["left"] - 0.5
    assert row["row"]["right"] <= row["card"]["right"] + 0.5
    assert row["row"]["top"] >= row["head"]["bottom"] - 0.5, (
        "the ghost row is not below the card head")


def test_the_geometry_guard_can_fail(rendered):
    """Calibration: the overflow assertion is one that a broken probe would
    pass vacuously, so prove the comparison still discriminates."""
    row = rendered[("disabled", min(WIDTHS))][0]
    assert row["rowClient"] > 0, "the probe measured a zero-width row"
    assert row["rowScroll"] + 40 > row["rowClient"] + 1


@pytest.mark.parametrize("width", WIDTHS)
def test_the_wording_a_player_reads(rendered, width):
    """Read back what is actually painted, not what the source says: the
    label is his own phrase and the timing readout is a sentence."""
    on = rendered[("on", width)][0]
    assert on["label"] == "Ghost Overlay"
    assert on["timing"].strip() == "3 frames ahead"


# --------------------------------------------------------------------------
# What the page actually SENDS
# --------------------------------------------------------------------------

DRAG = """
(() => {
  const el = document.querySelector('.ghost-opacity');
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype, 'value').set;
  const steps = [0.2, 0.3, 0.4, 0.5, 0.65, 0.8, 0.95];
  for (const value of steps) {
    setter.call(el, String(value));
    el.dispatchEvent(new Event('input', {bubbles: true}));
  }
  return steps.length;
})()
"""

CLICK_EARLIER = """
(() => {
  const buttons = document.querySelectorAll('.ghost-step');
  for (let i = 0; i < 5; i += 1) buttons[0].click();
  return 5;
})()
"""


@pytest.fixture(scope="module")
def driven():
    """One page, three gestures, and the server's own record of what arrived.

    The claim under test is the write policy: a flip writes at once, and a
    gesture that fires many events writes ONCE, carrying where it ended up.
    Nothing here can be proved from the source -- a trailing write and a
    per-event write are the same call in the same place.
    """
    out = {}
    with serve_ui_with_ghost(ghost_session(enabled=False, opacity=0.6),
                             arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            page.goto(base)
            page.wait_for(".ghost-row")
            page.evaluate("(() => document.querySelector('.ghost-switch').click())()")
            page.wait_ms(400)
            out["after_flip"] = {"writes": list(state["writes"]),
                                 "session": dict(state["session"])}
            out["flip_checked"] = page.evaluate(
                "document.querySelector('.ghost-switch').getAttribute('aria-checked')")
            page.wait_for(".ghost-opacity")
            state["writes"].clear()
            out["drag_events"] = page.evaluate(DRAG)
            page.wait_ms(500)
            out["after_drag"] = {"writes": list(state["writes"]),
                                 "opacity": state["session"]["opacity"]}
            state["writes"].clear()
            out["clicks"] = page.evaluate(CLICK_EARLIER)
            page.wait_ms(500)
            out["after_nudge"] = {"writes": list(state["writes"]),
                                  "nudge": state["session"]["nudge_frames"]}
            page.wait_ms(200)
            out["nudge_readout"] = page.evaluate(
                "document.querySelector('.ghost-nudge-value').textContent.trim()")
    return out


READ_OPACITY = """
(() => {
  const el = document.querySelector('.ghost-opacity');
  if (!el) return null;
  return {disabled: el.disabled,
          readout: el.parentElement.querySelector('.ghost-field-value')
                     .textContent.trim()};
})()
"""


@pytest.fixture(scope="module")
def unstated_opacity():
    """A session that never says how solid to draw the ghost, then one that
    does -- and every body the page PUT along the way.

    This is the shape behind a live finding: his stored ghost visibility is
    1.0 while the server's default has always been 0.5. The control used to
    turn a MISSING opacity into `OPACITY_MAX`, so it showed "100%" for a
    figure nobody had chosen, and a number on screen that looks chosen is one
    small drag from being written back for good.
    """
    payload = ghost_session(enabled=True, opacity=0.6)
    del payload["opacity"]
    out = {}
    with serve_ui_with_ghost(payload, arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            page.goto(base)
            page.wait_for(".ghost-opacity")
            out["unknown"] = page.evaluate(READ_OPACITY)
            # A real gesture that DOES write, while the visibility is still
            # unknown: flipping the switch must carry the switch and nothing
            # else. This is the write that could smuggle a default out.
            page.evaluate("(() => document.querySelector('.ghost-switch').click())()")
            page.wait_ms(400)
            out["writes_while_unknown"] = list(state["writes"])

            # The server catches up. Now the control has a value to show, it
            # is his to move, and his move is what reaches the server. The
            # overlay is switched back on because the flip above turned it
            # off, and the tuning row only exists while it is on.
            state["session"] = {**state["session"], "opacity": 0.5,
                                "enabled": True}
            state["writes"].clear()
            page.goto(base)
            page.wait_for(".ghost-opacity")
            out["known"] = page.evaluate(READ_OPACITY)
            page.evaluate(DRAG)
            page.wait_ms(500)
            out["writes_after_drag"] = list(state["writes"])
    return out


def test_a_visibility_nobody_stated_is_shown_as_unknown_and_cannot_be_moved(
        unstated_opacity):
    """Rendered, not reasoned: the slider is dead and the readout is a dash,
    so nothing on screen claims a value the server never sent."""
    unknown = unstated_opacity["unknown"]
    assert unknown is not None, "the tuning row never rendered"
    assert unknown["disabled"] is True, unknown
    assert unknown["readout"] == "—", unknown


def test_no_write_carries_a_visibility_until_he_has_moved_the_slider(
        unstated_opacity):
    """THE CONTRACT. The only opacity that may reach the server is one he
    chose or one it sent us -- never a client-side default, and never on the
    back of an unrelated write."""
    flips = unstated_opacity["writes_while_unknown"]
    assert flips == [{"enabled": False}], flips
    assert all("opacity" not in body for body in flips), flips

    known = unstated_opacity["known"]
    assert known["disabled"] is False, known
    assert known["readout"] == "50%", known
    dragged = unstated_opacity["writes_after_drag"]
    assert dragged == [{"opacity": 0.95}], dragged


def test_flipping_the_switch_writes_immediately_and_stays_flipped(driven):
    assert driven["after_flip"]["writes"] == [{"enabled": True}]
    assert driven["after_flip"]["session"]["enabled"] is True
    assert driven["flip_checked"] == "true", (
        "the switch snapped back -- the server's answer or its own broadcast "
        "overwrote the gesture")


def test_a_drag_is_one_write_carrying_where_it_stopped(driven):
    """Seven events, one PUT. A write per event would put a burst on the wire
    for one gesture, and no source scan can tell the two apart."""
    assert driven["drag_events"] == 7
    writes = driven["after_drag"]["writes"]
    assert len(writes) == 1, f"{len(writes)} writes for one drag: {writes}"
    assert writes[0] == {"opacity": 0.95}
    assert driven["after_drag"]["opacity"] == 0.95


def test_repeated_nudges_collapse_into_one_write(driven):
    """Five presses, one PUT, and it carries the SUM -- not the first press,
    which is what a plain debounce on the handler would have sent."""
    writes = driven["after_nudge"]["writes"]
    assert len(writes) == 1, f"{len(writes)} writes for five presses: {writes}"
    assert writes[0] == {"nudge_frames": 5}
    assert driven["after_nudge"]["nudge"] == 5
    assert driven["nudge_readout"] == "5 frames ahead"


def test_the_fixture_reaches_the_state_it_claims():
    """The fixture is an instrument; read its own output once. An empty
    session or a target the card does not recognise would render the
    still-looking state and every assertion above would be about that."""
    assert STATES["on"]["target"]["entity_key"] == "2-4"
    assert json.dumps(STATES["disabled"]["source"]["reason_text"]) \
        == json.dumps(NO_FRAME_MAP_TEXT)
