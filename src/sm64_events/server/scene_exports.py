"""The Scene viewer's recordings made into video files (M4 round 6, item 3): the export jobs.

    POST /api/scene-lab/export {session, timeline, fps[, duration_ms]}   -> {job}
    GET  /api/scene-lab/export/{job}                                      -> {state, progress, message, path}
    POST /api/scene-lab/export/{job}/cancel                               -> {state}

A scene recording (the glossary's) is the timeline the page kept while Record was on: one entry
per display frame, {t_ms, scene_frame, camera, camera_used, hud, ghosts, sound, play_ms}
(docs/api.md "Scene viewer"). POST checks it against the open scene, writes a job folder (the lab
config's paths, the package, the timeline, every ghost's clip as the catalog loads it, the
ghosts' look) and starts tools/scene_lab/scene_export.py as a below-normal, hidden child: the
pictures are drawn by a renderer host of its own and encoded by FFmpeg, so the server's event loop
and Python lock only read the child's progress lines. One export runs at a time (with the open
scene's host that is the two renderer hosts the machine allows). The file lands in the saved
replays' folder under "scene clips" (core/paths.py scene_clips_dir), named for the course, star
and local time, so the replay's own reveal route (POST /api/replay/reveal) shows it in Explorer.

A job is {state: running | done | error | cancelled, progress 0..1, message, path}; "done" adds
`stats` (pictures, drawn, seconds, codec, bytes). Cancel asks the child to stop (it removes its
partial file); one that has not ended within CANCEL_WAIT_SECONDS is ended by its PID. The codec
the replay's probe chose (the child's first export asks it) is kept for the next export.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

log = logging.getLogger("sm64.scene_lab")

FPS_CHOICES = (30, 60)
MAX_SECONDS = 30 * 60              # a recording longer than this is refused (the page stops at it)
MAX_ENTRIES = 300_000              # 30 minutes at 165 Hz
MAX_BODY_BYTES = 256 << 20
KEEP_JOBS = 8                      # finished jobs a page can still poll (server/jobs.py's leak guard)
CANCEL_WAIT_SECONDS = 15.0
CAMERA_KEYS = {"focus", "position", "roll", "fov"}
SOUND_KEYS = {"music", "effects", "volume", "muted", "ghost_mutes"}
BUSY = "A video is still being made. Wait for it to finish, or cancel it."
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _point(value) -> bool:
    return isinstance(value, list) and len(value) == 3 and all(_number(part) for part in value)


def _pose(value) -> dict:
    if not (isinstance(value, dict) and set(value) <= CAMERA_KEYS and _point(value.get("focus"))
            and _point(value.get("position"))
            and all(value.get(key) is None or _number(value[key]) for key in ("roll", "fov"))):
        raise ValueError("a camera pose is {focus, position[, roll, fov]}")
    return {key: value[key] for key in ("focus", "position", "roll", "fov") if value.get(key) is not None}


def _camera(value):
    """A camera as the page sent it (interface D): null, a pose, {"ghost": ref} or {"rig": {...}}."""
    if value is None:
        return None
    if isinstance(value, dict) and set(value) == {"ghost"} and isinstance(value["ghost"], str) and value["ghost"]:
        return {"ghost": value["ghost"][:512]}
    if isinstance(value, dict) and set(value) == {"rig"} and isinstance(value["rig"], dict) and len(json.dumps(value)) < 4096:
        return {"rig": value["rig"]}
    return _pose(value)


def _refs(value, what: str) -> list[str]:
    if not isinstance(value, list) or len(value) > 64 or not all(isinstance(ref, str) and 0 < len(ref) <= 512
                                                                  for ref in value):
        raise ValueError(f"{what} is a list of ghost refs")
    return list(value)


def checked_timeline(timeline, frames: int) -> list[dict]:
    """The page's timeline with only the fields an export reads, or ValueError naming what is wrong.
    `frames` is the open scene's frame count."""
    if not isinstance(timeline, list) or not timeline:
        raise ValueError("Nothing was recorded.")
    if len(timeline) > MAX_ENTRIES:
        raise ValueError(f"A recording is at most {MAX_SECONDS // 60} minutes long.")
    entries, last = [], 0.0
    for entry in timeline:
        if not isinstance(entry, dict):
            raise ValueError("each timeline entry is an object")
        t_ms, frame, play = entry.get("t_ms"), entry.get("scene_frame"), entry.get("play_ms")
        if not _number(t_ms) or t_ms < last or t_ms > MAX_SECONDS * 1000:
            raise ValueError("timeline entries run forward in time from 0, within the longest recording")
        if not isinstance(frame, int) or isinstance(frame, bool) or not 0 <= frame < frames:
            raise ValueError("a timeline entry names a frame this scene does not have")
        if play is not None and not _number(play):
            raise ValueError("play_ms is the recorded time sounding, or null")
        sound = entry.get("sound") or {}
        if not isinstance(sound, dict) or not set(sound) <= SOUND_KEYS:
            raise ValueError("a timeline entry's sound is {music, effects, volume, muted, ghost_mutes}")
        volume = sound.get("volume", 1.0)
        if not _number(volume) or not 0 <= volume <= 1:
            raise ValueError("the volume is 0 to 1")
        used = entry.get("camera_used")
        entries.append({
            "t_ms": float(t_ms), "scene_frame": frame, "camera": _camera(entry.get("camera")),
            "camera_used": None if used is None else _pose(used), "hud": entry.get("hud", True) is not False,
            "ghosts": _refs(entry.get("ghosts") or [], "a timeline entry's ghosts"),
            "play_ms": None if play is None else float(play),
            "sound": {"music": sound.get("music", True) is not False, "effects": sound.get("effects", True) is not False,
                      "volume": float(volume), "muted": sound.get("muted") is True,
                      "ghost_mutes": _refs(sound.get("ghost_mutes") or [], "the muted ghosts")}})
        last = t_ms
    return entries


