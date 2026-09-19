# src/sm64_events/ghost/source.py
"""Does this star or segment have a ghost, and where is its footage?

**A template is a document, not a video** (`inputs/templates.py`). Nothing
links one to a clip, so the footage has to be reached through the run the
template was marked from: active template -> its origin attempt -> that
attempt's SAVED recording -> the clip view payload carrying the frame map.
His ruling on that reading, 2026-09-18: *"yes"*.

Three things follow, and each is a case a player has to be told about rather
than left guessing at:

- a template that came from a file or was typed by hand has no run behind it;
- the ring is not a source. Footage that was never saved is gone with the
  session, so an unsaved run reads as unavailable rather than making the
  practice page cut a clip out of the live buffer;
- a clip recorded without the capture layer carries no frame map, so nothing
  can say which moment of the game each picture shows, and a ghost slaved to
  the live clock could not stay in step with him.

The view payload is **passed through untouched**. Its one owner is
`replay/service.py`; re-deriving any of `frame_map`, `picture_igt`,
`frame_times` or `video_start_s` here would be the competing second path the
single-source rule forbids (domain contract 6).

**Providers are rows.** `resolve_ghost` asks each in turn and takes the first
answer. Today there is one — the marked attempt's saved clip. An imported
template that ships its own replay, and downloaded footage trimmed by hand
(his two later asks), each arrive as one more function in `PROVIDERS`, not as
a second copy of this resolution.

**The SPAN is the attempt, and it is looked up rather than guessed.** His
ruling, 2026-09-19: *"Ghost footage should be clamped to the EXACT START FRAME
of the attempt, and the exact ending frame of the star grab... It should always
be clamped to exactly the span of the attempt, regardless of the user's
recording settings."* A clip is cut with whatever pre-roll and post-roll the
recorder was set to, so his own first try began with 44 pictures of the
PREVIOUS run — the game's timer still frozen at that run's final value through
the reset's reload — and ended fifteen pictures after the star. Neither is his
attempt.

Nothing has to be inferred from the timer's shape, because the system already
records both ends: the attempt row carries the game frame its anchor (the
reset) landed on and how many frames it ran, and the clip's frame map says
which game frame every picture shows. So the span is two lookups —
the first picture at or after the start, the last picture at or before the end
— and `basis` records which reading answered.

**A star ends on the TOUCH.** His ruling, 2026-09-19: *"End at the touch. No
reason to keep it after that... Disappear upon its own star touch."* The
recorded TIME is taken at the landing (the x-cam, 0-39 frames after a midair
grab), and the ghost used to run that far; it now stops where he stops
watching. The touch is journaled on the grab event's own `grab_frame` and
never copied onto the attempt, so it is read back through the narrowest
read-only door there is — the journal rows inside the attempt's own wall-clock
span. The landing stays on the span as `timed_frame`, so nothing is lost, and
a row whose touch cannot be found (a grab journaled before that field existed,
an imported time) falls back to it and says so in `basis`. A SEGMENT has no
touch and keeps its own end.

`span` is a SIBLING of `view`, never a rewrite of it — the view still belongs
to `replay/service.py` alone (domain contract 6).

**WHERE THE GHOST BELONGS.** His report, 2026-09-19: *"I exited LLL (by dying)
-- and it looks like the ghost is still playing... We should only play a ghost
when it's the active star / we're in that course/area."* The overlay page has
always had the gate for this and nothing to feed it, so the answer is added
here: a star's own course names one level, and the live clock says which level
the player is in. A COURSE'S OWN SUBAREAS ARE THE COURSE — the pyramid is still
Shifting Sand Land, and the same seven stars are practised inside it — so a
star's gate names the level and leaves the area open. The CASTLE is the
opposite case and the one exception: its areas are three different places, so a
castle secret star (course 0) names the area too where the game says which one.
A SEGMENT names no place at all: a movement crosses levels by definition, and a
gate that refused the second half of a route would be worse than none.

**AND WHERE MARIO IS IN IT.** His words, 2026-09-20: *"let's do tight
rectangles on our existing ghosts, let's try it."* A clip may have a box list
beside it -- one rectangle per picture, made outside the app -- and a ghost
that has one can be drawn as MARIO ALONE instead of the whole recorded screen.
The list is read and re-validated here on every answer (`ghost/mariobox.py`),
never cached and never trusted because it was saved; a ghost without one simply
does not offer that view, and says why. `mario_boxes` is another SIBLING of
`view`, like `span`: it is beside the clip on disk and owns nothing inside it.
"""
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from sm64_events.detectors.anchors import NEAR_ZERO_IGT
from sm64_events.ghost.mariobox import MarioBoxList
from sm64_events.ghost.mariobox import REASONS as BOX_REASONS
from sm64_events.ghost.mariobox import read_box_list
from sm64_events.memory.addresses import (CASTLE_SECRET_STAR_AREAS,
                                          COURSE_BY_LEVEL, LEVEL_CASTLE_INSIDE)

