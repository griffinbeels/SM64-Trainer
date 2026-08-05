# src/sm64_events/detectors/moment.py
"""Mid-star moments: the vocabulary a SUBSECTION is built out of.

Every trigger this project owned before this module was a place-change or a
collection — level_enter, level_exit, area_enter, warp_entered,
entrance_touched, key_grabbed, star_grabbed, spawned, attempt_anchor,
reset_game. So between spawning into a course and grabbing the star the
journal was EMPTY, and a builder that works by pointing at what you just did
had nothing to point at. Mario's action byte already carries the answer and is
already in every snapshot; this module turns its edges into events.

THE REGISTRY is MOMENTS below. Adding a moment kind is ONE ROW — the trigger
type, the builder's dropdown, the timeline, synthesis and the matcher all
reach it through `segments.TRIGGERS["moment_reached"]`, whose match lambda
compares the payload's `kind` rather than naming any kind itself. That is the
user's requirement made structural: "we need this to be flexible so that we
allow for the invention and innovation of new sections as needed" (2026-08-05).

ENTRY EDGE, never level. An action byte reads the same for every frame of a
door animation, so a moment is the frame Mario ENTERED it: `prev` outside the
set, `curr` inside it. Same discipline as star_grab's action edge, and the
reason re-collection works there.

ORDINALS count occurrences since `reset()`, which the service ties to the
attempt opening. They exist for START triggers: waypoints already order
everything after the arm, but "the 5th door in Big Boo's Haunt" is a start and
a start has no arm to count from. A kind — not an action — owns the counter,
so pulling one door and pushing the next reads as door 1 and door 2.

TARGET-GATED, and read EVERY TICK rather than once at construction. The user's
rule in task 0087 ("these should ONLY be tracked when we explicitly select /
autoselect a star or segment") is also what keeps a vocabulary this fine from
multiplying journal volume — the journal was deliberately trimmed from 4.97 MB
to 3.42 MB on 2026-08-04 and a moment per wall kick would undo that.

WHY THERE IS NO `first_controllable` MOMENT, since it is the obvious one to
add and it is already covered. `detectors/spawn.py` emits `spawned` on the
edge out of the spawn actions (kind "spawn") and out of ACT_INTRO_CUTSCENE
(kind "intro"), and addresses.py calls that second one "the canonical
Lakitu-skip timing start", live-verified 2026-06-12. A moment for the same
frame would be a SECOND DOOR onto one value — the divergent-duplication class
this project holds `tests/test_single_source.py` against. The only frames it
would add are leaving idle or sleeping, and stopping being idle is not a
practice boundary anyone wants.
"""
from collections.abc import Callable
from dataclasses import dataclass

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory import addresses as A


@dataclass(frozen=True)
class Moment:
    kind: str            # the wire + trigger vocabulary ("door_open", ...)
    actions: frozenset   # entering ANY of these IS this moment
    label: str           # the sentence the builder's picker shows


# THE registry. One row per moment kind; the sets are the ones addresses.py
# already keeps, so what counts as a door here and what counts as a door to
# the anchor detector can never drift apart.
MOMENTS: tuple[Moment, ...] = (
    Moment("door_open", A.DOOR_ACTIONS, "Open a door"),
    Moment("textbox", A.DIALOG_ACTIONS, "Trigger a textbox"),
)


