"""Saved clips are found from one walk of the save root, and a saved clip's
sidecar is parsed once per version of the file (replay/savedindex.py).

The answer must stay exactly what the per-question glob gave -- the first clip
in sorted order named `attempt_<id:04d>_*.mp4` anywhere under the root -- and
must stay honest when files move behind the app's back.
"""
import json
import os
from pathlib import Path

import pytest

from sm64_events.replay.savedindex import SavedClipIndex


def clip(root: Path, relative: str) -> Path:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"mp4")
    return path


def globbed(root: Path, attempt_id: int):
    matches = sorted(root.rglob(f"attempt_{attempt_id:04d}_*.mp4"))
    return matches[0] if matches else None


def test_it_answers_exactly_what_the_glob_answered(tmp_path):
    clip(tmp_path, "2026-09-20/session_1/attempt_0042_wf.mp4")
    clip(tmp_path, "2026-09-21/session_2/attempt_0042_again.mp4")
    clip(tmp_path, "2026-09-21/session_2/attempt_3400_ttc.mp4")
    clip(tmp_path, "attempt_34001_other.mp4")
    clip(tmp_path, "2026-09-21/attempt_0042_x.json.mp4.part")   # not a clip
    index = SavedClipIndex(tmp_path)
    for attempt_id in (42, 3400, 34001, 7):
        assert index.find(attempt_id) == globbed(tmp_path, attempt_id)
    assert index.ids() == {42, 3400, 34001}


def test_one_walk_serves_every_question(tmp_path, monkeypatch):
    clip(tmp_path, "a/attempt_0001_x.mp4")
    index = SavedClipIndex(tmp_path)
    walks = []
    original = Path.rglob
    monkeypatch.setattr(Path, "rglob", lambda self, pattern: walks.append(pattern) or original(self, pattern))
    for _ in range(20):
        index.find(1)
        index.find(2)
    assert len(walks) == 1


def test_a_clip_this_app_saves_is_known_at_once(tmp_path):
    index = SavedClipIndex(tmp_path)
    assert index.find(5) is None
    generation = index.generation
    index.add(clip(tmp_path, "d/attempt_0005_new.mp4"))
    assert index.find(5) == tmp_path / "d" / "attempt_0005_new.mp4"
    assert index.generation > generation


def test_a_clip_deleted_by_hand_is_never_served(tmp_path):
    first = clip(tmp_path, "a/attempt_0009_one.mp4")
    second = clip(tmp_path, "b/attempt_0009_two.mp4")
    index = SavedClipIndex(tmp_path)
    assert index.find(9) == first
    first.unlink()
    assert index.find(9) == second
    second.unlink()
    assert index.find(9) is None


def test_a_clip_copied_in_by_hand_is_found_within_the_max_age(tmp_path):
    now = [0.0]
    index = SavedClipIndex(tmp_path, clock=lambda: now[0])
    assert index.find(11) is None
    clip(tmp_path, "c/attempt_0011_restored.mp4")
    now[0] += SavedClipIndex.MAX_AGE_S / 2
    assert index.find(11) is None
    generation = index.generation
    now[0] += SavedClipIndex.MAX_AGE_S / 2
    assert index.find(11) is not None
    assert index.generation > generation


# -- the parsed sidecar ------------------------------------------------------

@pytest.fixture
def service(tmp_path):
    from types import SimpleNamespace

    from sm64_events.replay.config import ReplayConfig
    from sm64_events.replay.service import ReplayService
    cfg = ReplayConfig(save_root=tmp_path / "replays", scratch_dir=tmp_path / "buf",
                       settings_path=tmp_path / "rs.json")
    return ReplayService(cfg, SimpleNamespace(ghost_capture=None, ring=None),
                         SimpleNamespace(ffmpeg=None), SimpleNamespace(db=None))


def test_a_sidecar_is_parsed_once_per_version_and_handed_out_as_a_copy(service, tmp_path, monkeypatch):
    saved = clip(tmp_path, "replays/d/attempt_0003_x.mp4")
    meta = saved.with_suffix(".json")
    meta.write_text(json.dumps({"duration_s": 1.0, "frame_times": [0.0, 0.5]}))
    parses = []
    real = json.loads
    monkeypatch.setattr("sm64_events.replay.service.json.loads",
                        lambda text, *a, **k: parses.append(1) or real(text, *a, **k))
    first = service._saved_meta(saved)
    first["video_start_s"] = 9.9                  # a caller filling a field
    assert service._saved_meta(saved) == {"duration_s": 1.0, "frame_times": [0.0, 0.5]}
    assert len(parses) == 1
    meta.write_text(json.dumps({"duration_s": 2.0}))
    stat = meta.stat()
    os.utime(meta, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10_000_000))
    assert service._saved_meta(saved) == {"duration_s": 2.0}
    assert len(parses) == 2


def test_the_service_finds_through_the_index(service, tmp_path):
    saved = clip(tmp_path, "replays/d/attempt_0004_x.mp4")
    assert service.find_saved(4) == saved
    assert service.saved_attempt_ids() == {4}
