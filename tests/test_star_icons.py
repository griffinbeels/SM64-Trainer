# tests/test_star_icons.py
"""Per-star course-icon mode (spec 2026-07-24-star-icon-mode-design).

The star selector can render each main-course star's split-icon art
(ui/assets/star_icons/{prefix}{n}.png) instead of the generic gold star.
These tests pin the three seams that would break silently:

- the asset set: the chain indexes `{prefix}{slot+1}` blindly, so a missing
  file renders a broken img (the JS onerror fallback hides it, which makes
  the hole INVISIBLE in playtests — only this test shows it);
- the prefix registry: owned by entities.js, must cover course ids 1..15 in
  catalog order;
- the setting wiring: store.js owns the localStorage key, header.js offers
  the control, entityicons.js reads it into the icon context — three files
  that only agree by convention;
- the resolution itself: exactly two modules may build art at all, and every
  surface goes through them — enforced in tests/test_single_source.py, which
  is also where the next invariant of that shape goes (2026-07-26).

Also pins the scale-to-fit contract: the selector row must never regrow a
horizontal scrollbar (the 2026-07-24 UX fix this shipped with).
"""
import re
import struct
from pathlib import Path

from source_scan import strip_comments

UI = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui"

# Course ids 1..15 in COURSE_NAMES order (memory/addresses.py) -> icon prefix.
PREFIXES = ["bob", "wf", "jrb", "ccm", "bbh", "hmc", "lll", "ssl",
            "ddd", "sl", "wdw", "ttm", "thi", "ttc", "rr"]


def test_every_main_course_star_icon_exists():
    icons = UI / "assets" / "star_icons"
    missing = [f"{prefix}{slot}.png"
               for prefix in PREFIXES for slot in range(1, 8)
               if not (icons / f"{prefix}{slot}.png").is_file()]
    assert not missing, f"star_icons is missing {missing}"


def test_prefix_registry_matches_course_order():
    """COURSE_ICON_PREFIXES is owned by entities.js — the import-free module,
    so node can unit-test the icon chain against it. Since 2026-07-26 nothing
    else may even READ it: every surface asks entityicons.js for an entity's
    art rather than indexing this table itself. One registry, one consumer,
    still pinned against the catalog order here."""
    source = (UI / "entities.js").read_text(encoding="utf-8")
    match = re.search(r"COURSE_ICON_PREFIXES\s*=\s*\[([^\]]*)\]", source)
    assert match, "entities.js lost its COURSE_ICON_PREFIXES registry"
    listed = re.findall(r'"(\w+)"', match.group(1))
    assert listed == PREFIXES, (
        "COURSE_ICON_PREFIXES disagrees with the course catalog order: "
        f"{listed}")
    banner = (UI / "components" / "stagebanner.js").read_text(encoding="utf-8")
    assert 'from "./entityicons.js"' in banner, \
        "stagebanner.js no longer imports the shared icon resolver"
    # And the layering itself: the pure module must NOT reach up into a
    # component, or node can no longer execute it (merge resolution
    # 2026-07-25 — two branches extracted this registry the same day, one
    # downward into entities.js and one sideways into entityicons.js).
    entities_imports = re.findall(r'^import .*$', source, re.MULTILINE)
    assert not entities_imports, (
        f"entities.js must import nothing to stay node-testable: {entities_imports}")


def test_setting_is_wired_through_store_header_and_banner():
    store = (UI / "store.js").read_text(encoding="utf-8")
    header = (UI / "components" / "header.js").read_text(encoding="utf-8")
    banner = (UI / "components" / "stagebanner.js").read_text(encoding="utf-8")
    resolver = (UI / "components" / "entityicons.js").read_text(encoding="utf-8")
    assert "sm64.starIcons" in store, "store.js lost the sm64.starIcons key"
    assert "pickStarIcons" in store and "starIcons" in store
    assert "starIcons" in header, "settings drawer lost the star-icon control"
    # The preference is read ONCE, where the icon context is assembled
    # (entityicons.js::iconContext); the banner still owns PASSING `t` through
    # on every cell, which is what carries the setting to the chain.
    assert "t.starIcons" in resolver, \
        "entityicons.js's iconContext no longer reads the icon mode"
    assert "entityIconSrc(t" in banner, \
        "StarRow no longer feeds the tracker state into icon resolution"


