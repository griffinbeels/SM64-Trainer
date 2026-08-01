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
LEAVE Mario" — the DESTINATION of a level_exit. 52 of the 53 seeded exits omit
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


def practicable_here(stage: dict | None, node: str | None,
                     running: bool = False) -> bool:
    """May something that lives at `node` be made the target right now?

    TWO doors, and the second one is not a softening of the first — it is the
    correction to a premise the first one got wrong (user ruling 2026-08-01).

    `running` means this entity's own start trigger has ALREADY FIRED and it
    is under way. For a segment triggered by a TRANSITION, the place rule can
    never accept the pick, because the trigger is leaving the course and by
    the time it has fired the player is somewhere else by definition: standing
    in the Castle Lobby having just exited Whomp's Fortress, "WF -> SSL" was
    offered by the banner and refused on click with "that one is in Whomp's
    Fortress". His words: *"I am not presently IN whomps, but rather I am
    coming *from* whomps. Instead of it being bound on 'you can't select
    something for a stage you're not in' it's rather 'you can't select
    something where you didn't satisfy the trigger condition'."*

    So the origin is how you ask BEFORE the trigger fires, and satisfying the
    trigger is how you ask after. Only a SEGMENT can be running — a star is a
    single atomic grab with no trigger to have satisfied — which is a rule 11
    asymmetry stated rather than a gap: `running` is simply always False on
    the star side, and the caller is the one place that knows.

    Two unknowns both mean yes, because refusing on an unknown would strand
    the user with no way to pick anything:
      * `node` None — the definition names no place at all (a `reset_game`
        start, an unscoped key grab). "Anywhere" is a real answer, not a gap.
      * the player's place unknown — no stage at all, or a stage naming no
        level (the service's boot default, before the emulator is attached).
        Nothing to compare against, so nothing to refuse; this is what keeps
        the target settable while reviewing with the game closed.

    A stage whose `mode` is None is NOT one of those unknowns, and used to be
    (fixed 2026-07-27, second round of the same ruling): the file select, the
    castle grounds, the courtyard and the cap courses all resolve to mode
    None, so that clause permitted every pick from exactly the places you
    cannot practice anything in — including setting a Whomp's Fortress target
    while standing on the grounds, the move this rule exists to stop. A level
    names a place whether or not the quick-select banner has a row for it.
    """
    if running:
        return True
    here = stage_origin(stage.get("level"), stage.get("area")) if stage else None
    if node is None or here is None:
        return True
    return here == node
