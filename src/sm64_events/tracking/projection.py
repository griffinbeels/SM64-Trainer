"""Pure attempt projection: journal events in -> attempts out.

Two-pass projection: cleared_ids() first, then the sequential Projector —
so a grab marked "mistake" never moves the practice target, which
retroactively re-attributes every later failure. Attempt ids are the
journal id of the attempt's first event: stable across rebuilds.

Caveats (hard-won — keep these current):

1. Same-tick reset-race — HISTORICAL journals only, since 2026-08-01: a
   practice_reset followed by a star_collected in the same poll tick opens a
   new attempt and closes it on the grab, with rta near 0 while igt carries
   the PRIOR attempt's reconstructed time (see star_grab.py docstring) — the
   row's two clocks legitimately disagree; consumers must prefer igt for
   such rows. Nothing in this projector changed and nothing should: replaying
   those rows the same way is what keeps old journals stable. What changed is
   upstream, so no NEW journal can take that shape — the held grab is
   published before the reset that broke its wait (main.build_detectors'
   docstring), so it closes the attempt it belongs to and the reset finds
   nothing open. Read in the other direction, that ordering is what this
   caveat was always describing: one grab used to record twice, a reset row
   AND a success row (live report 2026-08-01).

2. Clearing invariant: attempt_cleared/attempt_restored payloads carry
   Attempt.id, which is the journal id of the attempt's FIRST event — for
   an anchored success that is the ANCHOR's id, NOT the star_collected
   event's id. Clearing by the grab's journal id is a silent no-op.

3. Payload trust: event payloads come from our own detectors/service and
   are trusted; a KeyError on a required key (course_id/star_id) means a
   corrupt journal and should fail loud rather than skip rows.

4. Outcomes: success (star grabbed), reset (practice_reset closes open
   attempt), hard_reset (game_reset), abandoned (session_started or
   level_changed closes open attempt), death (Mario entered a death action).
   outcome_detail carries the death cause string; menu detail has no Phase 1
   producer (reserved).

5. Inactivity discard: reset-closures where mario_acted=False are dropped
   entirely — the player never acted, so the closed attempt is not a real
   attempt. The anchor still opens the next attempt. Old journals lack the
   key; default is True, so rebuilds are stable.
   Additionally, reset-closures with paused_frames_before >=
   PAUSE_DISCARD_FRAMES are dropped the same way: a long Usamune-menu pause
   immediately before the reset means the player went AFK and came back —
   discarded even when the attempt had real activity (user decision).
   For attempts opened by an acted_tracking anchor the judgment is
   event-based (a mario_acted journal event during the attempt) and applies
   to EVERY non-success closure: reset, death, abandoned, hard_reset.
   Successes always count.

6. Strategy memory is PER STAR: strat_by_star[(course_id, star_id)] stores
   the last-set strategy for that star independently. Switching targets
   never leaks the previous star's strat. The strat_tag on an attempt is the
   attributed star's last-remembered strategy at close time. strat_set events
   write the same memory without moving the target.

7. Dust-trick attachment: rollout/jump events accumulate and attach to
   whichever attempt closes next (covers anchored AND grab-only attempts).
   Every boundary event (anchor, grab, death, game_reset, session_started,
   level_changed) zeroes the accumulators after its close runs, so counts
   never leak across attempts, idle gaps, or discarded no-op resets.

8. Rollout payload compat: journals written before the corrected timing
   model (no "landing_frames" key) counted visible slide frames as
   frames_late — there, frames_late == 1 IS the frame-perfect input
   (decomp-verified; see detectors/dust.py). _rollout_is_dustless()
   re-derives the classification on replay; new payloads are trusted.

9. Castle attempts: an attempt OPENED while the tracked level is a castle
   hub (CASTLE_LEVELS) is castle movement — discarded on every non-success
   closure, never attributed to a star. Judgment is at open time; the
   closing level_changed updates _level only AFTER its close runs, so exits
   are judged by the level the attempt lived in. _level starts None
   (unknown -> attribute) so pre-level-detector journals replay unchanged.

10. Tagged target identity: self.target is ("star", course_id, star_id) |
    ("segment", segment_id) | None. Journals written before segments carry
    target_set payloads WITHOUT a "kind" key — those replay as star targets,
    so historical journals rebuild unchanged. Star attribution/strat lookups
    go through _star_target(); a segment target attributes star-side
    failures to nothing (course/star None), mirroring no-target behavior.

11. Segment attempts: the SegmentEngine (tracking/segments.py) runs on the
    SAME feed, AFTER _dispatch — its MatchContext sees the post-event level
    plus the pre-event level captured before _dispatch. Engine-closed
    attempts get strat (strat_by_segment) and cleared state stamped here
    with the same first-event-id keying as star attempts (caveat 2: a
    segment attempt's id is its ARM event's journal id + the namespace
    offset, see segments.SEGMENT_ATTEMPT_OFFSET). A non-cleared segment
    success auto-follows the target like a star grab does — EXCEPT when it
    completes by entering a star stage, which clears the target instead
    (caveat 12).

12. Active-star retirement (active-star/segment exclusivity, 2026-06-12): a
    STAR target is the active practice focus only while we're plausibly still
    doing it, so feed() retires it (target -> None) on two signals — a segment
    going ARMED (a segment run just started; the just-armed segment is the new
    focus) and a level_changed into a DIFFERENT course (the star lives in
    another stage). course_for_level (addresses.py) bridges the level-id ->
    course-id gap; hub levels / Bowser arenas have NO course, so passing
    through them keeps the target and the retry loop is uninterrupted. SEGMENT
    targets retire by exactly the SAME rule (rewritten 2026-07-27): entering a
    course that is not where the segment is practiced FROM retires it, and the
    hubs/castle stay transit for both kinds. `_seg_origins` is
    segments.segment_origin — where the definition STARTS, plus the user's
    origin override. Its predecessor read `start_level_set`, i.e. `arm_level`,
    i.e. where a start trigger LEAVES Mario, which is None for 50 of the 51
    seeded `level_exit` clauses: 54 of 65 definitions answered "anywhere" and
    this branch was dead for every castle movement, which is the live report of
    2026-07-27 ("ACTIVE SEGMENT  WF -> SSL" while standing in CCM). A
    definition that genuinely names no place (a `reset_game` start) still
    answers None and still keeps its target, as does a deleted def.
    No resume stash for segments: the matcher re-arms on return (armed pins the
    UI) and the arena/stage banner re-targets on entry. A segment that
    SUCCEEDS by entering a star stage (its closing event is a level_changed
    into a course-bearing level — MIPS ends in DDD, LBLJ in BITDW) does NOT
    auto-follow onto the segment either: it lands us in a fresh stage with no
    star picked, so the target clears to None (no active star AND no active
    segment). Segment successes that do not enter a stage (star grab, mid-
    course, exit to hub) still auto-follow onto the segment. Retirement runs
    inside feed(), so replay rebuilds the same end-state target and service.
    _track auto-broadcasts target_changed. The UI reads this to highlight the
    active star XOR the active segment, never both (ui/components/practice.js);
    the frontend also retires its sticky segment pin on any segment success so
    a stage-entry completion leaves nothing pinned (ui/store.js).

13. Active-star resume (2026-06-12): a star target retired by LEAVING its
    course (caveat 12, either path) is stashed in _suspended_star. Re-entering
    that same course with NOTHING active (target is None) reinstates it —
    "I accidentally left HMC, going back resumes the star I was on." The stash
    survives hub transit (hub entries don't match any course) and the segment
    arm that deactivated the star; it is consumed on restore, overwritten by
    the next leaving-retirement, and dropped once a NEW focus is committed
    (a grab, a target_set, or a segment success — so doing the segment instead
    of going back does NOT later resurrect the star, and a segment completed
    into a stage stays "nothing active"). If re-entry ALSO arms a segment, the
    arm-clear in feed() retires the just-restored star again — segment wins,
    matching caveat 12.

14. Validity bounds (spec 2026-07-23): a SUCCESS whose completion time falls
    outside [min, max] is stamped cleared with an "auto: " reason at close
    time via _auto_ignored — stars judge igt (falling back to rta) against
    the time_filters KV (per "<course>:<star>", DEFAULT_MIN_FRAMES applies
    when no override sets min_frames); segments judge rta against their
    def's close-phase min_time/max_time guard rows (time_bounds), same
    DEFAULT_MIN_FRAMES floor when neither guard is present. Ids with ANY
    journaled clear/restore history (touched_ids) are exempt — manual always
    wins. Only successes are judged (a fast reset is legitimate practice; no
    clock at all is not judged either). An auto-cleared row doesn't move the
    target (the seg_closed loop's "not a.cleared" follow-check and
    _close_by_grab's identical check both skip it for free), and cleared rows
    are invisible to runs (RunTracker._apply skips them).

15. Last-star memory: _last_star_grabbed/_last_star_attempted update from the
    current event's closures (attributed star attempts only: grabs set both,
    non-success attributed rows set attempted; cleared rows still update —
    the grab happened physically) BEFORE the engine's ctx is built, so a
    guard evaluated on the closing event sees the fresh value; game_reset
    clears both to unknown (file can change), and unknown conservatively
    fails the last_star_* guards.

16. Strat reclassification: an attempt's strat_tag is the strategy remembered
    at CLOSE time (caveat 6) unless a journaled attempt_strat_set overrides
    it — strat_overrides() is the pre-pass (sibling of cleared_ids), keyed by
    the attempt's first-event id like clearing is (caveat 2), and applied at
    both stamping sites (_build for stars, the segment stamp in feed()).
    Last write wins, so re-picking the previous strategy is the undo. The
    event itself is inert in _dispatch: it opens and closes nothing.

17. Segment strategy DEFAULTS (spec 2026-07-24-segment-default-strat): a
    definition may carry a default_strat, and strat_by_segment starts out
    pre-seeded from the defs rather than empty. Caveat 6's rule is otherwise
    unchanged — the attempt still gets the strategy remembered at close time;
    the default just makes "remembered" non-empty from the first run, with
    nothing journaled on the user's behalf. Two consequences worth knowing:
    a journaled strat_set with a FALSY tag falls back to the default instead
    of clearing (no-strategy is not a legitimate state for a defaulted
    segment), and because the seed comes from the defs the whole thing
    re-derives on every replay. Stars have no equivalent: a default needs a
    seeded definition row and stars have none (CLAUDE.md rule 11 asymmetry,
    recorded in the spec).

18. Grab-steals-the-target exemption (spec 2026-07-28-multi-step-segments
    round 2, live report 2026-07-30): _close_by_grab’s "last VALID grab
    moves the practice target" rule normally fires on every star grab,
    unconditionally. feed() now restores the pre-event target when: it was
    already a SEGMENT target, that segment was already armed BEFORE this
    star_collected event, it is STILL armed AFTER the segment engine has
    processed the very same event without closing it, and the definition
    carries waypoints under strict/exclusive matching (never loose, which
    stays armed through any grab by design and so "still armed" carries no
    information there). This is what stops a Bowser Reds->Pipe run’s own
    mid-sequence star grab from handing the target to the star for the rest
    of the run — the star attempt itself still records exactly as before
    (caveat 6’s memory, caveat 15’s last-star tracking), only the TARGET
    move is suppressed. See feed()’s own comment for the full reasoning and
    why a genuinely unrelated grab can never trigger this (the matcher’s
    own major-action cancel already disarms a def that doesn’t expect it).

19. Late star times (2026-08-01, live report "the tool feels laggy"): a
    star_collected is journaled as soon as Usamune answers rather than after
    its whole settle window, so the row appears while the dance is still
    playing — and a `star_time_corrected` follows on the rare star (a subarea
    one) whose later whole-star write changes the number.
    time_corrections() is the pre-pass, keyed by the GRAB's journal id, and
    feed() folds the revision into that grab's own payload before dispatch.
    Everything downstream — the star attempt, a segment closed by the same
    grab, the 100-coin reattribution — therefore reads one number and never
    learns a correction happened, and re-projecting is the only thing that
    has to run for a corrected row to be right (tracking/service.py does
    exactly that when one is journaled). The event is inert in _dispatch: it
    opens and closes nothing, like attempt_strat_set.
"""
from dataclasses import dataclass, replace

