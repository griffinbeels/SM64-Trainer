# src/sm64_events/ghost/session.py
"""Preferences and selected native ghosts for the current practice target.

The watch resolves library and saved poses off the poll loop, sends controls
to the native renderer and broadcasts changed state. Native playback owns each
frame. Shutdown joins in-flight selection before revoking the final command.
"""
import asyncio
import logging
import threading
from contextlib import suppress
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace

from sm64_events.core.events import Event
from sm64_events.ghost.native_session import ghost_hue
from sm64_events.ghost.pb import GhostLibrary
from sm64_events.ghost.selection import (
    MAX_GHOSTS, SELECTION_KEY, GhostSelection, checked_choice, chosen_ghosts,
)
from sm64_events.ghost import settings
from sm64_events.ghost.settings import (
    DEFAULT_OPACITY, DEFAULT_TINT, HUE_LIMIT_DEGREES, NUDGE_LIMIT_FRAMES,
)

log = logging.getLogger("sm64.ghost")

#: The broadcast-only event every client refreshes on. Published on the
#: Broadcaster, never through `TrackerService.publish`, so there is no journal
#: behind it at all -- this is live UI state, like the segment notices.
EVENT_TYPE = "ghost_changed"

#: How often the watch re-reads the world. Slow on purpose: everything it
#: catches is human-paced; the renderer independently owns each game frame.
WATCH_PERIOD_S = 0.5
#: RESOLVING -- which ghosts exist for the target and whether each can play --
#: reads the template store, the saved-clip index and every candidate's
#: sidecar, 86 ms of CPU on his PB (2026-09-23). It used to run every 4 s
#: whatever happened, switch off included. It now runs when something that
#: decides the answer moves: the target, the templates (`TemplateStore.
#: generation`), the saved clips (`ReplayService.saved_generation`), the
#: database, a PB save or undo, a saved-ghost change, and a PUT that changes
#: which ghosts. This interval is the backstop for anything
#: that moves some other way, and it runs only while the switch is on: with
#: it off nothing is drawn, and turning it on resolves at once.
SAFETY_RESOLVE_S = 45.0
#: The PUT fields that change what the resolve answers: the settings rows
#: marked `resolves` (the switch) and the ticked ghost set. Every
#: other setting rides the native command and never needs one.
RESOLVING_CHANGES = frozenset({*settings.resolving(), "ghosts", "rivals"})

# The two "no ghost" cases the source resolver cannot speak for, in the same
# shape it answers in. Every one of these is read straight to the player, so it
# says what is missing rather than what failed.
NO_TARGET = {
    "available": False, "reason": "no_target",
    "reason_text": "Pick a star or a segment to practise and the ghost will "
                   "follow it.",
    "template_id": None, "attempt_id": None, "exactness": None,
    "ghosts": []}
NO_RECORDINGS = {
    "available": False, "reason": "no_recordings",
    "reason_text": "The trainer is not holding recordings this run, so there "
                   "is nothing to play alongside you.",
    "template_id": None, "attempt_id": None, "exactness": None,
    "ghosts": []}

#: What a client may send to PUT /api/ghost: every registry row, plus the
#: ticked ghost set and the target it is for.
SETTABLE = (*settings.BY_NAME, "ghosts", "target", "rivals")


def template_store(inputs):
    """The template store, resolved PER CALL for the reason the inputs router
    resolves it per request: the database can arrive after boot."""
    if inputs is None:
        raise RuntimeError("captured input is unavailable")
    service = inputs() if callable(inputs) else inputs
    return service.templates


def validate_changes(changes: dict) -> dict:
    """What a client asked for, or the one sentence saying why it cannot have
    it. Raises ValueError; nothing is applied until every value passes, so a
    request carrying one bad field changes nothing at all."""
    unknown = sorted(set(changes) - set(SETTABLE))
    if unknown:
        raise ValueError(
            f"The ghost has no setting called {unknown[0]!r}.")
    checked = settings.validated(changes)
    if "ghosts" in changes:
        checked["ghosts"] = checked_choice(changes["ghosts"])
        if not isinstance(changes.get("target"), dict):
            raise ValueError("Choose a ghost for the current practice target.")
        checked["target"] = dict(changes["target"])
    elif "target" in changes and "rivals" not in changes:
        raise ValueError("The ghost target follows the current practice target.")
    if "rivals" in changes:
        if "ghosts" in changes:
            raise ValueError("Choose rivals or a library ghost set in one request.")
        if not isinstance(changes["rivals"],dict) or not isinstance(changes.get("target"),dict):
            raise ValueError("Choose rivals for the current practice target.")
        checked["rivals"] = dict(changes["rivals"])
        checked["target"] = dict(changes["target"])
    return checked


