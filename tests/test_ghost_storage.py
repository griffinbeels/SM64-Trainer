"""Settings' ghost files (ghost/storage.py): what each button keeps, deletes and skips.

His words, 2026-11-08: "Maybe we need an extra button in the settings menu for purging all non-PB
ghost files (and another for purging all local ghost files)? This wouldn't clear the server, but
would free up space for the user."

Real owners on temporary folders: the replay service's committed clips (its methods on a stand-in
self), the local population (real SQLite), imported ghosts (real collection index), the community
download cache's two methods, and the lab's attempt scenes fed by a cutter double. The journal is
a double that records every write, so "the purge never changes the journal" is an assertion.
"""
import json
import sqlite3
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from types import MethodType, SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "scene_lab"))
import attempt_scenes  # noqa: E402
from sm64_events.ghost.population import LocalPopulation  # noqa: E402
from sm64_events.ghost.portable import make_entry  # noqa: E402
from sm64_events.ghost.session import FILES_EVENT, GhostSession  # noqa: E402
from sm64_events.ghost.sharing import GhostSharing  # noqa: E402
from sm64_events.ghost.storage import GhostStorage, kept_attempts  # noqa: E402
from sm64_events.replay.publication import atomic_json  # noqa: E402
from sm64_events.replay.service import ReplayService  # noqa: E402
from sm64_events.server.ghost_api import create_ghost_router  # noqa: E402

STARTED = "2026-11-08T10:00:{:02d}+00:00"
KB = 1024


class Journal:
    """The tracker database's reads the purge makes; any write is recorded."""

    def __init__(self, path):
        self.path, self.rows, self.pb_rows, self.state, self.templates, self.writes = path, {}, [], {}, [], []

    def attempt(self, number):
        return self.rows.get(number)

    def pbs(self):
        return [dict(row) for row in self.pb_rows]

    def pb_attempt_ids(self):
        return {row["attempt_id"] for row in self.pb_rows}

    def get_state(self, key, default=None):
        return self.state.get(key, default)

    def set_state(self, key, value):
        self.writes.append(key)
        self.state[key] = value

    def input_templates(self):
        return SimpleNamespace(all=lambda: list(self.templates))

    def segment_defs(self):
        return []


def _attempt(number, *, strat="Standard", course=1):
    return SimpleNamespace(id=number, session_id=3, course_id=course, star_id=1 if course else None, segment_id=None,
                           strat_tag=strat, outcome="success", anchor_frame=100, closed_frame=200,
                           started_utc=STARTED.format(number), ended_utc=STARTED.format(number + 30))


class Replay:
    """ReplayService's own native methods on a stand-in self: its folder, its cut locks."""

    def __init__(self, root: Path, journal):
        self.cfg = SimpleNamespace(save_root=root)
        self.journal, self.cuts, self.forgotten = journal, [], []
        for name in ("_native_files", "native_holdings", "remove_native_many", "_remember_purged"):
            setattr(self, name, MethodType(getattr(ReplayService, name), self))

    @contextmanager
    def _cut_lock(self, attempt_id):
        self.cuts.append(attempt_id)
        yield

    def _attempt(self, attempt_id):
        return self.journal.attempt(attempt_id)

    def _native_preservation_key(self, attempt):
        return attempt.id

    def _remember_native_preservation(self, key, reason):
        self.forgotten.append(key)


def _native(root: Path, number: int, size: int = 4 * KB, *, copy: bool = True) -> list[Path]:
    """A committed clip, its record and (by default) the playback copy beside it."""
    native = root / "native"
    (native / "inputs").mkdir(parents=True, exist_ok=True)
    made = [native / f"attempt_{number}.ghost", native / f"attempt_{number}.json"]
    if copy:
        made += [native / "inputs" / f"attempt_{number}.ghost", native / "inputs" / f"attempt_{number}.json"]
    for path in made:
        path.write_bytes(b"g" * (size if path.suffix == ".ghost" else 64))
    return made


def _digest(number: int) -> str:
    return f"{number:064x}"


