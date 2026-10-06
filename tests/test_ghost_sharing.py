"""Collections carry real poses, credit and portable strategy identity; never PBs."""
import hashlib
import json
from types import SimpleNamespace
import zipfile

import pytest

from sm64_events.ghost.clip import encode_clip, read_clip
from sm64_events.ghost.portable import make_entry, validate_package, write_package
from sm64_events.ghost.sharing import AUTHOR_KEY, GhostSharing
from sm64_events.ghost.sharing_targets import bind, describe
from sm64_events.tracking.segments import validate_definition
from tests.ghost_native_fixture import attempt, frame, producer


class Store:
    def __init__(self, *, definitions=(), attempts=()):
        self.definitions, self.runs, self.state, self.writes = list(definitions), list(attempts), {}, []

    def segment_defs(self):
        return self.definitions

    def attempts(self):
        return self.runs

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value
        self.writes.append(key)


def recording(tmp_path, *, kind="star", pad=(12, -34, 0x8000), level=24, target=None):
    raw = encode_clip([frame(1, igt=0, pad=pad, level=level), frame(2, igt=1, pad=(0, 0, 0), level=level)], producer(),
                      clock_kind=1 if kind == "star" else 2)
    path = tmp_path / "recording.ghost"
    path.write_bytes(raw)
    target = target or {"kind": "star", "entity_key": "2-1", "strat": "10 coin", "name": "Sky"}
    return path, make_entry(path, author="Runner One", target=target, frames=1, clock="igt",
                           recorded_utc="2026-09-30T12:00:00+00:00", practice=False, outcome="success")


def provider(tmp_path, store=None):
    replay = SimpleNamespace(cfg=SimpleNamespace(save_root=tmp_path / "replays"),
                             native_with_inputs=lambda _number, found: found)
    return GhostSharing(replay, store or Store())


def package(tmp_path, *, rows=None, kind="star"):
    if rows is None:
        path, row = recording(tmp_path, kind=kind)
    else:
        row, path = rows[0]
    destination = tmp_path / "collection.sm64ghosts"
    write_package(destination, rows or [(row, path)])
    return destination, path, row


def segment(number, name="Bowser 2", end_level=8):
    return dict(id=number, name=name, enabled=True, start_triggers=[{"type": "level_enter", "to": 34}],
                end_triggers=[{"type": "warp_entered", "level": end_level}], guards=[],
                waypoints=[], parents=[], match_mode="strict", clock_start="trigger")


def test_round_trip_keeps_exact_inputs_and_creator_and_never_writes_pb(tmp_path):
    source, path, row = package(tmp_path)
    sharing = provider(tmp_path)
    answer = sharing.import_file(source)
    assert answer["imported"] == 1 and answer["unmatched"] == 0
    ghost = sharing.imports_for("star", "2-1")[0]
    assert (ghost.author, ghost.strat_tag, ghost.frames, ghost.attempt_id, ghost.pb) == ("Runner One", "10 coin", 1, None, False)
    imported, manifest = sharing.resolve(ghost, {"kind": "star", "entity_key": "2-1"})
    assert imported.read_bytes() == path.read_bytes()
    assert read_clip(imported).frames[0].pad == (12, -34, 0x8000)
    assert manifest["pad_records"] == 2 and manifest["sha256"] == row["sha256"]
    assert sharing.database.writes == []
    assert sharing.import_file(source)["duplicates"] == 1
    assert len(sharing.imports_for("star", "2-1")) == 1


def test_bulk_shared_clip_once_and_foreign_credit_survives_reexport(tmp_path):
    path, first = recording(tmp_path)
    second = make_entry(path, author="Runner Two", target=first["target"], frames=2, clock="igt",
                        recorded_utc=first["recorded_utc"], practice=True, outcome="success")
    source, _, _ = package(tmp_path, rows=[(first, path), (second, path)])
    sharing = provider(tmp_path)
    assert sharing.import_file(source)["imported"] == 2
    assert len(list(sharing.root.glob("*.ghost"))) == 1
    result = sharing.export({"imports": [first["id"], second["id"]]}, "Local Name")
    with zipfile.ZipFile(result["path"]) as bundle:
        rows = json.loads(bundle.read("collection.json"))["ghosts"]
    assert [row["author"] for row in rows] == ["Runner One", "Runner Two"]
    assert [row["practice"] for row in rows] == [False, True]


