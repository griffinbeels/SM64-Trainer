"""The practice card explains itself while a segment is armed (Task 6, spec
2026-07-28-multi-step-segments).

Source-scan guards in the tests/test_ui_picker_parity.py style: assert on
strip_comments(source), and probe each guard with a comment-only sample (must
pass) and a real-code sample (must fail) — see tests/source_scan.py for why a
substring assertion cannot tell code from prose.

Two properties, both load-bearing:

1. `practicedHere` (ui/stagecontext.js) gates a pinned card on course match
   and deliberately has NO armed exception — it is the shared predicate star
   sections use too, and an exception baked in there would silently change
   star behaviour (see its own docstring, the `lastPinnedSeg` bug it exists
   to prevent). Completing "a running segment is never invisible" (user rule
   2026-07-24) on the gate that never reached it belongs at the SEGMENT call
   site in practice.js, reading the section's own `armed_detail` — server
   truth, re-derived from the journal on every event — never
   `armedSegments`/`lastPinnedSeg`, either of which is exactly what let
   "ACTIVE SEGMENT LBLJ" survive two course changes after the server had
   already retired it (live report 2026-07-27).
2. The card renders which step an armed def is on and what it is waiting for
   next, from `sec.armed_detail` (Task 4's `progress`/`total`/`waiting_for`).
"""
import re
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"


def read(relative: str) -> str:
    return (UI / relative).read_text(encoding="utf-8")


# armed_detail immediately followed by `||` and a call to the course-match
# predicate (either the shared `practicedHere` directly, or practice.js's own
# `here(...)` wrapper around it -- `const here = (sec) => practicedHere(sec,
# held)`, a few lines above the call sites this guards). Either spelling is
# the same override; a raw substring couldn't tell "armed_detail is checked
# somewhere, practicedHere is checked somewhere else, entirely unrelated"
# from "armed_detail overrides practicedHere", which is why this is anchored
# on the `||` between them rather than just requiring both words to appear.
PIN_GUARD = re.compile(r"armed_detail\s*\|\|\s*(?:practicedHere|here)\s*\(")


def pin_guard(src: str) -> bool:
    return bool(PIN_GUARD.search(src))


def test_the_card_keeps_a_pinned_segment_while_it_is_armed():
    src = strip_comments(read("components/practice.js"))
    assert "armed_detail" in src
    assert pin_guard(src)


def test_no_practice_surface_draws_a_step_track():
    """DELETED, and the guard is inverted rather than dropped.

    The card used to draw the whole route ("Step 3 of 4 · ✓ BitFS › ▸ Lobby")
    and this test pinned that it drew EVERY step rather than only the live
    one. Griffin removed the display on 2026-08-06 — "we should just remove
    the step indicator entirely from the display here. It's too cramped… they
    will know how to do the strat, no need to display the steps."

    So the claim now is that it stays gone. Two rounds tried to seat that row
    on the identity line and both were rejected for crowding it; a third
    attempt would look like an improvement and is the thing he ruled out. The
    server half is untouched and deliberately NOT asserted here — the section
    still carries `armed_detail`, which `test_views.py` owns.
    """
    owner = strip_comments(read("components/steptrack.js"))
    assert "StepTrack" not in owner, (
        "steptrack.js exports a read-only track again — the practice card's "
        "step display was deleted, and this module is the editor's now")
    for name in ("components/practicelog.js", "components/practice.js"):
        src = strip_comments(read(name))
        assert "detail.progress" not in src and "armed_detail.steps" not in src, (
            f"{name} reads a run's step cursor to draw it again")


def test_the_step_track_markup_exists_in_exactly_one_place():
    """Rule 11 with teeth: the star card (100 Coins) and the segment card
    were two byte-identical copies of this markup until 2026-08-03, and the
    EDITOR would have been a third. A second copy looks perfectly correct and
    drifts on the next change, so what is pinned is that the row can only be
    built in one file — the one both the card and the segment editor import.
    Counted across all of `ui/`, not just practice.js: scoping it to one file
    is exactly how the second copy gets written somewhere else."""
    others = [path for path in sorted(UI.rglob("*.js"))
              if "vendor" not in path.parts and path.name != "steptrack.js"]
    trespassers = {
        path.name: token
        for path in others
        for token in ("step-chip", "step-track", "step-mark", "step-row")
        if token in strip_comments(path.read_text(encoding="utf-8"))}
    assert trespassers == {}, (
        f"only steptrack.js may build a step row; found {trespassers}")
    owner = strip_comments(read("components/steptrack.js"))
    assert owner.count('class="step-mark"') == 1, (
        "the chip's own marker stack is the thing that must exist once — two "
        "copies inside the module is the same divergence one level down")


def test_no_practice_card_carries_the_step_rows_own_class():
    """`.seg-waiting` was the practice card's own spacing on top of the shared
    `.step-row` layout, and index.html still keys rules off it. With the card's
    track deleted (above), no practice surface may wear it — a card claiming
    that class would be claiming to be an armed row that no longer exists."""
    for name in ("components/practice.js", "components/practicelog.js"):
        assert 'class="seg-waiting"' not in strip_comments(read(name))


def test_the_guards_can_still_fail():
    # Comment-only: PIN_GUARD must not fire on prose merely mentioning both
    # words, and the plain "armed_detail" / "waiting_for" checks above must
    # not fire on a comment either.
    comment_only = strip_comments(
        "// armed_detail is server truth; practicedHere is the course gate\n"
        "// and waiting_for the next step\n")
    assert not pin_guard(comment_only)
    assert "armed_detail" not in comment_only
    assert "waiting_for" not in comment_only

    # Code that merely NAMES both, unrelated to each other, must not satisfy
    # the pin guard -- two independent checks are not an override.
    unrelated = "const a = sec.armed_detail; const b = practicedHere(sec, t);"
    assert not pin_guard(unrelated)

    # The real shape, either spelling, must satisfy it.
    assert pin_guard("sec.armed_detail || practicedHere(sec, held)")
    assert pin_guard("stickyPin.armed_detail || here(stickyPin)")
