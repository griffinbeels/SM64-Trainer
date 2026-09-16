# tests/test_library_board.py
"""library/board.py -- the [[Rank board]] and [[Runner page]] readings on a
`RatedSheet`, plus the `RatingsCache` that keeps rebuilding it off the
request path (spec 2026-08-20-ranked-leaderboard, Task 3). Driven directly
against a hand-built store/library, matching library.ratings' own "pure over
inputs" test style; test_ranks_api_marelo.py covers the REST routes wired on
top of this module."""
from sm64_events.library import board, ratings
from sm64_events.ranks import scopes
from sm64_events.ranks.classify import display_cs


class FakeRanks:
    """Stands in for ranks.standards.RankStandards -- same shape
    test_library_ratings.py's FakeRanks uses, plus `to_json`/`clock_for`,
    which board.py's cache fingerprint and `you_times_by_entity` need."""
    def __init__(self, data, clock="igt"):
        self._data = data
        self._clock = clock

    def ladders(self, entity_key, version=None):
        return self._data.get(entity_key, {})

    def clock_for(self, entity_key):
        return self._clock

    def to_json(self):
        return {"version": 1, "entities": self._data}


class FakeLibrary:
    def __init__(self, payload, revision="rev1"):
        self.payload = payload
        self.revision = revision


RANKS_DATA = {"star:1:0": {"Standard": {"Mario": 45.0, "Gold": 60.0}}}
GROUPS = [{"need": 1, "candidates": ["star:1:0"]}]
OVERALL_SPEC = [("overall", GROUPS, "Overall")]


def _entry(runner, time_cs):
    return {"runner": runner, "time_cs": time_cs, "video": None, "version": None}


def _item(name, entries, ids=("1",)):
    return {"ids": list(ids), "name": name, "best_cs": None, "best_runner": None,
            "times": {}, "ideal_cs": None, "fill_rate": None,
            "ladder": {"Mario": 999.0}, "ladder_jp": None, "entries": entries}


def _target(entity_key, label, approaches):
    return {"entity_key": entity_key, "group": "g", "section": "s", "label": label,
            "version": None, "miss_reason": None,
            "approaches": list(approaches), "subsections": []}


def _payload(entries):
    return {"targets": [_target("star:1:0", "Way A", [_item("Way A", entries)])]}


def _sheet(entries, ranks_data=RANKS_DATA):
    """A RatedSheet over one star with these entries -- the shape every
    reading test below starts from."""
    return board.RatingsCache().current(
        FakeLibrary(_payload(entries)), {}, FakeRanks(ranks_data), version="us")


