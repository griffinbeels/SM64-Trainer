"""A recorded Scene viewer timeline made into a video file (M4 round 6, item 3): the export child.

    python -m scene_export --job <folder>      # started below normal and hidden by server/scene_exports.py

His words: "we should be able to RECORD THE SCENE VIEWER and export it as a video ... with all the
camera movements / camera swaps / ghosts / hud or no hud / pauses / etc all included in the
recording". The page records what it showed, one entry per display frame (interface G, the
glossary's "scene recording"); this child draws that timeline again, picture by picture, through a
scene session of its own (same package, same ghosts, same HUD switch, same camera), at the
renderer's full size, and mixes the sound as the page played it (export_sound.py). A hitch while
recording never reaches the file: every output frame is drawn from its entry, not captured.

The folder holds job.json (written by the server: the lab config's paths, the package, the
timeline, the ghosts' clips as files beside it, their look, the output path and the encoder), and
gets sound.wav and pictures.json (each output frame's entry and picture hash, for checks). The
child writes one JSON line per event to stdout: {"kind": "progress", "progress", "message"},
{"kind": "codec", "codec"} once the encoder is chosen, then {"kind": "done", "path", "stats"} or
{"kind": "error", "message"} (a sentence for the page). "cancel" on stdin, or stdin closing (the
server gone), stops it: the partial file is removed and the renderer host, the encoder and the
ghost track maker end with it (each ends on its own pipe's EOF too).

Output frames: frame k shows the entry on screen at k / fps seconds (the last entry whose t_ms has
come), so a pause is that picture held, and two output frames that would draw the same request
(scene frame, camera, HUD, ghosts) reuse one picture. The camera sent is the entry's camera_used
when it has one (the pose the server drew: a rig, a preset, a ghost's view), else the camera the
page sent; either draws the same picture, so rigs and presets reproduce exactly.

Encoding follows the replay recorder's offline path (the saved clip's, replay/extract.py, and the
failure compilation's, replay/compilation.py): an FFmpeg child, the codec the replay's own probe
proved on this machine (replay/codecprobe.py: NVENC, then AMF, then Quick Sync, else x264), its
quality from the one registry (replay/config.py video_quality_args, stage "offline", CLIP_MAXRATE),
no B-frames, AAC at the ring's 160 kb/s, MP4 with faststart. The renderer's pictures are BGR rows
bottom-up, so the child flips them and converts them to BT.709 4:2:0 itself, tagged as such: fed
RGB, NVENC and x264 otherwise write 4:4:4 files most players and editors refuse (measured
2026-10-27: gbrp and yuv444p "High 4:4:4 Predictive").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import export_sound  # noqa: E402

FPS_CHOICES = (30, 60)
DISPLAY_MS = 1000 / 60            # a display frame, for a timeline whose last entry has no end
GOP_SECONDS = 2.0                 # a key picture this often: the ring's segment length, quick seeks in an editor
AUDIO_BITRATE = "160k"            # the ring's AAC (replay/ffmpeg_sink.py), which saved replays keep
DEFAULT_RATE = 48000              # the sound's rate when the scene has none (a silent track)
IN_FLIGHT = 2                     # pictures begun and not yet written (the server's PICTURES_IN_FLIGHT)
PROGRESS_SECONDS = 0.25
SOUND_SHARE, FINISH_SHARE = 0.05, 0.02    # of the progress bar; the pictures take the rest
COLOR = ["-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
PICTURE_FILTER = "vflip,scale=out_color_matrix=bt709:out_range=tv,format=yuv420p"


class Cancelled(Exception):
    """The server asked the export to stop, or went away."""


class ExportError(Exception):
    """Why the video could not be made, in a sentence the page shows."""


# ---------------------------------------------------------------- the timeline

def duration_ms(entries: list[dict], given: float | None = None) -> float:
    """How long the recording ran: the page's own stop time when it sent one, else the last entry
    plus one display frame."""
    if given is not None and entries and given >= entries[-1]["t_ms"]:
        return float(given)
    return (entries[-1]["t_ms"] + DISPLAY_MS) if entries else 0.0


def plan(entries: list[dict], fps: int, total_ms: float) -> list[int]:
    """The entry each output frame shows: frame k shows the last entry whose t_ms is at or before
    k / fps seconds (the first entry before it)."""
    count = max(1, round(total_ms * fps / 1000))
    times = np.array([entry["t_ms"] for entry in entries], np.float64)
    at = np.searchsorted(times, np.arange(count) * 1000.0 / fps, side="right") - 1
    return np.clip(at, 0, len(entries) - 1).tolist()


def _pose(camera) -> bool:
    return isinstance(camera, dict) and "focus" in camera and "position" in camera


def picture_camera(entry: dict):
    """The camera an entry's picture is drawn from: the pose the server drew when the page knew it
    (camera_used), else what the page sent (None, a pose, {"ghost"}, {"rig"})."""
    used = entry.get("camera_used")
    if _pose(used):
        return {key: used[key] for key in ("focus", "position", "roll", "fov") if used.get(key) is not None}
    return entry.get("camera")


def picture_key(entry: dict) -> str:
    """Two entries with the same key draw the same picture."""
    return json.dumps([entry["scene_frame"], picture_camera(entry), bool(entry.get("hud", True)),
                       list(entry.get("ghosts") or ())], sort_keys=True)


# ---------------------------------------------------------------- the encoder

class FfmpegVideo:
    """The FFmpeg child: raw pictures on its stdin, the mixed sound from a file, one MP4 out."""

    def __init__(self, ffmpeg: str, codec: str, width: int, height: int, fps: int, sound: Path, rate: int,
                 output: Path):
        from sm64_events.core.childproc import quiet_spawn_kwargs
        from sm64_events.replay.config import CLIP_MAXRATE, video_quality_args
        self.args = [ffmpeg, "-hide_banner", "-loglevel", "error",
                     "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-framerate", str(fps),
                     "-i", "pipe:0", "-i", str(sound), "-map", "0:v:0", "-map", "1:a:0",
                     "-vf", PICTURE_FILTER, "-c:v", codec, *video_quality_args(codec, "offline", CLIP_MAXRATE),
                     "-bf", "0", "-g", str(round(fps * GOP_SECONDS)), *COLOR,
                     "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", str(rate),
                     "-movflags", "+faststart", "-f", "mp4", "-y", str(output)]
        self.errors = b""
        self.process = subprocess.Popen(self.args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.PIPE, **quiet_spawn_kwargs())
        threading.Thread(target=self._drain, daemon=True).start()

    def _drain(self) -> None:
        self.errors = self.process.stderr.read()[-2000:]

    def write(self, pixels) -> None:
        try:
            self.process.stdin.write(pixels)
        except OSError:
            raise ExportError(f"The video encoder stopped: {self._reason()}") from None

    def close(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        if self.process.wait() != 0:
            raise ExportError(f"The video encoder failed: {self._reason()}")

    def abort(self) -> None:
        try:
            self.process.kill()
        except OSError:
            pass
        self.process.wait()

    def _reason(self) -> str:
        if self.process.poll() is None:
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                return "it does not answer"
        return self.errors.decode("utf-8", "replace").strip()[-400:] or f"exit {self.process.returncode}"


class FrameList:
    """A stand-in encoder for tests (encoder kind "frames"): the output is JSON naming each picture
    written (its sha256) and the sound's digest, so a test reads exactly what reached the encoder."""

    def __init__(self, width: int, height: int, fps: int, sound: Path, output: Path, fail_after: int | None = None):
        self.output, self.fail_after = output, fail_after
        self.made = {"width": width, "height": height, "fps": fps, "pictures": [],
                     "sound": hashlib.sha256(sound.read_bytes()).hexdigest()}

    def write(self, pixels) -> None:
        if self.fail_after is not None and len(self.made["pictures"]) >= self.fail_after:
            raise ExportError("The video encoder stopped: a test's encoder failed on purpose.")
        self.made["pictures"].append(hashlib.sha256(pixels).hexdigest())

    def close(self) -> None:
        self.output.write_text(json.dumps(self.made), encoding="utf-8")

    def abort(self) -> None:
        pass


