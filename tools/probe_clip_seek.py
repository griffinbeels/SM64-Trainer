"""Hop 6 of the input-timeline chain: which picture does the BROWSER present
when the timeline seeks to slot k of a cached clip?

    uv run python tools/probe_clip_seek.py --attempt N [--slots a-b]

Offline, read-only, no emulator: the clip is served to a headless Chromium
from a loopback port with byte-range support (without it every seek snaps to
0 and reads as "the clip cannot seek"), the page imports the SHIPPED
`ui/frame.js`, and for every slot in the range it seeks twice -- once at the
time the timeline computed before 2026-09-01 (`timeOfSlot(k, fps)`, which
assumes the clip's first picture sits at t = 0) and once at the corrected
time (`timeOfSlot(k, fps, video_start_s)`) -- then reads `mediaTime` back
off `requestVideoFrameCallback` and converts it to the presented slot with
the same `slotAtTime` the panel uses. No arithmetic is restated here: the
page runs the real functions, so what this prints is what the panel does.

What it found on 2026-09-01 (his Log Rolling "84 2 on screen, 84 in our
tool", attempt 5782): the clip's first video pts was 0.011003 s, so the old
seek landed one picture EARLY on every slot and the panel honestly drew the
earlier frame's pad; the corrected seek lands on k. Attempt 5574 (first pts
0.000) was exact both ways, which is why the fault hid behind one clip.

The diagnosis also read the presented PIXELS (the X-row letter cell) to
prove mediaTime honest: it was, on both clips, so this tool reports
mediaTime alone. A frame map, a pad reader and an extractor can all be
right while this hop is wrong; run it before touching any of them.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import shutil
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sm64_events.core.paths import bundled_ffmpeg  # noqa: E402
from sm64_events.core.childproc import quiet_spawn_kwargs  # noqa: E402
from sm64_events.replay.extract import ffprobe_beside, video_start_of  # noqa: E402

FRAME_JS = ROOT / "src" / "sm64_events" / "ui" / "frame.js"

PAGE = """<!doctype html>
<video id=v src="/clip.mp4" muted preload="auto"></video>
<script type="module">
import { slotAtTime, timeOfSlot } from "/frame.js";
const video = document.getElementById("v");
// A seek that lands on the picture already shown never fires rVFC (it
// fires only when a NEW picture is presented), so race it with `seeked`.
async function seekTo(target) {
  video.pause();
  const presented = new Promise((resolve) =>
    video.requestVideoFrameCallback((_now, meta) => resolve(meta.mediaTime)));
  const seeked = new Promise((resolve) =>
    video.addEventListener("seeked",
      () => setTimeout(() => resolve(video.currentTime), 250), { once: true }));
  video.currentTime = target;
  return Promise.race([presented, seeked]);
}
window.probeSlot = async (slot, fps, start) => {
  const oldTarget = timeOfSlot(slot, fps, 0);
  const oldTime = await seekTo(oldTarget);
  const newTarget = timeOfSlot(slot, fps, start);
  const newTime = await seekTo(newTarget);
  return {
    slot,
    old: { target: oldTarget, mediaTime: oldTime,
           presented: slotAtTime(oldTime, fps, start) },
    fixed: { target: newTarget, mediaTime: newTime,
             presented: slotAtTime(newTime, fps, start) },
  };
};
window.probeReady = new Promise((resolve) => {
  if (video.readyState >= 1) resolve(true);
  else video.addEventListener("loadedmetadata", () => resolve(true), { once: true });
});
</script>
"""


class ClipHandler(http.server.BaseHTTPRequestHandler):
    """Three routes on one origin -- the page, the shipped frame.js and the
    clip -- with byte ranges on the clip so Chromium can seek it."""

    clip: Path = Path()

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path == "/probe.html":
            return self._send_text(PAGE, "text/html")
        if self.path == "/frame.js":
            return self._send_text(FRAME_JS.read_text(encoding="utf-8"),
                                   "text/javascript")
        if self.path == "/clip.mp4":
            return self._send_clip()
        self.send_error(404)

    def _send_text(self, body: str, content_type: str):
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_clip(self):
        size = os.path.getsize(self.clip)
        start, end, status = 0, size - 1, 200
        wanted = self.headers.get("Range")
        match = re.match(r"bytes=(\d*)-(\d*)", wanted or "")
        if match:
            status = 206
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:
                start = max(0, size - int(match.group(2)))
        end = min(end, size - 1)
        self.send_response(status)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with open(self.clip, "rb") as handle:
            handle.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = handle.read(min(1 << 16, remaining))
                if not chunk:
                    return
                try:
                    self.wfile.write(chunk)
                except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
                    return
                remaining -= len(chunk)


def clip_fps(ffmpeg: str | None, clip: Path) -> float:
    """The clip's frame rate off ffprobe; 60 (the extractor's CFR grid)
    when it cannot be read."""
    ffprobe = ffprobe_beside(ffmpeg)
    if not ffprobe:
        return 60.0
    try:
        out = subprocess.run(
            [ffprobe, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True, timeout=30, check=False,
            **quiet_spawn_kwargs())
        numerator, denominator = out.stdout.strip().splitlines()[0].split("/")
        return float(numerator) / float(denominator)
    except (OSError, subprocess.SubprocessError, ValueError, IndexError,
            ZeroDivisionError):
        return 60.0


def parse_slots(text: str | None, slot_count: int) -> list[int]:
    if not text:
        # A spread across the clip: the ends and a handful in between.
        picks = [1, slot_count // 4, slot_count // 2, 3 * slot_count // 4,
                 slot_count - 2]
        return sorted({max(0, min(slot_count - 1, one)) for one in picks})
    low, _, high = text.partition("-")
    first = int(low)
    last = int(high) if high else first
    return list(range(first, min(last, slot_count - 1) + 1))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--clip", default=None, help="path to the .mp4 "
                        "(default data/replay_buffer/clips/clip_attempt_N.mp4)")
    parser.add_argument("--slots", default=None,
                        help="a-b (video slots, 0-based); default: five spread "
                             "across the clip")
    args = parser.parse_args()

    clip = Path(args.clip or f"data/replay_buffer/clips/clip_attempt_{args.attempt}.mp4")
    sidecar = clip.with_suffix(".json")
    if not clip.exists() or not sidecar.exists():
        print(f"no cached clip + sidecar at {clip} -- open the attempt's replay first")
        return 2
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    frame_map = meta.get("frame_map") or []
    ffmpeg = bundled_ffmpeg() or shutil.which("ffmpeg")   # what main.py resolves
    start = video_start_of(ffmpeg, clip)
    fps = clip_fps(ffmpeg, clip)
    stored = meta.get("video_start_s")
    print(f"clip {clip.name}: {len(frame_map)} mapped slots, {fps:g} fps, "
          f"first video pts {start:.6f} s"
          + (f" (sidecar holds {stored:.6f})" if stored is not None
             else " (sidecar predates video_start_s; the view assumes 0)"))

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed in this venv -- uilab's driver brings it")
        return 2

    slots = parse_slots(args.slots, max(len(frame_map), 2))
    handler = functools.partial(type("Bound", (ClipHandler,), {"clip": clip}))
    # Threaded, or Chromium's open-ended range read of the clip (it stalls
    # once its buffer fills) holds the one server thread and frame.js
    # never arrives: the page's load event then never fires.
    class Server(socketserver.ThreadingTCPServer):
        daemon_threads = True
        allow_reuse_address = True

    with Server(("127.0.0.1", 0), handler) as server:
        port = server.server_address[1]
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(f"http://127.0.0.1:{port}/probe.html")
            page.wait_for_function("window.probeReady !== undefined", timeout=30000)
            page.evaluate("() => window.probeReady")
            page.wait_for_function("document.getElementById('v').duration > 0",
                                   timeout=30000)
            early_old = early_fixed = 0
            for slot in slots:
                result = page.evaluate(
                    "([slot, fps, start]) => window.probeSlot(slot, fps, start)",
                    [slot, fps, start])
                old, fixed = result["old"], result["fixed"]
                early_old += old["presented"] != slot
                early_fixed += fixed["presented"] != slot
                print(f"slot {slot:5d}: old seek {old['target']:.4f} -> "
                      f"mediaTime {old['mediaTime']:.4f} = slot {old['presented']:5d}"
                      f"{'' if old['presented'] == slot else '  <- ' + str(old['presented'] - slot):8s}"
                      f" | fixed seek {fixed['target']:.4f} -> "
                      f"mediaTime {fixed['mediaTime']:.4f} = slot {fixed['presented']:5d}"
                      f"{'' if fixed['presented'] == slot else '  <- ' + str(fixed['presented'] - slot)}")
            browser.close()
        server.shutdown()

    print(f"VERDICT: the pre-fix seek missed its slot on {early_old} of {len(slots)}; "
          f"the corrected seek missed on {early_fixed} of {len(slots)}"
          + ("" if early_fixed == 0 else
             " -- hop 6 is STILL broken: the browser presents a different picture "
             "than the timeline asked for, and no map or reader change can fix that"))
    return 0 if early_fixed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
