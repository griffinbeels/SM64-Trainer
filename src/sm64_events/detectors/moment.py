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

NOT GATED ON A TARGET, since 2026-08-06, and the reversal is the user's own.
Task 0087's rule was "these should ONLY be tracked when we explicitly select /
autoselect a star or segment", written before the recorder existed in the form
that consumes these — and the recorder is exactly the surface where no target
has been picked yet, because pointing at what you just did is HOW a definition
gets made. Measured on his journal the day it bit: 207 moments, every one of
them inside a target window, and then a whole session in WF, HMC and SSL with
ZERO of any kind — his HMC target had retired on the level change into WF and
every door and dialogue after it was correctly suppressed. Two reports, one
cause: *"I went into Whomp's Fortress, triggered the Whomp King dialogue, and
now nothing popped up in the segment recorder tool"* and *"briefly I was able
to detect the doors in HMC, but... I lost the ability to detect those"*.

A RECORDER-OPEN gate was considered and does not work, which is worth stating
so it is not re-proposed: he does the thing FIRST and opens the recorder
afterwards to point at it, so detection has to have already happened.

What the gate was really protecting is journal volume (the 2026-08-04 trim from
4.97 MB to 3.42 MB), and this vocabulary is deliberately coarse — a door and a
textbox, not a wall kick — so the cost is ~200 rows per two days of his play.
A moment per wall kick would still undo that trim; the ceiling is the MOMENTS
registry below, and it is the thing to guard rather than the target.

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
from sm64_events.core.landmark import EngagementWatch, landmark_at
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory import addresses as A


@dataclass(frozen=True)
class Moment:
    kind: str            # the wire + trigger vocabulary ("door_open", ...)
    actions: frozenset   # entering ANY of these IS this moment
    label: str           # the sentence the builder's picker shows
    # How far Usamune's SCREEN sits above `counter + IgtClock.DISPLAY_TICK` on
    # the frame this moment fires -- see MomentDetector.DISPLAY_LAG_FRAMES for
    # why it is per-kind and what each value was measured from. Defaulted to
    # the door's, so an unmeasured kind carries the one value that has a
    # screenshot behind it and says so.
    display_lag: int = 1
    # Whether this moment must see the engaged-object pointer RETARGET before
    # it will believe it. FALSE for every kind whose action IS operating the
    # object -- pulling a door, grabbing a pole, picking something up, climbing
    # into a cannon -- because there the pointer is authoritative whether or
    # not its VALUE moved, and demanding a change costs the second, third and
    # fourth opening of the SAME door their name. See
    # MomentDetector.ENGAGE_FRESH_FRAMES for the measurement that split this
    # out of a blanket rule.
    needs_fresh_engagement: bool = False


# THE registry. One row per moment kind; the sets are the ones addresses.py
# already keeps, so what counts as a door here and what counts as a door to
# the anchor detector can never drift apart.
MOMENTS: tuple[Moment, ...] = (
    Moment("door_open", A.DOOR_ACTIONS, "Open a door"),
    # A TEXTBOX READS THE RAW COUNTER -- his frame-stepped replay, 2026-08-10,
    # is the only kind of evidence this number has ever moved on. See
    # MomentDetector.DISPLAY_LAG_FRAMES.
    # ...and it is the ONE kind that must see the pointer retarget: reading
    # text is not operating an object, so the pointer may be holding something
    # from minutes ago (the star door, 2026-08-10).
    Moment("textbox", A.DIALOG_ACTIONS, "Trigger a textbox", display_lag=-1,
           needs_fresh_engagement=True),
    Moment("pole_grab", A.POLE_GRAB_ACTIONS, "Grab a pole"),
    Moment("pickup", A.PICKUP_ACTIONS, "Pick up an object"),
    # Round 9 item 6: *"We also need to detect when the user enters a cannon"*
    # / *"(cannon entry xcam)"*. One row and its action set, which is what the
    # registry is for. Its landmark resolves `bhvCannon` -> "cannon" out of the
    # shipped catalogue, so a row reads "Enter a cannon in Bob-omb Battlefield"
    # with nothing else added; the specific cannon is nameable like any other
    # placed object. The OFFSET his x-cam wants is the open half —
    # `tools/score_moment_clock.py` settles it from one screenshot, the same
    # way a door's did, and until then a cannon carries the door's constant.
    Moment("cannon_enter", A.CANNON_ACTIONS, "Enter a cannon"),
    # Round 11: kinds with an EMPTY action set are CAUSED moments — the
    # player made something happen and the OBJECT recorded it, so
    # detectors/caused.py supplies the events off the pool and this detector
    # never matches them (an empty set matches no byte). The rows live here
    # anyway because this tuple is THE kind registry: labels, the builder's
    # dropdown and the timeline all read it, and a kind absent here is a kind
    # no surface can say. Which behaviours may fire each kind, and the
    # measured rule per behaviour: addresses.CAUSED_BEHAVIOURS.
    Moment("switch_press", frozenset(), "Press a switch"),
    Moment("enemy_defeated", frozenset(), "Defeat an enemy"),
)


