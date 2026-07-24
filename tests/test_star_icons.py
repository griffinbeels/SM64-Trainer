# tests/test_star_icons.py
"""Per-star course-icon mode (spec 2026-07-24-star-icon-mode-design).

The star selector can render each main-course star's split-icon art
(ui/assets/star_icons/{prefix}{n}.png) instead of the generic gold star.
These tests pin the three seams that would break silently:

- the asset set: stagebanner.js indexes `{prefix}{slot+1}` blindly, so a
  missing file renders a broken img (the JS onerror fallback hides it, which
  makes the hole INVISIBLE in playtests — only this test shows it);
- the prefix registry: hand-written in stagebanner.js, must cover course ids
  1..15 in catalog order;
- the setting wiring: store.js owns the localStorage key, header.js offers
  the control, stagebanner.js consumes the prop — three files that only
  agree by convention.

Also pins the scale-to-fit contract: the selector row must never regrow a
horizontal scrollbar (the 2026-07-24 UX fix this shipped with).
"""
import re
from pathlib import Path

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


def test_stagebanner_prefix_registry_matches_course_order():
    source = (UI / "components" / "stagebanner.js").read_text(encoding="utf-8")
    match = re.search(r"COURSE_ICON_PREFIXES\s*=\s*\[([^\]]*)\]", source)
    assert match, "stagebanner.js lost its COURSE_ICON_PREFIXES registry"
    listed = re.findall(r'"(\w+)"', match.group(1))
    assert listed == PREFIXES, (
        "COURSE_ICON_PREFIXES disagrees with the course catalog order: "
        f"{listed}")


def test_setting_is_wired_through_store_header_and_banner():
    store = (UI / "store.js").read_text(encoding="utf-8")
    header = (UI / "components" / "header.js").read_text(encoding="utf-8")
    banner = (UI / "components" / "stagebanner.js").read_text(encoding="utf-8")
    assert "sm64.starIcons" in store, "store.js lost the sm64.starIcons key"
    assert "pickStarIcons" in store and "starIcons" in store
    assert "starIcons" in header, "settings drawer lost the star-icon control"
    assert "t.starIcons" in banner, "StarRow no longer reads the icon mode"


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
