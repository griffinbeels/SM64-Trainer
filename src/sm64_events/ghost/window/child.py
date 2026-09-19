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
import sys
import threading
import time
from dataclasses import asdict, dataclass

from sm64_events.ghost.window import diag, native
from sm64_events.ghost.window.geometry import (
    GameWindow,
    Placement,
    Rect,
    follow_decision,
)
from sm64_events.ghost.window.host import BACKENDS, make_host
from sm64_events.ghost.window.placer import Placer
from sm64_events.ghost.window.protocol import (
    alpha_byte,
    encode_event,
    is_allowed_url,
    parse_control_line,
)

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
    """

    def __init__(self, host, *, stale_after_s: float = PAGE_STALE_S,
                 clock=time.monotonic, record=diag.record):
        self._host = host
        self._stale = stale_after_s
        self._clock = clock
        self._record = record
        self._lock = threading.Lock()
        self._answer: dict | None = None
        self._at: float | None = None
        self._reported: bool | None = None

    def poll(self) -> dict | None:
        answer = self._host.page_status()
        with self._lock:
            self._answer, self._at = answer, self._clock()
        said = bool(isinstance(answer, dict) and answer.get("presentable"))
        if said != self._reported:
            self._reported = said
            self._record("page_status", level="info", presentable=said,
                         said=answer)
        return answer

    def presentable(self) -> bool:
        with self._lock:
            answer, at = self._answer, self._at
        if at is None or self._clock() - at > self._stale:
            return False
        return bool(isinstance(answer, dict) and answer.get("presentable"))

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

    `settle` runs every tick whatever the decision is -- it is where the
    opacity the player just asked for reaches the window, on this thread
    rather than on the control pipe's.
    """

    def __init__(self, observe, apply, wanted, settle=None, presentable=None):
        self._observe = observe
        self._apply = apply
        self._wanted = wanted
        self._settle = settle
        self._presentable = presentable or (lambda: True)
        self._last: Placement | None = None

    def tick(self) -> Placement | None:
        """One look. Returns what was applied, or None if nothing changed."""
        if self._settle is not None:
            self._settle()
        placement = follow_decision(self._observe(), wanted_visible=self._wanted(),
                                    presentable=self._presentable())
        if placement == self._last:
            return None
        self._last = placement
        self._apply(placement)
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
    return parser


def make_emitter(stream):
    lock = threading.Lock()

    def emit(payload: dict) -> None:
        fields = dict(payload)
        line = encode_event(fields.pop("event"), **fields)
        with lock:
            stream.write(line)
            stream.flush()
    return emit


def build_follower(args, *, watcher, host, placer, state, emit, page) -> FollowLoop:
    """The follow loop wired to this run: what it looks at, what it moves.

    `--fixed-rect` stands in for the emulator so the suite never queries
    Project64's window; with no game handle the placer leaves the z-order
    alone, which is what makes a test child safe to run while he plays.
    """
    if args.fixed_rect is not None:
        fixed = GameWindow(found=True, minimized=False, client=args.fixed_rect)

        def observe() -> GameWindow:
            return fixed
    else:
        observe = watcher.observe

    def place(placement: Placement) -> None:
        emit({"event": "placed", "show": placement.show,
              "reason": placement.reason,
              "rect": placement.rect.as_list() if placement.rect else None})
        diag.record("decision", level="info", show=placement.show,
                    reason=placement.reason, rect=placement.rect,
                    game_hwnd=watcher.hwnd)
        placer.apply(host.hwnd(), placement, game_hwnd=watcher.hwnd)

    def settle() -> None:
        """Every tick, whatever the decision: the things that change WITHOUT
        the game picture moving. An opacity he just asked for, another window
        sliding in front of the ghost, and the one-time handover of the page
        background to the WebView2 control itself."""
        hwnd = host.hwnd()
        if hwnd:
            background = host.use_transparent_page_background()
            if background is not None:
                diag.record("page_background", level="info", result=background)
        placer.ensure_alpha(hwnd, alpha_byte(state.opacity))
        placer.keep_above(hwnd, watcher.hwnd)

    return FollowLoop(observe=observe, apply=place,
                      wanted=lambda: state.visible, settle=settle,
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
    emit = make_emitter(sys.stdout)
    placer = Placer()
    watcher = Project64Watcher(args.title)
    diag.record("child_start", level="info", backend=host.name, url=state.url,
                opacity=state.opacity, visible=state.visible,
                title=args.title, process_pin_ok=bool(dpi_pinned))

    page = PageWatch(host)
    follow = build_follower(args, watcher=watcher, host=host, placer=placer,
                            state=state, emit=emit, page=page)
    plane = ControlPlane(host=host, state=state, emit=emit, wake=wake)

    host.open(state.url, args.fixed_rect or Rect(x=0, y=0, width=640, height=480))

    def control() -> None:
        reason = run_control_loop(sys.stdin, plane)
        emit({"event": "stopping", "reason": reason})
        diag.record("stopping", level="info", reason=reason)
        stop.set()
        wake.set()

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
        stop.set()
        wake.set()
        host.close()
        for thread in threads:
            thread.join(timeout=2)
    return 1 if failed else 0