def pick_codec(ffmpeg: str) -> str:
    """The replay recorder's answer for this machine's FFmpeg (replay/codecprobe.py via
    replay/encoder.py): a real encode and decode on each hardware encoder in turn."""
    from sm64_events.replay.encoder import pick_video_codec
    return pick_video_codec(ffmpeg)


# ---------------------------------------------------------------- the job

def _session(job: dict, folder: Path):
    import scene_session
    import stage_ghosts
    config = SimpleNamespace(rom=Path(job["config"]["rom"]), pj64_plugin=Path(job["config"]["pj64_plugin"]),
                             renderer=job["config"].get("renderer", "trainer"),
                             resolution=tuple(job["config"]["resolution"]) if job["config"].get("resolution") else None,
                             map=Path(job["config"]["map"]) if job["config"].get("map") else None)
    options = {"encoder_factory": lambda: None}
    if job.get("host") == "fake":                      # tests: the protocol double, no renderer and no GPU
        import fake_viewer_host
        import gpu_gate
        gpu_gate.HOLD = folder / "no-hold.txt"
        options["host_factory"] = scene_session.command_host(fake_viewer_host.command(**job.get("fake_host", {})))
        options["evaluator_factory"] = lambda package, rom: fake_viewer_host.FakeEvaluator()
    sidecar = Path(job["sidecar"]) if job.get("sidecar") else None
    try:
        session = scene_session.SceneSession.open(config, Path(job["package"]), sidecar=sidecar, **options)
    except scene_session.Refused as refused:
        raise ExportError(str(refused)) from None
    session.ghost_look = stage_ghosts.Look.of(job.get("look"))
    return session


