"""Bounded community IO. Synchronous callers only enqueue immutable requests."""
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import threading
import time
import random
from uuid import uuid4

from sm64_events.ghost.sharing_targets import canonical, checked_target

from . import codec
from .cache import checked_metadata, target_key, value_of
from .config import CommunityError

MAX_QUEUE = 32
MAX_PAGE = 256
MAX_PAGES = 4
ASSET_RETRY_S = 30
PIN_RENEW_S = 24 * 60 * 60  # Seven-day server leases; visibility metadata has its own bounded refresh.
PIN_METADATA_RENEW_S = 600
CATALOG_RENEW_S = 3600


def _prepare_source(source):
    """Bound and revalidate the immutable local recording before encoding."""
    with Path(source['path']).open('rb') as stream:
        raw = stream.read(codec.MAX_RAW + 1)
    if len(raw) > codec.MAX_RAW or hashlib.sha256(raw).hexdigest() != source['raw_sha256']:
        raise CommunityError('source_changed')
    return raw, codec.encode(raw)


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
    rows = [checked_metadata(row) for row in value["recordings"]]
    if any(row["generation"] != coverage["generation"] for row in rows) or len({row["id"] for row in rows}) != len(rows):
        raise CommunityError("invalid_response")
    if generation is not None and coverage["generation"] != generation:
        raise CommunityError("generation_changed")
    return {"recordings": rows, "coverage": dict(coverage), "next_cursor": value["next_cursor"]}


