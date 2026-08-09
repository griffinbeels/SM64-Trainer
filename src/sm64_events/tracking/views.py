"""Builds the GET /api/session payload.

Contract (the UI builds against ALL of this):
- `scope` selects which attempts drive sections/attempt lists/unassigned:
  "session" (default) = the active session, "lifetime" = everything.
  Stat chips ALWAYS compute over lifetime history (spec §8). The timeline
  FOLLOWS scope (session view plots only that session's attempts — user
  request 2026-07-24; it originally stayed lifetime per spec §8).
- Star sections are ordered newest-activity-first (max scoped journal
  recency via projection.journal_id; fresh targets sort last); segment
  sections order among themselves the same way. Every section ALSO carries
  `last_activity` (that same journal recency, -1 for fresh) so the UI can
  interleave the two arrays into one recency-ordered list.
- The practice target's section is ALWAYS present, even with zero scoped
  attempts — the UI pins it as the active block (star AND segment kinds).
  ARMED segments are pinned the same way: active now => section present.
  **Exception, 2026-08-05: an AMBIENTLY-arming def (segments.arms_ambiently —
  the 100-coin star's own engine, seg:reds->pipe:*, the legacy pipe-entry
  trio) is exempt from "armed alone is enough."** Its arm is mere
  course/subarea presence, not a deliberate action, so a mere-armed-and-
  unchosen instance gets NO section — the same "not evidence of a deliberate
  attempt" premise `tracking/projection.py`'s `_untargeted_failure`/
  `_untargeted_ambient_failure` apply to the ROW those same engines would
  otherwise duplicate. It still gets one the instant it IS the live target
  (the ordinary target-pin rule above), checked per definition, never per
  course — two ambient defs can arm off the same course entry with only one
  of them chosen (live report: 8 Red Coins (Pipe) selected beside an unchosen,
  still-visible "No Reds" card in the same Bowser stage).
- Sections carry `markers_by_strat` (spec §3) and `progress` (spec §4,
  scoped successes grouped per session).
- Segment sections (`segments` key) mirror star sections but are RTA-only
  (segments have no IGT): pb / attempts / stats / timeline / progress all
  read rta_frames whatever the view clock. Marker keys: 'seg:<id>:<strat>'.
- `target` is kind-aware: service.target_payload() identity + display
  names, every key present for both kinds (shape stability).
- Every star AND segment section carries `time_filter: {min_frames,
  max_frames, is_default}` — effective validity bounds after the implicit
  0.5 s default is filled in; drives the header's `⏱` chip.
- Segment sections also carry `category` (a route/segment grouping label,
  or None) and `seeded` (bool — a bundled default, editable via reset-to-
  default; see tracking/defaults.py). Star sections omit both: stars are
  neither seeded nor categorized. `build_route_view`'s payload carries the
  same two keys for the route itself.
- `active_route` ({id, name, segment_ids} or None) mirrors the route-scoped
  arm from service.active_route() — the projector's journal-derived
  active_route_id(), never a service-only field (see select_route)."""
from sm64_events.core.timefmt import format_igt
from sm64_events.links import star_links
from sm64_events.memory.addresses import (COURSE_BY_LEVEL, COURSE_NAMES,
                                          course_name, star_count, star_name)
from sm64_events.ranks import classify
from sm64_events.ranks import scoring
from sm64_events.ranks.standards import entity_key
from sm64_events.stats.registry import (DEFAULT_STAT_MENU, REGISTRY,
                                        compute_stat, selection_id,
                                        selection_order)
from sm64_events.tracking.projection import DEFAULT_MIN_FRAMES, journal_id
from sm64_events.tracking.routes import route_stats
from sm64_events.tracking.caveats import (attempt_caveat, caveat_for,
                                           igt_seen_in,
                                          pb_blocked_by)
from sm64_events.tracking.segments import (arm_level, arms_ambiently,
                                            card_step_labels,
                                            card_waiting_for_sentence,
                                            course_groups, hundred_coin_entity,
                                            origin_course,
                                            origin_view, segment_origin,
                                            start_areas, start_levels,
                                            start_origin, time_bounds)

# Timeline markers (per-section event graph): outcomes that plot as points.
# Adding a marker kind is one row here (+ a style row in ui timeline.js).
# The frame position comes from the section's clock extractor: star
# sections pass igt (resets/deaths only have an IGT position), segment
# sections pass rta (segments have no IGT).
TIMELINE_OUTCOMES = frozenset({"success", "reset", "death"})


def _timeline(history, frames_of) -> dict | None:
    """X axis 0 -> longest SUCCESSFUL attempt; every qualifying attempt is
    a point at its frames_of(a) position. Points may exceed max_frames (a
    reset later than the best success) — the UI extends the axis as needed.

    The axis ends at the longest success when one exists, otherwise at the
    rightmost point; max_is_success=False lets the UI render a provisional
    axis until a success lands. Each point's display string keeps the "igt"
    key whatever the clock (UI contract — it is just formatted frames)."""
    points = []
    for a in history:
        if a.cleared or a.outcome not in TIMELINE_OUTCOMES:
            continue
        frames = frames_of(a)
        if frames is None:
            continue
        points.append({"frames": frames, "igt": format_igt(frames),
                       "outcome": a.outcome, "attempt_id": a.id})
    if not points:
        return None
    succ = [p["frames"] for p in points if p["outcome"] == "success"]
    max_frames = max(succ) if succ else max(p["frames"] for p in points)
    return {"max_frames": max_frames, "max_display": format_igt(max_frames),
            "max_is_success": bool(succ), "points": points}


def _fmt(value, fmt):
    if value is None:
        return None
    if fmt == "time":
        return format_igt(round(value))
    if fmt == "percent":
        return f"{round(value * 100)}%"
    return str(value)


def _current_pbs(pb_rows: list[dict]) -> dict:
    """Latest pb row per kind-aware key: ("segment", segment_id, mode) for
    segment rows, (course_id, star_id, mode) for star rows. Without the
    kind tag every segment pb collapses onto (None, None, "rta") and the
    newest segment's save shadows all the others (live bug, Task 12).

    This is the STRATEGY-BLIND "overall best PB" — for DISPLAY (sec.pb,
    pb_delta, is_current_pb) only. It must NEVER drive a rank; ranks use the
    per-strategy map below so a faster PB on one strat can't rank another."""
    out = {}
    for row in pb_rows:  # ordered by id: later rows win
        key = (("segment", row["segment_id"], row["timer_mode"])
               if row["segment_id"] is not None
               else (row["course_id"], row["star_id"], row["timer_mode"]))
        out[key] = row
    return out


def current_pbs_by_strat(pb_rows: list[dict]) -> dict:
    """Latest pb row keyed like _current_pbs but with the saving attempt's
    strat_tag appended. THE per-strategy ranking lookup: a strategy is ranked
    ONLY by times achieved WITH that strategy. PBs with no strat_tag can't be
    attributed to a strategy and are skipped (the entity stays unranked on
    every strat until a strat-tagged PB lands).

    PUBLIC for the same reason `grading_basis` is: `tracking/marelo.py` grades
    the identical thing in batch, and there is exactly ONE answer to "which of
    my saved times counts". It went through `min()` over raw attempts until
    2026-07-28, which paid MARELO out before the user clicked Save as PB
    (task 0034)."""
    out = {}
    for row in pb_rows:  # ordered by id: later rows win
        strat = row["strat_tag"]
        if not strat:
            continue
        key = (("segment", row["segment_id"], row["timer_mode"], strat)
               if row["segment_id"] is not None
               else (row["course_id"], row["star_id"], row["timer_mode"], strat))
        out[key] = row
    return out


def _attempt_json(a, pbs, clock, ranks=None, rank_clock=None, rank_ek=None):
    pb = pbs.get(("segment", a.segment_id, clock) if a.segment_id is not None
                 else (a.course_id, a.star_id, clock))
    frames = a.igt_frames if clock == "igt" else a.rta_frames
    race_row = clock == "rta" and frames == 0  # same-tick reset-race: rta is junk (see projection.py docstring)
    delta = (frames - pb["frames"]
             if pb and frames is not None and not race_row and a.outcome == "success"
             else None)
    # The medal grades on the LADDER's own clock, never the view clock --
    # same reasoning as the section banner's basis two callers up (I1, final
    # review 2026-07-26): an rta time systematically under-ranks against an
    # igt-defined ladder, so before this fix a Diamond V banner could sit
    # above the very attempt that earned it wearing a Platinum cap. Falls
    # back to the view clock when the caller has none to defer to (unassigned
    # attempts have no known entity, so the ladder lookup inside
    # _attempt_rank never matches anything regardless of clock). The
    # DISPLAYED frames/pb_delta above are unaffected -- that stays the view
    # clock, a display choice.
    rank_clock = clock if rank_clock is None else rank_clock
    rank_frames = a.igt_frames if rank_clock == "igt" else a.rta_frames
    return {"id": a.id,
            # Recency-comparable across BOTH id namespaces (spec 2026-07-28-
            # multi-step-segments, live report): a reattributed 100-coin
            # attempt keeps its SEGMENT-namespace `id` (caveat 2/11), a huge
            # number next to a native star attempt's plain journal id, so
            # sorting rows by raw `id` (practice.js's "newest"/"oldest"
            # comparator) stuck two real successes at the top of the
            # practice log forever while newer resets piled up underneath.
            # `journal_id()` is the SAME resolver views.py already used for
            # SEGMENT SECTION recency -- stamped per-attempt here so the
            # client sorts by it instead of `id` directly.
            "journal_id": journal_id(a.id),
            "outcome": a.outcome, "outcome_detail": a.outcome_detail,
            "anchor_type": a.anchor_type, "strat_tag": a.strat_tag,
            "igt_frames": a.igt_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta_frames": a.rta_frames,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "pb_delta_frames": delta, "cleared": a.cleared,
            # this attempt owns the CURRENT pb row on this clock — drives
            # the Save-as-PB / Undo-PB button swap (undo deletes that row)
            "is_current_pb": bool(pb) and pb["attempt_id"] == a.id,
            # Why this row may NOT be saved as a PB (a caveats.py KEY, or
            # None) — the same predicate save_pb refuses on, so the button
            # cannot offer what the server would reject. A key rather than a
            # sentence: the browser already owns the wording for each one
            # (ui/components/marks.js), and shipping prose here would be a
            # second vocabulary for the same fact.
            "pb_blocked_by": pb_blocked_by(a),
            # "the number printed on this row is not the quantity you think
            # you were practising", as a caveats.py KEY or None — the mark the
            # practice log draws beside the time itself. Separate from
            # `pb_blocked_by` because it answers a different question and
            # survives where that one is not drawn: a row already saved as a
            # PB shows Undo instead of a blocked button, and a cleared row
            # shows no button at all, but both still carry a time that
            # measures the wrong moment.
            "caveat": attempt_caveat(a),
            "cleared_reason": a.cleared_reason,
            "started_utc": a.started_utc, "ended_utc": a.ended_utc,
            "rollouts_total": a.rollouts_total,
            "rollouts_dustless": a.rollouts_dustless,
            "jumps_total": a.jumps_total,
            "jumps_dustless": a.jumps_dustless,
            "rank": _attempt_rank(a, rank_frames, ranks, rank_ek),
            "segment_id": a.segment_id}


def _catalog() -> dict:
    courses = []
    for cid, cname in COURSE_NAMES.items():
        # max(..., 1): the catalog always shows at least one star row even
        # for course 0 (display fallback); the count itself lives in
        # addresses.star_count
        n = max(star_count(cid), 1)
        courses.append({"id": cid, "name": cname,
                        "stars": [star_name(cid, s) for s in range(n)]})
    # The SAME grouping vocab ships, so a catalog-driven picker (the practice
    # target modal, the route step editor) files a star under the same castle
    # region a vocab-driven one does (the segment builder). Pure and cheap, so
    # computing it at import with the rest of the catalog is fine — but it must
    # never grow a database dependency.
    return {"courses": courses, "course_groups": course_groups()}


_CATALOG = _catalog()