def file_stem(heading: dict, scene_name: str | None, now: datetime) -> str:
    """A plain file name: the course and star (or the segment) and the local date and time, else
    the scene's title; never a character Windows refuses."""
    target = heading.get("target") or {}
    parts = [target.get("course_name"), target.get("star_name")] if target.get("course_name") else []
    if not any(parts) and target.get("segment_name"):
        parts = [target["segment_name"]]
    if not any(parts):
        parts = [heading.get("title") if heading.get("title") not in (None, "Lab recording")
                 else Path(scene_name or "Scene").stem]
    named = " - ".join(str(part) for part in parts if part)
    stem = _UNSAFE.sub("", f"{named} {now:%Y-%m-%d %H.%M.%S}").strip(" .")
    return stem[:150] or "Scene clip"


def unused_path(folder: Path, stem: str) -> Path:
    path = folder / f"{stem}.mp4"
    number = 2
    while path.exists() or path.with_name(path.name + ".part").exists():
        path, number = folder / f"{stem} ({number}).mp4", number + 1
    return path


class ExportJob:
    """One export child and what the page polls. The reader thread updates the status from the
    child's lines; the server's threads only start, read and stop it."""

    def __init__(self, ident: str, folder: Path, output: Path, keep_folder: bool = False):
        self.ident, self.folder, self.output, self.keep_folder = ident, folder, output, keep_folder
        self.status = {"state": "running", "progress": 0.0, "message": "Starting...", "path": None}
        self.process: subprocess.Popen | None = None
        self.codec: str | None = None
        self.cancelling = False
        self.ended = threading.Event()

    def start(self, argv: list[str], env: dict | None, spawn: dict, on_codec) -> None:
        log_file = open(self.folder / "child.log", "wb")  # noqa: SIM115 - the child writes it until it ends
        try:
            self.process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log_file,
                                            env=env, **spawn)
        finally:
            log_file.close()
        threading.Thread(target=self._read, args=(on_codec,), name=f"scene-export-{self.ident[:8]}",
                         daemon=True).start()

    def _read(self, on_codec) -> None:
        final = None
        for line in self.process.stdout:
            try:
                event = json.loads(line)
            except ValueError:
                continue
            kind = event.get("kind") if isinstance(event, dict) else None
            if kind == "progress":
                self.status.update(progress=float(event.get("progress", 0)), message=str(event.get("message", "")))
            elif kind == "codec":
                self.codec = event.get("codec")
                on_codec(self.codec)
            elif kind in ("done", "error", "cancelled"):
                final = event
        code = self.process.wait()
        if final is not None and final["kind"] == "done":
            stats = final.get("stats") or {}
            self.status.update(state="done", progress=1.0, path=final.get("path"), stats=stats,
                               message="Saved " + Path(final.get("path") or "").name + "."
                               + "".join(f" {note}" for note in stats.get("notes") or ()))
        elif final is not None and final["kind"] == "cancelled" or self.cancelling:
            self.status.update(state="cancelled", message="Cancelled.")
        else:
            reason = final.get("message") if final else f"The export stopped unexpectedly (exit {code})."
            self.status.update(state="error", message=reason)
            try:
                tail = (self.folder / "child.log").read_bytes()[-2000:].decode("utf-8", "replace")
            except OSError:
                tail = ""
            log.warning("A scene export failed: %s %s", reason, tail)
        if self.status["state"] != "done":
            self.output.with_name(self.output.name + ".part").unlink(missing_ok=True)
        if not self.keep_folder:
            shutil.rmtree(self.folder, ignore_errors=True)
        self.ended.set()

    def cancel(self) -> None:
        """Asks the child to stop; ends it by its PID if it has not within CANCEL_WAIT_SECONDS."""
        if self.status["state"] != "running" or self.process is None:
            return
        self.cancelling = True
        self.status["message"] = "Cancelling..."
        try:
            self.process.stdin.write(b"cancel\n")
            self.process.stdin.flush()
        except OSError:
            pass

        def deadline():
            if not self.ended.wait(CANCEL_WAIT_SECONDS) and self.process.poll() is None:
                self.process.kill()
        threading.Thread(target=deadline, daemon=True).start()


