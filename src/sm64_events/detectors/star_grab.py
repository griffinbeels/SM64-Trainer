# src/sm64_events/detectors/star_grab.py
"""Emits star_collected at the X-CAM moment — the leaderboard-legal time.

Why this works for re-collections: the game's interaction handler updates
gLastCompletedCourseNum/StarNum and Mario's numStars BEFORE setting the
star-dance action, on every grab. So at the grab edge, identity is already
current, and an unchanged numStars means the star was already owned.

## Why the emit is not on the grab edge

Usamune's `STOP` decides when Usamune's timer stops, and the manual defines
the choices: *Grab* = Mario touches the star, *Xcam* = Mario touches the
GROUND after the grab, *GrabX* = stop at the grab, then update at the x-cam.
Leaderboards accept `STOP` of GrabX or Xcam only, so the grab-frame number is
not a slightly-early time — it is an INVALID one, and it was our default.

Measured 2026-08-01 (`tools/derive_xcam.py`, scored automatically against
Usamune's own settled result store): **the x-cam moment is the star-DANCE
entry**, and Usamune's number is that frame's overall counter plus
`IgtClock.DISPLAY_TICK`. The arithmetic was never wrong; the FRAME was. Four
midair grabs came back within one frame of Usamune at the dance entry, against
-4, -11, -23 and -39 frames at the grab.

A GROUND grab enters the dance on the grab frame; a MIDAIR grab passes through
`ACT_FALL_AFTER_STAR_GRAB` first, so this detector holds the grab and marks the
x-cam when Mario lands — 0.1 s to 1.3 s later in his own play.

## Why the number is not simply the counter at the x-cam

**`USAMUNE_OVERALL` is subarea-local**: it restarts at an area warp inside a
level, so on a multi-area star our counter measures the time since entering
the subarea. Live 2026-08-01, his own gate run: nine single-area stars matched
Usamune exactly and the two subarea stars were 356 and 502 frames low — LLL
"Hot-Foot-It into the Volcano" 0'40"63 against 0'52"46, SSL "Inside the
Ancient Pyramid" 0'02"43 against 0'19"13. Usamune's result store is the only
thing that knows the whole star. So the x-cam says WHICH MOMENT and Usamune's
own write says WHAT NUMBER.

A second subarea run the same day (five grabs, `tools/derive_xcam.py`) showed
how that write actually arrives: **Usamune never writes the answer once.**
Every grab took 2-3 writes — a first one within 9 frames of the dance entry
echoing our own counter, then, on a subarea star only, the whole-star
correction as late as 41 frames after it; and the store is later CLEARED on
level exit (+92 = 0). The evidence sits on `RESULT_SETTLE_BRACKET` and is
pinned by `tests/test_star_grab.py`.

## Why the emit does NOT wait for all of that (2026-08-01, live report)

It used to. `star_collected` left at x-cam + `RESULT_SETTLE_FRAMES`, so the
practice log took a second and a half to acknowledge a grab that had already
happened — after a backflip's fall, on top of it: "now the tool feels like
it's broken and laggy… we HAVE THE ANSWER RIGHT WHEN THE STAR DANCE HAPPENS".
He is right that we do, in every case but one, and the one is knowable after
the fact rather than before it. So the emit is an OPTIMISTIC UPDATE:

* **Publish** the moment Usamune's own written answer AGREES with our
  derivation of the same x-cam (`_ready_to_publish`) — typically 1-3 frames,
  and 0 on a ground grab, where Usamune has already written by the time Mario
  is dancing. Agreement is the point: when both sources say the same number
  there is nothing left to wait for. Failing that, `PUBLISH_WAIT_FRAMES` (12,
  ~400 ms) is the backstop, and it publishes Usamune's value regardless — the
  cost of a disagreement is latency, never a wrong row. UNLESS the clock says
  its own zero point may be an area load
  (`IgtClock.counter_may_be_subarea_local`), in which case our number is the
  time since that load, agreement would be meaningless, and the grab waits the
  full `RESULT_SETTLE_FRAMES` to arrive right the first time.
* **Keep watching** to x-cam + `RESULT_SETTLE_FRAMES` regardless. If Usamune's
  answer CHANGES after we published, emit `star_time_corrected` carrying the
  final number and the recorded row is revised in place
  (`tracking/projection.py::time_corrections` folds it back into this event's
  own payload, so every consumer of the grab sees one number and never learns
  a correction happened).

Note what the fast door is NOT: "publish as soon as a write lands". That was
the shape for one afternoon and it flickered, because Usamune writes more than
once even on an ordinary star (+1=328 then +3=330 on WF "Shoot into the Wild
Blue") and the first of that burst is the GRAB-time value. Publishing it meant
correcting to the second a second later, on screen: "it writes the entry into
the system… and then the xcam happens, which overrides the original entry… it
should be hidden to the user" (2026-08-01). Agreement tells the two apart
where mere existence could not — 328 is two frames from our derivation and 330
is on it — so the row leaves at the same moment his own HUD stops changing,
which is the thing he actually asked for: "we should be able to time it
perfectly."

Every emitted grab carries `published_after` and the `result_writes` burst it
saw, journaled and read by nothing, because the remaining latency is an
argument about that burst and ordinary play can settle it.

So the correction is now a BACKSTOP rather than a routine event: it fires only
when a write lands after the publish window and says something new, which of
the measured shapes only a subarea star's whole-star write does — and the
subarea branch above is what stops even that one from being seen.

Where no write comes at all — `STOP` of Grab or None, both already illegal —
the counter derivation stands in, `igt_source` is `"counter"` rather than
`"result"`, and the publish deadline is what ends the wait. That case keeps
the subarea error; it cannot be fixed from a counter that restarted.

## Why this detector runs FIRST in the chain

Both the landing wait and the publish wait mean the event describes a frame
already past, and a reset or a
level change ENDS them early — so it routinely lands on the very tick another
detector closes or opens an attempt. `main.build_detectors` therefore
publishes this one before every closer: the held grab takes the attempt it
belongs to, and the reset that broke its wait finds nothing open, which is
already how a reset after a settled grab behaves. Without that ordering one
grab records twice — a reset row AND a success row (live report 2026-08-01,
his ruling: "we should NOT ever count a reset once we've triggered the star
grab"). That docstring carries the reasoning and why a sort by frame is not
the same thing; `tests/test_reset_during_star_grab.py` holds the door.

The grab-moment fallback exists because a grab that never reaches a dance must
still be journaled: `igt_timed_at` says which of the two moments the number
came from, so a row can always explain what it is rather than being silently
either.

IGT comes from the shared IgtClock (detectors/igt_clock.py) — result ->
counter -> reconstructed precedence; its docstring carries the rationale.
The same clock stamps the Bowser-3 grand star in detectors/key.py, at the
TOUCH: whether Usamune's STOP setting moves the grand star's number the way it
moves a collectable star's is unmeasured, and `ACT_JUMBO_STAR_CUTSCENE` has no
fall/dance pair to derive one from. Left alone deliberately rather than
changed by analogy.
"""
from dataclasses import dataclass

