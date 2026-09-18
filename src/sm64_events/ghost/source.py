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
"""
from dataclasses import dataclass
from typing import Any, Callable

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
                "exactness": self.exactness}


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
    if replay.find_saved(attempt_id) is None:
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
    return GhostSource(available=True, media_url=view["clip_url"], view=view,
                       exactness=EXACT, **known)


Provider = Callable[..., GhostSource | None]
PROVIDERS: tuple[Provider, ...] = (from_marked_attempt,)


def resolve_ghost(kind: str, entity_key: str, strat: str | None = None, *,
                  templates, replay,
                  providers: tuple[Provider, ...] = PROVIDERS) -> GhostSource:
    """Is there a ghost for this star or segment, and where is its footage?

    `templates` is the template store and `replay` the replay service; both
    are read-only here. The strategy falls back to the strategy-less template
    through the store's own rule, so a template recorded before he named a
    strategy still answers.
    """
    if kind not in KINDS:
        raise ValueError("a ghost is for a star or a segment")
    template = templates.active_for(kind, entity_key, strat)
    if template is None:
        return unavailable("no_template")
    for provider in providers:
        answer = provider(template, replay=replay)
        if answer is not None:
            return answer
    return unavailable("template_has_no_attempt", template_id=template.id)


__all__ = ["EXACT", "KINDS", "PROVIDERS", "REASONS", "GhostSource",
           "attempt_id_of", "from_marked_attempt", "resolve_ghost",
           "unavailable"]
