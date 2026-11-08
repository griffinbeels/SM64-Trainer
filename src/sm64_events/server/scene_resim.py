"""Re-simulated scenes for the Scene viewer (lab only): a ghost, or a course and act, becomes a stage.

    resim = SceneResim(lab)
    path, info = resim.ghost_scene_blocking(ref)           # a listed ghost, by the ref /ghosts gave
    path, info, sidecar = resim.ghost_scene_blocking(ref, progressive=True)   # as soon as its first frames are
    path, info = resim.stage_scene_blocking(course, act)   # a stage he may never have played
    resim.allows(info, package_frame)                      # the Save states row's answer for that frame
    resim.observe_cutter(event)                            # the cutter's events: a ready scene keeps its ghost's start

The work is tools/scene_lab/ghost_resim.py and stage_sim.py (their docstrings carry the measurements),
run in a below-normal child of its own (one at a time) so the server's interpreter never runs the
simulation. Measured 2026-11-08 on another player's 324-frame ghost, from his click to the scene's first picture
(fake renderer host): 0.9 s once its stage was warmed ahead (warm: GET /stage for a star makes it in the
background, idle priority on one core, ~10 s), 10 s for a course and act never made before, 0.4 s for a scene
made before; the whole scene is written ~6 s after the click.
Packages and stages are cached under the lab root's `resim` folder; a ghost's start frame is saved
beside the ghost recordings (sm64_events/ghost/start_frame.py, the ghost population's `starts`
folder), never in a clip and never uploaded.

`info` is what the page shows: "start" (ghost_start | downloaded_start | own_scene | another_reset |
simulated_reset),
"parting_frame" (the first ghost record where the simulated Mario leaves the recorded pose; None: never),
"first_record" (the package's frame 0 is that ghost record), "exact" (bytes | mario), "recorded_on" and
"simulated_on" (the ghost's cartridge and the lab's: "us" | "jp") and "save_states" (ghost_resim.SAVE_STATES,
the one switch).

The fast load (2026-11-08, his words: "Waiting 60 seconds to load a new ghost would be... really upsetting as a
user, to say the least. There's gotta be a fast load method."): the child writes the scene while it simulates
and publishes it as it grows (sim_scene.py), so a progressive ask answers at the first publication with the
package, its sidecar (the viewer opens it as a growing scene, scene_session.Growth) and a building table
({"building": True, "records", "parting_frame": None, save states closed}); finished(path) waits for the
final table once the scene is whole. Measured on a warm stage: the first frames are published ~0.3 s after
the child starts, and the rest follows at ~30 frames a second.
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
WARM_SECONDS = 120.0               # a stage warmed ahead: ~6 s cold; a stuck one is ended
_BELOW_NORMAL = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
_IDLE = getattr(subprocess, "IDLE_PRIORITY_CLASS", 0)


def _one_core(child: subprocess.Popen) -> None:
    """Pins a background child to the machine's last logical processor (Windows; elsewhere nothing)."""
    handle = getattr(child, "_handle", None)
    if handle is None or os.name != "nt":
        return
    import ctypes
    mask = 1 << max(0, min((os.cpu_count() or 1), 64) - 1)
    if not ctypes.windll.kernel32.SetProcessAffinityMask(ctypes.c_void_p(int(handle)), ctypes.c_size_t(mask)):
        log.info("A background re-simulation child could not be held to one core.")
CAPTURE_RETRY_SECONDS = (0.0, 20.0, 90.0)   # a run's clip is committed around the time its scene is ready
NO_SIMULATION = "The ghost could not be re-simulated. The Trainer's log says why."
BUILDING = "This ghost's scene is still being built. Its save states come when it is done."


class ResimRefused(Exception):
    """This ghost or stage cannot become a scene; the sentence says why."""


class Build:
    """One child's build that a page may open before it ends: `ready` once its first frames are published, or
    once it ended; `done` once it ended, with `answer` ({"path", "info"}) or `error` (a sentence)."""

    def __init__(self, ref=None):
        self.ref = ref
        self.ready, self.done = threading.Event(), threading.Event()
        self.published: dict | None = None          # the child's first publication: package, sidecar, start, total
        self.answer: dict | None = None
        self.error: str | None = None

    def note(self, progress: dict) -> None:
        if progress.get("published") and self.published is None:
            self.published = progress
            self.ready.set()

    def building_info(self) -> dict:
        """The resim table while the scene builds: what is known before the last frame."""
        published = self.published or {}
        return {"start": published.get("start"), "building": True, "records": (published.get("total") or 0) + 1,
                "first_record": 1, "parting_frame": None, "save_states": {"allowed": False, "reason": BUILDING}}


