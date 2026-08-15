# src/sm64_events/server/ranks_api.py
"""REST CRUD for rank standards, plus the MARELO scope surface built on top of
them. Same error taxonomy as api.py/replay_api.py: LookupError->404,
ValueError->409, RuntimeError->503 -- `/marelo*` mostly raises HTTPException
directly instead (an unknown scope IS a 404, not a caught LookupError)."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.links import xcams_url
from sm64_events.memory.addresses import COURSE_NAMES
from sm64_events.ranks import classify, history, scopes, scoring
from sm64_events.tracking import marelo as marelo_bridge
from sm64_events.tracking.views import entity_label, segment_courses


def _http(e: Exception) -> HTTPException:
    if isinstance(e, LookupError):
        return HTTPException(404, str(e))
    if isinstance(e, ValueError):
        return HTTPException(409, str(e))
    return HTTPException(503, str(e))


class ThresholdBody(BaseModel):
    seconds: float


class StrategyBody(BaseModel):
    strategy: str
    #: 0-5, the star a 100-coin run ends on. The server QUALIFIES the name with
    #: that variant's label and returns what it stored, so no client ever
    #: composes a variant-qualified name itself.
    exit_star: int | None = None


class VideoBody(BaseModel):
    url: str


class ModeBody(BaseModel):
    mode: str


class ExcludeBody(BaseModel):
    entity: str
    excluded: bool


class AckBody(BaseModel):
    """Dismisses a SCOPE celebration. `scope` is optional in the schema only
    so the endpoint can answer a missing one with its own 400 rather than a
    422 -- it is required in practice. The `entity` field this carried
    between task-f1 and task 0012 is gone with the per-entity celebrations
    themselves; an out-of-date client still sending one falls through to
    that same 400 instead of being silently accepted."""
    scope: str | None = None
    key: int


def _active_scope(service) -> str:
    """The focus route IS the scope (spec section 3.4) -- there is no second
    control. No route selected means Overall."""
    active = service.active_route()
    return f"route:{active['id']}" if active else "overall"


def _rank_mode(service) -> str:
    mode = service.db.get_state("rank_mode", classify.DEFAULT_RANK_MODE)
    return mode if mode in classify.RANK_MODES else classify.DEFAULT_RANK_MODE


def _groups(service, scope_id: str, excluded: set[str] | None = None):
    """Resolve a scope or 404. Segment->course comes from each definition's
    start levels, the same source the stage banner uses.

    `excluded` is the set actually applied to `rankable_entities`; the
    default (None) uses the user's real exclusion set
    (service.rank_excluded()). Passing an EMPTY set resolves scope membership
    WITHOUT the exclusion filter -- the second resolution
    `_append_excluded_rows` uses to recover excluded rows for display without
    letting them back into the aggregate."""
    if service.ranks is None or service.db is None:
        raise HTTPException(503, "rank standards unavailable")
    ladders = {key: service.ranks.ladders(key)
               for key in service.ranks.to_json()["entities"]}
    rankable = scopes.rankable_entities(
        ladders, service.rank_excluded() if excluded is None else excluded)
    groups = scopes.entity_groups(
        scope_id, rankable=rankable, routes=service.db.routes(),
        segment_courses=segment_courses(service.db))
    if groups is None:
        raise HTTPException(404, f"unknown scope {scope_id!r}")
    return groups


def _append_excluded_rows(service, scope_id: str, groups: list[dict],
                          excluded: set[str], out: dict) -> None:
    """Exclusion must be reversible from the UI, not just a raw db edit.
    scores.aggregate() never sees an excluded entity -- it left the rankable
    set before `groups` was even built -- so without this, an excluded row
    could never be found again in the response to flip `excluded` back off
    (the `entity["excluded"]` field on aggregate's own rows was consequently
    always False; dead code). Resolve the scope's membership a SECOND time
    with NO exclusion filter and append the difference as inert rows: no
    score/tier/division, gain 0.0 (an excluded entity earns the scope
    nothing while it stays out).

    Appended, not interleaved: an excluded entity holds no K-of-N slot (only
    aggregate() assigns those, and it never saw this entity), so there is no
    live position to interleave it into. Within the appended block, order
    follows the SAME group/step order aggregate() would have used, so a
    route-ordered UI still reads sensibly for the excluded tail; a
    gain-ordered UI trails them regardless, since gain=0.0 is the floor."""
    if not excluded:
        return
    all_groups = _groups(service, scope_id, excluded=set())
    present_keys = {key for group in groups for key in group["candidates"]}
    seen: set[str] = set()
    for group in all_groups:
        for key in group["candidates"]:
            if key in present_keys or key in seen:
                continue
            seen.add(key)
            out["entities"].append({
                "key": key, "score": None, "gain": 0.0,
                "label": entity_label(service.db, key),
                "excluded": True, "tier": None, "division": None,
                # Same "no score yet" shape the scored loop above gives an
                # unpracticed entity -- an excluded row is unscored too, it
                # just got there by choice instead of by never being played.
                "next_tier": scopes.UNPRACTICED_TARGET_TIER,
                "next_division": None})


def _score_scope(service, scope_id: str) -> dict:
    """The pure scoring path for one scope: groups -> entity scores ->
    aggregate -> per-entity tier/division/gain -> excluded rows appended.
    Touches no watermark state. `_build_marelo` layers the watermark
    sync/seed + celebration side effects on top of this; any caller that
    must NOT disturb them (a summary sweep over several scopes) calls this
    directly instead of `_build_marelo`."""
    groups = _groups(service, scope_id)
    keys = [key for group in groups for key in group["candidates"]]
    scored = marelo_bridge.entity_scores(service.db.attempts(), service.ranks,
                                         keys, _rank_mode(service),
                                         service.db.pbs())
    out = scopes.aggregate(scored, groups)
    excluded = service.rank_excluded()
    # aggregate() graded tier/division/gain against the FULL tier table --
    # it only sees scores, not ladders. A ragged ladder (one missing a tier)
    # still crosses that tier's score range, so a full-table lookup can name
    # a tier the ladder does not define (scoring.py's invariant, line 8).
    # Recompute per-entity against each entity's OWN ladder here, where the
    # ladders are actually available; the scope-level tier/division above
    # (out["tier"]/out["division"]) stays full-table on purpose -- a scope
    # score has no single ladder of its own.
    defined_by_key = {key: scoring.defined_tiers(ladder) for key, ladder in
                      marelo_bridge.entity_ladders(service.ranks, keys).items()}
    for entity in out["entities"]:
        entity["label"] = entity_label(service.db, entity["key"])
        # Always False here: `groups` above was already built from the
        # NON-excluded rankable set, so nothing excluded ever reaches
        # aggregate's numerator/denominator. The excluded rows themselves
        # are appended below, outside the scored block.
        entity["excluded"] = entity["key"] in excluded
        defined = defined_by_key.get(entity["key"])
        if entity["score"] is None:
            entity["tier"] = entity["division"] = None
            # No score to step up from -- the breakdown's "next rank" column
            # names what a FIRST practiced attempt targets (spec task C.3),
            # the same Gold anchor gain_for below already grades unpracticed
            # entities against. No division: there is nothing to be a
            # division INTO yet.
            entity["next_tier"] = scopes.UNPRACTICED_TARGET_TIER
            entity["next_division"] = None
        else:
            entity["tier"], entity["division"] = scoring.division_for(
                entity["score"], defined)
            # One DIVISION up, not one tier up: `next_tier_target` (used by
            # gain_for below) answers "how much score is the next TIER
            # worth", the whole-ladder quest; `division_progress` answers
            # "what's the very next step", the LP-style near-goal the
            # breakdown's next-rank column exists to show. `next_tier`/
            # `next_division` are None exactly when maxed (hardest tier this
            # ladder defines, division I) -- the UI reads that as "Maxed".
            next_step = scoring.division_progress(entity["score"], defined)
            entity["next_tier"] = next_step["next_tier"]
            entity["next_division"] = next_step["next_division"]
        entity["gain"] = scopes.gain_for(entity["score"], out["n"], defined)
    _append_excluded_rows(service, scope_id, groups, excluded, out)
    out["scope_id"] = scope_id
    out["label"] = _scope_label(service, scope_id)
    return out


def _build_marelo(service, scope_id: str) -> dict:
    out = _score_scope(service, scope_id)
    out["celebration"] = None
    if out["tier"]:
        key = scoring.progression_key(out["tier"], out["division"])
        service.sync_watermark(scope_id, key)          # follow a drop down
        # ONLY the active scope may celebrate, and only when arriving here was
        # not itself the thing that made it active (live report 2026-07-28:
        # "Swapping between routes like that should never trigger any rank
        # up"). Two rules, and each covers a hole the other cannot:
        #
        #   * `scope_id == active` stops the RANK TAB firing one. Browsing the
        #     scope chips fetches /api/marelo?scope=<other>, which is looking
        #     at a rating, not earning it.
        #   * `note_active_scope` stops the SWITCH itself firing one. A
        #     watermark could only ever be raised by ack_celebration -- i.e.
        #     by a celebration having been SHOWN -- so every scope held a
        #     rank-up it had never displayed and discharged it the moment the
        #     user looked at that scope.
        #
        # Arriving ABSORBS instead: the rank a scope already holds is the new
        # baseline. The cost, decided by the user rather than assumed: scopes
        # overlap (one star feeds many routes), so a rank-up genuinely earned
        # on a route you were not focused on is absorbed silently and never
        # celebrated. The rank itself is still there to see.
        active = _active_scope(service)
        if scope_id == active:
            if service.note_active_scope(active):
                service.absorb_watermark(scope_id, key)
            else:
                out["celebration"] = scopes.celebration_delta(
                    out["tier"], out["division"],
                    service.marelo_watermarks().get(scope_id))
        # A scope's FIRST rank is not a rank-up. Seeding it silently is what
        # stops the first view of a scope celebrating the user's whole
        # history at once. seed_watermark is a no-op once the key exists.
        service.seed_watermark(scope_id, key)
    # There is NO per-entity celebration here any more (task 0012,
    # 2026-07-26). A star's or segment's own rank-up is performed live by the
    # rank banner climbing (ui/rankclimb.js) rather than held as a payload to
    # be shown and acked later, so nothing needs a watermark and nothing needs
    # this endpoint to score the whole rankable corpus a second time on every
    # request for a non-overall scope.
    return out


_SUMMARY_CHIP_CAP = 6


def _summary_scope_ids(service) -> list[str]:
    """overall, then every route whose category begins "Main Categories",
    then the active scope if not already present -- the fixed op.gg-style
    chip row order (spec Task A). Capped last, so a large route library
    can't turn the always-visible row into a second scope picker."""
    scope_ids = ["overall"]
    for route in service.db.routes():
        if (route["category"] or "").startswith("Main Categories"):
            scope_ids.append(f"route:{route['id']}")
    active_scope_id = _active_scope(service)
    if active_scope_id not in scope_ids:
        scope_ids.append(active_scope_id)
    return scope_ids[:_SUMMARY_CHIP_CAP]


