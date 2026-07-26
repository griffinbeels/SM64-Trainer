"""ui/components/caps.js is THE tier registry: name, colour, treatment.

Two regressions have real precedent. Colour: Iron shipped at #8a8a8a and read
as a dim Silver at 24px (live report 2026-07-25) -- and the pair that failed
scored 168 on the redmean distance used here, so the floor is set above it.
The check is over EVERY pair, not adjacent ones: the Rank tab's ladder bar
renders all nine tiers' tints side by side (`ui/components/rankpage.js`'s
LadderBar) and the chart draws a dot per tier, so any two can end up next to
each other. Order: the JS key order IS the ladder, and a reorder would
silently mis-rank every entity.
"""
import json
import re
import shutil
import subprocess
from math import sqrt
from pathlib import Path

import pytest

from sm64_events.ranks.classify import RANK_NAMES
from tests.source_scan import strip_comments

CAPS_JS = Path(__file__).resolve().parents[1] / "src" / "sm64_events" / "ui" / "components" / "caps.js"
HAT_JS = CAPS_JS.parent / "hat.js"
UI_DIR = CAPS_JS.parents[1]
RANKICON_JS = CAPS_JS.parent / "rankicon.js"


def run_node(imports: str, body: str):
    """Execute caps.js for real -- it is import-free specifically so node can
    unit-test it (caps.js:14), the same convention as ui/entities.js
    (tests/test_ui_entities.py)."""
    script = f"import {{ {imports} }} from {CAPS_JS.as_uri()!r};\n{body}"
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)

# Anything at or below this failed in production; the palette must clear it
# with margin. Raising it is a decision, not a cleanup.
MIN_SEPARATION = 185.0


def _cap_table() -> dict[str, dict]:
    """{tier: {"color": hex, "pattern_color": hex|None}} in declaration
    order, comments stripped."""
    source = strip_comments(CAPS_JS.read_text(encoding="utf-8"))
    block = re.search(r"export const CAP = \{(.*?)\n\};", source, re.S)
    assert block, "CAP table not found in caps.js -- did it move or get renamed?"
    entries = re.findall(r"(\w+):\s*\{([^}]*)\}", block.group(1), re.S)
    assert entries, "CAP parsed to nothing -- the entry shape changed"
    table = {}
    for name, body in entries:
        color = re.search(r'color:\s*"(#[0-9a-fA-F]{6})"', body)
        pattern_color = re.search(r'patternColor:\s*"(#[0-9a-fA-F]{6})"', body)
        assert color, f"{name} has no color -- the entry shape changed"
        table[name] = {"color": color.group(1),
                        "pattern_color": pattern_color.group(1) if pattern_color else None}
    return table


def _channels(hex_color):
    return [int(hex_color[index:index + 2], 16) for index in (1, 3, 5)]


def redmean(first, second):
    """Cheap perceptual distance. Weights green heaviest and red by level,
    which is why it catches two light neutrals that plain RGB distance calls
    far apart."""
    r1, g1, b1 = _channels(first)
    r2, g2, b2 = _channels(second)
    mean_red = (r1 + r2) / 2
    dr, dg, db = r1 - r2, g1 - g2, b1 - b2
    return sqrt((2 + mean_red / 256) * dr * dr + 4 * dg * dg
                + (2 + (255 - mean_red) / 256) * db * db)


def combined_distance(first_color, second_color, first_pattern=None, second_pattern=None):
    """The palette guard's distance for one pair, pattern-aware (addendum 2,
    2026-07-25 / whole-branch review M2). Comparing base colour ALONE
    measures an icon that the two patterned tiers (Toadsworth, Toad) do not
    actually render -- both are TWO-TONE, a base cap plus contrasting spots.
    Where BOTH tiers in a pair carry a pattern, base and pattern colour are
    two INDEPENDENT distinguishing signals, combined in quadrature (root of
    the sum of squares) rather than requiring either alone to clear the
    floor: concretely, Toad #efe9e2/#e0453f vs Toadsworth #dad68c/#7a4f2a
    score 135.2 on base and 171.1 on pattern -- neither alone above 185, but
    combined 218.0, MORE separable than a single pair of flat fills at the
    190 the un-patterned tiers sit comfortably above.

    A pair where only one (or neither) side has a pattern -- or where the
    pattern colours happen to MATCH -- collapses back to base distance
    alone, so this can never wave a pair through merely for having spots
    (probed in test_the_guard_can_still_fail)."""
    base_distance = redmean(first_color, second_color)
    if first_pattern and second_pattern:
        pattern_distance = redmean(first_pattern, second_pattern)
        return sqrt(base_distance ** 2 + pattern_distance ** 2)
    return base_distance