def act_of_target(target) -> int | None:
    """The act a star ghost was played in: a course's stars 1-6 (entity_key "course-star", star from 0) are
    its acts. None for the 100-coin star, a castle star and a segment, which name no act (the build then
    takes the act of his reset of that level, else act 1, and says the act was assumed).

    Measured 2026-11-08 on another player's Whomp's Fortress ghost from the community cache: acts 1 and 2
    both kept Mario on all 318 recorded poses (act 4 parted at record 115), so the poses cannot pick the
    act; only the star can, and the wrong act draws the wrong objects around an exact Mario."""
    if not isinstance(target, dict) or target.get("kind") != "star":
        return None
    try:
        course, star = (int(part) for part in str(target.get("entity_key", "")).split("-"))
    except ValueError:
        return None
    return star + 1 if 1 <= course <= 15 and 0 <= star <= 5 else None


class SceneResim:
    def __init__(self, lab, *, starts_root: Path | None = None, runner=None):
        self.lab = lab
        self._starts_root = starts_root
        self.runner = runner or self._run_child
        self.turn = threading.Lock()                  # one simulation at a time
        self.progress: dict | None = None             # the running build, for /status: {what, stage, done, total}
        self.child: subprocess.Popen | None = None
        self.child_lock = threading.Lock()
        self.stopped = threading.Event()
        self.building: Build | None = None            # the newest progressive build (one runs at a time)
        self.warm_child: subprocess.Popen | None = None   # the one background child (warm)
        self.warmed: set[tuple[int, int]] = set()     # (course, act) warmed or tried this server run

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
    @staticmethod
    def _spawn(args: list[str], *, background: bool = False) -> subprocess.Popen:
        """ghost_resim's CLI in a child: below normal priority for a build he is waiting on; at idle priority on
        one core for background work (a stage warmed ahead), so it only ever takes time nothing else wants."""
        from sm64_events.core.childproc import python_child, quiet_spawn_kwargs
        tools = Path(__file__).resolve().parents[3] / "tools" / "scene_lab"
        spawn = quiet_spawn_kwargs()
        spawn["creationflags"] = spawn.get("creationflags", 0) | (_IDLE if background else _BELOW_NORMAL)
        argv, env = python_child("ghost_resim", "--scene-lab-resim", args, site_packages=True)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(tools), env.get("PYTHONPATH"))))
        child = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 encoding="utf-8", errors="replace", env=env, **spawn)
        if background:
            _one_core(child)
        return child

    def _run_child(self, args: list[str], on_progress=None) -> dict:
        """The child's answer (its last line); its progress lines go to self.progress, and to on_progress, as
        they come."""
        with self.turn:
            if self.stopped.is_set():
                raise ResimRefused("The scene lab is stopping.")
            child = self._spawn(args)
            with self.child_lock:
                self.child = child
            watchdog = threading.Timer(CHILD_SECONDS, child.kill)
            watchdog.start()
            errors: list[str] = []
            reader = threading.Thread(target=lambda: errors.append(child.stderr.read()), daemon=True)
            reader.start()
            last = None
            self.progress = {"what": args[0], "stage": "starting", "done": 0, "total": 0}
            try:
                for line in child.stdout:
                    line = line.strip()
                    if line.startswith('{"progress"'):
                        told = json.loads(line)["progress"]
                        self.progress = {"what": args[0], **{key: told[key] for key in ("stage", "done", "total")
                                                             if key in told}}
                        if on_progress is not None:
                            on_progress(told)
                    elif line:
                        last = line
                child.wait()
            finally:
                watchdog.cancel()
                reader.join(timeout=5)
                self.progress = None
                with self.child_lock:
                    self.child = None
        if child.returncode != 0 or last is None:
            log.warning("A ghost re-simulation ended with %s: %s", child.returncode, "".join(errors)[-800:])
            raise ResimRefused(NO_SIMULATION)
        answer = json.loads(last)
        if "error" in answer:                         # the page shows the sentence; the log keeps the child's detail
            log.info("A ghost re-simulation refused: %s %s", answer["error"], "".join(errors)[-800:])
            raise ResimRefused(answer["error"])
        return answer

    def _scene_args(self) -> list[str]:
        """His attempt scenes' folders: this journal's, and the one scenes lived in before they were per journal.
        A server with no journal open has none (a build then starts from a start file or a reset)."""
        folders = []
        try:
            folders.append(self.lab._scene_folder())
        except Exception as error:  # noqa: BLE001 - attempt_scenes.NoJournal: no journal, no scenes of his
            log.info("Re-simulating without this journal's attempt scenes: %s", error)
        legacy = self.lab._lab_root() / "attempts"
        folders += [legacy] if legacy not in folders else []
        return [part for folder in folders for part in ("--scenes", str(folder))]

    def _common(self, config) -> list[str]:
        return ["--lab-root", str(self.lab._lab_root()), "--cache", str(self.cache()), "--rom", str(config.rom),
                "--map", str(config.map)]

    def shutdown(self) -> None:
        """The children this owns (a build, a warm), by their handles."""
        self.stopped.set()
        with self.child_lock:
            children = (self.child, self.warm_child)
        for child in children:
            if child is not None and child.poll() is None:
                child.kill()

    # ---------------------------------------------------------------- scenes
    def ghost_scene_blocking(self, ref, *, act: int | None = None, progressive: bool = False):
        """A listed ghost's re-simulated scene (LookupError: no such ghost; ResimRefused: it cannot be one).
        The act is the ghost's star's (act_of_target), so another player's ghost of a course he never
        reset in that act still gets that act's objects. Progressive: (path, info, sidecar) as soon as the
        scene's first frames are published, the build going on (sidecar None: the scene was whole already)."""
        loaded = self.lab._load_ghost(ref)
        act = act or act_of_target(loaded.get("target"))
        if not progressive:
            return self.clip_scene_blocking(loaded["clip"], act=act)
        return self._progressive(ref, loaded["clip"], act)

    def is_building(self, ref) -> bool:
        build = self.building
        return build is not None and build.ref == ref and not build.done.is_set()

    def _progressive(self, ref, clip: bytes, act: int | None) -> tuple[Path, dict, Path | None]:
        build = Build(ref)

        def work() -> None:
            try:
                path, info = self.clip_scene_blocking(clip, act=act, on_progress=build.note)
                build.answer = {"path": str(path), "info": info}
            except ResimRefused as refused:
                build.error = str(refused)
            except Exception as error:  # noqa: BLE001 - the build's thread reports every end through the build
                log.warning("A ghost re-simulation failed: %s", error, exc_info=True)
                build.error = NO_SIMULATION
            finally:
                build.done.set()
                build.ready.set()
        self.building = build
        threading.Thread(target=work, name="resim-build", daemon=True).start()
        build.ready.wait()
        if build.done.is_set():                       # it ended before publishing (a cached scene), or failed
            if build.error is not None:
                raise ResimRefused(build.error)
            return Path(build.answer["path"]), build.answer["info"], None
        return Path(build.published["package"]), build.building_info(), Path(build.published["sidecar"])

    def finished(self, path: Path, timeout: float = CHILD_SECONDS) -> tuple[dict | None, str | None]:
        """A progressive build of `path` waited for: (its final table, None) or (None, why it failed)."""
        build = self.building
        if build is None or build.published is None or Path(build.published["package"]) != Path(path):
            return None, None
        build.done.wait(timeout)
        if build.answer is not None and Path(build.answer["path"]) == Path(path):
            return build.answer["info"], None
        return None, build.error or NO_SIMULATION

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

    def clip_scene_blocking(self, clip: bytes, *, act: int | None = None, on_progress=None) -> tuple[Path, dict]:
        config = self._config()
        with tempfile.TemporaryDirectory(prefix="sm64-resim-") as folder:
            clip_path = Path(folder) / "ghost.clip"
            clip_path.write_bytes(clip)
            args = ["build", *self._common(config), "--clip", str(clip_path), *self._scene_args(),
                    "--starts", str(self.starts_root())]
            if act is not None:
                args += ["--act", str(act)]
            answer = self.runner(args, on_progress=on_progress) if on_progress is not None else self.runner(args)
        return Path(answer["path"]), answer["info"]

    def stage_scene_blocking(self, course: int, act: int, area: int = 1, frames: int = 150) -> tuple[Path, dict]:
        """The simulated practice reset of that course's level and act, played on with no input."""
        config = self._config()
        answer = self.runner(["stage", *self._common(config), "--course", str(course), "--area", str(area),
                              "--act", str(act), "--frames", str(frames)])
        return Path(answer["path"]), answer["info"]

    def warm(self, course: int | None, act: int | None) -> None:
        """Makes that course and act's practice reset ahead, in the background (a stage from the cartridge alone
        costs ~6 s the first time, a cached one ~0.2 s), so a ghost of the star he is looking at starts at once.
        One at a time, each once per server run, at idle priority on one core; never waits for anything."""
        if course is None or act is None or self.stopped.is_set():
            return
        with self.child_lock:
            if (course, act) in self.warmed or self.warm_child is not None:
                return
            self.warmed.add((course, act))
        try:
            config = self._config()
            with Path(config.rom).open("rb") as handle:
                header = handle.read(0x40)
        except (ResimRefused, OSError):
            return
        from sm64_events.memory.version_probe import version_from_header
        if version_from_header(header) is None:            # not a cartridge a stage can be made from
            return
        args = ["warm", *self._common(config), "--course", str(course), "--act", str(act), *self._scene_args()]
        threading.Thread(target=self._warm, args=(args,), name=f"resim-warm-{course}-{act}", daemon=True).start()

    def _warm(self, args: list[str]) -> None:
        try:
            child = self._spawn(args, background=True)
        except OSError as error:
            log.info("A stage could not be warmed: %s", error)
            return
        with self.child_lock:
            self.warm_child = child
        try:
            output, errors = child.communicate(timeout=WARM_SECONDS)
            log.info("stage warmed: %s", (output.strip().splitlines() or [errors.strip()[-300:]])[-1])
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
        finally:
            with self.child_lock:
                self.warm_child = None

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
