from sm64_events.tracking.views import build_compare_view


class _FakeRanks:
    def __init__(self, url): self._url = url
    def video_for(self, ek, strat): return self._url


class _FakeDB:
    def __init__(self, rows): self._rows = rows
    def comparisons(self, entity, strat):
        return [r for r in self._rows
                if r["entity_key"] == entity and r["strat"] == strat]


def _row(**kw):
    base = {"id": 1, "entity_key": "star:7:0", "strat": "Ledgegrab",
            "name": "n", "source_kind": "youtube", "source_ref": "u",
            "cache_name": "c.mp4", "in_frame": None, "out_frame": None,
            "created_utc": "t", "last_used_utc": "t"}
    base.update(kw); return base


def test_view_saved_gets_clip_url_and_auto_saved():
    db = _FakeDB([_row(id=5, cache_name="abc.mp4",
                       last_used_utc="2026-07-02T00:00:00Z")])
    v = build_compare_view(db, _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["saved"][0]["clip_url"] == "/api/compare/cache/abc.mp4"
    assert v["auto"]["mode"] == "saved" and v["auto"]["comparison"]["id"] == 5


def test_view_suggestion_when_no_saved():
    v = build_compare_view(_FakeDB([]), _FakeRanks("https://youtu.be/std"),
                           "star:7:0", "Ledgegrab")
    assert v["suggestion"]["source_ref"] == "https://youtu.be/std"
    assert v["auto"]["mode"] == "suggestion"


def test_view_no_strat_is_empty():
    v = build_compare_view(_FakeDB([]), _FakeRanks(None), "star:7:0", None)
    assert v["saved"] == [] and v["auto"]["mode"] == "no_strat"
    assert v["suggestion"] is None


def test_view_none_ranks_no_suggestion():
    v = build_compare_view(_FakeDB([]), None, "star:7:0", "Ledgegrab")
    assert v["suggestion"] is None and v["auto"]["mode"] == "empty"