KINDS = ("star", "segment")

# One sentence per unavailable case, in the words a player would use. No
# technique names: the practice card shows this straight to him.
REASONS: dict[str, str] = {
    "no_template":
        "You have not set a template for this yet, so there is no run to "
        "play alongside you.",
    "template_has_no_attempt":
        "This template came from a file rather than from a run you played "
        "here, so there is no footage behind it.",
    "recording_not_saved":
        "The run this template was marked from has no saved recording, so "
        "there is nothing to show.",
    "recording_has_no_frame_map":
        "The recording behind this template does not say which moment of "
        "the game each picture shows, so it could not stay in step with "
        "your play.",
}

# What the ghost's timing is worth. "frame_map" is exact — every picture
# carries the game's own timer. "approximate" is reserved for downloaded
# footage, which has no map and can only count seconds from a trim point.
EXACT = "frame_map"

# How the span's two ends were found, on `GhostSpan.basis`; a reading that
# took both is joined, e.g. "attempt_frames+grab_event".
ATTEMPT_FRAMES = "attempt_frames"   # the attempt row's own anchor and length
GRAB_EVENT = "grab_event"           # the journalled star touch
PICTURE_TIMER = "picture_timer"     # the clip's per-picture game timer

#: The one event whose payload names the frame the star was touched on.
GRAB_EVENT_TYPE = "star_collected"

#: course id -> the level it is played in. `COURSE_BY_LEVEL` in
#: `memory/addresses.py` is THE registry and it is 1:1, so this is its own
#: table read backwards rather than a second opinion about which level a course
#: is; nothing here knows a level id that the registry does not.
LEVEL_BY_COURSE = {course: level for level, course in COURSE_BY_LEVEL.items()}


@dataclass(frozen=True)
class GhostSpan:
    """Which pictures of the clip ARE the attempt.

    `first_picture` and `last_picture` are INCLUSIVE indices into the view's
    per-picture arrays, so a follower clamps with them and needs no arithmetic
    of its own. The frames beside them say what those indices mean, so a
    disagreement can be read rather than argued about.

    `end_frame` is where the ghost STOPS — the touch for a star whose grab is
    on the journal. `timed_frame` is where the recorded TIME was taken, which
    for a midair grab is the landing a few frames later; it is kept because it
    is the other honest answer to "when did this run end" and losing it would
    make the two unreconcilable.
    """

    first_picture: int
    last_picture: int
    start_frame: int
    end_frame: int
    timed_frame: int
    basis: str
    grab_frame: int | None = None

    def payload(self) -> dict:
        return {"first_picture": self.first_picture,
                "last_picture": self.last_picture,
                "start_frame": self.start_frame,
                "end_frame": self.end_frame,
                "timed_frame": self.timed_frame,
                "grab_frame": self.grab_frame,
                "basis": self.basis}


@dataclass(frozen=True)
class GhostSource:
    """The answer, whichever way it went."""

    available: bool
    reason: str | None = None
    reason_text: str | None = None
    template_id: int | None = None
    attempt_id: int | None = None
    media_url: str | None = None
    view: dict | None = None
    exactness: str | None = None
    span: GhostSpan | None = None
    #: Where this ghost belongs: the level the target is played in, and the
    #: area only where the target names one. Null means "no gate" -- a segment,
    #: or a target whose place this build cannot name -- and the page then
    #: shows the ghost wherever the player is, which is what it did before.
    level: int | None = None
    area: int | None = None
    #: Mario's rectangle in every picture, when a box list sits beside this
    #: clip, and the named reason when one does not. Absent is the ORDINARY
    #: case: every ghost recorded before this existed has no boxes.
    mario_boxes: MarioBoxList | None = None
    mario_boxes_reason: str | None = None
    #: WHERE THE CLIP IS ON DISK, and deliberately NOT on the wire. The box
    #: list is found beside it, so the provider that resolved the clip is the
    #: only thing that can say where to look; the page has no business knowing
    #: a path on his machine, and `payload()` therefore omits it.
    clip_path: Path | None = None

    def payload(self) -> dict:
        """The JSON the route serves. `view` is the clip's own payload, by
        reference: nothing here reshapes it."""
        return {"available": self.available,
                "reason": self.reason,
                "reason_text": self.reason_text,
                "template_id": self.template_id,
                "attempt_id": self.attempt_id,
                "media_url": self.media_url,
                "view": self.view,
                "exactness": self.exactness,
                "span": self.span.payload() if self.span else None,
                "level": self.level,
                "area": self.area,
                "mario_boxes": self.mario_boxes.payload() if self.mario_boxes
                               else None,
                "mario_boxes_reason": self.mario_boxes_reason,
                "mario_boxes_reason_text":
                    BOX_REASONS.get(self.mario_boxes_reason)
                    if self.mario_boxes_reason else None}