def _population_row(store: LocalPopulation, number: int, source, *, digest=None, size=8 * KB) -> str:
    """A row as admission writes one: its clip under clips/ and its lineage in the document."""
    identity, digest = _digest(1000 + number), digest or _digest(number)
    clip = store.root / "clips" / f"{digest}.ghost"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"p" * size)
    with sqlite3.connect(store.root / "population.sqlite3") as connection:
        connection.execute("INSERT INTO recordings VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            identity, "me", "me", "star:1-1", "star", "igt", "us", 300 + number, digest, size,
            json.dumps({"id": identity, "source": source, "target": {"kind": "star", "strat": "Standard"}})))
    return identity


def _import(sharing: GhostSharing, tmp_path: Path, name: str) -> str:
    path = tmp_path / f"{name}.ghost"
    path.write_bytes(name.encode() * 512)
    row = make_entry(path, author=f"Runner {name}", target={"kind": "star", "entity_key": "1-1", "strat": "Standard"},
                     frames=300, clock="igt", recorded_utc="2026-11-01T12:00:00+00:00", practice=False,
                     outcome="success")
    sharing.root.mkdir(parents=True, exist_ok=True)
    (sharing.root / f"{row['sha256']}.ghost").write_bytes(path.read_bytes())
    rows = [*json.loads(sharing._index.read_text())["ghosts"]] if sharing._index.exists() else []
    atomic_json(sharing._index, {"version": 1, "ghosts": [*rows, row]})
    return row["id"]


class Cache:
    """community/cache.py CachedPopulation's two methods over a dict of downloads."""

    def __init__(self, root: Path):
        from sm64_events.community.cache import CachedPopulation
        self.root, self.local, self._lock, self._assets, self._kept, self._generation = root, None, \
            threading.RLock(), {}, frozenset(), 0
        root.mkdir(parents=True, exist_ok=True)
        self.cached_holdings = MethodType(CachedPopulation.cached_holdings, self)
        self.drop_cached = MethodType(CachedPopulation.drop_cached, self)

    def download(self, identifier: str, size: int = 2 * KB):
        for suffix in (".ghost", ".ghc1"):
            (self.root / (identifier + suffix)).write_bytes(b"c" * size)
        self._assets[identifier] = {}


class Cutter:
    def __init__(self, folder, on_event):
        self.folder, self.on_event, self.notes = folder, on_event, []

    def send(self, note):
        self.notes.append(note)

    def close(self):
        pass


@pytest.fixture
def world(tmp_path):
    """His runs: 1 the current PB, 2 the PB it replaced, 3 a saved ghost, 4 a template's run,
    5 a plain run, 6 an attempt with no strategy kept in the log only by its clip, 7 a run
    the admission worker still owes, 8 a run a pending upload names."""
    journal = Journal(tmp_path / "data" / "tracker.db")
    for number in range(1, 9):
        journal.rows[number] = _attempt(number, strat=None if number == 6 else "Standard")
    pb = {"course_id": 1, "star_id": 1, "segment_id": None, "timer_mode": "igt", "strat_tag": "Standard",
          "pb_version": "us", "frames": 300}
    journal.pb_rows = [{**pb, "id": 1, "attempt_id": 2}, {**pb, "id": 2, "attempt_id": 1}]
    journal.state["ghost_saved"] = [{"attempt_id": 3, "kind": "star", "entity_key": "1-1", "saved_utc": "x"}]
    journal.templates = [SimpleNamespace(origin="attempt:4")]
    replays = tmp_path / "replays"
    for number in range(1, 9):
        _native(replays, number)
    replay = Replay(replays, journal)
    store = LocalPopulation(tmp_path / "population")
    from sm64_events.ghost.pb import local_attempt_source
    rows = {number: _population_row(store, number, local_attempt_source(journal.rows[number])) for number in (1, 2, 5)}
    store.note_owed(local_attempt_source(journal.rows[7]), None, "2026-11-08T10:00:00+00:00")
    sharing = GhostSharing(SimpleNamespace(cfg=SimpleNamespace(save_root=replays)), journal)
    imported = _import(sharing, tmp_path, "rival")
    cache = Cache(tmp_path / "community_cache")
    cache.download(_digest(77))
    cache.local = store
    folder = attempt_scenes.journal_folder(tmp_path / "lab" / "attempts", journal)
    scenes = attempt_scenes.AttemptScenes(lambda: folder, Cutter, journal=lambda: journal)
    scenes.start()
    for number in (1, 3, 5):
        scenes.observe_completed([journal.rows[number]])
        path = folder / f"take-test-{number}.sm64scene"
        path.write_bytes(b"s" * (16 * KB))
        scenes._event({"id": number, "state": "ready", "seconds": 9.0, "path": str(path)})
    uploading = replays / "native" / "inputs" / "attempt_8.ghost"
    community = SimpleNamespace(pending_uploads=lambda: ({uploading}, set()))
    racing = set()
    storage = GhostStorage(database=lambda: journal, replay=replay, population=cache, sharing=lambda: sharing,
                           scenes=lambda: scenes, community=community, in_use=lambda: racing)
    yield SimpleNamespace(journal=journal, replays=replays, replay=replay, store=store, rows=rows, sharing=sharing,
                          imported=imported, cache=cache, scenes=scenes, folder=folder, storage=storage,
                          racing=racing, tmp=tmp_path)
    scenes.stop()
    store.close()


