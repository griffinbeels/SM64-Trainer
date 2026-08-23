"""library/ratings.py -- the bridge from the Ultimate Sheet to a runner
rating on MARELO's own 0..100 curve. Its twin, tracking/marelo.py, has its own
test file (test_marelo_bridge.py); this one proves the same absent-vs-zero
and standards-ladder rules hold for a sheet entry instead of an attempt."""
from sm64_events.library.audit import row_key
from sm64_events.library.ratings import rate_runners, runner_times
from sm64_events.ranks import scopes


class FakeRanks:
    """Stands in for ranks.standards.RankStandards -- same shape
    test_marelo_bridge.py's FakeRanks uses, plus the `version` argument the
    real store's `ladders()` takes."""
    def __init__(self, data):
        self._data = data

    def ladders(self, entity_key, version=None):
        return self._data.get(entity_key, {})


# Deliberately a HARDER ladder than the one in RANKS below, so a score
# computed against it (a bug grading on the sheet's own fitted ladder)
# disagrees with a score computed against the standards ladder.
WRONG_LADDER = {"Mario": 40.0, "Gold": 55.0}

RANKS = FakeRanks({
    "star:1:0": {"Standard": {"Mario": 45.0, "Gold": 60.0}},
    "star:9:9": {"Standard": {"Mario": 30.0, "Gold": 50.0}}})


def _item(name, entries, ids=("1",), ladder=None, ladder_jp=None):
    return {"ids": list(ids), "name": name, "best_cs": None, "best_runner": None,
            "times": {}, "ideal_cs": None, "fill_rate": None,
            "ladder": ladder or dict(WRONG_LADDER), "ladder_jp": ladder_jp,
            "entries": entries}


def _entry(runner, time_cs, version=None):
    return {"runner": runner, "time_cs": time_cs, "video": None, "version": version}


def _target(entity_key, label, approaches=(), subsections=(), miss_reason=None):
    return {"entity_key": entity_key, "group": "g", "section": "s", "label": label,
            "version": None, "miss_reason": miss_reason,
            "approaches": list(approaches), "subsections": list(subsections)}


def test_best_across_mappings_picks_the_minimum():
    # Two targets both map to star:1:0 -- the same star, two documented ways
    # -- so the runner's time is the faster of the two.
    payload = {"targets": [
        _target("star:1:0", "Way A",
               approaches=[_item("Way A", [_entry("Fast", 4400)])]),
        _target("star:1:0", "Way B",
               approaches=[_item("Way B", [_entry("Fast", 4300)])])]}
    assert runner_times(payload, {})["Fast"]["star:1:0"] == 4300


def test_an_entity_with_no_time_is_absent_never_zero():
    payload = {"targets": [
        _target("star:1:0", "Way A",
               approaches=[_item("Way A", [_entry("Fast", 4400)])]),
        _target("star:9:9", "Untouched", approaches=[_item("Untouched", [])])]}
    scores = rate_runners(payload, RANKS, {}, version="us").scores
    assert "star:9:9" not in scores["Fast"]
    assert set(scores["Fast"]) == {"star:1:0"}


def test_a_row_with_no_standards_ladder_is_absent_not_scored_zero():
    # star:5:5 has no entry in RANKS at all -- best_ladder({}) is empty, so
    # the row must be omitted rather than contributing a 0.0.
    payload = {"targets": [_target(
        "star:5:5", "No standards",
        approaches=[_item("No standards", [_entry("Fast", 100)])])]}
    scores = rate_runners(payload, RANKS, {}, version="us").scores
    assert scores == {}


def test_an_adopted_row_maps_a_target_with_no_entity_key():
    target = _target(None, "Lobby door (L) - BoB door",
                     approaches=[_item("Lobby door (L) - BoB door",
                                       [_entry("Fast", 900)])],
                     miss_reason="castle_movement")
    payload = {"targets": [target]}
    key = row_key(target, "Lobby door (L) - BoB door", ["1"])
    assert runner_times(payload, {}) == {}          # unadopted: contributes nothing
    times = runner_times(payload, {key: "segment:42"})
    assert times["Fast"]["segment:42"] == 900


def test_an_unadopted_subsection_does_not_inherit_its_targets_entity_key():
    # Every subsection in the shipped snapshot sits inside a target that
    # already has an entity_key (a star) -- it names a STRETCH of that star,
    # not the whole run, so it must not silently count towards the star's
    # own score until the user builds and adopts a segment for it.
    target = _target("star:1:0", "Some Star",
                     approaches=[_item("Some Star", [_entry("Fast", 4400)])],
                     subsections=[_item("A stretch", [_entry("Fast", 100)])])
    payload = {"targets": [target]}
    times = runner_times(payload, {})
    assert times["Fast"] == {"star:1:0": 4400}       # the stretch's 100 never lands


def test_a_jp_tagged_entry_disappears_under_us_and_untagged_survives_both():
    # Mixed tags on one row (both "jp" and "us" appear) is what makes the row
    # VERSIONED -- librarytarget.js::Section only filters once there is
    # something to distinguish.
    item = _item("Mixed", [_entry("OnlyJp", 100, "jp"),
                           _entry("OnlyUs", 100, "us"),
                           _entry("Neither", 100)])
    payload = {"targets": [_target("star:1:0", "Mixed", approaches=[item])]}
    us_times = runner_times(payload, {}, version="us")
    jp_times = runner_times(payload, {}, version="jp")
    assert set(us_times) == {"OnlyUs", "Neither"}
    assert set(jp_times) == {"OnlyJp", "Neither"}


