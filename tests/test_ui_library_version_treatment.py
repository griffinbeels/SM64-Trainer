"""Guards the JP/US treatment on the Library target page (librarytarget.js) —
the per-entry version badge (`.library-example-version`) and the no-toggle
`ladder_version` chip (`.library-ladder-version-chip`). Final whole-branch
review, HIGH finding: entries were banded against a ladder fitted from the
OTHER ROM version with nothing on screen saying so. The fix had no guard at
all until the first round of this file (`grep library-example-version
tests/` found zero hits) — the most user-visible change in the round,
protected only by a one-off render check that would not survive the next
person who touches `bandsOf`, `Section` or `ExampleCard`.

ROUND 2 (team-lead ruling): the badge is now a COLUMN, not an exception
marker. It no longer compares an entry's version against whichever ladder
happens to be showing (round 1 measured that at 108-of-112 badged in the
worst real band — an exception marker on 96% of a band reads as the section
being mislabelled, and the set inverted every time the JP/US toggle flipped).
`Section::mixedVersions` now decides per APPROACH: whenever an approach's own
entries carry BOTH ROM versions, every entry with a version wears its own,
always.

ROUND 2 ALSO fixes this file's own first-round mistake: pinning `mario_cards
== 112` / `len(badged) == 108` against TODAY's community spreadsheet, which
this project's own rule says never to do —

    Measured 2026-08-04 and asserted as FLOORS ... never as equalities —
    the sheet gains rows daily and an exact count would go red for the
    community doing exactly what we want. (.claude/rules/library.md)

So the expectation is now DERIVED from the shipped snapshot at test time
(same predicate `Section::mixedVersions`/badge-per-entry computes, re-derived
here in ~4 lines rather than imported, the same shape
`tests/test_log_card_name_fits.py` uses for its own corpus-driven floor) and
compared against the real rendered DOM, with `> 0` as the anti-vacuity floor
that catches a candidate-selection bug or a since-emptied corpus."""
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


