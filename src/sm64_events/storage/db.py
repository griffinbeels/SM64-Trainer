"""SQLite store: append-only event journal + derived/materialized tables.

The journal is the source of truth — append-only, except whole-session
deletion, a user-level operation (delete_session). `attempts` is a
rebuildable cache of tracking.projection.project(events). Sync sqlite3
behind a lock: writes are one tiny row per game event, far below any
contention threshold."""
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from sm64_events.core.events import Event
from sm64_events.tracking.projection import Attempt, journal_id

MIGRATIONS = [
    # v1
    """
    CREATE TABLE IF NOT EXISTS sessions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      started_utc TEXT NOT NULL,
      ended_utc TEXT,
      label TEXT
    );
    CREATE TABLE IF NOT EXISTS events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      session_id INTEGER NOT NULL REFERENCES sessions(id),
      seq INTEGER NOT NULL,
      type TEXT NOT NULL,
      frame INTEGER NOT NULL,
      wall_time_utc TEXT NOT NULL,
      payload TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS attempts (
      id INTEGER PRIMARY KEY,
      session_id INTEGER NOT NULL,
      course_id INTEGER, star_id INTEGER, strat_tag TEXT,
      anchor_type TEXT NOT NULL, anchor_frame INTEGER,
      outcome TEXT NOT NULL, outcome_detail TEXT,
      igt_frames INTEGER, rta_frames INTEGER,
      started_utc TEXT NOT NULL, ended_utc TEXT NOT NULL,
      cleared INTEGER NOT NULL DEFAULT 0, cleared_reason TEXT
    );
    CREATE TABLE IF NOT EXISTS pbs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      course_id INTEGER NOT NULL, star_id INTEGER NOT NULL, strat_tag TEXT,
      timer_mode TEXT NOT NULL, frames INTEGER NOT NULL,
      attempt_id INTEGER, saved_utc TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS ui_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """,
    # v2 — Phase 2: rollout sub-event counts on attempts
    """
    ALTER TABLE attempts ADD COLUMN rollouts_total INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE attempts ADD COLUMN rollouts_dustless INTEGER NOT NULL DEFAULT 0;
    """,
    # v3 — Phase 2 fix round: chained double/triple jump counts
    """
    ALTER TABLE attempts ADD COLUMN jumps_total INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE attempts ADD COLUMN jumps_dustless INTEGER NOT NULL DEFAULT 0;
    """,
    # v4 — segment events: definitions table, attempt linkage, kind-aware PBs
    """
    CREATE TABLE IF NOT EXISTS segment_defs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      enabled INTEGER NOT NULL DEFAULT 1,
      start_triggers TEXT NOT NULL,
      end_triggers TEXT NOT NULL,
      guards TEXT NOT NULL DEFAULT '[]',
      created_utc TEXT NOT NULL
    );
    ALTER TABLE attempts ADD COLUMN segment_id INTEGER;
    CREATE TABLE pbs_v2 (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      course_id INTEGER, star_id INTEGER, segment_id INTEGER, strat_tag TEXT,
      timer_mode TEXT NOT NULL, frames INTEGER NOT NULL,
      attempt_id INTEGER, saved_utc TEXT NOT NULL
    );
    INSERT INTO pbs_v2 (id, course_id, star_id, strat_tag, timer_mode,
                        frames, attempt_id, saved_utc)
      SELECT id, course_id, star_id, strat_tag, timer_mode, frames,
             attempt_id, saved_utc FROM pbs;
    DROP TABLE pbs;
    ALTER TABLE pbs_v2 RENAME TO pbs;
    -- EDITING A SEED VALUE BELOW? It only reaches FRESH dbs — existing ones
    -- (the live data/tracker.db, carried across sessions) NEVER re-read this
    -- seed. Ship a paired repair migration that UPDATEs the live rows, guarded
    -- on the exact OLD value so user customizations survive (see v5 LBLJ, v6
    -- Bowser 3). Omitting it leaves every existing db on the broken value —
    -- exactly how Bowser 3 shipped ending on star_grabbed for weeks.
    INSERT INTO segment_defs (name, enabled, start_triggers, end_triggers, guards, created_utc) VALUES
      ('LBLJ', 1, '[{"type":"level_enter","to":6,"from":16},{"type":"attempt_anchor","level":6,"area":1}]', '[{"type":"level_enter","to":17}]', '[]', '2026-06-11T00:00:00Z'),
      ('MIPS Clip', 1, '[{"type":"level_exit","from":7,"to":6}]', '[{"type":"level_enter","to":23}]', '[]', '2026-06-11T00:00:00Z'),
      ('Lakitu Skip', 1, '[{"type":"spawned","level":16}]', '[{"type":"level_enter","to":6}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitS Entry', 1, '[{"type":"area_enter","level":6,"area":2}]', '[{"type":"level_enter","to":21}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitDW Pipe Entry', 1, '[{"type":"level_enter","to":17},{"type":"attempt_anchor","level":17}]', '[{"type":"warp_entered","level":17}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitFS Pipe Entry', 1, '[{"type":"level_enter","to":19},{"type":"attempt_anchor","level":19}]', '[{"type":"warp_entered","level":19}]', '[]', '2026-06-11T00:00:00Z'),
      ('BitS Pipe Entry', 1, '[{"type":"level_enter","to":21},{"type":"attempt_anchor","level":21}]', '[{"type":"warp_entered","level":21}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 1', 1, '[{"type":"level_enter","to":30},{"type":"attempt_anchor","level":30}]', '[{"type":"key_grabbed","level":30}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 2', 1, '[{"type":"level_enter","to":33},{"type":"attempt_anchor","level":33}]', '[{"type":"key_grabbed","level":33}]', '[]', '2026-06-11T00:00:00Z'),
      ('Bowser 3', 1, '[{"type":"level_enter","to":34},{"type":"attempt_anchor","level":34}]', '[{"type":"key_grabbed","level":34}]', '[]', '2026-06-11T00:00:00Z');
    """,
    # v5 — warp-menu arming (live gate 2026-06-12): the Usamune warp menu
    # (06 01 00) deposits Mario at the castle lobby entrance — equivalent to
    # the grounds→lobby door — emitting only a practice_reset (menu pause →
    # warp → IGT reset; no level edge), so a level_enter-only LBLJ never
    # armed.  LBLJ gains an area-scoped attempt_anchor (lobby = area 1;
    # scoping prevents basement respawns from cross-arming).  Fresh DBs get
    # the new triggers from the edited v4 seed above; this entry repairs
    # existing DBs.  Name-guarded so a user-renamed/repurposed row id 1 is
    # left alone.
    """
    UPDATE segment_defs SET start_triggers='[{"type":"level_enter","to":6,"from":16},{"type":"attempt_anchor","level":6,"area":1}]' WHERE id=1 AND name='LBLJ';
    """,
    # v6 — grand-star repair (live report 2026-06-12): the B3 grand star is
    # NOT a collectable star — it enters ACT_JUMBO_STAR_CUTSCENE, never a
    # star-dance action, so it fires key_grabbed which='grand' and NEVER
    # star_collected (detectors/key.py; addresses.py FIGHT_END_LEVELS).  The
    # ORIGINAL v4 seed (commit c9a03cd) ended Bowser 3 on star_grabbed, which
    # the grand star can never satisfy — the segment armed but never
    # completed.  419c4e6 corrected the v4 seed for FRESH DBs but, unlike the
    # v5 LBLJ fix, shipped no repair for EXISTING ones, so every db seeded
    # before it kept the broken trigger.  This is that repair, mirroring v5.
    # Triple-guarded (id + name + the EXACT broken seed value) so a
    # user-renamed or deliberately re-pointed row is left untouched.
    """
    UPDATE segment_defs SET end_triggers='[{"type":"key_grabbed","level":34}]' WHERE id=10 AND name='Bowser 3' AND end_triggers='[{"type":"star_grabbed"}]';
    """,
    # v7 — routes: ordered star/segment practice plans (spec 2026-06-14).
    # Config like segment_defs (NOT history); steps is JSON, see
    # tracking/routes.py for the shape. The runs table arrives in v8 (Phase D).
    """
    CREATE TABLE IF NOT EXISTS routes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT NOT NULL,
      steps TEXT NOT NULL,
      created_utc TEXT NOT NULL,
      updated_utc TEXT NOT NULL
    );
    """,
    # v8 — runs: full-game run history (spec 2026-06-14, Phase D). Cache like
    # attempts, rebuilt from the journal (run_started + completions + resets).
    # route_steps/splits are JSON; id = the game_reset journal id that started
    # the run. Times stored offset-free; display adds start_offset_ms.
    """
    CREATE TABLE IF NOT EXISTS runs (
      id INTEGER PRIMARY KEY,
      route_id INTEGER,
      route_name TEXT NOT NULL,
      route_steps TEXT NOT NULL,
      mode TEXT NOT NULL,
      status TEXT NOT NULL,
      reached_step INTEGER NOT NULL,
      total_ms INTEGER,
      start_offset_ms INTEGER NOT NULL DEFAULT 0,
      started_utc TEXT NOT NULL,
      ended_utc TEXT NOT NULL,
      is_pb INTEGER NOT NULL DEFAULT 0,
      splits TEXT NOT NULL
    );
    """,
    # v9 — per-route run-start condition (spec 2026-06-15). The run clock starts
    # when this trigger fires; existing routes default to the game reset (F1).
    """
    ALTER TABLE routes ADD COLUMN start_condition TEXT NOT NULL
      DEFAULT '{"type":"reset_game"}';
    """,
    # v10 — comparisons: saved side-by-side comparison videos (spec 2026-07-02).
    # Config (like routes), never journaled. Keyed by (entity_key, strat);
    # cache_name points into data/compare_cache (content-addressed dedup).
    # in/out_frame are non-destructive sync bounds in GAME frames (NULL = ends).
    """
    CREATE TABLE IF NOT EXISTS comparisons (
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      entity_key    TEXT NOT NULL,
      strat         TEXT NOT NULL,
      name          TEXT NOT NULL,
      source_kind   TEXT NOT NULL,
      source_ref    TEXT NOT NULL,
      cache_name    TEXT NOT NULL,
      in_frame      INTEGER,
      out_frame     INTEGER,
      created_utc   TEXT NOT NULL,
      last_used_utc TEXT NOT NULL
    );
    """,
    # v11 — sequence segments + shared category + seed provenance
    # (spec 2026-07-23-default-routes-foundation). waypoints = ordered middle
    # steps (empty = today's start/end pair). category groups routes AND
    # segments. seed_key/seed_dirty back the editable-defaults reconcile:
    # seed_key is the stable identity a bundled default is matched on;
    # seed_dirty=1 means the user edited a seeded row, so reconcile leaves it.
    """
    ALTER TABLE segment_defs ADD COLUMN waypoints TEXT NOT NULL DEFAULT '[]';
    ALTER TABLE segment_defs ADD COLUMN category TEXT;
    ALTER TABLE segment_defs ADD COLUMN seed_key TEXT;
    ALTER TABLE segment_defs ADD COLUMN seed_dirty INTEGER NOT NULL DEFAULT 0;
    ALTER TABLE routes ADD COLUMN category TEXT;
    ALTER TABLE routes ADD COLUMN seed_key TEXT;
    ALTER TABLE routes ADD COLUMN seed_dirty INTEGER NOT NULL DEFAULT 0;
    """,
    # v12 — adopt the 10 pre-seed segments into the reconcile by name, so a
    # newer bundled seed can refresh them (they predate seed_key). Guarded on
    # seed_key IS NULL so a re-run never clobbers a user rename.
    """
    UPDATE segment_defs SET seed_key='seg:lblj'         WHERE name='LBLJ'            AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:mips-clip'     WHERE name='MIPS Clip'       AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:lakitu-skip'   WHERE name='Lakitu Skip'     AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bits-entry'    WHERE name='BitS Entry'      AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bitdw-pipe'    WHERE name='BitDW Pipe Entry' AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bitfs-pipe'    WHERE name='BitFS Pipe Entry' AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bits-pipe'     WHERE name='BitS Pipe Entry'  AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bowser-1'      WHERE name='Bowser 1'        AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bowser-2'      WHERE name='Bowser 2'        AND seed_key IS NULL;
    UPDATE segment_defs SET seed_key='seg:bowser-3'      WHERE name='Bowser 3'        AND seed_key IS NULL;
    """,
    # v13 — a definition's own strategy (spec 2026-07-24-segment-default-strat).
    # NULL = no default, today's behaviour. The 56 castle movements get
    # 'Standard' through the bundled seed, not from here: they are seeded rows
    # with seed_dirty=0, so reconcile_defaults refreshes them at startup. No
    # repair UPDATE, deliberately — writing through a dirtied row is the one
    # thing the seed_dirty contract exists to prevent, and Reset to default is
    # the escape hatch for a movement the user has edited.
    """
    ALTER TABLE segment_defs ADD COLUMN default_strat TEXT;
    """,
    # v14 — three icons that were UPLOADS become bundled art (user, 2026-07-26:
    # "can you make these default for all users?"). ui/assets/star_icons now
    # ships blj/lakitu/castle_movement, and ui/entities.js hands them to the
    # seeded definitions that should wear them by default. This repoints the
    # per-entity OVERRIDES that already named the uploaded copies — editing the
    # asset set alone only helps a fresh install, and an override always wins,
    # so without this the two segments that have one would keep resolving
    # `user:*.png` out of the data dir forever (auto-memory: a seed fix needs
    # its own repair migration). Guarded on the exact stored value, quotes
    # included, so it can only ever match those three JSON strings; every other
    # override and every db without them is untouched.
    """
    UPDATE ui_state SET value = replace(value, '"user:blj.png"', '"blj"')
      WHERE key='icon_overrides';
    UPDATE ui_state SET value = replace(value, '"user:lakitu.png"', '"lakitu"')
      WHERE key='icon_overrides';
    UPDATE ui_state
       SET value = replace(value, '"user:castle_movement.png"', '"castle_movement"')
      WHERE key='icon_overrides';
    """,
    # v15 — how forgiving this definition's matcher is (spec
    # 2026-07-28-multi-step-segments). 'strict' is today's behaviour: an
    # off-sequence star grab or level crossing cancels the arm. 'loose' stays
    # armed through them until the end trigger fires or the arm's staleness
    # deadline passes. DEFAULT 'strict' with no repair UPDATE, deliberately:
    # every existing row keeps matching exactly as it did, and the seeded
    # corpus converts one movement at a time with the BFS corpus test proving
    # each conversion safe. insert_segment_def's OWN Python default is ALSO
    # 'strict' (fix round, spec 2026-07-28-multi-step-segments Item 0) — it
    # briefly defaulted to 'loose' on the theory that a fresh row is always
    # someone AUTHORING a new segment, but reconcile_defaults calls this same
    # function to SEED the 56 castle movements on a fresh install, which is a
    # shipped default, not authoring. That made a fresh install's movements
    # come out loose while every migrated install's came out strict (backfilled
    # by this very ALTER), two behaviours picked by install date. The one
    # place "loose" IS the right authoring default is the API layer
    # (SegmentBody.match_mode / TrackerService.create_segment), which always
    # passes match_mode explicitly and so never falls back to this default.
    """
    ALTER TABLE segment_defs ADD COLUMN match_mode TEXT NOT NULL DEFAULT 'strict';
    """,
    # v16 — repair for the Bowser pipe family stranded disabled by a retired
    # UI-side mutual exclusion (live report 2026-07-29, fixed in commit
    # 912466d "Bowser row practices three things at once, no mutual
    # exclusion"). The Bowser banner used to enforce "reds OR no-reds" by
    # PUTting {"enabled": false}/{"enabled": true} at a pipe-entry segment
    # whenever the reds star or a pipe cell was picked — an ordinary
    # update_segment PATCH (tracking/service.py), which ALSO flips
    # seed_dirty=1 on a seeded row (a "user edit", protecting it from
    # reconcile). Once the corpus reshape (b9c72f3) gave each Bowser level TWO
    # segment_targets sharing the same start_levels — the legacy pipe-only
    # segment and its new reds->pipe sibling — segsForLevel's plain
    # start_levels filter picked up both, so this toggle could disable either
    # one depending on which cell a session happened to click. 912466d deleted
    # the client-side toggle, but the write it left behind does not self-heal:
    # seed_dirty=1 means reconcile_defaults's update branch never reaches
    # these rows again, so an enabled=0 stranded here sits forever, silently
    # recording nothing every session (a disabled definition never arms).
    # Two rows in the live db were found this way: id 5 (seg:bitdw-pipe) and
    # id 72 (seg:reds->pipe:bitfs), each explaining a Bowser scenario that
    # logged zero attempts across a full practice session that covered its
    # five siblings.
    #
    # Contrast v13, which shipped no repair: default_strat starts NULL for
    # every existing row and no client had ever written to it, so an untouched
    # seeded row (seed_dirty=0) simply picks it up from reconcile at the next
    # startup — there was nothing stranded to repair. Here the strand IS
    # seed_dirty=1, which is exactly what blocks the self-heal v13 relied on.
    #
    # Guarded to the six seed_keys the retired exclusion ever touched (the
    # three pipe-only rows plus their reds->pipe siblings) AND enabled=0, so a
    # segment disabled on purpose — Bowser or otherwise — is untouched.
    # seed_dirty is deliberately left exactly as it is: this repair is not a
    # user edit and must not change what reconcile does with these rows at the
    # next startup.
    """
    UPDATE segment_defs SET enabled = 1
      WHERE enabled = 0 AND seed_key IN (
        'seg:bitdw-pipe', 'seg:bitfs-pipe', 'seg:bits-pipe',
        'seg:reds->pipe:bitdw', 'seg:reds->pipe:bitfs', 'seg:reds->pipe:bits'
      );
    """,
    # v17 — v16 repaired `enabled` on the Bowser pipe family but, on explicit
    # instruction, deliberately left seed_dirty=1 standing (spec 2026-07-28-
    # multi-step-segments round 2, item 5, live report 2026-07-30/31: "if the
    # user grabbed the reds star in a bowser level, [the No Reds attempt]
    # shouldn't be added to the practice log" -- EXCLUSIVE mode's entire job,
    # yet seg:bitdw-pipe kept recording one anyway). That instruction was
    # right about not disguising a repair as a user edit, and wrong about the
    # consequence: seed_dirty=1 blocks reconcile_defaults's update branch
    # UNCONDITIONALLY (tracking/defaults.py: "if not existing['seed_dirty']"),
    # so those rows are frozen against every future corpus refresh, not just
    # v16's own field. Confirmed against a sqlite3.Connection.backup of this
    # branch's own dev db (never the live file): seg:bitdw-pipe was the ONE
    # row actually drifted (match_mode 'strict' in the db, 'exclusive' in the
    # bundled seed -- the shape that lets grabbing the reds star cancel a
    # no-reds attempt silently, which plain 'strict' cannot do); the other
    # five of the six already happened to match their seed value on every
    # field, by the coincidence of when their own retired-toggle write last
    # fired relative to when match_mode (v15) was introduced -- they were
    # equally frozen, just not yet visibly wrong.
    #
    # This does NOT set match_mode directly (that would repair one field and
    # leave these six rows frozen against the NEXT corpus change too, exactly
    # v16's own mistake one field later) -- it clears the flag that is
    # blocking reconcile, so reconcile's own existing update path (which
    # already runs at every startup) brings match_mode and anything else
    # current, then KEEPS it current from here on.
    #
    # Scoped to the exact six seed_keys v16 already named (the only rows the
    # retired mutual-exclusion toggle ever wrote to) -- never a broader
    # "clear every stale seed_dirty flag" sweep, which would silently discard
    # a genuine user edit elsewhere and is precisely the risk this flag
    # exists to prevent.
    """
    UPDATE segment_defs SET seed_dirty = 0
      WHERE seed_key IN (
        'seg:bitdw-pipe', 'seg:bitfs-pipe', 'seg:bits-pipe',
        'seg:reds->pipe:bitdw', 'seg:reds->pipe:bitfs', 'seg:reds->pipe:bits'
      );
    """,
    # v18 — untagged-PB backfill (live report 2026-07-31): "Bowser 1 shows PB
    # 0'26"30, but the rank display clearly shows Capless 5 -- this should
    # never happen." Root cause: `pbs.strat_tag`/`attempts.strat_tag` are
    # separate NULL-able columns, and views.py's per-strategy ranking lookup
    # (`current_pbs_by_strat`) skips a PB with no strat_tag entirely -- the
    # entity stays unranked even though a strategy-blind PB display (the SAME
    # kind of lookup, blind to strategy on purpose) shows one. Every one of
    # the ten legacy segments carried `default_strat=NULL` before this spec
    # (v13 added the column with no repair, deliberately), so their attempts
    # recorded no tag from day one. Measured against a sqlite3.Connection.
    # backup snapshot of this branch's own dev db: 36 of 126 saved PB rows
    # were untagged, across 17 entities (10 stars, 7 segments) -- every one of
    # those 7 segments is one of these ten legacy defs.
    #
    # Three of those seventeen are UNAMBIGUOUS -- no guess is involved,
    # because their rank standards define exactly ONE strategy apiece
    # (cross-checked against the bundled data/rank_standards.seed.json):
    # MIPS Clip and Bowser 1/2, all "Standard". (A fourth entity in that same
    # snapshot, a locally-named "BLJs" segment, resolves just as unambiguously
    # -- but it carries no seed_key, so it has no identity a portable
    # migration can reach on every install; it is left to the "unattributed"
    # display fix instead of a guess by name.) The other 13 of the 17
    # (10 stars, plus BitDW/BitFS/BitS Pipe Entry and Bowser 3) are genuinely
    # ambiguous (2+ strategies) and stay untagged on purpose -- attributing
    # one would credit a saved time to a strategy the player may never have
    # run, which is worse than showing nothing.
    #
    # Guarded by seed_key (stable across installs and renames, unlike a raw
    # segment_id) joined through segment_defs, AND `strat_tag IS NULL`
    # (idempotent -- a re-run touches nothing, and an already-tagged row, from
    # this migration or a real save, is never overwritten). Fixes BOTH tables
    # for the same reason: `pbs.strat_tag` is what current_pbs_by_strat reads
    # (the reported bug), and `attempts.strat_tag` is what valid_frames/
    # grading_basis read for average-mode ranking -- leaving the underlying
    # attempt NULL while its own pbs row now says "Standard" would be the same
    # fact recorded two different ways in two tables, and would silently keep
    # excluding these exact runs from every average-mode rank forever.
    """
    UPDATE pbs SET strat_tag = 'Standard'
      WHERE strat_tag IS NULL AND segment_id IN (
        SELECT id FROM segment_defs
         WHERE seed_key IN ('seg:mips-clip', 'seg:bowser-1', 'seg:bowser-2')
      );
    UPDATE attempts SET strat_tag = 'Standard'
      WHERE strat_tag IS NULL AND segment_id IN (
        SELECT id FROM segment_defs
         WHERE seed_key IN ('seg:mips-clip', 'seg:bowser-1', 'seg:bowser-2')
      );
    """,
    # v19 — how an attempt's time was MEASURED (ruling 6 of round 3). A pipe
    # PB saved before `warp_entered` carried Usamune's IGT stands on a
    # wall-frame delta, which counts paused frames and carries the arm-frame
    # alignment error -- so it sits ~1-2 frames CHEAP and an identical run
    # cannot beat it. Those rows cannot be backfilled in principle (the raw
    # counter at those frames was never journaled), so the ruling is that they
    # stand and are MARKED.
    #
    # No repair UPDATE, and that is the point: `Attempt.timed_by` is derived by
    # `SegmentEngine._close` from the closing event's own payload, and
    # projection is replay-derived -- so the next reproject stamps every
    # historical row correctly, on this install and on every other, with no
    # list of ids to keep true. A hand-listed set was the rejected alternative
    # and was already wrong when written (ruling 6 names four BitS Pipe Entry
    # PBs; there are seven, and the pb#138 it names does not exist here).
    #
    # DEFAULT 'igt' is the honest value for every pre-existing row up to the
    # moment the reproject runs: it is what every star, key and pipe-touch time
    # already is, and the only rows it is wrong for are the segment rows this
    # column exists to catch -- which the reproject fixes before anything reads
    # them. A NULL default would have made "not yet reprojected" and "measured
    # by delta" indistinguishable at exactly the sites that must tell them apart.
    """
    ALTER TABLE attempts ADD COLUMN timed_by TEXT NOT NULL DEFAULT 'igt';
    ALTER TABLE attempts ADD COLUMN closed_by TEXT;
    """,
    # v20 — WHICH MOMENT inside the closing event a star's time was taken at
    # (round-4 items 3/4). Usamune stops its clock at the x-cam; we stopped at
    # the grab, and the gap is 0-39 frames of Mario falling -- so every star
    # row recorded before 2026-08-01 holds a quantity no leaderboard accepts
    # and cannot be re-derived, because the journal keeps no post-grab frames.
    #
    # Same shape as v19 and for the same reason: no repair UPDATE, because
    # `Attempt.timed_at` is stamped by `_build` from the closing event's own
    # payload and projection is replay-derived, so the next reproject fills
    # every historical row correctly on every install with no list of ids to
    # keep true.
    #
    # NULL default, unlike v19's 'igt', and the asymmetry is deliberate: NULL
    # is the honest value for the MAJORITY of rows here (every segment, every
    # failure, every key/pipe closure), where the question does not arise at
    # all. v19's column described a property every row genuinely has; this one
    # describes a choice only a star grab makes.
    """
    ALTER TABLE attempts ADD COLUMN timed_at TEXT;
    """,
    # v21 — a seeded definition the human has EDITED is frozen against every
    # corpus refresh (`seed_dirty=1` blocks reconcile's update branch), so the
    # 2026-08-04 entrance sweep reached 55 shipped rows and stopped at his own
    # MIPS Clip and LBLJ. Live report the next morning: "timer still triggers
    # for MIPS CLIP upon actually entering DDD... it was still set up to use
    # entering DDD as the finish condition."
    #
    # This rewrites the END CLAUSE ONLY and leaves `seed_dirty` exactly as it
    # found it, which is the whole difference from v17. Clearing the flag was
    # right there (the rows were stranded by a retired feature, not by a
    # choice) and is WRONG here: both of his rows carry real edits beside the
    # end trigger -- MIPS Clip's start now pins the basement subarea, which is
    # better than what ships -- and reconcile would discard them. A repair may
    # fix the thing it is about; it may not spend the user's own work doing it.
    #
    # Guarded by SHAPE rather than by a list of keys, so it also catches the
    # intermediate `warp_entered`+`to` form that shipped for one afternoon on
    # 2026-08-04 and any row a hand edit left on the old shape: seeded rows
    # only, exactly one end clause, naming a destination that is not the castle
    # interior/grounds/courtyard (Lakitu Skip really does end on entering level
    # 6, and there is no entrance to the castle to touch). A destination-free
    # `warp_entered` -- the three legacy pipe entries -- has a NULL `to` and is
    # excluded by that same test. Idempotent: after it runs, no row matches.
    """
    UPDATE segment_defs
       SET end_triggers = json_array(
             json_object('type', 'entrance_touched',
                         'to', json_extract(end_triggers, '$[0].to')))
     WHERE seed_key IS NOT NULL
       AND json_valid(end_triggers)
       AND json_array_length(end_triggers) = 1
       AND json_extract(end_triggers, '$[0].type') IN ('level_enter',
                                                       'warp_entered')
       AND json_extract(end_triggers, '$[0].to') IS NOT NULL
       AND json_extract(end_triggers, '$[0].to') NOT IN (6, 16, 26);
    """,
    # v22 — a segment may be a SUBSECTION of a star or of another segment
    # (task 0087). The value is an entity key, "star:<course>:<slot>" or
    # "segment:<id>" — the same format sheet-library's mapping module emits,
    # so a subsection is mappable from the community sheet with no bridge.
    #
    # NO repair UPDATE and no NOT NULL default, unlike v13's default_strat and
    # v15's match_mode. Those two had to state a value for existing rows
    # because absence and "not set yet" meant different things there. Here
    # they mean the SAME thing: every definition written before today is
    # top-level, and NULL is exactly what top-level means. A DEFAULT would be
    # inventing a distinction the data does not have.
    """
    ALTER TABLE segment_defs ADD COLUMN parent TEXT;
    """,
    # v23 — Lakitu Skip ends at the DOOR, on his instruction: "Lakitu should be
    # determined by 'move it to the door' (when Mario touches the door)"
    # (2026-08-05). The corpus already says so (c9262be); his own row is
    # `seed_dirty=1`, so reconcile can never reach it and it keeps timing the
    # castle LOAD — 7"33 where the community reads 6"13, which is task 0026's
    # whole complaint.
    #
    # "TOUCHES" IS THE RIGHT WORD AND IT IS WHAT THE MOMENT MEASURES, checked
    # rather than assumed: `door_open` fires on the entry EDGE into
    # `addresses.DOOR_ACTIONS`, whose first two members are ACT_PULLING_DOOR
    # and ACT_PUSHING_DOOR — the frame Mario takes hold of the door, not the
    # frame the animation finishes.
    #
    # WHAT IT COSTS, measured before he decided and accepted by him: his 11
    # recorded Lakitu successes read 0 after this. `moment_reached` postdates
    # his entire journal, so no replay can produce one and nothing can be
    # backfilled. Leaving the row frozen was the alternative and he ruled
    # against it.
    #
    # Same discipline as v21, whose exclusion list (`to NOT IN (6, 16, 26)`)
    # is exactly what kept it away from this row: rewrite the END CLAUSE ONLY
    # and leave `seed_dirty` as found. A repair may fix the thing it is about;
    # it may not spend the user's own work doing it.
    #
    # Guarded on the seed_key AND the old shape, because here the identity IS
    # one definition rather than a shape 55 rows share — and shape alone would
    # match every other seeded row that legitimately ends on entering the
    # castle interior. Idempotent: after it runs, no row matches.
    """
    UPDATE segment_defs
       SET end_triggers = json_array(
             json_object('type', 'moment_reached', 'kind', 'door_open',
                         'level', 16, 'ordinal', 1))
     WHERE seed_key = 'seg:lakitu-skip'
       AND json_valid(end_triggers)
       AND json_array_length(end_triggers) = 1
       AND json_extract(end_triggers, '$[0].type') = 'level_enter'
       AND json_extract(end_triggers, '$[0].to') = 6;
    """,
    # THE LANDMARK CATALOGUE. One row names one thing he interacts with, and the
    # SAME table holds both levels of naming because `key` distinguishes them:
    # `kind:800ebc8c` names a whole family game-wide (every pole in the game at
    # once), `6:3:800ebc8c:1126,-1074,-2661` names one specific door. His ask,
    # 2026-08-05: "if we already know that a specific door is the door to HMC,
    # we don't ever need to redefine that" -- so these ship in
    # data/defaults.seed.json like segments and routes, with the same
    # seed_key/seed_dirty contract protecting an edit from the next refresh.
    """
    CREATE TABLE IF NOT EXISTS landmark_names (
        key         TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        seed_key    TEXT,
        seed_dirty  INTEGER NOT NULL DEFAULT 0,
        updated_utc TEXT
    );
    """,
    # WHEN THE CLOCK STARTS (round 15 item 3): "trigger" (the start trigger's
    # own frame — every row written before 2026-08-08) or "move" (the counter
    # zero the start caused; the moment Mario can move). NOT NULL with an
    # explicit default like v13/v15: absence and "trigger" mean the same
    # thing for every existing row, and the seeded corpus stays "trigger"
    # until the retiming of his recorded history is priced with numbers
    # rather than flipped silently.
    """
    ALTER TABLE segment_defs ADD COLUMN clock_start TEXT NOT NULL DEFAULT 'trigger';
    """,
    # v26 — the parent goes PLURAL (round 20 item 1): "sometimes the same
    # subsection might be practicable in multiple stars (in LLL, both Hot
    # Foot it Into The Volcano and Elevator Tour into the Volcano would do
    # volcano entry in the same way)". `parents` is a JSON array of the same
    # entity keys v22's scalar held; NULL/'[]' = top-level, same meaning
    # absence had. The old column is DROPPED, not mirrored — a scalar kept
    # beside the list is a second door for one fact, and every reader ships
    # in the same package as this schema. DROP COLUMN needs SQLite 3.35+;
    # Python 3.12's bundled sqlite3 (which the frozen exe carries too) is
    # well past it.
    """
    ALTER TABLE segment_defs ADD COLUMN parents TEXT;
    UPDATE segment_defs SET parents = json_array(parent)
     WHERE parent IS NOT NULL;
    ALTER TABLE segment_defs DROP COLUMN parent;
    """,
]

