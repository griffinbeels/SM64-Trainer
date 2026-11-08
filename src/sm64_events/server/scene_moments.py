"""Save this moment: one frame of a scene as a Usamune save state, into a slot of the running game (lab only).

    POST /api/scene-lab/moment {package|attempt|ghost, frame}   -> the built state, or {refused, reason}
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
import contextlib
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
from sm64_events.tracking.eventlabel import place_name

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
    "place": "The game is in {live_place}. This save state is for {place}. Go there, then press again.",
    "mario": "Mario's place in the game's memory changed while the Trainer checked it. Press again.",
    "unclear": "This save state has a part the Trainer cannot move to Mario's new place safely. Nothing was written.",
    "loaded": "This level loaded in a different way. Leave the level, enter it again, then press again.",
    "version": "This save state is from the {source} version. The Trainer could not change it to work in your "
               "{target} game, so nothing was written.",
}
REGION_WORDS = {"us": "US", "jp": "JP"}


class MomentBody(BaseModel):
    package: str | None = Field(default=None, min_length=1, max_length=255)
    attempt: int | None = Field(default=None, ge=0)
    ghost: str | None = Field(default=None, min_length=1, max_length=512)   # its re-simulated scene (scene_resim.py)
    frame: int = Field(ge=0)

    @model_validator(mode="after")
    def one_of_them(self):
        if sum(value is not None for value in (self.package, self.attempt, self.ghost)) != 1:
            raise ValueError("Name a package, an attempt or a ghost.")
        return self


class WriteBody(BaseModel):
    slot: int = Field(ge=1, le=9)
    # The watched ghost's star: the written state joins its star set (or, with no star, the course default) at
    # this slot (slotlibrary/keeper.py). Absent: the course and star the game is in now.
    course_id: int | None = Field(default=None, ge=1, le=30)
    star_id: int | None = Field(default=None, ge=0, le=7)


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

    def __init__(self, lab, *, builder=run_builder, game=None, library=None, backups: Path | None = None,
                 atlases=None):
        self.lab, self.builder, self.game, self.library, self.backups = lab, builder, game, library, backups
        self.built: OrderedDict[str, dict] = OrderedDict()
        self.by_frame: dict[tuple, str] = {}
        self.turn = asyncio.Lock()
        self.child: subprocess.Popen | None = None
        self.atlas_child: subprocess.Popen | None = None
        self.child_lock = threading.Lock()
        self.checks: dict[str, bool] = {}          # package path -> its frames need the load check
        self._atlases = atlases

    def _note_child(self, child) -> None:
        with self.child_lock:
            self.child = child

    def _note_atlas_child(self, child) -> None:
        with self.child_lock:
            self.atlas_child = child

    def atlases(self):
        """Where a save state from the other Usamune build finds its atlas; built in a child on first use."""
        if self._atlases is None:
            from sm64_events.slotlibrary.atlas import default_store
            self._atlases = default_store(self._note_atlas_child)
        return self._atlases

    def shutdown(self) -> None:
        """The children this owns, by their handles."""
        with self.child_lock:
            children = (self.child, self.atlas_child)
        for child in children:
            if child is not None and child.poll() is None:
                child.kill()

    async def _source(self, body: MomentBody) -> tuple[Path, str]:
        api = importlib.import_module("sm64_events.server.scene_lab_api")   # the module in sys.modules, as the routes catch
        config = await asyncio.to_thread(api.LabConfig.load, self.lab.config_path)
        if body.ghost is not None:
            return await self._ghost_source(body)
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

    async def _ghost_source(self, body: MomentBody) -> tuple[Path, str]:
        """A re-simulated ghost scene's package, when ghost_resim.SAVE_STATES lets this frame be a save state."""
        api = importlib.import_module("sm64_events.server.scene_lab_api")
        from sm64_events.server.scene_resim import ResimRefused
        try:
            path, info = await asyncio.to_thread(self.lab.resim.ghost_scene_blocking, body.ghost)
        except ResimRefused as refused:
            raise api.SceneRefused(str(refused)) from None
        allowed = await asyncio.to_thread(self.lab.resim.allows, info, body.frame)
        if not allowed["allowed"]:
            raise api.SceneRefused(allowed["reason"])
        self.checks[str(path)] = allowed.get("check") == "load"
        return path, "Ghost"

    async def build(self, body: MomentBody) -> dict:
        """The built state with its level, area and Mario word, or {refused, reason}. LookupError: no such
        scene. SceneRefused or ConfigError: this scene cannot be read now."""
        api = importlib.import_module("sm64_events.server.scene_lab_api")   # the module in sys.modules, as the routes catch
        resim = getattr(self.lab, "resim", None)
        if body.ghost is not None and resim is not None and resim.is_building(body.ghost):
            config = await asyncio.to_thread(api.LabConfig.load, self.lab.config_path)
            if config.map is None:
                raise api.ConfigError(NO_MAP)
            async with self.turn:
                done = await asyncio.to_thread(self._built_part_blocking, body, config, resim, api)
            if done is not None:
                answer, data = done
                return answer if data is None else self._keep(answer, data, f"Ghost frame {body.frame}", None)
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
            if body.ghost is not None and self.checks.get(str(path)):     # a rebuilt start: load it in the simulation first
                verdict = await asyncio.to_thread(self.lab.resim.check_slot_blocking, body.ghost, path, body.frame, data)
                if not verdict["ok"]:
                    return {"refused": "check", "reason": verdict["reason"]}
                answer = {**answer, "checked": {"frames": verdict["frames"], "compared": verdict["compared"]}}
            return self._keep(answer, data, f"{title} frame {body.frame}", key)

    def _keep(self, answer: dict, data: bytes, source: str, key) -> dict:
        token = uuid.uuid4().hex
        answer = {**answer, "id": token, "exact": not answer.get("worked_out", False),
                  "build": answer.get("build", "us")}
        self.built[token] = {"answer": answer, "data": data, "source": source}
        if key is not None:
            self.by_frame[key] = token
        while len(self.built) > KEEP_BUILT:
            gone, _ = self.built.popitem(last=False)
            self.by_frame = {k: v for k, v in self.by_frame.items() if v != gone}
        return answer

    def _built_part_blocking(self, body: MomentBody, config, resim, api) -> tuple[dict, bytes | None] | None:
        """A save state of a ghost's scene still being built (the lead's ruling, 2026-11-08): a frame the build has
        written, before any parting it found so far, is built from the part written so far and load-checked with the
        ghost's own pads, exactly as from the whole scene; a frame past it is refused {"refused": "building"}.
        None when the build ended meanwhile (the whole scene answers then)."""
        with resim.built_part(body.ghost) as part:
            if part is None:
                return None
            path, info = part
            allowed = resim.allows(info, body.frame)
            if allowed.get("building"):
                return {"refused": "building", "reason": allowed["reason"]}, None
            if not allowed["allowed"]:
                raise api.SceneRefused(allowed["reason"])
            answer, data = self.builder(config, path, body.frame, self._note_child)
            if data is None or allowed.get("check") != "load":
                return answer, data
            verdict = resim.check_slot_blocking(body.ghost, path, body.frame, data, first_record=info.get("first_record", 1))
            if not verdict["ok"]:
                return {"refused": "check", "reason": verdict["reason"]}, None
            return {**answer, "checked": {"frames": verdict["frames"], "compared": verdict["compared"]}}, data

    def _write_blocking(self, entry: dict, slot: int, course_id: int | None = None, star_id: int | None = None) -> dict:
        """The write and the star set it joins, under the star sets' lock when the server keeps them, so the slot
        worker never reads this slot half written or takes it for a save of his."""
        slot_sets = getattr(self.lab, "slot_sets", None)
        keeper = slot_sets() if slot_sets is not None else None
        with keeper.lock if keeper is not None else contextlib.nullcontext():
            return self._write_locked(entry, slot, keeper, course_id, star_id)

    def _write_locked(self, entry: dict, slot: int, keeper, course_id: int | None, star_id: int | None) -> dict:
        from sm64_events.core.paths import slot_library_dir
        from sm64_events.server.scene_lab_api import _lab_module
        tool = _lab_module("handoff_try")
        answer = entry["answer"]
        build = answer.get("build", "us")
        image = tool.SlotImage(entry["data"], slot - 1, answer.get("seed"), bytes.fromhex(answer["segments"]),
                               answer["current_area"], answer["fingerprint"], entry["source"], build)
        try:
            memory = self.game() if self.game is not None else tool.attach(None)
            installed = tool.install(memory, image, self.backups or tool.backups_folder(), dry_run=False,
                                     write_seed=False, atlases=self.atlases())
        except tool.GameDiffers as differs:
            kind = differs.kinds[0]
            seen = differs.seen
            if kind == "version":
                log.warning("A %s save state was not translated: %s", build.upper(), differs)
            return {"refused": kind, "reason": GAME_WORDS[kind].format(
                place=place_name(image.level, image.area),
                live_place=place_name(seen.get("live_level"), seen.get("live_area")),
                source=REGION_WORDS.get(build, build.upper()),
                target=REGION_WORDS["jp" if build == "us" else "us"])}
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
        written = {"written": True, "slot": slot, "frame": answer["frame"], "backup": installed.backup.name,
                   "kept": kept, "build": build, "set": None}
        if keeper is not None and kept is not None:
            from sm64_events.slotlibrary.usamune import SLOT_BYTES, slot_address
            try:
                written["set"] = keeper.claim(slot - 1, kept, memory.read(slot_address(slot - 1), SLOT_BYTES),
                                              course_id, star_id)
            except Exception:  # noqa: BLE001 - the slot is written; the set is a list of it
                log.warning("A written save state did not join its star set.", exc_info=True)
        translation = installed.translation
        if translation is not None:
            written["translated"] = {"from": translation.source, "to": translation.target,
                                     "words": translation.words(), "removed": len(translation.removed)}
        return written

    async def write(self, token: str, slot: int, course_id: int | None = None, star_id: int | None = None) -> dict:
        """LookupError: that build is gone. The game's own answer comes back as {refused, reason}."""
        entry = self.built.get(token)
        if entry is None:
            raise LookupError("That save state is gone. Press the slot again.")
        return await asyncio.to_thread(self._write_blocking, entry, slot, course_id, star_id)


def game_sentence(text: str) -> str:
    """handoff_try's refusals that are about the game as a whole, for a person at the page."""
    if "no Project64" in text or "no cartridge" in text:
        return "Project64 is not running a game. Start the game, then press again."
    if "is not Usamune" in text:
        return "The running game is not Usamune."
    if "has not checked" in text:
        return "This Trainer cannot write save states into this game's Usamune yet."
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
            return await moments.write(token, body.slot, body.course_id, body.star_id)
        except LookupError as missing:
            raise HTTPException(404, str(missing)) from None
        except ConfigError as refused:
            raise HTTPException(409, str(refused)) from None