def _strategies_for(registered: dict, attempts, course_id: int, star_id: int,
                    ranks=None, deleted=()) -> list[str]:
    """Registered strategies (ui_state) merged with every strat ever used
    on this star's attempts, plus any strategies defined in rank standards —
    union preserves registration order first, then observed, then standard.
    `deleted` (the star's deleted_strats tombstone list) is filtered out last —
    the observed-on-attempts union source is journal-derived and can't itself
    be deleted, so this is where a tombstoned name stops surfacing."""
    out = list(registered.get(f"{course_id}:{star_id}", []))
    for a in attempts:
        if (a.course_id, a.star_id) == (course_id, star_id) \
                and a.strat_tag and a.strat_tag not in out:
            out.append(a.strat_tag)
    if ranks is not None:
        for strat in ranks.strategies(entity_key(course_id, star_id)):
            if strat not in out:
                out.append(strat)
    return [s for s in out if s not in deleted]


def _seg_strategies(registered: dict, history, seg_id: int, ranks=None,
                    deleted=(), default_strat=None, standards_ek=None,
                    family_suffix=None) -> list:
    """Segment sibling of _strategies_for: the definition's own default_strat
    first, then registered strats (ui_state, keyed "seg:{id}" — see
    service._strategies_key), then observed-on-attempts (sorted), then
    rank-standard strats. Registration is what keeps a just-picked strategy in
    the dropdown before any attempt exists under it; the default is here for
    the same reason, one step earlier — it is never journaled, so nothing else
    would put it in the list on a segment that has never been run.
    `deleted` filters the segment's tombstoned names, same as _strategies_for;
    a default can never be among them — service.purge_strategy refuses to
    delete one, the same protection community strats get.

    `standards_ek`/`family_suffix` are the Bowser reds/pipe pairing's escape
    hatch: `seg:reds->pipe:<abbrev>` has no rank-standards entity of its own
    (the ladder lives on the paired star, `_reds_pipe_segments`) — passing
    the star's entity_key here pulls candidate names from THERE instead of
    this segment's own (empty) list, then `family_suffix` keeps only the
    " (Pipe)"-suffixed half so the segment never offers a Star-family name."""
    out = [default_strat] if default_strat else []
    for strat in registered.get(f"seg:{seg_id}", []):
        if strat not in out:
            out.append(strat)
    for strat in sorted({a.strat_tag for a in history if a.strat_tag}):
        if strat not in out:
            out.append(strat)
    if ranks is not None:
        names = ranks.strategies(standards_ek or entity_key(None, None, seg_id))
        if family_suffix:
            names = [n for n in names if n.endswith(family_suffix)]
        for strat in names:
            if strat not in out:
                out.append(strat)
    return [s for s in out if s not in deleted]


def _attempt_rank(a, frames, ranks, ek=None) -> dict | None:
    """{"rank", "division"} for one attempt's own medal, or None when
    ungradeable. Routed through `_graded_progress` -- the SAME curve
    `_section_banner` grades through -- rather than a second computation
    (`classify.rank_for` used to answer this directly, off the raw ladder,
    with no division of its own; addendum, task 8, 2026-07-26: an attempt
    medal must never disagree with the section banner about which division a
    tier is at).

    `ek` overrides the entity derived from the attempt's own identity -- the
    Bowser reds/pipe pairing grades a `seg:reds->pipe:<abbrev>` attempt
    against the paired STAR's ladder (`_reds_pipe_segments`), never the
    segment's own (nonexistent) one, so a per-row medal can't disagree with
    that section's banner."""
    if ranks is None or frames is None or a.outcome != "success" or not a.strat_tag:
        return None
    ek = ek or entity_key(a.course_id, a.star_id, a.segment_id)
    ladder = ranks.ladder_cs(ek, a.strat_tag)
    if not ladder:
        return None
    progress = _graded_progress(ladder, classify.display_cs(frames))
    return {"rank": progress["rank"], "division": progress["division"]}


def valid_frames(history, strat, clock) -> list[int]:
    """Chronological times (frames) of the runs that count toward an average
    (average rank mode spec): successful, not cleared (manual purge or
    auto-ignore), achieved WITH `strat`, with a real time on `clock` —
    excluding the rta==0 reset-race junk rows (projection.py docstring).
    `history` is journal-id ordered, so the list is chronological."""
    out = []
    for a in history:
        if a.outcome != "success" or a.cleared or a.strat_tag != strat:
            continue
        frames = a.igt_frames if clock == "igt" else a.rta_frames
        if frames is None or (clock == "rta" and frames == 0):
            continue
        out.append(frames)
    return out


def grading_basis(mode, pb, history, strat, clock) -> dict | None:
    """THE one 'which time does this rank grade?' resolver. Returns
    {"frames", "count", "window"} or None when nothing is gradeable.
    'pb' mode wraps the saved per-strategy PB row (count 1) — byte-for-byte
    today's grading; avg modes grade attempt history via classify.average_frames,
    so a run never saved as PB still counts.

    Public because MARELO grades the same basis (tracking/marelo.py): there is
    exactly ONE answer to "which of my times counts", and it lives here."""
    mode_def = classify.RANK_MODES.get(mode) or classify.RANK_MODES["pb"]
    if mode_def["order"] is None:
        return ({"frames": pb["frames"], "count": 1, "window": None}
                if pb else None)
    averaged = classify.average_frames(valid_frames(history, strat, clock),
                                       mode_def["window"], mode_def["order"])
    if averaged is None:
        return None
    mean_frames, count = averaged
    return {"frames": mean_frames, "count": count,
            "window": mode_def["window"]}


def _strat_rank(ranks, ek, strat, basis) -> dict | None:
    """{"rank", "division"} for an entity graded under `strat` at its grading
    basis (grading_basis output: PB row in pb mode, mean of valid runs in avg
    modes), or None when ungradeable (no ranks loaded, no active strat, no
    basis, or the strat has no ladder). THE single grading path shared by
    route candidates and the stage quick-select star grid (view's
    rank_by_star) — keep it one place so a medal never disagrees with the
    section banner / attempt medals.

    Routed through `_graded_progress` (addendum, task 8, 2026-07-26) rather
    than a second computation: this used to call `classify.rank_for` directly
    against the raw ladder, which answers the tier alone via threshold-
    crossing and has no division of its own -- so every consumer of this
    function's payload (rank_by_star, segment_targets, route candidates) drew
    an icon with no division to show, even though `_graded_progress` already
    computes exactly that division and already feeds the section banner.
    `_graded_progress`'s own tier answer is provably identical to
    `classify.rank_for`'s (both are threshold-crossings against the same
    ladder cutoffs; `_graded_progress` merely also carries the division),
    so this is not a behavior change to the tier itself."""
    if ranks is None or not strat or basis is None:
        return None
    ladder = ranks.ladder_cs(ek, strat)
    if not ladder:
        return None
    progress = _graded_progress(ladder, classify.display_cs(basis["frames"]))
    return {"rank": progress["rank"], "division": progress["division"]}


def _graded_progress(ladder: dict, time_cs: int) -> dict:
    """THE one place a section banner or entity rank turns a ladder + a
    graded time into 'what tier/division, how close within it, what's the
    next step, and how many centiseconds that costs' — chains
    score_for -> scoring.division_progress -> scoring.time_for_score so
    both callers compute this identically and neither touches the curve
    directly. `ladder` is already in centiseconds (ranks.ladder_cs's /
    scoring.best_ladder's shape); never called with an empty one (both
    callers guard first, so score_for here always returns a real float).

    `next_gap_cs` is the division-aware sibling of the OLD whole-tier
    `gap_cs` classify.band used to carry (spec 2026-07-25 round 3: the user
    asked for the time delta back once the bar itself became
    division-scoped) — None exactly when `next_tier` is None (maxed, no
    step to chase), and never 0 (`scoring.progress_for_time` owns why).

    This is an ADAPTER now, not a computation: the chain moved into
    `scoring.progress_for_time` when the displayed-centisecond boundary rule
    landed (2026-07-29), because that rule is knowledge about the curve's own
    rounding and belongs beside `time_for_score`, which does the rounding.
    All this still owns is the `rank` key — every consumer of these payloads
    calls a tier a rank."""
    progress = scoring.progress_for_time(ladder, time_cs)
    return {"score": progress["score"], "rank": progress["tier"],
            "division": progress["division"], "fill": progress["fill"],
            "next_tier": progress["next_tier"],
            "next_division": progress["next_division"],
            "next_gap_cs": progress["next_gap_cs"]}


def _fastest_strategy(ranks, ek, best_ladder_cs: dict) -> str | None:
    """Which strategy OWNS the entity's best-possible ladder (best_ladder_cs
    -- the pointwise minimum entity_rank already computed): the answer to
    'why is my star's rank lower than my strategy's rank'.

    Walks the ladder's own tiers hardest-first; at each tier, narrows the
    candidate strategies to the ones whose OWN ladder matches the pointwise
    minimum there -- the strategies that actually SET that entry. This stays
    well-defined on ragged ladders (a strategy missing a tier just can't win
    at it) and self-resolves ties by moving to the next-hardest tier both
    still define. A strategy tied at every shared tier is broken
    alphabetically -- arbitrary, but deterministic rather than dict-order
    luck."""
    candidates = ranks.strategies(ek)
    for tier in scoring.defined_tiers(best_ladder_cs):
        matching = [strat for strat in candidates
                   if ranks.ladder_cs(ek, strat).get(tier) == best_ladder_cs[tier]]
        if matching:
            candidates = matching
        if len(candidates) == 1:
            return candidates[0]
    return min(candidates, key=str) if candidates else None


def entity_rank(ranks, ek, frames) -> dict | None:
    """The star/segment's OWN rank: the time graded against the entity's
    best-possible ladder (pointwise best across every strategy) rather than
    the active strategy's. THE number MARELO aggregates.

    This is why a mastered slow strategy reads Mario on one banner and
    honestly less on the other: the strategy banner asks 'how well do I run
    THIS strat', this one asks 'how close is this to the fastest this star
    can be'. None when the entity has no standards or there is no time to
    grade (the banner is simply not rendered — no sentinel wording, unlike
    `_section_banner`; keeping this a plain None/dict contract, not a
    sentinel one, was a deliberate choice on 2026-07-25 round 2 rather than
    inventing wording nobody asked for).

    Shape deliberately mirrors `_section_banner`'s graded output — SAME
    fields (`_graded_progress`), so the UI renders both through the SAME
    ui/components/ranks.js RankBanner, side by side, with different data.
    Spec 2026-07-25 round 3: the user asked for the two banners to be
    genuinely interchangeable — same gradient, same bar, same `next:` line
    with its time delta — after round 2 still left this one looking like a
    lesser chip beside a full banner ("it feels like it's just a visual
    error"). Also carries `fastest_strat` (_fastest_strategy) — the strategy
    that actually sets this best-possible ladder — so the UI can explain a
    low entity rank next to a high strategy rank ("Iron I · fastest here is
    Sign Clip") instead of leaving the two numbers to look like a
    contradiction (live user report 2026-07-25)."""
    if ranks is None or frames is None:
        return None
    ladder = scoring.best_ladder(ranks.ladders(ek))
    if not ladder:
        return None
    progress = _graded_progress(ladder, classify.display_cs(frames))
    return {**progress, "score": round(progress["score"], 1),
            "fastest_strat": _fastest_strategy(ranks, ek, ladder)}


def ranks_share_ladder(ranks, ek, strat) -> bool:
    """Whether the ACTIVE strategy's ladder IS the entity's best-possible one.

    When it is, the strategy rank and the entity's own rank are not two
    measures that happen to agree - they are ONE measure, and the UI draws a
    single banner labelled for both (live report 2026-07-25: a lone banner
    labelled "STRATEGY" read as the star rank failing to load).

    Answered from the LADDERS, never from the two graded values, and that is
    the point: it is stable. Two genuinely different ladders that happen to
    grade today's time into the same tier stay two banners instead of merging
    and splitting again on the next run, and a strategy with no time yet still
    knows which case it is - which is what lets BOTH banners render at the
    Capless V default rather than the entity's appearing out of nowhere the
    moment a first time lands (live report 2026-07-27).
    """
    if ranks is None or not strat:
        return False
    ladder = ranks.ladder_cs(ek, strat)
    return bool(ladder) and ladder == scoring.best_ladder(ranks.ladders(ek))


