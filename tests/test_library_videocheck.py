"""Video liveness (task 0098 round 2): "If a video is not available for any
reason, we should choose the next eligible video as the example for a given
rank standard. If none are available, then the standard has no hyperlink."

The load-bearing properties: only an explicitly dead-shaped HTTP status may
kill an example (a timeout must never delete a working link), an unknowable
platform is never checked and never dead, a fresh verdict costs no request,
and the dead set feeding the resolver produces the NEXT-fastest eligible
example — or no link — through the existing fastest-in-band rule."""
from datetime import datetime, timezone

from sm64_events.library import videocheck
from sm64_events.ranks.standards import RankStandards

YT = "https://www.youtube.com/watch?v=abc123"
YT2 = "https://youtu.be/def456"
TWEET = "https://x.com/runner/status/123456"
TWITCH = "https://www.twitch.tv/videos/999"
FILE = "https://runner.example/clip.mp4"

NOW = datetime(2026, 8, 14, tzinfo=timezone.utc)


def test_platform_classification():
    assert videocheck.classify(YT, 200) == "ok"
    assert videocheck.classify(YT, 404) == "dead"
    assert videocheck.classify(YT, 400) == "dead"       # deleted/private oembed
    # Embed-disabled is WATCHABLE — the table links out, never embeds.
    assert videocheck.classify(YT, 401) == "ok"
    assert videocheck.classify(YT, 403) == "ok"
    assert videocheck.classify(YT, 500) == "unknown"
    assert videocheck.classify(YT, None) == "unknown"   # timeout, DNS, ...
    assert videocheck.classify(TWEET, 404) == "dead"
    assert videocheck.classify(FILE, 410) == "dead"
    # No cheap unauthenticated probe -> never checked, never dead.
    assert videocheck.probe_url(TWITCH) is None
    assert videocheck.classify(TWITCH, 404) == "unknown"


def test_probes_canonicalize_the_sheets_hand_pasted_link_styles():
    """Both false-dead classes the first real sweep's calibration caught
    (2026-08-14): a tweet linked as .../status/<id>/video/1 oEmbeds 404 while
    the tweet is alive, and youtu.be/<id>&t=5s glues params with `&` where a
    `?` belongs, which browsers tolerate and oEmbed rejects. The probe must
    answer for the VIDEO, not for however the sheet wrote the link."""
    suffixed = "https://x.com/runner/status/123456/video/1"
    assert quote_of(videocheck.probe_url(suffixed)).endswith("/status/123456")
    glued = "https://youtu.be/r6V19lejjR8&t=5s"
    assert quote_of(videocheck.probe_url(glued)).endswith("watch?v=r6V19lejjR8")
    listed = "https://www.youtube.com/watch?v=abc123def45&list=PL9&index=3"
    assert quote_of(videocheck.probe_url(listed)).endswith("watch?v=abc123def45")


def quote_of(probe: str) -> str:
    from urllib.parse import unquote
    return unquote(probe.split("url=", 1)[1])


def test_run_checks_stores_verdicts_and_skips_fresh_ones():
    checks = {}
    calls = []

    def fetch(probe):
        calls.append(probe)
        return 404 if "abc123" in probe else 200

    stats = videocheck.run_checks([YT, YT2, TWITCH], checks, fetch, now=NOW)
    assert stats == {"checked": 2, "skipped_fresh": 0, "unknown": 0}
    assert checks[YT]["status"] == "dead"
    assert checks[YT2]["status"] == "ok"
    assert TWITCH not in checks
    # Second run inside the freshness window: no requests at all.
    calls.clear()
    stats = videocheck.run_checks([YT, YT2], checks, fetch, now=NOW)
    assert stats["skipped_fresh"] == 2 and not calls
    # Past the window both are probed again — a dead video can come back.
    stats = videocheck.run_checks(
        [YT, YT2], checks, lambda probe: 200, now=NOW.replace(year=2027))
    assert stats["checked"] == 2
    assert checks[YT]["status"] == "ok"


def test_unknown_results_are_never_stored():
    checks = {}
    videocheck.run_checks([YT], checks, lambda probe: None, now=NOW)
    assert checks == {}                     # retried next run, filters nothing
    assert videocheck.dead_urls(checks) == set()


def test_stale_urls_matches_run_checks_freshness():
    checks = {YT: {"status": "ok", "checked": "2026-08-01T00:00:00Z"}}
    assert videocheck.stale_urls([YT, YT2, TWITCH], checks, now=NOW) == [YT2]
    later = NOW.replace(year=2027)
    assert videocheck.stale_urls([YT, YT2], checks, now=later) == [YT, YT2]


def test_checks_round_trip_and_absent_file_filters_nothing(tmp_path):
    path = tmp_path / "video_checks.seed.json.gz"
    checks = {YT: {"status": "dead", "checked": "2026-08-14T00:00:00Z"}}
    videocheck.save_checks(path, checks)
    assert videocheck.load_checks(path) == checks
    assert videocheck.dead_urls(checks) == {YT}
    assert videocheck.load_checks(tmp_path / "absent.gz") == {}
    assert videocheck.load_checks("") == {}


def test_a_dead_example_falls_back_to_the_next_eligible_video(tmp_path):
    """His rule end to end at the resolver: the fastest in-band clip is dead,
    so the NEXT fastest takes the tier; a band with only dead clips gets no
    link; a hand-attached override survives its own URL being marked dead."""
    import json
    p = tmp_path / "rs.json"
    p.write_text(json.dumps({"version": 3, "entities": {
        "star:8:2": {"clock": "igt",
            "strategies": {"Nuts": {"Mario": 12.93, "Diamond": 13.36}},
            "clips": {"Nuts": [[1280, "dead-fastest"], [1290, "alive-next"],
                               [1330, "dead-only-diamond"]]},
            "user_videos": {"Nuts": {"Diamond": "his-own-pick"}}}}}))
    s = RankStandards(p); s.load()
    dead = {"dead-fastest", "dead-only-diamond", "his-own-pick"}
    resolved = s.cutoff_videos("star:8:2", dead_urls=dead)["Nuts"]
    assert resolved["Mario"] == "alive-next"
    assert resolved["Diamond"] == "his-own-pick"   # overrides are HIS fact
    s.clear_video("star:8:2", "Nuts", "Diamond")
    resolved = s.cutoff_videos("star:8:2", dead_urls=dead).get("Nuts", {})
    assert "Diamond" not in resolved               # only dead clips -> no link