from sm64_events.memory.addresses import CASTLE_LEVELS, course_for_level
# one-way import: segments.py pulls Attempt lazily at call time, so this
# module-level import cannot cycle (see SegmentEngine.feed).
from sm64_events.tracking.prune import PRUNE_EVENT
from sm64_events.tracking.runs import RunTracker
from sm64_events.tracking.segments import (
    SEGMENT_ATTEMPT_OFFSET, MatchContext, SegmentEngine, hundred_coin_entity,
    origin_course, segment_origin, stage_origin, time_bounds)


@dataclass(frozen=True)
class Attempt:
    id: int                    # journal id of the attempt's first event
                               # (segment attempts: + namespace offset — caveat 11)
    session_id: int
    course_id: int | None      # None = failure with no declared target yet
    star_id: int | None
    strat_tag: str | None
    anchor_type: str           # practice_reset | state_loaded | none
    anchor_frame: int | None
    outcome: str               # success | reset | hard_reset | abandoned | death
    outcome_detail: str | None  # death cause; menu detail has no Phase 1 producer
    igt_frames: int | None
    rta_frames: int | None
    started_utc: str
    ended_utc: str
    cleared: bool
    cleared_reason: str | None
    rollouts_total: int = 0      # dust-trick sub-events during this attempt
    rollouts_dustless: int = 0
    jumps_total: int = 0         # chained double/triple jumps
    jumps_dustless: int = 0
    segment_id: int | None = None  # set => segment attempt; course/star None
    timed_by: str = "igt"      # how the recorded time was MEASURED, so a
                               # display can say why two times are not
                               # comparable (ruling 6, round 3):
                               #   "igt"   — Usamune's own IGT clock, via
                               #             detectors/igt_clock.py. Every star,
                               #             key and pipe-touch time, and every
                               #             segment whose closing event carried
                               #             an igt_frames it was allowed to use.
                               #   "delta" — a wall-frame delta (close.frame -
                               #             arm.start_frame), the honest
                               #             fallback SegmentEngine._close takes
                               #             when Usamune's number is absent or
                               #             does not measure this span. Counts
                               #             paused frames and carries the
                               #             arm-frame alignment error, so it
                               #             runs ~1-2 frames CHEAP against an
                               #             igt-timed run of identical play.
                               # Derived on every reproject from the journal, so
                               # it needs no backfill and cannot go stale: a
                               # historical warp_entered carries no igt_frames in
                               # its journaled payload and never will, which is
                               # "backfill is impossible in principle" seen from
                               # the other side. Default "igt" is correct for
                               # every producer except that one fallback branch.
    closed_by: str | None = None   # event TYPE that closed this attempt, or
                               # None for a producer that does not record one
                               # (star closures, where the type is implied by
                               # the outcome). Kept BESIDE timed_by because
                               # neither answers alone: 570 of 626 segment
                               # attempts in the 2026-07-31 journal are
                               # delta-timed, and most are delta FOREVER --
                               # a movement closing on a level_changed has no
                               # Usamune number to be given, so its delta is
                               # simply how that segment is measured and stays
                               # comparable to the next run of it. The rows
                               # that are NOT comparable are the ones whose
                               # closing event type is in
                               # core.events.IGT_BEARING_EVENT_TYPES, where a
                               # fresh run WOULD now be igt-timed.
    timed_at: str | None = None    # WHERE INSIDE the closing event the time was
                               # taken -- "xcam" | "grab" for a star closed by
                               # a star_collected, None for everything else
                               # (a segment, a failure, a key/pipe closure).
                               # Stars are the one kind with two candidate
                               # moments and a leaderboard rule about which is
                               # legal (round-4 items 3/4): Usamune stops at
                               # the x-cam, we used to stop at the grab, and
                               # the gap is 0-39 frames of falling.
                               #
                               # Read straight off the closing event's own
                               # payload (`igt_timed_at`, added by the x-cam
                               # fix), so this re-derives on every reproject
                               # like `timed_by` does and needs no backfill.
                               # ABSENCE of that key is UNKNOWN -- None -- and
                               # this said the opposite until 2026-08-02.
                               # The key did not exist before 2026-08-01, and
                               # reading its absence as "grab" asserted
                               # something the journal cannot support: of his
                               # 766 recorded grabs, 669 are legacy rows whose
                               # `igt_source` is "result", i.e. they took
                               # Usamune's OWN stored number. Under STOP=Xcam,
                               # or on any ground grab, that store holds the
                               # x-cam value and the row is perfectly legal;
                               # under GrabX with a real fall it holds the
                               # grab-time write and the row is not. Nothing
                               # in the journal says which -- it keeps no
                               # post-grab frames -- so the row is UNKNOWN,
                               # and a default that guesses either way is a
                               # claim rather than a record. It matters
                               # because behaviour now hangs off this: a
                               # grab-timed row cannot be saved as a PB
                               # (tracking/caveats.py::pb_blocked_by), and
                               # "grab" would have refused 669 saves for a
                               # reason nobody measured. The caveat MARK still
                               # covers the unknown rows -- an unverifiable
                               # time is exactly what a caveat is for -- so
                               # what he sees is unchanged; only the refusal
                               # is narrowed to proof.
                               #
                               # Bowser 3's grand star is deliberately NOT
                               # stamped: it closes on key_grabbed, whose
                               # cutscene action has no fall/dance pair to
                               # derive an x-cam from, and whether STOP moves
                               # its number at all is UNMEASURED. Claiming
                               # "grab" there would assert something nobody
                               # has run the experiment for; the limitation is
                               # recorded in docs/api.md instead.


