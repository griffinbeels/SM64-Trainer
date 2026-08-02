# src/sm64_events/tracking/service.py
"""Event pipeline + command surface.

The Poller publishes here (duck-typed broadcaster). Order per event:
broadcast first (liveness is never gated on the db), then journal, then
feed the projector; attempts closed by the event are persisted and an
attempt_completed derived event is emitted through the same pipeline
(the projector ignores derived types, so this cannot recurse).

Commands (set_target, clear/restore, save_pb/undo_pb, wipe_data,
new_session) append journal
events through the same path so the journal stays the single source of
truth; clear/restore re-run the full projection because their effect is
retroactive. With db=None the service degrades to broadcast-only.
Tracking failures are isolated inside publish() so the poll loop never
dies (spec §9)."""
import dataclasses
import json
import logging
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.paths import bundled_defaults_seed
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import course_name, node_label, star_name
from sm64_events.ranks.classify import RANK_MODES
from sm64_events.ranks.standards import entity_key
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking import practicable
from sm64_events.tracking.defaults import resolve_steps
from sm64_events.tracking.projection import (Projector, journal_id, replay,
                                             wipe_matches)
from sm64_events.tracking.segments import (SegmentDef, hundred_coin_entity,
                                           merge_definitions,
                                           segment_origin, split_definition,
                                           star_origin, validate_definition)
from sm64_events.tracking import routes as route_logic

log = logging.getLogger("sm64.tracker")

RUN_OFFSET_MIN, RUN_OFFSET_MAX = 0, 600000   # 0..10 min, ms


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _strategies_key(ek: str) -> str:
    """`strategies` ui_state KV key for a rank entity_key.

    star:C:S -> "C:S" (the historical shape — live dbs are keyed that way,
    don't change it); segment:N -> "seg:N", mirroring the timeline_markers
    namespace. views.py reads both back."""
    if ek.startswith("segment:"):
        return f"seg:{ek.split(':')[1]}"
    return ek[len("star:"):]


def _route_member_stars(route: dict) -> list[str]:
    """"<course>:<star>" keys for every star a route's steps reference, in
    route order, de-duplicated. Deliberately the SAME key shape as the session
    view's last_strat_by_star / rank_by_star maps so the UI tests membership
    with one Set lookup instead of a second convention."""
    keys: list[str] = []
    for step in route.get("steps") or []:
        for cand in step.get("candidates") or []:
            if cand.get("type") != "star":
                continue
            key = f"{cand['course']}:{cand['star']}"
            if key not in keys:
                keys.append(key)
    return keys


