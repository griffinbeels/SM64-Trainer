"""The practice-log card's floor-state rank display reads unbroken too.

Companion to test_ui_rank_progress_track_floor_wash.py (that file's own
docstring has the report and the root cause). This file drives the SECOND of
the two mechanisms that can render Stacked/Column: the practice log's
ancestor-scoped "Layout matrix" CSS (index.html), reached through
`/ui/tunelog.html` -- the shipped rig for this exact card, and the one his
report named directly ("This also didn't happen in the tuning tool"). Kept in
its own file (a separate `serve_ui()` fixture) rather than sharing a module
with the `tune.html` mechanism: two `serve_ui()` calls in one test module
collided over asyncio's "cannot be called from a running event loop", so one
`serve_ui()` per file is what this suite already needs.

The floor fixture card ("Go on a Ghost Hunt") is the one this same report
added to `ui/tunelog.js` -- until then no state like it existed in that
fixture at all (every section there was either fully graded or the
`no_strat` sentinel), which is the concrete form of "didn't happen in the
tuning tool": the rig could not RENDER the state that broke.
"""
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import serve_ui  # noqa: E402
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402

# Identified by the text a human reads, not a positional `nth-child` -- more
# robust if the fixture list in ui/tunelog.js is ever reordered again.
FLOOR_CARD_NAME = "Go on a Ghost Hunt"
GRADED_CARD_NAME = "Blast Away the Wall in Front"
SHARED_STAR_CARD_NAME = "Plunder in the Sunken Ship"
SHARED_SEGMENT_CARD_NAME = "DDD → BITFS"


def _alpha(rgba: str) -> float:
    inside = rgba[rgba.index("(") + 1: rgba.rindex(")")]
    parts = [part.strip() for part in inside.split(",")]
    return float(parts[3]) if len(parts) == 4 else 1.0


def _find_card(pane_index: int, name: str) -> str:
    """A JS EXPRESSION (not a statement) naming the matched `.log-card` --
    shared by the two scripts below so "how a card is found" stays in one
    place."""
    return (
        f"[...document.querySelectorAll('.tunelog-pane')[{pane_index}]"
        "    .querySelectorAll('.log-card')]"
        f"  .find((el) => el.textContent.includes({name!r}))"
    )


def _open_card_script(pane_index: int, name: str) -> str:
    """Rank placement ships at "body" for every quadrant (`ui/logtuning.js`'s
    `rankPlacement*` rows) -- the rank display renders only once a card is
    OPEN, so reading it starts with the SAME gesture a person would make:
    clicking the fold chevron. Guarded on `.is-closed` -- the fixture's OWN
    top card (the auto-open slot, `topEntityKey`) starts OPEN already, and an
    unconditional click would instead CLOSE it. Returns whether a card was
    found at all, so a selector drifting silently reads as "nothing to
    check" rather than a false pass."""
    return (
        "(() => {"
        f"  const card = {_find_card(pane_index, name)};"
        "  if (!card) return false;"
        "  if (card.classList.contains('is-closed')) {"
        "    card.querySelector('.log-card-fold').click();"
        "  }"
        "  return true;"
        "})()"
    )


def _read_card_script(pane_index: int, name: str) -> str:
    """Reads computed style in a SEPARATE evaluate call from whatever opened
    the card -- Preact commits after the dispatching tick, so reading in the
    SAME call would see the pre-render layout (`.claude/rules/ui-core.md`'s
    own warning on this exact trap)."""
    return (
        "(() => {"
        f"  const card = {_find_card(pane_index, name)};"
        "  if (!card) return null;"
        "  const track = card.querySelector('.rank-progress-track');"
        "  const fill = card.querySelector('.rank-progress-track i');"
        "  return {"
        "    trackBg: track ? getComputedStyle(track).backgroundColor : null,"
        "    fillWidth: fill ? getComputedStyle(fill).width : null,"
        "    fillBg: fill ? getComputedStyle(fill).backgroundColor : null,"
        "  };"
        "})()"
    )


