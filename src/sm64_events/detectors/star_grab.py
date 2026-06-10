"""Emits star_collected on the edge into a star-grab action.

Why this works for re-collections: the game's interaction handler updates
gLastCompletedCourseNum/StarNum and Mario's numStars BEFORE setting the
star-dance action, on every grab. So at the edge, identity is already
current, and an unchanged numStars means the star was already owned.
"""
from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.memory.addresses import STAR_GRAB_ACTIONS, course_name, star_name


class StarGrabDetector:
    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered:
            return []
        star_id = curr.last_completed_star - 1  # game is 1-based, API 0-based
        if star_id < 0:
            return []
        # course 0 is valid here: castle secret stars (Toad/MIPS) report
        # course 0. The boot-time "never set" state has star == 0 too, so
        # the star_id guard above already excludes it.
        course_id = curr.last_completed_course
        return [Event(
            type="star_collected",
            frame=max(0, curr.global_timer - curr.mario_action_timer),
            timestamp_utc=curr.wall_time_utc,
            payload={
                "course_id": course_id,
                "course_name": course_name(course_id),
                "star_id": star_id,
                "star_name": star_name(course_id, star_id),
                "already_collected": curr.num_stars == prev.num_stars,
            },
        )]