def test_same_segment_definition_binds_a_different_local_id_and_wrong_definition_does_not(tmp_path):
    original = segment(3)
    target = describe("segment", "3", "Standard", [original])
    assert bind(target, [segment(97, name="My name")]) == ("97", None)
    assert bind(target, [segment(3, end_level=9)])[0] is None
    assert bind(target, [segment(97), segment(98)])[0] is None
    changed_clock = {**segment(97), "clock_start": "counter_zero"}
    assert bind(target, [changed_clock])[0] is None


def test_segment_parent_references_are_portable_not_numeric():
    parent, child = segment(3), segment(4)
    child.update(parents=["segment:3"])
    other_parent, other_child = segment(30), segment(40)
    other_child.update(parents=["segment:30"])
    for row in (parent, child, other_parent, other_child):
        validate_definition(row)
    target = describe("segment", "4", None, [parent, child])
    assert bind(target, [other_parent, other_child]) == ("40", None)
    assert bind(target, [segment(30, end_level=9), other_child])[0] is None


@pytest.mark.parametrize("parents", [["star:2:1"], ["area:6:1"], ["area:6"],
                                     ["star:2:1", "area:6:1"]])
def test_star_and_area_parents_keep_global_identity_across_segment_numbers(parents):
    original, local = segment(3), segment(97)
    original.update(parents=parents)
    local.update(parents=list(parents))
    validate_definition(original)
    target = describe("segment", "3", None, [original])
    assert bind(target, [local]) == ("97", None)
    assert bind(target, [{**local, "parents": ["star:2:2"]}])[0] is None


@pytest.mark.parametrize("parent", [3, None, "3", "segment:", "segment:missing",
                                    "star:2", "area:6:1:2", "route:3", "star:2:1\n"])
def test_malformed_parent_keys_cannot_become_portable_identity(parent):
    row = {**segment(3), "parents": [parent]}
    with pytest.raises(ValueError, match="parent"):
        describe("segment", "3", None, [row])


def test_missing_and_circular_segment_parents_remain_unavailable():
    missing = {**segment(3), "parents": ["segment:99"]}
    with pytest.raises(ValueError, match="missing"):
        describe("segment", "3", None, [missing])
    first, second = segment(3), segment(4)
    first.update(parents=["segment:4"])
    second.update(parents=["segment:3"])
    with pytest.raises(ValueError, match="circular"):
        describe("segment", "3", None, [first, second])


@pytest.mark.parametrize("parents", ["star:2:1", {}, (), ["star:2:1", "star:2:1"]])
def test_malformed_parent_lists_cannot_become_portable_identity(parents):
    row = {**segment(3), "parents": parents}
    with pytest.raises(ValueError, match="parent"):
        describe("segment", "3", None, [row])


def test_whole_collection_is_validated_before_publication(tmp_path):
    source, path, row = package(tmp_path)
    corrupted = tmp_path / "bad.sm64ghosts"
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(corrupted, "w") as changed:
        for name in original.namelist():
            changed.writestr(name, original.read(name) if name == "collection.json" else b"wrong")
    sharing = provider(tmp_path)
    with pytest.raises(ValueError):
        sharing.import_file(corrupted)
    assert sharing.imported_rows() == [] and not sharing._index.exists()
    assert not list(sharing.root.glob("*.ghost"))
    assert sharing.import_file(source)["imported"] == 1


