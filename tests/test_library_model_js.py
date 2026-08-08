# tests/test_library_model_js.py
"""Node-drives `ui/components/librarymodel.js` directly -- the Library page's
pure rules, kept import-free so this file can pin them with no browser and no
Preact. Same idiom as test_cross_language_parity.py's `run_node` (read that
file's header before touching this one): the script is piped over STDIN, not
`-e` -- `-e` truncates on Windows the moment the script carries a quote the
shell would otherwise have to escape, which every JSON.stringify(...) call
here does. `encoding="utf-8"` is load-bearing on this machine (the Windows
ANSI codepage cannot even encode a script with a non-ASCII glyph, and it
silently mangles anything it CAN encode instead of raising).

`MODEL.as_uri()` (a `file://` URL), not a plain Windows path: an ESM `import`
of a bare "C:/..." path 404s -- verified empirically against this exact node
build, and it's the same fix test_cross_language_parity.py already needed for
every one of its own imports.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

MODEL = (Path(__file__).resolve().parent.parent / "src" / "sm64_events"
         / "ui" / "components" / "librarymodel.js")

pytestmark = pytest.mark.skipif(shutil.which("node") is None,
                                reason="node not on PATH")


def run_js(expr: str):
    script = (f"import * as m from {MODEL.as_uri()!r};\n"
              f"console.log(JSON.stringify({expr}));")
    result = subprocess.run(["node", "--input-type=module", "-"],
                            input=script, capture_output=True, text=True,
                            encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_sections_run_beginner_to_expert_with_no_ladder_last():
    approaches = [{"name": "fast", "ladder": {"Mario": 10.0}},
                  {"name": "none"},
                  {"name": "slow", "ladder": {"Mario": 20.0}}]
    assert [a["name"] for a in run_js(f"m.sectionOrder({json.dumps(approaches)})")] \
        == ["slow", "fast", "none"]


def test_auto_expand_prefers_the_selected_strategy_else_first():
    ordered = [{"name": "A"}, {"name": "B", "matched_strategy": "Skyjump"}]
    assert run_js(f"m.autoExpandName({json.dumps(ordered)}, 'Skyjump')") == "B"
    assert run_js(f"m.autoExpandName({json.dumps(ordered)}, null)") == "A"


def test_auto_expand_also_matches_a_variant_qualified_name():
    # Task 1's stamp (commit eb9a92e): the SAME (entity, strategy) pair can be
    # stamped on two sibling targets sharing an entity, and the stamped name
    # may be variant-qualified ("100c + Slide . Open") rather than a bare
    # strategy name. autoExpandName must match on the approach's OWN `name`
    # too, not only `matched_strategy` -- a caller may be resolving against
    # either vocabulary depending on where the selected strat came from.
    ordered = [{"name": "100c + Slide . Open"}, {"name": "B"}]
    assert run_js(
        f"m.autoExpandName({json.dumps(ordered)}, '100c + Slide . Open')") \
        == "100c + Slide . Open"


def test_bands_run_slowest_first_and_sort_slowest_within():
    ladder = {"Bronze": 14.0, "Mario": 12.0}
    entries = [{"runner": "a", "time_cs": 1500}, {"runner": "b", "time_cs": 1390},
               {"runner": "c", "time_cs": 1180}, {"runner": "d", "time_cs": 1395}]
    bands = run_js(f"m.bandsOf({json.dumps(ladder)}, {json.dumps(entries)})")
    assert [b["tier"] for b in bands] == ["Iron", "Bronze", "Mario"]
    assert [e["runner"] for e in bands[1]["entries"]] == ["d", "b"]  # slowest first


def test_every_laddered_band_carries_five_subdivisions_slowest_first():
    # Round 1 (2026-08-07): each tier band splits into five subdivision
    # shells, V (slowest) -> I, each entry filed into exactly one, brackets
    # coming from the scoring twin (pinned against the real Python curve by
    # test_cross_language_parity.py -- this test pins only the SHAPE).
    ladder = {"Bronze": 14.0, "Mario": 12.0}
    entries = [{"runner": "a", "time_cs": 1500}, {"runner": "b", "time_cs": 1390},
               {"runner": "c", "time_cs": 1180}]
    bands = run_js(f"m.bandsOf({json.dumps(ladder)}, {json.dumps(entries)})")
    for band in bands:
        assert [d["numeral"] for d in band["divisions"]] == ["V", "IV", "III", "II", "I"]
        # every band entry is in exactly one shell
        assert sum(len(d["entries"]) for d in band["divisions"]) == len(band["entries"])
        # brackets run slow -> fast within the band, EXCEPT a shell whose
        # exclusive range holds no whole centisecond (round 3's `empty`,
        # real on tight ladders)
        for division in band["divisions"]:
            if (division["slowCs"] is not None and division["fastCs"] is not None
                    and not division["empty"]):
                assert division["slowCs"] >= division["fastCs"]
    # the Iron floor's slowest shell has no slow edge (the asymptote)
    assert bands[0]["tier"] == "Iron"
    assert bands[0]["divisions"][0]["slowCs"] is None


def test_round3_boundaries_never_share_a_number():
    # Round 3, his words: "each number should be distinct ... There shouldn't
    # be overlap in that way." Across every adjacent pair of units (divisions
    # within a band, and across the band seam), a fast end is strictly faster
    # than the next-slower unit's slow end -- and the seam itself: a tier's
    # displayed fast bound is one centisecond slower than the next tier's own
    # cutoff, which that harder tier keeps (reaching a cutoff earns the rank).
    ladder = {"Bronze": 34.0, "Silver": 33.0, "Gold": 32.0, "Platinum": 31.5,
              "Diamond": 31.0, "Master": 30.5, "Grandmaster": 30.0, "Mario": 29.5}
    bands = run_js(f"m.bandsOf({json.dumps(ladder)}, [])")
    numbers = []
    for band in bands:
        assert band["fastCs"] is not None
        if band["cutoffCs"] is not None:
            numbers.append((band["tier"], band["cutoffCs"]))
        for division in band["divisions"]:
            if not division["empty"]:
                if division["slowCs"] is not None:
                    numbers.append((f"{band['tier']} {division['numeral']} slow",
                                    division["slowCs"]))
                numbers.append((f"{band['tier']} {division['numeral']} fast",
                                division["fastCs"]))
    # adjacent bands: fast bound of the slower = harder tier's cutoff + 1
    for slower, faster in zip(bands, bands[1:]):
        assert slower["fastCs"] == faster["cutoffCs"] + 1, (slower, faster)
    # and within a band, each division's fast end is the faster one's slow
    # edge + 1 (never the same number on two rows)
    for band in bands:
        shells = band["divisions"]
        for lower, upper in zip(shells, shells[1:]):
            if upper["slowCs"] is not None:
                assert lower["fastCs"] == upper["slowCs"] + 1, (band["tier"], lower, upper)


def test_a_ladderless_approach_bands_as_one_unranked_catchall():
    entries = [{"runner": "a", "time_cs": 1500}, {"runner": "b", "time_cs": 1390}]
    bands = run_js(f"m.bandsOf(null, {json.dumps(entries)})")
    assert len(bands) == 1
    assert bands[0]["tier"] is None and bands[0]["divisions"] is None
    assert [e["runner"] for e in bands[0]["entries"]] == ["a", "b"]


def test_band_for_agrees_with_a_hand_walked_non_monotonic_ladder():
    # bandFor's own comment claims it applies classify.rank_for's rule
    # ("the fastest tier a time still beats"), but the two walk the ladder in
    # OPPOSITE directions -- rank_for hardest-first-return-first, bandFor
    # easiest-first-overwrite-last (equivalent, since the hardest tier a time
    # beats is necessarily the LAST one an easy-to-hard scan finds satisfied).
    # A deliberately non-monotonic ladder (Silver's own cutoff looser than
    # Bronze's) is the case that would catch either scan getting this wrong,
    # unlike an ordinary ladder where "first" and "last" trivially coincide.
    # The floor is the registry key "Iron" (capName renders "Capless") since
    # round 1, 2026-08-07.
    ladder = {"Bronze": 20.0, "Silver": 25.0, "Mario": 5.0}
    assert run_js(f"m.bandFor({json.dumps(ladder)}, 1000)") == "Silver"
    assert run_js(f"m.bandFor({json.dumps(ladder)}, 300)") == "Mario"
    assert run_js(f"m.bandFor({json.dumps(ladder)}, 3000)") == "Iron"


def test_video_source_names_a_player_for_every_format_in_the_census():
    # One case per format family the 2026-08-07 census found in the shipped
    # snapshot. The kinds with an `embed` are the ones a click can play
    # inline; "link" is the honest fallback and must never carry an embed.
    cases = {
        "https://youtu.be/abc123XYZ_-": ("youtube", True),
        "https://www.twitch.tv/videos/2112223334?t=1h2m": ("twitch", True),
        "https://clips.twitch.tv/BraveClipSlug-abc123": ("twitch-clip", True),
        "https://www.twitch.tv/runnername/clip/BraveClipSlug-abc123": ("twitch-clip", True),
        "https://x.com/runner/status/1811111111111111111": ("tweet", True),
        "https://twitter.com/runner/status/1811111111111111111?s=20": ("tweet", True),
        # embed.bsky.app takes only a DID (measured live, 2026-08-07): a
        # handle URL ships embed=null and the CARD resolves the DID on click;
        # a did URL embeds directly.
        "https://bsky.app/profile/runner.bsky.social/post/3kabc123def": ("bsky", False),
        "https://bsky.app/profile/did:plc:abc123/post/3kabc123def": ("bsky", True),
        "https://streamable.com/abc123": ("streamable", True),
        "https://drive.google.com/file/d/1AbCdEf/view?usp=sharing": ("gdrive", True),
        "https://cdn.discordapp.com/attachments/1/2/run.mp4": ("file", True),
        "https://files.catbox.moe/abc.mp4": ("file", True),
        "https://i.imgur.com/abc123.png": ("image", False),
        "https://discord.com/channels/1/2/3": ("link", False),
        "https://www.tiktok.com/@runner/video/7222": ("link", False),
    }
    for url, (kind, has_embed) in cases.items():
        got = run_js(f"m.videoSource({json.dumps(url)}, '127.0.0.1')")
        assert got["kind"] == kind, (url, got)
        assert bool(got["embed"]) == has_embed, (url, got)
    # Twitch embeds refuse to load without the embedding page's own host.
    twitch = run_js("m.videoSource('https://www.twitch.tv/videos/123', '127.0.0.1')")
    assert "parent=127.0.0.1" in twitch["embed"]
    assert run_js("m.videoSource(null, 'x')") is None
    # Round 4: every player that can start muted does. X/Bluesky/Drive
    # expose no mute knob (X already autoplays muted by browser policy);
    # native <video> carries the `muted` attribute component-side.
    assert "muted=true" in twitch["embed"]
    clip = run_js("m.videoSource('https://clips.twitch.tv/Slug-abc', '127.0.0.1')")
    assert "muted=true" in clip["embed"]
    streamable = run_js("m.videoSource('https://streamable.com/abc123', 'x')")
    assert "muted=1" in streamable["embed"]
    yt = run_js("m.youtubeEmbed('https://youtu.be/abc123XYZ_-', null)")
    assert "mute=1" in yt


def test_grid_shapes():
    assert [run_js(f"m.gridShape({n})") for n in (1, 2, 3, 5, 9)] == [
        {"rows": 1, "cols": 1}, {"rows": 1, "cols": 2}, {"rows": 2, "cols": 2},
        {"rows": 2, "cols": 3}, {"rows": 3, "cols": 3}]


def test_youtube_helpers_and_search():
    assert run_js("m.youtubeId('https://youtu.be/abc123XYZ_-')") == "abc123XYZ_-"
    assert run_js("m.youtubeId('https://www.youtube.com/watch?v=Qabc123XYZ0&t=91s')") == "Qabc123XYZ0"
    assert run_js("m.youtubeId('https://clips.twitch.tv/x')") is None
    assert run_js("m.youtubeThumb('https://clips.twitch.tv/x')") is None
    assert run_js("m.youtubeThumb('https://youtu.be/abc123XYZ_-')") == \
        "https://i.ytimg.com/vi/abc123XYZ_-/hqdefault.jpg"
    assert run_js("m.youtubeEmbed('https://clips.twitch.tv/x', null)") is None
    assert run_js("m.matchesRunner({runner: 'Kally'}, 'kal')") is True
    assert run_js("m.matchesRunner({runner: 'Kally'}, 'zzz')") is False
    assert run_js("m.matchesRunner({runner: 'Kally'}, '')") is True


def test_last_practiced_takes_the_newest_attempt():
    # views.py::build_session_view ships two SEPARATE top-level arrays --
    # `stars` and `segments`, never one merged `sections` list (confirmed
    # against the real view builder and the JS consumers that already read
    # it this way: focustarget.js, compare.js, practicelog.js). A star
    # section also carries no `kind` key at all (views.py's own comment: the
    # UI branches on `kind` being undefined for stars), which this fixture
    # matches. And attempt recency compares `journal_id`, never the raw
    # `id` -- a reattributed 100-coin attempt keeps a segment-namespace id
    # around 7.5e11 that would outrank every native id forever
    # (tracking-storage.md's "Attempt ordering must use journal_id" law;
    # ui/focustarget.js::newestJournalId is the same rule already shipped).
    view = {"stars": [
        {"course_id": 2, "star_id": 3,
         "attempts": [{"journal_id": 5}, {"journal_id": 9}]}],
        "segments": [
        {"kind": "segment", "segment_id": 4,
         "attempts": [{"journal_id": 7}]}]}
    assert run_js(f"m.lastPracticed({json.dumps(view)})") == "star:2:3"


def test_last_practiced_prefers_the_segment_when_it_is_newest():
    view = {"stars": [
        {"course_id": 2, "star_id": 3, "attempts": [{"journal_id": 5}]}],
        "segments": [
        {"kind": "segment", "segment_id": 4,
         "attempts": [{"journal_id": 11}]}]}
    assert run_js(f"m.lastPracticed({json.dumps(view)})") == "segment:4"


def test_last_practiced_is_null_with_no_attempts_anywhere():
    view = {"stars": [{"course_id": 2, "star_id": 3, "attempts": []}],
            "segments": []}
    assert run_js(f"m.lastPracticed({json.dumps(view)})") is None


def test_tray_to_import_carries_the_trim_as_frames():
    # TASK 6: entity_key rides on the item itself now (task-6-caveats.md
    # point 6), not a second argument -- see test_tray_to_import_reads_the_
    # items_own_entity_key below for the case that argument existing used to
    # hide (a caller passing a DIFFERENT entity than the item's own).
    item = {"runner": "Kally", "time_cs": 4380, "video": "https://youtu.be/z",
            "trim": {"start_s": 12, "end_s": 19.5}, "entity_key": "star:1:0"}
    out = run_js(f"m.trayToImport({json.dumps(item)})")
    assert out["body"]["source_kind"] == "youtube"
    assert out["body"]["entity_key"] == "star:1:0"
    assert out["body"]["source_ref"] == "https://youtu.be/z"
    assert out["body"]["strat"] == "Standard"
    # TASK 5 RULING (task-5-caveats.md point 2): `name` is a pre-filled
    # default for compare.js's editable "name this comparison" field, not a
    # fixed label -- pinned to fmtSeconds (the SAME notation the Library card
    # this item came from already showed for time_cs) rather than the raw
    # `.toFixed(2)` it shipped with, so the number carries over unchanged.
    assert out["body"]["name"] == 'Kally 43"80', out["body"]["name"]
    # in/out_frame are GAME frames on Usamune's 30fps clock (storage/db.py's
    # own comment: "in/out_frame are non-destructive sync bounds in GAME
    # frames"; tracking/comparisons.py::master_seek_time divides by the same
    # 30 default) -- trim seconds x 30, matching GAME_FPS.
    assert out["edit"] == {"in_frame": 360, "out_frame": 585}


def test_tray_to_import_edit_is_null_when_untrimmed():
    item = {"runner": "Kally", "time_cs": 4380, "video": "https://youtu.be/z",
            "entity_key": "star:1:0"}
    out = run_js(f"m.trayToImport({json.dumps(item)})")
    assert out["edit"] is None


def test_tray_to_import_reads_the_items_own_entity_key():
    """A tray can hold items gathered from more than one entity (Task 5 fix
    round 1) -- this is the case a second `entityKey` PARAMETER could get
    wrong (a caller handing in whatever entity the user happens to be
    standing on, rather than the one the item actually came from), which is
    exactly why that parameter is gone."""
    item = {"runner": "Kally", "time_cs": 4380, "video": "https://youtu.be/z",
            "entity_key": "star:2:5"}
    out = run_js(f"m.trayToImport({json.dumps(item)})")
    assert out["body"]["entity_key"] == "star:2:5"


def test_linkable_rows_are_entityless_approaches_and_every_subsection():
    """Round 5: which rows offer the link-to-segment button. A star's
    approaches auto-adopt at scrape time, so linking them again is noise;
    subsections never auto-adopt (the user builds the segment first, his
    2026-08-05 ruling), so every one is linkable -- on star and movement
    targets alike. Rows served by an old snapshot without `row_key` get no
    button: a click that cannot name its row cannot be honest about failing."""
    movement = json.dumps({"entity_key": None})
    star = json.dumps({"entity_key": "star:2:4"})
    keyed = json.dumps({"row_key": "s||t||n||1"})
    unkeyed = json.dumps({})
    assert run_js(f"m.linkable({movement}, {keyed}, 'approach')") is True
    assert run_js(f"m.linkable({star}, {keyed}, 'approach')") is False
    assert run_js(f"m.linkable({star}, {keyed}, 'subsection')") is True
    assert run_js(f"m.linkable({movement}, {keyed}, 'subsection')") is True
    assert run_js(f"m.linkable({movement}, {unkeyed}, 'approach')") is False
