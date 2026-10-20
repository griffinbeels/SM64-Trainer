"""Published saved facts preserve the existing PB and grading contracts."""
from copy import deepcopy
import gzip
import json
from types import SimpleNamespace

from fastapi import HTTPException
import pytest

from sm64_events.community import CommunityError
from sm64_events.community import progress_snapshot as progress
from sm64_events.core.paths import bundled_defaults_seed, bundled_sheet_library
from sm64_events.core.timefmt import frame_at_or_after
from sm64_events.ghost.sharing_targets import canonical
from sm64_events.library.import_runner import candidates_for
from sm64_events.ranks.scorecard import build_card, card_keys, without_keys
from sm64_events.server.ranks_api import _score_scope
from sm64_events.server.scorecard_resolve import GoalResolver
from sm64_events.server.scorecard_rows import scope_rows
from sm64_events.tracking.importing import decide
from test_marelo_bridge import att
from test_overall_consumers import KEY, Standards

SAVED = "2026-10-19T12:34:56Z"
ACHIEVED = "2026-10-18T01:02:03Z"


def pb(identifier=1, **fields):
    return {"id": identifier, "course_id": 1, "star_id": 0, "segment_id": None, "strat_tag": "Standard",
            "timer_mode": "igt", "frames": 600, "attempt_id": None, "saved_utc": SAVED,
            "game_version": None, "pb_version": None, "imported_from": None, **fields}


class Database:
    def __init__(self, rows=(), definitions=(), attempts=(), routes=()):
        self.rows, self.definitions, self.history, self.route_rows = list(rows), list(definitions), list(attempts), list(routes)
    def pbs(self):
        return deepcopy(self.rows)
    def segment_defs(self):
        return deepcopy(self.definitions)
    def attempt(self, identifier):
        return next((row for row in self.history if row.id == identifier), None)
    def attempts(self):
        return list(self.history)
    def routes(self):
        return deepcopy(self.route_rows)
    def get_state(self, name, default=None):
        return "avg10" if name == "rank_mode" else default


def service(rows=(), definitions=(), attempts=(), *, version="us", routes=()):
    ranks = Standards()
    ranks.grading_version = version
    ranks.graded_entities = lambda: {KEY}
    return SimpleNamespace(db=Database(rows, definitions, attempts, routes), ranks=ranks,
                           effective_game_version=lambda: version, rank_excluded=lambda: set())


def seed_definitions():
    data = json.loads(bundled_defaults_seed().read_text(encoding="utf-8"))
    return [{**row, "id": number + 1000} for number, row in enumerate(data["segments"], 1)]


def test_latest_save_slower_replacement_undo_deletion_and_untagged_are_full_replacements():
    owner = service([pb(11, frames=300), pb(22, frames=900), pb(33, strat_tag=None, frames=700)])
    snapshot = progress.project(owner)
    assert [(row["strategy"], row["frames"], row["save_order"]) for row in snapshot["rows"]] == [("Standard", 900, 1), (None, 700, 2)]
    assert snapshot["coverage"] == {"total_rows": 2, "shared_rows": 2, "withheld_rows": 0, "unsupported_rows": 0, "complete": True}
    owner.db.rows.pop(1)  # Undo the deliberately slower latest save.
    assert progress.project(owner)["rows"][0]["frames"] == 300
    owner.db.rows.clear()
    assert progress.project(owner)["rows"] == []


def test_save_order_preserves_reassertion_without_publishing_database_ids_or_changing_dates():
    owner = service([pb(110, frames=600, game_version="jp", pb_version="jp"),
                     pb(900, frames=650, game_version="us", pb_version="us"),
                     pb(1500, frames=500, saved_utc="2026-01-01T00:00:00Z")])
    snapshot = progress.project(owner)
    assert [row["save_order"] for row in snapshot["rows"]] == [1, 2, 3]
    assert snapshot["rows"][-1]["saved_utc"] == "2026-01-01T00:00:00Z"
    assert all(not ({"id", "attempt_id", "segment_id", "course_id"} & row.keys()) for row in snapshot["rows"])
    resolved, unbound = progress.resolve_rows(snapshot)
    assert [row["id"] for row in resolved] == [1, 2, 3] and unbound == 0
    assert resolved[-1]["game_version"] is None and resolved[-1]["pb_version"] is None


