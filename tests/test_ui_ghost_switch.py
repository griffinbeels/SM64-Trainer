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


def ghost_classes_in(source: str) -> set:
    """Every `ghost-` class this source could put on an element.

    BOTH SPELLINGS, and the second one is why this is a function. A class built
    from a template (`class=${`ghost-switch${small ? " ghost-switch-sm" : ""}`}`)
    carries a `$`, and the first version of this scan stopped at one -- so the
    conditional half of a class, which is exactly where a rename hides, was
    never looked at, and neither was `.ghost-row` itself. Tokens inside an
    interpolation are read too: a class name is a class name wherever in the
    expression it is written.
    """
    found = set()
    for match in re.finditer(r'class=(?:"([^"]*)"|\$\{`([^`]*)`\})', source):
        text = match.group(1) if match.group(1) is not None else match.group(2)
        found.update(name for name in re.findall(r"[\w-]+", text)
                     if name.startswith("ghost-"))
    return found


def test_every_class_the_switch_renders_is_styled():
    """A class rename empties the stylesheet silently: the markup stays
    correct, `node --check` is happy, and nothing paints."""
    source = GHOSTSWITCH_JS.read_text(encoding="utf-8")
    css = INDEX_HTML.read_text(encoding="utf-8")
    ghost_classes = ghost_classes_in(source)
    assert ghost_classes, "no ghost classes found -- did the scan break?"
    # The scan reaches both spellings, which is the half that was silently
    # missing: the row's own class and the small switch's are only expressible
    # inside a template.
    assert {"ghost-row", "ghost-switch-sm", "ghost-hue"} <= ghost_classes, (
        sorted(ghost_classes))
    missing = [name for name in sorted(ghost_classes) if f".{name}" not in css]
    assert not missing, f"unstyled classes: {missing}"


