"""Shared stand-ins for the ghost-source tests, beside the repo's other
`*_fixture.py` helpers (tests/ is not a package, so a test module cannot be
imported from another one without loading it twice)."""
import json
from dataclasses import dataclass
from pathlib import Path

from sm64_events.inputs.document import encode
from sm64_events.inputs.frame import InputFrame

#: One real saved clip's per-picture arrays, nothing else — see the file's own
#: note. Kept as data because his first live try turned on exactly the shape no
#: hand-written fixture had: a long lead-in whose timer is the PREVIOUS run's.
REAL_CLIP = Path(__file__).parent / "fixtures" / "ghost_span_clip.json"


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


def real_clip() -> dict:
    """The whole fixture: the clip's arrays, its attempt row and its grab."""
    return json.loads(REAL_CLIP.read_text(encoding="utf-8"))


def a_real_clip_view(attempt_id: int = 9) -> dict:
    """The clip he actually played the overlay against, numbers only.

    642 pictures: the timer sits frozen at the previous run's 3029 through the
    first 45, then restarts and runs on past the star to 584, where Usamune's
    counter comes to rest on the star-select screen. Three slots carry no stamp
    at all. Footage, file paths and every other key of the real payload are
    left out.
    """
    clip = real_clip()
    return {"clip_url": f"/api/replay/saved/{attempt_id}", "source": "saved",
            "duration_s": 23.150689, "fps": 60, "game_fps": 30,
            "frame_map": clip["frame_map"], "frame_map_source": "plugin",
            "picture_igt": clip["picture_igt"], "video_start_s": 0.0,
            "encode": "picture_feed", "truncated": False}


def a_real_clip_attempt(attempt_id: int = 9, **overrides) -> "FakeAttempt":
    """Attempt 9 exactly as the journal holds it."""
    return FakeAttempt(**{**real_clip()["attempt"], "id": attempt_id,
                          **overrides})


@dataclass(frozen=True)
class FakeEvent:
    """One journal row, the three fields the grab lookup reads."""

    type: str
    frame: int
    payload: dict


def a_real_clip_journal(**overrides):
    """`(started_utc, ended_utc) -> the journal rows in that span`, holding the
    real grab that closed attempt 9 (`storage/db.py::events_between`'s shape:
    inclusive at both ends, oldest first)."""
    grab = real_clip()["grab_event"]
    row = FakeEvent(grab["type"], grab["frame"],
                    dict(grab["payload"], **overrides))
    return events_of(row)


def events_of(*rows: FakeEvent):
    def events(started_utc: str, ended_utc: str):
        assert started_utc and ended_utc, "the span comes off the attempt row"
        return list(rows)

    return events


@dataclass(frozen=True)
class FakeAttempt:
    """Only the fields the span reads off an attempt row.

    `tests/test_ghost_source.py` pins those names against the real
    `tracking/projection.py::Attempt`, so this cannot drift away from it
    silently.
    """

    id: int = 7
    anchor_frame: int | None = None
    rta_frames: int | None = None
    igt_frames: int | None = None
    timed_at: str | None = None
    course_id: int | None = None
    star_id: int | None = None
    segment_id: int | None = None
    started_utc: str = "2026-09-19T06:30:24.666969Z"
    ended_utc: str = "2026-09-19T06:30:42.709358Z"


def attempts_of(*rows: FakeAttempt):
    """`attempt_id -> row`, the shape and the LookupError of the captured-input
    service's own `attempt` door."""
    by_id = {row.id: row for row in rows}

    def attempt(attempt_id: int):
        if attempt_id not in by_id:
            raise LookupError(f"no attempt {attempt_id}")
        return by_id[attempt_id]

    return attempt


class FakeTracker:
    """`ReplayService.tracker`, only far enough for the journal door."""

    class Db:
        def __init__(self, events):
            self.events_between = events

    def __init__(self, events):
        self.db = None if events is None else self.Db(events)


class FakeReplay:
    """Only the two doors the ghost source reads — is this attempt's clip
    saved, and what is its view — plus the `tracker.db` the route reaches
    through for the journal, exactly as `ReplayService` exposes it."""

    def __init__(self, saved=(), views=None, journal=None, saved_dir=None):
        self._saved = set(saved)
        self._views = dict(views or {})
        self.tracker = FakeTracker(journal)
        self.viewed: list[int] = []
        #: Where the saved clips pretend to live. A REAL directory is what a
        #: test needs to put a Mario box list beside one; the bare filename
        #: this defaulted to could never have a file beside it.
        self.saved_dir = Path(saved_dir) if saved_dir else Path()

    def find_saved(self, attempt_id: int):
        if attempt_id not in self._saved:
            return None
        return self.saved_dir / f"attempt_{attempt_id:04d}_star.mp4"

    def view(self, attempt_id: int) -> dict:
        self.viewed.append(attempt_id)
        if attempt_id not in self._views:
            raise LookupError(f"no attempt {attempt_id}")
        return self._views[attempt_id]
