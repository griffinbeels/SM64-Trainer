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
import time
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
    # Two pieces now, each mounted once: the head's toggle and the row under
    # the head. Both speak for the entity being practised, so both carry the
    # card's own active flag and key.
    for component in ("GhostSwitch", "GhostToggle"):
        mounts = re.findall(r"<\$\{" + component + r"\}[^>]*>", source)
        assert len(mounts) == 1, f"expected one {component} mount, found {mounts}"
        assert "active=${active}" in mounts[0], (
            "the mount must pass the card's own active flag -- the overlay belongs "
            f"to the entity being practised, not to every card: {mounts[0]}")
        assert "entityKey=${ek}" in mounts[0]
        # ...and it is not gated on a kind, which would ship it for stars alone.
        assert not re.search(r"isSegment\(sec\)[^\n]*" + component, source), (
            f"{component} is gated on the entity kind -- stars and segments both "
            "get it (rule 11)")


def test_the_switch_never_reaches_a_card_that_is_not_active():
    """The prop is load-bearing: both the fetch and the render read it. With
    nine cards on the page, a switch that ignored it would be nine reads of
    one app-wide session and nine controls claiming to speak for it."""
    source = strip_comments(GHOSTSWITCH_JS.read_text(encoding="utf-8"))
    assert "if (!active) return undefined;" in source, (
        "the session read is not gated on `active` -- every card would "
        "request the session")
    # The head toggle and the row each refuse to render off the active card.
    assert source.count("if (!active) return null;") >= 2, (
        "GhostSwitch or GhostToggle renders without checking `active`")


def test_only_the_ghost_switch_names_the_session_endpoint():
    """One door, the way `ui/target.js` owns /api/target. A second caller is
    how two surfaces come to disagree about one app-wide session."""
    named = [path.name for path in UI.rglob("*.js")
             if names_ghost_session(strip_comments(path.read_text(encoding="utf-8")))]
    assert named == ["ghostswitch.js"], named


def names_ghost_session(source):
    """The session door, allowing its query; collection routes are separate."""
    return bool(re.search(r'''(["'])/api/ghost(?:\?[^"']*)?\1''', source))


def test_the_session_endpoint_scan_distinguishes_collection_routes():
    assert names_ghost_session('const url = "/api/ghost";')
    assert names_ghost_session("const url = '/api/ghost?target=star';")
    assert not names_ghost_session('const url = "/api/ghost/library";')
    assert not names_ghost_session('const url = "/api/ghost/jobs/3";')


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
    assert {"ghost-row", "ghost-row-lineup", "ghost-toggle", "ghost-switch-sm",
            "ghost-hue"} <= ghost_classes, sorted(ghost_classes)
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

from ghost_fixture import (NO_POSES_TEXT, ghost_session,  # noqa: E402
                           serve_ui_with_ghost)
from ui_fixture import FIXTURE_SEGMENT  # noqa: E402
from sm64_events.ghost.native_session import budget_reason  # noqa: E402
from uilab.driver import get_driver  # noqa: E402


STATES = {
    "off": ghost_session(enabled=False),
    "on": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                        tint={"enabled": True, "hue_degrees": 120}),
    "disabled": ghost_session(available=False, reason="older_pose_format",
                              reason_text=NO_POSES_TEXT),
    # The colour treatment switched OFF: the widest state is the one above
    # (the wheel is out), this is the one where a control has to DISAPPEAR
    # cleanly rather than sit there greyed.
    "plain": ghost_session(enabled=True, opacity=0.6, nudge_frames=3,
                           tint={"enabled": False, "hue_degrees": 120}),
    # Several ghosts the renderer cannot hold together: the card says so in
    # the server's own sentence (the native budget refusal, not a bad file).
    "no_room": {**ghost_session(enabled=True, opacity=0.6, nudge_frames=3),
                "window": {"running": False, "visible": False, "backend": "native",
                           "reason": budget_reason(2)}},
}


