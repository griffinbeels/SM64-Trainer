"""Guards the JP/US treatment on the Library target page (librarytarget.js).

ROUND 1 (2026-08-07) superseded the version-badge design this file used to
pin. His ruling, verbatim: "I have US mode enabled, but I see JP entries?
Same for JP (I see US entries). We should have 2 modes: JP (shows only JP
entries), US (shows only US entries)." So the JP/US control is a MODE that
FILTERS now: an entry tagged with the other version disappears, an entry
never annotated with a version shows in both modes (the combined-unless-
annotated rule applied to display), and the per-entry version pill
(`.library-example-version`) is DELETED — with every visible run being the
mode's own version, it had nothing left to annotate.

ROUND 24 (2026-09-02) kept that filter and changed its DEFAULT, because the
filter had become the complaint: "By default, in the Library, we should show
BOTH rank standards combined. (just with an annotation that it's JP or US...)
Right now, information about runners / approaches feels hidden, which is not
the intent." So a fresh page shows every entry of both regions, the control
NARROWS rather than picks, and the annotation round 1 deleted comes back in
the one form he asked for and the one this project trusts for a canonical
shape: the country's own flag, fetched not drawn (`regionflag.js`).

The expectations are DERIVED from the shipped snapshot at test time (this
project's own rule against pinning today's community sheet as an equality),
re-deriving the same predicate `Section::visibleEntries` computes, and
compared against the real rendered DOM in both modes.

Also still guarded here: the no-toggle `ladder_version` chip — a row fitted
from one ROM's times with too few of the other to earn a `ladder_jp`
companion. Since round 1 the chip renders INDEPENDENTLY of the mode toggle
(an approach can mix entry versions while its one ladder is still
single-version-fitted; both facts stay on screen).
"""
import gzip
import json
import shutil
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node not on PATH")

from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from ui_fixture import serve_ui  # noqa: E402
from uilab import driver  # noqa: E402

SNAPSHOT = REPO / "src" / "sm64_events" / "data" / "sheet_library.seed.json.gz"
CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'
# Subdivision groups ship collapsed by default (round 1); entries only render
# inside expanded ones, so the DOM counts below expand everything first.
EXPAND_DIVISIONS = (
    "Array.from(document.querySelectorAll("
    "'.library-section.open .library-division-head')).forEach((head) => "
    "head.getAttribute('aria-expanded') === 'true' || head.click())")
# Every rendered entry in the open section, cards and plain rows both.
COUNT_VISIBLE = (
    "document.querySelectorAll('.library-section.open .library-example').length"
    " + document.querySelectorAll('.library-section.open .library-plain-entry').length")


def _load_payload():
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _mode_count(entries, mode):
    """The SAME predicate `librarytarget.js::Section` computes as
    `visibleEntries` -- re-derived here so the expectation comes from TODAY's
    snapshot, never a number copied out of a past run."""
    entries = entries or []
    return sum(1 for e in entries
               if not e.get("version") or e.get("version") == mode)


def _mixes(entries):
    return len({e.get("version") for e in (entries or []) if e.get("version")}) > 1


def _unique_target_labels(payload):
    """(group, label) pairs that name exactly ONE target — a duplicate-labelled
    target (BBH's two Ghost Hunts) can make the textContent-matched click land
    on the wrong page."""
    counts = {}
    for target in payload["targets"]:
        key = (target["group"], target["label"])
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count == 1}


def _pick_worst_mixed_approach(payload):
    """The approach whose two mode counts DIFFER the most -- the clearest
    real demonstration that the filter actually filters, found by scanning
    rather than named by hand."""
    unique = _unique_target_labels(payload)
    best = None
    for target in payload["targets"]:
        if (target["group"], target["label"]) not in unique:
            continue
        approaches = target.get("approaches") or []
        names = [approach["name"] for approach in approaches]
        for approach in approaches:
            if names.count(approach["name"]) > 1:
                continue  # ambiguous section click
            entries = approach.get("entries")
            if not _mixes(entries):
                continue
            us_count, jp_count = _mode_count(entries, "us"), _mode_count(entries, "jp")
            spread = abs(us_count - jp_count)
            if best is None or spread > best["spread"]:
                best = {"group": target["group"], "target_label": target["label"],
                        "approach_name": approach["name"], "spread": spread,
                        "us": us_count, "jp": jp_count,
                        "total": len(entries or []),
                        # Round 24: the per-entry flags this approach should
                        # draw, one "JP"/"US" per TAGGED entry (an untagged
                        # entry is real in both regions and wears none).
                        "tags": [e["version"].upper() for e in (entries or [])
                                 if e.get("version")]}
    return best


