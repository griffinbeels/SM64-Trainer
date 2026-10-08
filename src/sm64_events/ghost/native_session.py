"""Human-paced native ghost selection; the renderer owns every frame decision."""
from __future__ import annotations

from datetime import datetime
import threading

from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT
from sm64_events.ghost.native_layout import GP_WRONG_SCENE, LABEL_BYTES
from sm64_events.ghost.pb import attempt_id_of
from sm64_events.ghost.selection import MISSING_REASON
from sm64_events.replay.capturecontrol import _kernel, _process_creation

#: Hue step between simultaneous ghosts: the golden angle, so any number of
#: them stay as far apart on the colour wheel as the count allows.
HUE_STEP_DEGREES = 137


#: The renderer's GIPC_BUDGET: the chosen set does not fit the clip budget (or
#: memory) together. The files are fine; fewer or shorter ghosts would show.
NATIVE_BUDGET_REASON = 12


def budget_reason(ghosts: int) -> str:
    """What the ghost card says when the set does not fit. One ghost alone
    always fits (a maximum clip is under the budget), so a single refusal
    only waits for the previous set's storage to clear; the renderer retries."""
    if ghosts > 1:
        return "These ghosts are too long to show together; untick one."
    return "Making room for this ghost..."


#: The ghost card while no renderer has served poses in this game.
START_GAME_REASON = "Start the game with the trainer's GLideN64 renderer."
#: The ghost card while the renderer that served poses is out of reach but its
#: game is still running: a GPU capture restart replaces the recorder's pose
#: source, and the new one takes a moment to reach the renderer. Until
#: 2026-09-27 that moment told him to start a game he was playing (9 of 1,293
#: ghost_changed on 2026-09-25, each right after a capture reconnect).
RECONNECTING_REASON = "Reconnecting to the game's renderer..."


def emulator_running(pid: int, created: int) -> bool:
    """Whether the process that served poses still runs: the same pid AND the
    same birth stamp (the renderer's `producer_created`, a FILETIME), so a pid
    reused by a later game counts as a new game. Unknown counts as not."""
    try:
        birth = _process_creation(_kernel(), pid)
    except (OSError, AttributeError):   # AttributeError: no kernel32 off Windows
        return False
    return birth is not None and (birth[0] | (birth[1] << 32)) == created


def ghost_hue(base: int, index: int) -> int:
    """The first ghost (his PB) keeps his chosen colour; the rest step away."""
    return (base + index * HUE_STEP_DEGREES) % 360


def fastest_clip(assets) -> int:
    """The playing ghost the path follows: the one with the fewest frames (its
    run time). A tie keeps the earlier (his PB leads the set); a ghost whose
    time is unknown is never preferred over one whose time is known."""
    timed = [(frames, index) for index, (*_, frames) in enumerate(assets)
             if type(frames) is int and frames >= 0]
    return min(timed)[1] if timed else 0


def ghost_label(ghost) -> str:
    """Credit belongs to the selected source, independently of cached poses."""
    if getattr(ghost, "import_id", None):
        author = getattr(ghost, "author", None)
        label = " ".join(author.split()) if isinstance(author, str) else ""
        label = "".join(char for char in label if ord(char) >= 32 and ord(char) != 127)
        label = label or "UNCREDITED"
    else:
        label = "YOUR PB" if getattr(ghost, "pb", False) else "YOUR RUN"
    # Portable credits remain intact in the library. The billboard has a
    # bounded wire field, shortened only at a complete UTF-8 character.
    return label.encode("utf-8", errors="replace")[:LABEL_BYTES - 1].decode("utf-8", errors="ignore")