class CommunitySync:
    def __init__(self, account, population, *, http=None):
        self.account, self.population, self._http = account, population, http
        self._lock = threading.RLock()
        self._queue, self._recent = OrderedDict(), OrderedDict()
        self._asset_retry = OrderedDict()
        self._offer_retry = OrderedDict()
        self._network_owner, self._network_until, self._network_error = None, 0, None
        self._loop = self._wake = self._task = None
        self._closing = False
        self._catalog, self._catalog_generation, self._catalog_until = {}, None, 0
        self._local_jobs = set()
        self._search_task = None
        self._pins_leased = False
        self._pin_key, self._pins_until = None, 0
        self._pin_metadata_key, self._pin_metadata_until = None, 0
        self.last_error = None

    async def start(self):
        if self._task and not self._task.done():
            return
        self._closing = False
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="community-sync")
        self._wake.set()

    async def stop(self):
        deadline = time.monotonic() + 5
        with self._lock:
            self._closing = True
            self._queue.clear()
            self._offer_retry.clear()
        tasks = [task for task in (self._task, self._search_task) if task and not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=max(0, deadline - time.monotonic()))
            for task in done:
                if not task.cancelled():
                    task.exception()
            if pending:
                raise CommunityError("shutdown_timeout", "Community processing is still finishing.")
        # CPU helpers only read bounded local bytes or return decoded bytes.
        # They never publish or mutate replay/provider state after cancellation.
        if self._local_jobs:
            try:
                await asyncio.wait_for(asyncio.shield(asyncio.gather(*self._local_jobs, return_exceptions=True)),
                                       max(0, deadline - time.monotonic()))
            except TimeoutError:
                raise CommunityError("shutdown_timeout", "Community processing is still finishing.") from None
        self._task = self._wake = self._loop = self._search_task = None

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
            self._queue[key] = operation
            self._queue.move_to_end(key)
            while len(self._queue) > MAX_QUEUE:
                self._queue.popitem(last=False)
            loop, wake = self._loop, self._wake
        if loop and wake and not loop.is_closed():
            loop.call_soon_threadsafe(wake.set)
        return True

    def request_pool(self, identity, *, frame_windows, pinned_ids=()):
        try:
            key = self.population.identity_key(identity)
            windows = tuple(tuple(window) for window in frame_windows)
            pins = tuple(dict.fromkeys(pinned_ids))
            if len(pins) > 4:
                raise ValueError()
            from sm64_events.ghost.portable import checked_hash
            for pin in pins:
                checked_hash(pin)
            target = self.population.portable_target(identity)
            frozen = {"target": target, "kind": value_of(identity, "kind"), "entity_key": value_of(identity, "entity_key"),
                "clock": value_of(identity, "clock"), "game_version": value_of(identity, "game_version"),
                "scope": value_of(identity, "scope", "strategy"), "strategy": value_of(identity, "strategy")}
        except (ValueError, TypeError):
            return False
        return self._enqueue(("pool", key), ("pool", frozen, windows, pins))

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
                if self._asset_retry.get((owner, identifier), 0) <= time.monotonic())
        identifiers = tuple(identifier for identifier in identifiers if not self.population.is_ready(identifier))
        if not identifiers:
            return True
        return self._enqueue(("assets",), ("assets", identifiers))

    def offer_source(self, source):
        fields = {"target", "clock", "game_version", "strategy", "frames", "path", "raw_sha256", "full_start", "valid"}
        try:
            if (not isinstance(source, dict) or set(source) != fields or source["full_start"] is not True
                    or source["valid"] is not True or source["clock"] not in ("igt", "rta")
                    or source["game_version"] not in ("us", "jp") or type(source["frames"]) is not int
                    or not 1 <= source["frames"] <= 2592000 or not isinstance(source["strategy"], str)):
                return False
            from sm64_events.ghost.portable import checked_hash
            checked_hash(source["raw_sha256"])
            target = checked_target(source["target"])
            snapshot = self.account.snapshot()
            if snapshot.get("status") != "active" or not snapshot.get("sharing"):
                return False
            frozen = {**source, "target": target, "path": str(Path(source["path"]).resolve()), "owner": snapshot.get("uid")}
            # Reclassified runs can reuse bytes with a different admission body.
            # Only the same owner and immutable admission identity share retries.
            key = ("offer", frozen["owner"], canonical(target), source["clock"], source["game_version"],
                   source["strategy"], source["frames"], source["raw_sha256"])
            return self._enqueue(key, ("offer", frozen))
        except (ValueError, TypeError, OSError):
            return False

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
        value = await self._json("GET", "/v1/catalog")
        generation, catalog = checked_catalog(value)
        if generation != self._catalog_generation:
            self._recent.clear()
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

    def _check_identity(self, rows, key):
        for row in rows:
            row_key = (*target_key(row["target"]), row["clock"], row["game_version"])
            if row_key != key[:4] or key[4] == "strategy" and row["strategy"] != key[5]:
                raise CommunityError("identity_mismatch")

    async def _page(self, path, query):
        value = await self._json("GET", path, params=query)
        try:
            return checked_page(value, generation=self._catalog_generation)
        except CommunityError as error:
            if error.code == "generation_changed":
                self._catalog_until = 0
            raise

    async def _pool(self, identity, windows, pins):
        owner = self.account.snapshot().get("uid")
        await self._catalog_ready()
        key, query = self._query(identity)
        minimum, maximum, base = frame_band(self._catalog[key], windows)
        canonical_key = (owner, key, minimum, maximum, base, self._catalog_generation, pins)
        coverage = self.population.coverage(identity)
        covered = (coverage.get("generation") == self._catalog_generation
            and coverage.get("min_step") == minimum and coverage.get("max_step") == maximum
            and coverage.get("base_step") == base)
        # Authentication changes clear the population view. An old request
        # throttle must never prevent refilling that newly empty account view.
        if covered and self._recent.get(canonical_key, 0) > time.monotonic():
            return
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
        self.population.remember_page(identity, combined)
        self._recent[canonical_key] = time.monotonic() + 120
        while len(self._recent) > MAX_QUEUE:
            self._recent.popitem(last=False)
        pin_key = owner, pins
        if (pins or self._pins_leased) and (pin_key != self._pin_key or time.monotonic() >= self._pins_until):
            await self._json("PUT", "/v1/pins", json={"recording_ids": list(pins)})
            self._pins_leased = bool(pins)
            self._pin_key, self._pins_until = pin_key, time.monotonic() + PIN_RENEW_S
        # Chosen rivals may sit outside every automatic rank band. Renew their
        # publication gates with this existing refresh, or fetch them on restart.
        if pin_key != self._pin_metadata_key or time.monotonic() >= self._pin_metadata_until:
            await self._assets(pins, renew=True, identity_key=key, owner=owner)
            self._pin_metadata_key, self._pin_metadata_until = pin_key, time.monotonic() + PIN_METADATA_RENEW_S

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

    async def _asset(self, identifier, *, identity_key=None):
        owner = self.account.snapshot().get("uid")
        value = await self._json("GET", "/v1/recordings/" + identifier)
        row = checked_metadata({key: item for key, item in value.items() if key != "v"})
        if row["id"] != identifier:
            raise CommunityError("identity_mismatch")
        if identity_key is not None:
            self._check_identity([row], identity_key)
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

    async def _assets(self, identifiers, *, renew=False, identity_key=None, owner=None):
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
                if self._asset_retry.get(key, 0) > time.monotonic():
                    continue
            try:
                await self._asset(identifier, identity_key=identity_key)
            except (CommunityError, ValueError, OSError):
                failed = True
                with self._lock:
                    self._asset_retry[key] = time.monotonic() + ASSET_RETRY_S
                    self._asset_retry.move_to_end(key)
                    while len(self._asset_retry) > MAX_QUEUE:
                        self._asset_retry.popitem(last=False)
            else:
                with self._lock:
                    self._asset_retry.pop(key, None)
        if failed:
            raise CommunityError("asset_unavailable")

    async def _offer(self, source):
        snapshot = self.account.snapshot()
        if snapshot.get("status") != "active" or not snapshot.get("sharing") or snapshot.get("uid") != source["owner"]:
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
        if self._closing:
            return
        body = {"target": source["target"], "clock": source["clock"], "rom": source["game_version"],
            "strategy": source["strategy"], "frames": source["frames"], "raw_sha256": source["raw_sha256"],
            "raw_bytes": len(raw), "encoded_sha256": hashlib.sha256(encoded).hexdigest(), "encoded_bytes": len(encoded),
            "codec": "ghc1", "generation": self._catalog_generation}
        if source.get("mutation_generation") != self._catalog_generation:
            source.pop("mutation_keys", None)
            source["mutation_generation"] = self._catalog_generation
        mutation_keys = source.setdefault("mutation_keys", {})
        grant = await self._json("POST", "/v1/uploads", json=body,
                                 headers={"Idempotency-Key": mutation_keys.setdefault("grant", str(uuid4()))})
        disposition = grant.get("disposition")
        if disposition in ("already_present", "not_faster"):
            return
        if disposition != "upload" or set(grant) - {"v", "disposition", "upload_id", "expires_at", "recording_id"}:
            raise CommunityError("invalid_response")
        identifier = grant.get("upload_id")
        if not isinstance(identifier, str) or not 1 <= len(identifier) <= 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in identifier):
            raise CommunityError("invalid_response")
        try:
            timestamp = grant["expires_at"]
            if type(timestamp) is not int:
                raise ValueError()
            expires = datetime.fromtimestamp(timestamp, timezone.utc)
            if expires <= datetime.now(timezone.utc):
                raise ValueError()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise CommunityError("grant_expired") from None
        snapshot = self.account.snapshot()
        if self._closing or not snapshot.get("sharing") or snapshot.get("uid") != source["owner"]:
            return
        result = await self._json("PUT", "/v1/uploads/" + identifier, content=encoded,
                                 headers={"Idempotency-Key": mutation_keys.setdefault("publish", str(uuid4()))})
        if result.get("disposition") not in ("published", "already_present", "not_faster"):
            raise CommunityError("invalid_response")

    def _retry_offer(self, key, operation, error):
        if operation[0] != "offer" or not isinstance(error, CommunityError) or not (
                error.code in ("network_unavailable", "budget_exhausted", "grant_expired", "generation_changed")
                or error.status in (429, 502, 503, 504)):
            return
        source = operation[1]
        snapshot = self.account.snapshot()
        if snapshot.get("uid") != source["owner"] or not snapshot.get("sharing"):
            return
        tries = min(6, source.get("retry_count", 0) + 1)
        source["retry_count"] = tries
        if error.code == "grant_expired":
            source.pop("mutation_keys", None)
        delay = max(error.retry_after or 0, self._network_until - time.monotonic(),
                    random.uniform(1, 1.25) * min(900, 30 * 2 ** (tries - 1)))
        with self._lock:
            self._offer_retry[key] = operation, time.monotonic() + delay, tries
            while len(self._offer_retry) > MAX_QUEUE:
                self._offer_retry.popitem(last=False)

    async def _run(self):
        while True:
            with self._lock:
                soonest = min((item[1] for item in self._offer_retry.values()), default=None)
            try:
                if soonest is None:
                    await self._wake.wait()
                else:
                    await asyncio.wait_for(self._wake.wait(), max(0, soonest - time.monotonic()))
            except TimeoutError:
                pass
            self._wake.clear()
            with self._lock:
                for key, (operation, due, _tries) in tuple(self._offer_retry.items()):
                    if due <= time.monotonic():
                        self._offer_retry.pop(key)
                        self._queue.setdefault(key, operation)
                while len(self._queue) > MAX_QUEUE:
                    self._queue.popitem(last=False)
            while True:
                with self._lock:
                    if not self._queue:
                        break
                    key, operation = self._queue.popitem(last=False)
                if self.account.snapshot().get("status") != "active":
                    continue
                try:
                    if operation[0] == "pool":
                        await self._pool(*operation[1:])
                    elif operation[0] == "assets":
                        await self._assets(operation[1])
                    else:
                        await self._offer(operation[1])
                    self.last_error = None
                except (CommunityError, ValueError, OSError) as error:
                    self.last_error = "Community ghosts are temporarily unavailable."
                    if isinstance(error, CommunityError) and error.code == "generation_changed":
                        self._catalog_until = 0
                    self._retry_offer(key, operation, error)
