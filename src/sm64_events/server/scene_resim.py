"""Re-simulated scenes for the Scene viewer (lab only): a ghost, or a course and act, becomes a stage.

    resim = SceneResim(lab)
    path, info = resim.ghost_scene_blocking(ref)           # a listed ghost, by the ref /ghosts gave
    path, info = resim.stage_scene_blocking(course, act)   # a stage he may never have played
    resim.allows(info, package_frame)                      # the Save states row's answer for that frame
    resim.observe_cutter(event)                            # the cutter's events: a ready scene keeps its ghost's start

The work is tools/scene_lab/ghost_resim.py and stage_sim.py (their docstrings carry the measurements),
run in a below-normal child of its own (one at a time) so the server's interpreter never runs the
simulation: a build is ~30 s of CPU, a new stage ~40 s more, and a cached one answers in ~0.01 s.
Packages and stages are cached under the lab root's `resim` folder; a ghost's start frame is saved
beside the ghost recordings (sm64_events/ghost/start_frame.py, the ghost population's `starts`
folder), never in a clip and never uploaded.

`info` is what the page shows: "start" (ghost_start | own_scene | another_reset | simulated_reset),
"parting_frame" (the first ghost record where the simulated Mario leaves the recorded pose; None: never),
"first_record" (the package's frame 0 is that ghost record), "exact" (bytes | mario) and "save_states"
(ghost_resim.SAVE_STATES, the one switch).
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("sm64.scene_lab")

CHILD_SECONDS = 900.0              # a long ghost and a new stage; a stuck child is ended
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
CAPTURE_RETRY_SECONDS = (0.0, 20.0, 90.0)   # a run's clip is committed around the time its scene is ready
NO_SIMULATION = "The ghost could not be re-simulated. The Trainer's log says why."


class ResimRefused(Exception):
    """This ghost or stage cannot become a scene; the sentence says why."""


class SceneResim:
    def __init__(self, lab, *, starts_root: Path | None = None, runner=None):
        self.lab = lab
        self._starts_root = starts_root
        self.runner = runner or self._run_child
        self.turn = threading.Lock()                  # one simulation at a time
        self.child: subprocess.Popen | None = None
        self.child_lock = threading.Lock()
        self.stopped = threading.Event()

    # ---------------------------------------------------------------- where things live
    def starts_root(self) -> Path:
        if self._starts_root is None:
            from sm64_events.core.paths import ghost_population_root
            self._starts_root = ghost_population_root()
        return Path(self._starts_root)

    def cache(self) -> Path:
        return self.lab._lab_root() / "resim"

    def _config(self):
        from sm64_events.server.scene_lab_api import LabConfig
        config = LabConfig.load(self.lab.config_path)
        if config.map is None:
            raise ResimRefused("The lab config does not name the symbol map, which the simulation needs.")
        return config

    # ---------------------------------------------------------------- the child
    def _run_child(self, args: list[str]) -> dict:
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        tools = Path(__file__).resolve().parents[3] / "tools" / "scene_lab"
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | _BELOW_NORMAL
        argv, env = python_child("ghost_resim", "--scene-lab-resim", args, site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(tools), env.get("PYTHONPATH"))))
        with self.turn:
            if self.stopped.is_set():
                raise ResimRefused("The scene lab is stopping.")
            child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     encoding="utf-8", errors="replace", env=env, **spawn)
            with self.child_lock:
                self.child = child
            try:
                stdout, stderr = child.communicate(timeout=CHILD_SECONDS)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                log.warning("A ghost re-simulation ran past %s s.", CHILD_SECONDS)
                raise ResimRefused(NO_SIMULATION) from None
            finally:
                with self.child_lock:
                    self.child = None
        if child.returncode != 0 or not stdout.strip():
            log.warning("A ghost re-simulation ended with %s: %s", child.returncode, stderr[-800:])
            raise ResimRefused(NO_SIMULATION)
        answer = json.loads(stdout.strip().splitlines()[-1])
        if "error" in answer:
            raise ResimRefused(answer["error"])
        return answer

    def _common(self, config) -> list[str]:
        return ["--lab-root", str(self.lab._lab_root()), "--cache", str(self.cache()), "--rom", str(config.rom),
                "--map", str(config.map)]

    def shutdown(self) -> None:
        """The one child this owns, by its handle."""
        self.stopped.set()
        with self.child_lock:
            child = self.child
        if child is not None and child.poll() is None:
            child.kill()

    # ---------------------------------------------------------------- scenes
    def ghost_scene_blocking(self, ref, *, act: int | None = None) -> tuple[Path, dict]:
        """A listed ghost's re-simulated scene (LookupError: no such ghost; ResimRefused: it cannot be one)."""
        loaded = self.lab._load_ghost(ref)
        return self.clip_scene_blocking(loaded["clip"], act=act)

    def check_slot_blocking(self, ref, package: Path, frame: int, slot: bytes) -> dict:
        """The Save states row's load check for one frame of a ghost's rebuilt scene (ghost_resim.check_slot_load):
        {"ok", "reason", "frames", "compared"}. Run in the child; the running game is never touched."""
        loaded = self.lab._load_ghost(ref)
        config = self._config()
        _path, info = self.clip_scene_blocking(loaded["clip"])
        with tempfile.TemporaryDirectory(prefix="sm64-resim-") as folder:
            clip_path, slot_path = Path(folder) / "ghost.clip", Path(folder) / "state.slot"
            clip_path.write_bytes(loaded["clip"])
            slot_path.write_bytes(slot)
            return self.runner(["check-slot", *self._common(config), "--package", str(package), "--frame", str(frame),
                                "--slot", str(slot_path), "--clip", str(clip_path),
                                "--first-record", str(info.get("first_record", 1))])

    def clip_scene_blocking(self, clip: bytes, *, act: int | None = None) -> tuple[Path, dict]:
        config = self._config()
        with tempfile.TemporaryDirectory(prefix="sm64-resim-") as folder:
            clip_path = Path(folder) / "ghost.clip"
            clip_path.write_bytes(clip)
            args = ["build", *self._common(config), "--clip", str(clip_path), "--scenes", str(self.lab._scene_folder()),
                    "--starts", str(self.starts_root())]
            if act is not None:
                args += ["--act", str(act)]
            answer = self.runner(args)
        return Path(answer["path"]), answer["info"]

    def stage_scene_blocking(self, course: int, act: int, area: int = 1, frames: int = 150) -> tuple[Path, dict]:
        """The simulated practice reset of that course's level and act, played on with no input."""
        config = self._config()
        answer = self.runner(["stage", *self._common(config), "--course", str(course), "--area", str(area),
                              "--act", str(act), "--frames", str(frames)])
        return Path(answer["path"]), answer["info"]

    def allows(self, info: dict, package_frame: int) -> dict:
        """The Save states row's answer for one frame of a re-simulated scene (ghost_resim.save_states)."""
        from sm64_events.server.scene_lab_api import _lab_module
        module = _lab_module("ghost_resim")
        return module.save_states(info["start"], frame=package_frame + info.get("first_record", 1),
                                  parting=info.get("parting_frame"))

    # ---------------------------------------------------------------- new recordings keep their start
    def observe_cutter(self, event: dict) -> None:
        """The cutter's event (attempt_scenes' child): a scene that became ready keeps its run's start
        beside the run's ghost, on a thread of its own (the cutter's reader never waits)."""
        if event.get("state") != "ready" or event.get("growing") or not event.get("path") or "id" not in event:
            return
        threading.Thread(target=self._capture, args=(event["id"], Path(event["path"])),
                         name=f"resim-start-{event['id']}", daemon=True).start()

    def _capture(self, attempt_id: int, scene: Path) -> None:
        for delay in CAPTURE_RETRY_SECONDS:
            if self.stopped.wait(delay):
                return
            try:
                clip = self._run_clip(attempt_id)
                if clip is None:
                    continue
                config = self._config()
                with tempfile.TemporaryDirectory(prefix="sm64-resim-") as folder:
                    clip_path = Path(folder) / "ghost.clip"
                    clip_path.write_bytes(clip)
                    answer = self.runner(["capture", *self._common(config), "--scene", str(scene),
                                          "--clip", str(clip_path), "--starts", str(self.starts_root())])
                log.info("attempt %s: ghost start %s", attempt_id, answer.get("path") or "not this scene's")
                return
            except (ResimRefused, OSError, ValueError) as error:
                log.warning("attempt %s: its ghost start was not kept: %s", attempt_id, error)
                return

    def _run_clip(self, attempt_id: int) -> bytes | None:
        """His run's committed clip for that attempt, through the ghost catalog's door; None while there is none."""
        ghosts = getattr(self.lab, "ghosts", None)
        replay = getattr(ghosts, "replay", None)
        if ghosts is None or replay is None or not hasattr(replay, "attempt"):
            return None
        try:
            found = ghosts.run_clip(replay.attempt(attempt_id))
        except (LookupError, OSError, ValueError):
            return None
        if isinstance(found, str):
            return None
        return Path(found[0]).read_bytes()
