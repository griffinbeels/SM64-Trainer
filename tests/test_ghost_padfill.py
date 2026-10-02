"""A native ghost recorded before pad capture shows the inputs its attempt's
track holds, frame for frame; captured pads, holes and ambiguous joins stay as
they are (ghost/padfill.py)."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from sm64_events.ghost.clip import decode_clip, encode_clip, read_clip
from sm64_events.ghost.native_layout import PAD_AT, VALID_PAD
from sm64_events.ghost.padfill import VALID_FIELDS_AT, PadFillRefused, fill_pads
from sm64_events.inputs.frame import InputFrame
from tests.ghost_native_fixture import attempt, frame, producer

IDLE, WALKING = 0x0C400201, 0x04000440
BUTTONS = (0, 0x8000, 0x4000, 0x2010, 0x0001)
REAL_GCLIP4 = Path(__file__).parent / "fixtures/attempt_2549_whomps-fortress_shoot-into-the-wild-blue_0m10s66.ghost"


def action_at(timer):
    return IDLE if timer // 4 % 2 else WALKING  # changes every four frames


def pad_at(timer):
    """A different pad on every frame, so a fill one frame off cannot pass."""
    return (timer * 7) % 161 - 80, 80 - (timer * 3) % 161, BUTTONS[timer % len(BUTTONS)]


def track(timers, *, action=action_at):
    return [(timer, InputFrame(pad_at(timer)[2], 0, *pad_at(timer)[:2], action(timer))) for timer in timers]


def recorded(sequences=range(1, 41), *, pads=None, timers=None):
    """Records at timer 100 + sequence unless `timers` says otherwise."""
    timers = timers or {}
    rows = [frame(n, timer=timers.get(n, 100 + n), action=action_at(timers.get(n, 100 + n)),
                  pad=(pads or {}).get(n)) for n in sequences]
    return decode_clip(encode_clip(rows, producer(), clock_kind=1))


def test_each_record_takes_the_track_pad_at_its_own_frame():
    clip = recorded()
    fill = fill_pads(clip, track(range(101, 141)), producer())
    filled = decode_clip(fill.data)
    assert (fill.filled, fill.captured) == (40, 0) and filled.clip_id == clip.clip_id
    assert [record.pad for record in filled.frames] == [pad_at(record.global_timer) for record in clip.frames]
    for before, after in zip(clip.frames, filled.frames, strict=True):
        assert after.valid_fields == before.valid_fields | VALID_PAD
        assert after.raw[:VALID_FIELDS_AT] == before.raw[:VALID_FIELDS_AT]
        assert after.raw[VALID_FIELDS_AT + 4:PAD_AT] == before.raw[VALID_FIELDS_AT + 4:PAD_AT]


def test_a_real_clip_saved_before_pad_capture_plays_as_the_current_format_with_its_identity():
    clip = read_clip(REAL_GCLIP4)
    assert clip.file_version == 4 and not any(record.pad for record in clip.frames)
    polled = [(record.global_timer, InputFrame(pad_at(record.global_timer)[2], 0,
                                               *pad_at(record.global_timer)[:2], record.live_action))
              for record in clip.frames]
    manifest = json.loads(REAL_GCLIP4.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    fill = fill_pads(clip, polled, SimpleNamespace(**manifest["source"]))
    filled = decode_clip(fill.data)
    assert fill.data[:8] == b"SM64GHO7" and filled.clip_id == clip.clip_id == manifest["clip_id"]
    assert fill.filled == len(clip.frames) == 320
    assert [record.pad for record in filled.frames] == [pad_at(record.global_timer) for record in clip.frames]


def test_captured_pads_are_kept_byte_for_byte():
    captured = {n: pad_at(100 + n) for n in range(1, 11)}
    clip = recorded(pads=captured)
    fill = fill_pads(clip, track(range(101, 141)), producer())
    filled = decode_clip(fill.data)
    assert (fill.filled, fill.captured) == (30, 10)
    assert [record.raw for record in filled.frames[:10]] == [record.raw for record in clip.frames[:10]]


def test_a_captured_pad_the_track_contradicts_refuses_the_whole_fill():
    clip = recorded(pads={5: (1, 2, 0)})
    with pytest.raises(PadFillRefused, match="captured pad disagrees"):
        fill_pads(clip, track(range(101, 141)), producer())


def test_frames_the_track_never_sampled_stay_unknown():
    clip = recorded()
    held = [item for item in track(range(101, 136)) if item[0] not in (110, 125)]
    filled = decode_clip(fill_pads(clip, held, producer()).data)
    unknown = [record for record in filled.frames if record.pad is None]
    assert [record.global_timer for record in unknown] == [110, 125, 136, 137, 138, 139, 140]
    assert all(not record.valid_fields & VALID_PAD and not any(record.raw[PAD_AT:PAD_AT + 8])
               for record in unknown)  # unknown, not a neutral stick
    assert fill_pads(clip, [], producer()) is None


def test_a_sample_that_is_not_a_pad_stays_unknown():
    garbage = {112: InputFrame(0, 0, 200, 0, action_at(112)), 113: InputFrame(0x0080, 0, 0, 0, action_at(113))}
    polled = [(timer, garbage.get(timer, sample)) for timer, sample in track(range(101, 141))]
    filled = decode_clip(fill_pads(recorded(), polled, producer()).data)
    assert [record.global_timer for record in filled.frames if record.pad is None] == [112, 113]


def test_a_repeated_frame_number_stays_unknown():
    clip = recorded(timers={6: 105})  # two occurrences of frame 105
    filled = decode_clip(fill_pads(clip, track(range(101, 141)), producer()).data)
    assert [record.pad for record in filled.frames if record.global_timer == 105] == [None, None]


def test_a_track_across_a_counter_restart_is_refused():
    restarted = track(range(101, 121)) + track(range(101, 141))
    with pytest.raises(PadFillRefused, match="frame counter"):
        fill_pads(recorded(), restarted, producer())


def test_a_track_that_does_not_line_up_with_the_clip_is_refused():
    late = track(range(101, 141), action=lambda timer: action_at(timer - 1))
    with pytest.raises(PadFillRefused, match="does not line"):
        fill_pads(recorded(), late, producer())
    # Mario never changing action proves nothing about the alignment.
    still = decode_clip(encode_clip([frame(n, action=IDLE) for n in range(1, 41)], producer(), clock_kind=1))
    with pytest.raises(PadFillRefused, match="does not line"):
        fill_pads(still, track(range(101, 141), action=lambda _timer: IDLE), producer())


def native_run(tmp_path, count=40):
    """A preserved native clip with no pads, recorded over timers 101..100+count."""
    from datetime import datetime, timedelta
    from sm64_events.ghost.archive import PoseArchive
    from test_replay_service import attempt as replay_attempt, make_service
    sample = attempt(end=100 + count, end_utc=1800000000.0 + count / 30)
    row = replay_attempt(anchor_frame=sample.anchor_frame, rta_frames=sample.rta_frames,
                         started_utc=sample.started_utc, ended_utc=sample.ended_utc,
                         native_clock_frame=sample.native_clock_frame,
                         native_clock_utc=sample.native_clock_utc,
                         native_clock_kind=sample.native_clock_kind, closed_frame=sample.closed_frame)
    service = make_service(tmp_path, [row], cov=(datetime.fromisoformat(row.started_utc) - timedelta(seconds=10),
                                               datetime.fromisoformat(row.ended_utc) + timedelta(seconds=10)))
    archive = PoseArchive(tmp_path / "poses.sqlite")
    archive.ingest(producer(), tuple(frame(n, action=action_at(100 + n)) for n in range(1, count + 1)))
    service.recorder.pose_archive = archive
    service.preserve_native(row.id)
    return service, row, archive


def test_the_renderer_plays_a_filled_copy_and_the_stored_clip_never_changes(tmp_path):
    from sm64_events.ghost.native_session import NativeGhost
    service, row, archive = native_run(tmp_path)
    try:
        source, stored = service.find_native(row.id)
        before = {path: path.read_bytes() for path in (source, source.with_suffix(".json"))}
        spans = []
        service.input_track = lambda run, span: spans.append((run.id, span)) or track(range(101, 141))
        ghost = NativeGhost(service)
        target = {"kind": "star", "entity_key": f"{row.course_id}-{row.star_id}", "strat": row.strat_tag}
        template = SimpleNamespace(id=5, origin=f"attempt:{row.id}", import_id=None)
        result, (path, manifest) = ghost._resolve_template(target, template)
        assert result["available"] and spans == [(row.id, (101, 140))]
        assert path.parent == service.cfg.save_root.resolve() / "native" / "inputs"
        # The renderer's identity check: the same clip, its own hash over the bytes it reads.
        assert manifest["clip_id"] == stored["clip_id"] and manifest["sha256"] != stored["sha256"]
        assert manifest["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
        played = read_clip(path, manifest["sha256"])
        assert [record.pad for record in played.frames] == [pad_at(100 + n) for n in range(1, 41)]
        assert {item: item.read_bytes() for item in before} == before
        assert service.find_native(row.id)[0] == source  # saves and pruning still see the capture
        stamp = path.stat().st_mtime_ns
        assert ghost._resolve_template(target, template)[1][0] == path and len(spans) == 1
        ghost._cached.clear()  # a fresh server re-derives the same bytes and leaves the file alone
        assert ghost._resolve_template(target, template)[1][1]["sha256"] == manifest["sha256"]
        assert path.stat().st_mtime_ns == stamp
    finally:
        archive.close()


def test_an_ambiguous_track_keeps_the_captured_clip(tmp_path):
    service, row, archive = native_run(tmp_path)
    try:
        found = service.find_native(row.id)

        def ambiguous(_run, _span):
            raise ValueError("input capture is ambiguous across repeated frame counters")
        service.input_track = ambiguous
        assert service.native_with_inputs(row.id, found) == found
        service.input_track = lambda _run, _span: track(range(101, 141), action=lambda timer: action_at(timer + 1))
        assert service.native_with_inputs(row.id, found) == found
        assert not (service.cfg.save_root / "native" / "inputs").exists()
    finally:
        archive.close()


def test_an_exported_collection_carries_the_filled_inputs(tmp_path):
    from sm64_events.ghost.portable import validate_package
    from sm64_events.ghost.sharing import GhostSharing
    from tests.test_ghost_sharing import Store
    service, row, archive = native_run(tmp_path)
    try:
        service.input_track = lambda _run, _span: track(range(101, 141))
        _path, played = service.native_with_inputs(row.id, service.find_native(row.id))
        result = GhostSharing(service, Store(attempts=[row])).export({"attempts": [row.id]}, "Me")
        staging = tmp_path / "staging"
        staging.mkdir()
        rows, verified = validate_package(result["path"], staging)
        assert rows[0]["sha256"] == played["sha256"] and verified[played["sha256"]][3] == 40
    finally:
        archive.close()