class SceneExports:
    """The lab's export jobs: at most one running, the last KEEP_JOBS kept for polling.
    `options` are merged into every job file (tests: {"host": "fake", "encoder": {"kind": "frames"}});
    `clips_dir` replaces where the files land; `keep_folders` keeps each job's folder (measurements)."""

    def __init__(self, lab, *, clips_dir: Path | None = None, options: dict | None = None,
                 keep_folders: bool = False, extra_path: tuple = ()):
        self.lab, self.clips_dir, self.options = lab, clips_dir, options or {}
        self.keep_folders, self.extra_path = keep_folders, extra_path
        self.jobs: dict[str, ExportJob] = {}
        self.codec: str | None = None

    def running(self) -> ExportJob | None:
        return next((job for job in self.jobs.values() if job.status["state"] == "running"), None)

    def folder(self) -> Path:
        if self.clips_dir is not None:
            return Path(self.clips_dir)
        replay = getattr(getattr(self.lab, "ghosts", None), "replay", None)
        root = getattr(getattr(replay, "cfg", None), "save_root", None)     # where saved replays land
        from sm64_events.core.paths import scene_clips_dir
        return scene_clips_dir() if root is None else Path(root) / scene_clips_dir().name

    async def start(self, body: dict) -> str:
        """Checks the body against the open scene and starts the child; ValueError (422),
        LookupError (409: no such scene open) or RuntimeError (409: busy, no FFmpeg)."""
        lab = self.lab
        if not isinstance(body, dict):
            raise ValueError("The export is {session, timeline, fps}.")
        fps = body.get("fps", 60)
        if fps not in FPS_CHOICES:
            raise ValueError("fps is 30 or 60.")
        duration = body.get("duration_ms")
        if duration is not None and (not _number(duration) or not 0 <= duration <= MAX_SECONDS * 1000):
            raise ValueError("duration_ms is how long the recording ran.")
        described, generation = lab.described, lab.generation
        if lab.session is None or described is None or body.get("session") != generation:
            raise LookupError("This scene is no longer open, so its recording cannot be exported.")
        if self.running() is not None:
            raise RuntimeError(BUSY)
        entries = await asyncio.to_thread(checked_timeline, body.get("timeline"), len(described["frames"]))
        package, sidecar = lab.open_path, None
        if lab.open_attempt is not None and lab.scenes is not None:
            try:
                package, sidecar = lab.scenes.path(lab.open_attempt)
            except Exception:  # noqa: BLE001 - the open scene's own file is still the one to draw
                package, sidecar = lab.open_path, None
        encoder = self.options.get("encoder") or {"kind": "ffmpeg"}
        if encoder.get("kind", "ffmpeg") == "ffmpeg":
            from sm64_events.core.paths import bundled_ffmpeg
            ffmpeg = encoder.get("ffmpeg") or bundled_ffmpeg() or shutil.which("ffmpeg")
            if not ffmpeg:
                raise RuntimeError("FFmpeg is missing, so the video cannot be encoded.")
            encoder = {"kind": "ffmpeg", "ffmpeg": ffmpeg, "codec": encoder.get("codec") or self.codec}
        heading = dict(lab.heading)
        job_folder = Path(tempfile.mkdtemp(prefix="scene-export-"))
        try:
            ghosts = await self._ghosts(entries, job_folder)
            clips = self.folder()
            clips.mkdir(parents=True, exist_ok=True)
            output = unused_path(clips, file_stem(heading, described.get("name"), datetime.now()))
            output.with_name(output.name + ".part").touch()          # the name is taken from now on
            config = await asyncio.to_thread(self._config)
            job = {"config": config, "package": str(package), "sidecar": str(sidecar) if sidecar else None,
                   "fps": fps, "duration_ms": duration, "timeline": entries, "ghosts": ghosts,
                   "look": lab._ghost_values(), "output": str(output), "encoder": encoder,
                   **{key: value for key, value in self.options.items() if key != "encoder"}}
            await asyncio.to_thread((job_folder / "job.json").write_text, json.dumps(job), "utf-8")
            return self._spawn(job_folder, output)
        except BaseException:
            shutil.rmtree(job_folder, ignore_errors=True)
            raise

    def _config(self) -> dict:
        from sm64_events.server.scene_lab_api import LabConfig
        config = LabConfig.load(self.lab.config_path)
        return {"rom": str(config.rom), "pj64_plugin": str(config.pj64_plugin) if config.pj64_plugin else "",
                "renderer": config.renderer, "resolution": list(config.resolution) if config.resolution else None,
                "map": str(config.map) if config.map else None}

    async def _ghosts(self, entries: list[dict], folder: Path) -> dict:
        """Every ghost the recording drew, its clip as the catalog loads it (the lab's _try_load, on
        threads), written beside the job; a ghost that no longer loads is left out of the video."""
        refs = list(dict.fromkeys(ref for entry in entries for ref in entry["ghosts"]))
        loaded = await asyncio.gather(*(asyncio.to_thread(self.lab._try_load, ref) for ref in refs))
        ghosts = {}
        for number, (ref, got) in enumerate(zip(refs, loaded, strict=True)):
            if isinstance(got, LookupError) or not isinstance(got, dict) or not got.get("clip"):
                log.warning("A recorded ghost could not be loaded for its video: %s", got)
                continue
            name = f"ghost-{number}.clip"
            await asyncio.to_thread((folder / name).write_bytes, bytes(got["clip"]))
            ghosts[ref] = {"clip": name, "player": got.get("player"), "frames": got.get("frames"),
                           "label": got.get("label"), "time": got.get("time")}
        return ghosts

    def _spawn(self, folder: Path, output: Path) -> str:
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        from sm64_events.server.scene_lab_api import TOOLS
        argv, env = python_child("scene_export", "--scene-lab-export", ["--job", str(folder)], site_packages=True)
        if env is not None:
            env["PYTHONPATH"] = os.pathsep.join(filter(None, (*map(str, self.extra_path), str(TOOLS),
                                                             env.get("PYTHONPATH"))))
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        ident = uuid.uuid4().hex
        job = ExportJob(ident, folder, output, keep_folder=self.keep_folders)
        for stale in list(self.jobs)[:-(KEEP_JOBS - 1)]:
            if self.jobs[stale].status["state"] != "running":
                self.jobs.pop(stale, None)
        self.jobs[ident] = job
        job.start(argv, env, spawn, self._remember_codec)
        return ident

    def _remember_codec(self, codec) -> None:
        if isinstance(codec, str) and codec:
            self.codec = codec

    def status(self, ident: str) -> dict:
        job = self.jobs.get(ident)
        if job is None:
            raise LookupError("No such export.")
        return dict(job.status)

    def cancel(self, ident: str) -> dict:
        job = self.jobs.get(ident)
        if job is None:
            raise LookupError("No such export.")
        job.cancel()
        return {"state": job.status["state"]}

    def shutdown(self, wait: float = 5.0) -> None:
        """At server shutdown: every running export is cancelled, then ended by its PID."""
        for job in list(self.jobs.values()):
            job.cancel()
        for job in list(self.jobs.values()):
            if job.process is not None and not job.ended.wait(wait) and job.process.poll() is None:
                job.process.kill()
                job.ended.wait(wait)


def mount_export_routes(router: APIRouter, exports: SceneExports) -> None:
    @router.post("/export")
    async def export(request: Request):
        raw = await request.body()
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(413, "This recording is too long to export.")
        try:
            body = await asyncio.to_thread(json.loads, raw or b"null")
            return {"job": await exports.start(body)}
        except LookupError as gone:
            raise HTTPException(409, str(gone)) from None
        except RuntimeError as refused:
            raise HTTPException(409, str(refused)) from None
        except ValueError as wrong:
            raise HTTPException(422, str(wrong)) from None

    @router.get("/export/{job}")
    def export_status(job: str):
        try:
            return exports.status(job)
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None

    @router.post("/export/{job}/cancel")
    def export_cancel(job: str):
        try:
            return exports.cancel(job)
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None
