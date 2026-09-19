"""Shrink a saved replay without changing what any consumer of it sees.

A saved replay's frame map, picture states and input timeline are lists
indexed by PICTURE POSITION, and `frame_times` is each picture's own time
(the sidecar, written at extraction). None of them reads the MP4. So a
re-encode leaves every view payload untouched provided the new file holds
the same pictures, in the same order, at the same ticks, for the same length,
under the same audio -- and that is what `prove` checks before anything is
replaced. A proof that fails keeps the original and says why.

Measured 2026-09-18 on two of his saved replays: hardware AV1 lands near a
quarter of the saved size at VMAF ~99. H.264 WITH picture reordering
(B-frames) passed every ffprobe check and still could not present the clip's
last 11 pictures in Chromium, so every candidate runs with reordering off --
which `picture_duration_filter` requires anyway. ffprobe agreeing is therefore
necessary, not sufficient: a new codec row earns its place with the
every-slot browser seek check (`tools/probe_clip_seek.py`).

Two steps, because a player may be reading the file when the encode ends:

1. `shrink` writes `<clip>.mp4.compressed.tmp` and, once proven, the
   `<clip>.mp4.compressed.json` that says so. Neither name matches the
   `attempt_*.mp4` glob that indexes the save tree, and the compressor keeps
   both in one hidden folder at the top of that tree, never beside the clip.
2. `adopt` swaps a proven file under the clip's own name and records the
   sidecar's `media` block. It runs when nobody has asked for the clip's
   bytes lately, when the app closes, and at session start.

A save leaves a job note in that folder before any work starts, so a close
that lands mid-encode (or before the worker reached the clip) still leaves the
job owed, and the next session start runs it.

The `media` block is also the manifest a later uploader/downloader verifies
with the same `fingerprint`: the file's sha256, the picture count and a
digest of the picture ticks, and a digest of the decoded audio.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from collections import deque
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.childproc import quiet_spawn_kwargs
from sm64_events.replay.config import ARCHIVE_CODECS, CLIP_MAXRATE, video_quality_args
from sm64_events.replay.extract import ffprobe_beside, frame_times_of
from sm64_events.replay.media import MEDIA_HZ, picture_duration_filter
from sm64_events.replay.publication import atomic_json

log = logging.getLogger("sm64.replay")

MEDIA_VERSION = 1
# Working files: an ordinary MP4, its proof, and the note that a job is owed,
# under names that do NOT end in .mp4 because every `attempt_*.mp4` in the
# save tree is a saved replay. The compressor keeps them in ONE hidden folder
# at the top of the save tree, never beside the clip: the folder he opens
# after a session holds his replays and nothing else ("i still see the temp
# files", 2026-09-19, after a save followed at once by closing the app).
WORK_DIR = ".compressing"
STAGED_SUFFIX = ".compressed.tmp"    # <clip>.mp4.compressed.tmp
PROOF_SUFFIX = ".compressed.json"    # <clip>.mp4.compressed.json
JOB_SUFFIX = ".job.json"             # <clip>.mp4.job.json: {"clip": path under the save tree}
SETTLED_STATES = ("compressed", "kept_original", "adopting")
# A re-encode that is structurally perfect and visually broken (a driver
# fault, a green picture) would destroy a PB for good. SSIM against the saved
# clip is the floor under that; measured archive encodes sit at 0.98-0.99.
MIN_SSIM = 0.95
# Not worth a generation of loss for less than this.
MIN_SAVING = 0.10
_ENCODE_TIMEOUT_S = 1800
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)


class Unproven(Exception):
    """The re-encode is not interchangeable with the saved clip."""


@dataclass(frozen=True)
class Fingerprint:
    bytes: int
    sha256: str
    pictures: int
    picture_times_sha256: str
    audio_sha256: str | None
    end_ticks: int

    def as_dict(self) -> dict:
        return {"bytes": self.bytes, "sha256": self.sha256, "pictures": self.pictures,
                "picture_times_sha256": self.picture_times_sha256,
                "audio_sha256": self.audio_sha256, "end_ticks": self.end_ticks}


def _spawn_kwargs() -> dict:
    kwargs = quiet_spawn_kwargs()
    if _BELOW_NORMAL:
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _BELOW_NORMAL
    return kwargs


_CHILDREN: set[subprocess.Popen] = set()
_CHILDREN_GUARD = threading.Lock()


def run_child(args, *, timeout, **kwargs) -> subprocess.CompletedProcess:
    """`subprocess.run`, except the child can be ended from another thread.
    An encode left running after the app closed would keep a core busy and
    finish a file nobody is waiting for."""
    text = kwargs.pop("text", False)
    kwargs.pop("check", None)
    on_line = kwargs.pop("on_line", None)
    if kwargs.pop("capture_output", False):
        kwargs.update(stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = subprocess.Popen(args, text=text, **kwargs)
    with _CHILDREN_GUARD:
        _CHILDREN.add(child)
    try:
        if on_line is None:
            out, err = child.communicate(timeout=timeout)
        else:
            out, err = _follow(child, on_line, timeout)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise
    finally:
        with _CHILDREN_GUARD:
            _CHILDREN.discard(child)
    return subprocess.CompletedProcess(args, child.returncode, out, err)


def _follow(child: subprocess.Popen, on_line, timeout: float):
    """Hand each stdout line to `on_line` as it arrives (ffmpeg's `-progress`
    feed) while stderr drains on its own thread, so neither pipe can fill."""
    errors: list = []
    drain = threading.Thread(target=lambda: errors.append(child.stderr.read()), daemon=True)
    drain.start()
    deadline = time.monotonic() + timeout
    for line in child.stdout:
        with suppress(Exception):   # a progress display must never cost the encode
            on_line(line if isinstance(line, str) else line.decode("utf-8", "replace"))
        if time.monotonic() > deadline:
            raise subprocess.TimeoutExpired(child.args, timeout)
    child.wait(timeout=max(1.0, deadline - time.monotonic()))
    drain.join(timeout=5)
    return None, (errors[0] if errors else None)


def progress_args() -> list[str]:
    return ["-progress", "pipe:1", "-nostats"]


def progress_of(line: str, pictures: int) -> float | None:
    """ffmpeg's `frame=<n>` as a fraction of the clip's pictures. Counted in
    pictures, not seconds: a picture-feed clip is variable-rate, and the
    comparison pass renumbers its timestamps."""
    key, _, value = line.strip().partition("=")
    if key != "frame" or pictures <= 0:
        return None
    with suppress(ValueError):
        return min(1.0, max(0.0, int(value) / pictures))
    return None


def end_children() -> None:
    with _CHILDREN_GUARD:
        running = list(_CHILDREN)
    for child in running:
        with suppress(OSError):
            child.kill()


def _hide(folder: Path) -> None:
    """Explorer's hidden flag, where there is one. Cosmetic: never raises."""
    with suppress(Exception):
        import ctypes
        ctypes.windll.kernel32.SetFileAttributesW(str(folder), 0x2)  # FILE_ATTRIBUTE_HIDDEN


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def picture_ticks(ffmpeg: str, clip: Path) -> list[int]:
    """Every presented picture's time on the 90 kHz media clock."""
    times = frame_times_of(ffmpeg, clip)
    if not times:
        raise Unproven(f"picture times unreadable: {clip.name}")
    return [round(at * MEDIA_HZ) for at in times]