@pytest.mark.parametrize("kind", ("star", "segment"))
@pytest.mark.parametrize("width", WIDTHS)
def test_failed_read_stays_visible_and_retry_restores_real_card(kind, width):
    """A temporary API failure must not erase a single active card's controls."""
    target = {"kind": kind, "entity_key": "5-4" if kind == "star" else str(FIXTURE_SEGMENT),
              "strat": "Standard"}
    fixture = {"stage": (5, 4), "target": (5, 4)} if kind == "star" else {
        "arm_segment": FIXTURE_SEGMENT, "target_segment": FIXTURE_SEGMENT}
    artifacts = REPO / ".iteration" / "adaptive-ghosts" / "round52-ui-renders"
    artifacts.mkdir(parents=True, exist_ok=True)
    with serve_ui_with_ghost(ghost_session(enabled=False, target=target),
                             **fixture) as (base, state):
        state["read_error"] = True
        with get_driver().launch() as page:
            page.set_viewport(width, 1200)
            page.goto(base)
            # An unreachable session is the Lineup's own note, with its Retry.
            page.wait_for(".ghost-row .lineup-note-action")
            rows = page.evaluate(MEASURE)
            assert len(rows) == 1 and rows[0]["onActiveCard"], rows
            assert rows[0]["toggles"] == 1, rows
            assert rows[0]["disabled"] and rows[0]["whySeen"], rows
            assert "couldn't load" in rows[0]["whyText"], rows
            assert rows[0]["rowScroll"] <= rows[0]["rowClient"] + 1, rows
            (artifacts / f"{kind}-{width}-failed.png").write_bytes(page.screenshot())
            initial_faults = page.problems()
            assert initial_faults and all("503" in fault for fault in initial_faults), initial_faults
            state["read_error"] = False
            page.click(".ghost-row .lineup-note-action")
            page.wait_for(f"{TOGGLE}:not([disabled])")
            page.click(TOGGLE)
            page.wait_for(f'{TOGGLE}[aria-pressed="true"]')
            assert state["writes"] == [{"enabled": True}], state["writes"]
            recovered = page.evaluate(MEASURE)
            assert len(recovered) == 1 and recovered[0]["onActiveCard"], recovered
            assert not recovered[0]["disabled"], recovered
            assert recovered[0]["rowScroll"] <= recovered[0]["rowClient"] + 1, recovered
            page.wait_ms(320)  # Capture the settled toggle, after its normal motion.
            (artifacts / f"{kind}-{width}-recovered.png").write_bytes(page.screenshot())
            assert not page.problems()


#: The overlay's on/off: the ghost toggle in the ACTIVE card's head. It is
#: disabled until the session has answered for this card, so `:enabled` is
#: also the moment the row under the head is the answered one.
TOGGLE = ".log-card.log-card-active .log-card-actions > .ghost-toggle"

#: The Ghost Library's own dialog. It opens ON TOP of the Ghost settings
#: sheet (its door lives there now), so a bare `.modal` names the sheet
#: behind it; this names the library's.
LIBRARY_MODAL = ".modal:has(> .modal-body > .ghost-library)"
LIBRARY_CLOSE = f"{LIBRARY_MODAL} > .modal-header > .modal-close"


def open_ghost_settings(page):
    """Practice log settings -> Ghost settings: the one sheet every ghost
    setting and the Ghost Library's door live in."""
    page.wait_for('button[aria-label="Practice log settings"]')
    page.click('button[aria-label="Practice log settings"]')
    page.wait_for("#practice-log-settings")
    page.evaluate("""Array.from(document.querySelectorAll('#practice-log-settings button'))
      .find((button) => button.textContent.trim() === 'Ghost settings').click()""")
    page.wait_for('.modal[aria-labelledby] .ghost-settings-heading')


