# src/sm64_events/tracking/service.py
"""Event pipeline + command surface.

The Poller publishes here (duck-typed broadcaster). Order per event:
broadcast first (liveness is never gated on the db), then journal, then
feed the projector; attempts closed by the event are persisted and an
attempt_completed derived event is emitted through the same pipeline
(the projector ignores derived types, so this cannot recurse).

Commands (set_target, clear/restore, save_pb, new_session) append journal
events through the same path so the journal stays the single source of
truth; clear/restore re-run the full projection because their effect is
retroactive. With db=None the service degrades to broadcast-only.
Tracking failures are isolated inside publish() so the poll loop never
dies (spec §9)."""
import logging
from datetime import datetime, timezone

from sm64_events.core.events import Event
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.storage.db import Database, EventRow
from sm64_events.tracking.projection import Projector, replay

log = logging.getLogger("sm64.tracker")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class TrackerService:
    def __init__(self, db: Database | None, broadcaster):
        self.db = db
        self.broadcaster = broadcaster
        self.session_id: int | None = None
        self._projector = Projector()

    # -- pipeline -------------------------------------------------------------
    async def start(self) -> None:
        if self.db is None:
            log.error("tracker running WITHOUT a database (broadcast-only)")
            return
        events = self.db.events()
        attempts, self._projector = replay(events)
        self.db.replace_attempts(attempts)
        self.session_id = self.db.insert_session(_iso(_now()))
        await self.publish(Event(type="session_started", frame=0,
                                 timestamp_utc=_now(),
                                 payload={"session_id": self.session_id}))

    async def publish(self, event: Event) -> None:
        seq = await self.broadcaster.publish(event)
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
        target_before = self._projector.target
        for attempt in self._projector.feed(row):
            self.db.upsert_attempt(attempt)
            # The derived event's journal row carries the CURRENT session_id,
            # which for a cross-session abandon differs from the attempt's own
            # session_id — the payload's session_id is authoritative.
            await self.publish(self._attempt_completed_event(attempt, event))
        if self._projector.target != target_before:
            await self.publish(Event(
                type="target_changed", frame=event.frame,
                timestamp_utc=event.timestamp_utc,
                payload=self._target_payload()))

    def _attempt_completed_event(self, a, close_event: Event) -> Event:
        return Event(type="attempt_completed", frame=close_event.frame,
                     timestamp_utc=close_event.timestamp_utc, payload={
                         "attempt_id": a.id, "session_id": a.session_id,
                         "course_id": a.course_id, "star_id": a.star_id,
                         "course_name": course_name(a.course_id) if a.course_id is not None else None,
                         "star_name": star_name(a.course_id, a.star_id) if a.course_id is not None else None,
                         "strat_tag": a.strat_tag,
                         "anchor_type": a.anchor_type, "outcome": a.outcome,
                         "outcome_detail": a.outcome_detail,
                         "igt_frames": a.igt_frames,
                         "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
                         "rta_frames": a.rta_frames,
                     })

    def _target_payload(self) -> dict:
        c, s = self._projector.target if self._projector.target else (None, None)
        return {"course_id": c, "star_id": s,
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

    def _require_db(self) -> Database:
        if self.db is None or self.session_id is None:
            raise RuntimeError("database unavailable")
        return self.db

    # -- commands ----------------------------------------------------------------
    async def set_target(self, course_id: int, star_id: int,
                         strat_tag: str | None = None) -> None:
        db = self._require_db()
        payload = {"course_id": course_id, "star_id": star_id}
        if strat_tag is not None:
            payload["strat_tag"] = strat_tag
        await self.publish(Event(type="target_set", frame=0,
                                 timestamp_utc=_now(), payload=payload))
        if strat_tag:
            key = f"{course_id}:{star_id}"
            strategies = db.get_state("strategies", {})
            existing = strategies.get(key, [])
            if strat_tag not in existing:
                strategies[key] = existing + [strat_tag]
                db.set_state("strategies", strategies)

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

    async def _reproject(self) -> None:
        db = self._require_db()
        before = self._projector.target
        attempts, projector = replay(db.events())
        # keep the live session: replayed projector state is authoritative
        self._projector = projector
        db.replace_attempts(attempts)
        await self.publish(Event(type="attempts_invalidated", frame=0,
                                 timestamp_utc=_now(), payload={}))
        if self._projector.target != before:
            await self.publish(Event(type="target_changed", frame=0,
                                     timestamp_utc=_now(),
                                     payload=self._target_payload()))

    async def save_pb(self, attempt_id: int, timer_mode: str) -> dict:
        db = self._require_db()
        if timer_mode not in ("igt", "rta"):
            raise ValueError(f"bad timer_mode {timer_mode!r}")
        attempt = next((a for a in db.attempts() if a.id == attempt_id), None)
        if attempt is None:
            raise LookupError(f"no attempt {attempt_id}")
        if attempt.outcome != "success" or attempt.cleared:
            raise ValueError(f"attempt {attempt_id} is not a saveable success")
        frames = attempt.igt_frames if timer_mode == "igt" else attempt.rta_frames
        if frames is None:
            raise ValueError(f"attempt {attempt_id} has no {timer_mode} clock")
        db.insert_pb(course_id=attempt.course_id, star_id=attempt.star_id,
                     strat_tag=attempt.strat_tag, timer_mode=timer_mode,
                     frames=frames, attempt_id=attempt_id, saved_utc=_iso(_now()))
        payload = {"course_id": attempt.course_id, "star_id": attempt.star_id,
                   "strat_tag": attempt.strat_tag, "timer_mode": timer_mode,
                   "frames": frames, "attempt_id": attempt_id}
        await self.publish(Event(type="pb_saved", frame=0,
                                 timestamp_utc=_now(), payload=payload))
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
