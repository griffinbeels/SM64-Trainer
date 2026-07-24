"""Build a failure compilation and run it as a background job.

Reuses ClipExtractor.extract per window (it already cuts one A/V-synced,
coverage-honouring clip), then ONE concat-filter ffmpeg pass scales+pads every
clip to a common canvas and re-encodes to a single MP4 — clips span sessions
and window resizes, so a `-c copy` concat would corrupt on a size mismatch
(the same frame-size hazard extract.py guards). The re-encode uses the shared
constant-quality target so the compilation is never softer than its source.

The job registry mirrors CompareService: start() returns a job id immediately,
a daemon thread does plan+build updating progress/message/state, status() is
polled. The output lives under save_root/compilations so the existing
/api/replay/reveal opens it with no new endpoint.
"""
import logging
import re
import shutil
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.paths import bundled_ffmpeg
from sm64_events.core.timefmt import format_igt
from sm64_events.memory.addresses import course_name, star_name
from sm64_events.replay.config import CLIP_MAXRATE, video_quality_args
from sm64_events.replay.service import _slug
from sm64_events.tracking.compilation import EntityRef, plan_compilation

log = logging.getLogger("sm64.compilation")

_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0
_DIMS_RE = re.compile(r"Video:.*?(\d{2,5})x(\d{2,5})")


@dataclass(frozen=True)
class CompilationResult:
    path: Path
    clip_count: int
    skipped_runtime: int
    finale_included: bool = False


class CompilationBuilder:
    """extract each spec -> concat/normalize into one mp4. ffmpeg-bound; the
    extractor + codec/fps are injected so tests can drive it without video."""

    def __init__(self, extractor, codec: str, fps: int, ffmpeg: str | None = None):
        self._extractor = extractor
        self._codec = codec
        self._fps = fps
        self._ffmpeg = ffmpeg or bundled_ffmpeg() or shutil.which("ffmpeg")

    def build(self, specs, ring, tmp_dir: Path, out_path: Path,
              resolve_saved, progress_cb) -> CompilationResult:
        if not self._ffmpeg:
            raise RuntimeError("ffmpeg binary not available for compilation")
        tmp_dir.mkdir(parents=True, exist_ok=True)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        clips: list[Path] = []
        finale_clip: Path | None = None
        skipped = 0
        total = len(specs)
        for i, spec in enumerate(specs):
            progress_cb(i / max(1, total), f"cutting {i + 1}/{total}")
            if spec.source == "saved":
                part = resolve_saved(spec.attempt_id)
                if part is None:
                    skipped += 1
                    continue
            else:
                part = tmp_dir / f"part_{i:03d}.mp4"
                try:
                    self._extractor.extract(ring, spec.span_start,
                                            spec.span_end, part)
                except ValueError:
                    skipped += 1        # ring shifted since planning — drop it
                    continue
            clips.append(part)
            if spec.kind == "finale":
                finale_clip = part
        if not clips:
            raise ValueError("nothing to compile — no footage available")
        progress_cb(0.95, "stitching")
        if len(clips) == 1:
            shutil.copy2(clips[0], out_path)     # single clip: no concat needed
        else:
            canvas = (self._probe_dims(finale_clip) if finale_clip else None)
            if canvas is None:
                canvas = self._probe_dims(clips[0])
            canvas = canvas or (1280, 960)
            self._concat_normalize(clips, canvas, out_path)
        return CompilationResult(path=out_path, clip_count=len(clips),
                                 skipped_runtime=skipped,
                                 finale_included=finale_clip is not None)

    def _probe_dims(self, path: Path):
        """(w, h) via ffmpeg's own probe (no ffprobe dependency): `-i` with no
        output exits non-zero and prints the stream line to stderr — expected,
        we only read the text. Returns None if unparseable (falls back to a
        default canvas)."""
        out = subprocess.run([self._ffmpeg, "-hide_banner", "-i", str(path)],
                             capture_output=True, creationflags=_NO_WINDOW)
        m = _DIMS_RE.search(out.stderr.decode("utf-8", "replace"))
        return (int(m.group(1)), int(m.group(2))) if m else None

    def _concat_normalize(self, clips, canvas, out_path: Path) -> None:
        w, h = canvas
        inputs: list[str] = []
        for p in clips:
            inputs += ["-i", str(p)]
        parts, labels = [], ""
        for idx in range(len(clips)):
            parts.append(
                f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={self._fps},"
                f"format=yuv420p[v{idx}]")
            parts.append(f"[{idx}:a]aresample=async=1[a{idx}]")
            labels += f"[v{idx}][a{idx}]"
        parts.append(f"{labels}concat=n={len(clips)}:v=1:a=1[v][a]")
        args = [self._ffmpeg, "-hide_banner", "-loglevel", "error", *inputs,
                "-filter_complex", ";".join(parts),
                "-map", "[v]", "-map", "[a]",
                "-c:v", self._codec,
                *video_quality_args(self._codec, "offline", CLIP_MAXRATE),
                "-c:a", "aac", "-ar", "48000",
                "-movflags", "+faststart", "-y", str(out_path)]
        try:
            subprocess.run(args, check=True, capture_output=True,
                           creationflags=_NO_WINDOW)
        except subprocess.CalledProcessError as exc:
            out_path.unlink(missing_ok=True)
            raise RuntimeError(
                "ffmpeg compilation failed: "
                + exc.stderr.decode("utf-8", "replace")[-500:]) from exc


