# src/sm64_events/ghost/session.py
"""THE GHOST SESSION: one switch, and everything that follows from it.

The app has exactly one ghost. Its whole state is "is it on", "how see-through
is it", "how far ahead or behind should it run" — and the answer to those three
against the star or segment he is practising RIGHT NOW. This module is the one
place that joins them:

    enabled + the current practice target HAS a ghost  ->  a visible overlay
    anything else                                      ->  no overlay, and a
                                                           sentence saying why

**It watches rather than waits to be asked.** The overlay has to follow what he
is practising whether or not a browser tab is open, and a target change is not
the only thing that moves the answer: marking a template, saving a recording,
the database arriving after a db-less boot and the overlay child dying all
change it too. A slow watch (`WATCH_PERIOD_S`) re-reads the world and reconciles
the window; only a change publishes `ghost_changed`. The alternative — a tap on
the event stream — would catch a target change and miss every other one.

**Nothing here runs on the poll loop.** The live clock reaches the overlay
through `server/ghost_clock.py`, which is publish-and-forget. This module only
ever touches the database and the filesystem from its own task, off the loop
thread, and never from the poller's.

**Two KINDS of thing reach the overlay, and they travel differently on
purpose.** WHICH RUN it is showing is the page, so a target change re-points
the window and the page loads. Everything the RUNNING page can simply take --
how far ahead it runs (the nudge), how its picture is coloured (the tint) and
whether it draws the whole picture or only Mario (the view) -- rides the clock
feed instead, so the page is never navigated for any of them. Doing that the
first way is what made his timing key blink the overlay off and re-seek it on
every press, and a colour he is dragging a slider through would do it fifteen
times a second.

**Hide on a flip, end on OFF.** Walking to the next star hides the window and
keeps the child, so coming back costs nothing. Turning the switch off ends the
child process, because off means off. Stopping the session — server shutdown —
ends it too, and `stop()` does not return until the process is gone.
"""
import asyncio
import logging
import threading
from contextlib import suppress
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from urllib.parse import urlencode

from sm64_events.core import paths
from sm64_events.core.events import Event
from sm64_events.ghost.native_session import ghost_hue
from sm64_events.ghost.pb import GhostLibrary
from sm64_events.ghost.source import resolve_ghost
from sm64_events.ghost.selection import (
    MAX_GHOSTS, MISSING_REASON, SELECTION_KEY, GhostSelection, checked_choice, chosen_ghosts,
)
from sm64_events.ghost import settings
from sm64_events.ghost.settings import (
    DEFAULT_OPACITY, DEFAULT_TINT, DEFAULT_VIEW, GHOST_VIEWS, HUE_LIMIT_DEGREES, NUDGE_LIMIT_FRAMES,
)

log = logging.getLogger("sm64.ghost")

#: The broadcast-only event every client refreshes on. Published on the
#: Broadcaster, never through `TrackerService.publish`, so there is no journal
#: behind it at all -- this is live UI state, like the segment notices.
EVENT_TYPE = "ghost_changed"

#: How often the watch re-reads the world. Slow on purpose: everything it
#: catches is human-paced, and the live clock the overlay actually follows
#: arrives on its own socket.
WATCH_PERIOD_S = 0.5
#: RESOLVING -- which ghosts exist for the target and whether each can play --
#: reads the template store, the saved-clip index and every candidate's
#: sidecar, 86 ms of CPU on his PB (2026-09-23). It used to run every 4 s
#: whatever happened, switch off included. It now runs when something that
#: decides the answer moves: the target, the templates (`TemplateStore.
#: generation`), the saved clips (`ReplayService.saved_generation`), the
#: database, a PB save or undo, a saved-ghost change, and a PUT that changes
#: which ghosts or which view. This interval is the backstop for anything
#: that moves some other way, and it runs only while the switch is on: with
#: it off nothing is drawn, and turning it on resolves at once.
SAFETY_RESOLVE_S = 45.0
#: The PUT fields that change what the resolve answers: the settings rows
#: marked `resolves` (the switch, the view) and the ticked ghost set. Every
#: other setting rides the clock feed or the native command and never needs one.
RESOLVING_CHANGES = frozenset({*settings.resolving(), "ghosts"})
#: A child that will not stay up is not retried forever; a crash loop spawning
#: a process twice a second is worse than no overlay.
MAX_START_ATTEMPTS = 3

PAGE_PATH = "/ui/ghost/index.html"

