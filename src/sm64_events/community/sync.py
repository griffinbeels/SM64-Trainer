"""Bounded community IO. Synchronous callers only enqueue immutable requests."""
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
import logging
from pathlib import Path
import threading
import time
import random
from uuid import uuid4

from sm64_events.ghost.sharing_targets import canonical, checked_target
from sm64_events.ranks.region_scope import REGIONAL, servable_versions

from . import claims, codec
from .cache import LEASE_RENEW_S, checked_metadata, target_key, value_of
from .config import CommunityError
from .outbox import RetractionOutbox, SourceOutbox, checked_source, checked_strategy, retraction_key, source_key

logger = logging.getLogger(__name__)

MAX_QUEUE = 32
MAX_RETIRED = 256
RETRACT_ROUTE = "/v1/recordings/retract"
CONTRIBUTIONS = ("offer", "retract")  # Serial owner-bound work on the contribution task.


def _lane(operation):
    """Which worker runs it: a 180 s recording download never holds up a rival list."""
    return "contribution" if operation[0] in CONTRIBUTIONS else "download" if operation[0] == "assets" else "list"
MAX_PAGE = 256
MAX_PAGES = 4
ASSET_RETRY_S = 30
POOL_RETRY_MIN_S = 5
"""The shortest wait before the same rival list is asked for again."""
PIN_RENEW_S = 24 * 60 * 60  # Seven-day server leases; visibility metadata has its own bounded refresh.
CATALOG_RENEW_S = 3600
MAX_POOL_JOBS = 64


def _prepare_source(source):
    """Bound and revalidate the immutable local recording before encoding.

    Nothing leaves the machine that its own bytes do not bear out: the clip's
    ROM, a pad on every record, and the run's time (community/claims.py). A
    refusal discards the offer instead of retrying it."""
    with Path(source['path']).open('rb') as stream:
        raw = stream.read(codec.MAX_RAW + 1)
    if len(raw) > codec.MAX_RAW or hashlib.sha256(raw).hexdigest() != source['raw_sha256']:
        raise CommunityError('source_changed')
    try:
        claims.check_claims(raw, game_version=source['game_version'], clock=source['clock'], frames=source['frames'])
    except ValueError as error:  # CommunityError, or the decoder's ClipError
        raise CommunityError('invalid_source', str(error)) from None
    return raw, codec.encode(raw)


def _upload_identifier(grant):
    if grant.get("disposition") in ("already_present", "not_faster"):
        return None
    if grant.get("disposition") != "upload" or set(grant) - {"v", "disposition", "upload_id", "expires_at", "recording_id"}:
        raise CommunityError("invalid_response")
    identifier = grant.get("upload_id")
    if (not isinstance(identifier, str) or not 1 <= len(identifier) <= 128
            or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in identifier)):
        raise CommunityError("invalid_response")
    try:
        timestamp = grant["expires_at"]
        if type(timestamp) is not int or datetime.fromtimestamp(timestamp, timezone.utc) <= datetime.now(timezone.utc):
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
        raise CommunityError("grant_expired") from None
    return identifier


def _retryable(error):
    return isinstance(error, CommunityError) and (
        error.code in ("network_unavailable", "budget_exhausted", "grant_expired", "upload_expired", "generation_changed")
        or error.status in (429, 502, 503, 504))


def _cutoffs(rows):
    if not isinstance(rows, list) or not 1 <= len(rows) <= 256:
        raise CommunityError("invalid_catalog")
    result, previous = [], -1
    for row in rows:
        if (not isinstance(row, dict) or set(row) != {"step", "frames"}
                or type(row["step"]) is not int or not previous < row["step"] <= 1000
                or type(row["frames"]) is not int or not 1 <= row["frames"] <= 2592000
                or result and row["frames"] > result[-1][1]):
            raise CommunityError("invalid_catalog")
        result.append((row["step"], row["frames"]))
        previous = row["step"]
    return tuple(result)


INTEGRITY_FAILURES = ("asset_mismatch", "incomplete_inputs", "invalid_metadata", "invalid_name", "identity_mismatch",
                      "invalid_response")


def _unusable_until(error):
    """How long one recording leaves automatic seats after its download failed.

    Wrong or malformed content is content-addressed, so it never changes: the
    rest of the session. A refusal about that recording (gone, hidden) waits
    out the retry. Network, quota and account trouble is nobody's recording:
    it never empties the lineup.
    """
    if isinstance(error, CommunityError):
        if error.code in INTEGRITY_FAILURES:
            return float("inf")
        if _retryable(error) or error.status in (401, 403) or error.code in ("closed", "canceled"):
            return None
        return time.monotonic() + ASSET_RETRY_S
    return None if isinstance(error, OSError) else float("inf")


def checked_catalog(value):
    if (not isinstance(value, dict) or set(value) != {"v", "generation", "entries"} or type(value["v"]) is not int or value["v"] != 1
            or not isinstance(value["generation"], str) or not 1 <= len(value["generation"]) <= 128
            or not isinstance(value["entries"], list) or len(value["entries"]) > 4096):
        raise CommunityError("invalid_catalog")
    result = {}
    for entry in value["entries"]:
        if (not isinstance(entry, dict) or set(entry) != {"target", "clock", "game_version", "strategies", "overall_cutoffs"}
                or entry["clock"] not in ("igt", "rta") or entry["game_version"] not in ("us", "jp")
                or not isinstance(entry["strategies"], list) or not 1 <= len(entry["strategies"]) <= 128):
            raise CommunityError("invalid_catalog")
        target = checked_target(entry["target"])
        base = (*target_key(target), entry["clock"], entry["game_version"])
        for strategy in entry["strategies"]:
            if (not isinstance(strategy, dict) or set(strategy) != {"name", "cutoffs"}
                    or not isinstance(strategy["name"], str) or not 1 <= len(strategy["name"].encode("utf8")) <= 256):
                raise CommunityError("invalid_catalog")
            key = (*base, "strategy", strategy["name"])
            if key in result:
                raise CommunityError("invalid_catalog")
            result[key] = _cutoffs(strategy["cutoffs"])
        key = (*base, "overall", None)
        if key in result:
            raise CommunityError("invalid_catalog")
        result[key] = _cutoffs(entry["overall_cutoffs"])
    return value["generation"], result


