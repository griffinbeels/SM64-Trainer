"""Real collection jobs and GhostSession over private native recordings."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import ui_fixture

from sm64_events.ghost.clip import encode_clip, read_clip
from sm64_events.ghost.native_session import NativeGhost
from sm64_events.ghost.portable import make_entry, write_package
from sm64_events.ghost.session import GhostSession
from sm64_events.ghost.sharing import GhostSharing
from sm64_events.ghost.sharing_targets import describe
from sm64_events.server.ghost_api import create_ghost_router
from tests.ghost_native_fixture import frame, producer

TARGET = {"kind": "star", "entity_key": "2-4", "strat": ui_fixture.FIXTURE_STRAT}
BASE_UTC = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
LOCAL_COUNT = 24
FOREIGN_COUNT = 26
AUTHORS = ("Aster", "René the Very Patient Speedrunner", "Luigi Runner")


class ScratchStore:
    def __init__(self):
        self.runs, self.state, self.writes = [], {}, []

    def attempts(self):
        return self.runs

    def segment_defs(self):
        return []

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.state[key] = value
        self.writes.append(key)


def recording(path, number, frames):
    start = BASE_UTC + timedelta(minutes=number)
    raw = encode_clip([frame(index + 1, igt=index, elapsed=index, pad=(12, -34, 0x8000))
                       for index in range(frames + 1)],
                      producer(utc_anchor=start.timestamp() - 1 / 30),
                      clock_kind=1, clip_id=f"{number:032x}")
    path.write_bytes(raw)
    clip = read_clip(path)
    manifest = {"clip_id": clip.clip_id, "sha256": hashlib.sha256(raw).hexdigest(), "blink_frames": 0}
    path.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
    return start, manifest


class ScratchReplay:
    def __init__(self, root, store):
        self.cfg = SimpleNamespace(save_root=root / "replays")
        self.store, self.assets, self.saved_generation, self.preserved = store, {}, 0, []
        self.cfg.save_root.mkdir()
        self.recorder = SimpleNamespace(ghost_capture=None, pose_archive=SimpleNamespace(status=lambda: {
            "first_utc": BASE_UTC.timestamp(), "last_utc": (BASE_UTC + timedelta(days=1)).timestamp()}))
        source = root / "source"; source.mkdir()
        for number in range(1, LOCAL_COUNT + 1):
            frames = 120 + number
            path = source / f"local-{number}.ghost"
            start, manifest = recording(path, number, frames)
            self.assets[number] = path, manifest
            store.runs.append(SimpleNamespace(id=number, session_id=1, course_id=2,
                star_id=4 if number <= 22 else 3, segment_id=None,
                strat_tag=TARGET["strat"] if number % 2 else "Sideflip", igt_frames=frames,
                rta_frames=frames, anchor_frame=101, native_clock_frame=101, closed_frame=101 + frames,
                native_clock_kind="star", native_clock_utc=start.isoformat(), started_utc=start.isoformat(),
                ended_utc=(start + timedelta(seconds=frames / 30)).isoformat(), outcome="success",
                anchor_type="state_loaded" if number == LOCAL_COUNT else "practice_reset",
                valid=True, invalid_reason=None))

    def attempt(self, number):
        return next(run for run in self.store.runs if run.id == number)

    def preserve_native(self, number):
        source, manifest = self.assets[number]
        folder = self.cfg.save_root / "native"; folder.mkdir(exist_ok=True)
        path = folder / f"attempt_{number}.ghost"
        path.write_bytes(source.read_bytes())
        path.with_suffix(".json").write_text(json.dumps(manifest), encoding="utf-8")
        self.saved_generation += 1
        self.preserved.append(number)
        return {"path": str(path)}

    def native_identity(self, number):
        return self.assets[number][1]["sha256"]

    def find_native(self, number):
        path = self.cfg.save_root / "native" / f"attempt_{number}.ghost"
        return (path, self.assets[number][1]) if path.exists() else None

    def native_with_inputs(self, number, found):
        # These recordings already carry their pads: the playback copy is the clip itself.
        return found


def foreign_collection(root):
    entries = []
    for number in range(1, FOREIGN_COUNT + 1):
        path = root / f"foreign-{number}.ghost"
        frames = 60 + number
        start, _manifest = recording(path, 100 + number, frames)
        key = "2-3" if number in (23, 24) else TARGET["entity_key"]
        strat = TARGET["strat"] if number % 2 else "Sideflip"
        target = describe("star", key, strat, [])
        row = make_entry(path, author=AUTHORS[(number - 1) % len(AUTHORS)], target=target,
                         frames=frames, clock="igt", recorded_utc=start.isoformat(),
                         practice=number >= 25, outcome="success")
        entries.append((row, path))
    path = root / "foreign-collection.sm64ghosts"
    write_package(path, entries)
    return path, [row for row, _path in entries]


class RecordedSession(GhostSession):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.writes = []

    async def update(self, changes=None, **named):
        answer = await super().update(changes, **named)
        self.writes.append({**(changes or {}), **named})
        return answer


@contextlib.contextmanager
def serve_collection(root: Path):
    root.mkdir(parents=True)
    store = ScratchStore()
    replay = ScratchReplay(root, store)
    sharing = GhostSharing(replay, store)
    package, entries = foreign_collection(root)
    templates = SimpleNamespace(generation=0, active_for=lambda *_: None, list_for=lambda *_: [])
    tracker = SimpleNamespace(db=store, target=("star", 2, 4), strat_tag=TARGET["strat"])
    original, holder = ui_fixture.create_app, {}

    def create_app(*args, **kwargs):
        app = original(*args, **kwargs)
        inputs = SimpleNamespace(templates=templates)
        session = RecordedSession(tracker=tracker, inputs=inputs, replay=replay, portable=sharing,
                                  broadcaster=args[1], native=NativeGhost(replay))
        holder["session"] = session
        app.router.routes = [route for route in app.router.routes
                             if not str(getattr(route, "path", "")).startswith(("/api/ghost", "/api/saved-ghosts"))]
        app.include_router(create_ghost_router(session=session))
        return app

    ui_fixture.create_app = create_app
    try:
        with ui_fixture.serve_ui(db_path=root / "fixture.db", seed_editor_fixtures=True,
                                 from_dev_db=False) as base:
            yield base, {"sharing": sharing, "replay": replay, "store": store, "session": holder["session"],
                         "package": package, "entries": entries}
    finally:
        ui_fixture.create_app = original
        if "session" in holder:
            asyncio.run(holder["session"].stop())
