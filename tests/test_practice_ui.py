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


def test_the_card_renders_what_the_segment_is_waiting_for():
    src = strip_comments(read("components/practice.js"))
    assert "waiting_for" in src
    assert "armed_detail.progress" in src
    assert "armed_detail.total" in src


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
