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
from types import SimpleNamespace
from urllib.parse import urlencode

from sm64_events.core import paths
from sm64_events.core.events import Event
from sm64_events.ghost.source import resolve_ghost

log = logging.getLogger("sm64.ghost")

#: The broadcast-only event every client refreshes on. Published on the
#: Broadcaster, never through `TrackerService.publish`, so there is no journal
#: behind it at all -- this is live UI state, like the segment notices.
EVENT_TYPE = "ghost_changed"

#: How see-through the ghost is the FIRST time it is ever switched on. A
#: shipped preference, so its exact value is his to move and no test pins it --
#: what matters is that a first switch-on is see-through, because a solid
#: overlay is indistinguishable from the emulator having vanished (which is
#: what it looked like on 2026-09-19). A value he has already chosen wins:
#: `_load_persisted` only falls back to this one.
DEFAULT_OPACITY = 0.5
#: HOW THE GHOST IS COLOURED, so it cannot be mistaken for the live picture.
#: His words, 2026-09-19: *"It feels really hard to understand which one is the
#: ACTUAL LIVE GAMEPLAY and which one is the GHOST gameplay... Maybe we can have
#: a color picker... and then apply a color filter to the entire picture? e.g.
#: 'Hue Adjustment: Shift the hue angle by +120 degrees'... The user can disable
#: the color shift if they want."* So: on by default, a third of the way round
#: the colour wheel, and his to move or switch off. A shipped preference like
#: the opacity above, so no test pins these two numbers -- what is pinned is
#: that they are a coherent, reachable choice.
DEFAULT_TINT = {"enabled": True, "hue_degrees": 120}
#: One full turn of the colour wheel. 0 and 360 are the same colour, so the
#: control offers the half-open range and 360 is refused rather than silently
#: meaning "off".
HUE_LIMIT_DEGREES = 359
#: WHAT THE GHOST SHOWS. His words, 2026-09-20: *"let's do tight rectangles on
#: our existing ghosts, let's try it."* The whole recorded screen is what the
#: ghost has always been, and his complaint about it is that two cameras' worth
#: of scenery overlap; "mario" draws only the rectangle Mario is in, in the
#: place the footage has him. Default "whole", because that is the ghost he
#: already knows and most clips have no boxes to draw the other one from.
GHOST_VIEWS = ("whole", "mario")
DEFAULT_VIEW = "whole"
#: How far the ghost may be nudged, in game frames. 600 is twenty seconds at
#: 30 fps -- past the length of most of the stars this is used on, so the limit
#: only ever catches a typo or a stuck key.
NUDGE_LIMIT_FRAMES = 600
#: How often the watch re-reads the world. Slow on purpose: everything it
#: catches is human-paced, and the live clock the overlay actually follows
#: arrives on its own socket.
WATCH_PERIOD_S = 0.5
#: Re-resolving asks the template store and looks for the clip on disk, so it
#: runs every Nth tick rather than every tick -- a target change re-resolves
#: immediately whatever this says.
RESOLVE_EVERY = 8
#: A child that will not stay up is not retried forever; a crash loop spawning
#: a process twice a second is worse than no overlay.
MAX_START_ATTEMPTS = 3

ENABLED_KEY = "ghost_enabled"
OPACITY_KEY = "ghost_opacity"
TINT_ENABLED_KEY = "ghost_tint_enabled"
TINT_HUE_KEY = "ghost_tint_hue"
VIEW_KEY = "ghost_view"

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

#: What a client may send to PUT /api/ghost.
SETTABLE = ("enabled", "opacity", "nudge_frames", "tint", "view")


def template_store(inputs):
    """The template store, resolved PER CALL for the reason the inputs router
    resolves it per request: the database can arrive after boot."""
    if inputs is None:
        raise RuntimeError("captured input is unavailable")
    service = inputs() if callable(inputs) else inputs
    return service.templates


def follower_url(target: dict, port: int | None = None) -> str:
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
    chosen = paths.server_port() if port is None else port
    return f"http://127.0.0.1:{chosen}{PAGE_PATH}?{urlencode(params)}"