def test_course_icons_are_the_default_mode():
    """User request 2026-07-24: the split-icon art is the default look."""
    store = (UI / "store.js").read_text(encoding="utf-8")
    assert 'localStorage.getItem("sm64.starIcons") || "course"' in store, \
        "sm64.starIcons no longer defaults to course icons"


def test_every_banner_row_renders_the_shared_cell():
    """All banner modes must render through PracticeCell — the segment rows
    shipped as bare text buttons and diverged from the approved star look
    (user report 2026-07-24, spec 2026-07-24-segment-icon-cells). Rows may
    go through StandardSegmentCell, which itself must wrap PracticeCell."""
    source = (UI / "components" / "stagebanner.js").read_text(encoding="utf-8")
    std = re.search(r"^function StandardSegmentCell\(.*?^}", source,
                    re.S | re.M)
    assert std and "<${PracticeCell}" in std.group(0), \
        "StandardSegmentCell no longer wraps PracticeCell"
    for row in ("StarRow", "SegmentRow", "ArenaRow", "BowserCourseRow",
                "ArmedOnlyRow"):
        match = re.search(rf"^function {row}\(.*?^}}", source, re.S | re.M)
        assert match, f"{row} not found in stagebanner.js"
        body = match.group(0)
        assert "<${PracticeCell}" in body \
            or "<${StandardSegmentCell}" in body, \
            f"{row} stopped rendering the shared cell"


def test_every_row_surfaces_armed_segments():
    """A RUNNING segment must never be invisible (user rule 2026-07-24):
    every banner row appends armedExtraCells for armed segments its own
    filter missed, and StageBanner falls back to ArmedOnlyRow (not the
    empty placeholder) while anything is armed."""
    source = (UI / "components" / "stagebanner.js").read_text(encoding="utf-8")
    for row in ("StarRow", "SegmentRow", "ArenaRow", "BowserCourseRow"):
        match = re.search(rf"^function {row}\(.*?^}}", source, re.S | re.M)
        assert match and "armedExtraCells(" in match.group(0), \
            f"{row} no longer unions in armed segments"
    assert "ArmedOnlyRow" in source, "the armed-only fallback row is gone"


def test_icon_picker_is_wired_into_banner_and_editor():
    banner = (UI / "components" / "stagebanner.js").read_text(encoding="utf-8")
    editor = (UI / "components" / "segments.js").read_text(encoding="utf-8")
    picker = (UI / "components" / "iconpicker.js").read_text(encoding="utf-8")
    assert "IconPicker" in banner, "banner cells lost their icon picker"
    assert "IconPicker" in editor, "segment editor lost its icon picker"
    assert "/api/icon" in picker and "/api/icons" in picker


def test_star_row_cannot_scroll_horizontally():
    css = (UI / "index.html").read_text(encoding="utf-8")
    row_rules = re.findall(r"\.starrow\s*\{[^}]*\}", css)
    assert row_rules, "index.html lost its .starrow rules"
    for rule in row_rules:
        assert "overflow-x: auto" not in rule and "overflow: auto" not in rule, (
            ".starrow regrew a horizontal scrollbar — the row must scale to "
            f"fit instead (spec 2026-07-24): {rule[:120]}")
    assert "container-type: inline-size" in css, (
        "the scale-to-fit container queries need a container ancestor")


def _stems_from(registry_name: str) -> list[str]:
    """Values of a {key: "stem"} registry in entities.js."""
    source = (UI / "entities.js").read_text(encoding="utf-8")
    block = re.search(registry_name + r"\s*=\s*\{([^}]*)\}", source)
    assert block, f"entities.js lost {registry_name}"
    return re.findall(r':\s*"([\w-]+)"', block.group(1))


def test_special_and_substitute_stems_all_have_a_real_file():
    """The nine special-stage stems and the four painting-less substitutes
    resolve to files that EXIST.

    tests/test_ui_entities.py asserts on the returned URL string and never
    touches the filesystem, so renaming vanish.png would quietly revert nine
    stages to a plain gold star with a green suite — exactly the bug 6fe0379
    was written to fix (whole-branch review I4, 2026-07-25). The JS onerror
    fallback hides a missing file in playtests; only this test sees it.
    """
    star_icons = UI / "assets" / "star_icons"
    course_icons = UI / "assets" / "course_icons"
    missing = []
    for stem in _stems_from("SPECIAL_COURSE_ICONS") + _stems_from("COURSE_SUBSTITUTE_ICONS"):
        if (star_icons / f"{stem}.png").exists():
            continue
        if list(course_icons.glob(f"{stem}.*")):
            continue
        missing.append(stem)
    assert not missing, f"no art file for {missing}"


