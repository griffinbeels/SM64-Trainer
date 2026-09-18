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

The follower is also the ONE place that shows or hides the overlay. A control
line only records what the player wants; the decision that turns that into a
visible window is `geometry.follow_decision`, so "the ghost is off", "PJ64 is
minimized" and "PJ64 is gone" cannot be three different pieces of code that
disagree.
"""
import argparse
import logging
import sys
import threading
from dataclasses import dataclass

from sm64_events.ghost.window import native
from sm64_events.ghost.window.geometry import (
    GameWindow,
    Placement,
    Rect,
    dpi_scale,
    follow_decision,
)
from sm64_events.ghost.window.host import BACKENDS, make_host
from sm64_events.ghost.window.protocol import (
    alpha_byte,
    encode_event,
    is_allowed_url,
    parse_control_line,
)

log = logging.getLogger("sm64.ghost")

#: How often the overlay re-checks where the game picture is. PJ64 is not
#: moved mid-attempt, so this is about catching a resize or a minimize
#: promptly, not about tracking a drag frame by frame.
DEFAULT_FOLLOW_PERIOD_S = 0.10


@dataclass
class OverlayState:
    """What the player has asked for, as opposed to what is on screen."""

    url: str
    opacity: float
    visible: bool


class ControlPlane:
    """Turns a control line into host calls and wanted state. Never shows."""

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
            self.host.set_alpha(alpha_byte(message.opacity))
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


class FollowLoop:
    """Keeps the overlay on the game picture, and applies only what changed."""

    def __init__(self, observe, apply, wanted, scale=None):
        self._observe = observe
        self._apply = apply
        self._wanted = wanted
        self._scale = scale or (lambda: 1.0)
        self._last: Placement | None = None

    def tick(self) -> Placement | None:
        """One look. Returns what was applied, or None if nothing changed."""
        placement = follow_decision(self._observe(), scale=self._scale(),
                                    wanted_visible=self._wanted())
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
            except Exception:  # noqa: BLE001 - a dead follower freezes the ghost on screen
                log.exception("ghost overlay follow tick failed")
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


def make_scale(watcher: Project64Watcher, host) -> float:
    """1.0 while the reader and the window live in the same DPI world.

    Pinning the process to per-monitor awareness is what buys that. If Windows
    refused -- something set the context first -- the emulator is read in
    physical pixels while the overlay is placed in scaled ones, and this is
    the correction.
    """
    return dpi_scale(native.window_dpi(watcher.hwnd), native.window_dpi(host.hwnd()))


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)

    dpi_pinned = native.set_process_per_monitor_aware()
    host = make_host(args.backend)
    state = OverlayState(url=args.url, opacity=args.opacity, visible=args.visible)
    stop = threading.Event()
    wake = threading.Event()
    emit = make_emitter(sys.stdout)

    watcher = Project64Watcher(args.title)
    if args.fixed_rect is not None:
        fixed = GameWindow(found=True, minimized=False, client=args.fixed_rect)

        def observe() -> GameWindow:
            return fixed

        def scale() -> float:
            return 1.0
    else:
        observe = watcher.observe

        def scale() -> float:
            return make_scale(watcher, host)

    def place(placement: Placement) -> None:
        emit({"event": "placed", "show": placement.show,
              "reason": placement.reason,
              "rect": placement.rect.as_list() if placement.rect else None})
        if placement.show and placement.rect is not None:
            host.place(placement.rect, owner_hwnd=watcher.hwnd)
            host.show()
        else:
            host.hide()

    follow = FollowLoop(observe=observe, apply=place,
                        wanted=lambda: state.visible, scale=scale)
    plane = ControlPlane(host=host, state=state, emit=emit, wake=wake)

    host.open(state.url, args.fixed_rect or Rect(x=0, y=0, width=640, height=480))
    host.set_alpha(alpha_byte(state.opacity))

    def control() -> None:
        reason = run_control_loop(sys.stdin, plane)
        emit({"event": "stopping", "reason": reason})
        stop.set()
        wake.set()

    threads = [
        threading.Thread(target=control, name="ghost-control", daemon=True),
        threading.Thread(target=follow.run, args=(stop, args.follow_period, wake),
                         name="ghost-follow", daemon=True),
    ]
    # Ready goes out BEFORE the follower can report its first placement, so
    # whoever reads this stream sees the child come up in one known order.
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
    finally:
        stop.set()
        wake.set()
        host.close()
        for thread in threads:
            thread.join(timeout=2)
    return 1 if failed else 0
