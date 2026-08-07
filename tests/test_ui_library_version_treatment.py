"""Guards the JP/US treatment on the Library target page (librarytarget.js) —
the per-entry foreign-version badge (`.library-example-version`) and the
no-toggle `ladder_version` chip (`.library-ladder-version-chip`). Final
whole-branch review, HIGH finding: entries were banded against a ladder
fitted from the OTHER ROM version with nothing on screen saying so. The fix
had no guard at all until now (`grep library-example-version tests/` found
zero hits) — the most user-visible change in the round, protected only by a
one-off render check that does not survive the next person who touches
`bandsOf`, `Section` or `ExampleCard`.

CONTENT, not existence, per this project's own rule (two guards already
shipped on this branch that checked something appeared while it was provably
empty): every assertion below is a COUNT or a NAMED runner's badge text,
driven against the REAL bundled snapshot's two headline cases rather than a
fixture. `tools/verify_foreign_version.mjs`-shaped numbers were re-derived
directly against `librarymodel.js::bandsOf` (real code, no browser) before
writing this file and are pinned in the assertions below:

  "Blast to the Stone Pillar" | "Blast to the Stone Pillar" (the target's own
  headline approach, `ladder_version: "us"`, `hasJp: true`): Mario band = 112
  entries, 108 of them JP (foreign) -- the worst-contaminated band in the
  corpus, and the seam reviewer's own worked example.

  "Big Bob-omb on the Summit" | "Left side strat" (`ladder_version: "jp"`, no
  `ladder_jp` companion -- 31 jp / 8 us, too few US runs to earn a second
  ladder): no JP/US toggle exists at all, so the inert `ladder_version` chip
  is the ONLY thing that can tell a US player they are graded on a JP-only
  ladder.

Both numbers were re-verified against the shipped snapshot the same session
this file was written, not carried over from the review by hand -- see this
file's own sibling probe in the session scratchpad (not committed)."""
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

CLICK_LIBRARY_TAB = 'document.querySelector(\'.nav-item[title="Library"]\').click()'


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


def test_the_worst_contaminated_mario_band_wears_its_badges(fresh_page):
    """"Blast to the Stone Pillar"'s own headline approach -- the seam
    reviewer's worked example (112 in the Mario band, 108 of them JP against
    a US-fitted ladder). If the badge branch were ever deleted this would
    fail on its own signal (foreign_count == 0), not merely on a changed
    total."""
    _navigate_to_section(fresh_page, "3. Jolly Roger Bay", "Blast to the Stone Pillar",
                         "Blast to the Stone Pillar")

    mario_cards = fresh_page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.library-section.open .library-band[data-tier=\"Mario\"] .library-example'))"
        ".length")
    assert mario_cards == 112, mario_cards

    badged = fresh_page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.library-section.open .library-band[data-tier=\"Mario\"] .library-example-version'))"
        ".map((el) => el.textContent.trim())")
    assert len(badged) == 108, (len(badged), badged[:10])
    assert set(badged) == {"JP"}, set(badged)

    # A named runner on each side, so this is not just a count that a wholly
    # unrelated regression could coincidentally reproduce.
    jp_runner_badge = fresh_page.evaluate(
        "(() => { const card = Array.from(document.querySelectorAll("
        "'.library-section.open .library-band[data-tier=\"Mario\"] .library-example'))"
        ".find((c) => c.querySelector('.library-example-runner').textContent === 'Wareagle1108');"
        "if (!card) return 'no card for Wareagle1108';"
        "const badge = card.querySelector('.library-example-version');"
        "return badge ? badge.textContent.trim() : 'no badge'; })()")
    assert jp_runner_badge == "JP", jp_runner_badge

    us_runner_badge = fresh_page.evaluate(
        "(() => { const card = Array.from(document.querySelectorAll("
        "'.library-section.open .library-band[data-tier=\"Mario\"] .library-example'))"
        ".find((c) => c.querySelector('.library-example-runner').textContent === 'BroMario28');"
        "if (!card) return 'no card for BroMario28';"
        "const badge = card.querySelector('.library-example-version');"
        "return badge ? badge.textContent.trim() : 'no badge'; })()")
    assert us_runner_badge == "no badge", (
        "BroMario28's own run is US, the same version the shown ladder is "
        f"fitted from -- it must not wear a foreign-version badge, got {us_runner_badge!r}")


def test_a_jp_only_ladder_wears_an_inert_chip_with_no_toggle(fresh_page):
    """13 approaches across the corpus fit a ladder from JP-only times with
    too few US runs to earn a `ladder_jp` companion (medium finding:
    `ladder_version` read nowhere) -- "Left side strat" is one of them (31 jp
    / 8 us). No toggle exists here at all, so the chip is the ONLY thing on
    screen that can tell a US player their time is being read against a
    JP-only ladder."""
    _navigate_to_section(fresh_page, "1. Bob-omb Battlefield", "Big Bob-omb on the Summit",
                         "Left side strat")

    toggle = fresh_page.evaluate(
        "!!document.querySelector('.library-section.open .library-jp-toggle')")
    assert not toggle, "a JP/US toggle rendered where none should -- this approach has no ladder_jp"

    chip = fresh_page.evaluate(
        "(() => { const el = document.querySelector("
        "'.library-section.open .library-ladder-version-chip'); "
        "return el ? el.textContent.trim() : null; })()")
    assert chip == "JP ladder only", chip

    badged = fresh_page.evaluate(
        "Array.from(document.querySelectorAll("
        "'.library-section.open .library-example-version')).map((el) => el.textContent.trim())")
    assert len(badged) == 8, (len(badged), badged)
    assert set(badged) == {"US"}, set(badged)