class NativeGhost:
    def __init__(self, replay, *, supported=True, origin_of=None, running=emulator_running, portable=None):
        self._replay, self._supported = replay, supported
        # (pid, birth) of the game whose renderer last served poses. It
        # outlives a replaced pose source on purpose: the game connection
        # ends when that process does, not when the recorder reconnects.
        self._served_by = None
        self._running = running
        self._asset = None
        self._source = None
        self._sent = None
        self._enabled = False
        self._cached = {}
        self._origin_of = origin_of
        self._control_lock = threading.Lock()
        self._target = None
        self._origin = None
        self._owner_suspended = False
        self._controls = None
        self._selected_asset = None
        self._requested = False
        self._portable = portable
        self._adaptive_generation = 0
        self._activate_generation = 0
        self._activate_start = 0
        self._start_sequence = 0
        self._pending_asset = None
        self._pending_hues = {}
        self._asset_hues = {}
        self._last_full_start = None
        self._preparation_canceled = False

    def set_portable(self, provider):
        """The human-paced collection provider; never consulted by the clock feed."""
        self._portable = provider

    @staticmethod
    def _fingerprint(paths):
        """Each file and its manifest, or None when any is missing."""
        try:
            return tuple((item.stat().st_size, item.stat().st_mtime_ns, item.stat().st_ctime_ns)
                         for path in paths for item in (path, path.with_suffix(".json")))
        except OSError:
            return None

    def _preservation_failure(self, answer, attempt_id):
        lookup = getattr(self._replay, "native_preservation_reason", None)
        reason = lookup(attempt_id) if lookup is not None else None
        if not reason:
            return answer
        if reason == OLDER_POSE_FORMAT_TEXT:
            return {**answer, "reason": "older_pose_format", "reason_text": reason}
        if reason == "native ghost has no supported recorded poses":
            detail = "this recording has no Mario poses supported by the renderer."
        elif reason == "This recording has no native Mario poses.":
            detail = "Mario poses were not recorded for this attempt."
        elif "elapsed clock" in reason or "clock continuity" in reason:
            detail = "this recording is missing a continuous game clock for Mario playback."
        elif "endpoint" in reason or "origin" in reason:
            detail = "this recording is missing an exact starting or ending frame for Mario playback."
        else:
            detail = "its Mario recording could not be saved."
        return {**answer, "reason": "native_preservation_failed",
                "reason_text": "The template was saved, but " + detail}

    def resolve(self, target, ghosts, missing=()):
        """Every chosen ghost's answer; the renderer receives the playable ones.

        The top-level answer is the first playable ghost's (else the first
        ghost's), so a single-ghost reader sees what it always saw; `ghosts`
        lists each one, PB first.
        """
        self._asset = None
        self._asset_hues = {}
        if not ghosts:
            result, _ = self._resolve_template(target, None)
            if missing and target is not None and self._supported:
                result = {**result, "reason": "selected_template_missing", "reason_text": MISSING_REASON}
            return {**result, "ghosts": []}
        answers, assets, clip_ids = [], [], set()
        for template in ghosts:
            result, asset = self._resolve_template(target, template)
            result = {**result, "import_id": getattr(template, "import_id", None),
                      "author": getattr(template, "author", None), "label": ghost_label(template)}
            if asset is not None:
                clip_id = asset[1]["clip_id"]
                if clip_id in clip_ids:
                    result = {**result, "available": False, "reason": "duplicate_clip",
                              "reason_text": "Already shown by another selected ghost."}
                    asset = None
                else:
                    clip_ids.add(clip_id)
                    hue = getattr(template, "hue_degrees", None)
                    if hue is not None:
                        if type(hue) is not int or not 0 <= hue < 360:
                            raise ValueError("ghost hue must be an integer in 0..359")
                        self._asset_hues[clip_id] = hue
            answers.append(result)
            if asset is not None:
                assets.append((*asset, result["label"], getattr(template, "frames", None)))
        self._asset = tuple(assets) or None
        first = next((answer for answer in answers if answer["available"]), answers[0])
        return {**first, "ghosts": [
            {"template_id": answer["template_id"], "attempt_id": answer["attempt_id"],
             "import_id": answer["import_id"], "author": answer["author"], "label": answer["label"],
             "pb": bool(getattr(template, "pb", False)),
             "available": answer["available"], "reason": answer.get("reason"),
             "reason_text": answer["reason_text"]}
            for template, answer in zip(ghosts, answers, strict=True)]}

    def probe(self, target, template):
        """Validate a candidate without changing the asset sent to the renderer."""
        return self._resolve_template(target, template)[0]

    def prepare(self, target, ghosts, controls: dict, generation: int):
        """Resolve and preload a roster off the poll loop, without adopting it.

        A certified start consumes one readiness snapshot in ``note``. A
        preparation that finishes later waits for the next full start.
        """
        if type(generation) is not int or not 0 < generation < 2**64:
            raise ValueError("ghost roster generation must be a positive uint64")
        answer = self.resolve(target, ghosts)
        assets, hues = self._asset or (), dict(self._asset_hues)
        source = getattr(getattr(self._replay, "recorder", None), "ghost_capture", None)
        identity = (target["kind"], target["entity_key"]) if target else None
        with self._control_lock:
            if source is not self._source or identity != self._target:
                self._stop()
                self._source, self._sent = source, None
                self._selected_asset = None
            if self._adaptive_generation and generation < self._adaptive_generation:
                raise ValueError("ghost roster generation must not move backward")
            self._target = identity
            self._pending_asset, self._pending_hues = assets, hues
            self._adaptive_generation = generation
            self._preparation_canceled = False
            self._requested = True
            self._controls = {**controls,
                              "stop_at_star": bool(target and target["kind"] == "star"), "mode": 0}
            self._send()
        return {**answer, "generation": generation}

    def cancel_preparation(self):
        """Discard an obsolete future roster while retaining the current race."""
        with self._control_lock:
            if not self._adaptive_generation:
                return
            self._preparation_canceled = True
            self._pending_asset, self._pending_hues = (), {}
            self._activate_generation = self._activate_start = 0
            self._send()

    def _resolve_template(self, target, template):
        answer = {"available": False, "reason": "no_native_poses",
                  "reason_text": "Save a PB with this strategy, or save a run as a ghost, to race Mario here.",
                  "template_id": None, "attempt_id": None, "exactness": None}
        if not self._supported:
            return {**answer, "reason": "unsupported_rom",
                    "reason_text": "Mario in the game currently supports the US practice ROM."}, None
        if target is None:
            return {**answer, "reason": "no_target",
                    "reason_text": "Pick a star or a segment to practise."}, None
        if template is None:
            return {**answer, "reason": "no_template",
                    "reason_text": "Save a PB with this strategy, or save a run as a ghost, to race Mario here."}, None
        answer["template_id"] = template.id
        if getattr(template, "import_id", None):
            return self._resolve_portable(target, template, answer)
        attempt_id = attempt_id_of(template.origin)
        answer["attempt_id"] = attempt_id
        if attempt_id is None or self._replay is None:
            return answer, None
        try:
            identity = self._replay.native_identity(attempt_id)
        except ValueError:
            return self._preservation_failure(answer, attempt_id), None
        # Keyed by the RUN, not the list that offered it: his PB, a saved ghost
        # and a template can all name one attempt, and it is validated once.
        key = (attempt_id, target["kind"], target["entity_key"], target.get("strat"), identity)
        cached = self._cached.get(attempt_id)
        if cached is not None:
            cached_key, asset, files, stamp, result = cached
            if key == cached_key and stamp is not None and self._fingerprint(files) == stamp:
                return {**result, "template_id": template.id}, asset
        found = self._replay.find_native(attempt_id)
        if found is None:
            return self._preservation_failure(answer, attempt_id), None
        # The renderer plays the copy whose missing pads the attempt's input
        # track filled (ghost/padfill.py); either file changing re-validates.
        fill = getattr(self._replay, "native_with_inputs", None)
        asset = fill(attempt_id, found) if fill is not None else found
        files = tuple(dict.fromkeys((found[0], asset[0])))
        result = {**answer, "available": True, "reason": None, "reason_text": None,
                  "exactness": "native_pose", "blinks": asset[1]["blink_frames"]}
        self._cached[attempt_id] = key, asset, files, self._fingerprint(files), result
        if len(self._cached) > 128:
            del self._cached[next(iter(self._cached))]
        return result, asset

    def _resolve_portable(self, target, ghost, answer):
        """Imported bytes have their own provider and never borrow local attempt IDs."""
        if ((ghost.kind, ghost.entity_key) != (target["kind"], target["entity_key"])
                or self._portable is None):
            return {**answer, "reason_text": "This imported ghost is not available for this target."}, None
        try:
            path, manifest = self._portable.resolve(ghost, target)
        except (ValueError, LookupError, OSError) as error:
            return {**answer, "reason_text": str(error)}, None
        return {**answer, "available": True, "reason": None, "reason_text": None,
                "exactness": "native_pose", "blinks": manifest["blink_frames"]}, (path, manifest)

    def reconcile(self, *, enabled, target, controls: dict):
        """`controls` are the settings' native command fields
        (`ghost/settings.py::native_controls`); the target adds the rest."""
        source = getattr(getattr(self._replay, "recorder", None), "ghost_capture", None)
        with self._control_lock:
            if self._adaptive_generation:
                if source is not self._source:
                    if self._source is not None:
                        self._source.set_playback(enabled=False)
                    self._source, self._sent = source, None
                    self._activate_generation = self._activate_start = 0
                identity = (target["kind"], target["entity_key"]) if target else None
                if identity != self._target:
                    self._stop()
                    self._asset = self._selected_asset = None
                    self._asset_hues = {}
                    self._target = identity
                    return
                else:
                    self._requested = bool(enabled)
                    if not enabled:
                        self._activate_generation = self._activate_start = 0
                    self._controls = {**controls,
                                      "stop_at_star": bool(target and target["kind"] == "star"), "mode": 0}
                    self._send()
                    return
            self._adaptive_generation = self._activate_generation = 0
            self._pending_asset = None
            self._pending_hues = {}
            if source is not self._source:
                self._stop()
                self._source, self._sent = source, None
            identity = (target["kind"], target["entity_key"]) if target else None
            if identity != self._target:
                self._origin = None
                self._owner_suspended = False
            self._target = identity
            self._selected_asset = self._asset
            self._requested = bool(enabled)
            self._controls = {**controls,
                              "stop_at_star": bool(target and target["kind"] == "star"), "mode": 0}
            self._send()

    def note(self, _snapshot=None):
        """After a settled poll tick, forward changed owner markers only.

        No files, model work or frame pacing. Skip a contended control update;
        the next tick observes the same authoritative arm/rebase/disarm state.
        Selection and template validation run separately on the session worker.
        """
        if not self._control_lock.acquire(blocking=False):
            return
        try:
            origin = (self._origin_of(*self._target)
                      if self._target and self._origin_of and self._requested else None)
            if self._adaptive_generation and not self._preparation_canceled and origin is not None:
                token = origin.get("full_start_token")
                if origin.get("full_start_now") and token is not None and token != self._last_full_start:
                    # Consume even while unready: the later status update may
                    # never turn this start into a mid-run roster change.
                    self._last_full_start = token
                    self._start_sequence += 1
                    status = self._source.snapshot_status(blocking=False) if self._source is not None else None
                    if status is not None and status.get("available") and status.get("prepared_generation") == self._adaptive_generation:
                        self._activate_generation = self._adaptive_generation
                        self._activate_start = self._start_sequence
                        self._selected_asset = self._pending_asset
            if origin is not None and origin.get("clock_ready") is False:
                # A fresh arm can be certified before its move clock settles.
                # Freeze the ready roster now; publish no playable origin until
                # this same arm has an authoritative clock. Its later rebase
                # never becomes a second chance to adopt a different roster.
                self._origin = None
                origin = None
            # Completion or a restore can temporarily remove the tracker arm.
            # Hide presentation while retaining native checkpoints. Explicit
            # switch-off and target changes still revoke the binding.
            self._owner_suspended = bool(origin is None and self._origin is not None and self._requested)
            if origin is not None or not self._requested:
                self._origin = origin
            self._send(blocking=False)
        finally:
            self._control_lock.release()

    def _send(self, *, blocking=True):
        source = self._source
        recording_enabled = getattr(getattr(self._replay, "recorder", None), "recording_enabled", True) is not False
        command = {"enabled": False}
        status = source.snapshot_status(blocking=blocking) if source is not None else {}
        if status is None:
            return
        self._remember_renderer(status)
        producer = status.get("producer")
        origin = self._origin
        assets = self._pending_asset if self._adaptive_generation else self._selected_asset
        if (recording_enabled and self._requested and self._selected_asset is not None and origin is not None
                and (producer is None or not producer.configuration or not producer.epoch)):
            # RomClosed/RomOpen temporarily removes the producer namespace.
            # Keep the requested binding through it; sending OFF here would
            # erase the native recovery cache before the same ROM reopens.
            return
        if (recording_enabled and self._requested and assets is not None and source is not None
                and (origin is not None or self._adaptive_generation) and producer is not None
                and producer.configuration and producer.epoch):
            instant = datetime.fromisoformat(origin["utc"].replace("Z", "+00:00")) if origin else None
            if instant is not None and instant.tzinfo is None:
                self._origin = None
                return self._send(blocking=blocking)
            controls = dict(self._controls)
            hue = controls.pop("hue_degrees")
            # A star whose live clock is a segment's (the 100-coin star, timed
            # to its exit star) is not over at the first star grab: his
            # 100-coin grab must not end the ghosts, nor theirs end them. His
            # exit grab closes the attempt, and the suspension hides them.
            controls["stop_at_star"] = controls["stop_at_star"] and bool(origin and origin["kind"] == "star")
            hues = self._pending_hues if self._adaptive_generation else self._asset_hues
            clips = [{"clip_id": bytes.fromhex(manifest["clip_id"]),
                      "clip_sha256": bytes.fromhex(manifest["sha256"]),
                      "clip_path": str(path), "hue_degrees": hues.get(manifest["clip_id"], ghost_hue(hue, index)), "label": label}
                     for index, (path, manifest, label, _frames) in enumerate(assets)]
            command = {"enabled": bool(origin and (clips or self._adaptive_generation and self._selected_asset)), "model_id": 1,
                       "owner_suspended": self._owner_suspended,
                       "clips": clips, "rom_path": "",
                       "path_clip": fastest_clip(assets), **controls}
            if origin is not None:
                command["origin"] = {"kind": 1 if origin["kind"] == "star" else 2,
                                  "configuration": producer.configuration, "epoch": producer.epoch,
                                  "global_timer": origin["frame"], "utc": instant.timestamp(),
                                  "session_id": origin["session_id"], "arm_id": origin["arm_id"]}
            if self._adaptive_generation:
                adopting = self._activate_generation == self._adaptive_generation
                command.update(prepare_generation=self._adaptive_generation,
                               activate_generation=self._activate_generation if adopting else 0,
                               activate_start=self._activate_start if adopting else 0)
                if self._preparation_canceled:
                    command["cancel_preparation"] = True
        if source is not None and command != self._sent:
            if source.set_playback(blocking=blocking, **command) is False:
                return
            self._sent = command
        self._enabled = command["enabled"]

    def _remember_renderer(self, status):
        producer = status.get("producer") if status.get("available") else None
        if getattr(producer, "pid", None):
            self._served_by = (producer.pid, producer.created)

    def _reconnecting(self) -> bool:
        """Poses were served earlier in this game and it still runs, so the
        renderer is out of reach only while capture reconnects."""
        served = self._served_by
        if served is not None and not self._running(*served):
            self._served_by = served = None
        return served is not None

    def state(self):
        status = (self._source.snapshot_status() or {}) if self._source is not None else {}
        self._remember_renderer(status)
        recorder = getattr(self._replay, "recorder", None)
        recording_disabled = getattr(recorder, "recording_enabled", True) is False
        ready = bool(not recording_disabled and self._enabled and not self._owner_suspended
                     and status.get("available") and status.get("state") == 4)
        # A generated pose is not display evidence. Only the renderer's last
        # qualified swap can say this command reached the game window.
        visible = ready and bool(status.get("displayed"))
        reason = None
        if self._requested and recording_disabled:
            reason = "Recording is off. Start recording to show Only Mario ghosts and play their sounds."
        elif ready and not visible and status.get("playback_reason") == GP_WRONG_SCENE:
            reason = "Ghost is in another area."
        elif self._requested and not ready:
            if self._owner_suspended:
                reason = "Waiting for the next attempt or a saved state."
            elif status.get("reason") == 10:
                reason = "Waiting for this attempt's recorded clock."
            elif not self._enabled and status.get("available"):
                reason = "Waiting for the next attempt."
            elif status.get("state") == 3:
                reason = "Preparing Mario..."
            elif status.get("state") == 5:
                reason = {4: "Open the US practice ROM to show Mario.",
                          5: "This practice ROM does not provide the required Mario poses.",
                          6: "The saved Mario poses could not be loaded.",
                          7: "Mario's model could not be loaded from this ROM.",
                          8: "The saved Mario poses could not be played.",
                          NATIVE_BUDGET_REASON: budget_reason(len(self._selected_asset or ())),
                          11: "The game ROM could not be read for Mario. Reopen the game with the Trainer graphics plugin."}.get(
                              status.get("reason"), "Mario playback is unavailable; try switching it off and on.")
            else:
                reason = RECONNECTING_REASON if self._reconnecting() else START_GAME_REASON
        result = {"running": ready, "visible": visible, "backend": "native", "reason": reason}
        if self._adaptive_generation:
            result.update(prepared_generation=status.get("prepared_generation", 0) if status.get("available") and not self._preparation_canceled else 0,
                          active_generation=status.get("active_generation", 0) if status.get("available") else 0)
        return result

    def stop(self):
        with self._control_lock:
            self._stop()

    def _stop(self):
        if self._source is not None and self._sent != {"enabled": False}:
            self._source.set_playback(enabled=False)
            self._sent = {"enabled": False}
        self._enabled = False
        self._requested = False
        self._origin = None
        self._owner_suspended = False
        self._adaptive_generation = self._activate_generation = 0
        self._activate_start = self._start_sequence = 0
        self._pending_asset = None
        self._pending_hues = {}
        self._last_full_start = None
        self._preparation_canceled = False