def _natives(world) -> list[int]:
    return sorted(row["attempt"] for row in world.replay.native_holdings())


def test_keep_pbs_keeps_exactly_the_pbs_saved_template_and_imported_ghosts(world):
    assert kept_attempts(world.journal) == {1, 3, 4}            # 2 is the PB 1 replaced
    usage = world.storage.usage()
    result = world.storage.purge(keep_pbs=True)
    assert _natives(world) == [1, 3, 4, 6, 7, 8]                # 6, 7, 8: the log, the owed run, the upload
    assert sorted(row["id"] for row in world.store.holdings()) == [world.rows[1]]
    assert sorted(row["attempt"] for row in world.scenes.holdings()) == [1, 3]
    assert [row["id"] for row in world.sharing.holdings()] == [world.imported]
    assert world.cache.cached_holdings() == []                  # a download comes back when a lineup asks
    # The ghosts deleted: run 2 (clip + row), run 5 (clip + row + scene), the community download.
    assert (result["deleted"], result["skipped"]) == (3, 0) and usage["except_pbs"]["ghosts"] == 3
    recorded_run = 2 * 4 * KB + 2 * 64                         # a clip, its playback copy, their records
    assert result["bytes"] == usage["except_pbs"]["bytes"] == 2 * recorded_run + 2 * 8 * KB + 16 * KB + 2 * 2 * KB
    assert world.journal.writes == []


def test_delete_all_leaves_replays_scene_clips_save_states_uploads_and_the_journal(world):
    session = world.replays / "2026-11-08" / "session_3"
    session.mkdir(parents=True)
    video = {name: session / name for name in ("attempt_0001_bob.mp4", "attempt_0001_bob.json", "attempt_0001_bob.ghost")}
    clips = world.replays / "scene clips"
    clips.mkdir()
    states = world.tmp / "slot-library"
    states.mkdir()
    for path in (*video.values(), clips / "BoB 2026-11-08.mp4", states / "slot-01.st"):
        path.write_bytes(b"keep me")
    journal_before = (dict(world.journal.rows), world.journal.pbs(), json.dumps(world.journal.state))
    usage = world.storage.usage()
    result = world.storage.purge(keep_pbs=False)
    assert all(path.read_bytes() == b"keep me" for path in (*video.values(), clips / "BoB 2026-11-08.mp4",
                                                            states / "slot-01.st"))
    assert _natives(world) == [6, 7, 8]                        # what keeps a log row, an owed run, an upload
    assert (world.replays / "native" / "inputs" / "attempt_8.ghost").is_file()
    assert world.store.holdings() == [] and world.sharing.holdings() == [] and world.scenes.holdings() == []
    assert world.store.owed_runs(10)                          # the owed table is his uploads' ledger
    assert (dict(world.journal.rows), world.journal.pbs(), json.dumps(world.journal.state)) == journal_before
    assert world.journal.writes == []
    assert result["deleted"] == usage["all"]["ghosts"] == 7    # runs 1-5, the community download, the import
    assert usage["total"]["ghosts"] == 10 and result["usage"]["all"] == {"ghosts": 0, "bytes": 0}


