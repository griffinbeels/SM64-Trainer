"""A public recording belongs to the exact attempt, across import and replay."""
import asyncio
from dataclasses import replace

import pytest

from sm64_events.server.scorecard_api import _column_resolve, _held_lookup
from sm64_events.tracking.importing import ImportCandidate
from test_service_import import make


URL = "https://youtu.be/example123?t=12"
OTHER = "https://www.youtube.com/watch?v=another123&t=42"


def brought(**kwargs):
    return ImportCandidate("star:1:0", "Standard", 900,
                           row_key="sheet-row", video=URL, **kwargs)


def land(svc, candidate, source="sheet:Runner"):
    return asyncio.run(svc.import_times(source, [candidate]))


def test_original_link_survives_reprojection_and_new_pb_is_unlinked(tmp_path):
    db, svc = make(tmp_path)
    land(svc, brought())
    first = db.attempts()[0].id
    assert svc.recording_link(first) == {"url": URL, "revision": 0}
    assert _column_resolve(svc)("star:1:0", "Standard", "igt", None)[2] == URL
    asyncio.run(svc._reproject())
    assert svc.recording_link(first)["url"] == URL
    land(svc, replace(brought(), time_cs=800, video=None))
    assert _column_resolve(svc)("star:1:0", "Standard", "igt", None)[:2] == (800, None)
    assert len(_column_resolve(svc)("star:1:0", "Standard", "igt", None)) == 2
    assert svc.recording_link(first)["url"] == URL


def test_edit_remove_undo_and_stale_undo_survive_reimport_and_reprojection(tmp_path):
    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id
    changed = asyncio.run(svc.set_recording_link(aid, f"  {OTHER}  ", 0))
    assert changed == {"url": OTHER, "revision": 1}
    removed = asyncio.run(svc.set_recording_link(aid, None, 1))
    assert removed == {"url": None, "revision": 2}
    land(svc, brought())
    asyncio.run(svc._reproject())
    assert svc.recording_link(aid) == removed
    assert asyncio.run(svc.set_recording_link(aid, OTHER, 2))["revision"] == 3
    with pytest.raises(ValueError, match="changed"):
        asyncio.run(svc.set_recording_link(aid, URL, 2))
    assert svc.recording_link(aid)["url"] == OTHER


def test_historic_same_source_import_backfills_without_duplicate(tmp_path):
    db, svc = make(tmp_path)
    land(svc, ImportCandidate("star:1:0", "Standard", 900))
    aid = db.attempts()[0].id
    result = land(svc, brought())
    assert result["imported"] == 0
    assert len(db.attempts()) == 1
    assert svc.recording_link(aid)["url"] == URL
    asyncio.run(svc.set_recording_link(aid, None))
    land(svc, brought())
    assert svc.recording_link(aid)["url"] is None


def test_historic_backfill_refuses_other_source_or_ambiguous_rows(tmp_path):
    db, svc = make(tmp_path)
    land(svc, ImportCandidate("star:1:0", "Standard", 900), "manual")
    aid = db.attempts()[0].id
    land(svc, brought())
    assert svc.recording_link(aid)["url"] is None
    asyncio.run(svc.remove_imported("manual"))
    land(svc, ImportCandidate("star:1:0", "Standard", 900))
    aid = db.attempts()[0].id
    asyncio.run(svc.import_times("sheet:Runner", [
        brought(), replace(brought(), row_key="different-row", video=OTHER)]))
    assert svc.recording_link(aid)["url"] is None


def test_provenance_disambiguates_rows_with_same_time(tmp_path):
    db, svc = make(tmp_path)
    land(svc, replace(brought(), video=None))
    aid = db.attempts()[0].id
    asyncio.run(svc.import_times("sheet:Runner", [
        brought(), replace(brought(), row_key="different-row", video=OTHER)]))
    assert svc.recording_link(aid)["url"] == URL


def test_held_cell_keeps_original_recording_for_export(tmp_path):
    db, svc = make(tmp_path)
    asyncio.run(svc.import_times("sheet:Runner", [], held=[{
        "row_key": "held-row", "time_cs": 806, "video": URL,
        "reason": "subsections", "platform": "n64"}]))
    assert db.held_times()[0]["video"] == URL
    assert _held_lookup(svc)("held-row", None) == (806, "n64", URL)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///clip.mp4",
                                  "https://user:pass@example.com/video", "bad"])
def test_bad_urls_never_change_a_saved_link(tmp_path, url):
    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id
    with pytest.raises(ValueError):
        asyncio.run(svc.set_recording_link(aid, url))
    assert svc.recording_link(aid) == {"url": URL, "revision": 0}


