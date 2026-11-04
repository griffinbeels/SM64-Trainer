"""Bounded admission jobs after committed attempts, away from the poller.

The population keeps one recording per player in each strategy subdivision
(his question, 2026-10-13: "Did we save the BEST ghost per subdivision and
remove the old, worse ghost from the system?"). A run slower than its strategy
ladder's ghost ceiling (`ghost/community_catalog.py::GHOST_CEILING_PERCENT`)
creates no ghost locally nor a community offer, silently. A strategy change
re-admits the run under its new strategy and removes it from the old one.
Each start also admits saved PB runs whose committed clip never became a row
(his runs 18711 and 18930 finished untagged and were tagged only afterwards).
An unassigned certified success retains its capture before the rolling archive
expires. It acquires no population row or community offer until classified.

No run owed a ghost is lost silently (his rule, 2026-11-02: "I want to make
sure we keep all of them and upload each one to the server"):

- The queue has no capacity to overflow. Runs arrive at human speed.
- A failed preservation retries with backoff while the poses still exist
  (`ARCHIVE_RETENTION_S`), then logs one error. A plain ValueError still means
  the run is not eligible and refuses at once, silently.
- Stop drains the queue within DRAIN_S, committing without waiting for a star
  dance, and logs what it could not finish. The poses go with the recorder.
- The `owed` table in population.sqlite3 lists every run the worker has taken
  and not finished. The next start admits those whose clip was committed. It
  holds an attempt only while it is owed, so a start grades no other clip.
- A run its own kept rows already beat in the bin never reaches the community
  outbox, so it spends no daily operation and evicts no best.

Not covered: a crash during an attempt or before its clip commits. The poses
live in session scratch, and the next start has a new archive.
"""
import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import logging
from contextlib import suppress
from pathlib import Path
import sqlite3
import time

from sm64_events.core.error_reporting_privacy import scrub_text
from sm64_events.ghost.archive import ARCHIVE_RETENTION_S
from sm64_events.ghost.pb import local_attempt_source
from sm64_events.ghost.population import PopulationFull, PreservationFailed

log = logging.getLogger("sm64.ghost")
_CURRENT_CONTRIBUTION = object()
MAX_BACKFILL = 64
MAX_OWED = 256
# Together these outlast ARCHIVE_RETENTION_S, so the poses' age ends the retries.
RETRY_DELAYS_S = (5, 15, 45, 120, 300, 600, 1200, 1800, 1800, 1800)
# Inside the shutdown budget: 30 s to force exit, less 3 s for connections,
# 5 s for the community and 15 s for the recorder (`server/app.py`).
DRAIN_S = 4.0
# How long a run owed an upload waits for its account to return.
OWED_OFFER_DAYS = 14


def failure_reason(error) -> str:
    """Why, for a log or Sentry message. str(OSError) names the file, so never that."""
    if isinstance(error, OSError):
        return error.strerror or type(error).__name__
    return scrub_text(error, 200)


class _OfferLater(Exception):
    """The run has its row, and its upload waits for its account to return."""


@dataclass
class _Job:
    kind: str  # admit, reclassify, keep, owed or backfill
    attempt: object  # owed: the stored row; backfill: the attempt number
    contribution: object = None
    tries: int = 0
    on_kept: object = None
    reported: object = None  # keep: the reason the session already logged as an error
    queued: bool = False


class _CommitNow:
    """The replay service while the app closes: a clip commits without its star dance."""
    def __init__(self, replay):
        self._replay = replay

    def __getattr__(self, name):
        return getattr(self._replay, name)

    def preserve_native(self, attempt_id):
        return self._replay.preserve_native(attempt_id, wait_tail=False)


