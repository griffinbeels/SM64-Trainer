"""Shared stand-ins for the ghost-source tests, beside the repo's other
`*_fixture.py` helpers (tests/ is not a package, so a test module cannot be
imported from another one without loading it twice)."""
from pathlib import Path

from sm64_events.inputs.document import encode
from sm64_events.inputs.frame import InputFrame


def a_document(target: str = "star 24 1") -> str:
    return encode([(0, InputFrame(0x8000, 0, 40, 40))], target=target,
                  strategy="10 coin", version="us", origin="attempt 1")


def a_view(attempt_id: int, frame_map=(11, 11, 12, 13)) -> dict:
    """The clip view payload in the shape `replay/service.py::_view` returns.

    Only the keys the ghost door reads are load-bearing; the rest ride along
    so a test can prove the payload is passed through rather than rebuilt.
    """
    mapped = frame_map is not None
    return {"clip_url": f"/api/replay/saved/{attempt_id}",
            "duration_s": 4.2, "fps": 60, "game_fps": 30, "source": "saved",
            "frame_map": list(frame_map) if mapped else None,
            "frame_map_source": "plugin" if mapped else None,
            "picture_igt": [100, 100, 101, 102],
            "frame_times": [0.0, 0.0167, 0.0334, 0.05],
            "video_start_s": 0.011003, "encode": "picture_feed",
            "truncated": False, "saved_path": "C:/replays/attempt_0007_x.mp4"}


class FakeReplay:
    """Only the two doors the ghost source reads: is this attempt's clip
    saved, and what is its view."""

    def __init__(self, saved=(), views=None):
        self._saved = set(saved)
        self._views = dict(views or {})
        self.viewed: list[int] = []

    def find_saved(self, attempt_id: int):
        if attempt_id not in self._saved:
            return None
        return Path(f"attempt_{attempt_id:04d}_star.mp4")

    def view(self, attempt_id: int) -> dict:
        self.viewed.append(attempt_id)
        if attempt_id not in self._views:
            raise LookupError(f"no attempt {attempt_id}")
        return self._views[attempt_id]
