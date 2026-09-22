# src/sm64_events/desktop/closeguard.py
"""Should closing the app wait for the page to ask first?

While saved replays are still being compressed, closing the window opens the
page's close warning instead (what is in flight, how far along, Exit anyway /
Wait). Nothing is lost by exiting -- the replays are already saved and an
unfinished one is shrunk at the next launch -- so every doubt resolves to
CLOSING: a server that does not answer, a page that cannot show the warning
(an old cached script, a page that never loaded), or a second close inside
`insist_s` of the first all let the app go. The guard must never be the reason
a window cannot be closed.

Pure of pywebview so it is testable: the shell supplies the three callables.
"""
import json
import logging
import threading
import time
import urllib.request

log = logging.getLogger("sm64.desktop")

# The page sets `window.__sm64CloseWarning` once its listener is mounted; the
# answer tells the shell whether anyone is there to show the warning.
ASK_PAGE_JS = (
    "(function(){ if (!window.__sm64CloseWarning) return false;"
    " window.dispatchEvent(new CustomEvent('sm64-close-requested')); return true; })()")


def compression_active(port: int, timeout_s: float = 0.5) -> bool:
    """Our own server's answer; anything but a clear yes is a no."""
    try:
        url = f"http://127.0.0.1:{port}/api/replay/compression"
        with urllib.request.urlopen(url, timeout=timeout_s) as response:  # noqa: S310 - loopback
            return json.load(response).get("active") is True
    except Exception:  # noqa: BLE001 - see module docstring: doubt closes
        return False


class CloseGuard:
    def __init__(self, *, is_active, ask_page, quit_all, clock=time.monotonic,
                 insist_s: float = 10.0):
        self._is_active, self._ask_page, self._quit_all = is_active, ask_page, quit_all
        self._clock, self._insist_s = clock, insist_s
        self._asked_at: float | None = None
        self.quitting = False

    def _must_ask(self) -> bool:
        if self.quitting or not self._is_active():
            return False
        now = self._clock()
        if self._asked_at is not None and now - self._asked_at < self._insist_s:
            return False   # he closed again with the warning up: he means it
        self._asked_at = now
        return True

    def _ask(self) -> None:
        def run():
            shown = False
            try:
                shown = self._ask_page() is True
            except Exception:  # noqa: BLE001
                log.debug("close warning could not be shown", exc_info=True)
            if not shown:
                self._quit_all()
        threading.Thread(target=run, name="close-warning", daemon=True).start()

    def closing(self) -> bool:
        """The window's close button. True lets the window close."""
        if self._must_ask():
            self._ask()
            return False
        return True

    def quit_requested(self) -> None:
        """Tray Quit: the same question, then the same quit."""
        if self._must_ask():
            self._ask()
        else:
            self._quit_all()
