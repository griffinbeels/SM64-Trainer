"""The scenes of his attempts (M4 B4, lab only): which attempts have one, and where it is.

    scenes = AttemptScenes(folder, start_cutter, in_use=lambda: open_path, journal=lambda: tracker.db)
    scenes.start()                                       # takes the folder, restores its scenes, starts the cutter
    scenes.observe_completed(completed, active_start)    # the tracker's completion hook (poll loop)
    scenes.observe_event(event)                          # the broadcaster's observer (poll loop)
    scenes.states([12, 13])  # {"12": {"state": "ready", "seconds": 15.2}, "13": {"state": "cutting"}}
    scenes.path(12)          # the package; LookupError (none) or SceneUnavailable (why not yet / never)
    scenes.live()            # {"recording": bool, "reason": str | None}
    scenes.holdings()        # every kept scene file, for Settings' ghost files (ghost/storage.py)
    scenes.purge([12])       # deletes those scenes, skipping one in use
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

Keeping scenes. Griffin, 2026-11-08: "Yeah, I think we should be able to keep ghosts after
restarts." A finished scene stays until he deletes it (Settings' ghost files, ghost/storage.py);
nothing deletes one by count. An attempt id belongs to one journal (tracker database), and
another checkout's server, with a journal of its own, shares the lab folder. So each journal has a
folder of its own (journal_folder: %LOCALAPPDATA%\\SM64Trainer\\scene-lab\\attempts\\<key of the
journal's file>), a lock file keeps a second server out of it while this one runs, and its index
(INDEX_NAME) names each finished scene's attempt with that attempt's start and end. start() serves
a scene again only when the journal still holds that attempt id with the same start and end, so a
journal restored from a backup or rebuilt at the same path never shows another attempt's scene;
everything else in the folder (a take still being written when the last server stopped, a scene
whose attempt is gone) is deleted. Files of the flat layout before 2026-11-08 cannot be tied to
a journal and are deleted once, unless an older server still holds that folder.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from concurrent.futures import Future
from pathlib import Path

import attempt_span

log = logging.getLogger("sm64.scene_lab")

NEW_PER_CALL = 20              # attempts one completion call may queue (a re-projection hands over the session)
RESTART_SECONDS = 30.0         # a cutter that exited is started again after this long
SOUND_WAIT_SECONDS = 0.6       # after a scene is whole, before its sound is taken from the ring
MAX_RESTARTS = 3               # and no more than this often in one server's life
LOCK_NAME = ".owner.lock"
INDEX_NAME = "scenes.json"     # the journal folder's finished scenes (_save_index)
INDEX_VERSION = 1
SCENE_FILES = (".sm64scene", ".grow", ".cap", ".npy")   # what the cutter and the sound step write here
ANCHORS = ("practice_reset", "state_loaded")
STARTING = "The scene cutter is starting."
STOPPED = "The scene cutter stopped; attempts finished meanwhile have no scene."
GAVE_UP = "The scene cutter keeps stopping; restart the Trainer to try again."
NO_LIVE_SOUND = "This server does not keep the game's sound for scenes."
SCENE_STARTING = "This attempt's scene is starting; open it again in a moment."
NO_JOURNAL = "This server has no practice journal open, so it keeps no attempt scenes."
SOUND_NOT_ADDED = "This attempt's sound was not added before the Trainer stopped."


def journal_folder(root, database) -> Path:
    """The folder under `root` (the lab's attempts folder) that keeps one journal's scenes: named
    by the journal file's full path, so another checkout's journal, or a copy of this one, keeps
    its own. NoJournal when there is none (`database` None, or one with no file)."""
    path = getattr(database, "path", None)
    if path is None:
        raise NoJournal(NO_JOURNAL)
    named = os.path.normcase(str(Path(path).resolve()))
    return Path(root) / hashlib.sha256(named.encode("utf-8")).hexdigest()[:16]


def _scene_file(path: Path) -> bool:
    return path.suffix in SCENE_FILES or path.name.endswith(".partial")


class SceneUnavailable(Exception):
    """This attempt has no scene to open (yet); the message says why in words the page shows."""


class FolderTaken(Exception):
    """Another server is cutting scenes into this folder."""


class NoJournal(ValueError):
    """No journal is open (a broadcast-only second server), so no scene can be tied to one."""


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
    attempt_cutter.CutterProcess (or a double with send and close). journal() -> the journal the
    attempt ids belong to (storage/db.py Database: its attempt(id)); with it, finished scenes are
    kept in the folder's index and served again by the next server. Without it (a lab script, a
    test) scenes live as long as this object and start() clears the folder. legacy() -> the
    flat folder of the layout before 2026-11-08, cleared once at start."""

    def __init__(self, folder, start_cutter, *, in_use=lambda: None, journal=None, legacy=None,
                 restart_seconds: float = RESTART_SECONDS, live_audio=None, sound_wait: float = SOUND_WAIT_SECONDS):
        self.folder_of, self.start_cutter, self.journal, self.legacy = folder, start_cutter, journal, legacy
        self.live_audio, self.sound_wait = live_audio, sound_wait     # live_audio.LiveAudio: the recorder's ring
        self.in_use, self.restart_seconds = in_use, restart_seconds
        self.lock = threading.Lock()
        self.index_lock = threading.Lock()      # one index write at a time; taken before `lock`, never inside it
        self.purged: set[int] = set()           # attempts deleted while this server runs: never cut again
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
        """Blocking (a lock, the index, a folder sweep, a process start): call it off the event loop.
        Holds the folder, serves again each finished scene the journal still vouches for, and
        deletes the rest of what a previous server left before the child writes there."""
        try:
            folder = Path(self.folder_of())
            self.held = _lock(folder)
            self._clear_legacy(folder)
            restored = self._restore(folder)
            kept = {Path(entry["path"]).name for entry in restored.values()}
            for path in folder.iterdir():
                if path.is_file() and path.name not in kept and _scene_file(path):
                    path.unlink(missing_ok=True)
            with self.lock:
                self.entries.update(restored)
            self.folder = folder
            self._save_index()
            self._start_cutter()
        except (FolderTaken, NoJournal) as refused:
            self.problem = str(refused)
        except Exception as error:  # noqa: BLE001 - the lab stays usable for packages without live scenes
            log.warning("The attempt scenes could not start.", exc_info=True)
            self.problem = f"Attempt scenes are off: {error}"

    def _start_cutter(self) -> None:
        if not self.stopping:
            self.cutter = self.start_cutter(self.folder, self._event)

    # ------------------------------------------------------------ keeping scenes across restarts

    def _database(self):
        """The journal, or None without one; ValueError when one was promised and is not open."""
        if self.journal is None:
            return None
        database = self.journal()
        if database is None:
            raise NoJournal(NO_JOURNAL)
        return database

    def _clear_legacy(self, folder: Path) -> None:
        """The flat layout's files (before 2026-11-08): no index names their journal, so none can be
        served again. Left alone while an older server still holds that folder."""
        legacy = Path(self.legacy()) if self.legacy is not None else None
        if legacy is None or legacy == folder or not legacy.is_dir():
            return
        try:
            held = _lock(legacy)
        except FolderTaken:
            return
        try:
            for path in legacy.iterdir():
                if path.is_file() and _scene_file(path):
                    path.unlink(missing_ok=True)
        finally:
            held.close()

    def _restore(self, folder: Path) -> dict[int, dict]:
        """The index's scenes whose file is here and whose attempt the journal holds with the same
        start and end: an attempt id reused by another journal at this path never gets them."""
        database = self._database()
        if database is None:
            return {}
        try:
            document = json.loads((folder / INDEX_NAME).read_text(encoding="utf-8"))
            rows = document["scenes"] if document.get("version") == INDEX_VERSION else []
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return {}
        restored = {}
        for row in rows if isinstance(rows, list) else []:
            try:
                attempt_id, name = row["attempt"], row["path"]
                if type(attempt_id) is not int or not isinstance(name, str) or Path(name).name != name \
                        or not name.endswith(".sm64scene") or not (folder / name).is_file():
                    continue
                started, ended = attempt_span.utc(row["started_utc"]), attempt_span.utc(row["ended_utc"])
                attempt = database.attempt(attempt_id)
                if attempt is None:
                    continue
                mark = attempt_span.AttemptMark.of(attempt)
                if (mark.started_utc, mark.ended_utc) != (started, ended):
                    continue
                sound = row.get("sound")
                restored[attempt_id] = {
                    "state": "ready", "growing": False, "path": str(folder / name), "grow": None,
                    "seconds": float(row["seconds"]), "started": started, "ended": ended,
                    "sound": "silent" if sound != "attached" else "attached",
                    "sound_reason": (SOUND_NOT_ADDED if sound == "pending" else row.get("sound_reason"))
                    if sound != "attached" else None}
            except (KeyError, TypeError, ValueError):
                continue
        return restored

    def _save_index(self) -> None:
        """Writes the folder's index of finished scenes (atomically), when there is a journal to tie
        them to. Never under `lock`: it takes `index_lock`, then `lock` only to copy the entries."""
        if self.journal is None or self.folder is None:
            return
        with self.index_lock:
            with self.lock:
                rows = [{"attempt": attempt_id, "path": Path(entry["path"]).name, "seconds": entry.get("seconds"),
                         "started_utc": entry["started"].isoformat(), "ended_utc": entry["ended"].isoformat(),
                         "sound": entry.get("sound"), "sound_reason": entry.get("sound_reason")}
                        for attempt_id, entry in sorted(self.entries.items())
                        if entry["state"] == "ready" and not entry.get("growing") and entry.get("path")
                        and entry.get("started") is not None]
            index = Path(self.folder) / INDEX_NAME
            partial = index.with_name(index.name + ".partial")
            try:
                partial.write_text(json.dumps({"version": INDEX_VERSION, "scenes": rows}), encoding="utf-8")
                os.replace(partial, index)
            except OSError:
                log.warning("The attempt scenes' index could not be written.", exc_info=True)
                partial.unlink(missing_ok=True)

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
        fresh = sorted((attempt for attempt in completed
                        if attempt.id not in self.entries and attempt.id not in self.purged),
                       key=lambda attempt: (attempt_span.utc(attempt.ended_utc), attempt.id))[-NEW_PER_CALL:]
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
                self.entries[attempt.id] = entry | {"started": mark.started_utc, "ended": mark.ended_utc}
        for note in notes:
            cutter.send(note)

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
            finished = False
            with self.lock:
                entry = self.entries.get(event["id"])
                if entry is not None and not (entry["state"] == "ready" and not entry.get("growing")):
                    entry.update(state=event["state"], reason=event.get("reason"),
                                 seconds=event.get("seconds") or entry.get("seconds"),
                                 path=event.get("path") or entry.get("path"),
                                 grow=event.get("grow"), growing=bool(event.get("growing")))
                    if event["state"] == "ready" and not event.get("growing"):
                        finished = True
                        self._sound_for(event["id"], entry, event)
                        log.info("attempt scene %s ready: %s", event["id"], {
                            key: event.get(key) for key in ("seconds", "frames", "bytes", "write_seconds",
                                                            "finish_seconds", "ready_after_decided_ms",
                                                            "ready_after_last_ms", "take_kind", "seat", "why",
                                                            "cpu_ms_per_frame", "write_ms_mean", "write_ms_p95")})
            if entry is None and event.get("state") == "ready" and event.get("path"):
                Path(event["path"]).unlink(missing_ok=True)          # nobody keeps that attempt
            if finished:
                self._save_index()

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
        if entry is not None:
            self._save_index()

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

    # ------------------------------------------------------------ deleting scenes (Settings' ghost files)

    def _busy(self, entry: dict) -> bool:
        """Under the lock: a finished scene something reads or writes right now (the open viewer, or
        its sound still being added, whose atomic replace would bring a deleted file back)."""
        reading = self.in_use()
        return entry.get("sound") == "pending" or bool(reading and Path(reading) == Path(entry["path"]))

    def holdings(self) -> list[dict]:
        """Every finished scene kept here: {"attempt", "bytes", "in_use"}. A scene still being cut
        or growing is not one yet and is never listed."""
        with self.lock:
            entries = [(attempt_id, dict(entry), self._busy(entry)) for attempt_id, entry in self.entries.items()
                       if entry["state"] == "ready" and not entry.get("growing") and entry.get("path")]
        found = []
        for attempt_id, entry, busy in entries:
            try:
                size = Path(entry["path"]).stat().st_size
            except OSError:
                continue
            found.append({"attempt": attempt_id, "bytes": size, "in_use": busy})
        return found

    def purge(self, attempt_ids) -> dict:
        """Deletes these attempts' finished scenes: {"deleted": [ids], "bytes", "skipped": [ids]}.
        A scene in use, or one Windows will not let go of (an export reading it), is skipped and
        stays served. A deleted attempt is never cut again while this server runs."""
        wanted = set(attempt_ids)
        with self.lock:
            taken = {attempt_id: self.entries.pop(attempt_id) for attempt_id, entry in list(self.entries.items())
                     if attempt_id in wanted and entry["state"] == "ready" and not entry.get("growing")
                     and entry.get("path") and not self._busy(entry)}
            busy = sorted(wanted & set(self.entries))
        deleted, freed, kept = [], 0, {}
        for attempt_id, entry in taken.items():
            path = Path(entry["path"])
            try:
                size = path.stat().st_size if path.is_file() else 0
                path.unlink(missing_ok=True)
            except OSError:
                kept[attempt_id] = entry
                continue
            deleted.append(attempt_id)
            freed += size
        with self.lock:
            self.entries.update(kept)
            self.purged.update(deleted)
        cutter = self.cutter
        for attempt_id in deleted:
            if cutter is not None:
                cutter.send({"op": "forget", "id": attempt_id})
        self._save_index()
        return {"deleted": sorted(deleted), "bytes": freed, "skipped": sorted([*busy, *kept])}

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
