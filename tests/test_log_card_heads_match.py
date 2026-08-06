# tests/test_log_card_heads_match.py
"""Every practice-log card's head is the same height, armed or not.

Replaces `test_step_track_on_the_identity_line.py`, which pinned WHERE the
armed card's step track sat. Griffin deleted that track on 2026-08-06 — "we
should just remove the step indicator entirely from the display here. It's too
cramped" — so the question changed from "is the extra element positioned
correctly" to "did the row go back to holding nothing extra at all".

That is worth a render rather than a source scan for the same reason the old
test was: an element added to this row grows the head, which is a pure layout
fact with no DOM signature. It is the failure this surface keeps producing —
an armed card twice its neighbours' height (2026-08-05), then 7px taller than
them (2026-08-06, a baseline-alignment attempt) — and both times every unit
test and `node --check` passed straight through it.

Deliberately NOT a fixed pixel number: the head's floor is derived
(`--log-icon-size` + `--log-head-pad-y` * 2) and both are tunable, so a
literal here would turn a tuning round red for no reason. What is pinned is
that the cards AGREE with each other.
"""
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

from ui_fixture import FIXTURE_SEGMENT, serve_ui  # noqa: E402

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab import driver  # noqa: E402

SETTLE = "new Promise(r => setTimeout(r, 2500))"

MEASURE = """
  (() => {
    const heads = Array.from(document.querySelectorAll('.log-card-head'));
    return {
      heights: heads.map((el) => Math.round(el.getBoundingClientRect().height)),
      names: heads.map((el) => {
        const b = el.querySelector('.log-card-name b, .log-card-name');
        return b ? b.textContent.trim().slice(0, 40) : '?';
      }),
      stepRows: document.querySelectorAll('.log-card .step-row').length,
      segWaiting: document.querySelectorAll('.log-card .seg-waiting').length,
    };
  })()
"""


@pytest.fixture(scope="module")
def drawn():
    with tempfile.TemporaryDirectory() as scratch:
        db = Path(scratch) / "heads.db"
        # `arm_segment` is what makes this fixture worth measuring: it is the
        # state that used to add the extra row, so a page without it would
        # pass this file forever without ever reaching the case.
        with serve_ui(db, arm_segment=FIXTURE_SEGMENT) as base:
            with driver.get_driver().launch(headless=True,
                                            viewport=(1500, 1000)) as page:
                page.goto(base)
                page.evaluate(SETTLE)
                return page.evaluate(MEASURE)


def test_the_fixture_really_drew_several_cards(drawn):
    """Without this the equality below holds vacuously on an empty page — the
    root cause on this surface three times over (`.claude/rules/ui-core.md`)."""
    assert len(drawn["heights"]) >= 2, (
        f"only {len(drawn['heights'])} card head(s) rendered: {drawn}")


def test_no_card_draws_a_step_track(drawn):
    """The armed segment is on this page (see the fixture), so a zero here is
    a real absence rather than a state nobody reached."""
    assert drawn["stepRows"] == 0, (
        f"{drawn['stepRows']} step row(s) back inside a practice-log card")
    assert drawn["segWaiting"] == 0, (
        f"{drawn['segWaiting']} `.seg-waiting` row(s) back inside a card")


def test_every_head_is_the_same_height(drawn):
    heights = drawn["heights"]
    assert len(set(heights)) == 1, (
        "practice-log card heads disagree on height, so one card is carrying "
        "something the others are not: "
        f"{list(zip(drawn['names'], heights))}")