def unavailable(reason: str, **known: Any) -> GhostSource:
    """No ghost, and the sentence saying why. Whatever was resolved before
    the refusal is carried anyway — the practice card can say which template
    it looked at."""
    return GhostSource(available=False, reason=reason,
                       reason_text=REASONS[reason], **known)


def attempt_id_of(origin: str) -> int | None:
    """`attempt:<id>` -> the id. `import:<name>` and `authored` -> None."""
    prefix, sep, rest = origin.partition(":")
    if prefix != "attempt" or not sep or not rest.isdigit():
        return None
    return int(rest)


def from_marked_attempt(template, *, replay) -> GhostSource | None:
    """The one provider that exists today: the saved clip of the run this
    template was marked from. Returns None for an origin it does not own, so
    the next row gets its turn."""
    attempt_id = attempt_id_of(template.origin)
    if attempt_id is None:
        return None
    known = {"template_id": template.id, "attempt_id": attempt_id}
    saved = replay.find_saved(attempt_id)
    if saved is None:
        # Asked BEFORE view(), which would cut a clip out of the live ring
        # for an unsaved run. Reading a practice card must never do that.
        return unavailable("recording_not_saved", **known)
    try:
        view = replay.view(attempt_id)
    except (LookupError, ValueError):
        # A file whose attempt left the history: no clock to line it up
        # against, which for him is the same as never having saved it.
        return unavailable("recording_not_saved", **known)
    if view.get("frame_map") is None:
        return unavailable("recording_has_no_frame_map", **known)
    # The clip's own path rides along so the box list can be found beside it;
    # `payload()` never emits it.
    return GhostSource(available=True, media_url=view["clip_url"], view=view,
                       exactness=EXACT, clip_path=saved, **known)


Provider = Callable[..., GhostSource | None]
PROVIDERS: tuple[Provider, ...] = (from_marked_attempt,)


def _attempt_ends(attempt) -> tuple[int, int] | None:
    """(start, timed) from the attempt's OWN frames, or None if it has none —
    an imported time, or any row closed with no anchor to measure from.

    `anchor_frame` is the game frame the reset landed on and `rta_frames` the
    frames from there to the closing event, which for a star IS the x-cam: the
    moment the recorded time was taken.
    """
    anchor = getattr(attempt, "anchor_frame", None)
    ran = getattr(attempt, "rta_frames", None)
    if anchor is None or ran is None or ran < 0:
        return None
    return anchor, anchor + ran


def _touch_frame(attempt, events, timed: int) -> tuple[int, str] | None:
    """(the frame the star was TOUCHED on, where that came from), or None.

    The touch lives on the grab event's `grab_frame` and nowhere else, so this
    reads the journal rows inside the attempt's own wall-clock span and takes
    the closing grab's — matched to the row's own star, so a stray grab inside
    the span cannot answer for it. Three ways to get nothing back, all
    ordinary: a SEGMENT has no touch at all; a grab journaled before that field
    existed carries none; and a boot with no database yet has no door to ask.

    A row whose time was taken AT the grab (`timed_at`) needs no lookup at all
    — the landing IS the touch there, the backstop case where no star dance
    ran — and says `attempt_frames`, because that is where the frame came from.
    """
    if getattr(attempt, "segment_id", None) is not None:
        return None
    if getattr(attempt, "timed_at", None) == "grab":
        return timed, ATTEMPT_FRAMES
    started = getattr(attempt, "started_utc", None)
    ended = getattr(attempt, "ended_utc", None)
    if events is None or not started or not ended:
        return None
    course = getattr(attempt, "course_id", None)
    star = getattr(attempt, "star_id", None)
    found = None
    for row in events(started, ended):
        if getattr(row, "type", None) != GRAB_EVENT_TYPE:
            continue
        payload = getattr(row, "payload", None) or {}
        if course is not None and payload.get("course_id") not in (None, course):
            continue
        if star is not None and payload.get("star_id") not in (None, star):
            continue
        frame = payload.get("grab_frame")
        if isinstance(frame, int):
            found = frame
    return None if found is None else (found, f"{ATTEMPT_FRAMES}+{GRAB_EVENT}")