def test_a_ghost_in_use_is_skipped_and_counted(world):
    """The game racing his PB's playback copy, the Scene viewer reading run 5's scene, and a clip
    Windows will not let go of: each is left out, and the purge says how many it skipped."""
    world.racing.add(world.replays / "native" / "inputs" / "attempt_1.ghost")
    world.scenes.in_use = lambda: str(world.folder / "take-test-5.sm64scene")
    held = (world.replays / "native" / "attempt_2.ghost").open("rb")      # an export reading it
    try:
        usage = world.storage.usage()
        assert usage["in_use"]["ghosts"] == 2
        result = world.storage.purge(keep_pbs=False)
    finally:
        held.close()
    assert (world.replays / "native" / "attempt_1.ghost").is_file()
    assert (world.replays / "native" / "attempt_2.json").is_file()      # its commit record put back
    assert (world.folder / "take-test-5.sm64scene").is_file()
    assert result["skipped"] == 3
    assert 1 in _natives(world) and 2 in _natives(world) and 5 not in _natives(world)


def test_an_unreadable_upload_list_deletes_no_run_an_upload_might_name(world):
    def unreadable():
        raise OSError("the outbox is locked")
    world.storage.community = SimpleNamespace(pending_uploads=unreadable)
    world.storage.purge(keep_pbs=False)
    assert _natives(world) == list(range(1, 9))
    assert len(world.store.holdings()) == 3                     # an upload names a run's clip or row
    assert world.scenes.holdings() == [] and world.sharing.holdings() == []    # never an upload's


def test_the_routes_answer_the_settings_section_and_broadcast_after_a_purge(world):
    published, refreshed = [], []

    async def publish(event):
        published.append(event)

    async def refresh(*, resolve=True):
        refreshed.append(resolve)
        return {}
    rivals = SimpleNamespace(files_changed=lambda: refreshed.append("rivals"))
    session = SimpleNamespace(_rivals=rivals, _lifecycle_lock=threading.RLock(), refresh=refresh,
                              _broadcaster=SimpleNamespace(publish=publish), storage=lambda scenes: world.storage)
    session.purge_files = MethodType(GhostSession.purge_files, session)
    app = FastAPI()
    app.include_router(create_ghost_router(session=session))
    with TestClient(app) as client:
        usage = client.get("/api/ghost/storage").json()
        assert usage["available"] is True and set(usage) == {"available", "total", "except_pbs", "all", "in_use"}
        answer = client.post("/api/ghost/storage/purge", json={"keep_pbs": True}).json()
        assert answer["deleted"] == usage["except_pbs"]["ghosts"] and answer["usage"]["except_pbs"]["ghosts"] == 0
        assert client.post("/api/ghost/storage/purge", json={"keep_pbs": "yes"}).status_code == 422
        world.storage.lock.acquire()
        try:
            assert client.post("/api/ghost/storage/purge", json={"keep_pbs": False}).status_code == 409
        finally:
            world.storage.lock.release()
    assert refreshed == ["rivals", True]
    assert [event.type for event in published] == [FILES_EVENT]
    assert published[0].payload == {"deleted": answer["deleted"], "bytes": answer["bytes"], "skipped": 0}
    unmanned = FastAPI()
    unmanned.include_router(create_ghost_router(session=None))
    with TestClient(unmanned) as client:
        assert client.get("/api/ghost/storage").json()["available"] is False
        assert client.post("/api/ghost/storage/purge", json={"keep_pbs": True}).status_code == 503