def test_registry_covers_every_tier_in_ladder_order():
    assert list(_cap_table()) == list(RANK_NAMES)


def test_every_pair_of_tiers_is_visually_distinct():
    table = _cap_table()
    tiers = list(table)
    for index, first in enumerate(tiers):
        for second in tiers[index + 1:]:
            distance = combined_distance(
                table[first]["color"], table[second]["color"],
                table[first]["pattern_color"], table[second]["pattern_color"])
            assert distance >= MIN_SEPARATION, (
                f"{first} {table[first]['color']} and {second} {table[second]['color']} "
                f"are only {distance:.1f} apart; the Iron/Silver pair that shipped as a "
                f"bug scored 168")


def test_the_guard_can_still_fail():
    """A guard that cannot fail is not one (tests/source_scan.py)."""
    assert redmean("#8a8a8a", "#c2c2c2") < MIN_SEPARATION   # the shipped bug
    assert redmean("#f5f7f8", "#eeeae4") < MIN_SEPARATION   # white vs off-white
    assert redmean("#e23b3b", "#3dc05c") > MIN_SEPARATION    # red vs green

    # Un-patterned path (either side missing a pattern) still uses base alone.
    assert combined_distance("#8a8a8a", "#c2c2c2") < MIN_SEPARATION

    # Patterned pair: base alone falls short but base+pattern combined clears
    # the floor -- the real Toad/Toadsworth pair, addendum 2, 2026-07-25.
    assert redmean("#efe9e2", "#dad68c") < MIN_SEPARATION
    assert redmean("#e0453f", "#7a4f2a") < MIN_SEPARATION
    assert combined_distance("#efe9e2", "#dad68c", "#e0453f", "#7a4f2a") >= MIN_SEPARATION

    # A patterned pair must NOT be waved through merely for carrying a
    # pattern: matching spot colours carry no distinguishing signal, so the
    # combination collapses to base alone -- which must still reject a base
    # too close to another tier's (the same shipped-bug pair, patterned).
    assert combined_distance("#8a8a8a", "#c2c2c2", "#e0453f", "#e0453f") < MIN_SEPARATION


def test_the_mask_and_the_shade_come_from_one_sprite():
    """Measured 2026-07-25: the tint is exact and backdrop-independent ONLY
    because the masked colour layer and the multiplied greyscale layer read the
    same PNG. Both rules must therefore resolve their art from the SAME custom
    property, so a call site cannot hand them different files."""
    css = strip_comments((CAPS_JS.parents[1] / "index.html").read_text(encoding="utf-8"))
    fill = re.search(r"\.hat \.fill\s*\{(.*?)\}", css, re.S)
    shade = re.search(r"\.hat \.shade\s*\{(.*?)\}", css, re.S)
    assert fill and shade, "the .hat .fill / .hat .shade rules are missing"
    assert "var(--art)" in fill.group(1) and "var(--art)" in shade.group(1), (
        "both layers must take their art from --art; two sources let the "
        "page backdrop leak into the multiply")
    assert "mix-blend-mode: multiply" in shade.group(1)


def test_the_glyph_rule_outranks_the_layer_rule():
    """`.hat i { inset: 0; display: block }` is class+element and beats a bare
    `.glyph` class, which silently parked the numeral outside the cap twice
    during design. The glyph rule needs two classes."""
    css = strip_comments((CAPS_JS.parents[1] / "index.html").read_text(encoding="utf-8"))
    assert ".hat .glyph" in css, "the glyph rule must be .hat .glyph, not .glyph"
    assert re.search(r"\.glyph\s*\{[^}]*inset:\s*auto[^}]*left:", css, re.S), (
        "inset is the shorthand for top/right/bottom/left -- declaring it AFTER "
        "left/top resets them; it must come first")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_division_five_wears_no_wings_and_division_one_wears_four():
    """M1 (final review, 2026-07-25): the old version of this test asserted
    only `"5 - digit" in source`, a substring that stayed green no matter
    what WING_TIERS or the clamp actually computed -- it could not tell 4
    wings from 2, and never touched division I at all. caps.js is
    import-free specifically so a guard like this one can execute the real
    function instead of reading its text."""
    wings = run_node("wingTiers", "console.log(JSON.stringify("
                     '["V", "IV", "III", "II", "I"].map((numeral) => '
                     'wingTiers("Mario", numeral))));')
    assert wings == [0, 1, 2, 3, 4], (
        "wingTiers must map division V (bottom of a tier) to 0 wings and "
        "climb one wing per division up to I (all four)")