def _best_strategy_graded(ranks, ek, history, pbs_by_strat, rank_mode,
                          deleted, pb_key_prefix, strategies=None,
                          clock=None) -> tuple[str, dict] | None:
    """The (strategy, _graded_progress) pair with the HIGHEST score among
    `ranks.strategies(ek)`, skipping tombstoned names (`deleted`) and
    strategies with no ladder or nothing gradeable. Ties break on
    min(strat) -- same deterministic convention `_fastest_strategy` uses,
    order-independent (a later tie only overwrites the running best when its
    name sorts earlier, so iteration order never decides the winner).
    `pb_key_prefix` is `(course_id, star_id)` or `("segment", segment_id)` --
    `current_pbs_by_strat`'s key shape minus (clock, strat).

    `strategies`/`clock` override the entity's own `ranks.strategies(ek)` /
    `ranks.clock_for(ek)` -- the Bowser reds/pipe toggle grades a
    family-filtered subset of the STAR's ladders against the PAIRED
    segment's own (rta) history this way, reusing this loop rather than a
    second one (`_reds_pipe_segments`)."""
    if clock is None:
        clock = ranks.clock_for(ek)
    best: tuple[str, dict] | None = None
    for strat in (strategies if strategies is not None else ranks.strategies(ek)):
        if strat in deleted:
            continue
        ladder = ranks.ladder_cs(ek, strat)
        if not ladder:
            continue
        basis = grading_basis(
            rank_mode, pbs_by_strat.get((*pb_key_prefix, clock, strat)),
            history, strat, clock)
        if basis is None:
            continue
        graded = _graded_progress(ladder, classify.display_cs(basis["frames"]))
        if best is None or graded["score"] > best[1]["score"] \
                or (graded["score"] == best[1]["score"] and strat < best[0]):
            best = (strat, graded)
    return best


def build_entity_ranks(db, service) -> dict[str, dict]:
    """The picker's "how good am I at this star" answer, keyed by canonical
    entity key: `{"rank": str, "division": str, "strat": str}` for every
    practised entity that grades on AT LEAST ONE strategy.

    A THIRD "which rank" answer, deliberately distinct from the other two in
    this file: `rank_by_star` grades the ACTIVE strategy ("how am I doing at
    what I'm about to run"), `entity_rank` grades the entity's best-POSSIBLE
    ladder — a pointwise minimum no single strategy may actually own ("how
    close is this to the fastest this star can be"). This one grades the
    single BEST-SCORING strategy's own ladder — "how good am I at this star,
    at all" — the number the target picker wants on a grid cell before any
    strategy has been chosen for the run about to start.

    An on-demand builder rather than a `build_session_view` field on
    purpose: the session view rebuilds on every WebSocket event, and average
    rank modes grade O(history) per strategy per entity — paying that for
    every practised entity on every event would cost real per-event latency
    for a number only the picker modal ever looks at. Callers fetch this
    once, when the modal opens.

    Candidate entities are read off ONE pass over `db.attempts()` (mirrors
    `build_session_view`'s `attempts_by_star`/`attempts_by_seg` grouping,
    ~line 643) rather than scanning `all_attempts` per entity — that
    O(entities × attempts) shape was removed from the session view
    deliberately (2026-07-23 review) and must not come back here. An entity
    where no strategy grades is ABSENT from the map, not present with nulls
    — the picker's "no rank if never attempted yet" is the absence."""
    if service.ranks is None:
        return {}
    all_attempts = db.attempts()
    attempts_by_star: dict = {}
    attempts_by_seg: dict = {}
    for a in all_attempts:
        if a.segment_id is not None:
            attempts_by_seg.setdefault(a.segment_id, []).append(a)
        elif a.course_id is not None:
            attempts_by_star.setdefault((a.course_id, a.star_id), []).append(a)

    pbs_by_strat = current_pbs_by_strat(db.pbs())
    rank_mode = db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    if rank_mode not in classify.RANK_MODES:   # forward-safe: junk reads as pb
        rank_mode = classify.DEFAULT_RANK_MODE
    deleted_strats = db.get_state("deleted_strats", {})

    # One (ek, history, pb_key_prefix) triple per candidate entity, stars
    # then segments, so the grading loop below runs ONCE for both kinds —
    # a field added to the emitted dict is then a one-place edit, not two.
    candidates = [
        (entity_key(course_id, star_id), history, (course_id, star_id))
        for (course_id, star_id), history in attempts_by_star.items()
    ] + [
        (entity_key(None, None, seg_id), history, ("segment", seg_id))
        for seg_id, history in attempts_by_seg.items()
    ]

    out: dict[str, dict] = {}
    for ek, history, pb_key_prefix in candidates:
        best = _best_strategy_graded(service.ranks, ek, history, pbs_by_strat,
                                     rank_mode, deleted_strats.get(ek, []),
                                     pb_key_prefix)
        if best:
            strat, graded = best
            out[ek] = {"rank": graded["rank"], "division": graded["division"],
                      "strat": strat}
    return out


def build_entity_strategies(db, service, ek: str) -> dict:
    """The picker's step-3 "which strategy" answer: every strategy this
    entity can be practised WITH, each carrying its own rank -- so a
    strategy card can be chosen on evidence (rank + PB) instead of a bare
    name in a dropdown.

    Strategy names come from the SAME merged lists build_session_view's
    sections already use -- `_strategies_for` (registered ui_state names
    UNION observed-on-attempts UNION rank-standard names, stars) or
    `_seg_strategies` (the same three sources, plus the definition's own
    `default_strat` first, segments); tombstones (`deleted_strats`) are
    already filtered out by those two. Before this endpoint existed the
    header read the raw registered-strategy map directly and offered a
    NARROWER list than the practice card -- this is the fix: one shared
    source feeding both surfaces.

    Each strategy is graded through the exact chain the session view's
    section banner uses -- `ranks.clock_for` -> `grading_basis` ->
    `_graded_progress` against that strategy's OWN ladder -- so a medal here
    can never disagree with the banner for the same entity+strategy. A
    strategy with no ladder or nothing gradeable reports rank/division/score
    all None: present as "unranked", not absent. `pb_display` is always
    that strategy's own SAVED pb (`format_igt`), independent of the active
    rank mode -- in an average mode the grade can come from a different
    basis than the displayed PB, the same split `sec["pb"]` vs `sec["rank"]`
    already draws on the session view.

    `current` is the entity's active strategy (`service.strat_by_star` /
    `service.strat_by_segment`), masked to None when tombstoned. `allow_blank`
    is False only for a segment carrying a truthy `default_strat` -- the rule
    `stratpicker.js` already applies client-side from `sec.default_strat`
    (projection.py caveat 17); a star is always blankable.

    `ek` is parsed by hand (`ranks.standards.entity_key` has no public
    inverse): `"segment:<id>"` or `"star:<course>:<star>"`; anything else --
    and any non-numeric id -- raises LookupError (-> 404, `_http`). A segment
    id that parses but names no known definition ALSO raises -- the same
    existence check `service.set_target_segment` applies, since this is the
    same picker flow. Stars carry no such check anywhere in the codebase
    (course/star ids are never validated against the catalog, see
    `service.set_target`), so none is added here either. Named `ek`, not
    `entity_key`, so the parameter can't shadow the module-level
    `ranks.standards.entity_key` import `build_entity_ranks` calls freely
    twenty lines above (M2, final review 2026-07-26)."""
    parts = ek.split(":")
    if len(parts) == 2 and parts[0] == "segment":
        kind, id_parts = "segment", parts[1:]
    elif len(parts) == 3 and parts[0] == "star":
        kind, id_parts = "star", parts[1:]
    else:
        raise LookupError(f"bad entity key {ek!r}")
    try:
        ids = [int(p) for p in id_parts]
    except ValueError:
        raise LookupError(f"bad entity key {ek!r}") from None

    ranks = service.ranks
    all_attempts = db.attempts()
    registered = db.get_state("strategies", {})
    deleted = db.get_state("deleted_strats", {}).get(ek, [])
    rank_mode = db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    if rank_mode not in classify.RANK_MODES:   # forward-safe: junk reads as pb
        rank_mode = classify.DEFAULT_RANK_MODE
    pbs_by_strat = current_pbs_by_strat(db.pbs())

    if kind == "star":
        course_id, star_id = ids
        history = [a for a in all_attempts
                  if (a.course_id, a.star_id) == (course_id, star_id)]
        names = _strategies_for(registered, all_attempts, course_id, star_id,
                                ranks, deleted)
        current_raw = service.strat_by_star.get((course_id, star_id))
        pb_key_prefix = (course_id, star_id)
        clock = ranks.clock_for(ek) if ranks else "igt"
        allow_blank = True
    else:
        (segment_id,) = ids
        history = [a for a in all_attempts if a.segment_id == segment_id]
        seg_def = next((d for d in db.segment_defs() if d["id"] == segment_id),
                       None)
        if seg_def is None:   # same existence check set_target_segment applies
            raise LookupError(f"segment {segment_id} not found")
        default_strat = seg_def.get("default_strat")
        names = _seg_strategies(registered, history, segment_id, ranks,
                                deleted, default_strat)
        current_raw = service.strat_by_segment.get(segment_id)
        pb_key_prefix = ("segment", segment_id)
        clock = ranks.clock_for(ek) if ranks else "rta"
        allow_blank = not bool(default_strat)

    current = current_raw if current_raw and current_raw not in deleted else None

    strategies = []
    for name in names:
        pb_row = pbs_by_strat.get((*pb_key_prefix, clock, name))
        rank = division = score = None
        if ranks is not None:
            ladder = ranks.ladder_cs(ek, name)
            if ladder:
                basis = grading_basis(rank_mode, pb_row, history, name, clock)
                if basis is not None:
                    graded = _graded_progress(ladder,
                                              classify.display_cs(basis["frames"]))
                    rank, division = graded["rank"], graded["division"]
                    score = round(graded["score"], 1)
        strategies.append({
            "name": name, "rank": rank, "division": division, "score": score,
            "pb_display": format_igt(pb_row["frames"]) if pb_row else None,
        })

    return {"entity": ek, "kind": kind, "current": current,
            "allow_blank": allow_blank, "strategies": strategies}


