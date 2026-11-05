"""Save this moment: one frame of a scene as a Usamune save state, into a slot of the running game (lab only).

    POST /api/scene-lab/moment {package|attempt, frame}   -> the built state, or {refused, reason}
    POST /api/scene-lab/moment/{id}/write {slot: 1..9}    -> {written, slot, ...}, or {refused, reason}

Griffin's words: "Once we build out the infrastructure so that I can select an arbitrary game state and create a
save state from it, I'll be able to test independently and gather results." `frame` is the package's own frame
(a scene row's package_frame). Slot N in these routes is the number Usamune's menu shows; its slot index is N-1.

The build runs in a hidden below-normal child (tools/scene_lab/moment_slot.py), one at a time, so neither the
event loop nor the poller shares Python's lock with it: a build is about half a second of pure CPU. The route
only reads the child's one line. The write goes through handoff_try.install, the one checked path: it takes the
target slot's backup first, refuses unless the running game can take this state, and reads every byte back. A state
that was written joins the Slot Library (handoff_try.keep_built).
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import subprocess
import tempfile
import threading
import uuid
from collections import OrderedDict
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from sm64_events.slotlibrary.usamune import MarioMoved

log = logging.getLogger("sm64.scene_lab")

CHILD_SECONDS = 120.0
KEEP_BUILT = 8                     # built states a page can still write; each is one slot's 156,768 bytes
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
GROWING = "This scene is still being recorded. Try again when it ends."
NO_MAP = "The lab config does not name the symbol map."
NO_BUILDER = "The save state could not be made. The Trainer's log says why."

# The running game's answers, in words for the page (handoff_try.GameDiffers' kinds).
GAME_WORDS = {
    "build": "This game runs another Usamune version than the recording.",
    "no-mario": "Mario is not in a level yet. Enter the level, then press again.",
    "place": "The game is in level {live_level} area {live_area}. This save state is for level {level} area {area}. "
             "Go there, then press again.",
    "mario": "Mario's place in the game's memory changed while the Trainer checked it. Press again.",
    "unclear": "This save state has a part the Trainer cannot move to Mario's new place safely. Nothing was written.",
    "loaded": "This level loaded in a different way. Leave the level, enter it again, then press again.",
}


class MomentBody(BaseModel):
    package: str | None = Field(default=None, min_length=1, max_length=255)
    attempt: int | None = Field(default=None, ge=0)
    frame: int = Field(ge=0)

    @model_validator(mode="after")
    def one_of_them(self):
        if (self.package is None) == (self.attempt is None):
            raise ValueError("Name a package or an attempt.")
        return self


class WriteBody(BaseModel):
    slot: int = Field(ge=1, le=9)


def run_builder(config, path: Path, frame: int, register=lambda child: None) -> tuple[dict, bytes | None]:
    """(what moment_slot.py printed, the slot's bytes or None). Blocking: a thread's work, on a child's CPU."""
    from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
    tools = Path(__file__).resolve().parents[3] / "tools" / "scene_lab"
    with tempfile.TemporaryDirectory(prefix="sm64-moment-") as folder:
        out = Path(folder) / "slot.bin"
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | _BELOW_NORMAL
        argv, env = python_child("moment_slot", "--scene-lab-moment", ["build", "--package", str(path), "--rom", str(config.rom),
                                 "--map", str(config.map), "--frame", str(frame), "--out", str(out)], site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(tools), env.get("PYTHONPATH"))))
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 encoding="utf-8", errors="replace", env=env, **spawn)
        register(child)
        try:
            stdout, stderr = child.communicate(timeout=CHILD_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            log.warning("A save state build ran past %s s.", CHILD_SECONDS)
            return {"refused": "builder", "reason": NO_BUILDER}, None
        finally:
            register(None)
        if child.returncode != 0 or not stdout.strip():
            log.warning("A save state build ended with %s: %s", child.returncode, stderr[-800:])
            return {"refused": "builder", "reason": NO_BUILDER}, None
        answer = json.loads(stdout.strip().splitlines()[-1])
        return answer, out.read_bytes() if "refused" not in answer else None


class SceneMoments:
    """The built states a page can still write, and the one build running."""

    def __init__(self, lab, *, builder=run_builder, game=None, library=None, backups: Path | None = None):
        self.lab, self.builder, self.game, self.library, self.backups = lab, builder, game, library, backups
        self.built: OrderedDict[str, dict] = OrderedDict()
        self.by_frame: dict[tuple, str] = {}
        self.turn = asyncio.Lock()
        self.child: subprocess.Popen | None = None
        self.child_lock = threading.Lock()

    def _note_child(self, child) -> None:
        with self.child_lock:
            self.child = child

    def shutdown(self) -> None:
        """The one child this owns, by its handle."""
        with self.child_lock:
            child = self.child
        if child is not None and child.poll() is None:
            child.kill()

    async def _source(self, body: MomentBody) -> tuple[Path, str]:
        api = importlib.import_module("sm64_events.server.scene_lab_api")   # the module in sys.modules, as the routes catch
        config = await asyncio.to_thread(api.LabConfig.load, self.lab.config_path)
        if body.attempt is None:
            return await asyncio.to_thread(config.package_path, body.package), body.package
        if self.lab.scenes is None:
            raise api.SceneRefused(self.lab.live_problem or "This lab has no attempt scenes.")
        try:
            path, sidecar = self.lab.scenes.path(body.attempt)
        except api._lab_module("attempt_scenes").SceneUnavailable as unavailable:
            raise api.SceneRefused(str(unavailable)) from None
        if sidecar is not None:
            raise api.SceneRefused(GROWING)
        return path, f"Attempt {body.attempt}"

    async def build(self, body: MomentBody) -> dict:
        """The built state with its level, area and Mario word, or {refused, reason}. LookupError: no such
        scene. SceneRefused or ConfigError: this scene cannot be read now."""
        api = importlib.import_module("sm64_events.server.scene_lab_api")   # the module in sys.modules, as the routes catch
        path, title = await self._source(body)
        config = await asyncio.to_thread(api.LabConfig.load, self.lab.config_path)
        if config.map is None:
            raise api.ConfigError(NO_MAP)
        stat = await asyncio.to_thread(path.stat)
        key = (str(path), stat.st_mtime_ns, stat.st_size, body.frame)
        async with self.turn:
            known = self.by_frame.get(key)
            if known in self.built:
                self.built.move_to_end(known)
                return self.built[known]["answer"]
            answer, data = await asyncio.to_thread(self.builder, config, path, body.frame, self._note_child)
            if data is None:
                return answer
            token = uuid.uuid4().hex
            answer = {**answer, "id": token, "exact": not answer.get("worked_out", False)}
            self.built[token] = {"answer": answer, "data": data, "source": f"{title} frame {body.frame}"}
            self.by_frame[key] = token
            while len(self.built) > KEEP_BUILT:
                gone, _ = self.built.popitem(last=False)
                self.by_frame = {k: v for k, v in self.by_frame.items() if v != gone}
            return answer

    def _write_blocking(self, entry: dict, slot: int) -> dict:
        from sm64_events.core.paths import slot_library_dir
        from sm64_events.server.scene_lab_api import _lab_module
        tool = _lab_module("handoff_try")
        answer = entry["answer"]
        image = tool.SlotImage(entry["data"], slot - 1, answer.get("seed"), bytes.fromhex(answer["segments"]),
                               answer["current_area"], answer["fingerprint"], entry["source"])
        try:
            memory = self.game() if self.game is not None else tool.attach(None)
            backup = tool.install(memory, image, self.backups or tool.backups_folder(), dry_run=False, write_seed=False)
        except tool.GameDiffers as differs:
            kind = differs.kinds[0]
            return {"refused": kind, "reason": GAME_WORDS[kind].format(level=image.level, area=image.area, **differs.seen)}
        except tool.Refusal as refused:
            return {"refused": "game", "reason": game_sentence(str(refused))}
        except MarioMoved:
            return {"refused": "moved", "reason": f"Mario's place in the game's memory changed during the write. Slot {slot} "
                                                  "is empty now, and its old save state is in the backup. Press again."}
        except tool.WriteFailed:
            return {"refused": "write", "reason": f"Slot {slot} could not be written. It is empty now, and its old "
                                                  "save state is in the backup."}
        kept = None
        try:
            kept = tool.keep_built(memory, image, self.library or tool.SlotLibrary(slot_library_dir()))[0].id
        except Exception:  # noqa: BLE001 - the slot is written; the library is a copy of it
            log.warning("A written save state did not join the Slot Library.", exc_info=True)
        return {"written": True, "slot": slot, "frame": answer["frame"], "backup": backup.name, "kept": kept}

    async def write(self, token: str, slot: int) -> dict:
        """LookupError: that build is gone. The game's own answer comes back as {refused, reason}."""
        entry = self.built.get(token)
        if entry is None:
            raise LookupError("That save state is gone. Press the slot again.")
        return await asyncio.to_thread(self._write_blocking, entry, slot)


def game_sentence(text: str) -> str:
    """handoff_try's refusals that are about the game as a whole, for a person at the page."""
    if "no Project64" in text or "no cartridge" in text:
        return "Project64 is not running a game. Start the game, then press again."
    if "not Usamune US" in text:
        return "The running game is not Usamune US."
    if "8 MB" in text:
        return "The game runs without the 8 MB of RAM that Usamune's save states need."
    return "The game refused this save state: " + text


def mount_moment_routes(router: APIRouter, moments: SceneMoments) -> None:
    from sm64_events.server.scene_lab_api import ConfigError, SceneRefused

    @router.post("/moment")
    async def build_moment(body: MomentBody):
        try:
            return await moments.build(body)
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None
        except (ConfigError, SceneRefused) as refused:
            raise HTTPException(409, str(refused)) from None

    @router.post("/moment/{token}/write")
    async def write_moment(token: str, body: WriteBody):
        try:
            return await moments.write(token, body.slot)
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None
        except ConfigError as refused:
            raise HTTPException(409, str(refused)) from None

