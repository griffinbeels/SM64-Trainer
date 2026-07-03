from sm64_events.tracking.views import build_compare_view


class _FakeRanks:
    def __init__(self, url): self._url = url
    def video_for(self, ek, strat): return self._url


class _FakeDB:
    def __init__(self, rows): self._rows = rows
    # build_compare_view loads ALL comparisons for the entity (across strats)
    def comparisons(self, entity, strat=None):
        return [r for r in self._rows
                if r["entity_key"] == entity
                and (strat is None or r["strat"] == strat)]


def _row(**kw):
    base = {"id": 1, "entity_key": "star:7:0", "strat": "Ledgegrab",
            "name": "n", "source_kind": "youtube", "source_ref": "u",
            "cache_name": "c.mp4", "in_frame": None, "out_frame": None,
            "created_utc": "t", "last_used_utc": "t"}
    base.update(kw); return base


def test_view_returns_all_entity_comparisons_with_clip_url():
    # two comparisons under DIFFERENT strategies both show (compare side by side)
    db = _FakeDB([_row(id=5, strat="Ledgegrab", cache_name="abc.mp4"),
                  _row(id=6, strat="Standard", cache_name="def.mp4")])
    v = build_compare_view(db, _FakeRanks(None), "star:7:0", "Ledgegrab")
    ids = {c["id"] for c in v["saved"]}
    assert ids == {5, 6}
    urls = {c["clip_url"] for c in v["saved"]}
    assert urls == {"/api/compare/cache/abc.mp4", "/api/compare/cache/def.mp4"}


def test_view_suggestion_when_focused_strat_not_saved():
    # Ledgegrab has a rank-standard video and no saved comparison yet -> suggest
    db = _FakeDB([_row(id=6, strat="Standard", cache_name="def.mp4")])
    v = build_compare_view(db, _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["suggestion"]["source_ref"] == "https://youtu.be/std"
    assert v["suggestion"]["strat"] == "Ledgegrab"


def test_view_no_suggestion_when_focused_strat_already_saved():
    # a comparison for the focused strat already exists -> don't suggest it again
    db = _FakeDB([_row(id=5, strat="Ledgegrab", cache_name="abc.mp4")])
    v = build_compare_view(db, _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["suggestion"] is None
    assert {c["id"] for c in v["saved"]} == {5}


def test_view_no_strat_no_suggestion_but_still_lists_saved():
    db = _FakeDB([_row(id=7, strat="", cache_name="loose.mp4")])
    v = build_compare_view(db, _FakeRanks("x"), "star:7:0", None)
    assert v["suggestion"] is None            # no focused strat -> no suggestion
    assert {c["id"] for c in v["saved"]} == {7}   # loose (strat "") video still shows


def test_view_none_ranks_no_suggestion():
    v = build_compare_view(_FakeDB([]), None, "star:7:0", "Ledgegrab")
    assert v["suggestion"] is None and v["saved"] == []
