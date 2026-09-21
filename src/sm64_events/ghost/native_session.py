"""Human-paced native ghost selection; the renderer owns every frame decision."""
from __future__ import annotations

from sm64_events.ghost.source import attempt_id_of


class NativeGhost:
    def __init__(self, replay, *, supported=True):
        self._replay, self._supported = replay, supported
        self._asset = None
        self._source = None
        self._sent = None
        self._enabled = False
        self._cached = None

    @staticmethod
    def _fingerprint(path):
        try:
            return tuple((item.stat().st_size, item.stat().st_mtime_ns, item.stat().st_ctime_ns)
                         for item in (path, path.with_suffix(".json")))
        except OSError:
            return None

    def resolve(self, target, templates):
        self._asset = None
        answer = {"available": False, "reason": "no_native_poses",
                  "reason_text": "Record a new attempt and mark it as the template to show Mario in the game.",
                  "template_id": None, "attempt_id": None, "exactness": None}
        if not self._supported:
            return {**answer, "reason": "unsupported_rom",
                    "reason_text": "Mario in the game currently supports the US practice ROM."}
        if target is None:
            return {**answer, "reason": "no_target",
                    "reason_text": "Pick a star or a segment to practise."}
        template = templates.active_for(target["kind"], target["entity_key"], target.get("strat"))
        if template is None:
            return {**answer, "reason": "no_template",
                    "reason_text": "Mark an attempt as the template for this target."}
        answer["template_id"] = template.id
        attempt_id = attempt_id_of(template.origin)
        answer["attempt_id"] = attempt_id
        if attempt_id is None or self._replay is None:
            return answer
        key = (template.id, template.origin, target["kind"], target["entity_key"], target.get("strat"))
        if self._cached is not None:
            cached_key, asset, stamp, result = self._cached
            if key == cached_key and stamp is not None and self._fingerprint(asset[0]) == stamp:
                self._asset = asset
                return dict(result)
        found = self._replay.find_native(attempt_id)
        if found is None:
            return answer
        path, manifest = found
        self._asset = (path, manifest)
        result = {**answer, "available": True, "reason": None, "reason_text": None,
                  "exactness": "native_pose"}
        self._cached = key, self._asset, self._fingerprint(path), result
        return result

    def reconcile(self, *, enabled, opacity, nudge, tint, target):
        source = getattr(getattr(self._replay, "recorder", None), "ghost_capture", None)
        if source is not self._source:
            self.stop()
            self._source, self._sent = source, None
        command = {"enabled": False}
        if enabled and self._asset is not None and source is not None:
            path, manifest = self._asset
            command = {"enabled": True, "model_id": 1,
                       "clip_id": bytes.fromhex(manifest["clip_id"]),
                       "clip_sha256": bytes.fromhex(manifest["sha256"]),
                       "clip_path": str(path), "rom_path": "",
                       "opacity": opacity, "nudge": nudge,
                       "tint_enabled": tint["enabled"], "hue_degrees": tint["hue_degrees"],
                       "stop_at_star": target["kind"] == "star", "mode": 0}
        if source is not None and command != self._sent:
            source.set_playback(**command)
            self._sent = command
        self._enabled = command["enabled"]

    def state(self):
        status = self._source.snapshot_status() if self._source is not None else {}
        ready = bool(self._enabled and status.get("available") and status.get("state") == 4)
        # A generated pose is not display evidence. Only the renderer's last
        # qualified swap can say this command reached the game window.
        visible = ready and bool(status.get("displayed"))
        reason = None
        if self._enabled and not ready:
            if status.get("state") == 3:
                reason = "Preparing Mario..."
            elif status.get("state") == 5:
                reason = {4: "Open the US practice ROM to show Mario.",
                          5: "This practice ROM does not provide the required Mario poses.",
                          6: "The saved Mario poses could not be loaded.",
                          7: "Mario's model could not be loaded from this ROM.",
                          8: "The saved Mario poses could not be played."}.get(
                              status.get("reason"), "Mario playback is unavailable; try switching it off and on.")
            else:
                reason = "Start the game with the trainer's GLideN64 renderer."
        return {"running": ready, "visible": visible, "backend": "native", "reason": reason}

    def stop(self):
        if self._source is not None and self._sent != {"enabled": False}:
            self._source.set_playback(enabled=False)
            self._sent = {"enabled": False}
        self._enabled = False