@pytest.mark.parametrize("source,kind", [(None, "played"), ("manual", "manual"), ("sheet:Actual private claim", "sheet"),
                                        ("https://private.invalid/secret-sheet", "imported")])
def test_sources_and_achievement_dates_never_turn_import_time_into_play_time(source, kind):
    attempt = att(id=77, ended_utc=ACHIEVED, timed_by="imported" if source else "igt")
    owner = service([pb(attempt_id=77, imported_from=source)], attempts=[attempt])
    row = progress.project(owner)["rows"][0]
    assert row["source_kind"] == kind and row["source_cs"] is None
    assert row["achieved_utc"] == (ACHIEVED if kind == "played" else None)
    assert row["saved_utc"] == SAVED
    assert "private" not in canonical(row).decode() and "https://" not in canonical(row).decode()


def test_unknown_dates_and_raw_and_joined_rom_remain_distinct():
    snapshot = progress.project(service([pb(saved_utc="legacy unknown", pb_version="jp")], version="jp"))
    row = snapshot["rows"][0]
    assert row["saved_utc"] is None and row["achieved_utc"] is None and row["source_kind"] == "legacy"
    assert snapshot["default_rom"] == "jp" and row["game_version"] is None and row["pb_version"] == "jp"
    resolved, _ = progress.resolve_rows(snapshot)
    assert resolved[0]["game_version"] is None and resolved[0]["pb_version"] == "jp"


def test_default_segment_uses_canonical_public_label_and_binds_by_signature_to_another_local_id():
    definitions = seed_definitions()
    definition = definitions[0]
    canonical_name = definition["name"]
    definition["name"] = "Private local label"
    row = pb(course_id=None, star_id=None, segment_id=definition["id"], timer_mode="rta")
    snapshot = progress.project(service([row], definitions))
    target = snapshot["rows"][0]["target"]
    assert target["entity_key"] == "1" and target["name"] == canonical_name
    assert "Private" not in canonical(snapshot).decode() and str(definition["id"]) not in canonical(target).decode()
    viewer_defs = [{**item, "id": item["id"] + 5000} for item in definitions]
    resolved, unbound = progress.resolve_rows(snapshot, viewer_defs)
    assert unbound == 0 and resolved[0]["segment_id"] == definition["id"] + 5000
    assert progress.resolve_rows(snapshot, ())[1] == 1


def test_custom_or_edited_segments_are_withheld_with_honest_coverage_and_missing_targets_are_unsupported():
    definitions = seed_definitions()
    definitions[0]["clock_start"] = "trigger" if definitions[0]["clock_start"] == "move" else "move"
    definitions.append({**definitions[1], "id": 9999, "seed_key": None, "name": "Secret custom segment"})
    rows = [pb(), pb(2, course_id=None, star_id=None, segment_id=definitions[0]["id"]),
            pb(3, course_id=None, star_id=None, segment_id=9999), pb(4, course_id=None, star_id=None, segment_id=9998)]
    snapshot = progress.project(service(rows, definitions))
    assert snapshot["coverage"] == {"total_rows": 4, "shared_rows": 1, "withheld_rows": 2, "unsupported_rows": 1, "complete": False}
    assert "Secret custom" not in canonical(snapshot).decode()


