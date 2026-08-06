---
paths:
  - "src/sm64_events/tracking/**"
  - "src/sm64_events/storage/**"
  - "src/sm64_events/stats/**"
  - "src/sm64_events/data/**"
  - "tools/corpus_*.py"
  - "tools/build_defaults_seed.py"
  - "tools/dedupe_journal.py"
  - "tests/test_defaults_corpus*.py"
  - "tests/test_build_defaults_seed.py"
---

# Tracking, storage, stats — where to change what

| To change... | Edit |
|---|---|
| Segment defs, trigger vocabulary, matcher FSM | `tracking/segments.py` — ONE registry (TRIGGERS/GUARDS) drives validation, matching, and the /api/segments/vocab endpoint; docstring carries the FSM invariants (closures before arming, guards re-evaluated every arm, silent disarm on foreign level change, position-gated anchor closures: retry re-arms in place at the arm position, a warp elsewhere disarms with no row and swaps to the destination's segment, load/door/save-prompt-echo shapes). **ARM-POSITION gate** (`can_run_from`, live report 2026-07-27) — **full detail below: [The arm-position gate](#the-arm-position-gate)**; **TOPOLOGICAL VALIDITY** (`_flush_move`, live report 2026-08-01) — **full detail below: [Topological validity](#topological-validity)**; GuardType.phase — close-phase `min_time`/`max_time` rows are declarative validity bounds read by projection (`time_bounds`), arm-phase `last_star_grabbed`/`last_star_attempted` gate on MatchContext's last-star memory. **Multi-step segments** (spec 2026-07-23-default-routes-foundation): `SegmentDef.waypoints` is an ordered list of middle any-of clause-sets (`[]` = plain start/end pair, byte-for-byte unchanged). A waypoint-bearing def's armed branch runs `SegmentEngine._feed_waypoint` with its own precedence: end (only once every waypoint is consumed) > death/game_reset (hard fail, row) > `session_started` (silent disarm) > echo anchor (invisible, shares `_anchor_echo`) > real anchor (rewinds `progress` to 0, re-arms in place — **records a RESET row for the attempt that ends there**, round 2 live report 2026-07-30, subject to the same AFK/unacted discard `_feed_strict` applies; this used to record no row at all, the "live-gate VERIFY" this line named until the user settled it — the practice log is how he sees his own retries, and a whole class of segment silently omitting them made it lie about what he did) > next waypoint (advance, no row) > **the anchor branch is additionally gated by `_arrived_by_a_real_move` (2026-08-03) -- full detail below: [A pause exit is not a retry](#a-pause-exit-is-not-a-retry)** > major action (`star_collected`/`key_grabbed`/a real-edge `level_changed` that isn't the next waypoint → SILENT cancel, no row) > transparent (`area_changed`/`warp_entered`/`spawned` stay invisible). Authoring caveat (docstring): a def's start trigger must be at least as specific as any waypoint clause it could collide with, or the same event that cancels the sequence can satisfy the start trigger and re-arm same-tick. **Route-scoped arming**: `MatchContext.route_segments`/`target_segment` (fed from the journaled `route_selected` event / current segment target) back the opt-in `in_active_route` guard (real arm gate = module-level `_route_allows(d, ctx)`); unguarded defs (all 10 pre-existing) are unaffected. **`SegmentDef.default_strat`** (spec 2026-07-24-segment-default-strat): the strategy the segment is practiced with unless the user picks another — the 56 castle movements carry `"Standard"`; the 10 legacy tricks, the 3 reds-to-pipe and the 15 100-coin exits carry None, as does every user-created def. The matcher stays strategy-blind: it is `validate_definition`-checked here (non-empty string or absent) and APPLIED by the projector. **Segment ORIGIN** (spec 2026-07-24-segment-origin-categories): `start_areas`/`start_levels` (moved DOWN here from views.py 2026-07-26, beside the `arm_level` they already read through, so `tracking/service.py` can ask "does this segment start where the player is standing" without importing the view layer). `start_origin(start_triggers)` maps a definition to the world node it can START in, through the `_ORIGIN_PARAMS` TABLE (one row per trigger type, read as param NAMES like `arm_level` — a new trigger type is one row or defaults to "Anywhere"). NOT arm_level's mapping: a `level_exit` arms at its destination but ORIGINATES at its source (52 of the 53 seeded exits omit `to`; the one that carries it, MIPS Clip, is still filed by its source, which is the point). Most-specific clause wins (a subarea beats the same level without one); conflicting clauses take the first. `origin_course(node)` maps a world node to the COURSE it sits in (None for the castle interior, the hubs and the arenas) — the vocabulary the RETIREMENT rule speaks, and what `views.py` stamps on every segment section as `course_id` so the practice page can ask "does this pinned card still belong where I am standing" without re-deriving anything. NOT `views.segment_courses`, which asks the same question through `start_levels`/`arm_level` and so answers None for every movement that starts in a course. `origin_view()` is the `{key,label,region,region_label}` shape the API stamps; `origin_taxonomy()` is the ordered region→place tree in `vocab()["origins"]`, deliberately domain-free so the picker modal can serve courses/stars through the same renderer. **The recorded TIME is Usamune's IGT, not a `global_timer` delta** (`_close`, live report 2026-07-31) — **full detail below: [A segment's time is Usamune's IGT](#a-segments-time-is-usamunes-igt)** | **Split / merge** (`split_definition`, `merge_definitions`) — **full detail below: [Split and merge](#split-and-merge)**
| Whether an attempt counts as one the player MADE (the no-op discard) | Two doors, and reading only one is how a measurement invents its own findings. `projection.py::_close_by_reset` discards on `_unacted_open() OR NOT ev.payload["mario_acted"]` — the ANCHOR PAYLOAD's flag, which `segments.py`'s two anchor branches also read. `_close_by_death` and `_close` (abandon / hard reset) call `_unacted_open()` alone, so for them only the `mario_acted` EVENT exists. Cost, 2026-08-04: a journal-level simulation of task 0084's detector fix patched the payload and not the event, and reported **27 removed deaths and 13 removed abandons that were pure instrument artefact** — the shape read as a dangerous change until the second door turned up. Anything that rewrites, replays or fabricates activity must move BOTH, and the `acted_tracking` marker gates the payload half only (a historical anchor without it keeps recording, by design) |
| An attempt's DURATION across an involuntary counter restart | `tracking/projection.py::_with_carried_igt` + `_open_carried_igt` — Usamune restarts its overall counter at a subarea load AND at an in-level teleporter, so a closing event that reads that counter reports only the LAST leg. Each involuntary anchor journaled its own pre-restart value; `_dispatch` banks them as they pass and the reset/death closers add them back. **Never the star closer** — that number is Usamune's own result store, already whole-star, so summing would double-count. Live report 2026-08-03: three CCM bridge warps then a reset read `0'08"93` for a run that took `0'25"06` (194+130+160+268 frames). Replayed both ways over all four journals, **exactly one recorded row moves — that one — with zero successes touched, zero `cleared` flags flipped, and no row added or removed** |
| Attempt state machine / projection | `tracking/projection.py` — docstrings carry the two-pass clearing, reset-race row, clear-by-anchor-id invariant, active-star retirement (segment-arm / different-course → target None) + segment-target retirement — **an ARMED segment is exempt in EVERY match mode, and the verdict is DEFERRED until the matcher has had the event (2026-08-03)**: the rule runs in `_dispatch`, which is BEFORE `SegmentEngine.feed`, so it can only read "was armed last event" — safe for a loose def, wrong for a strict one, which is why the exemption used to require `match_mode == "loose"` (`_armed_loosely`, deleted with this change: the deferral subsumes it). Flipping all 56 movements to strict took that exemption away from every one of them, so picking `Bowser 1 → WF` and walking into BitDW — **step 1 of its own declared route** — retired the pick, and `stagebanner.js`'s remembered Reds/Pipe default then filled the hand it found empty (that client guard is correct and never got to run). `_dispatch` now records `_pending_target_retire` and the bottom of `feed()` applies it only if the segment is no longer armed. His rule, and the whole of it: *"If I select a segment, it should be selected until it's no longer possible for it to be armed / it gets invalidated by deviating from the path"* — under the path cursor, deviating already cancels the definition on that very event, so **the matcher's disarm IS the invalidation**. Blast radius measured by replaying both journals under the old rule and the new one and diffing every target reading: **172 differences across 15 episodes in his live journal, ALL of them `None → segment` (a pick kept, never one wrongly held or swapped), on exactly two definitions — the two re-entry movements — and 0 differences across the repo journal's 19,211 events.** — since 2026-07-27 the SAME rule as the star's: entering a course that is not the segment's `segment_origin` retires it, hubs and the castle stay transit for both kinds (`_seg_origins`, caveat 12; it replaced `start_level_set`, which was `arm_level`-based and therefore dead for every castle movement); auto-ignores out-of-range successes (DEFAULT_MIN_FRAMES 0.5s + star `time_filters` KV / segment time guards → cleared with `auto:` reason; journaled clear/restore wins via touched_ids); tracks last star grabbed/attempted for the last_star_* guards (game_reset clears); **segment strategy defaults (caveat 17)**: `strat_by_segment` starts PRE-SEEDED from the defs' `default_strat` instead of empty, which is why no consumer (attempt stamping, section banner, `segment_targets`, target payload) needed a call site of its own — and a journaled `strat_set` with a FALSY tag falls back to the default rather than clearing, so "no strategy" is not a reachable state for a defaulted segment; per-attempt strat reclassification (`strat_overrides` pre-pass, caveat 16 — journaled `attempt_strat_set`, last write wins, applied at both strat_tag stamping sites); **route-scoped arming**: a `route_selected` event threads `self._route_segments` (frozenset of member ids, or None) + `self._active_route_id` into every `MatchContext`; `active_route_id()` is REPLAY-DERIVED (mirrors `armed_route_id()`) — no service-side field, so a mid-session restart never loses the active route; **nothing overwrites a segment pick (2026-08-01, superseding caveat 18)**: `_close_by_grab`'s "last valid grab moves the target" rule now fires only when the target is a star or nothing. A segment target is a CLICK; a grab is a thing that happened, and the second may not overwrite the first. Grabbing the star you leave a course with is the ordinary prelude to running that course's exit movement, so it fired at exactly the wrong moment — and since every castle movement is guarded to the target or the active route, losing the target cost the movement its arm, its section and its card together (live report: `WF → SSL` and `Bowser 1 → WF` simply absent). The star's own ATTEMPT is still recorded ("the practice log should still exist for star mode in the history, just that we don't see it"). Caveat 18's narrower restore is DELETED, not kept beside this: it only ever covered waypoint-bearing defs, because for a plain def "still armed after the grab" said nothing about whether the grab belonged to it — which is why it could not save either segment above. `stagebanner.js::ArenaRow`'s auto-select was the other thief and fired on mere ARRIVAL; it now fills an empty hand only |
| Which 100-coin LADDER a finished run is graded against | `tracking/hundred_coin.py::classify` (pure) + the resolver `tracking/service.py::_hundred_coin_strat` injects into `Projector`/`replay` -- **full detail in `.claude/rules/hundred-coin.md`** |
| The 100-coin star's attribution -- rule 11 asymmetry, written down per the rule's own terms | `tracking/segments.py::hundred_coin_entity` + `arms_ambiently`, `projection.py`'s reattribution + the generalized caveat-12 fix, `views.py`'s exclusion/stamping -- **full detail in `.claude/rules/hundred-coin.md`** |
| Ordering attempts anywhere (practice log, "last N successes", the newest-attempt/active-strategy rule) | Sort by `journal_id`, never the raw attempt `id` -- **full detail below: [Attempt ordering must use journal_id, never the raw id](#attempt-ordering-must-use-journalid-never-the-raw-id)** |
| Which SESSIONS survive a restart | `storage/db.py::delete_empty_sessions` + the shell `service.py::_purge_empty_sessions` — a session holding no attempt is dropped at boot (task 0086: *"if you didn't practice anything during a session, was it really a session?"*). Runs LAST in `start()`, after `_prune_unlabelled_attempts`, because the prune deletes attempts and so can empty a session that had rows when `start()` began. Nothing is journaled — `sessions` is authoritative state written by `insert_session`, not something replay derives — and a purged id can never come back, since the table is AUTOINCREMENT. **The journal slice STAYS, and that is the design**, not a shortcut: a session can bank no attempt of its own and still hold events that govern OTHER sessions' rows. Measured over the live journal 2026-08-04 — cutting the events of every 0-attempt session RESURRECTS 2,167 pruned attempts (233 successes) and rewrites the star/strategy of 13 more; deleting only the ROW moves 222→135 sessions in the repo journal and 45→30 in the installed exe's, with events, attempts and pbs all unchanged and replay byte-identical. A CLEARED attempt still counts as history, so its session is spared |
| Event pipeline + commands (journal→project→broadcast) | `tracking/service.py` |
| Where a practice target may be SET, and where it stops being one | `tracking/practicable.py` — `practicable_here(stage, node)`, pure. **You practice what you are standing in front of** (user ruling 2026-07-27, replacing the held-INTENT design of 2026-07-26 and deleting `pending_target.py` + its `target_pending` broadcast, the view's `pending_target` key, `DELETE /api/target/pending` and the banner's NEXT chip): an out-of-place pick is REFUSED with 409, not held. **CORRECTED 2026-08-01, and it is a premise fix rather than a loosening: a segment whose start trigger has ALREADY FIRED is picked where it has got you to, not where it began.** The place rule can never accept such a pick — for a transition-triggered movement the trigger IS leaving the course, so by the time it fires you are somewhere else by definition, and that is the only moment the pick makes sense. Live report: standing in the Castle Lobby having just exited WF, the banner offered `WF → SSL` and clicking it came back "you can only practice what you are standing in — that one is in Whomp's Fortress", i.e. the banner and the server disagreed about the same segment on the same screen. His words: *"it's rather 'you can't select something where you didn't satisfy the trigger condition'"*. So `practicable_here(stage, node, running)` has two doors — origin BEFORE the trigger fires, armed AFTER — and `running` is read from the projector's own armed set in `request_target`, so what the banner offers and what the server accepts cannot drift apart. Only a segment can be running (a star is one atomic grab with no trigger), a stated rule-11 asymmetry rather than a gap. Both kinds resolve to a world NODE and the two are compared — stars through `segments.star_origin`, segments through `segments.segment_origin` (= `start_origin` + the `origin_overrides` KV), so rule 11 parity is structural rather than duplicated. Two unknowns permit the pick: a def that names no place (`reset_game` start) and the PLAYER's place unknown — no live stage, or a stage naming no level (the emulator is detached, and reviewing with the game closed must not be read-only). A stage whose `mode` is None is NOT one of those and used to be, which was a hole in the rule rather than an exemption from it: the file select, the grounds, the courtyard and the cap courses all resolve to mode None, so that clause permitted every pick from exactly the places nothing can be practiced in — you could set a WF target while standing on the castle grounds. Closed 2026-07-27 on the live report "no course target available, so why is a past star in the active target section"; the UI half of that same report is `ui/stagecontext.js`. Re-picking what is ALREADY the target always succeeds, because a strategy edit posts through the same endpoint and must not be rejected for a position the player has since left. Enforced in `service.request_target` (the API layer; `set_target`/`set_target_segment` stay pure COMMIT primitives every internal caller wants) and in `projection` for retirement. **THE BUG BEHIND ALL OF IT** was the reader, not the policy: `start_levels`/`start_areas` derive from `arm_level` = where a trigger LEAVES Mario, which is None for 50 of the 51 seeded `level_exit` clauses, so **54 of 65 definitions resolved to no place at all** — the quick-select banner offered no castle movement, `belongs_to_stage` said False everywhere (a picked movement was held forever then dropped), and no movement target was ever retired. `start_origin` places every one (65 of 65 as measured then; the pin re-derives the set, so it still holds at 84). Pinned by `tests/test_practicable.py::test_every_seeded_definition_resolves_to_a_place`, which also asserts the OLD reader is still blind so the two can never be confused again |
| Whether a saved time MEANS what the rank beside it implies | `tracking/caveats.py` — `caveat_for(pb_row, attempt) -> key | None`, THE answer, read by the practice card's PB tag, the quick-select star cells (`caveat_by_star`) and the segment cells (`segment_targets[].caveat`). Three findings converged on one sentence and share one module because two surfaces honestly computing the same fact and wording it differently is the divergent-duplication class. `unattributed` = the PB has no `strat_tag`, so `current_pbs_by_strat` can never find it whichever strategy is active — the live report "Bowser 1 shows PB 0'26\"30, but the rank display clearly shows Capless 5"; the practice card already refused to floor that (`_section_banner`'s own sentinel) and the cell did not, which was the whole of round-4 item 2. `old_clock` = `timed_by == "delta"` **AND** `closed_by in core.events.IGT_BEARING_EVENT_TYPES` — the second clause is the finding, since 570 of 626 segment attempts are delta-timed and most are delta FOREVER (a movement closing on a `level_changed` has no Usamune number to be given, so its delta IS how it is measured); dropping it marks nearly every movement PB. `grab_timed` = `Attempt.timed_at == "grab"`. SEVERITY lives here, not in the browser: one badge draws one thing, so the server picks and the client only draws — `ui/components/marks.js` holds glyph/wording/floor-rule per key and `tests/test_cross_language_parity.py` pins the two key SETS equal (order is deliberately not compared; the JS object is a lookup). Measured against a reprojected `Connection.backup` of the dev journal: of 34 current PBs, 21 `grab_timed`, 3 `old_clock`, 2 `unattributed`, 8 clean — and `grab_timed` decays on its own as new PBs land, since every star recorded from 2026-08-01 is x-cam timed. **`attempt_caveat(attempt)` is the PRACTICE LOG's own mark** (2026-08-02, reversing the earlier alarm-fatigue ruling — "I want to add an extra (!) indicator to the entry if it was technically a star grab… if you've been practicing all wrong, you should know"), shipped per row as `_attempt_json`'s `caveat`. It asks about the ROW rather than about a PB and its rank, so it is **PROVEN-only**: `timed_at == "grab"`, never the unknown `None`. Settled with a number rather than an argument — of 837 star successes in his live journal, **3 carry `"grab"` and 670 carry `None`**, so marking the unknowns would put a warning on four fifths of the log and on nothing he can act on; those stay marked on the PB badge, the surface that asserts a grade. `_proven_grab_timed` is the one predicate behind BOTH this and `pb_blocked_by`, so a row can never be marked wrong-quantity by one and offered as a legal save by the other (pinned by `tests/test_caveats.py`). `old_clock` is deliberately absent here — it is the only other key an attempt could carry alone, it would land on segment rows, and its count is unmeasured; one key is a stated scope, not a gap |
| Session view payload | `tracking/views.py` — session view carries `active_route` (`{id, name, segment_ids}` or None, from `service.active_route()` → the projector's journal-derived `active_route_id()`); segment sections AND `build_route_view` carry `category` (free-text grouping label, or None) and `seeded` (bool, `seed_key is not None` — drives the Reset-to-default affordance); segment sections also carry `default_strat` (the definition's own strategy, or None), which the card turns into `allowBlank=false` on the shared picker, and — since 2026-07-27 — `course_id`, the course the segment is practiced in (`origin_course(segment_origin(...))`, None for the castle interior; a star section has always had the key, so this is rule 11 parity). The practice page compares it with `stage.course_id` to decide whether a pinned card still belongs where the player stands; a lobby segment kept reading "ACTIVE SEGMENT" two courses later because nothing answered that question client-side (`.claude/rules/ui.md`, the `practicedHere` row). Note `seen_segs`: a segment only HAS a section while it is the target, armed, or has attempts in scope — which is why a stale pin is invisible in a session-scoped snapshot with no attempts for it, and cost a verification round. **Correction, 2026-08-05: "armed" alone is NOT enough for an AMBIENTLY-arming def** (`segments.arms_ambiently` — the 100-coin star's own engine, `seg:reds->pipe:*`, the legacy pipe-entry trio) — an ambient arm with nothing chosen publishes no section, only the ordinary target branch does, and this is now the shape that used to manufacture an empty "100 Coins" card from mere course entry; full detail and the measured phantom-card counts in `.claude/rules/hundred-coin.md`'s "one CARD, only when the entity is the target" section. A non-ambient segment (any of the 56 castle movements) is unaffected — this sentence still describes it exactly. **`armed_detail`** (Task 6, spec 2026-07-28-multi-step-segments) is the other segment-only key: `{progress, total, waiting_for, …}` while a definition is ARMED, `None` otherwise — derived from `armed_arms`, i.e. re-derived from the journal on every view fetch, never from a client memory (which is what let a lobby LBLJ read "ACTIVE SEGMENT" two courses later). `waiting_for` is `card_waiting_for_sentence`, the CARD voice and deliberately not the builder's editor voice: "Waiting for Enter Shifting Sand Land" is the imperative step a player reads, where the editor's own sentence renders as "Waiting for You enter level Shifting Sand Land". The practice card also uses `armed_detail`'s mere presence to keep a pin that is still running, which is why a disarmed pin (`armed_detail: null`) falls through to the plain course check and the 2026-07-27 retirement fix stays intact. Star sections carry no `armed_detail` for the same reason as `default_strat` — an ORDINARY star is a single atomic grab with nothing to be part-way through — EXCEPT star 6 (100 Coins), the ONE documented exception (spec 2026-07-28-multi-step-segments, "the 100-coin star IS the segment": see the `hundred_coin_entity` row below); pinned by `test_star_sections_carry_no_arm_detail_except_the_100_coin_star`. Star sections still carry no `default_strat` key at all, unconditionally — a default needs a seeded definition row and stars have none (rule 11 asymmetry, spec 2026-07-24-segment-default-strat, pinned by `test_star_sections_carry_no_default_strategy`); `stamp_origins(rows, overrides)` adds `origin` to `GET /api/segments` rows (derived, or the `origin_overrides` ui_state KV — a KV NOT a column, because writing to `segment_defs` flips `seed_dirty` and would freeze a seeded movement against every future corpus refresh just for a label fix). **MARELO additions** (the scoring/scopes/history modules themselves are documented in `.claude/rules/ranks.md` — one fact, one place): `grading_basis`/`valid_frames`/`current_pbs_by_strat` are all PUBLIC, not `_grading_basis`/`_valid_frames`/`_current_pbs_by_strat`, because `tracking/marelo.py` needs the exact same "which of my times counts" resolvers — there is exactly ONE answer and it lives here (the SAVED pb row via `current_pbs_by_strat` in pb mode; `classify.average_frames` over `valid_frames`'s chronological, strat-matched, real-time rows otherwise). `current_pbs_by_strat` went public 2026-07-28 (task 0034) when `tracking/marelo.py::_pb_scores` started reading it directly instead of taking `min()` over raw attempts, which had been paying MARELO out before the user clicked Save as PB. Every star and segment section now carries `entity_rank` `{score, tier, division}` beside its existing `rank`: `entity_rank` grades against the entity's OWN best-possible ladder (`ranks.scoring.best_ladder`, pointwise minimum across strategies) while `rank` still grades the ACTIVE strategy's ladder — so mastering a slow strategy can read Mario on the strat side and honestly less on the entity side; both section builders emit it (rule 11 parity, pinned by `tests/test_ui_section_parity.py`). `_section_banner` also now carries `score` (the active strategy's own ladder, via `ranks.scoring.score_for`) specifically so the UI never re-derives the curve in JS — `standards.js` used to carry its own copy of `score_for` and it silently drifted when the Iron tail changed 2026-07-25; that copy was deleted once this field existed. `segment_courses(db)` (`{segment_id: course_id}`; a castle-interior segment maps to no course and is simply absent) and `entity_label(db, key)` (human name for an entity key) back the MARELO breakdown list and course-scope resolution in `server/ranks_api.py`; `entity_label` routes star names through the canonical `course_name`/`star_name` pair rather than indexing `COURSE_NAMES`/`STAR_NAMES` directly, because `STAR_NAMES[course_id]` is a TUPLE positioned by `star_id`, not a `{star_id: name}` dict — a shape that has now cost a wrong assumption twice |
| Which attempts the STARTUP PRUNE deletes, and what it may never touch | `tracking/prune.py` — `unlabelled(a)` + `prunable_ids(attempts, protected)`, pure; the shell is `service.py::_prune_unlabelled_attempts` and the replay branch that applies a journaled one is `projection.py::replay`. An attempt is UNLABELLED when nothing on it says what it was practice FOR: no star and no segment (the view's own "unassigned" test, `segment_id is None and course_id is None`), or one but no `strat_tag`. Runs once per `TrackerService.start`, **after** `session_started` — that event is what closes an attempt left in flight when the last session ended, and such a row belongs to the session that ended, so deciding before it existed left one stale row behind on every restart. Scoped by `a.session_id != self.session_id`: the rows he is making right now are the ones he still remembers. That filter is UNREACHABLE from the whole-app path (at startup the live session has no attempts yet) and is tested by driving the method directly — see `tests/test_prune.py::test_the_prune_itself_refuses_the_session_it_is_running_in`, which exists because without it a prune moved to any other call site would silently eat live rows and nothing would go red. **Protected = anything he deliberately SAVED**, both halves load-bearing and both measured against the live journal before this shipped: `db.pb_attempt_ids()` (34 pb rows sat on unlabelled attempts, **16 of them the CURRENT pb** for their entity — and `delete_orphaned_pbs` runs on the same re-projection and HARD-deletes a pb row whose attempt vanished, from a table that is not journal-derived, so that one deletion could never be undone) and `service.saved_clip_ids`, injected by `main.py` from `replay/service.py::saved_attempt_ids(root)` (5 of 15 saved clips were for unlabelled attempts; a clip is found only by `attempt_<id>_*.mp4`, so a pruned attempt strands its file forever). Wired off the replay CONFIG rather than the ReplayService so it still holds when replay is disabled. **Fails closed**: a clip scan that raises skips the whole prune, because an unknown protected set must never authorize a delete. The journaled event carries EXPLICIT ids, not the rule — re-deriving at replay time would let deleting a pb row today silently widen a prune that happened last week. Measured blast radius on his journal at the time: 2,247 of 3,576 attempts, ~45% of them from any given recent day, **and 276 successes** (his explicit call, 2026-08-02, over the alternative of sparing them; zero of that day's doomed rows were successes, so it costs nothing going forward and takes mostly pre-July history). The consequence to know: **a star grabbed with no strategy set does not survive the next restart** — three existing tests in `tests/test_tracker_service.py` had to start labelling their fixtures, which is the cheapest available demonstration of it |
| Telling the BROWSER that an armed segment's step cursor moved | `tracking/segments.py::SegmentEngine._progress_notices` — a `segment_progress` notice (broadcast-only, never journaled, like arm/disarm) whenever an armed def's `progress` changes, plus `segment_progress` in `ui/store.js`'s `REFRESH_ON`. **The bug, 2026-08-02**: he walked into the Basement, the engine advanced `WF → SSL` to step 2 on that exact frame (proved by replaying his own journal through the real engine), and the card read "Step 1 of 2 · Waiting for Enter Castle Inside Basement" for the next **77 seconds** — until an unrelated event happened to force a view refetch. `armed_detail` is re-derived from the arm on every `/api/session` fetch and so is always correct WHEN ASKED; nothing was asking. A cursor move journals no event of its own and `area_changed`/`level_changed` are deliberately not refresh triggers, so the one state change the whole multi-step display exists to show was the one with no way to reach the screen — the same shape as the topological cancel's 27.7-second late verdict, and the same lesson: *a verdict delivered late is indistinguishable, from the outside, from a rule that was never written.* Written as a DIFF of every armed def's progress across `feed()`, not as an append at each of the four branches that move a cursor (`_feed_waypoint`'s advance and its anchor rewind, and the same pair in `_feed_loose`) — a fifth arrives with the next mode, and a notice missing from one branch looks exactly like this bug again. The notice drain in `service.py` now forwards every key but `event`/`frame`, so armed/disarmed still publish exactly `{segment_id, name}` and this one carries `progress`/`total` into `tools/what_happened.py` |
| Turning a RUN THE PLAYER DID into a definition's steps | `tracking/synthesize.py::walked_steps(rows, start_row, end_row)` — every settled place walked between the two picked journal rows, each with its short label and the clause that requires it; served on `GET /api/segments/synthesize` as `steps`. The path was always in the journal and nothing was reading it, which is the whole reason a multi-step movement could not be made in the app at all. Borrows two rules verbatim from `SegmentEngine.feed` rather than restating them: read `area_changed` and NOT `level_changed` (an area payload names the level AND the settled area outright), and take the LAST candidate per FRAME (every castle entry loads the Lobby for one poll before warping to the real area, all on one frame — judged raw, that transient Lobby is a stop the player never made). The arm position is dropped unconditionally ("the start is implicit"); the END is dropped by IDENTITY via `segments.step_node`, **never by position** — a `level_changed` end row sits one id BEFORE its own co-frame `area_changed`, so the destination is outside the span and the last walked node is a real step. Dropping by position ate the Basement out of `WF → SSL`, the one route this feature is measured against, while leaving four-step arena routes looking perfect. `_step_clause` picks `level_enter`+`to_subarea` over `area_enter` when the frame carried a level edge — `can_run_from` rule (A)'s trap, absorbed rather than explained. Scored against the shipped corpus: `WF → SSL` and `Bowser 2 → Upstairs` derive byte-identical to their hand-authored rows |
| The step LIST an armed card draws | `tracking/segments.py::card_step_labels(d)` — one short label per step (waypoints then end), stamped onto `armed_detail.steps` beside the existing `waiting_for`; `len(steps) == total + 1` by construction, since `progress` indexes into it. Each label is `addresses.node_short_label(step_node(clause))` where the clause names a place, else `star_name` for a `star_grabbed`, else the registry's own `TriggerType.chip_label` (a THIRD voice after `label` and `card_label`: "Pipe"/"Key" where the card voice is a verb phrase, `None` = fall back to `card_label`). A clause SET is an any-of, so members collapse to their distinct labels; the one multi-member shape in the shipped corpus is the 100-coin exit's end (six `star_grabbed` alternatives in one course = "leave with anything"), which reads **Any star** — stated as a rule about the clauses (same type, same course, more than two), never as a lookup for that family. `card_waiting_for_sentence` is unchanged and still answers the different question (the FULL imperative for the step you are on, including "coming from …", which the card keeps on hover) |
| Whether an event type belongs in the JOURNAL at all | `tracking/service.py::BROADCAST_ONLY` — `attempt_completed`, `target_changed`, `attempts_invalidated` are published to the browser and never written, the discipline `stage_changed` and the segment notices already followed. Each restates something already stored (an attempts row; a value re-derived from `target_set` plus the projection rules; a bare "refetch" ping with no payload), and nothing reads any of them back — not `replay()`, not `GET /api/segments/timeline` (its membership rule names five real trigger types), and `tracking/eventlabel.py` already called them derived bookkeeping. **Proven, not argued**: dropping all three from the live journal replays byte-identical — all 3,576 attempts and every run — while removing 3,884 of 23,063 rows, 4.97 MB → 3.42 MB. `storage/db.py::purge_event_types` clears rows written before the rule existed, called once at the top of `start()`; after that it deletes 0 every boot and skips the VACUUM. **VACUUM alone does not shrink the file here and the checkpoint is not a flourish** — this db is WAL, so VACUUM's rebuilt pages sit in the `-wal` sidecar and the size does not move until something checkpoints; `wal_checkpoint(TRUNCATE)` is what makes the reclaim visible, and both halves are mutation-proved separately in `tests/test_prune.py`. **This deliberately stops short of erasing a pruned attempt's OWN events, which was measured and rejected**: the projector is a state machine, so removing an attempt's events rewrites its neighbours — a span-based cut over the live journal lost 221 surviving attempts, resurrected 141 pruned ones and **changed the `course_id`/`star_id`/`strat_tag` of 288 survivors**. The space is welded to the surviving history; the only thing that separates them is giving up replay entirely, which the user ruled against 2026-08-02 ("being able to analyze the journal is extremely important") |
| SQLite journal + derived tables | `storage/db.py` |
| Route defs (ordered star/segment plans), cumulative success, import/export | `tracking/routes.py` — pure: `validate_route`, `route_stats` (best-K product, no-data=0), `export_route` (embeds segment defs), `resolve_import` (reuse exact match / create rest). Steps are a uniform `{label?, need:K, candidates:[star\|segment]}` shape; a route also carries a `start_condition` trigger (default `reset_game`) that arms the run clock. `export_route`/`resolve_import`/`_segment_matches` carry `waypoints` as part of the embedded segment's identity — an imported re-entry segment with matching waypoints reuses the exact local def instead of duplicating |
| Route view payload | `tracking/views.py::build_route_view` — resolves candidate names + per-step/cumulative success + broken flag (deleted segment) |
| Route CRUD + import/export commands | `tracking/service.py` — create/update/delete_route (segment-existence check), export_route, import_route (dry-run preview; forwards `waypoints`/`category` when creating a missing segment — Task 10 fix); broadcast-only `routes_changed`. `select_route(route_id\|None)` journals `route_selected {route_id, segment_ids}` (member snapshot via `_route_member_segments`, same self-containment trick `_arm_run` uses); `update_route` re-emits it with fresh membership when the ACTIVE route's steps change, and `delete_route` clears it when the deleted route was active. `reset_route`/`reset_segment` restore a seeded row from the bundled seed by `seed_key` and clear `seed_dirty` (LookupError → 404 for a user-created row or an orphaned seed_key). A user edit to a seeded row flips `seed_dirty=1` via `db.set_seed_dirty` — protecting it from the next reconcile refresh until Reset clears it; reconcile writes through `db.update_segment_def`/`update_route` directly and never touches this flag |
| Route storage | `storage/db.py` — `routes` table (migration v7) + routes/insert_route/update_route/delete_route. Migration v11 adds `waypoints`/`category`/`seed_key`/`seed_dirty` to BOTH `segment_defs` and `routes`; v12 backfills `seed_key` onto the 10 pre-existing segment rows by name (guarded `seed_key IS NULL`, idempotent). `set_seed_dirty(table, row_id, dirty)` flips the flag for either table. Migration v13 adds `segment_defs.default_strat` with NO repair UPDATE — the 55 movements are seeded and clean, so reconcile stamps them at the next startup; a movement the user has EDITED (`seed_dirty=1`) keeps no default until Reset to default, deliberately. Migration v14 repoints three `icon_overrides` values from `user:<file>.png` to the bundled stem, after those three uploads became bundled art (`.claude/rules/ui.md`, the entity-icon row): an override always beats a default, so shipping the asset alone would only have helped a fresh install. Guarded on the exact stored JSON string, quotes included, so a differently-named upload never moves. Migration v15 adds `segment_defs.match_mode` (DEFAULT `'strict'`, no repair needed — every existing row already matched strictly). Migration v16 repairs the Bowser pipe family (`seg:bitdw-pipe`/`seg:bitfs-pipe`/`seg:bits-pipe` + their `seg:reds->pipe:*` siblings) left disabled by the retired Bowser-banner mutual exclusion (commit 912466d, live report 2026-07-29): that toggle wrote plain `enabled` PATCHes through `update_segment`, which ALSO flips `seed_dirty=1` — so unlike v13's untouched-column case, reconcile's own self-heal never reaches a stranded row here (`seed_dirty=1` blocks its update branch permanently). Guarded on `enabled=0 AND seed_key IN (...)` the six family keys, leaving any other user-disabled segment (Bowser or not) untouched; `seed_dirty` is deliberately left as found, since this repair is not a user edit. **Migration v17 (round 2, item 5, live report 2026-07-30/31) clears that same `seed_dirty` on those exact six seed_keys** — leaving it standing was right about not disguising a repair as a user edit and wrong about the consequence: `seed_dirty=1` blocks reconcile's update branch UNCONDITIONALLY, so those six rows were frozen against every FUTURE corpus refresh too, not just v16's own `enabled` field. Measured against this branch's own dev db (`sqlite3.Connection.backup`, never the live file): `seg:bitdw-pipe` was the one row actually drifted — `match_mode` stuck at `'strict'` (the pre-v15 column default) instead of the seed's `'exclusive'`, which is why grabbing the reds star kept recording a No Reds attempt (`'strict'` has no star/key cancel branch; `'exclusive'` does) — the other five of the six already happened to match their seed value on every field, frozen but not yet visibly wrong. v17 does not set `match_mode` directly (that would repair one field and refreeze against the next one); it clears the flag blocking reconcile, so reconcile's own existing update path brings everything current and KEEPS it current |
| Editable-defaults seed reconcile | `tracking/defaults.py` — `reconcile_defaults(db, seed)` + `resolve_steps` over `data/defaults.seed.json` (bundled via `core/paths.bundled_defaults_seed()`; segments block resolved BEFORE routes so a route candidate's `seed_key` can map to the now-known local `segment_id`); mirrors `ranks/standards.py`'s reconcile — an untouched seeded row (`seed_dirty=0`) refreshes from the seed, a dirtied or user-created (`seed_key IS NULL`) row is left alone, a seed row missing from the db is inserted; an unresolved route-candidate `seed_key` writes `segment_id=-1` (renders via the existing `broken` flag). Every row is validated and applied INDIVIDUALLY and `reconcile_defaults` RETURNS the skipped-row problems — one malformed row costs that row only; `main.py` logs what came back. **A user-DELETED default resurrects on the next update** (a seed row missing from the db is an insert); Disable is the protected hide path |
| Default route/segment CORPUS (authoring) | `tools/build_defaults_seed.py` + `tools/corpus_{vocab,legacy,movements,routes_main,routes_stage}.py` — compact Python tables expanded into `data/defaults.seed.json` (`_movement_row` stamps `guards`/`category`/`default_strat="Standard"` on every movement by construction — one edit changes all 56) (84 segments = 10 legacy + 56 movements + 3 reds-to-pipe + 15 hundred-coin exits; 48 routes = 13 main + 35 Stage RTA — every number here is parsed and checked against the corpus modules by `tests/test_rule_files.py::test_the_corpus_counts_stated_here_are_true`, because this sentence claimed 65 and 50 for a week while nothing failed). Generated input, checked-in artifact, reconcile at startup. **NEVER hand-edit the JSON** — `--check` is the drift guard, pinned by `tests/test_build_defaults_seed.py`. Movement shapes are FORCED by the matcher (spec `2026-07-24-default-routes-corpus-design.md` §4): a plain def is disarmed by any `area_changed` away from its arm position AND by any `level_changed` matching neither end, so anything crossing a castle region or a hub level (courtyard 26 / grounds 16) needs a waypoint — the castle interior is a **line** (basement↔lobby↔upstairs), so basement→upstairs is TWO area edges and needs one too; a waypoint def is silently cancelled by any star grab, so a movement spanning a Toad/MIPS star either stays plain or ends at the region boundary while the next one STARTS on `star_grabbed`. A movement may start on a star grab but must NEVER end on one (run-ordering trap) — and a `star_grabbed` start is only right when the star is grabbed WHERE THE MOVEMENT BEGINS, i.e. the castle secret stars (MIPS, the Toad stars, course 0), so the arm position is the movement's own start. A star grabbed inside a COURSE the movement then has to leave must start on the `level_exit` instead: `seg:ddd->bitfs` started on the DDD sub star until 2026-07-27 and so fired while the player was still standing in DDD — and since an arm retires a star target, practising that star lost the target on every successful grab (live report). Corpus content + the community-alias glossary + decomp evidence for course-0 star ids: `…-corpus-sources.md` |
| Route step ORDER (a hard contract) | `tracking/runs.py::RunTracker._apply` only ever considers `steps[current]`, and `projection.py` builds `closed` **stars-then-segments** within one event — so seeded route steps must be in completion-event order or a run stalls PERMANENTLY, silently. Rules: a movement step sits immediately before its destination's star block; a castle-secret star grabbed mid-movement sits immediately before that movement; a Bowser block reads `[→ course] [reds] [pipe] [fight]`. Pinned by `tests/test_defaults_corpus_routes.py` (all 13 main routes replayed + a misordered negative control) |
| Whether a movement, PERFORMED, advances a live run | `tests/test_run_mode_segments_trigger.py` — the join the other two gates left open. `test_defaults_corpus.py` proves a real walk fires the definition and never shows a run; `test_defaults_corpus_routes.py` proves the step ORDER and hands `RunTracker` a **FakeAttempt** for every step, so it never asks whether performing the movement produces that completion at all. This one runs the corpus walker's real performance through the REAL `SegmentEngine` and feeds what it records to a REAL armed `RunTracker` parked on that movement's own step — ~90 (movement, route, step) placements, each naming itself on failure. Mutation-proved by making the run's segment matcher off-by-one (202 of 205 red). **Scope is stated in the module docstring and enforced by a test**: it covers the 56 guarded castle movements, which is what `movement_walk` can express; the pipe-entry, 100-coin and legacy-trick families start on being SOMEWHERE (`spawned`/`attempt_anchor`/`area_enter`) rather than on going somewhere, so the world graph has no walk to derive. Written after a live report — *"when I got to the Bowser 1 → WF split in the run tool, it didn't successfully trigger"* — that turned out to be **no bug at all**: run 3534 had walked steps 0-4 and was correctly waiting on step 5, because all eleven Bowser 1 arena exits in that journal went to BitDW, LLL, BitS or the grounds and never to Whomp's Fortress. Establishing that took a hand replay of his database, which is the position this gate exists to prevent |
| Corpus verification | `tests/test_defaults_corpus.py` — synthesizes each movement's event stream from an INDEPENDENT world model (BFS over `addresses.WORLD_EDGES_*`) and asserts exactly one success + silence on all 55 other walks. The walker MUST emit a level entry and its establishing `area_changed` on ONE frame, as the real detectors do — a frame apart, every arm records `area=None` and the file passes vacuously |
| Run engine (forgiving-RTA full-game timer) | `tracking/runs.py` — pure `RunTracker`: arm on `run_started`, start the clock when the route's **`start_condition`** trigger fires (default `reset_game`=F1; a `game_reset` that is NOT the condition aborts) + `start_offset`, forgiving splits (wall-clock per step **minus paused time**, retries roll up), K-of-N no-dup completion, finish on the last step. `run_paused`/`run_resumed` exclude paused time AND suspend completions; `run_reset` aborts; `run_started` with `void_active` DISCARDS the in-flight run (route edited mid-run). `pb_run`/`gold_splits` helpers; run id = the starting game_reset journal id; times stored offset-free; cleared attempts (manual or auto-ignored) are invisible to runs |
| Run projection wiring | `tracking/projection.py` — `Projector` embeds `RunTracker`, feeds it `(ev, closed)`; `finished_runs()`/`active_run_view()`/`run_notices`. Runs re-derive on replay (cache like attempts) |
| Run storage | `storage/db.py` — `runs` table (migration v8) + insert/upsert/replace/`runs(route_id?,finished_only?)`; run settings in `ui_state` (`start_offset_ms`, default 1360) |
| Run lifecycle + view + API | `tracking/service.py` (`start_run`/`end_run`/`pause_run`/`resume_run`/`reset_run`/`run_settings`; `_arm_run` snapshots the route's steps+start_condition into `run_started`; editing the armed route re-arms with `void_active`) · `tracking/views.py` (`build_run_view`/`build_run_history`) · `server/api.py` (`/api/run/*` incl. pause/resume/reset) |
| Single-instance guard (broadcast-only fallback + self-heal) | `storage/instance_lock.py` — Windows msvcrt file-region lock; held for process lifetime; `wait_lock_free` is the restart-handoff wait (the port frees seconds BEFORE process exit releases the lock — waiting only on the port lost the race and stuck post-update servers in broadcast-only, live 2026-07-23); if a server still boots db-less, `server/app.py::_db_reattach_loop` + `TrackerService.attach_db` upgrade it to full tracking when the lock frees |
| Duplicate-event detection logic | `storage/dedupe.py` — pure fn; used by `tools/dedupe_journal.py` (scan read-only; `--fix` = delete duplicates + re-project, server stopped) |
| Stats | `stats/registry.py` — ONE StatDef per stat; THE registry; also owns chip identity + canonical order (`selection_id`/`selection_order`, mirrored in `ui/components/statmenu.js` keyOf) |
| Failure compilation (pure plan) | `tracking/compilation.py` — `plan_compilation`: picks non-cleared failures for an entity, orders by elapsed-into-the-run, gates each on ring coverage of its `[end-X,end+Y]` window, appends the fastest available success (ring full-run, else a saved clip) as the finale. Pure/unit-tested; `EntityRef`/`ClipSpec`/`CompilationPlan` |
| Side-by-side compare (pure bits) | `tracking/comparisons.py` — cache name, four-branch auto-select, offset-only sync math; payload in `tracking/views.py::build_compare_view` |

| Whether a star/segment's STRATEGY rank and its own rank are one measure or two | `tracking/views.py::ranks_share_ladder` -> `sec.one_ladder` on every star and segment section. True when the ACTIVE strategy's ladder IS the entity's best-possible one (`ranks.ladder_cs(ek, strat) == scoring.best_ladder(...)`), in which case the UI draws ONE banner labelled for both. Answered from the LADDERS, never from the two graded values: the field-by-field comparison it replaced (practice.js's `ranksAreIdentical`, deleted 2026-07-27) could not answer at all before a first time existed -- so the star banner was absent until one landed and then appeared out of nowhere -- and it merged two genuinely different measures on any run that happened to grade them alike, then split them again on the next |

## The arm-position gate

**ARM-POSITION gate** (`can_run_from`, live report 2026-07-27): a matched start trigger arms only if the segment could still be RUN from where the event left Mario — a start trigger says what happened, not where it ended, and 52 of the 53 seeded `level_exit` clauses omit `to` because every real course exit lands in the castle, so a Usamune menu warp (WF → CCM is ONE `level_changed` 24 → 5) armed WF → SSL inside Cool, Cool Mountain and nothing below disarms a def whose player then stays put. Three definition-derived rules, NO world-edge table (a stored def must keep matching fabricated edges, and a check derived from that table could only be tested against the table it came from): (A) the next required step — `waypoints[0]`, else the end — must be firable from here (`fires_from`, one row per trigger type in `_PRECONDITION_PARAM`; a THIRD mapping distinct from `arm_level`'s and `_ORIGIN_PARAMS`'), (B) an unpinned `level_exit` from a non-castle level must land in `CASTLE_LEVELS` — hub-and-spoke world; this is the rule the whole seeded corpus leans on, (C) the arm must not be standing on every end trigger's destination (`_end_destination_level`, `level_enter` only — `area_enter` names a subarea and a level-change arm records a STALE `ctx.area`). `game_reset` is exempt (`ctx.level` is the PRE-reset level). KNOWN LIMIT: an unpinned exit from a CASTLE level into a course is indistinguishable from the real castle→course edge without topology; no seeded def has that shape. Every rule is mutation-proven — `tests/test_defaults_corpus.py::test_a_menu_warp_into_a_course_arms_no_movement` (every source level any movement exits × every course) plus five `tests/test_segments.py` cases, each failing when its own rule is removed

## Split and merge

**Split / merge** (`split_definition`, `merge_definitions`, Task 17 of spec 2026-07-28-multi-step-segments): pure authoring ops, no matcher involvement — split re-expresses ONE definition as two meeting at a caller-supplied boundary (inherits `match_mode`, since flattening a waypoint says nothing about how tolerant either half should be; refuses a def carrying 2+ waypoints, which it would silently drop, and refuses a half lint calls `unfireable`), merge chains two into one KEEPING the seam as a waypoint (refuses a pair that does not meet, checked at castle-SUBAREA resolution — level 6 holds basement/lobby/Upstairs, so "same level" is not a seam, and the corpus really does hold three defs ending `area_enter(6,3)` beside one starting `area_enter(6,2)`). Neither result carries a `seed_key` (a derived def is not the row it came from, and one would make `reconcile_defaults` overwrite it at startup); both are NON-DESTRUCTIVE, the inputs surviving untouched because definitions arm in parallel and the whole plus its halves can all record on one play. Wired by `tracking/service.py::split_segment`/`merge_segments` -> `POST /api/segments/{id}/split` and `/api/segments/merge` (docs/api.md).

## Topological validity

**`SegmentEngine._flush_move`, spec `2026-08-01-topological-segment-validity`.**
Live report: standing in the **Bowser 1 arena**, `WF → SSL` read as ACTIVE
SEGMENT; standing **inside LLL**, `LLL → HMC` read "Step 1 of 1 · Waiting for
Enter Hazy Maze Cave". Both were the matcher, not a stale card — `armed_detail`
is re-derived from the armed set on every view fetch. The 56 seeded castle
movements are `loose`, and loose is transparent to everything but death,
`game_reset`, `session_started` and the staleness deadline, so a warp into
another course was invisible to it by design.

Two rules, judged against `world_connections()` — the SAME table the segment
builder's dropdown filtering uses, read for a second purpose:

1. **A move that is not an edge cancels every armed def.** The Usamune warp
   menu (or a savestate) fabricated it.
2. **A legal move that strictly INCREASES the hop count to a def's next
   required place cancels that def.** Basement → LLL is a real edge, so rule 1
   waves it through; what makes it a wrong turn is that HMC went from 1 hop
   away to 2. Strict increase only — equal is sideways, so a route with two
   shortest paths is never punished for picking either.

Both are SILENT (no attempt row, matching `_feed_waypoint`'s off-route cancel —
a movement that never happened must not bank a failure), and both exempt an arm
that began **at or after** the move, so warping somewhere to practise still
arms what lives there.

**This reverses `can_run_from`'s explicit refusal to consult that table.** Its
objection — "a check derived from that table could only ever be tested against
the table it came from" — is answered by `tools/measure_topology_cancels.py`,
which replays both real journals with the rules on and off and reports every
recorded SUCCESS they would have killed. **Any non-zero count is a missing edge
or a bug, examined one at a time, never a number to accept.**

### The three things that keep it from over-firing

- **`tracking/topology.py::node_for` — subareas count only inside the castle
  interior.** Courses have their own areas (SSL area 2 is the pyramid interior),
  and the graph does not model them. Keying on `(level, area)` everywhere read
  **97.9%** of the live journal's settled moves as off-graph against the true
  **54%** — a silent failure that reads exactly like a broken world table. Has
  its own mutation-proved test.
- **The ONE-FRAME DEFER.** Every castle entry loads the lobby for one poll
  before warping to the real area, all on ONE game frame, so `_pending_move` is
  judged only once the frame advances, taking the LAST candidate of that frame.
  Judged raw, a basement course exit reads as the non-edge "SSL → Lobby", and
  for an upstairs destination the transient lobby is CLOSER than the basement
  (2 hops vs 3) — both rules would fire on a move that never happened. This is
  the same per-frame collapse the design's measurement used, deliberately, so
  the number that justified the feature and the code implementing it cannot
  drift. Recorded on `area_changed` (whose payload names the level and settled
  area outright), never `level_changed` (where `ctx.area` is still the old
  level's); read with `.get()`, since a payload without `level` means position
  unknown and unknown declines to judge.
  **The defer needed a CLOCK, and not having one was worth 27.7 seconds
  (live report 2026-08-02).** "The frame advanced" was only ever observed
  through the next JOURNALED event, and standing still inside a course journals
  nothing — so entering Bowser in the Sky from Upstairs cancelled `Bowser 2 →
  WDW` correctly and the selector kept offering it, the card kept calling it
  ACTIVE SEGMENT, for **832 frames** (`tools/why_cancelled.py` on his own
  session; `data/ui_log.jsonl` has the chip drawn for 27.9 s, the same span).
  Earlier sightings were 96, 116 and 56 frames and read as noise. `SegmentEngine.
  settle(frame)` is the delivery half — `Projector.settle` → `TrackerService.
  settle_frame` → the poller's own `on_frame` hook, wired in `main.py`. Same
  verdict, same silence (no row), so a REPLAY reaching it at the next event
  lands in the same state; the only visible difference is the resurrection
  entry's expiry frame. The notice needs no view refetch to land: `store.js`
  keeps `armedSegs`/`armedOrder` from the notices themselves, and both the
  selector's chip (`stagecontext.armedSegments`) and the card's pin read that.
  The report arrived as "I think each segment just needs to know what conditions
  break it" — the rule that broke it already existed and already fired. **A
  verdict delivered late is indistinguishable, from the outside, from a rule
  that was never written.**
- **Two ways to be unconstrained, neither a special case.**
  `segments.step_node` answers None for a clause naming no place (`key_grabbed`,
  `warp_entered`, `star_grabbed`, `reset_game`, an unpinned `level_exit`), and
  `topology.hops` answers None for an unreachable target; `_next_step_hops`
  turns either into "no constraint". That is what exempts every Bowser fight and
  pipe entry without a maintained list. `segments.declared_nodes(d)` is the
  other: a node the definition names as a step of its own route is never a wrong
  turn, which is how a route that deliberately re-enters a place says so —
  Griffin's chosen discriminator over a dwell-time threshold. It is a SET, not a
  comparison against the arm's live `progress`: the waypoint match and the
  position judgement land on the same game frame but on DIFFERENT events, so a
  correctly-followed waypoint read as a move away from whatever came next and
  the declared re-entry cancelled itself.

### The one disarm the player can undo

`SegmentEngine._cancelled` — `{def id: (_Arm, expiry frame)}`. A topological
cancel remembers where the arm stood, and a REAL anchor at that position
re-arms it. Every other disarm in this engine is final.

Found by the measurement, not by design: journal ids 17926–17940, `Bowser 1 →
WF` armed by the arena exit into the lobby, a 7 s detour into BitDW, back to the
lobby, reset AT THE ARM POSITION, then lobby → WF in 16 s. Redoing that def's
start trigger (`level_exit from=30`) means redoing the whole fight, so the reset
IS how a castle movement is re-run — `_feed_loose` already reads an anchor at the
arm position that way for a live arm, and the rules were taking the loop away for
a cancelled one. Two bounds, both Griffin's:

- **FORFEIT** — a real anchor SOMEWHERE ELSE drops the memory permanently: *"if…
  in the middle of lobby -> wf, I decided to reset to bitdw, I think that's a
  genuine kill of the segment, because we've now gone out of order in a way that
  doesn't make sense for practicing… until I get back to Bowser 1 and trigger it
  from the beginning again"* (2026-08-01). Mirrors `_feed_strict`'s reading of a
  relocated anchor.
- **EXPIRY** — the same measured staleness budget a loose arm gets
  (`budget_frames`), applied to every mode here because a cancelled arm has no
  cancel rules left to bound it. Without a clock, a movement killed hours ago
  would re-arm the next time he happened to reset in the same room.

A normal arm pops the memory: the def is live again by its own start condition,
so the resurrection entry would only be a stale second door.

### Measured 2026-08-02

| journal | events | settled moves | off-graph | successes kept |
|---|---|---|---|---|
| installed exe | 17,424 | 419 | 221 (156 course→course) | **82 / 82** |
| repo checkout | 20,542 | 739 | 341 (235 course→course) | **110 / 112** |

Both losses were read back against the raw journal and ARE the live report,
banked as times: `LLL → HMC` (ids 13672–13687) exited LLL, warped BACK INTO LLL,
then warped LLL → HMC (22 → 7, not an edge either) — 23.8 s spanning the round
trip; `Bowser 2 → Upstairs` (ids 18355–18376) exited the arena to the basement,
spent 30 s inside BitFS, came out and went upstairs — 83 s mostly detour.

**NOT built, and named so it is not re-derived by guess:** the builder still
constrains each clause's own `from`/`to` against `connections` but not ACROSS
steps, so a segment is not yet a valid path by construction. That is Stage 2 of
the spec and carries one open decision — strict adjacency ("the only valid path
out of SSL is Basement") would make the 56 shipped two-step movements
un-editable.

## A segment's time is Usamune's IGT

`SegmentEngine._close` takes the CLOSING EVENT's own `igt_frames` as the
segment's time when there is one, and the `close.frame - arm.start_frame`
delta only as a fallback. The events that carry one: `star_collected` and
`key_grabbed` (since 2026-06-12), `warp_entered` (since 2026-07-31), each
stamped from the shared `detectors/igt_clock.py`; plus `death`, whose payload
carries the RAW counter with no display tick — a known 1-frame inconsistency
against the other three, left alone because it also feeds every STAR death row
and `IgtClock.DISPLAY_TICK`'s applicability at a death has never been
live-gated.

**Live report 2026-07-31**: BitDW "No Reds" displayed `0'35"90` where Usamune
showed `0'35"96` (journal ids 23044→23061, attempt `50000023044`, `rta_frames`
1077 vs 1079). The delta is wrong for two independent reasons, neither of them
a constant you could correct for — the arm frame is where a 60 Hz poll caught
a 30 Hz counter drop (the zero frame, or one after it), and the delta counts
paused frames. Measured distribution over 626 real attempts, and the full
arithmetic: the `rta_frames` clause in `tracking/segments.py`'s module
docstring, and the "A `global_timer` delta is not the IGT" paragraph in
`docs/architecture.md` (the game-behaviour half).

**The precondition is CHECKED, not assumed**: `_last_igt_zero_frame ==
arm.start_frame` — Usamune's counter was zeroed on the very frame the segment
armed and has not been zeroed since. `_zeroes_usamune_igt` is what feeds that
frame: every real-edge `level_changed`/`area_changed`, every anchor, and
`game_reset` — **echoes included**, because a door crossing is invisible to
the MATCHER (the player did not choose it) while still zeroing the counter the
time is read from. This used to be a comment in the docstring saying "none
exists; revisit if one is created", and it was already false: replaying the
user's own journal against the gate moved 5 recorded rows, every one a case
the old code got wrong. `Toad Star (Basement)` had banked **311** frames for a
movement that really took **732** — a post-star save-prompt reload (echo shape
4) re-zeroed Usamune mid-segment, so the star's own igt measured from the
reload. And one `death` event closed both `WF → SSL` and `DDD → BitFS (sub)`
with the *identical* 1267, though they armed minutes apart; they now read 2451
and 3988. No saved PB row was affected (checked against a `Connection.backup`
snapshot).

Forward-only for the pipe family: historical `warp_entered` rows carry no
`igt_frames` and the raw counter at those frames was never journaled, so
nothing can be backfilled — the pipe PBs saved before this (pb#139
`seg:bitdw-pipe` 1077, the four `BitS Pipe Entry` rows, pb#137/138) stand on
the old clock and are ~1–2 frames cheaper than anything set after it.
`tests/test_segment_igt.py` is the end-to-end proof: real snapshots through
`main.build_detectors()`, journaled the way `service.py` journals, projected
with the SHIPPED seed rows.

## Attempt ordering must use journal_id, never the raw id

A reattributed 100-coin attempt (see the section below) keeps its
SEGMENT-namespace id (`arm.jid + SEGMENT_ATTEMPT_OFFSET * def_id`,
`tracking/segments.py`) — a number around 7.5×10^11, permanently above every
plain journal-namespace attempt id for the same star. Any sort on the raw
`id` column therefore puts every reattributed row above every native row for
that entity FOREVER, regardless of which actually happened last — live
report (2026-07-29): two reattributed 100-coin successes stayed pinned to
the top of a practice log under "newest first" while six real resets piled
up beneath them, their ordinal labels climbing under a fixed pair.
`tracking/projection.py::journal_id(attempt_id) -> attempt_id %
SEGMENT_ATTEMPT_OFFSET` strips the offset back to the plain journal id both
namespaces share, and IS chronological across them (already used before
this fix for segment-SECTION recency ordering in views.py). Fixed at every
site that orders attempts, since each was found to disagree independently
rather than sharing one door:

- `storage/db.py::attempts()` — the root cause. Was `SELECT * FROM attempts
  ORDER BY id`; now fetches unordered and sorts in Python by `journal_id(a.id)`.
  This is the ONE list every other consumer (`attempts_by_star`/`attempts_by_seg`
  in views.py, `valid_frames`/`grading_basis` for rank/PB averaging,
  `_progress`'s completion graph, `_stats_for`'s `avg_last_n`) reads from, so
  fixing it here fixes all of them with no separate call-site change.
- `views.py::_attempt_json` stamps a `journal_id` field on every attempt row
  so the CLIENT can sort correctly without duplicating
  `SEGMENT_ATTEMPT_OFFSET` as a JS literal.
- `ui/components/practice.js::comparator` — the proximate cause the user
  actually saw: the "newest first"/"oldest first" sort control re-sorts
  client-side regardless of server array order, and did so on raw `id`.
  Sorts on `journal_id` now, both directions.
- `ui/components/rankpage.js`'s `EntityDetail` "last 10 successful attempts"
  sort — same bug, same fix, found by auditing every other id-sort in the UI
  rather than waiting for a second live report.
- `tracking/service.py::_newest_attempt_id` — used by `set_attempt_strat` to
  decide whether reclassifying an attempt should also move the entity's
  ACTIVE strategy ("the newest row's strategy follows a reclassification").
  Was `max(row.id ...)`; a raw max would always crown a segment-namespace row
  over a native one regardless of actual recency, silently breaking that
  rule for exactly the entities this reattribution created. No existing test
  covered a mixed-namespace shape for one entity — added
  `tests/test_tracker_service.py::test_newest_attempt_id_ignores_the_segment_namespace_offset`,
  which builds it through the real 100-coin engine rather than hand-inserted
  rows, and mutation-proved (reverting the key to `row.id` flips the
  assertion from `"Cannonless"` to the wrongly-promoted `"Slide Kick"`).

Checked and found CLEAN (a different id space, or a safe tiebreaker, so left
alone): `tracking/compilation.py`'s `sorted(failures, key=lambda a: (elapsed,
a.id))` — `a.id` is only a rare tiebreaker after the primary elapsed-time key;
`ui/stagecontext.js`'s `justCompletedSegment` `reduce` over one segment's own
attempts — reattribution is all-or-nothing per segment, so it never mixes
namespaces; `ui/components/segmenttimeline.js`'s `row.id > startRow.id` —
raw JOURNAL EVENT ids from `GET /api/segments/timeline`, a wholly separate id
space, never offset; the `pbs` table's own `ORDER BY id` (`db.pbs()`,
`_current_pbs`/`current_pbs_by_strat`) — that id is the `pbs` table's own
independent autoincrement PK, unrelated to the Attempt id namespace scheme.

Verified against a fresh `sqlite3.Connection.backup` of the live db (never
written to): course 2 (WF) star 6 carries a reattributed success at journal
id 22218 (started 05:38:52, SEGMENT-namespace id 750000022218) and a native
reset at journal id 22272 (started 05:42:42, plain id) — raw-id order puts
the success first forever; journal_id order puts the reset first, matching
real wall-clock order, in both "newest first" and "oldest first" (the actual
`comparator`/`EntityDetail` sort expressions were executed directly against
this data via node, not just reasoned about).

## A movement ends at the entrance, not at the course load

**Task 0081, 2026-08-04.** 55 definitions — MIPS Clip, LBLJ, BitS Entry and all
52 castle movements that ended on entering a course — now end on the ENTRANCE
TOUCH, 77 frames earlier (23 at a pipe). Lakitu Skip is the control and is
untouched: it ends by entering the CASTLE, which has no entrance. The detector
half, and the live trace that removed the hold a painting used to sit through,
are in `.claude/rules/memory-detectors.md`.

**TWO conditions read the one `warp_entered` event, and the difference is one
sentence** (live report 2026-08-05, with screenshots: *"it's super confusing
how to get the condition to work… Like, a specific option for triggering the
warp into the course"*). `warp_entered {level}` names where you ARE — the three
legacy pipe entries. `entrance_touched {to}` names where the entrance LEADS —
the other 55. The combined form put three controls on one row and the middle
one demanded you already know the DDD portal lives in the castle interior,
which is a fact about our world graph rather than about the game.

**`topology.entrance_level(to)` is THE door for that derivation** — the corpus
authoring helper and `segments.fires_from`'s arm-position gate both go through
it, so the builder and the shipped corpus cannot disagree about where an
entrance lives. A new condition also needs a row in three per-type registries,
and each has a completeness test that says so: `eventlabel.
TRIGGER_JOURNAL_TYPES`, `synthesize._SYNTH_PARAMS`, `corpus_from_db.
_CLAUSE_BUILDERS`.

**Migration v21 repairs a definition the human EDITED**, which the sweep could
not reach (`seed_dirty=1` blocks reconcile permanently). It rewrites the END
CLAUSE ONLY and leaves `seed_dirty` exactly as found — the difference from v17,
and deliberate: both of his frozen rows carry real edits beside the end trigger
(MIPS Clip's start pins the basement subarea, which is better than what ships),
and reconcile would discard them. **A repair may fix the thing it is about; it
may not spend the user's own work doing it.** Guarded by SHAPE rather than by
seed_key, so it also catches the intermediate `warp_entered`+`to` form that
shipped for one afternoon; idempotent.

**`step_node` is the part that could have failed silently, and it is the whole
of Griffin's constraint** (*"our topological logic is already working as
expected and fine — this should NOT break that. We need to be careful to
maintain the same functionality regarding legitimacy of segments / going
between steps of a segment"*). It answered None for `warp_entered`, and None
means UNCONSTRAINED — so re-pointing the corpus onto a place-less clause would
have switched the wrong-turn cancel off for every castle movement at once, with
nothing going red. A `warp_entered` carrying `to` now resolves to the
DESTINATION node, exactly as the `level_enter` it replaced: from the basement
DDD is 1 hop, from the lobby 2, Rule 2 fires unchanged. A destination-free
clause (the three legacy pipe defs) still answers None and stays unconstrained.

**`projection.warp_destinations` is the FOURTH pre-pass**, beside
`cleared_ids`, `strat_overrides` and `time_corrections`, and it is not
optional. Every touch written before 2026-08-04 carries no `to`, and both
obvious readings were measured over the real journal rather than argued:
refusing such a row VANISHES 54 of 106 recorded segment successes on the next
replay; waving it through FABRICATES 105, because a basement touch toward HMC
closes a DDD-pinned definition. The destination was never missing from the
JOURNAL, only from the row — the level edge that follows names it, which is
what the live detector now waits for — so replay recovers it the same way,
derived and never written back, bounded by the same `HOLD_CAP_FRAMES`.

**Measured 2026-08-04** by `tools/measure_entrance_sweep.py`, which replays each
journal under the old corpus and the new one and diffs both:

| | repo checkout | worktree |
|---|---|---|
| events | 21,383 | 391 |
| cancels before / after / differing | 7 / 7 / **0** | 0 / 0 / **0** |
| `declared_nodes` differing (84 defs) | **0** | **0** |
| successes | 106 → 99 | 0 → 0 |
| fabricated | **0** | 0 |
| re-timed | 47, by **23..77 frames**, nothing outside | 0 |

The re-timed distribution is the strongest available evidence the pairing is
right: the two measured fade constants and no third number. The 7 lost are read
back one at a time in the tool's own output — two predate the warp detector
entirely (2026-06-11) and five have no touch recorded at all, so nothing in the
change could have matched them.

**A Usamune menu warp no longer completes a movement**, deliberately: it
fabricates the edge, so there is no collision to detect. A movement is the
travelling, and a warp that skips it used to bank a meaningless number.
Recording nothing beats recording a wrong time — but it looks like a bug the
first time it is seen, and the open question about how often a REAL entry
misses its touch is in the detectors rule.

## A failure only names what he chose, and winning ends everything

Two rulings, 2026-08-05. Full reasoning in the two docstrings named below;
this is the map.

**A reset nobody chose is Unassigned** — *"we shouldn't misattribute resets
without explicit assignment or detection (by the player completing a valid
attempt)."* Entering HMC arms HMC→DDD, HMC→RR and MIPS Clip together; he went
to DDD with nothing selected, and HMC→RR grew a practice card off a later
reset. `projection.py::_untargeted_failure_for_segment` generalises the
Bowser-only `_untargeted_ambient_failure` to EVERY def: a non-success closure
records only when `self.target` names it. Arming is untouched — every def
still runs and still records its SUCCESS, and the failed span still lands in
Unassigned. His "literally only 1 option" clause needs no rule of its own: a
lone option is auto-selected (`loneRouteOption`, `ArenaRow`), which writes the
target. Written as a separate "only movement in flight" carve-out first, it
let his exact row through — the siblings all resolve BEFORE it does.

**Measured before shipping** via `tools/measure_unchosen_resets.py` (replays a
journal under both rules and diffs): 351 / 314 / 8 rows removed across the
three journals, all resets, zero successes, zero hand-labelled rows, zero
saved PBs. That tool's own trap, worth reading before writing another like it:
its first version called a row "labelled" if the attempt had a `strat_tag` and
reported 7 of 8 destroyed — but every seeded movement carries
`default_strat = "Standard"`, so it was counting the DEFAULT. A human
labelling a row is a journal event (`strat_overrides`).

**Winning the game leaves nothing running** — *"at the end of the game... there
should be absolutely no segments still running (the game is literally over)."*
`segments.py::feed` ends by silently cancelling every still-armed def on
`key_grabbed` with `which == "grand"` (already journalled, so no new memory
read, and retroactive on replay). LAST in `feed` so Bowser 3's own success
still records; SILENT via `_cancel_topologically` on that helper's own grounds.
The staleness predates the endgame — a loose movement whose end never fires
stays armed indefinitely — and that is pinned as its own precondition test.

## A pause exit is not a retry

`SegmentEngine._arrived_by_a_real_move`, gating the anchor branch of BOTH armed
handlers (`_feed_waypoint` and `_feed_loose`). Live report 2026-08-03:

> it briefly flashed step 3 of 3, then it reset

and, in the same message, nothing recorded on reaching WF. **Both symptoms are
this one bug** — a rewound cursor can never reach its own end, so the movement
could not complete and banked a `reset` row instead.

Usamune zeroes its IGT on the pause exit's level load, so the exit emits a
`practice_reset` on the SAME frame as the level edge. `_anchor_echo`'s
transition co-frame shape (3) already catches that shape, but is **pause-gated
on purpose**: a Usamune MENU WARP is co-frame too, carries
`paused_frames_before` 13-890, and really is a new attempt boundary. A pause
exit carries a long pause for exactly the same reason — the menu was open — so
it fell straight through and read as a player retry.

**The discriminator is the WORLD GRAPH**, which is rule 1's premise (see
[Topological validity](#topological-validity)) read for a second purpose: a menu
warp FABRICATES an edge, a pause exit WALKS one. Measured over co-frame anchors
with a long pause across both journals:

| | count | examples |
|---|---|---|
| real world edge | **73** | `30->17`, `17->6:1`, `21->6:2`, `7->6:1` — doors and pause exits |
| off-graph | **193** | `22->17`, `8->17`, `16->34`, and `17->6:2` — the Upstairs menu warp, not BitDW's own exit into the Lobby |

**DISPROVED, recorded so nobody spends an evening on it again:**
`frames_since_warp_op` does NOT separate them. It reads 0 for an ordinary door
and stale for a pause exit, which looks promising, but menu warps sit on both
sides of it.

**Blast radius, replayed both ways over the real journals:** his live journal
gains **27 attempts, all `success`** — `Bowser 1 → WF` 11, `Bowser 2 →
Upstairs` 10, `Bowser 2 → BitS` 6 — and loses **19 `reset` rows**, the phantom
retries the false rewind was banking, on those same three definitions. The repo
journal loses 2 resets and gains nothing. **No success is removed anywhere**,
which is the property that matters.

**Deliberately NARROW, and the wider version is owed rather than done:** this
gates the two armed branches' rewind, NOT `_anchor_echo` itself. Widening the
echo would make these anchors invisible to EVERY definition and to attempt
boundaries generally — a much larger claim about his recorded history than this
report supports. The measurement above is the head start for it.