def frame_band(cutoffs, windows):
    """Intersect inclusive frame windows with canonical bins, including base.

    Each bin is (next faster cutoff, this cutoff]. The slowest also covers
    lawful slower frames. No exact PB enters the request or cached pool key.
    """
    if not isinstance(windows, (tuple, list)) or not 1 <= len(windows) <= 8:
        raise CommunityError("invalid_windows")
    steps = set()
    for window in windows:
        if not isinstance(window, (tuple, list)) or len(window) != 2:
            raise CommunityError("invalid_windows")
        low, high = window
        low = 1 if low is None else low
        high = 2592000 if high is None else high
        if type(low) is not int or type(high) is not int or not 1 <= low <= high <= 2592000:
            raise CommunityError("invalid_windows")
        for index, (step, frames) in enumerate(cutoffs):
            maximum = 2592000 if index == 0 else frames
            minimum = cutoffs[index + 1][1] + 1 if index + 1 < len(cutoffs) else 1
            if max(low, minimum) <= min(high, maximum):
                steps.add(step)
    if not steps:
        raise CommunityError("unsupported_identity")
    return min(steps), max(steps), min(steps)


def checked_page(value, *, generation=None):
    if (not isinstance(value, dict) or set(value) != {"v", "recordings", "coverage", "next_cursor"} or type(value["v"]) is not int or value["v"] != 1
            or not isinstance(value["recordings"], list) or len(value["recordings"]) > MAX_PAGE
            or value["next_cursor"] is not None and (not isinstance(value["next_cursor"], str) or not 1 <= len(value["next_cursor"]) <= 2048)):
        raise CommunityError("invalid_response")
    coverage = value["coverage"]
    if (not isinstance(coverage, dict) or set(coverage) != {"complete", "min_step", "max_step", "base_step", "generation"}
            or type(coverage["complete"]) is not bool or not isinstance(coverage["generation"], str)
            or any(type(coverage[key]) is not int or not 0 <= coverage[key] <= 1000 for key in ("min_step", "max_step", "base_step"))
            or coverage["min_step"] > coverage["max_step"]
            or coverage["complete"] and value["next_cursor"] is not None):
        raise CommunityError("invalid_response")
    rows = []
    for row in value["recordings"]:
        try:
            rows.append(checked_metadata(row))
        except CommunityError as error:
            if error.code != "invalid_name":
                raise
            # A name the cloud itself would refuse: drop that recording only.
    if any(row["generation"] != coverage["generation"] for row in rows) or len({row["id"] for row in rows}) != len(rows):
        raise CommunityError("invalid_response")
    if generation is not None and coverage["generation"] != generation:
        raise CommunityError("generation_changed")
    return {"recordings": rows, "coverage": dict(coverage), "next_cursor": value["next_cursor"]}


