"""Run the REAL app offline on a free port, for a browser to drive.

Never `python -m sm64_events.main` for this: that attaches to PJ64 and takes
the recorder lock, which is the only thing protecting the user's recording
while they play.  Here the poller gets a memory stub that never attaches, so
every route, template and stylesheet is the shipping one and nothing goes near
the emulator.

Why it defaults to a SNAPSHOT of the dev db rather than an empty one: the
surfaces most worth measuring only exist when there is data behind them.  The
Active Target card -- the one that clipped its own "Ready" row at 900x1180 --
renders nothing at all without a target carrying rank standards, so an empty
fixture would sweep a page that cannot show the defect and report it clean.

The snapshot goes through `sqlite3.Connection.backup`, never `shutil.copy`: a
file copy of a live WAL database can catch a torn write, and the failure looks
like corrupt data rather than a bad copy.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sqlite3
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import uvicorn

from sm64_events.core.events import Event
from sm64_events.core.paths import bundled_rank_standards, rank_standards_path
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.service import TrackerService

REPO = Path(__file__).resolve().parents[1]
DEV_DB = REPO / "data" / "tracker.db"


class _OfflineMemory:
    """Mirrors the stub tests/test_api.py has used since the first API test.

    Kept local rather than imported so tools/ and tests/ do not reach into each
    other's private helpers.
    """

    attached = False

    def attach(self) -> bool:
        return False

    def detach(self) -> None:
        pass


def snapshot_db(source: Path, destination: Path) -> Path:
    """Online-backup `source` to `destination` and return the destination."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as origin, \
            sqlite3.connect(destination) as copy:
        origin.backup(copy)
    return destination


def seed_practice(service, course_id: int = 2, star_id: int = 0,
                  level: int = 5, attempts: bool = True) -> None:
    """Give the fixture an ACTIVE TARGET and a few attempts.

    Without this the Practice page renders only its empty states -- the
    no-target `objective-empty` card and the `selector-empty` banner -- because
    a db snapshot taken while nobody is playing has no session sections and no
    target. Anything that only exists on a POPULATED card is then invisible to
    the rig, which is how a whole feature (the per-card collapse toggles) got
    built, served correctly, and rendered zero times without a single error
    (2026-07-28).

    Publishes real events through the real service rather than writing rows, so
    the view is built by the same code path the app uses. Same shape as the
    `seed` helper tests/test_api.py has always had -- kept separate rather than
    imported so tools/ and tests/ do not depend on each other's helpers.
    """
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

    async def go() -> None:
        # Put the player IN the course first. Without this the Active Target
        # card renders "Nothing to practice here" no matter what the target is
        # — you may only practice what you are standing in front of, and with
        # no emulator attached the service's stage is empty. Every sweep before
        # this measured the EMPTY card while the user was reporting a bug on
        # the populated one, which is the third time this fixture has been the
        # thing that was wrong (2026-07-28).
        await service.publish(Event(
            type="stage_changed", frame=900, timestamp_utc=now,
            payload={"course_id": course_id, "level": level, "area": 1,
                     "mode": "stars"}))
        if not attempts:
            return
        for index, frames in enumerate([343, 361, 352]):
            await service.publish(Event(
                type="practice_reset", frame=1000 + index * 1000,
                timestamp_utc=now, payload={"igt_frames_before": 0}))
            await service.publish(Event(
                type="star_collected", frame=1350 + index * 1000,
                timestamp_utc=now,
                payload={"course_id": course_id, "star_id": star_id,
                         "igt_frames": frames}))

    asyncio.run(go())


