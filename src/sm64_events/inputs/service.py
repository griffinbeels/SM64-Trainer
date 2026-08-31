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
from sm64_events.inputs.markers import markers_of
from sm64_events.inputs.runs import Run, capture_axis, collapse, stretches
from sm64_events.inputs.templates import TemplateStore
from sm64_events.inputs.track import (document_for_attempt, target_of,
                                      track_for_attempt, track_with_lead)
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


def _shifted_spans(spans: list[dict], shift: int, limit: int) -> list[dict]:
    """Move spans right by `shift` and clip them at `limit` frames."""
    moved = []
    for span in spans:
        start = span["start"] + shift
        if limit and start >= limit:
            break
        length = span["length"]
        if limit:
            length = min(length, limit - start)
        moved.append({**span, "start": start, "length": length})
    return moved


class InputsService:
    """`attempts` is a callable returning the projected attempts; `version`
    is the ROM version being read, which is what an exported document
    records. `events(started_utc, ended_utc)` reads the journal rows inside
    a span and `landmark_names()` the catalogue they are named through --
    the two things the moment markers need; without them a timeline simply
    carries none."""

    def __init__(self, store, templates: TemplateStore, attempts,
                 version: str = "us", events=None, landmark_names=None):
        self.store = store
        self.templates = templates
        self._attempts = attempts
        self._version = version
        self._events = events
        self._landmark_names = landmark_names

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

    def pad_lookup(self, start_utc: str,
                   duration_s: float) -> dict[int, tuple[int, int, int]]:
        """Every captured frame in a wall-clock span: raw game frame ->
        (buttons, stick_x, stick_y) -- the pad as Usamune's display draws
        it. The pixel refiner (replay/pixelmap.py) reads the display out of
        the footage and fits the clip's frame map against this."""
        from datetime import datetime, timedelta
        start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        end = (start + timedelta(seconds=duration_s)).isoformat()
        return {number: (frame.buttons, frame.stick_x, frame.stick_y)
                for number, frame in
                self.store.frames_between(start.isoformat(), end)}

    def timeline(self, attempt_id: int,
                 span: tuple[int, int] | None = None) -> dict:
        """`span` is the range of game frames the attempt's CLIP shows, so
        the timeline can match the video exactly (round 32 item 53). The
        caller reads it off the clip's own frame map; with no clip there is
        no buffer to match and the track is the attempt alone."""
        attempt = self.attempt(attempt_id)
        frames, lead = track_with_lead(self.store, attempt, span)
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
            "markers": self._markers(attempt, frames),
            # The axis's seams -- (axis_start, raw_start, length) per stretch
            # -- so the clip's frame_map (raw game frames) lands on the axis
            # in the browser without a second copy of the restart rule.
            "stretches": [list(row) for row in stretches(frames)],
            "angle_units": A.ANGLE_UNITS,
            "buttons": [[bit, name] for bit, name in A.BUTTON_BITS],
            "stick_max": A.STICK_MAX,
            "dead_zone": A.STICK_DEAD_ZONE,
            # The lead-in's length: the timeline subtracts it, so FRAME 0
            # stays the attempt's start and the lead draws as negative
            # frames -- the PB-identical length is untouched.
            "lead_frames": lead,
            "template": self._template_payload(
                self.templates.active_for(kind, key, attempt.strat_tag),
                shift=lead, limit=axis[-1][0] + 1 if axis else 0),
        }

    def _markers(self, attempt, frames) -> list[dict]:
        """The journal's moments inside the attempt, on the track's axis --
        the recorder's own rows and sentences (`inputs/markers.py`)."""
        if self._events is None or not frames:
            return []
        rows = self._events(attempt.started_utc, attempt.ended_utc)
        names = self._landmark_names() if self._landmark_names else {}
        return markers_of(rows, frames, names)

    @staticmethod
    def _template_payload(template, shift: int = 0,
                          limit: int = 0) -> dict | None:
        if template is None:
            return None
        payload = {"id": template.id, "name": template.name,
                   "origin": template.origin}
        try:
            frames = template.frames()
        except Exception as error:
            # A hand-edited document that stopped loading. Say so on the
            # surface rather than drawing nothing and letting him wonder
            # which of the two runs is missing.
            payload.update(runs=[], actions=[], error=str(error))
            return payload
        # The SAME shape as the attempt's own track, Mario included: the
        # comparison he asked for is "against the exact example, including
        # all mario data", so the template carries everything the run does.
        payload["runs"] = runs_of(frames)
        payload["actions"] = actions_of(frames)
        if shift:
            # The attempt's own frame 0 sits `shift` slots into its axis
            # (the lead-in); the template's sits at 0. Move the template so
            # frame 0 aligns with frame 0, which is the whole comparison --
            # then CLIP to the track's own length. Shifting alone lets a
            # template as long as the attempt run off the right edge, and
            # the lanes then overflow their own box (66 layout defects,
            # measured 2026-08-31): a template is drawn to be compared
            # against what is there, so the part with nothing to compare
            # against is not drawn.
            for name in ("runs", "actions"):
                payload[name] = _shifted_spans(payload[name], shift, limit)
        return payload