def _section_banner(ranks, ek, strat, basis, mode, pb_untagged=False) -> dict | None:
    """Rank banner for a section: the grading basis (PB in pb mode, mean of
    valid runs in avg modes — grading_basis) graded under the ACTIVE strat.

    Returns None when the entity has NO standards (RankBanner not rendered).
    Otherwise the entity HAS standards; a {"rank": None, "reason": ...}
    sentinel says why it can't be graded so the UI can word it correctly:
      - "unattributed" : the entity's CURRENT PB (the same strategy-blind row
                      the display tag shows) carries no strat_tag at all — see
                      `pb_untagged` below. Checked before every other reason.
      - "no_strat"  : no active strategy selected.
      - "no_ladder" : the active strategy has no rank thresholds defined.
      - "unranked"  : the strategy has a ladder but nothing gradeable — no
                      saved PB (pb mode) / no valid runs (avg modes) on THIS
                      strategy (another strategy's times never count).
    Every payload carries "mode"; non-pb modes with a gradeable basis also
    carry "basis" {frames, display, count, window} — what the rank is based
    on (drives the banner's 'avg of N' line).

    `pb_untagged` — the caller's own answer to "does the entity's current,
    strategy-blind PB (`_current_pbs` / `sec.pb`) carry a strat_tag at all".
    True is the ONLY thing that can produce "unattributed", and it is checked
    FIRST, ahead of strat/ladder/basis — a PB with no strat_tag can never be
    found by `current_pbs_by_strat` regardless of which strategy is active, so
    "no_strat"/"unranked" would be true but misleading right beside a PB the
    reader can see (live report 2026-07-31: Bowser 1 showed PB 0'26"30 next to
    a Capless 5 floor rank — "this should never happen"). Distinct from
    "unranked" on purpose: "unranked" means a real ladder position with simply
    no time on it yet, which the UI floors (Capless V, an honest bottom-of-
    the-ladder claim); "unattributed" means a saved time exists that no
    strategy can claim, and floor-ing THAT would assert a concrete rank that
    directly contradicts the PB sitting next to it — so the UI never floors
    this reason (ui/components/ranks.js's RANK_SENTINEL, gated on
    reason === "unranked" alone).

    The graded shape carries "score", "division", "fill", "next_tier",
    "next_division", "next_gap_cs" (`_graded_progress` — the UI must never
    compute this curve itself, user report 2026-07-25). "next_tier"/
    "next_division" name the next STEP, whichever it is — one division up
    within this tier, or (already at the top division) the next harder
    tier's bottom one; "fill" measures progress WITHIN that current
    division, not the whole tier (a whole-tier bar barely moves after one
    good run); "next_gap_cs" is the TIME still needed to reach it (round
    2026-07-25 the user asked for the bar to go division-scoped, round
    2026-07-25 again asked the time delta back once it had). "rank" itself
    is `_graded_progress`'s own tier — provably identical to
    `classify.band`'s (the score/medal invariant, pinned by
    tests/test_ranks_scoring_seed.py), so `classify.band` is no longer
    called here; its own semantics, tests, and any other consumer are
    untouched, this call site just stopped needing it."""
    if ranks is None:
        return None
    has_standards = bool(ranks.ladders(ek))
    if not has_standards:
        return None
    if pb_untagged:
        return {"rank": None, "reason": "unattributed", "mode": mode}
    if not strat:
        return {"rank": None, "reason": "no_strat", "mode": mode}
    ladder = ranks.ladder_cs(ek, strat)
    if not ladder:
        return {"rank": None, "reason": "no_ladder", "mode": mode}
    if basis is None:
        return {"rank": None, "reason": "unranked", "mode": mode}
    out = {**_graded_progress(ladder, classify.display_cs(basis["frames"])),
           "mode": mode}
    if mode != "pb":
        out["basis"] = {"frames": basis["frames"],
                        "display": format_igt(basis["frames"]),
                        "count": basis["count"], "window": basis["window"]}
    return out


def _markers_for(markers_state: dict, course_id, star_id) -> dict:
    """strat -> sorted marker list for ONE section, from the ui_state KV.
    Key shape '<course>:<star>:<strat>' for stars, 'seg:<id>:<strat>' for
    segment sections (call with ("seg", segment_id)); '' = no strategy."""
    prefix = f"{course_id}:{star_id}:"
    return {k[len(prefix):]: v for k, v in markers_state.items()
            if k.startswith(prefix)}


def _time_filter_json(override: dict | None,
                      seg_guards: list | None = None) -> dict:
    """Effective validity bounds for one section (chip data). Stars pass the
    time_filters KV entry (None = no override); segments pass their def's
    guard rows (deleted def -> [] -> defaults). is_default drives the chip's
    dimmed state."""
    if seg_guards is not None:
        lo, hi = time_bounds(seg_guards)
    else:
        lo = (override or {}).get("min_frames")
        hi = (override or {}).get("max_frames")
    is_default = lo is None and hi is None
    return {"min_frames": DEFAULT_MIN_FRAMES if lo is None else lo,
            "max_frames": hi, "is_default": is_default}


def _stats_for(history, stat_menu, clock) -> list[dict]:
    """Stat chips for one section: canonical registry order, deduped by
    selection identity, computed over the LIFETIME history (spec §8).
    Star sections pass the view clock; segment sections always pass "rta"."""
    stats = []
    seen_stat_ids: set[str] = set()
    for sel in sorted(stat_menu,
                      key=lambda s: selection_order(s.get("key", ""),
                                                    s.get("params"))):
        if sel["key"] not in REGISTRY:
            continue
        sid = selection_id(sel["key"], sel.get("params"))
        if sid in seen_stat_ids:
            continue
        seen_stat_ids.add(sid)
        d = REGISTRY[sel["key"]]
        try:
            value = compute_stat(sel["key"], history, sel.get("params"), clock)
        except (ValueError, TypeError, KeyError):
            value = None  # bad stored params (e.g. n="abc") must not 500 the view
        # label N-substitution is keyed to avg_last_n; a future parameterized stat needs a label_template field instead
        label = d.label.replace("N", str(sel.get("params", {}).get("n", ""))) \
            if d.key == "avg_last_n" else d.label
        stats.append({"key": d.key, "label": label,
                      "params": sel.get("params", {}), "fmt": d.fmt,
                      "value": value, "display": _fmt(value, d.fmt)})
    return stats


def _progress(attempts, pb_ids: set, session_meta, frames_of,
              ranks=None, clock="igt", rank_clock=None) -> dict | None:
    """Completion-time-over-time points (spec §4): non-cleared successes of
    the SCOPED attempt list, grouped by session, chronological. A success
    qualifies when the section's clock (frames_of: stars igt, segments rta)
    has a value; every point still ships BOTH clock fields (the UI picks).
    Gold = explicitly saved PB rows (every save stays gold even when
    superseded). rta race rows (rta_frames == 0) ship as-is; the UI filters
    them. Resumed sessions append to their original group; within-group id
    order is still chronological (journal ids are wall-clock monotonic).

    Dot medals grade on `rank_clock` (the LADDER's own clock), never `clock`
    (the view clock) -- same rule and same reasoning as `_attempt_json`'s
    `rank_frames` (I1, final review 2026-07-26): a progress dot for the same
    attempt as an attempt-list row must never disagree with it. Falls back
    to `clock` when the caller has none to defer to."""
    rank_clock = clock if rank_clock is None else rank_clock
    by_session: dict[int, list] = {}
    for a in attempts:
        if a.outcome != "success" or a.cleared or frames_of(a) is None:
            continue
        rank_frames = a.igt_frames if rank_clock == "igt" else a.rta_frames
        by_session.setdefault(a.session_id, []).append({
            "t_utc": a.ended_utc,
            "igt_frames": a.igt_frames,
            "rta_frames": a.rta_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "attempt_id": a.id,
            "is_pb_igt": (a.id, "igt") in pb_ids,
            "is_pb_rta": (a.id, "rta") in pb_ids,
            "rank": _attempt_rank(a, rank_frames, ranks),
        })
    if not by_session:
        return None
    return {"sessions": [
        {"session_id": sid,
         "label": session_meta.get(sid, {}).get("label"),
         "started_utc": session_meta.get(sid, {}).get("started_utc"),
         "points": pts}
        for sid, pts in sorted(by_session.items())]}


# Castle-subarea quick-select: the (level, area) pairs a segment EXPLICITLY
# starts in, read off its start triggers. Only subarea-scoped triggers count —
# a bare "enter Castle Inside" with no subarea must NOT surface the segment in
# The quick-select banner's two "where does this segment start" readers live in
# tracking/segments.py as `start_areas`/`start_levels` (moved DOWN there
# 2026-07-26, beside the `arm_level` they already read through).
# They are the BANNER's readers only. "May this be practiced here" is answered
# by start_origin (tracking/practicable.py) — arm_level places 11 of the 65
# seeded definitions, start_origin places all 65, which is why the banner still
# offers no castle movement and these two are not the shared answer.


# Origin stamp for GET /api/segments (spec 2026-07-24-segment-origin-
# categories): the library groups by WHERE a definition can start, derived
# from its start rules. `overrides` is the ui_state KV `origin_overrides`
# (segment id as a string -> node key), which a user sets in the editor when
# the derivation guesses wrong; a KV rather than a column so correcting a
# label never flips seed_dirty and freezes a seeded row against corpus
# refreshes.
def stamp_origins(rows: list[dict], overrides: dict) -> list[dict]:
    stamped = []
    for row in rows:
        override = overrides.get(str(row["id"]))
        node = override if override else start_origin(row["start_triggers"])
        stamped.append({**row,
                        "origin": {**origin_view(node),
                                   "source": "override" if override
                                             else "derived"},
                        # The picker's exclusion signal (spec 2026-07-28-
                        # multi-step-segments, "the 100-coin star IS the
                        # segment"): GET /api/segments backs BOTH the
                        # Segments library/editor (which must still show
                        # this row for editing) and the target picker's
                        # course-union grid (ui/components/targetpicker.js),
                        # which must NOT offer it beside the star it now IS.
                        # Stamped here rather than left for the client to
                        # re-derive, since the same structural clause-search
                        # (segments.hundred_coin_entity) already answers
                        # "which entity owns this def's attempts" for
                        # projection.py -- one door, not a second heuristic
                        # keyed on category/seed_key.
                        "is_hundred_coin_engine": hundred_coin_entity(
                            row["start_triggers"], row["waypoints"]) is not None})
    return stamped


def segment_courses(db) -> dict:
    """{segment_id: course_id} from each definition's start levels -- the same
    resolution the stage quick-select banner uses, so a segment lands in the
    course scope the user practices it from. A castle-interior segment (LBLJ,
    MIPS clip) maps to no course and is simply absent: it belongs to `overall`
    and to routes, never to a course scope (spec section 3.3)."""
    from sm64_events.memory.addresses import COURSE_BY_LEVEL
    out = {}
    for d in db.segment_defs():
        for level in start_levels(d["start_triggers"]):
            course = COURSE_BY_LEVEL.get(level)
            if course is not None:
                out[d["id"]] = course
                break
    return out


# Bowser Reds/Pipe families (spec 2026-07-28-multi-step-segments, Bowser Reds
# star/pipe toggle): the (Star)/(Pipe) suffixed strategies in
# data/rank_standards.seed.json are a real, load-bearing naming convention,
# not decoration -- see the two constants and _reds_pipe_segments below.
STAR_FAMILY_SUFFIX = " (Star)"
PIPE_FAMILY_SUFFIX = " (Pipe)"
_REDS_PIPE_SEED_PREFIX = "seg:reds->pipe:"


def _reds_pipe_segments(seg_rows: list[dict]) -> tuple[dict[int, int], dict[int, str]]:
    """A Bowser course's 8-Red-Coins star practices as two things worth timing
    -- the grab alone (" (Star)" strategies) or the whole reds-then-pipe run
    (" (Pipe)" strategies) -- and BOTH ladders live on the star entity
    (measured, `rank_standards.seed.json`: star:16/17/18:0 carry paired
    "X (Star)"/"X (Pipe)" strategy names; the `seg:reds->pipe:<abbrev>`
    definition that actually records the Pipe-family attempts has no rank
    entity of its own). So grading the Pipe family means pairing the STAR's
    ladder with the SEGMENT's own (rta) history -- this is the resolver every
    grading call site borrows the star's entity_key from instead of the
    segment's, and the ONLY place that decides which segment that is.

    Returns (course_id -> segment_id, segment_id -> star's entity_key).
    Matched by seed_key prefix, never start_levels alone: the legacy
    exclusive "no reds" pipe-only segment (`seg:<abbrev>-pipe`) starts in the
    SAME level and would be indistinguishable otherwise (stagebanner.js's own
    docstring flagged this exact ambiguity as a future risk when it could
    only tell the two apart by name)."""
    by_course: dict[int, int] = {}
    grading_ek: dict[int, str] = {}
    for row in seg_rows:
        if not (row.get("seed_key") or "").startswith(_REDS_PIPE_SEED_PREFIX):
            continue
        for level in start_levels(row["start_triggers"]):
            course = COURSE_BY_LEVEL.get(level)
            if course is not None:
                by_course[course] = row["id"]
                grading_ek[row["id"]] = entity_key(course, 0)
                break
    return by_course, grading_ek


# The legacy EXCLUSIVE "no reds" pipe-only segments (seg:bitdw-pipe /
# seg:bitfs-pipe / seg:bits-pipe -- storage/db.py's own v4 schema INSERT,
# predating the corpus). Round 2, item 4's missing half (live report
# 2026-07-30): the naming fix that gave seg:reds->pipe:* the star's own
# family voice ("8 Red Coins (Pipe)") never reached this sibling family, so
# the pinned card still read the raw corpus name ("BitDW Pipe Entry") while
# the banner cell that selects it already reads "No Reds"
# (stagebanner.js's own row-local nameOverride, unrelated to this field --
# that one is JS-side and this row's own; the two must now AGREE, which is
# the whole point).
_LEGACY_NO_REDS_SEED_SUFFIX = "-pipe"