def _summary_chip(service, scope_id: str) -> dict:
    """A leaner /api/marelo payload for one scope: same scoring path
    (`_score_scope`), no `entities`/`celebration` -- op.gg's chip needs a
    tier badge and a number, not a breakdown."""
    scored = _score_scope(service, scope_id)
    return {"scope_id": scope_id, "label": scored["label"],
            "tier": scored["tier"], "division": scored["division"],
            "marelo": scored["marelo"], "n": scored["n"],
            "practiced": scored["practiced"]}


def _scope_label(service, scope_id: str) -> str:
    for scope in scopes.scope_list(routes=service.db.routes(),
                                   courses=COURSE_NAMES):
        if scope["id"] == scope_id:
            return scope["label"]
    return scope_id


def create_ranks_router(service, library=None, adoptions=None,
                        video_checks_path=None) -> APIRouter:
    """`library`/`adoptions` (the LibraryStore + Adoptions app.py already
    builds) let the standards payload widen its example clips with library
    entries (task 0098); both optional so every existing caller — and a
    broadcast-only instance — keeps its exact behaviour. `video_checks_path`
    overrides where the liveness verdicts load from (tests; production takes
    the bundled seed)."""

    def _library_clips(entity: str) -> dict:
        if library is None or service.ranks is None:
            return {}
        from sm64_events.library.examples import example_clips
        rows = adoptions.rows() if adoptions is not None else {}
        return example_clips(library.payload, rows, entity,
                             service.ranks.has_jp_ladder)

    # Videos the liveness sweep (tools/check_videos.py) marked gone — round 2
    # of task 0098: a dead video must never be THE example a standard links
    # to. Loaded once per process, like every other bundled seed; an absent
    # file filters nothing.
    from sm64_events.core.paths import bundled_video_checks
    from sm64_events.library import videocheck
    dead_videos = videocheck.dead_urls(videocheck.load_checks(
        video_checks_path or bundled_video_checks() or ""))
    router = APIRouter(prefix="/api")

    @router.get("/ranks/standards")
    def get_standards(entity: str | None = None):
        if service.ranks is None:
            raise HTTPException(503, "rank standards unavailable")
        if entity is None:
            return service.ranks.to_json()
        ladders = service.ranks.ladders(entity)
        alive = lambda clips: [c for c in clips if c[1] not in dead_videos]
        extra_clips = {strat: alive(clips) for strat, clips
                       in _library_clips(entity).items()}
        return {"entity": entity, "clock": service.ranks.clock_for(entity),
                "strategies": ladders,
                # THE entity's own ladder -- the pointwise best across every
                # strategy, which is what `views.entity_rank` grades against
                # and therefore what "rank up OVERALL" actually costs. It has
                # never been showable before: the entity's RANK had a banner
                # from the beginning, its STANDARDS had no surface at all, so
                # the only cutoffs anyone could read were per-strategy ones
                # (user, 2026-08-10: "make it very clear what it takes for you
                # to rank up overall, versus rank up per strategy"). Served in
                # SECONDS like `strategies`, so one formatter reads both, and
                # derived HERE rather than in the browser because a second
                # pointwise-min in JS is the divergence this project has a
                # rule against.
                "overall": {rank: cs / 100 for rank, cs
                            in scoring.best_ladder(ladders).items()},
                "overall_owners": scoring.best_ladder_owners(ladders),
                # Which of the names above (keys of "strategies") came off the
                # Ultimate Sheet rather than community-vetted standards --
                # ranks.is_fitted's own contract, exposed as a sibling LIST
                # rather than a per-strategy bool, since "strategies" is a
                # {name: ladder} dict and a "fitted" key inside a ladder
                # would collide with its own tier names (Mario, Bronze, ...).
                "fitted_strategies": service.ranks.fitted_strategies(entity),
                # `videos` and both clip pools are filtered through the
                # liveness verdicts (round 2): a URL the sweep marked dead
                # never reaches a link. `alive` is applied to every pool in
                # ONE place each, and `cutoff_videos` applies the same set
                # internally, so the tier links and the subdivision links
                # cannot disagree about whether a clip exists.
                "videos": {strat: url for strat, url
                           in service.ranks.videos(entity).items()
                           if url not in dead_videos},
                "cutoff_videos": service.ranks.cutoff_videos(
                    entity, extra_clips, dead_urls=dead_videos),
                # The RAW pool those links were resolved from (vetted xcams
                # clips + library entries, task 0098), per strategy, as
                # [[time_cs, url], ...]. The browser bands these into
                # SUBDIVISION examples itself via librarymodel.js::bandsOf —
                # the same walk the Library page files entries with — so the
                # expanded standards rows and the Library cannot disagree
                # about which division a clip belongs to, and a division-level
                # resolver here would be a second door onto that rule.
                "clips": {strat: (alive(service.ranks.clips(entity).get(strat, []))
                                  + extra_clips.get(strat, []))
                          for strat in ladders},
                # Which of those URLs are LIBRARY entries (round 3): a time
                # link deep-links into the Library only when there is an entry
                # card to land on — a vetted-only xcams URL keeps the plain
                # external behaviour, since arriving nowhere reads as broken.
                "library_urls": sorted({url for clips in extra_clips.values()
                                        for _cs, url in clips}),
                "user_videos": service.ranks.user_videos(entity),
                "seeded": service.ranks.seeded_strategies(entity),
                # Grouping is resolved HERE, not in the browser: a 100-coin
                # star's strategies are variant-qualified, and a second
                # implementation of "which variant is this" in JS is the
                # divergence this project has a rule against. [] for every
                # ordinary entity, which is what keeps the renderer flat.
                "strategy_groups": service.ranks.strategy_groups(entity),
                "exit_variants": service.ranks.exit_variants(entity),
                "exit_star_options": service.ranks.exit_star_options(entity),
                "xcams_url": xcams_url(entity)}

    @router.put("/ranks/standards/{entity}/{strategy}/{rank}")
    async def put_threshold(entity: str, strategy: str, rank: str, body: ThresholdBody):
        try:
            await service.set_rank_threshold(entity, strategy, rank, body.seconds)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.put("/ranks/mode")
    async def put_mode(body: ModeBody):
        try:
            await service.set_rank_mode(body.mode)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.put("/ranks/standards/{entity}/{strategy}/{rank}/video")
    async def put_video(entity: str, strategy: str, rank: str, body: VideoBody):
        try:
            await service.set_rank_video(entity, strategy, rank, body.url)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/ranks/standards/{entity}/{strategy}/{rank}/video")
    async def delete_video(entity: str, strategy: str, rank: str):
        try:
            await service.clear_rank_video(entity, strategy, rank)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/ranks/standards/{entity}")
    async def create_strategy(entity: str, body: StrategyBody):
        try:
            stored = await service.create_rank_strategy(
                entity, body.strategy, exit_star=body.exit_star)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        # The caller needs the STORED name to address its follow-up threshold
        # and video PUTs — it differs from what was posted whenever an exit
        # star qualified it.
        return {"ok": True, "strategy": stored}

    @router.delete("/ranks/standards/{entity}/{strategy}")
    async def delete_strategy(entity: str, strategy: str, purge: bool = False):
        try:
            if purge:
                await service.purge_strategy(entity, strategy)
            else:
                await service.delete_rank_strategy(entity, strategy)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/ranks/standards/{entity}/reset")
    async def reset_entity(entity: str):
        try:
            await service.reset_rank_entity(entity)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.get("/marelo/scopes")
    def marelo_scopes():
        if service.ranks is None or service.db is None:
            raise HTTPException(503, "rank standards unavailable")
        return {"scopes": scopes.scope_list(routes=service.db.routes(),
                                            courses=COURSE_NAMES),
                "active": _active_scope(service)}

    @router.get("/marelo")
    def marelo(scope: str | None = None):
        return _build_marelo(service, scope or _active_scope(service))

    @router.get("/marelo/summary")
    def marelo_summary():
        """The always-visible chip row (op.gg season-tier badges): overall,
        every "Main Categories" route, and the active scope, one aggregate
        per chip via `_score_scope` -- never `_build_marelo`, so this can
        never seed or lower a celebration watermark for a scope the user
        has not actually opened."""
        if service.ranks is None or service.db is None:
            raise HTTPException(503, "rank standards unavailable")
        return {"chips": [_summary_chip(service, scope_id)
                          for scope_id in _summary_scope_ids(service)]}

    @router.get("/marelo/history")
    def marelo_history(scope: str | None = None):
        scope_id = scope or _active_scope(service)
        groups = _groups(service, scope_id)
        mode = _rank_mode(service)
        keys = [key for group in groups for key in group["candidates"]]
        ladders = marelo_bridge.entity_ladders(service.ranks, keys)

        def scorer(key, frames):
            # `progress_for_time`, not `score_for`: it carries the ladder's
            # displayed-centisecond boundary rule, and this chart has to end
            # on the SAME number the card above it shows (see the comment
            # below about sharing a source) -- a raw curve score would put
            # its last point a hair under a division edge the card has
            # already awarded.
            ladder = ladders.get(key)
            return None if ladder is None else scoring.progress_for_time(
                ladder, classify.display_cs(frames))["score"]

        # Same source as the RATING, mode for mode (tracking/marelo.py): the
        # saved pbs in pb mode, every success in the averages. A chart drawn
        # from a different source than the card above it ends on a different
        # number, and _decimate always keeps the newest point.
        feed = (marelo_bridge.pb_feed(service.db.pbs(), service.ranks.clock_for)
                if classify.RANK_MODES[mode]["order"] is None
                else marelo_bridge.successes_for(service.db.attempts(),
                                                 service.ranks.clock_for))
        return {"scope_id": scope_id,
                "points": history.history_series(feed, groups, scorer, mode)}

    @router.get("/marelo/exclusions")
    def marelo_exclusions():
        """The raw exclusion set, for surfaces that need one entity's state
        without scoring a whole scope — the strategy modal's "include in
        ranking" tick (spec round 7). `/api/marelo` carries `excluded` per
        entity already, but it costs a full scope aggregation and only
        covers entities inside that scope; a modal opened on a star with no
        standards yet is in no scope at all."""
        return {"excluded": sorted(service.rank_excluded())}

    @router.post("/marelo/exclude")
    async def marelo_exclude(body: ExcludeBody):
        try:
            await service.set_rank_excluded(body.entity, body.excluded)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.post("/marelo/ack")
    async def marelo_ack(body: AckBody):
        """Dismisses a SCOPE celebration (the full-screen MARELO overlay).

        The `{entity, key}` arm this used to accept is gone with task 0012
        (2026-07-26): a per-entity rank-up is now performed live by the rank
        banner climbing, so there is no held celebration to acknowledge. An
        entity ack is a 400 rather than a silent no-op — a client still
        sending one is out of date, and answering "ok" would hide that."""
        if body.scope is None:
            raise HTTPException(400, "ack needs a scope")
        try:
            await service.ack_celebration(body.scope, body.key)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    return router