class CommunitySync:
    def __init__(self, account, population, *, http=None):
        self.account, self.population, self._http = account, population, http
        self._lock = threading.RLock()
        self._queue = OrderedDict()
        self._running = None
        # (owner, list key) -> ((windows, pins) last served, when it is due again)
        self._pool_jobs = OrderedDict()
        self._pool_failures = OrderedDict()  # (owner, list key) -> "offline" | "unsupported"
        self._asset_retry = OrderedDict()
        self._asset_unusable = OrderedDict()  # (owner, recording id) -> monotonic time it may seat again
        self._offer_retry = OrderedDict()
        self._network_owner, self._network_until, self._network_error = None, 0, None
        self._loop = self._wake = self._task = None
        self._contribution_wake = self._contribution_task = self._practice_started = None
        self._download_wake = self._download_task = None
        self._closing = False
        self._catalog, self._catalog_generation, self._catalog_until = {}, None, 0
        self._catalog_lock = asyncio.Lock()
        self._local_jobs = set()
        self._search_task = None
        self._pins_leased = False
        self._pin_key, self._pins_until = None, 0
        self._pin_metadata_key = None
        self._outbox = SourceOutbox(population.root / "pending-contributions.json")
        self._retractions = RetractionOutbox(population.root / "pending-retractions.json")
        self._retired = OrderedDict()  # raw_sha256 -> the one strategy it may still be offered under
        self._consent_epoch = 0
        self._recovery_owner, self._recovery_until = None, 0
        if hasattr(account, "on_contribution_cancel"):
            account.on_contribution_cancel = self.cancel_contributions
        self.last_error = None

    async def start(self):
        if self._task and not self._task.done():
            return
        self._closing = False
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._contribution_wake = asyncio.Event()
        self._download_wake = asyncio.Event()
        self._practice_started = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="community-sync")
        self._download_task = asyncio.create_task(self._run_downloads(), name="community-downloads")
        self._contribution_task = asyncio.create_task(self._run_contributions(), name="community-contributions")
        self._wake.set()
        self._download_wake.set()
        self._contribution_wake.set()

    async def stop(self):
        deadline = time.monotonic() + 5
        with self._lock:
            self._closing = True
            self._queue.clear()
            self._offer_retry.clear()
        tasks = [task for task in (self._task, self._download_task, self._contribution_task, self._search_task)
                 if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.monotonic()))
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                raise CommunityError("shutdown_timeout", "Community processing is still finishing.")
        # Helpers read bounded local bytes and write bounded retry metadata.
        # Join them before releasing resources; HTTP publication stays on tasks.
        if self._local_jobs:
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.gather(*self._local_jobs, return_exceptions=True)),
                                       max(0, deadline - time.monotonic()))
            except TimeoutError:
                raise CommunityError("shutdown_timeout", "Community processing is still finishing.") from None
        self._task = self._wake = self._loop = self._search_task = None
        self._contribution_task = self._contribution_wake = self._practice_started = None
        self._download_task = self._download_wake = None

    def _enqueue(self, key, operation):
        with self._lock:
            if self._closing:
                return False
            if operation[0] == "offer":
                pending = self._offer_retry.pop(key, None)
                if pending:
                    for name in ("mutation_keys", "mutation_generation", "retry_count"):
                        if name in pending[0][1]:
                            operation[1][name] = pending[0][1][name]
            elif operation[0] == "retract":
                self._offer_retry.pop(key, None)  # A newer keep for this clip replaces the old one.
            self._queue[key] = operation
            self._queue.move_to_end(key)
            while len(self._queue) > MAX_QUEUE:
                self._queue.popitem(last=False)
            loop = self._loop
            wake = {"contribution": self._contribution_wake, "download": self._download_wake}.get(
                _lane(operation), self._wake)
        if loop and wake and not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)
        return True

    def request_pool(self, identity, *, frame_windows, pinned_ids=()):
        """Ask for one identity's rival list, only when the network is needed.

        Safe to call every cycle. Nothing is queued while the same list is
        queued or running, or while the list and chosen rivals it last served
        are still inside their publication leases (or a failure's back-off).
        """
        try:
            key = self.population.identity_key(identity)
            windows = tuple(tuple(window) for window in frame_windows)
            pins = None if pinned_ids is None else tuple(dict.fromkeys(pinned_ids))
            if pins is not None and len(pins) > 4:
                raise ValueError()
            from sm64_events.ghost.portable import checked_hash
            for pin in pins or ():
                checked_hash(pin)
        except (ValueError, TypeError):
            return False
        job = self.account.snapshot().get("uid"), key
        with self._lock:
            if self._closing:
                return False
            if ("pool", key) in self._queue or self._running == ("pool", key):
                return True
            served, due = self._pool_jobs.get(job, (None, 0))
            if served == (windows, pins, value_of(identity, "region_scope", REGIONAL)) and time.monotonic() < due:
                # A successful job outlives the bounded list cache. Eviction
                # or sign-out must not leave an empty list waiting on its old
                # job lease; failed requests still keep their network backoff.
                if job in self._pool_failures or not self._lease_due(self.population.lease_until(identity)):
                    return True
        try:
            target = self.population.portable_target(identity)
            frozen = {"target": target, "kind": value_of(identity, "kind"), "entity_key": value_of(identity, "entity_key"),
                "clock": value_of(identity, "clock"), "game_version": value_of(identity, "game_version"),
                "scope": value_of(identity, "scope", "strategy"), "strategy": value_of(identity, "strategy"),
                "region_scope": value_of(identity, "region_scope", REGIONAL)}
        except (ValueError, TypeError):
            return False
        return self._enqueue(("pool", key), ("pool", frozen, windows, pins))

    def pool_status(self, identity):
        """'pending' while this list is queued or running; after a failed
        request 'unsupported' (the catalog has no such list) or 'offline'
        (unreachable, rate limited or in a network cooldown); else None."""
        try:
            key = self.population.identity_key(identity)
        except (ValueError, TypeError):
            return None
        owner = self.account.snapshot().get("uid")
        with self._lock:
            if ("pool", key) in self._queue or self._running == ("pool", key):
                return "pending"
            failure = self._pool_failures.get((owner, key))
        if failure is None and self._network_owner == owner and self._network_until > time.monotonic():
            return "offline"
        return failure

    def refused_assets(self):
        """Recordings that cannot download now; automatic seats pass them over."""
        owner, now = self.account.snapshot().get("uid"), time.monotonic()
        with self._lock:
            return frozenset(identifier for (holder, identifier), until in self._asset_unusable.items()
                             if holder == owner and until > now)

    def _pool_served(self, owner, key, windows, pins, *, due, failure=None, region_scope=REGIONAL):
        """Remember what a list request settled and when it is due again."""
        with self._lock:
            job = owner, key
            self._pool_jobs[job] = (tuple(tuple(window) for window in windows),
                                    None if pins is None else tuple(pins), region_scope), due
            self._pool_jobs.move_to_end(job)
            if failure is None:
                self._pool_failures.pop(job, None)
            else:
                self._pool_failures[job] = failure
                self._pool_failures.move_to_end(job)
            for mapping in (self._pool_jobs, self._pool_failures):
                while len(mapping) > MAX_POOL_JOBS:
                    mapping.popitem(last=False)

    def request_assets(self, recording_ids):
        from sm64_events.ghost.portable import checked_hash
        try:
            identifiers = tuple(dict.fromkeys(recording_ids))
            if len(identifiers) > 4:
                raise ValueError()
            for identifier in identifiers:
                checked_hash(identifier)
        except (ValueError, TypeError):
            return False
        owner = self.account.snapshot().get("uid")
        with self._lock:
            if self._closing:
                return False
            identifiers = tuple(identifier for identifier in identifiers
                if self._asset_retry.get((owner, identifier), 0) <= time.monotonic()
                and self._asset_unusable.get((owner, identifier), 0) <= time.monotonic())
        identifiers = tuple(identifier for identifier in identifiers if not self.population.is_ready(identifier))
        if not identifiers:
            return True
        return self._enqueue(("assets",), ("assets", identifiers))

    def _freeze_source(self, source):
        fields = {"target", "clock", "game_version", "strategy", "frames", "path", "raw_sha256", "full_start", "valid"}
        try:
            if (not isinstance(source, dict) or set(source) != fields or source["full_start"] is not True
                    or source["valid"] is not True or source["clock"] not in ("igt", "rta")
                    or source["game_version"] not in ("us", "jp") or type(source["frames"]) is not int
                    or not 1 <= source["frames"] <= 2592000 or not isinstance(source["strategy"], str)):
                return None
            from sm64_events.ghost.portable import checked_hash
            checked_hash(source["raw_sha256"])
            target = checked_target(source["target"])
            snapshot = self.account.snapshot()
            if snapshot.get("status") != "active" or not snapshot.get("sharing"):
                return None
            frozen = {**source, "target": target, "path": str(Path(source["path"]).resolve()), "owner": snapshot.get("uid")}
            return {**checked_source(frozen), "consent_epoch": self._consent_epoch}
        except (ValueError, TypeError, OSError):
            return None

    def offer_source(self, source):
        frozen = self._freeze_source(source)
        return self._enqueue(source_key(frozen), ("offer", frozen)) if frozen else False

    def contribution_context(self):
        """Capture attribution and consent before native qualification starts."""
        with self._lock:
            snapshot = self.account.snapshot()
            if self._closing or snapshot.get("status") != "active" or not snapshot.get("sharing"):
                return None
            return {"owner": snapshot.get("uid"), "consent_epoch": self._consent_epoch}

    def remember_source(self, source, *, owner, consent_epoch):
        """Persist on the existing native admission worker, never a poll/UI path.

        The owner was captured before qualification. A login/consent change
        during preservation refuses attribution instead of assigning a new UID.
        """
        frozen = self._freeze_source(source)
        if frozen is None or frozen["owner"] != owner:
            return False
        frozen["consent_epoch"] = consent_epoch
        if not self._source_active(frozen):
            return False
        try:
            self._outbox.remember({key: value for key, value in frozen.items() if key != "consent_epoch"})
        except (CommunityError, OSError):
            self.last_error = "A community contribution could not be saved for retry."
            return self._enqueue(source_key(frozen), ("offer", frozen))
        if not self._source_active(frozen):
            self._outbox.discard((frozen,))
            return False
        return self._enqueue(source_key(frozen), ("offer", frozen))

    def retract_other_strategies(self, *, raw_sha256, keep_strategy):
        """Stop sharing this clip under every strategy but `keep_strategy`.

        Safe on a worker thread, like remember_source. Pending offers of these
        bytes under another strategy leave the queue, the retry map and the
        outbox, and later ones are refused. Then one owner-bound retraction is
        saved and queued for the copies already shared (every strategy when
        `keep_strategy` is None). Signed out or sharing off: False, nothing sent.
        """
        from sm64_events.ghost.portable import checked_hash
        try:
            checked_hash(raw_sha256)
            if keep_strategy is not None:
                checked_strategy(keep_strategy)
        except (ValueError, TypeError, CommunityError):
            return False

        def retired(operation):
            return (operation[0] == "offer" and operation[1]["raw_sha256"] == raw_sha256
                    and operation[1]["strategy"] != keep_strategy)
        with self._lock:
            if self._closing:
                return False
            self._retired.pop(raw_sha256, None)
            self._retired[raw_sha256] = keep_strategy
            while len(self._retired) > MAX_RETIRED:
                self._retired.popitem(last=False)
            for key, operation in tuple(self._queue.items()):
                if retired(operation):
                    self._queue.pop(key)
            for key, (operation, _due, _tries) in tuple(self._offer_retry.items()):
                if retired(operation):
                    self._offer_retry.pop(key)
        try:
            self._outbox.retire(raw_sha256, keep_strategy)
        except (CommunityError, OSError):
            self.last_error = "A community contribution could not be updated."
        snapshot = self.account.snapshot()
        if snapshot.get("status") != "active" or not snapshot.get("sharing") or not snapshot.get("uid"):
            return False
        retraction = {"owner": snapshot["uid"], "raw_sha256": raw_sha256,
                      "keep_strategy": keep_strategy, "key": str(uuid4())}
        try:
            self._retractions.remember(retraction)
        except (CommunityError, OSError):
            self.last_error = "A community retraction could not be saved for retry."
        return self._enqueue(retraction_key(retraction), ("retract", retraction))

    def _source_active(self, source):
        snapshot = self.account.snapshot()
        with self._lock:
            kept = self._retired.get(source["raw_sha256"], source["strategy"])
        return (not self._closing and snapshot.get("status") == "active" and snapshot.get("sharing")
                and snapshot.get("uid") == source["owner"] and kept == source["strategy"]
                and source.get("consent_epoch", self._consent_epoch) == self._consent_epoch)

    async def cancel_contributions(self, owner):
        with self._lock:
            self._consent_epoch += 1
            for mapping in (self._queue, self._offer_retry):
                for key in tuple(mapping):
                    if key[0] == "offer" and (owner is None or key[1] == owner):
                        mapping.pop(key)
        await self._cpu(self._outbox.clear if owner is None else lambda: self._outbox.discard(owner=owner))
        self._recovery_until = 0

    async def _recover_sources(self):
        epoch = self._consent_epoch
        if not self._outbox._loaded:
            await self._cpu(self._outbox.load)
        snapshot = self.account.snapshot()
        owner = snapshot.get("uid")
        if owner and snapshot.get("authenticated") and (not snapshot.get("sharing") or snapshot.get("status") == "deleted"):
            await self.cancel_contributions(owner)
            return
        if snapshot.get("status") != "active" or not snapshot.get("sharing") or owner is None or epoch != self._consent_epoch:
            return
        if owner == self._recovery_owner and time.monotonic() < self._recovery_until:
            return
        self._recovery_owner, self._recovery_until = owner, time.monotonic() + 60
        for retraction in await self._cpu(self._retractions.pending, owner):
            key = retraction_key(retraction)
            with self._lock:
                if key in self._queue or key in self._offer_retry:
                    continue
            self._enqueue(key, ("retract", retraction))
        sources = await self._cpu(self._outbox.available, owner)
        if not sources:
            return
        await self._catalog_ready()
        if epoch != self._consent_epoch:
            return
        winners, retired = self._recovery_winners(sources)
        await self._cpu(self._outbox.discard, retired)
        queued = 0
        for source in winners.values():
            key = source_key(source)
            with self._lock:
                if key in self._queue or key in self._offer_retry:
                    continue
            source["consent_epoch"] = epoch
            if self._source_active(source) and self._outbox.contains(source) and self._enqueue(key, ("offer", source)):
                queued += 1
            if queued == 8:
                break

    def _recovery_winners(self, sources):
        winners, retired = {}, []
        for source in sources:
            key = (*target_key(source["target"]), source["clock"], source["game_version"], "strategy", source["strategy"])
            cutoffs = self._catalog.get(key)
            if not cutoffs:
                retired.append(source)
                continue
            bin_key = key, frame_band(cutoffs, [(source["frames"], source["frames"])])[0]
            previous = winners.get(bin_key)
            if previous is None or (source["frames"], source["raw_sha256"]) < (previous["frames"], previous["raw_sha256"]):
                if previous:
                    retired.append(previous)
                winners[bin_key] = source
            else:
                retired.append(source)
        return winners, retired

    async def _json(self, method, path, **kwargs):
        owner = self.account.snapshot().get("uid")
        if self._network_owner == owner and self._network_until > time.monotonic():
            code, status = self._network_error
            raise CommunityError(code, status=status, retry_after=self._network_until - time.monotonic())
        headers = dict(kwargs.pop("headers", {}) or {})
        if method.upper() in ("POST", "PUT", "PATCH", "DELETE"):
            if not any(str(key).lower() == "idempotency-key" for key in headers):
                headers["Idempotency-Key"] = str(uuid4())
        for index in range(3):
            try:
                return await self.account.request_json(method, path, http=self._http, headers=headers, **kwargs)
            except CommunityError as error:
                if error.code == "generation_changed":
                    self._catalog_until = 0
                replay_safe = method.upper() == "GET" or any(str(key).lower() == "idempotency-key" for key in headers)
                transient = error.code == "network_unavailable" or error.status in (429, 502, 503, 504)
                cooldown = (error.status == 429 or error.code == "budget_exhausted"
                            or transient and (error.retry_after is not None or index == 2))
                if cooldown:
                    delay = error.retry_after or (900 if error.code == "budget_exhausted" else 30)
                    self._network_owner, self._network_until = owner, time.monotonic() + min(3600, max(1, delay))
                    self._network_error = error.code, error.status
                if not replay_safe or index == 2 or not transient or cooldown:
                    raise
                await asyncio.sleep(random.uniform(0.5, 1) * (1, 2)[index])

    async def _catalog_ready(self):
        if self._catalog_until > time.monotonic():
            return
        async with self._catalog_lock:
            if self._catalog_until > time.monotonic():
                return
            value = await self._json("GET", "/v1/catalog")
            generation, catalog = checked_catalog(value)
            self._catalog_generation, self._catalog = generation, catalog
            self._catalog_until = time.monotonic() + CATALOG_RENEW_S

    def _query(self, identity):
        key = self.population.identity_key(identity)
        if key not in self._catalog:
            raise CommunityError("unsupported_identity")
        target = self.population.portable_target(identity)
        target.pop("strat", None)
        query = {"target": canonical(target).decode(), "clock": key[2], "rom": key[3], "scope": key[4]}
        if key[5] is not None:
            query["strategy"] = key[5]
        return key, query

    def _check_identity(self, rows, key, *, versions=None):
        for row in rows:
            row_key = (*target_key(row["target"]), row["clock"], row["game_version"])
            roms = (key[3],) if versions is None else versions
            if (row_key[:3] != key[:3] or row_key[3] not in roms
                    or key[4] == "strategy" and row["strategy"] != key[5]):
                raise CommunityError("identity_mismatch")

    async def _page(self, path, query):
        value = await self._json("GET", path, params=query)
        try:
            return checked_page(value, generation=self._catalog_generation)
        except CommunityError as error:
            if error.code == "generation_changed":
                self._catalog_until = 0
            raise

    def _lease_due(self, lease):
        return lease is None or lease - time.monotonic() <= LEASE_RENEW_S

    async def _pool(self, identity, windows, pins):
        """Fetch a list only when none is held for this catalog band and lease.

        The list covers the whole ladder, so it never depends on the PB or the
        rival settings: a new PB re-picks locally. A held list renews once,
        shortly before its publication lease lapses.
        """
        owner = self.account.snapshot().get("uid")
        await self._catalog_ready()
        key, query = self._query(identity)
        minimum, maximum, base = frame_band(self._catalog[key], windows)
        coverage = self.population.coverage(identity)
        # Authentication changes clear the population view, so a new account
        # never inherits a held list.
        held = (coverage.get("generation") == self._catalog_generation
            and coverage.get("min_step") == minimum and coverage.get("max_step") == maximum
            and coverage.get("base_step") == base and not self._lease_due(self.population.lease_until(identity)))
        if not held:
            query.update(min_step=minimum, max_step=maximum, base_step=base)
            combined = None
            for _ in range(MAX_PAGES):
                page = await self._page("/v1/pool", query)
                self._check_identity(page["recordings"], key)
                if combined is None:
                    combined = page
                else:
                    combined["recordings"] += page["recordings"]
                    combined["next_cursor"] = page["next_cursor"]
                    combined["coverage"]["complete"] = page["coverage"]["complete"]
                if page["next_cursor"] is None:
                    break
                query["cursor"] = page["next_cursor"]
            if combined["next_cursor"] is not None:
                combined["coverage"]["complete"] = False
            if self._closing or self.account.snapshot().get("uid") != owner:
                return
            # Stored under the identity this request named, never the one on screen now.
            self.population.remember_page(identity, combined)
        # The second ROM's metadata request must not clear account-wide pins.
        if pins is not None:
            await self._renew_pins(identity, key, pins, owner)
        leases = [lease for lease in (self.population.lease_until(identity),
                                      *(self.population.lease_until(recording=pin) for pin in pins or ()))
                  if lease is not None]
        due = max(time.monotonic() + POOL_RETRY_MIN_S, min(leases, default=0) - LEASE_RENEW_S)
        self._pool_served(owner, key, windows, pins, due=due,
                          region_scope=value_of(identity, "region_scope", REGIONAL))

    async def _renew_pins(self, identity, key, pins, owner):
        """One account-wide pin lease, including compatible cross-ROM choices."""
        pin_key = owner, pins
        versions = servable_versions(value_of(identity, "region_scope", REGIONAL), key[3])
        metadata_key = pin_key, versions
        if (pins or self._pins_leased) and (pin_key != self._pin_key or time.monotonic() >= self._pins_until):
            await self._json("PUT", "/v1/pins", json={"recording_ids": list(pins)})
            self._pins_leased = bool(pins)
            self._pin_key, self._pins_until = pin_key, time.monotonic() + PIN_RENEW_S
        # Chosen rivals may sit outside every automatic rank band. Renew their
        # publication gates before they lapse, or fetch them on restart.
        if metadata_key != self._pin_metadata_key or any(
                self._lease_due(self.population.lease_until(recording=pin)) for pin in pins):
            await self._assets(pins, renew=True, identity_key=key, owner=owner, versions=versions)
            self._pin_metadata_key = metadata_key

    async def search(self, identity, prefix, *, cursor=None):
        if self._closing:
            raise CommunityError("closed")
        prefix = self.population._prefix(prefix)
        if not prefix:
            return {"records": [], "next_cursor": None}
        if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 2048):
            raise CommunityError("invalid_cursor")
        current = asyncio.current_task()
        owner = self.account.snapshot().get("uid")
        if self._search_task and self._search_task is not current and not self._search_task.done():
            self._search_task.cancel()
        self._search_task = current
        try:
            await self._catalog_ready()
            key, query = self._query(identity)
            query["prefix"] = prefix
            if cursor is not None:
                query["cursor"] = cursor
            page = await self._page("/v1/search", query)
            if len(page["recordings"]) > 20:
                raise CommunityError("invalid_response")
            self._check_identity(page["recordings"], key)
            if self._closing or self.account.snapshot().get("uid") != owner:
                raise CommunityError("canceled")
            self.population.remember_page(identity, page, prefix=prefix, cursor=cursor)
            return self.population.search(identity, prefix, cursor=cursor)
        finally:
            if self._search_task is current:
                self._search_task = None

    async def _cpu(self, callback, *args):
        task = asyncio.create_task(asyncio.to_thread(callback, *args))
        self._local_jobs.add(task)
        task.add_done_callback(self._local_jobs.discard)
        return await asyncio.shield(task)

    async def _asset(self, identifier, *, identity_key=None, versions=None):
        owner = self.account.snapshot().get("uid")
        value = await self._json("GET", "/v1/recordings/" + identifier)
        row = checked_metadata({key: item for key, item in value.items() if key != "v"})
        if row["id"] != identifier:
            raise CommunityError("identity_mismatch")
        if identity_key is not None:
            self._check_identity([row], identity_key, versions=versions)
        if self._closing or self.account.snapshot().get("uid") != owner:
            return
        self.population.renew_metadata(row)
        if self.population.is_ready(identifier):
            # Renew the actual current publication gate, including a rename.
            return
        encoded = await self.account.request_bytes("GET", "/v1/recordings/" + identifier + "/asset",
                                                  http=self._http, limit=codec.MAX_ENCODED, deadline=180)
        if len(encoded) != row["encoded_bytes"] or hashlib.sha256(encoded).hexdigest() != row["encoded_sha256"]:
            raise CommunityError("asset_mismatch")
        raw = await self._cpu(lambda data: codec.decode(data, raw_sha256=row["raw_sha256"]), encoded)
        if self._closing or self.account.snapshot().get("uid") != owner:
            return
        await self._cpu(lambda: self.population.store_asset(row, encoded, raw, owner=owner))

    async def _assets(self, identifiers, *, renew=False, identity_key=None, owner=None, versions=None):
        """Try bounded IDs independently, without hot-looping failed gates."""
        owner = owner or self.account.snapshot().get("uid")
        failed = False
        for identifier in identifiers:
            snapshot = self.account.snapshot()
            if self._closing or snapshot.get("uid") != owner or snapshot.get("status") != "active":
                return
            if not renew and self.population.is_ready(identifier):
                continue
            key = (owner, identifier)
            with self._lock:
                if max(self._asset_retry.get(key, 0), self._asset_unusable.get(key, 0)) > time.monotonic():
                    continue
            try:
                await self._asset(identifier, identity_key=identity_key, versions=versions)
            except (CommunityError, ValueError, OSError) as error:
                failed = True
                with self._lock:
                    self._asset_retry[key] = time.monotonic() + ASSET_RETRY_S
                    self._asset_retry.move_to_end(key)
                    while len(self._asset_retry) > MAX_QUEUE:
                        self._asset_retry.popitem(last=False)
                    until = _unusable_until(error)
                    if until is not None:
                        self._asset_unusable[key] = until
                        self._asset_unusable.move_to_end(key)
                        while len(self._asset_unusable) > MAX_RETIRED:
                            self._asset_unusable.popitem(last=False)
            else:
                with self._lock:
                    self._asset_retry.pop(key, None)
                    self._asset_unusable.pop(key, None)
        if failed:
            raise CommunityError("asset_unavailable")

    async def _offer(self, source):
        if not self._source_active(source):
            return
        await self._catalog_ready()
        key = (*target_key(source["target"]), source["clock"], source["game_version"], "strategy", source["strategy"])
        cutoffs = self._catalog.get(key)
        if not cutoffs:
            raise CommunityError("unsupported_identity")
        step = frame_band(cutoffs, [(source["frames"], source["frames"])])[0]
        with self._lock:
            # Discard slower pending offers in this canonical bin, preserving
            # all other bins. A source is independent of local ladder admission.
            candidates = [(queue_key, item[1]) for queue_key, item in (
                *self._queue.items(), *((retry_key, retry[0]) for retry_key, retry in self._offer_retry.items())) if item[0] == "offer"
                and item[1]["owner"] == source["owner"]
                and target_key(item[1]["target"]) == key[:2] and item[1]["clock"] == source["clock"]
                and item[1]["game_version"] == source["game_version"] and item[1]["strategy"] == source["strategy"]
                and frame_band(cutoffs, [(item[1]["frames"], item[1]["frames"])])[0] == step]
            if any(item["frames"] < source["frames"] for _, item in candidates):
                return
            for queue_key, _ in candidates:
                self._queue.pop(queue_key, None)
                self._offer_retry.pop(queue_key, None)
        raw, encoded = await self._cpu(_prepare_source, source)
        if not self._source_active(source):
            return
        # The ROM comes from the clip header, never the attempt's own claim.
        body = {"target": source["target"], "clock": source["clock"], "rom": claims.region_of(raw),
            "strategy": source["strategy"], "frames": source["frames"], "raw_sha256": source["raw_sha256"],
            "raw_bytes": len(raw), "encoded_sha256": hashlib.sha256(encoded).hexdigest(), "encoded_bytes": len(encoded),
            "codec": "ghc1", "generation": self._catalog_generation}
        if source.get("mutation_generation") != self._catalog_generation:
            source.pop("mutation_keys", None)
            source["mutation_generation"] = self._catalog_generation
        mutation_keys = source.setdefault("mutation_keys", {})
        mutation_keys.setdefault("grant", str(uuid4()))
        await self._cpu(self._outbox.update, source)
        if not self._source_active(source):
            return
        grant = await self._json("POST", "/v1/uploads", json=body,
                                 headers={"Idempotency-Key": mutation_keys["grant"]})
        identifier = _upload_identifier(grant)
        if identifier is None:
            return
        if not self._source_active(source):
            return
        mutation_keys.setdefault("publish", str(uuid4()))
        await self._cpu(self._outbox.update, source)
        if not self._source_active(source):
            return
        result = await self._json("PUT", "/v1/uploads/" + identifier, content=encoded,
                                 headers={"Idempotency-Key": mutation_keys["publish"]})
        if result.get("disposition") not in ("published", "already_present", "not_faster"):
            raise CommunityError("invalid_response")

    async def _retract(self, retraction):
        snapshot = self.account.snapshot()
        if snapshot.get("uid") != retraction["owner"] or snapshot.get("status") != "active":
            return  # The saved retraction waits for its owner's next active session.
        if not await self._cpu(self._retractions.is_current, retraction):
            return  # A newer strategy superseded this queued/in-flight operation.
        body = {"raw_sha256": retraction["raw_sha256"], "keep_strategy": retraction["keep_strategy"]}
        try:
            value = await self._json("POST", RETRACT_ROUTE, json=body, headers={"Idempotency-Key": retraction["key"]})
        except CommunityError as error:
            if error.status != 404:
                raise
            # A service older than the route answers 404, like the names check.
            logger.debug("The community service has no retraction route; one retraction was dropped.")
        else:
            if set(value) != {"v", "retracted"} or type(value["retracted"]) is not int or value["retracted"] < 0:
                raise CommunityError("invalid_response")
        await self._cpu(self._retractions.discard, retraction)

    def _retry_offer(self, key, operation, error):
        """Schedule a transiently failed contribution again without a new PB."""
        if operation[0] not in CONTRIBUTIONS or not _retryable(error):
            return
        source = operation[1]
        snapshot = self.account.snapshot()
        if snapshot.get("uid") != source["owner"] or operation[0] == "offer" and not self._source_active(source):
            return
        tries = min(6, source.get("retry_count", 0) + 1)
        source["retry_count"] = tries
        if operation[0] == "offer" and error.code in ("grant_expired", "upload_expired"):
            source.pop("mutation_keys", None)
        delay = max(error.retry_after or 0, self._network_until - time.monotonic(),
                    random.uniform(1, 1.25) * min(900, 30 * 2 ** (tries - 1)))
        with self._lock:
            if operation[0] == "retract" and not self._retractions.is_current(source):
                return  # Its late HTTP failure must not retry over a newer keep.
            self._offer_retry[key] = operation, time.monotonic() + delay, tries
            while len(self._offer_retry) > MAX_QUEUE:
                self._offer_retry.popitem(last=False)

    def _take_operation(self, *, contribution=False, lane=None):
        lane = lane or ("contribution" if contribution else "list")
        with self._lock:
            for key, operation in self._queue.items():
                if _lane(operation) == lane:
                    self._queue.pop(key)
                    if lane == "list":
                        self._running = key  # Still in flight for request_pool's dedupe.
                    return key, operation
        return None

    def _pool_failed(self, operation, error):
        """Back off one list after a failure, and say why for the lineup."""
        try:
            key = self.population.identity_key(operation[1])
        except (ValueError, TypeError):
            return
        code = getattr(error, "code", None)
        delay = (CATALOG_RENEW_S if code == "unsupported_identity"
                 else POOL_RETRY_MIN_S if code == "generation_changed" else ASSET_RETRY_S)
        due = max(time.monotonic() + delay, self._network_until)
        self._pool_served(self.account.snapshot().get("uid"), key, operation[2], operation[3], due=due,
                          failure="unsupported" if code == "unsupported_identity" else "offline",
                          region_scope=value_of(operation[1], "region_scope", REGIONAL))

    async def _failed_operation(self, key, operation, error):
        self.last_error = "Community ghosts are temporarily unavailable."
        if isinstance(error, CommunityError) and error.code == "generation_changed":
            self._catalog_until = 0
        if operation[0] == "pool":
            self._pool_failed(operation, error)
        self._retry_offer(key, operation, error)
        if operation[0] == "offer" and (isinstance(error, OSError)
                or isinstance(error, CommunityError) and error.code in ("source_changed", "invalid_source", "unsupported_identity")):
            await self._cpu(self._outbox.discard, (operation[1],))
        if operation[0] == "retract" and not _retryable(error):
            try:
                await self._cpu(self._retractions.discard, operation[1])
            except (CommunityError, OSError):
                pass  # A row left saved is only sent again; the server answers replays.

    async def _run(self):
        """Rival lists never wait behind a contribution's or a recording's HTTP transfer."""
        while True:
            await self._wake.wait()
            self._wake.clear()
            while (item := self._take_operation(lane="list")) is not None:
                key, operation = item
                try:
                    if self.account.snapshot().get("status") != "active":
                        continue
                    try:
                        await self._pool(*operation[1:])
                        self.last_error = None
                    except (CommunityError, ValueError, OSError) as error:
                        await self._failed_operation(key, operation, error)
                finally:
                    self._running = None
            self._practice_started.set()

    async def _run_downloads(self):
        """Chosen seats' recordings, one request at a time, beside the list worker."""
        while True:
            await self._download_wake.wait()
            self._download_wake.clear()
            while (item := self._take_operation(lane="download")) is not None:
                key, operation = item
                if self.account.snapshot().get("status") != "active":
                    continue
                try:
                    await self._assets(operation[1])
                    self.last_error = None
                except (CommunityError, ValueError, OSError) as error:
                    await self._failed_operation(key, operation, error)

    async def _run_contributions(self):
        """One serial recovery/upload task, sharing the existing owned loop."""
        await self._practice_started.wait()
        while True:
            with self._lock:
                soonest = min((item[1] for item in self._offer_retry.values()), default=None)
            try:
                if soonest is None:
                    await asyncio.wait_for(self._contribution_wake.wait(), 5)
                else:
                    await asyncio.wait_for(self._contribution_wake.wait(), min(5, max(0, soonest - time.monotonic())))
            except TimeoutError:
                pass
            self._contribution_wake.clear()
            try:
                await self._recover_sources()
            except (CommunityError, ValueError, OSError):
                self.last_error = "Community contributions are temporarily unavailable."
            with self._lock:
                for key, (operation, due, _tries) in tuple(self._offer_retry.items()):
                    if due <= time.monotonic():
                        self._offer_retry.pop(key)
                        self._queue.setdefault(key, operation)
                while len(self._queue) > MAX_QUEUE:
                    self._queue.popitem(last=False)
            while (item := self._take_operation(contribution=True)) is not None:
                key, operation = item
                if self.account.snapshot().get("status") != "active":
                    continue
                try:
                    if operation[0] == "retract":
                        await self._retract(operation[1])
                    else:
                        await self._offer(operation[1])
                        if self._source_active(operation[1]):
                            await self._cpu(self._outbox.discard, (operation[1],))
                    self.last_error = None
                except (CommunityError, ValueError, OSError) as error:
                    await self._failed_operation(key, operation, error)
