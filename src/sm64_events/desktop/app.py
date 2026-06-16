# src/sm64_events/desktop/app.py
"""Desktop composition root: single-instance dialog -> server -> tray ->
window, with one-click restart. Runnable from source as
``uv run python -m sm64_events.desktop`` for fast iteration."""
import ctypes
import logging
import os

from sm64_events.core.logging_setup import configure_logging
from sm64_events.core.paths import (APP_DISPLAY_NAME, data_root,
                                    migrate_legacy_data_dir)
from sm64_events.core.relaunch import spawn_replacement, wait_port_free
from sm64_events.desktop import single_instance, tray, window
from sm64_events.desktop.server_runner import ServerRunner
from sm64_events.main import build

log = logging.getLogger("sm64.desktop")

_MB_YESNO = 0x4
_MB_ICONQUESTION = 0x20
_MB_ICONERROR = 0x10
_IDYES = 6


def _ask_takeover() -> bool:
    """Native yes/no: Yes = close the other instance and run here."""
    return ctypes.windll.user32.MessageBoxW(
        None,
        f"{APP_DISPLAY_NAME} is already running.\n\n"
        "Use THIS window and close the other instance?",
        APP_DISPLAY_NAME, _MB_YESNO | _MB_ICONQUESTION) == _IDYES


def _error(msg: str) -> None:
    ctypes.windll.user32.MessageBoxW(None, msg, APP_DISPLAY_NAME, _MB_ICONERROR)


def main() -> None:
    configure_logging()
    # Rename %LOCALAPPDATA%\sm64_tracker -> SM64Trainer BEFORE the mkdir below
    # touches the new dir, so existing PBs/replays carry over.
    migrate_legacy_data_dir()
    data_root().mkdir(parents=True, exist_ok=True)

    if os.environ.pop("SM64_RESTART", None):
        # Restart relaunch: the old process is exiting — wait for the port,
        # no dialog.
        wait_port_free()
    elif single_instance.instance_running():
        if not _ask_takeover():
            return  # keep the other instance; quit this one
        if not single_instance.take_over():
            _error("Could not close the other instance. It may still be "
                   "running.")
            return

    app = build()
    runner = ServerRunner(app)
    runner.start()
    if not runner.wait_until_ready():
        _error("The tracker server did not start. Check the logs.")
        runner.stop()
        return

    state = {"quit": False, "tray": None}

    def quit_all():
        if state["quit"]:
            return
        state["quit"] = True
        runner.stop()
        if state["tray"] is not None:
            tray.stop(state["tray"])
        for w in _windows():
            try:
                w.destroy()
            except Exception:
                pass

    def do_restart():
        spawn_replacement()
        quit_all()

    # Admin endpoints drive a FULL GUI quit / relaunch (not just the server),
    # so "close the other instance" and "Restart server" really do.
    app.state.request_shutdown = quit_all
    app.state.request_restart = do_restart

    win = window.create(on_closed=quit_all)
    state["tray"] = tray.create(on_show=win.show, on_quit=quit_all)
    tray.start(state["tray"])

    window.run()    # blocks until the window closes
    quit_all()      # idempotent backstop for the normal close path


def _windows():
    # pywebview 6.2.1 verified: webview.windows is a module-level list of
    # open Window objects; .destroy() closes the window (also verified present).
    import webview
    return list(webview.windows)