def _legacy_no_reds_segments(seg_rows: list[dict]) -> set[int]:
    """Segment ids for the legacy "no reds" family. Matched by seed_key
    SUFFIX alone -- unlike _reds_pipe_segments' own prefix match, this needs
    no exclusion clause for its Bowser sibling: `seg:reds->pipe:<abbrev>`
    ends in the course abbreviation ("...bitdw"), never literally "-pipe",
    so the two families' seed_keys are already suffix-disjoint by
    construction, not by a defensive extra check. Verified against the
    bundled corpus: exactly these three seed_keys end "-pipe" (no other
    segment does), so this is the actual, exhaustive set, not a
    coincidental generalization."""
    return {row["id"] for row in seg_rows
            if (row.get("seed_key") or "").endswith(_LEGACY_NO_REDS_SEED_SUFFIX)}


def entity_label(db, ek: str) -> str:
    """Human name for an entity key, for the MARELO breakdown list.

    Star names route through the canonical `course_name`/`star_name` pair
    (addresses.py) rather than indexing COURSE_NAMES/STAR_NAMES directly:
    STAR_NAMES[course_id] is a TUPLE positioned by star_id, not a {star_id:
    name} dict (brief's "verified shape" was wrong -- confirmed live), and
    star_name also owns the 1-15/star_id==6 -> "100 Coins" special case that a
    raw lookup here would silently miss."""
    from sm64_events.memory.addresses import course_name, star_name
    kind, _, rest = ek.partition(":")
    if kind == "segment":
        name = next((d["name"] for d in db.segment_defs()
                     if str(d["id"]) == rest), None)
        return name or f"segment {rest}"
    course, _, star = rest.partition(":")
    cid, sid = int(course), int(star)
    return f"{course_name(cid)} — {star_name(cid, sid)}"


def _armed_detail_for(d, seg_id: int, armed_arms: dict) -> dict | None:
    """{progress, total, start_frame, deadline_frame, waiting_for, steps} for
    an armed definition, or None while idle / deleted (Task 4/6, spec
    2026-07-28-multi-step-segments). Shared by segment sections and the
    100-coin star's section (spec 2026-07-28-multi-step-segments, "the
    100-coin star IS the segment") -- a HUNDRED_COIN_EXIT engine's arm state
    describes the STAR's own progress now, and this is the one place that
    turns an armed_arms() entry into the card-facing shape either way.

    `steps` (2026-08-03) is the WHOLE route, shortest-form, so the card can
    draw the track rather than only the step you are on; `progress` indexes
    into it, so the client needs no second source to know which chip is
    live. Live report: "This display (Step X of N) should now show all of the
    steps required to complete the segment, and visually update as we
    progress through each step."
    """
    if d is None or seg_id not in armed_arms:
        return None
    return {**armed_arms[seg_id],
            "waiting_for": card_waiting_for_sentence(
                d, armed_arms[seg_id]["progress"]),
            "steps": card_step_labels(d)}