def needs_fresh_engagement(kind: str) -> bool:
    """Whether this kind may only name an object the pointer just retargeted
    to. See `Moment.needs_fresh_engagement`."""
    return any(m.kind == kind and m.needs_fresh_engagement for m in MOMENTS)


def display_lag_for(kind: str) -> int:
    """THE one reading of a kind's display lag — `caused.py` emits moments too
    and must not grow a second answer. Reads the registry live so a test can
    move a row and watch every consumer follow."""
    for moment in MOMENTS:
        if moment.kind == kind:
            return moment.display_lag
    return MomentDetector.DISPLAY_LAG_FRAMES


class MomentDetector:
    """Emits `moment_reached {kind, ordinal, level, area, action}`."""

    # A DOOR'S DISPLAY LAG: Usamune's screen reads one game frame HIGHER than
    # `counter + IgtClock.DISPLAY_TICK` on the frame Mario enters a door, so a
    # moment is `counter + 2`.
    #
    # THE EVIDENCE IS ONE SCREENSHOT HOLDING BOTH NUMBERS (2026-08-06), and
    # that sentence is the whole point of this comment. The emulator reads
    # 1'06"83; the recorder's top row, same frame, reads 1'06"80. Journal id
    # 2279 is that door -- raw `counter` 2003, `action_timer` 1 -- so Usamune
    # showed frame 2005 while we published 2004. Not an inference about where
    # an older measurement came from: two numbers, one frame, 3 centiseconds
    # apart, which at 30 fps is exactly one frame.
    #
    # THIS CONSTANT HAS MOVED BY +-1 IN THREE ROUNDS, which is why the
    # instrument matters more than the value. `tools/score_moment_clock.py`
    # scores any reading off his screen against the RAW COUNTER the row
    # carries -- never against what we published, which is the number under
    # suspicion and changes whenever this does -- so doors journaled either
    # side of a flip stay comparable and one screenshot settles the next
    # disagreement. Its first output is the pair above.
    #
    # WHY A DOOR DIFFERS FROM A PIPE, which is the reason this looked wrong
    # twice: `warp.py` was live-verified at `counter + DISPLAY_TICK` against
    # Usamune's screen (0'35"96, 2026-07-31, same clock call), and reasoning
    # from that to "a door must read the same" is what deleted this constant on
    # 2026-08-06. The doors say otherwise and they are the measurement. Which
    # of the two mechanisms in the payload comment below produces the extra
    # frame is still open; the offset is not.
    #
    # WHAT ROUND 6 GOT RIGHT, kept because it is still true: his 2026-08-05
    # report ("the practice log consistently shows about one frame faster than
    # the time in Usamune") was read off the PRACTICE LOG, which was not
    # showing a moment's number at all -- a definition armed by a reload's
    # `spawned` missed `_close`'s "the counter zeroed on the arm frame" test by
    # one and banked the `global_timer` DELTA. `segments.IGT_ARM_SKEW_FRAMES`
    # fixed that and stands. Both fixes were needed and only one of them
    # survived the round; a clean Lakitu run's delta is `counter + 1`, which is
    # exactly why one report could be satisfied by either and why the residual
    # came straight back.
    #
    # IT IS PER-KIND SINCE 2026-08-10, and this is the value only for kinds
    # with no screenshot of their own. A TEXTBOX reads the RAW COUNTER --
    # `display_lag = -1`, total `counter + 0` -- and the evidence is the best
    # this number has ever moved on, because it is FRAME-STEPPED rather than
    # caught live: his replay paused on the star door's dialogue shows Usamune
    # at 0'11"53 while the practice log recorded 0'11"60 (journal id 28790,
    # raw `counter` 346; 346/30 = 11.53, and we published 348). His words:
    # *"we marked down 11\"60... Need to make sure we're detecting when the
    # trigger occurs more tightly."* `tools/score_moment_clock.py --usamune
    # 0'11"53` attributed it to that row and returned `counter + 0`.
    #
    # A GLOBAL FLIP WOULD HAVE BROKEN THE DOOR, which is the reason the field
    # moved onto the registry rather than this constant changing again: the
    # door's own screenshot says +2 and stands untouched, so the two readings
    # are not in conflict and neither has to be re-litigated to move the other.
    # WHY they differ is OPEN and is deliberately not guessed at -- the obvious
    # story, that a dialogue stops Usamune's counter, was MEASURED AND IS
    # FALSE: across every textbox in his journal the counter advances 1:1 with
    # `global_timer` through the dialogue (25 samples, 0 frozen frames).
    #
    # Every other kind carries the door's value and says so here rather than
    # in a second place: pole_grab, pickup, cannon_enter, and caused.py's
    # switch_press/enemy_defeated. One screenshot each settles them, through
    # the same instrument, without touching the two that are already answered.
    #
    # The two inert payload fields below are what made this a measurement
    # rather than an argument -- keep them.
    DISPLAY_LAG_FRAMES = 1

    # A MOMENT NAMES ONLY WHAT IT ENGAGED, and the engaged-object pointer
    # LINGERS -- it holds the last thing Mario operated until something else
    # overwrites it, and a star door's dialog overwrites nothing. His report,
    # 2026-08-10: *"when I FIRST TRIGGER the 70 Star door... it actually gets
    # incorrectly marked as 'Trigger the WOODEN door'... If I rename it, it
    # also renames the ACTUAL wooden door, which is wrong."* Journal ids
    # 28313/28316/28317 are that walk: the wooden door at frame 133800, the
    # star door's textbox 453 frames later STILL carrying the wooden door's
    # key, and the star door itself finally reaching the pointer 47 frames
    # after that when it actually opens.
    #
    # So a TEXTBOX's landmark is taken only when the engagement RETARGETED
    # around the action edge; anything older degrades to no landmark, never a
    # foreign name. Same rule, same numbers and the same code as `warp.py`'s
    # painting fix (round 12 items 5+6) -- see `core/landmark.py`'s
    # EngagementWatch for the measurement behind both.
    #
    # IT APPLIES TO THE TEXTBOX ALONE, and the day it did not is the whole
    # reason `Moment.needs_fresh_engagement` exists. Shipped as a blanket rule
    # it cost every REPEAT opening of one door its name: he practices BLJs by
    # reloading in front of the Upstairs Door and opening it again and again,
    # the pointer already holds that door from the previous run, so nothing
    # retargets and the row degraded to "Open a door in Castle Inside" beside
    # "Open the Upstairs Door in Castle Inside" -- *"these two are the same
    # thing!"* Read straight back out of the `engaged_age_frames` this change
    # had just started journaling: **21 of 51 door rows refused**, and the
    # ages are BIMODAL -- 0 or -1 where the pointer retargeted at the edge,
    # 55..1160 where the same door was simply opened again.
    #
    # THE DISCRIMINATOR IS WHETHER THE MOMENT IS ITSELF AN OBJECT INTERACTION.
    # Pulling a door, grabbing a pole, picking something up, climbing into a
    # cannon: Mario's action IS operating that object, so the game writes the
    # pointer as part of the action and it is authoritative whether or not the
    # VALUE moved -- "unchanged" there means he did the same thing twice, not
    # that we are reading a leftover. Reading text is not an interaction, so a
    # textbox is the one kind with nothing of its own to point at. The
    # one-poll settle stays the fix for the lag in BOTH cases.
    #
    # A per-kind BEHAVIOUR allowlist ("a door_open must name a door") was the
    # other candidate and is deliberately not built: this project already
    # refused a static-kind allowlist once, for the reason that it is a list
    # that rots while looking healthy (core/landmark.py's docstring).
    ENGAGE_FRESH_FRAMES = 4
    ENGAGE_ADOPT_FRAMES = 6

    def __init__(self, target_active: Callable[[], bool] = lambda: True):
        # Kept as an INJECTION POINT, defaulting permissive: `build()` no
        # longer passes one (see the module docstring on the retired
        # task-0087 gate), and a future rule that wants to narrow WHEN a
        # moment records has a seam to do it through rather than a new branch.
        self._target_active = target_active
        self._counts: dict[str, int] = {}
        # A moment CARRIES USAMUNE'S OWN NUMBER, through the shared clock that
        # star_grab, key and warp already read -- see `_emit`.
        self._clock = IgtClock()
        # THE ONE-POLL LANDMARK SETTLE (round 9 item 4). The event is built at
        # the edge — frame, igt, ordinal, action are all the edge's — but its
        # LANDMARK is re-read from the NEXT poll before publishing, because
        # the engaged-object pointer can lag the action byte by a read: his
        # first WF tree grab (journal id 2794, 2 s after spawning) resolved to
        # bhvSpinAirborneWarp — Mario's own SPAWN MARKER, the previous thing
        # the pointer held — while every later grab in the session read the
        # tree. Invisible before the kind catalogue named things, since both
        # objects labelled "Grab a pole" identically. One poll later Mario is
        # still IN the grab (a 60 Hz poll of a 30 fps game cannot miss it), so
        # the re-read is unambiguous; the hold costs 16 ms nobody can see.
        self._pending: list[Event] = []
        # WHEN that pointer last retargeted -- the only thing that separates a
        # reading from a leftover. See ENGAGE_FRESH_FRAMES above.
        self._engaged = EngagementWatch()

    def reset(self) -> None:
        """A new attempt opened: ordinals count from here.

        Called by the service when an anchor is published. Nothing derived is
        journaled — the projector re-derives from the journal on replay, and a
        derived row written back would make replay non-idempotent.
        """
        self._counts.clear()

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        backward = curr.global_timer < prev.global_timer
        if not self._engaged.seen():
            # Baseline off `prev` so a retarget on the very first pair still
            # counts as one, while whatever the pointer already held when this
            # detector was built reads as stale (landmark.py's own rule).
            self._engaged.observe(prev)
        self._engaged.observe(curr)
        released = self._release(curr, backward=backward)
        if backward:
            # Savestate load / console reset: the ordinals we were counting
            # belong to a run that is no longer happening.
            self.reset()
            return released
        if not self._target_active():
            return released
        for moment in MOMENTS:
            if (curr.mario_action in moment.actions
                    and prev.mario_action not in moment.actions):
                self._pending.append(self._emit(moment.kind, curr))
        return released

    def _release(self, curr: GameSnapshot, backward: bool) -> list[Event]:
        """Publish last poll's moments, with the landmark settled from THIS
        poll — see `_pending` in __init__ for why the edge's own read can
        name the wrong object. The settled read must agree about WHERE (a
        backward jump or a level edge on the settle poll means `curr` is no
        longer the place the moment happened), else the edge's reading
        stands: a possibly-stale name beats a definitely-foreign one."""
        if not self._pending:
            return []
        released, self._pending = self._pending, []
        for event in released:
            # INERT EVIDENCE, the pattern star_grab and warp already use: how
            # old the engagement was when this moment happened. If the gate
            # ever strips a name off a row that deserved one, this number says
            # by how much and the window moves from measurement rather than
            # from argument. None = the pointer was never seen to retarget.
            changed_at = self._engaged.changed_at
            event.payload["engaged_age_frames"] = (
                None if changed_at is None else event.frame - changed_at)
            if backward:
                continue
            settled = landmark_at(curr)
            if settled is None:
                continue
            if (curr.curr_level != event.payload["level"]
                    or curr.curr_area != event.payload["area"]):
                continue
            # For a kind that demands it, the settle may only ADOPT a retarget
            # belonging to this moment — a pointer that has held the same
            # object since long before the edge is the leftover it refuses.
            if (needs_fresh_engagement(event.payload["kind"])
                    and not self._engaged.fresh_for(event.frame,
                                                    self.ENGAGE_FRESH_FRAMES,
                                                    self.ENGAGE_ADOPT_FRAMES)):
                continue
            event.payload["landmark"] = settled.payload()
        return released

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
        igt_frames = reading + display_lag_for(kind)
        # WHICH door, as opposed to how many doors ago. The ordinal above stays
        # -- it is a property of the moment now rather than its name, which is
        # his own sentence -- and `landmark` is what a label and a subsection key
        # on. None where Mario engaged nothing the pool could name.
        # For a kind that demands it, taken only when the engagement is FRESH
        # -- a lingering pointer names the last door he walked through, not the
        # thing he just did.
        found = landmark_at(curr)
        if (needs_fresh_engagement(kind)
                and not self._engaged.fresh_for(curr.global_timer,
                                                self.ENGAGE_FRESH_FRAMES)):
            found = None
        return Event(type="moment_reached", frame=curr.global_timer,
                     timestamp_utc=curr.wall_time_utc,
                     payload={"kind": kind, "ordinal": self._counts[kind],
                              "landmark": found.payload() if found else None,
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