def _pixel_size(path):
    """(width, height) from a PNG or WEBP header — the two formats we ship.

    Deliberately raises on anything else instead of returning a pass: a new
    format silently reporting "fine" is how the floor below stops meaning
    anything.
    """
    data = path.read_bytes()
    if path.suffix.lower() == ".png":
        return struct.unpack(">II", data[16:24])
    assert path.suffix.lower() == ".webp", f"unhandled format: {path.name}"
    chunk = data[12:16]
    if chunk == b"VP8X":
        return (int.from_bytes(data[24:27], "little") + 1,
                int.from_bytes(data[27:30], "little") + 1)
    if chunk == b"VP8 ":
        return (int.from_bytes(data[26:28], "little") & 0x3FFF,
                int.from_bytes(data[28:30], "little") & 0x3FFF)
    assert chunk == b"VP8L", f"unhandled webp chunk {chunk!r} in {path.name}"
    bits = int.from_bytes(data[21:25], "little")
    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1


# Portraits render in ~104px picker cells and up to ~174px in the practice
# banner, so a 3x-DPI display asks for ~520px of real art. The set is 500-740px
# and this dict is EMPTY, which is the state worth defending: ttc (64x64) and rr
# (100x100) shipped as thumbnails that visibly blurred next to their neighbours
# until they were re-ripped at 600x600 (audit 2026-07-25). Name an exception
# here only with the same intent — a stopgap someone is going to replace.
KNOWN_LOW_RES_PORTRAITS = {}
MIN_PORTRAIT_PIXELS = 256


def test_course_portraits_are_big_enough_for_the_grid():
    small = {}
    for path in sorted((UI / "assets" / "course_icons").iterdir()):
        width, height = _pixel_size(path)
        if min(width, height) < MIN_PORTRAIT_PIXELS:
            small[path.name] = (width, height)
    assert small == KNOWN_LOW_RES_PORTRAITS, (
        "course portraits below the grid's render size changed: "
        f"{small} vs the known set {KNOWN_LOW_RES_PORTRAITS}")


# The structural half of the 2026-07-26 unification — that art resolution is
# not merely shared but UNREACHABLE anywhere else — lives in
# tests/test_single_source.py, with the server-port rule and the shape for
# adding the next one. It is not repeated here: two copies of a guard is the
# same mistake the guard exists to prevent, one level up.


def test_both_surfaces_that_offer_the_icon_override_share_one_picker():
    """The ✎ is one hook and one modal, not a second implementation: the
    practice banner's cells and the Rank tab's coverage tiles (added
    2026-07-26 — the strip is the only place that lists every entity in a
    scope, so it is the only place to repoint a star you are not standing
    in). Both must come from iconpicker.js."""
    for name in ("stagebanner.js", "rankpage.js"):
        source = strip_comments(
            (UI / "components" / name).read_text(encoding="utf-8"))
        assert "useIconPicking(t)" in source, f"{name} lost the shared hook"
        assert "iconIdentityForKey(" in source, \
            f"{name} hand-builds an /api/icon identity instead of deriving it"
        assert "<${IconPicker}" not in source, \
            f"{name} mounts its own IconPicker instead of using the hook"


def test_every_stem_a_default_registry_names_has_a_file():
    """The seed/category default registries point at real assets.

    A missing file renders a broken img that the onerror hides — invisible in
    a playtest, and now the DEFAULT for 59 seeded definitions rather than one
    hand-picked override (2026-07-26). Same failure the per-star sweep above
    guards, reached through the other two registries.
    """
    icons = UI / "assets" / "star_icons"
    missing = {name: stem
               for registry in ("SEGMENT_SEED_ICONS", "SEGMENT_CATEGORY_ICONS")
               for name, stem in ((registry, s) for s in _stems_from(registry))
               if not (icons / f"{stem}.png").is_file()}
    assert not missing, f"default-art registries name missing files: {missing}"