def build_session_view(db, service, clock: str, scope: str = "session") -> dict:
    all_attempts = db.attempts()
    session_attempts = [a for a in all_attempts
                        if a.session_id == service.session_id]
    # scoped determines which attempts drive the seen-set, in_section lists,
    # and unassigned list. Stats always use lifetime (all_attempts).
    scoped = all_attempts if scope == "lifetime" else session_attempts
    pb_rows = db.pbs()
    pbs = _current_pbs(pb_rows)              # strategy-blind, for DISPLAY only
    pbs_by_strat = current_pbs_by_strat(pb_rows)   # per-strategy, for RANKS
    pb_ids = {(r["attempt_id"], r["timer_mode"]) for r in pb_rows}
    # A PB's saving attempt, for `caveats.caveat_for`. `.get()` everywhere it
    # is used: a pb row survives its attempt being wiped (db.py keeps the row
    # for its own `frames`), and an unclaimable PB is still unclaimable with
    # no attempt behind it.
    attempt_by_id = {a.id: a for a in all_attempts}
    sessions_list = db.sessions()
    session_meta = {s["id"]: s for s in sessions_list}
    stat_menu = db.get_state("stat_menu", default=DEFAULT_STAT_MENU)
    registered = db.get_state("strategies", {})
    markers_state = db.get_state("timeline_markers", {})
    time_filters_state = db.get_state("time_filters", {})
    rank_mode = db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    if rank_mode not in classify.RANK_MODES:   # forward-safe: junk reads as pb
        rank_mode = classify.DEFAULT_RANK_MODE
    # Fetched once here (rather than again beside seg_meta below) so the
    # Bowser reds/pipe pairing is known before the star loop needs it.
    seg_rows = db.segment_defs()
    reds_pipe_by_course, reds_pipe_grading_ek = _reds_pipe_segments(seg_rows)
    legacy_no_reds_ids = _legacy_no_reds_segments(seg_rows)
    # {segment_id} for EVERY def (enabled or not) whose own sequence includes
    # grabbing a main course's 100-coin star, plus {(course_id, 6): the
    # FIRST such def} (spec 2026-07-28-multi-step-segments, "the 100-coin
    # star IS the segment"): the star entity IS the practiced thing now, so
    # this family never gets a segment section, a segment_targets row, or a
    # picker entry -- only its arm state backs the star section's own
    # armed_detail below. `hundred_coin_ids` hides EVERY matching def (a
    # disabled one included -- it is still this star's engine, just not
    # currently able to arm); `hundred_coin_engine_for_star` keeps only the
    # first match per star, same as the retired _hundred_coin_redirect did.
    hundred_coin_ids: set[int] = set()
    hundred_coin_engine_for_star: dict[tuple[int, int], object] = {}
    # {id: SegmentDef}, built here (moved UP from beside the segment-section
    # loop below, single source now) because the armed-segments loop a few
    # lines down needs a def's own start_triggers to ask arms_ambiently --
    # the very question that loop exists to answer.
    seg_defs: dict[int, object] = {}
    for d in service.segment_defs:
        seg_defs[d.id] = d
        hc = hundred_coin_entity(d.start_triggers, d.waypoints)
        if hc is None:
            continue
        hundred_coin_ids.add(d.id)
        hundred_coin_engine_for_star.setdefault(hc, d)

    # ONE pass over all_attempts → per-entity lifetime histories (id order
    # preserved). Shared by the section builders and every rank-mode average;
    # the per-star list-comp scans this replaces were O(stars × attempts) per
    # view build and ran even in pb mode, which discards the history entirely
    # (final-review finding, 2026-07-23).
    attempts_by_star: dict = {}
    attempts_by_seg: dict = {}
    for a in all_attempts:
        if a.segment_id is not None:
            attempts_by_seg.setdefault(a.segment_id, []).append(a)
        elif a.course_id is not None:
            attempts_by_star.setdefault((a.course_id, a.star_id), []).append(a)

    deleted_strats = db.get_state("deleted_strats", {})

    def masked(strat, ek, reject_suffix=None):
        """A tombstoned (fully deleted) strat must never surface as an
        active/last strat — the dropdowns no longer offer it.

        `reject_suffix` additionally drops a name from the OTHER Bowser
        reds/pipe family (e.g. a star's own active strat ending " (Pipe)")
        -- both suffixes share one rank-standards entity (the star's), so
        nothing stopped a pre-2026-07-30 pick from landing on the wrong side
        before this toggle existed to keep them apart. Self-heals on the next
        pick from the now family-filtered dropdown; no data migration."""
        if strat and strat in deleted_strats.get(ek, []):
            return None
        if reject_suffix and strat and strat.endswith(reject_suffix):
            return None
        return strat

    sections, unassigned = [], []
    seen: dict[tuple[int, int], None] = {}
    seen_segs: dict[int, None] = {}
    # newest-activity recency per section key; scoped is journal-id-ordered
    # WITHIN each key, so the last write per key wins. journal_id() strips
    # the segment-id namespace offset so star and segment sections both
    # compare by underlying journal recency.
    last_id: dict = {}
    for a in scoped:
        # HUNDRED_COIN_EXIT-family attempts never carry segment_id any more
        # (tracking/projection.py reattributes them to the star at close
        # time) -- the `not in hundred_coin_ids` guard is therefore
        # defensive, not load-bearing, and kept for the same reason
        # `armed`'s filter below is: a def a user re-enables/re-edits must
        # never resurface as a segment section through this path either.
        if a.segment_id is not None and a.segment_id not in hundred_coin_ids:
            seen_segs[a.segment_id] = None  # ...but are NEVER unassigned
            last_id[("segment", a.segment_id)] = journal_id(a.id)
        elif a.course_id is None:
            unassigned.append(_attempt_json(a, pbs, clock, service.ranks))
        else:
            seen[(a.course_id, a.star_id)] = None
            last_id[(a.course_id, a.star_id)] = journal_id(a.id)

    # the practice target ALWAYS gets a section (spec §5), whichever kind:
    # setting a target immediately surfaces its lifetime history, PB, and
    # markers. Fresh targets have no recency entry (-1) and sort last. This
    # branch is ALSO, since 2026-08-05, the ONLY way an ambiently-arming def
    # (below) or the 100-coin star's own engine gets a section with zero
    # attempts -- it is the live-target check, the exact "has he chosen this"
    # signal projection.py's `_untargeted_failure`/`_untargeted_ambient_
    # failure` read for the sibling ROW fix, generalised to a segment identity
    # per THIS entity rather than per course (his correction, live report
    # 2026-08-05: a course with one chosen ambient def beside an unchosen one
    # -- 8 Red Coins (Pipe) picked, No Reds not -- must keep exactly one).
    if service.target and service.target[0] == "star" \
            and service.target[1:] not in seen:
        seen[service.target[1:]] = None
    if service.target and service.target[0] == "segment" \
            and service.target[1] not in hundred_coin_ids:
        seen_segs.setdefault(service.target[1], None)
    # armed segments are "active now" by the same philosophy as the target
    # pin: their sections render even with zero attempts, so the armed
    # badge has somewhere to live and a plain refresh self-heals it.
    # sorted = deterministic tie order among fresh (-1 recency) sections.
    armed = service.armed_segment_ids
    # Per-id progress/total/start_frame/deadline_frame (spec 2026-07-28-
    # multi-step-segments) -- same live-projector self-heal reasoning as
    # `armed` above, read once here rather than per section.
    armed_arms = service.armed_arms
    for sid in sorted(armed):
        # A HUNDRED_COIN_EXIT engine arms ambiently on every entry to its
        # course -- excluded here so it never grows a segment section of
        # its own; its arm state instead backs the STAR section's
        # armed_detail (the star loop below, via hundred_coin_engine_for_star).
        if sid in hundred_coin_ids:
            continue
        d = seg_defs.get(sid)
        # An AMBIENT arm -- mere course/subarea presence, not a deliberate
        # action (segments.arms_ambiently: seg:reds->pipe:* and the legacy
        # pipe-entry trio, the only other family besides the 100-coin star's
        # own engine that shares this idiom) -- must not manufacture a card
        # any more than the sibling projection.py fix lets it manufacture a
        # phantom ROW. Live report 2026-08-05: an empty "100 Coins" card
        # appeared from walking into a course with nothing selected ("an
        # entity nobody chose should not get a card either"), and its Bowser
        # sibling in the SAME course as a def he HAD chosen ("the No Reds
        # card appeared immediately, but shouldn't because pipe was
        # selected") -- proving the grain has to be per-DEFINITION, not per
        # course: two defs can arm off the identical course entry and only
        # one may be his. An ambiently-armed def he DID choose still gets its
        # card and its live timer, through the target branch just above --
        # the identical `service.target` signal read here, not a second
        # answer to "did he choose this".
        if d is not None and arms_ambiently(d.start_triggers):
            continue
        seen_segs.setdefault(sid, None)
    # NOT "armed is active now" for the 100-coin star's own section (deleted
    # 2026-08-05, same live report as above): its HUNDRED_COIN_EXIT engine
    # arms on the identical mere-course-entry idiom as the ambient segments
    # just excluded, so mirroring the armed-segment rule here would
    # manufacture the same unchosen card. A player who chose star 6 already
    # has a section from the star half of the target branch above.

    scoped_set = set(scoped)
    igt_of = lambda a: a.igt_frames
    for course_id, star_id in seen:
        ek = entity_key(course_id, star_id)
        history = attempts_by_star.get((course_id, star_id), [])
        in_section = [a for a in history if a in scoped_set]
        pb_json = {}
        for mode in ("igt", "rta"):
            row = pbs.get((course_id, star_id, mode))
            # attempt_id lets the UI turn the PB tag into a "jump to this row"
            # link — the same pickFromGraph path a gold progress-graph dot uses.
            pb_json[mode] = ({"frames": row["frames"],
                              "display": format_igt(row["frames"]),
                              "attempt_id": row["attempt_id"],
                              # "this time does not mean what the rank beside
                              # it implies", or None. ONE derivation
                              # (tracking/caveats.py) shared with the
                              # quick-select cell, so the two surfaces cannot
                              # word the same fact differently.
                              "caveat": caveat_for(
                                  row, attempt_by_id.get(row["attempt_id"]),
                                  igt_seen_in(history))}
                             if row else None)
        # Basis computed ONCE per section and shared by both rank numbers
        # below: the strat rank grades it against the ACTIVE strategy's
        # ladder, the entity rank against the entity's best-possible one.
        # Graded on the LADDER's own clock (ranks.clock_for), not the view
        # clock -- a ladder is defined in one clock, so grading a time from
        # the OTHER clock compares it to the wrong ruler (an rta time
        # includes approach time and systematically under-ranks against an
        # igt-defined ladder). Falls back to the view clock when no
        # standards are loaded at all, since there is then no ladder clock
        # to defer to. The displayed PB (sec["pb"] below) is unaffected --
        # that stays a display choice tied to the view clock.
        # A Bowser Reds star (the only kind carrying paired " (Star)"/
        # " (Pipe)" strategies, _reds_pipe_segments) must never grade or
        # offer the Pipe half here -- that half belongs to the paired
        # segment's own section below, which grades against THIS ek instead
        # of its own. reject_suffix keeps a pre-toggle stray pick from
        # showing a Pipe-ladder medal on a grab-only time.
        pipe_seg_id = reds_pipe_by_course.get(course_id) if star_id == 0 else None
        family_reject = PIPE_FAMILY_SUFFIX if pipe_seg_id is not None else None
        # The def whose completed attempts BECOME this star's, when star_id
        # is 6 and an engine covers this course (spec 2026-07-28-multi-step-
        # segments) -- None for every other star, always.
        hc_engine = (hundred_coin_engine_for_star.get((course_id, star_id))
                    if star_id == 6 else None)
        star_strat = masked(service.strat_by_star.get((course_id, star_id)),
                            ek, family_reject)
        rank_clock = service.ranks.clock_for(ek) if service.ranks else clock
        star_basis = grading_basis(
            rank_mode, pbs_by_strat.get((course_id, star_id, rank_clock, star_strat)),
            history, star_strat, rank_clock)
        # The entity's CURRENT (strategy-blind) PB on the ladder's own clock —
        # the exact row `_current_pbs` keeps and the display tag shows. A PB
        # here with no strat_tag can never be found by current_pbs_by_strat
        # regardless of the active strat, which is the untagged-PB bug
        # (live report 2026-07-31): see _section_banner's pb_untagged param.
        star_pb_current = pbs.get((course_id, star_id, rank_clock))
        star_pb_untagged = (star_pb_current is not None
                            and star_pb_current["strat_tag"] is None)
        star_strategies = _strategies_for(registered, all_attempts, course_id, star_id,
                                          service.ranks, deleted_strats.get(ek, []))
        if family_reject is not None:
            star_strategies = [s for s in star_strategies if not s.endswith(family_reject)]
        # Note: star sections intentionally omit "kind". The UI branches on
        # sec.kind being undefined for stars (SegmentSection vs StarSection),
        # so adding kind="star" here would silently break that check. Do not
        # add the key unless the UI branch is updated at the same time.
        sections.append({
            "course_id": course_id, "star_id": star_id,
            "last_activity": last_id.get((course_id, star_id), -1),
            "course_name": course_name(course_id),
            "star_name": star_name(course_id, star_id),
            "links": star_links(course_id, star_id),
            "pb": pb_json,
            "attempts": [_attempt_json(a, pbs, clock, service.ranks, rank_clock)
                        for a in in_section],
            "stats": _stats_for(history, stat_menu, clock),
            "strategies": star_strategies,
            # Grouping for the strategy dropdown, resolved SERVER-side and []
            # for every star but a 100-coin one (spec 2026-08-03-hundred-coin-
            # exit-variants). A 100-coin star's strategies are qualified by the
            # exit star the run ends on, so a bare "Standard" names two
            # different ladders on CCM; the picker shows them under a heading
            # per variant, and never re-derives which is which.
            # Rule-11 note: a SEGMENT has no exit star, so its section carries
            # no such key at all — a stated asymmetry, not a gap.
            "strategy_groups": (service.ranks.strategy_groups(ek)
                                if service.ranks else []),
            "last_strat": star_strat,
            # The paired seg:reds->pipe:<abbrev> segment id, or None for every
            # star but a Bowser course's Reds -- the star/pipe toggle's escape
            # hatch into the OTHER half of this same practiced thing (its own
            # section is below, in the segment loop).
            "pipe_segment_id": pipe_seg_id,
            "timeline": _timeline(in_section, igt_of),
            "markers_by_strat": _markers_for(markers_state, course_id, star_id),
            "time_filter": _time_filter_json(
                time_filters_state.get(f"{course_id}:{star_id}")),
            "progress": _progress(in_section, pb_ids, session_meta, igt_of,
                                  service.ranks, clock, rank_clock),
            "rank": _section_banner(
                service.ranks, ek, star_strat, star_basis, rank_mode,
                pb_untagged=star_pb_untagged),
            "entity_rank": entity_rank(
                service.ranks, ek, star_basis and star_basis["frames"]),
            "one_ladder": ranks_share_ladder(service.ranks, ek, star_strat),
            # armed_detail is a documented rule-11 ASYMMETRY, not its
            # absence: every star but the 100-coin one carries no such key
            # (test_star_sections_carry_no_arm_detail) because an ordinary
            # star is a single atomic grab with nothing to be part-way
            # through. Star 6 is the one exception, on purpose (spec
            # 2026-07-28-multi-step-segments) -- its HUNDRED_COIN_EXIT
            # engine has a real waypoint sequence, and this is that engine's
            # arm state re-expressed as the star's own progress. None for
            # every other star AND for star 6 when no engine covers this
            # course or it isn't currently armed.
            "armed_detail": _armed_detail_for(hc_engine, hc_engine.id, armed_arms)
                           if hc_engine is not None else None,
        })
    sections.sort(key=lambda s: last_id.get((s["course_id"], s["star_id"]), -1),
                  reverse=True)

    # segment sections: same shape, RTA-only (segments have no IGT) — pb,
    # attempts, stats, timeline and progress all force the rta clock
    # whatever the view clock. "armed" reads the LIVE projector so a plain
    # view refresh self-heals the UI's armed badge after missed notices.
    # seg_defs itself is built earlier now (beside hundred_coin_ids), since
    # the armed-segments loop above needs it too -- single source, not a
    # second lookup keyed the same way.
    # db rows carry category/seed_key (SegmentDef, the dataclass seg_defs
    # holds, does not) — a separate lookup keyed the same as seg_defs so
    # each section can stamp its category/seeded without a second db read.
    # Reuses seg_rows (fetched above for reds_pipe_by_course/grading_ek)
    # rather than a second db.segment_defs() call.
    seg_meta = {r["id"]: r for r in seg_rows}
    # The user's per-segment origin override (ui_state KV) — the same input
    # the projector's retirement rule and the picker's availability take, so
    # re-homing a segment moves where its card stays pinned too.
    origin_overrides = db.get_state("origin_overrides", {})
    rta_of = lambda a: a.rta_frames
    seg_sections = []
    for seg_id in seen_segs:
        seg_ek = entity_key(None, None, seg_id)
        d = seg_defs.get(seg_id)
        meta = seg_meta.get(seg_id, {})
        history = attempts_by_seg.get(seg_id, [])
        in_section = [a for a in history if a in scoped_set]
        pb_row = pbs.get(("segment", seg_id, "rta"))
        # Basis computed ONCE per section, same reasoning as the star loop
        # above: shared by the strat rank and the entity rank. Routed
        # through clock_for like the star section rather than hardcoding
        # "rta" directly -- it resolves to "rta" for every segment today
        # (segments have no igt clock), but that's a coincidence of the
        # standards data, not a rule; one grading-clock rule beats a rule
        # for stars and a coincidence for segments.
        # seg:reds->pipe:<abbrev> has no rank-standards entity of its own --
        # its ladder lives on the paired star (_reds_pipe_segments). Every
        # LADDER lookup below reads grading_ek instead of this segment's own
        # seg_ek; identity (name/id/pb-store key) stays seg_ek throughout,
        # since the PBs are genuinely this segment's own times.
        pipe_star_ek = reds_pipe_grading_ek.get(seg_id)
        grading_ek = pipe_star_ek or seg_ek
        seg_strat = masked(service.strat_by_segment.get(seg_id), seg_ek,
                           STAR_FAMILY_SUFFIX if pipe_star_ek else None)
        seg_rank_clock = service.ranks.clock_for(seg_ek) if service.ranks else "rta"
        seg_basis = grading_basis(
            rank_mode, pbs_by_strat.get(("segment", seg_id, seg_rank_clock, seg_strat)),
            history, seg_strat, seg_rank_clock)
        # Same untagged-PB check as the star loop above, keyed off the
        # SEGMENT's own identity (PBs are genuinely this segment's own times,
        # even when grading_ek borrows a paired star's ladder).
        seg_pb_current = pbs.get(("segment", seg_id, seg_rank_clock))
        seg_pb_untagged = (seg_pb_current is not None
                           and seg_pb_current["strat_tag"] is None)
        # Computed once and reused below (the "course_id" field AND the
        # pipe-pairing display names) rather than re-derived twice — a
        # deleted definition has no place, which reads as "anywhere" and
        # keeps its card, matching the projector.
        seg_course_id = origin_course(segment_origin(
            seg_id, d.start_triggers, origin_overrides)) if d else None
        seg_sections.append({
            "kind": "segment", "segment_id": seg_id,
            "last_activity": last_id.get(("segment", seg_id), -1),
            "name": d.name if d else f"segment {seg_id} (deleted)",
            "broken": d is None,
            # The course this segment is practiced IN, or None for the castle
            # interior, the hubs and the arenas -- the same key a star section
            # has always carried (rule 11), and the one the practice page
            # compares against `stage.course_id` to decide whether a pinned
            # card still belongs where the player is standing. Resolved
            # through segment_origin, NOT segment_courses: that one reads
            # `start_levels` and answers None for every movement starting in a
            # course. A deleted definition has no place, which reads as
            # "anywhere" and keeps its card, matching the projector.
            "course_id": seg_course_id,
            "armed": seg_id in armed,
            # arms merely by the player being present (course/stage entry
            # or an attempt_anchor there), not by a deliberate action --
            # spec 2026-07-28-multi-step-segments, segments.arms_ambiently.
            # practice.js's pinned-card gate reads this (never a category
            # string) to stop an ambient arm from reading as "the user
            # chose this" -- true today for reds->pipe and the legacy
            # pipe-entry trio; the 100-coin family needs no such flag,
            # since it has no segment section left to gate.
            "arms_ambiently": arms_ambiently(d.start_triggers) if d else False,
            # Arm progress/deadline detail plus the plain-language "waiting
            # for" line (spec 2026-07-28-multi-step-segments) -- None while
            # idle. A deleted definition (`d is None`) has no detail even if
            # somehow still armed (armed_arms() already excludes it). Every
            # star but the 100-coin one carries no such key: an ordinary
            # star has no waypoint sequence or staleness deadline to
            # describe (rule 11 asymmetry, same shape as default_strat
            # below -- see test_star_sections_carry_no_arm_detail, which
            # names star 6's engine as the documented exception). CARD-
            # facing phrasing (Task 6), not waiting_for_sentence's editor
            # voice -- this value renders under the practice card's own
            # "Waiting for" label, and "Waiting for You enter level Shifting
            # Sand Land" is broken English where "Waiting for Enter
            # Shifting Sand Land" reads as the intended imperative step.
            "armed_detail": _armed_detail_for(d, seg_id, armed_arms),
            "category": meta.get("category"),
            "seeded": meta.get("seed_key") is not None,
            # The definition's own strategy, or None. The card reads this to
            # drop the "— no strat —" option: where a default exists, having
            # no strategy is not a state the user may choose (spec
            # 2026-07-24-segment-default-strat). Stars carry no such key —
            # the documented rule 11 asymmetry.
            "default_strat": meta.get("default_strat"),
            # The entities this is a SUBSECTION of, [] for a top-level
            # segment (task 0087; plural round 20). Stamped rather than
            # derived client-side for the same reason `course_id` is: the
            # selector asks "what has this target as a parent" on every
            # render, and a second derivation in JS is how two surfaces
            # start disagreeing.
            "parents": meta.get("parents") or [],
            # igt present-as-None: same shape-stability rule as the target
            # payload — UI code reading sec.pb.igt gets null, not undefined.
            "pb": {"igt": None,
                   "rta": ({"frames": pb_row["frames"],
                            "display": format_igt(pb_row["frames"]),
                            "attempt_id": pb_row["attempt_id"]}
                           if pb_row else None)},
            "attempts": [_attempt_json(a, pbs, "rta", service.ranks, seg_rank_clock,
                                       rank_ek=grading_ek)
                        for a in in_section],
            "stats": _stats_for(history, stat_menu, "rta"),
            # registered ∪ observed-on-attempts ∪ rank-standard strategies --
            # for the reds/pipe pairing, "rank-standard strategies" means the
            # star's " (Pipe)"-suffixed half (standards_ek/family_suffix).
            "strategies": _seg_strategies(registered, history, seg_id,
                                          service.ranks,
                                          deleted_strats.get(seg_ek, []),
                                          meta.get("default_strat"),
                                          standards_ek=pipe_star_ek,
                                          family_suffix=(PIPE_FAMILY_SUFFIX
                                                        if pipe_star_ek else None)),
            "last_strat": seg_strat,
            # The paired star's entity_key ("star:<course>:0") when this IS
            # the seg:reds->pipe:<abbrev> half of a Bowser Reds pairing, else
            # None -- the practice card's escape hatch into fetching THAT
            # entity's standards (this segment's own has none) and filtering
            # the table to the Pipe family (the star's own section carries
            # the inverse, `pipe_segment_id`).
            "pipe_star_entity": pipe_star_ek,
            # The paired star's OWN display names, for the pinned card's
            # heading (round 2, item 4, live report 2026-07-30): the card
            # used to read "Segment · BitDW — 8 Red Coins → Pipe" (the
            # eyebrow's "Segment" context plus this def's raw corpus name)
            # while the banner cell it was selected FROM already reads
            # "8 Red Coins (Pipe)" — the card should agree with the cell
            # rather than expose the segment's own identity, same shape as
            # the 100-coin star's own fix (b6640ee, "the card stopped
            # presenting a segment and presented the star"), applied to
            # naming only: this section still IS the segment (its own
            # attempts/strategies/PB stay exactly as they are), only the
            # HEADING borrows the star's course + name. None when this
            # isn't the paired half (same guard as pipe_star_entity, so a
            # caller can gate on either).
            "pipe_star_course_name": (course_name(seg_course_id)
                                      if pipe_star_ek else None),
            "pipe_star_name": (star_name(seg_course_id, 0)
                              if pipe_star_ek else None),
            # The reds->pipe fix's missing half (round 2, item 4, live
            # report 2026-07-30): the legacy EXCLUSIVE "no reds" pipe-only
            # segment (seg:bitdw-pipe/seg:bitfs-pipe/seg:bits-pipe) needs
            # the SAME family-voice treatment, but has no paired star to
            # borrow a name FROM -- its display name is the constant "No
            # Reds" (stagebanner.js's own row-local nameOverride already
            # uses this exact string; a boolean here is what lets the
            # pinned card agree with it, rather than a second server field
            # repeating a literal the client already owns). The card
            # resolves its course context from `course_id` (already stamped
            # below, on every segment) through the session's own
            # `catalog.courses` -- no new course-name field needed for a
            # fact the client already has.
            "is_no_reds_pipe": seg_id in legacy_no_reds_ids,
            "timeline": _timeline(in_section, rta_of),
            "markers_by_strat": _markers_for(markers_state, "seg", seg_id),
            "time_filter": _time_filter_json(
                None, seg_guards=d.guards if d else []),
            "progress": _progress(in_section, pb_ids, session_meta, rta_of,
                                  service.ranks, "rta", seg_rank_clock),
            "rank": _section_banner(
                service.ranks, grading_ek, seg_strat, seg_basis, rank_mode,
                pb_untagged=seg_pb_untagged),
            "entity_rank": entity_rank(
                service.ranks, grading_ek, seg_basis and seg_basis["frames"]),
            "one_ladder": ranks_share_ladder(service.ranks, grading_ek, seg_strat),
        })
    seg_sections.sort(
        key=lambda s: last_id.get(("segment", s["segment_id"]), -1),
        reverse=True)

    # kind-aware target: the service owns target identity (one builder
    # shared with the target_changed broadcast); the view adds display
    # names and guarantees every key exists for BOTH kinds.
    target = dict(service.target_payload())
    target.setdefault("segment_id", None)
    target.setdefault("segment_name", None)
    tgt_c, tgt_s = target["course_id"], target["star_id"]
    target["course_name"] = course_name(tgt_c) if tgt_c is not None else None
    target["star_name"] = star_name(tgt_c, tgt_s) if tgt_c is not None else None
    target_ek = (entity_key(None, None, target["segment_id"])
                if target["kind"] == "segment" else entity_key(tgt_c, tgt_s))
    target["strat_tag"] = masked(target.get("strat_tag"), target_ek)

    return {
        "session": {"id": service.session_id},
        "scope": scope,
        "sessions": sessions_list,
        "clock": clock,
        "target": target,
        "stat_menu": stat_menu,
        "catalog": _CATALOG,
        "stars": sections,
        "segments": seg_sections,
        "active_route": service.active_route(),
        "unassigned": unassigned,
        "strategies": registered,
        "last_strat_by_star": {
            f"{c}:{s}": masked(
                v, entity_key(c, s),
                PIPE_FAMILY_SUFFIX if s == 0 and c in reds_pipe_by_course else None)
            for (c, s), v in service.strat_by_star.items()},
        # Parallel to last_strat_by_star: each star's {rank, division} under
        # its ACTIVE strat, graded on the PB achieved WITH that strat
        # (per-strategy ranking — pbs_by_strat, never the strategy-blind
        # overall PB), for the quick-select grid's at-a-glance medal. Only
        # gradeable stars appear; recomputed every build so changing a star's
        # strat updates its medal on the next view. A tombstoned strat is
        # masked to None before grading — a deleted strategy must not keep
        # showing a medal.
        "rank_by_star": {
            f"{c}:{s}": rank
            for (c, s), strat in service.strat_by_star.items()
            if (live_strat := masked(
                strat, entity_key(c, s),
                PIPE_FAMILY_SUFFIX if s == 0 and c in reds_pipe_by_course else None))
            and (rank := _strat_rank(
                service.ranks, entity_key(c, s), live_strat,
                grading_basis(
                    rank_mode, pbs_by_strat.get((c, s, "igt", live_strat)),
                    attempts_by_star.get((c, s), []), live_strat, "igt")))},
        # Parallel to rank_by_star, and keyed over a DIFFERENT set on purpose:
        # rank_by_star lists stars with an active strategy, while the most
        # important caveat is precisely "this PB has no strategy at all". So
        # this is keyed off the strategy-blind current PB — every star that has
        # one, whether or not it can be graded.
        "caveat_by_star": {
            f"{c}:{s}": key
            for (c, s, mode), row in pbs.items()
            if c != "segment" and mode == "igt"
            and (key := caveat_for(
                row, attempt_by_id.get(row["attempt_id"]),
                igt_seen_in(attempts_by_star.get((c, s), []))))},
        "rank_mode": rank_mode,
        # Entity keys that HAVE a ladder, whether or not this player has a time
        # on them. `rank_by_star`/`segment_targets[].rank` are None in both the
        # "no standards exist" and "standards exist, no time of mine" cases, and
        # the UI has to draw those differently: the second shows the ladder
        # FLOOR (Capless 5 today) instead of a bare "–", so an unranked-but-
        # rankable thing reads as "bottom of the ladder" rather than "not a
        # thing you can rank" (user, 2026-07-30). One list, membership-tested
        # client-side, rather than widening two payload shapes that several
        # consumers already destructure.
        "standards_eks": sorted(service.ranks.graded_entities())
                          if service.ranks is not None else [],
        "stage": service.current_stage,
        # Segments that start in a known subarea OR level, for the quick-select
        # banner (filtered client-side by the current subarea/level). `enabled`
        # is carried so the castle banner can keep its enabled-only rule while
        # the Bowser banner still shows a DISABLED pipe-entry segment (clicking
        # its "no reds" option enables it — the mutual-exclusion with "reds").
        "segment_targets": [
            {"segment_id": d.id, "name": d.name, "enabled": d.enabled,
             "start_areas": areas, "start_levels": levels,
             # The entities this is a SUBSECTION of, [] for none (task 0087;
             # plural round 20). The SELECTOR reads this payload, not the
             # sections, so the field is needed in both places -- a
             # subsection missing it here would simply sit loose in the row
             # beside its own parent, which is the crowding progressive
             # disclosure exists to prevent.
             "parents": d.parents,
             # True for the seg:reds->pipe:<abbrev> half of a Bowser Reds
             # pairing -- the discriminator stagebanner.js needs to tell it
             # apart from the legacy exclusive "no reds" pipe-only segment,
             # which shares the SAME start_levels and used to be told apart
             # only by the corpus's names happening to differ (flagged as a
             # future-rename risk when that was still the only signal).
             "is_reds_pipe": d.id in reds_pipe_grading_ek,
             # active strat + its medal for the banner cell, graded by THE
             # shared path (_strat_rank/grading_basis, same as rank_by_star
             # and the route medals) so a cell can never disagree with the
             # section banner for the same strat. seg:reds->pipe:<abbrev>
             # grades against the paired STAR's ladder (_reds_pipe_segments)
             # -- it has none of its own.
             "strat": (seg_strat := masked(
                 service.strat_by_segment.get(d.id),
                 (seg_ek := entity_key(None, None, d.id)),
                 STAR_FAMILY_SUFFIX if d.id in reds_pipe_grading_ek else None)),
             "rank": _strat_rank(
                 service.ranks, reds_pipe_grading_ek.get(d.id, seg_ek), seg_strat,
                 grading_basis(
                     rank_mode,
                     pbs_by_strat.get(("segment", d.id, "rta", seg_strat)),
                     attempts_by_seg.get(d.id, []), seg_strat, "rta")),
             # Rule 11: the same mark the star cells get, from the same
             # derivation, off this segment's own strategy-blind current PB.
             "caveat": (lambda row: caveat_for(
                 row, attempt_by_id.get(row["attempt_id"]) if row else None,
                 igt_seen_in(attempts_by_seg.get(d.id, []))))(
                     pbs.get(("segment", d.id, "rta")))}
            # EVERY def is included except the HUNDRED_COIN_EXIT family
            # (spec 2026-07-28-multi-step-segments) — a fully location-less
            # start (e.g. reset_game) gets empty start_areas/start_levels,
            # which the banner's area/level filters treat as no match, but
            # the armed-segment union (stagebanner.js armedExtraCells) can
            # still surface it: a RUNNING segment must never be invisible
            # (spec addendum 2026-07-24). The 100-coin family is the one
            # exception on purpose — it never surfaces as a segment at all
            # any more, so it has nothing to contribute here; the star
            # section's own armed_detail carries its arm state instead.
            for d in service.segment_defs
            if d.id not in hundred_coin_ids
            for areas, levels in ((start_areas(d.start_triggers),
                                   start_levels(d.start_triggers)),)],
        # user-picked selector icons: entity_key -> icon stem (ui_state KV,
        # written by POST /api/icon; ui/components/stagebanner.js resolves
        # override > mode art > generic star)
        "icon_overrides": db.get_state("icon_overrides", {}),
    }