@pytest.mark.parametrize("extra", ["../escape.ghost", "C:/escape.ghost", "clips/unknown.ghost"])
def test_unexpected_or_traversing_paths_are_refused_without_extraction(tmp_path, extra):
    source, _, _ = package(tmp_path)
    with zipfile.ZipFile(source, "a") as bundle:
        bundle.writestr(extra, b"payload")
    with pytest.raises(ValueError, match="unexpected"):
        provider(tmp_path).import_file(source)
    assert not (tmp_path / "escape.ghost").exists()


def test_replaced_imported_bytes_refuse_playback(tmp_path):
    source, _, _ = package(tmp_path)
    sharing = provider(tmp_path)
    sharing.import_file(source)
    ghost = sharing.imports_for("star", "2-1")[0]
    path, _ = sharing.resolve(ghost, {"kind": "star", "entity_key": "2-1"})
    path.write_bytes(b"other")
    with pytest.raises(ValueError, match="hash"):
        sharing.resolve(ghost, {"kind": "star", "entity_key": "2-1"})


def test_kind_metadata_cannot_relabel_recorded_clock(tmp_path):
    source, _, _ = package(tmp_path, kind="segment")
    with pytest.raises(ValueError, match="clock"):
        provider(tmp_path).import_file(source)


@pytest.mark.parametrize("kind", ["star", "segment"])
@pytest.mark.parametrize("course,level", [(1, 9), (2, 24), (15, 15)])
def test_hundred_coin_star_round_trip_preserves_its_actual_native_clock(tmp_path, kind, course, level):
    target = {"kind": "star", "entity_key": f"{course}-6", "strat": "Exit star", "name": "100 Coins"}
    path, row = recording(tmp_path, kind=kind, level=level, target=target)
    source, _, _ = package(tmp_path, rows=[(row, path)])
    sharing = provider(tmp_path)
    assert sharing.import_file(source)["imported"] == 1
    ghost = sharing.get(row["id"])
    imported, _ = sharing.resolve(ghost, {"kind": "star", "entity_key": target["entity_key"]})
    assert read_clip(imported).clock_kind == (1 if kind == "star" else 2)
    assert sharing.database.writes == []


@pytest.mark.parametrize("key,level", [("2-1", 24), ("0-0", 6), ("19-0", 27)])
def test_segment_clock_cannot_relabel_an_ordinary_or_secret_star(tmp_path, key, level):
    path, row = recording(tmp_path, kind="segment", level=level, target={"kind": "star", "entity_key": key})
    source, _, _ = package(tmp_path, rows=[(row, path)])
    with pytest.raises(ValueError, match="clock"):
        provider(tmp_path).import_file(source)


@pytest.mark.parametrize("key,level", [
    ("0-0", 6), ("0-0", 16), ("0-0", 26),  # castle rewards have no course level
    ("2-1", 24), ("16-0", 17), ("17-0", 19), ("18-0", 21),
    ("19-0", 27), ("20-0", 28), ("21-0", 29), ("22-0", 18), ("23-0", 31), ("24-0", 20),
])
def test_star_scene_validation_keeps_castle_and_secret_course_recordings(tmp_path, key, level):
    path, row = recording(tmp_path, level=level, target={"kind": "star", "entity_key": key})
    source, _, _ = package(tmp_path, rows=[(row, path)])
    assert provider(tmp_path).import_file(source)["imported"] == 1


@pytest.mark.parametrize("key,level", [("8-1", 24), ("2-1", 6), ("0-0", 24), ("0-0", 30), ("19-0", 24)])
def test_star_metadata_cannot_relabel_a_different_recorded_course(tmp_path, key, level):
    path, row = recording(tmp_path, level=level, target={"kind": "star", "entity_key": key})
    source, _, _ = package(tmp_path, rows=[(row, path)])
    with pytest.raises(ValueError, match="course"):
        provider(tmp_path).import_file(source)