def _seed_target(base: str, course_id: int = 2, star_id: int = 0,
                 with_pb: bool = True) -> None:
    """Make the seeded star the ACTIVE target.

    Seeding attempts is not enough, and the difference is the whole page. With
    attempts but no target the Practice page renders "No active objective" and
    files the populated star into the practice index -- inside a CLOSED
    <details>. Everything interesting is then off-screen: the Active Target
    card is an empty state, and any control living in a StarSection is present
    in the DOM and genuinely not visible. A browser driver that refuses to
    click an invisible element reports that honestly; one that dispatches the
    event anyway hides it (2026-07-28).

    POST /api/target is allowed to refuse -- you may only practice what you are
    standing in front of -- but not here: with no emulator attached the
    player's place is unknown, and practicable_here() treats an unknown place
    as "nothing to compare against, so nothing to refuse". That clause exists
    precisely so a target stays settable while reviewing with the game closed.
    """
    import urllib.error
    import urllib.request

    def post(path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(request, timeout=10).read())
        except urllib.error.HTTPError as error:
            # Loud, not silent: a fixture that quietly fails to reach a state
            # is a fixture that measures a different page and calls it clean.
            raise RuntimeError(
                f"fixture could not POST {path}: {error.code} "
                f"{error.read()[:200]!r}") from error

    post("/api/target", {"course_id": course_id, "star_id": star_id})
    if not with_pb:
        return          # the dev db already has its own strat and PB
    # A strategy AND a saved PB, because without them the card renders
    # "pick a strat to see your rank" and the two RANK BANNERS never mount --
    # and the banners are the part the user reports crowding. A fixture that
    # stops at "a target is set" measures a card with the interesting row
    # missing (2026-07-28).
    # star:2:0 with strategy "Standard" is chosen because it HAS rank
    # standards. Pick one that does not and both banners stay null, which is
    # how a fixture ends up measuring a card with its most crowded row absent.
    post("/api/strat", {"course_id": course_id, "star_id": star_id,
                        "strat_tag": "Standard"})
    attempts = json.loads(urllib.request.urlopen(
        f"{base}/api/session?clock=igt&scope=session", timeout=10).read())
    rows = [a for star in attempts.get("stars", [])
            for a in star.get("attempts", []) if a.get("outcome") == "success"]
    if rows:
        post("/api/pb", {"attempt_id": rows[0]["id"], "timer_mode": "igt"})


