"""The overlay child process: one window, told what to do over stdin.

Three threads, and each one is here for a reason.

- MAIN belongs to the window host. pywebview will not run anywhere else.
- CONTROL reads JSON lines from stdin. When the pipe closes -- which is what
  happens the instant the trainer dies, with no signal and no quit line -- the
  overlay goes with it. That is what keeps a crashed trainer from leaving a
  window on the player's screen.
- FOLLOW asks where Project64 is and moves the overlay onto its picture. It
  is a thread of its own because a user32 query against PJ64 can block for
  110-170 ms at its 1 Hz window lock (replay/video.py), and the window host
  must never wait on that.
- PAGE asks the page whether it has a picture worth showing. A thread of its
  own for a harder reason: pywebview's `evaluate_js` waits on a semaphore
  with NO timeout, so a wedged page holds whoever asked, and a held follower
  is a ghost frozen on his screen. This thread is the one allowed to hang;
  the follower reads its last answer and treats a stale one as a no.
- REORDER pumps a message queue so Windows can tell us the stacking order
  moved -- his click on Project64 raises the emulator over the ghost, and
  waiting a whole tick to put it back is a blink he can see (`reorder.py`).
  It sets the follower's wake event and touches no window itself.

**FOLLOW owns every window call, and that is the fix of 2026-09-19.** DPI
awareness is per thread, so a rectangle read on one thread and applied on
another is a rectangle multiplied by the display scale. The control thread
only records what the player asked for; it does not touch the window. The
follow thread pins its own DPI context (`placer.Placer.adopt_thread`), reads,
places, reads the placement BACK, and hides the overlay rather than leaving an
unproved rectangle over somebody else's app.

The follower is also the ONE place that shows or hides the overlay. A control
line only records what the player wants; the decision that turns that into a
visible window is `geometry.follow_decision`, so "the ghost is off", "PJ64 is
minimized", "PJ64 is not the app he is using" and "PJ64 is gone" cannot be
four different pieces of code that disagree.
"""
import argparse
import logging
import os
import sys
import threading
import time
from dataclasses import asdict, dataclass

from sm64_events.ghost.window import diag, native
from sm64_events.ghost.window.geometry import (
    GameWindow,
    Placement,
    Rect,
    bottom_strip,
    follow_decision,
)
from sm64_events.ghost.window.host import BACKENDS, RAISED, PageAnswer, make_host
from sm64_events.ghost.window.placer import Placer
from sm64_events.ghost.window.protocol import (
    alpha_byte,
    encode_event,
    is_allowed_url,
    parse_control_line,
)
from sm64_events.ghost.window.reorder import ReorderWatch
from sm64_events.ghost.window.visibility import Visibility, visibility_for

log = logging.getLogger("sm64.ghost")

#: How often the overlay re-checks where the game picture is. PJ64 is not
#: moved mid-attempt, so this is about catching a resize, a minimize or an
#: alt-tab promptly, not about tracking a drag frame by frame.
DEFAULT_FOLLOW_PERIOD_S = 0.10

#: How often the page is asked whether it has a picture worth showing. Kept at
#: the follow period so the ghost appears within about two ticks of the
#: footage starting -- a delay he can see is a defect of its own.
PAGE_PERIOD_S = 0.10

#: An answer older than this is no answer. A page whose UI thread has wedged
#: stops answering rather than answering "no", and the ghost has to come off
#: his screen either way. Long enough that a busy decode does not blink it.
PAGE_STALE_S = 1.0

#: One full raw page answer every this many polls, on top of every change. At
#: the period above that is a sample roughly every ten seconds, which is what
#: makes a moving video time visible without writing ten rows a second.
PAGE_SAMPLE_EVERY = 100


@dataclass
class OverlayState:
    """What the player has asked for, as opposed to what is on screen."""

    url: str
    opacity: float
    visible: bool