ANCHOR_EVENT_TYPES = ("practice_reset", "state_loaded")

# AFK rule (spec 2026-06-11): a reset/load arriving after >=5 s of pause (the
# Usamune menu freezes IGT while gGlobalTimer keeps running) closes a run the
# player walked away from — that is AFK, not a practice reset. Discard applies
# even when the attempt had real activity before the pause (user decision).
PAUSE_DISCARD_FRAMES = 150  # 5 s x 30 fps

# Validity floor (spec 2026-07-23): a SUCCESS faster than this is a detection
# artifact (reset-race rows, mis-triggers), auto-cleared with an "auto: "
# reason. Applies to every star/segment unless an override says otherwise
# (stars: time_filters KV, min_frames 0 disables; segments: min_time guard).
DEFAULT_MIN_FRAMES = 15  # 0.5 s x 30 fps

# Events that delimit attempts; each one zeroes the rollout accumulator
# after its close runs (see docstring caveat 7).
BOUNDARY_EVENT_TYPES = frozenset(ANCHOR_EVENT_TYPES) | {
    "star_collected", "death", "game_reset", "session_started",
    "level_changed"}


def cleared_ids(events) -> dict[int, str | None]:
    """attempt_id -> reason for attempts whose LAST clear/restore is a clear."""
    cleared: dict[int, str | None] = {}
    for ev in events:
        if ev.type == "attempt_cleared":
            cleared[int(ev.payload["attempt_id"])] = ev.payload.get("reason")
        elif ev.type == "attempt_restored":
            cleared.pop(int(ev.payload["attempt_id"]), None)
    return cleared


def touched_ids(events) -> set[int]:
    """Attempt ids with ANY journaled clear/restore history — the manual
    domain. The auto validity-bounds rule never touches them (manual always
    wins), so Restore on an auto-ignored row is a per-row exemption."""
    out: set[int] = set()
    for ev in events:
        if ev.type in ("attempt_cleared", "attempt_restored"):
            out.add(int(ev.payload["attempt_id"]))
    return out


def strat_overrides(events) -> dict[int, str | None]:
    """attempt_id -> reclassified strat_tag (None = deliberately unlabeled).

    The compensating-event sibling of cleared_ids(). A strategy is declared
    BEFORE a run and is therefore often wrong after it ("I said Cannonless,
    then did something else"); the journal is append-only, so the correction
    is appended and folded in here rather than written into the derived
    attempts row. Last write wins, which makes re-picking the previous
    strategy the undo — no restore event needed."""
    out: dict[int, str | None] = {}
    for ev in events:
        if ev.type == "attempt_strat_set":
            # attempt_id stays a strict [] read — an override with no target
            # attempt is meaningless and should surface loudly. strat_tag
            # uses .get(), like cleared_ids()'s "reason": replay() runs at
            # server boot (TrackerService.start), so a KeyError here on a
            # malformed journal row would block startup instead of just
            # degrading that one row to "unlabeled" (None is already a valid
            # strat_tag value, so .get()'s default reads the same either way).
            out[int(ev.payload["attempt_id"])] = ev.payload.get("strat_tag")
    return out


# `igt_timed_at` is in the list because a correction can change WHICH MOMENT
# the row describes, not only its number: a grab published by the x-cam
# backstop (a pause mid-fall trips it) is marked "grab" and is not a
# leaderboard-legal time, and the x-cam arriving late is what makes it one
# again. tracking/caveats.py reads Attempt.timed_at for exactly that, so
# leaving this out would fold in the right number under a stale mark.
CORRECTED_TIME_KEYS = ("igt_frames", "igt", "igt_source", "igt_reconstructed",
                       "igt_timed_at")


def time_corrections(events) -> dict[int, dict]:
    """journal id of a star_collected -> the time fields Usamune later revised.

    The third compensating-event pre-pass, beside cleared_ids() and
    strat_overrides(), and the one that lets a grab be JOURNALED before
    Usamune has finished answering (detectors/star_grab.py: the row appears
    within ~400 ms instead of 1.5 s, and only a subarea star's late whole-star
    write ever changes it). Folding the correction back into the grab's own
    payload — rather than patching the attempt row — is what keeps every
    downstream reader unaware that a correction exists at all: the star
    attempt, a segment closed by that same grab, and the 100-coin
    reattribution all read `ev.payload["igt_frames"]` and all get the final
    number, with no second place that has to remember to apply it.

    Paired with the star_collected BEFORE it, because at most one grab is
    correctable at a time (star_grab.py::_detect ends the watch on the next
    grab) — and then CHECKED against that grab's identity, so a journal where
    the pairing is not what the detector meant drops the correction instead of
    moving another star's time."""
    out: dict[int, dict] = {}
    last_grab = None
    for ev in events:
        if ev.type == "star_collected":
            last_grab = ev
        elif ev.type == "star_time_corrected" and last_grab is not None:
            if all(ev.payload.get(k) == last_grab.payload.get(k)
                   for k in ("course_id", "star_id", "grab_frame")):
                out[last_grab.id] = {k: ev.payload[k]
                                     for k in CORRECTED_TIME_KEYS
                                     if k in ev.payload}
    return out


class _CorrectedRow:
    """One journal row seen with a revised payload. The single seam a time
    correction needs: everything downstream reads the row exactly as it always
    has, and reads the corrected number."""
    __slots__ = ("_row", "payload")

    def __init__(self, row, payload):
        self._row, self.payload = row, payload

    def __getattr__(self, name):
        return getattr(self._row, name)


