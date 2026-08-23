# src/sm64_events/server/ranks_api.py
"""REST CRUD for rank standards, plus the MARELO scope surface built on top of
them. Same error taxonomy as api.py/replay_api.py: LookupError->404,
ValueError->409, RuntimeError->503 -- `/marelo*` mostly raises HTTPException
directly instead (an unknown scope IS a 404, not a caught LookupError).

`/leaderboard*` (Task 3 of spec 2026-08-20-ranked-leaderboard) is the same
scope machinery pointed at the community sheet instead of the user alone --
see `library/board.py`'s module docstring for the scoring/caching contract."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from sm64_events.library import board
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
    # `classify_entity` recomputes per-entity against each entity's OWN
    # ladder instead -- the SAME door `library/board.py`'s runner breakdown
    # calls, so the user's own numbers and a runner's can never derive this
    # shape two different ways (`tests/test_single_source.py`'s "the entity
    # breakdown shape" row). The scope-level tier/division above
    # (out["tier"]/out["division"]) stays full-table on purpose -- a scope
    # score has no single ladder of its own.
    ladders_by_key = marelo_bridge.entity_ladders(service.ranks, keys)
    for entity in out["entities"]:
        entity["label"] = entity_label(service.db, entity["key"])
        # Always False here: `groups` above was already built from the
        # NON-excluded rankable set, so nothing excluded ever reaches
        # aggregate's numerator/denominator. The excluded rows themselves
        # are appended below, outside the scored block.
        entity["excluded"] = entity["key"] in excluded
        # The `{}` default never actually fires: every key in
        # out["entities"] came from `groups`, which `_groups` built from
        # `rankable_entities` -- and that function's own bar for "rankable"
        # is `scoring.best_ladder(ladders)` being non-empty. So every entity
        # reaching this loop already has a non-empty ladder in
        # `ladders_by_key`, and `classify_entity`'s `score is None` branch
        # (unpracticed) is what actually handles "nothing to grade" -- an
        # EMPTY ladder is a different, structurally unreachable case here,
        # and `defined_tiers({})` would silently walk the full tier table
        # instead of this entity's own if it ever were reached.
        classified = marelo_bridge.classify_entity(
            ladders_by_key.get(entity["key"], {}), entity["score"], out["n"])
        entity["tier"] = classified["tier"]
        entity["division"] = classified["division"]
        # One DIVISION up, not one tier up: `next_tier`/`next_division` name
        # the LP-style near-goal the breakdown's next-rank column exists to
        # show, None exactly when maxed (hardest tier this ladder defines,
        # division I) -- the UI reads that as "Maxed".
        entity["next_tier"] = classified["next_tier"]
        entity["next_division"] = classified["next_division"]
        entity["gain"] = classified["gain"]
    # The attempt that set his fastest PB here, whatever rank mode grades
    # the row -- the Rank tab's ▶ plays its saved replay (round 1, fifth
    # read: "if I have a PB and I've saved a replay for it... I should be
    # able to view any of my PBs on this page"). None when no PB exists;
    # whether a replay is actually obtainable is `/api/replay/available`'s
    # answer, read by the page.
    your_pbs = board.you_pb_by_entity(service.db.pbs(), service.ranks, keys)
    for entity in out["entities"]:
        entity["pb_attempt_id"] = your_pbs.get(entity["key"], {}).get("attempt_id")
    _append_excluded_rows(service, scope_id, groups, excluded, out)
    out["scope_id"] = scope_id
    out["label"] = _scope_label(service, scope_id)
    return out


def absorb_after_regrade(service) -> None:
    """Every rank on every scope just moved for a reason that is not a run --
    the game version setting flipped, so every ladder changed under him. Move
    every watermark to the scope's NEW key without celebrating: the same
    "arriving absorbs" rule `_build_marelo` applies to a scope switch, applied
    to every watermarked scope at once. Without this the next /api/marelo
    fetch (the store refetches on game_version_changed) reads a Silver->Gold
    crossing as earned and fires the full-screen MARELO takeover for a rank
    he did not run for (found in the whole-branch review, 2026-08-15,
    reproduced end to end); and since sync_watermark follows the drop back,
    every flip up would fire it again. Both directions: a scope re-graded
    LOWER has its watermark lowered too (what sync_watermark would do on the
    next build anyway), so a later real climb still celebrates from the
    right floor."""
    if service.db is None or service.ranks is None:
        return
    watermarks = service.marelo_watermarks()
    for scope_id in list(watermarks):
        try:
            scored = _score_scope(service, scope_id)
        except (LookupError, ValueError):
            continue                        # a scope that no longer resolves
        if not scored["tier"]:
            continue
        watermarks[scope_id] = int(scoring.progression_key(scored["tier"],
                                                           scored["division"]))
    service.db.set_state("marelo_watermarks", watermarks)


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
    # One cache per running app, same lifetime as `dead_videos` above -- a
    # module-level singleton would leak one test's cached board into an
    # unrelated test whose inputs merely look identical.
    board_cache = board.RatingsCache()
    router = APIRouter(prefix="/api")

    @router.get("/ranks/standards")
    def get_standards(entity: str | None = None, version: str | None = None):
        """`version` ("us"/"jp") resolves the WHOLE payload -- strategies,
        overall, owners, cutoff links -- on that game version; absent, it is
        the grading version (what the setting says). This is the standards
        panel's visual JP/US switch: it asks by name and grades nothing."""
        if service.ranks is None:
            raise HTTPException(503, "rank standards unavailable")
        if entity is None:
            return service.ranks.to_json()
        if version is not None and version not in ("us", "jp"):
            raise HTTPException(400, f"unknown game version {version!r}")
        resolved = version or service.ranks.grading_version
        ladders = service.ranks.ladders(entity, resolved)
        alive = lambda clips: [c for c in clips if c[1] not in dead_videos]
        extra_clips = {strat: alive(clips) for strat, clips
                       in _library_clips(entity).items()}
        return {"entity": entity, "clock": service.ranks.clock_for(entity),
                "strategies": ladders,
                # Which version the ladders above are resolved on, and which
                # one grading is on right now -- when they differ the panel
                # says so ("Viewing JP standards · you are graded on US")
                # rather than letting a rank that does not move read as a bug.
                "version": resolved,
                "grading_version": service.ranks.grading_version,
                # The OTHER version's answer to the same question, and the
                # names that actually differ between the two: the editor draws
                # a US and a JP field side by side from one fetch, and opens
                # the JP field only where a JP time is annotated.
                "strategies_us": service.ranks.ladders(entity, "us"),
                "strategies_jp": service.ranks.ladders(entity, "jp"),
                "jp_strategies": service.ranks.jp_strategies(entity),
                # Which of those the user can CLEAR (their overlay is in his
                # file); a sheet-fitted JP ladder is not, and the editor's
                # checkbox says so instead of offering a click that no-ops.
                "clearable_jp_strategies": service.ranks.clearable_jp_strategies(entity),
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
                    entity, extra_clips, dead_urls=dead_videos, version=resolved),
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
    async def put_threshold(entity: str, strategy: str, rank: str, body: ThresholdBody,
                            version: str = "us"):
        """`?version=jp` writes the strategy's JP time for that rank (its JP
        overlay); the default writes the base ladder both versions share."""
        if version not in ("us", "jp"):
            raise HTTPException(400, f"unknown game version {version!r}")
        try:
            await service.set_rank_threshold(entity, strategy, rank, body.seconds,
                                             version=version)
        except (LookupError, ValueError, RuntimeError) as e:
            raise _http(e)
        return {"ok": True}

    @router.delete("/ranks/standards/{entity}/{strategy}/jp")
    async def clear_jp(entity: str, strategy: str):
        """The editor's "JP timed differently" toggle turned OFF: drops the
        strategy's JP overlay so both versions grade on its base ladder."""
        try:
            await service.clear_rank_jp(entity, strategy)
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

    # The three /leaderboard routes are plain `def`, never `async def`: FastAPI
    # threadpools a sync route's WHOLE body, and everything here -- `_groups`,
    # the PB reads, the 29ms rating build -- would otherwise run on the
    # poller's own asyncio loop, one game frame being 33.3ms (measured 13.32ms
    # per request when this was `async`; `tests/test_ranks_api_marelo.py`
    # pins the sync-ness). `library` is never None in the running app
    # (`server/app.py` loads a `LibraryStore` unconditionally); the guards
    # below exist for a test that builds this router with no library at all.

    def _rated_sheet() -> board.RatedSheet:
        """Every runner rated on the CURRENT sheet/adoptions/standards/
        version -- cached, rebuilt only when one of those moves."""
        adoptions_rows = adoptions.rows() if adoptions is not None else {}
        return board_cache.current(library, adoptions_rows, service.ranks,
                                   version=service.ranks.grading_version)

    def _you_scores(keys: list[str]) -> dict[str, float]:
        """The user's own per-entity scores, graded PB-basis ALWAYS -- a
        leaderboard compares everyone on the same basis, whatever
        `rank_mode` the Rank tab happens to be showing him. `attempts=()`
        because pb mode reads only `pb_rows`; passing `db.attempts()` was
        12.35ms of dead weight per request."""
        return marelo_bridge.entity_scores((), service.ranks,
                                           keys, "pb", service.db.pbs())

    def _require_ranks():
        if service.ranks is None or service.db is None:
            raise HTTPException(503, "rank standards unavailable")

    def _scope_groups(scope_id: str) -> list[dict]:
        """Scope membership WITH the user's own exclusion set -- the same
        resolution his own `/api/marelo` grades on. Until round 1's third
        read (2026-08-23) this passed `excluded=set()` so a runner's
        denominator could not shrink by his choices; his ruling reversed
        it: "it should also be excluded for all of the fake leaderboards &
        their pages as well"."""
        return _groups(service, scope_id)

    @router.get("/leaderboard")
    def leaderboard(scope: str | None = None):
        """The [[Rank board]] for one scope: every community runner scored
        the way MARELO scores the user, plus the user's own row. `omitted`
        is how many rated runners have nothing in this scope and so got no
        row -- the UI must show it (`board.py::RatedSheet._scope_rows`)."""
        _require_ranks()
        scope_id = scope or _active_scope(service)
        groups = _scope_groups(scope_id)          # 404s an unknown scope first
        body = {"scope_id": scope_id, "label": _scope_label(service, scope_id),
                "basis": "pb", "rank_mode": _rank_mode(service)}
        if library is None:
            return {**body, "n": 0, "sheet_revision": None, "rows": [], "omitted": 0}
        keys = [key for group in groups for key in group["candidates"]]
        you_aggregate = scopes.aggregate(_you_scores(keys), groups)
        rows, omitted = _rated_sheet().leaderboard(
            scope_id, groups, you_aggregate=you_aggregate)
        return {**body, "n": you_aggregate["n"], "sheet_revision": library.revision,
                "rows": rows, "omitted": omitted}

    @router.get("/leaderboard/runner/{name:path}/summary")
    def leaderboard_runner_summary(name: str):
        """The [[Runner page]]'s scope chip row -- the same chip shape
        `/api/marelo/summary` returns, sourced from this runner. Registered
        ahead of the bare `{name:path}` route below: a path converter is
        greedy and would otherwise swallow `.../summary` into the name."""
        _require_ranks()
        if library is None:
            raise HTTPException(404, f"unknown runner {name!r}")
        scope_specs = [(scope_id, _scope_groups(scope_id), _scope_label(service, scope_id))
                       for scope_id in _summary_scope_ids(service)]
        chips = _rated_sheet().runner_summary(name, scope_specs)
        if chips is None:
            raise HTTPException(404, f"unknown runner {name!r}")
        return {"chips": chips}

    @router.get("/leaderboard/runner/{name:path}")
    def leaderboard_runner(name: str, scope: str | None = None):
        """One runner's scoped rating per entity, each widened with the
        user's own score/time/tier/division on the same entity -- the same
        field set `/api/marelo` returns plus `runner`, so `Breakdown` and
        `CoverageStrip` render either source unchanged."""
        _require_ranks()
        if library is None:
            raise HTTPException(404, f"unknown runner {name!r}")
        scope_id = scope or _active_scope(service)
        groups = _scope_groups(scope_id)
        keys = [key for group in groups for key in group["candidates"]]
        breakdown = _rated_sheet().runner_breakdown(
            name, groups, you_scores=_you_scores(keys),
            you_times=board.you_times_by_entity(service.db.pbs(), service.ranks, keys),
            label_of=lambda key: entity_label(service.db, key))
        if breakdown is None:
            raise HTTPException(404, f"unknown runner {name!r}")
        return {**breakdown, "scope_id": scope_id,
                "label": _scope_label(service, scope_id)}

    return router
