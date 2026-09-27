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
import ctypes
import ctypes.wintypes as wt
import json
import logging
import subprocess
import threading

from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
from sm64_events.ghost.window import diag
from sm64_events.ghost.window.protocol import encode_control_line, is_allowed_url

log = logging.getLogger("sm64.ghost")

TH32CS_SNAPPROCESS = 0x00000002
PROCESS_TERMINATE = 0x0001


class _PROCESSENTRY32(ctypes.Structure):
    _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                ("th32ProcessID", wt.DWORD),
                ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                ("th32ParentProcessID", wt.DWORD),
                ("pcPriClassBase", ctypes.c_long), ("dwFlags", wt.DWORD),
                ("szExeFile", ctypes.c_char * 260)]


def parent_map() -> dict[int, int]:
    """Every live process id -> its parent's, from one kernel snapshot.

    By PARENT PID and nothing else. Names and command lines are deliberately
    not read: half this machine is a python process, and matching on that is
    how a cleanup kills somebody else's work.
    """
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == wt.HANDLE(-1).value:
        return {}
    entry = _PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(_PROCESSENTRY32)
    found: dict[int, int] = {}
    try:
        if not kernel32.Process32First(snapshot, ctypes.byref(entry)):
            return {}
        while True:
            found[int(entry.th32ProcessID)] = int(entry.th32ParentProcessID)
            if not kernel32.Process32Next(snapshot, ctypes.byref(entry)):
                break
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def descendants(pid: int, parents: dict[int, int] | None = None) -> list[int]:
    """Every live process descended from `pid`, nearest generation first.

    This exists because THE RECORDED PID IS NOT THE OVERLAY. This venv starts
    Python through a shim, so `Popen.pid` is the shim and the process that
    owns the WebView2 window is its child -- terminating the shim leaves an
    overlay on his screen, which is the one failure this class was written to
    prevent.
    """
    known = parent_map() if parents is None else parents
    out: list[int] = []
    frontier = {pid}
    while frontier:
        children = {child for child, parent in known.items()
                    if parent in frontier and child != pid and child not in out}
        out.extend(sorted(children))
        frontier = children
    return out


def terminate(pid: int) -> bool:
    """End one process by its id. Returns whether the handle took it."""
    kernel32 = ctypes.windll.kernel32
    kernel32.OpenProcess.restype = wt.HANDLE
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(kernel32.TerminateProcess(handle, 1))
    finally:
        kernel32.CloseHandle(handle)

MODULE = "sm64_events.ghost.window"

#: How the SHIPPED exe re-enters as the overlay child. A PyInstaller build has
#: no `-m`: the exe is the trainer itself, so its entry point has to recognise
#: this flag and hand argv to `child.main`. The flag lives here so the entry
#: point and the controller cannot drift apart.
GHOST_WINDOW_FLAG = "--ghost-window"

DEFAULT_STOP_TIMEOUT_S = 5.0


def ghost_window_child(*, url: str, opacity: float, visible: bool,
                       backend: str = "webview", title: str = "Project64",
                       follow_period: float | None = None) -> tuple[list[str], dict | None]:
    """(argv, env) for the overlay: a direct child interpreter that still
    sees the venv's pywebview (core/childproc.py::python_child)."""
    args = ["--url", url, "--opacity", f"{opacity:g}",
            "--backend", backend, "--title", title]
    if visible:
        args.append("--visible")
    if follow_period is not None:
        args += ["--follow-period", f"{follow_period:g}"]
    return python_child(MODULE, GHOST_WINDOW_FLAG, args, site_packages=True)


def ghost_window_command(**settings) -> list[str]:
    return ghost_window_child(**settings)[0]


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

    def tree(self) -> list[int]:
        """The pids this controller is responsible for, recorded at spawn.

        The overlay first, then whatever it started. Read live rather than
        cached, because WebView2 starts its own helpers after we let go.
        """
        process = self._process
        if process is None:
            return []
        return [process.pid, *descendants(process.pid)]

    def start(self) -> None:
        if self.is_running():
            return
        command, env = (self._command, None) if self._command else ghost_window_child(
            url=self._url, opacity=self._opacity, visible=self._visible,
            backend=self._backend, title=self._title)
        try:
            self._process = subprocess.Popen(
                # stderr joins stdout so a WebView2 failure arrives as an
                # event instead of vanishing: this child has no console.
                command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
        """Ask, then terminate, then kill the whole TREE. Returns once gone.

        **The recorded pid is not the whole overlay.** WebView2 starts its own
        helper processes, and until 2026-09-27 a source run started Python
        through the venv's launcher, so the window lived one generation below
        `Popen.pid`. `terminate()` and `kill()` reach only that one process.
        Every survivor is found by PARENT PID from the pid we recorded at
        spawn -- never by name or command line.
        """
        process, self._process = self._process, None
        reader, self._reader = self._reader, None
        if process is None:
            return
        # Taken BEFORE anything ends: once a parent exits its children are
        # reparented and the chain back to our pid is gone.
        owned = [process.pid, *descendants(process.pid)]
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
        self._end_survivors(owned)
        if reader is not None:
            reader.join(timeout=2)
        _close(process.stdout)

    def _end_survivors(self, owned: list[int]) -> None:
        """Whatever the shim left behind, by the pids we recorded."""
        alive = parent_map()
        survivors = [pid for pid in owned if pid in alive]
        if not survivors:
            return
        # Deepest first: a parent that outlives its child cannot adopt it out.
        ended = [pid for pid in reversed(survivors) if terminate(pid)]
        still = [pid for pid in survivors if pid in parent_map()]
        log.warning("ghost overlay left %s behind; ended %s", survivors, ended)
        diag.record("window_tree_ended", level="error" if still else "info",
                    owned=owned, survivors=survivors, ended=ended,
                    still_running=still)

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
