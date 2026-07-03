from sm64_events.tracking.views import build_compare_view


class _FakeRanks:
    def __init__(self, url): self._url = url
    def video_for(self, ek, strat): return self._url


class _FakeDB:
    def __init__(self, rows): self._rows = rows
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


def test_saved_is_scoped_to_the_focused_strat_with_clip_url():
    db = _FakeDB([_row(id=5, strat="Ledgegrab", cache_name="abc.mp4"),
                  _row(id=6, strat="Standard", cache_name="def.mp4")])
    v = build_compare_view(db, _FakeRanks(None), "star:7:0", "Ledgegrab")
    assert {c["id"] for c in v["saved"]} == {5}                 # only this strat
    assert v["saved"][0]["clip_url"] == "/api/compare/cache/abc.mp4"


def test_library_lists_other_strats_comparisons():
    db = _FakeDB([_row(id=5, strat="Ledgegrab"),
                  _row(id=6, strat="Standard"),
                  _row(id=7, strat="Standard")])
    v = build_compare_view(db, _FakeRanks(None), "star:7:0", "Ledgegrab")
    assert {c["id"] for c in v["library"]} == {6, 7}            # not the focused strat
    assert all(c["strat"] == "Standard" for c in v["library"])


def test_suggestion_when_focused_strat_has_no_saved():
    db = _FakeDB([_row(id=6, strat="Standard")])
    v = build_compare_view(db, _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["suggestion"]["source_ref"] == "https://youtu.be/std"
    assert v["suggestion"]["strat"] == "Ledgegrab"


def test_no_suggestion_when_rank_video_already_saved_for_strat():
    db = _FakeDB([_row(id=5, strat="Ledgegrab", source_ref="https://youtu.be/std")])
    v = build_compare_view(db, _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["suggestion"] is None


def test_no_suggestion_when_no_ranks_or_no_strat():
    assert build_compare_view(_FakeDB([]), None, "star:7:0", "Ledgegrab")["suggestion"] is None
    assert build_compare_view(_FakeDB([]), _FakeRanks("x"), "star:7:0", None)["suggestion"] is None
