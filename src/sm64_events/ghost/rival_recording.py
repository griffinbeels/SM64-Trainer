"""Bounded admission jobs after committed attempts, away from the poller.

The population keeps one recording per player in each strategy subdivision
(his question, 2026-10-13: "Did we save the BEST ghost per subdivision and
remove the old, worse ghost from the system?"). A run slower than its strategy
ladder's ghost ceiling (`ghost/community_catalog.py::GHOST_CEILING_PERCENT`)
creates no ghost locally nor a community offer, silently. A strategy change
re-admits the run under its new strategy and removes it from the old one.
Each start also admits saved PB runs whose committed clip never became a row
(his runs 18711 and 18930 finished untagged and were tagged only afterwards).
"""
import asyncio
from dataclasses import replace
import logging
from contextlib import suppress
from pathlib import Path

log = logging.getLogger("sm64.ghost")
_CURRENT_CONTRIBUTION = object()
MAX_BACKFILL = 64


class PopulationRecorder:
    def __init__(self, population, replay, tracker, identity_of, *, capacity=32, community=None):
        self.population, self.replay, self.tracker = population, replay, tracker
        self.identity_of = identity_of
        self.community = community
        self.queue = asyncio.Queue(maxsize=capacity)
        self.task = None
        self.job = None
        self.dropped = 0
        self.refused = 0
        self.accepted = 0
        self.skipped = 0
        self.pruned = 0
        self.backfilled = 0

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
        if self.task is None or attempt.outcome != "success" or attempt.valid is not True or attempt.cleared:
            return
        self._put("admit", attempt)

    def reclassify(self, attempt) -> None:
        """After a strategy change: a synchronous enqueue, like `enqueue`.

        The worker re-reads the attempt, so the caller passes whatever row it
        holds and never waits on preservation, grading or the community.
        """
        if self.task is None:
            return
        self._put("reclassify", attempt)

    def _put(self, kind, attempt):
        try:
            contribution = self.community.contribution_context() if self.community is not None else None
            self.queue.put_nowait((kind, attempt, contribution))
        except asyncio.QueueFull:
            self.dropped += 1

    def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="ghost-population")

    async def stop(self):
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        # A cancelled waiter cannot stop preservation on its worker thread.
        # Join it before the session releases native/replay/database resources.
        if self.job is not None:
            with suppress(Exception):
                await self.job
            self.job = None

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
        try:
            backlog = await self._job(self._backfill_candidates)
        except Exception:  # noqa: BLE001 -- optional backfill cannot end the worker
            backlog = ()
            log.exception("population backfill failed")
        contribution = self.community.contribution_context() if backlog and self.community is not None else None
        for attempt_id in backlog:
            self.backfilled += 1
            await self._process(self._backfill, attempt_id, contribution)
        while True:
            kind, attempt, contribution = await self.queue.get()
            try:
                await self._process(self._reclassify if kind == "reclassify" else self._admit,
                                    attempt, contribution)
            finally:
                self.queue.task_done()

    async def _process(self, work, attempt, contribution):
        number = getattr(attempt, "id", attempt)
        try:
            accepted = await self._job(work, attempt, contribution)
            if accepted:
                self.accepted += 1
            else:
                self.skipped += 1
        except (ValueError, LookupError, OSError):
            self.refused += 1
            log.debug("population refused attempt %s", number, exc_info=True)
        except Exception:  # noqa: BLE001 -- optional admission cannot end the worker
            self.refused += 1
            log.exception("population admission failed for attempt %s", number)

    def _admit(self, attempt, contribution=_CURRENT_CONTRIBUTION):
        from uuid import uuid4
        from sm64_events.ghost.community_catalog import within_strategy_ceiling
        from sm64_events.ghost.library_preferences import AUTHOR_KEY
        database = self.tracker.db
        if database is None:
            raise ValueError("The practice database is unavailable.")
        identity = self.identity_of(attempt)
        if identity is None:
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
        if contribution is not None:
            source = self.population.qualified_source(attempt, self.replay,
                clock=identity.clock, game_version=identity.game_version, full_start=True)
            self.community.remember_source(source, **contribution)
        slower = ()
        if ranks is not None:
            mine = self._subdivision_rows(attempt, identity, ranks, frames, player_id)
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

    def _subdivision_rows(self, attempt, identity, ranks, frames, player_id):
        """This player's rows in the run's strategy subdivision, or None when the
        run has no grade. Subdivisions are the run's own strategy ladder,
        whichever scope the card shows."""
        from sm64_events.ghost.adaptive import rank_frames, rank_recordings
        bins = replace(identity, scope="strategy")
        progress = rank_frames(ranks, bins, frames)
        if progress is None:
            return None
        records = self.store.metadata_snapshot(identity.kind, identity.entity_key,
            clock=identity.clock, game_version=identity.game_version, strategy=identity.strategy)
        return [row for row in rank_recordings(records, ranks, bins)
                if row["player_id"] == player_id and row["strategy"] == attempt.strat_tag
                and row["step"] == progress["step"]]

    def _reclassify(self, attempt, contribution=_CURRENT_CONTRIBUTION):
        """Follow the attempt's CURRENT strategy: drop other-strategy rows, re-admit."""
        from sm64_events.ghost.pb import local_attempt_source
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
        from sm64_events.ghost.pb import local_attempt_source
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
        """The normal admission on the current attempt: ceiling, bins and offer all apply.

        A run whose subdivision already holds this player's equal-or-faster row
        is skipped before any hash or cloud offer: it would be a candidate again
        on every start, and each offer spends one of the account's daily cloud
        operations on a "not faster" answer.
        """
        attempt = self.replay.attempt(attempt_id)
        identity = self.identity_of(attempt)
        ranks = getattr(self.tracker, "ranks", None)
        player_id = self.tracker.db.get_state("ghost_player_id", None) if self.tracker.db is not None else None
        if identity is not None and ranks is not None and player_id:
            frames = getattr(attempt, f"{identity.clock}_frames", None)
            mine = self._subdivision_rows(attempt, identity, ranks, frames, player_id)
            if mine and any(row["frames"] <= frames for row in mine):
                return False
        return self._admit(attempt, contribution)

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
