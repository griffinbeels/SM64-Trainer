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
from sm64_events.memory.addresses import (COURSE_NAMES, course_name,
                                          star_count, star_name)
from sm64_events.ranks import classify
from sm64_events.ranks import scoring
from sm64_events.ranks.standards import entity_key
from sm64_events.stats.registry import (DEFAULT_STAT_MENU, REGISTRY,
                                        compute_stat, selection_id,
                                        selection_order)
from sm64_events.tracking.projection import DEFAULT_MIN_FRAMES, journal_id
from sm64_events.tracking.routes import route_stats
from sm64_events.tracking.segments import (arm_level, course_groups,
                                            origin_view, start_origin,
                                            time_bounds)

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


def _current_pbs_by_strat(pb_rows: list[dict]) -> dict:
    """Latest pb row keyed like _current_pbs but with the saving attempt's
    strat_tag appended. THE per-strategy ranking lookup: a strategy is ranked
    ONLY by times achieved WITH that strategy. PBs with no strat_tag can't be
    attributed to a strategy and are skipped (the entity stays unranked on
    every strat until a strat-tagged PB lands)."""
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


def _attempt_json(a, pbs, clock, ranks=None):
    pb = pbs.get(("segment", a.segment_id, clock) if a.segment_id is not None
                 else (a.course_id, a.star_id, clock))
    frames = a.igt_frames if clock == "igt" else a.rta_frames
    race_row = clock == "rta" and frames == 0  # same-tick reset-race: rta is junk (see projection.py docstring)
    delta = (frames - pb["frames"]
             if pb and frames is not None and not race_row and a.outcome == "success"
             else None)
    return {"id": a.id, "outcome": a.outcome, "outcome_detail": a.outcome_detail,
            "anchor_type": a.anchor_type, "strat_tag": a.strat_tag,
            "igt_frames": a.igt_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta_frames": a.rta_frames,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "pb_delta_frames": delta, "cleared": a.cleared,
            # this attempt owns the CURRENT pb row on this clock — drives
            # the Save-as-PB / Undo-PB button swap (undo deletes that row)
            "is_current_pb": bool(pb) and pb["attempt_id"] == a.id,
            "cleared_reason": a.cleared_reason,
            "started_utc": a.started_utc, "ended_utc": a.ended_utc,
            "rollouts_total": a.rollouts_total,
            "rollouts_dustless": a.rollouts_dustless,
            "jumps_total": a.jumps_total,
            "jumps_dustless": a.jumps_dustless,
            "rank": _attempt_rank(a, frames, ranks),
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
                    deleted=(), default_strat=None) -> list:
    """Segment sibling of _strategies_for: the definition's own default_strat
    first, then registered strats (ui_state, keyed "seg:{id}" — see
    service._strategies_key), then observed-on-attempts (sorted), then
    rank-standard strats. Registration is what keeps a just-picked strategy in
    the dropdown before any attempt exists under it; the default is here for
    the same reason, one step earlier — it is never journaled, so nothing else
    would put it in the list on a segment that has never been run.
    `deleted` filters the segment's tombstoned names, same as _strategies_for;
    a default can never be among them — service.purge_strategy refuses to
    delete one, the same protection community strats get."""
    out = [default_strat] if default_strat else []
    for strat in registered.get(f"seg:{seg_id}", []):
        if strat not in out:
            out.append(strat)
    for strat in sorted({a.strat_tag for a in history if a.strat_tag}):
        if strat not in out:
            out.append(strat)
    if ranks is not None:
        for strat in ranks.strategies(entity_key(None, None, seg_id)):
            if strat not in out:
                out.append(strat)
    return [s for s in out if s not in deleted]


def _attempt_rank(a, frames, ranks) -> str | None:
    if ranks is None or frames is None or a.outcome != "success" or not a.strat_tag:
        return None
    ek = entity_key(a.course_id, a.star_id, a.segment_id)
    return classify.rank_for(ranks.ladder_cs(ek, a.strat_tag), classify.display_cs(frames))


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


