"""Comparison-video import: YouTube (yt-dlp) or local file -> ONE normalized
mp4 in the content cache.

Every comparison becomes a plain <video> the frame-stepping player can drive
exactly like a replay clip. Normalization (ffmpeg, the bundled binary) forces
H.264 <=720p + faststart so seeking / single-frame stepping is reliable
regardless of the source's codec, resolution, or container.

Dedup = "load once": the cache file is named by a hash of source_ref
(cache_name_for), so importing the same URL/path twice returns the existing
file without re-downloading or re-encoding. The downloader (yt-dlp) and the
ffmpeg runner are injected so tests never touch the network or a codec.
"""
import hashlib
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from sm64_events.tracking.comparisons import cache_name_for

log = logging.getLogger("sm64.compare")


def _default_downloader(source_ref: str, dest_dir: Path) -> Path:
    """Fetch a YouTube URL to dest_dir at <=720p; return the downloaded file."""
    import yt_dlp
    out_tmpl = str(dest_dir / "src.%(ext)s")
    opts = {"format": "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
            "outtmpl": out_tmpl, "quiet": True, "noprogress": True,
            "merge_output_format": "mp4"}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([source_ref])
    files = list(dest_dir.glob("src.*"))
    if not files:
        raise RuntimeError("yt-dlp produced no file")
    # merge_output_format leaves exactly one src.* (the muxed result); the
    # intermediate stream files are cleaned up by yt-dlp, so files[0] is it.
    return files[0]


def _default_runner(cmd: list[str]) -> None:
    try:
        subprocess.run(cmd, check=True, capture_output=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.CalledProcessError as e:
        # capture_output swallows ffmpeg's stderr; log it and fold the tail into
        # the message so the RuntimeError names the real reason, not just a code.
        stderr = e.stderr.decode(errors="replace") if e.stderr else ""
        log.error("ffmpeg failed (exit %s): %s", e.returncode, stderr)
        lines = [line for line in stderr.strip().splitlines() if line.strip()]
        tail = lines[-1] if lines else f"exit {e.returncode}"
        raise RuntimeError(f"ffmpeg exit {e.returncode}: {tail}") from e


class VideoImporter:
    def __init__(self, cache_dir: Path, ffmpeg: str, *,
                 downloader=None, runner=None):
        self.cache_dir = Path(cache_dir)
        self.ffmpeg = ffmpeg
        self._download = downloader or _default_downloader
        self._run = runner or _default_runner

    def cache_path(self, cache_name: str) -> Path:
        return self.cache_dir / cache_name

    def _normalize_to_cache(self, raw: Path, name: str, progress_cb=None) -> None:
        """ffmpeg-normalize `raw` and publish it into the cache as `name`.
        Normalize INTO cache_dir (not TEMP): tmp_out and dest then share one
        filesystem, so publishing is an atomic os.replace. Landing in TEMP
        would force shutil's copy+unlink fallback across filesystems, whose
        interruption leaves a truncated file at dest that dedup (dest.exists)
        would forever trust as a valid cache hit."""
        if progress_cb:
            progress_cb(0.7, "normalizing")
        dest = self.cache_path(name)
        tmp_out = self.cache_dir / f".tmp-{name}"
        cmd = [self.ffmpeg, "-y", "-i", str(raw),
               "-vf", "scale=-2:min(720\\,ih)", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "20",
               "-movflags", "+faststart", "-c:a", "aac", str(tmp_out)]
        try:
            try:
                self._run(cmd)
            except Exception as e:
                raise RuntimeError(f"normalize failed: {e}") from e
            if not tmp_out.exists():
                raise RuntimeError("normalize produced no output")
            os.replace(tmp_out, dest)       # atomic publish (same filesystem)
        except Exception:
            tmp_out.unlink(missing_ok=True)  # never leave a partial dedup would trust
            raise

    def import_video(self, source_kind: str, source_ref: str,
                     progress_cb=None) -> str:
        if source_kind not in ("youtube", "file"):
            raise ValueError(f"unknown source_kind {source_kind!r}")
        name = cache_name_for(source_ref)
        dest = self.cache_path(name)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():                       # dedup / load-once
            if progress_cb:
                progress_cb(1.0, "already loaded")
            return name

        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            if source_kind == "file":
                src = Path(source_ref)
                if not src.is_file():
                    raise LookupError(f"no such file: {source_ref}")
                if progress_cb:
                    progress_cb(0.3, "copying")
                raw = tdp / f"src{src.suffix or '.mp4'}"
                shutil.copy2(src, raw)
            else:
                if progress_cb:
                    progress_cb(0.1, "downloading")
                try:
                    raw = self._download(source_ref, tdp)
                except Exception as e:
                    raise RuntimeError(f"download failed: {e}") from e
            self._normalize_to_cache(raw, name, progress_cb)
        if progress_cb:
            progress_cb(1.0, "done")
        return name

    def import_bytes(self, data: bytes, progress_cb=None) -> str:
        """Import raw uploaded video bytes. Content-addressed (dedup by
        CONTENT, not filename): the same file uploaded twice reuses the
        cache ("load once")."""
        name = hashlib.sha1(data).hexdigest()[:16] + ".mp4"
        dest = self.cache_path(name)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        if dest.exists():                       # dedup / load-once
            if progress_cb:
                progress_cb(1.0, "already loaded")
            return name
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "upload.bin"
            raw.write_bytes(data)
            self._normalize_to_cache(raw, name, progress_cb)
        if progress_cb:
            progress_cb(1.0, "done")
        return name