def _loader(job: dict, folder: Path):
    def load(ref: str) -> dict:
        found = (job.get("ghosts") or {}).get(ref)
        if found is None:
            raise LookupError("This ghost could not be read again for the video.")
        return {**found, "clip": (folder / found["clip"]).read_bytes()}
    return load


def _ghost_set(job: dict, refs) -> list[dict]:
    ghosts = job.get("ghosts") or {}
    return [{"ref": ref, "label": (ghosts.get(ref) or {}).get("label")} for ref in refs]


def _sound(session, job: dict, entries: list[dict], total_ms: float, loader, notes: list) -> tuple[np.ndarray, int]:
    """The timeline's sound and its rate (export_sound.mix over the scene's tracks)."""
    import stage_ghosts
    sound = session.sound
    if sound is None:
        notes.append(session.silence or "This recording has no sound, so the video is silent.")
        return np.zeros((max(0, round(total_ms * DEFAULT_RATE / 1000)), 2), np.int16), DEFAULT_RATE
    layer, described = sound.layer, session.describe()
    rows, fps = described["frames"], described["fps"]
    recorded = all(row["recorded_ms"] is not None for row in rows)       # sceneclock.js recordedTimes
    times = [row["recorded_ms"] if recorded else k * 1000 / fps for k, row in enumerate(rows)]
    runs = export_sound.sound_map(sound.samples, times, layer.rate, fps)
    split = bool(sound.stems is not None and sound.stems.available)
    streams: dict[str, np.ndarray] = {}

    def stage(track: str) -> np.ndarray:
        if track not in streams:
            if track == "mix":
                streams[track] = layer.pcm(0, layer.total_samples)
            else:
                streams[track] = np.concatenate([sound.stems.block(track, index)
                                                 for index in range(len(layer.blocks))])[:layer.total_samples]
        return streams[track]

    refs = list(dict.fromkeys(ref for entry in entries for ref in entry.get("ghosts") or ()))
    tracks = {}
    if refs:
        session.set_ghosts(_ghost_set(job, refs[:stage_ghosts.MAX_GHOSTS]), loader)
        for ghost in session.ghosts.shown:
            try:
                tracks[ghost.ref] = session.ghost_sounds.track(session, ghost)
            except (RuntimeError, OSError, ValueError, LookupError) as error:
                notes.append(f"One ghost's sound could not be made ({error}), so it is silent.")
    return export_sound.mix(entries, total_ms, layer.rate, runs, split, stage, tracks), layer.rate


