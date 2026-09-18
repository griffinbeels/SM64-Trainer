"""The overlay page in a real browser, driving a real <video>.

`tests/test_ui_ghost_follower.py` owns the NUMBERS -- what the rule decides and
how many game frames it is out by. This file owns the half a trace cannot
answer: that the page resolves its clip, that its module graph loads in a
browser at all, and that a decision reaches an actual HTMLVideoElement (a seek
lands, play runs, hold pauses). The invisible-feature rule in
`.claude/rules/ui-core.md` is exactly this gap -- unit tests plus `node --check`
once shipped a feature that rendered nothing.

The clock arrives by `postMessage`, which is the page's second real input and
not a test hook: it is how the parent window pushes the nudge, and how the
graphics plugin would publish the picture it is drawing if the memory poll ever
proves too slow (the brainstorm's A+ escalation).
"""
import base64
import json
import shutil
import subprocess
import sys
import threading
from functools import partial
from hashlib import sha1
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import pytest

REPO = Path(__file__).resolve().parents[1]
UI = REPO / "src" / "sm64_events" / "ui"

sys.path.insert(0, str(REPO / "tools"))
from find_uilab import find_uilab  # noqa: E402

_MISSING = find_uilab()
if _MISSING:
    pytest.skip(_MISSING, allow_module_level=True)

from uilab.driver import get_driver  # noqa: E402

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="ffmpeg builds the stand-in clip this page needs to play")

CLIP_SECONDS = 20
CLIP_FPS = 30
# A constant-rate stand-in for a template: one picture per game frame, the
# counter running from zero. The follower's own VFR/offset cases are scored in
# the trace harness; what this file needs is a clip a browser will really play.
VIEW = {"frame_times": None, "picture_igt": list(range(CLIP_SECONDS * CLIP_FPS)),
        "duration_s": float(CLIP_SECONDS), "video_start_s": 0.0,
        "fps": CLIP_FPS, "game_fps": 30, "encode": "cfr"}


def _build_clip(path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size=320x240:rate={CLIP_FPS}",
         "-t", str(CLIP_SECONDS), "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-g", "15", "-movflags", "+faststart", str(path)],
        check=True, capture_output=True, timeout=180)


