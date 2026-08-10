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
                        "total": len(entries or [])}
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


def _navigate_to_section(page, group_name, target_name, section_name):
    """Course grid -> a group's target grid -> the named target -> the named
    section, opened."""
    page.evaluate(CLICK_LIBRARY_TAB)
    page.wait_for(".library-page", timeout_ms=15000)
    # Scoped to `.library-page`, never a bare `.entity-grid`: this tab and
    # Compare both stay mounted with `display:none` while you are elsewhere,
    # and the recorder's parent dialog draws the SAME picker component, so an
    # unscoped query can answer with a grid belonging to another surface
    # entirely (2026-08-09 -- `.claude/rules/ui-core.md`'s own norm, written
    # after that swap read as the recorder refusing to close).
    page.evaluate(
        "(() => { const b = document.querySelector('.library-page .entity-back'); "
        "if (b) b.click(); })()")
    page.wait_for(".library-page .entity-grid button", timeout_ms=15000)

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


def test_each_mode_shows_only_its_own_versions_entries(payload, fresh_page):
    candidate = _pick_worst_mixed_approach(payload)
    assert candidate, "no approach in the shipped snapshot mixes JP and US entries"
    # Anti-vacuity: the filter must actually REMOVE something in each mode,
    # or a broken filter that shows everything passes both counts.
    assert candidate["us"] < candidate["total"], candidate
    assert candidate["jp"] < candidate["total"], candidate

    _navigate_to_section(fresh_page, candidate["group"], candidate["target_label"],
                         candidate["approach_name"])
    fresh_page.wait_for(".library-section.open .library-jp-toggle", timeout_ms=10000)
    fresh_page.evaluate(EXPAND_DIVISIONS)

    label = fresh_page.evaluate(
        "document.querySelector('.library-section.open .library-jp-toggle')"
        ".textContent.trim()")
    assert label.startswith("US mode"), label
    us_shown = fresh_page.evaluate(COUNT_VISIBLE)
    assert us_shown == candidate["us"], (
        f"US mode shows {us_shown} entries; the snapshot says "
        f"{candidate['us']} are US-or-untagged on {candidate['approach_name']!r}")

    fresh_page.evaluate(
        "document.querySelector('.library-section.open .library-jp-toggle').click()")
    fresh_page.evaluate(EXPAND_DIVISIONS)  # a fresh band list mounts collapsed again
    label_after = fresh_page.evaluate(
        "document.querySelector('.library-section.open .library-jp-toggle')"
        ".textContent.trim()")
    assert label_after.startswith("JP mode"), label_after
    jp_shown = fresh_page.evaluate(COUNT_VISIBLE)
    assert jp_shown == candidate["jp"], (
        f"JP mode shows {jp_shown} entries; the snapshot says "
        f"{candidate['jp']} are JP-or-untagged on {candidate['approach_name']!r}")

    # The pill is gone WITH the mixed display, not merely restyled.
    assert fresh_page.evaluate(
        "document.querySelectorAll('.library-example-version').length") == 0


def test_a_jp_only_ladder_wears_its_chip_beside_the_mode_toggle(payload, fresh_page):
    candidate = _pick_jp_only_chip_approach(payload)
    assert candidate, "no single-version-fitted (no ladder_jp) approach in the snapshot"

    _navigate_to_section(fresh_page, candidate["group"], candidate["target_label"],
                         candidate["approach_name"])

    chip = fresh_page.evaluate(
        "(() => { const el = document.querySelector("
        "'.library-section.open .library-ladder-version-chip'); "
        "return el ? el.textContent.trim() : null; })()")
    expected_chip = ("JP" if candidate["ladder_version"] == "jp" else "US") + " ladder only"
    assert chip == expected_chip, chip

    # Round 1's coexistence rule: mixed entries earn the mode toggle even
    # with no second ladder to switch to, and the chip stays beside it.
    toggle = fresh_page.evaluate(
        "!!document.querySelector('.library-section.open .library-jp-toggle')")
    assert toggle == candidate["mixes"], (toggle, candidate)