class MomentDetector:
    """Emits `moment_reached {kind, ordinal, level, area, action}`."""

    # A DOOR'S DISPLAY LAG. Usamune's screen reads one game frame HIGHER than
    # `IgtClock`'s counter path answers at a moment, so the number we banked
    # was always a frame cheap -- his report, 2026-08-05: "the practice log
    # consistently shows about one frame faster than the time in Usamune,
    # every time (the practice log is wrong and should be slower)".
    #
    # MEASURED, 16 samples, ZERO variance: three screenshot pairs he read off
    # the emulator (6"70/6"66, 6"50/6"46, 6"63/6"60) and then thirteen
    # consecutive Lakitu runs, every one exactly one frame apart. Nothing here
    # is inferred from a mechanism -- the OFFSET is what was measured, and
    # that is what is encoded, the same standing this project gives
    # `IgtClock.DISPLAY_TICK` itself.
    #
    # WHAT THE JOURNAL SAYS ABOUT THE MECHANISM, recorded because it is a real
    # observation and not because it explains the sign: `action_timer` reads
    # **1 on all 13**, so our poll never catches a door on its entry frame --
    # it sees it on the next one. Three readings were checked and none of them
    # accounts for the direction: the same clock call matched Usamune exactly
    # at a PIPE (0'35"96, live 2026-07-31), a torn read between `mario_action`
    # and `igt_overall` would put us one frame AHEAD, and action-timer
    # backdating moves the frame EARLIER. So the mechanism is open and the
    # measurement is not; do not "simplify" this away without re-measuring
    # against the emulator, which is what the two inert payload fields below
    # are kept for.
    #
    # A PIPE AND A STAR ARE DELIBERATELY UNTOUCHED. Both were calibrated
    # against a display Usamune had STOPPED, and both match; a moment is read
    # off a display that is still running, which is the one difference between
    # the calibrations and may well be the whole of it.
    DISPLAY_LAG_FRAMES = 1

    def __init__(self, target_active: Callable[[], bool] = lambda: True):
        self._target_active = target_active
        self._counts: dict[str, int] = {}
        # A moment CARRIES USAMUNE'S OWN NUMBER, through the shared clock that
        # star_grab, key and warp already read -- see `_emit`.
        self._clock = IgtClock()

    def reset(self) -> None:
        """A new attempt opened: ordinals count from here.

        Called by the service when an anchor is published. Nothing derived is
        journaled — the projector re-derives from the journal on replay, and a
        derived row written back would make replay non-idempotent.
        """
        self._counts.clear()

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if curr.global_timer < prev.global_timer:
            # Savestate load / console reset: the ordinals we were counting
            # belong to a run that is no longer happening.
            self.reset()
            return []
        if not self._target_active():
            return []
        events = []
        for moment in MOMENTS:
            if (curr.mario_action in moment.actions
                    and prev.mario_action not in moment.actions):
                events.append(self._emit(moment.kind, curr))
        return events

    def _emit(self, kind: str, curr: GameSnapshot) -> Event:
        """One moment, stamped with Usamune's own number at that frame.

        WHY IT CARRIES A TIME AT ALL. A segment closing on a moment used to be
        timed by `close.frame - arm.start_frame`, the `global_timer` delta --
        which is wrong for two independent reasons neither of which is a
        constant (the arm frame is where a 60 Hz poll caught a 30 Hz counter
        drop, and the delta counts paused frames), and is not the number on
        screen. Live report 2026-08-05: Usamune read **0'07"76** as he opened
        the castle door, and his expectation is exactly that — *"I would
        expect the timer to stop on door entry and the practice log entry to
        display for the DOOR timing"*.

        The touch frame IS the observed edge frame, for the same reason
        `warp.py` states: a moment is the frame Mario ENTERED the action, so
        there is no action-timer backdating to be had and the event's own
        `frame` and the frame the reading is taken at are one number.

        `igt` rides along pre-formatted, matching every other IGT-bearing
        event's payload, so a consumer never re-derives the display form.
        """
        self._counts[kind] = self._counts.get(kind, 0) + 1
        reading, source = self._clock.igt_at(curr.global_timer, curr)
        igt_frames = reading + self.DISPLAY_LAG_FRAMES
        return Event(type="moment_reached", frame=curr.global_timer,
                     timestamp_utc=curr.wall_time_utc,
                     payload={"kind": kind, "ordinal": self._counts[kind],
                              "level": curr.curr_level, "area": curr.curr_area,
                              "action": curr.mario_action,
                              "igt_frames": igt_frames, "igt_source": source,
                              "igt": format_igt(igt_frames),
                              # INERT EVIDENCE for the one-frame question
                              # (live report 2026-08-05: Usamune 6"70 where we
                              # banked 6"66; 6"50/6"46; 6"63/6"60 -- three
                              # samples, every one EXACTLY one game frame and
                              # no variance at all).
                              #
                              # Two hypotheses, and these two fields separate
                              # them from the JOURNAL after a single door, so
                              # no probe has to exist:
                              #   (A) our poll caught the action a frame late
                              #       -- then `action_timer` reads >= 1 here,
                              #       and the fix back-dates, which moves the
                              #       number the WRONG way;
                              #   (B) he reads the frame the door visibly
                              #       moves, one after the byte flips -- then
                              #       `action_timer` reads 0 and `counter` + 2
                              #       is what is on his screen.
                              # Reading the code cannot separate them: this
                              # same clock call was live-calibrated at a PIPE
                              # and matched exactly, but a pipe stops the
                              # clock and a door does not.
                              "action_timer": curr.mario_action_timer,
                              "counter": curr.igt_overall})