def _counting(monkeypatch):
    """Patches ratings.rate_runners with a call-counting wrapper around the
    real function -- the memoization proof needs a STUB, not a stopwatch."""
    calls = []
    real = ratings.rate_runners

    def wrapper(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(ratings, "rate_runners", wrapper)
    return calls


# -- ordering / ranking -------------------------------------------------

def test_leaderboard_orders_by_marelo_descending():
    sheet = _sheet([_entry("Speedy", 4000), _entry("Plodder", 9000)])
    you = scopes.aggregate({}, GROUPS)
    rows, omitted = sheet.leaderboard("overall", GROUPS, you_aggregate=you)
    order = [row["runner"] for row in rows if row["runner"]]
    assert order.index("Speedy") < order.index("Plodder")
    assert omitted == 0          # both runners practiced this scope's entity


def test_tied_marelo_shares_a_position_and_the_next_skips():
    sheet = _sheet([_entry("Speedy", 4000), _entry("Twin1", 5000),
                    _entry("Twin2", 5000), _entry("Plodder", 9000)])
    you = scopes.aggregate({}, GROUPS)          # never practiced -> 0.0, last
    rows, _omitted = sheet.leaderboard("overall", GROUPS, you_aggregate=you)
    by_runner = {row["runner"]: row for row in rows if row["runner"]}
    assert by_runner["Twin1"]["marelo"] == by_runner["Twin2"]["marelo"]
    assert by_runner["Twin1"]["position"] == by_runner["Twin2"]["position"]
    # Speedy 1st, the tied Twins share 2nd, Plodder is 4th (2nd's dupe skips
    # 3rd), the user (never practiced) is last at 5th.
    assert sorted(row["position"] for row in rows) == [1, 2, 2, 4, 5]


def test_a_runner_with_nothing_practiced_in_scope_is_left_off_the_board():
    """448 sheet runners, most of whom never touch a narrow scope -- a
    leaderboard tied at 0.0 for hundreds of rows is not a leaderboard.
    The user's own row carries no such filter (see the next test).

    The omission is counted, not just silent (his ruling): `omitted` must
    say how many were left off, or an empty-looking board reads as
    complete when it is not."""
    ranks_data = {**RANKS_DATA,
                  "star:2:0": {"Standard": {"Mario": 30.0, "Gold": 50.0}}}
    payload = {"targets": [
        _target("star:1:0", "Way A", [_item("Way A", [_entry("Speedy", 4000)])]),
        _target("star:2:0", "Way B", [_item("Way B", [_entry("Ghost", 3000)])])]}
    sheet = board.RatingsCache().current(
        FakeLibrary(payload), {}, FakeRanks(ranks_data), version="us")
    you = scopes.aggregate({}, GROUPS)
    rows, omitted = sheet.leaderboard("overall", GROUPS, you_aggregate=you)
    runners = [row["runner"] for row in rows]
    assert "Speedy" in runners and "Ghost" not in runners
    assert omitted == 1          # exactly Ghost


def test_the_users_row_is_present_and_marked():
    sheet = _sheet([_entry("Speedy", 4000)])
    you = scopes.aggregate({"star:1:0": 82.0}, GROUPS)
    rows, _omitted = sheet.leaderboard("overall", GROUPS, you_aggregate=you)
    you_rows = [row for row in rows if row["you"]]
    assert len(you_rows) == 1
    assert you_rows[0]["runner"] is None
    assert you_rows[0]["marelo"] == you["marelo"]


# -- the cached RatedSheet ------------------------------------------------

def _cache_inputs(revision="rev1", ranks_data=RANKS_DATA):
    library = FakeLibrary(_payload([_entry("Speedy", 4000)]), revision=revision)
    return library, FakeRanks(ranks_data)


def test_unchanged_inputs_do_not_rebuild(monkeypatch):
    library, ranks = _cache_inputs()
    cache = board.RatingsCache()
    calls = _counting(monkeypatch)
    first = cache.current(library, {}, ranks, version="us")
    second = cache.current(library, {}, ranks, version="us")
    assert len(calls) == 1 and first is second


def test_changing_the_adoptions_map_invalidates(monkeypatch):
    library, ranks = _cache_inputs()
    cache = board.RatingsCache()
    calls = _counting(monkeypatch)
    cache.current(library, {}, ranks, version="us")
    cache.current(library, {"some-row": "star:1:0"}, ranks, version="us")
    assert len(calls) == 2


def test_a_different_grading_version_invalidates(monkeypatch):
    library, ranks = _cache_inputs()
    cache = board.RatingsCache()
    calls = _counting(monkeypatch)
    cache.current(library, {}, ranks, version="us")
    cache.current(library, {}, ranks, version="jp")
    assert len(calls) == 2


def test_a_newer_library_revision_invalidates(monkeypatch):
    library, ranks = _cache_inputs(revision="rev1")
    cache = board.RatingsCache()
    calls = _counting(monkeypatch)
    cache.current(library, {}, ranks, version="us")
    library.revision = "rev2"
    cache.current(library, {}, ranks, version="us")
    assert len(calls) == 2


def test_editing_a_threshold_invalidates_and_the_board_changes(monkeypatch):
    """THE trap this task was warned about: `ranks_store.to_json()["version"]`
    is the bundled seed's version and never moves on an edit like this one --
    a cache keyed on it would miss this entirely and serve a stale board."""
    library, ranks = _cache_inputs(
        ranks_data={"star:1:0": {"Standard": {"Mario": 45.0, "Gold": 60.0}}})
    cache = board.RatingsCache()
    calls = _counting(monkeypatch)
    before = cache.current(library, {}, ranks, version="us").scores
    assert ranks.to_json()["version"] == 1        # the seed version: inert
    ranks._data["star:1:0"]["Standard"]["Mario"] = 39.0     # a threshold edit
    after = cache.current(library, {}, ranks, version="us").scores
    assert ranks.to_json()["version"] == 1        # ...stays inert throughout
    assert len(calls) == 2                        # yet the cache rebuilt
    assert before["Speedy"]["star:1:0"] != after["Speedy"]["star:1:0"]


def test_scope_rows_memoize_on_the_sheet_and_die_with_it():
    """A repeat read of the same scope reuses its rows (46ms to aggregate
    Overall over 448 runners); a rebuilt sheet starts from nothing, so no
    memo can outlive the ratings it came from."""
    library, ranks = _cache_inputs()
    cache = board.RatingsCache()
    sheet = cache.current(library, {}, ranks, version="us")
    you = scopes.aggregate({}, GROUPS)
    sheet.leaderboard("overall", GROUPS, you_aggregate=you)
    assert len(sheet._rows_by_scope) == 1
    # The memo keys on the RESOLVED groups, not the scope id alone: the
    # same scope narrowed by an exclusion (round 1, third read) is a
    # different board and must not reuse the wider one's rows.
    narrower = [{"need": 1, "candidates": []}]
    sheet.leaderboard("overall", narrower, you_aggregate=you)
    assert len(sheet._rows_by_scope) == 2
    library.revision = "rev2"
    rebuilt = cache.current(library, {}, ranks, version="us")
    assert rebuilt is not sheet and rebuilt._rows_by_scope == {}


# -- you_times_by_entity --------------------------------------------------

def test_you_times_picks_the_fastest_row_on_the_right_clock():
    ranks = FakeRanks(RANKS_DATA, clock="igt")
    pb_rows = [
        {"course_id": 1, "star_id": 0, "segment_id": None, "strat_tag": "A",
         "timer_mode": "igt", "frames": 1500, "id": 1},
        {"course_id": 1, "star_id": 0, "segment_id": None, "strat_tag": "B",
         "timer_mode": "igt", "frames": 1200, "id": 2},
        # A faster RTA row on the same entity must not win -- wrong clock.
        {"course_id": 1, "star_id": 0, "segment_id": None, "strat_tag": "C",
         "timer_mode": "rta", "frames": 1, "id": 3},
    ]
    times = board.you_times_by_entity(pb_rows, ranks, ["star:1:0"])
    assert times == {"star:1:0": display_cs(1200)}


def test_you_times_omits_an_entity_with_no_pb():
    ranks = FakeRanks({}, clock="igt")
    assert board.you_times_by_entity([], ranks, ["star:1:0"]) == {}


# -- runner_breakdown -------------------------------------------------------

def test_runner_breakdown_widens_entities_with_the_users_own_numbers():
    sheet = _sheet([_entry("Speedy", 4000)])
    breakdown = sheet.runner_breakdown(
        "Speedy", GROUPS, you_scores={"star:1:0": 50.0},
        you_times={"star:1:0": 5200}, label_of=lambda key: key)
    assert breakdown["runner"] == "Speedy"
    assert breakdown["n"] == 1 and breakdown["practiced"] == 1
    entity = breakdown["entities"][0]
    assert entity["key"] == "star:1:0" and entity["label"] == "star:1:0"
    assert entity["excluded"] is False
    assert entity["time_cs"] == 4000
    assert entity["you"] == {"score": 50.0, "time_cs": 5200,
                             "tier": entity["you"]["tier"],
                             "division": entity["you"]["division"]}
    assert entity["you"]["tier"] is not None    # 50.0 grades somewhere


def test_runner_breakdown_carries_the_video_of_the_entry_that_set_the_time():
    """Round 1, third read: the runner page plays the entry beneath the row,
    so the breakdown ships the VIDEO of the entry that set the runner's
    graded time -- the fastest one, never a slower run's video."""
    slow = {**_entry("Speedy", 4500), "video": "https://youtu.be/slow"}
    fast = {**_entry("Speedy", 4000), "video": "https://youtu.be/fast"}
    sheet = _sheet([slow, fast])
    breakdown = sheet.runner_breakdown(
        "Speedy", GROUPS, you_scores={}, you_times={}, label_of=lambda key: key)
    entity = breakdown["entities"][0]
    assert entity["time_cs"] == 4000 and entity["video"] == "https://youtu.be/fast"
    assert _sheet([_entry("Speedy", 4000)]).runner_breakdown(
        "Speedy", GROUPS, you_scores={}, you_times={},
        label_of=lambda key: key)["entities"][0]["video"] is None


def test_runner_breakdown_of_an_unknown_runner_is_none():
    sheet = _sheet([_entry("Speedy", 4000)])
    assert sheet.runner_breakdown(
        "Ghost", GROUPS, you_scores={}, you_times={},
        label_of=lambda key: key) is None


def test_runner_breakdown_entity_with_no_you_time_is_null_not_zero():
    sheet = _sheet([_entry("Speedy", 4000)])
    breakdown = sheet.runner_breakdown(
        "Speedy", GROUPS, you_scores={}, you_times={}, label_of=lambda key: key)
    you = breakdown["entities"][0]["you"]
    assert you == {"score": None, "time_cs": None, "tier": None, "division": None}


# -- runner_summary -----------------------------------------------------

def test_runner_summary_shape():
    sheet = _sheet([_entry("Speedy", 4000)])
    chips = sheet.runner_summary("Speedy", OVERALL_SPEC)
    assert len(chips) == 1
    assert set(chips[0]) == {"scope_id", "label", "tier", "division",
                             "marelo", "n", "practiced"}
    assert chips[0]["scope_id"] == "overall" and chips[0]["n"] == 1
    assert chips[0]["practiced"] == 1


def test_runner_summary_of_an_unknown_runner_is_none():
    sheet = _sheet([_entry("Speedy", 4000)])
    assert sheet.runner_summary("Ghost", OVERALL_SPEC) is None