def _load_payload():
    with gzip.open(SNAPSHOT, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _badge_count(entries):
    """The SAME predicate `librarytarget.js::Section` computes as
    `mixedVersions` + "every entry with a version badges" -- re-derived here
    so the expectation comes from TODAY's snapshot, never a number copied out
    of a past run."""
    entries = entries or []
    versions_present = {e.get("version") for e in entries if e.get("version")}
    if len(versions_present) <= 1:
        return 0
    return sum(1 for e in entries if e.get("version"))


def _unique_target_labels(payload):
    """(group, label) pairs that name exactly ONE target. The seam review's
    own LOW finding: Big Boo's Haunt opens two targets both labelled "Go on a
    Ghost Hunt" (one JP, one US) -- `_navigate_to_section`'s target click
    matches on `textContent`, so a candidate drawn from a DUPLICATE-labelled
    target can silently click the WRONG one and assert against data that
    never rendered. Restricting candidates to unique labels sidesteps a
    pre-existing bug this file is not the one to fix."""
    counts = {}
    for target in payload["targets"]:
        key = (target["group"], target["label"])
        counts[key] = counts.get(key, 0) + 1
    return {key for key, count in counts.items() if count == 1}


def _pick_worst_mixed_approach(payload):
    """The approach anywhere in the corpus whose badge count is largest --
    the clearest real demonstration available today, found by scanning
    rather than named by hand, so a sheet refresh that renames or retires
    "Blast to the Stone Pillar" cannot silently turn this into a test of a
    target that no longer exists."""
    unique = _unique_target_labels(payload)
    best = None
    for target in payload["targets"]:
        if (target["group"], target["label"]) not in unique:
            continue
        approaches = target.get("approaches") or []
        names = [approach["name"] for approach in approaches]
        for approach in approaches:
            if names.count(approach["name"]) > 1:
                continue  # ambiguous section click -- same reasoning as above
            count = _badge_count(approach.get("entries"))
            if count and (best is None or count > best["expected_badges"]):
                best = {"group": target["group"], "target_label": target["label"],
                        "approach_name": approach["name"], "expected_badges": count}
    return best


def _pick_jp_only_chip_approach(payload):
    """An approach with a `ladder_version` but no `ladder_jp` companion --
    hasJp is false, so no toggle renders and the chip is the only thing that
    can tell a reader which ROM population graded them. Picks the one with
    the most entries among the qualifying rows, for a robust real target."""
    unique = _unique_target_labels(payload)
    # (has-any-badge, entry count) -- a `ladder_version` can be stamped with
    # NO second population at all (row_times sets it for a single explicitly-
    # tagged group too, not only a genuine jp/us split), so "most entries"
    # alone can pick a candidate that never mixes and makes the badge half of
    # this test pass vacuously. Prefer a real mix first.
    best = None
    best_key = (-1, -1)
    for target in payload["targets"]:
        if (target["group"], target["label"]) not in unique:
            continue
        approaches = target.get("approaches") or []
        names = [approach["name"] for approach in approaches]
        for approach in approaches:
            if names.count(approach["name"]) > 1:
                continue  # ambiguous section click -- same reasoning as above
            if approach.get("ladder_version") and not approach.get("ladder_jp"):
                entries = approach.get("entries") or []
                badges = _badge_count(entries)
                key = (1 if badges else 0, len(entries))
                if key > best_key:
                    best_key = key
                    best = {"group": target["group"], "target_label": target["label"],
                            "approach_name": approach["name"],
                            "ladder_version": approach["ladder_version"],
                            "expected_badges": badges}
    return best


def _navigate_to_section(page, group_name, target_name, section_name):
    """Course grid -> a group's target grid -> the named target -> the named
    section, opened. `librarynav.js` renders each cell as a `<button>` with
    no stable class to query by other than its containing `.entity-grid`, so
    this matches on `textContent` the same way `test_ui_library_links.py`'s
    own `CLICK_SECTION_BY_NAME` already does for section heads."""
    page.evaluate(CLICK_LIBRARY_TAB)
    page.wait_for(".library-page", timeout_ms=15000)
    # Auto-open (lastPracticed) may have already landed on a DIFFERENT
    # target's page -- back out to the browse grid unconditionally.
    page.evaluate(
        "(() => { const b = document.querySelector('.entity-back'); "
        "if (b) b.click(); })()")
    page.wait_for(".entity-grid button", timeout_ms=15000)

    group_result = page.evaluate(f"""
      (() => {{
        const cell = Array.from(document.querySelectorAll('.entity-grid button'))
          .find((el) => el.textContent.includes({group_name!r}));
        if (!cell) return 'no group cell for ' + {group_name!r};
        cell.click();
        return 'clicked';
      }})()
    """)
    assert group_result == "clicked", group_result
    page.wait_for(".entity-grid button", timeout_ms=15000)

    target_result = page.evaluate(f"""
      (() => {{
        const cell = Array.from(document.querySelectorAll('.entity-grid button'))
          .find((el) => el.textContent.includes({target_name!r}));
        if (!cell) return 'no target cell for ' + {target_name!r};
        cell.click();
        return 'clicked';
      }})()
    """)
    assert target_result == "clicked", target_result
    page.wait_for(".library-target .library-section", timeout_ms=15000)

    section_result = page.evaluate(f"""
      (() => {{
        const heads = Array.from(document.querySelectorAll('.library-section-head'));
        const head = heads.find((h) =>
          h.querySelector('.library-section-name').textContent === {section_name!r});
        if (!head) return 'no section named ' + {section_name!r};
        head.click();
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
    """A fresh page per test (same isolation reasoning test_ui_library_target.py's
    own `library_page` fixture gives -- a clicked-open section or a scrolled
    band must not leak into whatever runs after it)."""
    with driver.get_driver().launch(headless=True) as page:
        page.goto(f"{library_server}/ui/index.html")
        page.wait_for(".log-list-card", timeout_ms=20000)
        yield page


def test_every_row_in_the_corpus_worst_mixed_section_wears_its_own_badge(payload, fresh_page):
    """"Every row in a mixed section carries its own version" -- checked
    against whichever approach the LIVE snapshot makes the clearest
    demonstration of that today, not a name or count copied out of a past
    run."""
    candidate = _pick_worst_mixed_approach(payload)
    assert candidate, "no approach in the shipped snapshot mixes JP and US entries"
    assert candidate["expected_badges"] > 0, candidate  # anti-vacuity floor

    _navigate_to_section(fresh_page, candidate["group"], candidate["target_label"],
                         candidate["approach_name"])

    badged = fresh_page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.library-section.open .library-example-version')).map((el) => el.textContent.trim())")
    assert len(badged) == candidate["expected_badges"], (
        f"expected {candidate['expected_badges']} badges on "
        f"{candidate['approach_name']!r} (derived from today's snapshot), found "
        f"{len(badged)}: {badged[:10]}")
    assert set(badged) <= {"JP", "US"}, set(badged)

    # And the section-level property itself, not just the count: no entry may
    # sit in a mixed section with a version tag and NO badge.
    unbadged_with_version = fresh_page.evaluate("""
      Array.from(document.querySelectorAll(
        '.library-section.open .library-example')).filter((card) =>
        !card.querySelector('.library-example-version')).length
    """)
    total_cards = fresh_page.evaluate(
        "document.querySelectorAll('.library-section.open .library-example').length")
    assert total_cards - unbadged_with_version == len(badged), (
        "badge count disagrees with itself between two independent queries")


def test_a_jp_only_ladder_wears_an_inert_chip_with_no_toggle(payload, fresh_page):
    """An approach that fits a ladder from only one ROM version's times, with
    too few of the other to earn a `ladder_jp` companion, has no toggle at
    all -- the chip is the ONLY thing on screen that can tell a reader which
    population they are graded against. Picks the corpus's own best real
    example rather than a name copied out of a past review."""
    candidate = _pick_jp_only_chip_approach(payload)
    assert candidate, "no JP-only/US-only (no ladder_jp) approach in the shipped snapshot"
    assert candidate["expected_badges"] > 0, (
        "picked a no-toggle approach that never mixes versions -- the badge "
        f"assertion below would pass vacuously: {candidate}")  # anti-vacuity floor

    _navigate_to_section(fresh_page, candidate["group"], candidate["target_label"],
                         candidate["approach_name"])

    toggle = fresh_page.evaluate(
        "!!document.querySelector('.library-section.open .library-jp-toggle')")
    assert not toggle, "a JP/US toggle rendered where none should -- this approach has no ladder_jp"

    chip = fresh_page.evaluate(
        "(() => { const el = document.querySelector("
        "'.library-section.open .library-ladder-version-chip'); "
        "return el ? el.textContent.trim() : null; })()")
    expected_chip = ("JP" if candidate["ladder_version"] == "jp" else "US") + " ladder only"
    assert chip == expected_chip, chip

    badged = fresh_page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.library-section.open .library-example-version')).map((el) => el.textContent.trim())")
    assert len(badged) == candidate["expected_badges"], (
        candidate["expected_badges"], len(badged), badged)
