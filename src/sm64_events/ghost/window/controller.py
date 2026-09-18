"""The trainer's side of the ghost overlay: start it, talk to it, end it.

The overlay is a child process because pywebview needs its own process's main
thread, and it belongs to the SERVER side rather than to `desktop/` because
browser and desktop users both get the ghost (domain contract 4).

Two things this class exists to guarantee, both of which have bitten this
project before:

- no console flash and no busy pointer -- every spawn goes through
  core/childproc.py, the one door;
- nothing left running -- `stop()` asks, then terminates, then kills, and does
  not return until the process is actually gone.
"""
import json
import logging
import subprocess
import sys
import threading

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.ghost.window.protocol import encode_control_line, is_allowed_url

log = logging.getLogger("sm64.ghost")

MODULE = "sm64_events.ghost.window"

#: How the SHIPPED exe re-enters as the overlay child. A PyInstaller build has
#: no `-m`: sys.executable is the trainer itself, so its entry point has to
#: recognise this flag and hand argv to `child.main`. The flag lives here so
#: the entry point and the controller cannot drift apart.
GHOST_WINDOW_FLAG = "--ghost-window"

DEFAULT_STOP_TIMEOUT_S = 5.0


def ghost_window_command(*, url: str, opacity: float, visible: bool,
                         backend: str = "webview", title: str = "Project64",
                         follow_period: float | None = None) -> list[str]:
    head = ([sys.executable, GHOST_WINDOW_FLAG] if getattr(sys, "frozen", False)
            else [sys.executable, "-m", MODULE])
    command = [*head, "--url", url, "--opacity", f"{opacity:g}",
               "--backend", backend, "--title", title]
    if visible:
        command.append("--visible")
    if follow_period is not None:
        command += ["--follow-period", f"{follow_period:g}"]
    return command


class GhostWindowController:
    """One overlay window, owned by whoever holds this object."""

    def __init__(self, *, url: str, opacity: float = 0.5, visible: bool = False,
                 backend: str = "webview", title: str = "Project64",
                 command: list[str] | None = None, on_event=None,
                 stop_timeout_s: float = DEFAULT_STOP_TIMEOUT_S):
        self._url = _checked_url(url)
        self._opacity = _checked_opacity(opacity)
        self._visible = bool(visible)
        self._backend = backend
        self._title = title
        self._command = command
        self._on_event = on_event or (lambda payload: log.debug("ghost overlay: %s", payload))
        self._stop_timeout_s = stop_timeout_s
        self._process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None

    # --- what the trainer reads --------------------------------------------

    @property
    def url(self) -> str:
        return self._url

    @property
    def opacity(self) -> float:
        return self._opacity

    @property
    def visible(self) -> bool:
        return self._visible

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    # --- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        if self.is_running():
            return
        command = self._command or ghost_window_command(
            url=self._url, opacity=self._opacity, visible=self._visible,
            backend=self._backend, title=self._title)
        try:
            self._process = subprocess.Popen(
                # stderr joins stdout so a WebView2 failure arrives as an
                # event instead of vanishing: this child has no console.
                command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                bufsize=1, **quiet_spawn_kwargs())
        except OSError:
            self._process = None
            log.exception("could not start the ghost overlay window")
            return
        self._reader = threading.Thread(target=self._read_events,
                                        args=(self._process,),
                                        name="ghost-window-events", daemon=True)
        self._reader.start()

    def stop(self) -> None:
        """Ask, then terminate, then kill. Returns only once it is gone."""
        process, self._process = self._process, None
        reader, self._reader = self._reader, None
        if process is None:
            return
        if process.poll() is None:
            self._write(process, encode_control_line(quit=True))
        _close(process.stdin)
        for ender in (None, process.terminate, process.kill):
            if ender is not None:
                ender()
            try:
                process.wait(timeout=self._stop_timeout_s if ender is None else 5.0)
                break
            except subprocess.TimeoutExpired:
                log.warning("ghost overlay did not end on its own; escalating")
        if process.poll() is None:
            log.error("ghost overlay process %s survived terminate and kill",
                      process.pid)
        if reader is not None:
            reader.join(timeout=2)
        _close(process.stdout)

    def __enter__(self) -> "GhostWindowController":
        self.start()
        return self

    def __exit__(self, *_exc) -> None:
        self.stop()

    # --- controls -----------------------------------------------------------

    def set_opacity(self, value: float) -> None:
        self._opacity = _checked_opacity(value)
        self._send(opacity=self._opacity)

    def set_visible(self, value: bool) -> None:
        self._visible = bool(value)
        self._send(visible=self._visible)

    def set_url(self, url: str) -> None:
        self._url = _checked_url(url)
        self._send(url=self._url)

    # --- the pipe -----------------------------------------------------------

    def _send(self, **fields) -> None:
        if not self.is_running():
            return  # the value is remembered; the next start carries it
        self._write(self._process, encode_control_line(**fields))

    def _write(self, process: subprocess.Popen, line: str) -> None:
        try:
            process.stdin.write(line)
            process.stdin.flush()
        except (OSError, ValueError):
            log.debug("ghost overlay pipe is closed; dropped %r", line, exc_info=True)

    def _read_events(self, process: subprocess.Popen) -> None:
        try:
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    payload = json.loads(text)
                except ValueError:
                    payload = {"event": "log", "line": text}
                self._on_event(payload)
        except (OSError, ValueError):
            log.debug("ghost overlay event stream ended", exc_info=True)


def _checked_opacity(value: float) -> float:
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError(f"opacity must be between 0 and 1, got {value!r}")
    return number


def _checked_url(url: str) -> str:
    if not is_allowed_url(url):
        raise ValueError(f"the overlay will not load {url!r}")
    return url


def _close(pipe) -> None:
    if pipe is None or pipe.closed:
        return
    try:
        pipe.close()
    except (OSError, ValueError):
        log.debug("ghost overlay pipe would not close", exc_info=True)