class Projector:
    """Sequential pass; feed() returns attempts CLOSED by that event."""

    def __init__(self, cleared: dict[int, str | None] | None = None,
                 segments: list | None = None,
                 time_filters: dict | None = None,
                 touched: set[int] | None = None,
                 strat_overrides: dict[int, str | None] | None = None,
                 origin_overrides: dict | None = None,
                 time_corrections: dict[int, dict] | None = None):
        self._cleared = cleared if cleared is not None else {}
        # attempt_id -> reclassified strat (caveat 16); shadows the strat
        # remembered at close time.
        self._strat_overrides = (strat_overrides
                                 if strat_overrides is not None else {})
        # star_collected journal id -> Usamune's revised time (caveat 19).
        # Empty while a journal is being fed LIVE, since the correction is
        # journaled after the grab it revises — the service re-projects on
        # one, which is where this dict comes from.
        self._time_corrections = (time_corrections
                                  if time_corrections is not None else {})
        # ("star", course_id, star_id) | ("segment", segment_id) | None
        self.target: tuple | None = None
        # (course_id, star_id) of the active star most recently retired BY
        # LEAVING its course (caveat 12). Re-entering that course with nothing
        # else active reinstates it ("resume the star I just stepped out of");
        # consumed on restore, overwritten on the next such retirement, and
        # dropped once a new focus is committed (grab / set_target / segment
        # success). See caveat 13.
        self._suspended_star: tuple[int, int] | None = None
        self.strat_by_star: dict[tuple[int, int], str | None] = {}
        # Pre-seeded from the definitions' own default_strat (caveat 17), so
        # every consumer of this dict — attempt stamping below, the strat_tag
        # property, the view's section banner / segment_targets / target
        # payload — gets the default without a call-site of its own. A
        # journaled strat_set overwrites the entry; nothing is ever written to
        # the journal on the user's behalf, so this re-derives identically on
        # every replay.
        self._seg_default_strat: dict[int, str] = {
            d.id: d.default_strat for d in (segments or []) if d.default_strat}
        self.strat_by_segment: dict[int, str | None] = dict(
            self._seg_default_strat)
        self._segments = SegmentEngine(segments or [])
        # Validity bounds (spec 2026-07-23). Stars: "<course>:<star>" ->
        # {min_frames, max_frames} from the time_filters ui_state KV.
        # Segments: derived here from each def's close-phase time guards.
        self._time_filters = time_filters or {}
        self._touched = touched if touched is not None else set()
        self._seg_bounds = {d.id: time_bounds(d.guards)
                            for d in (segments or [])}
        # def id -> the world node the segment is practiced FROM (None =
        # anywhere); drives segment-target retirement on level_changed
        # (caveat 12). `origin_overrides` is the ui_state KV a user sets when
        # the derivation guesses wrong — threaded in like `time_filters` so
        # the picker and this rule cannot disagree about where a segment is.
        self._seg_origins = {
            d.id: segment_origin(d.id, d.start_triggers, origin_overrides)
            for d in (segments or [])}
        # {(course_id, 6)} whose whole course-visit span is timed by an
        # ENABLED engine (spec 2026-07-28-multi-step-segments, "the 100-coin
        # star IS the segment"): _close_by_grab reads this to suppress the
        # plain star-6 attempt it would otherwise record on the grab itself,
        # since that engine's own completion (at the exit star) is what
        # closes it instead (see the seg_closed loop in feed()). Filtered to
        # d.enabled like _seg_origins is NOT — a disabled/deleted def never
        # arms at all, so it must never suppress anything; the plain star
        # attempt is the fallback that keeps the grab meaningful, same
        # philosophy the retired _hundred_coin_redirect used.
        self._hundred_coin_engines: set[tuple[int, int]] = {
            hc for d in (segments or []) if d.enabled
            and (hc := hundred_coin_entity(d.start_triggers, d.waypoints))
            is not None}
        self.segment_notices: list[dict] = []  # live-broadcast queue, drained by service
        self._runs = RunTracker()
        self.run_notices: list[dict] = []   # live-broadcast queue, drained by service
        self._num_stars: int | None = None
        # (course_id, star_id) of the most recent star grab / attributed star
        # attempt — feeds MatchContext for the last_star_* guards (spec
        # 2026-07-23). Updated from closures BEFORE the engine sees the same
        # event; game_reset clears both (file can change at the title screen,
        # same rationale as _num_stars). Cleared rows still update: the grab/
        # attempt happened physically, validity is a separate judgment.
        self._last_star_grabbed: tuple[int, int] | None = None
        self._last_star_attempted: tuple[int, int] | None = None
        # Active route's member segment ids (spec
        # 2026-07-23-default-routes-foundation), set by a journaled
        # route_selected event and fed into MatchContext for the
        # in_active_route arm guard. None = no active route.
        self._route_segments: frozenset | None = None
        # The route_id half of the same route_selected event — replay-derived
        # like armed_route_id(), so a service restart never loses track of
        # which route is active (there is no second, in-memory source of
        # truth for this; see active_route_id()).
        self._active_route_id: int | None = None
        self._open = None  # EventRow of the open attempt's anchor
        self._open_acted = False  # mario_acted seen since the last anchor; only meaningful while _open is set
        self._level: int | None = None   # gCurrLevelNum per level_changed; None = unknown (legacy journals)
        self._area: int | None = None    # gCurrAreaIndex per area_changed; None = unknown (legacy journals)
        self._open_castle = False        # open attempt was OPENED in a castle hub level; only meaningful while _open is set (the open site re-arms it)
        self._rollouts_total = 0
        self._rollouts_dustless = 0
        self._jumps_total = 0
        self._jumps_dustless = 0

    @property
    def strat_tag(self) -> str | None:
        """The current target's remembered strategy (per-target memory)."""
        if self.target and self.target[0] == "star":
            return self.strat_by_star.get(self.target[1:])
        if self.target and self.target[0] == "segment":
            return self.strat_by_segment.get(self.target[1])
        return None

    def _star_target(self) -> tuple[int, int] | None:
        """(course_id, star_id) when the target is a star, else None —
        segment targets attribute star-side failures to nothing."""
        if self.target and self.target[0] == "star":
            return self.target[1], self.target[2]
        return None

    def armed_segment_ids(self) -> set[int]:
        """Currently-armed segment def ids — engine-state delegate so the
        service can diff armed sets across a reproject without reaching
        into privates."""
        return self._segments.armed_ids()

    def _armed_loosely(self, segment_id: int) -> bool:
        """True when `segment_id` is currently armed AND its def is LOOSE-
        matched (task 5, spec 2026-07-28-multi-step-segments) — the property
        that exempts a segment target from the origin-retirement rule in
        _dispatch below.

        Scoped to LOOSE defs only, not "any armed segment" (the brief's
        version): a STRICT multi-step def that wanders into a level outside
        its waypoint sequence is SILENTLY CANCELLED by the matcher on this
        very event (segments.py's `_feed_waypoint` "major action" branch),
        so exempting it here too would leave a stale target pointing at a
        def the matcher just disarmed — caught by
        test_waypoint_level_keeps_a_multi_level_segment_target, which needs
        exactly that retirement on an unrelated level move mid-sequence.
        Loose mode's whole point is tolerating such a move without
        cancelling (it "stays armed through everything but its end and a
        staleness deadline"), so only loose earns the exemption."""
        if segment_id not in self.armed_segment_ids():
            return False
        d = self._segments.definition(segment_id)
        return d is not None and d.match_mode == "loose"

    def _clears_star_target(self, segment_id: int) -> bool:
        """True when `segment_id` just arming should retire the CURRENT star
        target (feed()'s "a segment armed retires a star target" rule,
        2026-06-12) — false when arming it is the ambient side effect of
        standing in the SAME course the target already names, not a
        different activity starting.

        GENERALIZED (spec 2026-07-28-multi-step-segments) from an earlier,
        narrower version scoped to "only star 6, only its own
        HUNDRED_COIN_EXIT engine" — that fix was correct as far as it went
        but left the regression it was patched against reachable for every
        OTHER star: the reshape that made `seg:100c->exit:*` arm on mere
        course ENTRY (b9c72f3/ccf989b) means walking into ANY of the 15 main
        courses while practicing a DIFFERENT star in that course silently
        wiped the target — confirmed live (`target_set` star (2,3), feed a
        level_changed into WF, `proj.target` came back None) — and Bowser's
        `seg:reds->pipe:*`/legacy pipe-entry trio share the identical arm
        shape (never surfaced the same bug only because a Bowser course has
        exactly one star to collide with).

        The real distinguishing question is not "which family is this" but
        "is the arming def practiced FROM the same course the target star
        lives in" — `segments.origin_course(segment_origin(...))`, the SAME
        reader the segment-target half of this rule already uses two
        branches below, applied here to the star half for the first time.
        Arming from a DIFFERENT course (or the castle/a hub — origin None)
        still retires the target unchanged: a movement leaving the target's
        own course already retires it via the course-change rule in
        _dispatch (which runs BEFORE this, on the SAME level_changed), so
        this branch is only ever reached with a star target for course-
        neutral arms — exactly the ambient case this exists for."""
        if not (self.target and self.target[0] == "star"):
            return False
        d = self._segments.definition(segment_id)
        if d is None:
            return True
        seg_course = origin_course(self._seg_origins.get(segment_id))
        return seg_course is None or seg_course != self.target[1]

    def armed_arms(self) -> dict[int, dict]:
        """Per-armed-id detail for the view's "waiting for" card (spec
        2026-07-28-multi-step-segments): {segment_id: {progress, total,
        start_frame, deadline_frame}}. `total` is the def's own waypoint
        count (0 for a plain start/end pair), so the UI can read
        "progress of total" without a second lookup. `waiting_for` is NOT
        computed here — that needs card_waiting_for_sentence, a segments.py
        concern, and views.py already holds the def for its section loop.
        A def missing from definition() (deleted mid-arm) is skipped rather
        than raised: this engine's _armed can only ever hold ids from its
        OWN def list, so the guard is defensive, not a live path."""
        arms = {}
        for sid, arm in self._segments.armed_items().items():
            d = self._segments.definition(sid)
            if d is None:
                continue
            arms[sid] = {"progress": arm.progress, "total": len(d.waypoints),
                        "start_frame": arm.start_frame,
                        "deadline_frame": arm.deadline_frame}
        return arms

    def armed_route_id(self):
        return self._runs.armed_route_id()

    def active_route_id(self):
        """The route_id of the most recent route_selected — replay-derived,
        restart-safe (mirrors armed_route_id()). None = no active route."""
        return self._active_route_id

    def finished_runs(self):
        return self._runs.finished_runs()

    def active_run_view(self):
        return self._runs.active_run_view()

    def feed(self, ev) -> list[Attempt]:
        # Usamune's late correction, folded into the grab it revises before
        # anyone reads it (caveat 19). Here rather than at the three payload
        # readers downstream, so no reader can be the one that forgets.
        if ev.type == "star_collected":
            fix = self._time_corrections.get(ev.id)
            if fix:
                ev = _CorrectedRow(ev, {**ev.payload, **fix})
        prev_level = self._level  # _dispatch may move it (level_changed)
        # Snapshotted BEFORE _dispatch (which runs _close_by_grab for a
        # star_collected event) so the grab-steals-the-target restore below
        # can tell "this segment was already my pinned, running focus" from
        # "I just now grabbed a star with nothing else going on" — see that
        # restore's own comment for the bug it exists for.
        target_before = self.target
        armed_before = self.armed_segment_ids()
        closed = self._dispatch(ev)
        for a in closed:
            if a.segment_id is None and a.course_id is not None:
                self._last_star_attempted = (a.course_id, a.star_id)
                if a.outcome == "success":
                    self._last_star_grabbed = (a.course_id, a.star_id)
        if ev.type == "star_collected" and "num_stars" in ev.payload:
            self._num_stars = ev.payload["num_stars"]
        elif ev.type == "game_reset":
            self._num_stars = None  # file can change at the title screen: unknown until the next grab
            self._last_star_grabbed = None
            self._last_star_attempted = None
        if ev.type == "route_selected":
            # Journaled route activation (spec 2026-07-23-default-routes-
            # foundation): the route's member segment ids scope the
            # in_active_route arm guard. Empty list -> None, same "no active
            # route" sentinel as a fresh Projector. Closes no attempt — falls
            # through _dispatch's default no-op.
            ids = ev.payload.get("segment_ids") or []
            self._route_segments = frozenset(ids) if ids else None
            self._active_route_id = ev.payload.get("route_id")
        # A segment target (set via target_set or a segment success) is ALSO
        # in-route by definition — practicing a segment directly must arm it
        # even when no route_selected has fired (or a different route is
        # active), same as the last_star_* guards read _dispatch's just-moved
        # target.
        target_seg = (self.target[1] if self.target
                      and self.target[0] == "segment" else None)
        ctx = MatchContext(level=self._level, prev_level=prev_level,
                           num_stars=self._num_stars, area=self._area,
                           last_star_grabbed=self._last_star_grabbed,
                           last_star_attempted=self._last_star_attempted,
                           route_segments=self._route_segments,
                           target_segment=target_seg)
        seg_closed, self.segment_notices = self._segments.feed(ev, ctx)
        for a in seg_closed:
            # The 100-coin star IS this segment when its def's own sequence
            # includes grabbing that course's 100-coin star (spec 2026-07-28-
            # multi-step-segments, hundred_coin_entity): EVERY outcome —
            # success, death, hard_reset — attributes to the star entity
            # (course_id/star_id, segment_id cleared), not the segment. The
            # segment stops existing as a visible practiced thing at all,
            # only as the timing engine. Reattribute BEFORE strat/cleared/
            # auto_ignored so every downstream rule (validity bounds, strat
            # memory) takes the SAME path an ordinary star_collected closure
            # would — _auto_ignored dispatches purely on
            # `segment_id is not None` (caveat 14).
            seg_def = self._segments.definition(a.segment_id)
            hc = (hundred_coin_entity(seg_def.start_triggers, seg_def.waypoints)
                 if seg_def is not None else None)
            if hc is not None:
                # A segment's own igt_frames is always None -- segments are
                # RTA-only by design (views.py: "segments have no igt
                # clock"). A reattributed attempt IS a star now, and stars
                # display/grade on IGT (his clock: Usamune IGT) -- without
                # this it renders with no time at all and cannot be graded
                # (live report: WF exit-star grab closed BOTH this attempt
                # and the ordinary star_id 3 one on the SAME event, and only
                # the star_id 3 row carried the real igt_frames the game
                # reported). The closing event's own payload is the
                # authoritative source -- exactly what _close_by_grab/
                # _close_by_death already read for an ordinary star, never a
                # value derived from rta_frames (a frame-delta, not the
                # Usamune IGT this project's own rule requires). Not every
                # closing event type carries the key (game_reset closures
                # pass igt_frames=None even on the star side, caveat
                # unaffected) -- .get() falls through to None exactly as the
                # star path already does for those.
                # `timed_by` follows the time it describes, and this row's time
                # just changed source: a star displays/grades on igt_frames, so
                # whatever _close decided about the discarded rta_frames says
                # nothing about it. Reset rather than inherited -- a delta-timed
                # segment closure that reattributes here would otherwise carry a
                # "not comparable" mark onto a number that IS Usamune's IGT.
                # `timed_at` follows the same reasoning as `timed_by` right
                # above: this row IS a star now and its time came out of the
                # closing event's payload, so it takes that event's own
                # answer about WHICH MOMENT -- and None whenever the closure
                # was not a star grab, where the question does not arise.
                a = replace(a, course_id=hc[0], star_id=hc[1], segment_id=None,
                            igt_frames=ev.payload.get("igt_frames"),
                            timed_by="igt",
                            timed_at=(ev.payload.get("igt_timed_at")
                                      if ev.type == "star_collected" else None))
                a = replace(a,
                            strat_tag=self._strat_overrides.get(
                                a.id, self.strat_by_star.get(hc)),
                            cleared=a.id in self._cleared,
                            cleared_reason=self._cleared.get(a.id))
            else:
                # same first-event-id cleared keying as _build (caveat 2/11)
                a = replace(a,
                            strat_tag=self._strat_overrides.get(
                                a.id, self.strat_by_segment.get(a.segment_id)),
                            cleared=a.id in self._cleared,
                            cleared_reason=self._cleared.get(a.id))
            a = self._auto_ignored(a)
            if not a.cleared and hc is not None:
                # last-star memory (caveat 15): the grab-time suppression in
                # _close_by_grab skips this update for the SAME reason a
                # star grab records it, so give it here instead — the
                # physical fact ("this star just closed") must not depend on
                # which code path recorded it.
                self._last_star_attempted = hc
                if a.outcome == "success":
                    self._last_star_grabbed = hc
            if a.outcome == "success" and not a.cleared:
                # A segment that COMPLETES by entering a star stage (the
                # closing event is a level_changed into a course-bearing level)
                # drops us into that stage with NO star picked yet, so there is
                # no active focus at all — clear the target rather than follow
                # onto the just-finished segment (MIPS ends by entering DDD;
                # LBLJ by entering BITDW — 2026-06-12). Completions that do NOT
                # enter a stage (a star grab, a mid-course end, an exit to the
                # hub) still auto-follow onto the segment — or, for a
                # reattributed HUNDRED_COIN_EXIT closure, onto the star it now
                # IS, exactly as a plain star grab auto-follows.
                entered_stage = (ev.type == "level_changed"
                                 and course_for_level(ev.payload["to"]) is not None)
                if hc is not None:
                    self.target = None if entered_stage else ("star", *hc)
                else:
                    self.target = None if entered_stage else ("segment", a.segment_id)
                self._suspended_star = None  # finished a segment: moved on (caveat 13)
            closed.append(a)
        # Caveat 18's grab-steals-the-target restore USED to sit here, and
        # is gone because the steal it undid can no longer happen: since
        # 2026-08-01 `_close_by_grab` simply does not move a SEGMENT
        # target at all. That restore only ever covered waypoint-bearing
        # strict/exclusive defs (Bowser's Reds -> Pipe consuming its own
        # reds star), because for a plain def "still armed after the grab"
        # carried no information about whether the grab belonged to it —
        # which is exactly why the narrow version could not save WF -> SSL
        # or Bowser 1 -> WF. Refusing the steal at the source needs no such
        # disambiguation: a segment target is a click, a grab is not.
        # A segment going ARMED means a segment run just started, so a star we
        # were practicing is no longer the active focus (active-star and
        # segment are mutually exclusive in the UI — 2026-06-12). Clear ONLY a
        # star target; a segment target (set just above on a success) IS the
        # new focus and must stay. service._track auto-broadcasts
        # target_changed off this in-feed change.
        #
        # EXCEPTION, GENERALIZED (spec 2026-07-28-multi-step-segments): a def
        # that arms ambiently on mere course entry (HUNDRED_COIN_EXIT,
        # reds->pipe, the legacy pipe-entry trio — segments.arms_ambiently)
        # is not "a segment run just started" in the sense this rule means,
        # when it is practiced FROM the SAME course the target star lives in
        # — it is that course's engine coming alive, not a different
        # activity. See _clears_star_target's own docstring for why the
        # comparison is by COURSE, not by family or by exact star, and for
        # the live regression this closes (any star 0-5 losing its target on
        # entry to its own course, not just star 6).
        if self.target and self.target[0] == "star" \
                and any(n["event"] == "segment_armed"
                        and self._clears_star_target(n["segment_id"])
                        for n in self.segment_notices):
            self._suspended_star = self.target[1:]  # resume on re-entry (caveat 13)
            self.target = None
        # Run engine sees the same event + the attempts just closed (star AND
        # segment successes/failures); it owns the run lifecycle independently.
        # ctx is the same MatchContext already built for the segment engine.
        self._runs.feed(ev, closed, ctx)
        self.run_notices = self._runs.run_notices
        if ev.type in BOUNDARY_EVENT_TYPES:
            self._rollouts_total = self._rollouts_dustless = 0
            self._jumps_total = self._jumps_dustless = 0
        return closed

    def _dispatch(self, ev) -> list[Attempt]:
        if ev.type in ANCHOR_EVENT_TYPES:
            if ev.payload.get("area_load"):
                # Going DEEPER into the level is not a retry: "if we enter a
                # subarea within a stage, we fundamentally DID NOT RESET…
                # showing a reset there is an error" (2026-08-01). Usamune
                # zeroes its counter on the load exactly as it does on an
                # L-reset, which is why this arrives as an anchor at all;
                # detectors/anchors.py::_is_area_load carries the measurement
                # that tells the two apart. Closes NOTHING, so the run
                # continues across the door and its rta spans the whole star.
                # Still OPENS one if nothing is open — walking into a course
                # and straight into its subarea has to start an attempt
                # somewhere, and this is the last boundary before the grab.
                if self._open is None:
                    self._open = ev
                    self._open_acted = False
                    self._open_castle = self._level in CASTLE_LEVELS
                return []
            closed = self._close_by_reset(ev)
            self._open = ev
            self._open_acted = False
            self._open_castle = self._level in CASTLE_LEVELS
            return closed
        if ev.type == "star_collected":
            return self._close_by_grab(ev)
        if ev.type == "death":
            return self._close_by_death(ev)
        if ev.type == "game_reset":
            return self._close(ev, outcome="hard_reset", igt_frames=None)
        if ev.type == "session_started":
            # A session boundary ends the live FOCUS, not just the open
            # attempt. Live report 2026-08-01: he practiced a 100-coin star,
            # closed the app, reopened it, and the star was still selected —
            # "it reads as a bug if something is selected for a new session."
            # The target is replay-derived, so nothing had ever ended it: the
            # journal's last target_set won however many launches ago it was
            # written. Close FIRST, the same discipline the course-change
            # branch keeps, so the run this boundary ends still attributes to
            # the star it was run on.
            #
            # The suspended star goes too. A star stashed by leaving its
            # course (caveat 13) is still a selection — it is one course entry
            # away from being back on screen, which is exactly the symptom he
            # reported, arriving a few seconds later.
            #
            # Deliberately NOT cleared: strat_by_star / strat_by_segment. The
            # target is where he is pointed right now; which strategy he
            # practices an entity with is a standing preference, and making
            # him re-pick it every launch would be a second annoyance wearing
            # the first one's fix.
            closed = self._close(ev, outcome="abandoned", igt_frames=None)
            self.target = None
            self._suspended_star = None
            return closed
        if ev.type == "level_changed":
            closed = self._close(ev, outcome="abandoned", igt_frames=None)
            to_level = ev.payload["to"]
            # Entering a DIFFERENT course retires a stale active-star target:
            # the practiced star lives in another stage, so it cannot be the
            # active star here (active-star/segment exclusivity — 2026-06-12).
            # Hub levels and the Bowser arenas have no course of their own
            # (course_for_level None) and are NOT a stage, so the target
            # survives an exit-and-re-enter and the retry loop is undisturbed;
            # an exit that lands straight INTO a segment is retired by the
            # arm-clear in feed() instead. Close FIRST (above) so the abandoned
            # run still attributes to the star we were on.
            to_course = course_for_level(to_level)
            # A SEGMENT target is retired by the SAME rule as a star's, one
            # line below: walking into a course that isn't where this one is
            # practiced means you are doing something else now (user report
            # 2026-07-23, Bowser 1 stayed "ACTIVE SEGMENT" after a warp to WF;
            # again 2026-07-27, WF -> SSL in Cool, Cool Mountain). The hubs
            # and the castle are TRANSIT for a segment exactly as they are for
            # a star — every course is entered through them — so they never
            # retire anything.
            #
            # `_seg_origins` is where the segment is practiced FROM
            # (segments.start_origin), not where its start trigger lands you:
            # the old reader was `start_level_set`, built on `arm_level`, and
            # it answered None for 54 of the 65 seeded definitions, so this
            # branch was dead for every castle movement. A def that names no
            # place at all still answers None and still keeps its target.
            # Runs in _dispatch, BEFORE feed()'s close/auto-follow, so a
            # success closed BY this very level_changed still follows onto the
            # segment; the NEXT level move retires it. No resume stash: the
            # matcher re-arms on return (armed pins the UI) and the arena
            # banner re-targets on entry, so nothing is lost.
            #
            # A segment that is ARMED and LOOSE-matched is exempt from this
            # rule (task 5, spec 2026-07-28-multi-step-segments; see
            # _armed_loosely for why the exemption stops at loose and does
            # not cover every armed segment): the two lines above were
            # written for an IDLE pin, where entering a foreign course really
            # does mean "doing something else now". Under loose matching
            # that reasoning breaks down while the segment is RUNNING -- a
            # re-entry movement enters another course on purpose (task
            # 0017's second example), and this rule was hiding the card
            # exactly while the segment was mid-sequence. Still runs in
            # _dispatch, BEFORE feed(), so `_armed_loosely` reads
            # armed_segment_ids() as of the PREVIOUS event, not this one --
            # that is correct, not an off-by-one, for a loose def: loose
            # stays armed through everything but its own end/deadline, so
            # "was armed last event" and "is armed after this one" agree. A
            # segment armed earlier is mid-sequence now, which is exactly
            # the case this exception exists for. The rule is unchanged for
            # anything not currently armed loosely, which is what it was
            # written for.
            if (self.target and self.target[0] == "segment" and to_course is not None
                    and not self._armed_loosely(self.target[1])):
                origin = self._seg_origins.get(self.target[1])
                if origin is not None and origin != stage_origin(to_level):
                    self.target = None
            if self.target and self.target[0] == "star":
                if to_course is not None and to_course != self.target[1]:
                    self._suspended_star = self.target[1:]  # resume on re-entry (caveat 13)
                    self.target = None
            # Resume the star we just stepped out of: re-entering its course
            # with nothing else active reinstates it (caveat 13). Guarded on
            # target is None so an explicit star/segment focus is never
            # overridden; an arm on this same entry re-clears it via feed().
            elif (self.target is None and self._suspended_star is not None
                  and to_course == self._suspended_star[0]):
                self.target = ("star", *self._suspended_star)
                self._suspended_star = None
            self._level = to_level
            # _area is deliberately NOT reset here: every level entry is
            # followed by an establishing area_changed (area.py keys its
            # last-EMITTED state on (level, area)), which keeps _area honest;
            # resetting would only widen the unknown window between the two.
            return closed
        if ev.type == "area_changed":
            # Tracked area for the segment matcher's area-scoped
            # attempt_anchor (segments.py — warp-menu arming 2026-06-12).
            self._area = ev.payload["to"]
            return []
        if ev.type == "target_set":
            self._suspended_star = None  # explicit focus overrides a resume (caveat 13)
            if ev.payload.get("kind") == "segment":
                self.target = ("segment", ev.payload["segment_id"])
            else:  # legacy payloads have no kind: star (caveat 10)
                c, s = ev.payload["course_id"], ev.payload["star_id"]
                self.target = ("star", c, s)
                if "strat_tag" in ev.payload:
                    self.strat_by_star[(c, s)] = ev.payload["strat_tag"]
            return []
        if ev.type == "strat_set":
            # per-target strategy memory write WITHOUT moving the target
            # (target_set is the only other writer); explicit null clears.
            if ev.payload.get("kind") == "segment":
                # A FALSY pick falls back to the definition's default instead
                # of clearing (caveat 17): "no strategy" is not a legitimate
                # choice for a defaulted segment, and that rule belongs in the
                # data — the picker merely hides the blank option, and old
                # journals can still carry a null. Undefaulted segments clear
                # to None exactly as before.
                seg_id = ev.payload["segment_id"]
                self.strat_by_segment[seg_id] = (
                    ev.payload.get("strat_tag")
                    or self._seg_default_strat.get(seg_id))
            else:
                self.strat_by_star[(ev.payload["course_id"],
                                    ev.payload["star_id"])] \
                    = ev.payload.get("strat_tag")
            return []
        if ev.type == "mario_acted":
            self._open_acted = True
            return []
        if ev.type == "rollout":
            self._rollouts_total += 1
            if self._rollout_is_dustless(ev.payload):
                self._rollouts_dustless += 1
            return []
        if ev.type == "jump":
            self._jumps_total += 1
            if ev.payload.get("dustless"):
                self._jumps_dustless += 1
            return []
        return []

    @staticmethod
    def _rollout_is_dustless(p: dict) -> bool:
        """Compat shim (docstring caveat 8): old payloads lack
        landing_frames and misclassified the frame-perfect case —
        frames_late == 1 meant ONE visible slide frame, which is perfect."""
        if "landing_frames" in p:
            return bool(p.get("dustless"))
        return bool(p.get("dustless")) or p.get("frames_late") == 1

    def _unacted_open(self) -> bool:
        """No-behavior rule (spec §2): the open attempt came from an
        acted-tracking anchor and no mario_acted event arrived during it.
        Legacy anchors (no marker) never match — old journals keep their
        original semantics. Stale values while _open is None are harmless:
        the only open-assignment site re-arms the flag."""
        return (self._open is not None
                and self._open.payload.get("acted_tracking", False)
                and not self._open_acted)

    def _open_is_castle(self) -> bool:
        """Castle rule (addendum 2026-06-11): an attempt opened while Mario
        was in a castle hub level is castle movement, never a star attempt.
        _level is None until the first level_changed -> legacy journals
        (no level detector) replay unchanged."""
        return self._open is not None and self._open_castle

    # -- closers -------------------------------------------------------------
    def _close_by_reset(self, ev) -> list[Attempt]:
        if ev.payload.get("paused_frames_before", 0) >= PAUSE_DISCARD_FRAMES:
            # AFK: a long menu pause immediately before the reset — throw the
            # run out (old journals lack the key -> 0 -> kept). The anchor
            # still opens the next attempt.
            self._open = None
            return []
        if self._open_is_castle():
            # castle movement, not a star attempt (addendum): discard
            self._open = None
            return []
        if self._unacted_open() or not ev.payload.get("mario_acted", True):
            # no-op reset spam: the player never acted, so the closed
            # attempt isn't a real attempt — drop it (anchor still opens
            # the next one). Old journals lack the key -> default True.
            self._open = None
            return []
        igt = ev.payload.get("igt_frames_before") if ev.type == "practice_reset" else None
        return self._close(ev, outcome="reset", igt_frames=igt)

    def _close_by_grab(self, ev) -> list[Attempt]:
        grabbed = (ev.payload["course_id"], ev.payload["star_id"])
        first = self._open if self._open is not None else ev
        if grabbed[1] == 6 and grabbed in self._hundred_coin_engines:
            # The 100-coin star never gets its OWN attempt when an engine is
            # timing the whole course visit (spec 2026-07-28-multi-step-
            # segments): its eventual completion (at the exit star) is
            # reattributed to this same entity in feed()'s seg_closed loop
            # instead. "Nobody times just the 100 star grab" (user ruling,
            # the retired star->segment TARGET redirect's own reasoning)
            # now applies to ATTRIBUTION too, not just target-picking.
            # _last_star_grabbed/_last_star_attempted still update directly
            # here (caveat 15: "the grab happened physically" even when no
            # attempt records it) -- no seeded guard reads star 6 today, but
            # a future one should see this exactly as it would any other
            # grab. Falls back to the plain star attempt below when no
            # engine covers this course (deleted/disabled def), same
            # fallback _hundred_coin_redirect used, so the click stays
            # meaningful.
            self._last_star_attempted = grabbed
            self._last_star_grabbed = grabbed
            self._open = None
            return []
        strat = self.strat_by_star.get(grabbed)
        attempt = self._build(
            first=first, close=ev, outcome="success", outcome_detail=None,
            course_id=grabbed[0], star_id=grabbed[1],
            igt_frames=ev.payload.get("igt_frames"), strat=strat)
        self._open = None
        if not attempt.cleared and not self._segment_targeted():
            # The last VALID grab moves the practice target — but only when
            # the target is a star or nothing. A SEGMENT target is a pick he
            # made with a click; a grab is a thing he did in the game, and
            # letting the second overwrite the first is how a movement he
            # deliberately chose disappears (live report 2026-08-01, WF -> SSL
            # and Bowser 1 -> WF). Grabbing the star you are leaving a course
            # with is the ORDINARY thing to do immediately before running that
            # course's exit movement, so this fired at exactly the wrong
            # moment; and because every castle movement is guarded to the
            # target or the active route, losing the target also cost it its
            # arm, its section and its card in one go.
            #
            # The star's own attempt is still recorded above — his ruling on
            # this same rule, 2026-07-30: "the practice log should still exist
            # for star mode in the history, just that we don't see it".
            self.target = ("star", *grabbed)
            self._suspended_star = None  # committed a new focus (caveat 13)
        return [attempt]

    def _segment_targeted(self) -> bool:
        return self.target is not None and self.target[0] == "segment"

    def _close_by_death(self, ev) -> list[Attempt]:
        if self._unacted_open():
            self._open = None
            return []
        if self._open_is_castle():
            # castle movement, not a star attempt (addendum): discard
            self._open = None
            return []
        # Deaths count even without an anchor (mirrors grab-only synthesis):
        # a death is always a meaningful failed attempt.
        first = self._open if self._open is not None else ev
        star_tgt = self._star_target()
        course_id, star_id = star_tgt if star_tgt else (None, None)
        strat = self.strat_by_star.get(star_tgt) if star_tgt else None
        attempt = self._build(
            first=first, close=ev, outcome="death",
            outcome_detail=ev.payload.get("cause"),
            course_id=course_id, star_id=star_id,
            igt_frames=ev.payload.get("igt_frames"), strat=strat)
        self._open = None
        return [attempt]

    def _close(self, ev, outcome: str, igt_frames: int | None) -> list[Attempt]:
        if self._open is None:
            return []
        if self._unacted_open():
            self._open = None
            return []
        if self._open_is_castle():
            # castle movement, not a star attempt (addendum): discard
            self._open = None
            return []
        star_tgt = self._star_target()
        course_id, star_id = star_tgt if star_tgt else (None, None)
        strat = self.strat_by_star.get(star_tgt) if star_tgt else None
        attempt = self._build(
            first=self._open, close=ev, outcome=outcome, outcome_detail=None,
            course_id=course_id, star_id=star_id,
            igt_frames=igt_frames, strat=strat)
        self._open = None
        return [attempt]

    def _build(self, first, close, outcome, outcome_detail, course_id, star_id,
               igt_frames, strat) -> Attempt:
        is_anchored = first.type in ANCHOR_EVENT_TYPES
        timed_at = (close.payload.get("igt_timed_at")
                    if close.type == "star_collected" else None)
        rta = (close.frame - first.frame
               if is_anchored and close.frame >= first.frame else None)
        return self._auto_ignored(Attempt(
            id=first.id, session_id=first.session_id,
            course_id=course_id, star_id=star_id,
            strat_tag=self._strat_overrides.get(first.id, strat),
            anchor_type=first.type if is_anchored else "none",
            anchor_frame=first.frame if is_anchored else None,
            outcome=outcome,
            outcome_detail=outcome_detail,
            igt_frames=igt_frames, rta_frames=rta,
            started_utc=first.wall_time_utc, ended_utc=close.wall_time_utc,
            cleared=first.id in self._cleared,
            cleared_reason=self._cleared.get(first.id),
            rollouts_total=self._rollouts_total,
            rollouts_dustless=self._rollouts_dustless,
            jumps_total=self._jumps_total,
            jumps_dustless=self._jumps_dustless,
            timed_at=timed_at))

    def _auto_ignored(self, a: Attempt) -> Attempt:
        """Range/validity check (spec 2026-07-23): an out-of-bounds SUCCESS
        is auto-cleared with an "auto: " reason — excluded everywhere cleared
        is (stats, rates, PBs, graphs, runs) but still visible in the hidden
        bucket. Manual clear/restore history exempts the id entirely; only
        successes are judged (a fast reset is legitimate practice). Stars are
        judged on igt (rta fallback), segments on rta; no clock -> no flag."""
        if a.outcome != "success" or a.cleared or a.id in self._touched:
            return a
        if a.segment_id is not None:
            lo, hi = self._seg_bounds.get(a.segment_id, (None, None))
            frames = a.rta_frames
        else:
            f = self._time_filters.get(f"{a.course_id}:{a.star_id}", {})
            lo, hi = f.get("min_frames"), f.get("max_frames")
            frames = a.igt_frames if a.igt_frames is not None else a.rta_frames
        if lo is None:
            lo = DEFAULT_MIN_FRAMES
        if frames is None:
            return a
        if frames < lo:
            return replace(a, cleared=True,
                           cleared_reason=f"auto: below {lo / 30:.2f}s min")
        if hi is not None and frames > hi:
            return replace(a, cleared=True,
                           cleared_reason=f"auto: above {hi / 30:.2f}s max")
        return a