class TrackerService:
    def __init__(self, db: Database | None, broadcaster, ranks=None):
        self.db = db
        self.broadcaster = broadcaster
        self.ranks = ranks            # RankStandards | None
        self.session_id: int | None = None
        self._segment_defs = self._load_segment_defs()
        self._projector = Projector(segments=self._segment_defs,
                                    time_filters=self._time_filters(),
                                    origin_overrides=self._origin_overrides())
        self._current_stage = {"course_id": None, "level": None,
                               "area": None, "mode": None}
        self._persisted_runs: list[int] = []

    def _load_segment_defs(self) -> list[SegmentDef]:
        # inclusion list (the dataclass's own fields), NOT exclusion of
        # created_utc: a future db column must not TypeError startup
        if self.db is None:
            return []
        keys = [f.name for f in dataclasses.fields(SegmentDef)]
        return [SegmentDef(**{k: row[k] for k in keys})
                for row in self.db.segment_defs()]

    def _time_filters(self) -> dict:
        """Star validity-bounds overrides (ui_state KV) for the projector;
        {} in degraded mode. Segment bounds ride the defs themselves."""
        return self.db.get_state("time_filters", {}) if self.db is not None else {}

    async def attach_db(self, db: Database) -> None:
        """Late DB attach: upgrade a broadcast-only instance to full
        tracking. A restart handoff that loses the instance-lock race
        builds this service with db=None; once the lock frees (the old
        process finally exited) the reattach loop in server/app.py hands
        the freshly opened Database here. start() rebuilds the projector
        from the journal and opens a session exactly as a clean boot
        would, and its session_started broadcast makes the UI refetch the
        view — the page un-sticks without a reload (live incident
        2026-07-23: post-update GUI stuck on 'loading…')."""
        self.db = db
        self._segment_defs = self._load_segment_defs()
        self._projector = Projector(segments=self._segment_defs,
                                    time_filters=self._time_filters(),
                                    origin_overrides=self._origin_overrides())
        await self.start()

    # -- pipeline -------------------------------------------------------------
    async def start(self) -> None:
        if self.db is None:
            log.error("tracker running WITHOUT a database (broadcast-only)")
            return
        events = self.db.events()
        attempts, self._projector = replay(
            events, segments=self._segment_defs,
            time_filters=self._time_filters(),
            origin_overrides=self._origin_overrides())
        self.db.replace_attempts(attempts)
        # Boot repair, same reason as in _reproject: an orphaned pb row keeps
        # GRADING, so a db that already carries some from an older delete or
        # wipe would otherwise keep showing a rank for a star with no history
        # until something happened to trigger a re-projection.
        orphaned = self.db.delete_orphaned_pbs()
        if orphaned:
            log.info("dropped %d PB row(s) whose attempts no longer exist", orphaned)
        self.db.replace_runs([r.as_row() for r in self._projector.finished_runs()])
        self._persisted_runs = [r.id for r in self._projector.finished_runs()]
        self.session_id = self.db.insert_session(_iso(_now()))
        await self.publish(Event(type="session_started", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"session_id": self.session_id}))

    async def publish(self, event: Event) -> None:
        seq = await self.broadcaster.publish(event)
        if event.type == "stage_changed":
            # Live presentation signal: cache for the session view's initial
            # load and NEVER journal it (recomputable from curr_level; a
            # journal row would only add replay/projection noise). Same
            # broadcast-only discipline as the segment notices.
            self._current_stage = dict(event.payload)
            return
        if self.db is None or self.session_id is None:
            return
        try:
            await self._track(event, seq)
        except Exception:
            log.exception("tracking pipeline failed for %s; event broadcast only",
                          event.type)

    async def _track(self, event: Event, seq: int) -> None:
        jid = self.db.append_event(self.session_id, seq, event)
        row = EventRow(id=jid, session_id=self.session_id, seq=seq,
                       type=event.type, frame=event.frame,
                       wall_time_utc=_iso(event.timestamp_utc),
                       payload=event.payload)
        if event.type == "star_time_corrected":
            # Usamune revised a time we already showed him (projection.py
            # caveat 19). The correction is a compensating event like a clear
            # or a strat reclassification, and like those it is folded in by a
            # PRE-PASS — which the sequential projector, fed one row at a
            # time, cannot run. So rebuild rather than patch the one row: one
            # door, and the live state cannot drift from what a restart would
            # rebuild. Measured at ~140 ms over his 20k-event journal, paid
            # once per SUBAREA star, while Mario is locked in a dance.
            await self._reproject()
            return
        proj = self._projector
        target_before = proj.target
        closed = proj.feed(row)
        # Drain segment notices IMMEDIATELY, BEFORE the attempt loop:
        # publishing attempt_completed below re-enters _track, whose nested
        # feed() RESETS projector.segment_notices — draining after the loop
        # would silently lose armed/disarmed events. Broadcast-only via
        # broadcaster.publish (NOT self.publish): notices are ephemeral UI
        # state and must never be journaled.
        #
        # Identity guard (`self._projector is not proj`): every await below
        # can suspend this tail while a CRUD command reprojects, SWAPPING
        # self._projector. The replay already accounted for this journaled
        # row — finishing the tail would upsert stale attempts from the
        # replaced projector and emit spurious notices/target_changed, so
        # abandon it after any await that saw a swap.
        for n in list(proj.segment_notices):
            await self.broadcaster.publish(Event(
                type=n["event"], frame=n["frame"],
                timestamp_utc=event.timestamp_utc,
                payload={"segment_id": n["segment_id"], "name": n["name"]}))
            if self._projector is not proj:
                return
        # Run drain: persist any newly finished/aborted runs produced by this
        # event. These are broadcast-only derived events (run_finished/run_aborted
        # must NEVER be journaled — the projector re-derives them on replay from
        # run_started + game_reset + completions). Use broadcaster.publish, not
        # self.publish, for the same reason segment notices use it.
        for r in proj.finished_runs()[len(self._persisted_runs):]:
            self.db.upsert_run(r.as_row())
            self._persisted_runs.append(r.id)
            await self.broadcaster.publish(self._run_completed_event(r, event))
            if self._projector is not proj:
                return
        for n in list(proj.run_notices):
            await self.broadcaster.publish(Event(
                type=n["event"], frame=event.frame,
                timestamp_utc=event.timestamp_utc, payload=n))
            if self._projector is not proj:
                return
        for attempt in closed:
            self.db.upsert_attempt(attempt)
            # The derived event's journal row carries the CURRENT session_id,
            # which for a cross-session abandon differs from the attempt's own
            # session_id — the payload's session_id is authoritative.
            await self.publish(self._attempt_completed_event(attempt, event))
            if self._projector is not proj:
                return
        if self._projector.target != target_before:
            await self.publish(Event(
                type="target_changed", frame=event.frame,
                timestamp_utc=event.timestamp_utc,
                payload=self.target_payload()))

    def _attempt_completed_event(self, a, close_event: Event) -> Event:
        return Event(type="attempt_completed", frame=close_event.frame,
                     timestamp_utc=close_event.timestamp_utc, payload={
                         "attempt_id": a.id, "session_id": a.session_id,
                         "kind": "segment" if a.segment_id is not None else "star",
                         "segment_id": a.segment_id,
                         "segment_name": self._segment_name(a.segment_id),
                         "course_id": a.course_id, "star_id": a.star_id,
                         "course_name": course_name(a.course_id) if a.course_id is not None else None,
                         "star_name": star_name(a.course_id, a.star_id) if a.course_id is not None else None,
                         "strat_tag": a.strat_tag,
                         "anchor_type": a.anchor_type, "outcome": a.outcome,
                         "outcome_detail": a.outcome_detail,
                         "igt_frames": a.igt_frames,
                         "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
                         "rta_frames": a.rta_frames,
                         "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
                         "rollouts_total": a.rollouts_total,
                         "rollouts_dustless": a.rollouts_dustless,
                         "jumps_total": a.jumps_total,
                         "jumps_dustless": a.jumps_dustless,
                     })

    def _segment_name(self, segment_id: int | None) -> str | None:
        if segment_id is None:
            return None
        return next((d.name for d in self._segment_defs
                     if d.id == segment_id), f"segment {segment_id}")

    def target_payload(self) -> dict:
        """Kind-aware target identity. ONE builder for both consumers --
        target_changed broadcasts and the session view (views.py) -- so the
        WS payload and GET /api/session can never drift."""
        tgt = self._projector.target
        if tgt and tgt[0] == "segment":
            # course_id/star_id stay present-as-None for shape stability:
            # the UI header keys off course_id presence for star targets.
            return {"kind": "segment", "segment_id": tgt[1],
                    "segment_name": self._segment_name(tgt[1]),
                    "course_id": None, "star_id": None,
                    "strat_tag": self._projector.strat_tag}
        c, s = (tgt[1], tgt[2]) if tgt else (None, None)
        return {"kind": "star", "course_id": c, "star_id": s,
                "strat_tag": self._projector.strat_tag}

    # -- state ------------------------------------------------------------------
    @property
    def target(self):
        return self._projector.target

    @property
    def strat_tag(self):
        return self._projector.strat_tag

    @property
    def strat_by_star(self) -> dict:
        return self._projector.strat_by_star

    @property
    def strat_by_segment(self) -> dict:
        return self._projector.strat_by_segment

    @property
    def current_stage(self) -> dict:
        """The quick-select banner context the player is standing in (payload's
        `mode`: stars / bowser_course / arena / castle / None), cached from the
        broadcast-only stage_changed event for the session view's initial load.
        See detectors/stage.py."""
        return self._current_stage

    @property
    def segment_defs(self) -> list[SegmentDef]:
        """Loaded definitions (enabled AND disabled -- the view names
        sections for disabled defs too; only the engine filters)."""
        return self._segment_defs

    @property
    def armed_segment_ids(self) -> set[int]:
        """Live armed set, straight from the projector -- lets a view
        refresh self-heal the UI's armed badge after missed notices."""
        return self._projector.armed_segment_ids()

    @property
    def armed_arms(self) -> dict[int, dict]:
        """Per-armed-id progress/total/start_frame/deadline_frame (spec
        2026-07-28-multi-step-segments) -- same self-heal rationale as
        armed_segment_ids: a plain view refresh reads the projector directly
        rather than depending on a missed segment_disarmed/armed notice."""
        return self._projector.armed_arms()

    def _require_db(self) -> Database:
        if self.db is None or self.session_id is None:
            raise RuntimeError("database unavailable")
        return self.db

    # -- commands ----------------------------------------------------------------
    def _register_strategy(self, db: Database, ek: str, strat_tag: str) -> None:
        """Append strat_tag to the entity's registered list (ui_state KV) if new.

        Registration is what makes a just-picked strategy survive in the
        dropdown before any attempt has been recorded under it — stars and
        segments both need it (views.py unions this with the observed and
        rank-standard names)."""
        key = _strategies_key(ek)
        strategies = db.get_state("strategies", {})
        existing = strategies.get(key, [])
        if strat_tag not in existing:
            strategies[key] = existing + [strat_tag]
            db.set_state("strategies", strategies)
        self._clear_tombstone(db, ek, strat_tag)

    def _clear_tombstone(self, db: Database, ek: str, strat: str) -> None:
        """Re-creating a strat un-deletes it: the tombstone (see
        purge_strategy) must not outlive the name's next creation, or the
        new strat would be invisible in every dropdown."""
        tombs = db.get_state("deleted_strats", {})
        if strat in tombs.get(ek, []):
            tombs[ek] = [s for s in tombs[ek] if s != strat]
            if not tombs[ek]:
                tombs.pop(ek)
            db.set_state("deleted_strats", tombs)

    # -- practice target: request (may refuse) vs set (always commits) -------
    # set_target / set_target_segment below are the COMMIT primitives -- they
    # move the target now, and every internal caller (a segment success, the
    # banner) wants exactly that. request_target is the layer the UI posts to:
    # it decides whether the pick is something the player can practice where
    # they stand, and REFUSES it if not (tracking/practicable.py). Keeping the
    # two apart is what stops the commit path re-entering the decision.

    def target_node(self, kind: str, course_id: int | None = None,
                    star_id: int | None = None,
                    segment_id: int | None = None) -> str | None:
        """The world node an entity is practiced at — THE resolution, used by
        the target rule here and by the picker's availability flags. A segment
        that no longer exists resolves to None ("anywhere"), which lets the
        caller's own 404 report the real problem."""
        if kind != "segment":
            return star_origin(course_id, star_id)
        found = next((d for d in self._segment_defs if d.id == segment_id),
                     None)
        if found is None:
            return None
        return segment_origin(segment_id, found.start_triggers,
                              self._origin_overrides())

    def _origin_overrides(self) -> dict:
        return self.db.get_state("origin_overrides", {}) \
            if self.db is not None else {}

    async def request_target(self, kind: str, course_id: int | None = None,
                             star_id: int | None = None,
                             segment_id: int | None = None,
                             strat_tag: str | None = None,
                             clear_strat: bool = False) -> dict:
        """Set the practice target, or refuse a pick from somewhere else.

        You practice what is in front of you: a pick the player is not
        standing in front of is rejected (409) rather than applied or held
        (user ruling 2026-07-27 -- see tracking/practicable.py). Re-picking
        what is ALREADY the target always succeeds: only its strategy can
        differ, and refusing a strategy edit because the player has since
        walked off would be a second bug wearing the first one's clothes.

        A star pick of (course, 6) -- the 100-coin star -- commits as a
        PLAIN star target like any other (spec 2026-07-28-multi-step-
        segments, "the 100-coin star IS the segment"): it no longer redirects
        to a segment target. The retired `_hundred_coin_redirect` existed so
        the practice card would show the family's real attempts/strat/rank,
        which lived on the segment; now that the engine's own completed
        attempts attribute directly to this star (tracking/projection.py's
        seg_closed reattribution via segments.hundred_coin_entity), a plain
        star target already shows the same thing with no indirection. The
        underlying HUNDRED_COIN_EXIT engine still arms and matches on its own
        world-state rules regardless of what the target is set to -- arming
        was always target-independent, and this target ONLY decides what the
        practice card highlights.
        """
        self._require_db()
        if kind == "segment" and segment_id is not None:
            found = next((d for d in self._segment_defs if d.id == segment_id),
                        None)
            hc = (hundred_coin_entity(found.start_triggers, found.waypoints)
                 if found is not None else None)
            if hc is not None:
                # This def IS a star now (spec 2026-07-28-multi-step-
                # segments) -- an explicit segment pick (a route candidate,
                # a direct API call) must land on the star it represents,
                # never on the segment itself, which no longer surfaces
                # anywhere a pick could originate from.
                kind, course_id, star_id, segment_id = "star", hc[0], hc[1], None
        if kind == "segment":
            if segment_id is None:
                raise ValueError("segment target needs segment_id")
            if all(d.id != segment_id for d in self._segment_defs):
                raise LookupError(f"segment {segment_id} not found")
            already = self.target == ("segment", segment_id)
        else:
            if course_id is None or star_id is None:
                raise ValueError("star target needs course_id and star_id")
            already = self.target == ("star", course_id, star_id)
        node = self.target_node(kind, course_id, star_id, segment_id)
        # A segment whose start trigger has already fired is UNDER WAY, and
        # you pick it where it has got you to, not where it began (user
        # ruling 2026-08-01 — practicable.py carries the reasoning). Read from
        # the projector, which re-derives the armed set from the journal, so
        # this asks the same question the banner's own armed cells answer:
        # what the banner offers and what the server accepts cannot disagree.
        running = (kind == "segment"
                   and segment_id in self._projector.armed_segment_ids())
        if not already and not practicable.practicable_here(self._current_stage,
                                                            node, running):
            raise ValueError(
                "you can only practice what you are standing in — "
                f"that one is in {node_label(node)}")
        if kind == "segment":
            await self.set_target_segment(segment_id, strat_tag,
                                          clear_strat=clear_strat)
        else:
            await self.set_target(course_id, star_id, strat_tag,
                                  clear_strat=clear_strat)
        return {}

    async def set_target(self, course_id: int, star_id: int,
                         strat_tag: str | None = None,
                         clear_strat: bool = False) -> None:
        db = self._require_db()
        payload = {"course_id": course_id, "star_id": star_id}
        # target_set's payload shape is unchanged: strat_tag rides along only
        # when truthy, exactly as before, so no target_set consumer sees a
        # new field. An explicit clear ("(no strategy)" in the picker) is
        # instead journaled through set_strat(course, star, None), which
        # already emits an explicit-null strat_set -- the star/segment
        # symmetric mechanism set_target_segment also uses (truthy tag ->
        # set_strat_segment(tag), explicit clear -> set_strat_segment(None)).
        if strat_tag is not None:
            payload["strat_tag"] = strat_tag
        await self.publish(Event(type="target_set", frame=0,
                                 timestamp_utc=_now(), payload=payload))
        if strat_tag:
            self._register_strategy(db, entity_key(course_id, star_id), strat_tag)
        elif clear_strat:
            await self.set_strat(course_id, star_id, None)

    async def set_target_segment(self, segment_id: int,
                                 strat_tag: str | None = None,
                                 clear_strat: bool = False) -> None:
        self._require_db()
        if all(d.id != segment_id for d in self._segment_defs):
            raise LookupError(f"segment {segment_id} not found")
        await self.publish(Event(type="target_set", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"kind": "segment",
                                          "segment_id": segment_id}))
        if strat_tag is not None:
            # segment strat memory is written via strat_set (the projector
            # ignores strat_tag inside segment target_set payloads)
            await self.set_strat_segment(segment_id, strat_tag)
        elif clear_strat:
            # mirror of set_target's clear_strat: explicit-null in the
            # picker's "no strategy" card journals a null strat_set rather
            # than leaving the existing strat in place. A defaulted segment
            # (default_strat truthy) can't actually reach "no strategy" --
            # the projector falls back a falsy strat_set to the def's own
            # default (caveat 17) -- so this is only observable on the 10
            # legacy tricks and user-created segments, deliberately.
            await self.set_strat_segment(segment_id, None)

    async def set_strat(self, course_id: int, star_id: int,
                        strat_tag: str | None) -> None:
        """Set a star's active strategy without touching the target."""
        db = self._require_db()
        await self.publish(Event(type="strat_set", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"course_id": course_id,
                                          "star_id": star_id,
                                          "strat_tag": strat_tag}))
        if strat_tag:
            self._register_strategy(db, entity_key(course_id, star_id), strat_tag)
        if self.target == ("star", course_id, star_id):
            # the target's strat changed: keep the WS contract honest so
            # other clients refresh (REFRESH_ON includes target_changed)
            await self.publish(Event(type="target_changed", frame=0,
                                     timestamp_utc=_now(),
                                     payload=self.target_payload()))

    async def set_strat_segment(self, segment_id: int,
                                strat_tag: str | None) -> None:
        """Segment sibling of set_strat — same journaled shape, same
        registration, same target-changed republish. Kept in step with the
        star version deliberately: the practice UI drives both through one
        picker (ui/components/stratpicker.js)."""
        db = self._require_db()
        if all(d.id != segment_id for d in self._segment_defs):
            raise LookupError(f"segment {segment_id} not found")
        await self.publish(Event(type="strat_set", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"kind": "segment",
                                          "segment_id": segment_id,
                                          "strat_tag": strat_tag}))
        if strat_tag:
            self._register_strategy(db, entity_key(None, None, segment_id),
                                    strat_tag)
        if self.target == ("segment", segment_id):
            await self.publish(Event(type="target_changed", frame=0,
                                     timestamp_utc=_now(),
                                     payload=self.target_payload()))

    async def clear_attempt(self, attempt_id: int, reason: str | None = None) -> None:
        """Hide one attempt, and UNDO any PB it saved.

        Hiding is "that run was a mistake", so the save it set is a mistake
        too. A pb row carries its own `frames`, so one left behind does not sit
        there inertly — it keeps GRADING: deleting a PB'd attempt left the star
        reading MARIO 1 off a row hidden from the log (live report 2026-07-29),
        and a cleared row shows no Undo PB button, so the only way back out was
        restore → Undo PB → delete again. `delete_pbs_for_attempts` is the same
        door session wipes and session deletion use, with the same consequence:
        the previous save (latest remaining row) becomes current again, or the
        entity simply has no PB. RESTORING does not bring the save back — the
        row is gone — and Save as PB on the restored row is one click.

        The pb delete runs BEFORE the journal write, for the reason
        set_attempt_strat's retag runs first: it is the half with no self-heal
        path. Failing after the journal write strands exactly the bug above,
        invisibly and unreachably; failing after the delete leaves a visible
        row with its own Save as PB button."""
        db = self._require_db()
        if not any(a.id == attempt_id for a in db.attempts()):
            raise LookupError(f"no attempt {attempt_id}")
        db.delete_pbs_for_attempts([attempt_id])
        await self.publish(Event(type="attempt_cleared", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"attempt_id": attempt_id, "reason": reason}))
        await self._reproject()

    async def restore_attempt(self, attempt_id: int) -> None:
        db = self._require_db()
        if not any(a.id == attempt_id for a in db.attempts()):
            raise LookupError(f"no attempt {attempt_id}")
        await self.publish(Event(type="attempt_restored", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"attempt_id": attempt_id}))
        await self._reproject()

    async def set_attempt_strat(self, attempt_id: int,
                                strat_tag: str | None) -> None:
        """Reclassify ONE recorded attempt's strategy (None = unlabeled).

        A strategy is declared before a run, so it is routinely wrong after
        it. Journal-first like clear/restore: the correction is appended and
        folded in by projection.strat_overrides, never written into the
        derived attempts row. Editing an OLDER row does not touch the live
        per-target strategy memory. Reclassifying the entity's NEWEST
        non-cleared attempt is the exception (user request 2026-07-24): "my
        last run was actually strat X" means X is what's being practiced
        NOW, so the active strategy follows via set_strat/set_strat_segment
        (journaled strat_set, so replay agrees). A None tag never propagates
        — un-labeling the newest row is not a switch. Re-picking the
        previous strategy is the undo.

        Note the asymmetry with set_strat_segment, which raises LookupError
        once a segment definition is deleted: this command deliberately
        checks only that the ATTEMPT exists, not that its segment definition
        is still enabled. In practice that difference never fires, because
        SegmentEngine keeps only enabled defs (segments.py `_defs = [d for d
        in defs if d.enabled]`) and _reproject() rebuilds from the DB-loaded
        defs via replay() + db.replace_attempts() — so deleting OR disabling
        a definition removes ALL of its attempt rows on the very next
        reprojection. A "broken" segment therefore has no attempts left to
        reclassify at all; calling this on one of its old ids hits
        `attempt is None` above and raises LookupError, same as any other
        unknown id. That is why the attempt-row strat dropdown in
        ui/components/practice.js is NOT gated on `sec.broken` the way the
        segment card's own header picker has to be — not because deleted
        history stays editable (it doesn't), but because there is no row
        left to gate. Do not "fix" that non-issue by adding the gate."""
        db = self._require_db()
        attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
        if attempt is None:
            raise LookupError(f"no attempt {attempt_id}")
        await self.publish(Event(type="attempt_strat_set", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"attempt_id": attempt_id,
                                          "strat_tag": strat_tag}))
        # A pbs row snapshots strat_tag at save time and is not derived, so
        # it cannot follow the reprojection on its own. It runs FIRST of the
        # side effects because it is the only one with no self-heal path: a
        # failure between the journal write and this line would strand the PB
        # under the old strategy permanently.
        db.retag_pbs_for_attempt(attempt_id, strat_tag)
        has_entity = (attempt.segment_id is not None
                      or attempt.course_id is not None)
        if strat_tag and has_entity:
            # The name would already resurface via the observed-strats union
            # once the reprojected attempt carries it; registering matters
            # because it ALSO clears the strategy's tombstone — assigning a
            # purged name to a run puts it back in use (the same un-delete
            # rule as re-creating it).
            self._register_strategy(
                db, entity_key(attempt.course_id, attempt.star_id,
                               attempt.segment_id), strat_tag)
        # Newest-attempt exception (docstring above): the active strategy
        # follows a reclassified top-of-log row.
        if (strat_tag and has_entity
                and attempt.id == self._newest_attempt_id(db, attempt)):
            if attempt.segment_id is not None:
                await self.set_strat_segment(attempt.segment_id, strat_tag)
            else:
                await self.set_strat(attempt.course_id, attempt.star_id,
                                     strat_tag)
        await self._reproject()

    @staticmethod
    def _newest_attempt_id(db, attempt) -> int | None:
        """Newest non-cleared attempt of `attempt`'s entity — the top row of
        that section's practice log (cleared rows are hidden from it, so a
        hidden newer row never counts).

        Compares by `journal_id()`, NOT the raw `id` (spec 2026-07-28-multi-
        step-segments, live report): a reattributed 100-coin attempt keeps
        its SEGMENT-namespace id (a huge number, projection.py caveat 2/11),
        which would win a raw `max()` over every native star-namespace
        attempt for the same entity regardless of which actually happened
        last -- silently breaking "reclassifying the newest attempt updates
        the active strategy" for exactly the entities this change created.
        Returns the WINNING ROW's real `id` (still needed by the caller to
        compare against `attempt.id`), just chosen by the chronological key."""
        def same_entity(row):
            if attempt.segment_id is not None:
                return row.segment_id == attempt.segment_id
            return (row.segment_id is None
                    and row.course_id == attempt.course_id
                    and row.star_id == attempt.star_id)
        candidates = [row for row in db.attempts()
                     if same_entity(row) and not row.cleared]
        if not candidates:
            return None
        return max(candidates, key=lambda row: journal_id(row.id)).id

    async def set_time_filter(self, course_id: int, star_id: int,
                              min_frames: int, max_frames: int | None) -> None:
        """Override one star's validity bounds (frames; min 0 = no floor,
        max None = no ceiling) and re-derive history. Mirrors the strategies
        KV RMW; the reproject applies the new bounds retroactively."""
        db = self._require_db()
        if min_frames < 0:
            raise ValueError("min_frames must be >= 0")
        if max_frames is not None and max_frames <= min_frames:
            raise ValueError("max_frames must exceed min_frames")
        filters = db.get_state("time_filters", {})
        filters[f"{course_id}:{star_id}"] = {"min_frames": min_frames,
                                             "max_frames": max_frames}
        db.set_state("time_filters", filters)
        await self._reproject()

    async def clear_time_filter(self, course_id: int, star_id: int) -> None:
        """Drop the star's override (back to the implicit defaults)."""
        db = self._require_db()
        filters = db.get_state("time_filters", {})
        if filters.pop(f"{course_id}:{star_id}", None) is not None:
            db.set_state("time_filters", filters)
        await self._reproject()

    # -- segment definitions ---------------------------------------------------
    async def create_segment(self, d: dict) -> int:
        db = self._require_db()
        validate_definition(d)          # BEFORE insert: invalid defs never land
        sid = db.insert_segment_def(d["name"], d["start_triggers"],
                                    d["end_triggers"], d.get("guards", []),
                                    _iso(_now()),
                                    enabled=d.get("enabled", True),
                                    waypoints=d.get("waypoints", []),
                                    category=d.get("category"),
                                    match_mode=d.get("match_mode", "loose"))
        await self._segments_changed()
        return sid

    async def update_segment(self, segment_id: int, d: dict) -> None:
        db = self._require_db()
        # partial patches (e.g. {"enabled": false}) must validate as the
        # MERGED definition, not in isolation
        current = next((r for r in db.segment_defs()
                        if r["id"] == segment_id), None)
        if current is None:
            raise LookupError(f"segment {segment_id} not found")
        validate_definition({**current, **d})
        db.update_segment_def(segment_id, **{
            k: d[k] for k in ("name", "enabled", "start_triggers",
                              "end_triggers", "waypoints", "guards",
                              "category", "match_mode") if k in d})
        if current.get("seed_key"):
            # a user edit to a seeded row protects it from reconcile's
            # refresh (tracking/defaults.py) until reset_segment clears the
            # flag again; reconcile itself calls db.update_segment_def
            # directly and never reaches this line, so it can't self-flip.
            db.set_seed_dirty("segment_defs", segment_id, 1)
        await self._segments_changed()

    async def delete_segment(self, segment_id: int) -> None:
        db = self._require_db()
        db.delete_segment_def(segment_id)
        await self._segments_changed()

    def _defaults_seed(self) -> dict:
        """The bundled routes/segments seed corpus, for reset-to-default.
        Missing/corrupt seed degrades to an empty corpus (mirrors main.py's
        reconcile_defaults guard) so reset just raises "not a default"
        instead of crashing."""
        seed_path = bundled_defaults_seed()
        if seed_path is None:
            return {"segments": [], "routes": []}
        try:
            return json.loads(seed_path.read_text())
        except (OSError, ValueError):
            return {"segments": [], "routes": []}

    async def reset_segment(self, segment_id: int) -> None:
        """Restore a seeded segment definition to its bundled defaults and
        clear seed_dirty. LookupError (-> 404) for a user-created segment
        (no seed_key) or one whose seed_key has no matching row in the
        current bundled seed (renamed/removed upstream)."""
        db = self._require_db()
        row = next((s for s in db.segment_defs() if s["id"] == segment_id), None)
        if row is None:
            raise LookupError(f"segment {segment_id} not found")
        if not row.get("seed_key"):
            raise LookupError(f"segment {segment_id} is not a default")
        srow = next((s for s in self._defaults_seed().get("segments", [])
                     if s["seed_key"] == row["seed_key"]), None)
        if srow is None:
            raise LookupError(f"no seed for {row['seed_key']}")
        db.update_segment_def(segment_id, name=srow["name"],
            enabled=srow.get("enabled", True),
            start_triggers=srow["start_triggers"],
            end_triggers=srow["end_triggers"],
            waypoints=srow.get("waypoints", []),
            guards=srow.get("guards", []), category=srow.get("category"))
        db.set_seed_dirty("segment_defs", segment_id, 0)
        await self._segments_changed()

    def _insert_definition(self, db: Database, d: dict) -> int:
        """Insert a definition dict produced by `segments.split_definition` /
        `segments.merge_definitions` -- validated exactly like create_segment,
        but through `insert_segment_def` DIRECTLY rather than reusing
        create_segment itself. That is deliberate, not a missed reuse: a
        split/merge dict can carry a real `default_strat` inherited from an
        existing definition (both pure ops' own docstrings document this),
        while create_segment's dict shape is built from SegmentBody, which has
        no `default_strat` field at all (the API deliberately never lets a
        user type one in directly -- it is corpus-authored, applied only by
        `tracking/defaults.py::reconcile_defaults` calling `update_segment_def`
        directly). Routing a split/merge result through create_segment would
        silently drop that field the same way an unreachable SegmentBody field
        would (the exact class of bug test_every_segment_model_field_
        reaches_its_write_path guards on the API's own path) -- so this is a
        second, deliberate bypass of the generic create path, mirroring
        reconcile's own precedent for the identical reason.

        Never passes `seed_key`: neither pure op's output dict carries one
        (by design -- a derived definition is not the row it came from), and
        insert_segment_def's own default is None."""
        validate_definition(d)
        return db.insert_segment_def(
            d["name"], d["start_triggers"], d["end_triggers"], d["guards"],
            _iso(_now()), enabled=d["enabled"], waypoints=d["waypoints"],
            default_strat=d.get("default_strat"),
            match_mode=d.get("match_mode", "loose"))

    async def split_segment(self, segment_id: int, mid: list,
                            names: tuple[str, str]) -> tuple[int, int]:
        """Break an existing definition into two new ones meeting at `mid`
        (`segments.split_definition`) -- **non-destructive**: `segment_id`
        itself is left completely untouched (definitions arm in parallel, so
        the whole and both halves can all record on the same play), and both
        halves are inserted as fresh, user-created rows (`seed_key=None`, so
        `reconcile_defaults` never refreshes or deletes them).

        LookupError (-> 404) for an unknown `segment_id`. The pure op's own
        ValueError -- an unfireable half, or more waypoints than the fold can
        preserve -- surfaces unchanged (-> 409 via the same `_http` path
        every other segment endpoint uses), as does a half that fails
        `validate_definition`; **no input can leave a half-split behind**,
        because both halves are validated in full before either is written
        (see the comment at that call -- this claim was false until
        2026-07-29 and produced a real orphaned row). A db-level failure
        BETWEEN the two inserts is not covered: that needs a transaction the
        storage layer does not expose today."""
        db = self._require_db()
        current = next((d for d in self._segment_defs if d.id == segment_id),
                       None)
        if current is None:
            raise LookupError(f"segment {segment_id} not found")
        first, second = split_definition(current, mid, names)
        # BOTH halves fully validated before EITHER is written. Not redundant
        # with _insert_definition's own validate: that one runs per-half at
        # insert time, and db.insert_segment_def commits unconditionally, so
        # a second half failing validation left the first COMMITTED and
        # orphaned while the caller got a clean 409 (live-reproduced with
        # second_name="   ", which validate_definition rejects as "name is
        # required" -- SegmentSplitBody's names are plain str with no
        # min_length, so any non-UI client could send it). Hoisting covers
        # every validate_definition rule, not just the name that found it.
        # NOT full atomicity: a db-level failure between the two inserts
        # would still half-write, which needs a transaction the storage layer
        # does not currently expose. No INPUT can produce a half-split.
        validate_definition(first)
        validate_definition(second)
        first_id = self._insert_definition(db, first)
        second_id = self._insert_definition(db, second)
        await self._segments_changed()
        return first_id, second_id

    async def merge_segments(self, first_id: int, second_id: int,
                             name: str) -> int:
        """Chain two existing definitions into one meeting at their shared
        boundary, kept as a waypoint (`segments.merge_definitions`) --
        **non-destructive**: both inputs survive completely untouched, and
        the merged definition is inserted as a fresh, user-created row
        (`seed_key=None`).

        LookupError (-> 404) for either unknown id. The pure op's own "do not
        meet" ValueError surfaces unchanged (-> 409) when the pair shares no
        boundary; nothing is inserted in that case."""
        db = self._require_db()
        first = next((d for d in self._segment_defs if d.id == first_id), None)
        if first is None:
            raise LookupError(f"segment {first_id} not found")
        second = next((d for d in self._segment_defs if d.id == second_id),
                      None)
        if second is None:
            raise LookupError(f"segment {second_id} not found")
        merged = merge_definitions(first, second, name)
        new_id = self._insert_definition(db, merged)
        await self._segments_changed()
        return new_id

    async def _segments_changed(self) -> None:
        """Definitions changed retroactively: reload, then re-derive every
        attempt from the journal (mirrors clear/restore)."""
        self._segment_defs = self._load_segment_defs()
        await self._reproject()

    # -- routes ----------------------------------------------------------------
    def _check_segment_refs(self, db: Database, steps: list) -> None:
        """Every segment candidate must reference an existing def (LookupError
        -> 404). Star candidates need no db check."""
        ids = {d["id"] for d in db.segment_defs()}
        for step in steps:
            for c in step["candidates"]:
                if c["type"] == "segment" and c["segment_id"] not in ids:
                    raise LookupError(f"segment {c['segment_id']} not found")

    def _route_member_segments(self, db: Database, route_id: int) -> list[int]:
        """Ordered, de-duplicated segment ids referenced by a route's steps
        (LookupError -> 404 if the route doesn't exist). Snapshotted into
        route_selected so replay never re-reads the mutable routes table."""
        route = next((r for r in db.routes() if r["id"] == route_id), None)
        if route is None:
            raise LookupError(f"route {route_id} not found")
        ids: list[int] = []
        for step in route["steps"]:
            for c in step["candidates"]:
                if c.get("type") == "segment" and c["segment_id"] not in ids:
                    ids.append(c["segment_id"])
        return ids

    async def create_route(self, d: dict) -> int:
        db = self._require_db()
        route_logic.validate_route(d)          # structural, BEFORE insert
        self._check_segment_refs(db, d["steps"])
        rid = db.insert_route(d["name"], d["steps"], _iso(_now()),
                              start_condition=d.get("start_condition"),
                              category=d.get("category"))
        await self._routes_changed()
        return rid

    async def update_route(self, route_id: int, d: dict) -> None:
        db = self._require_db()
        current = next((r for r in db.routes() if r["id"] == route_id), None)
        if current is None:
            raise LookupError(f"route {route_id} not found")
        merged = {**current, **d}              # partial patch validates as whole
        route_logic.validate_route(merged)
        self._check_segment_refs(db, merged["steps"])
        db.update_route(route_id, updated_utc=_iso(_now()),
                        **{k: d[k] for k in ("name", "steps", "start_condition",
                                             "category") if k in d})
        if current.get("seed_key"):
            # same user-edit signal as update_segment; reconcile itself
            # writes via db.update_route directly and never reaches here.
            db.set_seed_dirty("routes", route_id, 1)
        await self._routes_changed()
        if (("steps" in d or "start_condition" in d)
                and self._projector.armed_route_id() == route_id):
            await self._arm_run(route_id, void_active=True)  # re-arm: void any in-flight run, fresh snapshot
        if "steps" in d and self._projector.active_route_id() == route_id:
            await self.select_route(route_id)   # refresh the active route's member snapshot

    async def delete_route(self, route_id: int) -> None:
        db = self._require_db()
        was_active = self._projector.active_route_id() == route_id
        db.delete_route(route_id)
        await self._routes_changed()
        if was_active:
            # The active route no longer exists: clear arming (journals a
            # clearing route_selected {None, []}) so its segments don't stay
            # armed under in_active_route with no route to point back at.
            await self.select_route(None)

    async def reset_route(self, route_id: int) -> None:
        """Segment sibling: restores a seeded route's name/steps/
        start_condition/category from the bundled seed and clears
        seed_dirty. LookupError (-> 404) for a user-created route (no
        seed_key) or one whose seed_key has no matching bundled row. Step
        candidates are re-resolved seed_key -> local segment_id via the
        CURRENT segment_defs table (defaults.py's resolve_steps, the same
        helper reconcile uses), so a segment that was itself reset/re-seeded
        under a different id still binds correctly."""
        db = self._require_db()
        row = next((r for r in db.routes() if r["id"] == route_id), None)
        if row is None:
            raise LookupError(f"route {route_id} not found")
        if not row.get("seed_key"):
            raise LookupError(f"route {route_id} is not a default")
        rrow = next((r for r in self._defaults_seed().get("routes", [])
                     if r["seed_key"] == row["seed_key"]), None)
        if rrow is None:
            raise LookupError(f"no seed for {row['seed_key']}")
        key_to_id = {s["seed_key"]: s["id"] for s in db.segment_defs()
                     if s.get("seed_key")}
        db.update_route(route_id, updated_utc=_iso(_now()), name=rrow["name"],
                        steps=resolve_steps(rrow["steps"], key_to_id),
                        start_condition=rrow.get("start_condition",
                                                 {"type": "reset_game"}),
                        category=rrow.get("category"))
        db.set_seed_dirty("routes", route_id, 0)
        await self._routes_changed()

    async def _routes_changed(self) -> None:
        """Broadcast-only (like segment notices): routes are config, never
        journaled. The UI refetches the route list on this event."""
        await self.broadcaster.publish(Event(type="routes_changed", frame=0,
                                              timestamp_utc=_now(), payload={}))

    async def select_route(self, route_id: int | None) -> None:
        """Journal the active-route arm scope (spec 2026-07-23-default-routes-
        foundation §5.1). Snapshots member segment ids so replay reconstructs
        which route was active at each historical event WITHOUT reading the
        mutable routes table — the same self-containment trick _arm_run uses
        for run_started. None clears the scope: only a standalone segment
        target (MatchContext.target_segment) can still arm in_active_route
        defs. LookupError -> 404 if route_id is given but doesn't exist.

        The active route id itself is NOT stored on the service (no second
        source of truth) — self._projector.active_route_id() derives it from
        the journaled route_selected via feed(), exactly like
        armed_route_id() derives the run-armed route. This is what makes the
        active route restart-safe: replay() rebuilds it from the journal
        before this service ever calls select_route() again."""
        db = self._require_db()
        seg_ids = self._route_member_segments(db, route_id) if route_id is not None else []
        await self.publish(Event(type="route_selected", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"route_id": route_id,
                                          "segment_ids": seg_ids}))

    def active_route(self) -> dict | None:
        """Current active route for the session view ({id, name,
        segment_ids} or None). The id comes from the projector's
        journal-derived active_route_id() — same rule as select_route's
        docstring: no second `_active_route` field on the service, so this
        can never drift from what replay reconstructs. Robust to a route
        deleted out from under the arm: delete_route journals a clearing
        route_selected first (so active_route_id() is already None by the
        time this runs), but the `route is None` guard covers any other
        path that might leave a stale id pointing at nothing."""
        rid = self._projector.active_route_id()
        if rid is None or self.db is None:
            return None
        route = next((r for r in self.db.routes() if r["id"] == rid), None)
        if route is None:
            return None
        return {"id": route["id"], "name": route["name"],
                "segment_ids": self._route_member_segments(self.db, route["id"]),
                # "<course>:<star>" keys, same shape as the view's
                # last_strat_by_star / rank_by_star maps so the UI can test
                # membership with one Set lookup. Drives route-focus filtering:
                # with a route active the star selector offers ONLY its stars
                # (user request 2026-07-24 — practising 16 Star should not
                # present the four Whomp's Fortress stars it never collects).
                "star_keys": _route_member_stars(route)}

    # -- rank standards commands -----------------------------------------------
    async def _rank_standards_changed(self) -> None:
        """Broadcast-only: rank standards are config, never journaled."""
        await self.broadcaster.publish(Event(type="rank_standards_changed",
                                             frame=0, timestamp_utc=_now(), payload={}))

    def _require_ranks(self):
        if self.ranks is None:
            raise RuntimeError("rank standards unavailable")
        return self.ranks

    async def set_rank_threshold(self, ek, strat, rank, seconds) -> None:
        self._require_ranks().set_threshold(ek, strat, rank, seconds)
        await self._rank_standards_changed()

    async def create_rank_strategy(self, ek, strat) -> None:
        self._require_ranks().create_strategy(ek, strat)
        if self.db is not None:
            self._clear_tombstone(self.db, ek, strat)
        await self._rank_standards_changed()

    async def delete_rank_strategy(self, ek, strat) -> None:
        self._require_ranks().delete_strategy(ek, strat)
        await self._rank_standards_changed()

    async def purge_strategy(self, ek: str, strat: str) -> None:
        """Fully delete a CUSTOM strategy: standards data + registration +
        a tombstone hiding attempt-observed occurrences (attempts are
        journal-derived and must not be rewritten), and a journaled
        strat_set null when it was the star's active strat. Seeded
        (community) strats are protected. Re-creating the name clears the
        tombstone — see _clear_tombstone."""
        ranks = self._require_ranks()
        db = self._require_db()
        if strat in ranks.seeded_strategies(ek):
            raise ValueError(f"{strat!r} is a community default and can't be deleted")
        # Same protection, one layer down: a segment definition's own
        # default_strat isn't the user's to delete either. Without this, the
        # tombstone would mask the segment's only pickable strategy while the
        # card still hides its "no strategy" option (spec 2026-07-24).
        if any(d.default_strat == strat for d in self._segment_defs
               if f"segment:{d.id}" == ek):
            raise ValueError(f"{strat!r} is this segment's default strategy "
                             "and can't be deleted")
        ranks.delete_strategy(ek, strat)
        strategies = db.get_state("strategies", {})
        key = _strategies_key(ek)
        if strat in strategies.get(key, []):
            strategies[key] = [s for s in strategies[key] if s != strat]
            db.set_state("strategies", strategies)
        if ek.startswith("star:"):
            _, course_s, star_s = ek.split(":")
            course_id, star_id = int(course_s), int(star_s)
            if self.strat_by_star.get((course_id, star_id)) == strat:
                await self.set_strat(course_id, star_id, None)
        else:
            segment_id = int(ek.split(":")[1])
            # a deleted definition keeps its history (and its standards), so
            # only journal the clear while the segment still exists
            if self.strat_by_segment.get(segment_id) == strat \
                    and any(d.id == segment_id for d in self._segment_defs):
                await self.set_strat_segment(segment_id, None)
        tombs = db.get_state("deleted_strats", {})
        if strat not in tombs.get(ek, []):
            tombs[ek] = tombs.get(ek, []) + [strat]
            db.set_state("deleted_strats", tombs)
        await self._rank_standards_changed()

    async def reset_rank_entity(self, ek) -> None:
        self._require_ranks().reset_entity(ek)
        await self._rank_standards_changed()

    async def set_rank_video(self, ek, strat, rank, url) -> None:
        self._require_ranks().set_video(ek, strat, rank, url)
        await self._rank_standards_changed()

    async def clear_rank_video(self, ek, strat, rank) -> None:
        self._require_ranks().clear_video(ek, strat, rank)
        await self._rank_standards_changed()

    async def set_icon(self, ek: str, icon: str | None) -> None:
        """Set (or clear, icon=None) an entity's selector-icon override —
        the banner cell art the user hand-picked for a star or segment
        (spec 2026-07-24-segment-icon-cells). ui_state KV `icon_overrides`
        maps entity_key -> icon stem; broadcast-only like set_rank_mode (a
        display preference, never journaled). Stem validity is the API
        layer's job (server/api.py owns the bundled icon set)."""
        if self.db is None:
            raise RuntimeError("tracking database unavailable")
        if ek.startswith("segment:"):
            segment_id = int(ek.split(":")[1])
            if all(d.id != segment_id for d in self._segment_defs):
                raise LookupError(f"segment {segment_id} not found")
        overrides = self.db.get_state("icon_overrides", {})
        if icon is None:
            overrides.pop(ek, None)
        else:
            overrides[ek] = icon
        self.db.set_state("icon_overrides", overrides)
        await self.broadcaster.publish(Event(
            type="icons_changed", frame=0, timestamp_utc=_now(),
            payload={"entity": ek, "icon": icon}))

    async def set_segment_origin(self, segment_id: int,
                                 origin: str | None) -> None:
        """Pin (or clear, origin=None) a segment's library category — the
        castle place its start rules are read as beginning in.

        ui_state KV `origin_overrides` maps a segment id (string, as JSON
        object keys are) to a world-node key; broadcast-only like set_icon, a
        display preference that is never journaled. NOT a segment_defs column
        deliberately: a write there flips seed_dirty, which would opt a seeded
        castle movement out of every future corpus refresh just because the
        user fixed its label.
        """
        if self.db is None:
            raise RuntimeError("tracking database unavailable")
        if all(d.id != segment_id for d in self._segment_defs):
            raise LookupError(f"segment {segment_id} not found")
        overrides = self.db.get_state("origin_overrides", {})
        if origin is None:
            overrides.pop(str(segment_id), None)
        else:
            overrides[str(segment_id)] = origin
        self.db.set_state("origin_overrides", overrides)
        await self.broadcaster.publish(Event(
            type="origins_changed", frame=0, timestamp_utc=_now(),
            payload={"segment_id": segment_id, "origin": origin}))
        # An origin stopped being a pure display facet on 2026-07-27: it is
        # now WHERE THE SEGMENT IS PRACTICED, so the projector reads it to
        # decide when a segment target is no longer valid. Correcting one has
        # to reach that decision, and a reprojection is the one path every
        # other definition edit already takes. (This deliberately reverses
        # review I3, which dropped the reprojection here on the grounds that
        # the projector never read the value — true until this change.)
        await self._reproject()

    async def set_rank_mode(self, mode: str) -> None:
        """Persist the global rank-grading mode (average rank mode spec) to
        the ui_state KV and notify. Broadcast-only like the other rank
        commands: a display preference, never journaled."""
        if mode not in RANK_MODES:
            raise ValueError(f"unknown rank mode: {mode!r}")
        if self.db is None:
            raise RuntimeError("tracking database unavailable")
        self.db.set_state("rank_mode", mode)
        await self.broadcaster.publish(Event(type="rank_mode_changed",
                                             frame=0, timestamp_utc=_now(),
                                             payload={"mode": mode}))

    # ---- MARELO: rank exclusions + celebration watermarks -------------------
    def rank_excluded(self) -> set[str]:
        """Entity keys the user has opted out of ranking entirely -- they
        leave the numerator AND denominator of every scope. ui_state KV
        `rank_excluded`, not the standards store: this is user preference,
        and rank_standards.json is community data the bundled-seed reconcile
        overwrites on upgrade, which would silently discard the opt-out."""
        if self.db is None:
            return set()
        return set(self.db.get_state("rank_excluded", []))

    async def set_rank_excluded(self, entity_key: str, excluded: bool) -> None:
        """Broadcast-only like set_rank_mode/set_icon -- a display/scoring
        preference, never journaled."""
        if self.db is None:
            raise RuntimeError("tracking database unavailable")
        current = self.rank_excluded()
        if excluded:
            current.add(entity_key)
        else:
            current.discard(entity_key)
        self.db.set_state("rank_excluded", sorted(current))
        await self.broadcaster.publish(Event(
            type="marelo_changed", frame=0, timestamp_utc=_now(),
            payload={"entity": entity_key, "excluded": excluded}))

    def marelo_watermarks(self) -> dict[str, int]:
        """{scope_id: progression_key} -- the highest rank each scope has
        been SEEN at. `ranks.scopes.celebration_delta` fires a celebration
        only above it."""
        if self.db is None:
            return {}
        return self.db.get_state("marelo_watermarks", {})

    async def ack_celebration(self, scope_id: str, key: int) -> None:
        """Raise a watermark -- but only once the UI has actually SHOWN the
        rank-up, never when the payload is built. Written at build time
        instead, a client that fetches and never renders (a dead tab, a poll
        that beat the popup) would silently swallow the celebration. Guarded
        like sync_watermark/seed_watermark so a stale or repeated ack can
        never move it; broadcasts only on a real raise, so the OTHER open
        client (browser/GUI parity, rule 10) dismisses the same celebration
        instead of showing it a second time."""
        if self.db is None:
            raise RuntimeError("tracking database unavailable")
        watermarks = self.marelo_watermarks()
        if key > watermarks.get(scope_id, -1):
            watermarks[scope_id] = int(key)
            self.db.set_state("marelo_watermarks", watermarks)
            await self.broadcaster.publish(Event(
                type="marelo_changed", frame=0, timestamp_utc=_now(),
                payload={"scope_id": scope_id, "key": key}))

    def sync_watermark(self, scope_id: str, key: int) -> None:
        """Follow a rank DOWN silently so re-climbing celebrates again.
        Never raises (only ack_celebration does, gated on the UI having
        shown it) and never CREATES a watermark (only seed_watermark does)
        -- a scope with no watermark yet has nothing to drop from. Called
        inline while building the /marelo payload, so deliberately sync/
        broadcast-free: the response itself is the notification."""
        if self.db is None:
            return
        watermarks = self.marelo_watermarks()
        if scope_id in watermarks and key < watermarks[scope_id]:
            watermarks[scope_id] = int(key)
            self.db.set_state("marelo_watermarks", watermarks)

    def seed_watermark(self, scope_id: str, key: int) -> None:
        """Record a scope's FIRST observed rank without celebrating it -- a
        first rank is not a rank-UP. Without this, the first view of any
        scope would fire a celebration for every tier ever earned. No-op
        once a watermark already exists for the scope; only ack_celebration
        is allowed to raise one after that."""
        if self.db is None:
            return
        watermarks = self.marelo_watermarks()
        if scope_id not in watermarks:
            watermarks[scope_id] = int(key)
            self.db.set_state("marelo_watermarks", watermarks)

    def absorb_watermark(self, scope_id: str, key: int) -> None:
        """Raise a watermark WITHOUT celebrating -- the rank a scope already
        holds when you arrive at it becomes the new baseline.

        The second and last thing allowed to raise one, and deliberately not
        `ack_celebration`: that one broadcasts, because a raise there means a
        celebration was shown and the other open client must dismiss the same
        one. Nothing was shown here, so there is nothing to dismiss.

        Live report, 2026-07-28: "I just swapped the route around at the top
        over and over again, from a lower rank to a higher rank, and it seems
        to have triggered the middle-of-screen rank up animation. Swapping
        between routes like that should never trigger any rank up." Only
        `ack_celebration` could raise a watermark, so every scope silently
        accumulated a rank-up it had never displayed and discharged it the
        first time the user looked at that scope -- and with 48 routes in the
        library that is a queue of them, one per route. Measured on the live
        db: 3 of 7 watermarked scopes fired on sight, two of them tier
        crossings (the full-screen takeover)."""
        if self.db is None:
            return
        watermarks = self.marelo_watermarks()
        if key > watermarks.get(scope_id, -1):
            watermarks[scope_id] = int(key)
            self.db.set_state("marelo_watermarks", watermarks)

    def note_active_scope(self, scope_id: str) -> bool:
        """Record which scope is active; True if it just CHANGED.

        The one bit that tells "you earned this" from "you navigated here".
        `_active_scope` is derived from the journaled `route_selected`, so
        this is the server's own answer rather than something a client has to
        assert about itself -- and the client already has the mirror of this
        rule for the in-place bar (`identity` in ui/rankclimb.js: switching
        scope re-rates against a different set of entities and legitimately
        produces a higher rank nobody earned)."""
        if self.db is None:
            return False
        if self.db.get_state("marelo_active_scope", None) == scope_id:
            return False
        self.db.set_state("marelo_active_scope", scope_id)
        return True

    # There are NO per-ENTITY celebration watermarks (deleted with task 0012,
    # 2026-07-26). A star's or segment's own rank-up used to be held in an
    # `entity_rank_watermarks` KV until a client rendered and acked it; it is
    # now performed live by the rank banner climbing (ui/rankclimb.js), which
    # is a client-side consequence of the rank simply having changed. That
    # removed a KV read plus a write from EVERY `/api/marelo` request. The
    # SCOPE watermarks above are unchanged -- the full-screen MARELO overlay
    # still needs to be held and acked.

    # -- run lifecycle ---------------------------------------------------------
    async def _arm_run(self, route_id: int, void_active: bool = False) -> None:
        """Build and publish run_started. When void_active=True the payload
        carries the flag so RunTracker discards any in-flight run without
        saving it (used by update_route: edit = current run is invalid)."""
        db = self._require_db()
        route = next((r for r in db.routes() if r["id"] == route_id), None)
        if route is None:
            raise LookupError(f"route {route_id} not found")
        payload = {"route_id": route_id, "route_name": route["name"],
                   "route_steps": route["steps"], "mode": "forgiving",
                   "start_offset_ms": self.run_settings()["start_offset_ms"],
                   "start_condition": route.get("start_condition",
                                                {"type": "reset_game"})}
        if void_active:
            payload["void_active"] = True
        await self.publish(Event(type="run_started", frame=0,
                                 timestamp_utc=_now(), payload=payload))

    async def start_run(self, route_id: int) -> None:
        """Journal run_started (arms run mode). The clock starts at 0 on the
        next game_reset (F1). Validates route exists first (LookupError → 404).
        start_offset_ms comes from run_settings; default 1360 ms models the
        SM64 emulator reset-timing convention."""
        await self._arm_run(route_id, void_active=False)

    async def end_run(self) -> None:
        """Journal run_ended (disarms run mode). If a run is in progress it
        is saved as aborted."""
        self._require_db()
        await self.publish(Event(type="run_ended", frame=0,
                                 timestamp_utc=_now(), payload={}))

    async def pause_run(self) -> None:
        """Journal run_paused — suspends the wall clock and step tracking."""
        self._require_db()
        await self.publish(Event(type="run_paused", frame=0,
                                 timestamp_utc=_now(), payload={}))

    async def resume_run(self) -> None:
        """Journal run_resumed — resumes the wall clock and step tracking."""
        self._require_db()
        await self.publish(Event(type="run_resumed", frame=0,
                                 timestamp_utc=_now(), payload={}))

    async def reset_run(self) -> None:
        """Journal run_reset — aborts the active run; route stays armed so
        the next start-condition event begins a fresh run from step 0."""
        self._require_db()
        await self.publish(Event(type="run_reset", frame=0,
                                 timestamp_utc=_now(), payload={}))

    def run_settings(self) -> dict:
        """Current run settings, defaulting to start_offset_ms=1360."""
        db = self._require_db()
        return db.get_state("run_settings", {"start_offset_ms": 1360})

    async def update_run_settings(self, patch: dict) -> dict:
        """Persist updated run settings. Validates start_offset_ms in 0..600000 ms."""
        db = self._require_db()
        cur = self.run_settings()
        off = patch.get("start_offset_ms", cur["start_offset_ms"])
        if not isinstance(off, int) or off < RUN_OFFSET_MIN or off > RUN_OFFSET_MAX:
            raise ValueError("start_offset_ms must be 0..600000 ms")
        merged = {**cur, "start_offset_ms": off}
        db.set_state("run_settings", merged)
        return merged

    def active_run(self) -> dict | None:
        """Current active run view from the projector, or None."""
        return self._projector.active_run_view()

    def _run_completed_event(self, r, close_event: Event) -> Event:
        """Broadcast-only derived event for a finished or aborted run.
        Must NEVER be journaled — the projector re-derives these from the
        journal (run_started + game_reset + completions) on replay."""
        etype = "run_finished" if r.status == "finished" else "run_aborted"
        return Event(type=etype, frame=close_event.frame,
                     timestamp_utc=close_event.timestamp_utc,
                     payload={"run_id": r.id, "route_id": r.route_id,
                              "status": r.status, "reached_step": r.reached_step,
                              "total_ms": r.total_ms, "is_pb": r.is_pb})

    def export_route(self, route_id: int) -> dict:
        """Self-contained export (sync — read-only). Embeds segment defs."""
        db = self._require_db()
        route = next((r for r in db.routes() if r["id"] == route_id), None)
        if route is None:
            raise LookupError(f"route {route_id} not found")
        defs = {d["id"]: d for d in db.segment_defs()}
        return route_logic.export_route(route["name"], route["steps"], defs,
                                        start_condition=route.get("start_condition"))

    async def import_route(self, payload: dict, dry_run: bool = False) -> dict:
        """Reconcile + (unless dry_run) create missing segments and the route.
        Preview returns the reuse/create summary without writing anything."""
        db = self._require_db()
        resolved = route_logic.resolve_import(payload, db.segment_defs())
        if dry_run:
            return {"name": resolved["name"], "reused": resolved["reused"],
                    "created": resolved["created"], "dry_run": True}
        new_ids = []
        for emb in resolved["to_create"]:
            validate_definition({**emb, "enabled": True})
            new_ids.append(db.insert_segment_def(
                emb["name"], emb["start_triggers"], emb["end_triggers"],
                emb["guards"], _iso(_now()),
                waypoints=emb.get("waypoints", []),
                category=emb.get("category")))
        steps = self._finalize_import_steps(resolved["steps"], new_ids)
        rid = db.insert_route(resolved["name"], steps, _iso(_now()),
                              start_condition=resolved.get("start_condition"))
        await self._routes_changed()
        return {"id": rid, "name": resolved["name"], "reused": resolved["reused"],
                "created": resolved["created"], "dry_run": False}

    @staticmethod
    def _finalize_import_steps(steps: list, new_ids: list) -> list:
        """Rewrite {"create_index": i} segment candidates to real ids."""
        out = []
        for step in steps:
            cands = []
            for c in step["candidates"]:
                if c.get("type") == "segment" and "create_index" in c:
                    cands.append({"type": "segment",
                                  "segment_id": new_ids[c["create_index"]]})
                else:
                    cands.append(c)
            ns = {"need": step["need"], "candidates": cands}
            if step.get("label") is not None:
                ns["label"] = step["label"]
            out.append(ns)
        return out

    async def _reproject(self) -> None:
        db = self._require_db()
        before = self._projector.target
        old_armed = self._projector.armed_segment_ids()
        attempts, projector = replay(db.events(), segments=self._segment_defs,
                                     time_filters=self._time_filters(),
                                     origin_overrides=self._origin_overrides())
        # keep the live session: replayed projector state is authoritative
        self._projector = projector
        db.replace_attempts(attempts)
        # Every attempt the journal still supports has just been written; any
        # pb row pointing outside that set is an orphan, and an orphan keeps
        # GRADING (db.delete_orphaned_pbs). Here rather than in each caller so
        # it repairs rows left behind by deletes and wipes that predate those
        # callers cleaning up — which is what kept a cleared star reading
        # MARIO 1 (live report 2026-07-27).
        db.delete_orphaned_pbs()
        db.replace_runs([r.as_row() for r in projector.finished_runs()])
        self._persisted_runs = [r.id for r in projector.finished_runs()]
        # replay re-derives armed state silently; the UI badge must not lie
        # after a definition edit — broadcast the armed-set diff (broadcast-
        # only, like all notices: never journaled).
        new_armed = projector.armed_segment_ids()
        for sid in sorted(old_armed - new_armed):
            await self.broadcaster.publish(Event(
                type="segment_disarmed", frame=0, timestamp_utc=_now(),
                payload={"segment_id": sid, "name": self._segment_name(sid)}))
        for sid in sorted(new_armed - old_armed):
            await self.broadcaster.publish(Event(
                type="segment_armed", frame=0, timestamp_utc=_now(),
                payload={"segment_id": sid, "name": self._segment_name(sid)}))
        await self.publish(Event(type="attempts_invalidated", frame=0,
                                 timestamp_utc=_now(), payload={}))
        if self._projector.target != before:
            await self.publish(Event(type="target_changed", frame=0,
                                     timestamp_utc=_now(),
                                     payload=self.target_payload()))

    async def save_pb(self, attempt_id: int, timer_mode: str) -> dict:
        db = self._require_db()
        if timer_mode not in ("igt", "rta"):
            raise ValueError(f"bad timer_mode {timer_mode!r}")
        attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
        if attempt is None:
            raise LookupError(f"no attempt {attempt_id}")
        if attempt.outcome != "success" or attempt.cleared:
            raise ValueError(f"attempt {attempt_id} is not a saveable success")
        if attempt.segment_id is not None and timer_mode != "rta":
            raise ValueError("segments are RTA-only")
        frames = attempt.igt_frames if timer_mode == "igt" else attempt.rta_frames
        if frames is None:
            raise ValueError(f"attempt {attempt_id} has no {timer_mode} clock")
        db.insert_pb(course_id=attempt.course_id, star_id=attempt.star_id,
                     strat_tag=attempt.strat_tag, timer_mode=timer_mode,
                     frames=frames, attempt_id=attempt_id, saved_utc=_iso(_now()),
                     segment_id=attempt.segment_id)
        payload = {"course_id": attempt.course_id, "star_id": attempt.star_id,
                   "segment_id": attempt.segment_id,
                   "strat_tag": attempt.strat_tag, "timer_mode": timer_mode,
                   "frames": frames, "attempt_id": attempt_id}
        await self.publish(Event(type="pb_saved", frame=0,
                                 timestamp_utc=_now(), payload=payload))
        return payload

    async def undo_pb(self, attempt_id: int, timer_mode: str) -> dict:
        """Undo the attempt's pb save: delete the row it created, so the
        previous save (if any) is current again — latest-row-wins is the
        pbs contract (views._current_pbs). Only the CURRENT pb's owner may
        undo (ValueError otherwise): a superseded save's row is no longer
        what the pbtag shows, so "undo" would silently delete history the
        user can't see. Like save_pb, the pbs table is mutated directly —
        the journaled pb_undone event is record/broadcast only."""
        db = self._require_db()
        if timer_mode not in ("igt", "rta"):
            raise ValueError(f"bad timer_mode {timer_mode!r}")
        attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
        if attempt is None:
            raise LookupError(f"no attempt {attempt_id}")
        row = db.current_pb(attempt.course_id, attempt.star_id, timer_mode,
                            segment_id=attempt.segment_id)
        if row is None or row["attempt_id"] != attempt_id:
            raise ValueError(
                f"attempt {attempt_id} is not the current {timer_mode} PB")
        db.delete_pb(row["id"])
        restored = db.current_pb(attempt.course_id, attempt.star_id,
                                 timer_mode, segment_id=attempt.segment_id)
        payload = {"course_id": attempt.course_id, "star_id": attempt.star_id,
                   "segment_id": attempt.segment_id,
                   "strat_tag": row["strat_tag"], "timer_mode": timer_mode,
                   "frames": row["frames"], "attempt_id": attempt_id,
                   "restored_frames": restored["frames"] if restored else None,
                   "restored_attempt_id":
                       restored["attempt_id"] if restored else None}
        await self.publish(Event(type="pb_undone", frame=0,
                                 timestamp_utc=_now(), payload=payload))
        return payload

    async def wipe_data(self, kind: str, course_id: int | None = None,
                        star_id: int | None = None,
                        segment_id: int | None = None,
                        scope: str = "session") -> dict:
        """Wipe practice history for one star/segment or everything, scoped
        to the active session (scope="session") or all sessions ("lifetime").

        Star/segment wipes and session-scoped wipes are a journaled
        data_wiped event applied retroactively on replay (see
        projection.wipe_matches) — never journal deletion, because one
        journal event can close BOTH a star and a segment attempt, so
        deleting "this star's events" would corrupt the other kind's
        history. Only the lifetime kind="all" wipe hard-deletes
        (db.wipe_all_history): suppressing-but-keeping every event forever
        contradicts "wipe all data", and whole-journal deletion has no
        attribution problem. PB rows: lifetime star/segment wipes drop the
        key's rows outright; session wipes drop rows saved from the wiped
        attempts, so an earlier session's PB restores via latest-row-wins;
        markers/strategies/definitions survive every wipe (configuration,
        not history)."""
        db = self._require_db()
        if scope not in ("session", "lifetime"):
            raise ValueError(f"bad scope {scope!r}")
        if kind == "star":
            if course_id is None or star_id is None:
                raise ValueError("star wipe needs course_id and star_id")
        elif kind == "segment":
            if segment_id is None:
                raise ValueError("segment wipe needs segment_id")
        elif kind != "all":
            raise ValueError(f"bad kind {kind!r}")
        session_id = self.session_id if scope == "session" else None
        payload = {"kind": kind, "course_id": course_id, "star_id": star_id,
                   "segment_id": segment_id, "session_id": session_id}

        if kind == "all" and session_id is None:
            db.wipe_all_history(keep_session_id=self.session_id)
        elif session_id is None and kind == "star":
            db.delete_pbs_for_star(course_id, star_id)
        elif session_id is None and kind == "segment":
            db.delete_pbs_for_segment(segment_id)
        else:
            wiped_ids = [a.id for a in db.attempts() if wipe_matches(a, payload)]
            db.delete_pbs_for_attempts(wiped_ids)
        await self.publish(Event(type="data_wiped", frame=0,
                                 timestamp_utc=_now(), payload=payload))
        await self._reproject()
        return payload

    async def new_session(self, label: str | None = None) -> int:
        db = self._require_db()
        db.end_session(self.session_id, _iso(_now()))
        self.session_id = db.insert_session(_iso(_now()), label=label)
        await self.publish(Event(type="session_started", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"session_id": self.session_id,
                                          "label": label}))
        return self.session_id

    async def continue_session(self, session_id: int) -> int:
        """Resume an old session: new events land in it from now on."""
        db = self._require_db()
        if not any(s["id"] == session_id for s in db.sessions()):
            raise LookupError(f"no session {session_id}")
        if session_id == self.session_id:
            return session_id
        db.end_session(self.session_id, _iso(_now()))
        db.reopen_session(session_id)   # resumed session is active: no ended_utc
        self.session_id = session_id
        await self.publish(Event(type="session_started", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"session_id": session_id,
                                          "resumed": True}))
        return session_id

    async def delete_session(self, session_id: int) -> None:
        """Hard-delete a past session's data and re-derive everything.
        Caveat: clear/restore command events recorded IN the deleted
        session vanish with it, so attempts they affected in OTHER
        sessions revert on re-projection. attempt_strat_set joins that same
        class, with a twist: db.retag_pbs_for_attempt already wrote the new
        strat_tag into the attempt's saved `pbs` row (not derived, so it
        does not follow reprojection on its own — see set_attempt_strat).
        Deleting the session that held the attempt_strat_set event makes the
        attempt's tag revert on reprojection while its `pbs` row keeps the
        reclassified tag, so the two disagree PERMANENTLY (no further
        reprojection re-syncs them). Out of scope for this merge; the real
        fix is re-deriving pbs tags during _reproject."""
        db = self._require_db()
        if session_id == self.session_id:
            raise ValueError("cannot delete the active session")
        if not any(s["id"] == session_id for s in db.sessions()):
            raise LookupError(f"no session {session_id}")
        # The PBs SAVED from this session's attempts go with it. Without this
        # a pb row outlived the attempt that set it -- db.delete_session's own
        # docstring called a dangling attempt_id "informational only" -- and
        # since a pb row carries its own `frames`, the rank kept grading a
        # time whose entire history had been deleted: an empty practice log
        # under a WALUIGI 4 banner and a live PB tag (live report 2026-07-27,
        # after clearing session data to restart a progression).
        #
        # Collected BEFORE the delete, from the attempts cache, rather than
        # swept up afterwards as orphans: an attempt's id is the journal id of
        # its first event (projection.py), so this is exact, and it reuses
        # `delete_pbs_for_attempts`' existing contract -- the previous pb row
        # for that key restores automatically, which is the user's "I should
        # now be ranked at whatever the next highest star is". With no earlier
        # pb row the star simply has no PB again, and in an averaging rank
        # mode the rank re-derives from the surviving attempts regardless.
        doomed = [a.id for a in db.attempts() if a.session_id == session_id]
        db.delete_session(session_id)
        db.delete_pbs_for_attempts(doomed)
        await self._reproject()