@pytest.mark.skipif(shutil.which("node") is None, reason="node not on PATH")
def test_capless_never_wears_wings_at_any_division():
    """Correction (addendum, task 8, 2026-07-26): Capless (Iron) is the one
    tier that never wears wings, at any division -- Capless means you have no
    cap, and wings are a thing a cap earns. This must hold from caps.js's
    OWN wingTiers alone (not a special case added in hat.js/medal.js/
    celebrate.js) -- isolated here so the policy can change without a
    renderer knowing."""
    wings = run_node("wingTiers", "console.log(JSON.stringify("
                     '["V", "IV", "III", "II", "I"].map((numeral) => '
                     'wingTiers("Iron", numeral))));')
    assert wings == [0, 0, 0, 0, 0], (
        "Iron (Capless) must wear zero wings at every division -- Capless "
        "means you have no cap, and wings are a thing a cap earns")


def _tinted_pair_problems(source: str) -> list:
    """Everything wrong with hat.js's fill/shade emission, or [] if the
    tinted-pair invariant (final review I4, 2026-07-25) holds structurally:
    every `.fill`/`.shade` pair must be built by ONE helper that resolves
    art(stem) exactly once and reuses it for both layers, and that helper
    must be the ONLY place in the file a `.shade` layer is ever constructed
    -- a hand-written pair (or a lone hand-written `.shade`) elsewhere could
    read a different --art file than its sibling with nothing to catch it.
    Factored out (rather than written inline in the test) so
    test_the_guard_can_still_fail_on_a_hand_written_shade can run it against
    synthetic BAD source, not just trust the real file never regresses."""
    code = strip_comments(source)
    problems = []
    helper = re.search(r"function tintedPair\([^)]*\)\s*\{(.*?)\n\}", code, re.S)
    if not helper:
        problems.append("no tintedPair(...) helper found")
        return problems
    body = helper.group(1)
    art_calls = re.findall(r"\bart\(", body)
    if len(art_calls) != 1:
        problems.append(
            f"tintedPair calls art() {len(art_calls)} times, not once -- it "
            "must resolve art(stem) ONCE and reuse the result for both layers")
    else:
        bound = re.search(r"const (\w+)\s*=\s*art\(", body)
        if not bound:
            problems.append("tintedPair does not bind art(stem) to a local before using it")
        elif body.count(f"--art:${{{bound.group(1)}}}") != 2:
            problems.append(
                f"the fill and shade layers do not both interpolate the same "
                f"resolved {bound.group(1)}")
    # Excludes "spot-shade", a deliberately different layer (spots tint from
    # the CAP's own greyscale, not their own) -- see spec.pattern below.
    shade_sites = len(re.findall(r"(?<!spot-)\bshade\b", code))
    if shade_sites != 1:
        problems.append(
            f"found {shade_sites} places a .shade layer is built, not one -- "
            "a layer built outside tintedPair can read a different --art "
            "than its sibling .fill")
    return problems


def test_the_mask_and_the_shade_are_built_from_one_helper_call():
    """I4 (final review, 2026-07-25): the CSS-text check above
    (test_the_mask_and_the_shade_come_from_one_sprite) cannot see a JS-side
    divergence -- hat.js used to set --art independently on the sibling
    .fill and .shade elements of every tinted pair (the main cap AND each
    wing side), so changing one art() call and not its twin broke the tint
    with the whole suite green."""
    assert _tinted_pair_problems(HAT_JS.read_text(encoding="utf-8")) == []


def test_the_guard_can_still_fail_on_a_hand_written_shade():
    """A guard that cannot fail is not one (tests/source_scan.py) -- probe
    _tinted_pair_problems in both directions against synthetic source."""
    good = """
function tintedPair(stem, color) {
  const artUrl = art(stem);
  return [
    html`<i class=${withSide("fill")} style=${`--art:${artUrl}`}></i>`,
    html`<i class=${withSide("shade")} style=${`--art:${artUrl}`}></i>`,
  ];
}
"""
    assert _tinted_pair_problems(good) == []

    # Regression shape 1: the pair resolves art() TWICE instead of sharing
    # one call -- the exact bug this guard exists to catch.
    two_calls = """
function tintedPair(stem, color) {
  return [
    html`<i class=${withSide("fill")} style=${`--art:${art(stem)}`}></i>`,
    html`<i class=${withSide("shade")} style=${`--art:${art(stem)}`}></i>`,
  ];
}
"""
    assert _tinted_pair_problems(two_calls) != []

    # Regression shape 2: a SECOND .shade layer built outside the helper
    # (e.g. a future treatment hand-rolling its own greyscale multiply).
    extra_shade = good + '\nlayers.push(html`<i class="shade" style=${`--art:${art("cap_outline")}`}></i>`);\n'
    assert _tinted_pair_problems(extra_shade) != []