def _entity_slug(tracker, identity: EntityRef) -> str:
    if identity.segment_id is not None:
        name = next((d.name for d in tracker.segment_defs
                     if d.id == identity.segment_id),
                    f"segment-{identity.segment_id}")
        return _slug(name) or f"segment-{identity.segment_id}"
    course = course_name(identity.course_id)
    star = star_name(identity.course_id, identity.star_id)
    return "-".join(p for p in (_slug(course), _slug(star)) if p) or "star"


class CompilationService:
    """Async job registry over CompilationBuilder (mirrors CompareService).

    Composes the ReplayService for its ring / extractor pads / saved-clip
    lookup so the compilation and a normal replay clip always cut from the same
    live buffer with the same padding.
    """

    def __init__(self, replay, tracker, builder: CompilationBuilder,
                 out_dir: Path):
        self.replay = replay
        self.tracker = tracker
        self.builder = builder
        self.out_dir = out_dir
        self._jobs: dict[str, dict] = {}

    def start(self, identity: EntityRef, x_before: float, y_after: float) -> str:
        if self.tracker.db is None:
            raise RuntimeError("database unavailable")
        if x_before < 0 or y_after < 0:
            raise ValueError("x_before and y_after must be >= 0")
        job_id = uuid.uuid4().hex
        self._jobs[job_id] = {"state": "running", "progress": 0.0,
                              "message": "planning", "result": None}
        threading.Thread(
            target=self._run_job, name="compilation", daemon=True,
            args=(job_id, identity, float(x_before), float(y_after))).start()
        return job_id

    def _run_job(self, job_id, identity, x_before, y_after) -> None:
        job = self._jobs[job_id]

        def progress(frac, msg):
            job["progress"] = frac
            job["message"] = msg

        try:
            ring = self.replay.recorder.ring
            plan = plan_compilation(
                list(self.tracker.db.attempts()), ring.coverage("video"),
                self.replay._saved_attempt_ids(), identity, x_before, y_after,
                self.replay.pre_pad_s, self.replay.post_pad_s)
            if not plan.specs:
                raise ValueError(
                    "nothing to compile — no failures with footage and no "
                    "available successful run")
            slug = _entity_slug(self.tracker, identity)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            out_path = self.out_dir / f"compilation_{slug}_{stamp}_{job_id[:8]}.mp4"
            tmp_dir = self.out_dir / f".build_{job_id}"
            try:
                res = self.builder.build(plan.specs, ring, tmp_dir, out_path,
                                         self.replay.find_saved, progress)
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            job["result"] = {
                "path": str(out_path),
                "clip_count": res.clip_count,
                "skipped": plan.aged_out + res.skipped_runtime,
                "no_finale": plan.no_finale or not res.finale_included,
                "finale_time": (format_igt(plan.finale_frames)
                                if plan.finale_frames is not None
                                and res.finale_included else None)}
            job["progress"] = 1.0
            job["message"] = "done"
            job["state"] = "done"
        except Exception as e:
            log.exception("compilation failed")
            job["state"] = "error"
            job["message"] = str(e)

    def status(self, job_id: str) -> dict:
        job = self._jobs.get(job_id)
        if job is None:
            raise LookupError("no such compilation job")
        return dict(job)   # shallow copy: callers never mutate live job state
