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

import contextlib
import hashlib
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
BUILDING = "This part of the run is still being built."


class ResimRefused(Exception):
    """This ghost or stage cannot become a scene; the sentence says why."""


class Build:
    """One child's build that a page may open before it ends: `ready` once its first frames are published, or
    once it ended; `done` once it ended, with `answer` ({"path", "info"}) or `error` (a sentence); `cancelled` when
    a newer open (or the cancel route) ended it."""

    def __init__(self, ref=None, clip: bytes | None = None):
        self.ref, self.clip = ref, clip
        self.ready, self.done = threading.Event(), threading.Event()
        self.published: dict | None = None          # the child's first publication: package, sidecar, start, total
        self.latest: dict | None = None             # its newest: built_to, parting, save_states too
        self.answer: dict | None = None
        self.error: str | None = None
        self.cancelled = False

    def note(self, progress: dict) -> None:
        if progress.get("published"):
            self.latest = progress
            if self.published is None:
                self.published = progress
                self.ready.set()

    def building_info(self) -> dict:
        """The resim table while the scene builds. "built_to": the newest ghost record in the scene so far (every one
        compared before it was written); "parting_frame": the first parting found so far, or None; save states are
        offered for the built part before any parting, under the start's own rule."""
        latest = self.latest or {}
        return {"start": latest.get("start"), "building": True, "records": (latest.get("total") or 0) + 1,
                "first_record": 1, "built_to": latest.get("built_to") or 0, "parting_frame": latest.get("parting"),
                "save_states": latest.get("save_states") or {"allowed": False, "reason": BUILDING}}


def prebuild_key(sha256: str) -> tuple:
    """The warmer's key for one ghost's scene made ahead: its clip's hash, as the scene cache names it."""
    return ("ghost", sha256[:16])


def _list_tier() -> int:
    from sm64_events.server.scene_warm import LIST
    return LIST


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


def stage_of_target(target) -> tuple[int, int, int] | None:
    """(level, area, act) of a star ghost's stage, the warmer's key (scene_warm.stage_of); None where the target names
    no course level."""
    from sm64_events.server.scene_warm import stage_of
    if not isinstance(target, dict) or target.get("kind") != "star":
        return None
    try:
        course, star = (int(part) for part in str(target.get("entity_key", "")).split("-"))
    except ValueError:
        return None
    return stage_of(course, star)


