"""The scenes of his recent attempts (M4 B4, lab only): which attempts have one, and where it is.

    scenes = AttemptScenes(start_cutter, in_use=lambda: open_path)
    scenes.start()                                       # takes the folder, starts the cutter child
    scenes.observe_completed(completed, active_start)    # the tracker's completion hook (poll loop)
    scenes.observe_event(event)                          # the broadcaster's observer (poll loop)
    scenes.states([12, 13])  # {"12": {"state": "ready", "seconds": 15.2}, "13": {"state": "cutting"}}
    scenes.path(12)          # the package; LookupError (none) or SceneUnavailable (why not yet / never)
    scenes.live()            # {"recording": bool, "reason": str | None}
    scenes.stop()

Griffin, 2026-10-26: "I want to be able to play, and then IN REAL TIME and IMMEDIATELY AFTER
FINISHING AN ATTEMPT, be able to open the scene viewer and inspect the results." Every attempt the
tracker completes (stars and segments alike; imported times never reach the hook) becomes
"cutting" at once, and the cutter child (attempt_cutter.py) writes its package while he plays and
reports "ready" or "unavailable" with one plain sentence. The starts it needs come from the event
stream: practice_reset and state_loaded (anchors) and segment_armed/segment_disarmed notices.

What the poll loop pays: observe_completed and observe_event build a small dict and queue it for
the child's pipe thread; nothing here reads the recording, writes a file or starts a process on
that thread. The cutter's events arrive on its reader thread.

Retention: the scenes of the newest RETAIN_ATTEMPTS completed attempts (by end time) stay under
the folder (%LOCALAPPDATA%\\SM64Trainer\\scene-lab\\attempts\\<attempt id>.sm64scene); an older one
is deleted as a newer attempt completes, unless the open viewer reads it (in_use), and then at the
next pass after it closes. Scenes live as long as this server: an attempt id belongs to one
journal and another checkout's server may share the folder later, so start() deletes what a
previous server left instead of serving it, and a lock file keeps a second server out of the
folder while this one runs.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import attempt_span

log = logging.getLogger("sm64.scene_lab")

RETAIN_ATTEMPTS = 20           # completed attempts whose scenes are kept
RESTART_SECONDS = 30.0         # a cutter that exited is started again after this long
SOUND_WAIT_SECONDS = 0.6       # after a scene is whole, before its sound is taken from the ring
MAX_RESTARTS = 3               # and no more than this often in one server's life
LOCK_NAME = ".owner.lock"
ANCHORS = ("practice_reset", "state_loaded")
STARTING = "The scene cutter is starting."
STOPPED = "The scene cutter stopped; attempts finished meanwhile have no scene."
GAVE_UP = "The scene cutter keeps stopping; restart the Trainer to try again."
NO_LIVE_SOUND = "This server does not keep the game's sound for scenes."
SCENE_STARTING = "This attempt's scene is starting; open it again in a moment."


class SceneUnavailable(Exception):
    """This attempt has no scene to open (yet); the message says why in words the page shows."""


class FolderTaken(Exception):
    """Another server is cutting scenes into this folder."""


def _lock(folder: Path):
    folder.mkdir(parents=True, exist_ok=True)
    handle = open(folder / LOCK_NAME, "a+b")  # noqa: SIM115 - held for the server's life
    try:
        import msvcrt
    except ImportError:
        return handle
    try:
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        handle.close()
        raise FolderTaken("Another Trainer server is cutting attempt scenes into this lab folder.") from None
    return handle


class AttemptScenes:
    """folder() -> the folder the scenes go in; start_cutter(folder, on_event) -> a started
    attempt_cutter.CutterProcess (or a double with send and close)."""

    def __init__(self, folder, start_cutter, *, in_use=lambda: None, retain: int = RETAIN_ATTEMPTS,
                 restart_seconds: float = RESTART_SECONDS, live_audio=None, sound_wait: float = SOUND_WAIT_SECONDS):
        self.folder_of, self.start_cutter = folder, start_cutter
        self.live_audio, self.sound_wait = live_audio, sound_wait     # live_audio.LiveAudio: the recorder's ring
        self.in_use, self.retain, self.restart_seconds = in_use, retain, restart_seconds
        self.lock = threading.Lock()
        self.entries: dict[int, dict] = {}       # attempt id -> {"state", "ended", ...}
        self.takes: dict[str, dict] = {}         # package path -> a take the child is writing (its "take" event)
        self.folder: Path | None = None
        self.cutter = None
        self.held = None
        self.stopping = False
        self.problem: str | None = None
        self.recording: dict | None = None
        self.restarts = 0
        self.fits: dict[int, tuple[Future, Path]] = {}   # token -> a growing scene's sound fit in the child
        self.fit_serial = 0

    # ------------------------------------------------------------ lifetime

    def start(self) -> None:
        """Blocking (a lock, a folder sweep, a process start): call it off the event loop. Holds
        the folder and clears what a previous server left in it before the child writes there."""
        try:
            folder = Path(self.folder_of())
            self.held = _lock(folder)
            for path in folder.iterdir():
                if path.name != LOCK_NAME and (path.suffix in (".sm64scene", ".grow", ".cap", ".npy")
                                               or path.name.endswith(".partial")):
                    path.unlink(missing_ok=True)
            self.folder = folder
            self._start_cutter()
        except FolderTaken as taken:
            self.problem = str(taken)
        except Exception as error:  # noqa: BLE001 - the lab stays usable for packages without live scenes
            log.warning("The attempt scenes could not start.", exc_info=True)
            self.problem = f"Attempt scenes are off: {error}"

    def _start_cutter(self) -> None:
        if not self.stopping:
            self.cutter = self.start_cutter(self.folder, self._event)

    def stop(self) -> None:
        self.stopping = True
        cutter, self.cutter = self.cutter, None
        if cutter is not None:
            cutter.close()
        self._fits_end(STOPPED)
        if self.held is not None:
            self.held.close()
            self.held = None

    # ------------------------------------------------------------ the poll loop's side

    def observe_completed(self, completed, active_start=None, *, replace: bool = False) -> None:
        """The tracker's completion hook, chained after the replay's: queue what is new."""
        if not completed:
            return
        fresh = sorted((attempt for attempt in completed if attempt.id not in self.entries),
                       key=lambda attempt: (attempt_span.utc(attempt.ended_utc), attempt.id))[-self.retain:]
        if not fresh:
            return
        cutter, notes = self.cutter, []
        # The oldest attempt the tracker still has open: the cutter keeps a take writing past this
        # call's attempts for one that began with them (attempt_cutter.Brain.note).
        open_since = None if active_start is None else attempt_span.utc(active_start).isoformat()
        with self.lock:
            for attempt in fresh:
                mark = attempt_span.AttemptMark.of(attempt)
                take = self._take_for(mark) if cutter is not None else None
                if cutter is None:
                    entry = {"state": "unavailable", "reason": self.problem or STARTING}
                elif take is not None:              # ready now, growing to its fade-out: no wait
                    entry = {"state": "ready", "growing": True, "path": take["path"], "grow": take["grow"],
                             "seconds": round((mark.ended_utc - mark.started_utc).total_seconds(), 1)}
                else:
                    entry = {"state": "cutting"}
                if cutter is not None:
                    notes.append({"op": "complete", "attempt": mark.as_json(), "open_since": open_since})
                self.entries[attempt.id] = entry | {"ended": mark.ended_utc}
            dropped = self._retain()
        for note in notes:
            cutter.send(note)
        self._discard(dropped)

    def observe_event(self, event) -> None:
        """The broadcaster's observer: where attempts may start, as the stream announces them."""
        cutter = self.cutter
        if cutter is None:
            return
        if event.type in ANCHORS:
            cutter.send({"op": "start", "kind": "anchor", "frame": event.frame,
                         "utc": event.timestamp_utc.isoformat()})
        elif event.type == "segment_armed" and event.frame > 0:
            # The tracker's notice carries the arm's frame as the event's own (tracking/service.py's
            # notice drain), never in its payload; frame 0 is a re-projection's badge refresh.
            cutter.send({"op": "start", "kind": "segment", "frame": event.frame,
                         "segment_id": event.payload.get("segment_id"), "utc": event.timestamp_utc.isoformat()})
        elif event.type == "segment_disarmed":
            cutter.send({"op": "stop", "segment_id": event.payload.get("segment_id")})

    # ------------------------------------------------------------ the cutter's side

    def _take_for(self, mark) -> dict | None:
        """Where this attempt's scene grows when the child is writing a take from its start (its
        anchor frame, at about its start time); under the lock. The child announces each seat
        nobody holds yet; when another kept attempt already holds every one, the path is None
        until the child names the seat it gives this one (attempt_cutter.Take.seat_for)."""
        if mark.anchor_frame is None:
            return None
        found = [take for take in self.takes.values() if take["frame"] == mark.anchor_frame and abs(
            (take["utc"] - mark.started_utc).total_seconds()) <= attempt_span.MATCH_SECONDS]
        held = {entry.get("path") for entry in self.entries.values()}
        return next((take for take in reversed(found) if take["path"] not in held),
                    {"path": None, "grow": None} if found else None)

    def _event(self, event: dict) -> None:
        if "live" in event:
            self.recording = event["live"]
        elif "exited" in event:
            self._exited(event["exited"])
        elif "take" in event:
            take = event["take"]
            with self.lock:
                self.takes[take["path"]] = take | {"utc": attempt_span.utc(take["utc"])}
        elif "take_ended" in event:
            with self.lock:
                self.takes.pop(event["take_ended"], None)
        elif "id" in event and "sound" in event:
            self._sound_added(event["id"], event["sound"])
        elif "fit" in event:
            with self.lock:
                future, path = self.fits.pop(event["fit"], (None, None))
            if future is not None:
                path.unlink(missing_ok=True)
                future.set_result({key: value for key, value in event.items() if key != "fit"})
        elif "id" in event:
            with self.lock:
                entry = self.entries.get(event["id"])
                if entry is not None and not (entry["state"] == "ready" and not entry.get("growing")):
                    entry.update(state=event["state"], reason=event.get("reason"),
                                 seconds=event.get("seconds") or entry.get("seconds"),
                                 path=event.get("path") or entry.get("path"),
                                 grow=event.get("grow"), growing=bool(event.get("growing")))
                    if event["state"] == "ready" and not event.get("growing"):
                        self._sound_for(event["id"], entry, event)
                        log.info("attempt scene %s ready: %s", event["id"], {
                            key: event.get(key) for key in ("seconds", "frames", "bytes", "write_seconds",
                                                            "finish_seconds", "ready_after_decided_ms",
                                                            "ready_after_last_ms", "take_kind", "seat", "why",
                                                            "cpu_ms_per_frame", "write_ms_mean", "write_ms_p95")})
            if entry is None and event.get("state") == "ready" and event.get("path"):
                Path(event["path"]).unlink(missing_ok=True)          # retention dropped it meanwhile

    # ------------------------------------------------------------ the recorder's sound

    def _sound_for(self, attempt_id: int, entry: dict, event: dict) -> None:
        """Under the lock, once a scene is whole: its sound comes from the recorder's ring on a
        thread of its own, never before "ready" and never delaying it."""
        presents = event.get("presents_qpc")
        if self.live_audio is None:
            entry.update(sound="silent", sound_reason=NO_LIVE_SOUND)
        elif not presents:
            entry.update(sound="silent", sound_reason=event.get("silent") or NO_LIVE_SOUND)
        else:
            entry.update(sound="pending", sound_reason=None)
            threading.Thread(target=self._write_sound, args=(attempt_id, *presents, event.get("qpc_frequency")),
                             name="scene-sound", daemon=True).start()

    def _write_sound(self, attempt_id: int, first: int, last: int, frequency: int) -> None:
        """The ring's chunks around the scene's pictures, written once the last picture's sound
        has had time to arrive (SOUND_WAIT_SECONDS: the ring can receive a chunk up to
        live_audio.LATE_S after it sounded); the cutter child then attaches them."""
        time.sleep(self.sound_wait)
        if frequency != self.live_audio.frequency:
            self._sound_added(attempt_id, "This scene's pictures and the recorder's sound count time "
                                          "on different clocks.")
            return
        capture = Path(self.folder) / f"sound-{attempt_id}.cap"
        written = self.live_audio.write_span(first, last, capture)
        cutter = self.cutter
        if not written["written"] or cutter is None:
            capture.unlink(missing_ok=True)
            self._sound_added(attempt_id, written["reason"] or STOPPED)
            return
        cutter.send({"op": "sound", "id": attempt_id, "capture": str(capture)})

    def _sound_added(self, attempt_id: int, reason: str | None) -> None:
        if self.folder is not None:
            (Path(self.folder) / f"sound-{attempt_id}.cap").unlink(missing_ok=True)
        with self.lock:
            entry = self.entries.get(attempt_id)
            if entry is not None:
                entry.update(sound="attached" if reason is None else "silent", sound_reason=reason)

    def fit(self, stamps, origin_qpc: int, frequency: int) -> Future:
        """A growing scene's one sound fit, which the cutter child makes (scene_audio.LiveSound):
        `stamps` live_audio.stamps_of the ring's chunks, `origin_qpc` the scene's first present.
        The future gives the child's answer, live_audio.fit_stamps's dict with "fit_ms", or
        {"reason"} with the plain sentence. Writes one small file beside the scenes."""
        import numpy as np

        future: Future = Future()
        cutter, folder = self.cutter, self.folder
        if cutter is None or folder is None:
            future.set_result({"reason": self.problem or STOPPED})
            return future
        with self.lock:
            self.fit_serial += 1
            token = self.fit_serial
            path = Path(folder) / f"fit-{token}.npy"
            self.fits[token] = (future, path)
        with path.open("wb") as out:
            np.save(out, stamps)
        cutter.send({"op": "fit", "token": token, "stamps": str(path), "origin": int(origin_qpc),
                     "frequency": int(frequency)})
        return future

    def _fits_end(self, reason: str) -> None:
        with self.lock:
            pending, self.fits = self.fits, {}
        for future, path in pending.values():
            path.unlink(missing_ok=True)
            future.set_result({"reason": reason})

    def sound_reason(self, attempt_id: int) -> str | None:
        """Why this attempt's scene has no sound (yet), as the viewer says it; None once it has."""
        with self.lock:
            entry = dict(self.entries.get(attempt_id) or {})
        if entry.get("sound") == "pending":
            return "This attempt's sound is still being added; open it again in a moment to hear it."
        return entry.get("sound_reason")

    def _exited(self, code) -> None:
        with self.lock:
            for entry in self.entries.values():
                if entry["state"] == "cutting" or entry.get("growing"):     # its take died with the child
                    entry.update(state="unavailable", reason=STOPPED, growing=False)
            self.takes.clear()
        self._fits_end(STOPPED)
        if self.stopping:
            return
        self.cutter = None
        self.recording = {"recording": False, "reason": STOPPED}
        self.restarts += 1
        if self.restarts > MAX_RESTARTS:
            log.warning("The scene cutter exited (%s) again; leaving it stopped.", code)
            self.problem = GAVE_UP
            return
        log.warning("The scene cutter exited (%s); starting it again in %.0f s.", code, self.restart_seconds)
        timer = threading.Timer(self.restart_seconds, self._restart)
        timer.daemon = True
        timer.start()

    def _restart(self) -> None:
        try:
            self._start_cutter()
        except Exception:  # noqa: BLE001 - the next exit or the lab's own status says it
            log.warning("The scene cutter could not start again.", exc_info=True)

    # ------------------------------------------------------------ retention

    def _retain(self) -> list[tuple[int, dict]]:
        """Under the lock: the entries past the newest `retain`, removed from the catalog."""
        order = sorted(self.entries.items(), key=lambda item: (item[1]["ended"], item[0]))
        dropped = order[:max(0, len(order) - self.retain)]
        for attempt_id, _ in dropped:
            del self.entries[attempt_id]
        return dropped

    def _discard(self, dropped: list[tuple[int, dict]]) -> None:
        cutter = self.cutter
        for attempt_id, _entry in dropped:
            if cutter is not None:
                cutter.send({"op": "forget", "id": attempt_id})
        self.sweep()

    def sweep(self) -> None:
        """Deletes every scene file in the folder no kept attempt owns, except the open one."""
        folder = self.folder
        if folder is None or not folder.is_dir():
            return
        with self.lock:
            kept = {Path(entry["path"]).name for entry in self.entries.values() if entry.get("path")}
            kept |= {Path(path).name for path in self.takes}                # takes the child is writing
            kept |= {f"{attempt_id}.sm64scene" for attempt_id, entry in self.entries.items()
                     if entry["state"] == "cutting"}
        reading = self.in_use()
        for path in folder.glob("*.sm64scene"):
            if path.name in kept or path.name.startswith("take-") or (reading and Path(reading) == path):
                continue
            try:
                path.unlink()
            except OSError:
                pass                                                # open elsewhere: the next sweep

    # ------------------------------------------------------------ the routes' side

    def states(self, ids) -> dict:
        found = {}
        with self.lock:
            for attempt_id in ids:
                entry = self.entries.get(attempt_id)
                if entry is None:
                    continue
                if entry["state"] == "ready":
                    found[str(attempt_id)] = {"state": "ready", "seconds": entry["seconds"],
                                              "growing": bool(entry.get("growing"))}
                elif entry["state"] == "cutting":
                    found[str(attempt_id)] = {"state": "cutting"}
                else:
                    found[str(attempt_id)] = {"state": "unavailable", "reason": entry.get("reason")}
        return found

    def ready_ids(self) -> list[int]:
        """The attempts whose scene opens now (growing ones included), oldest first."""
        with self.lock:
            return [attempt_id for attempt_id, entry in sorted(self.entries.items(), key=lambda item: item[1]["ended"])
                    if entry["state"] == "ready"]

    def path(self, attempt_id: int) -> tuple[Path, Path | None]:
        """(the scene's package, its growth sidecar while it is still being written, else None)."""
        with self.lock:
            entry = dict(self.entries.get(attempt_id) or {})
        if not entry:
            raise LookupError("The lab has no scene for that attempt.")
        if entry["state"] == "cutting":
            raise SceneUnavailable("This attempt's scene is still being cut.")
        if entry["state"] != "ready":
            raise SceneUnavailable(entry.get("reason") or "This attempt has no scene.")
        if not entry.get("path"):                           # its take is giving it a package of its own
            raise SceneUnavailable(SCENE_STARTING)
        path = Path(entry["path"])
        if not path.is_file():                              # a take's new package appears between two frames
            raise SceneUnavailable(SCENE_STARTING if entry.get("growing") else "This attempt's scene file is gone.")
        sidecar = Path(entry["grow"]) if entry.get("growing") and entry.get("grow") else None
        if sidecar is not None and not sidecar.is_file() and entry.get("growing"):
            with self.lock:                                 # finished meanwhile, or not yet published
                current = self.entries.get(attempt_id) or {}
            if current.get("growing"):
                raise SceneUnavailable(SCENE_STARTING)
            sidecar = None
        return path, sidecar

    def live(self) -> dict:
        if self.problem:
            return {"recording": False, "reason": self.problem}
        return dict(self.recording) if self.recording else {"recording": False, "reason": STARTING}


def default_lab_root() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SM64Trainer" / "scene-lab"
