"""Every entity selection renders through the shared picker.

This is the test that addresses the actual complaint (2026-07-25: "feels like
we're redoing a lot of the same work over and over again"). Without it a fifth
hand-rolled course/star select appears the next time someone needs one in a
hurry, and the grouping silently stops being universal.
"""
from pathlib import Path

UI = Path(__file__).resolve().parent.parent / "src" / "sm64_events" / "ui"

# Call sites that select a level, course, star or segment. Each must import
# the shared picker. Add a row when a new one appears — do NOT add an exception.
ENTITY_PICKER_CALL_SITES = [
    "components/segments.js",   # clause params: level / course / star
    "components/header.js",     # practice-target modal: star
    "components/routes.js",     # route step editor: star / segment
]


def test_every_entity_selection_uses_the_shared_picker():
    for relative in ENTITY_PICKER_CALL_SITES:
        source = (UI / relative).read_text(encoding="utf-8")
        assert "GroupedPicker" in source, relative


def test_no_call_site_rebuilds_the_grouping_itself():
    # The taxonomy has one home (tracking/segments.py). A call site computing
    # its own region membership is the drift this whole change removes.
    for relative in ENTITY_PICKER_CALL_SITES:
        source = (UI / relative).read_text(encoding="utf-8")
        for derivation in ("world_regions", "CASTLE_REGION", "region_for_node"):
            assert derivation not in source, f"{relative}: {derivation}"


def test_the_picker_owns_no_domain_vocabulary():
    # The inverse guard: domain rules must not migrate INTO the picker.
    picker = (UI / "components" / "picker.js").read_text(encoding="utf-8")
    for domain_word in ("course", "star", "level", "segment", "topology",
                        "route"):
        assert domain_word not in picker.lower().split("//")[0], domain_word
