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
from sm64_events.core.timefmt import format_igt
from sm64_events.core.paths import (bundled_defaults_seed, bundled_rank_standards,
                                    rank_standards_path)
from sm64_events.ranks.standards import RankStandards
from sm64_events.server.app import create_app
from sm64_events.server.broadcaster import Broadcaster
from sm64_events.server.poller import Poller
from sm64_events.storage.db import Database
from sm64_events.tracking.defaults import reconcile_defaults
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


# The star the fixture practises, chosen for a reason that is very easy to lose
# in a default argument. `star:2:4` has FIVE strategies in the bundled
# standards, so whichever one is active is not the entity's best ladder and
# BOTH rank banners render (tracking/views.py::ranks_share_ladder).
#
# Until 2026-07-29 this was `star:2:0`, which has exactly one strategy. One
# strategy means the strategy ladder IS the star's best, the two measures are
# one measure, and the card draws a SINGLE banner labelled "Strategy · Star".
# So every sweep ever run measured a one-banner card -- and the entire class of
# "the two banners crowd each other" defects was unreachable by the gate. The
# user reported the stacked washes overlapping three times over two days; it
# could only ever be measured by hand, against his own database.
#
# Whomp's Fortress, "Fall onto the Caged Island", TJ Owlless -- the same card
# he sent a screenshot of.
#
# FIXTURE_LEVEL corrected 5 -> 24 (untagged-PB fix, live report 2026-07-31):
# 5 is course 4's raw level id, not course 2's (WF's real one, per
# tracking/segments.py::_LEVEL_BY_COURSE[2] == 24) -- a mismatch invisible
# until `seed_practice` gained its own explicit `request_target` call (below),
# because the ATTEMPTS loop auto-targets the star on its first star_collected
# event regardless of the player's stage, so `_seed_target`'s later POST always
# saw `already=True` and skipped the practicable_here check this constant
# would otherwise have failed every time. Only mattered once something finally
# asked "is the player standing where they can practice this" BEFORE an
# attempt had already answered it implicitly.
FIXTURE_COURSE = 2
FIXTURE_LEVEL = 24
FIXTURE_STAR = 4
FIXTURE_STRAT = "TJ Owlless"

# The segment the fixture arms, chosen for the SAME reason as FIXTURE_STAR
# above and caught by the same review the day after: LBLJ (segment:1) has
# exactly one bundled strategy, so its strategy ladder IS its best ladder
# (tracking/views.py::ranks_share_ladder) and the armed-segment card drew
# ONE combined banner -- the two-banner-plus-`.seg-waiting` layout has never
# been rendered by any instrument, and a CSS fix scoped to "the non-last
# banner" (index.html) was therefore INERT: with one banner it is both first
# and last, so `:not(:last-child)` matches nothing.
#
# BitFS Pipe Entry (segment:6) has FOUR bundled strategies -- picked over
# segment:5's two for the same margin-of-safety reason FIXTURE_STAR picked
# five over LBLJ's one. `FIXTURE_SEGMENT_STRAT` = "Pole Glitch", deliberately
# NOT the fastest one ("BLJ" is faster at every tier it defines) -- an active
# strategy that happened to tie the entity's own best ladder would merge the
# two banners back into one exactly as LBLJ did.
FIXTURE_SEGMENT = 6
FIXTURE_SEGMENT_STRAT = "Pole Glitch"



# EVERY PLACE EVENT CARRIES A TIME, because the real detectors do (2026-08-06,
# his report: "some events have the timer next to them, most don't? I would
# expect the timer for all of them"). `area.py`, `level.py` and `spawn.py` each
# stamp the shared IgtClock now, so a fixture publishing bare payloads renders
# a recorder nobody will ever see -- and the reach test that counts timed rows
# would go on passing against exactly the state the fix removed.
def _place_time(payload: dict, igt_frames: int) -> dict:
    """The `igt` trio a real place detector stamps, folded onto a hand-built
    payload. One door, so a new fixture event cannot forget the shape."""
    return {**payload, "igt_frames": igt_frames, "igt_source": "counter",
            "igt": format_igt(igt_frames)}