from sm64_events.core.events import Event
from sm64_events.core.snapshot import GameSnapshot
from sm64_events.core.timefmt import format_igt
from sm64_events.detectors.igt_clock import IgtClock
from sm64_events.memory.addresses import (KEY_GRAB_LEVELS, STAR_DANCE_ACTIONS,
                                          STAR_GRAB_ACTIONS, course_name,
                                          star_name)


@dataclass
class _PendingGrab:
    """A midair grab, identified, waiting for Mario to land.

    Everything the event needs is captured at the GRAB edge, including the
    grab-moment IGT: the identity fields are only current for a tick, and the
    fallback number has to be the one we would have emitted before deferring,
    not a back-computation over a window that may contain a pause."""
    course_id: int
    star_id: int
    already_collected: bool
    num_stars: int
    level: int
    grab_frame: int
    grab_igt: int
    grab_igt_source: str
    # Filled in when Mario lands. The reading is taken THERE and kept, so the
    # settle wait that follows can never back-compute the counter across it.
    xcam_frame: int | None = None
    xcam_igt: int = 0
    xcam_igt_source: str = ""
    # Captured with the x-cam reading, not asked for again later: whether our
    # own counter can be trusted is a fact about the run this grab belongs to,
    # and by the time the emit decides, a new run may have started.
    xcam_counter_is_partial: bool = False
    # (igt_frames, igt_source) already sent to the world, or None while the
    # grab is still unpublished. Its presence is what turns this record from
    # "an event nobody has seen yet" into "a row that may still be corrected".
    published: tuple[int, str] | None = None
    # WHICH MOMENT the published row describes. "xcam" for every ordinary
    # grab; "grab" only while the backstop has published a row whose x-cam has
    # not happened yet — the one state in which a correction changes a row's
    # LEGALITY and not just its number.
    timed_at: str = "xcam"
    # Our own whole-star answer for a subarea star, when we had the base to
    # build one. Journaled beside the published number so a session's play
    # SCORES the carry against Usamune instead of arguing about it: if the two
    # always agree, the wait can go entirely and the row can publish at the
    # x-cam like any other star.
    carried_igt: int | None = None
    # Observational, journaled with the event and read by nothing: how many
    # frames past the x-cam the row actually left, and every result-store
    # write it saw, as (offset, value). This file's whole remaining latency is
    # an argument about that burst, and an argument is what a measurement
    # replaces — every ordinary grab now carries its own evidence.
    published_after: int = 0
    writes: list[tuple[int, int]] | None = None