def _pick_jp_only_chip_approach(payload):
    """An approach with a `ladder_version` but no `ladder_jp` companion.
    Prefers one that ALSO mixes entry versions, so the toggle-plus-chip
    coexistence (round 1's own new rule) is what gets exercised."""
    unique = _unique_target_labels(payload)
    best, best_key = None, (-1, -1)
    for target in payload["targets"]:
        if (target["group"], target["label"]) not in unique:
            continue
        approaches = target.get("approaches") or []
        names = [approach["name"] for approach in approaches]
        for approach in approaches:
            if names.count(approach["name"]) > 1:
                continue
            if approach.get("ladder_version") and not approach.get("ladder_jp"):
                entries = approach.get("entries") or []
                key = (1 if _mixes(entries) else 0, len(entries))
                if key > best_key:
                    best_key = key
                    best = {"group": target["group"], "target_label": target["label"],
                            "approach_name": approach["name"],
                            "ladder_version": approach["ladder_version"],
                            "mixes": _mixes(entries)}
    return best


# Scoped to `.library-page`, never a bare `.entity-grid`: this tab and Compare
# both stay mounted with `display:none` while you are elsewhere, and the
# recorder's parent dialog draws the SAME picker component, so an unscoped
# query can answer with a grid belonging to another surface entirely
# (2026-08-09 -- `.claude/rules/ui-core.md`'s own norm, written after that swap
# read as the recorder refusing to close). `getClientRects()` rather than mere
# presence, for the same reason: a hidden tab's grid is still in the DOM.
LIBRARY_LEVEL = """
(() => {
  const visible = (el) => !!el && el.getClientRects().length > 0;
  const back = document.querySelector('.library-page .entity-back');
  const cells = [...document.querySelectorAll(
    '.library-page .entity-grid button')];
  return {back: visible(back), cells: cells.some(visible)};
})()
"""


def _to_course_grid(page):
    """Leave the Library showing its top-level COURSE grid.

    Clicking the tab does not land here. The Library auto-opens onto the
    last-practiced target, and that lands about 25ms AFTER `.library-page`
    first renders -- measured 12 runs out of 12, back button never once
    present at the moment this helper used to look for it. So the one-shot
    `if (back) back.click()` fired on arrival was ALWAYS a no-op, and whether
    the wait after it found any grid buttons was a race against the auto-open
    replacing them. It won that race most of the time, which is exactly why
    this read as flakiness rather than as a bug: reproduced at 2 runs in 4,
    each time as a 15s timeout on `.entity-grid button`, and it took down two
    different tests across two release builds.

    Click back until there is nothing left to go back from, and require the
    grid to survive a second look 60ms later -- longer than the auto-open
    window, so a late one is caught rather than raced. Waiting for the
    auto-open instead would pin this helper to a fixture that happens to seed
    a last-practiced target.
    """
    page.wait_for(".library-page", timeout_ms=15000)
    deadline = time.monotonic() + 15
    state = None
    settled = 0
    while time.monotonic() < deadline:
        state = page.evaluate(LIBRARY_LEVEL)
        if state["back"]:
            page.evaluate(
                "document.querySelector('.library-page .entity-back').click()")
            settled = 0
        elif state["cells"]:
            settled += 1
            if settled >= 2:
                return
        page.wait_ms(60)
    raise AssertionError(
        f"the Library never settled on its course grid; last saw {state!r}")


def _navigate_to_section(page, group_name, target_name, section_name):
    """Course grid -> a group's target grid -> the named target -> the named
    section, opened."""
    page.evaluate(CLICK_LIBRARY_TAB)
    _to_course_grid(page)

    for label, name in (("group", group_name), ("target", target_name)):
        result = page.evaluate(f"""
          (() => {{
            const cell = Array.from(document.querySelectorAll(
              '.library-page .entity-grid button'))
              .find((el) => el.textContent.includes({name!r}));
            if (!cell) return 'no {label} cell for ' + {name!r};
            cell.click();
            return 'clicked';
          }})()
        """)
        assert result == "clicked", result
        if label == "group":
            page.wait_for(".library-page .entity-grid button", timeout_ms=15000)
    page.wait_for(".library-target .library-section", timeout_ms=15000)

    section_result = page.evaluate(f"""
      (() => {{
        const heads = Array.from(document.querySelectorAll('.library-section-head'));
        const head = heads.find((h) =>
          h.querySelector('.library-section-name').textContent === {section_name!r});
        if (!head) return 'no section named ' + {section_name!r};
        // Round 3: clicking an OPEN section's head now CLOSES it (the
        // everything-collapsible rule) -- only click when it is closed.
        if (!head.closest('.library-section').classList.contains('open')) head.click();
        return 'clicked';
      }})()
    """)
    assert section_result == "clicked", section_result
    page.wait_for(".library-section.open .library-band", timeout_ms=15000)


@pytest.fixture(scope="module")
def payload():
    return _load_payload()


@pytest.fixture(scope="module")
def library_server():
    with serve_ui() as base:
        yield base


@pytest.fixture
def fresh_page(library_server):
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        yield page


TOGGLE = ("Array.from(document.querySelectorAll('.version-switch-seg'))"
          ".find((seg) => seg.getAttribute('aria-label') === '{}').click()")
