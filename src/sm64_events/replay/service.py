"""Attempt -> span -> clip -> save.

Error taxonomy matches server/api.py:
  LookupError  -> 404 (no such attempt/clip)
  ValueError   -> 409 (no footage / span too short)
  RuntimeError -> 503 (db unavailable)
Anything else (e.g. codec failure on a corrupt segment) is a genuine 500 —
extract.py already guarantees no partial file survives those.
"""
import json
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from sm64_events.core.timefmt import GAME_FPS, format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.replay.config import (ReplayConfig, save_settings,
                                       validate_settings)

_CLIP_NAME = "clip_attempt_{id}.mp4"
# fullmatch pattern — rejects traversal, wrong extension, empty id
_CLIP_RE = re.compile(r"clip_attempt_\d+\.mp4")


def _parse_utc(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _open_explorer_select(path: Path) -> None:
    """Open File Explorer with the file pre-selected. explorer.exe wants the
    /select,"path" form as ONE argument string; it always exits 1, so this is
    fire-and-forget."""
    import subprocess
    subprocess.Popen(f'explorer /select,"{path}"')


def _slug(s: str) -> str:
    """Lower-case alphanumeric slug; apostrophes are removed (possessives stay
    joined), other non-alnum runs collapse to a single dash."""
    s = s.replace("'", "")  # "Whomp's" -> "Whomps" (don't insert a dash)
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def slug_filename(a, course: str, star: str) -> str:
    """Human-readable filename for a saved clip.

    IGT display format from format_igt is M'SS"CC (Usamune style).
    We replace ' -> m and " -> s so the filename is filesystem-safe:
    e.g. 0'11"43 -> 0m11s43. Segment attempts are RTA-only (spec
    2026-06-11): their time gets an explicit -rta marker.

    Parts list drops empty slugs so a segment attempt (empty star part)
    produces no double underscore.
    """
    if a.igt_frames is not None:
        igt = format_igt(a.igt_frames).replace("'", "m").replace('"', "s")
    elif a.rta_frames is not None:
        igt = format_igt(a.rta_frames).replace("'", "m").replace('"', "s") + "-rta"
    else:
        igt = "no-igt"
    suffix = "" if a.outcome == "success" else f"_{a.outcome}"
    parts = [p for p in (_slug(course), _slug(star)) if p]
    return f"attempt_{a.id:04d}_{'_'.join(parts)}_{igt}{suffix}.mp4"


class ReplayService:
    """Orchestrates replay operations for attempts.

    Public surface (consumed by Task 12 router):
      status()           -> dict
      view(attempt_id)   -> dict  {clip_url, duration_s, truncated}
      save(attempt_id)   -> dict  {path, truncated}
      reveal(path)       -> None  (opens Explorer with the saved file selected)
      clip_path(name)    -> Path  (validated; raises LookupError on bad name)
      lifecycle_start()
      lifecycle_stop()
    """

    def __init__(self, cfg: ReplayConfig, recorder, extractor, tracker,
                 revealer=None):
        self.cfg = cfg
        self.recorder = recorder
        self.extractor = extractor
        self.tracker = tracker
        self._revealer = revealer or _open_explorer_select
        # clips_dir lives inside scratch_dir; it is created in lifecycle_start
        # AFTER recorder.start() so any future recursive wipe by the recorder
        # doesn't evict a directory we created first.
        self.clips_dir = cfg.scratch_dir / "clips"
        # Pads are settings-mutable (cfg is frozen): these live values are
        # what _span uses; update_settings replaces them.
        self.pre_pad_s = cfg.pre_pad_s
        self.post_pad_s = cfg.post_pad_s

    # -- queries -------------------------------------------------------------

    def status(self) -> dict:
        return {"enabled": True, **self.recorder.status()}

    def settings(self) -> dict:
        """Storage limits + where the bytes are. saved_bytes walks save_root
        on demand (panel-open frequency) — deliberately NOT part of the 5 s
        status poll."""
        root = self.cfg.save_root
        saved = (sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
                 if root.exists() else 0)
        return {"retention_s": self.recorder.ring.retention_s,
                "max_buffer_bytes": self.recorder.ring.max_bytes,
                "pre_pad_s": self.pre_pad_s,
                "post_pad_s": self.post_pad_s,
                "save_root": str(root),
                "saved_bytes": saved}

    def update_settings(self, retention_s: float | None,
                        max_buffer_bytes: int,
                        pre_pad_s: float | None = None,
                        post_pad_s: float | None = None) -> dict:
        """Validate -> persist -> apply live (ring evicts immediately; pads
        affect the next view(); the recorder's idle threshold follows the
        padding window). None pads = keep current. Persist before apply so
        a write failure can't leave limits applied but not durable."""
        pre = self.pre_pad_s if pre_pad_s is None else float(pre_pad_s)
        post = self.post_pad_s if post_pad_s is None else float(post_pad_s)
        validate_settings(retention_s, max_buffer_bytes, pre, post)
        save_settings(self.cfg.settings_path, retention_s, max_buffer_bytes,
                      pre, post)
        self.recorder.ring.set_limits(retention_s, max_buffer_bytes)
        self.pre_pad_s, self.post_pad_s = pre, post
        self.recorder.set_idle_after(pre + post)
        return self.settings()

    def _attempt(self, attempt_id: int):
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        for a in self.tracker.db.attempts():
            if a.id == attempt_id:
                return a
        raise LookupError(f"no attempt {attempt_id}")

    def _span(self, a) -> tuple[datetime, datetime]:
        start = _parse_utc(a.started_utc) - timedelta(seconds=self.pre_pad_s)
        end = _parse_utc(a.ended_utc) + timedelta(seconds=self.post_pad_s)
        return start, end

    # -- commands ------------------------------------------------------------

    def view(self, attempt_id: int) -> dict:
        """Return clip metadata, extracting and caching on first call.

        Cache key: clip file + JSON sidecar both exist. Re-extraction is
        triggered only when either is missing (e.g. scratch_dir wiped on
        restart — intentional; clip cache dies with the buffer).
        """
        a = self._attempt(attempt_id)
        name = _CLIP_NAME.format(id=attempt_id)
        clip = self.clips_dir / name
        meta = clip.with_suffix(".json")
        if not (clip.exists() and meta.exists()):
            start, end = self._span(a)
            self._wait_for_tail(end)
            res = self.extractor.extract(self.recorder.ring, start, end, clip)
            meta.write_text(json.dumps(
                {"duration_s": res.duration_s, "truncated": res.truncated}))
        m = json.loads(meta.read_text())
        # fps = encoded rate (CFR); game_fps = SM64 logic rate — the
        # frame-step UI steps in GAME frames: each spans two encoded
        # frames, so stepping 1/fps changed the image only every 2nd press
        # (live-reported 2026-06-12).
        return {"clip_url": f"/api/replay/clips/{name}",
                "duration_s": m["duration_s"], "truncated": m["truncated"],
                "fps": self.cfg.fps, "game_fps": GAME_FPS}

    def _wait_for_tail(self, end_utc: datetime) -> None:
        """Bounded wait: a click right after the event can outrace the last
        segment's rotation (spec: post-padding race)."""
        deadline = time.monotonic() + self.cfg.extract_wait_s
        while time.monotonic() < deadline:
            if not self.recorder.status().get("recording"):
                return
            cov = self.recorder.ring.coverage("video")
            if cov is not None and cov[1] >= end_utc:
                return
            time.sleep(0.25)

    def save(self, attempt_id: int) -> dict:
        """Persist a clip to the permanent save tree (date/session/).

        Calls view() first so the clip is always extracted before copying;
        the view() result is cached so a second view() call is a no-op.
        """
        a = self._attempt(attempt_id)
        self.view(attempt_id)  # ensure clip exists (cached when already cut)
        clip = self.clips_dir / _CLIP_NAME.format(id=attempt_id)
        ended_local = _parse_utc(a.ended_utc).astimezone()  # folder by local date
        dest_dir = (self.cfg.save_root / ended_local.strftime("%Y-%m-%d")
                    / f"session_{a.session_id}")
        dest_dir.mkdir(parents=True, exist_ok=True)
        if a.segment_id is not None:
            c_name = next((d.name for d in self.tracker.segment_defs
                           if d.id == a.segment_id),
                          f"segment-{a.segment_id}")
            s_name = ""
        else:
            c_name = course_name(a.course_id) if a.course_id is not None else "no-course"
            s_name = (star_name(a.course_id, a.star_id)
                      if a.star_id is not None and a.course_id is not None else "no-star")
        dest = dest_dir / slug_filename(a, c_name, s_name)
        shutil.copy2(clip, dest)
        meta = clip.with_suffix(".json")
        m = json.loads(meta.read_text())
        return {"path": str(dest), "truncated": m["truncated"]}

    def reveal(self, path_str: str) -> None:
        """Open Explorer with a SAVED clip selected. Only paths inside
        save_root are allowed — the path comes back from our own save()
        response, but the endpoint is reachable by anything on localhost."""
        root = self.cfg.save_root.resolve()
        p = Path(path_str).resolve()
        if not p.is_relative_to(root) or not p.is_file():
            raise LookupError("no such saved replay")
        self._revealer(p)

    def clip_path(self, name: str) -> Path:
        """Return validated Path for serving a clip.

        fullmatch rejects directory traversal and anything that isn't
        exactly one of our clip names (e.g. wrong extension, empty id).
        """
        if not _CLIP_RE.fullmatch(name):
            raise LookupError("no such clip")
        p = self.clips_dir / name
        if not p.exists():
            raise LookupError("no such clip")
        return p

    # -- lifecycle (called from app lifespan) --------------------------------

    def lifecycle_start(self) -> None:
        # Start recorder first; it may wipe scratch_dir contents on init.
        # clips_dir is created after so a future recursive wipe doesn't
        # evict a directory we made first.
        self.recorder.start()
        self.clips_dir.mkdir(parents=True, exist_ok=True)

    def lifecycle_stop(self) -> None:
        self.recorder.stop()