def _probe(ffmpeg: str, clip: Path, stream: str, entries: str) -> list[str]:
    ffprobe = ffprobe_beside(ffmpeg)
    if not ffprobe:
        raise Unproven("no ffprobe beside ffmpeg")
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", stream, "-show_entries", entries,
         "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True, timeout=120, check=False, **_spawn_kwargs())
    if out.returncode:
        raise Unproven(f"ffprobe failed on {clip.name}: {out.stderr[-200:].strip()}")
    return [line.strip().rstrip(",") for line in out.stdout.splitlines() if line.strip()]


def end_ticks(ffmpeg: str, clip: Path) -> int:
    """Where the last picture stops being shown: the video track's own end."""
    rows = _probe(ffmpeg, clip, "v:0", "stream=start_time,duration")
    try:
        start, duration = (float(part) for part in rows[0].split(",")[:2])
    except (IndexError, ValueError) as unreadable:
        raise Unproven(f"video length unreadable: {clip.name}") from unreadable
    return round((max(start, 0.0) + duration) * MEDIA_HZ)


def end_in_stream_units(ffmpeg: str, clip: Path) -> int:
    """The same end, counted in the video track's OWN time base -- the unit
    the encoder's packets carry under `-enc_time_base demux`, and so the unit
    `picture_duration_filter` compares against. A saved replay's is 1/90000;
    nothing here may assume it (a 1/15360 clip held its last picture for 11 s
    when the end was passed in 90 kHz ticks, which the proof refused)."""
    rows = _probe(ffmpeg, clip, "v:0", "stream=time_base,start_time,duration")
    try:
        base, start, duration = rows[0].split(",")[:3]
        numerator, denominator = (int(part) for part in base.split("/"))
        return round((max(float(start), 0.0) + float(duration)) * denominator / numerator)
    except (IndexError, ValueError, ZeroDivisionError) as unreadable:
        raise Unproven(f"video time base unreadable: {clip.name}") from unreadable