def _checked_opacity(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("How see-through the ghost is has to be a number "
                         "between 0 and 1.")
    if not 0.0 <= float(value) <= 1.0:
        raise ValueError("How see-through the ghost is has to be between 0 "
                         "(invisible) and 1 (solid).")
    return float(value)


def _checked_nudge(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("The nudge is a whole number of game frames.")
    if abs(value) > NUDGE_LIMIT_FRAMES:
        raise ValueError(
            f"The ghost can only be nudged up to {NUDGE_LIMIT_FRAMES} frames "
            "(about twenty seconds) either way.")
    return value


def _checked_enabled(value) -> bool:
    if not isinstance(value, bool):
        raise ValueError("The ghost overlay is either on or off.")
    return value


def _checked_tint(value) -> dict:
    """The colour treatment, as a PARTIAL: a client may send either half.

    Sending only the angle must not switch the treatment off and sending only
    the switch must not throw away the angle he chose, so this returns exactly
    the fields it was given and `_apply` merges them over what is already set.
    """
    if not isinstance(value, dict):
        raise ValueError("The ghost's colour is a switch and an angle.")
    unknown = sorted(set(value) - {"enabled", "hue_degrees"})
    if unknown:
        raise ValueError(
            f"The ghost's colour has no setting called {unknown[0]!r}.")
    checked = {}
    if "enabled" in value:
        if not isinstance(value["enabled"], bool):
            raise ValueError("The ghost's colour shift is either on or off.")
        checked["enabled"] = value["enabled"]
    if "hue_degrees" in value:
        hue = value["hue_degrees"]
        if isinstance(hue, bool) or not isinstance(hue, int):
            raise ValueError("The ghost's colour is a whole number of degrees "
                             "around the colour wheel.")
        if not 0 <= hue <= HUE_LIMIT_DEGREES:
            raise ValueError(
                f"The ghost's colour goes from 0 to {HUE_LIMIT_DEGREES} "
                "degrees around the colour wheel.")
        checked["hue_degrees"] = hue
    return checked


def _checked_view(value) -> str:
    """Which of the ghost's views he asked for.

    Asking for "mario" on a ghost whose clip has no boxes is NOT an error: the
    setting is app-wide and he walks between stars, so refusing it would make
    the control jam on the first ghost without boxes. The page falls back to
    the whole picture and says why in `ghostStatus()`; the switch row gets the
    same reason from the source.
    """
    if value not in GHOST_VIEWS:
        raise ValueError("The ghost shows either the whole picture or only "
                         "Mario.")
    return value


def validate_changes(changes: dict) -> dict:
    """What a client asked for, or the one sentence saying why it cannot have
    it. Raises ValueError; nothing is applied until every value passes, so a
    request carrying one bad field changes nothing at all."""
    unknown = sorted(set(changes) - set(SETTABLE))
    if unknown:
        raise ValueError(
            f"The ghost overlay has no setting called {unknown[0]!r}.")
    checked = {}
    if "enabled" in changes:
        checked["enabled"] = _checked_enabled(changes["enabled"])
    if "opacity" in changes:
        checked["opacity"] = _checked_opacity(changes["opacity"])
    if "nudge_frames" in changes:
        checked["nudge_frames"] = _checked_nudge(changes["nudge_frames"])
    if "tint" in changes:
        checked["tint"] = _checked_tint(changes["tint"])
    if "view" in changes:
        checked["view"] = _checked_view(changes["view"])
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

        self._enabled = False
        self._opacity = DEFAULT_OPACITY
        self._tint = dict(DEFAULT_TINT)
        self._view = DEFAULT_VIEW
        self._nudge = 0
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
        return {"enabled": self._enabled,
                "opacity": self._opacity,
                "tint": dict(self._tint),
                "view": self._view,
                "nudge_frames": self._nudge,
                "target": dict(self._target) if self._target else None,
                "source": dict(self._source),
                "window": self._window_state()}

    def _window_state(self) -> dict:
        if self._view == "mario" and self._native is not None:
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
        tick = 0
        while True:
            await asyncio.sleep(self._period_s)
            tick += 1
            try:
                await self.refresh(resolve=tick % RESOLVE_EVERY == 0)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad cycle must not end the watch
                log.exception("ghost session cycle failed")

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
            self._target, self._nudge, self._starts = target, 0, 0
            resolve = True
        self._load_persisted()
        if self._pending:
            resolve = resolve or "view" in self._pending
            self._apply(self._pending)
            self._pending = {}
        # Every cycle, not only the ones that changed it: a feed attached late,
        # or a session whose watch reset the nudge on a target change, converges
        # here rather than waiting for the next press.
        self._tell_clock()
        if resolve:
            self._source = self._resolve_source(target)
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

    def _resolve_source(self, target: dict | None) -> dict:
        video = self._resolve_video_source(target)
        if self._native is not None:
            try:
                self._native_answer = self._native.resolve(target, template_store(self._inputs))
            except Exception:  # noqa: BLE001 - selection failure must revoke the prior target
                log.exception("native ghost source unavailable")
                self._native_answer = {"available": False, "reason": "native_unavailable",
                                       "reason_text": "The template's Mario poses could not be read."}
            selected = self._native_answer if self._view == "mario" else video
            return {**NO_TARGET, **selected,
                    "native_available": self._native_answer.get("available", False),
                    "native_reason_text": self._native_answer.get("reason_text"),
                    "whole_available": video["available"], "whole_reason_text": video["reason_text"]}
        return video

    def _resolve_video_source(self, target: dict | None) -> dict:
        if target is None:
            return dict(NO_TARGET)
        if self._replay is None:
            return dict(NO_RECORDINGS)
        try:
            answer = self._resolve(target["kind"], target["entity_key"],
                                   target["strat"],
                                   templates=template_store(self._inputs),
                                   replay=self._replay)
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

    # -- the preference -------------------------------------------------------

    def _db(self):
        return getattr(self._tracker, "db", None)

    def _load_persisted(self) -> None:
        database = self._db()
        if self._loaded or database is None:
            return
        try:
            stored_enabled = database.get_state(ENABLED_KEY, False)
            stored_opacity = database.get_state(OPACITY_KEY, DEFAULT_OPACITY)
            stored_tint_on = database.get_state(TINT_ENABLED_KEY,
                                                DEFAULT_TINT["enabled"])
            stored_hue = database.get_state(TINT_HUE_KEY,
                                            DEFAULT_TINT["hue_degrees"])
            stored_view = database.get_state(VIEW_KEY, DEFAULT_VIEW)
        except Exception:  # noqa: BLE001 - a preference must never block the app
            log.debug("ghost preference unreadable", exc_info=True)
            return
        self._enabled = bool(stored_enabled)
        # Coerced rather than trusted: a stored value is whatever the last
        # version of this code wrote, and a nonsense one must not make the
        # window unopenable.
        try:
            self._opacity = min(1.0, max(0.0, float(stored_opacity)))
        except (TypeError, ValueError):
            self._opacity = DEFAULT_OPACITY
        try:
            hue = int(stored_hue) % (HUE_LIMIT_DEGREES + 1)
        except (TypeError, ValueError):
            hue = DEFAULT_TINT["hue_degrees"]
        self._tint = {"enabled": bool(stored_tint_on), "hue_degrees": hue}
        # A stored value from a build that knew another view name is not a
        # view: it falls back rather than leaving the page asking for
        # something nothing draws.
        self._view = (stored_view if stored_view in GHOST_VIEWS
                      else DEFAULT_VIEW)
        self._loaded = True

    def _apply(self, changes: dict) -> None:
        database = self._db()
        if "enabled" in changes:
            self._enabled = changes["enabled"]
            self._starts = 0
            _remember(database, ENABLED_KEY, self._enabled)
        if "opacity" in changes:
            self._opacity = changes["opacity"]
            _remember(database, OPACITY_KEY, self._opacity)
        if "tint" in changes:
            # Merged, not replaced: the control sends the half he touched.
            self._tint = {**self._tint, **changes["tint"]}
            _remember(database, TINT_ENABLED_KEY, self._tint["enabled"])
            _remember(database, TINT_HUE_KEY, self._tint["hue_degrees"])
        if "view" in changes:
            self._view = changes["view"]
            _remember(database, VIEW_KEY, self._view)
        if "nudge_frames" in changes:
            # Never persisted: it belongs to the target, and the target is
            # gone by the next session.
            self._nudge = changes["nudge_frames"]

    async def update(self, changes: dict | None = None, **named) -> dict:
        """Apply what a client asked for, or refuse the whole request with the
        one sentence saying why. Takes the request body as a dict (the route's
        door) or as keyword arguments (everyone else's)."""
        asked = dict(changes or {})
        asked.update(named)
        self._pending.update(validate_changes(asked))
        return await self.refresh(resolve=True)

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
            self._clock.set_nudge(self._nudge)
            self._clock.set_tint(self._tint)
            self._clock.set_view(self._view)
        except Exception:  # noqa: BLE001 - the overlay's timing is not worth the app
            log.debug("the ghost clock would not take the nudge", exc_info=True)

    # -- the window -----------------------------------------------------------

    def _reconcile(self) -> None:
        if self._native is not None:
            native_view = self._view == "mario"
            self._native.reconcile(enabled=native_view and self._enabled and self._source.get("available", False),
                                   opacity=self._opacity, nudge=self._nudge,
                                   tint=self._tint, target=self._target)
            if native_view:
                self._end_window()
                return
        controller = self._controller
        if not self._enabled:
            self._end_window()
            return
        if self._target is None or not self._source.get("available"):
            # An ordinary flip: hide, keep the child. Killing it here would
            # make coming back cost a process start and a page load.
            if controller is not None and controller.is_running():
                controller.set_visible(False)
            return
        url = follower_url(self._target, self._port)
        if controller is None:
            controller = self._controller = self._factory(
                url=url, opacity=self._opacity, visible=True,
                on_event=self._child_said)
        if not controller.is_running():
            if self._starts >= MAX_START_ATTEMPTS:
                return
            self._starts += 1
            self._diag("window_starting", url=url, opacity=self._opacity,
                       attempt=self._starts)
            # Set first, start second: a value handed to a stopped controller
            # is carried into the next start (ghost/window/controller.py).
            controller.set_url(url)
            controller.set_opacity(self._opacity)
            controller.set_visible(True)
            controller.start()
            return
        self._starts = 0
        if controller.url != url:
            controller.set_url(url)
        if controller.opacity != self._opacity:
            controller.set_opacity(self._opacity)
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
    if database is None:
        return
    try:
        database.set_state(key, value)
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
