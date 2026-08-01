"""The caveat-mark registry's SHAPE (ui/components/marks.js).

A caveat says "this saved time does not mean what the rank beside it implies",
and three separate findings converged on it (round-4 items 2 and 4, round-3
ruling 6). The visual TREATMENT is a judgement call still owed to the human --
tools/mark_sheet.py draws all three candidates side by side -- so nothing here
asserts which one wins or what any of them looks like. That is deliberate and
it is the same trap the shipped-defaults rule names: a test that pins a
provisional choice turns the decision into a red build.

What IS pinned is what has to be true whichever treatment survives:

  * every treatment answers EVERY slot. `PracticeCell` and `PbTag` call
    cellSlot/cellOverlay/cellClass/cardMark unconditionally, so a treatment
    missing one is a TypeError the instant it is selected -- and the contact
    sheet renders every treatment at once, so one broken entry blanks the
    whole comparison the pick depends on;
  * every treatment declares `suppressFloor`. It is the one BEHAVIOURAL claim
    a treatment makes rather than a visual one (whether PracticeCell may still
    draw the ladder floor beneath the mark), and `undefined` reads as false --
    i.e. it silently reinstates the exact live-reported bug this mark exists
    to fix, in the one direction nobody would look for it;
  * every CAVEAT is ranked by CAVEAT_ORDER and vice versa. One 16px slot draws
    exactly one mark, so a caveat missing from the order can never be chosen
    by `worstCaveat` and would be silently invisible on the cell while still
    showing on the card -- the two surfaces disagreeing, which is the whole
    reason these three marks share one module.
"""
import re
from pathlib import Path

from source_scan import strip_comments

MARKS_JS = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui" / \
    "components" / "marks.js"

TREATMENT_SLOTS = ("cellSlot", "cellOverlay", "cellClass", "cardMark")


def _block(source: str, name: str) -> str:
    """The text of `export const <name> = {...}`, brace-matched."""
    stripped = strip_comments(source)
    start = stripped.find(f"export const {name} = {{")
    assert start != -1, f"{name} not found in marks.js -- renamed or moved?"
    open_brace = stripped.index("{", start)
    depth = 0
    for index in range(open_brace, len(stripped)):
        if stripped[index] == "{":
            depth += 1
        elif stripped[index] == "}":
            depth -= 1
            if depth == 0:
                return stripped[open_brace:index + 1]
    raise AssertionError(f"{name}'s object literal is unterminated")


def _treatments(source: str) -> dict[str, str]:
    """Each treatment key -> the text of its own entry."""
    block = _block(source, "CAVEAT_TREATMENTS")
    entries: dict[str, str] = {}
    for match in re.finditer(r"^  (\w+): \{$", block, re.MULTILINE):
        key = match.group(1)
        depth, start = 0, match.end() - 1
        for index in range(start, len(block)):
            if block[index] == "{":
                depth += 1
            elif block[index] == "}":
                depth -= 1
                if depth == 0:
                    entries[key] = block[start:index + 1]
                    break
    return entries


def _caveat_keys(source: str) -> list[str]:
    return re.findall(r"^  (\w+): \{$", _block(source, "CAVEATS"), re.MULTILINE)


def _order_keys(source: str) -> list[str]:
    stripped = strip_comments(source)
    match = re.search(r"export const CAVEAT_ORDER = \[(.*?)\];", stripped, re.S)
    assert match, "CAVEAT_ORDER not found in marks.js"
    return re.findall(r'"(\w+)"', match.group(1))


def test_every_treatment_answers_every_slot_and_declares_suppress_floor():
    entries = _treatments(MARKS_JS.read_text(encoding="utf-8"))
    assert entries, "CAVEAT_TREATMENTS is empty -- the contact sheet has nothing to draw"
    for key, body in entries.items():
        for slot in TREATMENT_SLOTS:
            assert f"{slot}:" in body, (
                f"CAVEAT_TREATMENTS.{key} has no {slot} -- PracticeCell/PbTag "
                f"call it unconditionally, so selecting this treatment throws")
        assert "suppressFloor:" in body, (
            f"CAVEAT_TREATMENTS.{key} does not declare suppressFloor -- "
            f"undefined reads as false, which silently draws the ladder floor "
            f"under a PB no strategy can claim (the bug this mark exists for)")


def test_the_caveats_and_their_severity_order_name_the_same_set():
    source = MARKS_JS.read_text(encoding="utf-8")
    assert sorted(_caveat_keys(source)) == sorted(_order_keys(source)), (
        "CAVEATS and CAVEAT_ORDER disagree -- a caveat absent from the order "
        "can never be picked by worstCaveat, so it would show on the practice "
        "card and vanish from the quick-select cell")


def test_the_marks_guards_can_still_fail():
    """A guard nobody has seen fail is green forever (tests/source_scan.py).

    Each shape below is a REAL regression this file's checks are aimed at, fed
    as source text rather than produced by editing marks.js."""
    complete = ('export const CAVEAT_TREATMENTS = {\n'
                '  slot: {\n'
                '    suppressFloor: true,\n'
                '    cellSlot: () => null,\n'
                '    cellOverlay: () => null,\n'
                '    cellClass: () => "",\n'
                '    cardMark: () => null,\n'
                '  },\n'
                '};\n'
                'export const CAVEATS = {\n'
                '  grab_timed: {\n'
                '    glyph: "!",\n'
                '  },\n'
                '};\n'
                'export const CAVEAT_ORDER = ["grab_timed"];\n')
    entries = _treatments(complete)
    assert list(entries) == ["slot"]
    assert all(f"{slot}:" in entries["slot"] for slot in TREATMENT_SLOTS)
    assert _caveat_keys(complete) == _order_keys(complete) == ["grab_timed"]

    # Regression 1: a treatment drops one renderer.
    missing_slot = complete.replace('    cellClass: () => "",\n', "")
    assert "cellClass:" not in _treatments(missing_slot)["slot"]

    # Regression 2: a treatment forgets suppressFloor, so it defaults to
    # "keep flooring" — the bug, wearing a mark.
    no_floor_claim = complete.replace("    suppressFloor: true,\n", "")
    assert "suppressFloor:" not in _treatments(no_floor_claim)["slot"]

    # Regression 3: a caveat is added and the severity order is not.
    orphaned = complete.replace('  grab_timed: {\n    glyph: "!",\n  },\n',
                                '  grab_timed: {\n    glyph: "!",\n  },\n'
                                '  old_clock: {\n    glyph: "!=",\n  },\n')
    assert sorted(_caveat_keys(orphaned)) != sorted(_order_keys(orphaned))
