# src/sm64_events/tracking/practicable.py
"""Where a practice target may be SET, and where it stops being the target.

Replaced tracking/pending_target.py on 2026-07-27. That module held an
out-of-stage pick as an INTENT and committed it when the player walked in;
the user's ruling is that the pick should not be possible at all — "setting a
target like this doesn't make any sense because it's logically inconsistent
with how you would actually practice the game. You should be restricted to the
segments / stars in the area you're actually in (you have to just warp to the
new area to see the new segments / stars)."

ONE question, one door, both kinds: an entity lives at a world NODE, the
player stands at a world NODE, and the two are compared. Stars resolve through
`segments.star_origin`, segments through `segments.start_origin` — the same
vocabulary, which is what makes rule 11 parity structural here rather than
duplicated. The callers hand over a resolved node, never the raw definition:
there is exactly one place a definition becomes a node, and a caller that
assembled its own could assemble it differently (this is the mistake that made
the OLD readers wrong — see below).

WHY THE OLD READERS WERE WRONG, since it is the whole bug: `start_levels` /
`start_areas` derive from `arm_level`, which answers "where does this trigger
LEAVE Mario" — the DESTINATION of a level_exit. 50 of the 51 seeded exits omit
`to`, so those answer None and **54 of the 65 seeded definitions resolved to no
place at all**. Every consumer inherited that blind spot at once: the
quick-select banner never offered a castle movement, `belongs_to_stage` said
False everywhere so a picked movement was held forever and then dropped, and
the projector never retired a movement target — which is the live report of
2026-07-27, "ACTIVE SEGMENT  WF -> SSL" while standing in Cool, Cool Mountain.
`start_origin` answers where a definition STARTS FROM and places 65 of 65.

Pure decision logic — no service, db or event access.
"""

from sm64_events.tracking.segments import stage_origin


def practicable_here(stage: dict | None, node: str | None) -> bool:
    """May something that lives at `node` be made the target right now?

    Two unknowns both mean yes, because refusing on an unknown would strand
    the user with no way to pick anything:
      * `node` None — the definition names no place at all (a `reset_game`
        start, an unscoped key grab). "Anywhere" is a real answer, not a gap.
      * no live stage — the emulator is not attached, or the player is on the
        title screen. Nothing to compare against, so nothing to refuse; this
        is also what keeps the target settable while reviewing with the game
        closed.
    """
    if node is None or not stage or stage.get("mode") is None:
        return True
    return stage_origin(stage.get("level"), stage.get("area")) == node