def test_an_unversioned_row_shows_every_entry_in_both_modes():
    # A row with exactly one distinct tag and no ladder_jp of its own has
    # nothing to distinguish -- too few of that version's times to ever fit a
    # second ladder -- so Section shows it in both modes, and so must we (52
    # approaches/subsections in the shipped snapshot are exactly this shape).
    item = _item("Thin JP-only", [_entry("Runner", 100, "jp")])
    payload = {"targets": [_target("star:1:0", "Thin JP-only", approaches=[item])]}
    assert "Runner" in runner_times(payload, {}, version="us")
    assert "Runner" in runner_times(payload, {}, version="jp")


def test_scoring_grades_on_the_standards_ladder_never_the_fitted_item_ladder():
    # The item carries WRONG_LADDER (harder than RANKS's star:1:0 ladder) --
    # if scoring read item["ladder"] instead of ranks_store.ladders(), this
    # time would grade below Mario's anchor rather than exactly at it.
    payload = {"targets": [_target(
        "star:1:0", "Way A",
        approaches=[_item("Way A", [_entry("Fast", 4500)], ladder=WRONG_LADDER)])]}
    scores = rate_runners(payload, RANKS, {}, version="us").scores
    assert scores["Fast"]["star:1:0"] == 95.0        # RANKS's Mario cutoff, not WRONG_LADDER's


def test_rated_scores_feed_scopes_aggregate_directly():
    """The contract the interface promises: `{entity_key: score}` is exactly
    what `scopes.aggregate` wants, with no reshaping in between."""
    payload = {"targets": [_target(
        "star:1:0", "Way A",
        approaches=[_item("Way A", [_entry("Fast", 4500)])])]}
    scores = rate_runners(payload, RANKS, {}, version="us").scores
    groups = [{"need": 1, "candidates": ["star:1:0"]}]
    result = scopes.aggregate(scores["Fast"], groups)
    assert result["marelo"] == 95.0
    assert result["practiced"] == 1


# ---------------------------------------------------------------------------
# Step 5: the real bundled snapshot and the real vetted seed. Values below
# pin the SHAPE of the 2026-08-20 measurement, not its exact float -- the
# seed is regenerated by tools/scrape_sheet.py and an exact pin would turn a
# routine refresh into a red build for no reason.

def test_real_snapshot_shape_matches_the_measured_expectations(tmp_path):
    # NOT `paths.bundled_rank_standards()` -- tests/conftest.py's autouse
    # `_isolate_rank_standards` nulls that for every test (no test may touch
    # production rank_standards.json by surprise), and this test genuinely
    # wants the real seed. Same escape test_library_store.py's refresh test
    # uses: point RankStandards at the file directly.
    from pathlib import Path

    from sm64_events.core.paths import bundled_sheet_library
    from sm64_events.library.store import LibraryStore
    from sm64_events.ranks.standards import RankStandards

    repo = Path(__file__).resolve().parents[1]
    real_seed = repo / "src" / "sm64_events" / "data" / "rank_standards.seed.json"
    assert real_seed.exists(), f"expected the real seed at {real_seed}"

    store = LibraryStore(bundled_path=bundled_sheet_library())
    store.load()
    payload = store.payload
    assert payload["targets"], "the bundled sheet library snapshot is missing"

    standards = RankStandards(tmp_path / "rank_standards.json", seed_path=real_seed)
    standards.load()

    scores = rate_runners(payload, standards, {}, version="us").scores

    # "Runners with >=1 time on a mapped entity" -- measured 446.
    assert len(scores) > 400

    # "Distinct entities the sheet reaches" -- measured 112 of 117.
    graded = standards.graded_entities()
    reached = {entity_key for by_entity in scores.values() for entity_key in by_entity}
    assert len(graded) >= 100
    assert len(reached) > 100
    assert reached <= set(graded)

    rankable = scopes.rankable_entities(
        {entity_key: standards.ladders(entity_key, "us") for entity_key in graded})
    groups = scopes.entity_groups("overall", rankable=rankable, routes=[],
                                  segment_courses={})

    overall = {runner: scopes.aggregate(by_entity, groups)
              for runner, by_entity in scores.items()}

    # "the top runner scores above 80" -- a sanity bound on the whole
    # pipeline's output, not a regression guard on any one rule.
    assert max(result["marelo"] for result in overall.values()) > 80

    # The absent-vs-zero guard that matters most, aimed at the field the
    # rule actually moves. `scopes.aggregate` folds a missing entity and an
    # explicit 0.0 into `marelo` IDENTICALLY (`total += score or 0.0`), so a
    # `marelo` bound alone cannot tell "omitted" from "scored zero" apart --
    # it would pass unchanged even if `rate_runners` wrote 0.0 for every
    # entity a runner never touched. `practiced` is the field the rule
    # actually gates (`if score is not None: practiced += 1`), and it is
    # what the leaderboard PRINTS on every row as coverage (practiced/n) --
    # a zeroing regression would read "117/117" beside a runner who has one
    # real time. So the guard is: the single-entry runner's own AGGREGATED
    # coverage names exactly the one entity they ran, not the whole board.
    times = runner_times(payload, {}, version="us")
    single_entry_runners = [runner for runner, by_entity in times.items()
                            if len(by_entity) == 1]
    assert single_entry_runners, "no single-entry runner in the bundled seed"
    for runner in single_entry_runners:
        assert overall[runner]["practiced"] == 1
        assert overall[runner]["marelo"] < 2.0
