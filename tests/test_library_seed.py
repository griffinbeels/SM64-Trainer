"""Pins the committed snapshot's real numbers.

Unit tests over a hand-built fixture cannot see a mapping hole -- only the
OUTPUT shows it, which is the same reason tests/test_scrape_ranks.py pins the
bundled rank seed's Bowser coverage. Every count here is a floor rather than
an equality: the sheet gains rows daily, so an exact number would go red for
the community doing exactly what we want them to do."""
import gzip
import json
from pathlib import Path

import pytest

SEED = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
        / "data" / "sheet_library.seed.json.gz")

# Eight main-course red-coin stars the sheet only ever times TOGETHER with 100
# coins, so those rows map to the course's 100-coin star and the reds star
# itself has no row of its own. Absent from the sheet, not missed by us.
REDS_ONLY_WITH_100C = {"star:1:3", "star:2:3", "star:3:3", "star:4:2",
                       "star:5:3", "star:6:1", "star:9:2", "star:11:4"}


@pytest.fixture(scope="module")
def payload():
    with gzip.open(SEED, "rt", encoding="utf-8") as handle:
        return json.load(handle)


def test_schema_and_revision_present(payload):
    assert payload["schema_version"] == 1
    assert payload["sheet_revision"] >= "2026-08-04T20:14:25"


def test_every_audit_key_is_unique(payload):
    """An override is stored under a key. Two targets or two rows sharing one
    means a human's correction silently rules on both -- which is the exact
    failure the audit exists to catch, arriving inside the audit itself. BBH
    opens two targets called "Go on a Ghost Hunt" (one per ROM version) and 19
    targets repeat a row name, so this is a live hazard rather than a
    hypothetical one."""
    from sm64_events.library.audit import row_key, target_key
    keys = [target_key(t) for t in payload["targets"]]
    assert len(keys) == len(set(keys)), \
        [k for k in keys if keys.count(k) > 1][:5]
    rows = [row_key(t, item["name"], item["ids"]) for t in payload["targets"]
            for item in t["approaches"] + t["subsections"]]
    assert len(rows) == len(set(rows)), \
        [k for k in rows if rows.count(k) > 1][:5]


def test_no_unreviewed_unknown_targets(payload):
    unknown = [f"{t['section']} | {t['label']}" for t in payload["targets"]
               if t["entity_key"] is None and t["miss_reason"] == "unknown"]
    assert unknown == [], f"unmapped and unexplained: {unknown}"


def test_every_main_course_star_is_covered_or_explained(payload):
    mapped = {t["entity_key"] for t in payload["targets"] if t["entity_key"]}
    missing = [f"star:{course}:{star}"
               for course in range(1, 16) for star in range(7)
               if f"star:{course}:{star}" not in mapped]
    assert set(missing) <= REDS_ONLY_WITH_100C, f"newly uncovered: {missing}"


def test_bowser_courses_are_covered_all_three_ways(payload):
    mapped = {t["entity_key"] for t in payload["targets"] if t["entity_key"]}
    for course, pipe, battle in ((16, 5, 8), (17, 6, 9), (18, 7, 10)):
        assert f"star:{course}:0" in mapped      # the 8-red-coin star
        assert f"segment:{pipe}" in mapped       # the course run to the pipe
        assert f"segment:{battle}" in mapped     # the fight


def test_the_library_is_actually_populated(payload):
    entries = [entry for target in payload["targets"]
               for item in target["approaches"] + target["subsections"]
               for entry in item["entries"]]
    assert len(payload["targets"]) >= 250
    assert len(payload["runners"]) >= 440
    assert len(entries) >= 44000, len(entries)
    assert sum(1 for entry in entries if entry["video"]) >= 7000


def test_no_subsection_was_promoted_to_an_approach(payload):
    """The failure this whole classification exists to prevent, checked on the
    real output rather than on a fixture.

    Each approach is compared against the BEST OF THE ONES BEFORE IT, which is
    the basis the parser's veto uses. Comparing the fastest against the slowest
    instead would be a different claim and a false one: an approach may be
    legitimately slower than its siblings (WF's owl strat is 15.60 against a
    10.90 sibling) without anything having gone wrong."""
    for target in payload["targets"]:
        if "RTA" in target["label"]:
            continue                       # different routes, different lengths
        basis = None
        for approach in target["approaches"]:
            best = approach["best_cs"]
            if best is None:
                continue
            if basis is not None:
                assert best >= 0.70 * basis, (
                    f"{target['label']} / {approach['name']}: {best} cs is "
                    f"{best / basis:.2f} of the {basis} cs before it")
            basis = best if basis is None else min(basis, best)


def test_every_subsection_beats_the_target_it_belongs_to(payload):
    """The other direction: a part cannot be slower than the whole."""
    for target in payload["targets"]:
        bests = [a["best_cs"] for a in target["approaches"] if a["best_cs"]]
        if not bests:
            continue
        for subsection in target["subsections"]:
            if subsection["best_cs"] is None:
                continue
            assert subsection["best_cs"] < max(bests), (
                f"{target['label']} / {subsection['name']}: "
                f"{subsection['best_cs']} cs against {max(bests)} cs")