# The two "no ghost" cases the source resolver cannot speak for, in the same
# shape it answers in. Every one of these is read straight to the player, so it
# says what is missing rather than what failed.
NO_TARGET = {
    "available": False, "reason": "no_target",
    "reason_text": "Pick a star or a segment to practise and the ghost will "
                   "follow it.",
    "template_id": None, "attempt_id": None, "exactness": None,
    "mario_boxes_available": False, "mario_boxes_reason": None,
    "mario_boxes_reason_text": None}
NO_RECORDINGS = {
    "available": False, "reason": "no_recordings",
    "reason_text": "The trainer is not holding recordings this run, so there "
                   "is nothing to play alongside you.",
    "template_id": None, "attempt_id": None, "exactness": None,
    "mario_boxes_available": False, "mario_boxes_reason": None,
    "mario_boxes_reason_text": None}

#: What a client may send to PUT /api/ghost: every registry row, plus the
#: ticked ghost set and the target it is for.
SETTABLE = (*settings.BY_NAME, "ghosts", "target")


def template_store(inputs):
    """The template store, resolved PER CALL for the reason the inputs router
    resolves it per request: the database can arrive after boot."""
    if inputs is None:
        raise RuntimeError("captured input is unavailable")
    service = inputs() if callable(inputs) else inputs
    return service.templates


def follower_url(target: dict, port: int | None = None, *, template_id=None) -> str:
    """The overlay page for this target, on this server's own port.

    `core/paths.py::server_port()` is the one port source -- the overlay is
    served by the very app it is watching, so guessing 8064/8065 here is the
    one thing that could point the window at another instance's page.

    **THE NUDGE IS NOT IN HERE**, and that is the whole point of this URL being
    stable: changing it is how the window is told to LOAD ANOTHER PAGE, so
    anything that moves often must travel some other way. Only the thing he is
    practising belongs here, because that genuinely is another page. The nudge
    rides the clock feed (`server/ghost_clock.py`).
    """
    params = {"kind": target["kind"], "entity_key": target["entity_key"]}
    if target.get("strat"):
        params["strat"] = target["strat"]
    if template_id is not None:
        params["template_id"] = template_id
    chosen = paths.server_port() if port is None else port
    return f"http://127.0.0.1:{chosen}{PAGE_PATH}?{urlencode(params)}"


def validate_changes(changes: dict) -> dict:
    """What a client asked for, or the one sentence saying why it cannot have
    it. Raises ValueError; nothing is applied until every value passes, so a
    request carrying one bad field changes nothing at all."""
    unknown = sorted(set(changes) - set(SETTABLE))
    if unknown:
        raise ValueError(
            f"The ghost overlay has no setting called {unknown[0]!r}.")
    checked = settings.validated(changes)
    if "ghosts" in changes:
        checked["ghosts"] = checked_choice(changes["ghosts"])
        if not isinstance(changes.get("target"), dict):
            raise ValueError("Choose a ghost for the current practice target.")
        checked["target"] = dict(changes["target"])
    elif "target" in changes:
        raise ValueError("The ghost target follows the current practice target.")
    return checked