def test_the_class_scan_can_still_fail():
    """The calibration for the guard above: a scan that matched nothing would
    pass it forever while looking healthy."""
    assert ghost_classes_in('class="ghost-made-up"') == {"ghost-made-up"}
    assert ghost_classes_in(
        'class=${`ghost-a ${x ? "ghost-b" : ""}`}') == {"ghost-a", "ghost-b"}
    assert ghost_classes_in('class="log-card"') == set()


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
    # Template is an existing player-facing concept (Save as template), and
    # the ghost recording picker now explicitly follows that input template.
    for banned in ("frame map", "nudge", "opacity", "PJ64"):
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
    # The colour treatment switched OFF: the widest state is the one above
    # (the wheel is out), this is the one where a control has to DISAPPEAR
    # cleanly rather than sit there greyed.
    "plain": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           tint={"enabled": False, "hue_degrees": 120}),
    # A ghost whose recording HAS had Mario found in it: the one state where
    # the view control can actually be used.
    "boxed": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           mario_boxes=True),
    "mario": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           mario_boxes=True, view="mario"),
    # THE SESSION ASKS FOR MARIO AND THIS GHOST HAS NO BOXES. The setting is
    # app-wide and he walks between stars, so this is the ordinary state on
    # every older recording once he has ever picked the Mario view.
    "asked": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           view="mario"),
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
    const hue = row.querySelector('.ghost-hue');
    const pick = row.querySelector('.ghost-view-select');
    const viewWhy = row.querySelector('.ghost-view-why');
    const colour = row.querySelector('.ghost-colour');
    const colourSwitch = colour && colour.querySelector('.ghost-switch');
    return {
      views: row.querySelectorAll('.ghost-view').length,
      viewValue: pick ? pick.value : null,
      viewDisabled: pick ? pick.disabled : null,
      viewOptions: pick
        ? Array.from(pick.options).map((o) => o.textContent.trim()) : null,
      viewTitle: pick ? pick.title : null,
      // The popup is drawn by the browser off the SELECT's own computed
      // background, so a transparent one gets a white list: read the paint.
      viewPaint: pick ? getComputedStyle(pick).backgroundColor : null,
      viewWhy: viewWhy ? viewWhy.textContent.trim() : null,
      viewWhySeen: seen(viewWhy),
      colours: row.querySelectorAll('.ghost-colour').length,
      hues: row.querySelectorAll('.ghost-hue').length,
      hueValue: hue ? hue.value : null,
      // THE PAINT, not the class: the wheel is a gradient, and a gradient that
      // failed to parse leaves the element a plain box with the class still on
      // it. `none` is the answer for a background-image that is not there.
      hueTrack: hue ? getComputedStyle(hue).backgroundImage : null,
      hueBox: box(hue),
      colourChecked: colourSwitch ? colourSwitch.getAttribute('aria-checked') : null,
      colourNamed: colourSwitch ? colourSwitch.getAttribute('aria-label') : null,
      colourLabel: colour
        ? colour.querySelector('.ghost-field-name').textContent.trim() : null,
      colourReadout: colour && colour.querySelector('.ghost-field-value')
        ? colour.querySelector('.ghost-field-value').textContent.trim() : null,
      colourKnobShift: colourSwitch
        ? getComputedStyle(colourSwitch.querySelector('.ghost-switch-knob')).transform
        : null,
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
      // The control GROUPS, not everything in the row: the reason sentence
      // is a row of its own and is measured by its own checks.
      tuneBoxes: Array.from(row.querySelectorAll('.ghost-tune > .ghost-field'))
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
    assert (row["sliders"], row["steps"], row["resets"], row["colours"],
            row["views"]) == (0, 0, 0, 0, 0)


@pytest.mark.parametrize("width", WIDTHS)
def test_off_offers_the_switch_and_nothing_else(rendered, width):
    row = rendered[("off", width)][0]
    assert row["disabled"] is False
    assert row["checked"] == "false"
    assert row["whyText"] is None
    assert (row["sliders"], row["steps"], row["colours"], row["views"]) \
        == (0, 0, 0, 0), (
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


#: The tallest a tuning group is on ONE line: the range input's own 34px box
#: (the timing group of 24px chips is the short one). A group that wrapped
#: inside itself is 48px or more, so this discriminates -- measured, not
#: chosen, and it is what makes "each group stays whole" a real claim.
ONE_LINE_PX = 40


@pytest.mark.parametrize("width", WIDTHS)
def test_the_tuning_line_holds_its_three_groups_inside_the_row(rendered, width):
    """Three groups now: how solid, what colour, and where it sits against
    your run.

    MEASURED, and the numbers decide rather than anybody's intention. The
    groups are 203 + 226 + 299px with 16px gaps -- 760px -- and the card's pane
    gives the row 1187/887/717/667px at 1500/1200/900/850. So all three share
    one line down to 1200 and the timing group takes a second line at 900 and
    850, which is a deliberate wrap and not an overflow.

    What is pinned is the part that must hold at every width: each group stays
    WHOLE (a wrap inside one would put the two step buttons a card apart, or
    the wheel under its own switch) and nothing reaches past the row.
    """
    row = rendered[("on", width)][0]
    boxes = row["tuneBoxes"]
    assert len(boxes) == 4, f"slider, colour, view and timing: {boxes}"
    for name, box in zip(("visibility", "colour", "view", "timing"), boxes,
                         strict=True):
        assert box["height"] <= ONE_LINE_PX, (
            f"the {name} group wrapped inside itself at {width}px: {box}")
    right = max(b["right"] for b in boxes)
    assert right <= row["row"]["right"] + 0.5, (
        f"the tuning controls reach past the row at {width}px: {right}")
    if width >= 1200:
        # Wide enough for one line, so it IS one line: they overlap in y.
        assert max(b["top"] for b in boxes) < min(b["bottom"] for b in boxes), (
            f"the tuning controls wrapped at {width}px with room: {boxes}")


@pytest.mark.parametrize("width", WIDTHS)
def test_the_ghost_is_coloured_and_the_wheel_is_the_control(rendered, width):
    """ROUND 3, ITEM 2. His words: *"It feels really hard to understand which
    one is the ACTUAL LIVE GAMEPLAY and which one is the GHOST gameplay...
    Maybe we can have a color picker... The user can disable the color shift if
    they want."*

    The label says what it does rather than naming the technique, the switch is
    the same control the row's own switch is, and the slider wears the colour
    wheel -- read off the PAINT, because a gradient that failed to parse leaves
    a plain box with the class still on it.
    """
    row = rendered[("on", width)][0]
    assert row["colours"] == 1 and row["hues"] == 1
    assert row["colourLabel"] == "Colour"
    assert row["colourChecked"] == "true"
    assert row["hueValue"] == "120"
    assert row["colourReadout"] == "+120°"
    assert "gradient" in (row["hueTrack"] or ""), row["hueTrack"]
    assert row["hueBox"]["width"] > 40, row["hueBox"]
    # The name a screen reader gets says what the control does, in the same
    # words -- there is no visible label beside this smaller switch.
    assert "colour" in (row["colourNamed"] or "").lower(), row["colourNamed"]


@pytest.mark.parametrize("width", WIDTHS)
def test_switching_the_colour_off_takes_the_wheel_away(rendered, width):
    """Omitted, not greyed: with the treatment off there is nothing for the
    wheel to act on, and the acceptance rule is to omit an action that does not
    apply. The switch itself stays, because that is how it comes back."""
    row = rendered[("plain", width)][0]
    assert row["colours"] == 1, "the colour control vanished with its slider"
    assert row["colourChecked"] == "false"
    assert row["hues"] == 0, "a wheel with nothing to act on is on screen"
    assert row["colourReadout"] is None
    assert row["colourKnobShift"] != rendered[("on", width)][0]["colourKnobShift"], (
        "the smaller switch paints identically on and off")


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

DRAG_HUE = """
(() => {
  const el = document.querySelector('.ghost-hue');
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype, 'value').set;
  const steps = [30, 90, 150, 210, 270, 300, 330];
  for (const value of steps) {
    setter.call(el, String(value));
    el.dispatchEvent(new Event('input', {bubbles: true}));
  }
  return steps.length;
})()
"""


def settled(page, state, expected, within_ms=4000):
    """The writes the page has actually sent, once it has stopped sending.

    Waits for `expected` of them and then for the window to go quiet, so a
    check that one gesture is ONE write still fails when it is two. A bounded
    deadline, and the assertion afterwards is what reports an absence -- this
    only stops the rig calling a slow write a missing one.
    """
    waited = 0
    while waited < within_ms:
        if len(state["writes"]) >= expected:
            break
        page.wait_ms(50)
        waited += 50
    # Past the trailing edge (`ghostswitch.js::WRITE_SETTLE_MS` is 180 ms), so
    # a second write would have shown up by now.
    page.wait_ms(400)
    return list(state["writes"])


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
            # The row mounts while GET /api/ghost is still pending. A DOM
            # .click() on its waiting/disabled switch is ignored by the browser.
            page.wait_for(".ghost-row-head > .ghost-switch:enabled")
            page.evaluate("(() => document.querySelector('.ghost-switch').click())()")
            out["after_flip"] = {"writes": settled(page, state, 1),
                                 "session": dict(state["session"])}
            out["flip_checked"] = page.evaluate(
                "document.querySelector('.ghost-switch').getAttribute('aria-checked')")
            page.wait_for(".ghost-opacity")
            state["writes"].clear()
            out["drag_events"] = page.evaluate(DRAG)
            out["after_drag"] = {"writes": settled(page, state, 1),
                                 "opacity": state["session"]["opacity"]}
            state["writes"].clear()
            out["clicks"] = page.evaluate(CLICK_EARLIER)
            out["after_nudge"] = {"writes": settled(page, state, 1),
                                  "nudge": state["session"]["nudge_frames"]}
            page.wait_ms(200)
            out["nudge_readout"] = page.evaluate(
                "document.querySelector('.ghost-nudge-value').textContent.trim()")
            # The colour: a drag through the wheel is ONE write carrying where
            # it stopped, and the switch beside it writes on the press.
            state["writes"].clear()
            out["hue_events"] = page.evaluate(DRAG_HUE)
            out["after_hue"] = {"writes": settled(page, state, 1),
                                "tint": dict(state["session"]["tint"])}
            state["writes"].clear()
            page.evaluate(
                "(() => document.querySelector('.ghost-colour .ghost-switch')"
                ".click())()")
            out["after_colour_off"] = {"writes": settled(page, state, 1),
                                       "tint": dict(state["session"]["tint"])}
            out["wheel_after_off"] = page.evaluate(
                "document.querySelectorAll('.ghost-hue').length")
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
            out["writes_while_unknown"] = settled(page, state, 1)

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
            out["writes_after_drag"] = settled(page, state, 1)
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


def test_dragging_the_colour_wheel_is_one_write_carrying_where_it_stopped(driven):
    """The whole reason the colour rides the clock feed rather than the page's
    URL: he tunes this by dragging, and seven drag events must be one PUT --
    and the value it carries is the angle he let go on."""
    assert driven["hue_events"] == 7
    writes = driven["after_hue"]["writes"]
    assert len(writes) == 1, f"{len(writes)} writes for one drag: {writes}"
    assert writes[0] == {"tint": {"hue_degrees": 330}}
    assert driven["after_hue"]["tint"]["hue_degrees"] == 330


def test_switching_the_colour_off_keeps_the_angle_he_chose(driven):
    """The press writes at once (it is one decision), it carries ONLY the
    switch, and the angle survives -- so switching the treatment back on
    returns to his colour rather than to the shipped one."""
    writes = driven["after_colour_off"]["writes"]
    assert writes == [{"tint": {"enabled": False}}], writes
    assert driven["after_colour_off"]["tint"] == {"enabled": False,
                                                  "hue_degrees": 330}
    assert driven["wheel_after_off"] == 0, (
        "the wheel is still on screen with nothing to act on")


def test_the_fixture_reaches_the_state_it_claims():
    """The fixture is an instrument; read its own output once. An empty
    session or a target the card does not recognise would render the
    still-looking state and every assertion above would be about that."""
    assert STATES["on"]["target"]["entity_key"] == "2-4"
    assert json.dumps(STATES["disabled"]["source"]["reason_text"]) \
        == json.dumps(NO_FRAME_MAP_TEXT)

@pytest.mark.parametrize("width", WIDTHS)
def test_the_ghost_can_be_asked_to_show_only_mario(rendered, width):
    """His words: *"It's still extremely hard to understand the ghost, because
    all the camera movement / scenery overlaps... we can try a v0 where it's
    just a super tight rectangle around mario's body."*

    Two options in a native select -- this project's rule for a SHORT dropdown
    -- named for what each one DRAWS. Read back off the rendered control, not
    off the source that builds it.
    """
    row = rendered[("boxed", width)][0]
    assert row["views"] == 1
    assert row["viewOptions"] == ["Whole picture", "Only Mario"]
    assert row["viewDisabled"] is False
    assert row["viewValue"] == "whole"
    assert row["viewWhy"] is None, "a usable control is explaining itself"
    # An opaque background of its own, or Chromium draws its popup white.
    assert row["viewPaint"] != "rgba(0, 0, 0, 0)", row["viewPaint"]
    assert rendered[("mario", width)][0]["viewValue"] == "mario"


@pytest.mark.parametrize("width", WIDTHS)
def test_a_ghost_with_no_mario_boxes_says_why_it_cannot_show_him(
        rendered, width):
    """Every recording made before this round. His acceptance rule is that a
    control which cannot run says why, and it matters more than usual here:
    only-Mario is the point of the round, so a control that silently vanished
    would read as the feature not shipping."""
    row = rendered[("on", width)][0]
    assert row["views"] == 1, "the control was dropped instead of explained"
    assert row["viewDisabled"] is True
    assert row["viewWhy"], "the reason is not on screen"
    assert row["viewWhySeen"], "the reason is in the DOM but not visible"
    assert "Mario" in row["viewWhy"]
    # The same sentence is the hover, so a narrow row still answers.
    assert row["viewTitle"] == row["viewWhy"]


@pytest.mark.parametrize("width", WIDTHS)
def test_a_control_he_cannot_use_reads_what_is_actually_on_screen(
        rendered, width):
    """The session is set to "only Mario" and this ghost has no boxes, so the
    overlay is drawing the WHOLE PICTURE. A disabled control reading "Only
    Mario" beside it is a control contradicting the screen -- and the reason
    sentence right under it says the whole picture is all that can be shown."""
    row = rendered[("asked", width)][0]
    assert row["viewDisabled"] is True
    assert row["viewValue"] == "whole", (
        "the control names a view the overlay is not drawing")
    assert row["viewWhy"], "and it still says why"
