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
import hashlib
from sm64_events.core.profiling import measured

from sm64_events.core.timefmt import GAME_FPS
from sm64_events.inputs.document import DocumentError, decode
from sm64_events.inputs.frame import InputFrame
from sm64_events.inputs.markers import markers_of
from sm64_events.inputs.runs import Run, capture_axis, collapse, stretches
from sm64_events.inputs.templates import TemplateStore
from sm64_events.inputs.track import (document_for_attempt, target_of,
                                      resolve_track, track_for_attempt)
from sm64_events.memory import addresses as A

# Development credit only. A future profile passes its name at composition.
DEVELOPMENT_AUTHOR = "griffman1212"


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


def runs_of(frames: list[tuple[int, InputFrame]], origin: int | None = None) -> list[dict]:
    """The pad (and facing, and speed) as runs on the capture axis."""
    return [_run_payload(run)
            for run in collapse(capture_axis(frames, origin), _same_pad)]


def actions_of(frames: list[tuple[int, InputFrame]], origin: int | None = None) -> list[dict]:
    """What Mario was DOING, as spans on the same axis.

    Its own list rather than a field on every run: a run breaks whenever the
    pad moves, and an action lasts across dozens of those, so putting the
    action on each run would repeat one fact hundreds of times and still need
    the reader to stitch the spans back together.
    """
    return [_action_payload(run)
            for run in collapse(capture_axis(frames, origin), _same_action)]


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
                 version: str = "us", events=None, landmark_names=None,
                 author: str = DEVELOPMENT_AUTHOR):
        self.store = store
        self.templates = templates
        self._attempts = attempts
        self._version = version
        self._events = events
        self._landmark_names = landmark_names
        self.author = author

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
        return document_for_attempt(self.store, attempt, self._version, self.author)

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
            document=document_for_attempt(self.store, attempt, self._version, self.author))

    def preview_template(self, attempt_id: int, text: str) -> dict:
        """Describe the file and its explicit local destination before import.

        A segment number belongs to the sender's database. The file's target
        stays intact for provenance; selecting this attempt chooses where the
        imported example will be compared in this database.
        """
        attempt = self.attempt(attempt_id)
        document = decode(text)
        if not document.frames:
            raise DocumentError("this document has no captured input")
        kind, key = entity_key_of(attempt)
        return {
            "document": {"target": document.target, "strategy": document.strategy,
                         "version": document.version, "author": document.author,
                         "name": document.name,
                         "frames": document.frame_count},
            "destination": {"kind": kind, "entity_key": key,
                            "target": target_of(attempt), "strategy": attempt.strat_tag},
        }

    def import_template(self, attempt_id: int, text: str, name: str):
        """Import into the chosen attempt's target without rewriting provenance."""
        attempt = self.attempt(attempt_id)
        kind, key = entity_key_of(attempt)
        return self.templates.save(kind=kind, entity_key=key,
                                   strat_tag=attempt.strat_tag, name=name,
                                   origin=f"import:{name}", document=text)

    def select_template(self, attempt_id: int, template_id: int):
        """Use a library example for this attempt's strategy.

        A template's original strategy remains useful to other attempts.
        Reuse a local binding, or create one with the portable source intact,
        so the selected example actually replaces this timeline's comparison.
        """
        attempt = self.attempt(attempt_id)
        kind, key = entity_key_of(attempt)
        source = self.templates.get(template_id)
        if (source.kind, source.entity_key) != (kind, key):
            raise ValueError("choose a template for this attempt's target")
        if not decode(source.document).frames:
            raise DocumentError("this document has no captured input")
        if (source.strat_tag or "") == (attempt.strat_tag or ""):
            return self.templates.activate(source.id)
        for existing in self.templates.list_for(kind, key):
            if ((existing.strat_tag or "") == (attempt.strat_tag or "")
                    and (existing.document, existing.name, existing.origin)
                    == (source.document, source.name, source.origin)):
                return self.templates.activate(existing.id)
        return self.templates.save(kind=kind, entity_key=key, strat_tag=attempt.strat_tag,
                                   name=source.name, origin=source.origin,
                                   document=source.document)

    def pad_lookup(self, start_utc: str,
                   duration_s: float) -> dict[int, tuple[int, int, int]]:
        """Every captured frame in a wall-clock span: raw game frame ->
        (buttons, stick_x, stick_y) -- the pad as Usamune's display draws
        it. Read by the offline pad instruments (`tools/score_pad_read.py`,
        `tools/score_picture_offset.py`), which check what the screen shows
        against what the timeline holds. Nothing in extraction calls it: the
        capture layer stamps the pad beside each picture, so the shipped
        check compares two numbers rather than reading pixels."""
        from datetime import datetime, timedelta
        start = datetime.fromisoformat(start_utc.replace("Z", "+00:00"))
        end = (start + timedelta(seconds=duration_s)).isoformat()
        return {number: (frame.buttons, frame.stick_x, frame.stick_y)
                for number, frame in
                self.store.frames_between(start.isoformat(), end)}

    @measured("inputs.timeline")
    def timeline(self, attempt_id: int,
                 span: tuple[int, int] | None = None) -> dict:
        """Widen the selected capture occurrence to include `span` buffers.

        Raw min/max cannot describe multiple counter epochs in a clip. That
        requires an ordered occurrence join; this legacy range is not one.
        Without a span, the track is the attempt alone.
        """
        attempt = self.attempt(attempt_id)
        track = resolve_track(self.store, attempt, span)
        frames, lead = track.frames, track.lead_frames
        total = track.frame_count
        kind, key = entity_key_of(attempt)
        return {
            "attempt_id": attempt.id,
            "kind": kind,
            "entity_key": key,
            "target": target_of(attempt),
            "strategy": attempt.strat_tag,
            "local_author": self.author,
            "fps": GAME_FPS,
            "frames": total,
            # The attempt's OWN length -- Usamune's number, the one the row
            # shows -- for the header. `frames` is what the track holds,
            # which with a clip's buffers is longer, and before the
            # settling flush can be a frame short; neither is the time he
            # is graded on (his report 2026-09-01: 19"16 before the clip,
            # 21"30 after, against a 0'19"20 row).
            "attempt_frames": (getattr(attempt, "igt_frames", None)
                               or getattr(attempt, "rta_frames", None)
                               or total),
            "runs": runs_of(frames, track.origin),
            "actions": actions_of(frames, track.origin),
            "markers": self._markers(attempt, frames, track.origin, total),
            # The axis's seams -- (axis_start, raw_start, length) per stretch
            # -- so the clip's frame_map (raw game frames) lands on the axis
            # in the browser without a second copy of the restart rule.
            "stretches": [list(row) for row in stretches(frames, track.origin, total)],
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
                shift=lead, limit=total),
        }

    def _markers(self, attempt, frames, origin=None, frame_count=None) -> list[dict]:
        """The journal's moments inside the attempt, on the track's axis --
        the recorder's own rows and sentences (`inputs/markers.py`)."""
        if self._events is None or not frames:
            return []
        rows = self._events(attempt.started_utc, attempt.ended_utc)
        names = self._landmark_names() if self._landmark_names else {}
        return markers_of(rows, frames, names, origin, frame_count)

    @staticmethod
    def _template_payload(template, shift: int = 0,
                          limit: int | None = None) -> dict | None:
        if template is None:
            return None
        payload = {"id": template.id, "name": template.name,
                   "origin": template.origin}
        try:
            document = decode(template.document)
            frames = document.frames
            payload.update(author=document.author, frames=document.frame_count)
        except Exception as error:
            # A hand-edited document that stopped loading. Say so on the
            # surface rather than drawing nothing and letting him wonder
            # which of the two runs is missing.
            payload.update(runs=[], actions=[], error=str(error))
            return payload
        # The SAME shape as the attempt's own track, Mario included: the
        # comparison he asked for is "against the exact example, including
        # all mario data", so the template carries everything the run does.
        # Document numbers already name the frame-zero axis. capture_axis
        # would move a first captured frame after a leading gap back to zero.
        payload["runs"] = [_run_payload(run) for run in collapse(frames, _same_pad)]
        payload["actions"] = [_action_payload(run) for run in collapse(frames, _same_action)]
        # Review can translate the whole example into a shorter attempt. Keep
        # its original axis and gaps available, including the currently hidden
        # tail; the legacy fields remain lead-shifted and clipped as before.
        payload["source"] = {
            "frames": document.frame_count,
            "runs": payload["runs"], "actions": payload["actions"],
            "revision": hashlib.sha256(template.document.encode("utf-8")).hexdigest(),
        }
        for name in ("runs", "actions"):
            payload[name] = ([] if limit == 0 else
                             _shifted_spans(payload[name], shift, limit or 0))
        return payload