def _recorded_frames(attempt) -> int | None:
    """How long the row says the attempt ran — Usamune's number where there is
    one, the wall-frame delta otherwise. Only the timer fallback needs it."""
    for field in ("igt_frames", "rta_frames"):
        value = getattr(attempt, field, None)
        if value:
            return int(value)
    return None


def _timer_pictures(view: dict, attempt) -> tuple[int, int] | None:
    """The run's first and last picture read off the clip's own per-picture
    timer, for a row whose frames are unavailable.

    The start is the LAST drop into the near-zero band (`anchors.NEAR_ZERO_IGT`,
    the same one the detectors call near-zero): a clip can open with the
    previous run's final value held through the reset's reload, and a clip that
    opens already at zero has no drop to find. The end is the first picture to
    reach the recorded time, so the frozen tail after the star is left out.
    """
    timer = view.get("picture_igt") or []
    frames = view.get("frame_map") or []
    known = [i for i, (t, f) in enumerate(zip(timer, frames, strict=False))
             if t is not None and f is not None]
    if not known:
        return None
    drops = [slot for before, slot in zip(known, known[1:], strict=False)
             if timer[before] > NEAR_ZERO_IGT >= timer[slot]]
    first = drops[-1] if drops else None
    if first is None and timer[known[0]] <= NEAR_ZERO_IGT:
        first = known[0]
    recorded = _recorded_frames(attempt)
    if first is None or recorded is None:
        return None
    run = [i for i in known if i >= first]
    return first, next((i for i in run if timer[i] >= recorded), run[-1])


def span_of(view: dict, attempt=None, events=None) -> GhostSpan | None:
    """The pictures this ghost may show: exactly the attempt, never the
    lead-in before the reset nor anything past the star it was practising.

    Both ends are LOOKED UP in the frame map — the first picture at or after
    the start, the last at or before the end — so two pictures sharing a game
    frame and a picture with no stamp at all leave the answer where it was.
    A span that lands nowhere in this clip is None rather than a guess: the
    page has its own fallback and a wrong clamp would hide real footage.
    """
    frames = view.get("frame_map") or []
    ends = _attempt_ends(attempt)
    if ends is not None:
        start, timed = ends
        touch = _touch_frame(attempt, events, timed)
        if touch is None or not start <= touch[0] <= timed:
            # No touch to be had: end where the time was taken, which is what
            # the ghost did before his ruling, and say so in the basis.
            grab, end, basis = None, timed, ATTEMPT_FRAMES
        else:
            grab, basis = touch
            end = grab
        first = next((i for i, f in enumerate(frames)
                      if f is not None and f >= start), None)
        last = next((i for i in reversed(range(len(frames)))
                     if frames[i] is not None and frames[i] <= end), None)
        if first is None or last is None or last < first:
            return None
        return GhostSpan(first, last, start, end, timed, basis, grab)
    pictures = _timer_pictures(view, attempt)
    if pictures is None:
        return None
    first, last = pictures
    return GhostSpan(first, last, frames[first], frames[last], frames[last],
                     PICTURE_TIMER)


def place_of(kind: str, entity_key: str) -> tuple[int | None, int | None]:
    """(level, area) the ghost for this target belongs in, either may be None.

    A star's key is `"<course>-<star>"` (`inputs/service.py::entity_key_of`).
    The course names its level through the registry above; the AREA is left
    open, because a course's own subareas are that course -- gating LLL's
    volcano out would take the ghost down inside the very star it is playing.

    Course 0 is the castle's own secret stars, and the castle is the one place
    whose areas are genuinely different rooms. Those stars name the castle
    interior, and the area as well for the two the game pins down
    (`CASTLE_SECRET_STAR_AREAS`); the rest name the level alone rather than a
    guess.

    A segment answers (None, None): a movement crosses levels by definition.
    """
    if kind != "star":
        return None, None
    course, _, star = str(entity_key).partition("-")
    if not course.isdigit() or not star.isdigit():
        return None, None
    course_id, star_id = int(course), int(star)
    if course_id == 0:
        return LEVEL_CASTLE_INSIDE, CASTLE_SECRET_STAR_AREAS.get(star_id)
    return LEVEL_BY_COURSE.get(course_id), None