def test_missing_attempt_is_not_an_empty_recording(tmp_path):
    _db, svc = make(tmp_path)
    with pytest.raises(LookupError):
        svc.recording_link(876543)
    with pytest.raises(LookupError):
        asyncio.run(svc.set_recording_link(876543, URL))


def test_played_equal_time_is_never_backfilled_but_can_be_edited(tmp_path):
    from test_tracker_service import ev, star

    db, svc = make(tmp_path)
    asyncio.run(svc.set_target(1, 0, strat_tag="Standard"))
    asyncio.run(svc.publish(ev("practice_reset", 1000)))
    asyncio.run(svc.publish(star(1270, course=1, star_id=0, igt=270)))
    attempt = db.attempts()[0]
    db.insert_pb(1, 0, "Standard", "igt", 270, attempt.id,
                 "2026-09-05T00:00:00Z")
    assert land(svc, brought())["imported"] == 0
    assert svc.recording_link(attempt.id) == {"url": None, "revision": 0}
    asyncio.run(svc.set_recording_link(attempt.id, URL, 0))
    asyncio.run(svc._reproject())
    assert svc.recording_link(attempt.id) == {"url": URL, "revision": 1}


def test_competing_conditional_edits_have_exactly_one_winner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id

    def save(url):
        try:
            return asyncio.run(svc.set_recording_link(aid, url, 0))
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, [OTHER, None]))
    assert results.count("conflict") == 1
    assert svc.recording_link(aid)["revision"] == 1


def test_removal_survives_database_reopen(tmp_path):
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.storage.db import Database
    from sm64_events.tracking.service import TrackerService

    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id
    asyncio.run(svc.set_recording_link(aid, None))
    db.close()
    reopened = Database(tmp_path / "t.db")
    restarted = TrackerService(reopened, Broadcaster())
    asyncio.run(restarted.start())
    land(restarted, brought())
    assert restarted.recording_link(aid) == {"url": None, "revision": 1}


def test_column_links_follow_chosen_rom_and_leftover_strategy(tmp_path):
    _db, svc = make(tmp_path)
    land(svc, replace(brought(game_version="jp"), video=OTHER))
    land(svc, replace(brought(game_version="us"), time_cs=800))
    resolve = _column_resolve(svc)
    assert resolve("star:1:0", "Standard", "igt", "jp")[2] == OTHER
    assert resolve("star:1:0", None, "igt", "us")[2] == URL
    assert resolve("star:1:0", None, "igt", "us", excluding={"Standard"}) is None


def test_ronc3na_links_reach_saved_attempts_holds_and_export(tmp_path):
    from collections import Counter

    from sm64_events.library.audit import row_key
    from sm64_events.library.import_runner import candidates_for
    from sm64_events.server.import_api import sheet_row_placer
    from sm64_events.server.scorecard_api import _column_identity
    from test_import_runner import payload

    source = payload()
    originals = {(row_key(target, item["name"], item.get("ids") or ()),
                  entry.get("version")): entry.get("video")
                 for target in source["targets"]
                 for collection in ("approaches", "subsections")
                 for item in target.get(collection, [])
                 for entry in item.get("entries", [])
                 if entry["runner"] == "RONC3NA"}
    assert sum(bool(url) for url in originals.values()) == 132
    db, svc = make(tmp_path)
    candidates, held = candidates_for(source, "RONC3NA", sheet_row_placer(svc, None))
    asyncio.run(svc.import_times("sheet:RONC3NA", candidates, held=held))
    events = [event for event in db.events() if event.type == "time_imported"]
    saved = [svc.recording_link(event.id)["url"] for event in events]
    saved.extend(cell["video"] for cell in db.held_times())
    assert Counter(url for url in saved if url) == Counter(
        url for url in originals.values() if url)
    resolve, held_resolve = _column_resolve(svc), _held_lookup(svc)
    by_id = {event.id: event.payload for event in events}
    for candidate in candidates:
        time = resolve(candidate.entity_key, candidate.strat_tag,
                       candidate.timer_mode, candidate.game_version)
        course, star, segment = _column_identity(candidate.entity_key)
        chosen = db.current_pb(course, star, candidate.timer_mode,
                               segment_id=segment, strat_tag=candidate.strat_tag,
                               game_version=candidate.game_version)
        origin = by_id[chosen["attempt_id"]]
        expected = originals[origin["row_key"], origin["game_version"]]
        assert (time[2] if len(time) == 3 else None) == expected
    for cell in db.held_times():
        time = held_resolve(cell["row_key"], cell["game_version"])
        expected = originals[cell["row_key"], cell["game_version"]]
        assert (time[2] if len(time) == 3 else None) == expected