# Every rendered entry's region flag in the open section, by the region its
# `alt` names -- the annotation round 24 asked for, read off the real DOM.
FLAG_ALTS = ("Array.from(document.querySelectorAll("
             "'.library-section.open .library-entry-flag'))"
             ".map((img) => img.getAttribute('alt'))")


def test_both_regions_show_by_default_and_the_control_narrows(payload, fresh_page):
    candidate = _pick_worst_mixed_approach(payload)
    assert candidate, "no approach in the shipped snapshot mixes JP and US entries"
    # Anti-vacuity: the filter must actually REMOVE something in each mode,
    # or a broken filter that shows everything passes both counts.
    assert candidate["us"] < candidate["total"], candidate
    assert candidate["jp"] < candidate["total"], candidate

    _navigate_to_section(fresh_page, candidate["group"], candidate["target_label"],
                         candidate["approach_name"])
    fresh_page.wait_for(".version-switch", timeout_ms=10000)
    fresh_page.evaluate(EXPAND_DIVISIONS)
    assert fresh_page.evaluate("!document.querySelector('.library-jp-toggle')"), (
        "the retired per-section chip is still rendering")

    # Round 24's default: BOTH segments on, and every entry of either region
    # on screen. This is the assertion the whole round exists for.
    pressed = fresh_page.evaluate(
        "Array.from(document.querySelectorAll('.version-switch-seg'))"
        ".map((seg) => seg.getAttribute('aria-pressed'))")
    assert pressed == ["true", "true"], pressed
    both_shown = fresh_page.evaluate(COUNT_VISIBLE)
    assert both_shown == candidate["total"], (
        f"the default view shows {both_shown} entries; the snapshot says this "
        f"approach has {candidate['total']} on {candidate['approach_name']!r}")

    # ...and every TAGGED one wears its own region's flag. Compared against
    # the snapshot's own tags rather than a count, so the annotation is proved
    # to follow the data instead of merely existing.
    alts = fresh_page.evaluate(FLAG_ALTS)
    assert sorted(alts) == sorted(candidate["tags"]), (
        f"the rendered region flags {sorted(alts)} do not match the snapshot's "
        f"own tags {sorted(candidate['tags'])}")

    fresh_page.evaluate(TOGGLE.format("JP"))          # US only
    fresh_page.evaluate(EXPAND_DIVISIONS)  # a fresh band list mounts collapsed again
    us_shown = fresh_page.evaluate(COUNT_VISIBLE)
    assert us_shown == candidate["us"], (
        f"US-only shows {us_shown} entries; the snapshot says "
        f"{candidate['us']} are US-or-untagged on {candidate['approach_name']!r}")

    fresh_page.evaluate(TOGGLE.format("JP"))          # both
    fresh_page.evaluate(TOGGLE.format("US"))          # JP only
    fresh_page.evaluate(EXPAND_DIVISIONS)
    jp_pressed = fresh_page.evaluate(
        "document.querySelectorAll('.version-switch-seg')[0].getAttribute('aria-pressed')")
    assert jp_pressed == "true", jp_pressed
    jp_shown = fresh_page.evaluate(COUNT_VISIBLE)
    assert jp_shown == candidate["jp"], (
        f"JP-only shows {jp_shown} entries; the snapshot says "
        f"{candidate['jp']} are JP-or-untagged on {candidate['approach_name']!r}")

    # Round 1's per-entry version PILL stays deleted -- round 24 brought the
    # annotation back as a flag on the runner's name, never as that chip.
    assert fresh_page.evaluate(
        "document.querySelectorAll('.library-example-version').length") == 0


def test_a_jp_only_ladder_wears_its_chip_beside_the_version_switch(payload, fresh_page):
    candidate = _pick_jp_only_chip_approach(payload)
    assert candidate, "no single-version-fitted (no ladder_jp) approach in the snapshot"

    _navigate_to_section(fresh_page, candidate["group"], candidate["target_label"],
                         candidate["approach_name"])

    chip = fresh_page.evaluate(
        "(() => { const el = document.querySelector("
        "'.library-section.open .library-ladder-version-chip'); "
        "return el ? el.textContent.trim() : null; })()")
    # Round 24: the region is the flag inside the chip, so the chip's own
    # text is just the noun; the region is read off the flag's `alt`.
    assert chip == "ladder only", chip
    flag_alt = fresh_page.evaluate(
        "(() => { const img = document.querySelector("
        "'.library-section.open .library-ladder-version-chip img.region-flag'); "
        "return img ? img.getAttribute('alt') : null; })()")
    assert flag_alt == ("JP" if candidate["ladder_version"] == "jp" else "US"), flag_alt

    # Round 1's coexistence rule ("mixed entries earn the mode toggle even
    # with no second ladder to switch to") is now structural rather than
    # conditional: his 2026-08-15 ruling made the switch PAGE-level and
    # unconditional, so what this guards is that it still coexists with the
    # ladder-version chip rather than one hiding the other -- never that it
    # appears only when this one approach mixes versions.
    assert fresh_page.evaluate("!!document.querySelector('.version-switch')"), (
        "the page-level version switch is missing beside the ladder-version chip")