def _arm_segment(base: str, service, segment_id: int = 1) -> None:
    """Arm a real segment definition and leave it ARMED -- the only way to
    reach `sec.armed_detail` non-null (`.seg-waiting`, Task 6, spec
    2026-07-28-multi-step-segments), which neither the responsive sweep nor
    `tools/measure_objective_card.py` could reach before this (final review
    of that spec, finding 2): both only ever seeded a STAR target, and
    `.seg-waiting` renders only inside `SegmentSection`.

    Defaults to segment id 1, LBLJ -- one of the ten legacy tricks baked
    directly into the schema MIGRATION itself (storage/db.py's v4 INSERT),
    so it exists in every fresh `Database` with no defaults-corpus reconcile
    needed: the fixture never calls `reconcile_defaults` (only `main.py`
    does, at real startup), so a segment from the 84-def corpus would not
    exist here at all.

    Does not touch the active target. An armed segment gets its own section
    (`views.py`'s `seen_segs`: armed OR targeted OR has attempts) and
    therefore its own `.objective-card` in the practice index regardless of
    whether it is the target -- so this composes with whatever star target
    `serve_ui` already seeded rather than replacing it. That card starts
    inside a closed `<details class="practice-index-item">`
    (`ui/components/practice.js`); `tools/uilab_project.py`'s `_EXPAND_ALL`
    opens it before measuring.

    Runs the exact event shape `tests/test_views.py`'s own `lblj_success`/
    `lvl` helpers use -- real `level_changed` events through the real
    matcher, not a hand-built row: 16->6 arms LBLJ, 6->17 closes it as a
    genuine success (giving it a real PB before the card is measured), then
    16->6 again, left unclosed. The measured card then carries a real rank
    AND `armed_detail` together -- the actually-crowded combination, the
    same reasoning `_seed_target`'s own `with_pb` follows for a star.
    """
    import urllib.error
    import urllib.request

    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

    def post(path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{base}{path}", data=json.dumps(payload).encode(), method="POST",
            headers={"Content-Type": "application/json"})
        try:
            return json.loads(urllib.request.urlopen(request, timeout=10).read())
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"fixture could not POST {path}: {error.code} "
                f"{error.read()[:200]!r}") from error

    async def arm_and_close() -> None:
        await service.publish(Event(type="level_changed", frame=5000,
                                    timestamp_utc=now,
                                    payload={"from": 16, "to": 6}))
        await service.publish(Event(type="level_changed", frame=5085,
                                    timestamp_utc=now,
                                    payload={"from": 6, "to": 17}))

    async def rearm() -> None:
        await service.publish(Event(type="level_changed", frame=6000,
                                    timestamp_utc=now,
                                    payload={"from": 16, "to": 6}))

    # A strat active BEFORE the closing edge, so the completed attempt's own
    # strat_tag stamps "Standard" -- LBLJ carries no default_strat (only the
    # corpus movements do: views.py caveat 17), and an unlabelled attempt
    # can't become a PB under a strat the rank banners are keyed on.
    post("/api/strat", {"kind": "segment", "segment_id": segment_id,
                        "strat_tag": "Standard"})
    asyncio.run(arm_and_close())
    # rta_frames == 85 (5085 - 5000), not just outcome == "success": a
    # from_dev_db=True fixture snapshots the REAL journal, which may already
    # hold other successful LBLJ attempts from actual play -- the frame delta
    # this function itself just produced is the only value guaranteed to
    # name the row it just closed rather than some earlier real one.
    attempts = json.loads(urllib.request.urlopen(
        f"{base}/api/session?clock=igt&scope=session", timeout=10).read())
    rows = [a for sec in attempts.get("segments", [])
            if sec.get("segment_id") == segment_id
            for a in sec.get("attempts", [])
            if a.get("outcome") == "success" and a.get("rta_frames") == 85]
    if rows:
        post("/api/pb", {"attempt_id": rows[0]["id"], "timer_mode": "rta"})
    asyncio.run(rearm())


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def serve_ui(db_path: Path | None = None, timeout: float = 30,
              seed: bool = True, from_dev_db: bool = False,
              stage: tuple[int, int] | None = None,
              target: tuple[int, int] | None = None,
              arm_segment: int | None = None):
    """Yield the base URL of an offline instance; stop it on the way out.

    DETERMINISTIC BY DEFAULT: an empty database plus `seed_practice`, so two
    runs a week apart measure the same page.

    It used to snapshot the dev database, for realism, and that realism cost
    more than it bought: the content changes every time the user plays, so the
    defect set drifted underneath the gate. Two rows appeared in one checkout
    that a worktree run minutes earlier had not produced -- not a regression,
    just different data. A gate whose expected set moves on its own trains you
    to ignore it, and `known_defects` rows keyed on viewport + selector cannot
    survive that.

    `from_dev_db=True` still snapshots, for exploratory work where you want
    whatever is really in there. Never for a gate.

    A fresh clone has no dev database at all, so the default also happens to be
    the only mode that works everywhere.

    `arm_segment` additionally arms a real segment definition (see
    `_arm_segment`) -- additive, not a replacement for `target`: an armed
    segment gets its own section regardless of which kind is the active
    target, so a star target and an armed segment coexist on the same page.
    """
    scratch = None
    if db_path is None:
        scratch = tempfile.TemporaryDirectory(prefix="sm64-fixture-")
        db_path = Path(scratch.name) / "fixture.db"
        if from_dev_db and DEV_DB.exists():
            snapshot_db(DEV_DB, db_path)

    database = Database(db_path)
    broadcaster = Broadcaster()
    # `ranks=` is NOT optional here, whatever the signature says. Omit it and
    # every rank builder short-circuits to empty -- /api/ranks/standards starts
    # answering "rank standards unavailable", the rank banners never render,
    # and the Active Target card measures SHORTER than it really is. The first
    # sweep run made exactly that mistake and under-reported the one card it
    # was built to measure (2026-07-28), which is the failure mode
    # .claude/rules/ui-core.md warns reads as a broken builder.
    ranks = RankStandards(rank_standards_path(), bundled_rank_standards())
    ranks.load()
    service = TrackerService(database, broadcaster, ranks=ranks)
    poller = Poller(_OfflineMemory(), [], service)
    app = create_app(poller, broadcaster, service=service)

    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + timeout
        while not server.started and thread.is_alive() \
                and time.monotonic() < deadline:
            time.sleep(0.02)
        if not server.started:
            raise RuntimeError("fixture server failed to start within "
                               f"{timeout}s (port {port})")
        # AFTER startup, never before: publishing on a service whose app
        # lifespan has not run creates nothing at all — measured 2026-07-28,
        # three events in and `db.attempts()` still empty. tests/test_api.py
        # has always seeded inside `with client:` for the same reason; doing it
        # at construction time fails silently, which is the worst version.
        base = f"http://127.0.0.1:{port}"
        if seed:
            # Segment FIRST, star SECOND: `_arm_segment`'s level_changed
            # events cross real course boundaries (Grounds -> HMC -> BitDW),
            # and `_dispatch` retires the ACTIVE STAR TARGET the moment such
            # an event's course differs from the target's own -- a real
            # product rule (projection.py caveat 12), not a fixture quirk.
            # Arming before the star target exists means there is nothing yet
            # for that rule to retire; nothing published afterwards is a
            # level_changed event that could retire it either, so the star
            # target set below survives untouched and coexists with the
            # still-armed segment. Setting a star target itself only journals
            # `target_set` -- it does not read or touch segment arm state.
            if arm_segment is not None:
                _arm_segment(base, service, segment_id=arm_segment)
            course, level = stage or (2, 5)
            seed_practice(service, course_id=course, level=level,
                          attempts=target is None)
            _seed_target(base, *(target or (2, 0)),
                         with_pb=target is None)
        yield base
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        # Close the connection BEFORE removing the directory holding it.
        # Windows refuses to unlink an open file, so a leaked handle here is
        # not a warning -- it is a PermissionError that fails the caller.
        database.close()
        if scratch is not None:
            scratch.cleanup()