class GhostSession:
    """The app's one ghost. Built in the composition root, owned by the server
    app's lifespan, read and written through `GET/PUT /api/ghost`."""

    def __init__(self, *, tracker, inputs, replay, broadcaster,
                 period_s: float = WATCH_PERIOD_S, native=None, portable=None,
                 population=None, version_of=None, community=None):
        self._tracker = tracker
        self._inputs = inputs
        self._replay = replay
        self._broadcaster = broadcaster
        self._period_s = period_s
        self._native = native
        self._portable = portable
        self._rivals = None
        self._population_recorder = None
        self._population = population
        self._community = community
        self._population_closed = False
        if population is not None and native is not None:
            from sm64_events.ghost.rivals import Rivals
            from sm64_events.ghost.rival_recording import PopulationRecorder
            self._rivals = Rivals(tracker,population,native,version_of or (lambda:None),
                source_of=lambda:id(getattr(getattr(replay,"recorder",None),"ghost_capture",None)), community=community)
            self._population_recorder = PopulationRecorder(population,replay,tracker,self._attempt_identity,community=community)
        self._native_answer = {}
        # When the last resolve ran, and what the world it read looked like
        # (`_world`): a change there is itself the event that re-resolves.
        self._resolved_at: float | None = None
        self._world = None
        self._selection = GhostSelection()
        self._candidates = []
        self._pb = None
        self._missing = ()

        #: Every registry setting's current value (`ghost/settings.py`).
        self._values = settings.defaults()
        self._loaded = False
        self._pending: dict = {}

        self._target: dict | None = None
        self._source: dict = dict(NO_TARGET)
        self._task: asyncio.Task | None = None
        self._published: dict | None = None
        # The watch, a GET and a PUT can all land at once. One cycle at a time
        # keeps them from reading a half-applied change or sending a partial native command.
        self._lock = asyncio.Lock()
        self._lifecycle_lock = threading.RLock()
        self._closing = False

    # -- what anyone reads ---------------------------------------------------

    def payload(self) -> dict:
        """Preferences, chosen ghosts, readiness and native visibility."""
        return {**{name: settings.copied(value) for name, value in self._values.items()},
                "limits": settings.limits(),
                "target": dict(self._target) if self._target else None,
                "selection": {"ghosts": self._selection.chosen(self._target, self._library_or_none()),
                              "missing": list(self._missing),
                              "playing": self._playing(),
                              "pb": dict(self._pb) if self._pb else None,
                              "candidates": [dict(row) for row in self._candidates]},
                "source": dict(self._source),
                "window": self._window_state(),
                **({"rivals":self._rivals.payload()} if self._rivals else {})}

    def _playing(self) -> list:
        """Which ticked ghosts Only Mario draws, in order, with the colour each
        is tinted (None when the tint is off) -- the same order and hues the
        native command carries (`native_session.py::ghost_hue`)."""
        ghosts = [ghost for ghost in self._native_answer.get("ghosts", []) if ghost["available"]]
        return [{"key": "pb" if ghost.get("pb") else ghost.get("import_id") or ghost["attempt_id"],
                 "attempt_id": ghost["attempt_id"],
                 "import_id": ghost.get("import_id"), "label": ghost.get("label"),
                 "hue_degrees": ghost_hue(self._values["tint"]["hue_degrees"], index) if self._values["tint"]["enabled"] else None}
                for index, ghost in enumerate(ghosts)]

    def _window_state(self) -> dict:
        if self._native is not None:
            return self._native.state()
        return {"running": False, "visible": False}

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        """Read the world once, then watch it. The first read never
        broadcasts: nothing has changed yet."""
        if self._task is not None:
            return
        self._closing = False
        if self._population_recorder is not None:
            self._population_recorder.start()
        try:
            await self.refresh(resolve=True, publish=False)
        except Exception:  # noqa: BLE001 - the ghost may never stop the trainer booting
            log.exception("the ghost session could not read its first state")
        self._task = asyncio.create_task(self._watch(), name="ghost-session")

    async def stop(self) -> None:
        """Join the watch and revoke native playback."""
        self._closing = True
        if self._population_recorder is not None:
            await self._population_recorder.stop()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if self._community is not None:
            for close in (self._community.stop, self._community.account.close):
                try:
                    await close()
                except Exception:  # noqa: BLE001 -- auth failure text must never reach logs
                    log.warning('Community shutdown did not finish cleanly; continuing native cleanup.')
        await asyncio.to_thread(self._finish_stop)

    def _finish_stop(self):
        # Cancelling an asyncio waiter does not cancel its to_thread work.
        # Join the in-flight selection before revoking the final command.
        with self._lifecycle_lock:
            if self._native is not None:
                self._native.stop()
            if self._population is not None and not self._population_closed:
                self._population.close()
                self._population_closed = True

    async def _watch(self) -> None:
        while True:
            await asyncio.sleep(self._period_s)
            try:
                await self.refresh(resolve=self._safety_resolve_due())
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad cycle must not end the watch
                log.exception("ghost session cycle failed")

    def _safety_resolve_due(self) -> bool:
        """The backstop resolve (`SAFETY_RESOLVE_S`), only while drawing."""
        return (self._values["enabled"] and self._resolved_at is not None
                and monotonic() - self._resolved_at >= SAFETY_RESOLVE_S)

    def _world_now(self):
        """Everything a resolve reads that can change without an event
        reaching this session: which database, which templates, which saved
        clips and imported collection. This runs on the session worker,
        separately from native frame playback."""
        database = self._db()
        try:
            store = template_store(self._inputs)
            templates = (id(store), getattr(store, "generation", None))
        except Exception:  # noqa: BLE001 - no input service yet is a world too
            templates = None
        return (id(database) if database is not None else None, templates,
                getattr(self._replay, "saved_generation", None), getattr(self._sharing(), "generation", None))

    # -- the cycle ------------------------------------------------------------

    async def refresh(self, *, resolve: bool = True, publish: bool = True) -> dict:
        """Re-read the world, reconcile native playback, and tell every client if
        anything moved.

        Database reads, pose lookup and native commands run off the event
        loop, which the poller shares.
        """
        async with self._lock:
            target = self._read_target()
            await asyncio.to_thread(self._cycle, target, resolve)
            body = self.payload()
            if body == self._published:
                return body
            self._published = body
        if publish:
            await self._publish(body)
        return body

    def _cycle(self, target: dict | None, resolve: bool) -> None:
        with self._lifecycle_lock:
            if not self._closing:
                previous_scope = (self._rivals.scope_for(target) if self._rivals is not None
                                  and "scope" in self._pending.get("rivals",{}) else None)
                try:
                    self._active_cycle(target, resolve)
                except Exception:  # noqa: BLE001 - a refused scope swap leaves its preference unchanged
                    if previous_scope is not None and self._rivals.scope_for(target)!=previous_scope:
                        self._rivals.apply({"scope":previous_scope})
                    raise
                finally:
                    try:
                        self._reconcile()
                    except Exception:  # noqa: BLE001 - renderer failure must not stop practice
                        log.exception("native ghost playback could not be reconciled")

    def _active_cycle(self, target: dict | None, resolve: bool) -> None:
        if target != self._target:
            # His timing offset is about ONE thing he is practising; carrying
            # it onto the next star would put that ghost silently out of step.
            self._target, self._values["nudge_frames"] = target, 0
            resolve = True
        self._load_persisted()
        if self._pending:
            pending, self._pending = self._pending, {}
            resolve = resolve or bool(RESOLVING_CHANGES & set(pending))
            self._apply(pending)
        world = self._world_now()
        resolve = resolve or world != self._world or self._resolved_at is None
        if resolve:
            self._source = self._resolve_source(target)
            if self._rivals is None:
                self._refresh_candidates(target)
            self._world, self._resolved_at = world, monotonic()
        elif self._rivals is not None:
            answer = self._rivals.refresh(target,settings.native_controls(self._values),
                enabled=self._values["enabled"],sharing=self._sharing())
            if answer is not None:
                self._native_answer = answer
                self._source = {**NO_TARGET,**answer}

    def _read_target(self) -> dict | None:
        """The practice target through the tracker's own door, stars and
        segments alike.

        A template is filed under the INPUT layer's key -- `24-1` for a star,
        the bare id for a segment -- and NOT under the `star:24:1` key
        `SegmentDef.parents` speaks. `inputs/service.py::entity_key_of` owns
        that spelling, and it is asked here through the attempt shape it reads
        rather than copied: a second copy would look right and drift the first
        time either format moves.
        """
        from sm64_events.inputs.service import entity_key_of
        tuple_target = self._tracker.target
        if tuple_target is None:
            return None
        if tuple_target[0] == "segment":
            named = SimpleNamespace(segment_id=tuple_target[1],
                                    course_id=None, star_id=None)
        else:
            named = SimpleNamespace(segment_id=None, course_id=tuple_target[1],
                                    star_id=tuple_target[2])
        kind, key = entity_key_of(named)
        return {"kind": kind, "entity_key": key,
                "strat": self._tracker.strat_tag}

    def _chosen(self, target):
        """The ghosts that play here (PB first), ticked attempts no longer
        offered, and whether he unticked everything (then nothing plays, not
        the default)."""
        if target is None:
            return [], (), False
        library = self.library()
        choice = self._selection.chosen(target, library)
        if not choice["pb"] and not choice["attempts"] and not choice.get("imports"):
            return [], (), True
        ghosts, missing = chosen_ghosts(choice, library, target)
        return ghosts, tuple(missing), False


    def _resolve_source(self, target: dict | None) -> dict:
        if self._rivals is not None and not self._rivals.manual:
            answer = self._rivals.refresh(target,settings.native_controls(self._values),
                enabled=self._values["enabled"],sharing=self._sharing(),force=True)
            if answer is not None:
                self._native_answer = answer
            return {**NO_TARGET,**self._native_answer}
        ghosts, self._missing, unticked = self._chosen(target)
        if self._native is None:
            self._native_answer = dict(NO_TARGET if target is None else NO_RECORDINGS)
        else:
            try:
                self._native.set_portable(self._sharing())
                self._native_answer = self._native.resolve(target, ghosts, self._missing)
            except Exception:  # noqa: BLE001 - selection failure must revoke the prior target
                log.exception("native ghost source unavailable")
                self._native_answer = {**NO_RECORDINGS, "reason": "native_unavailable",
                                       "reason_text": "The template's Mario poses could not be read."}
        if target is not None and not ghosts and unticked:
            self._native_answer = {**NO_TARGET, "reason": "no_ghost_chosen",
                                   "reason_text": "Tick a ghost to race for this star or segment."}
        return {**NO_TARGET, **self._native_answer}


    def library(self) -> GhostLibrary:
        """The current PB, saved runs, template attempts and imported ghosts."""
        return GhostLibrary(template_store(self._inputs), self._db(),
                            attempt_of=getattr(self._replay, "attempt", None), portable=self._sharing())

    def _sharing(self):
        if self._portable is not None:
            return self._portable
        if self._replay is None or getattr(self._replay, "cfg", None) is None or self._db() is None:
            return None
        from sm64_events.ghost.sharing import sharing_for
        return sharing_for(self._replay, self._db())

    def _library_or_none(self):
        try:
            return self.library()
        except RuntimeError:  # no captured-input service yet
            return None


    def _refresh_pb(self, target):
        """The current PB and whether its saved poses can play."""
        library = self.library()
        pb = library.pb_for(target["kind"], target["entity_key"], target["strat"]) if target else None
        if pb is None:
            return None
        native = self._native.probe(target, pb) if self._native is not None else {
            "available": False, "reason_text": "Mario playback is not available in this window."}
        return {"attempt_id": pb.attempt_id, "frames": pb.frames, "blinks": native.get("blinks", 0),
                "native_available": bool(native["available"]), "native_reason_text": native["reason_text"]}

    def _refresh_candidates(self, target):
        try:
            self._pb = self._refresh_pb(target)
        except Exception:  # noqa: BLE001 - the picker must not stop the current ghost
            log.debug("PB ghost unavailable", exc_info=True)
            self._pb = None
        try:
            self._candidates = self._selection.candidates(
                target, self.library(), self._native, variant=self._world_now())
            # The chosen ghosts were resolved just now. Do not let the list's
            # short cache hide a deletion or a newly saved file on them.
            fresh = {ghost.get("import_id") or ghost["attempt_id"]: ghost
                     for ghost in self._native_answer.get("ghosts", [])}
            for row in self._candidates:
                identity = row.get("import_id") or row["attempt_id"]
                if identity in fresh:
                    row["native_available"] = fresh[identity]["available"]
                    row["native_reason"] = fresh[identity].get("reason")
                    row["native_reason_text"] = fresh[identity]["reason_text"]
        except Exception:  # noqa: BLE001 - the picker must not stop the current ghost
            log.debug("ghost choices unavailable", exc_info=True)
            self._candidates = []

    def is_saved_ghost(self, attempt_id: int) -> bool:
        from sm64_events.ghost.saved import is_saved
        return is_saved(self._db(), attempt_id)

    async def save_ghost(self, attempt_id: int) -> dict:
        """Save one run as a ghost for its star or segment, keep its Mario
        poses while the recorder still holds them, and tick it so he races it
        next. Returns the preservation outcome and the session."""
        from sm64_events.ghost import saved
        from sm64_events.inputs.service import entity_key_of

        database = self._db()
        if database is None or self._replay is None:
            raise ValueError("Saving a ghost needs the trainer's recordings and database.")
        attempt = await asyncio.to_thread(self._replay.attempt, attempt_id)
        kind, key = entity_key_of(attempt)
        if kind not in ("star", "segment"):
            raise ValueError("Only a run on a star or a segment can be a ghost.")
        kept = await asyncio.to_thread(self._replay.preserve_native, attempt_id)
        target = {"kind": kind, "entity_key": key, "strat": attempt.strat_tag}

        def remember():
            with self._lifecycle_lock:
                saved.save(database, attempt_id, kind, key)
                if self._rivals is not None:
                    self._rivals.remember_saved(target, attempt)
                self._selection.load(database)
                choice = self._selection.chosen(target, self.library())
                if attempt_id not in choice["attempts"] and len(choice["attempts"]) + choice["pb"] < MAX_GHOSTS:
                    choice["attempts"].append(attempt_id)
                    _remember(database, SELECTION_KEY, self._selection.choose(target, choice))
        await asyncio.to_thread(remember)
        body = await self.refresh(resolve=True)
        return {"attempt_id": attempt_id, "saved": True,
                "poses_reason": kept.get("native_ghost_reason"), "session": body}

    async def remove_ghost(self, attempt_id: int) -> dict:
        """Forget a saved ghost and untick it everywhere. A template's run
        stays offered by its template."""
        from sm64_events.ghost import saved

        database = self._db()

        def forget():
            with self._lifecycle_lock:
                if database is None:
                    return
                saved.remove(database, attempt_id)
                if self._rivals is not None:
                    self._rivals.forget_saved(database.attempt(attempt_id))
                self._selection.load(database)
                self._selection.untick(attempt_id)
                _remember(database, SELECTION_KEY, self._selection.stored())
        await asyncio.to_thread(forget)
        body = await self.refresh(resolve=True)
        return {"attempt_id": attempt_id, "saved": False, "session": body}

    async def pb_changed(self, attempt, saved: bool) -> None:
        """A PB was just saved or undone: the ghost follows it NOW, not on
        the watch's next resolve (seconds later).

        His loop, 2026-09-22: *"play runs versus current ghost, beat ghost,
        validate the PB, ghost is automatically generated and ready to go ...
        I want to get back to playing immediately, against my new ghost."*
        A save commits the run's Mario poses first -- only the recorder's
        short archive still holds them, and nothing here waits on the video.
        Nothing else changes: with the target's PB box ticked the PB ghost is
        derived, so the new save (or after an undo, the previous one) is
        already the answer; with it unticked, no PB plays at all.
        """
        if self._rivals is not None:
            target = None  # Disconnect invalidation refreshes the current owner.
            if attempt is not None:
                from sm64_events.inputs.service import entity_key_of
                kind,key = entity_key_of(attempt)
                target = {"kind":kind,"entity_key":key}
            async with self._lock:
                def invalidate():
                    with self._lifecycle_lock:
                        self._rivals.pb_changed(target)
                await asyncio.to_thread(invalidate)
        if saved and attempt is not None:
            preserve = getattr(self._replay, "preserve_native", None)
            if preserve is not None:
                try:
                    kept = await asyncio.to_thread(preserve, attempt.id)
                    if self._rivals is not None and isinstance(kept, dict) and kept.get("native_ghost"):
                        async with self._lock:
                            def committed():
                                with self._lifecycle_lock:
                                    self._rivals.pb_preserved(attempt.id)
                            await asyncio.to_thread(committed)
                except Exception:  # noqa: BLE001 - the PB itself is saved; say why in the picker
                    log.exception("PB %s Mario poses could not be kept", attempt.id)
        await self.refresh(resolve=True)


    # -- the preference -------------------------------------------------------

    def _db(self):
        return getattr(self._tracker, "db", None)

    def _load_persisted(self) -> None:
        database = self._db()
        if self._loaded or database is None:
            return
        try:
            stored = {row.name: row.read(database) for row in settings.ROWS if row.remembered()}
            self._selection.load(database)
        except Exception:  # noqa: BLE001 - a preference must never block the app
            log.debug("ghost preference unreadable", exc_info=True)
            return
        self._values.update(stored)
        self._loaded = True

    def _apply(self, changes: dict) -> None:
        database = self._db()
        if "ghosts" in changes and self._rivals is not None:
            ghosts,_ = chosen_ghosts(changes["ghosts"],self.library(),self._target)
            self._rivals.choose_library(self._target,ghosts,changes["ghosts"]["pb"])
        for name, checked in changes.items():
            row = settings.BY_NAME.get(name)
            if row is None:
                continue
            self._values[name] = settings.applied(self._values[name], row, checked)
            if row.remembered():
                _remember_setting(database, row, self._values[name])
        if "ghosts" in changes:
            choices = self._selection.choose(self._target, changes["ghosts"])
            _remember(database, SELECTION_KEY, choices)
        if "rivals" in changes and self._rivals is not None:
            self._rivals.apply(changes["rivals"])

    async def update(self, changes: dict | None = None, **named) -> dict:
        """Apply what a client asked for, or refuse the whole request with the
        one sentence saying why. Takes the request body as a dict (the route's
        door) or as keyword arguments (everyone else's)."""
        asked = dict(changes or {})
        asked.update(named)
        checked = validate_changes(asked)
        async with self._lock:
            target = self._read_target()
            await asyncio.to_thread(self._queue_update, checked, target)
            # A slider dragged through fifteen values is fifteen PUTs; only a
            # change to which ghosts or the switch itself asks the
            # resolve anything new (`RESOLVING_CHANGES`).
            await asyncio.to_thread(self._cycle, target, False)
            body = self.payload()
            changed = body != self._published
            self._published = body
        if changed:
            await self._publish(body)
        return body

    def _queue_update(self, checked, target):
        if "rivals" in checked:
            if self._rivals is None:
                raise ValueError("Adaptive rivals are unavailable in this window.")
            checked["rivals"] = self._rivals.checked(checked["rivals"],target)
            if checked.pop("target") != target:
                raise ValueError("The practice target changed. Choose rivals for the current target.")
        if "ghosts" in checked:
            if target is None or checked.pop("target") != target:
                raise ValueError("The practice target changed. Choose a ghost for the current target.")
            if self._rivals is not None:
                count = len(checked["ghosts"]["attempts"])+len(checked["ghosts"].get("imports",[]))
                if count>4 or count+int(checked["ghosts"]["pb"])>self._rivals.library_limit(target):
                    raise ValueError("Choose up to four rivals; a historical PB may also need a rival slot.")
            offered = {ghost.attempt_id for ghost in
                       self.library().ghosts_for(target["kind"], target["entity_key"])}
            if any(number not in offered for number in checked["ghosts"]["attempts"]):
                raise ValueError("That ghost is not saved for this star or segment. Choose another ghost.")
            imports = {ghost.import_id for ghost in self.library().imports_for(
                target["kind"], target["entity_key"])}
            if any(identity not in imports for identity in checked["ghosts"].get("imports", [])):
                raise ValueError("That imported ghost is not available for this star or segment.")
            if self._rivals is not None:
                ghosts,_ = chosen_ghosts(checked["ghosts"],self.library(),target)
                self._rivals.check_library(target,ghosts)
        # Queued changes FOLD the way applied ones merge: two halves of the
        # colour (or of the overlap fade) queued before one cycle both land.
        for name, value in checked.items():
            row = settings.BY_NAME.get(name)
            if row is not None and row.merge and name in self._pending:
                value = {**self._pending[name], **value}
            self._pending[name] = value


    # -- native playback ------------------------------------------------------

    def _reconcile(self) -> None:
        if self._native is not None:
            # A pending unavailable roster must not disable the current race.
            available = (self._rivals.identity is not None if self._rivals is not None
                         else self._source.get("available",False))
            self._native.reconcile(enabled=self._values["enabled"] and available,
                                   target=self._rivals.native_target(self._target) if self._rivals is not None else self._target,
                                   controls=settings.native_controls(self._values))

    def attempt_saved(self, attempt):
        if self._population_recorder is not None:
            self._population_recorder.enqueue(attempt)

    def _attempt_identity(self, attempt):
        from sm64_events.inputs.service import entity_key_of
        from sm64_events.ghost.pb import eligible_attempt
        kind,key = entity_key_of(attempt)
        if attempt.game_version not in ("us","jp"):
            return None
        identity = self._rivals.identity_for({"kind":kind,"entity_key":key,"strat":attempt.strat_tag},
                                             version=attempt.game_version)
        return identity if identity is not None and eligible_attempt(attempt,identity) else None

    async def search_rivals(self, target, player, cursor=None, *, scope=None):
        if self._rivals is None:
            raise ValueError("Adaptive rivals are unavailable in this window.")
        async with self._lock:
            identity = self._rivals.identity_for(target)
            if target != self._rivals.target or identity is None or scope is not None and scope != identity.scope:
                raise ValueError('The practice target or ghost view changed. Search in the current view.')
        if (self._community is not None and not isinstance(cursor, list)
                and self._community.account.snapshot().get('status') == 'active'):
            from sm64_events.community.config import CommunityError
            try:
                await self._community.search(identity, player, cursor=cursor)
            except CommunityError:
                pass  # Local/imported ghosts remain searchable while community IO is unavailable.
        async with self._lock:
            return await asyncio.to_thread(self._rivals.search,target,player,cursor,scope=scope)


    # -- the wire -------------------------------------------------------------

    async def _publish(self, body: dict) -> None:
        await self._broadcaster.publish(
            Event(type=EVENT_TYPE, frame=0,
                  timestamp_utc=datetime.now(timezone.utc), payload=body))


def _remember(database, key: str, value) -> None:
    _saving(database, lambda: database.set_state(key, value))


def _remember_setting(database, row, value) -> None:
    """A registry setting, under its own key(s) (`ghost/settings.py`)."""
    _saving(database, lambda: row.write(database, value))


def _saving(database, save) -> None:
    if database is None:
        return
    try:
        save()
    except Exception:  # noqa: BLE001 - failing to remember must not fail the flip
        log.debug("ghost preference could not be saved", exc_info=True)


__all__ = ["DEFAULT_OPACITY", "DEFAULT_TINT", "EVENT_TYPE",
           "HUE_LIMIT_DEGREES",
           "NUDGE_LIMIT_FRAMES", "SETTABLE", "GhostSession",
           "template_store", "validate_changes"]