class ControlPlane:
    """Turns a control line into wanted state. Never touches the window.

    The one thing it does reach the host for is a URL, because loading a page
    is pywebview's own business and carries no coordinates. Everything with a
    rectangle or an alpha in it is left for the follower thread to apply.
    """

    def __init__(self, host, state: OverlayState, emit, wake=None):
        self.host = host
        self.state = state
        self._emit = emit
        self._wake = wake

    def apply_line(self, line: str) -> bool:
        """Apply one line. False means the trainer asked the overlay to end."""
        message = parse_control_line(line)
        if message is None:
            return True
        for reason in message.rejected:
            self._emit({"event": "rejected", "detail": reason})
        applied: dict = {}
        if message.url is not None:
            self.state.url = message.url
            self.host.load_url(message.url)
            applied["url"] = message.url
        if message.opacity is not None:
            self.state.opacity = message.opacity
            applied["opacity"] = message.opacity
        if message.visible is not None:
            self.state.visible = message.visible
            applied["visible"] = message.visible
        if applied:
            self._emit({"event": "applied", **applied})
            if self._wake is not None:
                self._wake.set()
        return not message.quit


def run_control_loop(stream, plane: ControlPlane) -> str:
    """Read control lines until told to quit or the other end goes away."""
    while True:
        line = stream.readline()
        if not line:
            return "parent_gone"
        if not plane.apply_line(line):
            return "quit"


class PageWatch:
    """Asks the page whether it has a picture, on a thread that may hang.

    `host.page_status()` waits on pywebview's semaphore with no timeout, so a
    wedged page holds whichever thread asked. It holds THIS one, which has
    nothing else to do. The follower reads `presentable()`, which never blocks
    and treats an answer that has stopped arriving exactly like a "no" -- so a
    page that dies takes the ghost off his screen rather than freezing it
    there.

    A page that has not defined `ghostStatus`, or answers without the field,
    is NOT presentable. The overlay dims everything under it, so "I do not
    know yet" has to mean "stay off".

    **Every poll is written down**, which it was not in his second try: that
    log carries ONE page row, `said: null`, taken before the window existed,
    and it could not distinguish "never asked" from "raised every time" from
    "answered no". Now each poll records WHICH KIND of answer came back, the
    exception text when one was raised, and the page's own status; identical
    consecutive rows collapse into one carrying a count, and a steady run
    still drops a full sample every `PAGE_SAMPLE_EVERY` polls so a page whose
    video time is moving leaves proof of it.
    """

    def __init__(self, host, *, stale_after_s: float = PAGE_STALE_S,
                 clock=time.monotonic, record=None):
        self._host = host
        self._stale = stale_after_s
        self._clock = clock
        self._record = diag.Collapsing() if record is None else record
        self._lock = threading.Lock()
        self._answer: PageAnswer | None = None
        self._at: float | None = None
        self._signature: tuple | None = None
        self._polls = 0

    def poll(self) -> PageAnswer:
        answer = self._host.page_status()
        with self._lock:
            self._answer, self._at = answer, self._clock()
        self._polls += 1
        signature = (answer.state, answer.presentable, answer.detail)
        fresh = signature != self._signature
        self._signature = signature
        # The raw status carries a moving video time, so repeating it every
        # tick would defeat the collapsing. It rides the first row of each run
        # -- never dropping a kind -- and one periodic sample after that.
        sample = fresh or self._polls % PAGE_SAMPLE_EVERY == 0
        self._record("page_poll", level="error" if answer.state == RAISED else "info",
                     state=answer.state, presentable=answer.presentable,
                     detail=answer.detail,
                     said=answer.status if sample else None)
        return answer

    def presentable(self) -> bool:
        with self._lock:
            answer, at = self._answer, self._at
        if at is None or self._clock() - at > self._stale:
            return False
        return bool(answer is not None and answer.presentable)

    def run(self, stop: threading.Event, period_s: float) -> None:
        while not stop.is_set():
            try:
                self.poll()
            except Exception as error:  # noqa: BLE001 - the ghost outlives one bad answer
                log.exception("ghost overlay page status failed")
                self._record("page_status_failed", level="error",
                             detail=f"{type(error).__name__}: {error}")
            stop.wait(period_s)