# --- Task 8, 2026-07-25-mario-cap-rank-icons: the rank-icon STYLE registry --
# rankicon.js::ICON_STYLES is what makes "adding a style touches no call
# site" true; both guards below are what keeps that claim actually true
# rather than aspirational prose.

def _icon_styles_from_source(source: str) -> dict:
    """{key: {"label": bool, "render": bool}} in declaration order, comments
    stripped -- same convention as _cap_table() above, and for the same
    reason: a real ICON_STYLES parse can tell a missing renderer from a
    present one, a substring check cannot."""
    code = strip_comments(source)
    block = re.search(r"export const ICON_STYLES = \{(.*?)\n\};", code, re.S)
    if not block:
        return {}
    entries = re.findall(r"(\w+):\s*\{([^}]*)\}", block.group(1), re.S)
    return {name: {"label": bool(re.search(r'label:\s*["\']', body)),
                   "render": bool(re.search(r"render:\s*\w+", body))}
            for name, body in entries}


def test_hat_is_first_and_every_icon_style_has_a_renderer():
    """hat's default-ness is load-bearing (the user: "Hats as default"), and
    an ICON_STYLES entry with no renderer would crash RankIcon the instant a
    caller (or the settings control) picks it."""
    styles = _icon_styles_from_source(RANKICON_JS.read_text(encoding="utf-8"))
    assert styles, "ICON_STYLES not found in rankicon.js -- did it move or get renamed?"
    keys = list(styles)
    assert keys[0] == "hat", (
        f"hat must be FIRST in ICON_STYLES (found order {keys}) -- header.js's "
        "settings <select> and RankIcon's own default both depend on it")
    for key, entry in styles.items():
        assert entry["render"], f"ICON_STYLES.{key} has no render function"
        assert entry["label"], f"ICON_STYLES.{key} has no label"


def test_the_icon_styles_guard_can_still_fail():
    """A guard that cannot fail is not one (tests/source_scan.py)."""
    good = ('export const ICON_STYLES = {\n'
            '  hat: { label: "Mario caps", render: Hat },\n'
            '  medal: { label: "Medals", render: Medal },\n'
            '};')
    styles = _icon_styles_from_source(good)
    assert list(styles) == ["hat", "medal"]
    assert all(entry["render"] and entry["label"] for entry in styles.values())

    # Regression shape 1: hat is not first.
    reordered = ('export const ICON_STYLES = {\n'
                 '  medal: { label: "Medals", render: Medal },\n'
                 '  hat: { label: "Mario caps", render: Hat },\n'
                 '};')
    assert list(_icon_styles_from_source(reordered))[0] != "hat"

    # Regression shape 2: an entry missing its renderer entirely.
    no_render = ('export const ICON_STYLES = {\n'
                 '  hat: { label: "Mario caps", render: Hat },\n'
                 '  medal: { label: "Medals" },\n'
                 '};')
    assert not _icon_styles_from_source(no_render)["medal"]["render"]

    # Comment-only mention must not be mistaken for a real entry.
    assert _icon_styles_from_source(
        "// medal: { label: \"Medals\", render: Medal },\n") == {}


# Only rankicon.js may import a style's own renderer module -- every other
# file must go through RankIcon, which is what keeps "a fifth style touches
# no call site" true rather than aspirational.
STYLE_RENDERER_FILES = ("hat.js", "medal.js")
ALLOWED_STYLE_IMPORTERS = {"components/rankicon.js"}


def style_renderer_import_offenders(source: str) -> list:
    code = strip_comments(source)
    return [name for name in STYLE_RENDERER_FILES
            if re.search(rf'from\s+["\']\./{re.escape(name)}["\']', code)]


def test_no_call_site_imports_a_style_renderer_directly():
    for path in sorted(UI_DIR.rglob("*.js")):
        relative = path.relative_to(UI_DIR).as_posix()
        if relative in ALLOWED_STYLE_IMPORTERS:
            continue
        offenders = style_renderer_import_offenders(path.read_text(encoding="utf-8"))
        assert not offenders, (
            f"{relative}: imports {offenders} directly -- route it through "
            "RankIcon (components/rankicon.js) so a new style never touches "
            "a call site")


def test_the_style_import_guard_can_still_fail():
    # Comment-only: a note naming the old import by example must stay green.
    assert style_renderer_import_offenders(
        '// used to import { Hat } from "./hat.js"\n') == []

    # Real code: a direct import of either renderer is caught.
    assert style_renderer_import_offenders(
        'import { Hat } from "./hat.js";') == ["hat.js"]
    assert style_renderer_import_offenders(
        'import { Medal } from "./medal.js";') == ["medal.js"]

    # The dispatcher import is not an offense.
    assert style_renderer_import_offenders(
        'import { RankIcon } from "./rankicon.js";') == []
