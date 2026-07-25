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
checks that scan real content and are demonstrated to fail on a real probe.
"""
import re
from pathlib import Path

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


def _js_files():
    return sorted(path for path in UI.rglob("*.js"))


def test_no_hand_rolled_entity_select_outside_the_allowlist():
    for path in _js_files():
        relative = path.relative_to(UI).as_posix()
        if relative in ALLOWED_HAND_ROLLED_SELECTS:
            continue
        source = path.read_text(encoding="utf-8")
        if "<option" not in source:
            continue
        for marker in DOMAIN_VOCAB_MARKERS:
            assert marker not in source, (
                f"{relative}: renders <option> and references {marker!r} — "
                "route it through EntityPicker/entities.js, or add it to "
                "ALLOWED_HAND_ROLLED_SELECTS with a reason")


def _strip_comments(source: str) -> str:
    without_block_comments = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"^\s*//.*$", "", without_block_comments, flags=re.MULTILINE)


def test_the_picker_owns_no_domain_vocabulary():
    # Retargeted 2026-07-25 from components/picker.js to components/
    # entitymodal.js: the native <select> was replaced by the icon modal, and
    # the guard follows the shared component rather than a filename.
    # The inverse guard: domain rules must not migrate INTO the picker. Strip
    # BOTH comment styles before checking — the old `split("//")[0]` only
    # inspected the header's import block (127 of 2472 chars) and would not
    # have caught a domain word added to the component body.
    picker_source = (UI / "components" / "entitymodal.js").read_text(encoding="utf-8")
    residue = _strip_comments(picker_source).lower()
    for domain_word in ("course", "star", "level", "segment", "topology",
                        "route"):
        assert domain_word not in residue, domain_word
