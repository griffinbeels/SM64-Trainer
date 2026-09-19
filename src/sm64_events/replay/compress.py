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

1. `shrink` writes `<clip>.mp4.shrink` beside the clip and, once proven, the
   `<clip>.mp4.shrink.json` that says so. Neither name matches the
   `attempt_*.mp4` glob that indexes the save tree.
2. `adopt` swaps a proven file under the clip's own name and records the
   sidecar's `media` block. It runs when nobody has asked for the clip's
   bytes lately, and at session start, when nobody can have.

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
STAGED_SUFFIX = ".shrink"            # <clip>.mp4.shrink
PROOF_SUFFIX = ".shrink.json"        # <clip>.mp4.shrink.json
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
    out = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(clip),
         "-map", "0:a:0", "-f", "framemd5", "-"],
        capture_output=True, timeout=600, check=False, **_spawn_kwargs())
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


def picture_similarity(ffmpeg: str, clip: Path, reference: Path) -> float:
    """Mean SSIM, pictures paired by POSITION (their times are proven apart)."""
    graph = ("[0:v]setpts=N,format=yuv420p[a];[1:v]setpts=N,format=yuv420p[b];"
             "[a][b]ssim")
    out = subprocess.run(
        [ffmpeg, "-nostdin", "-hide_banner", "-nostats", "-loglevel", "info",
         "-i", str(clip), "-i", str(reference), "-lavfi", graph, "-f", "null", "-"],
        capture_output=True, text=True, timeout=_ENCODE_TIMEOUT_S, check=False,
        **_spawn_kwargs())
    found = re.findall(r"SSIM .*All:([0-9.]+)", out.stderr)
    if out.returncode or not found:
        raise Unproven("picture comparison failed")
    return float(found[-1])


def prove(ffmpeg: str, saved: Path, staged: Path, original: Fingerprint,
          sidecar_times: list | None) -> tuple[Fingerprint, float]:
    """Raise Unproven unless `staged` can stand in for `saved` everywhere."""
    candidate = fingerprint(ffmpeg, staged)
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
    similarity = picture_similarity(ffmpeg, staged, saved)
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


def staged_path(saved: Path) -> Path:
    return saved.with_name(saved.name + STAGED_SUFFIX)


def proof_path(saved: Path) -> Path:
    return saved.with_name(saved.name + PROOF_SUFFIX)


def shrink(ffmpeg: str, saved: Path, *, codecs=ARCHIVE_CODECS, run=subprocess.run) -> dict:
    """Step 1. Encode beside `saved`, prove it, and leave the proof on disk.

    Returns the `media` block either way: state `ready` (a proven staged file
    awaits adoption) or `kept_original` with the reason. Never touches `saved`.
    """
    sidecar = saved.with_suffix(".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.exists() else {}
    settled = meta.get("media")
    if isinstance(settled, dict) and settled.get("state") in SETTLED_STATES:
        # Already answered for this clip. Re-encoding a compressed replay
        # would stack a second generation of loss on a PB for nothing.
        return settled
    staged, proof = staged_path(saved), proof_path(saved)
    staged.unlink(missing_ok=True)
    proof.unlink(missing_ok=True)
    reasons: list[str] = []
    encoded = False
    try:
        original = fingerprint(ffmpeg, saved)
        end = end_in_stream_units(ffmpeg, saved)
        for codec in codecs:
            started = time.monotonic()
            result = run(encode_args(ffmpeg, saved, staged, codec, end),
                         capture_output=True, text=True, timeout=_ENCODE_TIMEOUT_S,
                         check=False, **_spawn_kwargs())
            if result.returncode:
                reasons.append(f"{codec}: ffmpeg exited {result.returncode}: "
                               f"{(result.stderr or '')[-160:].strip()}")
                staged.unlink(missing_ok=True)
                continue
            encoded = True
            try:
                candidate, similarity = prove(ffmpeg, saved, staged, original,
                                              meta.get("frame_times"))
            except Unproven as refused:
                reasons.append(f"{codec}: {refused}")
                staged.unlink(missing_ok=True)
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
    kept = {"version": MEDIA_VERSION, "state": "kept_original", "reasons": reasons}
    if encoded and sidecar.exists():
        # An answer about THIS clip (it does not shrink, or does not survive
        # the proof) is settled. A machine that could not encode or probe at
        # all has answered nothing, so the next session may try again.
        atomic_json(sidecar, {**meta, "media": kept}, allow_nan=True)
    return kept


def adopt(saved: Path) -> dict | None:
    """Step 2. Swap a proven staged file under the clip's own name.

    None when there is nothing proven to adopt, or the clip is open elsewhere
    (Windows refuses the replace; the caller simply tries again later). The
    sidecar names the swap BEFORE it happens, so a crash between the two
    writes is visible afterwards as a digest that does not match the file.
    """
    staged, proof, sidecar = staged_path(saved), proof_path(saved), saved.with_suffix(".json")
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


def settle(saved: Path) -> None:
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
        proof_path(saved).unlink(missing_ok=True)
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

    def start(self) -> None:
        # Session start: no player exists yet, so every proven file is idle.
        self.adopt_ready(everything_idle=True)
        self._stopping.clear()
        self._thread = threading.Thread(target=self._work, name="replay-compressor",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def enqueue(self, saved: Path) -> None:
        with self._guard:
            if saved not in self._jobs:
                self._jobs.append(saved)
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
        for proof in self._root.rglob(f"attempt_*.mp4{PROOF_SUFFIX}"):
            saved = proof.with_name(proof.name.removesuffix(PROOF_SUFFIX))
            if not (everything_idle or self._idle(saved)):
                continue
            try:
                settle(saved)
                done = adopt(saved)
            except (OSError, ValueError, KeyError) as failed:
                log.warning("replay compression: could not adopt %s: %s", saved.name, failed)
                continue
            if done is not None:
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
                try:
                    outcome = self._shrink(self._ffmpeg, saved)
                except Exception:  # noqa: BLE001 - the worker outlives one bad clip
                    log.exception("replay compression failed for %s", saved.name)
                    outcome = {"state": "kept_original", "reasons": ["worker error"]}
                if outcome.get("state") != "ready":
                    log.info("replay kept as saved: %s (%s)", saved.name,
                             "; ".join(outcome.get("reasons", [])))
                self.outcomes.append({"clip": saved.name, **outcome})
            self.outcomes.extend(self.adopt_ready())
            with self._guard:
                waiting = bool(self._jobs)
            if not waiting:
                self._wake.wait(self._poll_s)
                self._wake.clear()