class GhostSession:
    """The app's one ghost. Built in the composition root, owned by the server
    app's lifespan, read and written through `GET/PUT /api/ghost`."""

    def __init__(self, *, tracker, inputs, replay, broadcaster, clock=None,
                 port: int | None = None, controller_factory=None,
                 resolve=resolve_ghost, period_s: float = WATCH_PERIOD_S, native=None):
        self._tracker = tracker
        self._inputs = inputs
        self._replay = replay
        self._broadcaster = broadcaster
        self._clock = clock
        self._port = port
        self._factory = controller_factory or _default_controller
        self._resolve = resolve
        self._period_s = period_s
        self._native = native
        self._native_answer = {}
        # When the last resolve ran, and what the world it read looked like
        # (`_world`): a change there is itself the event that re-resolves.
        self._resolved_at: float | None = None
        self._world = None
        # PBs whose video save has been asked for and not finished: the only
        # time "still being saved" is true (`video_pending`, wired in main.py).
        self._videos_pending: set[int] = set()
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
        self._controller = None
        self._starts = 0
        self._task: asyncio.Task | None = None
        self._published: dict | None = None
        # The watch, a GET and a PUT can all land at once. One cycle at a time
        # keeps them from reading a half-applied change or starting the window
        # twice.
        self._lock = asyncio.Lock()
        self._lifecycle_lock = threading.RLock()
        self._closing = False

    def attach_clock(self, clock) -> None:
        """Hand the session the clock feed the overlay follows.

        The nudge travels on that feed rather than in the page's URL, so this
        join is what makes his timing key reach the overlay at all. It is made
        at the composition surface (`server/ghost_api.py::mount_ghost_routes`,
        which is handed both), not in `main.py`, so the app factory's one ghost
        call stays the one ghost call.
        """
        self._clock = clock
        self._tell_clock()

    # -- what anyone reads ---------------------------------------------------

    def payload(self) -> dict:
        """The whole frozen shape: what the switch is, what it is pointed at,
        whether there is anything to show, and what the window is doing."""
        return {**{name: settings.copied(value) for name, value in self._values.items()},
                "limits": settings.limits(),
                "target": dict(self._target) if self._target else None,
                "selection": {"ghosts": self._selection.chosen(self._target, self._library_or_none()),
                              "missing": list(self._missing),
                              "playing": self._playing(),
                              "pb": dict(self._pb) if self._pb else None,
                              "candidates": [dict(row) for row in self._candidates]},
                "source": dict(self._source),
                "window": self._window_state()}

    def _playing(self) -> list:
        """Which ticked ghosts Only Mario draws, in order, with the colour each
        is tinted (None when the tint is off) -- the same order and hues the
        native command carries (`native_session.py::ghost_hue`)."""
        if self._values["view"] != "mario":
            return []
        ghosts = [ghost for ghost in self._native_answer.get("ghosts", []) if ghost["available"]]
        return [{"key": "pb" if ghost.get("pb") else ghost["attempt_id"],
                 "attempt_id": ghost["attempt_id"],
                 "hue_degrees": ghost_hue(self._values["tint"]["hue_degrees"], index) if self._values["tint"]["enabled"] else None}
                for index, ghost in enumerate(ghosts)]

    def _window_state(self) -> dict:
        if self._values["view"] == "mario" and self._native is not None:
            return self._native.state()
        controller = self._controller
        running = controller is not None and controller.is_running()
        return {"running": running,
                "visible": bool(running and controller.visible)}

    # -- lifecycle ------------------------------------------------------------

    async def start(self) -> None:
        """Read the world once, then watch it. The first read never
        broadcasts: nothing has changed yet."""
        if self._task is not None:
            return
        self._closing = False
        try:
            await self.refresh(resolve=True, publish=False)
        except Exception:  # noqa: BLE001 - the ghost may never stop the trainer booting
            log.exception("the ghost session could not read its first state")
        self._task = asyncio.create_task(self._watch(), name="ghost-session")

    async def stop(self) -> None:
        """Leave nothing running. Returns once the overlay child is gone."""
        self._closing = True
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        await asyncio.to_thread(self._finish_stop)

    def _finish_stop(self):
        # Cancelling an asyncio waiter does not cancel its to_thread work.
        # Join the in-flight selection before revoking the final command.
        with self._lifecycle_lock:
            self._end_window()
            if self._native is not None:
                self._native.stop()

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
        clips. Each is a counter, so asking costs nothing."""
        database = self._db()
        try:
            store = template_store(self._inputs)
            templates = (id(store), getattr(store, "generation", None))
        except Exception:  # noqa: BLE001 - no input service yet is a world too
            templates = None
        return (id(database) if database is not None else None, templates,
                getattr(self._replay, "saved_generation", None))

    # -- the cycle ------------------------------------------------------------

    async def refresh(self, *, resolve: bool = True, publish: bool = True) -> dict:
        """Re-read the world, reconcile the window, and tell every client if
        anything moved.

        The database read, the clip lookup and the child-process calls all run
        off the event loop: `stop()` waits on a process and `start()` spawns
        one, and the poller shares this loop.
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
                self._active_cycle(target, resolve)

    def _active_cycle(self, target: dict | None, resolve: bool) -> None:
        if target != self._target:
            # His timing offset is about ONE thing he is practising; carrying
            # it onto the next star would put that ghost silently out of step.
            self._target, self._values["nudge_frames"], self._starts = target, 0, 0
            resolve = True
        self._load_persisted()
        if self._pending:
            resolve = resolve or bool(RESOLVING_CHANGES & set(self._pending))
            self._apply(self._pending)
            self._pending = {}
        world = self._world_now()
        resolve = resolve or world != self._world or self._resolved_at is None
        # Every cycle, not only the ones that changed it: a feed attached late,
        # or a session whose watch reset the nudge on a target change, converges
        # here rather than waiting for the next press.
        self._tell_clock()
        if resolve:
            self._source = self._resolve_source(target)
            self._refresh_candidates(target)
            self._world, self._resolved_at = world, monotonic()
        try:
            self._reconcile()
        except Exception:  # noqa: BLE001 - a window that will not open is not a reason to stop practising
            log.exception("the ghost window could not be reconciled")

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
        if not choice["pb"] and not choice["attempts"]:
            return [], (), True
        ghosts, missing = chosen_ghosts(choice, library, target)
        return ghosts, tuple(missing), False

    def selected_ghost(self, target):
        """What a one-recording (whole picture) reader shows for `target`:
        `(first chosen ghost or None, whether a ticked ghost went missing)`.
        None with nothing missing means the library's default."""
        with self._lifecycle_lock:
            self._selection.load(self._db())
            ghosts, missing, _unticked = self._chosen(target)
            return (ghosts[0] if ghosts else None), bool(missing and not ghosts)

    def _resolve_source(self, target: dict | None) -> dict:
        ghosts, self._missing, unticked = self._chosen(target)
        # Whole picture shows ONE recording: the first chosen ghost.
        if target is not None and not ghosts and (self._missing or unticked):
            video = ({**NO_TARGET, "reason": "selected_template_missing", "reason_text": MISSING_REASON}
                     if self._missing else
                     {**NO_TARGET, "reason": "no_ghost_chosen",
                      "reason_text": "Tick a ghost to race for this star or segment."})
        else:
            video = self._resolve_video_source(target, ghosts[0] if ghosts else None)
        if self._native is not None:
            try:
                self._native_answer = self._native.resolve(target, ghosts, self._missing)
            except Exception:  # noqa: BLE001 - selection failure must revoke the prior target
                log.exception("native ghost source unavailable")
                self._native_answer = {"available": False, "reason": "native_unavailable",
                                       "reason_text": "The template's Mario poses could not be read."}
            selected = self._native_answer if self._values["view"] == "mario" else video
            return {**NO_TARGET, **selected,
                    "native_available": self._native_answer.get("available", False),
                    "native_reason_text": self._native_answer.get("reason_text"),
                    "whole_available": video["available"], "whole_reason_text": video["reason_text"]}
        return video

    def _resolve_video_source(self, target: dict | None, ghost=None) -> dict:
        if target is None:
            return dict(NO_TARGET)
        if self._replay is None:
            return dict(NO_RECORDINGS)
        try:
            chosen = {"template": ghost} if ghost is not None else {}
            answer = self._resolve(target["kind"], target["entity_key"],
                                   target["strat"],
                                   templates=self.library(),
                                   replay=self._replay, **chosen)
        except Exception:  # noqa: BLE001 - a db hiccup may not disable the switch silently
            log.debug("ghost source unavailable", exc_info=True)
            return dict(NO_RECORDINGS)
        answer = answer.payload()
        # The I1 answer WITHOUT the keys only the overlay page needs: the
        # practice card has no use for a clip URL, and the view payload and the
        # BOX LIST are both large enough to make a broadcast worth not sending
        # -- one rectangle per picture is hundreds of rows, on every
        # `ghost_changed`. What the card needs from them is one bit and one
        # sentence: can this ghost be drawn as Mario alone, and if not, why.
        answer["mario_boxes_available"] = answer["mario_boxes"] is not None
        return {key: answer[key] for key in NO_TARGET}

    def library(self, inputs=None) -> GhostLibrary:
        """Every ghost path's template store: his current PB first, then the
        active input template (`ghost/pb.py`). `inputs` lets the source route
        pass the service it already resolved for this request."""
        return GhostLibrary(template_store(inputs if inputs is not None else self._inputs), self._db(),
                            attempt_of=getattr(self._replay, "attempt", None))

    def _library_or_none(self):
        try:
            return self.library()
        except RuntimeError:  # no captured-input service yet
            return None

    def video_pending(self, attempt_id: int, pending: bool) -> None:
        """A PB's video save was asked for (True) or has finished (False)."""
        if pending:
            self._videos_pending.add(attempt_id)
        else:
            self._videos_pending.discard(attempt_id)

    def _row_video(self):
        """How the picker's rows learn whether the WHOLE PICTURE can play.

        Only Mario draws poses, so there a row's whole-picture answer decides
        one thing -- whether switching to Whole picture is offered at all --
        and one playable recording settles that. Each answer reads a clip's
        sidecar and checks its picture map, so after the first yes (the
        current source's own counts) the rest are left unasked (`None`);
        switching views resolves again and asks them properly."""
        if self._values["view"] != "mario":
            return self._resolve_video_source
        found = [bool(self._source.get("whole_available"))]

        def video_of(target, ghost):
            if found[0]:
                return {"available": None, "reason_text": None}
            answer = self._resolve_video_source(target, ghost)
            found[0] = bool(answer["available"])
            return answer
        return video_of

    def _refresh_pb(self, target, video_of):
        """What the picker's default option plays: the PB ghost and whether
        each Show mode can draw it. Never a saved choice."""
        library = self.library()
        pb = library.pb_for(target["kind"], target["entity_key"], target["strat"]) if target else None
        if pb is None:
            return None
        video = video_of(target, pb)
        native = self._native.probe(target, pb) if self._native is not None else {
            "available": False, "reason_text": "Mario playback is not available in this window."}
        row = {"attempt_id": pb.attempt_id, "frames": pb.frames, "blinks": native.get("blinks", 0),
               "native_available": bool(native["available"]),
               "native_reason_text": native["reason_text"]}
        if video["available"] is None:
            return {**row, "whole_available": None, "whole_reason_text": None}
        mine = video.get("attempt_id") == pb.attempt_id
        # "Still being saved" only while a save really is running; a PB whose
        # footage was never kept says so instead of promising it.
        waiting = ("This PB's video is still being saved." if pb.attempt_id in self._videos_pending
                   else "There is no saved video for this PB.")
        return {**row, "whole_available": bool(video["available"]) and mine,
                "whole_reason_text": video["reason_text"] if mine else waiting}

    def _refresh_candidates(self, target):
        video_of = self._row_video()
        try:
            self._pb = self._refresh_pb(target, video_of)
        except Exception:  # noqa: BLE001 - the picker must not stop the current ghost
            log.debug("PB ghost unavailable", exc_info=True)
            self._pb = None
        try:
            self._candidates = self._selection.candidates(
                target, self.library(), video_of, self._native,
                variant=(self._values["view"], self._world_now()))
            # The chosen ghosts were resolved just now. Do not let the list's
            # short cache hide a deletion or a newly saved file on them.
            fresh = {ghost["attempt_id"]: ghost for ghost in self._native_answer.get("ghosts", [])}
            for row in self._candidates:
                if row["attempt_id"] in fresh:
                    row["native_available"] = fresh[row["attempt_id"]]["available"]
                    row["native_reason"] = fresh[row["attempt_id"]].get("reason")
                    row["native_reason_text"] = fresh[row["attempt_id"]]["reason_text"]
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
        if saved:
            preserve = getattr(self._replay, "preserve_native", None)
            if preserve is not None:
                try:
                    await asyncio.to_thread(preserve, attempt.id)
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
        for name, checked in changes.items():
            row = settings.BY_NAME.get(name)
            if row is None:
                continue
            self._values[name] = settings.applied(self._values[name], row, checked)
            if row.remembered():
                _remember_setting(database, row, self._values[name])
        if "enabled" in changes:
            self._starts = 0
        if "ghosts" in changes:
            choices = self._selection.choose(self._target, changes["ghosts"])
            _remember(database, SELECTION_KEY, choices)
            self._starts = 0

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
            # change to which ghosts, which view or the switch itself asks the
            # resolve anything new (`RESOLVING_CHANGES`).
            await asyncio.to_thread(self._cycle, target, False)
            body = self.payload()
            changed = body != self._published
            self._published = body
        if changed:
            await self._publish(body)
        return body

    def _queue_update(self, checked, target):
        if "ghosts" in checked:
            if target is None or checked.pop("target") != target:
                raise ValueError("The practice target changed. Choose a ghost for the current target.")
            offered = {ghost.attempt_id for ghost in
                       self.library().ghosts_for(target["kind"], target["entity_key"])}
            if any(number not in offered for number in checked["ghosts"]["attempts"]):
                raise ValueError("That ghost is not saved for this star or segment. Choose another ghost.")
        # Queued changes FOLD the way applied ones merge: two halves of the
        # colour (or of the overlap fade) queued before one cycle both land.
        for name, value in checked.items():
            row = settings.BY_NAME.get(name)
            if row is not None and row.merge and name in self._pending:
                value = {**self._pending[name], **value}
            self._pending[name] = value

    def _tell_clock(self) -> None:
        """Publish the nudge, the colour and the view to whoever is following
        the clock.

        All three travel this way for the same reason: the page is already
        reading this socket every game frame, so a change reaches it without
        the window being re-pointed at a URL -- which is a page LOAD. Best
        effort by design: a feed that cannot take them must not stop the switch
        working.
        """
        if self._clock is None:
            return
        try:
            self._clock.set_nudge(self._values["nudge_frames"])
            self._clock.set_tint(self._values["tint"])
            self._clock.set_view(self._values["view"])
        except Exception:  # noqa: BLE001 - the overlay's timing is not worth the app
            log.debug("the ghost clock would not take the nudge", exc_info=True)

    # -- the window -----------------------------------------------------------

    def _reconcile(self) -> None:
        if self._native is not None:
            native_view = self._values["view"] == "mario"
            self._native.reconcile(enabled=native_view and self._values["enabled"] and self._source.get("available", False),
                                   target=self._target, controls=settings.native_controls(self._values))
            if native_view:
                self._end_window()
                return
        controller = self._controller
        if not self._values["enabled"]:
            self._end_window()
            return
        if self._target is None or not self._source.get("available"):
            # An ordinary flip: hide, keep the child. Killing it here would
            # make coming back cost a process start and a page load.
            if controller is not None and controller.is_running():
                controller.set_visible(False)
            return
        # The follower asks the session which recording plays (`selected_ghost`).
        url = follower_url(self._target, self._port)
        if controller is None:
            controller = self._controller = self._factory(
                url=url, opacity=self._values["opacity"], visible=True,
                on_event=self._child_said)
        if not controller.is_running():
            if self._starts >= MAX_START_ATTEMPTS:
                return
            self._starts += 1
            self._diag("window_starting", url=url, opacity=self._values["opacity"],
                       attempt=self._starts)
            # Set first, start second: a value handed to a stopped controller
            # is carried into the next start (ghost/window/controller.py).
            controller.set_url(url)
            controller.set_opacity(self._values["opacity"])
            controller.set_visible(True)
            controller.start()
            return
        self._starts = 0
        if controller.url != url:
            controller.set_url(url)
        if controller.opacity != self._values["opacity"]:
            controller.set_opacity(self._values["opacity"])
        if not controller.visible:
            controller.set_visible(True)

    def _end_window(self) -> None:
        controller, self._controller = self._controller, None
        self._starts = 0
        if controller is not None:
            self._diag("window_ending")
            controller.stop()

    # -- the diagnostic log ---------------------------------------------------

    def _diag(self, event: str, **fields) -> None:
        """Into `data/ghost_window.jsonl`, beside what the child writes there.

        Imported per call for the same reason `_default_controller` is: nothing
        outside a running overlay should pay for the window package. Best
        effort by design -- a log that can stop the ghost is a log that deletes
        its own evidence.
        """
        try:
            from sm64_events.ghost.window import diag
            diag.record(event, **fields)
        except Exception:  # noqa: BLE001 - never let the record stop the window
            log.debug("the ghost diagnostic log would not take %s", event,
                      exc_info=True)

    def _child_said(self, payload) -> None:
        """Every line the overlay child prints, kept where a failed try is read.

        The child writes its own rectangles, DPI contexts and errors; this adds
        the stream the trainer sees, which is all that survives when the child
        dies before it can write anything itself -- a WebView2 that will not
        start being the case that matters.
        """
        kind = payload.get("event") if isinstance(payload, dict) else None
        self._diag("child_said", level="error" if kind == "error" else "info",
                   said=payload)

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


def _default_controller(**kwargs):
    """Imported here so nothing outside the running app pays for the window
    package, and so a test can replace the whole thing."""
    from sm64_events.ghost.window import GhostWindowController
    return GhostWindowController(**kwargs)


__all__ = ["DEFAULT_OPACITY", "DEFAULT_TINT", "DEFAULT_VIEW", "EVENT_TYPE",
           "GHOST_VIEWS", "HUE_LIMIT_DEGREES", "MAX_START_ATTEMPTS",
           "NUDGE_LIMIT_FRAMES", "SETTABLE", "GhostSession", "follower_url",
           "template_store", "validate_changes"]