def _with_place(answer: GhostSource, kind: str, entity_key: str) -> GhostSource:
    """Say where this ghost belongs, whichever way the answer went."""
    level, area = place_of(kind, entity_key)
    return replace(answer, level=level, area=area)


def _with_boxes(answer: GhostSource) -> GhostSource:
    """Attach Mario's per-picture rectangles, or the named reason there are
    none. Beside the view, never inside it.

    READ EVERY TIME, and validated against THIS clip's picture count. The list
    is made outside the app by a hand-run tool; the clip can be re-extracted
    under it, and the count is the one check that catches a list belonging to
    another cut of the same run. Caching it would be caching somebody else's
    file. Attached HERE rather than in the provider so a later provider (an
    imported replay, downloaded footage) gets it for free and none of them can
    grow a second rule for where the boxes live.
    """
    if not answer.available or not answer.view or answer.clip_path is None:
        return replace(answer, mario_boxes=None,
                       mario_boxes_reason="no_box_list")
    stamps = answer.view.get("picture_igt") or ()
    boxes, reason = read_box_list(answer.clip_path,
                                  pictures=len(stamps) or None)
    return replace(answer, mario_boxes=boxes, mario_boxes_reason=reason)


def _attempt_row(attempts, attempt_id):
    """The attempt behind this ghost, or None when nothing can name it. A
    missing row is not an error — the footage is still playable, unclamped."""
    if attempts is None or attempt_id is None:
        return None
    try:
        return attempts(attempt_id)
    except LookupError:
        return None


def _with_span(answer: GhostSource, attempts, events) -> GhostSource:
    """Attach the span BESIDE the view, whichever provider answered."""
    if not answer.available or not answer.view:
        return answer
    row = _attempt_row(attempts, answer.attempt_id)
    return replace(answer, span=span_of(answer.view, row, events))


def _answer(source: GhostSource, kind: str, entity_key: str) -> GhostSource:
    """Every answer, whichever way it went, says where the ghost belongs and
    whether Mario has been found in it. One door so a new refusal cannot ship
    with a field nobody filled -- `None` there would mean "not asked", which is
    a third state the page has no reading for."""
    return _with_place(_with_boxes(source), kind, entity_key)


def resolve_ghost(kind: str, entity_key: str, strat: str | None = None, *,
                  templates, replay, attempts=None, events=None,
                  providers: tuple[Provider, ...] = PROVIDERS) -> GhostSource:
    """Is there a ghost for this star or segment, and where is its footage?

    `templates` is the template store and `replay` the replay service; both
    are read-only here. The strategy falls back to the strategy-less template
    through the store's own rule, so a template recorded before he named a
    strategy still answers.

    `attempts` is `attempt_id -> the attempt row` (the captured-input service's
    own door) and `events` is `(started_utc, ended_utc) -> the journal rows in
    that span` (`storage/db.py::events_between`). Together they are what let
    the span be exact; without them a ghost still resolves, and the span falls
    back to the landing, to the clip's timer, or to null. The span is attached
    HERE rather than inside a provider so every future provider gets it for
    free and none of them can grow a second rule for it.
    """
    if kind not in KINDS:
        raise ValueError("a ghost is for a star or a segment")
    template = templates.active_for(kind, entity_key, strat)
    if template is None:
        return _answer(unavailable("no_template"), kind, entity_key)
    for provider in providers:
        answer = provider(template, replay=replay)
        if answer is not None:
            return _answer(_with_span(answer, attempts, events),
                           kind, entity_key)
    return _answer(unavailable("template_has_no_attempt",
                               template_id=template.id), kind, entity_key)


__all__ = ["ATTEMPT_FRAMES", "BOX_REASONS", "EXACT", "GRAB_EVENT",
           "GRAB_EVENT_TYPE", "KINDS", "LEVEL_BY_COURSE", "PICTURE_TIMER",
           "PROVIDERS", "REASONS", "GhostSource", "GhostSpan", "attempt_id_of",
           "from_marked_attempt", "place_of", "resolve_ghost", "span_of",
           "unavailable"]