def _open_and_read_card(page, pane_index: int, name: str) -> dict:
    opened = page.evaluate(_open_card_script(pane_index, name))
    assert opened, f"no card named {name!r} (or no fold button on it) in pane {pane_index}"
    page.wait_ms(150)
    result = page.evaluate(_read_card_script(pane_index, name))
    assert result, f"card {name!r} vanished after opening it (pane {pane_index})"
    return result


def _read_rank_buttons(page, pane_index: int, name: str) -> list[list[str]]:
    return page.evaluate(
        "(() => {"
        f"  const card = {_find_card(pane_index, name)};"
        "  if (!card) return null;"
        "  return [...card.querySelectorAll('.rank-mode-button')]"
        "    .map(b => [b.textContent.trim(), b.getAttribute('aria-pressed')]);"
        "})()")


@pytest.fixture(scope="module")
def tunelog_demo():
    with serve_ui() as base:
        with get_driver().launch(headless=True, viewport=(1920, 1200)) as page:
            page.goto(f"{base}/ui/tunelog.html")
            page.wait_for(".log-card", timeout_ms=20_000)
            yield page


# Wide pane first (index 0), narrow second (index 1) -- ui/tunelog.html's own
# stacking order, and the task's own ask: "at wide and narrow".
@pytest.mark.parametrize("pane_index,pane_label", [(0, "wide"), (1, "narrow")])
def test_the_practice_log_floor_card_reads_unbroken(tunelog_demo, pane_index, pane_label):
    """Column is the shipped default at every width (`ui/logtuning.js`), so
    both panes exercise the log-card ancestor-scoped mechanism, never the
    generic `layout` prop test_ui_rank_progress_track_floor_wash.py drives."""
    result = _open_and_read_card(tunelog_demo, pane_index, FLOOR_CARD_NAME)
    assert result["trackBg"], f"no .rank-progress-track on the floor card ({pane_label})"
    alpha = _alpha(result["trackBg"])
    assert alpha == 0, (
        f"the {pane_label} floor card's track still paints a background "
        f"(alpha {alpha}) -- the wash behind it would still read as cut")


@pytest.mark.parametrize("pane_index,pane_label", [(0, "wide"), (1, "narrow")])
def test_a_graded_card_still_reads_its_fill_clearly(tunelog_demo, pane_index, pane_label):
    """The other half of the fix's own constraint: a graded rank must not
    lose the contrast it needs just because the track's own background went
    transparent. `caps.js::barFill` anchors every graded (non-floor) rank at
    >=50% width of solid `--climb-color`, so the fill itself -- not the
    track behind it -- is what has to still read clearly."""
    result = _open_and_read_card(tunelog_demo, pane_index, GRADED_CARD_NAME)
    assert result["fillWidth"] and result["fillWidth"] != "0px", (
        f"the {pane_label} graded card's fill drew no width at all")
    assert result["fillBg"], f"the {pane_label} graded card's fill painted no colour"
    alpha = _alpha(result["fillBg"])
    assert alpha > 0.9, (
        f"the {pane_label} graded card's fill lost its own opacity (alpha "
        f"{alpha}) -- it must stay a solid, clearly-readable colour")


@pytest.mark.parametrize("card_name", [SHARED_STAR_CARD_NAME,
                                        SHARED_SEGMENT_CARD_NAME])
def test_shared_ladder_star_and_segment_only_offer_overall(tunelog_demo, card_name):
    """One ladder means one inert Overall control for either entity kind."""
    assert tunelog_demo.evaluate(_open_card_script(0, card_name)), card_name
    tunelog_demo.wait_ms(150)
    assert _read_rank_buttons(tunelog_demo, 0, card_name) == [["Overall", "true"]]
    tunelog_demo.evaluate(
        "(() => {"
        f"  const card = {_find_card(0, card_name)};"
        "  card.querySelector('.rank-mode-button').click();"
        "  return true;"
        "})()")
    tunelog_demo.wait_ms(40)
    swapping = tunelog_demo.evaluate(
        "(() => {"
        f"  const card = {_find_card(0, card_name)};"
        "  return card.querySelectorAll('.rank-banner.is-swapping').length;"
        "})()")
    assert swapping == 0