_ATTEMPT_COLS = ("id", "session_id", "course_id", "star_id", "strat_tag",
                 "anchor_type", "anchor_frame", "outcome", "outcome_detail",
                 "igt_frames", "rta_frames", "started_utc", "ended_utc",
                 "cleared", "cleared_reason",
                 "rollouts_total", "rollouts_dustless",
                 "jumps_total", "jumps_dustless",
                 "segment_id", "timed_by", "closed_by", "timed_at")


class EventRow:
    """One journal row, payload already decoded."""
    __slots__ = ("id", "session_id", "seq", "type", "frame",
                 "wall_time_utc", "payload")

    def __init__(self, id, session_id, seq, type, frame, wall_time_utc, payload):
        self.id, self.session_id, self.seq = id, session_id, seq
        self.type, self.frame = type, frame
        self.wall_time_utc, self.payload = wall_time_utc, payload


def _iso(dt) -> str:
    return dt.isoformat().replace("+00:00", "Z")


class Database:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for i, script in enumerate(MIGRATIONS[version:], start=version + 1):
                # One transaction per entry: a mid-migration crash rolls back
                # BOTH the partial schema changes and the version write
                # (PRAGMA user_version is a header field — transactional).
                # Without this, a crash inside v4's DROP/RENAME leaves no pbs
                # table, and re-opening dies on the duplicate-column ALTER.
                try:
                    self._conn.executescript(
                        f"BEGIN;{script};PRAGMA user_version = {i};COMMIT;")
                except Exception:
                    # a failed statement leaves the explicit txn open on the
                    # connection (write lock held) — release it before
                    # re-raising so a retry/reopen isn't "database is locked"
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK")
                    raise

    def close(self) -> None:
        self._conn.close()

    # -- journal -----------------------------------------------------------
    def append_event(self, session_id: int, seq: int, event: Event) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (session_id, seq, type, frame, wall_time_utc, payload)"
                " VALUES (?,?,?,?,?,?)",
                (session_id, seq, event.type, event.frame,
                 _iso(event.timestamp_utc), json.dumps(event.payload)))
            self._conn.commit()
            return cur.lastrowid

    # -- landmark catalogue --------------------------------------------------
    def landmark_names(self) -> dict[str, str]:
        """Every name in the catalogue: key -> name, kinds and instances alike."""
        with self._lock:
            rows = self._conn.execute("SELECT key, name FROM landmark_names").fetchall()
            return {row["key"]: row["name"] for row in rows}

    def name_landmark(self, key: str, name: str) -> None:
        """HIS naming gesture. Blank erases the row rather than storing "".

        `seed_dirty=1` because a name he typed is an edit, and reconcile must
        never overwrite it at the next corpus refresh -- the same contract
        segment_defs and routes have carried since 2026-07-23.
        """
        with self._lock:
            if not name.strip():
                self._conn.execute("DELETE FROM landmark_names WHERE key=?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO landmark_names (key, name, seed_dirty, updated_utc)"
                    " VALUES (?,?,1,?)"
                    " ON CONFLICT(key) DO UPDATE SET name=excluded.name,"
                    " seed_dirty=1, updated_utc=excluded.updated_utc",
                    (key, name.strip(), _iso(datetime.now(timezone.utc))))
            self._conn.commit()

    def seed_landmark_name(self, key: str, name: str, seed_key: str) -> None:
        """A SHIPPED name. Refreshes an untouched row, never a row he edited."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO landmark_names (key, name, seed_key, seed_dirty, updated_utc)"
                " VALUES (?,?,?,0,?)"
                " ON CONFLICT(key) DO UPDATE SET name=excluded.name,"
                " seed_key=excluded.seed_key, updated_utc=excluded.updated_utc"
                " WHERE landmark_names.seed_dirty = 0",
                (key, name, seed_key, _iso(datetime.now(timezone.utc))))
            self._conn.commit()

    def delete_events(self, ids: list[int]) -> None:
        with self._lock:
            self._conn.executemany("DELETE FROM events WHERE id=?",
                                   [(i,) for i in ids])
            self._conn.commit()

    def events(self, after_id: int | None = None) -> list[EventRow]:
        """The journal, oldest first. `after_id` returns only rows above that
        id — the tail, for a caller (the API's journal cache) that already
        holds everything up to it. Same shape either way."""
        with self._lock:
            if after_id is None:
                rows = self._conn.execute(
                    "SELECT * FROM events ORDER BY id").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM events WHERE id > ? ORDER BY id",
                    (after_id,)).fetchall()
            return [EventRow(r["id"], r["session_id"], r["seq"], r["type"],
                             r["frame"], r["wall_time_utc"], json.loads(r["payload"]))
                    for r in rows]

    # -- sessions ----------------------------------------------------------
    def insert_session(self, started_utc: str, label: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions (started_utc, label) VALUES (?,?)",
                (started_utc, label))
            self._conn.commit()
            return cur.lastrowid

    def end_session(self, session_id: int, ended_utc: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET ended_utc=? WHERE id=?",
                               (ended_utc, session_id))
            self._conn.commit()

    def reopen_session(self, session_id: int) -> None:
        with self._lock:
            self._conn.execute("UPDATE sessions SET ended_utc=NULL WHERE id=?",
                               (session_id,))
            self._conn.commit()

    def sessions(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT s.id, s.started_utc, s.ended_utc, s.label,"
                " (SELECT COUNT(*) FROM attempts a WHERE a.session_id = s.id)"
                "   AS attempts"
                " FROM sessions s ORDER BY s.id DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_session(self, session_id: int) -> None:
        """Hard-deletes the session's journal slice. The attempts cache is
        NOT touched here — callers must re-project afterwards (the journal
        is the source of truth).

        PB rows are NOT touched here either, and a caller that is deleting a
        session must drop them itself (`delete_pbs_for_attempts`, which is
        what `TrackerService.delete_session` does). A pb row carries its own
        `frames`, so one left behind keeps GRADING a time whose entire history
        is gone — an empty practice log under a real rank (live report
        2026-07-27). This used to read "PB rows survive… a dangling attempt_id
        is informational only", which was true of the row and false of what
        the row does."""
        with self._lock:
            self._conn.execute("DELETE FROM events WHERE session_id=?",
                               (session_id,))
            self._conn.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            self._conn.commit()

    def delete_empty_sessions(self, keep_session_id: int) -> list[int]:
        """Drop the session ROWS that hold no attempt, and return their ids.
        `keep_session_id` is the live session, which is always empty at boot.

        The journal slice STAYS, and that is the whole design. A session can
        bank no attempt of its own and still hold events that govern OTHER
        sessions' attempts — a clear, a strat reclassification, a prune
        verdict. Measured over the live journal (2026-08-04): cutting the
        events of every 0-attempt session RESURRECTS 2,167 pruned attempts
        (233 of them successes) and rewrites the star/strategy of 13 more.
        So this deletes only the row, which nothing derived reads —
        `projection.replay` never opens this table, and `views` looks a
        session up only for attempts that exist.

        Safe to leave the events pointing at a missing row because `sessions`
        is AUTOINCREMENT: a purged id is retired for good and can never be
        handed to a future session.

        Reads the `attempts` CACHE, so callers must have projected first."""
        with self._lock:
            doomed = [r["id"] for r in self._conn.execute(
                "SELECT id FROM sessions WHERE id<>?"
                " AND id NOT IN (SELECT DISTINCT session_id FROM attempts)",
                (keep_session_id,)).fetchall()]
            if doomed:
                self._conn.executemany("DELETE FROM sessions WHERE id=?",
                                       [(sid,) for sid in doomed])
                self._conn.commit()
            return doomed

    # -- attempts (derived cache) -------------------------------------------
    def _attempt_params(self, a: Attempt) -> tuple:
        return (a.id, a.session_id, a.course_id, a.star_id, a.strat_tag,
                a.anchor_type, a.anchor_frame, a.outcome, a.outcome_detail,
                a.igt_frames, a.rta_frames, a.started_utc, a.ended_utc,
                int(a.cleared), a.cleared_reason,
                a.rollouts_total, a.rollouts_dustless,
                a.jumps_total, a.jumps_dustless,
                a.segment_id, a.timed_by, a.closed_by, a.timed_at)

    def replace_attempts(self, attempts: list[Attempt]) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM attempts")
            self._conn.executemany(
                f"INSERT INTO attempts ({','.join(_ATTEMPT_COLS)})"
                f" VALUES ({','.join('?' * len(_ATTEMPT_COLS))})",
                [self._attempt_params(a) for a in attempts])
            self._conn.commit()

    def upsert_attempt(self, a: Attempt) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO attempts ({','.join(_ATTEMPT_COLS)})"
                f" VALUES ({','.join('?' * len(_ATTEMPT_COLS))})",
                self._attempt_params(a))
            self._conn.commit()

    def attempts(self) -> list[Attempt]:
        # Chronological by JOURNAL id, not the raw `id` column (spec
        # 2026-07-28-multi-step-segments, live report): a reattributed
        # 100-coin attempt keeps its SEGMENT-namespace id (arm.jid +
        # SEGMENT_ATTEMPT_OFFSET * def_id, caveat 2/11 in projection.py) —
        # a huge number that sorts permanently above every native
        # star-namespace attempt for the same entity regardless of when it
        # actually happened, which is exactly the bug (his practice log:
        # two reattributed successes stuck at the top forever while newer
        # resets piled up underneath, ordinal labels climbing under them).
        # `journal_id()` is the SAME resolver views.py already uses to
        # order SEGMENT SECTIONS by recency -- applied here to every
        # attempt, not just section ordering, so every consumer of this
        # list (grading's `valid_frames`, whose own docstring already
        # claims "journal-id ordered, so chronological" -- a claim this
        # makes true rather than merely documented) gets the correct order
        # for free, with no second sort to remember downstream.
        with self._lock:
            rows = self._conn.execute("SELECT * FROM attempts").fetchall()
            out = [Attempt(**{**{k: r[k] for k in _ATTEMPT_COLS},
                              "cleared": bool(r["cleared"])}) for r in rows]
        return sorted(out, key=lambda a: journal_id(a.id))

    # -- segment definitions -------------------------------------------------
    def segment_defs(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM segment_defs ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "enabled": bool(r["enabled"]),
                 "start_triggers": json.loads(r["start_triggers"]),
                 "end_triggers": json.loads(r["end_triggers"]),
                 "waypoints": json.loads(r["waypoints"]),
                 "guards": json.loads(r["guards"]),
                 "category": r["category"],
                 "seed_key": r["seed_key"], "seed_dirty": r["seed_dirty"],
                 "default_strat": r["default_strat"],
                 "match_mode": r["match_mode"],
                 "parents": json.loads(r["parents"]) if r["parents"] else [],
                 "clock_start": r["clock_start"],
                 "created_utc": r["created_utc"]} for r in rows]

    def insert_segment_def(self, name: str, start_triggers: list,
                           end_triggers: list, guards: list,
                           created_utc: str, enabled: bool = True,
                           waypoints: list | None = None,
                           category: str | None = None,
                           seed_key: str | None = None,
                           default_strat: str | None = None,
                           match_mode: str = "strict",
                           parents: list | None = None,
                           clock_start: str = "trigger") -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO segment_defs (name, enabled, start_triggers,"
                " end_triggers, waypoints, guards, category, seed_key,"
                " default_strat, match_mode, parents, clock_start,"
                " created_utc)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (name, int(enabled), json.dumps(start_triggers),
                 json.dumps(end_triggers), json.dumps(waypoints or []),
                 json.dumps(guards), category, seed_key, default_strat,
                 match_mode, json.dumps(parents or []), clock_start,
                 created_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_segment_def(self, def_id: int, **fields) -> None:
        cols = {"name": lambda v: v, "enabled": int,
                "start_triggers": json.dumps, "end_triggers": json.dumps,
                "waypoints": json.dumps, "guards": json.dumps,
                "category": lambda v: v, "seed_key": lambda v: v,
                "default_strat": lambda v: v, "seed_dirty": int,
                "match_mode": lambda v: v,
                "parents": lambda v: json.dumps(v or []),
                "clock_start": lambda v: v}
        if set(fields) - set(cols):
            raise ValueError(f"unknown fields {sorted(set(fields) - set(cols))}")
        sets, vals = [], []
        for k, conv in cols.items():
            if k in fields:
                sets.append(f"{k}=?"); vals.append(conv(fields[k]))
        if not sets:
            return
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE segment_defs SET {','.join(sets)} WHERE id=?",
                (*vals, def_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"segment {def_id} not found")

    def delete_segment_def(self, def_id: int) -> None:
        # attempts cache rows are NOT touched — callers must re-project
        # (mirrors delete_session)
        with self._lock:
            cur = self._conn.execute("DELETE FROM segment_defs WHERE id=?",
                                     (def_id,))
            self._conn.execute("DELETE FROM pbs WHERE segment_id=?",
                               (def_id,))  # spec: cascade — nothing to refer to
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"segment {def_id} not found")

    # -- routes (config) -----------------------------------------------------
    def routes(self) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM routes ORDER BY id").fetchall()
        return [{"id": r["id"], "name": r["name"],
                 "steps": json.loads(r["steps"]),
                 "start_condition": json.loads(r["start_condition"]),
                 "category": r["category"],
                 "seed_key": r["seed_key"], "seed_dirty": r["seed_dirty"],
                 "created_utc": r["created_utc"],
                 "updated_utc": r["updated_utc"]} for r in rows]

    def insert_route(self, name: str, steps: list, created_utc: str,
                     start_condition: dict | None = None,
                     category: str | None = None,
                     seed_key: str | None = None) -> int:
        sc = start_condition if start_condition is not None else {"type": "reset_game"}
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO routes (name, steps, start_condition, category,"
                " seed_key, created_utc, updated_utc) VALUES (?,?,?,?,?,?,?)",
                (name, json.dumps(steps), json.dumps(sc), category, seed_key,
                 created_utc, created_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_route(self, route_id: int, **fields) -> None:
        cols = {"name": lambda v: v, "steps": json.dumps,
                "start_condition": json.dumps, "category": lambda v: v,
                "seed_key": lambda v: v, "seed_dirty": int,
                "updated_utc": lambda v: v}
        if set(fields) - set(cols):
            raise ValueError(f"unknown fields {sorted(set(fields) - set(cols))}")
        sets, vals = [], []
        for k, conv in cols.items():
            if k in fields:
                sets.append(f"{k}=?"); vals.append(conv(fields[k]))
        if not sets:
            return
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE routes SET {','.join(sets)} WHERE id=?",
                (*vals, route_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"route {route_id} not found")

    def set_seed_dirty(self, table: str, row_id: int, dirty: int) -> None:
        """Flip the seed_dirty flag (1 = user-edited, protected from reconcile;
        0 = pristine/reset). `table` is 'segment_defs' or 'routes'."""
        if table not in ("segment_defs", "routes"):
            raise ValueError(f"bad table {table!r}")
        with self._lock:
            self._conn.execute(f"UPDATE {table} SET seed_dirty=? WHERE id=?",
                               (dirty, row_id))
            self._conn.commit()

    def delete_route(self, route_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM routes WHERE id=?",
                                     (route_id,))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"route {route_id} not found")

    # -- comparisons (config) ------------------------------------------------
    _COMP_COLS = ("id", "entity_key", "strat", "name", "source_kind",
                  "source_ref", "cache_name", "in_frame", "out_frame",
                  "created_utc", "last_used_utc")

    def comparisons(self, entity_key: str | None = None,
                    strat: str | None = None) -> list[dict]:
        q, params, where = "SELECT * FROM comparisons", [], []
        if entity_key is not None:
            where.append("entity_key=?"); params.append(entity_key)
        if strat is not None:
            where.append("strat=?"); params.append(strat)
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [{k: r[k] for k in self._COMP_COLS} for r in rows]

    def insert_comparison(self, entity_key: str, strat: str, name: str,
                          source_kind: str, source_ref: str, cache_name: str,
                          created_utc: str, last_used_utc: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO comparisons (entity_key, strat, name, source_kind,"
                " source_ref, cache_name, created_utc, last_used_utc)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (entity_key, strat, name, source_kind, source_ref, cache_name,
                 created_utc, last_used_utc))
            self._conn.commit()
            return cur.lastrowid

    def update_comparison(self, comp_id: int, **fields) -> None:
        cols = ("name", "strat", "in_frame", "out_frame", "last_used_utc")
        unknown = set(fields) - set(cols)
        if unknown:
            raise ValueError(f"unknown fields {sorted(unknown)}")
        sets, vals = [], []
        for k in cols:
            if k in fields:
                sets.append(f"{k}=?"); vals.append(fields[k])
        if not sets:
            return
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE comparisons SET {','.join(sets)} WHERE id=?",
                (*vals, comp_id))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"comparison {comp_id} not found")

    def delete_comparison(self, comp_id: int) -> None:
        with self._lock:
            cur = self._conn.execute("DELETE FROM comparisons WHERE id=?",
                                     (comp_id,))
            self._conn.commit()
        if cur.rowcount == 0:
            raise LookupError(f"comparison {comp_id} not found")

    def comparison_cache_refs(self, cache_name: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM comparisons WHERE cache_name=?",
                (cache_name,)).fetchone()
        return row["n"]

    # -- runs (history cache) ------------------------------------------------
    _RUN_COLS = ("id", "route_id", "route_name", "route_steps", "mode",
                 "status", "reached_step", "total_ms", "start_offset_ms",
                 "started_utc", "ended_utc", "is_pb", "splits")

    def _run_params(self, r: dict) -> tuple:
        return (r["id"], r["route_id"], r["route_name"],
                json.dumps(r["route_steps"]), r["mode"], r["status"],
                r["reached_step"], r["total_ms"], r["start_offset_ms"],
                r["started_utc"], r["ended_utc"], int(r["is_pb"]),
                json.dumps(r["splits"]))

    def insert_run(self, r: dict) -> None:
        with self._lock:
            self._conn.execute(
                f"INSERT OR REPLACE INTO runs ({','.join(self._RUN_COLS)})"
                f" VALUES ({','.join('?' * len(self._RUN_COLS))})",
                self._run_params(r))
            self._conn.commit()

    upsert_run = insert_run   # same INSERT OR REPLACE (id is stable)

    def replace_runs(self, runs: list) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM runs")
            self._conn.executemany(
                f"INSERT INTO runs ({','.join(self._RUN_COLS)})"
                f" VALUES ({','.join('?' * len(self._RUN_COLS))})",
                [self._run_params(r) for r in runs])
            self._conn.commit()

    def runs(self, route_id: int | None = None,
             finished_only: bool = False) -> list[dict]:
        q, params = "SELECT * FROM runs", []
        where = []
        if route_id is not None:
            where.append("route_id=?"); params.append(route_id)
        if finished_only:
            where.append("status='finished'")
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id"
        with self._lock:
            rows = self._conn.execute(q, params).fetchall()
        return [{"id": r["id"], "route_id": r["route_id"],
                 "route_name": r["route_name"],
                 "route_steps": json.loads(r["route_steps"]), "mode": r["mode"],
                 "status": r["status"], "reached_step": r["reached_step"],
                 "total_ms": r["total_ms"], "start_offset_ms": r["start_offset_ms"],
                 "started_utc": r["started_utc"], "ended_utc": r["ended_utc"],
                 "is_pb": bool(r["is_pb"]), "splits": json.loads(r["splits"])}
                for r in rows]

    # -- pbs -----------------------------------------------------------------
    # A saved PB whose attempt is HIDDEN does not count. Both readers below
    # carry this clause and nothing else in the codebase reads the table, so
    # there is one answer to "which saved times count" and no call site can
    # forget it (tests/test_single_source.py pins that).
    #
    # Deleting the row is the wrong tool for the hidden-by-a-RULE case, which
    # is why this is a read filter and not another cleanup: a success outside
    # its star's validity bounds is auto-cleared by the projector
    # (projection.py), the bounds are editable, and re-widening them brings the
    # attempt back — so its save has to come back with it. A row deleted on
    # reprojection could not. `clear_attempt` still deletes outright, because a
    # MANUAL hide is a judgement about the run and the user asked for it to
    # undo the save for good.
    #
    # Retroactive by construction: an install whose db already holds a pb row
    # on a hidden attempt (every one from before 2026-07-29) stops grading it
    # on the next read, with no migration.
    #
    # `attempt_id IS NULL` rows were never tied to an attempt and always count
    # — the same rule delete_orphaned_pbs applies, and it falls out of NOT
    # EXISTS for free (`attempts.id = NULL` matches nothing), so it is pinned by
    # a test rather than spelled out in the SQL.
    #
    # NOT EXISTS against the attempts PRIMARY KEY, deliberately, not
    # `NOT IN (SELECT id FROM attempts WHERE cleared=1)`: that form re-scans all
    # ~2k attempts on every call and took current_pb from 4 to 66 us (measured
    # against the live db). One rowid lookup per candidate row instead gives
    # current_pb back for free — 4.5 us, and it is called once per route
    # candidate per view build.
    _VISIBLE_PB = (" NOT EXISTS (SELECT 1 FROM attempts"
                   " WHERE attempts.id = pbs.attempt_id AND attempts.cleared=1)")

    def insert_pb(self, course_id: int | None, star_id: int | None,
                  strat_tag: str | None, timer_mode: str, frames: int,
                  attempt_id: int | None, saved_utc: str,
                  segment_id: int | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO pbs (course_id, star_id, segment_id, strat_tag,"
                " timer_mode, frames, attempt_id, saved_utc)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (course_id, star_id, segment_id, strat_tag, timer_mode,
                 frames, attempt_id, saved_utc))
            self._conn.commit()
            return cur.lastrowid

    def pbs(self) -> list[dict]:
        """Every saved PB that still counts, id-ordered (later saves win).

        Rows on hidden attempts are filtered out — see _VISIBLE_PB. This is
        the GRADING view of the table, which is what every caller wants; the
        delete/repair paths (delete_orphaned_pbs, delete_pbs_for_*) speak SQL
        directly and see every row."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM pbs WHERE" + self._VISIBLE_PB
                + " ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def current_pb(self, course_id: int | None, star_id: int | None,
                   timer_mode: str, segment_id: int | None = None,
                   strat_tag: str | None = None) -> dict | None:
        """Latest saved row for one star/segment + mode — the same row
        views._current_pbs picks (later saves win). Kind-aware like
        insert_pb: segment rows match by segment_id, star rows by
        course+star (segment_id IS NULL keeps the kinds disjoint).

        When strat_tag is given, restricts to PBs achieved WITH that
        strategy — the per-strategy ranking lookup (only a strategy's own
        times count toward its rank; the overall/strat-blind PB never does).

        Rows on hidden attempts are skipped (see _VISIBLE_PB), so hiding the
        current PB's run resolves to the previous save exactly as undoing it
        by hand does."""
        strat_clause = " AND strat_tag=?" if strat_tag is not None else ""
        strat_param = (strat_tag,) if strat_tag is not None else ()
        if segment_id is not None:
            q = ("SELECT * FROM pbs WHERE segment_id=? AND timer_mode=?"
                 + strat_clause + " AND" + self._VISIBLE_PB
                 + " ORDER BY id DESC LIMIT 1")
            params = (segment_id, timer_mode) + strat_param
        else:
            q = ("SELECT * FROM pbs WHERE course_id=? AND star_id=?"
                 " AND segment_id IS NULL AND timer_mode=?"
                 + strat_clause + " AND" + self._VISIBLE_PB
                 + " ORDER BY id DESC LIMIT 1")
            params = (course_id, star_id, timer_mode) + strat_param
        with self._lock:
            row = self._conn.execute(q, params).fetchone()
            return dict(row) if row else None

    def delete_pb(self, pb_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pbs WHERE id=?", (pb_id,))
            self._conn.commit()

    def purge_event_types(self, types) -> int:
        """Delete every journal row of the given types and RECLAIM the file
        space, returning how many went.

        For event types that are pure derived bookkeeping — the browser
        listens to them live and nothing ever reads them back out of the
        journal (`tracking/service.py::BROADCAST_ONLY`). Measured against the
        live journal 2026-08-02: 3,884 of 23,063 rows, 4.97 MB -> 3.42 MB,
        with every attempt and every run replaying byte-identical.

        VACUUM is the point, not a flourish: SQLite frees the pages into its
        own freelist on DELETE and the file on disk does not shrink, so
        without it this reclaims nothing a user could see. It cannot run
        inside a transaction, hence the explicit commit first, and it is
        skipped entirely when nothing was deleted — after the writer stops
        journaling these types that is every startup but the first.

        The checkpoint is the second half of the same fact and was found by
        watching the size NOT move: this db runs in WAL mode, so VACUUM's
        rebuilt pages land in the -wal sidecar and `tracker.db` keeps its old
        size until something checkpoints. TRUNCATE folds the WAL back in and
        resets it, so the shrink is visible immediately rather than whenever
        the next automatic checkpoint happens to fire.
        """
        types = tuple(types)
        if not types:
            return 0
        placeholders = ",".join("?" * len(types))
        with self._lock:
            cursor = self._conn.execute(
                f"DELETE FROM events WHERE type IN ({placeholders})", types)
            deleted = cursor.rowcount
            self._conn.commit()
            if deleted:
                self._conn.execute("VACUUM")
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return deleted

    def pb_attempt_ids(self) -> set[int]:
        """Every attempt a saved pb row points at.

        Deliberately NOT filtered by `_VISIBLE_PB`: that clause answers
        "which saved times may GRADE", and this answers "which attempts may
        never be deleted from under a save" (tracking/prune.py). A pb row on
        a hidden attempt still exists and is still his, and
        `delete_orphaned_pbs` below would collect it just the same.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT attempt_id FROM pbs"
                " WHERE attempt_id IS NOT NULL").fetchall()
        return {row[0] for row in rows}

    def delete_orphaned_pbs(self) -> int:
        """Drop pb rows whose saving attempt no longer exists, and return how
        many went.

        A pb row carries its own `frames`, so an orphan does not sit there
        inertly — it keeps GRADING. That is how a star kept reading MARIO 1
        with an empty practice log after its history was cleared (live report
        2026-07-27): the rows outlived the attempts by design, and nothing
        ever collected them.

        Run on every re-projection, which is what makes it a REPAIR and not
        just a guard: rows orphaned by any earlier delete or wipe — including
        ones made before the callers started cleaning up after themselves —
        are collected the next time the journal is replayed. An attempt's id
        is the journal id of its first event (projection.py), so it is never
        reused and this can never take a live row. `attempt_id IS NULL` rows
        are left alone: they were never tied to an attempt to begin with.
        """
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM pbs WHERE attempt_id IS NOT NULL"
                " AND attempt_id NOT IN (SELECT id FROM attempts)")
            self._conn.commit()
            return cursor.rowcount

    def delete_pbs_for_attempts(self, attempt_ids: list[int]) -> None:
        """Session-scoped wipes: drop pb rows saved from the wiped attempts
        so the previous PB (latest remaining row) restores automatically."""
        with self._lock:
            self._conn.executemany("DELETE FROM pbs WHERE attempt_id=?",
                                   [(i,) for i in attempt_ids])
            self._conn.commit()

    def retag_pbs_for_attempt(self, attempt_id: int,
                              strat_tag: str | None) -> None:
        """Follow an attempt's reclassification into the PBs it saved.

        A pbs row snapshots strat_tag at save time and is not derived from
        the journal, so it cannot self-heal on reproject the way the attempt
        does — without this the star's PB for the OLD strategy stays a time
        that was not achieved with it. Keyed on attempt_id, so re-picking the
        original strategy retags the row back."""
        with self._lock:
            self._conn.execute("UPDATE pbs SET strat_tag=? WHERE attempt_id=?",
                               (strat_tag, attempt_id))
            self._conn.commit()

    def delete_pbs_for_star(self, course_id: int, star_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM pbs WHERE course_id=? AND star_id=?"
                " AND segment_id IS NULL", (course_id, star_id))
            self._conn.commit()

    def delete_pbs_for_segment(self, segment_id: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM pbs WHERE segment_id=?",
                               (segment_id,))
            self._conn.commit()

    def wipe_all_history(self, keep_session_id: int) -> None:
        """Factory-reset of practice HISTORY: every journal event, every pb,
        every session row except the active one (it stays open and keeps
        receiving events). Segment definitions and ui_state survive — they
        are user configuration, not history. Callers must re-project."""
        with self._lock:
            self._conn.execute("DELETE FROM events")
            self._conn.execute("DELETE FROM pbs")
            self._conn.execute("DELETE FROM sessions WHERE id<>?",
                               (keep_session_id,))
            self._conn.commit()

    # -- ui_state ------------------------------------------------------------
    def get_state(self, key: str, default):
        with self._lock:
            row = self._conn.execute("SELECT value FROM ui_state WHERE key=?",
                                     (key,)).fetchone()
            return json.loads(row["value"]) if row else default

    def set_state(self, key: str, value) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO ui_state (key, value) VALUES (?,?)",
                (key, json.dumps(value)))
            self._conn.commit()