def seed_practice(service, course_id: int = FIXTURE_COURSE,
                  star_id: int = FIXTURE_STAR,
                  level: int = FIXTURE_LEVEL, attempts: bool = True,
                  strat: str | None = None) -> None:
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

    `strat`, when given, sets the ACTIVE TARGET and STRATEGY BEFORE any
    attempt below is recorded -- required, not cosmetic (untagged-PB fix,
    live report 2026-07-31). An attempt's strat_tag is stamped from whichever
    strategy is remembered for the CURRENT target at the moment the attempt
    CLOSES (tracking/projection.py::MatchContext.strat_tag reads
    `self.target`/`strat_by_star`, never retroactively) -- so setting the
    strategy only AFTERWARD, the way `_seed_target` used to do it alone,
    leaves every one of these attempts permanently untagged regardless. That
    is real product behaviour, not a fixture quirk: an untagged attempt behind
    a saved PB is the EXACT shape of the untagged-PB bug this fixture exists
    to render, and this fixture was reproducing it BY ACCIDENT -- which used
    to pass unnoticed because the "unranked" sentinel this untagged PB
    produced happened to render as a real (floored) banner, masking two
    `test_fixture_reaches_the_real_page.py` checks behind a look-alike state
    until `views.py`'s pb_untagged fix correctly told the two apart and
    surfaced "unattributed" instead. Calls `service.request_target` directly
    (no HTTP round-trip -- this runs before the fixture's `base` URL exists).
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
        # `stage_changed` is BROADCAST-ONLY, so it reaches the store and never
        # the journal — which means it says nothing about where these grabs
        # happened to anything that reads events back. `area_changed` is the
        # one type that names the level AND the settled area outright, and the
        # recorder's per-place cards are derived from it alone; without this
        # row every grab below belongs to no place and the cards render as one
        # "Somewhere unrecorded" heap. Added 2026-08-05 with the cards.
        await service.publish(Event(
            type="area_changed", frame=900, timestamp_utc=now,
            payload=_place_time({"level": level, "from": None, "to": 1}, 30)))
        if not attempts:
            return
        if strat:
            await service.request_target("star", course_id=course_id,
                                         star_id=star_id, strat_tag=strat)
        for index, frames in enumerate([343, 361, 352]):
            await service.publish(Event(
                type="practice_reset", frame=1000 + index * 1000,
                timestamp_utc=now, payload={"igt_frames_before": 0}))
            await service.publish(Event(
                type="star_collected", frame=1350 + index * 1000,
                timestamp_utc=now,
                payload={"course_id": course_id, "star_id": star_id,
                         "igt_frames": frames,
                         # An x-cam-timed grab, i.e. the ordinary modern one.
                         # WITHOUT this key a star replays as the GRAB
                         # quantity (projection.py: its absence is exactly
                         # what a pre-2026-08-01 row means), which cannot be
                         # saved as a PB — and this fixture saves one two
                         # steps later to reach the state every render gate
                         # measures.
                         "igt_timed_at": "xcam"}))
        # ...and ONE grab-timed row, because the practice log's caveat badge
        # is only drawn on a row whose x-cam provably never happened, and a
        # fixture made entirely of clean rows measures a log the mark can
        # never appear in. Seeded LAST so `_seed_target` still saves its PB
        # off the first success (a grab-timed one is refused).
        await service.publish(Event(
            type="practice_reset", frame=4000, timestamp_utc=now,
            payload={"igt_frames_before": 0}))
        await service.publish(Event(
            type="star_collected", frame=4350, timestamp_utc=now,
            payload={"course_id": course_id, "star_id": star_id,
                     "igt_frames": 784, "igt_timed_at": "grab"}))

    asyncio.run(go())


def _seed_target(base: str, course_id: int = FIXTURE_COURSE,
                 star_id: int = FIXTURE_STAR, with_pb: bool = True) -> None:
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
    # The strategy must exist in the bundled standards for THIS star, or both
    # banners stay null and the fixture measures a card with its most crowded
    # row absent. See FIXTURE_STRAT for why this particular one -- it is not
    # the star's best ladder, which is what makes the SECOND banner render.
    post("/api/strat", {"course_id": course_id, "star_id": star_id,
                        "strat_tag": FIXTURE_STRAT})
    attempts = json.loads(urllib.request.urlopen(
        f"{base}/api/session?clock=igt&scope=session", timeout=10).read())
    rows = [a for star in attempts.get("stars", [])
            for a in star.get("attempts", []) if a.get("outcome") == "success"]
    if rows:
        post("/api/pb", {"attempt_id": rows[0]["id"], "timer_mode": "igt"})


# Padding for the practice LOG's own pagination (practicelog.js's
# CARDS_PER_PAGE = 5): with only the star target and one armed segment, the
# view carries 2 sections, `sections.length (2) > shown (5)` is never true,
# and the "Show 5 more" control -- plus a full-length list -- had never been
# rendered by any gate (Task 7 review). Four more of the ten legacy tricks
# baked into the schema migration (storage/db.py's v4 INSERT), each closed
# with a SINGLE completed attempt rather than left armed: a completed
# attempt is a permanent journal fact (`views.py`'s `seen_segs`: "has
# attempts" survives regardless of what arms/disarms elsewhere), so padding
# this way can never interfere with `_arm_segment`'s own segment staying
# ARMED at the end of the whole sequence -- which a shared "leave it armed"
# approach could not promise (entering a level foreign to an armed match_mode
# def disarms it; two simultaneously-armed defs at different levels cannot
# both survive a fixture that visits both levels). BitDW/BitS Pipe Entry
# share BitFS Pipe Entry's own [level_enter, attempt_anchor] -> close shape;
# Bowser 1/2 close on key_grabbed instead of warp_entered. Each entry is
# (segment_id, level, close_event_type).
_PADDING_SEGMENTS = (
    (5, 17, "warp_entered"),   # BitDW Pipe Entry
    (7, 21, "warp_entered"),   # BitS Pipe Entry
    (8, 30, "key_grabbed"),    # Bowser 1
    (9, 33, "key_grabbed"),    # Bowser 2
)


def _pad_log_with_more_entities(service) -> None:
    """Complete one attempt on each of `_PADDING_SEGMENTS`, giving the log 4
    more entity cards (6 total with the star + `_arm_segment`'s own segment)
    -- enough to exceed CARDS_PER_PAGE (5) and render "Show 5 more". Must run
    BEFORE `_arm_segment` (caller's own ordering): each entry here is a real
    course-crossing `level_changed`, and `_arm_segment`'s own segment must be
    the LAST thing armed or these would disarm it on their way past (the same
    "nothing published afterwards" invariant `serve_ui`'s own docstring
    states for `_arm_segment` today)."""
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

    async def go() -> None:
        # Frames stay below seed_practice's own range (1000+) and
        # _arm_segment's (5000+) -- this runs before both, and the number is
        # cosmetic (a self-healing detector never assumes frames only move
        # forward, rule 4), but a frame that LOOKS earlier than what
        # chronologically follows it is one less thing to double-take on.
        previous_level = 0
        frame = 100
        for segment_id, level, close_type in _PADDING_SEGMENTS:
            await service.publish(Event(
                type="level_changed", frame=frame, timestamp_utc=now,
                payload=_place_time({"from": previous_level, "to": level},
                                    frame % 600)))
            # >= DEFAULT_MIN_FRAMES (15, projection.py) between arm and close,
            # or the projector auto-ignores the "attempt" as too fast to be
            # real (`_auto_ignored`) -- measured directly: at +10 frames every
            # padding entity's only attempt was cleared, so its card rendered
            # AttemptLogEmpty's "every attempt is filtered out" state instead
            # of a real row. 60 frames (2s) clears the floor with margin.
            frame += 60
            await service.publish(Event(
                type=close_type, frame=frame, timestamp_utc=now,
                payload=_place_time({"level": level}, frame % 600)))
            frame += 100
            previous_level = level

    asyncio.run(go())


def _arm_segment(base: str, service, segment_id: int = FIXTURE_SEGMENT) -> None:
    """Arm a real segment definition and leave it ARMED -- the only way to
    reach `sec.armed_detail` non-null (`.seg-waiting`, Task 6, spec
    2026-07-28-multi-step-segments), which the responsive sweep could not
    reach before this (final review of that spec, finding 2): it only ever
    seeded a STAR target.

    Defaults to `FIXTURE_SEGMENT` (BitFS Pipe Entry, segment id 6) -- one of
    the ten legacy tricks baked directly into the schema MIGRATION itself
    (storage/db.py's v4 INSERT), so it exists in every fresh `Database` with
    no defaults-corpus reconcile needed: the fixture never calls
    `reconcile_defaults` (only `main.py` does, at real startup), so a segment
    from the 84-def corpus would not exist here at all. See `FIXTURE_SEGMENT`
    for why THIS one of the ten, specifically -- it is not an arbitrary pick.

    Does not touch the active target -- an armed segment gets its own SECTION
    regardless (`views.py`'s `seen_segs`: armed OR targeted OR has attempts),
    so this composes with whatever star target `serve_ui` already seeded
    rather than replacing it. Getting a section is not the same as getting the
    `.log-card-active` highlight, though: `Practice()` suppresses every
    segment PIN while a star target is active
    (`pinnedSegs = !inContext || starActive ? [] : ...`) -- so an armed-but-
    not-targeted segment surfaces only as an ORDINARY (non-active) `.log-card`
    in the practice log. Pass `target_segment=segment_id` to `serve_ui`
    (below) when the segment itself needs to BE the active one -- retiring
    the star target is what puts its own `.seg-waiting` on a card carrying
    `.log-card-active` too (amendment A8, spec practice-log-entity-cards).

    Real events through the real matcher, not a hand-built row, matching
    BitFS Pipe Entry's own shape (storage/db.py's v4 INSERT: start
    `level_enter(to=19)` OR `attempt_anchor(level=19)`; end
    `warp_entered(level=19)`): a `level_changed` from 17 (BitDW) to 19 arms
    it, a `warp_entered(level=19)` closes it as a genuine success (giving it
    a real PB before the card is measured), then `level_changed` 17->19
    again, left unclosed. The measured card then carries a real rank AND
    `armed_detail` together -- the actually-crowded combination, the same
    reasoning `_seed_target`'s own `with_pb` follows for a star.
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

    # EVERY level edge is followed by its establishing `area_changed` ON THE
    # SAME FRAME, because that is what the real detectors emit (the corpus
    # walker's own docstring says the same, and for the same reason) -- and
    # because `area_changed` is the ONLY thing that says where the player is.
    # Without these rows every event in the fixture belongs to no place at
    # all, and the recorder's per-place cards render as one "Somewhere
    # unrecorded" heap: a gate reporting a clean page nobody is looking at,
    # which is this rig's own documented failure mode. Added 2026-08-05 with
    # the cards; nothing measured before them depends on an area row.
    async def arm_and_close() -> None:
        await service.publish(Event(type="level_changed", frame=5000,
                                    timestamp_utc=now,
                                    payload=_place_time({"from": 17, "to": 19},
                                                        412)))
        await service.publish(Event(type="area_changed", frame=5000,
                                    timestamp_utc=now,
                                    payload=_place_time({"level": 19,
                                                         "from": None,
                                                         "to": 1}, 412)))
        await service.publish(Event(type="warp_entered", frame=5085,
                                    timestamp_utc=now,
                                    payload=_place_time({"level": 19}, 497)))
        # TWO doors, so the recorder draws BOTH landmark states on one page: the
        # first is named by the shipped catalogue (its key is the HMC Door row in
        # tools/corpus_landmarks.py, taken from his own 2026-08-05 session), the
        # second is a real basement door nobody has named yet and so still reads
        # by its kind. Without a landmark-bearing row here the rename control
        # renders nowhere and every gate passes on a page that cannot show it --
        # this rig's own documented failure mode.
        for frame, home in ((5100, [1126, -1074, -2661]),
                            (5200, [717, -1177, -869])):
            key = "6:3:800ebc8c:{},{},{}".format(*home)
            await service.publish(Event(
                type="moment_reached", frame=frame, timestamp_utc=now,
                payload={"kind": "door_open", "ordinal": 1, "level": 6,
                         "area": 3, "action": 0x00001321,
                         "igt_frames": 200, "igt_source": "counter",
                         "igt": format_igt(200),
                         "landmark": {"key": key, "kind_key": "kind:800ebc8c",
                                      "behaviour": 0x800EBC8C, "home": home,
                                      "placed": True}}))
        # A THIRD moment whose object the GAME made mid-play -- home (0,0,0),
        # `placed` false. Every such object shares that one key, so a name typed
        # on it would land on all of them at once and the recorder must offer no
        # rename control at all. Without this row the rule has nothing to be
        # tested against and a pencil on everything would look correct.
        await service.publish(Event(
            type="moment_reached", frame=5300, timestamp_utc=now,
            payload={"kind": "textbox", "ordinal": 1, "level": 6, "area": 3,
                     "action": 0x20001305, "igt_frames": 260,
                     "igt_source": "counter", "igt": format_igt(260),
                     "landmark": {"key": "6:3:800ee040:0,0,0",
                                  "kind_key": "kind:800ee040",
                                  "behaviour": 0x800EE040, "home": [0, 0, 0],
                                  "placed": False}}))
        # Name the FIRST door and leave the second alone, so one page carries
        # both states. Applied directly rather than through reconcile: the
        # catalogue does not depend on the 84-segment corpus this fixture
        # deliberately skips, and a named row is the whole point of the row.
        service.db.seed_landmark_name("kind:800ebc8c", "Door", "landmark:kind")
        service.db.seed_landmark_name(
            "6:3:800ebc8c:1126,-1074,-2661", "HMC Door", "landmark:hmc")

    async def rearm() -> None:
        await service.publish(Event(type="level_changed", frame=6000,
                                    timestamp_utc=now,
                                    payload=_place_time({"from": 17, "to": 19},
                                                        538)))
        await service.publish(Event(type="area_changed", frame=6000,
                                    timestamp_utc=now,
                                    payload=_place_time({"level": 19,
                                                         "from": None,
                                                         "to": 1}, 538)))

    # A strat active BEFORE the closing edge, so the completed attempt's own
    # strat_tag stamps FIXTURE_SEGMENT_STRAT -- BitFS Pipe Entry carries no
    # default_strat (only the corpus movements do: views.py caveat 17), and
    # an unlabelled attempt can't become a PB under a strat the rank banners
    # are keyed on.
    post("/api/strat", {"kind": "segment", "segment_id": segment_id,
                        "strat_tag": FIXTURE_SEGMENT_STRAT})
    asyncio.run(arm_and_close())
    # rta_frames == 85 (5085 - 5000), not just outcome == "success": a
    # from_dev_db=True fixture snapshots the REAL journal, which may already
    # hold other successful attempts for this segment from actual play -- the
    # frame delta this function itself just produced is the only value
    # guaranteed to name the row it just closed rather than some earlier
    # real one.
    attempts = json.loads(urllib.request.urlopen(
        f"{base}/api/session?clock=igt&scope=session", timeout=10).read())
    rows = [a for sec in attempts.get("segments", [])
            if sec.get("segment_id") == segment_id
            for a in sec.get("attempts", [])
            if a.get("outcome") == "success" and a.get("rta_frames") == 85]
    if rows:
        post("/api/pb", {"attempt_id": rows[0]["id"], "timer_mode": "rta"})
    asyncio.run(rearm())


def _target_segment(base: str, segment_id: int) -> None:
    """Make an already-armed segment the ACTIVE target, retiring whatever
    star `_seed_target` set.

    This is what puts the `.log-card-active` highlight (amendment A8, spec
    practice-log-entity-cards) on the segment's own practice-log card instead
    of the star's -- `Practice()` suppresses every segment pin while a star
    target is active, so `_arm_segment` alone (which deliberately never
    touches the target) leaves the segment as an ordinary, non-active
    `.log-card`. Retiring the star is the server's own rule (one active
    target, mutually exclusive kinds), and because the segment stays ARMED
    throughout, its section keeps `armed_detail` non-null through the swap --
    verified against a live fixture (2026-08-03): `POST /api/target` with a
    segment body returns `ok`, the session view's `target` flips to
    `kind: "segment"`, and the segment's own section still carries both
    `armed_detail` (still mid-run) and populated `rank`/`entity_rank` (two
    banners render, since `one_ladder` is false for a strategy that is not
    the entity's own best -- see FIXTURE_SEGMENT_STRAT above)."""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(
        f"{base}/api/target",
        data=json.dumps({"kind": "segment", "segment_id": segment_id}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(request, timeout=10)
    except urllib.error.HTTPError as error:
        raise RuntimeError(
            f"fixture could not target segment {segment_id}: {error.code} "
            f"{error.read()[:200]!r}") from error


def _publish_bowser_stage(service, course_id: int, level: int) -> None:
    """Publish a `stage_changed` naming a Bowser-course PIPE stage (BitDW/
    BitFS/BitS) -- the one `mode` `seed_practice`'s own stage_changed call can
    never produce, since that call hardcodes `mode="stars"` (see its own
    comment). Without this, `t.stage.mode` is never anything but "stars" or
    whatever `seed_practice` was given, and `stagebanner.js::BowserCourseRow`
    -- three cells since 912466d rewrote it from two -- had never been
    rendered by any gate, before OR after that rewrite (task-bowser-sweep).

    Additive, like `_arm_segment`: does not touch the star target `_seed_
    target` sets. `stage_changed` is broadcast-only and retires nothing on
    its own (detectors/stage.py's own docstring) -- only a JOURNALED
    `level_changed` retires an active star target on a real course change
    (projection.py caveat 12), and this publishes no such event. So a page
    can carry an ordinary star target (Whomp's, say) for the Active Target
    card while the quick-select banner above it shows a completely different
    course's Bowser row -- the same "coexist" shape `_arm_segment` already
    relies on for the armed-segment card.
    """
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

    async def go() -> None:
        await service.publish(Event(
            type="stage_changed", frame=5200, timestamp_utc=now,
            payload={"course_id": course_id, "level": level, "area": 1,
                     "mode": "bowser_course"}))

    asyncio.run(go())


def _enter_level(service, level: int, frame: int = 9000) -> None:
    """Publish ONE real `level_changed` entering `level`, arming whatever
    real definition(s) key off it and leaving them ARMED -- no closing event
    follows, so this is for a STRUCTURAL render check (a section merely
    EXISTING is enough -- `pipe_star_entity`/`armed_detail` are both derived
    from the definition's shape, not from an attempt's outcome), never for
    exercising a saved PB. Kept generic (by level, not by segment id): the
    Bowser reds->pipe family and the legacy no-reds pipe trio both arm off
    the identical `[level_enter, attempt_anchor]` idiom, and a caller
    reconciling the full corpus may not know their post-reconcile ids
    (`tracking-storage.md`'s `arms_ambiently`)."""
    now = datetime(2026, 6, 10, 12, 0, 0, tzinfo=timezone.utc)

    async def go() -> None:
        await service.publish(Event(
            type="level_changed", frame=frame, timestamp_utc=now,
            payload=_place_time({"from": 0, "to": level}, 271)))

    asyncio.run(go())


def _arm_hundred_coin_star(base: str, service, course_id: int, level: int) -> None:
    """Arm a SYNTHETIC 100-coin-star engine for `course_id`'s star 6 and
    leave it armed -- the STAR half of `armed_detail` (Task 7 review):
    `_arm_segment` above only ever exercises the SEGMENT half, so
    `segments.hundred_coin_entity`'s reattribution path -- and the star-kind
    `.seg-waiting` row `practicelog.js`'s `LogCard` shares between kinds --
    had never been rendered by any gate, only unit-tested against hand-built
    dicts (`tests/test_ui_entity_section.py`).

    `hundred_coin_entity` (tracking/segments.py) pattern-matches a
    definition's raw trigger clauses -- it does not care whether the shape is
    one of the 15 bundled hundred-coin exits or a hand-authored one -- so no
    defaults-corpus reconcile is needed, only a def whose end trigger reads
    `star_grabbed(star=6, course=course_id)`. Its ARM position is `level`,
    the caller's own FIXTURE_LEVEL (WF) -- deliberately the SAME level
    `_arm_segment`'s own BitFS Pipe Entry re-arms on at the very end of the
    whole sequence would NOT be, if this ran independently: two defs armed
    at two different levels cannot both survive a fixture that visits both
    (entering a level foreign to an armed match_mode def disarms it). Callers
    of this helper therefore run it WITHOUT `arm_segment` also set, so there
    is nothing else to conflict with and no such visit happens.

    The resulting entity (`star:{course_id}:6`) is that course's own real
    100-coin star, coexisting with any ordinary star target already set on
    the SAME course (arming does not retire a target on its own course --
    `projection.py`'s course-scoped retirement rule)."""
    import urllib.error
    import urllib.request

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

    post("/api/segments", {
        "name": "Fixture 100 Coins",
        "start_triggers": [{"type": "level_enter", "to": level},
                          {"type": "attempt_anchor", "level": level}],
        # `hundred_coin_entity` (tracking/segments.py) scans start_triggers
        # and WAYPOINTS, never end_triggers -- the real corpus's own
        # HUNDRED_COIN_EXITS (tools/corpus_movements.py) puts the 100-coin
        # grab in `via` (waypoints), matching "you don't exit the stage when
        # you grab 100 coins, you must find another star to exit". End
        # trigger is any OTHER star purely so the def validates; it is never
        # meant to fire (this stays armed, not closed).
        "waypoints": [[{"type": "star_grabbed", "star": 6, "course": course_id}]],
        "end_triggers": [{"type": "star_grabbed", "star": 0, "course": course_id}],
        "guards": [], "enabled": True, "match_mode": "strict",
    })
    _enter_level(service, level, frame=8000)


# Two user-authored segments, byte-identical to each other, for the segments-
# editor Story/tests (uilab_project.py, test_fixture_reaches_the_real_page.py)
# -- opening either one must show a REAL `duplicate` lint finding (the lint
# panel renders NOTHING at all when `lintFindings` is empty:
# `${lintFindings.length > 0 && html...}` in segments.js, so a definition with
# no finding sweeps an invisible panel), a split panel (needs exactly one
# waypoint) and a merge panel (needs another segment to offer, which the
# other of this pair, plus the ten legacy tricks, already supplies).
#
# Levels 12/13/14/15 (Jolly Roger Bay / Tiny-Huge Island / Tick Tock Clock /
# Rainbow Ride) are deliberately INERT: none of `_arm_segment`'s events (16,
# 6, 17) ever touch them, so neither fixture arms and neither adds an
# unplanned `.objective-card` to the practice index -- these two exist purely
# to be opened in the editor, never to be practiced.
_EDITOR_FIXTURE_DEFINITION = {
    "start_triggers": [{"type": "level_enter", "to": 13, "from": 12}],
    "end_triggers": [{"type": "level_enter", "to": 14}],
    "guards": [], "enabled": True,
    "waypoints": [[{"type": "level_enter", "to": 15}]],
    "match_mode": "strict",
}


def _seed_editor_fixtures(base: str) -> None:
    """POST the two `_EDITOR_FIXTURE_DEFINITION` segments (see its own
    comment). Read-only for everything else: no target, no arming, nothing
    published through `service` -- just two ordinary saved definitions the
    library can list and the editor can open."""
    import urllib.error
    import urllib.request

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

    for suffix in ("A", "B"):
        post("/api/segments",
             {"name": f"Editor Fixture {suffix}", **_EDITOR_FIXTURE_DEFINITION})


# A PARENT and its two SUBSECTIONS, all starting in the fixture's own level,
# so the selector's progressive disclosure has something to disclose. Nothing
# in the shipped corpus carries a `parent`, so before this the expanded row
# was unreachable by every instrument -- and the two defects in `28ef261`
# (a blank moment dropdown, a subarea selector that meant nothing) are what
# that costs: both invisible to every assertion, both obvious on sight.
#
# Parented to FIXTURE_STAR, deliberately: "sometimes we want to practice only
# a small portion of a star" is the case Griffin named first, and the STAR row
# is the one that had no disclosure wiring at all until 2026-08-05.
#
# `moment_reached` starts them, which is also what makes them practicable
# HERE: `segments.start_levels` reads a moment's own `level`, so these three
# surface in the Whomp's Fortress row and nowhere else.
def _subsection_definition(ordinal: int) -> dict:
    return {
        "start_triggers": [{"type": "moment_reached", "kind": "door_open",
                            "level": FIXTURE_LEVEL, "ordinal": ordinal}],
        "end_triggers": [{"type": "star_grabbed", "course": FIXTURE_COURSE,
                          "star": FIXTURE_STAR}],
        "guards": [], "enabled": True, "waypoints": [], "match_mode": "loose",
    }


def _seed_subsections(base: str) -> None:
    """POST a parent movement in the fixture's level plus two subsections of
    the fixture STAR -- the state `visibleEntities` expands into."""
    import urllib.error
    import urllib.request

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

    star_key = f"star:{FIXTURE_COURSE}:{FIXTURE_STAR}"
    for ordinal, name in enumerate(("Tower Climb", "Owl Drop"), start=1):
        post("/api/segments", {"name": name, "parent": star_key,
                               **_subsection_definition(ordinal)})


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def serve_ui(*args, **kwargs):
    """`serve_ui_live`, with the service handle dropped.

    THE default door, and the one every gate uses: a caller that only needs a
    URL should not be handed a live TrackerService it could publish through by
    accident. The one caller that DOES need it is measuring latency -- how
    long a real game event takes to reach the screen -- and that cannot be
    asked of a page whose server nothing can make anything happen on.
    """
    with serve_ui_live(*args, **kwargs) as (base, _service):
        yield base


@contextlib.contextmanager
def serve_ui_live(db_path: Path | None = None, timeout: float = 30,
              seed: bool = True, from_dev_db: bool = False,
              stage: tuple[int, int] | None = None,
              target: tuple[int, int] | None = None,
              arm_segment: int | None = None,
              target_segment: int | None = None,
              seed_editor_fixtures: bool = False,
              seed_subsections: bool = False,
              reconcile_full_corpus: bool = False,
              bowser_stage: tuple[int, int] | None = None,
              enter_level: int | None = None,
              arm_hundred_coin: tuple[int, int] | None = None):
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
    segment gets its own SECTION regardless of which kind is the active
    target, so a star target and an armed segment coexist on the same page.
    It does NOT carry the `.log-card-active` highlight unless it is also the
    active target (see `target_segment` below) -- `Practice()` suppresses
    every segment pin while a star target is active, so an armed-but-
    untargeted segment surfaces only as an ordinary `.log-card`.

    `target_segment` additionally makes an armed segment the ACTIVE target
    (see `_target_segment`), retiring whatever star `target`/`_seed_target`
    set. Pass the SAME id as `arm_segment` to reach the one state that puts
    two rank banners AND a `.seg-waiting` row on the SAME `.log-card`, the
    one also carrying `.log-card-active`.

    `seed_editor_fixtures` additionally POSTs two saved, byte-identical
    segments purpose-built for opening in the Segments editor (see
    `_seed_editor_fixtures`) -- this branch's authoring surfaces (lint,
    backtest, split, merge) have never been rendered by any gate, because
    reaching them needs a definition on disk to open, which no earlier
    fixture state provided.

    `seed_subsections` additionally POSTs two subsections of the fixture STAR
    (see `_seed_subsections`) -- the only way to reach the selector's EXPANDED
    state, since nothing in the shipped corpus carries a `parent`.

    `reconcile_full_corpus` additionally applies the bundled 84-segment
    default corpus (`tracking/defaults.reconcile_defaults` against `data/
    defaults.seed.json`) to a FRESH db, the same call `main.py` makes at real
    startup and this fixture otherwise never makes (see the module docstring
    on `FIXTURE_SEGMENT` -- "the fixture never calls reconcile_defaults...
    so a segment from the 84-def corpus would not exist here at all"). Needed
    for anything that depends on a corpus-only row rather than one of the ten
    legacy tricks baked into the schema migration itself -- e.g. the Bowser
    "reds -> pipe" segments (`seg:reds->pipe:*`, Task 20), which coexist with
    the legacy `seg:*-pipe` trio only once this has run.

    `bowser_stage` additionally publishes a `stage_changed` naming a Bowser
    course's pipe stage -- `(course_id, level)`, e.g. `(16, 17)` for BitDW --
    with `mode="bowser_course"` (see `_publish_bowser_stage`). Without it
    `t.stage.mode` can never be anything but "stars", the mode `seed_practice`
    hardcodes, and `stagebanner.js::BowserCourseRow` is unreachable by this
    fixture no matter what `stage`/`target` are given.

    `enter_level` publishes ONE real `level_changed` entering that level (see
    `_enter_level`) -- for arming a def structurally, by LEVEL rather than by
    segment id (needed after `reconcile_full_corpus` for a corpus-only def
    whose post-reconcile id the caller does not know, e.g. the Bowser
    `seg:reds->pipe:<abbrev>` family). Callers combining this with
    `arm_segment` must reason about ordering themselves -- unlike the padding
    below, this is a single caller-controlled event, not a sequence this
    module already orders safely.

    `arm_hundred_coin` additionally arms a SYNTHETIC 100-coin-star engine for
    `(course_id, level)` and leaves it armed (see `_arm_hundred_coin_star`) --
    the star-kind half of `armed_detail` (`_arm_segment` above only ever
    exercises the segment kind). Pass this WITHOUT `arm_segment`: two defs
    armed at two different levels cannot both survive a fixture that visits
    both (a foreign level change disarms an armed def), so this and
    `arm_segment` are for two separate, independent fixture instances, not
    one shared one.
    """
    scratch = None
    if db_path is None:
        scratch = tempfile.TemporaryDirectory(prefix="sm64-fixture-")
        db_path = Path(scratch.name) / "fixture.db"
        if from_dev_db and DEV_DB.exists():
            snapshot_db(DEV_DB, db_path)

    database = Database(db_path)
    if reconcile_full_corpus:
        # Before the server starts: reconcile is a plain db-level operation
        # (mirrors main.py's own startup call), and doing it early means every
        # request the fixture makes afterwards already sees the full corpus.
        seed_path = bundled_defaults_seed()
        if seed_path is not None:
            seed_data = json.loads(seed_path.read_text(encoding="utf-8"))
            problems = reconcile_defaults(database, seed_data)
            if problems:
                raise RuntimeError(
                    f"fixture's reconcile_defaults skipped rows: {problems}")
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
                # Pad FIRST: each padding entity is a real course-crossing
                # level_changed that would disarm `arm_segment`'s own
                # still-armed instance if it ran after (see
                # `_pad_log_with_more_entities`'s own docstring).
                _pad_log_with_more_entities(service)
                _arm_segment(base, service, segment_id=arm_segment)
            if seed_editor_fixtures:
                _seed_editor_fixtures(base)
            if seed_subsections:
                _seed_subsections(base)
            course, level = stage or (FIXTURE_COURSE, FIXTURE_LEVEL)
            seed_practice(service, course_id=course, level=level,
                          star_id=(target or (0, FIXTURE_STAR))[1],
                          attempts=target is None,
                          strat=FIXTURE_STRAT if target is None else None)
            _seed_target(base, *(target or (FIXTURE_COURSE, FIXTURE_STAR)),
                         with_pb=target is None)
            if target_segment is not None:
                # AFTER _seed_target, not before: retiring the star target
                # _seed_target just set is the whole point (see
                # _target_segment's own docstring). Requires the segment to
                # already be armed (arm_segment), or there is no `armed_
                # detail` for the resulting card to carry.
                _target_segment(base, target_segment)
            if bowser_stage is not None:
                # AFTER _seed_target, not instead of it: broadcast-only and
                # retires nothing (see _publish_bowser_stage), so the star
                # target set above survives untouched underneath a Bowser
                # quick-select banner from a different course entirely.
                _publish_bowser_stage(service, *bowser_stage)
            if enter_level is not None:
                _enter_level(service, enter_level)
            if arm_hundred_coin is not None:
                _arm_hundred_coin_star(base, service, *arm_hundred_coin)
        yield base, service
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        # Close the connection BEFORE removing the directory holding it.
        # Windows refuses to unlink an open file, so a leaked handle here is
        # not a warning -- it is a PermissionError that fails the caller.
        database.close()
        if scratch is not None:
            scratch.cleanup()