# --- the tuning fixture must reach every state the real card has -----------

ACTION_CELLS = """
  (() => {
    const rows = Array.from(document.querySelectorAll('.log-card tr'))
      .filter((tr) => tr.querySelector('.attempt-actions'));
    const cell = (tr) => tr.querySelector('.attempt-actions');
    // `.attempt-result.good` IS the success class attemptlog.js stamps. A
    // glyph match would depend on the tick surviving three layers of quoting,
    // which is how the first version of this read zero success rows and
    // reported the fixture broken when it was fine.
    const success = rows.filter((tr) => tr.querySelector('.attempt-result.good'));
    const active = (tr) => {
      const card = tr.closest('.log-card');
      const head = card.querySelector('.log-card-strat-picker select');
      const row = tr.querySelector('.attempt-strategy select');
      return head && row && head.value === row.value;
    };
    const hasAction = (tr) => !!cell(tr).querySelector('button:not(.icon-button)');
    return {
      rows: rows.length,
      successRows: success.length,
      // A success row on the card's ACTIVE strategy must offer an action
      // (Save or Undo); one on another strategy must offer NONE (2026-08-22).
      // An EMPTY actions cell on an active-strategy row is the shape a
      // fixture takes when it stops shipping the field the column is drawn
      // from.
      mute: success.filter((tr) => active(tr) && !hasAction(tr)).length,
      leaking: success.filter((tr) => !active(tr) && hasAction(tr)).length,
      foreign: success.filter((tr) => !active(tr)).length,
      saves: document.querySelectorAll('.log-card .attempt-actions button.pb-glow, '
        + '.log-card .attempt-actions button:not(.icon-button)').length,
      pbTags: [...new Set(Array.from(document.querySelectorAll('.log-card .pbtag'))
        .map((t) => t.textContent.trim()))],
    };
  })()
"""


def test_the_tuning_fixture_draws_a_real_actions_column(tunelog_demo):
    """This page is where the log card gets tuned by feel, so a state it
    cannot draw is a state he cannot tune -- and the actions column is drawn
    from a SERVER-resolved field now (`pb_action`/`pb_blocked`,
    tracking/caveats.py::pb_action, 2026-08-20). A fixture still shipping only
    the old `is_current_pb` renders every button away and nothing else here
    goes red: no assertion in this suite reads that column, by the standing
    rule that no test may pin a tuning page's CONTENTS.

    So this pins COHERENCE, not contents: every success row on the card's
    active strategy offers an action, every success row on another strategy
    offers none (his 2026-08-22 ruling), and the page holds at least one of
    each so both row shapes are tuned against."""
    state = tunelog_demo.evaluate(ACTION_CELLS)
    assert state["successRows"] >= 3, state
    assert state["mute"] == 0, (
        "active-strategy success rows with an empty actions cell -- tunelog.js "
        "is not shipping pb_action/pb_blocked", state)
    assert state["leaking"] == 0, (
        "a row on another strategy offers a PB action", state)
    assert state["foreign"] >= 1, (
        "no other-strategy row anywhere on the tuning page: the emptiest "
        "actions cell cannot be tuned against a page that never draws it",
        state)


def test_the_tuning_fixture_draws_every_pb_tag_state(tunelog_demo):
    """The tag has three shapes since the PB became per-strategy, and they are
    different WIDTHS in a grid track that was tuned against only the first --
    which is exactly how the first version of this feature overflowed into the
    strategy picker. All three have to be on the page he tunes it on."""
    tags = tunelog_demo.evaluate(ACTION_CELLS)["pbTags"]
    assert any(t.startswith("PB ") and '"' in t for t in tags), tags
    assert any(t.startswith("no PB") for t in tags), tags
    assert any("no strategy" in t for t in tags), tags