@pytest.mark.parametrize("regions", [["us"], ["jp"], ["us", "jp"]])
def test_remote_rows_use_the_exact_scorecard_reader_for_regions_untagged_and_slower_saves(regions):
    rows = [pb(1, frames=450, game_version="us", pb_version="us"), pb(2, frames=900, game_version="us", pb_version="us"),
            pb(3, frames=300, game_version="jp", pb_version="jp"), pb(4, star_id=6, frames=1200, strat_tag=None),
            pb(5, star_id=1, frames=600, strat_tag="Different method")]
    owner = service(rows)
    resolved, _ = progress.resolve_rows(progress.project(owner))
    spec = without_keys(scope_rows(owner, "overall"), {"star:1:1"})
    keys = card_keys(spec)
    resolver = GoalResolver(owner)
    local = resolver.your_times(keys, regions)
    remote = resolver.your_times(keys, regions, pb_rows=resolved)
    assert local == remote and "star:1:1" not in remote and "star:1:6" in remote
    assert local[KEY] == ({"us": 3000, "jp": 1000}[regions[0]] if len(regions) == 1 else 1000)
    card = build_card(spec, you=local, goal=remote)
    assert all(tile["delta_cs"] == 0 for row in card["rows"] for tile in row["tiles"] if tile["you_cs"] is not None)
    assert card["total"]["delta_cs"] == 0
    assert resolver.your_times(keys, regions, pb_rows=[]) == {}


def test_remote_rank_uses_current_saved_pb_mode_and_never_viewers_private_average_attempts():
    owner = service([pb(frames=600)], attempts=[att(igt_frames=1200)])
    before = _score_scope(owner, "overall")  # Existing local avg10 selection remains authoritative locally.
    rows, _ = progress.resolve_rows(progress.project(owner))
    remote = _score_scope(owner, "overall", pb_rows=rows)
    expected = _score_scope(owner, "overall", mode="pb")
    assert remote["marelo"] == expected["marelo"] and remote["marelo"] != before["marelo"]
    assert _score_scope(owner, "overall") == before and owner.db.get_state("rank_mode") == "avg10"
    with pytest.raises(ValueError, match="average"):
        _score_scope(owner, "overall", pb_rows=rows, mode="avg10")


def test_unstamped_owner_context_and_deliberate_viewer_context_remain_distinct_without_forging_a_rom():
    def contextual(version, rows):
        context = service(rows, version=version)
        context.ranks.overall_curve = lambda _key, version=None: context.ranks.by_version[version or context.ranks.grading_version]
        return context
    owner = contextual("jp", [pb(frames=600, pb_version="jp")])
    content = progress.project(owner)
    rows, _ = progress.resolve_rows(content)
    local = _score_scope(owner, "overall", mode="pb")
    owner_reading = _score_scope(owner, "overall", pb_rows=rows)
    viewer_reading = _score_scope(contextual("us", []), "overall", pb_rows=rows)
    assert content["default_rom"] == "jp" and rows[0]["game_version"] is None
    assert owner_reading["marelo"] == local["marelo"] != viewer_reading["marelo"]
    # A stamped record retains its recorded context under either viewer default.
    rows[0]["game_version"] = "jp"
    assert _score_scope(owner, "overall", pb_rows=rows)["marelo"] == _score_scope(contextual("us", []), "overall", pb_rows=rows)["marelo"]


@pytest.mark.parametrize("change", ["schema", "extra", "counts", "complete", "frames", "rom", "order", "duplicate", "date", "import_date", "private_target"])
def test_received_snapshot_is_strict_and_never_silently_clips(change):
    content = progress.project(service([pb()]))
    row = content["rows"][0]
    if change == "schema": content["schema"] = True
    elif change == "extra": content["private"] = "secret"
    elif change == "counts": content["coverage"]["total_rows"] = 2
    elif change == "complete": content["coverage"]["complete"] = False
    elif change == "frames": row["frames"] = True
    elif change == "rom": row["game_version"] = "jp"
    elif change == "order": row["save_order"] = True
    elif change == "duplicate":
        content["rows"].append({**deepcopy(row), "save_order": 2})
        content["coverage"].update(total_rows=2, shared_rows=2)
    elif change == "date": row["saved_utc"] = "2026-10-19"
    elif change == "import_date": row.update(source_kind="manual", achieved_utc=ACHIEVED)
    else: row["target"]["segment_id"] = 123
    with pytest.raises(CommunityError): progress.checked_snapshot(content)


