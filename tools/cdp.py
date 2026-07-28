"""Drive headless Chrome over the DevTools Protocol.  No Playwright.

`--dump-dom` + `--virtual-time-budget` RENDERS the app but cannot DRIVE it:
clicks dispatch, DOM listeners fire, and no Preact re-render ever happens
(measured 2026-07-26 -- `document.body.innerHTML.length` came back
byte-identical after clicking a plain nav tab).  Anything needing a viewport
override or an interaction has to speak CDP, so this is the smallest client
that does: launch, attach, resize, evaluate, screenshot.

Collecting `Runtime.exceptionThrown` is not optional.  Without it a broken
FIXTURE and a broken FEATURE look identical -- the app keeps firing DOM
listeners while its Preact tree has stopped updating, which reads as "the
feature does nothing".

Nothing this module starts outlives the caller: Chrome is spawned with
CREATE_NO_WINDOW (Claude's own tooling never earns focus on this machine) and
is terminated, waited on, and has its profile removed in a `finally`.
"""
from __future__ import annotations

import base64
import contextlib
import json
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

_WINDOWS_CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
)


def _find_chrome() -> Path | None:
    for name in ("chrome", "google-chrome", "chromium"):
        found = shutil.which(name)
        if found:
            return Path(found)
    return next((Path(c) for c in _WINDOWS_CANDIDATES if Path(c).exists()), None)


CHROME = _find_chrome()

# No console window, ever.  See the `spawned-processes` skill: a human gesture
# earns focus, Claude's own tooling never does.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class Session:
    """One attached page.  Synchronous: every call blocks for its reply."""

    def __init__(self, socket_url: str):
        self._ws = connect(socket_url, max_size=None, open_timeout=20)
        self._next_id = 0
        self.errors: list[str] = []
        self._send("Runtime.enable")
        self._send("Page.enable")

    def _send(self, method: str, **params) -> dict:
        self._next_id += 1
        message_id = self._next_id
        self._ws.send(json.dumps(
            {"id": message_id, "method": method, "params": params}))
        while True:
            message = json.loads(self._ws.recv())
            # Events arrive interleaved with command replies.  Dropping them is
            # how a broken fixture masquerades as a broken feature.
            if message.get("method") == "Runtime.exceptionThrown":
                detail = message["params"].get("exceptionDetails", {})
                text = detail.get("exception", {}).get("description") \
                    or detail.get("text", "unknown exception")
                self.errors.append(str(text))
            elif (message.get("method") == "Runtime.consoleAPICalled"
                  and message["params"].get("type") == "error"):
                self.errors.append(json.dumps(message["params"].get("args")))
            elif message.get("id") == message_id:
                if "error" in message:
                    raise RuntimeError(f"{method}: {message['error']}")
                return message.get("result", {})

    def set_viewport(self, width: int, height: int) -> None:
        """Override the LAYOUT viewport.

        Deliberately not a window resize: the override is exact, immediate and
        does not depend on window-manager behaviour or OS chrome, and headless
        Chrome's window size is not a thing the sweep should have to trust.
        """
        self._send("Emulation.setDeviceMetricsOverride", width=width,
                   height=height, deviceScaleFactor=1, mobile=False)

    def evaluate(self, expression: str):
        result = self._send("Runtime.evaluate", expression=expression,
                            awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            raise RuntimeError(detail.get("exception", {}).get("description")
                               or detail.get("text", "evaluate failed"))
        return result.get("result", {}).get("value")

    def screenshot(self) -> bytes:
        return base64.b64decode(
            self._send("Page.captureScreenshot", format="png")["data"])

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._ws.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextlib.contextmanager
def chrome_session(url: str, settle_ms: int = 900):
    """Launch headless Chrome on `url`, yield a Session, always clean up."""
    if CHROME is None:
        raise RuntimeError(
            "Chrome not found. Install it, or set SM64_SKIP_SWEEP=1 to opt out "
            "of the render gate deliberately.")
    port = _free_port()
    profile = tempfile.mkdtemp(prefix="sm64-sweep-")
    process = subprocess.Popen(
        [str(CHROME), "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={profile}", "--no-first-run",
         "--no-default-browser-check", "--disable-extensions", "--disable-gpu",
         # Scale factor pinned: this machine runs at 150% and a scaled
         # screenshot would make every measured pixel a lie.
         "--force-device-scale-factor=1",
         # NOT --hide-scrollbars: a hidden scrollbar hides the overflow the
         # sweep exists to find.
         url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=_NO_WINDOW)
    session = None
    try:
        deadline = time.monotonic() + 30
        target = None
        while target is None and time.monotonic() < deadline:
            try:
                listing = json.loads(urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/list", timeout=2).read())
                target = next((t for t in listing if t.get("type") == "page"
                               and t.get("webSocketDebuggerUrl")), None)
            except Exception:
                pass
            if target is None:
                time.sleep(0.15)
        if target is None:
            raise RuntimeError(f"Chrome never exposed a page target on {port}")
        session = Session(target["webSocketDebuggerUrl"])
        time.sleep(settle_ms / 1000)      # let the app's first fetches land
        yield session
    finally:
        if session is not None:
            session.close()
        process.terminate()
        with contextlib.suppress(Exception):
            process.wait(timeout=10)
        shutil.rmtree(profile, ignore_errors=True)
