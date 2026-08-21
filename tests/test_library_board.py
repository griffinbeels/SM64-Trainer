# tests/test_library_board.py
"""library/board.py -- the ordered leaderboard and one-runner breakdown built
on top of library.ratings' runner score map, plus the cache that keeps
rebuilding it off the request path (spec 2026-08-20-ranked-leaderboard,
Task 3). Driven directly against a hand-built store/library, matching
library.ratings' own "pure over inputs" test style; test_ranks_api_marelo.py
covers the two REST routes wired on top of this module."""
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


def _counting(monkeypatch):
    """Patches ratings.runner_scores with a call-counting wrapper around the
    real function -- the memoization proof needs a STUB, not a stopwatch."""
    calls = []
    real = ratings.runner_scores

    def wrapper(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(ratings, "runner_scores", wrapper)
    return calls


# -- ordering / ranking -------------------------------------------------

def test_leaderboard_orders_by_marelo_descending():
    payload = _payload([_entry("Speedy", 4000), _entry("Plodder", 9000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    you = scopes.aggregate({}, GROUPS)
    rows, omitted = board.leaderboard(cache, library, {}, ranks, GROUPS,
                                      "overall", version="us", you_aggregate=you)
    order = [row["runner"] for row in rows if row["runner"]]
    assert order.index("Speedy") < order.index("Plodder")
    assert omitted == 0          # both runners practiced this scope's entity


def test_tied_marelo_shares_a_position_and_the_next_skips():
    payload = _payload([_entry("Speedy", 4000), _entry("Twin1", 5000),
                        _entry("Twin2", 5000), _entry("Plodder", 9000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    you = scopes.aggregate({}, GROUPS)          # never practiced -> 0.0, last
    rows, _omitted = board.leaderboard(cache, library, {}, ranks, GROUPS,
                                       "overall", version="us", you_aggregate=you)
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
    library = FakeLibrary(payload)
    ranks = FakeRanks(ranks_data)
    cache = board.RunnerScoreCache()
    you = scopes.aggregate({}, GROUPS)
    rows, omitted = board.leaderboard(cache, library, {}, ranks, GROUPS,
                                      "overall", version="us", you_aggregate=you)
    runners = [row["runner"] for row in rows]
    assert "Speedy" in runners and "Ghost" not in runners
    assert omitted == 1          # exactly Ghost


def test_the_users_row_is_present_and_marked():
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    you = scopes.aggregate({"star:1:0": 82.0}, GROUPS)
    rows, _omitted = board.leaderboard(cache, library, {}, ranks, GROUPS,
                                       "overall", version="us", you_aggregate=you)
    you_rows = [row for row in rows if row["you"]]
    assert len(you_rows) == 1
    assert you_rows[0]["runner"] is None
    assert you_rows[0]["marelo"] == you["marelo"]


# -- the memoized {runner: {entity: score}} map --------------------------

def test_unchanged_inputs_do_not_rebuild(monkeypatch):
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    calls = _counting(monkeypatch)
    cache.refresh(library, {}, ranks, version="us")
    cache.refresh(library, {}, ranks, version="us")
    assert len(calls) == 1


def test_changing_the_adoptions_map_invalidates(monkeypatch):
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    calls = _counting(monkeypatch)
    cache.refresh(library, {}, ranks, version="us")
    cache.refresh(library, {"some-row": "star:1:0"}, ranks, version="us")
    assert len(calls) == 2


def test_a_different_grading_version_invalidates(monkeypatch):
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    calls = _counting(monkeypatch)
    cache.refresh(library, {}, ranks, version="us")
    cache.refresh(library, {}, ranks, version="jp")
    assert len(calls) == 2


def test_a_newer_library_revision_invalidates(monkeypatch):
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload, revision="rev1")
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    calls = _counting(monkeypatch)
    cache.refresh(library, {}, ranks, version="us")
    library.revision = "rev2"
    cache.refresh(library, {}, ranks, version="us")
    assert len(calls) == 2


def test_editing_a_threshold_invalidates_and_the_board_changes(monkeypatch):
    """THE trap this task was warned about: `ranks_store.to_json()["version"]`
    is the bundled seed's version and never moves on an edit like this one --
    a cache keyed on it would miss this entirely and serve a stale board."""
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks({"star:1:0": {"Standard": {"Mario": 45.0, "Gold": 60.0}}})
    cache = board.RunnerScoreCache()
    calls = _counting(monkeypatch)
    before, _ = cache.refresh(library, {}, ranks, version="us")
    assert ranks.to_json()["version"] == 1        # the seed version: inert
    ranks._data["star:1:0"]["Standard"]["Mario"] = 39.0     # a threshold edit
    after, _ = cache.refresh(library, {}, ranks, version="us")
    assert ranks.to_json()["version"] == 1        # ...stays inert throughout
    assert len(calls) == 2                        # yet the cache rebuilt
    assert before["Speedy"]["star:1:0"] != after["Speedy"]["star:1:0"]


# -- you_times_by_entity --------------------------------------------------

def test_you_times_picks_the_fastest_row_on_the_right_clock():
    ranks = FakeRanks({}, clock="igt")
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
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    breakdown = board.runner_breakdown(
        cache, library, {}, ranks, GROUPS, "Speedy", version="us",
        you_scores={"star:1:0": 50.0}, you_times={"star:1:0": 5200},
        label_of=lambda key: key)
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


def test_runner_breakdown_of_an_unknown_runner_is_none():
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    assert board.runner_breakdown(
        cache, library, {}, ranks, GROUPS, "Ghost", version="us",
        you_scores={}, you_times={}, label_of=lambda key: key) is None


def test_runner_breakdown_entity_with_no_you_time_is_null_not_zero():
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    breakdown = board.runner_breakdown(
        cache, library, {}, ranks, GROUPS, "Speedy", version="us",
        you_scores={}, you_times={}, label_of=lambda key: key)
    you = breakdown["entities"][0]["you"]
    assert you == {"score": None, "time_cs": None, "tier": None, "division": None}


# -- runner_summary -----------------------------------------------------

def test_runner_summary_shape():
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    chips = board.runner_summary(
        cache, library, {}, ranks, [("overall", GROUPS, "Overall")],
        "Speedy", version="us")
    assert len(chips) == 1
    assert set(chips[0]) == {"scope_id", "label", "tier", "division",
                             "marelo", "n", "practiced"}
    assert chips[0]["scope_id"] == "overall" and chips[0]["n"] == 1
    assert chips[0]["practiced"] == 1


def test_runner_summary_of_an_unknown_runner_is_none():
    payload = _payload([_entry("Speedy", 4000)])
    library = FakeLibrary(payload)
    ranks = FakeRanks(RANKS_DATA)
    cache = board.RunnerScoreCache()
    assert board.runner_summary(
        cache, library, {}, ranks, [("overall", GROUPS, "Overall")],
        "Ghost", version="us") is None
