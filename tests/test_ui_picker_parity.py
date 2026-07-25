"""Every entity selection renders through the shared picker.

This is the test that addresses the actual complaint (2026-07-25: "feels like
we're redoing a lot of the same work over and over again"). Without it a fifth
hand-rolled course/star select appears the next time someone needs one in a
hurry, and the grouping silently stops being universal.

Final-review fix (2026-07-25, I1-I3): the first version of this file pinned a
hardcoded three-file call-site LIST and checked only for the string
"GroupedPicker" somewhere in each — invisible to a fourth hand-rolled picker
in a NEW file, or a second hand-rolled select added next to the shared one in
an EXISTING listed file. It also had a domain-vocabulary guard that only
inspected the import block (`split("//")[0]` truncates at the header comment's
first `//`), and a re-derivation guard for Python identifiers that appear in
no `.js` file and so could never fail. All three are replaced below with
checks that scan real content.

Both guards are expressed as FUNCTIONS of source text so
`test_the_guards_can_still_fail` can feed them a comment-only sample and a
real-code sample on every run — see tests/source_scan.py for why a guard that
is never probed is not a guard.
"""
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"

# Every .js file under ui/ is scanned for the SHAPE of a hand-rolled entity
# select: an <option> alongside a reference to one of the server-shipped
# vocabularies that only the shared builders (ui/entities.js) should be
# reading directly. A file that legitimately needs to do this is named here,
# with the reason — this is what a future author has to consciously edit,
# unlike a call-site list they never see.
DOMAIN_VOCAB_MARKERS = (
    "catalog.courses", "vocab.courses", "vocab.stars", "vocab.levels",
    ".origin.region",
)
ALLOWED_HAND_ROLLED_SELECTS = {
    # The `subarea` branch renders a 3-item castle_areas list — nothing to
    # group (see ParamInput's comment in segments.js).
    "components/segments.js",
    # entitymodal.js IS the shared cell renderer every other file defers to.
    # It renders PracticeCell buttons and contains no <option> at all today, so
    # it would not trip the scan — listed anyway so a future markup change
    # cannot silently make the shared component itself the violation.
    "components/entitymodal.js",
}

# Domain vocabulary that must not migrate INTO the shared picker: it takes
# groups, ids and an iconFor() from its caller and must never learn what a
# course or a star is. Union of the two lists that grew in parallel branches
# (this file's and test_ui_entitymodal.py's) — one guard, one home.
PICKER_FORBIDDEN_WORDS = ("course", "star", "level", "segment", "topology",
                          "route", "vocab", "catalog")


def _js_files():
    return sorted(path for path in UI.rglob("*.js"))


def hand_rolled_select_offenders(source: str) -> list:
    """Domain vocabularies referenced by a file that renders its own <option>."""
    code = strip_comments(source)
    if "<option" not in code:
        return []
    return [marker for marker in DOMAIN_VOCAB_MARKERS if marker in code]


def picker_domain_residue(source: str) -> list:
    """Domain words surviving in the shared picker's CODE."""
    residue = strip_comments(source).lower()
    return [word for word in PICKER_FORBIDDEN_WORDS if word in residue]


def test_no_hand_rolled_entity_select_outside_the_allowlist():
    for path in _js_files():
        relative = path.relative_to(UI).as_posix()
        if relative in ALLOWED_HAND_ROLLED_SELECTS:
            continue
        offenders = hand_rolled_select_offenders(
            path.read_text(encoding="utf-8"))
        assert not offenders, (
            f"{relative}: renders <option> and references {offenders} — "
            "route it through EntityPicker/entities.js, or add it to "
            "ALLOWED_HAND_ROLLED_SELECTS with a reason")


def test_the_picker_owns_no_domain_vocabulary():
    # Retargeted 2026-07-25 from components/picker.js to components/
    # entitymodal.js: the native <select> was replaced by the icon modal, and
    # the guard follows the shared component rather than a filename.
    picker_source = (UI / "components" / "entitymodal.js").read_text(
        encoding="utf-8")
    assert picker_domain_residue(picker_source) == []


def test_the_guards_can_still_fail():
    # The by-hand re-probe both guards needed after every retarget, run on
    # every suite instead. Each pair is (comment-only → clean, code → caught):
    # a guard that a comment trips reports work that is fine as a violation, and
    # a guard blind to code reports a violation as fine.
    assert picker_domain_residue("// course grouping stays at the call site\n") == []
    assert picker_domain_residue("const courseName = groups[0];\n") == ["course"]

    option_with_comment = "// vocab.courses used to be read here\n<option/>\n"
    assert hand_rolled_select_offenders(option_with_comment) == []
    assert hand_rolled_select_offenders(
        "<option>${vocab.courses[0]}</option>") == ["vocab.courses"]
    # No <option> anywhere: reading the vocabulary is not by itself a select.
    assert hand_rolled_select_offenders("const all = vocab.courses;") == []