def _fmt_ms(ms):
    if ms is None:
        return None
    s, ms = divmod(int(ms), 1000)
    m, s = divmod(s, 60)
    return f"{m}:{s:02d}.{ms:03d}"


def _resolve_cands(cands, seg_names):
    out = []
    for c in cands:
        if c["type"] == "segment":
            out.append({"kind": "segment", "segment_id": c["segment_id"],
                        "display": seg_names.get(c["segment_id"],
                                                 f"segment {c['segment_id']} (deleted)")})
        else:
            out.append({"kind": "star", "course": c["course"], "star": c["star"],
                        "display": star_name(c["course"], c["star"])})
    return out


def build_run_view(db, service) -> dict:
    """Live run state for the run panel: the active run (resolved step names +
    elapsed + per-step PB-cumulative and gold-duration for ±/gold) plus the
    route's PB total and gold sum-of-best."""
    from sm64_events.tracking.runs import pb_run, gold_splits
    act = service.active_run()
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    offset = service.run_settings()["start_offset_ms"]
    out = {"active": None, "pb": None, "gold": None, "start_offset_ms": offset}
    if act is None:
        return out
    steps_def = next((r["steps"] for r in db.routes()
                      if r["id"] == act["route_id"]), [])
    runs = db.runs(route_id=act["route_id"]) if act["route_id"] is not None else []
    pb = pb_run(runs)
    gold = gold_splits(runs, steps_def)
    pb_cum = {s["step_index"]: s["elapsed_ms"] for s in pb["splits"]} if pb else {}
    gold_dur = gold["durations"]
    steps = []
    for i, s in enumerate(act["steps"]):
        cands = _resolve_cands(steps_def[i]["candidates"], seg_names) \
            if i < len(steps_def) else []
        steps.append({**s, "candidates": cands,
                      "display": cands[0]["display"] if cands else "?",
                      "elapsed_display": _fmt_ms(
                          None if s["elapsed_ms"] is None
                          else s["elapsed_ms"] + offset),
                      "pb_elapsed_ms": pb_cum.get(i),
                      "gold_ms": gold_dur.get(i)})
    out["active"] = {**act, "steps": steps}
    out["pb"] = {"total_ms": pb["total_ms"],
                 "display": _fmt_ms(pb["total_ms"] + offset)} if pb else None
    out["gold"] = {"sum_of_best": gold["sum_of_best"],
                   "display": _fmt_ms(None if gold["sum_of_best"] is None
                                      else gold["sum_of_best"] + offset)}
    return out