def _write_wav(path: Path, pcm: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as file:
        file.setnchannels(2)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(np.ascontiguousarray(pcm, "<i2").tobytes())


def _encoder(job: dict, width: int, height: int, fps: int, sound: Path, rate: int, output: Path, report):
    chosen = job["encoder"]
    if chosen.get("kind") == "frames":
        return FrameList(width, height, fps, sound, output, chosen.get("fail_after"))
    codec = chosen.get("codec")
    if not codec:
        report({"kind": "progress", "progress": SOUND_SHARE, "message": "Choosing this computer's video encoder..."})
        codec = pick_codec(chosen["ffmpeg"])
        report({"kind": "codec", "codec": codec})
    job["encoder"]["codec"] = codec
    return FfmpegVideo(chosen["ffmpeg"], codec, width, height, fps, sound, rate, output)


def run(job: dict, folder: Path, report, cancelled: threading.Event) -> dict:
    """Makes the video; returns its stats. ExportError with a sentence, or Cancelled."""
    started = time.perf_counter()
    entries, fps = job["timeline"], int(job["fps"])
    if fps not in FPS_CHOICES or not entries:
        raise ExportError("Nothing was recorded.")
    total_ms = duration_ms(entries, job.get("duration_ms"))
    shows = plan(entries, fps, total_ms)
    output = Path(job["output"])
    partial = output.with_name(output.name + ".part")
    report({"kind": "progress", "progress": 0.0, "message": "Opening the scene..."})
    session = _session(job, folder)
    video, notes, pictures = None, [], []
    try:
        loader = _loader(job, folder)
        report({"kind": "progress", "progress": 0.01, "message": "Mixing the sound..."})
        pcm, rate = _sound(session, job, entries, total_ms, loader, notes)
        _write_wav(folder / "sound.wav", pcm, rate)
        if cancelled.is_set():
            raise Cancelled
        render_started = time.perf_counter()
        drawn = _draw(session, job, entries, shows, loader, report, cancelled, pictures,
                      lambda width, height: _encoder(job, width, height, fps, folder / "sound.wav", rate, partial,
                                                     report))
        video, first = drawn
        report({"kind": "progress", "progress": 1 - FINISH_SHARE, "message": "Finishing the file..."})
        video.close()
        render_seconds = time.perf_counter() - render_started
        os.replace(partial, output)
        (folder / "pictures.json").write_text(json.dumps({"entries": shows, "sha256": pictures}), encoding="utf-8")
        seconds = time.perf_counter() - started
        return {"path": str(output), "pictures": len(shows), "drawn": len(set(pictures)), "fps": fps,
                "seconds": round(seconds, 2), "render_seconds": round(render_seconds, 2),
                "video_seconds": round(len(shows) / fps, 3), "width": first["width"], "height": first["height"],
                "codec": job["encoder"].get("codec") or job["encoder"].get("kind"), "bytes": output.stat().st_size,
                "notes": notes}
    except BaseException:
        if video is not None:
            video.abort()
        partial.unlink(missing_ok=True)
        raise
    finally:
        session.close()


def _draw(session, job, entries, shows, loader, report, cancelled, pictures, start_video):
    """Every output frame's picture, in order, to the encoder: the session's worker half (begin) on
    this thread, its delivery half (finish) and the write on a second, as the server pipelines a
    page's pictures. Returns (encoder, first picture's header)."""
    waiting: queue.Queue = queue.Queue(maxsize=IN_FLIGHT)
    state = {"video": None, "first": None, "error": None, "last": None}
    same = object()

    def deliver():
        while True:
            item = waiting.get()
            if item is None:
                return
            if state["error"] is not None:
                continue
            try:
                if item is same:
                    pixels, digest = state["last"]
                else:
                    header, pixels = session.finish(item)
                    digest = header["sha256"]
                    if state["first"] is None:
                        state["first"] = header
                        state["video"] = start_video(header["width"], header["height"])
                    elif (header["width"], header["height"]) != (state["first"]["width"], state["first"]["height"]):
                        raise ExportError("The renderer changed its picture size during the export.")
                    state["last"] = (pixels, digest)
                state["video"].write(pixels)
                pictures.append(digest)
            except BaseException as error:  # noqa: BLE001 - handed to the drawing thread, which raises it
                state["error"] = error

    thread = threading.Thread(target=deliver, name="scene-export-delivery", daemon=True)
    thread.start()
    drawn_key, drawn_set, shown_at = None, None, 0.0
    try:
        for position, at in enumerate(shows):
            if cancelled.is_set():
                raise Cancelled
            if state["error"] is not None:
                raise state["error"]
            entry = entries[at]
            key = picture_key(entry)
            if key == drawn_key:
                waiting.put(same)
                continue
            refs = list(entry.get("ghosts") or ())
            if refs != drawn_set:
                session.set_ghosts(_ghost_set(job, refs), loader)
                drawn_set = refs
            try:
                pending = session.begin(entry["scene_frame"], picture_camera(entry), "raw", bool(entry.get("hud", True)))
            except ValueError as error:
                raise ExportError(f"Frame {entry['scene_frame'] + 1} of the recording could not be drawn again: "
                                  f"{error}.") from None
            waiting.put(pending)
            drawn_key = key
            now = time.perf_counter()
            if now - shown_at >= PROGRESS_SECONDS:
                shown_at = now
                share = SOUND_SHARE + (1 - SOUND_SHARE - FINISH_SHARE) * position / len(shows)
                report({"kind": "progress", "progress": round(share, 4),
                        "message": f"Drawing picture {position + 1} of {len(shows)}..."})
    finally:
        waiting.put(None)
        thread.join()
    if state["error"] is not None:
        if state["video"] is not None:
            state["video"].abort()
        raise state["error"]
    return state["video"], state["first"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--job", type=Path, required=True)
    args = parser.parse_args(argv)
    cancelled = threading.Event()
    lock = threading.Lock()

    def report(event: dict) -> None:
        with lock:
            sys.stdout.write(json.dumps(event) + "\n")
            sys.stdout.flush()

    def watch():
        for line in sys.stdin:
            if line.strip() == "cancel":
                break
        cancelled.set()                                # "cancel", or the server's end of the pipe closed
    threading.Thread(target=watch, name="scene-export-cancel", daemon=True).start()
    try:
        job = json.loads((args.job / "job.json").read_text(encoding="utf-8"))
        stats = run(job, args.job, report, cancelled)
    except Cancelled:
        report({"kind": "cancelled", "message": "Cancelled."})
        return 2
    except ExportError as error:
        report({"kind": "error", "message": str(error)})
        return 1
    except Exception as error:  # noqa: BLE001 - the page shows something rather than a silent end
        report({"kind": "error", "message": f"The video could not be made: {error}"})
        raise
    report({"kind": "done", "path": stats["path"], "stats": stats})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