def audio_digest(ffmpeg: str, clip: Path) -> str | None:
    """Digest of the DECODED audio as presented: every frame's time, length
    and samples. None when the clip has no audio track."""
    if not _probe(ffmpeg, clip, "a:0", "stream=index"):
        return None
    out = run_child(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-map", "0:a:0", "-f", "framemd5", "-"],
        capture_output=True, timeout=600, **_spawn_kwargs())
    if out.returncode:
        raise Unproven(f"audio unreadable: {clip.name}")
    rows = [line for line in out.stdout.splitlines() if not line.startswith(b"#")]
    return hashlib.sha256(b"\n".join(rows)).hexdigest()


def fingerprint(ffmpeg: str, clip: Path) -> Fingerprint:
    """What a copy of this clip must reproduce to be the same replay."""
    ticks = picture_ticks(ffmpeg, clip)
    times = hashlib.sha256(",".join(map(str, ticks)).encode()).hexdigest()
    return Fingerprint(clip.stat().st_size, file_sha256(clip), len(ticks), times,
                       audio_digest(ffmpeg, clip), end_ticks(ffmpeg, clip))


def picture_similarity(ffmpeg: str, clip: Path, reference: Path, on_line=None) -> float:
    """Mean SSIM, pictures paired by POSITION (their times are proven apart)."""
    graph = ("[0:v]setpts=N,format=yuv420p[a];[1:v]setpts=N,format=yuv420p[b];"
             "[a][b]ssim")
    follow = {"on_line": on_line} if on_line is not None else {}
    out = run_child(
        [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info",
         *(progress_args() if on_line is not None else []),
         "-i", str(clip), "-i", str(reference), "-lavfi", graph, "-f", "null", "-"],
        capture_output=True, text=True, timeout=_ENCODE_TIMEOUT_S, **follow,
        **_spawn_kwargs())
    found = re.findall(r"SSIM .*All:([0-9.]+)", out.stderr)
    if out.returncode or not found:
        raise Unproven("picture comparison failed")
    return float(found[-1])


def prove(ffmpeg: str, saved: Path, staged: Path, original: Fingerprint,
          sidecar_times: list | None, report=None) -> tuple[Fingerprint, float]:
    """Raise Unproven unless `staged` can stand in for `saved` everywhere.
    `report(fraction)` follows the proof; the picture comparison is most of it."""
    def told(fraction: float) -> None:
        if report is not None:
            report(fraction)

    told(0.0)
    candidate = fingerprint(ffmpeg, staged)
    told(0.2)
    if candidate.pictures != original.pictures:
        raise Unproven(f"picture count {original.pictures} -> {candidate.pictures}")
    if candidate.picture_times_sha256 != original.picture_times_sha256:
        raise Unproven("a picture moved in time")
    if sidecar_times is not None:
        expected = [round(at * MEDIA_HZ) for at in sidecar_times]
        if expected != picture_ticks(ffmpeg, staged):
            raise Unproven("pictures no longer sit on the sidecar's frame times")
    if candidate.audio_sha256 != original.audio_sha256:
        raise Unproven("the audio changed")
    if abs(candidate.end_ticks - original.end_ticks) > 1:
        raise Unproven(f"clip length {original.end_ticks} -> {candidate.end_ticks} ticks")
    if candidate.bytes > original.bytes * (1 - MIN_SAVING):
        raise Unproven(f"not smaller: {original.bytes} -> {candidate.bytes} bytes")
    def compared(line: str) -> None:
        seen = progress_of(line, original.pictures)
        if seen is not None:
            told(0.2 + 0.8 * seen)

    similarity = picture_similarity(ffmpeg, staged, saved,
                                    compared if report is not None else None)
    told(1.0)
    if similarity < MIN_SSIM:
        raise Unproven(f"pictures differ too much (SSIM {similarity:.4f})")
    return candidate, similarity