class FollowLoop:
    """Keeps the overlay on the game picture, and applies only what changed.

    Two callbacks, and the order between them is the fix of 2026-09-19.
    `prepare` runs EVERY tick and answers whether the window is ours yet --
    styled, faded and confirmed by readback. `apply` runs only when the
    decision has changed, and only after prepare said yes.

    **A decision is remembered as done only when it was actually done.** The
    old loop stored the placement before applying it, so a tick that found no
    window handle yet still counted, and nothing ran again until the decision
    CHANGED. In his second try that left the window unstyled for 4.1 seconds
    and the opacity unset for nineteen.
    """

    def __init__(self, observe, apply, wanted, prepare=None, presentable=None):
        self._observe = observe
        self._apply = apply
        self._wanted = wanted
        self._prepare = prepare
        self._presentable = presentable or (lambda: True)
        self._last: Placement | None = None

    def tick(self) -> Placement | None:
        """One look. Returns what was applied, or None if nothing was."""
        placement = follow_decision(self._observe(), wanted_visible=self._wanted(),
                                    presentable=self._presentable())
        if self._prepare is not None and not self._prepare(placement):
            return None
        if placement == self._last:
            return None
        if not self._apply(placement):
            return None
        self._last = placement
        return placement

    def run(self, stop: threading.Event, period_s: float,
            wake: threading.Event | None = None) -> None:
        while not stop.is_set():
            try:
                self.tick()
            except Exception as error:  # noqa: BLE001 - a dead follower freezes the ghost on screen
                log.exception("ghost overlay follow tick failed")
                diag.record("follow_failed", level="error",
                            detail=f"{type(error).__name__}: {error}")
            if wake is None:
                stop.wait(period_s)
            else:
                wake.wait(period_s)
                wake.clear()


class Project64Watcher:
    """Where the emulator window is, re-found whenever it goes away."""

    def __init__(self, title_substring: str):
        self._title = title_substring
        self.hwnd = 0

    def observe(self) -> GameWindow | None:
        if not native.is_window(self.hwnd):
            self.hwnd = native.find_game_window(self._title) or 0
        if not self.hwnd:
            return None
        return native.read_game_window(self.hwnd)


def parse_rect(text: str) -> Rect:
    parts = (text or "").split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("expected x,y,width,height")
    try:
        x, y, width, height = (int(part) for part in parts)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected four whole numbers") from error
    return Rect(x=x, y=y, width=width, height=height)


def checked_url(text: str) -> str:
    if not is_allowed_url(text):
        raise argparse.ArgumentTypeError("url must be http, https or file")
    return text


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sm64_events.ghost.window",
        description="Show one page over the Project64 picture, without ever "
                    "taking focus or the mouse.")
    parser.add_argument("--url", required=True, type=checked_url,
                        help="the page the overlay shows")
    parser.add_argument("--opacity", type=float, default=0.5,
                        help="0 (invisible) to 1 (solid)")
    parser.add_argument("--visible", action="store_true",
                        help="start with the ghost on screen")
    parser.add_argument("--backend", choices=sorted(BACKENDS), default="webview",
                        help="'none' runs everything but the window")
    parser.add_argument("--title", default="Project64",
                        help="substring identifying the emulator window")
    parser.add_argument("--follow-period", type=float,
                        default=DEFAULT_FOLLOW_PERIOD_S,
                        help="seconds between looks at the game window")
    parser.add_argument("--fixed-rect", type=parse_rect, default=None,
                        metavar="X,Y,W,H",
                        help="stand in for the emulator at this rectangle "
                             "instead of looking for it")
    parser.add_argument("--anchor-hwnd", type=int, default=0,
                        help="measurement only: the window to sit directly "
                             "above while --fixed-rect stands in for the "
                             "emulator's picture. Read, never modified.")
    parser.add_argument("--no-reorder-watch", action="store_true",
                        help="measurement only: fall back to the follow tick "
                             "for putting the ghost back above the game")
    return parser


