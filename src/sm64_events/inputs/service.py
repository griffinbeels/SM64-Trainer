# src/sm64_events/inputs/service.py
"""What the timeline asks for: one attempt's input, and the template beside it.

The payload sends RUNS rather than frames — 45 s of play is 1,348 frames and
289 runs — and it sends the BUTTON TABLE with them. That second part is not
convenience: it means the browser never names a button bit, so there is no
second copy of the table to drift from `memory/addresses.py`. A duplicate that
cannot be written needs no parity test to keep it honest.
"""
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.templates import TemplateStore
from sm64_events.inputs.track import target_of, track_for_attempt
from sm64_events.memory import addresses as A

FPS = 30


def entity_key_of(attempt) -> tuple[str, str]:
    """(kind, key) — the identity a template hangs off.

    Star and segment attempts both answer, per the star-segment parity rule:
    an input timeline is a property of a practiced thing, and both kinds are
    practiced things.
    """
    if attempt.segment_id is not None:
        return "segment", str(attempt.segment_id)
    return "star", f"{attempt.course_id}-{attempt.star_id}"


def actions_of(frames: list[tuple[int, InputFrame]]) -> list[dict]:
    """What Mario was DOING, as spans a person can read.

    Its own list rather than a field on every run: a run breaks whenever the
    pad moves, and an action lasts across dozens of those, so putting the
    action on each run would repeat one fact hundreds of times and still need
    the reader to stitch the spans back together.

    The label is the action's own NAME where `addresses.py` knows it and its
    GROUP otherwise -- never a bare hex id, which is a number nobody can read
    dressed up as diagnostic information.
    """
    spans: list[dict] = []
    offset = -frames[0][0] if frames else 0
    previous: int | None = None
    for number, frame in frames:
        if previous is not None and number < previous:
            offset = (spans[-1]["start"] + spans[-1]["length"]) - number
        local = number + offset
        previous = number
        if spans and spans[-1]["action"] == frame.action \
                and spans[-1]["start"] + spans[-1]["length"] == local:
            spans[-1]["length"] += 1
            continue
        spans.append({"start": local, "length": 1, "action": frame.action,
                      "label": A.action_label(frame.action),
                      "group": A.action_group(frame.action)})
    return spans


def runs_of(frames: list[tuple[int, InputFrame]]) -> list[list[int]]:
    """[start, length, buttons, stick_x, stick_y, yaw, speed], zero-based.

    The x-axis is a position in the CAPTURE, not the raw counter, and those
    are not the same thing: the game's frame counter restarts on a console
    reset, so a track that spans one contains a descending number. Zero-basing
    on the first frame alone would then produce NEGATIVE offsets and a
    timeline that reads `-937 frames` -- which is exactly what the fixture
    render showed before this handled it (2026-08-21).

    So each backward step lays the next stretch of counter END TO END after
    the last, while holes INSIDE a stretch are preserved as holes. A reset is
    a seam in the recording, not a jump backwards through it.
    """
    if not frames:
        return []
    out: list[list[int]] = []
    offset = -frames[0][0]
    previous: int | None = None
    for number, frame in frames:
        if previous is not None and number < previous:
            offset = (out[-1][0] + out[-1][1]) - number
        local = number + offset
        previous = number
        if out:
            start, length, buttons, stick_x, stick_y, yaw, speed = out[-1]
            if (local == start + length and buttons == frame.buttons
                    and stick_x == frame.stick_x
                    and stick_y == frame.stick_y and yaw == frame.yaw
                    and speed == frame.speed):
                out[-1][1] = length + 1
                continue
        out.append([local, 1, frame.buttons, frame.stick_x, frame.stick_y,
                    frame.yaw, round(frame.speed, 3)])
    return out


def _span(runs: list[list[int]]) -> int:
    return (runs[-1][0] + runs[-1][1]) if runs else 0


class InputsService:
    """Reads only. Nothing here writes to the journal or to the store."""

    def __init__(self, store, templates: TemplateStore, attempts):
        self._store = store
        self._templates = templates
        self._attempts = attempts        # callable -> list[Attempt]

    def _attempt(self, attempt_id: int):
        for attempt in self._attempts():
            if attempt.id == attempt_id:
                return attempt
        raise LookupError(f"no attempt {attempt_id}")

    def timeline(self, attempt_id: int) -> dict:
        attempt = self._attempt(attempt_id)
        frames = track_for_attempt(self._store, attempt)
        runs = runs_of(frames)
        kind, key = entity_key_of(attempt)
        template = self._templates.active_for(kind, key, attempt.strat_tag)
        template_payload = None
        if template is not None:
            try:
                template_payload = {
                    "id": template.id, "name": template.name,
                    "origin": template.origin,
                    "runs": runs_of(template.frames()),
                    "actions": actions_of(template.frames()),
                }
            except Exception as error:
                # A hand-edited document that stopped loading. Say so on the
                # surface rather than drawing nothing and letting him wonder
                # which of the two runs is missing.
                template_payload = {"id": template.id, "name": template.name,
                                    "origin": template.origin, "runs": [],
                                    "error": str(error)}
        return {
            "attempt_id": attempt.id,
            "kind": kind,
            "entity_key": key,
            "target": target_of(attempt),
            "strategy": attempt.strat_tag,
            "fps": FPS,
            "frames": _span(runs),
            "runs": runs,
            "actions": actions_of(frames),
            "angle_units": A.ANGLE_UNITS,
            "buttons": [[bit, name] for bit, name in A.BUTTON_BITS],
            "stick_max": A.STICK_MAX,
            "dead_zone": A.STICK_DEAD_ZONE,
            "template": template_payload,
        }