def test_a_row_from_another_profile_with_a_reused_attempt_number_is_not_his_pb(world):
    """A fresh profile reuses attempt numbers; a row whose lineage names another start is no run
    of this journal, so "keep PBs" does not keep it for attempt 1's sake."""
    stranger = _population_row(world.store, 41, {"session_id": 99, "attempt_id": 1, "started_utc": STARTED.format(59)},
                               digest=_digest(41))
    world.storage.purge(keep_pbs=True)
    assert stranger not in {row["id"] for row in world.store.holdings()}
    assert world.rows[1] in {row["id"] for row in world.store.holdings()}


def test_a_clip_two_rows_share_frees_its_bytes_only_when_both_rows_go(world):
    twin = _population_row(world.store, 42, {"session_id": 3, "attempt_id": 1, "started_utc": STARTED.format(1)},
                           digest=_digest(5))                    # the same bytes as run 5's row, under PB run 1
    usage = world.storage.usage()
    result = world.storage.purge(keep_pbs=True)
    assert (world.store.root / "clips" / f"{_digest(5)}.ghost").is_file()
    assert twin in {row["id"] for row in world.store.holdings()}
    recorded_run = 2 * 4 * KB + 2 * 64
    # Run 5's row goes but its clip stays for the twin: only run 2's row frees population bytes.
    assert result["bytes"] == usage["except_pbs"]["bytes"] == 2 * recorded_run + 8 * KB + 16 * KB + 2 * 2 * KB


def _start(world, digest: str, size: int = 3 * KB, *, downloaded: bool = False) -> Path:
    """A ghost start beside the population, as ghost/start_frame.py names it."""
    folder = world.store.root / "starts" / ("downloaded" if downloaded else "")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{digest}.sm64start"
    path.write_bytes(b"s" * size)
    return path


def test_a_ghost_start_goes_with_its_clip_and_counts_in_its_ghost(world):
    """Run 5's population row goes with "keep PBs" and takes its start; PB run 1's start stays."""
    gone, kept = _start(world, _digest(5)), _start(world, _digest(1), downloaded=True)
    usage = world.storage.usage()
    result = world.storage.purge(keep_pbs=True)
    assert not gone.exists() and kept.exists()
    recorded_run = 2 * 4 * KB + 2 * 64
    assert result["bytes"] == usage["except_pbs"]["bytes"] == 2 * recorded_run + 2 * 8 * KB + 16 * KB + 2 * 2 * KB + 3 * KB
    assert (result["deleted"], usage["except_pbs"]["ghosts"]) == (3, 3)      # the start is no ghost of its own
    assert usage["total"]["bytes"] - world.storage.usage()["total"]["bytes"] == result["bytes"]


def test_a_start_stays_while_any_clip_of_its_bytes_stays(world):
    twin = _population_row(world.store, 42, {"session_id": 3, "attempt_id": 1, "started_utc": STARTED.format(1)},
                           digest=_digest(5))                    # run 5's bytes, kept under PB run 1
    start = _start(world, _digest(5))
    world.storage.purge(keep_pbs=True)
    assert start.exists() and twin in {row["id"] for row in world.store.holdings()}
    world.storage.purge(keep_pbs=False)
    assert not start.exists()


def test_a_start_of_his_recorded_run_follows_that_run(world):
    import hashlib
    clip = world.replays / "native" / "attempt_2.ghost"           # run 2: the PB run 1 replaced
    clip.write_bytes(b"run two's own bytes" * 200)                 # the fixture's runs share their bytes otherwise
    start = _start(world, hashlib.sha256(clip.read_bytes()).hexdigest())
    world.storage.purge(keep_pbs=True)
    assert not clip.exists() and not start.exists()


def test_a_start_whose_clip_is_gone_goes_with_either_button(world):
    orphan = _start(world, _digest(999))
    usage = world.storage.usage()
    world.storage.purge(keep_pbs=True)
    assert not orphan.exists() and usage["except_pbs"]["ghosts"] == 3   # freed bytes, no extra ghost counted