def encode_args(ffmpeg: str, saved: Path, staged: Path, codec: str, end: int) -> list[str]:
    return [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(saved), "-map", "0:v:0", "-map", "0:a:0?", "-c:a", "copy",
            "-c:v", codec, *video_quality_args(codec, "archive", CLIP_MAXRATE),
            # No reordering: see the module docstring. Every picture keeps its
            # own tick, and the last one is held to the original's end.
            "-bf", "0", "-g", "60", "-fps_mode", "passthrough",
            "-enc_time_base", "demux", "-bsf:v", picture_duration_filter(end),
            "-movflags", "+faststart", "-f", "mp4", str(staged)]


def _tell(report, stage: str, fraction: float | None) -> None:
    if fraction is not None:
        report(stage, fraction)


def staged_path(saved: Path, work: Path | None = None) -> Path:
    return (work or saved.parent) / (saved.name + STAGED_SUFFIX)


def proof_path(saved: Path, work: Path | None = None) -> Path:
    return (work or saved.parent) / (saved.name + PROOF_SUFFIX)


def job_path(saved: Path, work: Path) -> Path:
    return work / (saved.name + JOB_SUFFIX)


def shrink(ffmpeg: str, saved: Path, *, codecs=ARCHIVE_CODECS, run=run_child,
           cancel: threading.Event | None = None, work: Path | None = None,
           report=None) -> dict:
    """Step 1. Encode into `work`, prove it, and leave the proof on disk.

    Returns a block whose state is `ready` (a proven staged file awaits
    adoption), `kept_original` (a verdict about this clip, written to its
    sidecar and never asked again), `unavailable` (this machine could not
    encode or probe at all) or `interrupted` (`cancel`: the app closed
    mid-job). The last two say nothing about the clip, write nothing, and
    leave nothing behind. Never touches `saved`.

    `work` is where the working files go; None means beside the clip.
    `report(stage, fraction)` follows the job for whoever is watching it:
    stage `compressing` then `checking`, each with its own 0..1.
    """
    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    sidecar = saved.with_suffix(".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    settled = meta.get("media")
    if isinstance(settled, dict) and settled.get("state") in SETTLED_STATES:
        # Already answered for this clip. Re-encoding a compressed replay
        # would stack a second generation of loss on a PB for nothing.
        return settled
    staged, proof = staged_path(saved, work), proof_path(saved, work)
    proof.unlink(missing_ok=True)
    staged.unlink(missing_ok=True)
    reasons: list[str] = []
    encoded = False
    try:
        original = fingerprint(ffmpeg, saved)
        end = end_in_stream_units(ffmpeg, saved)
        for codec in codecs:
            if cancelled():
                break
            started = time.monotonic()
            args = encode_args(ffmpeg, saved, staged, codec, end)
            follow = {}
            if report is not None:
                # The feed goes right after the binary; the output stays last.
                args = [args[0], *progress_args(), *args[1:]]
                follow["on_line"] = lambda line: _tell(
                    report, "compressing", progress_of(line, original.pictures))
                report("compressing", 0.0)
            result = run(args, capture_output=True, text=True, timeout=_ENCODE_TIMEOUT_S,
                         check=False, **follow, **_spawn_kwargs())
            if cancelled():
                break
            if result.returncode:
                reasons.append(f"{codec}: ffmpeg exited {result.returncode}: "
                               f"{(result.stderr or '')[-160:].strip()}")
                continue
            encoded = True
            try:
                candidate, similarity = prove(
                    ffmpeg, saved, staged, original, meta.get("frame_times"),
                    (lambda f: report("checking", f)) if report is not None else None)
            except Unproven as refused:
                if cancelled():
                    break
                reasons.append(f"{codec}: {refused}")
                continue
            block = {"version": MEDIA_VERSION, "state": "ready", "codec": codec,
                     **candidate.as_dict(), "original": original.as_dict(),
                     "ssim": round(similarity, 5),
                     "encode_s": round(time.monotonic() - started, 2),
                     "proven_utc": datetime.now(timezone.utc).isoformat()}
            atomic_json(proof, block)
            return block
    except Unproven as refused:
        reasons.append(str(refused))
    except (OSError, subprocess.SubprocessError) as failed:
        reasons.append(f"{type(failed).__name__}: {failed}")
    staged.unlink(missing_ok=True)
    if cancelled() or not encoded:
        # Nothing was learned about THIS clip, so no verdict is written. The
        # compressor's job file is what makes the next session try again.
        state = "interrupted" if cancelled() else "unavailable"
        return {"version": MEDIA_VERSION, "state": state, "reasons": reasons}
    # An answer about this clip (it does not shrink, or does not survive the
    # proof) is settled, and is never asked again.
    kept = {"version": MEDIA_VERSION, "state": "kept_original", "reasons": reasons}
    if sidecar.exists():
        atomic_json(sidecar, {**meta, "media": kept}, allow_nan=True)
    return kept


def adopt(saved: Path, work: Path | None = None) -> dict | None:
    """Step 2. Swap a proven staged file under the clip's own name.

    None when there is nothing proven to adopt, or the clip is open elsewhere
    (Windows refuses the replace; the caller simply tries again later). The
    sidecar names the swap BEFORE it happens, so a crash between the two
    writes is visible afterwards as a digest that does not match the file.
    """
    staged, proof = staged_path(saved, work), proof_path(saved, work)
    sidecar = saved.with_suffix(".json")
    if not (staged.is_file() and proof.is_file() and saved.is_file() and sidecar.is_file()):
        return None
    block = json.loads(proof.read_text(encoding="utf-8"))
    if block.get("state") != "ready" or staged.stat().st_size != block.get("bytes"):
        return None
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    if file_sha256(saved) != block["original"]["sha256"]:
        # Someone replaced the clip since the proof was made: the proof is void.
        staged.unlink(missing_ok=True)
        proof.unlink(missing_ok=True)
        return None
    atomic_json(sidecar, {**meta, "media": {**block, "state": "adopting"}}, allow_nan=True)
    try:
        os.replace(staged, saved)
    except PermissionError:
        atomic_json(sidecar, meta, allow_nan=True)
        return None
    done = {**block, "state": "compressed",
            "adopted_utc": datetime.now(timezone.utc).isoformat()}
    atomic_json(sidecar, {**meta, "media": done}, allow_nan=True)
    proof.unlink(missing_ok=True)
    return done


def settle(saved: Path, work: Path | None = None) -> None:
    """Repair a sidecar left at `adopting` by a crash: the file's own digest
    says which side of the swap it is on."""
    sidecar = saved.with_suffix(".json")
    if not sidecar.is_file():
        return
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    block = meta.get("media")
    if not isinstance(block, dict) or block.get("state") != "adopting":
        return
    if file_sha256(saved) == block.get("sha256"):
        meta["media"] = {**block, "state": "compressed"}
        proof_path(saved, work).unlink(missing_ok=True)
    else:
        meta.pop("media")
    atomic_json(sidecar, meta, allow_nan=True)


class SavedReplayCompressor:
    """One low-priority worker: shrink what was just saved, adopt when idle.

    `touch` is called whenever a player asks for a saved clip's bytes; a clip
    asked for within `idle_s` is not swapped under that player.
    """

    def __init__(self, ffmpeg: str, root: Path, *, idle_s: float = 120.0,
                 poll_s: float = 15.0, clock=time.monotonic, shrink_fn=shrink):
        self._ffmpeg, self._root = ffmpeg, root
        self._idle_s, self._poll_s, self._clock = idle_s, poll_s, clock
        self._shrink = shrink_fn
        self._jobs: list[Path] = []
        self._touched: dict[str, float] = {}
        self._guard = threading.Lock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._thread: threading.Thread | None = None
        self.outcomes: deque[dict] = deque(maxlen=50)   # newest last; for status/tests
        # What the close warning and the recording panel show: this session's
        # jobs by clip name, in the order they were queued.
        self._shown: dict[str, dict] = {}

    # A job's one bar runs across both halves of the work. The encode is a
    # little over half of the wall time on the clips measured (6 s of 10).
    _ENCODE_SHARE = 0.55
    _SHOWN_MAX = 8

    def _show(self, name: str, **fields) -> None:
        with self._guard:
            row = self._shown.get(name)
            if row is not None:
                # One bar never runs backwards, whatever order reports land in.
                if "fraction" in fields and fields["fraction"] is not None:
                    fields["fraction"] = max(row.get("fraction") or 0.0, fields["fraction"])
                row.update(fields)

    def _report_for(self, name: str):
        def report(stage: str, fraction: float) -> None:
            share = self._ENCODE_SHARE
            overall = fraction * share if stage == "compressing" else share + fraction * (1 - share)
            self._show(name, stage=stage, fraction=round(min(overall, 0.999), 4))
        return report

    def status(self) -> dict:
        """The contract of `GET /api/replay/compression` (docs/api.md)."""
        with self._guard:
            jobs = [dict(row) for row in reversed(self._shown.values())][:self._SHOWN_MAX]
        return {"active": any(j["stage"] in ("waiting", "compressing", "checking") for j in jobs),
                "jobs": jobs}

    def start(self) -> None:
        # Session start: no player exists yet, so every proven file is idle.
        self.adopt_ready(everything_idle=True)
        self._requeue_interrupted()
        self._stopping.clear()
        self._thread = threading.Thread(target=self._work, name="replay-compressor",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """The app is closing: end the job in flight, then swap everything
        already proven. Nothing can be playing a clip after this, and he
        expects to find the small file, not two files, when he looks in the
        folder (2026-09-19: "I would expect the old, uncompressed files to be
        deleted, while the new video file replaces the old file")."""
        self._stopping.set()
        self._wake.set()
        end_children()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self.adopt_ready(everything_idle=True)

    @property
    def work(self) -> Path:
        """The one hidden folder holding every working file (see WORK_DIR)."""
        folder = self._root / WORK_DIR
        if not folder.is_dir():
            folder.mkdir(parents=True, exist_ok=True)
            _hide(folder)
        return folder

    def _clip_of(self, note: Path) -> Path | None:
        """The saved replay a job note or proof belongs to. The note holds its
        path under the save tree; a clip he moved in Explorer is found by name."""
        name = note.name.removesuffix(JOB_SUFFIX).removesuffix(PROOF_SUFFIX)
        job = self.work / (name + JOB_SUFFIX)
        with suppress(OSError, ValueError, KeyError, TypeError):
            listed = self._root / json.loads(job.read_text(encoding="utf-8"))["clip"]
            if listed.is_file():
                return listed
        return next((p for p in self._root.rglob(name) if p.is_file()), None)

    def _forget(self, saved_name: str) -> None:
        for suffix in (JOB_SUFFIX, STAGED_SUFFIX, PROOF_SUFFIX):
            (self.work / (saved_name + suffix)).unlink(missing_ok=True)

    def _requeue_interrupted(self) -> None:
        """A job note with no proof is a save the last session never finished
        shrinking: it closed mid-encode, or before the worker reached it."""
        for note in sorted(self.work.glob(f"*{JOB_SUFFIX}")):
            name = note.name.removesuffix(JOB_SUFFIX)
            if (self.work / (name + PROOF_SUFFIX)).exists():
                continue
            saved = self._clip_of(note)
            if saved is None:
                self._forget(name)   # he deleted the replay; nothing is owed
                continue
            shown = {}
            with suppress(OSError, ValueError, TypeError):
                shown = json.loads(note.read_text(encoding="utf-8")).get("shown") or {}
            self.enqueue(saved, **{key: shown.get(key)
                                   for key in ("attempt_id", "label", "time_text")})

    def enqueue(self, saved: Path, *, attempt_id: int | None = None,
                label: str | None = None, time_text: str | None = None) -> None:
        """`label`/`time_text` are what the progress list calls this replay;
        they ride in the job note so a job re-run next session is still named."""
        shown = {"attempt_id": attempt_id, "label": label, "time_text": time_text}
        # Written BEFORE the work starts: a close between the save and the
        # first encoded byte must still leave the job owed.
        with suppress(OSError, ValueError):
            atomic_json(job_path(saved, self.work),
                        {"clip": saved.relative_to(self._root).as_posix(), "shown": shown})
        size = None
        with suppress(OSError):
            size = saved.stat().st_size
        with self._guard:
            if saved not in self._jobs:
                self._jobs.append(saved)
            self._shown.pop(saved.name, None)   # re-queued: back to the newest end
            self._shown[saved.name] = {**shown, "stage": "waiting", "fraction": None,
                                       "from_bytes": size, "to_bytes": None}
            while len(self._shown) > self._SHOWN_MAX * 2:
                self._shown.pop(next(iter(self._shown)))
        self._wake.set()

    def touch(self, saved: Path) -> None:
        with self._guard:
            self._touched[str(saved)] = self._clock()

    def _idle(self, saved: Path) -> bool:
        with self._guard:
            last = self._touched.get(str(saved))
        return last is None or self._clock() - last >= self._idle_s

    def adopt_ready(self, *, everything_idle: bool = False) -> list[dict]:
        adopted = []
        if not self._root.exists():
            return adopted
        for proof in sorted(self.work.glob(f"*{PROOF_SUFFIX}")):
            saved = self._clip_of(proof)
            if saved is None:
                self._forget(proof.name.removesuffix(PROOF_SUFFIX))
                continue
            if not (everything_idle or self._idle(saved)):
                continue
            try:
                settle(saved, self.work)
                done = adopt(saved, self.work)
            except (OSError, ValueError, KeyError) as failed:
                log.warning("replay compression: could not adopt %s: %s", saved.name, failed)
                continue
            if done is not None:
                self._forget(saved.name)
                self._show(saved.name, stage="done", fraction=1.0,
                           from_bytes=done["original"]["bytes"], to_bytes=done["bytes"])
                log.info("replay compressed: %s %.1f -> %.1f MB (%s, SSIM %.4f)",
                         saved.name, done["original"]["bytes"] / 2**20,
                         done["bytes"] / 2**20, done["codec"], done["ssim"])
                adopted.append(done)
        return adopted

    def _work(self) -> None:
        while not self._stopping.is_set():
            with self._guard:
                saved = self._jobs.pop(0) if self._jobs else None
            if saved is not None and saved.is_file():
                self._show(saved.name, stage="compressing", fraction=0.0)
                try:
                    outcome = self._shrink(self._ffmpeg, saved, cancel=self._stopping,
                                           work=self.work, report=self._report_for(saved.name))
                except Exception:  # noqa: BLE001 - the worker outlives one bad clip
                    log.exception("replay compression failed for %s", saved.name)
                    outcome = {"state": "unavailable", "reasons": ["worker error"]}
                state = outcome.get("state")
                if state == "ready":
                    self._show(saved.name, stage="in_use", fraction=1.0,
                               to_bytes=outcome.get("bytes"))
                elif state in ("compressed", "adopting"):
                    self._show(saved.name, stage="done", fraction=1.0,
                               to_bytes=outcome.get("bytes"))
                elif state in ("kept_original", "unavailable"):
                    self._show(saved.name, stage="kept", fraction=1.0)
                if outcome.get("state") in ("kept_original", "compressed", "adopting"):
                    self._forget(saved.name)   # answered; `unavailable` and
                    # `interrupted` keep their note and run again next start
                if outcome.get("state") in ("kept_original", "unavailable"):
                    log.info("replay kept as saved: %s (%s)", saved.name,
                             "; ".join(outcome.get("reasons", [])))
                self.outcomes.append({"clip": saved.name, **outcome})
            self.outcomes.extend(self.adopt_ready())
            with self._guard:
                waiting = bool(self._jobs)
            if not waiting:
                self._wake.wait(self._poll_s)
                self._wake.clear()
