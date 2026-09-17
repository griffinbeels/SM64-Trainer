"""Eviction in the metadata-to-publication seam must not break a Save."""
from pathlib import Path
import shutil

import pytest

from sm64_events.core.paths import bundled_ffmpeg
from test_replay_fragment_service import fragment_service as fragment_service
from test_replay_fragment_producer import continuing_output
from test_replay_picture_identity import read_pictures


@pytest.fixture
def encoder():
    ff = bundled_ffmpeg() or shutil.which("ffmpeg")
    if not ff:
        pytest.skip("ffmpeg required")
    return ff, "libx264"


@pytest.fixture
def recording(tmp_path, encoder):
    continuing_output(tmp_path, *encoder)
    return (tmp_path / "live.mp4").read_bytes()


@pytest.mark.parametrize("cached", [False, True])
def test_save_keeps_dependency_lease_across_view_and_atomic_publication(fragment_service, monkeypatch, cached):
    service = fragment_service
    if cached:
        service.view(42)
    original = service._view
    cuts = []

    def expire_after_selection(*args, **kwargs):
        result = original(*args, **kwargs)
        service.fragments.expire_before(service.fragments._utc(2000))
        cuts.append(result)
        return result

    monkeypatch.setattr(service, "_view", expire_after_selection)
    saved = Path(service.save(42)["path"])
    assert saved.is_file() and cuts
    pictures = read_pictures(saved)
    assert [number for _, number in pictures[:3]] == [15, 16, 17]
    assert pictures[-1][1] == 59
    assert service.view(42)["frame_map"] == cuts[0]["frame_map"]
    assert not service.recorder.ring.protected_paths()