class PopulationRecorder:
    def __init__(self, population, replay, tracker, identity_of, *, community=None):
        self.population, self.replay, self.tracker = population, replay, tracker
        self.identity_of = identity_of
        self.community = community
        self.queue = asyncio.Queue()
        self.task = None
        self.job = None
        self.dropped = 0
        self.refused = 0
        self.accepted = 0
        self.skipped = 0
        self.pruned = 0
        self.backfilled = 0
        self.closing = False
        self.retry_delays = RETRY_DELAYS_S
        self._retries = []  # (due, tie, job)
        self._retry_tie = 0
        self._reported = set()  # One Sentry error per reason, per session.
        if community is not None:
            community.earlier_sources = self.earlier_sources

    @property
    def store(self):
        """The local store under the bundled/community wrappers: it owns the rows.

        The wrappers delegate admission and merge reads; only this store's rows
        are this install's recordings, so replacement and pruning act on it.
        """
        store = self.population
        while not hasattr(store, "rows_for_source") and hasattr(store, "local"):
            store = store.local
        return store

    def enqueue(self, attempt):
        """A synchronous enqueue only; never preserve/read/grade on the poll loop."""
        if attempt.outcome != "success" or attempt.valid is not True or attempt.cleared:
            return
        self._put("admit", attempt)

    def reclassify(self, attempt) -> None:
        """After a strategy change: a synchronous enqueue, like `enqueue`.

        The worker re-reads the attempt, so the caller passes whatever row it
        holds and never waits on preservation, grading or the community.
        """
        self._put("reclassify", attempt)

    def keep_pb(self, attempt, on_kept=None, reported=None) -> None:
        """A saved PB whose poses did not commit: keep trying while they exist.

        Independent of admission: a PB past the ceiling or with no grade still
        keeps its ghost. `on_kept(attempt_id)` runs on the event loop afterwards.
        """
        self._put("keep", attempt, on_kept=on_kept, reported=reported)

    def _put(self, kind, attempt, **details):
        if self.closing:  # The poses are about to go, and nothing would ever read this.
            self.dropped += 1
            log.warning("attempt %s finished while the app was closing, so it keeps no ghost",
                        getattr(attempt, "id", attempt))
            return
        contribution = self.community.contribution_context() if self.community is not None else None
        self.queue.put_nowait(_Job(kind, attempt, contribution, queued=True, **details))

    def start(self):
        if self.task is None:
            self.closing = False
            self.task = asyncio.create_task(self._run(), name="ghost-population")

    async def stop(self):
        """Finish what the queue holds within DRAIN_S, then say what is left."""
        task, self.task = self.task, None
        self.closing = True
        replay, self.replay = self.replay, _CommitNow(self.replay)
        if task is not None:
            self.queue.put_nowait(None)  # Wakes a worker waiting on an empty queue.
            done, _pending = await asyncio.wait({task}, timeout=DRAIN_S)
            if not done:
                task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:  # noqa: BLE001 -- a worker failure cannot stop native cleanup
                log.exception("population worker ended with an error")
        # A cancelled waiter cannot stop preservation on its worker thread.
        # Join it before the session releases native/replay/database resources.
        if self.job is not None:
            with suppress(Exception):
                await self.job
            self.job = None
        self.replay = replay
        left = len(self._retries)
        self._retries.clear()
        while not self.queue.empty():
            if self.queue.get_nowait() is not None:
                left += 1
            self.queue.task_done()
        if left:
            log.error("%s finished runs were not admitted before the app closed; "
                      "the next start admits those whose poses were committed", left)

    async def _job(self, work, *args):
        self.job = asyncio.create_task(asyncio.to_thread(work, *args))
        try:
            return await asyncio.shield(self.job)
        finally:
            if self.job is not None and self.job.done():
                self.job = None

    async def _run(self):
        try:
            self.pruned += await self._job(self._prune)
        except Exception:  # noqa: BLE001 -- optional cleanup cannot end the worker
            log.exception("population prune failed")
        await self._run_backlog()
        while (job := await self._next()) is not None:
            queued, job.queued = job.queued, False  # A retry is not a queue item.
            try:
                await self._process(job)
            finally:
                if queued:
                    self.queue.task_done()

    async def _run_backlog(self):
        """What earlier sessions left: owed runs first, so backfill sees their rows."""
        try:
            owed = await self._job(self._owed_candidates)
        except Exception:  # noqa: BLE001 -- optional recovery cannot end the worker
            owed = ()
            log.exception("population could not read its owed runs")
        for row in owed:
            if self.closing:
                return
            await self._process(_Job("owed", row))
        try:
            backlog = await self._job(self._backfill_candidates)
        except Exception:  # noqa: BLE001 -- optional backfill cannot end the worker
            backlog = ()
            log.exception("population backfill failed")
        contribution = self.community.contribution_context() if backlog and self.community is not None else None
        for attempt_id in backlog:
            if self.closing:
                return
            self.backfilled += 1
            await self._process(_Job("backfill", attempt_id, contribution))

    async def _next(self):
        """The next job: the queue, or a retry coming due. While closing, what is left, once."""
        while True:
            if self.closing:
                if not self.queue.empty():
                    job = self.queue.get_nowait()
                elif self._retries:
                    return self._retries.pop(0)[2]
                else:
                    return None
            else:
                due = self._retries[0][0] if self._retries else None
                try:
                    job = await asyncio.wait_for(self.queue.get(), None if due is None else max(0, due - time.monotonic()))
                except TimeoutError:
                    return self._retries.pop(0)[2]
            if job is not None:
                return job
            self.queue.task_done()  # The sentinel from stop; `closing` is already set.

    async def _process(self, job):
        number = self._number(job)
        try:
            accepted = await self._job(self._work, job)
        except _OfferLater as later:
            self._retry(job, later)
        except PopulationFull as error:
            self.refused += 1
            self._give_up(job, number, failure_reason(error))
            await self._settle(job)
        except (PreservationFailed, OSError, sqlite3.Error) as error:
            await self._failed(job, error)
        except (ValueError, LookupError):
            self.refused += 1
            log.debug("population refused attempt %s", number, exc_info=True)
            await self._settle(job)
        except Exception:  # noqa: BLE001 -- optional admission cannot end the worker
            self.refused += 1
            log.exception("population admission failed for attempt %s", number)
            await self._settle(job)
        else:
            if accepted:
                self.accepted += 1
            else:
                self.skipped += 1
            await self._settle(job)
            if job.kind == "keep" and job.on_kept is not None:
                try:
                    await job.on_kept(number)
                except Exception:  # noqa: BLE001 -- the clip is kept; a stale lineup heals itself
                    log.exception("the ghost could not follow PB %s", number)

    @staticmethod
    def _number(job):
        if job.kind == "owed":
            return job.attempt["attempt_id"]
        return getattr(job.attempt, "id", job.attempt)

    def _work(self, job):
        if job.kind == "admit":
            self._note_owed(job)
            return self._admit(job.attempt, job.contribution)
        if job.kind == "reclassify":
            return self._reclassify(job.attempt, job.contribution)
        if job.kind == "keep":
            return self._keep(job.attempt)
        if job.kind == "owed":
            return self._admit_owed(job.attempt)
        return self._backfill(job.attempt, job.contribution)

    def _note_owed(self, job):
        source = local_attempt_source(job.attempt)
        note = getattr(self.store, "note_owed", None)
        if source is not None and note is not None:
            owner = job.contribution.get("owner") if isinstance(job.contribution, dict) else None
            note(source, owner, datetime.now(timezone.utc).isoformat())

    async def _settle(self, job):
        """Nothing more is owed for this job's run, this session or the next."""
        if job.kind not in ("admit", "owed"):
            return
        source = (local_attempt_source(job.attempt) if job.kind == "admit" else
                  {key: job.attempt[key] for key in ("attempt_id", "session_id", "started_utc")})
        settle = getattr(self.store, "settle_owed", None)
        if source is None or settle is None:
            return
        try:
            await self._job(settle, source)
        except (OSError, sqlite3.Error):  # A row left owed is only admitted again.
            log.debug("population could not settle attempt %s", self._number(job), exc_info=True)

    def _poses_alive(self, attempt):
        """Whether the rolling archive may still hold this run's poses."""
        try:
            ended = datetime.fromisoformat(attempt.ended_utc.replace("Z", "+00:00"))
            return (self._now() - ended).total_seconds() < ARCHIVE_RETENTION_S
        except (AttributeError, TypeError, ValueError):
            return False

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    async def _failed(self, job, error):
        """Retry while it can still work, then say so once. Old runs fail quietly:
        a reclassified or backfilled run outlived its poses long ago."""
        number, reason = self._number(job), failure_reason(error)
        lost_poses = isinstance(error, PreservationFailed)
        live = job.kind in ("admit", "reclassify", "keep") and self._poses_alive(job.attempt)
        if lost_poses and not live:
            if job.tries == 0:
                self.refused += 1
                log.debug("population refused attempt %s: %s", number, reason)
                await self._settle(job)
                return
        elif not self.closing and self._retry(job, error):
            return
        self.refused += 1
        self._give_up(job, number, reason, reported=job.reported)
        if lost_poses and not self.closing:
            await self._settle(job)  # Poses are gone, and only they could have helped.

    def _retry(self, job, _error):
        """Schedule this job again; False when its retries are spent."""
        if self.closing or job.tries >= len(self.retry_delays):
            return False
        delay = self.retry_delays[job.tries]
        job.tries += 1
        self._retry_tie += 1
        self._retries.append((time.monotonic() + delay, self._retry_tie, job))
        self._retries.sort(key=lambda entry: entry[:2])
        return True

    def _give_up(self, job, number, reason, *, reported=None):
        """An error reaches Sentry; each reason does so once a session."""
        first = reason not in self._reported and reason != reported
        self._reported.add(reason)
        (log.error if first else log.warning)(
            "%s %s lost its ghost: %s", "Saved PB" if job.kind == "keep" else "Attempt", number, reason)

    def _keep(self, attempt):
        kept = self.replay.preserve_native(attempt.id)
        if not (isinstance(kept, dict) and kept.get("native_ghost")):
            raise PreservationFailed((isinstance(kept, dict) and kept.get("native_ghost_reason"))
                                     or "This PB has no preserved Mario recording.")
        return True

    def _owed_candidates(self):
        runs = getattr(self.store, "owed_runs", None)
        return runs(MAX_OWED) if runs is not None else ()

    def _committed(self, attempt_id):
        root = getattr(getattr(self.replay, "cfg", None), "save_root", None)
        return root is None or (Path(root).resolve() / "native" / f"attempt_{attempt_id}.json").is_file()

    def _admit_owed(self, row):
        """A run an earlier session took and never finished.

        Its account comes from the row, never from today's login. At boot the
        account may not be back yet, so the row lands now and the offer waits.
        """
        source = {key: row[key] for key in ("attempt_id", "session_id", "started_utc")}
        attempt = self.replay.attempt(row["attempt_id"])  # LookupError: refused and settled.
        if local_attempt_source(attempt) != source:
            return False  # A fresh profile reused the number.
        if not self._committed(row["attempt_id"]):
            log.warning("Attempt %s ended with the app before its Mario poses were saved, so it has no ghost",
                        row["attempt_id"])
            return False
        owner = row["owner"] if self.community is not None else None
        noted = datetime.fromisoformat(row["noted_utc"])
        if owner and (self._now() - noted).days >= OWED_OFFER_DAYS:
            owner = None
        context = self.community.contribution_context() if owner else None
        if context is not None and context.get("owner") != owner:
            context, owner = None, None  # Another account never inherits this run.
        accepted = self._admit(attempt, context)
        if owner and context is None:
            raise _OfferLater()
        return accepted

    def _admit(self, attempt, contribution=_CURRENT_CONTRIBUTION):
        from uuid import uuid4
        from sm64_events.ghost.community_catalog import within_strategy_ceiling
        from sm64_events.ghost.library_preferences import AUTHOR_KEY
        database = self.tracker.db
        if database is None:
            raise ValueError("The practice database is unavailable.")
        identity = self.identity_of(attempt)
        if identity is None:
            if not getattr(attempt, "strat_tag", None):
                return self._preserve_unassigned(attempt, contribution)
            raise ValueError("This attempt has no complete compatible start certificate.")
        ranks = getattr(self.tracker, "ranks", None)
        frames = getattr(attempt, f"{identity.clock}_frames", None)
        if ranks is not None and not within_strategy_ceiling(ranks, identity, frames):
            # His rule: no ghost and no community offer, and nothing tells him so.
            log.debug("attempt %s is slower than its strategy's ghost ceiling", attempt.id)
            return False
        player_id = database.get_state("ghost_player_id", None)
        if not player_id:
            player_id = str(uuid4())
            database.set_state("ghost_player_id", player_id)
        author = database.get_state(AUTHOR_KEY, "") or "You"
        if contribution is _CURRENT_CONTRIBUTION:
            contribution = self.community.contribution_context() if self.community is not None else None
        step, mine = self._subdivision(attempt, identity, ranks, frames, player_id)
        if contribution is not None:
            worth, offered_bin = self._offer_decision(attempt, identity, frames, step, mine, player_id)
            if worth:
                source = self.population.qualified_source(attempt, self.replay,
                    clock=identity.clock, game_version=identity.game_version, full_start=True)
                if offered_bin is not None:
                    source = {**source, "bin": offered_bin}
                self.community.remember_source(source, **contribution)
        slower = ()
        if ranks is not None:
            if mine is None or any(row["frames"] <= frames for row in mine):
                return False
            slower = tuple(row["id"] for row in mine)
        accepted = self.population.accept_verified(attempt, self.replay, player_id=player_id,
            player_name=author, clock=identity.clock, game_version=identity.game_version,
            full_start=True)
        kept = accepted.get("id") if isinstance(accepted, dict) else None
        if slower:
            self.store.remove([row_id for row_id in slower if row_id != kept])
        return True

    def _preserve_unassigned(self, attempt, contribution):
        """Keep certified bytes only, without inventing a strategy or grading one.

        The normal reclassification callback later retargets these manifests
        and applies every final admission rule. Native-only commits also spare
        their unassigned attempts from the existing startup prune.
        """
        from sm64_events.ghost.adaptive import PracticeIdentity, rank_entity_key
        from sm64_events.ghost.pb import eligible_capture
        from sm64_events.ghost.population import _preserved_asset
        from sm64_events.inputs.service import entity_key_of
        current = self.replay.attempt(attempt.id)
        if current.strat_tag:
            return self._admit(current, contribution)
        ranks = getattr(self.tracker, "ranks", None)
        if ranks is None:
            raise ValueError("This attempt has no known practice clock.")
        kind, key = entity_key_of(current)
        identity = PracticeIdentity(kind, key, "igt", current.game_version, None)
        identity = replace(identity, clock=ranks.clock_for(rank_entity_key(identity)))
        if not eligible_capture(current, identity):
            raise ValueError("This attempt has no complete compatible start certificate.")
        _preserved_asset(self.replay, current)  # Strict owner, hash and complete-input validation.
        return False  # Retained capture, never an admitted population recording.

    def _subdivision(self, attempt, identity, ranks, frames, player_id):
        """The run's step and this player's rows there, or (None, None) when the
        run has no grade. Subdivisions are the run's own strategy ladder,
        whichever scope the card shows."""
        from sm64_events.ghost.adaptive import rank_frames, rank_recordings
        if ranks is None:
            return None, None
        bins = replace(identity, scope="strategy")
        progress = rank_frames(ranks, bins, frames)
        if progress is None:
            return None, None
        records = self.store.metadata_snapshot(identity.kind, identity.entity_key,
            clock=identity.clock, game_version=identity.game_version, strategy=identity.strategy)
        return progress["step"], [row for row in rank_recordings(records, ranks, bins)
                                  if row["player_id"] == player_id and row["strategy"] == attempt.strat_tag
                                  and row["step"] == progress["step"]]

    def _offer_decision(self, attempt, identity, frames, step, mine, player_id):
        """(whether an offer could win its bin, which bin), before any hash or cloud call.

        The bin is the cloud's when its catalog is held, because the cloud's
        bands decide and can differ from a local ladder. Otherwise the local
        grade. With neither there is no way to know, so the run is offered and
        the cloud answers. The run's own kept row is no reason to skip: its
        offer may never have been made.
        """
        lineage = local_attempt_source(attempt)

        def beaten(rows):
            return any(row["frames"] < frames or row["frames"] == frames and row.get("source") != lineage
                       for row in rows)
        ask, portable = getattr(self.community, "catalog_step", None), getattr(self.store, "portable_target", None)
        if ask is not None and portable is not None and type(frames) is int:
            target = portable(attempt)

            def cloud_step(time):
                return ask(target, identity.clock, identity.game_version, attempt.strat_tag, time)
            cloud = cloud_step(frames)
            if cloud is not None:
                rows = self.store.metadata_snapshot(identity.kind, identity.entity_key, clock=identity.clock,
                    game_version=identity.game_version, strategy=identity.strategy)
                return not beaten(row for row in rows if row["player_id"] == player_id
                                  and row["strategy"] == attempt.strat_tag
                                  and cloud_step(row["frames"]) == cloud), cloud
        if mine is None:
            return True, None
        return not beaten(mine), step

    def _reclassify(self, attempt, contribution=_CURRENT_CONTRIBUTION):
        """Follow the attempt's CURRENT strategy: drop other-strategy rows, re-admit."""
        from sm64_events.ghost.population import committed_digest
        current = self.replay.attempt(attempt.id)
        strategy = current.strat_tag or None
        rows = self.store.rows_for_source(local_attempt_source(current))
        stale = [row for row in rows if row["strategy"] != strategy]
        digests = list(dict.fromkeys(row["sha256"] for row in stale))
        if stale:
            self.store.remove([row["id"] for row in stale])
        offered = committed_digest(self.replay, current)
        if offered is not None and offered not in digests:
            digests.append(offered)
        retract = getattr(self.community, "retract_other_strategies", None) if self.community is not None else None
        for digest in digests if retract is not None else ():
            try:
                retract(raw_sha256=digest, keep_strategy=strategy)
            except Exception:  # noqa: BLE001 -- community IO cannot block local reclassification
                log.debug("community retraction failed for attempt %s", current.id, exc_info=True)
        if strategy is None:
            return False
        return self._admit(current, contribution)

    def _backfill_candidates(self):
        """Saved PB runs with a committed clip and no population row, newest first.

        A cheap existence check on the committed manifest, never a hash; the
        admission itself re-reads and validates. A row counts only for the
        run's own lineage (a fresh profile reuses attempt numbers), and the
        attempt is read only when its number already has a row. Bounded by
        MAX_BACKFILL.
        """
        pbs = getattr(self.tracker.db, "pbs", None)
        root = getattr(getattr(self.replay, "cfg", None), "save_root", None)
        admitted = getattr(self.store, "source_lineages", None)
        if pbs is None or root is None or admitted is None:
            return ()
        present = admitted()
        numbers = {lineage[1] for lineage in present}
        native = Path(root).resolve() / "native"
        result = []
        for attempt_id in sorted({row.get("attempt_id") for row in pbs()} - {None}, reverse=True):
            if not (native / f"attempt_{attempt_id}.json").is_file():
                continue
            if attempt_id in numbers:
                try:
                    lineage = local_attempt_source(self.replay.attempt(attempt_id))
                except (LookupError, ValueError, RuntimeError):
                    continue
                if lineage is None or (lineage["session_id"], attempt_id, lineage["started_utc"]) in present:
                    continue
            result.append(attempt_id)
            if len(result) == MAX_BACKFILL:
                break
        return tuple(result)

    def _backfill(self, attempt_id, contribution):
        """The normal admission on the current attempt: ceiling, bins and offer all apply."""
        return self._admit(self.replay.attempt(attempt_id), contribution)

    def earlier_sources(self):
        """Offers for every ghost this install recorded, or None while the store is not ready."""
        from sm64_events.community.earlier_ghosts import own_sources
        database, store = self.tracker.db, self.store
        player = database.get_state("ghost_player_id", None) if database is not None else None
        snapshot = getattr(store, "snapshot_all", None)
        if not player or snapshot is None:
            return None
        return own_sources(snapshot(), player, store.root)

    def _prune(self):
        """Once per start: keep each player's fastest recording per strategy subdivision.

        Uses the current ranks; a target whose ladder or clock cannot grade its
        rows is left alone. Bounded by `snapshot_all` (MAX_RECORDINGS).
        """
        ranks = getattr(self.tracker, "ranks", None)
        snapshot_all = getattr(self.store, "snapshot_all", None)
        if ranks is None or snapshot_all is None:
            return 0
        from sm64_events.ghost.adaptive import PracticeIdentity, rank_recordings
        from sm64_events.ghost.community_catalog import strategy_ghost_cutoff, within_ghost_ceiling
        groups = {}
        for row in snapshot_all():
            key = (row["kind"], row["entity_key"], row.get("signature"),
                   row.get("clock"), row.get("game_version"), row["strategy"])
            groups.setdefault(key, []).append(row)
        doomed = []
        for (kind, entity, signature, clock, version, strategy), rows in groups.items():
            try:
                identity = PracticeIdentity(kind, entity, clock, version, strategy, signature)
                graded = rank_recordings(rows, ranks, identity)
                slowest = strategy_ghost_cutoff(ranks, identity)  # once per group, not per row
            except (ValueError, LookupError):
                continue
            fastest = {}
            for row in sorted(graded, key=lambda row: (row["frames"], row["id"])):
                if not within_ghost_ceiling(row["frames"], slowest):
                    doomed.append(row["id"])  # stored before the ceiling existed
                elif fastest.setdefault((row["player_id"], row["step"]), row["id"]) != row["id"]:
                    doomed.append(row["id"])
        return self.store.remove(doomed) if doomed else 0