class _Handler(BaseHTTPRequestHandler):
    """Serves the real `ui/` off disk plus the one endpoint the page calls.

    Range support is not optional: Chromium will not seek inside a video the
    server answers with a flat 200, and the whole page is seeks.
    """

    def __init__(self, *args, clip: Path, available: bool, **kwargs):
        self.clip = clip
        self.available = available
        super().__init__(*args, **kwargs)

    def log_message(self, *args):
        pass

    def _json(self, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _media(self) -> None:
        data = self.clip.read_bytes()
        start, end = 0, len(data) - 1
        header = self.headers.get("Range")
        if header and header.startswith("bytes="):
            first, _, last = header[6:].partition("-")
            start = int(first or 0)
            end = int(last) if last else end
        chunk = data[start:end + 1]
        self.send_response(206 if header else 200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(len(chunk)))
        if header:
            self.send_header("Content-Range",
                             f"bytes {start}-{end}/{len(data)}")
        self.end_headers()
        self.wfile.write(chunk)

    def _accept_websocket(self) -> None:
        """Answer the clock socket's handshake and then hold it open.

        The page opens `/ws/ghost-clock` for real, so a fixture that 404s it
        would leave a failed-connection error in the console and a retry timer
        running -- indistinguishable, in `page.problems()`, from the page being
        broken. This never SENDS: the trace arrives by postMessage, which is
        the same door the companion window uses.
        """
        key = self.headers.get("Sec-WebSocket-Key", "")
        digest = base64.b64encode(sha1(
            (key + "258EAFA5-E914-47DA-95CA-5AB0DC85B11F").encode()).digest())
        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", digest.decode())
        self.end_headers()
        self.connection.settimeout(1.0)
        while not self.server._closing:
            try:
                if not self.connection.recv(1024):
                    return
            except OSError:
                continue

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler's own spelling
        path = unquote(urlparse(self.path).path)
        if path == "/ws/ghost-clock":
            return self._accept_websocket()
        if path == "/media/ghost.mp4":
            return self._media()
        if path == "/api/ghost/source":
            if not self.available:
                return self._json({
                    "available": False, "reason": "template_has_no_attempt",
                    "reason_text": "this template was typed in, so it has no footage",
                    "template_id": 3, "attempt_id": None, "media_url": None,
                    "view": None, "exactness": None})
            return self._json({
                "available": True, "reason": None, "reason_text": None,
                "template_id": 3, "attempt_id": 35076,
                "media_url": "/media/ghost.mp4", "view": VIEW,
                "exactness": "frame_map"})
        if path.startswith("/ui/"):
            target = UI / path[len("/ui/"):]
            if target.is_file():
                body = target.read_bytes()
                kind = ("text/html" if target.suffix == ".html"
                        else "text/javascript" if target.suffix == ".js"
                        else "application/octet-stream")
                self.send_response(200)
                self.send_header("Content-Type", f"{kind}; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
        # A miss returning {} is indistinguishable from a component that draws
        # nothing (ui-core.md). Say so out loud.
        print("GHOST FIXTURE MISS", path)
        self.send_response(404)
        self.end_headers()


class _Server:
    """One server for both fixtures; `available` is swapped between the two
    navigations so the page is asked the same question twice."""

    def __init__(self, clip: Path):
        self.clip = clip
        self.httpd = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(_Handler, clip=clip, available=True))
        # Held sockets poll this so the server can actually be torn down.
        self.httpd._closing = False
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def set_available(self, available: bool) -> None:
        self.httpd.RequestHandlerClass = partial(
            _Handler, clip=self.clip, available=available)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd._closing = True
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


# Push a clock from INSIDE the page at the game's own rate, and read the
# status from inside it too.
#
# Both halves matter. Driving 120 messages one CDP round trip at a time would
# measure the harness rather than the page; and READING the status over a
# round trip after the trace ends would race the follower's own starvation
# hold -- it stops after `starveMs` without a sample, exactly as it must when
# the emulator is paused, so a late read reports the hold instead of the play
# it was asked about. The page samples itself 25 ms after the last message.
_TRACE = """(() => {
  const igts = %s;
  window.__done = false; window.__result = null;
  let k = 0;
  const id = setInterval(() => {
    window.postMessage({type: 'ghost-clock', igt: igts[k],
                        global_timer: 9000 + k, level: 8, area: 1}, '*');
    k += 1;
    if (k >= igts.length) {
      clearInterval(id);
      setTimeout(() => {
        window.__result = window.ghostStatus();
        window.__done = true;
      }, 25);
    }
  }, 33);
  return true;
})()"""


def _trace(page, igts: list) -> dict:
    """Run one clock trace and answer with what the page was doing at its end."""
    page.evaluate(_TRACE % json.dumps(igts))
    for _ in range(len(igts) * 2 + 60):
        if page.evaluate("window.__done === true"):
            break
        page.wait_ms(50)
    result = page.evaluate("window.__result")
    assert result is not None, "the trace never finished"
    return result


def test_the_overlay_page_follows_a_clock_in_a_real_browser(tmp_path):
    clip = tmp_path / "ghost.mp4"
    _build_clip(clip)

    with _Server(clip) as server, get_driver().launch() as page:
        # 1. Nothing to show says so, and paints nothing.
        server.set_available(False)
        page.goto(f"{server.url}/ui/ghost/index.html?kind=star&entity_key=LLL-1")
        page.wait_for('body[data-ghost="unavailable"]')
        assert page.evaluate("document.body.dataset.reason") == "template_has_no_attempt"
        assert page.evaluate(
            "getComputedStyle(document.getElementById('ghost')).visibility"
        ) == "hidden"

        # 2. A real template: the page resolves it and opens the clip.
        server.set_available(True)
        page.goto(f"{server.url}/ui/ghost/index.html?kind=star&entity_key=LLL-1")
        page.wait_for('body[data-ghost="ready"]')
        assert page.evaluate("window.ghostStatus().template.pictures") == 600
        assert page.evaluate("document.getElementById('ghost').muted") is True
        # Full-bleed: the video fills the window, with no chrome beside it.
        box = page.evaluate(
            "(() => { const r = document.getElementById('ghost')"
            ".getBoundingClientRect(); return {w: r.width, h: r.height,"
            " x: r.x, y: r.y}; })()")
        assert box["x"] == 0 and box["y"] == 0
        assert box["w"] == page.evaluate("window.innerWidth")
        assert box["h"] == page.evaluate("window.innerHeight")

        # 3. Steady play: the element really runs, on the picture the rule
        #    names. The tolerance is 2 rather than the trace harness's 1
        #    because this one includes a decoder: `shown_igt` reads the
        #    picture Chromium PRESENTED, which trails the element's own time.
        playing = _trace(page, list(range(0, 121)))
        assert playing["samples"] >= 100, playing
        assert playing["paused"] is False, playing
        assert playing["shown_igt"] is not None, playing
        assert abs(playing["shown_igt"] - playing["target_igt"]) <= 2, playing

        # 4. The timer stalls: the element pauses, with no pause detector.
        stalled = _trace(page, [120] * 20)
        assert stalled["paused"] is True, stalled
        assert stalled["decision"]["reason"] == "stalled", stalled

        # 5. A reset: the counter returns to zero and so does the ghost, held
        #    on the template's own first picture until the timer moves.
        reset = _trace(page, [0])
        assert reset["decision"]["reason"] == "reset", reset
        assert reset["paused"] is True, reset
        assert reset["shown_igt"] == 0, reset

        # 6. His timing hotkey, over the same channel the window will use.
        page.evaluate(
            "(() => { window.postMessage({type: 'ghost-nudge',"
            " nudge_frames: 45}, '*'); return true; })()")
        page.wait_ms(200)
        nudged = _trace(page, list(range(100, 131)))
        assert nudged["nudge_frames"] == 45, nudged
        assert nudged["target_igt"] == nudged["sample"]["igt"] + 45, nudged
        assert abs(nudged["shown_igt"] - nudged["target_igt"]) <= 2, nudged

        assert page.problems() == []