def test_shared_bytes_are_checked_against_each_target_before_collection_publication(tmp_path):
    path, first = recording(tmp_path)
    wrong = make_entry(path, author=first["author"], target={"kind": "star", "entity_key": "8-1"},
                       frames=first["frames"], clock=first["clock"], recorded_utc=first["recorded_utc"],
                       practice=False, outcome="success")
    source, _, _ = package(tmp_path, rows=[(first, path), (wrong, path)])
    sharing = provider(tmp_path)
    with pytest.raises(ValueError, match="course"):
        sharing.import_file(source)
    assert sharing.imported_rows() == [] and not sharing._index.exists()
    assert not list(sharing.root.glob("*.ghost"))


def test_export_bound_counts_unique_clip_bytes_and_manifest_at_the_exact_import_bound(tmp_path, monkeypatch):
    path, first = recording(tmp_path)
    second = make_entry(path, author="Second Runner", target=first["target"], frames=first["frames"],
                        clock=first["clock"], recorded_utc=first["recorded_utc"], practice=False, outcome="success")
    rows = [(first, path), (second, path)]
    reference = tmp_path / "reference.sm64ghosts"
    write_package(reference, rows)
    with zipfile.ZipFile(reference) as bundle:
        total = sum(item.file_size for item in bundle.infolist())
        metadata_bytes = bundle.getinfo("collection.json").file_size
        assert len(bundle.infolist()) == 2  # two credits share one recording
    monkeypatch.setattr("sm64_events.ghost.portable.MAX_UNPACKED_BYTES", total)
    destination = tmp_path / "exact.sm64ghosts"
    write_package(destination, rows)
    staging = tmp_path / "staging"; staging.mkdir()
    checked, _ = validate_package(destination, staging)
    assert [row["author"] for row in checked] == [first["author"], "Second Runner"]
    monkeypatch.setattr("sm64_events.ghost.portable.MAX_UNPACKED_BYTES", total - 1)
    refused = tmp_path / "refused.sm64ghosts"
    with pytest.raises(ValueError, match="large"):
        write_package(refused, rows)
    assert not refused.exists()
    monkeypatch.setattr("sm64_events.ghost.portable.MAX_UNPACKED_BYTES", total)
    monkeypatch.setattr("sm64_events.ghost.portable.MAX_MANIFEST_BYTES", metadata_bytes - 1)
    with pytest.raises(ValueError, match="metadata"):
        write_package(refused, rows)
    assert not refused.exists()


def test_local_export_saves_recent_poses_and_reports_unavailable_selected_runs(tmp_path):
    path, _row = recording(tmp_path)
    run = attempt()
    run.__dict__.update(course_id=2, star_id=1, segment_id=None, igt_frames=2, strat_tag="10 coin",
                        outcome="success", anchor_type="practice_reset", valid=True, invalid_reason=None)
    sharing = provider(tmp_path, Store(attempts=[run]))
    saved = []
    sharing.replay.attempt = lambda number: run if number == 77 else (_ for _ in ()).throw(LookupError("Run removed"))
    sharing.replay.preserve_native = lambda number: (saved.append(number) or {"path": str(path)})
    sharing.replay.find_native = lambda _number: (path, {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    result = sharing.export({"attempts": [77, 78]}, "My Name")
    assert saved == [77] and result["exported"] == 1
    assert result["skipped"] == [{"source": "local", "id": 78, "reason": "Run removed"}]
    assert sharing.database.writes == [AUTHOR_KEY]
    staging = tmp_path / "staging"; staging.mkdir()
    rows, _ = validate_package(result["path"], staging)
    assert rows[0]["author"] == "My Name" and rows[0]["frames"] == 2


def test_unknown_inputs_remain_unknown_in_collection(tmp_path):
    path, row = recording(tmp_path, pad=None)
    source, _, _ = package(tmp_path, rows=[(row, path)])
    sharing = provider(tmp_path)
    sharing.import_file(source)
    ghost = sharing.get(row["id"])
    imported, manifest = sharing.resolve(ghost, {"kind": "star", "entity_key": "2-1"})
    assert read_clip(imported).frames[0].pad is None
    assert manifest["pad_records"] == 1