def wipe_matches(a: Attempt, p: dict) -> bool:
    """data_wiped scope filter — shared by replay() (attempt suppression)
    and service.wipe_data (collecting pb rows to delete), so the two can
    never disagree about what a wipe covers. session_id None = every
    session; kind "all" = every attempt in the session scope (including
    unassigned rows, which belong to no star)."""
    if p.get("session_id") is not None and a.session_id != p["session_id"]:
        return False
    if p["kind"] == "star":
        return (a.segment_id is None and a.course_id == p["course_id"]
                and a.star_id == p["star_id"])
    if p["kind"] == "segment":
        return a.segment_id == p["segment_id"]
    return True  # kind == "all"


def replay(events, segments=None, time_filters=None,
           origin_overrides=None, on_notices=None) -> tuple[list[Attempt], Projector]:
    """`on_notices`, default None (spec 2026-07-28-multi-step-segments, the
    backtest arm-count gap): an optional callback invoked with
    `proj.segment_notices` after every fed event, for a caller that needs
    more than the final-state snapshot `Projector` exposes.

    `Projector.segment_notices` is OVERWRITTEN every `feed()` call — it is a
    live-broadcast queue `service.py` drains once per tick, not a log — so a
    replay caller that wants the full history (e.g. every `segment_armed`
    across the whole journal, not just the last event's) has no other way to
    see it after the fact. Rather than have `replay()` itself accumulate
    (this is the LIVE projection path too — service.py's own re-projection
    calls it, and widening its return value would ripple into every
    existing caller) or give the caller a second event loop that duplicates
    the `data_wiped` retroactive-suppression branch below, this is the one
    seam: a caller passes a callback, sees every event's notices exactly
    once, and every existing caller (on_notices=None) is byte-for-byte
    unaffected. `tracking/backtest.py::_run` is the one consumer so far,
    counting `segment_armed` notices to distinguish "never armed" from
    "armed and never closed" — two candidates `unclosed` alone cannot tell
    apart, since it only reports what's STILL armed at the journal's end."""
    proj = Projector(cleared_ids(events), segments=segments,
                     time_filters=time_filters, touched=touched_ids(events),
                     strat_overrides=strat_overrides(events),
                     origin_overrides=origin_overrides,
                     time_corrections=time_corrections(events))
    attempts: list[Attempt] = []
    for ev in events:
        if ev.type == "data_wiped":
            # Retroactive suppression (the wipe analogue of attempt_cleared):
            # drop everything accumulated so far that falls in scope; attempts
            # closed AFTER the wipe accumulate fresh. The event never reaches
            # the projector — an in-flight open attempt deliberately survives
            # (it closes after the wipe, so it is post-wipe data). Never
            # reaches proj.feed(), so on_notices is correctly never called
            # for it either -- there are no notices to report.
            attempts = [a for a in attempts if not wipe_matches(a, ev.payload)]
            continue
        if ev.type == PRUNE_EVENT:
            # The startup prune of unlabelled attempts (tracking/prune.py),
            # applied the same retroactive way a wipe is — and for the same
            # reason it never reaches proj.feed(): an attempt still open when
            # the prune ran closes afterwards and is post-prune data.
            #
            # Carries EXPLICIT ids rather than a rule to re-evaluate, so a
            # past prune means the same thing forever. The rule reads the
            # `pbs` table and the saved-clip directory to decide what is
            # protected; both change under us, so re-deriving here would let
            # deleting a PB row today silently widen a prune that happened
            # last week — and there would be no record that it had.
            pruned = set(ev.payload.get("attempt_ids", ()))
            attempts = [a for a in attempts if a.id not in pruned]
            continue
        attempts.extend(proj.feed(ev))
        if on_notices is not None:
            on_notices(proj.segment_notices)
    return attempts, proj


def project(events, segments=None, time_filters=None,
            origin_overrides=None) -> list[Attempt]:
    return replay(events, segments=segments, time_filters=time_filters,
                  origin_overrides=origin_overrides)[0]


def journal_id(attempt_id: int) -> int:
    """Recency-comparable id across kinds: segment attempt ids carry a
    namespace offset (segments.SEGMENT_ATTEMPT_OFFSET); strip it."""
    return attempt_id % SEGMENT_ATTEMPT_OFFSET