def open_ghost_library(page):
    """The Ghost Library, through the door in the Ghost settings sheet. The
    door opens once the sheet's session has answered."""
    open_ghost_settings(page)
    page.wait_for(".ghost-library-button:enabled")
    page.click(".ghost-library-button")

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
    const toggle = card.querySelector(':scope .log-card-actions > .ghost-toggle');
    // The reason sentence: under the head for the older controls, or the
    // Lineup's note when the session could not be read at all.
    const why = row.querySelector('.ghost-row-head > .ghost-why') || row.querySelector('.lineup-note');
    const slider = row.querySelector('.ghost-opacity');
    const hue = row.querySelector('.ghost-hue');
    const colour = row.querySelector('.ghost-colour');
    const colourSwitch = colour && colour.querySelector('.ghost-switch');
    const track = colourSwitch && colourSwitch.querySelector('.ghost-switch-track');
    const toggleStyle = toggle && getComputedStyle(toggle);
    return {
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
      toggles: document.querySelectorAll('.ghost-toggle').length,
      label: toggle ? toggle.getAttribute('aria-label') : null,
      title: toggle ? toggle.getAttribute('title') : null,
      pressed: toggle ? toggle.getAttribute('aria-pressed') : null,
      tag: toggle ? toggle.tagName : null,
      disabled: toggle ? toggle.disabled : null,
      // THE PAINT of the toggle, not its class: gold while ghosts race.
      togglePaint: toggleStyle
        ? [toggleStyle.color, toggleStyle.borderTopColor, toggleStyle.backgroundColor].join(' | ') : null,
      toggleColour: toggleStyle ? toggleStyle.color : null,
      toggleBox: box(toggle),
      whyText: why ? why.textContent.trim() : null,
      whySeen: seen(why),
      steps: row.querySelectorAll('.ghost-step').length,
      resets: row.querySelectorAll('.ghost-reset').length,
      sliders: row.querySelectorAll('.ghost-opacity').length,
      sliderValue: slider ? slider.value : null,
      timing: (row.querySelector('.ghost-nudge-value') || {}).textContent,
      readout: (row.querySelector('.ghost-field-value') || {}).textContent,
      // The colour treatment's own switch: the one iOS-style switch left on
      // this row with a knob that has to move.
      trackPaint: track ? getComputedStyle(track).backgroundColor : null,
      // The control GROUPS, not everything in the row: the reason sentence
      // is a row of its own and is measured by its own checks.
      tuneBoxes: [slider && slider.closest('.ghost-field'), colour,
                  row.querySelector('.ghost-timing')].filter(Boolean).map(box),
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
                # Not `.ghost-row`: before the session answers, the row is the
                # Lineup waiting to be told what to draw. The enabled toggle
                # is the answered card.
                page.wait_for(f"{TOGGLE}:enabled")
                for width in WIDTHS:
                    page.set_viewport(width, 1200)
                    page.wait_ms(320)
                    out[(name, width)] = page.evaluate(MEASURE)
    return out


@pytest.mark.parametrize("state", sorted(STATES))
@pytest.mark.parametrize("width", WIDTHS)
def test_exactly_one_card_carries_the_toggle_and_the_row(rendered, state, width):
    """The practice log draws up to nine cards. Counting them is the check --
    a second row or toggle would mean the active-card rule stopped holding,
    and it looks entirely correct in a screenshot of one card."""
    rows = rendered[(state, width)]
    assert len(rows) == 1, f"{len(rows)} ghost rows at {width}px in {state}"
    assert rows[0]["onActiveCard"], rows[0]["cardClasses"]
    assert rows[0]["toggles"] == 1, f"{rows[0]['toggles']} ghost toggles at {width}px in {state}"
    assert rows[0]["label"] == "Ghost Overlay"
    assert rows[0]["tag"] == "BUTTON" and rows[0]["pressed"] in ("true", "false")


@pytest.mark.parametrize("width", WIDTHS)
def test_a_target_with_no_ghost_says_why_on_screen(rendered, width):
    """The reason is VISIBLE rather than behind a hover, checked with the
    longest sentence the server can produce. The toggle is a preference now,
    not a capability (round 3): it stays usable and keeps his choice, and the
    row says why nothing races."""
    row = rendered[("disabled", width)][0]
    assert row["disabled"] is False
    assert row["pressed"] == ("true" if STATES["disabled"]["enabled"] else "false")
    assert row["whyText"] == NO_POSES_TEXT
    assert row["whySeen"], "the reason is in the DOM but not on the screen"
    # Nothing to tune when there is nothing to show.
    assert (row["sliders"], row["steps"], row["resets"], row["colours"]) == (0, 0, 0, 0)


@pytest.mark.parametrize("width", WIDTHS)
def test_off_offers_the_toggle_and_nothing_else(rendered, width):
    row = rendered[("off", width)][0]
    assert row["disabled"] is False
    assert row["pressed"] == "false"
    assert row["whyText"] is None
    assert (row["sliders"], row["steps"], row["colours"]) \
        == (0, 0, 0), (
            "the tuning controls are on screen with the overlay off")


@pytest.mark.parametrize("width", WIDTHS)
def test_on_offers_visibility_and_timing(rendered, width):
    row = rendered[("on", width)][0]
    assert row["pressed"] == "true"
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

    The native annotations, sound and path share this row. Each group stays
    WHOLE (a wrap inside one would put the two step buttons a card apart, or
    the wheel under its own switch) and nothing reaches past the row.
    """
    row = rendered[("on", width)][0]
    boxes = row["tuneBoxes"]
    assert len(boxes) == 3, f"slider, colour and timing: {boxes}"
    for name, box in zip(("visibility", "colour", "timing"), boxes,
                         strict=True):
        assert box["height"] <= ONE_LINE_PX, (
            f"the {name} group wrapped inside itself at {width}px: {box}")
    right = max(b["right"] for b in boxes)
    assert right <= row["row"]["right"] + 0.5, (
        f"the tuning controls reach past the row at {width}px: {right}")


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
    fails -- the class is on the element either way. Read the PAINT.

    The switch measured is the colour treatment's: the overlay's own on/off is
    the head's ghost toggle now, which has no knob (checked below)."""
    on = rendered[("on", width)][0]
    plain = rendered[("plain", width)][0]
    assert (on["colourChecked"], plain["colourChecked"]) == ("true", "false")
    assert on["colourKnobShift"] != plain["colourKnobShift"], (
        f"the knob paints identically on and off: {on['colourKnobShift']}")
    assert on["trackPaint"] != plain["trackPaint"], (
        f"the track paints identically on and off: {on['trackPaint']}")
    for paint in (on["trackPaint"], plain["trackPaint"]):
        assert paint not in (None, "rgba(0, 0, 0, 0)"), (
            "the track's background computed to transparent -- a colour-mix "
            "that is invalid at computed-value time reverts silently")


@pytest.mark.parametrize("width", WIDTHS)
def test_the_toggle_paints_differently_when_ghosts_race(rendered, width):
    """The head toggle's on look is a colour-mix of the gold: read the PAINT,
    because a mix that is invalid at computed-value time reverts silently and
    leaves on and off looking the same."""
    on = rendered[("on", width)][0]
    off = rendered[("off", width)][0]
    assert on["togglePaint"] != off["togglePaint"], (
        f"the ghost toggle paints identically on and off: {on['togglePaint']}")
    for row in (on, off):
        assert row["toggleColour"] not in (None, "rgba(0, 0, 0, 0)"), row["togglePaint"]
        assert row["toggleBox"]["width"] > 0 and row["toggleBox"]["height"] > 0, row["toggleBox"]


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
def test_ghosts_that_do_not_fit_together_say_so_on_the_card(rendered, width):
    """His ghosts are fine; there are too many long ones at once. The card
    says what to do, on screen, and the toggle stays usable."""
    row = rendered[("no_room", width)][0]
    assert row["whyText"] == "These ghosts are too long to show together; untick one."
    assert row["whySeen"] and row["pressed"] == "true" and not row["disabled"]


@pytest.mark.parametrize("width", WIDTHS)
def test_the_wording_a_player_reads(rendered, width):
    """Read back what is actually painted, not what the source says: the
    toggle's name is his own phrase, its hover says what a click does, and
    the timing readout is a sentence."""
    on = rendered[("on", width)][0]
    off = rendered[("off", width)][0]
    assert on["label"] == off["label"] == "Ghost Overlay"
    assert on["title"].startswith("Ghosts on.") and off["title"].startswith("Ghosts off."), (
        on["title"], off["title"])
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
            # The toggle mounts while GET /api/ghost is still pending. A DOM
            # .click() on its waiting/disabled button is ignored by the browser.
            page.wait_for(f"{TOGGLE}:enabled")
            page.evaluate(f"(() => document.querySelector({TOGGLE!r}).click())()")
            out["after_flip"] = {"writes": settled(page, state, 1),
                                 "session": dict(state["session"])}
            out["flip_checked"] = page.evaluate(
                f"document.querySelector({TOGGLE!r}).getAttribute('aria-pressed')")
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
            # unknown: flipping the toggle must carry the toggle and nothing
            # else. This is the write that could smuggle a default out.
            page.wait_for(f"{TOGGLE}:enabled")
            page.evaluate(f"(() => document.querySelector({TOGGLE!r}).click())()")
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
        "the toggle snapped back -- the server's answer or its own broadcast "
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
        == json.dumps(NO_POSES_TEXT)


def test_escape_in_the_library_closes_only_the_library_and_keeps_the_settings_draft():
    """A dialog opened from inside another closes alone on Escape.

    The Ghost Library opens from inside the Ghost settings sheet. Every dialog
    listens on the document and the sheet registered first, so Escape used to
    close both and drop the sheet's unsaved draft. ui/components/modal.js keeps
    a stack, and only the topmost dialog answers keys. The draft is a real
    one: a visibility he moved in the sheet and has not saved, which must
    still be on screen after the library closes and be the one thing Done
    writes.
    """
    count = "document.querySelectorAll('.modal').length"
    escape = ("(document.activeElement || document.body).dispatchEvent("
              "new KeyboardEvent('keydown', {key: 'Escape', bubbles: true, cancelable: true}))")
    sheet_opacity = "document.querySelector('.modal .ghost-settings-appearance .ghost-opacity').value"
    draft = """(() => {
      const el = document.querySelector('.modal .ghost-settings-appearance .ghost-opacity');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, '0.3');
      el.dispatchEvent(new Event('input', {bubbles: true}));
    })()"""

    def settle_at(page, expected):
        for _ in range(60):
            if page.evaluate(count) == expected:
                return
            time.sleep(0.05)
        raise AssertionError(f"expected {expected} dialogs, saw {page.evaluate(count)}")

    with serve_ui_with_ghost(ghost_session(enabled=True, opacity=0.6), arm_segment=FIXTURE_SEGMENT,
                             seed_editor_fixtures=True) as (base, state):
        with get_driver().launch() as page:
            page.goto(base)
            open_ghost_settings(page)
            page.wait_for(".modal .ghost-settings-appearance .ghost-opacity:enabled")
            page.evaluate(draft)
            page.wait_ms(400)   # past the row's trailing write: the sheet keeps it local
            assert page.evaluate(sheet_opacity) == "0.3" and state["writes"] == [], state["writes"]
            page.wait_for(".ghost-library-button:enabled")
            page.click(".ghost-library-button")
            page.wait_for(LIBRARY_MODAL)
            settle_at(page, 2)
            page.evaluate(escape)
            settle_at(page, 1)
            assert page.evaluate("Boolean(document.querySelector('.modal .ghost-settings-heading'))")
            assert page.evaluate(sheet_opacity) == "0.3", "the sheet's unsaved draft was lost"
            assert state["writes"] == [], state["writes"]
            page.evaluate("""Array.from(document.querySelectorAll('.modal .primary-button'))
              .find((button) => button.textContent.trim() === 'Done').click()""")
            settle_at(page, 0)
            assert settled(page, state, 1) == [{"opacity": 0.3}]