def make_emitter(stream, on_broken=None):
    """Event lines to the trainer. A write that fails means it has gone.

    A dead trainer takes this pipe and the control pipe in the same instant, so
    the write that would have said "stopping" is the first thing to fail. It
    used to raise out of the control thread ahead of the line that stops the
    window (2026-09-19: two ghost windows still over his game hours after
    their servers had gone, one logging Errno 22 ten times a second). Now the
    first failure calls `on_broken` once and every later line is dropped.
    """
    lock = threading.Lock()
    broken = threading.Event()

    def emit(payload: dict) -> None:
        if broken.is_set():
            return
        fields = dict(payload)
        line = encode_event(fields.pop("event"), **fields)
        try:
            with lock:
                stream.write(line)
                stream.flush()
        except (OSError, ValueError) as error:  # ValueError: a closed stream
            if broken.is_set():
                return
            broken.set()
            diag.record("trainer_pipe_broken", level="info",
                        detail=f"{type(error).__name__}: {error}")
            if on_broken is not None:
                on_broken()
    return emit


def quiet_a_dead_stdout() -> None:
    """Exit 0, not 120, once the trainer's pipe has gone.

    The interpreter flushes stdout on the way out and turns a failure there
    into exit code 120. Pointing the descriptor at the null device is the
    recipe the Python docs give for a reader that has left.
    """
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError):
            pass


def record_geometry(record, game: GameWindow | None) -> None:
    """What the emulator's window actually looks like, whenever it changes.

    Round 3's instrument: he reported the ghost "almost" overlaid, and nothing
    on disk could say which rectangle it had been given or what was inside it.
    So every number the picture is derived from is written down -- the client
    area, every child window with its class and rectangle, the picture that
    came out and which child was subtracted to get it. `diag.Collapsing` means
    a steady window costs one row, not ten a second.
    """
    if game is None or not game.found or game.client is None:
        record("game_geometry", level="info", found=bool(game and game.found),
               client=None, children=[], picture=None, why="no_client")
        return
    strip = bottom_strip(game.client, game.children)
    record("game_geometry", level="info", client=game.client,
           children=[{"class": child.cls, "rect": child.rect,
                      "visible": child.visible} for child in game.children],
           picture=game.picture, foreground=game.foreground,
           why=f"client_minus_{strip.cls}" if strip else "whole_client")