def test_oversized_projection_fails_instead_of_publishing_a_truncated_complete_profile(monkeypatch):
    owner = service([pb(1), pb(2, star_id=1), pb(3, star_id=2)])
    monkeypatch.setattr(progress, "MAX_ROWS", 2)
    with pytest.raises(CommunityError) as error: progress.project(owner)
    assert error.value.code == "progress_snapshot_too_large"
    monkeypatch.setattr(progress, "MAX_ROWS", 4096)
    content = progress.project(owner)
    monkeypatch.setattr(progress, "MAX_BYTES", len(canonical(content)) - 1)
    with pytest.raises(CommunityError) as error: progress.project(owner)
    assert error.value.code == "progress_snapshot_too_large"


def test_shared_scope_rows_preserve_course_route_order_and_unknown_scope_errors():
    owner = service(routes=[{"id": 42, "name": "My route", "steps": [{"need": 1, "candidates": [
        {"type": "star", "course": 1, "star": 0}, {"type": "star", "course": 1, "star": 6}]}]}])
    assert {KEY, "star:1:6"} <= set(card_keys(scope_rows(owner, "course:1")))
    assert set(card_keys(scope_rows(owner, "route:42"))) == {KEY, "star:1:6"}
    for name in ("garbage", "route:999999", "course:999999"):
        with pytest.raises(HTTPException) as error: scope_rows(owner, name)
        assert error.value.status_code == 404


def test_largest_bundled_sheet_column_fits_atomic_limits_and_full_projection_uses_real_import_mapping():
    with gzip.open(bundled_sheet_library(), "rt", encoding="utf-8") as stream: payload = json.load(stream)
    candidates, _ = candidates_for(payload, "Raisn")
    rows = []
    for identifier, candidate in enumerate(candidates, 1):
        _, course, star = candidate.entity_key.split(":")
        rows.append(pb(identifier, course_id=int(course), star_id=int(star), frames=frame_at_or_after(candidate.time_cs),
                       strat_tag=candidate.strat_tag or None, game_version=candidate.game_version, pb_version=candidate.game_version,
                       imported_from="sheet:Raisn"))
    snapshot = progress.project(service(rows))
    assert snapshot["coverage"]["complete"] and len(snapshot["rows"]) > 200
    assert len(canonical(snapshot)) < progress.MAX_BYTES and len(snapshot["rows"]) < progress.MAX_ROWS


def test_all_bundled_importable_columns_roundtrip_through_real_import_selector_and_scorecard_reader():
    with gzip.open(bundled_sheet_library(), "rt", encoding="utf-8") as stream: payload = json.load(stream)
    spec = scope_rows(service(), "overall")
    keys = card_keys(spec)
    covered = set()
    for runner in payload["runners"]:
        candidates, _ = candidates_for(payload, runner)
        rows = []
        for identifier, (candidate, frames) in enumerate(decide(candidates, lambda *_: None).landing, 1):
            _, course, star = candidate.entity_key.split(":")
            covered.add((candidate.entity_key, candidate.strat_tag, candidate.game_version))
            rows.append(pb(identifier, course_id=int(course), star_id=int(star), frames=frames,
                           strat_tag=candidate.strat_tag or None, game_version=candidate.game_version,
                           pb_version=candidate.game_version, imported_from="sheet:" + runner))
        owner = service(rows)
        snapshot = progress.project(owner)
        remote, unbound = progress.resolve_rows(snapshot)
        assert snapshot["coverage"]["complete"] and unbound == 0, runner
        resolver = GoalResolver(owner)
        for regions in (["us"], ["jp"], ["us", "jp"]):
            local_times = resolver.your_times(keys, regions)
            remote_times = resolver.your_times(keys, regions, pb_rows=remote)
            assert remote_times == local_times, (runner, regions)
            assert all(tile["delta_cs"] == 0 for row in build_card(spec, you=local_times, goal=remote_times)["rows"]
                       for tile in row["tiles"] if tile["you_cs"] is not None), (runner, regions)
    assert covered and {"us", "jp"} <= {version for _key, _strategy, version in covered}
    assert len({strategy for _key, strategy, _version in covered}) > 1