def _strat_rank(ranks, ek, strat, basis) -> str | None:
    """Rank NAME for an entity graded under `strat` at its grading basis
    (grading_basis output: PB row in pb mode, mean of valid runs in avg
    modes), or None when ungradeable (no ranks loaded, no active strat, no
    basis, or the strat has no ladder). THE single grading path shared by
    route candidates and the stage quick-select star grid (view's
    rank_by_star) — keep it one place so a medal never disagrees with the
    section banner / attempt medals."""
    if ranks is None or not strat or basis is None:
        return None
    ladder = ranks.ladder_cs(ek, strat)
    if not ladder:
        return None
    return classify.rank_for(ladder, classify.display_cs(basis["frames"]))


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
    step to chase)."""
    score = scoring.score_for(ladder, time_cs)
    progress = scoring.division_progress(score, scoring.defined_tiers(ladder))
    next_gap_cs = None
    if progress["next_at"] is not None:
        target_cs = scoring.time_for_score(ladder, progress["next_at"])
        if target_cs is not None:
            next_gap_cs = time_cs - target_cs
    return {"score": score, "rank": progress["tier"],
            "division": progress["division"], "fill": progress["fill"],
            "next_tier": progress["next_tier"],
            "next_division": progress["next_division"],
            "next_gap_cs": next_gap_cs}


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


def _best_strategy_graded(ranks, ek, history, pbs_by_strat, rank_mode,
                          deleted, pb_key_prefix) -> tuple[str, dict] | None:
    """The (strategy, _graded_progress) pair with the HIGHEST score among
    `ranks.strategies(ek)`, skipping tombstoned names (`deleted`) and
    strategies with no ladder or nothing gradeable. Ties break on
    min(strat) -- same deterministic convention `_fastest_strategy` uses,
    order-independent (a later tie only overwrites the running best when its
    name sorts earlier, so iteration order never decides the winner).
    `pb_key_prefix` is `(course_id, star_id)` or `("segment", segment_id)` --
    `_current_pbs_by_strat`'s key shape minus (clock, strat)."""
    clock = ranks.clock_for(ek)
    best: tuple[str, dict] | None = None
    for strat in ranks.strategies(ek):
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

    pbs_by_strat = _current_pbs_by_strat(db.pbs())
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


