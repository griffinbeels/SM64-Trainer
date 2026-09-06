"""Shared on-demand recording preparation, independent of PBs and comparison rows."""
import logging
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlencode, urlsplit

from sm64_events.core.recording_url import (
    media_identity, start_seconds, validate_recording_url, youtube_id,
)

log = logging.getLogger("sm64.recordings")


def preview_recording(url: str) -> dict:
    """Only a fixed provider endpoint, never fetch arbitrary pasted page URLs."""
    import json
    from urllib.request import Request, urlopen
    video = youtube_id(url)
    if not video:
        return {}
    endpoint = "https://www.youtube.com/oembed?" + urlencode(
        {"url": media_identity(url), "format": "json"})
    with urlopen(Request(endpoint, headers={"User-Agent": "SM64Trainer"}), timeout=5) as response:
        payload = json.loads(response.read(64 * 1024))
    return {"title": payload.get("title"),
            "thumbnail": f"https://i.ytimg.com/vi/{video}/hqdefault.jpg"}


def frame_step(path) -> float | None:
    """Check encoded presentation timestamps, not an assumed game clock.

    Packet timestamps avoid decoding every pixel. Varying or missing timestamps
    omit stepping. Cap the inspection for very long recordings; playback remains.
    """
    import av
    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        stamps = []
        for packet in container.demux(stream):
            if packet.pts is not None:
                stamps.append(packet.pts * packet.time_base)
            if len(stamps) > 1_000_000:
                return None
    if len(stamps) < 3:
        return None
    stamps.sort()
    period = stamps[1] - stamps[0]
    if period <= 0 or period > 1 or period < 1 / 240:
        return None
    # Containers sometimes quantize fractional-rate frames to adjacent ticks.
    if any(abs((after - before) - period) > period / 100
           for before, after in zip(stamps, stamps[1:])):
        return None
    return float(period)


class RecordingMedia:
    def __init__(self, importer, *, preview_probe=preview_recording,
                 frame_probe=frame_step):
        self.importer = importer
        self._preview_probe = preview_probe
        self._frame_probe = frame_probe
        self._lock = threading.RLock()
        self._slots = threading.BoundedSemaphore(2)
        self._jobs = OrderedDict()
        self._previews = OrderedDict()
        self._frames = OrderedDict()

    def preview(self, url: str) -> dict:
        url = validate_recording_url(url)
        key = media_identity(url)
        with self._lock:
            cached = self._previews.get(key)
        if cached and time.monotonic() - cached[0] < 300:
            details = cached[1]
        else:
            try:
                details = self._preview_probe(url)
            except Exception:
                log.debug("recording preview unavailable", exc_info=True)
                details = {}
            with self._lock:
                self._previews[key] = (time.monotonic(), details)
                if len(self._previews) > 128:
                    self._previews.popitem(last=False)
        video = youtube_id(url)
        return {"url": url, "site": "YouTube" if video else urlsplit(url).hostname,
                "title": details.get("title"),
                "thumbnail": details.get("thumbnail") or (
                    f"https://i.ytimg.com/vi/{video}/hqdefault.jpg" if video else None)}

    def status(self, url: str) -> dict:
        url = validate_recording_url(url)
        result = {"url": url, "start_s": start_seconds(url)}
        key = media_identity(url)
        name = self.importer.cached_name(url) if self.importer else None
        with self._lock:
            job = self._jobs.get(key)
            if job and job["state"] == "running":
                result.update({field: value for field, value in job.items()
                               if field != "thread"})
            elif name:
                result.update(state="ready", clip_url=f"/api/media/cache/{name}")
                period = self._frames.get(name)
                if period:
                    result["frame_step_s"] = period
            else:
                if job and job["state"] == "ready":
                    job = None  # deleted cache: a new play prepares it again
                result.update({field: value for field, value in job.items()
                               if field != "thread"} if job else {"state": "missing"})
        return result

    def start(self, url: str, retry=False) -> dict:
        url = validate_recording_url(url)
        key = media_identity(url)
        with self._lock:
            current = self.status(url)
            if current["state"] == "ready":
                # A cache loaded by Compare can acquire stepping on first play.
                name = self.importer.cached_name(url)
                if (name not in self._frames
                        and sum(job["state"] == "running" for job in self._jobs.values()) < 8):
                    self._launch(key, url, existing=name)
                    return self.status(url)
                return current
            if current["state"] == "running" or (current["state"] == "error" and not retry):
                return current
            if self.importer is None:
                return {**current, "state": "error",
                        "error": "Local video preparation is unavailable. Watch the original recording below."}
            if sum(job["state"] == "running" for job in self._jobs.values()) >= 8:
                return {**current, "state": "error",
                        "error": "Other recordings are preparing. Try again shortly."}
            self._launch(key, url)
            return self.status(url)

    def _launch(self, key, url, existing=None):
        job = {"state": "running", "progress": 0, "message": "Preparing recording"}
        self._jobs[key] = job
        while len(self._jobs) > 32:
            completed = next((item for item, value in self._jobs.items()
                              if value["state"] != "running"), None)
            if completed is None:
                break
            del self._jobs[completed]
        thread = threading.Thread(target=self._prepare, args=(key, url, existing),
                                  name="recording-media", daemon=True)
        job["thread"] = thread
        thread.start()

    def _prepare(self, key, url, existing):
        with self._slots:
            try:
                def progress(fraction, message):
                    with self._lock:
                        self._jobs[key].update(progress=fraction, message=message)
                name = existing or self.importer.import_video("youtube", url, progress)
                try:
                    period = self._frame_probe(self.importer.cache_path(name))
                except Exception:
                    log.debug("frame timing unavailable for %s", name, exc_info=True)
                    period = None
                with self._lock:
                    self._frames[name] = period
                    if len(self._frames) > 256:
                        self._frames.popitem(last=False)
                    self._jobs[key].update(state="ready", progress=1,
                                            message="Recording ready")
            except Exception as error:
                log.warning("recording preparation failed: %s", error)
                with self._lock:
                    self._jobs[key].update(state="error",
                        error="Could not prepare this video. Watch the original or retry.",
                        message="Recording unavailable locally")

    def cache_path(self, name):
        if not self.importer or not re.fullmatch(r"[0-9a-f]{16}\.mp4", name):
            raise LookupError("No such recording.")
        path = self.importer.cache_path(name)
        if not path.is_file():
            raise LookupError("No such recording.")
        return path

    def wait_for_idle(self, timeout=5):
        """Bounded join for shutdown/tests; does not hold a worker's state lock."""
        deadline = time.monotonic() + timeout
        with self._lock:
            threads = [job["thread"] for job in self._jobs.values() if "thread" in job]
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
