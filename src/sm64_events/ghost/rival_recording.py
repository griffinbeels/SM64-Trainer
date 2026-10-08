"""Bounded admission jobs after committed attempts, away from the poller."""
import asyncio
import logging
from contextlib import suppress

log = logging.getLogger("sm64.ghost")


class PopulationRecorder:
    def __init__(self, population, replay, tracker, identity_of, *, capacity=32):
        self.population, self.replay, self.tracker = population, replay, tracker
        self.identity_of = identity_of
        self.queue = asyncio.Queue(maxsize=capacity)
        self.task = None
        self.job = None
        self.dropped = 0
        self.refused = 0
        self.accepted = 0
        self.skipped = 0

    def enqueue(self, attempt):
        """A synchronous enqueue only; never preserve/read/grade on the poll loop."""
        if self.task is None or attempt.outcome != "success" or attempt.valid is not True or attempt.cleared:
            return
        try:
            self.queue.put_nowait(attempt)
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

    async def _run(self):
        while True:
            attempt = await self.queue.get()
            try:
                self.job = asyncio.create_task(asyncio.to_thread(self._admit, attempt))
                accepted = await asyncio.shield(self.job)
                if accepted:
                    self.accepted += 1
                else:
                    self.skipped += 1
            except (ValueError, LookupError, OSError):
                self.refused += 1
                log.debug("population refused attempt %s", attempt.id, exc_info=True)
            except Exception:  # noqa: BLE001 -- optional admission cannot end the worker
                self.refused += 1
                log.exception("population admission failed for attempt %s", attempt.id)
            finally:
                if self.job is not None and self.job.done():
                    self.job = None
                self.queue.task_done()

    def _admit(self, attempt):
        from uuid import uuid4
        from sm64_events.ghost.library_preferences import AUTHOR_KEY
        database = self.tracker.db
        if database is None:
            raise ValueError("The practice database is unavailable.")
        identity = self.identity_of(attempt)
        if identity is None:
            raise ValueError("This attempt has no complete compatible start certificate.")
        player_id = database.get_state("ghost_player_id", None)
        if not player_id:
            player_id = str(uuid4())
            database.set_state("ghost_player_id", player_id)
        author = database.get_state(AUTHOR_KEY, "") or "You"
        ranks = getattr(self.tracker,"ranks",None)
        if ranks is not None:
            from sm64_events.ghost.adaptive import rank_frames, rank_recordings
            frames = getattr(attempt,f"{identity.clock}_frames")
            progress = rank_frames(ranks,identity,frames)
            if progress is None:
                return False
            records = self.population.metadata_snapshot(identity.kind,identity.entity_key,
                clock=identity.clock,game_version=identity.game_version)
            winners = rank_recordings(records,ranks,identity)
            if any(row["player_id"]==player_id and row["strategy"]==attempt.strat_tag
                   and row["step"]==progress["step"] and row["frames"]<=frames for row in winners):
                return False
        self.population.accept_verified(attempt, self.replay, player_id=player_id,
            player_name=author, clock=identity.clock, game_version=identity.game_version,
            full_start=True)
        return True