def _section_banner(ranks, ek, strat, basis, mode) -> dict | None:
    """Rank banner for a section: the grading basis (PB in pb mode, mean of
    valid runs in avg modes — grading_basis) graded under the ACTIVE strat.

    Returns None when the entity has NO standards (RankBanner not rendered).
    Otherwise the entity HAS standards; a {"rank": None, "reason": ...}
    sentinel says why it can't be graded so the UI can word it correctly:
      - "no_strat"  : no active strategy selected.
      - "no_ladder" : the active strategy has no rank thresholds defined.
      - "unranked"  : the strategy has a ladder but nothing gradeable — no
                      saved PB (pb mode) / no valid runs (avg modes) on THIS
                      strategy (another strategy's times never count).
    Every payload carries "mode"; non-pb modes with a gradeable basis also
    carry "basis" {frames, display, count, window} — what the rank is based
    on (drives the banner's 'avg of N' line).

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
              ranks=None, clock="igt") -> dict | None:
    """Completion-time-over-time points (spec §4): non-cleared successes of
    the SCOPED attempt list, grouped by session, chronological. A success
    qualifies when the section's clock (frames_of: stars igt, segments rta)
    has a value; every point still ships BOTH clock fields (the UI picks).
    Gold = explicitly saved PB rows (every save stays gold even when
    superseded). rta race rows (rta_frames == 0) ship as-is; the UI filters
    them. Resumed sessions append to their original group; within-group id
    order is still chronological (journal ids are wall-clock monotonic)."""
    by_session: dict[int, list] = {}
    for a in attempts:
        if a.outcome != "success" or a.cleared or frames_of(a) is None:
            continue
        frames = a.igt_frames if clock == "igt" else a.rta_frames
        by_session.setdefault(a.session_id, []).append({
            "t_utc": a.ended_utc,
            "igt_frames": a.igt_frames,
            "rta_frames": a.rta_frames,
            "igt": format_igt(a.igt_frames) if a.igt_frames is not None else None,
            "rta": format_igt(a.rta_frames) if a.rta_frames is not None else None,
            "attempt_id": a.id,
            "is_pb_igt": (a.id, "igt") in pb_ids,
            "is_pb_rta": (a.id, "rta") in pb_ids,
            "rank": _attempt_rank(a, frames, ranks),
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
# every subarea (that is what keeps LBLJ out of Upstairs). Derived from the
# trigger param NAMES (stable across the matcher), so this stays decoupled from
# segments.py's registry:
#   area_enter / attempt_anchor : (level, area)
#   level_enter / level_exit    : (to, to_subarea)   [to_subarea exists once the
#       subarea-trigger work lands; until then .get() returns None and the row
#       contributes nothing — forward-safe]
# The UI (ui/components/stagebanner.js) filters these by the current castle
# subarea (stage_changed carries level+area) to offer one-click segment targets.
def _segment_start_areas(start_triggers: list) -> list:
    out: list = []
    for trig in start_triggers:
        kind = trig.get("type")
        if kind in ("area_enter", "attempt_anchor"):
            level, area = trig.get("level"), trig.get("area")
        elif kind in ("level_enter", "level_exit"):
            level, area = trig.get("to"), trig.get("to_subarea")
        else:
            continue
        if level is not None and area is not None and [level, area] not in out:
            out.append([level, area])
    return out


# Whole-LEVEL quick-select: the levels a segment EXPLICITLY starts in, ignoring
# subarea. The Bowser banner (BitDW/BitFS/BitS courses + the 1/2/3 arenas) has no
# castle-style subareas — it offers segments by level alone (pipe-entry segments
# start in level 17/19/21; fight segments in 30/33/34). Reads the same trigger
# param NAMES as _segment_start_areas (decoupled from the matcher), taking only
# the level; `spawned` carries a level too (e.g. Lakitu Skip). The UI
# (ui/components/stagebanner.js) filters these by the current level.
def _segment_start_levels(start_triggers: list) -> list:
    out: list = []
    for trig in start_triggers:
        level = arm_level(trig)   # shared reader (tracking/segments.py)
        if level is not None and level not in out:
            out.append(level)
    return out


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
                                             else "derived"}})
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
        for level in _segment_start_levels(d["start_triggers"]):
            course = COURSE_BY_LEVEL.get(level)
            if course is not None:
                out[d["id"]] = course
                break
    return out


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


def build_session_view(db, service, clock: str, scope: str = "session") -> dict:
    all_attempts = db.attempts()
    session_attempts = [a for a in all_attempts
                        if a.session_id == service.session_id]
    # scoped determines which attempts drive the seen-set, in_section lists,
    # and unassigned list. Stats always use lifetime (all_attempts).
    scoped = all_attempts if scope == "lifetime" else session_attempts
    pb_rows = db.pbs()
    pbs = _current_pbs(pb_rows)              # strategy-blind, for DISPLAY only
    pbs_by_strat = _current_pbs_by_strat(pb_rows)   # per-strategy, for RANKS
    pb_ids = {(r["attempt_id"], r["timer_mode"]) for r in pb_rows}
    sessions_list = db.sessions()
    session_meta = {s["id"]: s for s in sessions_list}
    stat_menu = db.get_state("stat_menu", default=DEFAULT_STAT_MENU)
    registered = db.get_state("strategies", {})
    markers_state = db.get_state("timeline_markers", {})
    time_filters_state = db.get_state("time_filters", {})
    rank_mode = db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    if rank_mode not in classify.RANK_MODES:   # forward-safe: junk reads as pb
        rank_mode = classify.DEFAULT_RANK_MODE

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

    def masked(strat, ek):
        """A tombstoned (fully deleted) strat must never surface as an
        active/last strat — the dropdowns no longer offer it."""
        return None if strat and strat in deleted_strats.get(ek, []) else strat

    sections, unassigned = [], []
    seen: dict[tuple[int, int], None] = {}
    seen_segs: dict[int, None] = {}
    # newest-activity recency per section key; scoped is journal-id-ordered
    # WITHIN each key, so the last write per key wins. journal_id() strips
    # the segment-id namespace offset so star and segment sections both
    # compare by underlying journal recency.
    last_id: dict = {}
    for a in scoped:
        if a.segment_id is not None:   # segment attempts have course_id None
            seen_segs[a.segment_id] = None  # ...but are NEVER unassigned
            last_id[("segment", a.segment_id)] = journal_id(a.id)
        elif a.course_id is None:
            unassigned.append(_attempt_json(a, pbs, clock, service.ranks))
        else:
            seen[(a.course_id, a.star_id)] = None
            last_id[(a.course_id, a.star_id)] = journal_id(a.id)

    # the practice target ALWAYS gets a section (spec §5), whichever kind:
    # setting a target immediately surfaces its lifetime history, PB, and
    # markers. Fresh targets have no recency entry (-1) and sort last.
    if service.target and service.target[0] == "star" \
            and service.target[1:] not in seen:
        seen[service.target[1:]] = None
    if service.target and service.target[0] == "segment":
        seen_segs.setdefault(service.target[1], None)
    # armed segments are "active now" by the same philosophy as the target
    # pin: their sections render even with zero attempts, so the armed
    # badge has somewhere to live and a plain refresh self-heals it.
    # sorted = deterministic tie order among fresh (-1 recency) sections.
    armed = service.armed_segment_ids
    for sid in sorted(armed):
        seen_segs.setdefault(sid, None)

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
                              "attempt_id": row["attempt_id"]}
                             if row else None)
        # Basis computed ONCE per section and shared by both rank numbers
        # below: the strat rank grades it against the ACTIVE strategy's
        # ladder, the entity rank against the entity's best-possible one.
        star_strat = masked(service.strat_by_star.get((course_id, star_id)), ek)
        star_basis = grading_basis(
            rank_mode, pbs_by_strat.get((course_id, star_id, clock, star_strat)),
            history, star_strat, clock)
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
            "attempts": [_attempt_json(a, pbs, clock, service.ranks) for a in in_section],
            "stats": _stats_for(history, stat_menu, clock),
            "strategies": _strategies_for(registered, all_attempts, course_id, star_id,
                                         service.ranks, deleted_strats.get(ek, [])),
            "last_strat": masked(service.strat_by_star.get((course_id, star_id)), ek),
            "timeline": _timeline(in_section, igt_of),
            "markers_by_strat": _markers_for(markers_state, course_id, star_id),
            "time_filter": _time_filter_json(
                time_filters_state.get(f"{course_id}:{star_id}")),
            "progress": _progress(in_section, pb_ids, session_meta, igt_of,
                                  service.ranks, clock),
            "rank": _section_banner(
                service.ranks, ek, star_strat, star_basis, rank_mode),
            "entity_rank": entity_rank(
                service.ranks, ek, star_basis and star_basis["frames"]),
        })
    sections.sort(key=lambda s: last_id.get((s["course_id"], s["star_id"]), -1),
                  reverse=True)

    # segment sections: same shape, RTA-only (segments have no IGT) — pb,
    # attempts, stats, timeline and progress all force the rta clock
    # whatever the view clock. "armed" reads the LIVE projector so a plain
    # view refresh self-heals the UI's armed badge after missed notices.
    seg_defs = {d.id: d for d in service.segment_defs}
    # db rows carry category/seed_key (SegmentDef, the dataclass seg_defs
    # holds, does not) — a separate lookup keyed the same as seg_defs so
    # each section can stamp its category/seeded without a second db read.
    seg_meta = {r["id"]: r for r in db.segment_defs()}
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
        # above: shared by the strat rank and the entity rank.
        seg_strat = masked(service.strat_by_segment.get(seg_id), seg_ek)
        seg_basis = grading_basis(
            rank_mode, pbs_by_strat.get(("segment", seg_id, "rta", seg_strat)),
            history, seg_strat, "rta")
        seg_sections.append({
            "kind": "segment", "segment_id": seg_id,
            "last_activity": last_id.get(("segment", seg_id), -1),
            "name": d.name if d else f"segment {seg_id} (deleted)",
            "broken": d is None,
            "armed": seg_id in armed,
            "category": meta.get("category"),
            "seeded": meta.get("seed_key") is not None,
            # The definition's own strategy, or None. The card reads this to
            # drop the "— no strat —" option: where a default exists, having
            # no strategy is not a state the user may choose (spec
            # 2026-07-24-segment-default-strat). Stars carry no such key —
            # the documented rule 11 asymmetry.
            "default_strat": meta.get("default_strat"),
            # igt present-as-None: same shape-stability rule as the target
            # payload — UI code reading sec.pb.igt gets null, not undefined.
            "pb": {"igt": None,
                   "rta": ({"frames": pb_row["frames"],
                            "display": format_igt(pb_row["frames"]),
                            "attempt_id": pb_row["attempt_id"]}
                           if pb_row else None)},
            "attempts": [_attempt_json(a, pbs, "rta", service.ranks) for a in in_section],
            "stats": _stats_for(history, stat_menu, "rta"),
            # registered ∪ observed-on-attempts ∪ rank-standard strategies
            "strategies": _seg_strategies(registered, history, seg_id,
                                          service.ranks,
                                          deleted_strats.get(seg_ek, []),
                                          meta.get("default_strat")),
            "last_strat": masked(service.strat_by_segment.get(seg_id), seg_ek),
            "timeline": _timeline(in_section, rta_of),
            "markers_by_strat": _markers_for(markers_state, "seg", seg_id),
            "time_filter": _time_filter_json(
                None, seg_guards=d.guards if d else []),
            "progress": _progress(in_section, pb_ids, session_meta, rta_of,
                                  service.ranks, "rta"),
            "rank": _section_banner(
                service.ranks, seg_ek, seg_strat, seg_basis, rank_mode),
            "entity_rank": entity_rank(
                service.ranks, seg_ek, seg_basis and seg_basis["frames"]),
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
        "last_strat_by_star": {f"{c}:{s}": masked(v, entity_key(c, s))
                               for (c, s), v in service.strat_by_star.items()},
        # Parallel to last_strat_by_star: each star's rank under its ACTIVE
        # strat, graded on the PB achieved WITH that strat (per-strategy
        # ranking — pbs_by_strat, never the strategy-blind overall PB), for
        # the quick-select grid's at-a-glance medal. Only gradeable stars
        # appear; recomputed every build so changing a star's strat updates
        # its medal on the next view. A tombstoned strat is masked to None
        # before grading — a deleted strategy must not keep showing a medal.
        "rank_by_star": {
            f"{c}:{s}": rank
            for (c, s), strat in service.strat_by_star.items()
            if (live_strat := masked(strat, entity_key(c, s)))
            and (rank := _strat_rank(
                service.ranks, entity_key(c, s), live_strat,
                grading_basis(
                    rank_mode, pbs_by_strat.get((c, s, "igt", live_strat)),
                    attempts_by_star.get((c, s), []), live_strat, "igt")))},
        "rank_mode": rank_mode,
        "stage": service.current_stage,
        # Segments that start in a known subarea OR level, for the quick-select
        # banner (filtered client-side by the current subarea/level). `enabled`
        # is carried so the castle banner can keep its enabled-only rule while
        # the Bowser banner still shows a DISABLED pipe-entry segment (clicking
        # its "no reds" option enables it — the mutual-exclusion with "reds").
        "segment_targets": [
            {"segment_id": d.id, "name": d.name, "enabled": d.enabled,
             "start_areas": areas, "start_levels": levels,
             # active strat + its medal for the banner cell, graded by THE
             # shared path (_strat_rank/grading_basis, same as rank_by_star
             # and the route medals) so a cell can never disagree with the
             # section banner for the same strat.
             "strat": (seg_strat := masked(
                 service.strat_by_segment.get(d.id),
                 (seg_ek := entity_key(None, None, d.id)))),
             "rank": _strat_rank(
                 service.ranks, seg_ek, seg_strat,
                 grading_basis(
                     rank_mode,
                     pbs_by_strat.get(("segment", d.id, "rta", seg_strat)),
                     attempts_by_seg.get(d.id, []), seg_strat, "rta"))}
            # EVERY def is included — a fully location-less start (e.g.
            # reset_game) gets empty start_areas/start_levels, which the
            # banner's area/level filters treat as no match, but the
            # armed-segment union (stagebanner.js armedExtraCells) can still
            # surface it: a RUNNING segment must never be invisible (spec
            # addendum 2026-07-24).
            for d in service.segment_defs
            for areas, levels in ((_segment_start_areas(d.start_triggers),
                                   _segment_start_levels(d.start_triggers)),)],
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
                    deleted_strats: dict) -> str | None:
    """Rank for one route candidate under its active strat, graded by the
    rank-mode basis (per-strategy: another strat's times never count).
    `by_star`/`by_seg` are the caller's one-pass attempt groupings (id order)
    so a route with many candidates never rescans the attempt list.
    `deleted_strats` is the deleted_strats KV (read once per route view build,
    by the caller) — a tombstoned active strat is masked to None here before
    grading, same rule as build_session_view's `masked` helper, so a deleted
    strategy can't keep showing a route medal."""
    if service.ranks is None:
        return None  # skip the lookups entirely when nothing can be graded
    if c["type"] == "segment":
        ek = entity_key(None, None, c["segment_id"])
        strat = service.strat_by_segment.get(c["segment_id"])
        clock = "rta"
        history = by_seg.get(c["segment_id"], [])
        if strat in deleted_strats.get(ek, []):
            strat = None
        pb = (db.current_pb(None, None, "rta", segment_id=c["segment_id"],
                            strat_tag=strat) if strat else None)
    else:
        ek = entity_key(c["course"], c["star"])
        strat = service.strat_by_star.get((c["course"], c["star"]))
        clock = "igt"
        history = by_star.get((c["course"], c["star"]), [])
        if strat in deleted_strats.get(ek, []):
            strat = None
        pb = (db.current_pb(c["course"], c["star"], "igt", strat_tag=strat)
              if strat else None)
    return _strat_rank(service.ranks, ek, strat,
                       grading_basis(mode, pb, history, strat, clock))


def build_route_view(db, service, route_id: int) -> dict:
    """Resolve a route for display: each step's candidates get names, plus the
    per-step success rate and cumulative product (tracking/routes.route_stats).
    A candidate whose segment was deleted is marked broken (no cascade).
    Each step gains 'rank' (best-ranked candidate); the route view gains
    'avg_rank' (nearest-tier mean of step ranks) and 'weakest_step' index."""
    route = next((r for r in db.routes() if r["id"] == route_id), None)
    if route is None:
        raise LookupError(f"route {route_id} not found")
    attempts = db.attempts()
    rank_mode = db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    if rank_mode not in classify.RANK_MODES:   # forward-safe: junk reads as pb
        rank_mode = classify.DEFAULT_RANK_MODE
    seg_names = {d["id"]: d["name"] for d in db.segment_defs()}
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
                                      by_seg, deleted_strats)
                      for c in step["candidates"]]
        best = max((r for r in ranks_here if r),
                   key=lambda r: classify.RANK_SCORE[r], default=None)
        steps.append({"label": step.get("label"), "need": step["need"],
                      "candidates": cands, "step_rate": st["step_rate"],
                      "cumulative": st["cumulative"], "broken": broken,
                      "rank": best})
    scored = [classify.RANK_SCORE[s["rank"]] for s in steps if s["rank"]]
    avg_rank = None
    weakest_step = None
    if scored:
        mean = sum(scored) / len(scored)
        tier = min(classify.RANK_SCORE, key=lambda n: abs(classify.RANK_SCORE[n] - mean))
        avg_rank = {"score": round(mean, 1), "tier": tier}
        ranked = [(i, classify.RANK_SCORE[s["rank"]]) for i, s in enumerate(steps)
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
