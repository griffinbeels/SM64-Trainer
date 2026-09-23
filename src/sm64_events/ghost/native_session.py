"""Human-paced native ghost selection; the renderer owns every frame decision."""
from __future__ import annotations

from datetime import datetime
import threading

from sm64_events.ghost.clip import OLDER_POSE_FORMAT_TEXT, pose_gaps
from sm64_events.ghost.source import attempt_id_of
from sm64_events.ghost.selection import MISSING_REASON

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


def ghost_hue(base: int, index: int) -> int:
    """The first ghost (his PB) keeps his chosen colour; the rest step away."""
    return (base + index * HUE_STEP_DEGREES) % 360


class NativeGhost:
    def __init__(self, replay, *, supported=True, origin_of=None):
        self._replay, self._supported = replay, supported
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

    @staticmethod
    def _fingerprint(path):
        try:
            return tuple((item.stat().st_size, item.stat().st_mtime_ns, item.stat().st_ctime_ns)
                         for item in (path, path.with_suffix(".json")))
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
        if not ghosts:
            result, _ = self._resolve_template(target, None)
            if missing and target is not None and self._supported:
                result = {**result, "reason": "selected_template_missing", "reason_text": MISSING_REASON}
            return {**result, "ghosts": []}
        answers, assets = [], []
        for template in ghosts:
            result, asset = self._resolve_template(target, template)
            answers.append(result)
            if asset is not None:
                assets.append(asset)
        self._asset = tuple(assets) or None
        first = next((answer for answer in answers if answer["available"]), answers[0])
        return {**first, "ghosts": [
            {"template_id": answer["template_id"], "attempt_id": answer["attempt_id"],
             "pb": bool(getattr(template, "pb", False)),
             "available": answer["available"], "reason_text": answer["reason_text"]}
            for template, answer in zip(ghosts, answers, strict=True)]}

    def probe(self, target, template):
        """Validate a candidate without changing the asset sent to the renderer."""
        return self._resolve_template(target, template)[0]

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
            cached_key, asset, stamp, result = cached
            if key == cached_key and stamp is not None and self._fingerprint(asset[0]) == stamp:
                return {**result, "template_id": template.id}, asset
        found = self._replay.find_native(attempt_id)
        if found is None:
            return self._preservation_failure(answer, attempt_id), None
        path, manifest = found
        asset = (path, manifest)
        result = {**answer, "available": True, "reason": None, "reason_text": None,
                  "exactness": "native_pose", "pose_gaps": pose_gaps(manifest)}
        self._cached[attempt_id] = key, asset, self._fingerprint(path), result
        if len(self._cached) > 128:
            del self._cached[next(iter(self._cached))]
        return result, asset

    def reconcile(self, *, enabled, target, controls: dict):
        """`controls` are the settings' native command fields
        (`ghost/settings.py::native_controls`); the target adds the rest."""
        source = getattr(getattr(self._replay, "recorder", None), "ghost_capture", None)
        with self._control_lock:
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
        command = {"enabled": False}
        status = source.snapshot_status(blocking=blocking) if source is not None else {}
        if status is None:
            return
        producer = status.get("producer")
        origin = self._origin
        if (self._requested and self._selected_asset is not None and origin is not None
                and (producer is None or not producer.configuration or not producer.epoch)):
            # RomClosed/RomOpen temporarily removes the producer namespace.
            # Keep the requested binding through it; sending OFF here would
            # erase the native recovery cache before the same ROM reopens.
            return
        if (self._requested and self._selected_asset is not None and source is not None
                and origin is not None and producer is not None
                and producer.configuration and producer.epoch):
            instant = datetime.fromisoformat(origin["utc"].replace("Z", "+00:00"))
            if instant.tzinfo is None:
                self._origin = None
                return self._send(blocking=blocking)
            controls = dict(self._controls)
            hue = controls.pop("hue_degrees")
            # A star whose live clock is a segment's (the 100-coin star, timed
            # to its exit star) is not over at the first star grab: his
            # 100-coin grab must not end the ghosts, nor theirs end them. His
            # exit grab closes the attempt, and the suspension hides them.
            controls["stop_at_star"] = controls["stop_at_star"] and origin["kind"] == "star"
            clips = [{"clip_id": bytes.fromhex(manifest["clip_id"]),
                      "clip_sha256": bytes.fromhex(manifest["sha256"]),
                      "clip_path": str(path), "hue_degrees": ghost_hue(hue, index)}
                     for index, (path, manifest) in enumerate(self._selected_asset)]
            command = {"enabled": True, "model_id": 1,
                       "owner_suspended": self._owner_suspended,
                       "clips": clips, "rom_path": "",
                       **controls,
                       "origin": {"kind": 1 if origin["kind"] == "star" else 2,
                                  "configuration": producer.configuration, "epoch": producer.epoch,
                                  "global_timer": origin["frame"], "utc": instant.timestamp(),
                                  "session_id": origin["session_id"], "arm_id": origin["arm_id"]}}
        if source is not None and command != self._sent:
            if source.set_playback(blocking=blocking, **command) is False:
                return
            self._sent = command
        self._enabled = command["enabled"]

    def state(self):
        status = (self._source.snapshot_status() or {}) if self._source is not None else {}
        ready = bool(self._enabled and not self._owner_suspended
                     and status.get("available") and status.get("state") == 4)
        # A generated pose is not display evidence. Only the renderer's last
        # qualified swap can say this command reached the game window.
        visible = ready and bool(status.get("displayed"))
        reason = None
        if self._requested and not ready:
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
                reason = "Start the game with the trainer's GLideN64 renderer."
        return {"running": ready, "visible": visible, "backend": "native", "reason": reason}

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
