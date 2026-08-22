# src/sm64_events/inputs/service.py
"""Everything the outside asks of captured input, behind ONE door.

The timeline payload, the attempt's document, marking an attempt as the
template, and the template store itself all come through here, so the REST
router owns no logic of its own and the composition root wires one object
rather than a bundle of four.

The payload sends RUNS rather than frames — 45 s of play is 1,348 frames and
289 runs — and it sends the BUTTON TABLE with them. That second part is not
convenience: it means the browser never names a button bit, so there is no
second copy of the table to drift from `memory/addresses.py`. A duplicate that
cannot be written needs no parity test to keep it honest.
"""
from sm64_events.core.timefmt import GAME_FPS
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.runs import Run, capture_axis, collapse
from sm64_events.inputs.templates import TemplateStore
from sm64_events.inputs.track import (document_for_attempt, target_of,
                                      track_for_attempt)
from sm64_events.memory import addresses as A


def entity_key_of(attempt) -> tuple[str, str]:
    """(kind, key) — the identity a template hangs off.

    Star and segment attempts both answer, per the star-segment parity rule:
    an input timeline is a property of a practiced thing, and both kinds are
    practiced things.
    """
    if attempt.segment_id is not None:
        return "segment", str(attempt.segment_id)
    return "star", f"{attempt.course_id}-{attempt.star_id}"


def _same_pad(frame: InputFrame, previous: InputFrame) -> bool:
    """What a timeline run holds still for: the pad, and Mario's facing and
    speed. NOT his action -- that lasts across dozens of pad changes and
    rides its own span list (`actions_of`)."""
    return (frame.buttons == previous.buttons
            and frame.stick_x == previous.stick_x
            and frame.stick_y == previous.stick_y
            and frame.yaw == previous.yaw
            and frame.speed == previous.speed)


def _same_action(frame: InputFrame, previous: InputFrame) -> bool:
    return frame.action == previous.action


def _run_payload(run: Run) -> dict:
    return {"start": run.start, "length": run.length,
            "buttons": run.frame.buttons,
            "stick_x": run.frame.stick_x, "stick_y": run.frame.stick_y,
            "yaw": run.frame.yaw, "speed": round(run.frame.speed, 3)}


def _action_payload(run: Run) -> dict:
    # The label is the action's own NAME where `addresses.py` knows it and
    # its GROUP otherwise -- never a bare hex id, which is a number nobody can
    # read dressed up as diagnostic information.
    return {"start": run.start, "length": run.length,
            "action": run.frame.action,
            "label": A.action_label(run.frame.action),
            "group": A.action_group(run.frame.action)}


def runs_of(frames: list[tuple[int, InputFrame]]) -> list[dict]:
    """The pad (and facing, and speed) as runs on the capture axis."""
    return [_run_payload(run)
            for run in collapse(capture_axis(frames), _same_pad)]


def actions_of(frames: list[tuple[int, InputFrame]]) -> list[dict]:
    """What Mario was DOING, as spans on the same axis.

    Its own list rather than a field on every run: a run breaks whenever the
    pad moves, and an action lasts across dozens of those, so putting the
    action on each run would repeat one fact hundreds of times and still need
    the reader to stitch the spans back together.
    """
    return [_action_payload(run)
            for run in collapse(capture_axis(frames), _same_action)]


class InputsService:
    """`attempts` is a callable returning the projected attempts; `version`
    is the ROM version being read, which is what an exported document
    records."""

    def __init__(self, store, templates: TemplateStore, attempts,
                 version: str = "us"):
        self.store = store
        self.templates = templates
        self._attempts = attempts
        self._version = version

    def attempt(self, attempt_id: int):
        for attempt in self._attempts():
            if attempt.id == attempt_id:
                return attempt
        raise LookupError(f"no attempt {attempt_id}")

    def document(self, attempt_id: int) -> str:
        """The attempt's track as a portable document.

        Raises LookupError for an attempt with no captured input: there is no
        honest document to write for one, and an empty file looks like a
        finished export.
        """
        attempt = self.attempt(attempt_id)
        if not track_for_attempt(self.store, attempt):
            raise LookupError("this attempt has no captured input")
        return document_for_attempt(self.store, attempt, self._version)

    def mark_template(self, attempt_id: int, name: str | None = None):
        """Make this attempt the template for its target and strategy.

        Raises ValueError for an attempt with no captured input -- there is
        nothing to compare against, which is a conflict with the request
        rather than a missing attempt.
        """
        attempt = self.attempt(attempt_id)
        if not track_for_attempt(self.store, attempt):
            raise ValueError("this attempt has no captured input, so there "
                             "is nothing to compare against")
        kind, key = entity_key_of(attempt)
        return self.templates.save(
            kind=kind, entity_key=key, strat_tag=attempt.strat_tag,
            name=name or f"attempt #{attempt.id}",
            origin=f"attempt:{attempt.id}",
            document=document_for_attempt(self.store, attempt, self._version))

    def timeline(self, attempt_id: int) -> dict:
        attempt = self.attempt(attempt_id)
        frames = track_for_attempt(self.store, attempt)
        axis = capture_axis(frames)
        kind, key = entity_key_of(attempt)
        return {
            "attempt_id": attempt.id,
            "kind": kind,
            "entity_key": key,
            "target": target_of(attempt),
            "strategy": attempt.strat_tag,
            "fps": GAME_FPS,
            "frames": axis[-1][0] + 1 if axis else 0,
            "runs": runs_of(frames),
            "actions": actions_of(frames),
            "angle_units": A.ANGLE_UNITS,
            "buttons": [[bit, name] for bit, name in A.BUTTON_BITS],
            "stick_max": A.STICK_MAX,
            "dead_zone": A.STICK_DEAD_ZONE,
            "template": self._template_payload(
                self.templates.active_for(kind, key, attempt.strat_tag)),
        }

    @staticmethod
    def _template_payload(template) -> dict | None:
        if template is None:
            return None
        payload = {"id": template.id, "name": template.name,
                   "origin": template.origin}
        try:
            payload["runs"] = runs_of(template.frames())
        except Exception as error:
            # A hand-edited document that stopped loading. Say so on the
            # surface rather than drawing nothing and letting him wonder
            # which of the two runs is missing.
            payload["runs"] = []
            payload["error"] = str(error)
        return payload
