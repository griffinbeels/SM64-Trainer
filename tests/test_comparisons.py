from sm64_events.tracking.comparisons import (cache_name_for, master_seek_time,
                                              resolve_auto)


def test_cache_name_is_deterministic_and_mp4():
    a = cache_name_for("https://youtu.be/abc")
    assert a == cache_name_for("https://youtu.be/abc")
    assert a.endswith(".mp4")
    assert a != cache_name_for("https://youtu.be/xyz")


def test_resolve_auto_prefers_most_recent_saved():
    saved = [{"id": 1, "last_used_utc": "2026-07-01T00:00:00Z"},
             {"id": 2, "last_used_utc": "2026-07-02T00:00:00Z"}]
    r = resolve_auto(saved, suggestion="https://youtu.be/std", strat="Ledgegrab")
    assert r["mode"] == "saved"
    assert r["comparison"]["id"] == 2


def test_resolve_auto_falls_back_to_suggestion():
    r = resolve_auto([], suggestion="https://youtu.be/std", strat="Ledgegrab")
    assert r["mode"] == "suggestion"
    assert r["comparison"] is None


def test_resolve_auto_empty_when_no_saved_no_suggestion():
    r = resolve_auto([], suggestion=None, strat="Ledgegrab")
    assert r["mode"] == "empty"


def test_resolve_auto_no_strat():
    r = resolve_auto([], suggestion=None, strat=None)
    assert r["mode"] == "no_strat"


def test_master_seek_time_offset_and_half_frame():
    # master frame 0, no in-point -> middle of frame 0 = 0.5/30
    assert master_seek_time(None, 0) == 0.5 / 30
    # in-point 90 (3 s), master frame 30 (1 s) -> (90+30+0.5)/30
    assert master_seek_time(90, 30) == (90 + 30 + 0.5) / 30