@pytest.mark.parametrize("runner, source_url, accepted_url", [
    ("Twig64", "http://books/", None),
    ("Shans", "www.youtube.com/watch?v=u_SFBWwS4g8",
     "https://www.youtube.com/watch?v=u_SFBWwS4g8"),
    ("Falcon", "youtube.com/watch?v=-DuUClItqkw",
     "https://youtube.com/watch?v=-DuUClItqkw"),
])
def test_real_runner_bad_optional_recording_does_not_reject_times(
        tmp_path, runner, source_url, accepted_url):
    from sm64_events.library.import_runner import candidates_for
    from sm64_events.server.import_api import sheet_row_placer
    from test_import_runner import payload

    db, svc = make(tmp_path)
    candidates, held = candidates_for(payload(), runner, sheet_row_placer(svc, None))
    original_links = [candidate.video for candidate in candidates]
    original_links.extend(cell.get("video") for cell in held)
    assert source_url in original_links, "the regression must still exist in the source fixture"
    expected = svc._plan_import(candidates).summary["imported"]
    result = asyncio.run(svc.import_times(f"sheet:{runner}", candidates, held=held))
    assert result["imported"] == expected > 0
    assert len(db.held_times()) == len(held)
    stored = [svc.recording_link(attempt.id)["url"] for attempt in db.attempts()]
    stored.extend(cell["video"] for cell in db.held_times())
    assert source_url not in stored
    if accepted_url:
        assert accepted_url in stored
    else:
        assert result["recordings_skipped"] >= 1


def test_invalid_optional_source_links_skip_only_recordings(tmp_path):
    db, svc = make(tmp_path)
    result = asyncio.run(svc.import_times("sheet:Runner", [
        brought(), replace(brought(), entity_key="star:2:0", video="javascript:alert(1)")],
        held=[{"row_key": "bad-held", "time_cs": 806, "video": "http://localhost/video"}]))
    assert result["imported"] == 2
    assert result["recordings_skipped"] == 2
    assert [svc.recording_link(attempt.id)["url"] for attempt in db.attempts()] == [URL, None]
    assert db.held_times()[0]["video"] is None


@pytest.mark.parametrize("erase", ["import", "session", "all"])
def test_erasing_history_erases_its_stored_recording_overlays(tmp_path, erase):
    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id
    asyncio.run(svc.set_recording_link(aid, OTHER))
    assert db._conn.execute("SELECT url FROM attempt_recordings").fetchall()
    if erase == "import":
        asyncio.run(svc.remove_imported("sheet:Runner"))
    elif erase == "session":
        session = svc.session_id
        asyncio.run(svc.new_session())
        asyncio.run(svc.delete_session(session))
    else:
        asyncio.run(svc.wipe_data("all", scope="lifetime"))
    assert db._conn.execute("SELECT url FROM attempt_recordings").fetchall() == []


def test_clearing_then_restoring_attempt_keeps_its_recording_edit(tmp_path):
    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id
    asyncio.run(svc.set_recording_link(aid, OTHER))
    asyncio.run(svc.clear_attempt(aid, reason="accidental"))
    assert svc.recording_link(aid) == {"url": OTHER, "revision": 1}
    asyncio.run(svc.restore_attempt(aid))
    assert svc.recording_link(aid) == {"url": OTHER, "revision": 1}


def test_recording_cleanup_uses_journal_ownership_not_temporary_cache_absence(tmp_path):
    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id
    asyncio.run(svc.set_recording_link(aid, OTHER))
    db.replace_attempts([])
    assert db.delete_orphaned_recordings() == 0
    asyncio.run(svc._reproject())
    assert svc.recording_link(aid) == {"url": OTHER, "revision": 1}


def test_startup_repairs_recording_left_by_older_history_deletion(tmp_path):
    from sm64_events.server.broadcaster import Broadcaster
    from sm64_events.tracking.service import TrackerService

    db, svc = make(tmp_path)
    land(svc, brought())
    aid = db.attempts()[0].id
    asyncio.run(svc.set_recording_link(aid, OTHER))
    db.delete_events([aid])
    restarted = TrackerService(db, Broadcaster())
    asyncio.run(restarted.start())
    assert db._conn.execute("SELECT url FROM attempt_recordings").fetchall() == []
