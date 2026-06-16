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
import logging
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.projection import Projector, replay, wipe_matches
from sm64_events.tracking.segments import SegmentDef, validate_definition
from sm64_events.tracking import routes as route_logic

log = logging.getLogger("sm64.tracker")

RUN_OFFSET_MIN, RUN_OFFSET_MAX = 0, 600000   # 0..10 min, ms


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class TrackerService:
    def __init__(self, db: Database | None, broadcaster):
        self.db = db
        self.broadcaster = broadcaster
        self.session_id: int | None = None
        self._segment_defs = self._load_segment_defs()
        self._projector = Projector(segments=self._segment_defs)
        self._current_stage = {"course_id": None, "level": None,
                               "area": None, "in_stage": False}
        self._persisted_runs: list[int] = []

    def _load_segment_defs(self) -> list[SegmentDef]:
        # inclusion list (the dataclass's own fields), NOT exclusion of
        # created_utc: a future db column must not TypeError startup
        if self.db is None:
            return []
        keys = [f.name for f in dataclasses.fields(SegmentDef)]
        return [SegmentDef(**{k: row[k] for k in keys})
                for row in self.db.segment_defs()]

    # -- pipeline -------------------------------------------------------------
    async def start(self) -> None:
        if self.db is None:
            log.error("tracker running WITHOUT a database (broadcast-only)")
            return
        events = self.db.events()
        attempts, self._projector = replay(events, segments=self._segment_defs)
        self.db.replace_attempts(attempts)
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
        """The main course the player is standing in (else in_stage=False),
        cached from the broadcast-only stage_changed event for the session
        view's initial load. See detectors/stage.py."""
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

    def _require_db(self) -> Database:
        if self.db is None or self.session_id is None:
            raise RuntimeError("database unavailable")
        return self.db

    # -- commands ----------------------------------------------------------------
    def _register_strategy(self, db: Database, course_id: int, star_id: int,
                           strat_tag: str) -> None:
        """Append strat_tag to the star's registered list (ui_state KV) if new."""
        key = f"{course_id}:{star_id}"
        strategies = db.get_state("strategies", {})
        existing = strategies.get(key, [])
        if strat_tag not in existing:
            strategies[key] = existing + [strat_tag]
            db.set_state("strategies", strategies)

    async def set_target(self, course_id: int, star_id: int,
                         strat_tag: str | None = None) -> None:
        db = self._require_db()
        payload = {"course_id": course_id, "star_id": star_id}
        if strat_tag is not None:
            payload["strat_tag"] = strat_tag
        await self.publish(Event(type="target_set", frame=0,
                                 timestamp_utc=_now(), payload=payload))
        if strat_tag:
            self._register_strategy(db, course_id, star_id, strat_tag)

    async def set_target_segment(self, segment_id: int,
                                 strat_tag: str | None = None) -> None:
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
            await self.publish(Event(type="strat_set", frame=0,
                                     timestamp_utc=_now(),
                                     payload={"kind": "segment",
                                              "segment_id": segment_id,
                                              "strat_tag": strat_tag}))

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
            self._register_strategy(db, course_id, star_id, strat_tag)
        if self.target == ("star", course_id, star_id):
            # the target's strat changed: keep the WS contract honest so
            # other clients refresh (REFRESH_ON includes target_changed)
            await self.publish(Event(type="target_changed", frame=0,
                                     timestamp_utc=_now(),
                                     payload=self.target_payload()))

    async def clear_attempt(self, attempt_id: int, reason: str | None = None) -> None:
        db = self._require_db()
        if not any(a.id == attempt_id for a in db.attempts()):
            raise LookupError(f"no attempt {attempt_id}")
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

    # -- segment definitions ---------------------------------------------------
    async def create_segment(self, d: dict) -> int:
        db = self._require_db()
        validate_definition(d)          # BEFORE insert: invalid defs never land
        sid = db.insert_segment_def(d["name"], d["start_triggers"],
                                    d["end_triggers"], d.get("guards", []),
                                    _iso(_now()),
                                    enabled=d.get("enabled", True))
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
                              "end_triggers", "guards") if k in d})
        await self._segments_changed()

    async def delete_segment(self, segment_id: int) -> None:
        db = self._require_db()
        db.delete_segment_def(segment_id)
        await self._segments_changed()

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

    async def create_route(self, d: dict) -> int:
        db = self._require_db()
        route_logic.validate_route(d)          # structural, BEFORE insert
        self._check_segment_refs(db, d["steps"])
        rid = db.insert_route(d["name"], d["steps"], _iso(_now()),
                              start_condition=d.get("start_condition"))
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
                        **{k: d[k] for k in ("name", "steps", "start_condition") if k in d})
        await self._routes_changed()
        if (("steps" in d or "start_condition" in d)
                and self._projector.armed_route_id() == route_id):
            await self.start_run(route_id)   # re-arm: fresh route_steps/start_condition snapshot

    async def delete_route(self, route_id: int) -> None:
        db = self._require_db()
        db.delete_route(route_id)
        await self._routes_changed()

    async def _routes_changed(self) -> None:
        """Broadcast-only (like segment notices): routes are config, never
        journaled. The UI refetches the route list on this event."""
        await self.broadcaster.publish(Event(type="routes_changed", frame=0,
                                              timestamp_utc=_now(), payload={}))

    # -- run lifecycle ---------------------------------------------------------
    async def start_run(self, route_id: int) -> None:
        """Journal run_started (arms run mode). The clock starts at 0 on the
        next game_reset (F1). Validates route exists first (LookupError → 404).
        start_offset_ms comes from run_settings; default 1360 ms models the
        SM64 emulator reset-timing convention."""
        db = self._require_db()
        route = next((r for r in db.routes() if r["id"] == route_id), None)
        if route is None:
            raise LookupError(f"route {route_id} not found")
        offset = self.run_settings()["start_offset_ms"]
        sc = route.get("start_condition", {"type": "reset_game"})
        await self.publish(Event(type="run_started", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"route_id": route_id,
                                          "route_name": route["name"],
                                          "route_steps": route["steps"],
                                          "mode": "forgiving",
                                          "start_offset_ms": offset,
                                          "start_condition": sc}))

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
                emb["guards"], _iso(_now())))
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
        attempts, projector = replay(db.events(), segments=self._segment_defs)
        # keep the live session: replayed projector state is authoritative
        self._projector = projector
        db.replace_attempts(attempts)
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
        sessions revert on re-projection."""
        db = self._require_db()
        if session_id == self.session_id:
            raise ValueError("cannot delete the active session")
        if not any(s["id"] == session_id for s in db.sessions()):
            raise LookupError(f"no session {session_id}")
        db.delete_session(session_id)
        await self._reproject()
