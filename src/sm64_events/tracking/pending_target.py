# src/sm64_events/tracking/pending_target.py
"""Pending practice target — an intent held until the player follows through.

Picking something you cannot practice where you stand ("I'm in Lethal Lava
Land, the star I want is in Shifting Sand Land") used to move the target
immediately: the practice card then described a place the player wasn't, while
the quick-select row kept offering the course they were actually in. The pick
is instead held as an INTENT and resolved by where the player goes NEXT (user,
2026-07-26 — "my next action in game should be moving to SSL… if it is not, we
should assume I changed my mind"):

  * they enter the entity's OWN stage        -> COMMIT the target + its strategy
  * they enter a DIFFERENT practicable stage -> DROP it; normal detection carries
                                                on with the target untouched
  * anywhere else                            -> HOLD

The third branch is what makes the feature possible rather than theoretical:
every course is entered THROUGH Castle Inside, so counting the castle as a
change of mind would drop every intent one room before it could be honoured.
Cap stages, secret-star areas and the hub are transit for the same reason.

Pure decision logic — no service, db or event access — so the three outcomes
are testable without a game. The intent itself is broadcast-only live state and
is NEVER journaled: it has no historical value, and a replay must not
resurrect one (the same discipline stage_changed already follows).
"""

# Entering one of these means the player COMMITTED to practicing here: each
# offers one-click targets of its own (detectors/stage.py's `mode`). "castle"
# is deliberately absent — it is the hub, not a destination, even though it
# does offer castle-movement segments. A pending CASTLE segment still commits
# there, via belongs_to_stage below, so nothing is lost by leaving it out.
COMMITMENT_MODES = frozenset({"stars", "bowser_course", "arena"})

COMMIT, DROP, HOLD = "commit", "drop", "hold"


def belongs_to_stage(stage: dict | None, kind: str, course_id: int | None,
                     start_levels: list, start_areas: list) -> bool:
    """Is this entity practiced in the stage the player is standing in?

    Stars belong to a course, which `stage_changed` names directly (main
    courses and the three Bowser courses both carry course_id). Segments have
    no course: they belong wherever their start triggers can arm, which is the
    same `start_levels` / `start_areas` pair the quick-select banner filters
    by (tracking/views.py). Levels are checked as well as [level, area] pairs
    because the Bowser rows offer segments by level alone.
    """
    if not stage:
        return False
    if kind == "segment":
        level, area = stage.get("level"), stage.get("area")
        if level is None:
            return False
        return level in start_levels or [level, area] in start_areas
    return course_id is not None and stage.get("course_id") == course_id


def resolve(stage: dict | None, kind: str, course_id: int | None,
            start_levels: list, start_areas: list) -> str:
    """COMMIT / DROP / HOLD for a pending target, given the stage just entered."""
    if belongs_to_stage(stage, kind, course_id, start_levels, start_areas):
        return COMMIT
    if (stage or {}).get("mode") in COMMITMENT_MODES:
        return DROP
    return HOLD