def _enrich_splits(run, seg_names):
    """Add display name, duration_ms, and duration_display to each split.

    display: resolved from completed_item (star name or segment name).
    duration_ms: time spent on this step (elapsed_ms minus previous
      split's elapsed_ms, i.e. the wall-clock split for that step).
    duration_display: human-readable duration_ms via _fmt_ms."""
    out, prev = [], 0
    for s in run["splits"]:
        ci = s.get("completed_item") or {}
        if ci.get("type") == "segment":
            disp = seg_names.get(ci.get("segment_id"),
                                 f"segment {ci.get('segment_id')} (deleted)")
        elif ci.get("type") == "star":
            disp = star_name(ci.get("course"), ci.get("star"))
        else:
            disp = "?"
        dur = (s["elapsed_ms"] - prev) if s["elapsed_ms"] is not None else None
        prev = s["elapsed_ms"] if s["elapsed_ms"] is not None else prev
        out.append({**s, "display": disp, "duration_ms": dur,
                    "duration_display": _fmt_ms(dur)})
    return out


def build_run_history(db, route_id: int | None = None) -> dict:
    """Saved runs (optionally one route) + the PB. display_total folds in the
    per-run offset; finished runs flagged is_pb power the progression graph.
    Each run's splits are enriched with display names and per-step durations."""
    from sm64_events.tracking.runs import pb_run
    runs = db.runs(route_id=route_id)
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
    out_runs = [{**r,
                 "display_total": _fmt_ms(None if r["total_ms"] is None
                                          else r["total_ms"] + r["start_offset_ms"]),
                 "splits": _enrich_splits(r, seg_names)}
                for r in runs]
    pb = pb_run(runs)
    return {"runs": out_runs,
            "pb": {"total_ms": pb["total_ms"]} if pb else None}


def _candidate_rank(db, service, c, mode, by_star, by_seg,
                    deleted_strats: dict, pipe_grading_ek: dict | None = None) -> dict | None:
    """{"rank", "division"} for one route candidate under its active strat,
    graded by the rank-mode basis (per-strategy: another strat's times never
    count) — a thin dispatch straight into `_strat_rank`, so it carries the
    SAME division that function now computes (addendum, task 8, 2026-07-26).
    `by_star`/`by_seg` are the caller's one-pass attempt groupings (id order)
    so a route with many candidates never rescans the attempt list.
    `deleted_strats` is the deleted_strats KV (read once per route view build,
    by the caller) — a tombstoned active strat is masked to None here before
    grading, same rule as build_session_view's `masked` helper, so a deleted
    strategy can't keep showing a route medal.

    `pipe_grading_ek` (`_reds_pipe_segments`'s segment_id -> star's
    entity_key map) grades a `seg:reds->pipe:<abbrev>` route candidate
    (every seeded Bowser Reds route step names this one, never the bare
    star — the corpus already assumes Pipe timing throughout a route) against
    the paired star's ladder instead of the segment's own (nonexistent) one."""
    if service.ranks is None:
        return None  # skip the lookups entirely when nothing can be graded
    if c["type"] == "segment":
        ek = entity_key(None, None, c["segment_id"])
        grading_ek = (pipe_grading_ek or {}).get(c["segment_id"], ek)
        strat = service.strat_by_segment.get(c["segment_id"])
        clock = "rta"
        history = by_seg.get(c["segment_id"], [])
        if strat in deleted_strats.get(ek, []):
            strat = None
        pb = (db.current_pb(None, None, "rta", segment_id=c["segment_id"],
                            strat_tag=strat) if strat else None)
    else:
        ek = grading_ek = entity_key(c["course"], c["star"])
        strat = service.strat_by_star.get((c["course"], c["star"]))
        clock = "igt"
        history = by_star.get((c["course"], c["star"]), [])
        if strat in deleted_strats.get(ek, []):
            strat = None
        pb = (db.current_pb(c["course"], c["star"], "igt", strat_tag=strat)
              if strat else None)
    return _strat_rank(service.ranks, grading_ek, strat,
                       grading_basis(mode, pb, history, strat, clock))


def build_route_view(db, service, route_id: int) -> dict:
    """Resolve a route for display: each step's candidates get names, plus the
    per-step success rate and cumulative product (tracking/routes.route_stats).
    A candidate whose segment was deleted is marked broken (no cascade).
    Each step gains 'rank' (best-ranked candidate's {rank, division} — or
    None; addendum, task 8, 2026-07-26 added the division alongside the tier
    that was already there); the route view gains 'avg_rank' (nearest-tier
    mean of step ranks — {score, tier} only, no division: it names the
    nearest tier to a MEAN score, which is not a real graded time and so has
    no real division to show) and 'weakest_step' index."""
    route = next((r for r in db.routes() if r["id"] == route_id), None)
    if route is None:
        raise LookupError(f"route {route_id} not found")
    attempts = db.attempts()
    rank_mode = db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    if rank_mode not in classify.RANK_MODES:   # forward-safe: junk reads as pb
        rank_mode = classify.DEFAULT_RANK_MODE
    seg_rows = db.segment_defs()
    seg_names = {d["id"]: d["name"] for d in seg_rows}
    _, pipe_grading_ek = _reds_pipe_segments(seg_rows)
    deleted_strats = db.get_state("deleted_strats", {})
    stats = route_stats(route["steps"], attempts)
    # one-pass groupings for _candidate_rank (same shape as the session
    # view's attempts_by_star/seg — never rescan attempts per candidate)
    by_star: dict = {}
    by_seg: dict = {}
    for a in attempts:
        if a.segment_id is not None:
            by_seg.setdefault(a.segment_id, []).append(a)
        elif a.course_id is not None:
            by_star.setdefault((a.course_id, a.star_id), []).append(a)
    steps = []
    for step, st in zip(route["steps"], stats):
        cands, broken = [], False
        for c in step["candidates"]:
            if c["type"] == "segment":
                name = seg_names.get(c["segment_id"])
                if name is None:
                    broken = True
                    name = f"segment {c['segment_id']} (deleted)"
                cands.append({"kind": "segment", "segment_id": c["segment_id"],
                              "display": name})
            else:
                cands.append({"kind": "star", "course": c["course"],
                              "star": c["star"],
                              "display": star_name(c["course"], c["star"]),
                              "course_name": course_name(c["course"])})
        ranks_here = [_candidate_rank(db, service, c, rank_mode, by_star,
                                      by_seg, deleted_strats, pipe_grading_ek)
                      for c in step["candidates"]]
        # best is a {rank, division} dict (or None) -- the WINNING
        # candidate's own graded division rides along, so the step's medal
        # never has a tier with no division to show (addendum, task 8).
        best = max((r for r in ranks_here if r),
                   key=lambda r: classify.RANK_SCORE[r["rank"]], default=None)
        steps.append({"label": step.get("label"), "need": step["need"],
                      "candidates": cands, "step_rate": st["step_rate"],
                      "cumulative": st["cumulative"], "broken": broken,
                      "rank": best})
    scored = [classify.RANK_SCORE[s["rank"]["rank"]] for s in steps if s["rank"]]
    avg_rank = None
    weakest_step = None
    if scored:
        mean = sum(scored) / len(scored)
        tier = min(classify.RANK_SCORE, key=lambda n: abs(classify.RANK_SCORE[n] - mean))
        avg_rank = {"score": round(mean, 1), "tier": tier}
        ranked = [(i, classify.RANK_SCORE[s["rank"]["rank"]]) for i, s in enumerate(steps)
                  if s["rank"]]
        weakest_step = min(ranked, key=lambda t: t[1])[0]
    return {"id": route["id"], "name": route["name"],
            "start_condition": route["start_condition"], "steps": steps,
            "avg_rank": avg_rank, "weakest_step": weakest_step,
            "category": route["category"],
            "seeded": route["seed_key"] is not None}


def build_compare_view(db, ranks, entity: str, strat: str | None) -> dict:
    """Compare-tab payload for one (entity, strat): the saved comparison videos
    for THAT combo (each with a servable clip_url) — reloaded whenever a run of
    that star+strategy is opened. Plus the rank-standard `suggestion` (the
    default to auto-load when the combo has none) and `library` — the entity's
    comparisons saved under OTHER strategies, for the 'load existing' picker.
    Ranks may be None; `strat` may be None/'' (no strategy → no suggestion)."""
    saved = [{**c, "clip_url": f"/api/compare/cache/{c['cache_name']}"}
             for c in db.comparisons(entity, strat)]
    suggestion_url = ranks.video_for(entity, strat) if (ranks and strat) else None
    already = any(c["source_ref"] == suggestion_url for c in saved)
    suggestion = ({"source_kind": "youtube", "source_ref": suggestion_url,
                   "name": f"{strat} — rank standard", "strat": strat}
                  if suggestion_url and not already else None)
    library = [{"id": c["id"], "name": c["name"], "strat": c["strat"],
                "source_kind": c["source_kind"], "source_ref": c["source_ref"]}
               for c in db.comparisons(entity) if c["strat"] != strat]
    # rank_source is the rank-standard URL for this strat whether or not it is
    # already saved — the UI opens it by DEFAULT (opt-out) when nothing is open.
    return {"entity": entity, "strat": strat, "saved": saved,
            "suggestion": suggestion, "library": library,
            "rank_source": suggestion_url}