class StarGrabDetector:
    # Generous: the longest fall measured live was 39 frames (a Whomp's
    # caged-island grab), but a fall is as long as the drop under it. This is
    # only a backstop for a grab that never reaches a dance at all — a
    # savestate load and a level change both cut the wait short on their own.
    XCAM_TIMEOUT_FRAMES = 300
    # How long to let Usamune's own result write settle after the x-cam. This
    # number is BRACKETED at both ends by live measurement, and both ends bite
    # — see RESULT_SETTLE_BRACKET below for the floor, the ceiling, and the
    # test that pins them. Under `STOP` of Grab or None no write ever comes and
    # this wait is paid in full; both are settings a leaderboard already
    # rejects.
    RESULT_SETTLE_FRAMES = 45
    # How long the EMIT waits — as opposed to how long the correction watch
    # above runs. Usamune's writes after the x-cam landed at +1, +1, +3, +6,
    # +8 and +9 frames across the measured grabs, so 12 covers every one of
    # them with three frames of margin and still publishes inside 400 ms,
    # which is where a person stops reading a response as instant.
    #
    # It is a fixed deadline and NOT "publish the moment a write lands", which
    # is what it was for one afternoon: Usamune writes MORE THAN ONCE even on
    # an ordinary star (WF "Shoot into the Wild Blue": +1=328, then +3=330),
    # so leaving on the first one published a number that a correction then
    # moved. That flicker is the thing he objected to — "it writes the entry
    # into the system, and then the xcam happens, which overrides the original
    # entry… it should be hidden to the user" (2026-08-01). Waiting the whole
    # 12 frames takes the LAST write of that burst, so an ordinary star is
    # published once and never corrected.
    PUBLISH_WAIT_FRAMES = 12
    # How far Usamune's own written answer may sit from our derivation of the
    # same moment and still count as AGREEMENT — the fast door out of the wait
    # above (_ready_to_publish). One frame, because the counter path measured
    # 1-2 frames UNDER Usamune, so two would match the grab-time write.
    AGREEMENT_FRAMES = 1
    # (floor, ceiling), exclusive of neither end by accident:
    #
    # FLOOR 28 — Usamune never writes the answer once. Live 2026-08-01, his
    # subarea run, five grabs, 2-3 writes each. The first writes are ECHOES of
    # our own counter (value = that sample's counter + 1) at the grab and again
    # at the dance entry; on a SUBAREA star a further write lands 26-28 frames
    # after the dance entry carrying the whole-star time, and only that one is
    # the star's number. SSL Pyramid +6=74 then +32=545; THI Tip Top +9=1362
    # then +35=1618; LLL Elevator Tour +1=465, +14=479, +41=777; CCM Slip
    # Slidin' +8=221 then +33=1524. Leaving on the first write would have
    # journaled the subarea-local number — the exact bug this wait exists for.
    #
    # CEILING 90 — the store does not merely settle, it is CLEARED. Same run,
    # WF "Shoot into the Wild Blue": +1=328, +3=330, then +92=0 as he left the
    # course. A window generous enough to catch that zero would journal
    # 0'00"00. So this is not a value to raise "for safety"; there is a wrong
    # answer waiting on the far side of it.
    #
    # A single-area star is corrected by nobody, and at the moment the echo
    # lands there is no way to tell "no correction is coming" from "not yet" —
    # which is why the WATCH is unconditional rather than subarea-only. Since
    # 2026-08-01 the watch is all it is: the emit no longer sits inside it
    # (PUBLISH_WAIT_FRAMES below), so what a widened window would cost is a
    # wrong CORRECTION rather than a wrong row, and the floor still has to be
    # cleared or the correction is the one thing it exists to catch.
    RESULT_SETTLE_BRACKET = (28, 90)
    # A Usamune reset while a grab is pending destroys the context the number
    # would describe, and the counter falling is how it shows. Distinguished
    # from noise by SIZE, not by direction: a snapshot is twelve separate reads
    # and can come back one frame stale (verify_death_clock.READ_SKEW_FRAMES),
    # while a real reset drops the counter by the whole attempt. A false abort
    # here would emit the grab time — the exact bug this file fixes — so the
    # threshold sits far above the skew and far below any real reset.
    IGT_RESET_DROP_FRAMES = 15

    def __init__(self):
        self._clock = IgtClock()
        self._pending: _PendingGrab | None = None

    def process(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        if self._clock.empty():
            self._clock.observe(prev)
        events = self._detect(prev, curr)
        self._clock.observe(curr)
        return events

    def _detect(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        events: list[Event] = []
        if self._pending is not None:
            events.extend(self._settle(prev, curr))
        entered = (curr.mario_action in STAR_GRAB_ACTIONS
                   and prev.mario_action not in STAR_GRAB_ACTIONS)
        if not entered:
            return events
        grab = self._identify(prev, curr)
        if grab is None:
            return events
        if self._pending is not None:
            # A second grab while the first is still in flight. Cannot happen
            # in a star dance long enough to cover the correction watch, but
            # the alternative to closing the first one is dropping it
            # silently — and ending the watch here is what keeps a correction
            # unambiguous: at most one grab is ever correctable, so a
            # `star_time_corrected` always belongs to the star_collected
            # before it (tracking/projection.py::time_corrections pairs them
            # that way, and re-checks identity rather than trusting this).
            events.extend(self._close_now(self._pending, curr))
        if curr.mario_action in STAR_DANCE_ACTIONS:
            self._mark_xcam(grab, grab.grab_frame, curr)  # ground: x-cam is now
        self._pending = grab
        return events

    def _close_now(self, grab: _PendingGrab, curr: GameSnapshot) -> list[Event]:
        """End this grab's life now, with the best reading already taken — the
        x-cam one if Mario has landed, the grab one if he has not. Nothing to
        emit if it was already published: the row exists, and all that ends
        here is its chance of being corrected."""
        self._pending = None
        if grab.published is not None:
            return []
        if grab.xcam_frame is None:
            return [self._emit(grab, curr, grab.grab_frame, "grab",
                               grab.grab_igt, grab.grab_igt_source)]
        return [self._emit(grab, curr, grab.xcam_frame, "xcam",
                           grab.xcam_igt, grab.xcam_igt_source)]

    def _mark_xcam(self, grab: _PendingGrab, frame: int,
                   curr: GameSnapshot) -> None:
        grab.xcam_frame = frame
        grab.xcam_igt, grab.xcam_igt_source = self._clock.igt_at_xcam(frame, curr)
        grab.xcam_counter_is_partial = self._clock.counter_may_be_subarea_local()
        carried = self._clock.carried_igt_at_xcam(frame, curr)
        if grab.xcam_counter_is_partial and carried is not None:
            # We can answer a subarea star ourselves after all: the counter is
            # subarea-local, but we kept what it had counted before the warp,
            # so the sum is the whole star (IgtClock.carried_igt_at_xcam).
            # That turns the settle WAIT back into the ordinary agreement
            # door — Usamune's early echo of the subarea-local number
            # disagrees with this and is waited through, and the whole-star
            # write agrees and publishes. The DEADLINE deliberately stays the
            # long one (`_ready_to_publish`): a carry is a prediction until a
            # session of play has scored it, so if nothing ever agrees with
            # it, this ends exactly where it ends today — on Usamune's own
            # number, 45 frames out. A wrong carry costs latency, never a row.
            grab.carried_igt = carried[0]
            grab.xcam_igt, grab.xcam_igt_source = carried

    def _identify(self, prev: GameSnapshot,
                  curr: GameSnapshot) -> _PendingGrab | None:
        if curr.curr_level in KEY_GRAB_LEVELS:
            return None  # Bowser key, not a star — detectors/key.py owns it
        star_id = curr.last_completed_star - 1  # game is 1-based, API 0-based
        if star_id < 0:
            return None
        grab_frame = max(0, curr.global_timer - curr.mario_action_timer)
        igt_frames, source = self._clock.igt_at_xcam(grab_frame, curr)
        # course 0 is valid here: castle secret stars (Toad/MIPS) report
        # course 0. The boot-time "never set" state has star == 0 too, so
        # the star_id guard above already excludes it.
        return _PendingGrab(
            course_id=curr.last_completed_course, star_id=star_id,
            already_collected=curr.num_stars == prev.num_stars,
            num_stars=curr.num_stars, level=curr.curr_level,
            grab_frame=grab_frame, grab_igt=igt_frames,
            grab_igt_source=source,
        )

    def _settle(self, prev: GameSnapshot, curr: GameSnapshot) -> list[Event]:
        """Three waits, in order, and each one can end early and badly: for
        Mario to LAND (which moment), for Usamune's first write (publish the
        row), then for a LATER write to change its mind (correct the row)."""
        grab = self._pending
        broken = (curr.global_timer < prev.global_timer        # savestate load
                  or curr.curr_level != grab.level             # left the level
                  or prev.igt_overall - curr.igt_overall
                  > self.IGT_RESET_DROP_FRAMES)                # Usamune reset
        if grab.xcam_frame is None:
            if curr.mario_action in STAR_DANCE_ACTIONS:
                # Back-computed to the dance's ENTRY frame, so a poll that
                # arrives late still lands on the right one — but never before
                # the grab it belongs to, which a stale action timer could
                # otherwise claim.
                self._mark_xcam(grab, max(grab.grab_frame,
                                          curr.global_timer
                                          - curr.mario_action_timer), curr)
            elif broken:
                # A reset or a level change destroys the context an x-cam
                # would describe: none is coming, so the grab moment is the
                # final answer (or, if the backstop already published it,
                # there is nothing left to say).
                self._pending = None
                if grab.published is not None:
                    return []
                return [self._emit(grab, curr, grab.grab_frame, "grab",
                                   grab.grab_igt, grab.grab_igt_source)]
            elif grab.published is not None:
                return []   # backstopped at the grab; the x-cam may still come
            elif (curr.global_timer - grab.grab_frame
                  >= self.XCAM_TIMEOUT_FRAMES):
                # The backstop, and it is a BACKSTOP now rather than an
                # ending: a Usamune PAUSE freezes game logic while
                # gGlobalTimer runs on (anchors.py's pause streak measures the
                # same thing), so ten seconds in the menu mid-fall trips this
                # even though the player is about to land perfectly normally.
                # Publish the grab moment so the row is never lost, and KEEP
                # WATCHING: if Mario reaches the dance after all, the x-cam
                # overwrites this with the leaderboard-legal time and the row
                # stops being marked `grab` (live report 2026-08-02).
                grab.published = (grab.grab_igt, grab.grab_igt_source)
                grab.timed_at = "grab"
                return [self._emit(grab, curr, grab.grab_frame, "grab",
                                   *grab.published)]
            else:
                return []
        waited = curr.global_timer - grab.xcam_frame
        if grab.published is not None and grab.timed_at == "grab":
            # Published by the backstop above and the x-cam has now arrived.
            # Same decision as a first publish — when is Usamune's number
            # final? — but it lands as a CORRECTION, because the row already
            # exists and only its time and its legality change.
            if not broken and not self._ready_to_publish(grab, curr, waited):
                return []
            igt_frames, source = self._answer(grab, curr)
            grab.published, grab.timed_at = (igt_frames, source), "xcam"
            grab.published_after = waited
            if broken or waited >= self.RESULT_SETTLE_FRAMES:
                self._pending = None
            return [self._correction(grab, curr, igt_frames, source)]
        if grab.published is None:
            # Publishing early means publishing OUR derivation, and on a
            # subarea star that is the time since the area loaded — the one
            # number Usamune is about to contradict. When the clock says its
            # zero point may be an area load, wait the whole window instead
            # and publish Usamune's answer once: the correction exists as a
            # backstop, but a row that lands right the first time is what
            # stops an impossible PB flashing on screen (live report
            # 2026-08-01, the SSL pyramid at 0'02"26 and then 0'27"66).
            if not broken and not self._ready_to_publish(grab, curr, waited):
                return []
            igt_frames, source = self._answer(grab, curr)
            grab.published = (igt_frames, source)
            grab.published_after = waited
            grab.writes = [(write_frame - grab.xcam_frame, value) for
                           write_frame, value in
                           self._clock.result_writes_since(grab.xcam_frame, curr)]
            if broken or waited >= self.RESULT_SETTLE_FRAMES:
                self._pending = None  # nothing left that could correct it
            return [self._emit(grab, curr, grab.xcam_frame, "xcam",
                               igt_frames, source)]
        # Published, and still watching for Usamune's late whole-star write.
        # A break ends the watch with no correction: whatever the counter or
        # the store would say after a reset or a level exit describes another
        # context (the store is CLEARED on exit — RESULT_SETTLE_BRACKET).
        if broken:
            self._pending = None
            return []
        if waited < self.RESULT_SETTLE_FRAMES:
            return []
        self._pending = None
        final = self._answer(grab, curr)
        if final == grab.published:
            return []   # Usamune agreed with what we already showed him
        return [self._correction(grab, curr, *final)]

    def _ready_to_publish(self, grab: _PendingGrab, curr: GameSnapshot,
                          waited: int) -> bool:
        """Has Usamune finished answering — or must we stop waiting anyway?

        The fast door is AGREEMENT. We derive the x-cam number ourselves from
        the counter and Usamune writes its own into the result store, and when
        those two say the same thing there is nothing left to learn: no later
        write can move a number both sources already agree on, except the
        whole-star correction on a subarea star, which is the branch above.
        That is what makes it safe to leave BEFORE the deadline — publishing
        on the mere EXISTENCE of a write is what flickered, because the burst
        includes the grab-time value (WF: +1=328 when the answer was 330).

        Agreement is exact to a frame, and deliberately not two: the counter
        path ran 1-2 frames under Usamune across the measured grabs, so a
        two-frame tolerance would match that 328 and publish the number this
        whole file exists to refuse. When they disagree by more, the deadline
        publishes Usamune's own value anyway — the cost of a torn read is
        latency, never a wrong row."""
        if grab.xcam_counter_is_partial and grab.carried_igt is None:
            return waited >= self.RESULT_SETTLE_FRAMES
        usamune = self._clock.settled_result_at_or_after(grab.xcam_frame, curr)
        if (usamune is not None
                and abs(usamune - grab.xcam_igt) <= self.AGREEMENT_FRAMES
                and grab.xcam_igt_source != "reconstructed"):
            return True
        # A CARRIED subarea star keeps the long deadline. Its early write is
        # the subarea-local echo, and leaving on the short backstop would
        # publish that echo — the impossible PB this file exists to refuse.
        # The carry buys an early exit through agreement, never a later one.
        return waited >= (self.RESULT_SETTLE_FRAMES
                          if grab.xcam_counter_is_partial
                          else self.PUBLISH_WAIT_FRAMES)

    def _answer(self, grab: _PendingGrab,
                curr: GameSnapshot) -> tuple[int, str]:
        """Usamune's number for this x-cam, as best known RIGHT NOW — the one
        door both the publish and the correction read, so the two can never
        disagree about anything except when they were asked."""
        usamune = self._clock.settled_result_at_or_after(grab.xcam_frame, curr)
        if usamune is not None and grab.xcam_igt_source != "reconstructed":
            # Usamune's own number for this x-cam beats our derivation of it —
            # not for precision (they agreed to a frame on nine of eleven live
            # grabs) but because ours is subarea-local; see
            # IgtClock.settled_result_at_or_after. The reconstructed carve-out
            # keeps the reset-race guard: a grab that raced a reset has a
            # near-zero result written for it, which is the case that guard
            # exists to refuse.
            return usamune, "result"
        return grab.xcam_igt, grab.xcam_igt_source

    def _emit(self, grab: _PendingGrab, curr: GameSnapshot, frame: int,
              timed_at: str, igt_frames: int, source: str) -> Event:
        return Event(
            type="star_collected",
            frame=frame,
            timestamp_utc=curr.wall_time_utc,
            payload={
                "course_id": grab.course_id,
                "course_name": course_name(grab.course_id),
                "star_id": grab.star_id,
                "star_name": star_name(grab.course_id, grab.star_id),
                "already_collected": grab.already_collected,
                "igt_frames": igt_frames,
                "igt": format_igt(igt_frames),
                "igt_source": source,
                "igt_reconstructed": source == "reconstructed",
                "igt_timed_at": timed_at,
                "grab_frame": grab.grab_frame,
                "num_stars": grab.num_stars,
                "published_after": grab.published_after,
                "result_writes": grab.writes or [],
                "carried_igt": grab.carried_igt,
            },
        )

    def _correction(self, grab: _PendingGrab, curr: GameSnapshot,
                    igt_frames: int, source: str) -> Event:
        """The published row's answer changed. Two causes, and they differ in
        what they cost the player:

        * a subarea star's whole-star write, which moves the NUMBER;
        * an x-cam that arrived after the backstop already published the grab
          moment, which moves the number AND the row's legality — `igt_timed_at`
          goes from "grab" to "xcam", so a time a leaderboard would reject
          becomes one it accepts. That is why the moment travels in the
          payload rather than being assumed: a correction is the only thing
          that can make a recorded row legal again.

        The payload carries the grab's identity as well, so the projector can
        refuse a correction that does not belong to the star_collected it
        would otherwise be paired with — one grab is correctable at a time
        (see _detect), and this makes that a checked fact rather than a
        promise. `frame` is the x-cam."""
        return Event(
            type="star_time_corrected",
            frame=grab.xcam_frame,
            timestamp_utc=curr.wall_time_utc,
            payload={
                "course_id": grab.course_id,
                "star_id": grab.star_id,
                "grab_frame": grab.grab_frame,
                "igt_frames": igt_frames,
                "igt": format_igt(igt_frames),
                "igt_source": source,
                "igt_reconstructed": source == "reconstructed",
                "igt_timed_at": "xcam",
            },
        )