class SceneResim:
    def __init__(self, lab, *, starts_root: Path | None = None, runner=None, pace=None):
        from sm64_events.server.scene_warm import StageWarmer
        self.lab = lab
        self._starts_root = starts_root
        self.runner = runner or self._run_child
        self.turn = threading.Lock()                  # one simulation at a time
        self.progress: dict | None = None             # the running build, for /status: {what, stage, done, total}
        self.child: subprocess.Popen | None = None    # the one build or stage child (turn)
        self.children: set[subprocess.Popen] = set()  # every child running, a load check beside a build included
        self.ended: set[subprocess.Popen] = set()     # children cancel() ended (their exit is no failure)
        self.cancelled_packages: set[str] = set()     # packages whose build was cancelled (their scenes stop growing)
        self.child_lock = threading.Lock()
        self.stopped = threading.Event()
        self.building: Build | None = None            # the newest progressive build (one runs at a time)
        self._recipe: tuple | None = None             # ((rom path, size, mtime), stage_sim.recipe): the cache's names
        self.warmer = StageWarmer(self._spawn_warm, pace=pace, route=self._route_stars, answered=self._warm_answered,
                                  spawn_ghost=self._spawn_prebuild, finished=self._prebuilt)

    # ---------------------------------------------------------------- where things live
    def starts_root(self) -> Path:
        if self._starts_root is None:
            from sm64_events.core.paths import ghost_population_root
            self._starts_root = ghost_population_root()
        return Path(self._starts_root)

    def cache(self) -> Path:
        return self.lab._lab_root() / "resim"

    def _config(self):
        """The lab's config with the ROM and the map a simulation needs, else ResimRefused with the sentence why (the
        installed app's config says it in the player's words: core/lab_inputs.py)."""
        config = self.lab.config()
        if config.rom is None:                        # the installed app before its ROM store found one
            raise ResimRefused(config.rom_problem)
        if config.map is None:
            if config.in_app:
                from sm64_events.core.lab_inputs import NO_MAP
                raise ResimRefused(NO_MAP)
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

    def _run_child(self, args: list[str], on_progress=None, exclusive: bool = True) -> dict:
        """The child's answer (its last line); its progress lines go to self.progress, and to on_progress, as
        they come. Exclusive (a build, a stage): one at a time, and /status shows it; a load check runs beside a
        build (a save state of a scene still being built), on its own."""
        with self.turn if exclusive else contextlib.nullcontext():
            if self.stopped.is_set():
                raise ResimRefused("The scene lab is stopping.")
            child = self._spawn(args)
            with self.child_lock:
                self.children.add(child)
                if exclusive:
                    self.child = child
            watchdog = threading.Timer(CHILD_SECONDS, child.kill)
            watchdog.start()
            errors: list[str] = []
            reader = threading.Thread(target=lambda: errors.append(child.stderr.read()), daemon=True)
            reader.start()
            last = None
            if exclusive:
                self.progress = {"what": args[0], "stage": "starting", "done": 0, "total": 0}
            try:
                for line in child.stdout:
                    line = line.strip()
                    if line.startswith('{"progress"'):
                        told = json.loads(line)["progress"]
                        if exclusive:
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
                with self.child_lock:
                    self.children.discard(child)
                    if exclusive:
                        self.progress = None
                        self.child = None
        if child.returncode != 0 or last is None:
            if child in self.ended:                   # cancel() ended it: nothing went wrong
                self.ended.discard(child)
                raise ResimRefused("cancelled")
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
        """The children this owns (a build, a load check, a warm), by their handles."""
        self.stopped.set()
        self.warmer.stop()
        with self.child_lock:
            children = list(self.children)
        for child in children:
            if child.poll() is None:
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
        return self._progressive(ref, loaded["clip"], act, stage_of_target(loaded.get("target")))

    def is_building(self, ref) -> bool:
        build = self.building
        return build is not None and build.ref == ref and not build.done.is_set()

    def cancel(self, keep_ref=None) -> bool:
        """Ends the running progressive build (POST /open/cancel, or a newer open: one build at a time) unless it is
        `keep_ref`'s. Its child ends; its package goes unless it was whole already (its note written). The scene
        it fed keeps the frames it has. True when a build was ended."""
        build = self.building
        if build is None or build.done.is_set() or (keep_ref is not None and build.ref == keep_ref):
            return False
        build.cancelled = True
        published = build.published or {}
        package = Path(published["package"]) if published.get("package") else None
        if package is not None:                       # first, so its scene stops growing before the file goes
            self.cancelled_packages.add(str(package))
        with self.child_lock:
            child = self.child
        if child is not None and child.poll() is None:
            self.ended.add(child)
            child.kill()
        build.done.wait(30)
        if package is not None and not package.with_suffix(".json").is_file():
            for path in (package, Path(published["sidecar"])):
                with contextlib.suppress(OSError):
                    path.unlink(missing_ok=True)
        return True

    def _progressive(self, ref, clip: bytes, act: int | None, stage: tuple[int, int, int] | None):
        running = self.building
        if running is not None and running.ref == ref and not running.done.is_set():
            build = running                           # the same ghost again: join its build
        else:
            self.cancel()
            self.warmer.join(stage)                   # a warm of its own stage: wait for it rather than race it
            self.warmer.join(prebuild_key(hashlib.sha256(clip).hexdigest()))   # its scene made ahead, the same way
            build = Build(ref, clip)

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
            self.cancelled_packages.discard(str(Path(build.answer["path"])))
            return Path(build.answer["path"]), build.answer["info"], None
        self.cancelled_packages.discard(str(Path(build.published["package"])))   # written anew: it grows again
        return Path(build.published["package"]), build.building_info(), Path(build.published["sidecar"])

    def was_cancelled(self, path: Path) -> bool:
        """Whether the build writing `path` was cancelled (its scene keeps the frames it has, and grows no more)."""
        return path is not None and str(Path(path)) in self.cancelled_packages

    def current(self, path: Path) -> dict | None:
        """The newest table of the build writing `path`, while it runs."""
        build = self.building
        if build is None or build.done.is_set() or build.published is None or Path(build.published["package"]) != Path(path):
            return None
        return build.building_info()

    def finished(self, path: Path, timeout: float = CHILD_SECONDS) -> tuple[dict | None, str | None]:
        """A progressive build of `path` waited for: (its final table, None), (None, why it failed), or (None, None)
        when it was cancelled or is not this path's (the scene keeps what it has)."""
        build = self.building
        if build is None or build.published is None or Path(build.published["package"]) != Path(path):
            return None, None
        build.done.wait(timeout)
        if build.cancelled:
            return None, None
        if build.answer is not None and Path(build.answer["path"]) == Path(path):
            return build.answer["info"], None
        return None, build.error or NO_SIMULATION

    @contextlib.contextmanager
    def built_part(self, ref):
        """(a whole package of the frames a running build of `ref` has written so far, its table), for a save state
        of a scene still being built; the package is the growing file's body under its newest publication (header,
        body, index: what scene_session.Growth reads), removed again after. None when no such build runs."""
        build = self.building
        if build is None or build.ref != ref or build.done.is_set() or build.latest is None:
            yield None
            return
        from sm64_events.server.scene_lab_api import _lab_module
        pk = _lab_module("package")
        latest = build.latest
        info = build.building_info()
        sidecar, package = Path(latest["sidecar"]), Path(latest["package"])
        try:
            published = sidecar.read_bytes()
        except FileNotFoundError:                     # whole since: the file itself
            yield package, info
            return
        header = pk.Header(*pk.HEADER.unpack_from(published))
        with package.open("rb") as handle:
            handle.seek(pk.HEADER.size)
            body = handle.read(header.index_offset - pk.HEADER.size)
        with tempfile.TemporaryDirectory(prefix="sm64-resim-part-") as folder:
            part = Path(folder) / package.name
            part.write_bytes(published[:pk.HEADER.size] + body + published[pk.HEADER.size:])
            yield part, info

    def check_slot_blocking(self, ref, package: Path, frame: int, slot: bytes, first_record: int | None = None) -> dict:
        """The Save states row's load check for one frame of a ghost's rebuilt scene (ghost_resim.check_slot_load):
        {"ok", "reason", "frames", "compared"}. Run in a child beside any build; the running game is never touched."""
        building = self.building if self.is_building(ref) else None
        clip = building.clip if building is not None else self.lab._load_ghost(ref)["clip"]
        if first_record is None:
            first_record = self.clip_scene_blocking(clip)[1].get("first_record", 1)
        config = self._config()
        with tempfile.TemporaryDirectory(prefix="sm64-resim-") as folder:
            clip_path, slot_path = Path(folder) / "ghost.clip", Path(folder) / "state.slot"
            clip_path.write_bytes(clip)
            slot_path.write_bytes(slot)
            return self.runner(["check-slot", *self._common(config), "--package", str(package), "--frame", str(frame),
                                "--slot", str(slot_path), "--clip", str(clip_path),
                                "--first-record", str(first_record)], exclusive=False)

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

    def warm(self, course: int | None, star: int | None, tier: int | None = None) -> None:
        """That star's stage made ahead, in the background (scene_warm.StageWarmer: its order, its one idle child on
        one core, paused while his game runs slow). Never waits for anything."""
        if self.stopped.is_set() or course is None:
            return
        from sm64_events.server import scene_warm
        self.warmer.ask(scene_warm.stage_of(course, star), scene_warm.CURRENT if tier is None else tier)

    def prebuild(self, path: Path, sha256: str, target) -> bool:
        """One of his PB ghosts' scene made ahead, after its stage, at the warmer's idle pace (scene_warm.ask_ghost),
        unless that scene was made before. `path` is the clip the race plays and `sha256` its hash (run_clip's
        manifest); a click on that ghost then joins it or reads the made scene. True when queued. Never waits."""
        if self.stopped.is_set() or not isinstance(sha256, str) or len(sha256) != 64 or self.scene_made(sha256):
            return False
        stage = stage_of_target(target)
        if stage is not None:
            self.warmer.ask(stage, _list_tier(), then_rest=False)
        self.warmer.ask_ghost(prebuild_key(sha256), (Path(path), sha256, act_of_target(target)), _list_tier())
        return True

    def scene_made(self, sha256: str) -> bool:
        """Whether a scene of the ghost with this clip hash is in the cache (ghost_resim names it by that hash)."""
        return any(self.cache().glob(f"ghost-{sha256[:16]}-*.json"))

    def made_holdings(self) -> list[dict]:
        """Every finished ghost scene in the cache, for Settings -> Ghost files (`ghost/storage.py`): {"ref" (its
        note's name), "sha16" (its clip's hash, as ghost_resim names it), "bytes", "paths", "in_use"}. A scene
        still growing (its .grow sidecar beside it) is not one yet and is never listed; the scene open in the
        Ghost Lab is in use. His call, 2026-11-09: a made scene counts in its ghost and goes with it."""
        root = self.cache()
        if not root.is_dir():
            return []
        open_path = getattr(self.lab, "open_path", None)
        found = []
        for note in sorted(root.glob("ghost-*.json")):
            scene = note.with_suffix(".sm64scene")
            parts = note.stem.split("-")
            if len(parts) < 3 or not scene.is_file() or note.with_suffix(".grow").exists():
                continue
            try:
                size = note.stat().st_size + scene.stat().st_size
            except OSError:
                continue
            busy = open_path is not None and Path(open_path).resolve() == scene.resolve()
            found.append({"ref": note.name, "sha16": parts[1], "bytes": size, "paths": (str(scene), str(note)),
                          "in_use": busy})
        return found

    def remove_made(self, refs) -> dict:
        """Deletes these made scenes (made_holdings' refs): {"deleted": [refs], "bytes", "skipped": [refs]}. One in
        use, or one Windows will not let go of, is skipped. A scene the warmer made ahead is not made again
        this server run (its key stays done)."""
        held = {row["ref"]: row for row in self.made_holdings()}
        deleted, skipped, freed = [], [], 0
        for ref in refs:
            row = held.get(ref)
            if row is None or row["in_use"]:
                skipped.append(ref)
                continue
            try:
                for path in row["paths"]:          # the scene first: a note with no scene is never read as made
                    Path(path).unlink(missing_ok=True)
            except OSError:
                skipped.append(ref)
                continue
            deleted.append(ref)
            freed += row["bytes"]
        return {"deleted": deleted, "bytes": freed, "skipped": skipped}

    def _spawn_prebuild(self, payload) -> subprocess.Popen:
        """ghost_resim's `build` of one PB ghost, in the warmer's idle background child; its clip copied beside the
        cache first (the race's file may change under it), removed again once the child ends (_prebuilt)."""
        path, sha256, act = payload
        config = self._config()
        clip = self.cache() / f"prebuild-{sha256[:16]}.clip"
        clip.parent.mkdir(parents=True, exist_ok=True)
        data = Path(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ResimRefused("This ghost's recording changed after it was listed.")
        clip.write_bytes(data)
        args = ["build", *self._common(config), "--clip", str(clip), *self._scene_args(),
                "--starts", str(self.starts_root())]
        return self._spawn(args + (["--act", str(act)] if act is not None else []), background=True)

    def _prebuilt(self, key: tuple) -> None:
        if key[0] == "ghost":
            (self.cache() / f"prebuild-{key[1]}.clip").unlink(missing_ok=True)

    def _spawn_warm(self, level: int, area: int, act: int) -> subprocess.Popen | None:
        """The warm child for that stage, or None when the stage cache already holds what it would make: each stage
        is asked once per server run, and before 2026-11-09 every one of the 102 started a child (an app start, in
        the installed app) only to find its files made. A stage a warm could not make on an earlier run is skipped
        too (stage_cache.failed): a build he clicks still tries it."""
        config = self._config()
        with Path(config.rom).open("rb") as handle:
            header = handle.read(0x40)
        from sm64_events.memory.version_probe import version_from_header
        if version_from_header(header) is None:
            raise ResimRefused("The lab's ROM is not a cartridge a stage can be made from.")
        if self._stage_made(Path(config.rom), level, area, act) or self._stage_failed(level, area, act):
            return None
        args = ["warm", *self._common(config), "--level", str(level), "--area", str(area), "--act", str(act),
                *self._scene_args()]
        return self._spawn(args, background=True)             # the warmer owns it: it ends it at shutdown

    def _stage_made(self, rom: Path, level: int, area: int, act: int) -> bool:
        """Whether the stage cache holds that stage's boot and reset already (tools/scene_lab/stage_sim.py `made`, the
        names a warm writes). The ROM's recipe is read once per file version: it hashes the whole cartridge."""
        recipe = self._recipe_of(rom)
        return recipe is not None and self._stage_cache().made(self.cache() / "stages", recipe, level, area, act)

    @staticmethod
    def _stage_cache():
        """tools/scene_lab/stage_cache.py: the cache's names without the simulation (the server never imports it)."""
        from sm64_events.server.scene_lab_api import _lab_module
        return _lab_module("stage_cache")

    def _recipe_of(self, rom: Path) -> str | None:
        stat = rom.stat()
        key = (str(rom), stat.st_size, stat.st_mtime_ns)
        if self._recipe is None or self._recipe[0] != key:
            self._recipe = (key, self._stage_cache().recipe(rom.read_bytes()))
        return self._recipe[1]

    def _stage_failed(self, level: int, area: int, act: int) -> bool:
        config = self.lab.config()
        recipe = self._recipe_of(Path(config.rom)) if config.rom is not None else None
        return recipe is not None and (level, area, act) in self._stage_cache().failed(self.cache() / "stages", recipe)

    def _warm_answered(self, stage: tuple[int, int, int], answer: dict) -> None:
        """A warm child's answer: a stage it refused, and did not make, is kept off later runs' warms."""
        if "error" not in answer:
            return
        config = self.lab.config()
        if config.rom is None or self._stage_made(Path(config.rom), *stage):
            return
        recipe = self._recipe_of(Path(config.rom))
        if recipe is not None:
            self._stage_cache().fail(self.cache() / "stages", recipe, *stage)

    def _route_stars(self) -> list[str]:
        """His practice route's "course:star" keys (the tracker's active route), for the warmer's route tier."""
        tracker = getattr(self.lab, "tracker", None)
        route = tracker.active_route() if tracker is not None and hasattr(tracker, "active_route") else None
        return (route or {}).get("star_keys") or []

    def allows(self, info: dict, package_frame: int) -> dict:
        """The Save states row's answer for one frame of a re-simulated scene (ghost_resim.save_states). While the
        scene builds, a frame past "built_to" is not built yet ({"allowed": False, "building": True}); a built one
        before any parting found so far follows the start's own rule, its parting frames refused as always."""
        from sm64_events.server.scene_lab_api import _lab_module
        module = _lab_module("ghost_resim")
        record = package_frame + info.get("first_record", 1)
        if info.get("building") and record > (info.get("built_to") or 0):
            return {"allowed": False, "building": True, "reason": BUILDING}
        return module.save_states(info["start"], frame=record, parting=info.get("parting_frame"))

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