def build_follower(args, *, watcher, host, placer, state, emit, page) -> FollowLoop:
    """The follow loop wired to this run: what it looks at, what it moves.

    `--fixed-rect` stands in for the emulator so the suite never queries
    Project64's window. With no anchor handle the placer leaves the z-order
    alone, which is what makes a test child safe to run while he plays;
    `--anchor-hwnd` is how the rig hands it a stand-in to sit above instead.
    """
    if args.fixed_rect is not None:
        fixed = GameWindow(found=True, minimized=False, client=args.fixed_rect)
        watcher.hwnd = int(args.anchor_hwnd or 0)

        def observe() -> GameWindow:
            return fixed
    else:
        observe = watcher.observe

    geometry_log = diag.Collapsing()

    def look() -> GameWindow | None:
        game = observe()
        record_geometry(geometry_log, game)
        return game

    # The background handover is retried until it takes, so its refusals
    # collapse into one row with a count rather than one row per tick.
    background_log = diag.Collapsing()

    def wanted_for(placement: Placement) -> Visibility:
        """The ONE place the "is it on screen" decision is taken. Pure, so it
        costs nothing to ask twice in a tick."""
        return visibility_for(placement, wanted_alpha=alpha_byte(state.opacity))

    def prepare(placement: Placement) -> bool:
        """Is the window ours yet? Every tick, retrying whatever is not.

        Nothing here is one-shot. A handle that does not exist, a window whose
        layered bit pywebview just removed, and a WebView2 control that has
        not been built yet are all the same thing: ask again next tick.
        """
        if not host.hosts_window:
            return True
        hwnd = host.hwnd()
        if hwnd:
            background = host.use_transparent_page_background()
            if background is not None:
                background_log("page_background", level="info", result=background)
        ready = placer.prepare(hwnd)
        if ready.ok:
            placer.keep_above(hwnd, watcher.hwnd)
        return ready.ok

    def place(placement: Placement) -> bool:
        emit({"event": "placed", "show": placement.show,
              "reason": placement.reason,
              "rect": placement.rect.as_list() if placement.rect else None})
        diag.record("decision", level="info", show=placement.show,
                    reason=placement.reason, rect=placement.rect,
                    game_hwnd=watcher.hwnd)
        placer.apply(host.hwnd(), wanted_for(placement), game_hwnd=watcher.hwnd)
        return True

    return FollowLoop(observe=look, apply=place,
                      wanted=lambda: state.visible, prepare=prepare,
                      presentable=page.presentable)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    # Asked for, never assumed: python.exe's manifest and pywebview's WinForms
    # backend both fix the process's awareness, and either one makes this
    # refuse. The follower thread pins its own context regardless.
    dpi_pinned = native.set_process_per_monitor_aware()
    host = make_host(args.backend)
    state = OverlayState(url=args.url, opacity=args.opacity, visible=args.visible)
    stop = threading.Event()
    wake = threading.Event()

    def end() -> None:
        stop.set()
        wake.set()

    emit = make_emitter(sys.stdout, on_broken=end)
    placer = Placer()
    watcher = Project64Watcher(args.title)
    diag.record("child_start", level="info", backend=host.name, url=state.url,
                opacity=state.opacity, visible=state.visible,
                title=args.title, process_pin_ok=bool(dpi_pinned),
                reorder_watch=not args.no_reorder_watch,
                anchor_hwnd=int(args.anchor_hwnd or 0))

    page = PageWatch(host)
    watch = ReorderWatch(wake)
    follow = build_follower(args, watcher=watcher, host=host, placer=placer,
                            state=state, emit=emit, page=page)
    plane = ControlPlane(host=host, state=state, emit=emit, wake=wake)

    host.open(state.url, args.fixed_rect or Rect(x=0, y=0, width=640, height=480))

    def control() -> None:
        # Ending never waits on being able to SAY so: whatever this thread
        # meets on the way out, the window comes down.
        try:
            reason = run_control_loop(sys.stdin, plane)
            diag.record("stopping", level="info", reason=reason)
            emit({"event": "stopping", "reason": reason})
        finally:
            end()

    def follow_thread() -> None:
        report = placer.adopt_thread(process_pin_ok=dpi_pinned)
        emit({"event": "dpi", **asdict(report)})
        follow.run(stop, args.follow_period, wake)

    threads = [
        threading.Thread(target=control, name="ghost-control", daemon=True),
        threading.Thread(target=follow_thread, name="ghost-follow", daemon=True),
        # Daemon, and never joined with a deadline that matters: this is the
        # thread allowed to hang inside a wedged page's evaluate_js.
        threading.Thread(target=page.run, args=(stop, PAGE_PERIOD_S),
                         name="ghost-page", daemon=True),
    ]
    if not args.no_reorder_watch:
        # Its own thread because it has to pump a message queue, and the
        # follower cannot: it spends its time inside user32 queries against
        # Project64. All it ever does is set the follower's wake event.
        threads.append(threading.Thread(
            target=watch.run, args=(stop,), name="ghost-reorder", daemon=True))
    # Ready goes out BEFORE the follower can report its DPI context or its
    # first placement, so whoever reads this stream sees the child come up in
    # one known order.
    emit({"event": "ready", "backend": host.name, "dpi_pinned": dpi_pinned})
    for thread in threads:
        thread.start()

    failed = None
    try:
        host.run(stop)
    except Exception as error:  # noqa: BLE001 - stderr is discarded; the trainer only sees this stream
        failed = f"{type(error).__name__}: {error}"
        log.exception("ghost overlay window host failed")
        emit({"event": "error", "detail": failed})
        diag.record("host_failed", level="error", detail=failed)
    finally:
        end()
        host.close()
        for thread in threads:
            thread.join(timeout=2)
        quiet_a_dead_stdout()
    return 1 if failed else 0
